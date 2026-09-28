"""VolTarget: top-K by exp_ret, weighted by the inverse of the predicted intraday range
(pred_range = mean(pred_high - pred_low) / last_close). Optional `target_range` scales total
exposure down when the weighted predicted range exceeds it (uses the model's risk view)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.strategies.base import Strategy


class VolTarget(Strategy):
    name = "vol_target"

    def __init__(self, k: int = 20, target_range: float | None = None, range_floor: float = 1e-4, **params):
        super().__init__(k=k, target_range=target_range, range_floor=range_floor, **params)
        self.k, self.range_floor = int(k), float(range_floor)
        self.target_range = float(target_range) if target_range is not None else None

    def weights(self, date, signals: pd.DataFrame, prev_w: pd.Series) -> pd.Series:
        need = {"exp_ret", "pred_range"}
        if not need.issubset(signals.columns):
            return self.finalize(pd.Series(dtype=float))
        s = signals[["exp_ret", "pred_range"]].dropna()
        top = s.nlargest(self.k, "exp_ret")
        if top.empty:
            return self.finalize(pd.Series(dtype=float))
        rng = top["pred_range"].clip(lower=self.range_floor)
        inv = 1.0 / rng
        w = inv / inv.sum()
        if self.target_range is not None:
            port_range = float((w * rng).sum())
            exposure = min(1.0, self.target_range / port_range) if port_range > 0 else 1.0
            w = w * exposure
        return self.finalize(w)
