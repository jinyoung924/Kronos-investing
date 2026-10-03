"""Price-only features for benchmark strategies and naive controls, from ADJUSTED close (raw * F) using
rows dated <= as_of only (rolling windows look backwards; verified by the future-invariance test).

    mom{W} = close_t / close_{t-W} - 1            (W = signal.mom_window, default 20)
    vol{W} = std of simple daily returns over W rows (pandas ddof 1, no NaN padding; W = signal.vol_window)
    rev{W} = -(close_t / close_{t-W} - 1)         (W = signal.rev_window, default 5)
Windows count trading-day rows of the price table. Pure functions only.
"""
from __future__ import annotations

import pandas as pd

from common.data import ffill_wide, to_wide


def feature_names(mom_window: int, vol_window: int, rev_window: int) -> dict[str, str]:
    return {"mom": f"mom{int(mom_window)}", "vol": f"vol{int(vol_window)}", "rev": f"rev{int(rev_window)}"}


def adjusted_close_wide(prices: pd.DataFrame, adj_factor: pd.DataFrame, ffill_limit: int | None = None) -> pd.DataFrame:
    """date x ticker adjusted close (raw close * F). Gaps (no bar) stay NaN unless ffill_limit is given."""
    close = to_wide(prices, "close")
    f = to_wide(adj_factor, "factor").reindex_like(close)
    return ffill_wide(close * f, ffill_limit) if ffill_limit else close * f


def baseline_features(prices: pd.DataFrame, adj_factor: pd.DataFrame, as_of_dates, mom_window: int = 20,
                      vol_window: int = 20, rev_window: int = 5, ffill_limit: int | None = None) -> pd.DataFrame:
    """Long frame (as_of_date, ticker, mom{W}, vol{W}, rev{W}) for every ticker at every as_of date."""
    names = feature_names(mom_window, vol_window, rev_window)
    c = adjusted_close_wide(prices, adj_factor, ffill_limit)
    feats = {
        names["mom"]: c / c.shift(int(mom_window)) - 1.0,
        names["vol"]: c.pct_change(fill_method=None).rolling(int(vol_window)).std(),
        names["rev"]: -(c / c.shift(int(rev_window)) - 1.0),
    }
    dates = pd.DatetimeIndex(pd.to_datetime(list(as_of_dates))).normalize()
    missing = dates.difference(c.index)
    if len(missing):
        raise ValueError(f"as_of dates not in the price calendar: {list(missing[:3])}")
    parts = [w.reindex(dates).stack(future_stack=True).rename(n) for n, w in feats.items()]
    out = pd.concat(parts, axis=1)
    out.index.names = ["as_of_date", "ticker"]
    return out.reset_index()
