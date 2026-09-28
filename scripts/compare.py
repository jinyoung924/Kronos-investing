#!/usr/bin/env python
"""Compare every strategy under results/{run_id}/: metrics table + cumulative NAV chart.

    python scripts/compare.py --run-id <id> [--config configs/base.yaml]

Writes results/{run_id}/compare.csv, compare.md, compare.png. The Deflated Sharpe column
`dsr_cross` re-computes DSR using the number of strategies actually present as the trial count
and the observed cross-strategy variance of the per-period Sharpe ratio.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

import _bootstrap  # noqa: F401
from _bootstrap import ROOT

from backtest.metrics import deflated_sharpe_ratio
from backtest.report import compare_table, plot_compare
from common.config import load_config
from common.paths import Paths


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-id", required=True)
    p.add_argument("--config", default="configs/base.yaml")
    p.add_argument("--root", default=str(ROOT))
    a = p.parse_args(argv)
    root = Path(a.root)
    cfg = load_config(root / a.config)
    base = Paths(cfg, root).results_dir(a.run_id)

    metrics, navs, rets = {}, {}, {}
    for d in sorted(x for x in base.iterdir() if x.is_dir()):
        mf, nf = d / "metrics.json", d / "nav.csv"
        if not (mf.exists() and nf.exists()):
            continue
        metrics[d.name] = json.loads(mf.read_text())
        frame = pd.read_csv(nf, index_col="date", parse_dates=True)
        navs[d.name], rets[d.name] = frame["nav"], frame["ret"].iloc[1:]
    if not metrics:
        raise SystemExit(f"no strategy results under {base}")

    table = compare_table(metrics)
    # cross-strategy deflated Sharpe
    per_period_sr = {k: float(r.mean() / r.std(ddof=1)) if r.std(ddof=1) > 0 else np.nan for k, r in rets.items()}
    sr_var = float(np.nanvar(list(per_period_sr.values()), ddof=1)) if len(per_period_sr) > 1 else None
    table["dsr_cross"] = [deflated_sharpe_ratio(rets[k], n_trials=len(rets), sr_variance=sr_var)["dsr"] for k in table.index]
    # prediction metrics (identical for every Kronos strategy; show once per run)
    pred_rows = {}
    for d in sorted(x for x in base.iterdir() if x.is_dir()):
        pf = d / "prediction_metrics.json"
        if pf.exists():
            pm = json.loads(pf.read_text())
            for target in ("ret_cc", "ret_oo"):
                if target in pm:
                    pred_rows[target] = {
                        "rank_ic_mean": pm[target]["rank_ic"].get("mean"), "icir": pm[target]["rank_ic"].get("icir"),
                        "ic_t_stat": pm[target]["rank_ic"].get("t_stat"),
                        "q5_q1_spread": (pm[target].get("quantiles") or {}).get("spread_mean"),
                        "direction_acc_p_up": (pm[target].get("direction") or {}).get("acc_p_up"),
                        "calib_spearman": (pm[target].get("calibration") or {}).get("spearman_std_abs_err"),
                        "ic_momentum20": ((pm[target].get("naive_comparison") or {}).get("momentum20") or {}).get("mean"),
                        "ic_mean_reversion5": ((pm[target].get("naive_comparison") or {}).get("mean_reversion5") or {}).get("mean"),
                    }
            for key, entry in (pm.get("by_index") or {}).items():
                for target in ("ret_cc", "ret_oo"):
                    if target in entry:
                        pred_rows[f"{target}@{key}"] = {
                            "rank_ic_mean": entry[target]["rank_ic"].get("mean"), "icir": entry[target]["rank_ic"].get("icir"),
                            "ic_t_stat": entry[target]["rank_ic"].get("t_stat"),
                            "q5_q1_spread": (entry[target].get("quantiles") or {}).get("spread_mean"),
                            "direction_acc_p_up": (entry[target].get("direction") or {}).get("acc_p_up"),
                            "calib_spearman": None,
                            "ic_momentum20": ((entry[target].get("naive_comparison") or {}).get("momentum20") or {}).get("mean"),
                            "ic_mean_reversion5": ((entry[target].get("naive_comparison") or {}).get("mean_reversion5") or {}).get("mean"),
                        }
            break

    table.to_csv(base / "compare.csv", float_format="%.4f")
    md = ["# Strategy comparison: run_id=" + a.run_id, "", table.round(4).to_markdown(), ""]
    if pred_rows:
        md += ["## Prediction quality (strategy-independent)", "", pd.DataFrame(pred_rows).T.round(4).to_markdown(), ""]
    (base / "compare.md").write_text("\n".join(md), encoding="utf-8")
    plot_compare(navs, base / "compare.png", title=f"{a.run_id}: cumulative NAV (net)")
    pd.set_option("display.width", 200)
    print(table.round(3).to_string())
    if pred_rows:
        print("\nprediction quality:\n" + pd.DataFrame(pred_rows).T.round(4).to_string())
    print(f"\nwritten: {base/'compare.csv'}, {base/'compare.md'}, {base/'compare.png'}")


if __name__ == "__main__":
    main()
