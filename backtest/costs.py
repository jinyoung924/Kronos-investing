"""Per-market transaction costs applied per ticker."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np

from common.config import cfg_get


@dataclass(frozen=True)
class MarketCost:
    commission: float = 0.0   # charged on buys and sells
    sell_tax: float = 0.0     # charged on sells only
    slippage: float = 0.0     # charged on buys and sells

    @property
    def buy_rate(self) -> float:
        return self.commission + self.slippage

    @property
    def sell_rate(self) -> float:
        return self.commission + self.sell_tax + self.slippage


class CostModel:
    def __init__(self, market_costs: Mapping[str, MarketCost], ticker_market: Mapping[str, str] | None = None,
                 default_market: str | None = None):
        self.market_costs = dict(market_costs)
        self.ticker_market = dict(ticker_market or {})
        self.default_market = default_market

    @classmethod
    def from_config(cls, cfg: dict, ticker_market: Mapping[str, str] | None = None) -> "CostModel":
        raw = cfg_get(cfg, "backtest.costs", {}) or {}
        mc = {m: MarketCost(**{k: float(v) for k, v in spec.items()}) for m, spec in raw.items()}
        return cls(mc, ticker_market, cfg_get(cfg, "backtest.default_market"))

    @classmethod
    def zero(cls) -> "CostModel":
        return cls({}, {}, None)

    def _cost(self, ticker: str) -> MarketCost:
        market = self.ticker_market.get(ticker, self.default_market)
        if market is None or market not in self.market_costs:
            if self.market_costs and market is not None:
                raise KeyError(f"no cost spec for market {market!r} (ticker {ticker})")
            return MarketCost()
        return self.market_costs[market]

    def buy_rates(self, tickers) -> np.ndarray:
        return np.array([self._cost(t).buy_rate for t in tickers], dtype=float)

    def sell_rates(self, tickers) -> np.ndarray:
        return np.array([self._cost(t).sell_rate for t in tickers], dtype=float)
