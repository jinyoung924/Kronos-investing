"""Stage 0: common/ modules (config hash, require, schema validators, paths, meta, lookahead aliases)."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from common.config import ConfigError, cfg_override, config_hash, load_config, require
from common.lookahead import LookaheadError, assert_fill_after_signal, assert_no_future, assert_no_future_rows
from common.meta import file_hash, read_meta, write_meta
from common.paths import Paths
from common.schema import (validate_nav, validate_predictions, validate_prices, validate_signals, validate_weights,
                           weights_to_wide)


# ---------------------------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------------------------
def test_config_hash_ignores_key_order_but_not_values():
    a = {"x": 1, "y": {"p": [1, 2], "q": "s"}}
    b = {"y": {"q": "s", "p": [1, 2]}, "x": 1}
    assert config_hash(a) == config_hash(b)
    assert config_hash(a) != config_hash({**a, "x": 2})
    assert len(config_hash(a)) == 12


def test_require_raises_on_null_and_missing(cfg):
    assert require(cfg, "period.start") == "2024-07-01"
    with pytest.raises(ConfigError):
        require(cfg, "backtest.init_cash")        # [사용자] value left null
    with pytest.raises(ConfigError):
        require(cfg, "does.not.exist")
    assert require(cfg_override(cfg, {"backtest.init_cash": 1e8}), "backtest.init_cash") == 1e8


def test_base_config_skeleton_has_every_stage_section():
    cfg = load_config("configs/base.yaml")
    for key in ("project", "period", "data", "universe", "krx", "model", "infer", "signal", "strategies",
                "costs", "backtest", "evaluate", "report"):
        assert key in cfg, key
    assert set(cfg["infer"]["profiles"]) == {"base", "paper"}
    assert cfg["infer"]["profiles"]["paper"]["step"] == 1 and cfg["infer"]["profiles"]["base"]["step"] == 5
    # user-decided values stay null until the user fills them in
    assert cfg["costs"]["sell_tax_table"] == [] and cfg["backtest"]["init_cash"] is None


# ---------------------------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------------------------
def test_paths_do_not_overlap_across_run_ids_or_strategies(cfg, tmp_path):
    p = Paths(cfg, tmp_path)
    a, b = "run_a", "run_b"
    pairs = [
        (p.predictions_dir(a), p.predictions_dir(b)),
        (p.signals_path(a), p.signals_path(b)),
        (p.weights_path(a, "topk"), p.weights_path(b, "topk")),
        (p.weights_path(a, "topk"), p.weights_path(a, "equal_weight")),
        (p.backtest_dir(a, "v1", "topk"), p.backtest_dir(b, "v1", "topk")),
        (p.backtest_dir(a, "v1", "topk"), p.backtest_dir(a, "v2", "topk")),
        (p.backtest_dir(a, "v1", "topk"), p.backtest_dir(a, "v1", "equal_weight")),
        (p.metrics_dir(a), p.metrics_dir(b)),
        (p.report_dir(a), p.report_dir(b)),
    ]
    for x, y in pairs:
        assert x != y and not x.is_relative_to(y) and not y.is_relative_to(x), (x, y)
    # every stage output lives under its own letter folder; everything is under the given root
    assert p.prepared_path("prices").parts[-3:] == ("A_prepared", "prices.parquet")[-2:] or "A_prepared" in p.prepared_path("prices").parts
    assert "B_predictions" in p.prediction_file(a, "2024-07-01").parts
    assert p.prediction_file(a, "2024-07-01").name == "as_of=2024-07-01.parquet"
    assert "C_signals" in p.signals_path(a).parts and "D_weights" in p.weights_path(a, "s").parts
    assert "E_backtest" in p.backtest_dir(a, "v1", "s").parts and "F_metrics" in p.metrics_dir(a).parts
    assert p.report_dir(a).parts[-2] == "reports"
    for q in (p.raw_dir(), p.prepared_dir(), p.predictions_dir(a), p.metrics_dir(a), p.report_dir(a)):
        assert q.is_relative_to(tmp_path.resolve())
    assert not hasattr(p, "results_dir")


# ---------------------------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------------------------
def _prices():
    d = pd.to_datetime(["2024-07-01", "2024-07-02"])
    return pd.DataFrame({
        "date": list(d) * 2, "ticker": ["000020"] * 2 + ["005930"] * 2, "market": ["KOSPI"] * 4,
        "open": [100.0, np.nan, 50.0, 51.0], "high": [110.0, np.nan, 52.0, 53.0], "low": [95.0, np.nan, 49.0, 50.0],
        "close": [105.0, 105.0, 51.0, 52.0], "volume": [10.0, 0.0, 5.0, 6.0], "value": [1050.0, 0.0, 255.0, 312.0],
    })


def test_validate_prices_accepts_halt_rows_and_rejects_bad_frames():
    out = validate_prices(_prices())
    assert out["date"].dtype.kind == "M" and out["ticker"].dtype == object
    with pytest.raises(ValueError, match="missing columns"):
        validate_prices(_prices().drop(columns=["value"]))
    with pytest.raises(ValueError, match="duplicate"):
        validate_prices(pd.concat([_prices(), _prices().iloc[:1]]))
    with pytest.raises(ValueError, match="negative"):
        validate_prices(_prices().assign(volume=[-1.0, 0.0, 5.0, 6.0]))
    with pytest.raises(ValueError, match="NaN close"):
        validate_prices(_prices().assign(close=[np.nan, 105.0, 51.0, 52.0]))
    with pytest.raises(ValueError, match="high"):
        validate_prices(_prices().assign(high=[90.0, np.nan, 52.0, 53.0]))     # high < close
    with pytest.raises(ValueError, match="not coercible"):
        validate_prices(_prices().assign(close=["a", "b", "c", "d"]))


def _signals():
    return pd.DataFrame({
        "as_of_date": pd.to_datetime(["2024-07-05"] * 2), "ticker": ["000020", "005930"],
        "exp_ret": [0.01, -0.02], "exp_ret_mean": [0.005, -0.01], "std": [0.02, 0.03], "p_up": [0.6, 0.3],
        "pred_range": [0.04, 0.05], "n_samples": [20, 20], "mom20": [0.1, np.nan],
    })


def test_validate_signals_ranges_and_keys():
    out = validate_signals(_signals())
    assert out["n_samples"].dtype == "int64" and "mom20" in out.columns
    with pytest.raises(ValueError, match="p_up"):
        validate_signals(_signals().assign(p_up=[1.2, 0.3]))
    with pytest.raises(ValueError, match="negative"):
        validate_signals(_signals().assign(std=[-0.1, 0.3]))
    with pytest.raises(ValueError, match="non-finite"):
        validate_signals(_signals().assign(exp_ret=[np.inf, 0.0]))
    with pytest.raises(ValueError, match="duplicate"):
        validate_signals(_signals().assign(ticker=["000020", "000020"]))
    with pytest.raises(ValueError, match="n_samples"):
        validate_signals(_signals().assign(n_samples=[0, 20]))
    with pytest.raises(ValueError, match="missing columns"):
        validate_signals(_signals().drop(columns=["exp_ret_mean"]))


def _weights():
    return pd.DataFrame({
        "as_of_date": pd.to_datetime(["2024-07-05"] * 3 + ["2024-07-12"] * 2),
        "ticker": ["a", "b", "c", "a", "b"], "weight": [0.5, 0.3, np.nan, 0.6, 0.4],
    })


def test_validate_weights_long_only_sum_and_hold_nan():
    out = validate_weights(_weights())
    wide = weights_to_wide(out)
    assert wide.shape == (2, 3) and np.isnan(wide.loc["2024-07-05", "c"]) and np.isnan(wide.loc["2024-07-12", "c"])
    with pytest.raises(ValueError, match="negative"):
        validate_weights(_weights().assign(weight=[0.5, -0.1, np.nan, 0.6, 0.4]))
    with pytest.raises(ValueError, match="sum"):
        validate_weights(_weights().assign(weight=[0.7, 0.4, np.nan, 0.6, 0.4]))
    with pytest.raises(ValueError, match="duplicate"):
        validate_weights(_weights().assign(ticker=["a", "a", "c", "a", "b"]))
    with pytest.raises(ValueError, match="infinite"):
        validate_weights(_weights().assign(weight=[np.inf, 0.3, np.nan, 0.6, 0.4]))
    with pytest.raises(ValueError, match="missing columns"):
        validate_weights(_weights().rename(columns={"weight": "w"}))


def _nav():
    return pd.DataFrame({"date": pd.to_datetime(["2024-07-01", "2024-07-02", "2024-07-03"]),
                         "nav": [1.0, 1.01, 1.005], "ret": [0.0, 0.01, -0.00495], "cost": [0.0, 0.0001, 0.0]})


def test_validate_nav_positive_sorted_finite():
    validate_nav(_nav())
    with pytest.raises(ValueError, match="sorted"):
        validate_nav(_nav().iloc[::-1])
    with pytest.raises(ValueError, match="> 0"):
        validate_nav(_nav().assign(nav=[1.0, 0.0, 1.0]))
    with pytest.raises(ValueError, match="non-finite"):
        validate_nav(_nav().assign(ret=[0.0, np.nan, 0.0]))
    with pytest.raises(ValueError, match="negative"):
        validate_nav(_nav().assign(cost=[0.0, -0.1, 0.0]))
    with pytest.raises(ValueError, match="duplicate"):
        validate_nav(_nav().assign(date=pd.to_datetime(["2024-07-01"] * 3)))


def test_validate_predictions_still_rejects_bad_dtypes():
    df = pd.DataFrame({"as_of_date": [pd.Timestamp("2024-07-05")], "ticker": ["a"], "horizon_step": [1], "sample_id": [0],
                       "pred_open": [1.0], "pred_high": [1.0], "pred_low": [1.0], "pred_close": [1.0], "pred_volume": [1.0]})
    validate_predictions(df, as_of="2024-07-05", horizon=5)
    with pytest.raises(ValueError):
        validate_predictions(df.assign(pred_close=[np.nan]))
    with pytest.raises(ValueError):
        validate_predictions(df.assign(horizon_step=["x"]))
    with pytest.raises(ValueError):
        validate_predictions(df.drop(columns=["sample_id"]))


# ---------------------------------------------------------------------------------------------
# meta
# ---------------------------------------------------------------------------------------------
def test_write_meta_records_hashes_and_identity(cfg, tmp_path):
    inp = tmp_path / "in.parquet"
    inp.write_bytes(b"hello")
    out_dir = tmp_path / "out"
    write_meta(out_dir, cfg, inputs={"prices": inp, "missing": tmp_path / "nope.parquet"},
               extra={"run_id": "r1", "stage": "E_backtest", "strategy": "topk", "engine": "v1"}, root=tmp_path)
    meta = read_meta(out_dir)
    assert meta["run_id"] == "r1" and meta["stage"] == "E_backtest" and meta["strategy"] == "topk" and meta["engine"] == "v1"
    assert meta["config_hash"] == config_hash(cfg)
    assert meta["inputs"]["prices"]["sha256"] == file_hash(inp) and meta["inputs"]["prices"]["bytes"] == 5
    assert meta["inputs"]["missing"]["missing"] is True and meta["inputs"]["missing"]["sha256"] is None
    assert meta["git_commit"] is None  # tmp_path is not a git repo
    assert "run_at_utc" in meta
    json.loads((out_dir / "meta.json").read_text())  # valid JSON on disk


# ---------------------------------------------------------------------------------------------
# lookahead aliases
# ---------------------------------------------------------------------------------------------
def test_lookahead_spec_aliases():
    assert assert_no_future_rows is assert_no_future
    trades = pd.DataFrame({"signal_date": pd.to_datetime(["2024-07-05", "2024-07-12"]),
                           "fill_date": pd.to_datetime(["2024-07-08", "2024-07-15"])})
    assert_fill_after_signal(trades)
    assert_fill_after_signal(trades.iloc[:0])
    with pytest.raises(LookaheadError):
        assert_fill_after_signal(trades.assign(fill_date=trades["signal_date"]))
    with pytest.raises(ValueError):
        assert_fill_after_signal(trades.drop(columns=["fill_date"]))
