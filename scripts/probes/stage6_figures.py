"""Stage 6 user-check figures (docs/spec.md Stage 6 "사용자 확인") from data/C_signals, data/E_backtest, data/F_metrics:
  1. kronos_base_v1: mean realised label by exp_ret quintile, next to the naive rev5 / mom20 IC
  2. cumulative NAV of the five base-profile strategies (v1, paper costs) vs the KOSPI / KOSDAQ index
  3. exp_ret against the z-score of the last close inside the 400-day input window (mean-reversion finding)
  4. TopK daily number of swapped names (fake_dummy_paper: the real paper-profile run does not exist yet)
python scripts/probes/stage6_figures.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.config import load_config  # noqa: E402
from common.paths import Paths  # noqa: E402
from stage6_mean_reversion import window_stats  # noqa: E402

OUT = ROOT / "docs/stage_reports/figures"
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"
PAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#7a6fd0", "#8a8984"]
RUN = "kronos_base_v1"


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
    written = []

    s = json.loads((paths.metrics_dir(RUN) / "signal_metrics.json").read_text())
    q = s["quantiles"]["exp_ret"]["mean_by_quantile"]
    fig, ax = plt.subplots(figsize=(6.2, 3.4), facecolor=SURFACE)
    style(ax, f"{RUN}: exp_ret 분위별 평균 실현수익률 (H {s['H']}, {s['ic']['exp_ret']['n_dates']}일)")
    ax.bar(list(q), list(q.values()), color=PAL[0], width=0.6)
    for i, v in enumerate(q.values()):
        ax.annotate(f"{v:+.2%}", (i, v), xytext=(0, 4 if v >= 0 else -10), textcoords="offset points", ha="center", fontsize=8, color=INK2)
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=2)); ax.axhline(0, color=GRID, lw=1)
    fig.tight_layout(); f = OUT / "stage6_quantile_returns.png"; fig.savefig(f, dpi=130, facecolor=SURFACE); written.append(f)

    fig, ax = plt.subplots(figsize=(9, 4), facecolor=SURFACE)
    style(ax, f"누적 NAV: {RUN} 전략 5개 (v1, 논문 비용) vs KOSPI·KOSDAQ 지수 (종가, 2024-07-01 = 1)")
    names = ["conf_weighted", "vol_target", "equal_weight", "momentum20_topk", "random_topk"]
    for i, name in enumerate(names):
        nav = pd.read_csv(paths.backtest_dir(RUN, "v1", name + "@paper_costs") / "nav.csv", parse_dates=["date"]).set_index("date")["nav"]
        ax.plot(nav.index, nav, color=PAL[i], lw=2.2 if i < 2 else 1.4, label=f"{name} {nav.iloc[-1]:.2f}")
        dates = nav.index
    bench = pd.read_parquet(paths.prepared_path("benchmark"))
    for j, tk in enumerate(cfg["evaluate"]["reference_indices"]):
        c = bench[bench["ticker"] == tk].set_index("date")["close"].reindex(dates).ffill()
        c = c / c.iloc[0]
        ax.plot(c.index, c, color=PAL[5 + j], lw=1.4, ls="--", label=f"{tk} 지수 {c.iloc[-1]:.2f}")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m")); ax.legend(frameon=False, fontsize=8, labelcolor=INK2, loc="lower left", ncol=2)
    fig.tight_layout(); f = OUT / "stage6_nav.png"; fig.savefig(f, dpi=130, facecolor=SURFACE); written.append(f)

    sig = pd.read_parquet(paths.signals_path(RUN))
    manifest = json.loads(paths.manifest_file(RUN).read_text())
    a = sig.merge(window_stats(paths, sorted(sig["as_of_date"].unique()), int(manifest["lookback"])), on=["as_of_date", "ticker"]).dropna(subset=["z"])
    fig, ax = plt.subplots(figsize=(6.2, 4), facecolor=SURFACE)
    style(ax, f"{RUN}: exp_ret vs 입력 구간(400일) 안에서 마지막 종가의 z (Spearman {a['exp_ret'].corr(a['z'], method='spearman'):+.2f})")
    sm = a.sample(min(len(a), 8000), random_state=0)
    ax.scatter(sm["z"], sm["exp_ret"].clip(-1, 1.5), s=4, color=PAL[0], alpha=0.25, linewidths=0)
    ax.axhline(0, color=GRID, lw=1); ax.set_xlabel("z = (마지막 종가 − 구간 평균) / 구간 표준편차", fontsize=8, color=INK2); ax.set_ylabel("exp_ret (5일)", fontsize=8, color=INK2)
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    fig.tight_layout(); f = OUT / "stage6_exp_ret_vs_window_z.png"; fig.savefig(f, dpi=130, facecolor=SURFACE); written.append(f)

    wpath = paths.weights_path("fake_dummy_paper", "topk")
    if wpath.exists():
        w = pd.read_parquet(wpath)
        buys = w[w["weight"].notna()].groupby("as_of_date").size().reindex(sorted(w["as_of_date"].unique()), fill_value=0).iloc[1:]
        fig, ax = plt.subplots(figsize=(6.2, 3.2), facecolor=SURFACE)
        style(ax, f"TopK 하루 교체 종목 수 분포 (fake_dummy_paper, k 50 · n 5, {len(buys)}일)")
        vc = buys.value_counts().sort_index()
        ax.bar(vc.index.astype(str), vc.to_numpy(), color=PAL[0], width=0.6)
        for i, v in enumerate(vc.to_numpy()):
            ax.annotate(str(v), (i, v), xytext=(0, 3), textcoords="offset points", ha="center", fontsize=8, color=INK2)
        fig.tight_layout(); f = OUT / "stage6_topk_swaps.png"; fig.savefig(f, dpi=130, facecolor=SURFACE); written.append(f)
        print("topk swaps per day:", vc.to_dict(), "mean", round(float(buys.mean()), 2))
    for f in written:
        print("wrote", f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
