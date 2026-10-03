"""Stage 4 user-check figures (docs/spec.md Stage 4 "사용자 확인"):
  1. cumulative NAV of oracle Top-20 (exp_ret) vs the same picks applied one rebalance later vs EqualWeight (no costs)
  2. distribution of RandomTopK (k = 20) total return over seeds vs EqualWeight (no costs)
Uses fake_oracle_base signals and engine v1 directly (weights built here, as in tests/test_stage4_engine_v1.py).
Writes PNGs to docs/stage_reports/figures/.   python scripts/probes/stage4_figures.py
"""
from __future__ import annotations

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
from common.config import cfg_override, load_config  # noqa: E402
from common.paths import Paths  # noqa: E402
from E_backtest.costs import CostModel  # noqa: E402
from E_backtest.engine_v1_weights import run_v1  # noqa: E402

OUT = ROOT / "docs/stage_reports/figures"
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"
S1, S2, S3 = "#2a78d6", "#eb6834", "#1baf7a"
K, N_SEEDS = 20, 16


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


def topk(sig, col, k):
    rows = []
    for d, g in sig.groupby("as_of_date"):
        top = g.sort_values([col, "ticker"], ascending=[False, True]).head(k)["ticker"]
        rows.append(pd.DataFrame({"as_of_date": d, "ticker": top.to_numpy(), "weight": 1.0 / len(top)}))
    return pd.concat(rows, ignore_index=True)


def main() -> int:
    cfg = cfg_override(load_config(ROOT / "configs/base.yaml"), {"backtest.delist_policy": "last_close"})
    paths = Paths(cfg, ROOT)
    prices = pd.read_parquet(paths.prepared_path("prices"), columns=["date", "ticker", "market", "open", "close"])
    prices = prices.merge(pd.read_parquet(paths.prepared_path("adj_factor")), on=["date", "ticker"])
    cal = pd.DatetimeIndex(pd.read_parquet(paths.prepared_path("calendar"))["date"])
    sig = pd.read_parquet(paths.signals_path("fake_oracle_base"))
    free = CostModel(cfg, "none")
    n = sig.groupby("as_of_date")["ticker"].transform("size")
    ew = run_v1(sig[["as_of_date", "ticker"]].assign(weight=1.0 / n), prices, cal, free, cfg)["nav"].set_index("date")["nav"]
    w = topk(sig, "exp_ret", K)
    oracle = run_v1(w, prices, cal, free, cfg)["nav"].set_index("date")["nav"]
    dates = sorted(w["as_of_date"].unique())
    shift = dict(zip(dates[:-1], dates[1:]))
    late = w[w["as_of_date"].isin(shift)].assign(as_of_date=lambda d: d["as_of_date"].map(shift))
    stale = run_v1(late, prices, cal, free, cfg)["nav"].set_index("date")["nav"]
    print(f"total return: oracle top{K} {oracle.iloc[-1]-1:+.1%}, same picks one week later {stale.iloc[-1]-1:+.1%}, equal_weight {ew.iloc[-1]-1:+.1%}")

    plt.rcParams.update({"font.family": ["AppleGothic", "Malgun Gothic", "NanumGothic", "DejaVu Sans"], "axes.unicode_minus": False})
    OUT.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(9, 3.8), facecolor=SURFACE)
    style(ax, f"누적 NAV (로그, 비용 0): 오라클 top{K} vs 같은 종목을 1주 늦게 체결 vs EqualWeight")
    for s, c, lab in ((oracle, S1, f"oracle top{K}"), (stale, S2, "oracle +1 rebalance (stale)"), (ew, S3, "equal_weight")):
        ax.plot(s.index, s.to_numpy(), color=c, lw=2, label=lab)
        ax.annotate(f"{lab} {s.iloc[-1]:.2f}", (s.index[-1], s.iloc[-1]), xytext=(4, 0), textcoords="offset points", color=INK2, fontsize=8, va="center")
    ax.set_yscale("log"); ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.legend(frameon=False, fontsize=8, labelcolor=INK2, loc="upper left")
    fig.tight_layout(); f1 = OUT / "stage4_oracle_vs_stale.png"; fig.savefig(f1, dpi=130, facecolor=SURFACE)

    rets = []
    for seed in range(N_SEEDS):
        rows = []
        for d, g in sig.groupby("as_of_date"):
            t = sorted(g["ticker"])
            rng = np.random.default_rng([seed, int(pd.Timestamp(d).value // 10**9)])
            pick = np.array(t)[np.argsort(-rng.random(len(t)))[:K]]
            rows.append(pd.DataFrame({"as_of_date": d, "ticker": pick, "weight": 1.0 / len(pick)}))
        rets.append(run_v1(pd.concat(rows, ignore_index=True), prices, cal, free, cfg)["nav"]["nav"].iloc[-1] - 1)
    rets = np.array(rets)
    print(f"random top{K} over {N_SEEDS} seeds: mean {rets.mean():+.1%}, std {rets.std(ddof=1):.1%}, min {rets.min():+.1%}, max {rets.max():+.1%}; equal_weight {ew.iloc[-1]-1:+.1%}")
    fig, ax = plt.subplots(figsize=(7, 3.4), facecolor=SURFACE)
    style(ax, f"RandomTopK (k = {K}) seed별 총수익률 분포 ({N_SEEDS} seeds, 비용 0) vs EqualWeight")
    ax.hist(rets, bins=8, color=S1, alpha=0.8, edgecolor=SURFACE)
    ax.axvline(ew.iloc[-1] - 1, color=S3, lw=2); ax.annotate("equal_weight", (ew.iloc[-1] - 1, ax.get_ylim()[1] * 0.9), xytext=(4, 0), textcoords="offset points", color=INK2, fontsize=8)
    ax.axvline(rets.mean(), color=S2, lw=2, ls="--"); ax.annotate("random mean", (rets.mean(), ax.get_ylim()[1] * 0.75), xytext=(4, 0), textcoords="offset points", color=INK2, fontsize=8)
    ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    fig.tight_layout(); f2 = OUT / "stage4_random_seeds.png"; fig.savefig(f2, dpi=130, facecolor=SURFACE)
    print(f"wrote {f1}\nwrote {f2}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
