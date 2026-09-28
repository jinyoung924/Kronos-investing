"""Strategy registry: load a strategy by its `name`."""
from __future__ import annotations

from backtest.strategies.base import Strategy
from backtest.strategies.baselines import EqualWeight, IndexBuyHold, Momentum20TopK, RandomSignal
from backtest.strategies.conf_weighted import ConfidenceWeighted
from backtest.strategies.topk import TopK
from backtest.strategies.vol_target import VolTarget

REGISTRY: dict[str, type[Strategy]] = {}


def register(cls: type[Strategy]) -> type[Strategy]:
    if not cls.name:
        raise ValueError(f"{cls.__name__} has no name")
    if cls.name in REGISTRY and REGISTRY[cls.name] is not cls:
        raise ValueError(f"duplicate strategy name {cls.name}")
    REGISTRY[cls.name] = cls
    return cls


for _cls in (TopK, ConfidenceWeighted, VolTarget, EqualWeight, Momentum20TopK, RandomSignal, IndexBuyHold):
    register(_cls)

KRONOS_STRATEGIES = ("topk", "conf_weighted", "vol_target")
BENCHMARK_STRATEGIES = ("equal_weight", "momentum20", "random", "index_buy_hold")


def available() -> list[str]:
    return sorted(REGISTRY)


def get_strategy(name: str, **params) -> Strategy:
    if name not in REGISTRY:
        raise KeyError(f"unknown strategy {name!r}; available: {available()}")
    return REGISTRY[name](**params)


def uses_predictions(name: str) -> bool:
    return name in KRONOS_STRATEGIES


__all__ = ["Strategy", "REGISTRY", "register", "available", "get_strategy", "uses_predictions",
           "KRONOS_STRATEGIES", "BENCHMARK_STRATEGIES"]
