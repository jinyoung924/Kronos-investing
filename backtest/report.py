"""Persist results (nav.csv, weights.parquet, metrics.json, config.yaml) and draw charts."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from backtest.metrics import drawdown_series  # noqa: E402
from common.config import dump_config  # noqa: E402


def _jsonable(obj):
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (pd.Timestamp, pd.Period)):
        return str(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        return None if not np.isfinite(obj) else float(obj)
    if isinstance(obj, (pd.Series, pd.DataFrame, pd.Index)):
        return None
    return obj


def write_json(obj: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(obj), indent=2, ensure_ascii=False), encoding="utf-8")


def save_strategy_results(result, metrics: dict, cfg: dict, out_dir: Path, bench_nav: pd.Series | None = None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    frame = result.summary_frame()
    if bench_nav is not None:
        frame["nav_benchmark"] = bench_nav.reindex(frame.index)
    frame.index.name = "date"
    frame.to_csv(out_dir / "nav.csv", float_format="%.10g")
    w = result.weights.copy()
    w.index.name = "date"
    w.loc[:, (w != 0).any(axis=0)].to_parquet(out_dir / "weights.parquet")
    write_json(metrics, out_dir / "metrics.json")
    dump_config(cfg, out_dir / "config.yaml")
    plot_nav(frame, out_dir / "nav.png", title=out_dir.name)


def plot_nav(frame: pd.DataFrame, path: Path, title: str = "") -> None:
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    ax1.plot(frame.index, frame["nav"], label="net", lw=1.4)
    ax1.plot(frame.index, frame["nav_gross"], label="gross", lw=0.9, alpha=0.7)
    if "nav_benchmark" in frame:
        ax1.plot(frame.index, frame["nav_benchmark"], label="benchmark", lw=1.0, ls="--")
    ax1.set_ylabel("NAV"); ax1.legend(); ax1.grid(alpha=0.3); ax1.set_title(title)
    ax2.fill_between(frame.index, drawdown_series(frame["nav"]), 0, alpha=0.4)
    ax2.set_ylabel("drawdown"); ax2.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(path, dpi=120); plt.close(fig)


def save_prediction_report(pred_report: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(pred_report, out_dir / "prediction_metrics.json")
    for target in ("ret_cc", "ret_oo"):
        ic = pred_report.get(f"_ic_series_{target}")
        if ic is not None and not ic.dropna().empty:
            fig, ax = plt.subplots(figsize=(10, 3.5))
            ax.bar(ic.index, ic.values, width=4, alpha=0.6, label="rank IC")
            ax.plot(ic.index, ic.rolling(8, min_periods=1).mean(), color="k", lw=1.2, label="8-period mean")
            ax.axhline(0, color="grey", lw=0.8); ax.set_title(f"cross-sectional rank IC (exp_ret vs {target})")
            ax.legend(); ax.grid(alpha=0.3); fig.tight_layout(); fig.savefig(out_dir / f"ic_series_{target}.png", dpi=120); plt.close(fig)
            ic.to_csv(out_dir / f"ic_series_{target}.csv", header=True)
        per_date = pred_report.get(f"_quantile_per_date_{target}")
        if per_date is not None and not per_date.empty:
            fig, ax = plt.subplots(figsize=(6, 3.5))
            m = per_date.mean()
            ax.bar([str(int(k)) for k in m.index], m.values)
            ax.set_xlabel("exp_ret quantile (1 = lowest)"); ax.set_ylabel(f"mean {target}")
            ax.set_title("realised return by prediction quantile"); ax.grid(alpha=0.3, axis="y")
            fig.tight_layout(); fig.savefig(out_dir / f"quantiles_{target}.png", dpi=120); plt.close(fig)
            per_date.to_csv(out_dir / f"quantiles_{target}.csv")
    reg = pred_report.get("_regime_labels")
    if reg is not None:
        reg.to_csv(out_dir / "regime_labels.csv")


def compare_table(metrics_by_strategy: dict[str, dict]) -> pd.DataFrame:
    rows = []
    for name, m in metrics_by_strategy.items():
        vb = m.get("vs_benchmark", {}) or {}
        sig = m.get("significance", {}) or {}
        tr = m.get("trading", {}) or {}
        rows.append({
            "strategy": name, "total_return": m.get("total_return"), "cagr": m.get("cagr"), "ann_vol": m.get("ann_vol"),
            "sharpe": m.get("sharpe"), "sortino": m.get("sortino"), "calmar": m.get("calmar"), "mdd": m.get("mdd"),
            "mdd_recovery_days": m.get("mdd_recovery_days"),
            "excess_cagr": vb.get("excess_cagr"), "alpha_ann": vb.get("alpha_ann"), "beta": vb.get("beta"),
            "tracking_error": vb.get("tracking_error"), "info_ratio": vb.get("information_ratio"), "monthly_hit": vb.get("monthly_hit_ratio"),
            "annual_turnover": tr.get("annual_turnover"), "avg_holdings": tr.get("avg_holdings"), "cost_drag_cagr": tr.get("cost_drag_cagr"),
            "sharpe_ci_lo": (sig.get("bootstrap_sharpe_ci95") or {}).get("lo"), "sharpe_ci_hi": (sig.get("bootstrap_sharpe_ci95") or {}).get("hi"),
            "dsr": (sig.get("deflated_sharpe") or {}).get("dsr"),
        })
    return pd.DataFrame(rows).set_index("strategy")


def plot_compare(navs: dict[str, pd.Series], path: Path, title: str = "cumulative NAV (net of costs)") -> None:
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 8), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    for name, nav in navs.items():
        ax1.plot(nav.index, nav.values, label=name, lw=1.3)
        ax2.plot(nav.index, drawdown_series(nav).values, lw=0.9)
    ax1.set_title(title); ax1.set_ylabel("NAV"); ax1.legend(ncol=2, fontsize=8); ax1.grid(alpha=0.3)
    ax2.set_ylabel("drawdown"); ax2.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(path, dpi=120); plt.close(fig)
