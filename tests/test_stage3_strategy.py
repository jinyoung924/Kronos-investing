"""Stage 3 (docs/spec.md): strategy interface, registry, three benchmark strategies, run_strategy."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from common.config import cfg_override, load_config
from common.paths import Paths
from common.schema import validate_weights
from D_strategy import registry
from D_strategy.base import check_weights, equal_weights, top_k
from D_strategy.run_strategy import run_one, run_strategies, schedule_dates

ROOT = Paths(load_config("configs/base.yaml"), ".").root
K_TEST = 10
TEST_STRATEGY_PARAMS = {
    "strategies.equal_weight": {"profile": "base", "schedule": "weekly"},
    "strategies.momentum20_topk": {"profile": "base", "schedule": "weekly", "k": K_TEST},
    "strategies.random_topk": {"profile": "base", "schedule": "weekly", "k": K_TEST},
}


@pytest.fixture(scope="module")
def scfg(cfg):
    return cfg_override(cfg, TEST_STRATEGY_PARAMS)


def _signals(date="2024-07-05", n=25, seed=0, ties=False, nan_mom=0):
    rng = np.random.default_rng(seed)
    t = [f"T{i:03d}" for i in range(n)]
    df = pd.DataFrame({
        "as_of_date": pd.Timestamp(date), "ticker": t,
        "exp_ret": rng.normal(0, 0.02, n), "exp_ret_mean": rng.normal(0, 0.01, n), "std": rng.uniform(0.01, 0.05, n),
        "p_up": rng.uniform(0, 1, n), "pred_range": rng.uniform(0.01, 0.1, n), "n_samples": 20, "last_close": 1000.0,
        "mom20": (np.full(n, 0.05) if ties else rng.normal(0, 0.1, n)), "vol20": rng.uniform(0.01, 0.05, n), "rev5": rng.normal(0, 0.03, n),
    })
    if nan_mom:
        df.loc[df.index[:nan_mom], "mom20"] = np.nan
    return df


def _sig_day(df):
    return df.set_index("ticker")


# ---------------------------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------------------------
def test_registry_names_files_and_config_keys_agree(scfg):
    names = registry.available()
    assert names == registry.strategy_module_names()
    files = sorted(p.stem for p in (ROOT / "D_strategy").glob("*.py") if p.stem not in registry.NON_STRATEGY_MODULES)
    assert names == files and len(registry.resolve("all")) == len(files)
    for n in names:
        cls = registry.discover()[n]
        assert cls.name == n and cls.__module__ == f"D_strategy.{n}"
        assert isinstance(scfg["strategies"].get(n), dict), f"configs strategies.{n} missing"
    assert registry.resolve("random_topk,equal_weight") == ["random_topk", "equal_weight"]
    with pytest.raises(ValueError):
        registry.resolve("no_such_strategy")
    with pytest.raises(ImportError):
        registry.register(type("Bad", (registry.Strategy,), {"name": "wrong_name", "weights": lambda *a: None, "__module__": "D_strategy.equal_weight"}))


@pytest.mark.parametrize("name", registry.available())
def test_every_strategy_respects_the_weight_contract(scfg, name):
    strat = registry.get_strategy(name, scfg)
    strat.reset()
    sig = _sig_day(_signals(nan_mom=3))
    before = sig.copy(deep=True)
    prev = pd.Series(dtype=float)
    w1 = check_weights(strat.weights("2024-07-05", sig, prev), sig, name, "2024-07-05")
    assert sig.equals(before)                                   # input untouched
    assert (w1.dropna() >= 0).all() and w1.dropna().sum() <= 1 + 1e-9
    assert set(w1.index) <= set(sig.index)
    strat.reset()
    w2 = strat.weights("2024-07-05", sig, prev)
    pd.testing.assert_series_equal(w1.sort_index(), check_weights(w2, sig, name).sort_index())   # deterministic
    # out-of-contract outputs are rejected
    with pytest.raises(ValueError):
        check_weights(pd.Series({"ZZZ": 0.5}), sig, name)
    with pytest.raises(ValueError):
        check_weights(pd.Series({"T000": -0.1}), sig, name)
    with pytest.raises(ValueError):
        check_weights(pd.Series({"T000": 0.6, "T001": 0.6}), sig, name)


def test_topk_strategies_hold_exactly_min_k_n(scfg):
    for name in ("momentum20_topk", "random_topk"):
        strat = registry.get_strategy(name, scfg)
        w = strat.weights("2024-07-05", _sig_day(_signals(n=25)), pd.Series(dtype=float))
        assert len(w) == K_TEST and np.allclose(w, 1 / K_TEST)
        w = strat.weights("2024-07-05", _sig_day(_signals(n=6)), pd.Series(dtype=float))
        assert len(w) == 6 and np.allclose(w, 1 / 6)
    # NaN mom20 names are not eligible for momentum
    strat = registry.get_strategy("momentum20_topk", scfg)
    w = strat.weights("2024-07-05", _sig_day(_signals(n=12, nan_mom=5)), pd.Series(dtype=float))
    assert len(w) == 7
    ew = registry.get_strategy("equal_weight", scfg).weights("2024-07-05", _sig_day(_signals(n=25)), pd.Series(dtype=float))
    assert len(ew) == 25 and ew.sum() == pytest.approx(1.0)


def test_momentum_picks_highest_mom20_and_breaks_ties_by_ticker(scfg):
    strat = registry.get_strategy("momentum20_topk", scfg)
    sig = _sig_day(_signals(n=30))
    w = strat.weights("2024-07-05", sig, pd.Series(dtype=float))
    assert set(w.index) == set(sig["mom20"].nlargest(K_TEST).index)
    tied = _sig_day(_signals(n=30, ties=True))
    a = strat.weights("2024-07-05", tied, pd.Series(dtype=float))
    b = strat.weights("2024-07-05", tied.sample(frac=1, random_state=3), pd.Series(dtype=float))   # shuffled rows
    assert list(a.index) == list(b.index) == sorted(tied.index)[:K_TEST]
    assert top_k(pd.Series({"b": 1.0, "a": 1.0, "c": np.nan}), 1) == ["a"]
    with pytest.raises(ValueError):
        top_k(pd.Series({"a": 1.0}), 0)


def test_random_topk_seed_and_date_behaviour(scfg):
    sig = _sig_day(_signals(n=40))
    s1 = registry.get_strategy("random_topk", scfg)
    s2 = registry.get_strategy("random_topk", scfg)
    s3 = registry.get_strategy("random_topk", cfg_override(scfg, {"project.seed": scfg["project"]["seed"] + 1}))
    w1 = s1.weights("2024-07-05", sig, pd.Series(dtype=float))
    w2 = s2.weights("2024-07-05", sig, pd.Series(dtype=float))
    w3 = s3.weights("2024-07-05", sig, pd.Series(dtype=float))
    pd.testing.assert_series_equal(w1, w2)
    assert set(w1.index) != set(w3.index)
    assert set(w1.index) != set(s1.weights("2024-07-12", sig, pd.Series(dtype=float)).index)   # a new draw per date
    shuffled = s1.weights("2024-07-05", sig.sample(frac=1, random_state=1), pd.Series(dtype=float))
    pd.testing.assert_series_equal(w1.sort_index(), shuffled.sort_index())                # independent of row order


def test_null_k_stops_with_a_clear_error(scfg):
    with pytest.raises(ValueError, match="strategies.momentum20_topk.k"):
        registry.get_strategy("momentum20_topk", cfg_override(scfg, {"strategies.momentum20_topk": {"profile": "base", "schedule": "weekly"}}))
    with pytest.raises(ValueError, match="strategies.random_topk.k"):
        registry.get_strategy("random_topk", cfg_override(scfg, {"strategies.random_topk": {"profile": "base", "schedule": "weekly"}}))


# ---------------------------------------------------------------------------------------------
# run_strategy on a tiny temp root
# ---------------------------------------------------------------------------------------------
@pytest.fixture()
def tmp_root(scfg, tmp_path):
    cal = pd.bdate_range("2024-06-03", "2024-08-30")
    paths = Paths(scfg, tmp_path)
    paths.prepared_dir().mkdir(parents=True)
    pd.DataFrame({"date": cal}).to_parquet(paths.prepared_path("calendar"), index=False)
    c = cfg_override(scfg, {"period.start": "2024-07-01", "period.end": "2024-08-09"})
    weekly = cal[(cal >= "2024-07-01") & (cal <= "2024-08-09")][::5]
    sig = pd.concat([_signals(d, n=25, seed=i) for i, d in enumerate(weekly)], ignore_index=True)
    pdir = paths.predictions_dir("run_base"); pdir.mkdir(parents=True)
    paths.manifest_file("run_base").write_text(json.dumps({"run_id": "run_base", "profile": "base", "pred_len": 5, "step": 5, "sample_count": 20}))
    paths.signals_dir("run_base").mkdir(parents=True)
    sig.to_parquet(paths.signals_path("run_base"), index=False)
    # a daily run of another profile
    daily = cal[(cal >= "2024-07-01") & (cal <= "2024-07-12")]
    sigd = pd.concat([_signals(d, n=25, seed=i) for i, d in enumerate(daily)], ignore_index=True)
    paths.predictions_dir("run_paper").mkdir(parents=True)
    paths.manifest_file("run_paper").write_text(json.dumps({"run_id": "run_paper", "profile": "paper", "pred_len": 10, "step": 1, "sample_count": 10}))
    paths.signals_dir("run_paper").mkdir(parents=True)
    sigd.to_parquet(paths.signals_path("run_paper"), index=False)
    return c, tmp_path, weekly, daily


def test_run_strategy_writes_weights_for_all_and_checks_profile(tmp_root):
    c, root, weekly, daily = tmp_root
    res = run_strategies(c, "run_base", registry.resolve("all"), root, log=lambda *_: None)
    paths = Paths(c, root)
    for name in registry.available():
        w = validate_weights(pd.read_parquet(paths.weights_path("run_base", name)))
        assert list(pd.DatetimeIndex(sorted(w["as_of_date"].unique()))) == list(weekly)
        assert (w["weight"] > 0).all()                                       # zeros are not stored
        meta = json.loads(paths.weights_meta_path("run_base", name).read_text())
        assert meta["stage"] == "D_strategy" and meta["strategy"] == name and meta["run_id"] == "run_base"
        assert meta["holdings_per_date"]["max"] == (25 if name == "equal_weight" else K_TEST)
        assert res[name]["n_dates"] == len(weekly)
    with pytest.raises(ValueError, match="profile"):
        run_strategies(c, "run_paper", ["equal_weight"], root, log=lambda *_: None)
    # a daily strategy of the paper profile rebalances on every signal date
    cd = cfg_override(c, {"strategies.equal_weight": {"profile": "paper", "schedule": "daily"}})
    run_strategies(cd, "run_paper", ["equal_weight"], root, log=lambda *_: None)
    w = pd.read_parquet(paths.weights_path("run_paper", "equal_weight"))
    assert list(pd.DatetimeIndex(sorted(w["as_of_date"].unique()))) == list(daily)
    # weekly dates without signals -> error
    ce = cfg_override(c, {"period.end": "2024-08-30"})
    with pytest.raises(ValueError, match="no signals"):
        run_strategies(ce, "run_base", ["equal_weight"], root, log=lambda *_: None)


def test_run_one_keeps_hold_nan_and_turnover(scfg):
    class Hold(registry.Strategy):
        name = "hold_test"
        def __init__(self):
            super().__init__({"profile": "base", "schedule": "weekly"}, 0)
            self.calls = 0
        def weights(self, date, signals, prev_w):
            self.calls += 1
            if self.calls == 1:
                return pd.Series({"T000": 0.5, "T001": 0.5, "T002": 0.0})
            return pd.Series({"T000": np.nan, "T003": 0.5})        # keep T000 (hold), sell T001, buy T003
    dates = pd.DatetimeIndex(["2024-07-05", "2024-07-12"])
    sig = pd.concat([_signals(d) for d in dates], ignore_index=True)
    w, diag = run_one(Hold(), sig, dates)
    first = w[w["as_of_date"] == dates[0]]
    assert set(first["ticker"]) == {"T000", "T001"}                # zero weight dropped
    second = w[w["as_of_date"] == dates[1]].set_index("ticker")["weight"]
    assert np.isnan(second["T000"]) and second["T003"] == 0.5 and "T001" not in second.index
    assert diag["n_hold_rows"] == 1 and diag["holdings_per_date"]["max"] == 2
    assert diag["turnover_per_rebalance"]["mean"] == pytest.approx(0.5)      # sell 0.5, buy 0.5 -> 0.5


def test_schedule_dates_weekly_matches_calendar(scfg):
    cal = pd.bdate_range("2024-06-03", "2024-08-30")
    c = cfg_override(scfg, {"period.start": "2024-07-01", "period.end": "2024-08-09"})
    weekly = cal[(cal >= "2024-07-01") & (cal <= "2024-08-09")][::5]
    sig = pd.concat([_signals(d) for d in weekly], ignore_index=True)
    strat = registry.get_strategy("equal_weight", c)
    assert list(schedule_dates(strat, sig, cal, c, 5)) == list(weekly)


def test_real_weights_on_disk():
    cfg = load_config(ROOT / "configs/base.yaml")
    paths = Paths(cfg, ROOT)
    if not paths.weights_path("fake_dummy_base", "equal_weight").exists():
        pytest.skip("run D_strategy.run_strategy --run-id fake_dummy_base --strategy all first")
    sig = pd.read_parquet(paths.signals_path("fake_dummy_base"))
    for name in registry.available():
        w = validate_weights(pd.read_parquet(paths.weights_path("fake_dummy_base", name)))
        assert w["as_of_date"].nunique() == 49
        for d, g in w.groupby("as_of_date"):
            assert set(g["ticker"]) <= set(sig.loc[sig["as_of_date"] == d, "ticker"])
            assert g["weight"].sum() == pytest.approx(1.0)
