"""Interface conformance and behaviour of every registered strategy."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.strategies import BENCHMARK_STRATEGIES, KRONOS_STRATEGIES, REGISTRY, available, get_strategy, uses_predictions
from backtest.strategies.base import Strategy


def _signals(n=40, seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.Index([f"T{i:03d}" for i in range(n)], name="ticker")
    return pd.DataFrame({
        "exp_ret": rng.normal(0.0, 0.03, n), "std": rng.uniform(0.01, 0.05, n),
        "p_up": rng.uniform(0, 1, n), "pred_range": rng.uniform(0.01, 0.06, n),
        "mom20": rng.normal(0, 0.1, n), "vol20": rng.uniform(0.1, 0.5, n), "rev5": rng.normal(0, 0.05, n),
        "last_close": rng.uniform(10, 500, n), "n_samples": 20,
    }, index=idx)


def test_registry_is_complete():
    assert set(KRONOS_STRATEGIES) | set(BENCHMARK_STRATEGIES) == set(available())
    for name in available():
        assert REGISTRY[name].name == name
        assert issubclass(REGISTRY[name], Strategy)
    with pytest.raises(KeyError):
        get_strategy("does_not_exist")
    assert uses_predictions("topk") and not uses_predictions("equal_weight")


@pytest.mark.parametrize("name", available())
def test_interface_contract(name):
    params = {"tickers": ["SPY"]} if name == "index_buy_hold" else {}
    strat = get_strategy(name, **params)
    sig = _signals()
    before = sig.copy()
    prev = pd.Series(dtype=float)
    w = strat.weights(pd.Timestamp("2024-08-01"), sig, prev)
    assert isinstance(w, pd.Series)
    assert (w >= 0).all() and (w > 0).all()
    assert w.sum() <= 1.0 + 1e-9
    if name != "index_buy_hold":
        assert set(w.index) <= set(sig.index)
    pd.testing.assert_frame_equal(sig, before)  # pure: no mutation
    w2 = strat.weights(pd.Timestamp("2024-08-01"), sig, prev)
    pd.testing.assert_series_equal(w, w2)  # deterministic


@pytest.mark.parametrize("name", available())
def test_empty_signals_yield_cash(name):
    params = {"tickers": []} if name == "index_buy_hold" else {}
    w = get_strategy(name, **params).weights(pd.Timestamp("2024-08-01"), _signals(0), pd.Series(dtype=float))
    assert len(w) == 0


def test_topk_uses_only_ranking():
    sig = _signals()
    w = get_strategy("topk", k=7).weights(pd.Timestamp("2024-08-01"), sig, pd.Series(dtype=float))
    assert len(w) == 7 and np.allclose(w, 1 / 7)
    assert set(w.index) == set(sig["exp_ret"].nlargest(7).index)
    # monotone transform of exp_ret leaves TopK unchanged
    sig2 = sig.assign(exp_ret=np.exp(sig["exp_ret"] * 10))
    w2 = get_strategy("topk", k=7).weights(pd.Timestamp("2024-08-01"), sig2, pd.Series(dtype=float))
    assert set(w.index) == set(w2.index)


def test_conf_weighted_uses_dispersion_and_goes_to_cash():
    sig = _signals()
    strat = get_strategy("conf_weighted", threshold=0.5, max_weight=0.1)
    w = strat.weights(pd.Timestamp("2024-08-01"), sig, pd.Series(dtype=float))
    score = (sig["exp_ret"] / sig["std"])
    assert set(w.index) == set(score[(score >= 0.5) & (sig["exp_ret"] > 0)].index)
    assert w.max() <= 0.1 + 1e-12
    # doubling std of one name lowers its weight relative to peers
    t = w.index[0]
    sig2 = sig.copy(); sig2.loc[t, "std"] *= 2
    w2 = strat.weights(pd.Timestamp("2024-08-01"), sig2, pd.Series(dtype=float))
    if t in w2.index and w[t] < 0.1:
        assert w2[t] < w[t]
    # nothing clears the threshold -> all cash
    cash = strat.weights(pd.Timestamp("2024-08-01"), sig.assign(exp_ret=-0.05), pd.Series(dtype=float))
    assert len(cash) == 0


def test_vol_target_inverse_range_weights():
    sig = _signals()
    strat = get_strategy("vol_target", k=10)
    w = strat.weights(pd.Timestamp("2024-08-01"), sig, pd.Series(dtype=float))
    assert len(w) == 10 and w.sum() == pytest.approx(1.0)
    top = sig.nlargest(10, "exp_ret")
    expected = (1 / top["pred_range"]) / (1 / top["pred_range"]).sum()
    pd.testing.assert_series_equal(w.sort_index(), expected.sort_index().rename("weight"), check_names=False)
    scaled = get_strategy("vol_target", k=10, target_range=0.01).weights(pd.Timestamp("2024-08-01"), sig, pd.Series(dtype=float))
    assert scaled.sum() < 1.0


def test_random_is_seeded_per_date():
    sig = _signals()
    s = get_strategy("random", k=5, seed=1)
    a = s.weights(pd.Timestamp("2024-08-01"), sig, pd.Series(dtype=float))
    b = s.weights(pd.Timestamp("2024-08-01"), sig, pd.Series(dtype=float))
    c = s.weights(pd.Timestamp("2024-08-08"), sig, pd.Series(dtype=float))
    assert list(a.index) == list(b.index) and list(a.index) != list(c.index)
    assert set(a.index) != set(get_strategy("random", k=5, seed=2).weights(pd.Timestamp("2024-08-01"), sig, pd.Series(dtype=float)).index)


def test_equal_weight_and_momentum():
    sig = _signals()
    ew = get_strategy("equal_weight").weights(pd.Timestamp("2024-08-01"), sig, pd.Series(dtype=float))
    assert len(ew) == len(sig) and np.allclose(ew, 1 / len(sig))
    mom = get_strategy("momentum20", k=6).weights(pd.Timestamp("2024-08-01"), sig, pd.Series(dtype=float))
    assert set(mom.index) == set(sig["mom20"].nlargest(6).index)


def test_index_buy_hold_keeps_position():
    s = get_strategy("index_buy_hold", tickers=["SPY"])
    first = s.weights(pd.Timestamp("2024-08-01"), _signals(), pd.Series(dtype=float))
    assert first.to_dict() == {"SPY": 1.0}
    drifted = pd.Series({"SPY": 0.97})
    again = s.weights(pd.Timestamp("2024-08-08"), _signals(), drifted)
    assert again.to_dict() == {"SPY": 0.97}  # no trade
