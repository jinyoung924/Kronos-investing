"""ConfidenceWeighted: score = exp_ret / std (expected return per unit of sample dispersion). Tickers with
score >= threshold get weights proportional to their score; no such ticker -> all cash. Base profile, weekly.

A weight must be positive, so a ticker also needs score > 0 (matters only when threshold <= 0). Tickers whose
std is zero, negative or not finite have no score and are not eligible. Parameter threshold is user-decided
(configs strategies.conf_weighted.threshold; null stops the run)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from D_strategy.base import Strategy, require_param
from D_strategy.registry import register


@register
class ConfWeighted(Strategy):
    name = "conf_weighted"

    def __init__(self, params: dict, seed: int):
        super().__init__(params, seed)
        self.threshold = float(require_param(self.params, "threshold", self.name))
        self.signal_col = str(self.params.get("signal_col", "exp_ret"))

    def scores(self, signals: pd.DataFrame) -> pd.Series:
        for col in (self.signal_col, "std"):
            if col not in signals.columns:
                raise ValueError(f"{self.name}: signals lack column {col!r}")
        ret = pd.to_numeric(signals[self.signal_col], errors="coerce").to_numpy(dtype=float)
        std = pd.to_numeric(signals["std"], errors="coerce").to_numpy(dtype=float)
        ok = np.isfinite(ret) & np.isfinite(std) & (std > 0)
        s = np.full(len(signals), np.nan)
        s[ok] = ret[ok] / std[ok]
        return pd.Series(s, index=signals.index.astype(str).rename("ticker"))

    def weights(self, date, signals: pd.DataFrame, prev_w: pd.Series) -> pd.Series:
        s = self.scores(signals).dropna()
        s = s[(s >= self.threshold) & (s > 0)].sort_index()
        if s.empty:
            return pd.Series(dtype=float, name="weight")
        return (s / s.sum()).rename("weight")
