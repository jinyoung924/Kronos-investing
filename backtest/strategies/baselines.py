"""Benchmark strategies sharing the Strategy interface."""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.strategies.base import Strategy


class EqualWeight(Strategy):
    """Equal weight across every investable name in the point-in-time universe."""
    name = "equal_weight"

    def weights(self, date, signals: pd.DataFrame, prev_w: pd.Series) -> pd.Series:
        tickers = signals.index
        if "last_close" in signals.columns:
            tickers = signals.index[signals["last_close"].notna()]
        return self.finalize(self.equal(tickers))


class Momentum20TopK(Strategy):
    """Top-K by trailing `mom20` (price-only signal, no model)."""
    name = "momentum20"

    def __init__(self, k: int = 20, signal: str = "mom20", **params):
        super().__init__(k=k, signal=signal, **params)
        self.k, self.signal = int(k), signal

    def weights(self, date, signals: pd.DataFrame, prev_w: pd.Series) -> pd.Series:
        if self.signal not in signals.columns:
            return self.finalize(pd.Series(dtype=float))
        chosen = signals[self.signal].dropna().nlargest(self.k).index
        return self.finalize(self.equal(chosen))


class RandomSignal(Strategy):
    """Top-K of a random signal. Deterministic per (seed, date). Net of costs it should track
    EqualWeight; a persistent gap flags a universe or cost bias in the pipeline."""
    name = "random"

    def __init__(self, k: int = 20, seed: int = 42, **params):
        super().__init__(k=k, seed=seed, **params)
        self.k, self.seed = int(k), int(seed)

    def weights(self, date, signals: pd.DataFrame, prev_w: pd.Series) -> pd.Series:
        tickers = signals.index
        if "last_close" in signals.columns:
            tickers = signals.index[signals["last_close"].notna()]
        if len(tickers) == 0:
            return self.finalize(pd.Series(dtype=float))
        rng = np.random.default_rng([self.seed, int(pd.Timestamp(date).toordinal())])
        score = pd.Series(rng.random(len(tickers)), index=tickers)
        return self.finalize(self.equal(score.nlargest(self.k).index))


class IndexBuyHold(Strategy):
    """Hold the configured index ETF(s). With one ticker this is exact buy-and-hold; with several,
    the target is re-set to equal weight at each rebalance (tiny trades between the ETFs)."""
    name = "index_buy_hold"

    def __init__(self, tickers=None, **params):
        super().__init__(tickers=tickers, **params)
        self.tickers = list(tickers or [])

    def weights(self, date, signals: pd.DataFrame, prev_w: pd.Series) -> pd.Series:
        if len(self.tickers) == 1:
            if prev_w is not None and float(prev_w.sum()) > 0:
                return self.finalize(prev_w)  # keep what we hold: no trade
        return self.finalize(self.equal(self.tickers))
