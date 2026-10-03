#!/usr/bin/env python
"""Run zero-shot Kronos inference on every rebalance date and write one parquet per as_of.

    python -m B_model_infer.run_inference --run-id kronos_base_v1 --config configs/base.yaml \
        [--start 2024-07-01 --end 2025-06-30 --step 5 --lookback 400 --horizon 5 --sample-count 20]
        [--profile base|paper] [--backend kronos|dummy] [--device cuda:0] [--batch-size 256]

Sampling parameters (lookback, pred_len, step, temperature, top_p, sample_count) come from the
inference profile `infer.profiles.<profile>` (default infer.default_profile, D-12: base); CLI flags override
them for one run. The manifest records `profile` and those values. as_of dates = rebalance_dates(start, end, step).
Resumable: dates whose parquet already exists are skipped. Files are written atomically.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from B_model_infer.env_info import collect_env  # noqa: E402
from common.config import ConfigError, cfg_get, cfg_override, load_config  # noqa: E402
from common.data import load_prices, rebalance_dates, trading_calendar  # noqa: E402
from common.meta import atomic_parquet, git_commit_hash, write_json_atomic  # noqa: E402
from common.paths import Paths  # noqa: E402
from common.schema import predictions_from_array, validate_predictions  # noqa: E402
from common.universe import combined_universe_at, load_constituents  # noqa: E402
from B_model_infer.build_batch import build_batch  # noqa: E402

# names kept for callers/tests written against the previous module layout
git_commit = git_commit_hash
write_manifest = write_json_atomic


def profile_cfg(cfg: dict, name: str | None = None) -> dict:
    """{profile, lookback, pred_len, step, temperature, top_p, sample_count} of one inference profile."""
    name = name or cfg_get(cfg, "infer.default_profile", "base")
    prof = cfg_get(cfg, f"infer.profiles.{name}")
    if not isinstance(prof, dict):
        raise ConfigError(f"infer.profiles.{name} is not defined in the config")
    missing = [k for k in ("lookback", "pred_len", "step", "temperature", "top_p", "sample_count") if prof.get(k) is None]
    if missing:
        raise ConfigError(f"infer.profiles.{name} lacks {missing}")
    return {"profile": name, **prof}


def make_backend(cfg: dict, name: str, prices: pd.DataFrame, signal_strength: float = 0.0, profile: str | None = None,
                 allow_unpinned: bool = False):
    seed = int(cfg_get(cfg, "project.seed", 0))
    if name == "dummy":
        from B_model_infer.backends import DummyBackend
        return DummyBackend(seed=seed, signal_strength=signal_strength, prices=prices if signal_strength > 0 else None)
    if name == "kronos":
        from B_model_infer.backends import KronosBackend
        prof = profile_cfg(cfg, profile)
        rev, trev = cfg_get(cfg, "model.revision"), cfg_get(cfg, "model.tokenizer_revision")
        if (not rev or not trev) and not allow_unpinned:
            raise ConfigError("model.revision / model.tokenizer_revision are null: pin the HF commit shas (appendix D), "
                              "or pass --allow-unpinned for a smoke test (the resolved shas are written to the manifest)")
        dtype = str(cfg_get(cfg, "model.dtype", "float32"))
        if dtype != "float32":
            raise ConfigError(f"model.dtype {dtype!r} is not supported: keep float32 (bf16 changes the numbers)")
        bs = cfg_get(cfg, "model.batch_size")
        if bs is None:
            raise ConfigError("model.batch_size is null: fill it from the Pod probe (appendix C-2) or pass --batch-size")
        return KronosBackend(
            model_name=cfg_get(cfg, "model.name"), tokenizer_name=cfg_get(cfg, "model.tokenizer"),
            revision=rev or None, tokenizer_revision=trev or None, kronos_repo=cfg_get(cfg, "model.kronos_repo"),
            device=cfg_get(cfg, "model.device", "cuda:0"), max_context=int(cfg_get(cfg, "model.max_context", 512)),
            temperature=float(prof["temperature"]), top_p=float(prof["top_p"]),
            top_k=int(cfg_get(cfg, "model.top_k", 0)), batch_size=int(bs), seed=seed,
        )
    raise ValueError(f"unknown backend {name}")


def run(cfg: dict, run_id: str, backend, root: Path = Path("."), prices: pd.DataFrame | None = None,
        constituents: dict | None = None, log=print, extra_manifest: dict | None = None,
        profile: str | None = None, max_tickers: int | None = None) -> list[pd.Timestamp]:
    """Core loop, importable for tests and for make_fake_predictions. Returns dates written.
    profile: inference profile name (None = infer.default_profile). max_tickers: smoke tests only, keep the first N
    universe members per date (recorded in the manifest; never used for a real run)."""
    paths = Paths(cfg, root)
    prof = profile_cfg(cfg, profile)
    infer_variant = prof.get("universe_variant") or cfg_get(cfg, "universe.variant", "base")   # D-5
    if prices is None:
        prices = load_prices(cfg, root, include_benchmark=False)
    if constituents is None:
        constituents = load_constituents(cfg, root, variant=infer_variant)

    lookback, horizon = int(prof["lookback"]), int(prof["pred_len"])
    sample_count, step = int(prof["sample_count"]), int(prof["step"])
    start, end = cfg_get(cfg, "period.start"), cfg_get(cfg, "period.end")
    max_stale = int(cfg_get(cfg, "universe.max_stale_days", 5))
    cal = trading_calendar(prices)
    dates = rebalance_dates(cal, start, end, step)

    manifest_path = paths.manifest_file(run_id)
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    manifest.update({
        "run_id": run_id,
        "backend": getattr(backend, "name", type(backend).__name__),
        "profile": prof["profile"],
        "model": cfg_get(cfg, "model.name"), "tokenizer": cfg_get(cfg, "model.tokenizer"),
        "hf_revision_requested": cfg_get(cfg, "model.revision"),
        "hf_revision_resolved": getattr(backend, "resolved_revision", None),
        "lookback": lookback, "pred_len": horizon, "temperature": prof["temperature"],
        "top_p": prof["top_p"], "top_k": cfg_get(cfg, "model.top_k"), "sample_count": sample_count,
        "universe": cfg_get(cfg, "universe.indices"), "universe_variant": infer_variant,
        "backtest_universe_variant": cfg_get(cfg, "universe.variant", "base"), "dtype": cfg_get(cfg, "model.dtype", "float32"),
        "env": collect_env(cfg_get(cfg, "model.device"), str(cfg_get(cfg, "model.dtype", "float32"))),
        "start": start, "end": end,
        "step": step, "n_rebalance_dates": int(len(dates)), "code_commit": git_commit_hash(root),
        "max_tickers": max_tickers, "smoke": max_tickers is not None,
        "started_at": manifest.get("started_at") or pd.Timestamp.utcnow().isoformat(),
        "last_updated_at": pd.Timestamp.utcnow().isoformat(), "config": cfg,
    })
    manifest.update(extra_manifest or {})
    write_json_atomic(manifest_path, manifest)

    written: list[pd.Timestamp] = []
    skipped_log = manifest.setdefault("skipped_by_date", {})
    env_by_date = manifest.setdefault("env_by_date", {})
    elapsed_by_date = manifest.setdefault("elapsed_by_date", {})
    env = manifest["env"]
    session = {"started_at": pd.Timestamp.utcnow().isoformat(), "hostname": env.get("hostname"), "gpu_name": env.get("gpu_name"),
               "pod_id": env.get("pod_id"), "n_written": 0, "finished_at": None}
    manifest.setdefault("sessions", []).append(session)
    for i, d in enumerate(dates):
        out = paths.prediction_file(run_id, d)
        if out.exists():
            log(f"[{i+1}/{len(dates)}] {d.date()} exists, skip")
            continue
        members = combined_universe_at(constituents, d)["ticker"].tolist()
        if max_tickers is not None:
            members = members[: int(max_tickers)]
        batch = build_batch(prices, members, d, lookback, horizon, max_stale_days=max_stale)
        t0 = time.time()
        if len(batch) == 0:
            log(f"[{i+1}/{len(dates)}] {d.date()} no eligible tickers ({len(batch.skipped)} skipped)")
            skipped_log[str(d.date())] = {"n_pred": 0, "skipped": batch.skipped}
            continue
        samples = backend.predict(batch, horizon, sample_count)
        df = predictions_from_array(d, batch.tickers, samples)
        df = validate_predictions(df, as_of=d, horizon=horizon)
        atomic_parquet(df, out)
        written.append(d)
        skipped_log[str(d.date())] = {"n_pred": len(batch), "n_skipped": len(batch.skipped),
                                      "skip_reasons": _count_reasons(batch.skipped)}
        env_by_date[str(d.date())] = {"gpu_name": env.get("gpu_name"), "driver_version": env.get("driver_version"), "hostname": env.get("hostname")}
        elapsed_by_date[str(d.date())] = round(time.time() - t0, 2)
        session["n_written"] = len(written)
        manifest["last_updated_at"] = pd.Timestamp.utcnow().isoformat()
        write_json_atomic(manifest_path, manifest)
        log(f"[{i+1}/{len(dates)}] {d.date()} tickers={len(batch)} skipped={len(batch.skipped)} "
            f"rows={len(df)} {time.time()-t0:.1f}s")
    session["finished_at"] = pd.Timestamp.utcnow().isoformat()
    manifest["completed_at"] = session["finished_at"]
    manifest["n_files_present"] = len(list(paths.predictions_dir(run_id).glob("as_of=*.parquet")))
    write_json_atomic(manifest_path, manifest)
    return written


def _count_reasons(skipped: dict[str, str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in skipped.values():
        key = r.split("(")[0]
        out[key] = out.get(key, 0) + 1
    return out


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-id", required=True)
    p.add_argument("--config", default="configs/base.yaml")
    p.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    p.add_argument("--backend", default="kronos", choices=["kronos", "dummy"])
    p.add_argument("--profile", help="inference profile (infer.profiles.*); default infer.default_profile")
    p.add_argument("--start"); p.add_argument("--end")
    p.add_argument("--step", type=int); p.add_argument("--lookback", type=int); p.add_argument("--horizon", type=int)
    p.add_argument("--sample-count", type=int); p.add_argument("--batch-size", type=int)
    p.add_argument("--device"); p.add_argument("--temperature", type=float); p.add_argument("--top-p", type=float)
    p.add_argument("--model"); p.add_argument("--tokenizer"); p.add_argument("--revision"); p.add_argument("--tokenizer-revision"); p.add_argument("--kronos-repo")
    p.add_argument("--allow-unpinned", action="store_true", help="smoke tests only: run without pinned HF revisions")
    p.add_argument("--max-tickers", type=int, help="smoke tests only: first N universe members per date")
    return p.parse_args(argv)


def main(argv=None):
    a = parse_args(argv)
    root = Path(a.root)
    cfg = load_config(root / a.config)
    profile = a.profile or cfg_get(cfg, 'infer.default_profile', 'base')
    prof = f"infer.profiles.{profile}"
    cfg = cfg_override(cfg, {
        "period.start": a.start, "period.end": a.end,
        f"{prof}.step": a.step, f"{prof}.lookback": a.lookback, f"{prof}.pred_len": a.horizon,
        f"{prof}.sample_count": a.sample_count, f"{prof}.temperature": a.temperature, f"{prof}.top_p": a.top_p,
        "model.batch_size": a.batch_size, "model.device": a.device, "model.name": a.model, "model.tokenizer": a.tokenizer,
        "model.revision": a.revision, "model.tokenizer_revision": a.tokenizer_revision, "model.kronos_repo": a.kronos_repo,
    })
    p = profile_cfg(cfg, profile)
    if int(p["step"]) != int(p["pred_len"]):
        print(f"note: profile {p['profile']} step={p['step']} != pred_len={p['pred_len']} (weekly profile expects equal)")
    prices = load_prices(cfg, root, include_benchmark=False)
    backend = make_backend(cfg, a.backend, prices, profile=profile, allow_unpinned=a.allow_unpinned)
    written = run(cfg, a.run_id, backend, root=root, prices=prices, profile=profile, max_tickers=a.max_tickers)
    print(f"done: {len(written)} new date files in {Paths(cfg, root).predictions_dir(a.run_id)}")


if __name__ == "__main__":
    main()
