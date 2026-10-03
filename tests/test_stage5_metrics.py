"""Stage 5 (docs/spec.md): labels, signal metrics, portfolio metrics (cross-checked with quantstats), significance,
run_evaluate bookkeeping. Real-data checks run only when the fake run_ids have been evaluated."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from common.config import load_config
from common.paths import Paths
from F_evaluate import portfolio_metrics as pm
from F_evaluate.labels import compute_labels
from F_evaluate.run_evaluate import append_trials
from F_evaluate.signal_metrics import calibration, hit_rate, ic_by_regime, ic_summary, quantile_returns, rank_ic, regime_labels
from F_evaluate.significance import block_bootstrap_sharpe_ci, deflated_sharpe, deflated_sharpe_from_returns, sharpe_per_period

ROOT = Paths(load_config("configs/base.yaml"), ".").root
qs = pytest.importorskip("quantstats", reason="quantstats is the cross-check library for Stage 5 (pip install quantstats)")


# ---------------------------------------------------------------------------------------------
# labels
# ---------------------------------------------------------------------------------------------
def _toy_prices(n_days=12, tickers=("A", "B", "C"), seed=0):
    cal = pd.bdate_range("2024-07-01", periods=n_days)
    rng = np.random.default_rng(seed)
    rows = []
    for t in tickers:
        o = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n_days)))
        for d, oo in zip(cal, o):
            rows.append({"date": d, "ticker": t, "open": oo, "close": oo * 1.001})
    prices = pd.DataFrame(rows)
    adj = prices[["date", "ticker"]].assign(factor=1.0)
    return prices, adj, cal


def test_labels_start_at_the_next_open_and_ignore_the_as_of_day():
    prices, adj, cal = _toy_prices()
    s = cal[2]
    lab = compute_labels(prices, adj, cal, [s], horizon=3).set_index("ticker")
    oa = prices.pivot(index="date", columns="ticker", values="open")
    f = cal[3]
    assert (lab["fill_date"] == f).all() and (lab["end_date"] == cal[6]).all()
    assert lab.loc["A", "label"] == pytest.approx(oa.loc[cal[6], "A"] / oa.loc[f, "A"] - 1)
    assert lab.loc["A", "label_mean"] == pytest.approx(oa.loc[cal[4:7], "A"].mean() / oa.loc[f, "A"] - 1)
    # changing anything on the as_of day (or before) leaves the labels unchanged; changing the fill-day open does not
    p2 = prices.copy(); p2.loc[p2["date"] <= s, ["open", "close"]] *= 3.0
    pd.testing.assert_frame_equal(compute_labels(p2, adj, cal, [s], 3), compute_labels(prices, adj, cal, [s], 3))
    p3 = prices.copy(); p3.loc[(p3["date"] == f) & (p3["ticker"] == "A"), "open"] *= 1.1
    assert compute_labels(p3, adj, cal, [s], 3).set_index("ticker").loc["A", "label"] != pytest.approx(lab.loc["A", "label"])
    # a split inside the window (raw halves, F doubles) does not change the label
    p4, a4 = prices.copy(), adj.copy()
    m = (p4["ticker"] == "B") & (p4["date"] >= cal[5])
    p4.loc[m, ["open", "close"]] /= 2; a4.loc[m, "factor"] = 2.0
    assert compute_labels(p4, a4, cal, [s], 3).set_index("ticker").loc["B", "label"] == pytest.approx(lab.loc["B", "label"])
    # windows past the calendar end are dropped; a missing open gives NaN which is dropped per column
    assert compute_labels(prices, adj, cal, [cal[-2]], 3).empty


# ---------------------------------------------------------------------------------------------
# signal metrics
# ---------------------------------------------------------------------------------------------
def _panel(n_dates=30, n=60, seed=1):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-07-01", periods=n_dates)
    rows = []
    for d in dates:
        lab = rng.normal(0, 0.05, n)
        for i in range(n):
            rows.append({"as_of_date": d, "ticker": f"T{i:03d}", "label": lab[i], "label_mean": lab[i] / 2})
    labels = pd.DataFrame(rows)
    return dates, labels


def test_ic_is_one_for_label_minus_one_for_negated_and_zero_for_noise():
    dates, labels = _panel()
    sig = labels[["as_of_date", "ticker"]].assign(exp_ret=labels["label"], exp_ret_mean=labels["label_mean"], std=0.01, p_up=(labels["label"] > 0).astype(float))
    ic = rank_ic(sig, labels, "exp_ret")
    assert len(ic) == len(dates) and np.allclose(ic, 1.0)
    assert np.allclose(rank_ic(sig.assign(exp_ret=-sig["exp_ret"]), labels, "exp_ret"), -1.0)
    rng = np.random.default_rng(5)
    noise = rank_ic(sig.assign(exp_ret=rng.normal(size=len(sig))), labels, "exp_ret")
    s = ic_summary(noise)
    assert abs(s["mean"]) < 0.05 and abs(s["t"]) < 2.5 and s["n_dates"] == len(dates)
    s1 = ic_summary(ic)
    assert s1["mean"] == 1.0 and s1["share_positive"] == 1.0
    # hit rate is 1 when p_up encodes the realised direction; calibration needs std variation
    assert hit_rate(sig, labels)["hit_rate"] == 1.0
    assert np.isnan(calibration(sig, labels)["spearman"])            # constant std -> undefined
    sig2 = sig.assign(std=np.abs(sig["exp_ret"]) + 1e-6, exp_ret=0.0)
    assert calibration(sig2, labels)["spearman"] > 0.99               # std == |error| -> perfectly calibrated ordering


def test_quantile_returns_and_regimes():
    dates, labels = _panel()
    sig = labels[["as_of_date", "ticker"]].assign(exp_ret=labels["label"])
    qdf, summ = quantile_returns(sig, labels, "exp_ret", q=5)
    assert list(qdf.columns) == ["Q1", "Q2", "Q3", "Q4", "Q5", "spread"] and len(qdf) == len(dates)
    assert (qdf["Q5"] > qdf["Q1"]).all() and summ["mean_spread"] > 0 and summ["t"] > 10
    assert (qdf[["Q1", "Q2", "Q3", "Q4", "Q5"]].diff(axis=1).iloc[:, 1:] >= 0).all().all()   # monotone when signal == label
    market = labels.groupby("as_of_date")["label"].mean()
    reg = regime_labels(dates, market)
    assert set(reg["period"]) == {"2024H2"} and set(reg["market"]) <= {"up", "down"}
    by = ic_by_regime(rank_ic(sig, labels, "exp_ret"), reg)
    assert by["period"]["2024H2"]["n_dates"] == len(dates)
    assert sum(v["n_dates"] for v in by["market"].values()) == len(dates)
    with pytest.raises(ValueError):
        regime_labels(dates, market, split="quarter")
    # too few names on a date -> that date is skipped
    few = sig[sig["ticker"] < "T005"]
    assert rank_ic(few, labels, "exp_ret", min_obs=10).empty


# ---------------------------------------------------------------------------------------------
# portfolio metrics vs quantstats
# ---------------------------------------------------------------------------------------------
def _returns(seed=7, n=300):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-02", periods=n)
    return pd.Series(rng.normal(0.0004, 0.012, n), index=idx)


def test_core_metrics_match_quantstats():
    r = _returns()
    nav = pd.concat([pd.Series([1.0], index=[r.index[0] - pd.Timedelta(days=1)]), (1 + r).cumprod()])
    assert pm.cagr(r) == pytest.approx(float(qs.stats.cagr(r, periods=252)), rel=1e-6)
    assert pm.ann_vol(r) == pytest.approx(float(qs.stats.volatility(r, periods=252)), rel=1e-9)
    assert pm.sharpe(r) == pytest.approx(float(qs.stats.sharpe(r, periods=252)), rel=1e-9)
    assert pm.sortino(r) == pytest.approx(float(qs.stats.sortino(r, periods=252)), rel=1e-9)
    assert pm.max_drawdown(nav)["max_drawdown"] == pytest.approx(float(qs.stats.max_drawdown(r)), rel=1e-9)
    assert pm.calmar(r, nav) == pytest.approx(float(qs.stats.calmar(r, periods=252)), rel=1e-6)
    assert pm.total_return(r) == pytest.approx(float(qs.stats.comp(r)), rel=1e-9)
    # drawdown bookkeeping: recovery after the trough
    nav2 = pd.Series([1.0, 1.1, 0.99, 1.05, 1.2], index=pd.bdate_range("2024-01-01", periods=5))
    mdd = pm.max_drawdown(nav2)
    assert mdd["max_drawdown"] == pytest.approx(0.99 / 1.1 - 1) and mdd["peak"] == nav2.index[1] and mdd["trough"] == nav2.index[2]
    assert mdd["recovery"] == nav2.index[4] and mdd["recovery_rows"] == 3


def test_relative_and_trading_metrics_by_hand():
    r = _returns(1)
    b = _returns(2)
    rel = pm.relative_metrics(r, b)
    beta = np.cov(r, b, ddof=1)[0, 1] / b.var(ddof=1)
    assert rel["beta"] == pytest.approx(beta)
    assert rel["alpha"] == pytest.approx((r.mean() - beta * b.mean()) * 252)
    act = r - b
    assert rel["tracking_error"] == pytest.approx(act.std(ddof=1) * np.sqrt(252))
    assert rel["ir"] == pytest.approx(act.mean() / act.std(ddof=1) * np.sqrt(252))
    assert rel["aer"] == pytest.approx(pm.cagr(r) - pm.cagr(b))
    self_rel = pm.relative_metrics(r, r)
    assert self_rel["beta"] == pytest.approx(1.0) and self_rel["aer"] == pytest.approx(0.0) and np.isnan(self_rel["ir"])
    daily = pd.DataFrame({"date": pd.bdate_range("2024-07-01", periods=11), "turnover": [0] + [0.5, 0, 0, 0, 0.25, 0, 0, 0, 0, 0.1],
                          "n_holdings": [0] + [20] * 10, "cost": [0] + [0.001] * 10})
    trades = pd.DataFrame({"fill_date": [daily["date"][1]] * 3 + [daily["date"][5]] * 2, "ticker": list("ABCDE"), "trade_w": [0.1, -0.1, 0.0, 0.2, -0.2]})
    tm = pm.trading_metrics(daily, trades)
    assert tm["annual_turnover"] == pytest.approx(0.85 * 252 / 10) and tm["n_fills"] == 3 and tm["turnover_per_fill"] == pytest.approx(0.85 / 3)
    assert tm["names_changed_per_fill"] == pytest.approx(2.0) and tm["names_changed_per_day"] == pytest.approx(4 / 10) and tm["mean_holdings"] == 20


# ---------------------------------------------------------------------------------------------
# significance
# ---------------------------------------------------------------------------------------------
def test_bootstrap_is_reproducible_and_brackets_the_point_estimate():
    r = _returns(3)
    a = block_bootstrap_sharpe_ci(r, n=500, block=20, seed=11)
    b = block_bootstrap_sharpe_ci(r, n=500, block=20, seed=11)
    c = block_bootstrap_sharpe_ci(r, n=500, block=20, seed=12)
    assert a == b and (a["lower"], a["upper"]) != (c["lower"], c["upper"])
    assert a["lower"] < a["point"] < a["upper"] and a["n"] == 500 and a["T"] == len(r)
    assert a["point"] == pytest.approx(sharpe_per_period(r) * np.sqrt(252))
    assert np.isnan(block_bootstrap_sharpe_ci(r.iloc[:5], n=100, block=20, seed=0)["lower"])


def test_deflated_sharpe_decreases_with_trials():
    vals = [deflated_sharpe(sr=0.1, n_trials=n, T=250, skew=0.0, kurt=3.0)["dsr"] for n in (1, 2, 5, 10, 50, 200)]
    assert all(np.diff(vals) < 0), vals
    assert deflated_sharpe(0.1, 1, 250, 0.0, 3.0)["sr_star"] == 0.0
    d = deflated_sharpe_from_returns(_returns(4), n_trials=8)
    assert 0 <= d["dsr"] <= 1 and d["n_trials"] == 8 and d["T"] == 300


def test_trials_append_dedupes_on_config_hash(tmp_path):
    f = tmp_path / "trials.csv"
    rows = [{"run_id": "r", "engine": "v1", "strategy": "a", "config_hash": "h1", "sharpe": 1.0, "cagr": 0.1, "evaluated_at": "t"},
            {"run_id": "r", "engine": "v1", "strategy": "b", "config_hash": "h1", "sharpe": 0.5, "cagr": 0.0, "evaluated_at": "t"}]
    t1, n1 = append_trials(f, rows)
    t2, n2 = append_trials(f, rows + [{"run_id": "r", "engine": "v1", "strategy": "a", "config_hash": "h2", "sharpe": 1.1, "cagr": 0.1, "evaluated_at": "t"}])
    assert n1 == 2 and n2 == 1 and len(t2) == 3


# ---------------------------------------------------------------------------------------------
# real outputs (skipped when absent)
# ---------------------------------------------------------------------------------------------
def test_real_metrics_oracle_near_one_dummy_near_zero():
    cfg = load_config(ROOT / "configs/base.yaml")
    paths = Paths(cfg, ROOT)
    fo, fd = paths.metrics_dir("fake_oracle_base") / "signal_metrics.json", paths.metrics_dir("fake_dummy_base") / "signal_metrics.json"
    if not (fo.exists() and fd.exists()):
        pytest.skip("run F_evaluate.run_evaluate for fake_oracle_base and fake_dummy_base first")
    o, d = json.loads(fo.read_text()), json.loads(fd.read_text())
    # the oracle knows close -> close(+H); the label is open(f) -> open(f+H): one day apart, so IC is high but < 1
    assert o["ic"]["exp_ret"]["mean"] > 0.6 and o["ic"]["exp_ret_mean"]["mean"] > 0.6 and o["hit_rate"]["hit_rate"] > 0.75
    assert o["quantiles"]["exp_ret"]["mean_spread"] > 0.05 and o["quantiles"]["exp_ret"]["t"] > 5
    assert abs(d["ic"]["exp_ret"]["mean"]) < 0.03 and abs(d["ic"]["exp_ret"]["t"]) < 2.5 and abs(d["hit_rate"]["hit_rate"] - 0.5) < 0.05
    assert o["ic"]["exp_ret"]["n_dates"] == d["ic"]["exp_ret"]["n_dates"] == 49
    pmx = pd.read_csv(paths.metrics_dir("fake_dummy_base") / "portfolio_metrics.csv")
    assert {"equal_weight@paper_costs", "equal_weight@no_costs", "index:KOSPI"} <= set(pmx["strategy"])
    ew = pmx.set_index("strategy").loc["equal_weight@paper_costs"]
    assert ew["aer"] == pytest.approx(0.0, abs=1e-12) and ew["beta"] == pytest.approx(1.0) and ew["cost_drag_cagr"] > 0
    ci = pmx.dropna(subset=["sharpe_ci_lower"])
    assert (ci["sharpe_ci_lower"] <= ci["sharpe"]).all() and (ci["sharpe"] <= ci["sharpe_ci_upper"]).all()
    trials = pd.read_csv(paths.metrics_dir("fake_dummy_base") / "trials.csv")
    assert not trials.duplicated(["run_id", "engine", "strategy", "config_hash"]).any()


# ---------------------------------------------------------------------------------------------
# Stage 5 follow-up (D-16, D-17)
# ---------------------------------------------------------------------------------------------
def test_market_contribution_splits_returns_by_market():
    from F_evaluate.run_evaluate import market_contribution
    dates = pd.bdate_range("2024-07-01", periods=4)
    hold = pd.DataFrame({"date": np.repeat(dates, 2), "ticker": ["A", "B"] * 4, "weight": [0.5, 0.5] * 4})
    ret = pd.DataFrame({"A": [np.nan, 0.02, -0.01, 0.03], "B": [np.nan, -0.02, 0.01, 0.01]}, index=dates)
    out = pd.DataFrame(market_contribution(hold, ret, pd.Series({"A": "KOSPI", "B": "KOSDAQ"}), 252)).set_index("market")
    assert out.loc["KOSPI", "contribution_total"] == pytest.approx(0.5 * (0.02 - 0.01 + 0.03))
    assert out.loc["KOSDAQ", "contribution_total"] == pytest.approx(0.5 * (-0.02 + 0.01 + 0.01))
    assert out.loc["KOSPI", "weight_share_mean"] == pytest.approx(0.5) and out.loc["KOSPI", "n_tickers_mean"] == 1
    assert out.loc["KOSPI", "subbook_cagr"] == pytest.approx((1.02 * 0.99 * 1.03) ** (252 / 3) - 1)


def test_n_trials_scope_counts_real_trials_of_the_same_profile():
    from F_evaluate.run_evaluate import n_trials_for
    ledger = pd.DataFrame([
        {"run_id": "fake_dummy_base", "profile": "base", "fake": True, "config_hash": "a"},
        {"run_id": "fake_dummy_base", "profile": "base", "fake": True, "config_hash": "b"},
        {"run_id": "kronos_base_v1", "profile": "base", "fake": False, "config_hash": "a"},
        {"run_id": "kronos_base_v1", "profile": "base", "fake": False, "config_hash": "c"},
        {"run_id": "kronos_base_v2", "profile": "base", "fake": False, "config_hash": "d"},
        {"run_id": "kronos_paper_v1", "profile": "paper", "fake": False, "config_hash": "e"},
    ])
    assert n_trials_for(ledger, "fake_dummy_base", "base", True) == 2          # a fake run counts only itself
    assert n_trials_for(ledger, "kronos_base_v1", "base", False) == 3          # real trials of the profile, across run_ids
    assert n_trials_for(ledger, "kronos_paper_v1", "paper", False) == 1
    assert n_trials_for(ledger, "kronos_paper_v1", "paper", False, scope="all") == 4
    assert n_trials_for(ledger.iloc[0:0], "x", "base", False) == 0


def test_real_metrics_have_index_benchmark_and_by_market():
    cfg = load_config(ROOT / "configs/base.yaml")
    paths = Paths(cfg, ROOT)
    f = paths.metrics_dir("fake_oracle_base")
    if not (f / "portfolio_by_market.csv").exists():
        pytest.skip("run F_evaluate.run_evaluate first")
    pmx = pd.read_csv(f / "portfolio_metrics.csv")
    strat = pmx[~pmx["strategy"].str.startswith("index:")]
    assert (strat["paper_benchmark"] == cfg["evaluate"]["paper_benchmark"]).all()
    assert strat["aer_vs_index"].notna().all() and strat["ir_vs_index"].notna().all()
    bm = pd.read_csv(f / "portfolio_by_market.csv")
    assert set(bm["market"]) == set(cfg["data"]["markets"])
    share = bm.groupby("strategy")["weight_share_mean"].sum()
    assert np.allclose(share, 1.0, atol=0.02)
    s = json.loads((f / "signal_metrics.json").read_text())
    assert set(s["ic_by_market"]) == set(cfg["data"]["markets"])
    assert all(v["exp_ret"]["mean"] > 0.6 for v in s["ic_by_market"].values())
    ledger = pd.read_csv(paths.trials_ledger())
    assert {"profile", "fake"} <= set(ledger.columns) and not ledger.duplicated(["run_id", "engine", "strategy", "config_hash"]).any()
