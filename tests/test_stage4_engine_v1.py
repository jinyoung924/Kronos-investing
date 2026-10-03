"""Stage 4 (docs/spec.md): engine v1 (weight space) and the cost model.

Toy examples are built in-test with hand-computed expectations written out step by step. The oracle and
random tests use the real fake run_ids (skipped when they are not generated)."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from common.config import ConfigError, cfg_override, load_config
from common.lookahead import LookaheadError, assert_fill_after_signal
from common.paths import Paths
from E_backtest.costs import CostModel
from E_backtest.engine_v1_weights import run_v1

ROOT = Paths(load_config("configs/base.yaml"), ".").root
TEST_TAX = [{"market": "KOSPI", "start": "2024-01-01", "rate": 0.0018}, {"market": "KOSPI", "start": "2025-01-01", "rate": 0.0015},
            {"market": "KOSDAQ", "start": "2024-01-01", "rate": 0.0018}, {"market": "KOSDAQ", "start": "2025-01-01", "rate": 0.0015}]
BUY, SELL = 0.01, 0.02          # toy cost rates (test values)
TOL = 1e-10


# ---------------------------------------------------------------------------------------------
# toy data: 3 tickers A, B, C over 6 trading days d0..d5 (raw prices, F = 1 unless stated)
# ---------------------------------------------------------------------------------------------
CAL = pd.bdate_range("2024-07-01", periods=6)          # d0 Mon .. d5 Mon (weekend skipped)
#            d0     d1     d2     d3     d4     d5
OPEN = {"A": [100.0, 102.0, 104.0, 101.0, 103.0, 105.0],
        "B": [50.0, 49.0, 52.0, 53.0, 51.0, 50.0],
        "C": [200.0, 198.0, 202.0, 205.0, 210.0, 208.0]}
CLOSE = {"A": [101.0, 103.0, 102.0, 102.0, 104.0, 106.0],
         "B": [50.0, 50.0, 53.0, 52.0, 51.0, 49.0],
         "C": [199.0, 200.0, 204.0, 206.0, 209.0, 207.0]}


def toy_prices(open_=OPEN, close=CLOSE, factor=None, market="KOSPI"):
    rows = []
    for t in open_:
        for i, d in enumerate(CAL):
            o, c = open_[t][i], close[t][i]
            if c is None:
                continue
            rows.append({"date": d, "ticker": t, "market": market, "open": o, "close": c, "factor": 1.0 if factor is None else factor[t][i]})
    return pd.DataFrame(rows)


def toy_weights(spec: dict):
    """{as_of_date: {ticker: weight}} -> long frame (NaN allowed = hold)."""
    rows = [(pd.Timestamp(d), t, w) for d, ws in spec.items() for t, w in ws.items()]
    return pd.DataFrame(rows, columns=["as_of_date", "ticker", "weight"])


class ConstCost:
    scenario = "test"

    def __init__(self, buy=0.0, sell=0.0):
        self.buy, self.sell = buy, sell

    def rates(self, date, markets):
        n = len(markets)
        return np.full(n, self.buy), np.full(n, self.sell)

    def describe(self):
        return {"scenario": "test", "buy": self.buy, "sell": self.sell}


def _cfg(**over):
    cfg = load_config(ROOT / "configs/base.yaml")
    return cfg_override(cfg, {"backtest.delist_policy": "last_close", "period.end": "2024-07-08", **over})


def test_toy_example_matches_hand_calculation():
    """Signal d0: A 50%, B 50%. Signal d3: A 25%, C 75% (B sold). Costs: buy 1%, sell 2%. NAV starts 1 at d0 close.

    d1 (fill of d0): old book is all cash -> leg 1 = 0, nav_open = 1.
        buys A 0.5, B 0.5 -> cost = (0.5 + 0.5) * 0.01 = 0.01, nav_after = 0.99,
        post weights = 0.5 / 0.99 each (positions bought with 0.5 of nav_open, nav shrank by the cost paid from cash).
        leg 2: A 102 -> 103 (+0.98039%), B 49 -> 50 (+2.04082%)
        nav_d1 = 0.99 * (1 + 0.5/0.99 * (103/102 - 1) + 0.5/0.99 * (50/49 - 1)) = 0.99 + 0.5*(103/102 - 1) + 0.5*(50/49 - 1)
    d2: no fill. holdings A, B drift with close/close: nav_d2 = nav_d1 * (1 + wA*(102/103 - 1) + wB*(53/50 - 1))
    d3: no fill (signal at close). nav_d3 = nav_d2 * (1 + wA*(102/102 - 1) + wB*(52/53 - 1))
    d4 (fill of d3): leg 1: A 102 -> 103 open, B 52 -> 51 open. Then sell B fully (0.02), buy C 0.75 (0.01), A: target 0.25 vs drifted.
    d5: no fill. The expected values below are computed step by step with the same arithmetic, independently of the engine."""
    prices = toy_prices()
    weights = toy_weights({"2024-07-01": {"A": 0.5, "B": 0.5}, "2024-07-04": {"A": 0.25, "C": 0.75}})
    out = run_v1(weights, prices, CAL, ConstCost(BUY, SELL), _cfg())
    nav = out["nav"].set_index("date")["nav"]

    # ---- hand calculation ------------------------------------------------------------------------
    # d1
    cost1 = (0.5 + 0.5) * BUY
    nav_after1 = 1.0 * (1 - cost1)
    wA, wB = 0.5 / (1 - cost1), 0.5 / (1 - cost1)
    g2 = 1 + wA * (103 / 102 - 1) + wB * (50 / 49 - 1)
    nav1 = nav_after1 * g2
    wA, wB = wA * (103 / 102) / g2, wB * (50 / 49) / g2
    assert nav.loc[CAL[1]] == pytest.approx(nav1, abs=TOL)
    # d2
    g = 1 + wA * (102 / 103 - 1) + wB * (53 / 50 - 1)
    nav2 = nav1 * g
    wA, wB = wA * (102 / 103) / g, wB * (53 / 50) / g
    assert nav.loc[CAL[2]] == pytest.approx(nav2, abs=TOL)
    # d3
    g = 1 + wA * (102 / 102 - 1) + wB * (52 / 53 - 1)
    nav3 = nav2 * g
    wA, wB = wA * (102 / 102) / g, wB * (52 / 53) / g
    assert nav.loc[CAL[3]] == pytest.approx(nav3, abs=TOL)
    # d4: leg 1 to the open
    g1 = 1 + wA * (103 / 102 - 1) + wB * (51 / 52 - 1)
    nav_open4 = nav3 * g1
    preA, preB = wA * (103 / 102) / g1, wB * (51 / 52) / g1
    # trades: A -> 0.25 (buy if preA < 0.25 else sell), B -> 0 (sell preB), C -> 0.75 (buy)
    buyA, sellA = max(0.25 - preA, 0), max(preA - 0.25, 0)
    cost4 = buyA * BUY + sellA * SELL + preB * SELL + 0.75 * BUY
    nav_after4 = nav_open4 * (1 - cost4)
    pA, pC = 0.25 / (1 - cost4), 0.75 / (1 - cost4)
    g2 = 1 + pA * (104 / 103 - 1) + pC * (209 / 210 - 1)
    nav4 = nav_after4 * g2
    pA, pC = pA * (104 / 103) / g2, pC * (209 / 210) / g2
    assert nav.loc[CAL[4]] == pytest.approx(nav4, abs=TOL)
    # d5
    g = 1 + pA * (106 / 104 - 1) + pC * (207 / 209 - 1)
    nav5 = nav4 * g
    assert nav.loc[CAL[5]] == pytest.approx(nav5, abs=TOL)
    # costs in NAV units, fills and statuses
    daily = out["daily"].set_index("date")
    assert daily.loc[CAL[1], "cost"] == pytest.approx(1.0 * cost1, abs=TOL)
    assert daily.loc[CAL[4], "cost"] == pytest.approx(nav_open4 * cost4, abs=TOL)
    tr = out["trades"]
    assert sorted(tr["fill_date"].unique()) == [CAL[1], CAL[4]]
    b4 = tr[(tr["fill_date"] == CAL[4]) & (tr["ticker"] == "B")].iloc[0]
    assert b4["w_tgt"] == 0 and b4["status"] == "filled" and b4["trade_w"] == pytest.approx(-preB, abs=TOL)
    assert_fill_after_signal(tr)
    # holdings at the close of d5 sum to the invested fraction (1 - cash)
    h5 = out["holdings"][out["holdings"]["date"] == CAL[5]]
    assert h5["weight"].sum() == pytest.approx(1 - daily.loc[CAL[5], "cash"], abs=TOL)


def test_costs_zero_gives_no_cost_and_paid_costs_equal_traded_weight_times_rate():
    prices = toy_prices()
    weights = toy_weights({"2024-07-01": {"A": 0.5, "B": 0.5}, "2024-07-04": {"A": 0.25, "C": 0.75}})
    free = run_v1(weights, prices, CAL, ConstCost(0, 0), _cfg())
    assert (free["nav"]["cost"] == 0).all() and (free["trades"]["cost"] == 0).all()
    assert free["daily"]["cash"].iloc[1:].abs().max() < TOL                # fully invested, no cost -> cash exactly 0
    rate = 0.001
    paid = run_v1(weights, prices, CAL, ConstCost(rate, rate), _cfg())
    tr = paid["trades"]
    # per trade row: cost fraction = |traded weight| * rate (one rate for buys and sells here)
    assert np.allclose(tr["cost"], tr["trade_w"].abs() * rate, atol=TOL)
    # per fill day: cost fraction = (buys + sells) * rate = 2 * turnover * rate
    d = paid["daily"].set_index("date")
    for day in (CAL[1], CAL[4]):
        assert tr.loc[tr["fill_date"] == day, "cost"].sum() == pytest.approx(2 * d.loc[day, "turnover"] * rate, abs=TOL)
    # the NAV gap compounds those fractions (second-order difference from the open->close leg only)
    frac = [tr.loc[tr["fill_date"] == day, "cost"].sum() for day in (CAL[1], CAL[4])]
    assert paid["nav"]["nav"].iloc[-1] / free["nav"]["nav"].iloc[-1] == pytest.approx(np.prod([1 - c for c in frac]), rel=1e-4)
    assert paid["nav"]["nav"].iloc[-1] < free["nav"]["nav"].iloc[-1]


def test_drift_between_fills_keeps_sum_and_follows_price_ratios():
    prices = toy_prices()
    weights = toy_weights({"2024-07-01": {"A": 0.5, "B": 0.5}})
    out = run_v1(weights, prices, CAL, ConstCost(0, 0), _cfg())
    h = out["holdings"].pivot(index="date", columns="ticker", values="weight")
    assert np.allclose(h.sum(axis=1), 1.0, atol=TOL)                       # fully invested, no cash leak
    # weight ratio A/B moves exactly with the adjusted close ratio
    for i in range(2, 6):
        ratio = (h.loc[CAL[i], "A"] / h.loc[CAL[i], "B"]) / (h.loc[CAL[i - 1], "A"] / h.loc[CAL[i - 1], "B"])
        assert ratio == pytest.approx((CLOSE["A"][i] / CLOSE["A"][i - 1]) / (CLOSE["B"][i] / CLOSE["B"][i - 1]), abs=TOL)
    # nav equals the buy-and-hold of the two positions bought at d1 open
    bh = 0.5 * CLOSE["A"][5] / OPEN["A"][1] + 0.5 * CLOSE["B"][5] / OPEN["B"][1]
    assert out["nav"]["nav"].iloc[-1] == pytest.approx(bh, abs=TOL)


def test_hold_nan_means_no_trade_and_partial_hold_keeps_drifted_weight():
    prices = toy_prices()
    # daily rebalancing, every signal after the first is "hold everything" -> buy & hold, no cost
    spec = {"2024-07-01": {"A": 0.5, "B": 0.5}}
    for d in CAL[1:4]:
        spec[str(d.date())] = {"A": np.nan, "B": np.nan}
    out = run_v1(toy_weights(spec), prices, CAL, ConstCost(BUY, SELL), _cfg())
    tr = out["trades"]
    assert set(tr.loc[tr["fill_date"] > CAL[1], "status"]) == {"hold"}
    assert tr.loc[tr["fill_date"] > CAL[1], "cost"].sum() == 0 and (tr.loc[tr["fill_date"] > CAL[1], "trade_w"] == 0).all()
    bh = run_v1(toy_weights({"2024-07-01": {"A": 0.5, "B": 0.5}}), prices, CAL, ConstCost(BUY, SELL), _cfg())
    pd.testing.assert_frame_equal(out["nav"], bh["nav"])
    # partial hold: A held (NaN), B re-targeted; A keeps its drifted weight exactly
    spec2 = {"2024-07-01": {"A": 0.5, "B": 0.5}, "2024-07-03": {"A": np.nan, "B": 0.2}}
    out2 = run_v1(toy_weights(spec2), prices, CAL, ConstCost(0, 0), _cfg())
    t4 = out2["trades"][out2["trades"]["fill_date"] == CAL[3]].set_index("ticker")
    assert t4.loc["A", "status"] == "hold" and t4.loc["A", "trade_w"] == 0 and np.isnan(t4.loc["A", "w_tgt"])
    assert t4.loc["B", "status"] == "filled" and t4.loc["B", "w_pre"] + t4.loc["B", "trade_w"] == pytest.approx(0.2, abs=TOL)


def test_buys_are_scaled_when_they_exceed_the_available_weight():
    prices = toy_prices()
    # A held 50%, hold A (NaN) and ask for B 70% -> only 50% available -> B scaled to 0.5, status scaled
    spec = {"2024-07-01": {"A": 0.5, "B": 0.5}, "2024-07-03": {"A": np.nan, "B": 0.7}}
    out = run_v1(toy_weights(spec), prices, CAL, ConstCost(0, 0), _cfg())
    t = out["trades"][out["trades"]["fill_date"] == CAL[3]].set_index("ticker")
    assert t.loc["B", "status"] == "scaled"
    assert t.loc["A", "w_pre"] + t.loc["B", "w_pre"] + t.loc["B", "trade_w"] == pytest.approx(1.0, abs=TOL)


def test_no_open_price_means_no_trade_and_delisting_follows_policy():
    open_ = {k: list(v) for k, v in OPEN.items()}
    close = {k: list(v) for k, v in CLOSE.items()}
    open_["B"][1] = np.nan                                  # B halted at the d1 open: cannot be bought
    prices = toy_prices(open_, close)
    out = run_v1(toy_weights({"2024-07-01": {"A": 0.5, "B": 0.5}}), prices, CAL, ConstCost(0, 0), _cfg())
    t1 = out["trades"][out["trades"]["fill_date"] == CAL[1]].set_index("ticker")
    assert t1.loc["B", "status"] == "no_price" and t1.loc["B", "trade_w"] == 0 and t1.loc["A", "status"] == "filled"
    # cash was 0.5 of the open NAV; at the close A has moved 102 -> 103, so the cash fraction is 0.5 / (0.5 * 103/102 + 0.5)
    assert out["daily"].set_index("date").loc[CAL[1], "cash"] == pytest.approx(0.5 / (0.5 * 103 / 102 + 0.5), abs=TOL)
    # delisting: C disappears after d3 while held -> last_close converts to cash at the d3 close, zero writes it off
    close2 = {k: list(v) for k, v in CLOSE.items()}
    open2 = {k: list(v) for k, v in OPEN.items()}
    close2["C"][4] = close2["C"][5] = None
    open2["C"][4] = open2["C"][5] = None
    prices2 = toy_prices(open2, close2)
    w = toy_weights({"2024-07-01": {"A": 0.5, "C": 0.5}})
    lc = run_v1(w, prices2, CAL, ConstCost(0, 0), _cfg())
    zero = run_v1(w, prices2, CAL, ConstCost(0, 0), _cfg(**{"backtest.delist_policy": "zero"}))
    d_lc, d_zero = lc["daily"].set_index("date"), zero["daily"].set_index("date")
    # value of the C position at the d3 close under both policies
    c_val = 0.5 * CLOSE["C"][3] / OPEN["C"][1]
    a_val3 = 0.5 * CLOSE["A"][3] / OPEN["A"][1]
    assert d_lc.loc[CAL[3], "nav"] == pytest.approx(a_val3 + c_val, abs=TOL)
    assert d_lc.loc[CAL[4], "nav"] == pytest.approx(0.5 * CLOSE["A"][4] / OPEN["A"][1] + c_val, abs=TOL)      # C -> cash at last close
    assert d_zero.loc[CAL[4], "nav"] == pytest.approx(0.5 * CLOSE["A"][4] / OPEN["A"][1], abs=TOL)            # C written off
    assert lc["delist_policy_used"] == "last_close" and zero["delist_policy_used"] == "zero"
    assert list(lc["delisted"]["ticker"]) == ["C"] and lc["delisted"]["date"].iloc[0] == CAL[4]
    with pytest.raises(ConfigError, match="delist_policy"):
        run_v1(w, prices2, CAL, ConstCost(0, 0), _cfg(**{"backtest.delist_policy": None}))
    # the policy is not required when nothing delists
    assert run_v1(toy_weights({"2024-07-01": {"A": 1.0}}), prices2, CAL, ConstCost(0, 0), _cfg(**{"backtest.delist_policy": None}))["delist_policy_used"] is None


def test_adjusted_prices_split_inside_holding_period_is_not_a_loss():
    # A has a 2:1 split at d3: raw halves, F doubles -> adjusted series continuous
    open_ = {k: list(v) for k, v in OPEN.items()}
    close = {k: list(v) for k, v in CLOSE.items()}
    factor = {k: [1.0] * 6 for k in OPEN}
    for i in range(3, 6):
        open_["A"][i] /= 2; close["A"][i] /= 2; factor["A"][i] = 2.0
    out = run_v1(toy_weights({"2024-07-01": {"A": 1.0}}), toy_prices(open_, close, factor), CAL, ConstCost(0, 0), _cfg())
    assert out["nav"]["nav"].iloc[-1] == pytest.approx(CLOSE["A"][5] / OPEN["A"][1], abs=TOL)


def test_lookahead_guard_and_signal_dates_must_be_trading_days():
    prices = toy_prices()
    with pytest.raises(ValueError, match="trading days"):
        run_v1(toy_weights({"2024-07-06": {"A": 1.0}}), prices, CAL, ConstCost(0, 0), _cfg())     # Saturday
    out = run_v1(toy_weights({"2024-07-01": {"A": 1.0}}), prices, CAL, ConstCost(0, 0), _cfg())
    assert (out["trades"]["signal_date"] < out["trades"]["fill_date"]).all()
    with pytest.raises(LookaheadError):
        assert_fill_after_signal(out["trades"].assign(fill_date=out["trades"]["signal_date"]))
    # a signal on the last day cannot fill within the window -> reported, not silently dropped
    out2 = run_v1(toy_weights({"2024-07-01": {"A": 1.0}, "2024-07-08": {"B": 1.0}}), prices, CAL, ConstCost(0, 0), _cfg())
    assert out2["unfilled_signal_dates"] == [CAL[5]]


# ---------------------------------------------------------------------------------------------
# cost model
# ---------------------------------------------------------------------------------------------
def test_cost_model_scenarios_and_tax_table():
    cfg = load_config(ROOT / "configs/base.yaml")
    none = CostModel(cfg, "none")
    assert none.buy_cost_rate("2024-07-01", "KOSPI") == 0 and none.sell_cost_rate("2024-07-01", "KOSPI") == 0
    paper = CostModel(cfg, "paper")
    assert paper.buy_cost_rate("2024-07-01", "KOSDAQ") == pytest.approx(cfg["costs"]["paper"]["buy"])
    assert paper.sell_cost_rate("2024-07-01", "KOSDAQ") == pytest.approx(cfg["costs"]["paper"]["sell"])
    with pytest.raises(ConfigError, match="sell_tax_table"):
        CostModel(cfg_override(cfg, {"costs.sell_tax_table": []}), "kr")
    kr = CostModel(cfg_override(cfg, {"costs.sell_tax_table": TEST_TAX}), "kr")
    slip = cfg["costs"]["slippage_bps"] / 1e4
    assert kr.buy_cost_rate("2024-12-31", "KOSPI") == pytest.approx(cfg["costs"]["commission_buy"] + slip)
    assert kr.sell_cost_rate("2024-12-31", "KOSPI") == pytest.approx(cfg["costs"]["commission_sell"] + slip + 0.0018)
    assert kr.sell_cost_rate("2025-01-02", "kosdaq") == pytest.approx(cfg["costs"]["commission_sell"] + slip + 0.0015)
    buy, sell = kr.rates("2025-03-03", np.array(["KOSPI", "KOSDAQ", "KOSPI"], dtype=object))
    assert np.allclose(sell, cfg["costs"]["commission_sell"] + slip + 0.0015) and np.allclose(buy, kr.buy_base)
    with pytest.raises(ConfigError):
        kr.sell_cost_rate("2023-01-02", "KOSPI")          # before the first start
    with pytest.raises(ConfigError):
        kr.sell_cost_rate("2024-07-01", "NYSE")


# ---------------------------------------------------------------------------------------------
# real fake run_ids (skipped when absent)
# ---------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def real():
    cfg = load_config(ROOT / "configs/base.yaml")
    paths = Paths(cfg, ROOT)
    if not (paths.signals_path("fake_oracle_base").exists() and paths.prepared_path("prices").exists()):
        pytest.skip("generate fake_oracle_base signals first")
    prices = pd.read_parquet(paths.prepared_path("prices"), columns=["date", "ticker", "market", "open", "close"])
    adj = pd.read_parquet(paths.prepared_path("adj_factor"))
    prices = prices.merge(adj, on=["date", "ticker"], validate="one_to_one")
    cal = pd.DatetimeIndex(pd.read_parquet(paths.prepared_path("calendar"))["date"])
    sig = pd.read_parquet(paths.signals_path("fake_oracle_base"))
    return {"cfg": cfg_override(cfg, {"backtest.delist_policy": "last_close"}), "prices": prices, "cal": cal, "sig": sig}


def _topk_weights(sig: pd.DataFrame, col: str, k: int) -> pd.DataFrame:
    rows = []
    for d, g in sig.groupby("as_of_date"):
        top = g.sort_values([col, "ticker"], ascending=[False, True]).head(k)["ticker"]
        rows.append(pd.DataFrame({"as_of_date": d, "ticker": top.to_numpy(), "weight": 1.0 / len(top)}))
    return pd.concat(rows, ignore_index=True)


def _ew_weights(sig: pd.DataFrame) -> pd.DataFrame:
    n = sig.groupby("as_of_date")["ticker"].transform("size")
    return sig[["as_of_date", "ticker"]].assign(weight=1.0 / n)


def test_oracle_topk_beats_equal_weight_and_the_edge_disappears_one_period_later(real):
    cfg, prices, cal, sig = real["cfg"], real["prices"], real["cal"], real["sig"]
    free = CostModel(cfg, "none")
    ew = run_v1(_ew_weights(sig), prices, cal, free, cfg)["nav"]["nav"].iloc[-1] - 1
    oracle = run_v1(_topk_weights(sig, "exp_ret", 20), prices, cal, free, cfg)["nav"]["nav"].iloc[-1] - 1
    # same picks applied one rebalance later: the oracle knew s -> s+5 returns, now it trades at s+6
    w = _topk_weights(sig, "exp_ret", 20)
    dates = sorted(w["as_of_date"].unique())
    shift = dict(zip(dates[:-1], dates[1:]))
    late = w[w["as_of_date"].isin(shift)].assign(as_of_date=lambda d: d["as_of_date"].map(shift))
    stale = run_v1(late, prices, cal, free, cfg)["nav"]["nav"].iloc[-1] - 1
    assert oracle > 5.0                                     # the realised-future picker compounds absurdly (> +500% in a year)
    assert oracle > ew + 1.0
    assert (stale - ew) < 0.25 * (oracle - ew)             # the edge collapses when the information is a week old


def test_random_topk_mean_is_near_equal_weight(real):
    """RandomTopK (k = 20) over several seeds should average close to EqualWeight when costs are off: a large,
    systematic gap would point at an engine or data bias rather than skill. Tolerance 0.15 (15 %p of total
    return over the year) reflects the dispersion of 20-stock portfolios drawn from ~850 names (single-seed
    outcomes differ by tens of %p; the mean of 8 seeds narrows to a few %p)."""
    cfg, prices, cal, sig = real["cfg"], real["prices"], real["cal"], real["sig"]
    free = CostModel(cfg, "none")
    ew = run_v1(_ew_weights(sig), prices, cal, free, cfg)["nav"]["nav"].iloc[-1] - 1
    rets = []
    for seed in range(8):
        rows = []
        for d, g in sig.groupby("as_of_date"):
            t = sorted(g["ticker"])
            rng = np.random.default_rng([seed, int(pd.Timestamp(d).value // 10**9)])
            pick = np.array(t)[np.argsort(-rng.random(len(t)))[:20]]
            rows.append(pd.DataFrame({"as_of_date": d, "ticker": pick, "weight": 1.0 / len(pick)}))
        rets.append(run_v1(pd.concat(rows, ignore_index=True), prices, cal, free, cfg)["nav"]["nav"].iloc[-1] - 1)
    assert abs(float(np.mean(rets)) - ew) < 0.15, (rets, ew)


def test_real_outputs_on_disk():
    cfg = load_config(ROOT / "configs/base.yaml")
    paths = Paths(cfg, ROOT)
    d = paths.backtest_dir("fake_dummy_base", "v1", "equal_weight@no_costs")
    if not (d / "nav.csv").exists():
        pytest.skip("run E_backtest.run_backtest first")
    for name in ("equal_weight", "momentum20_topk", "random_topk"):
        for sfx in ("@no_costs", "@paper_costs"):
            out = paths.backtest_dir("fake_dummy_base", "v1", name + sfx)
            nav = pd.read_csv(out / "nav.csv", parse_dates=["date"])
            tr = pd.read_parquet(out / "trades.parquet")
            meta = json.loads((out / "meta.json").read_text())
            assert nav["date"].iloc[0] == pd.Timestamp("2024-07-01") and nav["date"].iloc[-1] == pd.Timestamp("2025-06-30")
            assert (nav["nav"] > 0).all() and (nav["cost"] >= 0).all()
            assert (nav["cost"].sum() == 0) == (sfx == "@no_costs")
            assert_fill_after_signal(tr)
            assert meta["engine"] == "v1" and meta["strategy"] == name and meta["config_hash"]
