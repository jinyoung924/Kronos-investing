"""compare.md building blocks (docs/spec.md Stage 8 task 1). Pure: tables and markdown from the F_metrics contents that
run_report loads. No metric is computed here; every number is read from signal_metrics.json / portfolio_metrics.csv."""
from __future__ import annotations

import numpy as np
import pandas as pd

IC_COLUMNS = ["signal", "ic_mean", "ic_std", "icir", "t", "share_positive", "n_dates"]
STRATEGY_COLUMNS = ["row", "run_id", "strategy", "costs", "cagr", "aer", "sharpe", "max_drawdown", "ir", "annual_turnover", "names_changed_per_day",
                    "cost_drag_cagr", "sharpe_ci_lower", "sharpe_ci_upper", "deflated_sharpe", "n_trials", "benchmark", "n_days"]
PCT = {"cagr", "aer", "max_drawdown", "cost_drag_cagr", "d_cagr", "total_cost", "min_cash_share"}


def ic_table(sig: dict) -> pd.DataFrame:
    """Kronos signals and the naive controls of one run_id (signal_metrics.json)."""
    rows = []
    for name, v in list(sig.get("ic", {}).items()) + [(f"naive {k}", v) for k, v in sig.get("naive", {}).items()]:
        if "mean" not in v:
            rows.append({"signal": name, "ic_mean": np.nan, "ic_std": np.nan, "icir": np.nan, "t": np.nan, "share_positive": np.nan, "n_dates": 0})
            continue
        rows.append({"signal": name, "ic_mean": v["mean"], "ic_std": v["std"], "icir": v["icir"], "t": v["t"],
                     "share_positive": v.get("share_positive", np.nan), "n_dates": v["n_dates"]})
    return pd.DataFrame(rows, columns=IC_COLUMNS)


def row_label(strategy_base: str, run_id: str, costs: str) -> str:
    """`{strategy}@{run_id}`, with the cost scenario appended when it is not the Korean default."""
    return f"{strategy_base}@{run_id}" + ("" if costs in ("kr", "", None) else f"@{costs}")


def strategy_table(metrics: dict[str, pd.DataFrame], strategies: list[str] | None = None) -> pd.DataFrame:
    """One row per (run_id, strategy, cost scenario) from portfolio_metrics.csv, cost-free twins left out (their effect is
    the cost_drag_cagr column). Index reference rows come last, once per ticker. strategies=None keeps every strategy."""
    rows, index_rows = [], {}
    for run_id, pm in metrics.items():
        for r in pm.to_dict("records"):
            name = str(r["strategy"])
            if name.startswith("index:"):
                index_rows.setdefault(name, {**r, "row": name, "run_id": run_id, "strategy": name})
                continue
            base, costs = str(r["strategy_base"]), str(r["costs"])
            if costs == "no_costs" or (strategies is not None and base not in strategies):
                continue
            rows.append({**r, "row": row_label(base, run_id, costs), "strategy": base})
    out = pd.DataFrame(rows + list(index_rows.values()))
    for c in STRATEGY_COLUMNS:
        if c not in out.columns:
            out[c] = np.nan
    return out[STRATEGY_COLUMNS].reset_index(drop=True)


def consistency_notes(table: pd.DataFrame, periods: dict[str, tuple[str, str]]) -> list[str]:
    """Notes for the comparison table when rows do not share the period or the benchmark (spec: say so under the table)."""
    notes = []
    strat = table[~table["row"].astype(str).str.startswith("index:")]
    spans = {periods[r] for r in strat["row"] if r in periods}
    if len(spans) > 1:
        notes.append("행마다 기간이 다르다: " + "; ".join(f"{r} {periods[r][0]} ~ {periods[r][1]}" for r in strat["row"] if r in periods))
    benches = sorted(set(strat["benchmark"].dropna().astype(str)))
    if len(benches) > 1:
        notes.append("행마다 벤치마크가 다르다(같은 run_id·같은 비용 시나리오의 equal_weight가 기준이다): " + ", ".join(benches))
    days = sorted(set(strat["n_days"].dropna().astype(int)))
    if len(days) > 1:
        notes.append(f"거래일 수가 다르다: {days}")
    return notes


def fmt(v, col: str = "") -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "—"
    if isinstance(v, (int, np.integer)):
        return f"{int(v):,}"
    if isinstance(v, (float, np.floating)):
        if col == "d_cagr":
            return f"{v * 100:+.2f}%p"
        if col in ("total_cost", "min_cash_share"):
            return f"{v * 100:.1f}%"
        if col in PCT:
            return f"{v * 100:+.1f}%"
        if col in ("n_dates", "n_days", "n_trials"):
            return f"{int(v):,}"
        return f"{v:+.3f}" if col in ("ic_mean", "icir", "t", "sharpe", "ir", "d_sharpe") else f"{v:.2f}"
    return str(v)


def md_table(df: pd.DataFrame, headers: dict[str, str] | None = None) -> str:
    cols = list(df.columns)
    head = [(headers or {}).get(c, c) for c in cols]
    lines = ["| " + " | ".join(head) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for r in df.to_dict("records"):
        lines.append("| " + " | ".join(fmt(r[c], c) for c in cols) + " |")
    return "\n".join(lines)
