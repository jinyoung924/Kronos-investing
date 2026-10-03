"""EqualWeight benchmark: every ticker with a signal that day, equal weight, fully invested."""
from __future__ import annotations

import pandas as pd

from D_strategy.base import Strategy, equal_weights
from D_strategy.registry import register


@register
class EqualWeight(Strategy):
    name = "equal_weight"

    def weights(self, date, signals: pd.DataFrame, prev_w: pd.Series) -> pd.Series:
        return equal_weights(signals.index)
