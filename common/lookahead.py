"""Lookahead-bias guards. Every place that touches time-indexed data goes through these."""
from __future__ import annotations

import pandas as pd


class LookaheadError(AssertionError):
    """Raised when data from after the decision time would be used."""


def assert_no_future(df: pd.DataFrame, as_of, date_col: str = "date") -> None:
    """Raise LookaheadError if any row is dated after `as_of`."""
    as_of = pd.Timestamp(as_of)
    dates = df.index if date_col is None else df[date_col]
    if len(dates) == 0:
        return
    max_date = pd.Timestamp(pd.Series(dates).max())
    if max_date > as_of:
        raise LookaheadError(f"data contains rows after as_of={as_of.date()}: max date {max_date.date()}")


def slice_as_of(df: pd.DataFrame, as_of, date_col: str = "date") -> pd.DataFrame:
    """Return rows with date <= as_of and verify that nothing later slipped through."""
    as_of = pd.Timestamp(as_of)
    if date_col is None:
        out = df.loc[df.index <= as_of]
    else:
        out = df.loc[pd.to_datetime(df[date_col]) <= as_of]
    assert_no_future(out, as_of, date_col)
    return out


def assert_signal_before_fill(as_of_dates, fill_dates) -> None:
    """Each signal date must be strictly earlier than the date its trades are filled."""
    a = pd.DatetimeIndex(as_of_dates)
    f = pd.DatetimeIndex(fill_dates)
    if len(a) != len(f):
        raise ValueError("as_of_dates and fill_dates must align")
    bad = a >= f
    if bad.any():
        i = int(bad.argmax())
        raise LookaheadError(f"signal date {a[i].date()} is not before fill date {f[i].date()}")
