"""Stage 6 (docs/spec.md): conf_weighted, vol_target and the prediction-folder validator.
The shared weight-contract test of Stage 3 picks the three Kronos strategies up from the registry; TopK's own
rules are in tests/test_topk_dropout.py."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from B_model_infer import validate_predictions as vp
from common.config import cfg_override
from common.paths import Paths
from common.schema import predictions_from_array
from D_strategy import registry
from D_strategy.base import check_weights

K = 5


@pytest.fixture(scope="module")
def kcfg(cfg):
    return cfg_override(cfg, {
        "strategies.conf_weighted": {"profile": "base", "schedule": "weekly", "signal_col": "exp_ret", "threshold": 0.5},
        "strategies.vol_target": {"profile": "base", "schedule": "weekly", "signal_col": "exp_ret", "k": K},
    })


def _day(exp_ret, std, pred_range):
    n = len(exp_ret)
    return pd.DataFrame({"exp_ret": exp_ret, "exp_ret_mean": exp_ret, "std": std, "p_up": 0.5, "pred_range": pred_range,
                         "n_samples": 20, "last_close": 1000.0, "mom20": 0.0, "vol20": 0.02, "rev5": 0.0},
                        index=pd.Index([f"T{i:02d}" for i in range(n)], name="ticker"))


def test_three_kronos_strategies_are_registered():
    assert {"topk", "conf_weighted", "vol_target"} <= set(registry.available())


def test_conf_weighted_weights_by_score_and_goes_to_cash_below_threshold(kcfg):
    strat = registry.get_strategy("conf_weighted", kcfg)
    #        score:   1.0    0.5    0.4   -1.0   (std 0)  (std nan)
    sig = _day([0.02, 0.01, 0.008, -0.02, 0.05, 0.05], [0.02, 0.02, 0.02, 0.02, 0.0, np.nan], 0.03)
    w = check_weights(strat.weights("2024-07-05", sig, pd.Series(dtype=float)), sig, "conf_weighted")
    assert list(w.index) == ["T00", "T01"]                               # score >= 0.5 only; zero / NaN std are not eligible
    assert w["T00"] == pytest.approx(1.0 / 1.5) and w["T01"] == pytest.approx(0.5 / 1.5) and w.sum() == pytest.approx(1.0)
    low = _day([0.005, 0.001, -0.01], [0.02, 0.02, 0.02], 0.03)          # every score < 0.5 -> all cash
    assert strat.weights("2024-07-05", low, pd.Series(dtype=float)).empty
    # a non-positive threshold never produces a negative or zero weight
    loose = registry.get_strategy("conf_weighted", cfg_override(kcfg, {"strategies.conf_weighted.threshold": -5.0}))
    w0 = check_weights(loose.weights("2024-07-05", sig, pd.Series(dtype=float)), sig, "conf_weighted")
    assert (w0 > 0).all() and "T03" not in w0.index
    with pytest.raises(ValueError, match="threshold"):
        registry.get_strategy("conf_weighted", cfg_override(kcfg, {"strategies.conf_weighted": {"profile": "base", "schedule": "weekly"}}))


def test_vol_target_inverse_range_weights_and_zero_range(kcfg):
    strat = registry.get_strategy("vol_target", kcfg)
    exp_ret = [0.09, 0.08, 0.07, 0.06, 0.05, 0.04, 0.03, 0.02]
    rng = [0.02, 0.04, 0.0, 0.08, np.nan, 0.02, 0.04, 0.02]              # T02 has range 0, T04 NaN
    sig = _day(exp_ret, 0.02, rng)
    w = check_weights(strat.weights("2024-07-05", sig, pd.Series(dtype=float)), sig, "vol_target")
    assert sorted(w.index) == ["T00", "T01", "T03", "T05", "T06"]        # best K among tickers with a positive finite range
    inv = pd.Series({"T00": 50.0, "T01": 25.0, "T03": 12.5, "T05": 50.0, "T06": 25.0})
    pd.testing.assert_series_equal(w.sort_index(), (inv / inv.sum()).rename("weight"), check_names=False)
    assert w.sum() == pytest.approx(1.0) and np.isfinite(w).all()
    none = _day([0.01, 0.02], 0.02, [0.0, 0.0])                          # no eligible ticker -> all cash
    assert strat.weights("2024-07-05", none, pd.Series(dtype=float)).empty


# ---------------------------------------------------------------------------------------------
# B_model_infer.validate_predictions (pure checks on small frames)
# ---------------------------------------------------------------------------------------------
PROFILE = {"profile": "base", "lookback": 60, "pred_len": 3, "step": 5, "temperature": 1.0, "top_p": 0.9, "sample_count": 4}


def test_manifest_profile_check_reports_mismatches():
    m = {"profile": "base", "lookback": 60, "pred_len": 3, "step": 5, "temperature": 1.0, "top_p": 0.9, "sample_count": 4}
    assert vp.check_manifest(m, PROFILE) == {}
    bad = vp.check_manifest({**m, "temperature": 0.6, "sample_count": 2}, PROFILE)
    assert bad == {"temperature": {"manifest": 0.6, "config": 1.0}, "sample_count": {"manifest": 2, "config": 4}}


def test_date_and_shape_checks():
    expected = pd.DatetimeIndex(["2024-07-01", "2024-07-08", "2024-07-15"])
    assert vp.check_dates(pd.DatetimeIndex(["2024-07-01", "2024-07-15", "2024-07-16"]), expected) == {
        "n_expected": 3, "n_found": 3, "missing": ["2024-07-08"], "unexpected": ["2024-07-16"]}
    rng = np.random.default_rng(0)
    df = predictions_from_array("2024-07-01", ["a", "b"], rng.lognormal(0, 0.01, (2, 4, 3, 5)) * 100)
    assert vp.check_shape(df, horizon=3, sample_count=4) == {"n_tickers": 2, "bad_steps": 0, "bad_sample_count": 0}
    short = df[~((df["ticker"] == "b") & (df["sample_id"] == 3))]
    assert vp.check_shape(short, horizon=3, sample_count=4)["bad_sample_count"] == 1
    assert vp.check_shape(df[df["horizon_step"] < 3], horizon=3, sample_count=4)["bad_steps"] == 2


def test_price_basis_check_separates_raw_from_adjusted():
    # ticker S split 5:1 after as_of: raw close 5000, adjusted close 1000. Predictions near 5000 = raw basis.
    closes = pd.DataFrame({"ticker": ["S", "N"], "raw_close": [5000.0, 800.0], "adj_close": [1000.0, 800.0]})
    step1 = pd.DataFrame({"ticker": ["S", "N"], "pred_close": [5050.0, 790.0]})
    out = vp.price_basis(step1, closes)
    assert out["n_split_tickers"] == 1 and out["closer_to_raw"] == 1 and out["closer_to_adjusted"] == 0
    assert out["median_ratio_to_raw"] == pytest.approx(1.01) and out["median_ratio_to_adjusted"] == pytest.approx(5.05)
    adj = vp.price_basis(pd.DataFrame({"ticker": ["S"], "pred_close": [1010.0]}), closes)
    assert adj["closer_to_adjusted"] == 1 and adj["closer_to_raw"] == 0
