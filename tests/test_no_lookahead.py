"""Lookahead guards that survive the rewrite: common/lookahead, common/universe, common/schema and
the inference input builder (B_model_infer/build_batch, B_model_infer/run_inference). Engine- and signal-level
lookahead tests are rebuilt per Stage in docs/spec.md."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from common.lookahead import LookaheadError, assert_no_future, assert_signal_before_fill, slice_as_of
from common.paths import Paths
from common.schema import validate_predictions
from common.universe import universe_at
from B_model_infer.backends import DummyBackend
from B_model_infer.build_batch import build_batch
from B_model_infer.run_inference import run as run_inference


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
    # rebased last close == raw close at as_of (predictions are on the raw scale of as_of)
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
    df = pd.read_parquet(paths.prediction_file("rw", written[0]))
    assert (df["as_of_date"] == written[0]).all()
    validate_predictions(df, as_of=written[0], horizon=int(cfg["infer"]["profiles"]["base"]["pred_len"]))
    with pytest.raises(ValueError):
        validate_predictions(df, as_of=written[1])


def test_inference_is_resumable(cfg, prices, constituents, tmp_path):
    backend = DummyBackend(seed=0)
    w1 = run_inference(cfg, "rw", backend, root=tmp_path, prices=prices, constituents=constituents, log=lambda *_: None)
    w2 = run_inference(cfg, "rw", backend, root=tmp_path, prices=prices, constituents=constituents, log=lambda *_: None)
    assert len(w1) > 0 and w2 == []
    manifest = Paths(cfg, tmp_path).manifest_file("rw")
    assert manifest.exists()
