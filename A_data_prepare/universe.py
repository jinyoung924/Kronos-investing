"""Point-in-time KOSPI universe from daily membership.

The daily endpoint lists every security that was listed on that day, delisted names included
up to their last trading day, so each day's response IS the point-in-time universe. On top of
that, investability filters that use only trailing data are applied at each date:

  * listed (has a row) and not halted on the as_of date
  * at least `min_history_rows` prior bars in our data (aligns benchmarks with the model,
    whose lookback needs the same history)
  * trailing `liquidity_window`-day mean trading value >= `min_avg_trdval` (KRW)
"""
from __future__ import annotations

import pandas as pd


def build_constituents(prices: pd.DataFrame, dates, min_history_rows: int = 400,
                       liquidity_window: int = 20, min_avg_trdval: float = 0.0,
                       exclude_halted: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (constituents(date, ticker), coverage(date, n_listed, n_members))."""
    dates = pd.DatetimeIndex(sorted(pd.DatetimeIndex(dates).unique()))
    listed = prices.pivot(index="date", columns="ticker", values="close").notna()
    halted = prices.pivot(index="date", columns="ticker", values="halted").astype("boolean").fillna(True).astype(bool)
    trdval = prices.pivot(index="date", columns="ticker", values="trdval")
    history = listed.cumsum()                                         # bars up to and including t
    liq = trdval.fillna(0.0).rolling(liquidity_window, min_periods=liquidity_window).mean()

    rows, cov = [], []
    for d in dates:
        if d not in listed.index:
            continue
        ok = listed.loc[d] & (history.loc[d] >= min_history_rows)
        if exclude_halted:
            ok &= ~halted.loc[d]
        if min_avg_trdval > 0:
            ok &= liq.loc[d].fillna(0.0) >= min_avg_trdval
        members = ok.index[ok.to_numpy()]
        rows.append(pd.DataFrame({"date": d, "ticker": members}))
        cov.append({"date": d, "n_listed": int(listed.loc[d].sum()), "n_members": int(len(members))})
    cons = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["date", "ticker"])
    return cons, pd.DataFrame(cov)
