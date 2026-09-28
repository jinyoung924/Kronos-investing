#!/usr/bin/env python
"""Run zero-shot Kronos inference on every rebalance date and write one parquet per as_of.

    python infer/run_inference.py --run-id kronos_base_v1 --config configs/base.yaml \
        [--start 2024-07-01 --end 2025-06-30 --step 5 --lookback 400 --horizon 5 --sample-count 20]
        [--backend kronos|dummy] [--device cuda:0] [--batch-size 256]

Resumable: dates whose parquet already exists are skipped. Files are written atomically.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.config import cfg_get, cfg_override, load_config  # noqa: E402
from common.data import load_prices, rebalance_dates, trading_calendar  # noqa: E402
from common.paths import Paths  # noqa: E402
from common.schema import predictions_from_array, validate_predictions  # noqa: E402
from common.universe import combined_universe_at, load_constituents  # noqa: E402
from infer.build_batch import build_batch  # noqa: E402


def git_commit(root: Path) -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    except Exception:
        return None


def write_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def atomic_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def make_backend(cfg: dict, name: str, prices: pd.DataFrame, signal_strength: float = 0.0):
    if name == "dummy":
        from infer.backends import DummyBackend
        return DummyBackend(seed=int(cfg_get(cfg, "model.seed", 0)), signal_strength=signal_strength,
                            prices=prices if signal_strength > 0 else None)
    if name == "kronos":
        from infer.backends import KronosBackend
        return KronosBackend(
            model_name=cfg_get(cfg, "model.name"), tokenizer_name=cfg_get(cfg, "model.tokenizer"),
            revision=cfg_get(cfg, "model.revision"), kronos_repo=cfg_get(cfg, "model.kronos_repo"),
            device=cfg_get(cfg, "model.device", "cuda:0"), max_context=int(cfg_get(cfg, "model.max_context", 512)),
            temperature=float(cfg_get(cfg, "model.temperature", 1.0)), top_p=float(cfg_get(cfg, "model.top_p", 0.9)),
            top_k=int(cfg_get(cfg, "model.top_k", 0)), batch_size=int(cfg_get(cfg, "model.batch_size", 256)),
            seed=int(cfg_get(cfg, "model.seed", 0)),
        )
    raise ValueError(f"unknown backend {name}")


def run(cfg: dict, run_id: str, backend, root: Path = Path("."), prices: pd.DataFrame | None = None,
        constituents: dict | None = None, log=print, extra_manifest: dict | None = None) -> list[pd.Timestamp]:
    """Core loop, importable for tests and for scripts/make_dummy_predictions.py. Returns dates written."""
    paths = Paths(cfg, root)
    if prices is None:
        prices = load_prices(cfg, root, include_benchmark=False)
    if constituents is None:
        constituents = load_constituents(cfg, root)

    lookback = int(cfg_get(cfg, "model.lookback"))
    horizon = int(cfg_get(cfg, "model.horizon"))
    sample_count = int(cfg_get(cfg, "model.sample_count"))
    step = int(cfg_get(cfg, "run.step"))
    max_stale = int(cfg_get(cfg, "universe.max_stale_days", 5))
    cal = trading_calendar(prices)
    dates = rebalance_dates(cal, cfg_get(cfg, "run.start"), cfg_get(cfg, "run.end"), step)

    manifest_path = paths.manifest_file(run_id)
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    manifest.update({
        "run_id": run_id,
        "backend": getattr(backend, "name", type(backend).__name__),
        "model": cfg_get(cfg, "model.name"), "tokenizer": cfg_get(cfg, "model.tokenizer"),
        "hf_revision_requested": cfg_get(cfg, "model.revision"),
        "hf_revision_resolved": getattr(backend, "resolved_revision", None),
        "lookback": lookback, "pred_len": horizon, "temperature": cfg_get(cfg, "model.temperature"),
        "top_p": cfg_get(cfg, "model.top_p"), "top_k": cfg_get(cfg, "model.top_k"), "sample_count": sample_count,
        "universe": cfg_get(cfg, "universe.indices"), "start": cfg_get(cfg, "run.start"), "end": cfg_get(cfg, "run.end"),
        "step": step, "n_rebalance_dates": int(len(dates)), "code_commit": git_commit(root),
        "started_at": manifest.get("started_at") or pd.Timestamp.utcnow().isoformat(),
        "last_updated_at": pd.Timestamp.utcnow().isoformat(), "config": cfg,
    })
    manifest.update(extra_manifest or {})
    write_manifest(manifest_path, manifest)

    written: list[pd.Timestamp] = []
    skipped_log = manifest.setdefault("skipped_by_date", {})
    for i, d in enumerate(dates):
        out = paths.prediction_file(run_id, d)
        if out.exists():
            log(f"[{i+1}/{len(dates)}] {d.date()} exists, skip")
            continue
        members = combined_universe_at(constituents, d)["ticker"].tolist()
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
        manifest["last_updated_at"] = pd.Timestamp.utcnow().isoformat()
        write_manifest(manifest_path, manifest)
        log(f"[{i+1}/{len(dates)}] {d.date()} tickers={len(batch)} skipped={len(batch.skipped)} "
            f"rows={len(df)} {time.time()-t0:.1f}s")
    manifest["completed_at"] = pd.Timestamp.utcnow().isoformat()
    write_manifest(manifest_path, manifest)
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
    p.add_argument("--start"); p.add_argument("--end")
    p.add_argument("--step", type=int); p.add_argument("--lookback", type=int); p.add_argument("--horizon", type=int)
    p.add_argument("--sample-count", type=int); p.add_argument("--batch-size", type=int)
    p.add_argument("--device"); p.add_argument("--temperature", type=float); p.add_argument("--top-p", type=float)
    p.add_argument("--model"); p.add_argument("--tokenizer"); p.add_argument("--revision"); p.add_argument("--kronos-repo")
    return p.parse_args(argv)


def main(argv=None):
    a = parse_args(argv)
    root = Path(a.root)
    cfg = cfg_override(load_config(root / a.config), {
        "run.start": a.start, "run.end": a.end, "run.step": a.step,
        "model.lookback": a.lookback, "model.horizon": a.horizon, "model.sample_count": a.sample_count,
        "model.batch_size": a.batch_size, "model.device": a.device, "model.temperature": a.temperature,
        "model.top_p": a.top_p, "model.name": a.model, "model.tokenizer": a.tokenizer,
        "model.revision": a.revision, "model.kronos_repo": a.kronos_repo,
    })
    if int(cfg_get(cfg, "run.step")) != int(cfg_get(cfg, "model.horizon")):
        print(f"warning: run.step={cfg_get(cfg, 'run.step')} != model.horizon={cfg_get(cfg, 'model.horizon')}")
    prices = load_prices(cfg, root, include_benchmark=False)
    backend = make_backend(cfg, a.backend, prices)
    written = run(cfg, a.run_id, backend, root=root, prices=prices)
    print(f"done: {len(written)} new date files in {Paths(cfg, root).predictions_dir(a.run_id)}")


if __name__ == "__main__":
    main()
