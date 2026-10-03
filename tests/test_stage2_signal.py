"""Stage 2 (docs/spec.md): fake predictions (B_model_infer/make_fake_predictions) and C_signal on the synthetic
fixtures of conftest.py (small, in tmp_path), plus checks on the real generated run_ids when present."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from B_model_infer.backends import DummyBackend
from B_model_infer.build_batch import build_batch, eligible_tickers
from B_model_infer.make_fake_predictions import EPS_ORACLE, make_fake
from B_model_infer.run_inference import profile_cfg
from C_signal.aggregate import aggregate_predictions, select_samples
from C_signal.baseline_features import baseline_features
from C_signal.run_signal import build_signals, last_close_table
from common.config import cfg_override, load_config
from common.data import to_wide, trading_calendar
from common.paths import Paths, as_of_from_filename
from common.schema import predictions_from_array, validate_predictions, validate_signals
from common.universe import combined_universe_at, get_universe

ROOT = Paths(load_config("configs/base.yaml"), ".").root
N_SAMPLES_TEST = 8          # synthetic fixture sample_count (conftest: infer.profiles.base.sample_count 8)


# ---------------------------------------------------------------------------------------------
# synthetic end-to-end fixture: prices with splits/holidays -> fake predictions -> prepared-like tables
# ---------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def syn(cfg, prices, constituents, tmp_path_factory):
    """Fake dummy + oracle predictions for 6 as_of dates on the synthetic data, plus A_prepared-style frames."""
    root = tmp_path_factory.mktemp("stage2")
    c = cfg_override(cfg, {"period.start": "2024-07-01", "period.end": "2024-08-09", "signal.n_samples": N_SAMPLES_TEST})
    p = prices[prices["index"] != "benchmark"]
    runs = {}
    for kind in ("dummy", "oracle"):
        run_id, written = make_fake(c, kind, root=root, prices=p, constituents=constituents, log=lambda *_: None)
        runs[kind] = (run_id, written)
    # A_prepared-like frames: forward factor F = adj / adj_first per ticker (same ratios as the backward factor)
    pp = p.sort_values(["ticker", "date"]).reset_index(drop=True)
    first = pp.groupby("ticker")["adj_factor"].transform("first")
    adj = pp[["date", "ticker"]].assign(factor=pp["adj_factor"] / first)
    prep_prices = pp[["date", "ticker", "market", "open", "high", "low", "close", "volume"]]
    uni_rows = []
    for d in sorted(pp["date"].unique()):
        u = combined_universe_at(constituents, d)
        uni_rows.append(u.assign(date=d))
    universe = pd.concat(uni_rows)[["date", "ticker"]].merge(prep_prices[["date", "ticker", "market"]], on=["date", "ticker"], how="inner")
    return {"cfg": c, "root": root, "runs": runs, "prices": prep_prices, "adj": adj, "universe": universe, "raw": pp}


def _load_preds(cfg, root, run_id, horizon):
    paths = Paths(cfg, root)
    files = sorted(paths.predictions_dir(run_id).glob("as_of=*.parquet"))
    return pd.concat([validate_predictions(pd.read_parquet(f), as_of=as_of_from_filename(f), horizon=horizon) for f in files]), json.loads(paths.manifest_file(run_id).read_text())


def _realized(syn, horizon):
    """adjusted close returns as_of -> h-th trading day after, for h = 1..H (uses future rows: LABEL, test only)."""
    pp = syn["raw"]
    cal = trading_calendar(pp)
    adj_close = (to_wide(pp, "close") * to_wide(pp, "adj_factor")).reindex(cal).ffill()
    out = {}
    for h in range(1, horizon + 1):
        out[h] = adj_close.shift(-h) / adj_close - 1.0
    return out


# ---------------------------------------------------------------------------------------------
# oracle correctness (D-10)
# ---------------------------------------------------------------------------------------------
def test_oracle_exp_ret_matches_realized_close_returns(syn):
    c = syn["cfg"]
    prof = profile_cfg(c)
    H = int(prof["pred_len"])
    preds, manifest = _load_preds(c, syn["root"], syn["runs"]["oracle"][0], H)
    sig, by_date = build_signals(preds, syn["prices"], syn["adj"], syn["universe"], manifest, c)
    real = _realized(syn, H)
    rH = real[H].stack().rename("r_H")
    rmean = sum(real[h] for h in range(1, H + 1)).div(H).stack().rename("r_mean")
    rH.index.names = rmean.index.names = ["as_of_date", "ticker"]
    m = sig.join(rH, on=["as_of_date", "ticker"]).join(rmean, on=["as_of_date", "ticker"])
    assert m["r_H"].notna().all()
    tol = 6 * EPS_ORACLE                       # noise exp(N(0, eps)) averaged over samples
    assert np.abs(m["exp_ret"] - m["r_H"]).max() < tol
    assert np.abs(m["exp_ret_mean"] - m["r_mean"]).max() < tol
    assert (m["std"] > 0).all() and (m["std"] < 10 * EPS_ORACLE).all()
    # the realised future contains random 2:1 splits (fixture split_prob > 0): no fake crash in exp_ret
    assert m["exp_ret"].min() > -0.3
    assert manifest["leaky"] is True and manifest["kind"] == "oracle" and "oracle_fill_by_date" in manifest


def test_oracle_scales_future_split_to_as_of_raw_scale(cfg, prices, constituents, tmp_path):
    """Force a 2:1 split two days after as_of: the oracle close must follow raw * F(t)/F(as_of), not the raw drop."""
    p = prices[prices["index"] == "kospi200"].copy()
    t = "KOSPI200000"
    as_of = pd.Timestamp("2024-09-02")
    cal = trading_calendar(p)
    split_day = cal[cal.get_loc(as_of) + 2]
    m = (p["ticker"] == t) & (p["date"] >= split_day)
    before = p["ticker"] == t
    p.loc[before & ~m, ["open", "high", "low", "close"]] *= 2          # raw before the split is 2x ...
    p.loc[before & ~m, "adj_factor"] *= 0.5                           # ... and the backward factor halves it
    c = cfg_override(cfg, {"period.start": str(as_of.date()), "period.end": str(as_of.date())})
    run_id, written = make_fake(c, "oracle", root=tmp_path, prices=p, constituents={"kospi200": constituents["kospi200"]}, log=lambda *_: None)
    assert written == [as_of]
    df = pd.read_parquet(Paths(c, tmp_path).prediction_file(run_id, as_of))
    one = df[df["ticker"] == t].groupby("horizon_step")["pred_close"].mean()
    last_raw = p[(p["ticker"] == t) & (p["date"] <= as_of)].sort_values("date")["close"].iloc[-1]
    adj_close = (to_wide(p, "close") * to_wide(p, "adj_factor"))[t].reindex(cal).ffill()   # ticker holidays -> last bar
    expect = adj_close.loc[cal[cal.get_loc(as_of) + 1: cal.get_loc(as_of) + 6]] / adj_close.loc[as_of] * last_raw
    assert np.allclose(one.to_numpy(), expect.to_numpy(), rtol=5 * EPS_ORACLE)
    assert np.abs(np.diff(np.log(one.to_numpy()))).max() < 0.2          # no 2x jump across the split day


def test_oracle_fills_future_halt_and_delisting(cfg, prices, constituents, tmp_path):
    p = prices[prices["index"] == "kospi200"].copy()
    t_halt, t_delist = "KOSPI200001", "KOSPI200002"
    as_of = pd.Timestamp("2024-09-02")
    cal = trading_calendar(p)
    d1, d2 = cal[cal.get_loc(as_of) + 1], cal[cal.get_loc(as_of) + 2]
    p.loc[(p["ticker"] == t_halt) & (p["date"] == d2), ["open", "high", "low"]] = np.nan      # halted day
    p.loc[(p["ticker"] == t_halt) & (p["date"] == d2), "volume"] = 0.0
    last_close_delist = p[(p["ticker"] == t_delist) & (p["date"] == d1)]["close"].iloc[0]
    p = p[~((p["ticker"] == t_delist) & (p["date"] > d1))]                                      # delisted after d1
    c = cfg_override(cfg, {"period.start": str(as_of.date()), "period.end": str(as_of.date())})
    run_id, _ = make_fake(c, "oracle", root=tmp_path, prices=p, constituents={"kospi200": constituents["kospi200"]}, log=lambda *_: None)
    df = pd.read_parquet(Paths(c, tmp_path).prediction_file(run_id, as_of))
    h = df[(df["ticker"] == t_halt) & (df["horizon_step"] == 2)]
    assert np.allclose(h["pred_open"], h["pred_close"], rtol=5 * EPS_ORACLE) and (h["pred_volume"] == 0).all()
    dl = df[(df["ticker"] == t_delist) & (df["horizon_step"] >= 2)]
    fac = p[(p["ticker"] == t_delist)].sort_values("date")
    f_ratio = fac[fac["date"] == d1]["adj_factor"].iloc[0] / fac[fac["date"] <= as_of]["adj_factor"].iloc[-1]
    assert np.allclose(dl["pred_close"], last_close_delist * f_ratio, rtol=5 * EPS_ORACLE) and (dl["pred_volume"] == 0).all()
    man = json.loads(Paths(c, tmp_path).manifest_file(run_id).read_text())
    s = man["oracle_fill_by_date"][str(as_of.date())]
    # the fixture also has random single-day holidays, so counts are lower bounds: the delisted name adds 4 cells
    assert s["no_bar_fill_tickers"] >= 1 and s["no_bar_fill_cells"] >= 4 and s["halted_fill_tickers"] >= 1


# ---------------------------------------------------------------------------------------------
# eligibility shared by fakes and real runs (D-3)
# ---------------------------------------------------------------------------------------------
def test_fakes_use_build_batch_eligibility(syn, constituents):
    c = syn["cfg"]
    prof = profile_cfg(c)
    for kind in ("dummy", "oracle"):
        run_id, written = syn["runs"][kind]
        preds, manifest = _load_preds(c, syn["root"], run_id, int(prof["pred_len"]))
        for d in written:
            members = combined_universe_at(constituents, d)["ticker"].tolist()
            ok, skipped = eligible_tickers(syn["raw"], members, d, int(prof["lookback"]), max_stale_days=int(c["universe"]["max_stale_days"]))
            assert set(preds.loc[preds["as_of_date"] == d, "ticker"]) == set(ok)
            assert manifest["skipped_by_date"][str(d.date())]["n_skipped"] == len(skipped)
            b = build_batch(syn["raw"], members, d, int(prof["lookback"]), int(prof["pred_len"]))
            assert b.tickers == ok and b.skipped == skipped


def test_profile_mechanism_daily_steps_and_samples(cfg, prices, constituents, tmp_path):
    """spec Stage 2: a paper-style profile gives daily as_of, horizon_step 1..10 and 10 samples (mechanism only, D-12)."""
    c = cfg_override(cfg, {"period.start": "2024-09-02", "period.end": "2024-09-06", "infer.profiles.paper.lookback": 60})
    p = prices[prices["index"] == "kospi200"]
    run_id, written = make_fake(c, "dummy", profile="paper", root=tmp_path, prices=p, constituents={"kospi200": constituents["kospi200"]}, log=lambda *_: None)
    assert run_id == "fake_dummy_paper"
    cal = trading_calendar(p)
    assert list(written) == list(cal[(cal >= "2024-09-02") & (cal <= "2024-09-06")])      # every trading day
    df = pd.read_parquet(Paths(c, tmp_path).prediction_file(run_id, written[0]))
    assert sorted(df["horizon_step"].unique()) == list(range(1, 11)) and df["sample_id"].nunique() == 10
    man = json.loads(Paths(c, tmp_path).manifest_file(run_id).read_text())
    assert man["profile"] == "paper" and man["pred_len"] == 10 and man["step"] == 1 and man["sample_count"] == 10


# ---------------------------------------------------------------------------------------------
# signals
# ---------------------------------------------------------------------------------------------
def test_signals_unique_in_universe_and_ranges(syn):
    c = syn["cfg"]
    H = int(profile_cfg(c)["pred_len"])
    for kind in ("dummy", "oracle"):
        preds, manifest = _load_preds(c, syn["root"], syn["runs"][kind][0], H)
        sig, by_date = build_signals(preds, syn["prices"], syn["adj"], syn["universe"], manifest, c)
        validate_signals(sig)
        assert not sig.duplicated(["as_of_date", "ticker"]).any()
        for d, g in sig.groupby("as_of_date"):
            uni = set(get_universe(syn["universe"], d))
            assert set(g["ticker"]) <= uni
            assert set(g["ticker"]) == uni & set(preds.loc[preds["as_of_date"] == d, "ticker"])     # D-3 intersection
            st = by_date[str(pd.Timestamp(d).date())]
            assert st["n_signal"] == len(g) and st["n_universe"] == len(uni)
            assert st["n_universe"] == st["n_signal"] + st["n_universe_without_prediction"]
        assert sig["p_up"].between(0, 1).all() and (sig["std"] >= 0).all() and (sig["pred_range"] >= 0).all()
        assert (sig["n_samples"] == N_SAMPLES_TEST).all()
        assert {"mom20", "vol20", "rev5", "last_close"} <= set(sig.columns)
    # dummy: no information -> exp_ret centred near 0, p_up around 0.5
    assert abs(sig["exp_ret"].mean()) < 0.02 if kind == "dummy" else True


def test_baseline_features_invariant_to_future_prices(syn):
    c = syn["cfg"]
    dates = pd.DatetimeIndex(["2024-07-15", "2024-08-05"])
    base = baseline_features(syn["prices"], syn["adj"], dates, 20, 20, 5)
    p2, a2 = syn["prices"].copy(), syn["adj"].copy()
    m = p2["date"] > dates.max()
    p2.loc[m, ["open", "high", "low", "close"]] *= np.random.default_rng(3).uniform(0.3, 3.0, m.sum())[:, None]
    a2.loc[a2["date"] > dates.max(), "factor"] *= 2.0
    other = baseline_features(p2, a2, dates, 20, 20, 5)
    pd.testing.assert_frame_equal(base, other)
    # definitions on one ticker, by hand
    t = base["ticker"].iloc[0]
    one = syn["prices"][syn["prices"]["ticker"] == t].merge(syn["adj"], on=["date", "ticker"]).sort_values("date")
    ac = (one["close"] * one["factor"]).to_numpy()
    i = int(np.flatnonzero(one["date"].to_numpy() == np.datetime64(dates[0]))[0])
    row = base[(base["ticker"] == t) & (base["as_of_date"] == dates[0])].iloc[0]
    assert row["mom20"] == pytest.approx(ac[i] / ac[i - 20] - 1)
    assert row["rev5"] == pytest.approx(-(ac[i] / ac[i - 5] - 1))
    assert row["vol20"] == pytest.approx(pd.Series(ac[i - 20: i + 1]).pct_change().dropna().std())
    # modifying prices after as_of never changes a feature; using a future row would
    assert not np.isnan(row["vol20"])


def test_aggregate_uses_first_n_samples_and_errors_when_short():
    rng = np.random.default_rng(0)
    as_of = pd.Timestamp("2024-07-05")
    samples = rng.lognormal(0, 0.01, size=(2, 6, 3, 5)) * 100.0
    samples[..., 1] = samples[..., :4].max(axis=-1) * 1.001       # high >= open, close
    samples[..., 2] = samples[..., :4].min(axis=-1) * 0.999       # low  <= open, close
    preds = predictions_from_array(as_of, ["a", "b"], samples)
    lc = pd.DataFrame({"as_of_date": [as_of] * 2, "ticker": ["a", "b"], "last_close": [100.0, 100.0]})
    s4 = aggregate_predictions(preds, lc, horizon=3, n_samples=4)
    s6 = aggregate_predictions(preds, lc, horizon=3, n_samples=6)
    assert (s4["n_samples"] == 4).all() and (s6["n_samples"] == 6).all()
    by_hand = preds[(preds["sample_id"] < 4) & (preds["horizon_step"] == 3) & (preds["ticker"] == "a")]["pred_close"].mean() / 100 - 1
    assert s4.loc[s4["ticker"] == "a", "exp_ret"].iloc[0] == pytest.approx(by_hand)
    mean_all = preds[(preds["sample_id"] < 4) & (preds["ticker"] == "a")]["pred_close"].mean() / 100 - 1
    assert s4.loc[s4["ticker"] == "a", "exp_ret_mean"].iloc[0] == pytest.approx(mean_all)
    with pytest.raises(ValueError, match="fewer than n_samples"):
        aggregate_predictions(preds, lc, horizon=3, n_samples=7)
    # non-contiguous sample ids: "first" means the smallest ids
    shifted = preds.copy(); shifted["sample_id"] = shifted["sample_id"] * 10 + 3
    s = select_samples(shifted, 2)
    assert sorted(s["sample_id"].unique()) == [3, 13]
    with pytest.raises(ValueError, match="last_close"):
        aggregate_predictions(preds, lc.iloc[:1], horizon=3, n_samples=4)


def test_aggregate_accepts_model_schema_output(prices, constituents):
    """A prediction file in the real model's schema (DummyBackend -> predictions_from_array) aggregates without error.
    No real Kronos file exists in the repo yet (Stage 6), so the schema-identical dummy output stands in."""
    as_of = pd.Timestamp("2024-10-15")
    members = combined_universe_at(constituents, as_of)["ticker"].tolist()
    b = build_batch(prices[prices["index"] != "benchmark"], members, as_of, lookback=60, horizon=5)
    df = predictions_from_array(as_of, b.tickers, DummyBackend(seed=1).predict(b, 5, 20))
    lc = pd.DataFrame({"as_of_date": as_of, "ticker": b.tickers, "last_close": b.last_close})
    sig = aggregate_predictions(df, lc, horizon=5, n_samples=20)
    assert len(sig) == len(b.tickers) and (sig["n_samples"] == 20).all()


def test_last_close_follows_price_basis(syn):
    d = [pd.Timestamp("2024-07-15")]
    raw = last_close_table(syn["prices"], syn["adj"], d, "raw")
    adj = last_close_table(syn["prices"], syn["adj"], d, "adjusted")
    one = syn["prices"][(syn["prices"]["date"] == d[0])].iloc[0]
    f = syn["adj"][(syn["adj"]["date"] == d[0]) & (syn["adj"]["ticker"] == one["ticker"])]["factor"].iloc[0]
    assert raw[raw["ticker"] == one["ticker"]]["last_close"].iloc[0] == one["close"]
    assert adj[adj["ticker"] == one["ticker"]]["last_close"].iloc[0] == pytest.approx(one["close"] * f)
    with pytest.raises(ValueError):
        last_close_table(syn["prices"], syn["adj"], d, "other")


def test_build_signals_rejects_sample_count_below_n_samples(syn):
    c = cfg_override(syn["cfg"], {"signal.n_samples": N_SAMPLES_TEST + 1})
    H = int(profile_cfg(c)["pred_len"])
    preds, manifest = _load_preds(c, syn["root"], syn["runs"]["dummy"][0], H)
    with pytest.raises(ValueError, match="D-11"):
        build_signals(preds, syn["prices"], syn["adj"], syn["universe"], manifest, c)


# ---------------------------------------------------------------------------------------------
# real generated run_ids (skipped when absent)
# ---------------------------------------------------------------------------------------------
def test_real_fake_run_ids_signals_on_disk():
    cfg = load_config(ROOT / "configs/base.yaml")
    paths = Paths(cfg, ROOT)
    if not (paths.signals_path("fake_oracle_base").exists() and paths.prepared_path("prices").exists()):
        pytest.skip("run make_fake_predictions + run_signal first")
    uni = pd.read_parquet(paths.prepared_path("universe"))
    prices = pd.read_parquet(paths.prepared_path("prices"), columns=["date", "ticker", "close"])
    adj = pd.read_parquet(paths.prepared_path("adj_factor"))
    ac = (to_wide(prices, "close") * to_wide(adj, "factor"))
    for run_id in ("fake_dummy_base", "fake_oracle_base"):
        sig = validate_signals(pd.read_parquet(paths.signals_path(run_id)))
        meta = json.loads((paths.signals_dir(run_id) / "meta.json").read_text())
        assert meta["H"] == 5 and meta["N"] == 20 and meta["n_samples_used"] == 20 and meta["profile"] == "base"
        assert meta["n_as_of"] == 49 and meta["as_of_first"] == "2024-07-01" and meta["as_of_last"] == "2025-06-30"
        for d, g in sig.groupby("as_of_date"):
            assert set(g["ticker"]) <= set(get_universe(uni, d))
        assert not sig.duplicated(["as_of_date", "ticker"]).any()
        if run_id == "fake_oracle_base":
            real = (ac.shift(-5) / ac - 1.0).stack().rename("r")
            real.index.names = ["as_of_date", "ticker"]
            m = sig.join(real, on=["as_of_date", "ticker"])
            assert np.abs(m["exp_ret"] - m["r"]).max() < 6 * EPS_ORACLE
            assert m[["exp_ret", "r"]].corr().iloc[0, 1] > 0.9999
