"""TopK: the paper's investment simulation (docs/spec.md appendix D), Qlib TopkDropoutStrategy with
topk = k, n_drop, hold_thresh = hold_min_days, method_sell bottom, method_buy top. Daily, paper profile.

State = the tickers it holds and how many trading days each has been held (its own past outputs only).
Per day, with scores = signal_col (ties break on ticker, non-finite scores rank last and are never bought):
    last  = held tickers, best first
    today = the best n_drop + k - |last| tickers that are not held
    comb  = last + today, best first
    sell  = held tickers among the worst n_drop of comb that have been held >= hold_min_days
    buy   = the best |sell| + k - |last| of today, 1/k each
Returns buy -> 1/k, kept holdings -> NaN (hold: no trade), sold tickers are left out (target 0).
The first day buys the best k at 1/k. A held ticker with no signal row that day leaves the book (the output
cannot name it, so the engine sells it) whatever its holding age.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from D_strategy.base import Strategy, require_param
from D_strategy.registry import register


@register
class TopK(Strategy):
    name = "topk"

    def __init__(self, params: dict, seed: int):
        super().__init__(params, seed)
        self.k = int(require_param(self.params, "k", self.name))
        self.n_drop = int(require_param(self.params, "n_drop", self.name))
        self.hold_min_days = int(require_param(self.params, "hold_min_days", self.name))
        self.signal_col = str(require_param(self.params, "signal_col", self.name))
        if self.k < 1 or self.n_drop < 0 or self.hold_min_days < 0:
            raise ValueError(f"{self.name}: need k >= 1, n_drop >= 0, hold_min_days >= 0")
        self.days_held: dict[str, int] = {}

    def reset(self) -> None:
        self.days_held = {}

    def weights(self, date, signals: pd.DataFrame, prev_w: pd.Series) -> pd.Series:
        if self.signal_col not in signals.columns:
            raise ValueError(f"{self.name}: signals lack column {self.signal_col!r}")
        raw = pd.to_numeric(signals[self.signal_col], errors="coerce").to_numpy(dtype=float)
        score = {str(t): (v if math.isfinite(v) else -math.inf) for t, v in zip(signals.index, raw)}

        def ranked(tickers):
            return sorted(tickers, key=lambda t: (-score[t], t))

        self.days_held = {t: d + 1 for t, d in self.days_held.items() if t in score}
        last = ranked(self.days_held)
        candidates = ranked(t for t, v in score.items() if t not in self.days_held and v > -math.inf)
        today = candidates[: max(0, self.n_drop + self.k - len(last))]
        comb = ranked(last + today)
        bottom = set(comb[-self.n_drop:]) if self.n_drop > 0 else set()
        sell = [t for t in last if t in bottom and self.days_held[t] >= self.hold_min_days]
        buy = today[: max(0, len(sell) + self.k - len(last))]

        for t in sell:
            del self.days_held[t]
        out = {t: np.nan for t in self.days_held}
        for t in buy:
            self.days_held[t] = 0
            out[t] = 1.0 / self.k
        return pd.Series(out, dtype=float, name="weight").rename_axis("ticker")
