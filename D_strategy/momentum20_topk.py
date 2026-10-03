"""Momentum20 TopK benchmark: the K tickers with the highest mom20 (20-day adjusted return), equal weight.
Tickers without a finite mom20 are not eligible. Parameter k is user-decided (configs strategies.momentum20_topk.k)."""
from __future__ import annotations

import pandas as pd

from D_strategy.base import Strategy, equal_weights, require_param, top_k
from D_strategy.registry import register


@register
class Momentum20TopK(Strategy):
    name = "momentum20_topk"

    def __init__(self, params: dict, seed: int):
        super().__init__(params, seed)
        self.k = int(require_param(self.params, "k", self.name))
        self.signal_col = str(self.params.get("signal_col", "mom20"))

    def weights(self, date, signals: pd.DataFrame, prev_w: pd.Series) -> pd.Series:
        if self.signal_col not in signals.columns:
            raise ValueError(f"{self.name}: signals lack column {self.signal_col!r}")
        return equal_weights(top_k(signals[self.signal_col], self.k))
