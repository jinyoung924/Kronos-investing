"""Stage 5 user-check figures (docs/spec.md Stage 5 "사용자 확인") from data/F_metrics/ and data/E_backtest/:
  1. mean realised label by exp_ret quantile for fake_oracle_base and fake_dummy_base (bars)
  2. cumulative NAV of the benchmark strategies (paper costs) vs KOSPI / KOSDAQ index levels
Prints the IC summary and the benchmark strategy table for the report.   python scripts/probes/stage5_figures.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from common.config import load_config  # noqa: E402
from common.paths import Paths  # noqa: E402

OUT = ROOT / "docs/stage_reports/figures"
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"
PAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]


def style(ax, title):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=8, length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_title(title, loc="left", fontsize=10, color=INK, fontweight="bold", pad=8)


def main() -> int:
    cfg = load_config(ROOT / "configs/base.yaml")
    paths = Paths(cfg, ROOT)
    plt.rcParams.update({"font.family": ["AppleGothic", "Malgun Gothic", "NanumGothic", "DejaVu Sans"], "axes.unicode_minus": False})
    OUT.mkdir(parents=True, exist_ok=True)
    runs = ["fake_oracle_base", "fake_dummy_base"]
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.4), facecolor=SURFACE)
    rows = []
    for ax, run_id in zip(axes, runs):
        s = json.loads((paths.metrics_dir(run_id) / "signal_metrics.json").read_text())
        q = s["quantiles"]["exp_ret"]["mean_by_quantile"]
        style(ax, f"{run_id}: exp_ret 분위별 평균 실현수익률 (H {s['H']}, {s['ic']['exp_ret']['n_dates']}일)")
        ax.bar(list(q.keys()), [v for v in q.values()], color=PAL[0], width=0.6)
        for i, (k, v) in enumerate(q.items()):
            ax.annotate(f"{v:+.2%}", (i, v), xytext=(0, 4 if v >= 0 else -10), textcoords="offset points", ha="center", fontsize=8, color=INK2)
        ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
        ax.axhline(0, color=GRID, lw=1)
        for col in ("exp_ret", "exp_ret_mean"):
            ic = s["ic"][col]
            rows.append({"run_id": run_id, "signal": col, "IC mean": round(ic["mean"], 3), "IC std": round(ic["std"], 3), "ICIR": round(ic["icir"], 2), "t": round(ic["t"], 1), "n dates": ic["n_dates"]})
        for col, v in s["naive"].items():
            if "mean" in v:
                rows.append({"run_id": run_id, "signal": f"naive {col}", "IC mean": round(v["mean"], 3), "IC std": round(v["std"], 3), "ICIR": round(v["icir"], 2), "t": round(v["t"], 1), "n dates": v["n_dates"]})
        print(f"{run_id}: hit rate {s['hit_rate']['hit_rate']:.3f}, calibration {s['calibration']['spearman']:+.3f}, Q5-Q1 spread {s['quantiles']['exp_ret']['mean_spread']:+.4f} (t {s['quantiles']['exp_ret']['t']:.1f}), "
              f"regimes {json.dumps({k: {kk: round(vv['mean'], 3) for kk, vv in v.items()} for k, v in s['ic_by_regime']['exp_ret'].items()})}")
    fig.tight_layout(); f1 = OUT / "stage5_quantile_returns.png"; fig.savefig(f1, dpi=130, facecolor=SURFACE)
    print("\nIC summary"); print(pd.DataFrame(rows).to_string(index=False))

    pm = pd.read_csv(paths.metrics_dir("fake_dummy_base") / "portfolio_metrics.csv")
    cols = ["strategy", "cagr", "ann_vol", "sharpe", "sortino", "max_drawdown", "aer", "beta", "ir", "annual_turnover", "mean_holdings", "cost_drag_cagr", "sharpe_ci_lower", "sharpe_ci_upper", "deflated_sharpe"]
    print("\nbenchmark strategies (fake_dummy_base, v1)"); print(pm[cols].round(3).to_string(index=False))

    fig, ax = plt.subplots(figsize=(9, 3.8), facecolor=SURFACE)
    style(ax, "누적 NAV: 벤치마크 전략 3개 (v1, 논문 비용) vs KOSPI·KOSDAQ 지수 (종가, 2024-07-01 = 1)")
    i = 0
    for name in ("equal_weight@paper_costs", "momentum20_topk@paper_costs", "random_topk@paper_costs"):
        nav = pd.read_csv(paths.backtest_dir("fake_dummy_base", "v1", name) / "nav.csv", parse_dates=["date"]).set_index("date")["nav"]
        ax.plot(nav.index, nav, color=PAL[i], lw=2, label=name.split("@")[0]); ax.annotate(f"{name.split('@')[0]} {nav.iloc[-1]:.2f}", (nav.index[-1], nav.iloc[-1]), xytext=(4, 0), textcoords="offset points", fontsize=8, color=INK2, va="center"); i += 1
        dates = nav.index
    bench = pd.read_parquet(paths.prepared_path("benchmark"))
    for j, tk in enumerate(cfg["evaluate"]["reference_indices"]):
        c = bench[bench["ticker"] == tk].set_index("date")["close"].reindex(dates).ffill()
        c = c / c.iloc[0]
        ax.plot(c.index, c, color=PAL[i], lw=1.5, ls="--", label=f"{tk} index")
        ax.annotate(f"{tk} {c.iloc[-1]:.2f}", (c.index[-1], c.iloc[-1]), xytext=(4, -10 * (j + 1)), textcoords="offset points", fontsize=8, color=INK2, va="center"); i += 1
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m")); ax.legend(frameon=False, fontsize=8, labelcolor=INK2, loc="lower left")
    fig.tight_layout(); f2 = OUT / "stage5_benchmarks_nav.png"; fig.savefig(f2, dpi=130, facecolor=SURFACE)
    print(f"\nwrote {f1}\nwrote {f2}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
