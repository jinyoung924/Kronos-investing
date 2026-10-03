#!/usr/bin/env python
"""Orchestrator for the KRX dataset.   python -m A_data_prepare.run [step ...]

steps (no args = all, in this order):
  probe      one call per service on a known day: approvals, schema, code format   (network, <=5 calls)
  fetch      cache every weekday of every endpoint under data/krx_raw/<snapshot>/  (network, resumable)
  build      raw JSON -> data/raw/{kospi,kosdaq}/prices.parquet + constituents (base + variants) + validation
  benchmark  index levels + ETFs -> data/raw/benchmark/prices.parquet
  sanity     cross-file gates (adj_factor vs FLUC_RT, index reconstruction, universe reconciliation ...)
  manifest   sha256 manifest of data/raw, data/universe and the snapshot cache -> data/MANIFEST.json

Options: --dry-run (fetch: only count calls)  --refetch-empty  --force (fetch: ignore cache)
         --config configs/base.yaml  --root <repo>
The vintage is frozen by `krx.snapshot`: build/sanity never touch the network, and every output
records the snapshot, config hash and code commit in data/raw/BUILD_METADATA.json.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.config import cfg_get, config_hash, load_config  # noqa: E402
from common.meta import git_commit_hash as git_commit  # noqa: E402
from common.paths import Paths  # noqa: E402
from A_data_prepare import manifest as manifest_mod  # noqa: E402
from A_data_prepare import probe as probe_mod  # noqa: E402
from A_data_prepare import sanity as sanity_mod  # noqa: E402
from A_data_prepare.benchmark import etf_prices, index_prices  # noqa: E402
from A_data_prepare.env import load_api_key  # noqa: E402
from A_data_prepare.krx_client import KRXClient, KRXError  # noqa: E402
from A_data_prepare.log import get_logger  # noqa: E402
from A_data_prepare.transform import compute_adj_factor, filter_securities, rows_to_frame, stock_prices, to_project_format  # noqa: E402
from A_data_prepare.universe import build_constituents  # noqa: E402
from A_data_prepare.validate import validate_dataset  # noqa: E402

STOCK_API = {"KOSPI": "stk_bydd_trd", "KOSDAQ": "ksq_bydd_trd"}
INDEX_KEY = {"KOSPI": "kospi", "KOSDAQ": "kosdaq"}       # exchange -> universe index key / data/raw/{key}
STEPS = ("probe", "fetch", "build", "benchmark", "sanity", "manifest")


# ----------------------------------------------------------------------------------------------
# config helpers
# ----------------------------------------------------------------------------------------------
def krx_cfg(cfg: dict) -> dict:
    k = dict(cfg_get(cfg, "krx", {}) or {})
    k.setdefault("fetch_start", "2022-10-01")
    k.setdefault("fetch_end", "2025-07-15")
    k.setdefault("markets", ["KOSPI"])
    k.setdefault("index_series", {"kospi": {"api": "kospi_dd_trd", "name": "코스피", "ticker": "KOSPI"}})
    k.setdefault("benchmark_etfs", [])
    k.setdefault("min_interval_sec", 0.15)
    k.setdefault("snapshot", None)
    return k


def _apis(k: dict) -> list[str]:
    apis = [STOCK_API[m] for m in k["markets"]] + [spec["api"] for spec in k["index_series"].values()]
    if k["benchmark_etfs"]:
        apis.append("etf_bydd_trd")
    return apis


def _per_index(value, index_key: str, default=0.0) -> float:
    if isinstance(value, dict):
        return float(value.get(index_key, default))
    return float(value if value is not None else default)


def universe_variants(cfg: dict) -> dict[str, dict]:
    """{variant: effective universe settings} - 'base' is always present."""
    uni = dict(cfg_get(cfg, "universe", {}) or {})
    variants = dict(uni.get("variants") or {})
    variants.setdefault("base", {})
    out = {}
    for name, override in variants.items():
        eff = {k: v for k, v in uni.items() if k not in ("variants", "variant")}
        eff.update(override or {})
        out[name] = eff
    return out


def base_profile(cfg: dict) -> dict:
    """Inference profile the universe history filter and the validation summary refer to (infer.default_profile)."""
    name = cfg_get(cfg, "infer.default_profile", "base")
    return dict(cfg_get(cfg, f"infer.profiles.{name}", {}) or {})


def _client(cfg: dict, root: Path, log, offline: bool = False) -> KRXClient:
    k = krx_cfg(cfg)
    return KRXClient(api_key="offline" if offline else load_api_key(root), cache_dir=Paths(cfg, root).krx_cache_dir(),
                     min_interval=float(k["min_interval_sec"]), log=log.info)


# ----------------------------------------------------------------------------------------------
# steps
# ----------------------------------------------------------------------------------------------
def step_probe(cfg: dict, root: Path, log, **_) -> dict:
    client = _client(cfg, root, log)
    return probe_mod.main(client, _apis(krx_cfg(cfg)), log=log.info)


def step_fetch(cfg: dict, root: Path, log, dry_run: bool = False, refetch_empty: bool = False, force: bool = False, **_) -> None:
    k = krx_cfg(cfg)
    apis = _apis(k)
    days = pd.bdate_range(k["fetch_start"], k["fetch_end"])
    if dry_run:
        client = _client(cfg, root, log, offline=True)
        need = {a: sum(client.cached(a, d.strftime("%Y%m%d")) is None for d in days) for a in apis}
        log.info(f"snapshot={k['snapshot']} weekdays={len(days)} calls needed={need} total={sum(need.values())} (quota 10,000/day)")
        return
    client = _client(cfg, root, log)
    if force:
        log.warning("--force: existing cache files are ignored and re-fetched (this changes the vintage!)")
    for a in apis:
        try:
            if force:
                for d in days:
                    p = client.cache_path(a, d.strftime("%Y%m%d"))
                    if p.exists():
                        p.unlink()
            client.fetch_range(a, k["fetch_start"], k["fetch_end"], refetch_empty=refetch_empty)
        except KRXError as e:
            if "HTTP 401" in str(e) or "HTTP 403" in str(e):
                log.warning(f"{a}: {e} -> apply for this service on openapi.krx.co.kr (서비스 이용신청) and re-run; skipping")
                if a in STOCK_API.values():
                    raise
                continue
            raise


def _load_cached(cfg: dict, root: Path, api_id: str, log) -> pd.DataFrame:
    k = krx_cfg(cfg)
    client = _client(cfg, root, log, offline=True)
    rows, missing = {}, []
    for d in pd.bdate_range(k["fetch_start"], k["fetch_end"]):
        s = d.strftime("%Y%m%d")
        r = client.cached(api_id, s)
        (missing.append(s) if r is None else rows.__setitem__(s, r))
    if missing:
        raise FileNotFoundError(f"{api_id}: {len(missing)} weekdays not cached under {client.cache_dir} (first {missing[:3]}); run fetch")
    return rows_to_frame(rows)


def build_market(cfg: dict, root: Path, market: str, log) -> dict:
    """One exchange -> prices.parquet + constituents for every universe variant + validation.json.
    The same filter rules are applied to every exchange; variants only override listed keys."""
    paths = Paths(cfg, root)
    key = INDEX_KEY[market]
    variants = universe_variants(cfg)
    base = variants["base"]
    raw = _load_cached(cfg, root, STOCK_API[market], log)
    prices0 = stock_prices(raw)
    log.info(f"[{key}] raw rows={len(prices0)} tickers={prices0['ticker'].nunique()} days={prices0['date'].nunique()}")

    # security filter of the BASE variant defines the price file; variants that drop more names only
    # affect constituents (prices of dropped names stay available for labels / benchmarks)
    prices, drop_stats = filter_securities(prices0, bool(base.get("common_stock_only", True)),
                                           base.get("exclude_name_patterns") or [], base.get("exclude_sect_patterns") or [])
    log.info(f"[{key}] security filter (base): {drop_stats} -> tickers={prices['ticker'].nunique()}")
    prices, events = compute_adj_factor(prices)
    log.info(f"[{key}] reference-price events: {len(events)} (applied {int(events['applied'].sum()) if len(events) else 0})")
    out = to_project_format(prices)
    f = paths.price_file(key)
    f.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(f, index=False)
    events.to_csv(f.parent / "adjustment_events.csv", index=False)

    run_start = pd.Timestamp(cfg_get(cfg, "period.start")) - pd.Timedelta(days=int(base.get("universe_start_buffer_days", 30)))
    dates = pd.DatetimeIndex(sorted(out["date"].unique()))
    dates = dates[dates >= run_start]
    summary = {}
    for vname, eff in variants.items():
        pv, vstats = filter_securities(prices, bool(eff.get("common_stock_only", True)),
                                       eff.get("exclude_name_patterns") or [], eff.get("exclude_sect_patterns") or [])
        cons, cov = build_constituents(
            pv, dates, min_history_rows=int(eff.get("min_history_rows", base_profile(cfg).get("lookback", 400))),
            liquidity_window=int(eff.get("liquidity_window", 20)),
            min_avg_trdval=_per_index(eff.get("min_avg_trdval", 0.0), key),
            exclude_halted=bool(eff.get("exclude_halted", True)))
        cf = paths.constituents_file(key, vname)
        cf.parent.mkdir(parents=True, exist_ok=True)
        cons.to_parquet(cf, index=False)
        cov.to_csv(cf.with_name(cf.stem.replace("constituents", "coverage") + ".csv"), index=False)
        log.info(f"[{key}/{vname}] universe: {cons['date'].nunique()} snapshots, members/day "
                 f"{cov['n_members'].min()}..{cov['n_members'].max()} (listed {cov['n_listed'].min()}..{cov['n_listed'].max()})"
                 + (f" extra filter: {vstats}" if vname != "base" else ""))
        s, big = validate_dataset(out, cons, events, cfg_get(cfg, "period.start"), cfg_get(cfg, "period.end"),
                                  int(base_profile(cfg)["lookback"]), int(base_profile(cfg)["pred_len"]))
        s["exchange"], s["variant"], s["security_filter"] = market, vname, (drop_stats if vname == "base" else vstats)
        summary[vname] = s
        if vname == "base":
            (f.parent / "validation.json").write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
            big.to_csv(f.parent / "residual_big_moves.csv", index=False)
            log.info(f"[{key}] delisted during run: {s['delisted']['n_during_run']} (members: {s['delisted']['n_that_were_universe_members']}), "
                     f"residual big moves: {s['residual_big_moves_after_adjustment']}, hard failures: {s['hard_failures']}")
        if s["hard_failures"]:
            raise SystemExit(f"[{key}/{vname}] validation failed: {s['hard_failures']}")
    return summary


def step_build(cfg: dict, root: Path, log, **_) -> dict:
    k = krx_cfg(cfg)
    summaries = {INDEX_KEY[m]: build_market(cfg, root, m, log) for m in k["markets"]}
    paths = Paths(cfg, root)
    if len(summaries) > 1:
        frames = {key: pd.read_parquet(paths.price_file(key), columns=["date", "ticker"]) for key in summaries}
        allp = pd.concat([f.assign(index=key) for key, f in frames.items()])
        both = allp.groupby("ticker")["index"].nunique()
        log.info(f"tickers present on both exchanges (transfers): {int((both > 1).sum())}, "
                 f"same-day duplicates: {int(allp.duplicated(['date', 'ticker']).sum())}")
    _write_build_metadata(cfg, root, log, {"build": summaries})
    return summaries


def step_benchmark(cfg: dict, root: Path, log, **_) -> None:
    k = krx_cfg(cfg)
    paths = Paths(cfg, root)
    bench = []
    for key, spec in k["index_series"].items():
        try:
            bench.append(index_prices(_load_cached(cfg, root, spec["api"], log), spec["name"], spec["ticker"]))
        except FileNotFoundError as e:
            log.warning(f"benchmark index {key} skipped: {e}")
    if k["benchmark_etfs"]:
        try:
            bench.append(etf_prices(_load_cached(cfg, root, "etf_bydd_trd", log), [str(c) for c in k["benchmark_etfs"]]))
        except FileNotFoundError as e:
            log.warning(f"benchmark ETF skipped: {e}")
    bench = [b for b in bench if not b.empty]
    if not bench:
        log.warning("no benchmark series written: index_buy_hold will be skipped; equal_weight remains the benchmark")
        return
    bench_df = pd.concat(bench, ignore_index=True)
    bf = paths.price_file("benchmark")
    bf.parent.mkdir(parents=True, exist_ok=True)
    bench_df.to_parquet(bf, index=False)
    log.info(f"wrote {bf}: {bench_df['ticker'].unique().tolist()}")
    _write_build_metadata(cfg, root, log, {"benchmark": {"tickers": bench_df["ticker"].unique().tolist(), "n_rows": int(len(bench_df))}})


def step_sanity(cfg: dict, root: Path, log, **_) -> dict:
    return sanity_mod.run(cfg, root, log=log.info)


def step_manifest(cfg: dict, root: Path, log, **_) -> None:
    manifest_mod.write(root / "data", manifest_mod.default_subdirs(cfg), cfg_get(cfg, "krx.snapshot"), log=log.info)


def _write_build_metadata(cfg: dict, root: Path, log, extra: dict) -> None:
    paths = Paths(cfg, root)
    mp = paths.raw_dir() / "BUILD_METADATA.json"
    meta = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {}
    meta.update({
        "snapshot": cfg_get(cfg, "krx.snapshot"), "config_hash": config_hash(cfg), "code_commit": git_commit(root),
        "built_at_utc": pd.Timestamp.utcnow().isoformat(), "fetch_range": [krx_cfg(cfg)["fetch_start"], krx_cfg(cfg)["fetch_end"]],
        "run_range": [cfg_get(cfg, "period.start"), cfg_get(cfg, "period.end")], "indices": cfg_get(cfg, "universe.indices"),
        "universe_variants": universe_variants(cfg),
    })
    meta.update(extra)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps(meta, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    log.info(f"build metadata -> {mp}")


STEP_FN = {"probe": step_probe, "fetch": step_fetch, "build": step_build, "benchmark": step_benchmark,
           "sanity": step_sanity, "manifest": step_manifest}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("steps", nargs="*", help=f"any of {STEPS}; none = all")
    p.add_argument("--config", default="configs/base.yaml")
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--refetch-empty", action="store_true")
    p.add_argument("--force", action="store_true")
    a = p.parse_args(argv)
    root = Path(a.root)
    cfg = load_config(root / a.config)
    log = get_logger("data_prepare", Paths(cfg, root).logs_dir())
    steps = a.steps or list(STEPS)
    unknown = [s for s in steps if s not in STEP_FN]
    if unknown:
        raise SystemExit(f"unknown steps {unknown}; choose from {STEPS}")
    log.info(f"snapshot={cfg_get(cfg, 'krx.snapshot')} config={a.config} hash={config_hash(cfg)} steps={steps}")
    for s in steps:
        t0 = time.time()
        log.info(f"===== {s} =====")
        STEP_FN[s](cfg, root, log, dry_run=a.dry_run, refetch_empty=a.refetch_empty, force=a.force)
        log.info(f"===== {s} done in {time.time() - t0:.0f}s =====")
        if s == "fetch" and a.dry_run:
            return


if __name__ == "__main__":
    main()
