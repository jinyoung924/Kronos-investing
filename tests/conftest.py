"""Shared synthetic fixtures for common/ and B_model_infer/ tests. No network, no real data.

Stage tests (tests/test_stageN_*.py, see docs/spec.md) use the real prepared data and
their own toy examples; these fixtures only serve the lookahead / schema guards.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from common.config import cfg_override, load_config
from common.synthetic import synthetic_dataset

ROOT = Path(__file__).resolve().parents[1]

TEST_OVERRIDES = {
    "universe.indices": ["kospi200", "sp500"], "universe.markets": {"kospi200": "KR", "sp500": "US"},
    "data.index_tickers": {"kospi200": "069500", "sp500": "SPY"},
    "period.start": "2024-07-01", "period.end": "2025-06-30",
    "infer.default_profile": "base",
    "infer.profiles.base.lookback": 60, "infer.profiles.base.pred_len": 5, "infer.profiles.base.step": 5,
    "infer.profiles.base.sample_count": 8,
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
    tick_market = {v: cfg["universe"]["markets"][k] for k, v in cfg["data"]["index_tickers"].items()}
    frames.append(bench.assign(index="benchmark", market=bench["ticker"].map(tick_market)))
    from common.data import normalize_prices
    return normalize_prices(pd.concat(frames, ignore_index=True)), cons, bench


@pytest.fixture(scope="session")
def prices(dataset):
    return dataset[0]


@pytest.fixture(scope="session")
def constituents(dataset):
    return dataset[1]
