"""VolTarget: the K tickers with the highest exp_ret, weighted proportionally to 1 / pred_range (the predicted
high-low range relative to the last close), so calmer names get more weight. Base profile, weekly.

A ticker whose pred_range is zero, negative or not finite has no defined weight: it is not eligible, and the K
names are picked among the eligible ones. Parameter k is user-decided (configs strategies.vol_target.k)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from D_strategy.base import Strategy, require_param, top_k
from D_strategy.registry import register


@register
class VolTarget(Strategy):
    name = "vol_target"

    def __init__(self, params: dict, seed: int):
        super().__init__(params, seed)
        self.k = int(require_param(self.params, "k", self.name))
        self.signal_col = str(self.params.get("signal_col", "exp_ret"))

    def weights(self, date, signals: pd.DataFrame, prev_w: pd.Series) -> pd.Series:
        for col in (self.signal_col, "pred_range"):
            if col not in signals.columns:
                raise ValueError(f"{self.name}: signals lack column {col!r}")
        rng = pd.to_numeric(signals["pred_range"], errors="coerce")
        rng.index = rng.index.astype(str)
        eligible = rng[np.isfinite(rng.to_numpy(dtype=float)) & (rng.to_numpy(dtype=float) > 0)]
        scores = pd.to_numeric(signals[self.signal_col], errors="coerce")
        scores.index = scores.index.astype(str)
        picks = sorted(top_k(scores.reindex(eligible.index), self.k))
        if not picks:
            return pd.Series(dtype=float, name="weight")
        inv = 1.0 / eligible.reindex(picks)
        return (inv / inv.sum()).rename("weight").rename_axis("ticker")
