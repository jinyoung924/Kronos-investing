"""Stage 3 user-check figures (docs/spec.md Stage 3 "사용자 확인") from data/D_weights/fake_dummy_base/:
  1. holdings per rebalance date, per strategy
  2. turnover between consecutive rebalances (sum |w_t - w_{t-1}| / 2 on target weights), per strategy
Writes PNGs to docs/stage_reports/figures/ and prints the summary.   python scripts/probes/stage3_figures.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from common.config import load_config  # noqa: E402
from common.paths import Paths  # noqa: E402
from D_strategy.registry import available  # noqa: E402

OUT = ROOT / "docs/stage_reports/figures"
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"
COLORS = ["#2a78d6", "#eb6834", "#1baf7a"]
RUN_ID = "fake_dummy_base"


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
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))


def turnover(w: pd.DataFrame) -> pd.Series:
    wide = w.pivot(index="as_of_date", columns="ticker", values="weight").fillna(0.0)
    return (wide.diff().abs().sum(axis=1) / 2).iloc[1:]


def main() -> int:
    cfg = load_config(ROOT / "configs/base.yaml")
    paths = Paths(cfg, ROOT)
    plt.rcParams.update({"font.family": ["AppleGothic", "Malgun Gothic", "NanumGothic", "DejaVu Sans"], "axes.unicode_minus": False})
    OUT.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(2, 1, figsize=(9, 6), facecolor=SURFACE)
    style(axes[0], f"전략별 날짜당 보유 종목 수 ({RUN_ID}, 리밸런싱일 49개)")
    style(axes[1], "리밸런싱 사이 종목 교체율 (Σ|Δw|/2, 목표 비중 기준; 1.0 = 전량 교체)")
    rows = []
    for i, (color, name) in enumerate(zip(COLORS, available())):
        w = pd.read_parquet(paths.weights_path(RUN_ID, name))
        n = w.groupby("as_of_date").size()
        to = turnover(w)
        axes[0].plot(n.index, n.to_numpy(), color=color, lw=2, label=name)
        axes[0].annotate(f"{name} {int(n.iloc[-1])}", (n.index[-1], n.iloc[-1]), xytext=(4, 10 * (len(COLORS) - 1 - i) if int(n.iloc[-1]) < 100 else 0),
                         textcoords="offset points", color=INK2, fontsize=8, va="center")   # stacked labels when lines coincide
        axes[1].plot(to.index, to.to_numpy(), color=color, lw=2, label=name)
        axes[1].annotate(f"{name} {to.iloc[-1]:.2f}", (to.index[-1], to.iloc[-1]), xytext=(4, 0), textcoords="offset points", color=INK2, fontsize=8, va="center")
        rows.append({"strategy": name, "holdings_min": int(n.min()), "holdings_mean": round(float(n.mean()), 1), "holdings_max": int(n.max()),
                     "turnover_mean": round(float(to.mean()), 3), "turnover_min": round(float(to.min()), 3), "turnover_max": round(float(to.max()), 3)})
    axes[0].set_yscale("log"); axes[0].legend(frameon=False, fontsize=8, labelcolor=INK2, loc="center left")
    axes[1].set_ylim(0, 1.05); axes[1].legend(frameon=False, fontsize=8, labelcolor=INK2, loc="center left")
    fig.tight_layout()
    f = OUT / "stage3_holdings_turnover.png"
    fig.savefig(f, dpi=130, facecolor=SURFACE)
    print(pd.DataFrame(rows).to_string(index=False))
    print(f"wrote {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
