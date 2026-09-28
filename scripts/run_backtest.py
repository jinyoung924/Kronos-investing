#!/usr/bin/env python
"""Backtest one strategy on one prediction run.

    python scripts/run_backtest.py --run-id <id> --strategy topk --config configs/base.yaml
    python scripts/run_backtest.py --run-id <id> --strategy all          # every registered strategy
    python scripts/run_backtest.py --run-id <id> --strategy all --indices kosdaq   # KOSDAQ-only universe

Writes results/{run_id}/{strategy}/{nav.csv, weights.parquet, metrics.json, config.yaml, nav.png}
and, for prediction-based strategies, prediction_metrics.json + IC / quantile charts.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from _bootstrap import ROOT

from backtest.report import save_prediction_report, save_strategy_results
from backtest.runner import evaluate, load_context
from backtest.strategies import BENCHMARK_STRATEGIES, KRONOS_STRATEGIES, available
from common.config import cfg_override, load_config
from common.paths import Paths


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-id", required=True)
    p.add_argument("--strategy", default="all", help=f"one of {available()} or 'all' / 'kronos' / 'benchmarks'")
    p.add_argument("--config", default="configs/base.yaml")
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--start"); p.add_argument("--end")
    p.add_argument("--indices", default=None, help="comma-separated index keys to restrict the universe, e.g. kospi or kosdaq; "
                                                    "results go to results/{run_id}/{strategy}@{indices}/")
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="dotted config override, e.g. strategies.topk.k=10")
    a = p.parse_args(argv)

    root = Path(a.root)
    overrides = {"run.start": a.start, "run.end": a.end}
    for kv in a.set:
        k, v = kv.split("=", 1)
        try:
            overrides[k] = json.loads(v)      # numbers, lists, true/false/null
        except json.JSONDecodeError:
            overrides[k] = v                  # plain string
    cfg = cfg_override(load_config(root / a.config), overrides)

    if a.strategy == "all":
        names = list(KRONOS_STRATEGIES) + list(BENCHMARK_STRATEGIES)
    elif a.strategy == "kronos":
        names = list(KRONOS_STRATEGIES)
    elif a.strategy == "benchmarks":
        names = list(BENCHMARK_STRATEGIES)
    else:
        names = [a.strategy]

    need_preds = any(n in KRONOS_STRATEGIES for n in names)
    indices = [x.strip() for x in a.indices.split(",")] if a.indices else None
    ctx = load_context(cfg, a.run_id, root, need_predictions=need_preds, indices=indices)
    suffix = "@" + "+".join(indices) if indices else ""
    print(f"run_id={a.run_id} dates={len(ctx.dates)} ({ctx.dates[0].date()} .. {ctx.dates[-1].date()}) "
          f"signals={len(ctx.signals)} predictions={'yes' if ctx.preds is not None else 'no'}")
    paths = Paths(cfg, root)
    for name in names:
        try:
            out = evaluate(ctx, name)
        except KeyError as e:
            print(f"{name:15s} skipped: {e}")
            continue
        out_dir = paths.results_dir(a.run_id, name + suffix)
        bench_nav = out["bench_result"].nav if out["bench_result"] is not None else None
        save_strategy_results(out["result"], out["metrics"], cfg, out_dir, bench_nav)
        if out["pred_report"] is not None:
            save_prediction_report(out["pred_report"], out_dir)
        m = out["metrics"]
        print(f"{name:15s} total={m['total_return']:+.3f} cagr={m['cagr']:+.3f} sharpe={m['sharpe']:.2f} "
              f"mdd={m['mdd']:.3f} turnover/yr={m['trading']['annual_turnover']:.1f} -> {out_dir}")


if __name__ == "__main__":
    main()
