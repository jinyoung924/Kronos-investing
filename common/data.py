"""Price data loading helpers for the collected (data/raw) format.

Long price format (one row per date x ticker):
    date, ticker, open, high, low, close, volume, adj_factor, market, index

* open/high/low/close/volume are RAW (unadjusted) exchange prices.
* adj_factor is the cumulative split/dividend factor such that raw * adj_factor is a
  total-return-comparable price series. Only RATIOS of adj_factor between two dates are ever
  used, and a ratio between dates s < t only depends on corporate actions between s and t,
  which are known at t. Rebasing to as_of (factor / factor[as_of]) is therefore point-in-time.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from common.calendar import rebalance_dates as _rebalance_dates
from common.config import cfg_get
from common.paths import Paths

PRICE_COLUMNS = ["date", "ticker", "open", "high", "low", "close", "volume", "adj_factor"]


def load_prices(cfg: dict, root: str | Path = ".", indices: Iterable[str] | None = None,
                include_benchmark: bool = True) -> pd.DataFrame:
    """Concatenate data/raw/{index}/prices.parquet for the configured indices (+ benchmark ETFs)."""
    paths = Paths(cfg, root)
    indices = list(indices) if indices is not None else list(cfg_get(cfg, "universe.indices", []))
    markets = cfg_get(cfg, "universe.markets", {})
    frames = []
    for idx in indices:
        f = paths.price_file(idx)
        if not f.exists():
            raise FileNotFoundError(f"missing price file {f}; place raw OHLCV + adj_factor parquet there")
        df = pd.read_parquet(f)
        df["index"] = idx
        if idx in markets:
            df["market"] = markets[idx]
        frames.append(df)
    if include_benchmark:
        f = paths.price_file("benchmark")
        if f.exists():
            df = pd.read_parquet(f)
            df["index"] = "benchmark"
            frames.append(df)
    if not frames:
        raise FileNotFoundError("no price files found")
    prices = pd.concat(frames, ignore_index=True)
    return normalize_prices(prices)


def normalize_prices(prices: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in PRICE_COLUMNS if c not in prices.columns]
    if missing:
        raise ValueError(f"price frame missing columns {missing}")
    out = prices.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.normalize()
    out["ticker"] = out["ticker"].astype(str)
    if "adj_factor" not in out or out["adj_factor"].isna().all():
        out["adj_factor"] = 1.0
    out["adj_factor"] = out["adj_factor"].fillna(1.0).astype(float)
    out = out.sort_values(["ticker", "date"]).drop_duplicates(["ticker", "date"], keep="last")
    return out.reset_index(drop=True)


def to_wide(prices: pd.DataFrame, col: str) -> pd.DataFrame:
    """date x ticker matrix for one column (NaN where a ticker has no bar)."""
    return prices.pivot(index="date", columns="ticker", values=col).sort_index()


def ticker_market_map(prices: pd.DataFrame) -> dict[str, str]:
    if "market" not in prices.columns:
        return {}
    return prices.drop_duplicates("ticker").set_index("ticker")["market"].to_dict()


def trading_calendar(prices: pd.DataFrame) -> pd.DatetimeIndex:
    """Union of all trading dates across tickers (KR and US calendars differ)."""
    return pd.DatetimeIndex(sorted(prices["date"].unique()))


# rebalance_dates lives in common.calendar; re-exported here for existing callers (run_inference, tests).
rebalance_dates = _rebalance_dates


def ffill_wide(wide: pd.DataFrame, limit: int | None) -> pd.DataFrame:
    """Forward-fill holiday gaps. Filling uses only PAST values, so it is lookahead-safe."""
    return wide.ffill(limit=limit) if limit else wide.ffill()


def adjusted_wide(prices: pd.DataFrame, col: str, ffill_limit: int | None = None) -> pd.DataFrame:
    """raw[col] * adj_factor as a date x ticker matrix (forward-filled)."""
    raw = to_wide(prices, col)
    adj = to_wide(prices, "adj_factor").reindex_like(raw)
    return ffill_wide(raw * adj, ffill_limit)
