"""Stage 7 (docs/spec.md): engine v2 (orders, cash + shares) and its six constraints.

Toy examples are built in-test with the expected numbers written out. The v1 consistency test uses the real
fake_dummy_base weights (skipped when they are not generated)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from common.config import ConfigError, cfg_override, load_config
from common.paths import Paths
from E_backtest import constraints as C
from E_backtest.costs import CostModel
from E_backtest.engine_v1_weights import run_v1
from E_backtest.engine_v2_orders import run_v2
from E_backtest.run_backtest import configured_constraints, load_prices, shortfall_scenarios

ROOT = Paths(load_config("configs/base.yaml"), ".").root
CASH0 = 1_000_000.0
TICKS = [{"min_price": 0, "tick": 1}, {"min_price": 1000, "tick": 5}]          # test tick table
CAL = pd.bdate_range("2024-07-01", periods=6)          # d0 .. d5
#            d0     d1     d2     d3     d4     d5
OPEN = {"A": [100.0, 102.0, 104.0, 101.0, 103.0, 105.0],
        "B": [50.0, 49.0, 52.0, 53.0, 51.0, 50.0],
        "C": [200.0, 198.0, 202.0, 205.0, 210.0, 208.0]}
CLOSE = {"A": [101.0, 103.0, 102.0, 102.0, 104.0, 106.0],
         "B": [50.0, 50.0, 53.0, 52.0, 51.0, 49.0],
         "C": [199.0, 200.0, 204.0, 206.0, 209.0, 207.0]}


def toy_prices(open_=OPEN, close=CLOSE, factor=None, value=None):
    rows = []
    for t in open_:
        for i, d in enumerate(CAL):
            rows.append({"date": d, "ticker": t, "market": "KOSPI", "open": open_[t][i], "close": close[t][i],
                         "value": 1e12 if value is None else value[t][i], "factor": 1.0 if factor is None else factor[t][i]})
    return pd.DataFrame(rows)


def toy_weights(spec: dict):
    return pd.DataFrame([(pd.Timestamp(d), t, w) for d, ws in spec.items() for t, w in ws.items()], columns=["as_of_date", "ticker", "weight"])


def toy_halts(spec: dict | None = None):
    rows = [{"date": d, "ticker": t, "is_halted": bool(spec and (t, i) in spec)} for t in OPEN for i, d in enumerate(CAL)]
    return pd.DataFrame(rows)


class Cost:
    """Test cost rates split the way engine v2 books them."""
    scenario = "test"

    def __init__(self, buy=0.0, sell=0.0, slip=0.0, tax=0.0):
        self.buy, self.sell, self.slip, self.tax = buy, sell, slip, tax

    def components(self, date, markets):
        return {"commission_buy": self.buy, "commission_sell": self.sell, "slippage": self.slip, "tax": np.full(len(markets), self.tax)}

    def rates(self, date, markets):
        n = len(markets)
        return np.full(n, self.buy + self.slip), np.full(n, self.sell + self.slip + self.tax)


def _cfg(**over):
    return cfg_override(load_config(ROOT / "configs/base.yaml"), {
        "backtest.delist_policy": "last_close", "backtest.init_cash": CASH0, "period.end": "2024-07-08",
        "backtest.v2.price_limit_pct": 0.3, "backtest.v2.tick_table": TICKS, "backtest.v2.max_participation": 0.1, **over})


def _pos(out, date, ticker):
    p = out["positions"]
    row = p[(p["date"] == pd.Timestamp(date)) & (p["ticker"] == ticker)]
    return float(row["shares"].iloc[0]) if len(row) else 0.0


# ---------------------------------------------------------------------------------------------
# constraints.py
# ---------------------------------------------------------------------------------------------
def test_constraint_functions():
    assert C.scenario_name([]) == "all_off" and C.scenario_name(["cash", "costs"]) == "costs+cash"
    assert C.parse_scenario("all_off") == [] and C.parse_scenario("integer_shares+costs") == ["costs", "integer_shares"]
    with pytest.raises(ValueError):
        C.scenario_name(["nope"])
    # base 100 -> 130 / 70 on a 1-won tick. base 990 -> 1287 / 693: the upper limit is in the 5-won band -> 1285 (down), lower 693
    up, lo = C.limit_prices(np.array([100.0, 990.0, 1001.0]), 0.3, TICKS)
    assert list(up) == [130.0, 1285.0, 1300.0] and list(lo) == [70.0, 693.0, 701.0]      # 1001 * 0.7 = 700.7 -> up to 701
    with pytest.raises(ConfigError):
        C.tick_size(np.array([100.0]), [])
    assert list(C.base_price(np.array([1000.0, 500.0]), np.array([5.0, 1.0]))) == [200.0, 500.0]          # 1:5 split day
    assert list(C.floor_shares(np.array([3.9999999999, 4.2, 0.99]))) == [4.0, 4.0, 0.0]
    assert list(C.liquidity_cap(np.array([100.0, 100.0]), np.array([50.0, 50.0]), np.array([10_000.0, 1e9]), 0.1)) == [20.0, 100.0]
    assert C.cash_scale(np.array([600.0, 400.0]), 500.0) == 0.5 and C.cash_scale(np.array([10.0]), 500.0) == 1.0


def test_shortfall_scenarios_are_cumulative():
    cfg = load_config(ROOT / "configs/base.yaml")
    names = [C.scenario_name(s) for s in shortfall_scenarios(cfg)]
    assert names == ["all_off", "costs", "costs+integer_shares", "costs+integer_shares+cash", "costs+integer_shares+cash+price_limit",
                     "costs+integer_shares+cash+price_limit+halt", "costs+integer_shares+cash+price_limit+halt+liquidity"]
    assert configured_constraints(cfg) == list(C.CONSTRAINTS)


# ---------------------------------------------------------------------------------------------
# toy example: integer shares + cash, hand calculation
# ---------------------------------------------------------------------------------------------
def test_toy_integer_shares_and_cash_match_hand_calculation():
    """Signal d0: A 50%, B 30%, C 20%. Signal d3: A 25%, C 75% (B sold). Buy commission 1%, sell commission 2%. Cash 1,000,000.

    d1 fill, open NAV 1,000,000, opens A 102, B 49, C 198:
        targets  A floor(500000 / 102) = 4901, B floor(300000 / 49) = 6122, C floor(200000 / 198) = 1010
        cash needed = (499902 + 299978 + 199980) * 1.01 = 999860 * 1.01 = 1009858.6 > 1,000,000
        -> k = 1000000 / 1009858.6 = 0.990237..., shares floor(4901k) = 4853, floor(6122k) = 6062, floor(1010k) = 1000   (scaled_cash)
        value = 495006 + 297038 + 198000 = 990044, commission 9900.44, cash = 1000000 - 990044 - 9900.44 = 55.56
        close NAV = 4853 * 103 + 6062 * 50 + 1000 * 200 + 55.56 = 499859 + 303100 + 200000 + 55.56 = 1003014.56
    d4 fill (signal d3), opens A 103, B 51, C 210:
        open NAV = 4853 * 103 + 6062 * 51 + 1000 * 210 + 55.56 = 499859 + 309162 + 210000 + 55.56 = 1019076.56
        targets  A floor(0.25 * 1019076.56 / 103) = floor(2473.48) = 2473, C floor(0.75 * 1019076.56 / 210) = floor(3639.55) = 3639, B 0
        sells    A 4853 - 2473 = 2380 -> 245140, B 6062 -> 309162; proceeds 554302, commission 2% = 11086.04
                 cash = 55.56 + 554302 - 11086.04 = 543271.52
        buy      C 3639 - 1000 = 2639 -> 554190 * 1.01 = 559731.9 > 543271.52
                 -> k = 543271.52 / 559731.9 = 0.970592..., floor(2639k) = 2561 (scaled_cash); value 537810, commission 5378.10
                 cash = 543271.52 - 537810 - 5378.10 = 83.42
        close NAV = 2473 * 104 + 3561 * 209 + 83.42 = 257192 + 744249 + 83.42 = 1001524.42
    """
    w = toy_weights({CAL[0]: {"A": 0.5, "B": 0.3, "C": 0.2}, CAL[3]: {"A": 0.25, "C": 0.75}})
    out = run_v2(w, toy_prices(), toy_halts(), CAL, Cost(buy=0.01, sell=0.02), _cfg(), ["costs", "integer_shares", "cash"])
    d = out["daily"].set_index("date")
    assert [_pos(out, CAL[1], t) for t in "ABC"] == [4853.0, 6062.0, 1000.0]
    assert d.loc[CAL[1], "cash"] == pytest.approx(55.56, abs=1e-6) and d.loc[CAL[1], "nav_krw"] == pytest.approx(1003014.56, abs=1e-6)
    assert d.loc[CAL[1], "cost"] == pytest.approx(9900.44 / CASH0)
    assert [_pos(out, CAL[4], t) for t in "ABC"] == [2473.0, 0.0, 3561.0]
    assert d.loc[CAL[4], "cash"] == pytest.approx(83.42, abs=1e-6) and d.loc[CAL[4], "nav_krw"] == pytest.approx(1001524.42, abs=1e-6)
    assert d.loc[CAL[4], "cost"] == pytest.approx((11086.04 + 5378.10) / CASH0)
    assert out["nav"]["nav"].iloc[-1] == pytest.approx((2473 * 106 + 3561 * 207 + 83.42) / CASH0)
    tr = out["trades"]
    assert set(tr.loc[tr["side"] == "buy", "status"]) == {"scaled_cash"} and set(tr.loc[tr["side"] == "sell", "status"]) == {"filled"}
    c_buy = tr[(tr["date"] == CAL[4]) & (tr["ticker"] == "C")].iloc[0]
    assert c_buy["target_shares"] == 3639 and c_buy["filled_shares"] == 2561 and c_buy["value"] == 537810 and c_buy["commission"] == pytest.approx(5378.10)
    assert (pd.to_datetime(tr["signal_date"]) < pd.to_datetime(tr["date"])).all()                # lookahead


def test_missing_user_values_stop_with_a_clear_error():
    w = toy_weights({CAL[0]: {"A": 1.0}})
    with pytest.raises(ConfigError, match="init_cash"):
        run_v2(w, toy_prices(), toy_halts(), CAL, Cost(), cfg_override(_cfg(), {"backtest": {"delist_policy": "last_close", "v2": {}}}), [])
    base = cfg_override(load_config(ROOT / "configs/base.yaml"), {"backtest.init_cash": CASH0, "period.end": "2024-07-08"})
    for cons, key in ((["price_limit"], "price_limit_pct"), (["liquidity"], "max_participation")):
        if base["backtest"]["v2"].get(key) is None:
            with pytest.raises(ConfigError, match=key):
                run_v2(w, toy_prices(), toy_halts(), CAL, Cost(), base, cons)


# ---------------------------------------------------------------------------------------------
# corporate action, price limit, halt, liquidity
# ---------------------------------------------------------------------------------------------
def test_split_keeps_the_position_value():
    """A splits 1:5 on d3: raw price 100 -> 20, factor 1 -> 5. The adjusted price does not move, so NAV must not."""
    op = {"A": [100.0, 100.0, 100.0, 20.0, 20.0, 20.0]}
    fac = {"A": [1.0, 1.0, 1.0, 5.0, 5.0, 5.0]}
    w = toy_weights({CAL[0]: {"A": 1.0}})
    for cons in ([], ["integer_shares", "cash"]):
        out = run_v2(w, toy_prices(op, op, fac), None, CAL, Cost(), _cfg(), cons)
        assert _pos(out, CAL[2], "A") == 10000 and _pos(out, CAL[3], "A") == 50000
        assert out["nav"]["nav"].to_numpy() == pytest.approx(np.ones(6)) and len(out["trades"]) == 1
    # reverse split 5:1 on an odd holding with integer shares: the fraction is paid in cash, value unchanged
    op2 = {"A": [30.0, 30.0, 30.0, 150.0, 150.0, 150.0]}
    fac2 = {"A": [1.0, 1.0, 1.0, 0.2, 0.2, 0.2]}
    out = run_v2(w, toy_prices(op2, op2, fac2), None, CAL, Cost(), _cfg(), ["integer_shares", "cash"])
    assert _pos(out, CAL[2], "A") == 33333 and _pos(out, CAL[3], "A") == 6666                    # 33333 / 5 = 6666.6
    assert out["daily"].set_index("date").loc[CAL[3], "cash"] == pytest.approx(10.0 + 0.6 * 150.0)
    assert out["nav"]["nav"].to_numpy() == pytest.approx(np.ones(6))


def test_price_limit_rejects_a_buy_at_the_upper_limit_and_leaves_cash():
    """B closes 50 on d0 and opens d1 at 65 = 50 * 1.3 (upper limit): the buy of B is rejected, its 30% stays in cash."""
    op = {**OPEN, "B": [50.0, 65.0, 65.0, 65.0, 65.0, 65.0]}
    cl = {**CLOSE, "B": [50.0, 65.0, 65.0, 65.0, 65.0, 65.0]}
    w = toy_weights({CAL[0]: {"A": 0.5, "B": 0.3, "C": 0.2}})
    out = run_v2(w, toy_prices(op, cl), toy_halts(), CAL, Cost(), _cfg(), ["price_limit"])
    tr = out["trades"].set_index("ticker")
    assert tr.loc["B", "status"] == "rejected_limit" and tr.loc["B", "filled_shares"] == 0 and tr.loc["A", "status"] == "filled"
    assert _pos(out, CAL[1], "B") == 0 and out["daily"].set_index("date").loc[CAL[1], "cash"] == pytest.approx(0.3 * CASH0)
    off = run_v2(w, toy_prices(op, cl), toy_halts(), CAL, Cost(), _cfg(), [])
    assert _pos(off, CAL[1], "B") == pytest.approx(0.3 * CASH0 / 65.0)                            # without the constraint it fills
    # a sell at the lower limit is rejected too: B falls to 35 = 50 * 0.7 on the day it should be sold
    op3 = {**OPEN, "B": [50.0, 50.0, 50.0, 50.0, 35.0, 35.0]}
    cl3 = {**CLOSE, "B": [50.0, 50.0, 50.0, 50.0, 35.0, 35.0]}
    w3 = toy_weights({CAL[0]: {"A": 0.5, "B": 0.5}, CAL[3]: {"A": 1.0}})
    out3 = run_v2(w3, toy_prices(op3, cl3), toy_halts(), CAL, Cost(), _cfg(), ["price_limit"])
    t3 = out3["trades"]
    assert t3[(t3["date"] == CAL[4]) & (t3["ticker"] == "B")]["status"].iloc[0] == "rejected_limit" and _pos(out3, CAL[4], "B") == 10000


def test_halted_ticker_order_is_rejected_and_valued_at_the_previous_price():
    """B is halted on d4 (no open, the close repeats 52): the sell of B is rejected and B stays in the book at 52."""
    op = {**OPEN, "B": [50.0, 49.0, 52.0, 53.0, np.nan, 50.0]}
    cl = {**CLOSE, "B": [50.0, 50.0, 53.0, 52.0, 52.0, 49.0]}
    w = toy_weights({CAL[0]: {"A": 0.5, "B": 0.5}, CAL[3]: {"A": 1.0}})
    out = run_v2(w, toy_prices(op, cl), toy_halts({("B", 4)}), CAL, Cost(), _cfg(), ["halt"])
    tr = out["trades"]
    b = tr[(tr["date"] == CAL[4]) & (tr["ticker"] == "B")].iloc[0]
    shares_b = 0.5 * CASH0 / 49.0
    assert b["status"] == "rejected_halt" and b["filled_shares"] == 0 and _pos(out, CAL[4], "B") == pytest.approx(shares_b)
    p = out["positions"]
    assert float(p[(p["date"] == CAL[4]) & (p["ticker"] == "B")]["close"].iloc[0]) == 52.0
    assert _pos(out, CAL[5], "B") == pytest.approx(shares_b)                                      # not resubmitted on d5
    # halt off: same outcome through the no-price rule, as v1
    off = run_v2(w, toy_prices(op, cl), toy_halts({("B", 4)}), CAL, Cost(), _cfg(), [])
    assert off["trades"][(off["trades"]["date"] == CAL[4]) & (off["trades"]["ticker"] == "B")]["status"].iloc[0] == "no_price"
    assert off["nav"]["nav"].to_numpy() == pytest.approx(out["nav"]["nav"].to_numpy())


def test_liquidity_caps_the_order_at_the_participation_rate():
    """C trades 500,000 won on d1; 10% participation at open 198 allows 50000 / 198 = 252.5 -> 252 whole shares."""
    val = {t: [1e12] * 6 for t in OPEN}
    val["C"] = [1e12, 500_000.0, 1e12, 1e12, 1e12, 1e12]
    w = toy_weights({CAL[0]: {"A": 0.5, "B": 0.3, "C": 0.2}})
    out = run_v2(w, toy_prices(value=val), toy_halts(), CAL, Cost(), _cfg(), ["integer_shares", "liquidity"])
    c = out["trades"].set_index("ticker").loc["C"]
    assert c["status"] == "partial_liquidity" and c["target_shares"] == 1010 and c["filled_shares"] == 252


# ---------------------------------------------------------------------------------------------
# invariants on a random book, and agreement with v1
# ---------------------------------------------------------------------------------------------
def test_invariants_with_cash_and_integer_constraints():
    rng = np.random.default_rng(0)
    n, cal = 30, pd.bdate_range("2024-07-01", periods=40)
    tick = [f"T{i:02d}" for i in range(n)]
    px = 5000 * np.exp(np.cumsum(rng.normal(0, 0.02, (len(cal), n)), axis=0))
    prices = pd.DataFrame({"date": np.repeat(cal, n), "ticker": tick * len(cal), "market": "KOSPI", "open": px.ravel(),
                           "close": (px * np.exp(rng.normal(0, 0.01, px.shape))).ravel(), "value": rng.uniform(1e5, 5e6, px.size), "factor": 1.0})
    rows = []
    for d in cal[:-1:3]:
        picks = rng.choice(tick, 10, replace=False)
        wts = rng.dirichlet(np.ones(10))
        rows += [(d, t, x) for t, x in zip(picks, wts)]
    w = pd.DataFrame(rows, columns=["as_of_date", "ticker", "weight"])
    cfg = _cfg(**{"period.end": str(cal[-1].date())})
    out = run_v2(w, prices, None, cal, Cost(buy=0.001, sell=0.003, slip=0.0005, tax=0.0018), cfg, list(C.CONSTRAINTS))
    assert (out["daily"]["cash"] >= 0).all()                                                     # cash constraint
    sh = out["positions"]["shares"].to_numpy()
    assert (sh > 0).all() and np.allclose(sh, np.round(sh))                                      # whole, non-negative shares
    tr = out["trades"]
    assert (tr["filled_shares"] >= 0).all() and np.allclose(tr["filled_shares"], np.round(tr["filled_shares"]))
    assert (pd.to_datetime(tr["signal_date"]) < pd.to_datetime(tr["date"])).all()
    assert "partial_liquidity" in set(tr["status"]) and (tr.loc[tr["status"] == "partial_liquidity", "filled_shares"] < tr.loc[tr["status"] == "partial_liquidity", "target_shares"].abs() + 1e9).all()
    pos_value = out["positions"].groupby("date")["value"].sum()
    d = out["daily"].set_index("date")
    assert (pos_value + d.loc[pos_value.index, "cash"]).to_numpy() == pytest.approx(d.loc[pos_value.index, "nav_krw"].to_numpy())
    costs = tr["commission"].sum() + tr["tax"].sum() + tr["slippage"].sum()
    assert costs == pytest.approx(out["nav"]["cost"].sum() * CASH0)


def test_all_off_v2_matches_v1_on_the_toy_book():
    w = toy_weights({CAL[0]: {"A": 0.5, "B": 0.5}, CAL[2]: {"A": np.nan, "C": 0.5}, CAL[3]: {"A": 0.25, "C": 0.75}})      # includes a hold
    cfg = _cfg()
    a = run_v1(w, toy_prices(), CAL, CostModel(cfg, "none"), cfg)
    b = run_v2(w, toy_prices(), toy_halts(), CAL, CostModel(cfg, "none"), cfg, [])
    assert b["nav"]["ret"].to_numpy() == pytest.approx(a["nav"]["ret"].to_numpy(), abs=1e-12)


@pytest.mark.parametrize("strategy", ["equal_weight", "momentum20_topk", "random_topk"])
def test_all_off_v2_matches_v1_on_fake_dummy(strategy):
    cfg = cfg_override(load_config(ROOT / "configs/base.yaml"), {"backtest.delist_policy": "last_close", "backtest.init_cash": 1e8})
    paths = Paths(cfg, ROOT)
    wpath = paths.weights_path("fake_dummy_base", strategy)
    if not wpath.exists():
        pytest.skip("run D_strategy.run_strategy --run-id fake_dummy_base --strategy all first")
    prices, cal = load_prices(paths)
    halts = pd.read_parquet(paths.prepared_path("halts"))
    w = pd.read_parquet(wpath)
    a = run_v1(w, prices, cal, CostModel(cfg, "none"), cfg)
    b = run_v2(w, prices, halts, cal, CostModel(cfg, "none"), cfg, [])
    assert np.abs(a["nav"]["ret"].to_numpy() - b["nav"]["ret"].to_numpy()).max() < 1e-8          # daily returns agree
    assert b["nav"]["nav"].iloc[-1] == pytest.approx(a["nav"]["nav"].iloc[-1], rel=1e-8)
    assert len(a["delisted"]) == len(b["delisted"])
