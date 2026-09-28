"""Single strategy interface. One class per file; registered by name in strategies/__init__.py."""
from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np
import pandas as pd


class Strategy(ABC):
    name: str = ""

    def __init__(self, **params):
        self.params = dict(params)

    @abstractmethod
    def weights(self, date: pd.Timestamp, signals: pd.DataFrame, prev_w: pd.Series) -> pd.Series:
        """signals: ticker x feature frame for `date` (only information known at the close of
        `date`). Returns ticker -> target weight, all >= 0, sum <= 1 (remainder is cash)."""

    # ---- helpers shared by implementations ---------------------------------------------
    @staticmethod
    def finalize(w: pd.Series, cap: float | None = None) -> pd.Series:
        """Drop non-positive weights, cap per-name weight (excess goes to cash), enforce sum <= 1."""
        w = w.astype(float).replace([np.inf, -np.inf], np.nan).dropna()
        w = w[w > 0]
        if cap is not None:
            w = w.clip(upper=cap)
        total = float(w.sum())
        if total > 1.0 + 1e-12:
            w = w / total
        w.index.name = "ticker"
        return w.rename("weight")

    @staticmethod
    def equal(tickers, exposure: float = 1.0) -> pd.Series:
        tickers = list(tickers)
        if not tickers:
            return pd.Series(dtype=float, name="weight")
        return pd.Series(exposure / len(tickers), index=pd.Index(tickers, name="ticker"), name="weight")

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.params})"
