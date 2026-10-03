"""Synthetic prices / constituents in the exact on-disk format, for tests and dry runs.

Nothing here downloads anything. `write_synthetic_dataset` lets the whole pipeline
(dummy inference -> backtest -> compare) run end-to-end without real market data.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from common.config import cfg_get
from common.paths import Paths


def synthetic_prices(tickers: list[str], start="2023-01-02", end="2025-07-31", seed: int = 0,
                     daily_vol: float = 0.015, drift: float = 0.0002, split_prob: float = 0.0,
                     holiday_prob: float = 0.02, calendar: pd.DatetimeIndex | None = None) -> pd.DataFrame:
    """GBM prices with raw (unadjusted) OHLCV and a cumulative adj_factor. Optional random 2:1
    splits (raw price halves, adj_factor halves so adjusted prices stay continuous) and random
    single-day holidays per ticker (to exercise gap handling)."""
    rng = np.random.default_rng(seed)
    cal = calendar if calendar is not None else pd.bdate_range(start, end)
    n = len(cal)
    frames = []
    for i, t in enumerate(tickers):
        lr = rng.normal(drift, daily_vol, n)
        adj_close = 100.0 * np.exp(np.cumsum(lr)) * rng.uniform(0.5, 2.0)
        gap = rng.normal(0, daily_vol / 3, n)
        adj_open = adj_close * np.exp(gap)
        hi = np.maximum(adj_open, adj_close) * np.exp(np.abs(rng.normal(0, daily_vol / 2, n)))
        lo = np.minimum(adj_open, adj_close) * np.exp(-np.abs(rng.normal(0, daily_vol / 2, n)))
        factor = np.ones(n)
        if split_prob > 0:
            for d in np.flatnonzero(rng.random(n) < split_prob):
                factor[:d] *= 0.5  # before the split the raw price was 2x; adj_factor 0.5 restores continuity
        # raw = adjusted / factor  -> raw * factor == adjusted
        vol = rng.lognormal(12, 0.5, n)
        df = pd.DataFrame({
            "date": cal, "ticker": t,
            "open": adj_open / factor, "high": hi / factor, "low": lo / factor, "close": adj_close / factor,
            "volume": vol * factor, "adj_factor": factor,
        })
        if holiday_prob > 0:
            keep = rng.random(n) >= holiday_prob
            keep[-1] = True
            df = df[keep]
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def synthetic_constituents(tickers: list[str], snapshots: dict[str, list[str]] | None = None,
                           first_date="2023-01-01") -> pd.DataFrame:
    """One snapshot at first_date (all tickers) unless explicit {date: [tickers]} snapshots given."""
    if snapshots is None:
        snapshots = {str(first_date): list(tickers)}
    rows = [(pd.Timestamp(d), t) for d, ts in snapshots.items() for t in ts]
    return pd.DataFrame(rows, columns=["date", "ticker"])


def synthetic_dataset(cfg: dict, n_per_index: int = 30, seed: int = 0, **price_kw):
    """(prices_by_index, constituents_by_index, benchmark_prices) following cfg.universe."""
    indices = list(cfg["universe"]["indices"])
    prices, cons = {}, {}
    cal = pd.bdate_range(price_kw.pop("start", "2023-01-02"), price_kw.pop("end", "2025-07-31"))
    for j, idx in enumerate(indices):
        tks = [f"{idx.upper()}{i:03d}" for i in range(n_per_index)]
        prices[idx] = synthetic_prices(tks, seed=seed + j, calendar=cal, **price_kw)
        # the last two tickers join at a later snapshot -> point-in-time membership is exercised
        late = tks[-2:]
        cons[idx] = synthetic_constituents(tks, {"2023-01-01": tks[:-2], "2025-01-01": tks})
    bench_tks = list((cfg_get(cfg, "data.index_tickers") or {}).values())
    bench = synthetic_prices(bench_tks, seed=seed + 99, calendar=cal, holiday_prob=0.0) if bench_tks else None
    return prices, cons, bench


def write_synthetic_dataset(cfg: dict, root: str | Path, n_per_index: int = 30, seed: int = 0, **price_kw) -> None:
    paths = Paths(cfg, root)
    prices, cons, bench = synthetic_dataset(cfg, n_per_index, seed, **price_kw)
    markets = cfg["universe"].get("markets", {})
    for idx in prices:
        f = paths.price_file(idx)
        f.parent.mkdir(parents=True, exist_ok=True)
        prices[idx].to_parquet(f, index=False)
        paths.constituents_file(idx).parent.mkdir(parents=True, exist_ok=True)
        cons[idx].to_parquet(paths.constituents_file(idx), index=False)
    if bench is not None:
        f = paths.price_file("benchmark")
        f.parent.mkdir(parents=True, exist_ok=True)
        tick_market = {v: markets.get(k) for k, v in (cfg_get(cfg, "data.index_tickers") or {}).items()}
        bench = bench.assign(market=bench["ticker"].map(tick_market))
        bench.to_parquet(f, index=False)
