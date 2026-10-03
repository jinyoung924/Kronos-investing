"""Realised returns (LABELS). The only place in the project that computes them (docs/outline.md invariant 3).

    label(s, i)      = Open_adj_i(f + H) / Open_adj_i(f) - 1,   f = next_trading_day(s)
    label_mean(s, i) = mean_{h=1..H} Open_adj_i(f + h) / Open_adj_i(f) - 1     (scores exp_ret_mean)
Same basis as the engine's fills (next open) and horizon H from the run_id's manifest. Adjusted open = raw open
* F. A missing open on f or f + h (halted / delisted) gives NaN. Pure functions only; never passed to strategies.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from common.calendar import next_trading_day


def adjusted_open_wide(prices: pd.DataFrame, adj_factor: pd.DataFrame, calendar: pd.DatetimeIndex) -> pd.DataFrame:
    o = prices.pivot(index="date", columns="ticker", values="open")
    f = adj_factor.pivot(index="date", columns="ticker", values="factor").reindex_like(o)
    return (o * f).reindex(calendar)


def compute_labels(prices: pd.DataFrame, adj_factor: pd.DataFrame, calendar: pd.DatetimeIndex, as_of_dates, horizon: int,
                   tickers=None) -> pd.DataFrame:
    """Long frame (as_of_date, ticker, fill_date, end_date, label, label_mean) for every as_of date and ticker
    (or the given tickers). Rows whose window runs past the calendar end are dropped."""
    calendar = pd.DatetimeIndex(calendar)
    oa = adjusted_open_wide(prices, adj_factor, calendar)
    if tickers is not None:
        oa = oa.reindex(columns=sorted(set(map(str, tickers))))
    H = int(horizon)
    arr = oa.to_numpy(dtype=float)
    parts = []
    for s in pd.DatetimeIndex(pd.to_datetime(list(as_of_dates))).normalize():
        f = next_trading_day(calendar, s)
        i = calendar.get_loc(f)
        if i + H >= len(calendar):
            continue
        base = arr[i]
        fut = arr[i + 1: i + H + 1]                     # H rows: f+1 .. f+H
        with np.errstate(invalid="ignore", divide="ignore"):
            label = fut[-1] / base - 1.0
            label_mean = fut.mean(axis=0) / base - 1.0  # NaN if any day in the window is missing
        parts.append(pd.DataFrame({"as_of_date": s, "ticker": oa.columns, "fill_date": f, "end_date": calendar[i + H],
                                   "label": label, "label_mean": label_mean}))
    if not parts:
        return pd.DataFrame(columns=["as_of_date", "ticker", "fill_date", "end_date", "label", "label_mean"])
    out = pd.concat(parts, ignore_index=True)
    out["ticker"] = out["ticker"].astype(str)
    return out[np.isfinite(out["label"]) | np.isfinite(out["label_mean"])].reset_index(drop=True)
