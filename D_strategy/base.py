"""Strategy interface (docs/outline.md D_strategy, docs/spec.md Stage 3).

A strategy sees one day's SIGNALS (ticker x signal columns) and its own previous target weights, never
prices or labels. It returns ticker -> target weight: values >= 0, non-NaN values sum to <= 1 (the rest is
cash), NaN = hold (keep the drifted position, no trade). run_strategy calls reset() once, then weights()
once per rebalance date in ascending order; stateful strategies may remember only their own past outputs.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np
import pandas as pd

WEIGHT_SUM_TOL = 1e-9


def require_param(params: dict, key: str, strategy: str):
    """A strategy parameter that the user must set (configs strategies.<name>.<key>); null stops the run."""
    v = params.get(key)
    if v is None:
        raise ValueError(f"strategies.{strategy}.{key} is null: set it in configs/base.yaml")
    return v


class Strategy(ABC):
    name: str = ""          # == file name == cfg.strategies key (checked by the registry)
    profile: str            # inference profile of the predictions this strategy uses (base | paper)
    schedule: str           # weekly (H spacing) | daily

    def __init__(self, params: dict, seed: int):
        self.params = dict(params or {})
        self.seed = int(seed)
        self.profile = str(require_param(self.params, "profile", self.name))
        self.schedule = str(require_param(self.params, "schedule", self.name))
        if self.schedule not in ("weekly", "daily"):
            raise ValueError(f"strategies.{self.name}.schedule must be weekly or daily, got {self.schedule!r}")

    def reset(self) -> None:
        """Called once before the first weights() call. Stateful strategies clear their state here."""

    @abstractmethod
    def weights(self, date, signals: pd.DataFrame, prev_w: pd.Series) -> pd.Series:
        """signals: that date's rows indexed by ticker. prev_w: previous TARGET weights (not actual holdings).
        Returns ticker -> target weight (>= 0, non-NaN sum <= 1, NaN = hold)."""


# ---------------------------------------------------------------------------------------------
# helpers shared by strategy files
# ---------------------------------------------------------------------------------------------
def equal_weights(tickers, exposure: float = 1.0) -> pd.Series:
    """1/n each over `tickers` (sorted for a stable order); empty -> empty series (all cash)."""
    t = sorted(set(map(str, tickers)))
    if not t:
        return pd.Series(dtype=float, name="weight")
    return pd.Series(exposure / len(t), index=pd.Index(t, name="ticker"), name="weight")


def top_k(scores: pd.Series, k: int) -> list[str]:
    """Tickers of the k largest finite scores. Ties break on ticker (ascending) so runs are reproducible."""
    k = int(k)
    if k < 1:
        raise ValueError("k must be >= 1")
    s = pd.to_numeric(scores, errors="coerce")
    s = s[np.isfinite(s.to_numpy(dtype=float))]
    if s.empty:
        return []
    order = sorted(zip(-s.to_numpy(dtype=float), map(str, s.index)))
    return [t for _, t in order[:k]]


def check_weights(w: pd.Series, signals: pd.DataFrame, strategy: str, date=None) -> pd.Series:
    """Enforce the contract: tickers ⊆ that day's signals, no negatives, non-NaN sum <= 1."""
    w = pd.Series(w, dtype=float).rename("weight")
    w.index = w.index.astype(str).rename("ticker")
    tag = f"{strategy}@{pd.Timestamp(date).date()}" if date is not None else strategy
    if w.index.has_duplicates:
        raise ValueError(f"{tag}: duplicate tickers in weights")
    extra = sorted(set(w.index) - set(map(str, signals.index)))
    if extra:
        raise ValueError(f"{tag}: weights for tickers without signals that day, e.g. {extra[:3]}")
    if (w.dropna() < 0).any():
        raise ValueError(f"{tag}: negative weight")
    if np.isinf(w.to_numpy(dtype=float)).any():
        raise ValueError(f"{tag}: infinite weight")
    total = float(w.dropna().sum())
    if total > 1 + WEIGHT_SUM_TOL:
        raise ValueError(f"{tag}: non-NaN weights sum to {total:.6f} > 1")
    return w
