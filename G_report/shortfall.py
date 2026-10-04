"""shortfall.md building blocks (docs/spec.md Stage 8 task 2). Pure: the waterfall table from shortfall_metrics.csv
(F_evaluate --engine v2). The only arithmetic here is the difference between consecutive rows of numbers F computed."""
from __future__ import annotations

import pandas as pd

V1_ROW = "v1@no_costs"
WATERFALL_COLUMNS = ["step", "scenario", "cagr", "sharpe", "d_cagr", "d_sharpe", "total_cost", "min_cash_share"]


def scenario_sequence(order: list[str]) -> list[str]:
    """all_off, then the cumulative scenarios in `order` (the names engine v2 writes are in canonical constraint order)."""
    return ["all_off"] + ["+".join(order[:k]) for k in range(1, len(order) + 1)]


def canonical(name: str, canon: list[str]) -> str:
    if name == "all_off":
        return name
    on = set(name.split("+"))
    return "+".join(c for c in canon if c in on)


def waterfall(sf: pd.DataFrame, order: list[str], canon: list[str]) -> pd.DataFrame:
    """Rows: v1 (no costs) -> v2 all_off (the "engine difference") -> one constraint more per row, in `order`.
    sf: the shortfall_metrics rows of ONE strategy. d_* = change against the previous row (first row 0)."""
    by = {str(r["scenario"]): r for r in sf.to_dict("records")}
    steps = [("v1 (비용 없음)", V1_ROW), ("엔진 차이 (v2, 제약 없음)", "all_off")] + \
            [(f"+ {c}", canonical(s, canon)) for c, s in zip(order, scenario_sequence(order)[1:])]
    missing = [s for _, s in steps if s not in by]
    if missing:
        raise KeyError(f"shortfall scenarios missing from shortfall_metrics.csv: {missing}")
    rows, prev = [], None
    for label, scen in steps:
        r = by[scen]
        rows.append({"step": label, "scenario": scen, "cagr": float(r["cagr"]), "sharpe": float(r["sharpe"]),
                     "d_cagr": 0.0 if prev is None else float(r["cagr"]) - float(prev["cagr"]),
                     "d_sharpe": 0.0 if prev is None else float(r["sharpe"]) - float(prev["sharpe"]),
                     "total_cost": float(r.get("total_cost", float("nan"))), "min_cash_share": float(r.get("min_cash_share", float("nan")))})
        prev = r
    return pd.DataFrame(rows, columns=WATERFALL_COLUMNS)


def largest_step(wf: pd.DataFrame) -> dict:
    """The constraint row with the most negative d_cagr (engine-difference row excluded)."""
    body = wf.iloc[2:]
    r = body.loc[body["d_cagr"].idxmin()]
    return {"step": r["step"], "d_cagr": float(r["d_cagr"])}
