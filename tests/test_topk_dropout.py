"""Stage 6 (docs/spec.md): TopK = Qlib TopkDropoutStrategy rules on synthetic daily signals, and its weights in engine v1."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from common.config import cfg_override
from D_strategy import registry
from D_strategy.run_strategy import run_one
from E_backtest.costs import CostModel
from E_backtest.engine_v1_weights import run_v1

K, N_DROP, HOLD_MIN = 10, 3, 5
N_TICKERS, N_DAYS = 40, 30
DATES = pd.bdate_range("2024-07-01", periods=N_DAYS)
TICKERS = [f"T{i:03d}" for i in range(N_TICKERS)]


@pytest.fixture(scope="module")
def tcfg(cfg):
    return cfg_override(cfg, {"strategies.topk": {"profile": "paper", "schedule": "daily", "signal_col": "exp_ret_mean",
                                                 "k": K, "n_drop": N_DROP, "hold_min_days": HOLD_MIN}})


def daily_signals(seed=0, constant=False):
    rng = np.random.default_rng(seed)
    base = rng.normal(0, 0.02, N_TICKERS)
    rows = []
    for d in DATES:
        s = base if constant else rng.normal(0, 0.02, N_TICKERS)
        rows.append(pd.DataFrame({"as_of_date": d, "ticker": TICKERS, "exp_ret": s, "exp_ret_mean": s, "std": 0.02, "p_up": 0.5,
                                  "pred_range": 0.03, "n_samples": 10, "last_close": 1000.0, "mom20": 0.0, "vol20": 0.02, "rev5": 0.0}))
    return pd.concat(rows, ignore_index=True)


def replay(weights: pd.DataFrame):
    """Rebuild the book from the weights file alone: per date (held set, bought, sold)."""
    held: set[str] = set()
    out = []
    for d, g in weights.groupby("as_of_date", sort=True):
        w = g.set_index("ticker")["weight"]
        bought = set(w.index[w.notna()])
        kept = set(w.index[w.isna()])
        assert kept <= held and not (bought & held)          # holds are previous holdings, buys are new names
        sold = held - kept
        held = kept | bought
        out.append((pd.Timestamp(d), set(held), bought, sold))
    return out


def test_topk_dropout_rules_on_30_days(tcfg):
    strat = registry.get_strategy("topk", tcfg)
    sig = daily_signals(seed=1)
    w, diag = run_one(strat, sig, DATES)
    book = replay(w)
    first_date, first_held, first_buy, first_sold = book[0]
    by_date = {d: g.set_index("ticker") for d, g in sig.groupby("as_of_date")}
    best = by_date[first_date]["exp_ret_mean"].sort_values(ascending=False).index[:K]
    assert first_buy == set(best) and not first_sold                                   # day 1: the best k at 1/k
    assert w[w["as_of_date"] == first_date]["weight"].eq(1 / K).all()
    bought_on: dict[str, int] = {}
    n_swaps = 0
    for i, (d, held, bought, sold) in enumerate(book):
        assert len(held) == K                                                          # (1) always k names
        assert len(bought) <= (K if i == 0 else N_DROP) and len(sold) <= N_DROP        # (2) at most n in, n out per day
        for t in sold:
            assert i - bought_on.pop(t) >= HOLD_MIN                                    # (3) never sold before hold_min_days
        for t in bought:
            bought_on[t] = i
        day = w[w["as_of_date"] == d]["weight"]
        if i > 0:
            assert day.isna().any()                                                    # (4) holds are NaN ...
            n_swaps += len(sold)
        assert day.dropna().sum() <= 1 + 1e-9 and (day.dropna() == 1 / K).all()        # ... and buys are 1/k, sum <= 1
    assert n_swaps > 0                                                                 # random signals do rotate
    w2, _ = run_one(registry.get_strategy("topk", tcfg), sig, DATES)                   # (6) same input, same output
    pd.testing.assert_frame_equal(w, w2)
    assert diag["holdings_per_date"] == {"min": K, "mean": float(K), "max": K}


def test_topk_does_not_rotate_when_signals_do_not_change(tcfg):
    w, _ = run_one(registry.get_strategy("topk", tcfg), daily_signals(seed=2, constant=True), DATES)
    book = replay(w)
    assert all(not bought and not sold for _, _, bought, sold in book[1:])              # (5) no swaps
    assert w[w["as_of_date"] > DATES[0]]["weight"].isna().all()


def test_topk_min_hold_blocks_sales_and_ties_are_stable(tcfg):
    # every day the ranking flips completely: without the holding rule n names would rotate every day
    rows = []
    for i, d in enumerate(DATES[:8]):
        s = np.arange(N_TICKERS, dtype=float) * (1 if i % 2 == 0 else -1)
        rows.append(pd.DataFrame({"as_of_date": d, "ticker": TICKERS, "exp_ret": s, "exp_ret_mean": s, "std": 0.02, "p_up": 0.5,
                                  "pred_range": 0.03, "n_samples": 10, "last_close": 1000.0, "mom20": 0.0, "vol20": 0.02, "rev5": 0.0}))
    sig = pd.concat(rows, ignore_index=True)
    book = replay(run_one(registry.get_strategy("topk", tcfg), sig, DATES[:8])[0])
    assert all(not sold for _, _, _, sold in book[1:HOLD_MIN])                         # held < hold_min_days: nothing is sold
    assert len(book[HOLD_MIN][3]) == N_DROP                                            # the first eligible day sells n
    # all scores tied -> ticker order decides, identically on every run
    tie = sig.assign(exp_ret_mean=0.0)
    a = run_one(registry.get_strategy("topk", tcfg), tie, DATES[:8])[0]
    b = run_one(registry.get_strategy("topk", tcfg), tie.sample(frac=1.0, random_state=3), DATES[:8])[0]
    pd.testing.assert_frame_equal(a, b)
    assert set(a[a["as_of_date"] == DATES[0]]["ticker"]) == set(TICKERS[:K])


def test_topk_drops_a_holding_that_lost_its_signal(tcfg):
    sig = daily_signals(seed=4, constant=True)
    first = sig[sig["as_of_date"] == DATES[0]].sort_values("exp_ret_mean", ascending=False)["ticker"].iloc[0]
    sig = sig[~((sig["ticker"] == first) & (sig["as_of_date"] >= DATES[2]))]
    book = replay(run_one(registry.get_strategy("topk", tcfg), sig, DATES[:5])[0])
    assert first in book[1][1] and first in book[2][3] and len(book[2][1]) == K        # sold on the day it vanished, replaced


def test_topk_weights_in_engine_v1_hold_rows_and_daily_trade_count(tcfg, cfg):
    sig = daily_signals(seed=5)
    w, _ = run_one(registry.get_strategy("topk", tcfg), sig, DATES)
    cal = pd.bdate_range("2024-07-01", periods=N_DAYS + 3)
    rng = np.random.default_rng(6)
    px = 1000 * np.exp(np.cumsum(rng.normal(0, 0.01, (len(cal), N_TICKERS)), axis=0))
    prices = pd.DataFrame({"date": np.repeat(cal, N_TICKERS), "ticker": TICKERS * len(cal), "market": "KOSPI",
                           "open": px.ravel(), "close": (px * np.exp(rng.normal(0, 0.005, px.shape))).ravel(), "factor": 1.0})
    c = cfg_override(cfg, {"period.start": str(cal[0].date()), "period.end": str(cal[-1].date()), "backtest.delist_policy": "last_close"})
    out = run_v1(w, prices, cal, CostModel(c, "none"), c)
    trades = out["trades"]
    hold_rows = int(w["weight"].isna().sum())
    assert int((trades["status"] == "hold").sum()) == hold_rows                        # one hold row per held (NaN) target
    assert (trades.loc[trades["status"] == "hold", "trade_w"].abs() < 1e-12).all()     # a hold never trades
    traded = trades[trades["trade_w"].abs() > 1e-12]
    per_day = traded.groupby("fill_date")["ticker"].nunique()
    assert per_day.iloc[0] == K and (per_day.iloc[1:] <= 2 * N_DROP).all()             # after day 1: at most n sells + n buys
