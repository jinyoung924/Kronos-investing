"""Transaction cost model (docs/spec.md Stage 4 task 1, docs/outline.md E_backtest).

    buy_cost_rate(date, market)  = commission_buy  + slippage
    sell_cost_rate(date, market) = commission_sell + slippage + sell_tax(date, market)
Scenarios (configs costs.*):
    kr    : Korean costs; sell_tax from costs.sell_tax_table = [{market, start, rate}, ...], the latest `start` <= date
            for that market applies. An empty table is a hard error (the rate is a user-verified fact).
    paper : the original paper's qlib setting, buy costs.paper.buy, sell costs.paper.sell, no tax, no slippage.
    none  : every rate 0 (run_backtest --no-costs).
Pure; no file IO.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from common.config import ConfigError, cfg_get, require

SCENARIOS = ("kr", "paper", "none")


class CostModel:
    def __init__(self, cfg: dict, scenario: str | None = None):
        self.scenario = str(scenario or cfg_get(cfg, "costs.scenario", "kr"))
        if self.scenario not in SCENARIOS:
            raise ValueError(f"costs scenario must be one of {SCENARIOS}, got {self.scenario!r}")
        self._tax: dict[str, list[tuple[pd.Timestamp, float]]] = {}
        if self.scenario == "kr":
            self.buy_base = float(require(cfg, "costs.commission_buy")) + float(require(cfg, "costs.slippage_bps")) / 1e4
            self.sell_base = float(require(cfg, "costs.commission_sell")) + float(require(cfg, "costs.slippage_bps")) / 1e4
            table = cfg_get(cfg, "costs.sell_tax_table")
            if not table:
                raise ConfigError("costs.sell_tax_table is empty: fill [{market, start, rate}, ...] in configs/base.yaml "
                                  "(user-verified securities transaction tax), or run with --costs paper / --no-costs")
            for row in table:
                for k in ("market", "start", "rate"):
                    if row.get(k) is None:
                        raise ConfigError(f"costs.sell_tax_table row {row} lacks {k!r}")
                self._tax.setdefault(str(row["market"]).upper(), []).append((pd.Timestamp(row["start"]).normalize(), float(row["rate"])))
            for m in self._tax:
                self._tax[m].sort()
        elif self.scenario == "paper":
            self.buy_base = float(require(cfg, "costs.paper.buy"))
            self.sell_base = float(require(cfg, "costs.paper.sell"))
        else:
            self.buy_base = self.sell_base = 0.0

    # ---- scalar API (spec names) ----------------------------------------------------------------
    def sell_tax(self, date, market: str) -> float:
        if self.scenario != "kr":
            return 0.0
        rows = self._tax.get(str(market).upper())
        if not rows:
            raise ConfigError(f"costs.sell_tax_table has no rows for market {market!r}")
        d = pd.Timestamp(date).normalize()
        rate = None
        for start, r in rows:
            if start <= d:
                rate = r
        if rate is None:
            raise ConfigError(f"costs.sell_tax_table has no rate for market {market!r} effective on {d.date()} (first start {rows[0][0].date()})")
        return rate

    def buy_cost_rate(self, date, market: str) -> float:
        return self.buy_base

    def sell_cost_rate(self, date, market: str) -> float:
        return self.sell_base + self.sell_tax(date, market)

    # ---- vector API used by the engines ----------------------------------------------------------
    def rates(self, date, markets) -> tuple[np.ndarray, np.ndarray]:
        """(buy_rate, sell_rate) arrays aligned with `markets` (one entry per ticker)."""
        markets = np.asarray(markets, dtype=object)
        buy = np.full(len(markets), self.buy_base, dtype=float)
        if self.scenario != "kr":
            return buy, np.full(len(markets), self.sell_base, dtype=float)
        tax = {m: self.sell_tax(date, m) for m in set(markets.tolist())}
        sell = np.array([self.sell_base + tax[m] for m in markets], dtype=float)
        return buy, sell

    def describe(self) -> dict:
        return {"scenario": self.scenario, "buy_base": self.buy_base, "sell_base": self.sell_base,
                "sell_tax_table": {m: [(str(s.date()), r) for s, r in rows] for m, rows in self._tax.items()}}
