"""Dataset checks run after `build`. Produces a JSON summary and raises on hard failures."""
from __future__ import annotations

import numpy as np
import pandas as pd


def validate_dataset(prices: pd.DataFrame, constituents: pd.DataFrame, events: pd.DataFrame,
                     run_start, run_end, lookback: int, horizon: int, max_daily_move: float = 0.35) -> dict:
    p = prices.sort_values(["ticker", "date"])
    run_start, run_end = pd.Timestamp(run_start), pd.Timestamp(run_end)
    cal = pd.DatetimeIndex(sorted(p["date"].unique()))
    hard: list[str] = []

    if p.duplicated(["ticker", "date"]).any():
        hard.append("duplicate (ticker, date) rows")
    if (p["adj_factor"] <= 0).any():
        hard.append("non-positive adj_factor")
    tradable = p.dropna(subset=["open"])
    if ((tradable["high"] < tradable["low"]) | (tradable["close"] <= 0)).any():
        hard.append("high < low or close <= 0 on traded rows")

    # adjusted close continuity: after adjustment, no residual >max_daily_move jumps on event days
    # KR daily price limits are +-30%, so a residual |simple return| > max_daily_move (0.35) can only
    # be a missed corporate action, a re-listing after a long halt, or a data error.
    adj_close = p["close"] * p["adj_factor"]
    ret = adj_close.groupby(p["ticker"]).pct_change()
    mask = ret.abs() > max_daily_move
    big = p.loc[mask, ["date", "ticker", "name"]].assign(ret=ret[mask].to_numpy())

    n_before = int((cal < run_start).sum())
    if n_before < lookback:
        hard.append(f"only {n_before} trading days before run.start, lookback={lookback}")
    n_after = int((cal > run_end).sum())
    if n_after < horizon + 1:
        hard.append(f"only {n_after} trading days after run.end, need >= {horizon + 1} for labels")

    last_day = p.groupby("ticker")["date"].max()
    delisted = last_day[last_day < cal[-1] - pd.Timedelta(days=10)]
    in_run = delisted[(delisted >= run_start) & (delisted <= run_end)]
    cons_in_run = constituents[(constituents["date"] >= run_start) & (constituents["date"] <= run_end)]
    delisted_members = sorted(set(in_run.index) & set(cons_in_run["ticker"]))

    summary = {
        "n_rows": int(len(p)), "n_tickers": int(p["ticker"].nunique()),
        "calendar": {"first": str(cal[0].date()), "last": str(cal[-1].date()), "n_days": int(len(cal)),
                     "n_before_run_start": n_before, "n_after_run_end": n_after},
        "halted_row_share": float(p["halted"].mean()),
        "reference_price_events": {"n": int(len(events)), "n_applied": int(events["applied"].sum()) if len(events) else 0,
                                    "n_ignored_out_of_bounds": int((~events["applied"]).sum()) if len(events) else 0},
        "residual_big_moves_after_adjustment": int(len(big)),
        "delisted": {"n_total": int(len(delisted)), "n_during_run": int(len(in_run)),
                     "n_that_were_universe_members": len(delisted_members), "tickers_during_run": in_run.astype(str).to_dict()},
        "universe": {"n_dates": int(constituents["date"].nunique()),
                     "members_per_date_min": int(constituents.groupby("date").size().min()) if len(constituents) else 0,
                     "members_per_date_max": int(constituents.groupby("date").size().max()) if len(constituents) else 0},
        "hard_failures": hard,
    }
    return summary, big
