"""RandomTopK control: K tickers picked by a seeded random score, equal weight. The score is drawn per date
from rng(seed, date) over the sorted ticker list, so the same seed gives the same picks on every run and
across machines, a different seed gives different picks, and the draw never depends on row order."""
from __future__ import annotations

import numpy as np
import pandas as pd

from D_strategy.base import Strategy, equal_weights, require_param, top_k
from D_strategy.registry import register


@register
class RandomTopK(Strategy):
    name = "random_topk"

    def __init__(self, params: dict, seed: int):
        super().__init__(params, seed)
        self.k = int(require_param(self.params, "k", self.name))

    def scores(self, date, tickers) -> pd.Series:
        t = sorted(map(str, tickers))
        rng = np.random.default_rng([self.seed, int(pd.Timestamp(date).value // 10**9)])
        return pd.Series(rng.random(len(t)), index=pd.Index(t, name="ticker"))

    def weights(self, date, signals: pd.DataFrame, prev_w: pd.Series) -> pd.Series:
        return equal_weights(top_k(self.scores(date, signals.index), self.k))
