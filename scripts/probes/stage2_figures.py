"""Stage 2 user-check figures (docs/spec.md Stage 2 "사용자 확인") from data/C_signals/ and data/A_prepared/:
  1. signal tickers per as_of date (universe, predicted ∩ universe = signal rows, excluded) for the base profile
  2. oracle exp_ret vs realised 5-day adjusted close return, scatter (should sit on the diagonal)
Also prints the rebalance-date list summary. Writes PNGs to docs/stage_reports/figures/.   python scripts/probes/stage2_figures.py
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
from common.data import to_wide  # noqa: E402
from common.paths import Paths  # noqa: E402

OUT = ROOT / "docs/stage_reports/figures"
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"
S1, S2, S3 = "#2a78d6", "#eb6834", "#1baf7a"


def style(ax, title):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=8, length=0)
    ax.grid(color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_title(title, loc="left", fontsize=10, color=INK, fontweight="bold", pad=8)


def main() -> int:
    cfg = load_config(ROOT / "configs/base.yaml")
    paths = Paths(cfg, ROOT)
    plt.rcParams.update({"font.family": ["AppleGothic", "Malgun Gothic", "NanumGothic", "DejaVu Sans"], "axes.unicode_minus": False})
    OUT.mkdir(parents=True, exist_ok=True)

    meta = json.loads((paths.signals_dir("fake_oracle_base") / "meta.json").read_text())
    bd = pd.DataFrame(meta["by_date"]).T
    bd.index = pd.to_datetime(bd.index)
    print(f"rebalance dates: {len(bd)} ({bd.index[0].date()} .. {bd.index[-1].date()}), first 3 {[d.date() for d in bd.index[:3]]}, last 3 {[d.date() for d in bd.index[-3:]]}")
    print(bd[["n_universe", "n_signal", "n_universe_without_prediction"]].astype(int).describe().loc[["min", "mean", "max"]].round(1))

    fig, ax = plt.subplots(figsize=(9, 3.4), facecolor=SURFACE)
    style(ax, "날짜별 종목 수 (base 프로필, 리밸런싱일 49개): 유니버스 vs 시그널 행(= 유니버스 ∩ 예측) vs 제외")
    for col, color, label in (("n_universe", S1, "universe"), ("n_signal", S2, "signal rows"), ("n_universe_without_prediction", S3, "excluded (no prediction)")):
        ax.plot(bd.index, bd[col].astype(int), color=color, lw=2, label=label, marker="o", ms=3)
        ax.annotate(f"{label} {int(bd[col].iloc[-1])}", (bd.index[-1], int(bd[col].iloc[-1])), xytext=(4, 0), textcoords="offset points", color=INK2, fontsize=8, va="center")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.legend(frameon=False, fontsize=8, loc="center left", labelcolor=INK2)
    ax.set_ylim(0)
    fig.tight_layout()
    f1 = OUT / "stage2_signal_counts.png"
    fig.savefig(f1, dpi=130, facecolor=SURFACE)

    sig = pd.read_parquet(paths.signals_path("fake_oracle_base"))
    prices = pd.read_parquet(paths.prepared_path("prices"), columns=["date", "ticker", "close"])
    adj = pd.read_parquet(paths.prepared_path("adj_factor"))
    ac = to_wide(prices, "close") * to_wide(adj, "factor")
    real = (ac.shift(-5) / ac - 1.0).stack().rename("realized")
    real.index.names = ["as_of_date", "ticker"]
    m = sig.join(real, on=["as_of_date", "ticker"]).dropna(subset=["realized"])
    err = (m["exp_ret"] - m["realized"]).abs()
    print(f"oracle vs realized: n {len(m):,}, corr {m[['exp_ret', 'realized']].corr().iloc[0, 1]:.6f}, max |diff| {err.max():.2e}, mean |diff| {err.mean():.2e}")
    fig, ax = plt.subplots(figsize=(5.2, 5.2), facecolor=SURFACE)
    style(ax, "오라클 exp_ret vs 실현 5거래일 수정 종가 수익률")
    lim = float(np.nanmax(np.abs(m[["exp_ret", "realized"]].to_numpy()))) * 1.05
    ax.plot([-lim, lim], [-lim, lim], color=GRID, lw=1)
    ax.scatter(m["realized"], m["exp_ret"], s=6, color=S1, alpha=0.35, edgecolors="none")
    ax.set_xlabel("realized (as_of close -> +5 trading days, adjusted)", color=INK2, fontsize=8)
    ax.set_ylabel("oracle exp_ret", color=INK2, fontsize=8)
    ax.set_xlim(-lim, lim); ax.set_ylim(-lim, lim)
    ax.text(0.02, 0.96, f"n = {len(m):,}\ncorr = {m[['exp_ret', 'realized']].corr().iloc[0, 1]:.5f}\nmax |diff| = {err.max():.1e}", transform=ax.transAxes, fontsize=8, color=INK2, va="top")
    fig.tight_layout()
    f2 = OUT / "stage2_oracle_scatter.png"
    fig.savefig(f2, dpi=130, facecolor=SURFACE)
    print(f"wrote {f1}\nwrote {f2}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
