"""Shared synthetic fixtures. No network, no real data."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from common.config import cfg_override, load_config
from common.synthetic import synthetic_dataset, write_synthetic_dataset

ROOT = Path(__file__).resolve().parents[1]

TEST_OVERRIDES = {
    "universe.indices": ["kospi200", "sp500"], "universe.markets": {"kospi200": "KR", "sp500": "US"},
    "benchmark.index_tickers": {"kospi200": "069500", "sp500": "SPY"},
    "run.start": "2024-07-01", "run.end": "2025-06-30", "run.step": 5,
    "model.lookback": 60, "model.horizon": 5, "model.sample_count": 8,
    "metrics.bootstrap": {"n": 200, "block": 10, "seed": 0},
    "strategies.topk": {"k": 10}, "strategies.random": {"k": 10, "seed": 42}, "strategies.momentum20": {"k": 10},
    "strategies.vol_target": {"k": 10, "target_range": None},
}


@pytest.fixture(scope="session")
def cfg() -> dict:
    return cfg_override(load_config(ROOT / "configs/base.yaml"), TEST_OVERRIDES)


@pytest.fixture(scope="session")
def dataset(cfg):
    """(prices long frame incl. market/index, constituents dict, benchmark prices)."""
    prices, cons, bench = synthetic_dataset(cfg, n_per_index=25, seed=1, split_prob=0.002, holiday_prob=0.02)
    frames = []
    for idx, df in prices.items():
        frames.append(df.assign(index=idx, market=cfg["universe"]["markets"][idx]))
    tick_market = {v: cfg["universe"]["markets"][k] for k, v in cfg["benchmark"]["index_tickers"].items()}
    frames.append(bench.assign(index="benchmark", market=bench["ticker"].map(tick_market)))
    from common.data import normalize_prices
    return normalize_prices(pd.concat(frames, ignore_index=True)), cons, bench


@pytest.fixture(scope="session")
def prices(dataset):
    return dataset[0]


@pytest.fixture(scope="session")
def constituents(dataset):
    return dataset[1]


@pytest.fixture(scope="session")
def data_root(cfg, tmp_path_factory) -> Path:
    """A temp project root with data/raw + data/universe written in the on-disk format."""
    root = tmp_path_factory.mktemp("root")
    write_synthetic_dataset(cfg, root, n_per_index=25, seed=1, split_prob=0.002, holiday_prob=0.02)
    return root
