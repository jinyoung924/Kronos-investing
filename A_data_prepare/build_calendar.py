"""Stage 1: trading calendar and per-(date, ticker) halt flags.

calendar: one row per KRX trading day (`date`). The union of dates across both exchange files;
          sanity gate 3 (docs/data_pipeline.md §6) guarantees stocks, indices and ETFs share it.
halts:    date, ticker, is_halted for every price row. Rule confirmed by scripts/probes/stage1_halts.py:
          a halted / no-trade day comes back from KRX with open = high = low = "0" and volume "0" while the
          close carries the reference price. The collected file already encodes this as open/high/low = NaN
          and `halted = True`; the flag is recomputed here from the prices so it never drifts from them.
Pure functions only; file IO lives in run_prepare.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from common.calendar import as_calendar


def build_calendar(prices: pd.DataFrame) -> pd.DataFrame:
    """DataFrame(date) of all trading days present in the prepared prices."""
    return pd.DataFrame({"date": as_calendar(prices["date"])})


def halt_flag(prices: pd.DataFrame) -> pd.Series:
    """True where nothing printed: volume 0 (or missing) or no valid open. Mirrors A_data_prepare.transform.stock_prices."""
    vol0 = prices["volume"].fillna(0) <= 0
    no_open = prices["open"].isna() | (prices["open"] <= 0)
    return (vol0 | no_open).rename("is_halted")


def build_halts(prices: pd.DataFrame, raw_halted: pd.Series | None = None) -> pd.DataFrame:
    """DataFrame(date, ticker, is_halted). If the collected `halted` column is passed it must agree."""
    flag = halt_flag(prices)
    if raw_halted is not None:
        rh = np.asarray(raw_halted, dtype=bool)
        if len(rh) != len(flag) or not np.array_equal(rh, flag.to_numpy()):
            n = int((rh != flag.to_numpy()).sum()) if len(rh) == len(flag) else -1
            raise ValueError(f"recomputed halt flag disagrees with the collected `halted` column on {n} rows")
    out = pd.DataFrame({"date": prices["date"].to_numpy(), "ticker": prices["ticker"].to_numpy(), "is_halted": flag.to_numpy()})
    return out.sort_values(["ticker", "date"]).reset_index(drop=True)
