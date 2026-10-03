"""Build a same-length batch of input windows for one as_of date.

All windows end at (or, for a market holiday, shortly before) as_of and are cut with
`slice_as_of`, which raises if a future row is present. Prices in the window are rebased with
adj_factor / adj_factor[last row] so that splits inside the lookback do not create jumps while
the last close stays equal to the raw close at as_of (predictions are on the raw scale, D-1).

Eligibility (D-3): `qualify_window` is the single place that decides whether a ticker gets a
prediction on an as_of date (enough history, not stale, no NaN bar in the window, positive prices).
build_batch and the oracle fake predictions both go through it, so every run_id, real or fake, drops the
same tickers for the same reasons.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from common.lookahead import LookaheadError, assert_no_future, slice_as_of

INPUT_COLS = ["open", "high", "low", "close", "volume", "amount"]
WINDOW_COLS = ["open", "high", "low", "close", "volume"]


@dataclass
class Batch:
    as_of: pd.Timestamp
    tickers: list[str]
    df_list: list[pd.DataFrame]          # each: lookback rows x INPUT_COLS
    x_timestamps: list[pd.Series]        # each: lookback datetimes (<= as_of)
    y_timestamps: list[pd.Series]        # each: horizon datetimes (> as_of)
    last_close: np.ndarray               # raw close of the last row (== rebased close)
    skipped: dict[str, str] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.tickers)


def future_timestamps(as_of, horizon: int) -> pd.Series:
    """Business-day stamps after as_of. Only calendar arithmetic, no price data involved."""
    start = pd.Timestamp(as_of).normalize() + pd.offsets.BDay(1)
    return pd.Series(pd.bdate_range(start, periods=horizon))


def qualify_window(hist: pd.DataFrame, lookback: int, as_of: pd.Timestamp, max_stale_days: int) -> tuple[pd.DataFrame | None, str]:
    """Return (window, "") when `hist` (one ticker, rows dated <= as_of, sorted) can feed the model, else (None, reason).

    reasons: insufficient_history(n<lookback) | stale(last=YYYY-MM-DD) | nan_in_window | nonpositive_price"""
    if len(hist) < lookback:
        return None, f"insufficient_history({len(hist)}<{lookback})"
    w = hist.iloc[-lookback:]
    last_date = pd.Timestamp(w["date"].iloc[-1])
    if last_date > as_of:  # belt and braces; slice_as_of already guarantees this
        raise LookaheadError(f"window ends after as_of: {last_date} > {as_of}")
    if (as_of - last_date).days > max_stale_days:
        return None, f"stale(last={last_date.date()})"
    if w[WINDOW_COLS].isna().any().any():
        return None, "nan_in_window"
    if (w[["open", "high", "low", "close"]] <= 0).any().any():
        return None, "nonpositive_price"
    return w, ""


def eligible_tickers(prices: pd.DataFrame, tickers, as_of, lookback: int, max_stale_days: int = 5) -> tuple[list[str], dict[str, str]]:
    """(eligible tickers in input order, {ticker: reason} for the rest) using exactly build_batch's rule."""
    as_of = pd.Timestamp(as_of).normalize()
    tickers = [str(t) for t in tickers]
    hist_all = slice_as_of(prices[prices["ticker"].isin(tickers)], as_of, "date")
    groups = {t: g.sort_values("date") for t, g in hist_all.groupby("ticker", sort=False)}
    ok, skipped = [], {}
    for t in tickers:
        hist = groups.get(t)
        if hist is None:
            skipped[t] = "no_data"
            continue
        w, reason = qualify_window(hist, lookback, as_of, max_stale_days)
        (ok.append(t) if w is not None else skipped.__setitem__(t, reason))
    return ok, skipped


def build_batch(prices: pd.DataFrame, tickers, as_of, lookback: int, horizon: int,
                max_stale_days: int = 5) -> Batch:
    """prices: long frame (date, ticker, open, high, low, close, volume, adj_factor)."""
    as_of = pd.Timestamp(as_of).normalize()
    tickers = [str(t) for t in tickers]
    hist_all = slice_as_of(prices[prices["ticker"].isin(tickers)], as_of, "date")
    assert_no_future(hist_all, as_of, "date")
    groups = {t: g.sort_values("date") for t, g in hist_all.groupby("ticker", sort=False)}
    y_ts = future_timestamps(as_of, horizon)

    out = Batch(as_of=as_of, tickers=[], df_list=[], x_timestamps=[], y_timestamps=[], last_close=np.array([]))
    last_closes = []
    for t in tickers:
        hist = groups.get(t)
        if hist is None:
            out.skipped[t] = "no_data"
            continue
        w, reason = qualify_window(hist, lookback, as_of, max_stale_days)
        if w is None:
            out.skipped[t] = reason
            continue
        factor = (w["adj_factor"] / w["adj_factor"].iloc[-1]).to_numpy()
        df = pd.DataFrame(index=range(lookback))
        for c in ("open", "high", "low", "close"):
            df[c] = w[c].to_numpy() * factor
        df["volume"] = w["volume"].to_numpy() / factor
        df["amount"] = w["close"].to_numpy() * w["volume"].to_numpy()  # invariant to splits
        out.tickers.append(t)
        out.df_list.append(df[INPUT_COLS].astype(np.float64))
        out.x_timestamps.append(pd.Series(pd.to_datetime(w["date"].to_numpy())))
        out.y_timestamps.append(y_ts.copy())
        last_closes.append(float(w["close"].iloc[-1]))
    out.last_close = np.asarray(last_closes, dtype=float)
    return out
