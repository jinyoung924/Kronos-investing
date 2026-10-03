"""Stage 1 (docs/spec.md): A_data_prepare builders on the REAL collected data (data/raw, data/universe).

The builders are pure, so the tests rebuild every table in memory from data/raw and never depend on a prior
`run_prepare`. Skipped when the collected data is absent (run `python -m A_data_prepare.manifest --verify`).
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from A_data_prepare.build_adj_factor import adjusted_close, build_adj_factor, build_events, unexplained_moves
from A_data_prepare.build_calendar import build_calendar, build_halts, halt_flag
from A_data_prepare.build_prices import build_prices
from A_data_prepare.build_universe import build_universe, members_per_date
from common.calendar import as_calendar, next_trading_day, prev_trading_day, rebalance_dates, shift_trading_days
from common.config import load_config
from common.data import rebalance_dates as data_rebalance_dates
from common.paths import Paths
from common.schema import validate_prices
from common.universe import get_universe, universe_at

PRICE_LIMIT = 0.30          # KRX daily price limit (test constant; backtest.v2.price_limit_pct is a user value)
BIG_MOVE = 0.35             # same threshold as docs/data_pipeline.md §5-5
DELIST_WINDOW = 14
ROOT = Paths(load_config("configs/base.yaml"), ".").root


@pytest.fixture(scope="module")
def real():
    cfg = load_config(ROOT / "configs/base.yaml")
    paths = Paths(cfg, ROOT)
    indices = cfg["universe"]["indices"]
    if not all(paths.price_file(i).exists() for i in indices):
        pytest.skip("collected data (data/raw) not present")
    raw = {i: pd.read_parquet(paths.price_file(i)) for i in indices}
    events_raw = pd.concat([pd.read_csv(paths.price_file(i).parent / "adjustment_events.csv", dtype={"ticker": str}) for i in indices])
    cons = {i: pd.read_parquet(paths.constituents_file(i, "base")) for i in indices}
    prices = build_prices(raw, markets=cfg["data"]["markets"])
    raw_all = pd.concat(raw.values(), ignore_index=True)
    raw_all["date"] = pd.to_datetime(raw_all["date"]).dt.normalize()
    raw_all = raw_all.sort_values(["ticker", "date"]).reset_index(drop=True)
    events = build_events(events_raw)
    adj = build_adj_factor(prices, events)
    halts = build_halts(prices, raw_halted=raw_all["halted"])
    uni = build_universe(cons, prices)
    return {"cfg": cfg, "paths": paths, "raw": raw_all, "prices": prices, "events": events, "adj": adj,
            "halts": halts, "universe": uni, "cons": cons, "calendar": as_calendar(build_calendar(prices))}


# ---------------------------------------------------------------------------------------------
# prices / halts
# ---------------------------------------------------------------------------------------------
def test_prices_unique_nonnegative_and_market_labels(real):
    p = real["prices"]
    validate_prices(p)                                   # (date, ticker) unique, no negative prices, OHLC order
    assert not p.duplicated(["date", "ticker"]).any()
    assert (p[["open", "high", "low", "close", "volume", "value"]].min() >= 0).all()
    assert set(p["market"]) == set(real["cfg"]["data"]["markets"])
    assert p["close"].notna().all()
    # halted days: no open/high/low, close kept; traded days: full OHLC
    h = real["halts"].set_index(["ticker", "date"])["is_halted"].reindex(pd.MultiIndex.from_frame(p[["ticker", "date"]])).to_numpy()
    assert p.loc[h, ["open", "high", "low"]].isna().all().all()
    assert p.loc[~h, ["open", "high", "low"]].notna().all().all()
    # a transferred ticker carries the exchange it traded on that day, never two rows on one day
    both = p.groupby("ticker")["market"].nunique()
    assert (both > 1).sum() >= 1


def test_halts_match_collected_flag_and_probe_rule(real):
    raw, halts = real["raw"], real["halts"]
    assert len(halts) == len(raw)
    assert np.array_equal(halts["is_halted"].to_numpy(), raw["halted"].to_numpy())
    # probe rule: a halted day has volume 0 or no open; recomputing from the prepared prices gives the same flag
    assert np.array_equal(halt_flag(real["prices"]).to_numpy(), halts["is_halted"].to_numpy())
    with pytest.raises(ValueError):
        build_halts(real["prices"], raw_halted=~raw["halted"])


# ---------------------------------------------------------------------------------------------
# adjustment factor
# ---------------------------------------------------------------------------------------------
def test_forward_factor_starts_at_one_and_matches_backward_factor_ratios(real):
    adj, raw = real["adj"], real["raw"]
    first = adj.groupby("ticker")["factor"].first()
    assert np.allclose(first.to_numpy(), 1.0)
    # F_t / F_s == adj_factor_t / adj_factor_s for consecutive rows of every ticker (same (ticker, date) order)
    assert adj[["ticker", "date"]].equals(raw[["ticker", "date"]])
    g = adj["ticker"].to_numpy()
    same = g[1:] == g[:-1]
    f_ratio = (adj["factor"].to_numpy()[1:] / adj["factor"].to_numpy()[:-1])[same]
    b_ratio = (raw["adj_factor"].to_numpy()[1:] / raw["adj_factor"].to_numpy()[:-1])[same]
    assert np.allclose(f_ratio, b_ratio, rtol=1e-9, atol=0)
    # F changes only on applied ex-dates
    changes = adj[(adj["factor"].to_numpy() != np.r_[np.nan, adj["factor"].to_numpy()[:-1]]) & same.tolist() + [False]][["ticker", "date"]] if False else None
    ev = real["events"]
    chg = adj.assign(prev=adj.groupby("ticker")["factor"].shift(1))
    chg = chg[chg["prev"].notna() & ~np.isclose(chg["factor"], chg["prev"])]
    key_chg = set(zip(chg["ticker"], chg["date"]))
    key_ev = set(zip(ev.loc[ev["applied"], "ticker"], ev.loc[ev["applied"], "ex_date"]))
    assert key_chg == key_ev


def test_split_sample_adjusted_return_within_price_limit_on_ex_date(real):
    p, adj, ev, halts = real["prices"], real["adj"], real["events"], real["halts"]
    applied = ev[ev["applied"]]
    assert len(applied) > 600
    big_splits = applied[applied["ratio"] <= 0.2]           # 5:1 and larger splits (e.g. 10:1 of BYC, 남양유업, 영풍)
    assert len(big_splits) >= 5
    px = p[["date", "ticker", "close"]].sort_values(["ticker", "date"]).reset_index(drop=True)
    px["adj_close"] = adjusted_close(px, adj)
    px["ret"] = px.groupby("ticker")["adj_close"].pct_change()
    px["raw_ret"] = px.groupby("ticker")["close"].pct_change()
    key = px.set_index(["ticker", "date"])
    rows = key.loc[list(zip(applied["ticker"], applied["ex_date"]))]
    # on the ex-date the ADJUSTED return is inside the daily limit for every applied event ...
    assert rows["ret"].abs().max() <= PRICE_LIMIT + 1e-9, rows[rows["ret"].abs() > PRICE_LIMIT]
    # ... while the RAW return of the big splits is far outside it (that is what the factor fixes)
    raw_big = key.loc[list(zip(big_splits["ticker"], big_splits["ex_date"])), "raw_ret"]
    assert (raw_big.abs() > PRICE_LIMIT).all()


def test_forward_factor_is_invariant_to_future_data(real):
    p, ev = real["prices"], real["events"]
    cut = pd.Timestamp("2024-06-28")
    base = build_adj_factor(p, ev)
    # scramble everything after the cut: drop later events, add a fake 2:1 split and halve later prices
    ev2 = ev[ev["ex_date"] <= cut].copy()
    t = ev["ticker"].iloc[0]
    later = p[(p["ticker"] == t) & (p["date"] > cut)]["date"]
    fake = pd.DataFrame([{"ticker": t, "ex_date": later.iloc[3], "ratio": 0.5, "r": 2.0, "applied": True,
                          "source": "fake", "name": "", "prev_close": np.nan, "base_price": np.nan}])
    ev2 = pd.concat([ev2, fake], ignore_index=True)
    p2 = p.copy()
    m = p2["date"] > cut
    p2.loc[m, ["open", "high", "low", "close"]] *= 0.5
    other = build_adj_factor(p2, ev2)
    a = base[base["date"] <= cut].reset_index(drop=True)
    b = other[other["date"] <= cut].reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b)
    assert not np.allclose(base.loc[base["ticker"] == t, "factor"].to_numpy()[-1], other.loc[other["ticker"] == t, "factor"].to_numpy()[-1])


def test_unexplained_moves_are_all_liquidation_trading(real):
    moves = unexplained_moves(real["prices"], real["adj"], real["halts"], max_move=BIG_MOVE, delist_window=DELIST_WINDOW)
    assert len(moves) > 0
    assert set(moves["category"]) == {"liquidation"}, moves[moves["category"] != "liquidation"]
    assert (moves["last_date"] < real["prices"]["date"].max()).all()
    # the listing excludes new-listing days and halt-resumption days by construction
    assert moves["prev_date"].notna().all()


# ---------------------------------------------------------------------------------------------
# universe
# ---------------------------------------------------------------------------------------------
def test_universe_is_point_in_time_and_keeps_delisted_names_until_their_last_membership(real):
    uni, cons, p = real["universe"], real["cons"], real["prices"]
    pooled = pd.concat(cons.values()).drop_duplicates(["date", "ticker"])
    assert len(uni) == len(pooled)
    for d in (pd.Timestamp("2024-07-01"), pd.Timestamp("2024-07-03"), pd.Timestamp("2025-06-30")):
        snap = uni.loc[uni["date"] <= d, "date"].max()
        expect = sorted(uni.loc[uni["date"] == snap, "ticker"])
        assert get_universe(uni, d) == expect == universe_at(uni, d)
        assert set(get_universe(uni, d, market="KOSPI")) == set(uni.loc[(uni["date"] == snap) & (uni["market"] == "KOSPI"), "ticker"])
    assert get_universe(uni, "2000-01-01") == []
    # market agrees with the price bar of that day
    px = p.set_index(["date", "ticker"])["market"]
    assert (uni["market"].to_numpy() == px.reindex(pd.MultiIndex.from_frame(uni[["date", "ticker"]])).to_numpy()).all()
    # delisted former members: present in every snapshot up to their last membership, absent afterwards, never
    # removed retroactively
    for idx in cons:
        v = json.loads((real["paths"].price_file(idx).parent / "validation.json").read_text())
        for t, last_px in v["delisted"]["tickers_during_run"].items():
            mem = uni[uni["ticker"] == t]
            if mem.empty:
                continue
            assert mem["date"].max() <= pd.Timestamp(last_px)
            assert t in get_universe(uni, mem["date"].max())
            assert t not in get_universe(uni, pd.Timestamp(last_px) + pd.Timedelta(days=30))


def test_universe_top_n_mktcap(real):
    uni_all = real["universe"]
    uni200 = build_universe(real["cons"], real["prices"], top_n_mktcap=200)
    sizes = uni200.groupby("date").size()
    assert (sizes == 200).all()
    assert set(map(tuple, uni200[["date", "ticker"]].to_numpy())) <= set(map(tuple, uni_all[["date", "ticker"]].to_numpy()))
    d = uni200["date"].iloc[0]
    p = real["prices"].set_index(["date", "ticker"])
    cap = {t: p.loc[(d, t), "close"] * p.loc[(d, t), "listed_shares"] for t in uni_all.loc[uni_all["date"] == d, "ticker"]}
    top = sorted(cap, key=cap.get, reverse=True)[:200]
    assert set(top) == set(uni200.loc[uni200["date"] == d, "ticker"])
    m = members_per_date(uni_all)
    assert {"date", "n_members", "n_kospi", "n_kosdaq"} <= set(m.columns)
    assert (m["n_members"] == m["n_kospi"] + m["n_kosdaq"]).all()


# ---------------------------------------------------------------------------------------------
# calendar
# ---------------------------------------------------------------------------------------------
def test_calendar_arithmetic_and_rebalance_dates(real):
    cal = real["calendar"]
    assert cal.is_monotonic_increasing and cal.is_unique and len(cal) == 680
    assert next_trading_day(cal, "2024-07-05") == pd.Timestamp("2024-07-08")      # Friday -> Monday
    assert next_trading_day(cal, "2024-07-06") == pd.Timestamp("2024-07-08")      # Saturday -> Monday
    assert prev_trading_day(cal, "2024-07-08") == pd.Timestamp("2024-07-05")
    assert shift_trading_days(cal, "2024-07-01", 5) == pd.Timestamp("2024-07-08")
    assert shift_trading_days(cal, "2024-07-08", -5) == pd.Timestamp("2024-07-01")
    with pytest.raises(ValueError):
        shift_trading_days(cal, "2024-07-06", 1)          # not a trading day
    with pytest.raises(ValueError):
        next_trading_day(cal, cal[-1])
    rd = rebalance_dates(cal, "2024-07-01", "2025-06-30", 5)
    assert rd[0] == pd.Timestamp("2024-07-01") and len(rd) == 49 and rd[-1] <= pd.Timestamp("2025-06-30")
    assert rebalance_dates is data_rebalance_dates                 # B_model_infer uses the same function
    assert len(rebalance_dates(cal, "2024-07-01", "2025-06-30", 1)) == int(((cal >= "2024-07-01") & (cal <= "2025-06-30")).sum())


# ---------------------------------------------------------------------------------------------
# on-disk outputs (only when run_prepare has been run)
# ---------------------------------------------------------------------------------------------
def test_prepared_files_on_disk_match_in_memory_build(real):
    paths = real["paths"]
    if not paths.prepared_path("prices").exists():
        pytest.skip("data/A_prepared not built yet (python -m A_data_prepare.run_prepare)")
    for name, df in (("prices", real["prices"]), ("adj_factor", real["adj"]), ("halts", real["halts"]), ("universe", real["universe"])):
        disk = pd.read_parquet(paths.prepared_path(name))
        pd.testing.assert_frame_equal(disk.reset_index(drop=True), df.reset_index(drop=True), check_dtype=False)
    meta = json.loads((paths.prepared_dir() / "meta.json").read_text())
    assert meta["stage"] == "A_data_prepare" and meta["universe_variant"] == "base"
    assert {"prices_kospi", "events_kospi", "constituents_kosdaq", "benchmark"} <= set(meta["inputs"])
    assert all(v["sha256"] for v in meta["inputs"].values())
    bench = pd.read_parquet(paths.prepared_path("benchmark"))
    assert set(bench["kind"]) == {"index", "etf"} and set(bench["ticker"]) >= {"KOSPI", "KOSDAQ"}
    assert as_calendar(bench["date"]).equals(real["calendar"])                 # shared calendar (sanity gate 3)
