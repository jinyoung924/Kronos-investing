"""Automatic verification of every lookahead rule in outline.md section 1."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.engine import run_backtest
from backtest.runner import LABEL_COLS, build_weight_matrix, load_context
from backtest.signals import build_signals, load_predictions, realized_returns
from backtest.strategies import get_strategy
from common.data import rebalance_dates, to_wide, trading_calendar
from common.lookahead import LookaheadError, assert_no_future, assert_signal_before_fill, slice_as_of
from common.paths import Paths
from common.schema import validate_predictions
from common.universe import universe_at
from infer.backends import DummyBackend
from infer.build_batch import build_batch
from infer.run_inference import run as run_inference


def _perturb_after(prices: pd.DataFrame, date, factor: float = 3.0, seed: int = 7) -> pd.DataFrame:
    """Scramble every price strictly after `date` (multiplicative noise + level shift)."""
    p = prices.copy()
    m = p["date"] > pd.Timestamp(date)
    rng = np.random.default_rng(seed)
    noise = rng.uniform(0.5, factor, m.sum())
    for c in ("open", "high", "low", "close"):
        p.loc[m, c] = p.loc[m, c] * noise
    p.loc[m, "volume"] = p.loc[m, "volume"] * 2
    return p


# ---------------------------------------------------------------------------------------------
# guards
# ---------------------------------------------------------------------------------------------
def test_slice_as_of_removes_future_and_assert_raises(prices):
    as_of = pd.Timestamp("2024-09-10")
    sliced = slice_as_of(prices, as_of)
    assert sliced["date"].max() <= as_of
    with pytest.raises(LookaheadError):
        assert_no_future(prices, as_of)  # full frame has future rows
    with pytest.raises(LookaheadError):
        assert_no_future(pd.DataFrame({"date": [as_of + pd.Timedelta(days=1)]}), as_of)


def test_signal_must_precede_fill():
    d = pd.DatetimeIndex(["2024-07-01", "2024-07-02"])
    assert_signal_before_fill(d, d + pd.Timedelta(days=1))
    with pytest.raises(LookaheadError):
        assert_signal_before_fill(d, d)  # same day fill = lookahead
    with pytest.raises(LookaheadError):
        assert_signal_before_fill(d, d - pd.Timedelta(days=1))


# ---------------------------------------------------------------------------------------------
# inference input
# ---------------------------------------------------------------------------------------------
def test_build_batch_uses_only_past_rows_and_future_stamps(prices, constituents, cfg):
    as_of = pd.Timestamp("2024-10-15")
    tickers = universe_at(constituents["kospi200"], as_of)
    b = build_batch(prices, tickers, as_of, lookback=60, horizon=5)
    assert len(b) > 0
    for xs, ys, df in zip(b.x_timestamps, b.y_timestamps, b.df_list):
        assert xs.max() <= as_of
        assert ys.min() > as_of
        assert len(df) == 60 and len(xs) == 60 and len(ys) == 5
        assert not df.isna().any().any()
    # rebased last close == raw close at as_of
    raw = prices[(prices["ticker"] == b.tickers[0]) & (prices["date"] <= as_of)].iloc[-1]["close"]
    assert b.df_list[0]["close"].iloc[-1] == pytest.approx(raw)
    assert b.last_close[0] == pytest.approx(raw)


def test_build_batch_invariant_to_future_prices(prices, constituents):
    as_of = pd.Timestamp("2024-10-15")
    tickers = universe_at(constituents["sp500"], as_of)
    b1 = build_batch(prices, tickers, as_of, lookback=60, horizon=5)
    b2 = build_batch(_perturb_after(prices, as_of), tickers, as_of, lookback=60, horizon=5)
    assert b1.tickers == b2.tickers
    for d1, d2 in zip(b1.df_list, b2.df_list):
        pd.testing.assert_frame_equal(d1, d2)


def test_build_batch_rebases_splits_inside_window(prices):
    """A split inside the lookback must not create a jump in the model input."""
    t = prices["ticker"].iloc[0]
    one = prices[prices["ticker"] == t].sort_values("date").copy()
    as_of = one["date"].iloc[200]
    k = 170
    one.loc[one.index[:k], ["open", "high", "low", "close"]] *= 2   # raw prices doubled before a 2:1 split
    one.loc[one.index[:k], "adj_factor"] *= 0.5
    b = build_batch(one, [t], as_of, lookback=60, horizon=5)
    c = b.df_list[0]["close"].to_numpy()
    assert np.abs(np.diff(np.log(c))).max() < 0.25  # no 2x jump


# ---------------------------------------------------------------------------------------------
# universe
# ---------------------------------------------------------------------------------------------
def test_universe_is_point_in_time(constituents):
    cons = constituents["kospi200"]
    late_joiners = set(cons[cons["date"] == cons["date"].max()]["ticker"]) - set(cons[cons["date"] == cons["date"].min()]["ticker"])
    assert late_joiners
    before = set(universe_at(cons, "2024-12-31"))
    after = set(universe_at(cons, "2025-01-02"))
    assert not (late_joiners & before)
    assert late_joiners <= after
    assert universe_at(cons, "2000-01-01") == []


# ---------------------------------------------------------------------------------------------
# predictions contract
# ---------------------------------------------------------------------------------------------
def test_prediction_file_must_match_its_as_of(cfg, prices, constituents, tmp_path):
    backend = DummyBackend(seed=0)
    written = run_inference(cfg, "rw", backend, root=tmp_path, prices=prices, constituents=constituents, log=lambda *_: None)
    assert len(written) > 10
    paths = Paths(cfg, tmp_path)
    f = paths.prediction_file("rw", written[0])
    df = pd.read_parquet(f)
    assert (df["as_of_date"] == written[0]).all()
    with pytest.raises(ValueError):
        validate_predictions(df, as_of=written[1])
    # corrupt one file's as_of_date column -> loader refuses
    df2 = df.copy(); df2["as_of_date"] = written[1]
    df2.to_parquet(f, index=False)
    with pytest.raises(ValueError):
        load_predictions(cfg, "rw", tmp_path)


def test_inference_is_resumable(cfg, prices, constituents, tmp_path):
    backend = DummyBackend(seed=0)
    w1 = run_inference(cfg, "rw", backend, root=tmp_path, prices=prices, constituents=constituents, log=lambda *_: None)
    w2 = run_inference(cfg, "rw", backend, root=tmp_path, prices=prices, constituents=constituents, log=lambda *_: None)
    assert len(w1) > 0 and w2 == []
    manifest = Paths(cfg, tmp_path).manifest_file("rw")
    assert manifest.exists()


# ---------------------------------------------------------------------------------------------
# signals
# ---------------------------------------------------------------------------------------------
def test_signals_invariant_to_future_prices(cfg, prices, constituents):
    cal = trading_calendar(prices)
    dates = rebalance_dates(cal, "2024-07-01", "2025-06-30", 5)
    cut = dates[len(dates) // 2]
    s1 = build_signals(cfg, prices, constituents, dates)
    s2 = build_signals(cfg, _perturb_after(prices, cut), constituents, dates)
    upto = dates[dates <= cut]
    a = s1.loc[s1.index.get_level_values(0).isin(upto)]
    b = s2.loc[s2.index.get_level_values(0).isin(upto)]
    pd.testing.assert_frame_equal(a, b)
    # and the features after `cut` DO change (the perturbation is real)
    later = dates[dates > cut]
    assert not s1.loc[s1.index.get_level_values(0).isin(later), "mom20"].equals(
        s2.loc[s2.index.get_level_values(0).isin(later), "mom20"])


def test_feature_frame_never_contains_labels(cfg, prices, constituents):
    cal = trading_calendar(prices)
    dates = rebalance_dates(cal, "2024-07-01", "2025-06-30", 5)
    sig = build_signals(cfg, prices, constituents, dates)
    assert not set(LABEL_COLS) & set(sig.columns)
    lab = realized_returns(prices, dates, 5)
    assert set(LABEL_COLS) <= set(lab.columns)


# ---------------------------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------------------------
def test_engine_fills_next_day_and_nav_is_causal(prices):
    open_raw, adj = to_wide(prices, "open"), to_wide(prices, "adj_factor")
    cal = open_raw.index
    tickers = list(open_raw.columns[:10])
    dates = cal[(cal >= "2024-07-01") & (cal <= "2025-06-30")][::5]
    W = pd.DataFrame(0.1, index=dates, columns=tickers)
    res = run_backtest(W, open_raw, adj)
    assert (res.fill_dates > dates[: len(res.fill_dates)]).all()
    assert (res.fill_dates == cal[cal.searchsorted(dates, side="right")]).all()
    # NAV up to date d must not depend on prices after d
    cut = dates[len(dates) // 2]
    p2 = _perturb_after(prices, cut)
    res2 = run_backtest(W, to_wide(p2, "open"), to_wide(p2, "adj_factor"))
    pd.testing.assert_series_equal(res.nav.loc[:cut], res2.nav.loc[:cut])
    assert not res.nav.loc[cut:].equals(res2.nav.loc[cut:])


def test_engine_rejects_signal_before_calendar(prices):
    open_raw, adj = to_wide(prices, "open"), to_wide(prices, "adj_factor")
    W = pd.DataFrame(0.1, index=[open_raw.index[0] - pd.Timedelta(days=10)], columns=list(open_raw.columns[:5]))
    with pytest.raises(LookaheadError):
        run_backtest(W, open_raw, adj)


def test_engine_drops_signal_without_fill_day(prices):
    open_raw, adj = to_wide(prices, "open"), to_wide(prices, "adj_factor")
    W = pd.DataFrame(0.1, index=[open_raw.index[-1]], columns=list(open_raw.columns[:5]))
    with pytest.warns(UserWarning):
        res = run_backtest(W, open_raw, adj)
    assert len(res.fill_dates) == 0 and (res.nav == 1.0).all()


# ---------------------------------------------------------------------------------------------
# end to end: a pure random-walk dummy run has no skill; a leaky run has a lot
# ---------------------------------------------------------------------------------------------
def test_end_to_end_no_leak_in_pipeline(cfg, data_root):
    from backtest.metrics import prediction_report
    from common.data import load_prices
    from common.universe import load_constituents
    prices = load_prices(cfg, data_root, include_benchmark=False)
    cons = load_constituents(cfg, data_root)
    run_inference(cfg, "rw", DummyBackend(seed=3), root=data_root, prices=prices, constituents=cons, log=lambda *_: None)
    run_inference(cfg, "leak", DummyBackend(seed=3, signal_strength=1.0, prices=prices), root=data_root,
                  prices=prices, constituents=cons, log=lambda *_: None)
    ics = {}
    for run_id in ("rw", "leak"):
        ctx = load_context(cfg, run_id, data_root)
        labelled = ctx.signals.join(realized_returns(ctx.prices, ctx.dates, 5), how="left")
        rep = prediction_report(labelled)
        ics[run_id] = rep["ret_cc"]["rank_ic"]["mean"]
        assert set(rep["by_index"]) == {"kospi200", "sp500"}
        assert all(abs(v["ret_cc"]["rank_ic"]["mean"] - ics[run_id]) < 0.5 for v in rep["by_index"].values())
    assert abs(ics["rw"]) < 0.08, f"random-walk predictions show IC {ics['rw']:.3f}: something leaks"
    assert ics["leak"] > 0.5, f"leaky predictions should have high IC, got {ics['leak']:.3f}"
