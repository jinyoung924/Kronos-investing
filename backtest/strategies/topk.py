"""TopK: equal-weight the K names with the highest expected return (uses only the ranking)."""
from __future__ import annotations

import pandas as pd

from backtest.strategies.base import Strategy


class TopK(Strategy):
    name = "topk"

    def __init__(self, k: int = 20, signal: str = "exp_ret", exposure: float = 1.0, **params):
        super().__init__(k=k, signal=signal, exposure=exposure, **params)
        self.k, self.signal, self.exposure = int(k), signal, float(exposure)

    def weights(self, date, signals: pd.DataFrame, prev_w: pd.Series) -> pd.Series:
        if self.signal not in signals.columns:
            return self.finalize(pd.Series(dtype=float))
        s = signals[self.signal].dropna()
        chosen = s.nlargest(self.k).index
        return self.finalize(self.equal(chosen, self.exposure))
