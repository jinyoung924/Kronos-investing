"""ConfidenceWeighted: weight proportional to exp_ret / std (a t-stat-like score) among names
whose score clears `threshold`; per-name cap `max_weight`, the rest stays in cash. Uses the
dispersion of the sampled paths, not only their mean."""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.strategies.base import Strategy


class ConfidenceWeighted(Strategy):
    name = "conf_weighted"

    def __init__(self, threshold: float = 0.5, max_weight: float = 0.1, min_exp_ret: float = 0.0,
                 max_positions: int | None = None, **params):
        super().__init__(threshold=threshold, max_weight=max_weight, min_exp_ret=min_exp_ret,
                         max_positions=max_positions, **params)
        self.threshold, self.max_weight = float(threshold), float(max_weight)
        self.min_exp_ret = float(min_exp_ret)
        self.max_positions = int(max_positions) if max_positions else None

    def weights(self, date, signals: pd.DataFrame, prev_w: pd.Series) -> pd.Series:
        need = {"exp_ret", "std"}
        if not need.issubset(signals.columns):
            return self.finalize(pd.Series(dtype=float))
        s = signals[["exp_ret", "std"]].dropna()
        s = s[(s["std"] > 0) & (s["exp_ret"] > self.min_exp_ret)]
        score = (s["exp_ret"] / s["std"]).replace([np.inf, -np.inf], np.nan).dropna()
        score = score[score >= self.threshold]
        if self.max_positions:
            score = score.nlargest(self.max_positions)
        if score.empty:
            return self.finalize(pd.Series(dtype=float))  # all cash
        w = score / score.sum()
        # iterative cap: excess above max_weight is NOT redistributed (it goes to cash)
        return self.finalize(w, cap=self.max_weight)
