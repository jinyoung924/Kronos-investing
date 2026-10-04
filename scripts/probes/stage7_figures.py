"""Stage 7 user-check outputs (docs/spec.md Stage 7 "사용자 확인") from data/E_backtest/{run_id}/v2:
  1. per strategy: the cumulative shortfall scenarios with CAGR, Sharpe, total cost, lowest cash and order status counts
  2. orders rejected by the price limit (date, ticker, side, open, reference price, upper / lower limit)
  3. figure: NAV of v1 (no costs) over v2 all_off, and the v2 scenarios
python scripts/probes/stage7_figures.py [run_id] [strategy,strategy]
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
from common.config import cfg_get, load_config  # noqa: E402
from common.paths import Paths  # noqa: E402

OUT = ROOT / "docs/stage_reports/figures"
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"
PAL = ["#8a8984", "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#7a6fd0"]


def scenario_order(cfg) -> list[str]:
    order = list(cfg_get(cfg, "report.shortfall_order"))
    return ["all_off"] + ["+".join(order[:k]) for k in range(1, len(order) + 1)]


def main(run_id: str = "kronos_base_v1", strategies: str = "conf_weighted,vol_target,equal_weight") -> int:
    cfg = load_config(ROOT / "configs/base.yaml")
    paths = Paths(cfg, ROOT)
    ppy = int(cfg_get(cfg, "evaluate.periods_per_year", 252))
    plt.rcParams.update({"font.family": ["AppleGothic", "Malgun Gothic", "DejaVu Sans"], "axes.unicode_minus": False})
    names = strategies.split(",")
    fig, axes = plt.subplots(1, len(names), figsize=(5.2 * len(names), 3.6), facecolor=SURFACE, squeeze=False)
    limit_rows = []
    for ax, name in zip(axes[0], names):
        rows = []
        ax.set_facecolor(SURFACE)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        ax.tick_params(colors=INK2, labelsize=8, length=0); ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.set_title(f"{run_id} / {name}: v1(비용 없음)과 v2 시나리오", loc="left", fontsize=9, color=INK, fontweight="bold")
        v1 = pd.read_csv(paths.backtest_dir(run_id, "v1", name + "@no_costs") / "nav.csv", parse_dates=["date"]).set_index("date")["nav"]
        ax.plot(v1.index, v1, color=INK, lw=4, alpha=0.25, label="v1 @no_costs")
        for k, sc in enumerate(scenario_order(cfg)):
            d = paths.backtest_scenario_dir(run_id, name, sc)
            if not (d / "nav.csv").exists():
                continue
            nav = pd.read_csv(d / "nav.csv", parse_dates=["date"]).set_index("date")
            meta = json.loads((d / "meta.json").read_text())
            r = nav["ret"].iloc[1:]
            years = len(r) / ppy
            rows.append({"scenario": sc, "nav_end": nav["nav"].iloc[-1], "cagr": nav["nav"].iloc[-1] ** (1 / years) - 1,
                         "sharpe": r.mean() / r.std(ddof=1) * np.sqrt(ppy), "total_cost": nav["cost"].sum(), "min_cash_share": meta["min_cash"] / meta["init_cash"],
                         "mean_holdings": meta["mean_holdings"], **{f"n_{s}": c for s, c in meta["order_status_counts"].items()}})
            if sc in ("all_off", "costs", scenario_order(cfg)[-1]):
                ax.plot(nav.index, nav["nav"], color=PAL[min(k, len(PAL) - 1)], lw=1.4, label=sc if sc != scenario_order(cfg)[-1] else "모든 제약")
            if sc == "all_off":
                print(f"{name}: max |v2 all_off ret - v1 ret| = {(nav['ret'] - pd.read_csv(paths.backtest_dir(run_id, 'v1', name + '@no_costs') / 'nav.csv')['ret'].to_numpy()).abs().max():.2e}")
            tr = pd.read_parquet(d / "trades.parquet")
            if sc == scenario_order(cfg)[-1] and (tr["status"] == "rejected_limit").any():
                limit_rows.append(tr[tr["status"] == "rejected_limit"].assign(strategy=name))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%y-%m")); ax.legend(frameon=False, fontsize=7, labelcolor=INK2, loc="lower left")
        t = pd.DataFrame(rows).fillna(0)
        t["d_cagr"] = t["cagr"].diff().fillna(0)
        print(f"\n{run_id} / {name}"); print(t.round(4).to_string(index=False))
    fig.tight_layout(); f = OUT / "stage7_v1_vs_v2.png"; fig.savefig(f, dpi=130, facecolor=SURFACE); print("\nwrote", f)
    if limit_rows:
        lim = pd.concat(limit_rows)
        px = pd.read_parquet(paths.prepared_path("prices"), columns=["date", "ticker", "open", "close"]).sort_values(["ticker", "date"])
        px["prev_close"] = px.groupby("ticker")["close"].shift(1)
        lim = lim.merge(px, on=["date", "ticker"], how="left")
        lim["open_vs_prev"] = lim["open"] / lim["prev_close"] - 1
        print("\norders rejected by the price limit"); print(lim[["strategy", "date", "ticker", "side", "open", "prev_close", "open_vs_prev", "target_shares"]].round(4).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:3]))
