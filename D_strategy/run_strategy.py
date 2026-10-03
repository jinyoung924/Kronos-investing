#!/usr/bin/env python
"""D_strategy entry point: data/C_signals/{run_id}/signals.parquet -> data/D_weights/{run_id}/{strategy}.parquet.

    python -m D_strategy.run_strategy --run-id fake_dummy_base --strategy all|a,b [--config configs/base.yaml] [--root .]

Per strategy: (1) the run_id's manifest profile must equal the strategy's profile, else a clear error;
(2) rebalance dates follow the schedule: weekly = rebalance_dates(period.start, period.end, H) on the trading
calendar, daily = every as_of date in the signals; (3) reset(), then weights() per date ascending, each
output checked against the contract; stored long as (as_of_date, ticker, weight) with zero weights dropped
and holds kept as NaN, plus {strategy}.meta.json. The only module of the stage that touches files.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.calendar import rebalance_dates  # noqa: E402
from common.config import cfg_get, load_config, require  # noqa: E402
from common.meta import atomic_parquet, write_meta  # noqa: E402
from common.paths import Paths  # noqa: E402
from common.schema import validate_signals, validate_weights  # noqa: E402
from D_strategy.base import Strategy, check_weights  # noqa: E402
from D_strategy.registry import get_strategy, resolve  # noqa: E402

STAGE = "D_strategy"


def check_profile(strategy: Strategy, manifest: dict, run_id: str) -> None:
    run_profile = manifest.get("profile")
    if run_profile != strategy.profile:
        raise ValueError(f"strategy {strategy.name!r} uses profile {strategy.profile!r} but run_id {run_id!r} was produced with "
                         f"profile {run_profile!r}; pick a run_id of the right profile (configs project.run_ids)")


def schedule_dates(strategy: Strategy, signals: pd.DataFrame, calendar: pd.DatetimeIndex, cfg: dict, horizon: int) -> pd.DatetimeIndex:
    """weekly: every H-th trading day in the period (same as B_model_infer); daily: every as_of date with signals."""
    have = pd.DatetimeIndex(sorted(signals["as_of_date"].unique()))
    if strategy.schedule == "daily":
        return have
    dates = rebalance_dates(calendar, require(cfg, "period.start"), require(cfg, "period.end"), int(horizon))
    missing = dates.difference(have)
    if len(missing):
        raise ValueError(f"{strategy.name}: {len(missing)} weekly rebalance dates have no signals, e.g. {[d.date() for d in missing[:3]]}")
    return dates


def run_one(strategy: Strategy, signals: pd.DataFrame, dates: pd.DatetimeIndex) -> tuple[pd.DataFrame, dict]:
    """Pure loop: reset, weights per date, contract check. Returns (long weights, diagnostics)."""
    strategy.reset()
    prev_w = pd.Series(dtype=float, name="weight")
    rows, n_hold, turnover = [], [], []
    by_date = {d: g.set_index("ticker") for d, g in signals.groupby("as_of_date", sort=True)}
    for d in dates:
        sig = by_date.get(d)
        if sig is None or sig.empty:
            raise ValueError(f"{strategy.name}: no signals on {pd.Timestamp(d).date()}")
        snapshot = sig.copy(deep=True)
        w = check_weights(strategy.weights(d, sig, prev_w.copy()), sig, strategy.name, d)
        if not sig.equals(snapshot):
            raise RuntimeError(f"{strategy.name} modified its input signals on {pd.Timestamp(d).date()}")
        held = w.dropna()
        held = held[held > 0]
        n_hold.append(int(len(held)) + int(w.isna().sum()))
        # turnover between consecutive target weights (holds inherit the previous target)
        eff = w.copy()
        eff[eff.isna()] = prev_w.reindex(eff.index[eff.isna()]).fillna(0.0)
        allt = eff.index.union(prev_w.index)
        turnover.append(float((eff.reindex(allt).fillna(0.0) - prev_w.reindex(allt).fillna(0.0)).abs().sum() / 2))
        keep = w[w.isna() | (w > 0)]
        rows.append(pd.DataFrame({"as_of_date": pd.Timestamp(d), "ticker": keep.index.astype(str), "weight": keep.to_numpy(dtype=float)}))
        prev_w = eff
    out = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["as_of_date", "ticker", "weight"])
    out = validate_weights(out)
    diag = {
        "n_dates": int(len(dates)), "first_date": str(pd.Timestamp(dates[0]).date()), "last_date": str(pd.Timestamp(dates[-1]).date()),
        "holdings_per_date": {"min": int(min(n_hold)), "mean": round(float(np.mean(n_hold)), 1), "max": int(max(n_hold))},
        "turnover_per_rebalance": {"mean": round(float(np.mean(turnover[1:])), 4) if len(turnover) > 1 else None,
                                   "max": round(float(max(turnover[1:])), 4) if len(turnover) > 1 else None},
        "n_hold_rows": int(out["weight"].isna().sum()), "n_rows": int(len(out)),
    }
    return out, diag


def run_strategies(cfg: dict, run_id: str, names: list[str], root: Path, log=print) -> dict:
    paths = Paths(cfg, root)
    mpath = paths.manifest_file(run_id)
    if not mpath.exists():
        raise FileNotFoundError(f"missing {mpath}")
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    spath = paths.signals_path(run_id)
    if not spath.exists():
        raise FileNotFoundError(f"missing {spath}; run C_signal.run_signal first")
    signals = validate_signals(pd.read_parquet(spath))
    calendar = pd.DatetimeIndex(pd.read_parquet(paths.prepared_path("calendar"))["date"])
    horizon = int(manifest["pred_len"])
    results = {}
    for name in names:
        t0 = time.time()
        strat = get_strategy(name, cfg)
        check_profile(strat, manifest, run_id)
        dates = schedule_dates(strat, signals, calendar, cfg, horizon)
        weights, diag = run_one(strat, signals, dates)
        out = paths.weights_path(run_id, name)
        atomic_parquet(weights, out)
        extra = {"stage": STAGE, "run_id": run_id, "strategy": name, "engine": None, "profile": strat.profile,
                 "schedule": strat.schedule, "params": strat.params, "H": horizon, **diag, "elapsed_sec": round(time.time() - t0, 1)}
        write_meta(out.parent, cfg, inputs={"signals": spath, "manifest": mpath, "calendar": paths.prepared_path("calendar")},
                   extra=extra, root=root, filename=paths.weights_meta_path(run_id, name).name)
        results[name] = diag
        log(f"{run_id}/{name}: {diag['n_dates']} dates, holdings/date {diag['holdings_per_date']}, "
            f"turnover/rebalance mean {diag['turnover_per_rebalance']['mean']} -> {out}")
    return results


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-id", required=True)
    p.add_argument("--strategy", required=True, help="comma-separated names or 'all'")
    p.add_argument("--config", default="configs/base.yaml")
    p.add_argument("--root", default=str(ROOT))
    a = p.parse_args(argv)
    root = Path(a.root)
    cfg = load_config(root / a.config)
    run_strategies(cfg, a.run_id, resolve(a.strategy), root)


if __name__ == "__main__":
    main()
