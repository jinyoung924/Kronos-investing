"""Trading-calendar arithmetic. The calendar is the sorted DatetimeIndex of KRX trading days
(data/A_prepared/calendar.parquet, column `date`); every function here takes it explicitly so
nothing reads a file or guesses business days.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def as_calendar(dates) -> pd.DatetimeIndex:
    """Sorted, unique, normalised DatetimeIndex from any iterable of dates or a frame with a `date` column."""
    if isinstance(dates, pd.DataFrame):
        dates = dates["date"]
    idx = pd.DatetimeIndex(pd.to_datetime(pd.Series(list(dates)))).normalize()
    return pd.DatetimeIndex(np.unique(idx.to_numpy()))


def is_trading_day(calendar: pd.DatetimeIndex, d) -> bool:
    return pd.Timestamp(d).normalize() in calendar


def next_trading_day(calendar: pd.DatetimeIndex, d) -> pd.Timestamp:
    """First trading day strictly after d (d itself need not be a trading day). Raises past the calendar end."""
    pos = calendar.searchsorted(pd.Timestamp(d).normalize(), side="right")
    if pos >= len(calendar):
        raise ValueError(f"no trading day after {pd.Timestamp(d).date()} in a calendar ending {calendar[-1].date()}")
    return calendar[pos]


def prev_trading_day(calendar: pd.DatetimeIndex, d) -> pd.Timestamp:
    """Last trading day strictly before d."""
    pos = calendar.searchsorted(pd.Timestamp(d).normalize(), side="left") - 1
    if pos < 0:
        raise ValueError(f"no trading day before {pd.Timestamp(d).date()} in a calendar starting {calendar[0].date()}")
    return calendar[pos]


def shift_trading_days(calendar: pd.DatetimeIndex, d, n: int) -> pd.Timestamp:
    """Trading day n steps after d (n < 0 = before). d must be a trading day. Raises outside the calendar."""
    d = pd.Timestamp(d).normalize()
    pos = calendar.searchsorted(d)
    if pos >= len(calendar) or calendar[pos] != d:
        raise ValueError(f"{d.date()} is not a trading day")
    j = pos + int(n)
    if j < 0 or j >= len(calendar):
        raise ValueError(f"shift of {n} from {d.date()} leaves the calendar ({calendar[0].date()} .. {calendar[-1].date()})")
    return calendar[j]


def rebalance_dates(calendar: pd.DatetimeIndex, start, end, step: int) -> pd.DatetimeIndex:
    """Every `step`-th trading day in [start, end], starting at the first trading day >= start.
    Identical to what B_model_infer.run_inference uses to choose as_of dates."""
    if step < 1:
        raise ValueError("step must be >= 1")
    cal = calendar[(calendar >= pd.Timestamp(start)) & (calendar <= pd.Timestamp(end))]
    return cal[::step]
