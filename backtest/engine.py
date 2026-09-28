"""Strategy-agnostic portfolio engine.

Inputs
------
target_weights : DataFrame indexed by as_of (signal) dates, columns = tickers, values in [0, 1],
                 row sums <= 1 (the remainder is cash). A signal at as_of date t is filled at the
                 OPEN of the next trading day t+1 (shift(1) on the trading calendar).
open_prices    : DataFrame date x ticker of RAW open prices (the trading calendar is its index).
adj_factor     : DataFrame date x ticker; open * adj_factor is used for returns.

Mechanics
---------
* nav[t] is the portfolio value at the open of day t after that day's trades and costs.
* Between rebalances holdings drift with prices (no daily re-weighting).
* A ticker with no price at a fill date cannot be traded: its target goes to cash.
* A held ticker whose price disappears (gap longer than ffill_limit) is liquidated at its last
  known price (0 return on that day) and the proceeds go to cash.
* Costs: buy_rate / sell_rate per ticker from CostModel, charged on traded notional.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from backtest.costs import CostModel
from common.data import ffill_wide, open_to_open_returns
from common.lookahead import LookaheadError, assert_signal_before_fill


@dataclass
class BacktestResult:
    nav: pd.Series                    # net of costs, indexed by trading day (value at the open)
    nav_gross: pd.Series              # same weights, no costs
    returns: pd.Series                # nav.pct_change() (net)
    returns_gross: pd.Series
    turnover: pd.Series               # one-way turnover (sum|dw|/2) on each fill date, 0 elsewhere
    n_holdings: pd.Series             # number of positions held each day
    costs: pd.Series                  # cost paid each day as a fraction of NAV
    weights: pd.DataFrame             # realised (drifted) weights at each day's open, post-trade
    fill_dates: pd.DatetimeIndex      # fill date of each row of target_weights actually traded
    untradable: dict = field(default_factory=dict)  # fill_date -> tickers whose target went to cash

    def summary_frame(self) -> pd.DataFrame:
        return pd.DataFrame({
            "nav": self.nav, "nav_gross": self.nav_gross, "ret": self.returns, "ret_gross": self.returns_gross,
            "turnover": self.turnover, "n_holdings": self.n_holdings, "cost": self.costs,
        })


def validate_target_weights(W: pd.DataFrame, tol: float = 1e-8) -> pd.DataFrame:
    W = W.astype(float).fillna(0.0)
    if (W < -tol).any().any():
        raise ValueError("negative target weight (long-only engine)")
    row_sum = W.sum(axis=1)
    if (row_sum > 1 + 1e-6).any():
        bad = row_sum[row_sum > 1 + 1e-6]
        raise ValueError(f"target weights sum > 1 on {list(bad.index[:3].date)}")
    return W.clip(lower=0.0)


def fill_positions(calendar: pd.DatetimeIndex, as_of_dates: pd.DatetimeIndex) -> np.ndarray:
    """Index in `calendar` of the fill day (next trading day after each as_of). -1 if none."""
    pos = calendar.searchsorted(as_of_dates, side="right")  # first index with date > as_of
    return np.where(pos < len(calendar), pos, -1)


def run_backtest(target_weights: pd.DataFrame, open_prices: pd.DataFrame, adj_factor: pd.DataFrame | None,
                 cost_model: CostModel | None = None, initial_nav: float = 1.0,
                 ffill_limit: int | None = 5) -> BacktestResult:
    cost_model = cost_model or CostModel.zero()
    calendar = pd.DatetimeIndex(open_prices.index).sort_values()
    if not calendar.is_unique:
        raise ValueError("open_prices index must be unique")
    open_prices = open_prices.reindex(calendar)

    W = validate_target_weights(target_weights).sort_index()
    W.index = pd.DatetimeIndex(W.index)
    unknown = [t for t in W.columns if t not in open_prices.columns]
    if unknown:
        raise KeyError(f"target weights for tickers without prices: {unknown[:5]}")
    if W.index.min() < calendar[0]:
        raise LookaheadError("signal date before the first trading day in the price calendar")

    # ---- signal -> fill mapping (shift(1) on the trading calendar) --------------------------
    pos = fill_positions(calendar, W.index)
    ok = pos >= 0
    if (~ok).any():
        warnings.warn(f"{int((~ok).sum())} signal rows have no fill day (after calendar end); dropped")
    W, pos = W.loc[ok], pos[ok]
    fill_dates = calendar[pos]
    assert_signal_before_fill(W.index, fill_dates)  # hard guard: as_of < fill

    # ---- returns ---------------------------------------------------------------------------
    tickers = list(open_prices.columns)
    adj = adj_factor.reindex(index=calendar, columns=tickers) if adj_factor is not None else None
    adj_open = ffill_wide(open_prices * adj if adj is not None else open_prices, ffill_limit)
    R = open_to_open_returns(adj_open).to_numpy()      # row t: open[t] -> open[t+1]
    tradable = adj_open.notna().to_numpy()
    n_days, n_tk = R.shape
    col_idx = {t: i for i, t in enumerate(tickers)}
    target_by_pos: dict[int, np.ndarray] = {}
    for p, (_, row) in zip(pos, W.iterrows()):
        vec = np.zeros(n_tk)
        for t, v in row.items():
            vec[col_idx[t]] = v
        target_by_pos[int(p)] = vec  # later signal for the same fill day overrides (should not happen)
    buy_rate, sell_rate = cost_model.buy_rates(tickers), cost_model.sell_rates(tickers)

    nav = np.empty(n_days); nav_g = np.empty(n_days)
    turnover = np.zeros(n_days); costs = np.zeros(n_days); n_hold = np.zeros(n_days, dtype=int)
    weights = np.zeros((n_days, n_tk))
    untradable: dict = {}

    w = np.zeros(n_tk)           # weights as fraction of NAV (cash = 1 - sum)
    v, v_g = float(initial_nav), float(initial_nav)
    for t in range(n_days):
        if t in target_by_pos:
            target = target_by_pos[t].copy()
            bad = (target > 0) & ~tradable[t]
            if bad.any():
                untradable[calendar[t]] = [tickers[i] for i in np.flatnonzero(bad)]
                target[bad] = 0.0
            delta = target - w
            buys, sells = np.clip(delta, 0, None), np.clip(-delta, 0, None)
            cost_frac = float(buys @ buy_rate + sells @ sell_rate)
            turnover[t] = 0.5 * float(np.abs(delta).sum())
            costs[t] = cost_frac
            v *= (1.0 - cost_frac)
            w = target
        nav[t], nav_g[t] = v, v_g
        weights[t] = w
        n_hold[t] = int((w > 1e-12).sum())
        if t == n_days - 1:
            break
        r = R[t].copy()
        dead = (w > 0) & ~np.isfinite(r)
        r[~np.isfinite(r)] = 0.0             # liquidate at last known price (0 return)
        r_p = float(w @ r)
        v *= 1.0 + r_p
        v_g *= 1.0 + r_p
        w = w * (1.0 + r) / (1.0 + r_p) if (1.0 + r_p) != 0 else np.zeros(n_tk)
        w[dead] = 0.0

    idx = calendar
    nav_s, nav_gs = pd.Series(nav, idx, name="nav"), pd.Series(nav_g, idx, name="nav_gross")
    return BacktestResult(
        nav=nav_s, nav_gross=nav_gs,
        returns=nav_s.pct_change().fillna(0.0).rename("ret"),
        returns_gross=nav_gs.pct_change().fillna(0.0).rename("ret_gross"),
        turnover=pd.Series(turnover, idx, name="turnover"), n_holdings=pd.Series(n_hold, idx, name="n_holdings"),
        costs=pd.Series(costs, idx, name="cost"), weights=pd.DataFrame(weights, idx, tickers),
        fill_dates=fill_dates, untradable=untradable,
    )
