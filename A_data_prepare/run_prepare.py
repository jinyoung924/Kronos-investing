#!/usr/bin/env python
"""Stage 1 entry point: collected files (data/raw, data/universe) -> data/A_prepared/.

    python -m A_data_prepare.run_prepare [--config configs/base.yaml] [--root .] [--variant base] [--top-n-mktcap N]

Outputs (all parquet, raw prices; see each build_*.py):
    prices      date, ticker, market, open, high, low, close, volume, value, listed_shares
    calendar    date
    halts       date, ticker, is_halted
    adj_factor  date, ticker, factor          (forward cumulative F, first row 1.0)
    events      ticker, ex_date, ratio, r, applied, source, name, prev_close, base_price
    universe    date, ticker, market          (universe.variant, optional universe.top_n_mktcap)
    benchmark   date, ticker, kind, name, open, high, low, close, volume   (D-6: index levels + ETFs)
    unexplained_moves.csv   diagnostics for the Stage report
    meta.json   common rule 13
The only module of the stage that touches files. Nothing here re-parses the raw KRX JSON.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from A_data_prepare.build_adj_factor import build_adj_factor, build_events, unexplained_moves  # noqa: E402
from A_data_prepare.build_calendar import build_calendar, build_halts  # noqa: E402
from A_data_prepare.build_prices import build_prices  # noqa: E402
from A_data_prepare.build_universe import build_universe, members_per_date  # noqa: E402
from A_data_prepare.log import get_logger  # noqa: E402
from common.config import cfg_get, cfg_override, load_config, require  # noqa: E402
from common.meta import atomic_parquet, write_meta  # noqa: E402
from common.paths import Paths  # noqa: E402

STAGE = "A_data_prepare"
EVENTS_FILE = "adjustment_events.csv"


def build_benchmark(bench_raw: pd.DataFrame, index_tickers: dict) -> pd.DataFrame:
    """Benchmark levels as collected (adj_factor is 1 for all): index rows get kind=index, the rest kind=etf."""
    b = bench_raw.copy()
    b["date"] = pd.to_datetime(b["date"]).dt.normalize()
    b["ticker"] = b["ticker"].astype(str)
    idx_tickers = {str(v) for v in (index_tickers or {}).values()}
    b["kind"] = b["ticker"].map(lambda t: "index" if t in idx_tickers else "etf")
    if "name" not in b.columns:
        b["name"] = ""
    cols = ["date", "ticker", "kind", "name", "open", "high", "low", "close", "volume"]
    out = b[cols].sort_values(["ticker", "date"]).reset_index(drop=True)
    if out.duplicated(["date", "ticker"]).any():
        raise ValueError("benchmark has duplicate (date, ticker) rows")
    return out


def prepare(cfg: dict, root: Path, log=print) -> dict:
    """Read the collected files, build every Stage 1 table, write data/A_prepared/. Returns a summary dict."""
    paths = Paths(cfg, root)
    indices = list(require(cfg, "universe.indices"))
    markets = list(require(cfg, "data.markets"))
    variant = cfg_get(cfg, "universe.variant", "base") or "base"
    top_n = cfg_get(cfg, "universe.top_n_mktcap")
    out_dir = paths.prepared_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    inputs: dict[str, Path] = {}
    raw_by_index, raw_events, cons_by_index = {}, [], {}
    for idx in indices:
        f = paths.price_file(idx)
        raw_by_index[idx] = pd.read_parquet(f)
        inputs[f"prices_{idx}"] = f
        e = f.parent / EVENTS_FILE
        raw_events.append(pd.read_csv(e, dtype={"ticker": str}))
        inputs[f"events_{idx}"] = e
        c = paths.constituents_file(idx, variant)
        cons_by_index[idx] = pd.read_parquet(c)
        inputs[f"constituents_{idx}"] = c
    bench_file = paths.price_file("benchmark")
    inputs["benchmark"] = bench_file

    t0 = time.time()
    prices = build_prices(raw_by_index, markets=markets)
    log(f"prices: {len(prices):,} rows, {prices['ticker'].nunique()} tickers, {prices['date'].min().date()} .. {prices['date'].max().date()}, markets {sorted(prices['market'].unique())}")
    calendar = build_calendar(prices)
    raw_all = pd.concat([raw_by_index[i] for i in indices], ignore_index=True).sort_values(["ticker", "date"]).reset_index(drop=True)
    raw_all["date"] = pd.to_datetime(raw_all["date"]).dt.normalize(); raw_all["ticker"] = raw_all["ticker"].astype(str)
    assert raw_all[["date", "ticker"]].equals(prices[["date", "ticker"]])
    halts = build_halts(prices, raw_halted=raw_all["halted"] if "halted" in raw_all.columns else None)
    events = build_events(pd.concat(raw_events, ignore_index=True))
    adj = build_adj_factor(prices, events)
    universe = build_universe(cons_by_index, prices, top_n_mktcap=top_n)
    bench = build_benchmark(pd.read_parquet(bench_file), cfg_get(cfg, "data.index_tickers") or {}) if bench_file.exists() else None
    moves = unexplained_moves(prices, adj, halts, max_move=float(require(cfg, "data.max_daily_move")),
                              delist_window=int(require(cfg, "data.delist_window_days")))
    log(f"calendar {len(calendar)} days; halted rows {int(halts['is_halted'].sum()):,}; events {len(events)} (applied {int(events['applied'].sum())}); "
        f"universe[{variant}{'' if top_n is None else f', top {top_n}'}] {universe['date'].nunique()} snapshots, members/day "
        f"{universe.groupby('date').size().min()}..{universe.groupby('date').size().max()}; "
        f"big moves: {moves['category'].value_counts().to_dict()}")

    tables = {"prices": prices, "calendar": calendar, "halts": halts, "adj_factor": adj, "events": events, "universe": universe}
    if bench is not None:
        tables["benchmark"] = bench
    for name, df in tables.items():
        atomic_parquet(df, paths.prepared_path(name))
    moves.to_csv(out_dir / "unexplained_moves.csv", index=False)
    members_per_date(universe).to_csv(out_dir / "universe_members_by_date.csv", index=False)
    summary = {
        "stage": STAGE, "run_id": None, "strategy": None, "engine": None,
        "universe_variant": variant, "top_n_mktcap": top_n, "markets": markets, "indices": indices,
        "n_rows": {k: int(len(v)) for k, v in tables.items()},
        "n_tickers": int(prices["ticker"].nunique()),
        "calendar": {"first": str(calendar["date"].min().date()), "last": str(calendar["date"].max().date()), "n_days": int(len(calendar))},
        "events_applied": int(events["applied"].sum()),
        "universe_members_per_day": {"min": int(universe.groupby("date").size().min()), "max": int(universe.groupby("date").size().max())},
        "big_moves": moves["category"].value_counts().to_dict(),
        "elapsed_sec": round(time.time() - t0, 1),
    }
    write_meta(out_dir, cfg, inputs=inputs, extra=summary, root=root)
    log(f"wrote {sorted(f'{k}.parquet' for k in tables)} + unexplained_moves.csv, universe_members_by_date.csv, meta.json -> {out_dir} ({summary['elapsed_sec']}s)")
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default="configs/base.yaml")
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--variant", help="override universe.variant (base | liq5 | clean)")
    p.add_argument("--top-n-mktcap", type=int, help="override universe.top_n_mktcap")
    a = p.parse_args(argv)
    root = Path(a.root)
    cfg = cfg_override(load_config(root / a.config), {"universe.variant": a.variant, "universe.top_n_mktcap": a.top_n_mktcap})
    log = get_logger("data_prepare", Paths(cfg, root).logs_dir())   # D-9: logger name kept
    log.info(f"===== prepare (variant={cfg_get(cfg, 'universe.variant')}, top_n_mktcap={cfg_get(cfg, 'universe.top_n_mktcap')}) =====")
    prepare(cfg, root, log=log.info)


if __name__ == "__main__":
    main()
