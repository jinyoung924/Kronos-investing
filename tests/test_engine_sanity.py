"""Engine wiring and bias checks (outline.md section 5)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.costs import CostModel, MarketCost
from backtest.engine import run_backtest
from backtest.metrics import cagr, sharpe
from backtest.runner import build_weight_matrix, run_strategy, load_context
from backtest.signals import build_signals, realized_returns
from backtest.strategies import get_strategy
from common.data import rebalance_dates, ticker_market_map, to_wide, trading_calendar


@pytest.fixture(scope="module")
def ctx(cfg, data_root):
    return load_context(cfg, None, data_root, need_predictions=False)


def test_a_perfect_foresight_makes_abnormal_returns(cfg, ctx):
    """Feed the REALISED open-to-open forward return as `exp_ret`. If the engine is wired to
    shift(1) and open->open returns correctly, TopK on this 'signal' must be spectacular."""
    labels = realized_returns(ctx.prices, ctx.dates, int(cfg["model"]["horizon"]))
    cheat = ctx.signals.copy()
    cheat["exp_ret"] = labels["ret_oo"].reindex(cheat.index)
    W = build_weight_matrix(get_strategy("topk", k=5), cheat, ctx.dates)
    res = run_backtest(W, ctx.open_raw, ctx.adj_factor, ctx.cost_model)
    ew, _ = run_strategy(ctx, "equal_weight")
    assert res.nav.iloc[-1] > 3.0 * ew.nav.iloc[-1], (res.nav.iloc[-1], ew.nav.iloc[-1])
    assert sharpe(res.returns.iloc[1:]) > 4.0


def test_a_foresight_shifted_by_one_period_is_not_abnormal(cfg, ctx):
    """Using the PREVIOUS period's realised return (a legitimate, lagged signal) must not be
    spectacular; guards against an off-by-one that would make yesterday's label tomorrow's signal."""
    labels = realized_returns(ctx.prices, ctx.dates, int(cfg["model"]["horizon"]))
    lagged = labels["ret_oo"].unstack("ticker").shift(1).stack(future_stack=True)
    lagged.index.names = ["as_of_date", "ticker"]
    s = ctx.signals.copy(); s["exp_ret"] = lagged.reindex(s.index)
    W = build_weight_matrix(get_strategy("topk", k=5), s, ctx.dates)
    res = run_backtest(W, ctx.open_raw, ctx.adj_factor, ctx.cost_model)
    ew, _ = run_strategy(ctx, "equal_weight")
    assert res.nav.iloc[-1] < 2.0 * ew.nav.iloc[-1]


def test_b_random_signal_tracks_equal_weight_after_costs(ctx):
    """Average over several seeds: gross of costs the random top-K portfolio must land on
    EqualWeight (no universe bias); net of costs it must sit below EqualWeight by an amount
    that is fully explained by the extra turnover (cost accounting is sane)."""
    ew, _ = run_strategy(ctx, "equal_weight")
    net, gross, drag = [], [], []
    for seed in range(6):
        r, _ = run_strategy(ctx, "random", params={"k": 20, "seed": seed})
        net.append(r.nav.iloc[-1]); gross.append(r.nav_gross.iloc[-1]); drag.append(r.costs.sum())
    gross_gap = np.mean(gross) - ew.nav_gross.iloc[-1]
    assert abs(gross_gap) < 0.04, f"random vs EW gross gap {gross_gap:+.3f}: universe bias?"
    net_gap = np.mean(net) - ew.nav.iloc[-1]
    expected_drag = np.mean(drag) - ew.costs.sum()
    assert net_gap <= 0.02, "random should not beat EW net of costs"
    assert abs(net_gap - (gross_gap - expected_drag)) < 0.02, "net gap not explained by costs"
    assert 0.0 < expected_drag < 0.15


def test_buy_and_hold_single_asset_equals_adjusted_open_ratio(prices):
    open_raw, adj = to_wide(prices, "open"), to_wide(prices, "adj_factor")
    t = open_raw.columns[0]
    cal = open_raw.index
    d0 = cal[100]
    W = pd.DataFrame({t: [1.0]}, index=[d0])
    res = run_backtest(W, open_raw, adj, ffill_limit=None)
    fill = cal[101]
    adj_open = (open_raw[t] * adj[t]).ffill()
    expected = adj_open.iloc[-1] / adj_open.loc[fill]
    assert res.nav.iloc[-1] == pytest.approx(expected, rel=1e-9)
    assert res.nav.loc[:d0].eq(1.0).all()
    assert res.turnover.sum() == pytest.approx(0.5)  # one-way, 100% bought once


def test_costs_are_charged_per_market():
    cal = pd.bdate_range("2024-01-01", periods=10)
    open_raw = pd.DataFrame({"KR1": 100.0, "US1": 100.0}, index=cal)  # flat prices
    adj = pd.DataFrame(1.0, index=cal, columns=open_raw.columns)
    cm = CostModel({"KR": MarketCost(0.00015, 0.0018, 0.0005), "US": MarketCost(0.0, 0.0, 0.0005)}, {"KR1": "KR", "US1": "US"})
    W = pd.DataFrame({"KR1": [0.5, 0.0], "US1": [0.5, 0.0]}, index=[cal[0], cal[4]])
    res = run_backtest(W, open_raw, adj, cm)
    buy = 0.5 * (0.00015 + 0.0005) + 0.5 * 0.0005
    sell = 0.5 * (0.00015 + 0.0018 + 0.0005) + 0.5 * 0.0005
    assert res.nav.iloc[-1] == pytest.approx((1 - buy) * (1 - sell), rel=1e-12)
    assert res.nav_gross.iloc[-1] == pytest.approx(1.0)
    assert res.n_holdings.loc[cal[1]] == 2 and res.n_holdings.iloc[-1] == 0


def test_weights_drift_between_rebalances():
    cal = pd.bdate_range("2024-01-01", periods=6)
    open_raw = pd.DataFrame({"A": [100, 100, 110, 121, 121, 121], "B": [100, 100, 100, 100, 100, 100]}, index=cal, dtype=float)
    adj = pd.DataFrame(1.0, index=cal, columns=open_raw.columns)
    W = pd.DataFrame({"A": [0.5], "B": [0.5]}, index=[cal[0]])
    res = run_backtest(W, open_raw, adj)
    assert res.nav.loc[cal[3]] == pytest.approx(0.5 * 1.21 + 0.5)
    wA = res.weights.loc[cal[3], "A"]
    assert wA == pytest.approx(0.5 * 1.21 / (0.5 * 1.21 + 0.5))
    assert res.turnover.sum() == pytest.approx(0.5)


def test_untradable_target_goes_to_cash_and_dead_position_is_liquidated():
    cal = pd.bdate_range("2024-01-01", periods=8)
    a = [100.0] * 8; b = [100.0, 100.0, 100.0, np.nan, np.nan, np.nan, np.nan, np.nan]
    open_raw = pd.DataFrame({"A": a, "B": b}, index=cal)
    adj = pd.DataFrame(1.0, index=cal, columns=open_raw.columns)
    W = pd.DataFrame({"A": [0.5, 0.5], "B": [0.5, 0.5]}, index=[cal[0], cal[4]])
    res = run_backtest(W, open_raw, adj, ffill_limit=1)
    assert res.weights.loc[cal[1], "B"] == pytest.approx(0.5)
    assert res.weights.loc[cal[5], "B"] == 0.0            # B untradable on cal[5] -> cash
    assert cal[5] in res.untradable and res.untradable[cal[5]] == ["B"]
    assert res.nav.iloc[-1] == pytest.approx(1.0)         # flat prices, zero costs


def test_index_restriction_keeps_only_that_exchange(cfg, data_root):
    from backtest.runner import load_context, run_strategy
    full = load_context(cfg, None, data_root, need_predictions=False)
    kr = load_context(cfg, None, data_root, need_predictions=False, indices=["kospi200"])
    assert set(kr.signals["index"]) == {"kospi200"}
    assert set(full.signals["index"]) == {"kospi200", "sp500"}
    assert kr.dates.equals(full.dates)
    ew, W = run_strategy(kr, "equal_weight")
    assert all(t.startswith("KOSPI200") for t in W.columns[(W > 0).any()])
    with pytest.raises(KeyError):
        load_context(cfg, None, data_root, need_predictions=False, indices=["nasdaq"])
