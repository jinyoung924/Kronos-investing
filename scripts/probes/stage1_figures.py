"""Stage 1 user-check figures (docs/spec.md Stage 1 "사용자 확인"), drawn from data/A_prepared/:
  1. raw vs adjusted close of three well-known split names (10:1 splits: BYC 001460, 남양유업 003920, 영풍 000670)
  2. universe members per date (total and per market)
Writes PNGs to docs/stage_reports/figures/. Read-only on data.   python scripts/probes/stage1_figures.py
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

OUT = ROOT / "docs/stage_reports/figures"
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"
S1, S2, S3 = "#2a78d6", "#eb6834", "#1baf7a"       # reference categorical slots 1-3 (dataviz palette, light mode)
SPLITS = ["001460", "003920", "000670"]


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


def main() -> int:
    cfg = load_config(ROOT / "configs/base.yaml")
    paths = Paths(cfg, ROOT)
    prices = pd.read_parquet(paths.prepared_path("prices"), columns=["date", "ticker", "close"])
    adj = pd.read_parquet(paths.prepared_path("adj_factor"))
    events = pd.read_parquet(paths.prepared_path("events"))
    names = pd.read_parquet(paths.price_file("kospi"), columns=["ticker", "name"]).drop_duplicates("ticker").set_index("ticker")["name"]
    OUT.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": ["AppleGothic", "Malgun Gothic", "NanumGothic", "DejaVu Sans"], "axes.unicode_minus": False})

    fig, axes = plt.subplots(len(SPLITS), 1, figsize=(9, 2.6 * len(SPLITS)), facecolor=SURFACE)
    for ax, t in zip(axes, SPLITS):
        p = prices[prices["ticker"] == t].merge(adj[adj["ticker"] == t], on=["date", "ticker"]).sort_values("date")
        p["adjusted"] = p["close"] * p["factor"]
        ev = events[(events["ticker"] == t) & events["applied"]]
        style(ax, f"{t} {names.get(t, '')}: 원주가(raw close) vs 수정주가(raw × F)  —  ex-date {', '.join(d.strftime('%Y-%m-%d') + f' (ratio {r:.2f})' for d, r in zip(ev['ex_date'], ev['ratio']))}")
        ax.plot(p["date"], p["close"], color=S1, lw=2, label="raw close")
        ax.plot(p["date"], p["adjusted"], color=S2, lw=2, label="adjusted (raw × F)")
        for d in ev["ex_date"]:
            ax.axvline(d, color=GRID, lw=1, ls="--")
        ax.set_yscale("log")
        last = p.iloc[-1]
        ax.annotate("raw", (last["date"], last["close"]), xytext=(4, 0), textcoords="offset points", color=INK2, fontsize=8, va="center")
        ax.annotate("adjusted", (last["date"], last["adjusted"]), xytext=(4, 0), textcoords="offset points", color=INK2, fontsize=8, va="center")
        ax.legend(frameon=False, fontsize=8, loc="lower left", labelcolor=INK2)
    fig.tight_layout()
    f1 = OUT / "stage1_splits_raw_vs_adjusted.png"
    fig.savefig(f1, dpi=130, facecolor=SURFACE)

    m = pd.read_csv(paths.prepared_dir() / "universe_members_by_date.csv", parse_dates=["date"])
    fig, ax = plt.subplots(figsize=(9, 3.4), facecolor=SURFACE)
    style(ax, f"유니버스 종목 수 (variant {cfg['universe']['variant']}), 날짜별 스냅샷")
    for col, color, label in (("n_members", S1, "total"), ("n_kospi", S2, "KOSPI"), ("n_kosdaq", S3, "KOSDAQ")):
        ax.plot(m["date"], m[col], color=color, lw=2, label=label)
        ax.annotate(f"{label} {int(m[col].iloc[-1])}", (m["date"].iloc[-1], m[col].iloc[-1]), xytext=(4, 0),
                    textcoords="offset points", color=INK2, fontsize=8, va="center")
    ax.axvspan(pd.Timestamp(cfg["period"]["start"]), pd.Timestamp(cfg["period"]["end"]), color=GRID, alpha=0.35, lw=0)
    ax.legend(frameon=False, fontsize=8, loc="upper left", labelcolor=INK2)
    ax.set_ylim(0)
    fig.tight_layout()
    f2 = OUT / "stage1_universe_members.png"
    fig.savefig(f2, dpi=130, facecolor=SURFACE)
    print(f"wrote {f1}\nwrote {f2}")
    print(m.describe().loc[["min", "max"], ["n_members", "n_kospi", "n_kosdaq"]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
