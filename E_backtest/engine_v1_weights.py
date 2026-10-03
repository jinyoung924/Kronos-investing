"""Engine v1: idealised, weight-space, vectorised across tickers, one loop over trading days
(docs/outline.md E_backtest, docs/spec.md Stage 4 "시간 규칙" and task 2).

Time rules (shared with v2)
  1. The target weights of rebalance (signal) date s fill at the OPEN of f = next_trading_day(s).
  2. Valuation is at every CLOSE. The fill day has two legs: prev close -> open with the old holdings,
     open -> close with the new holdings.
  3. Between fills quantities are fixed, so weights drift with prices; the next fill trades the difference
     between the drifted weights and the targets.
  4. Returns use adjusted prices (raw * F).
Fill-day rules
  * w_pre = drifted weights at the open; a ticker whose target is NaN (hold) keeps w_pre (status `hold`);
    a ticker with no open price that day (halted) is not traded and keeps w_pre (status `no_price`), even
    when the target is 0; tickers absent from the file are sold.
  * Sells execute in full. If the buys exceed the available weight 1 - (kept + post-sell weights) they are
    all scaled by the same factor (status `scaled`).
  * cost = sum(buy * buy_rate) + sum(sell * sell_rate) as a fraction of the open NAV, paid from cash
    (v1 has no cash constraint, so cash may go slightly negative on a fully invested book).
  * A held ticker whose last bar has passed (delisted) is closed under backtest.delist_policy:
    `last_close` -> converted to cash at its last close, `zero` -> written off. The policy is only required
    when this actually happens; a gap that is followed by later bars is carried at the previous close.
Pure function: run_v1(weights, prices, calendar, cost_model, cfg) -> dict(nav, daily, trades, holdings).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from common.calendar import next_trading_day
from common.config import ConfigError, cfg_get
from common.lookahead import assert_fill_after_signal
from common.schema import validate_nav, validate_weights

TRADE_COLUMNS = ["fill_date", "signal_date", "ticker", "w_pre", "w_tgt", "trade_w", "cost", "status"]
DELIST_POLICIES = ("last_close", "zero")
EPS = 1e-12
SCALE_TOL = 1e-9       # buys exceeding the available weight by less than this are not scaled


def _delist_policy(cfg: dict) -> str:
    p = cfg_get(cfg, "backtest.delist_policy")
    if p is None:
        raise ConfigError("backtest.delist_policy is null but a held ticker was delisted: set last_close | zero in configs/base.yaml")
    if p not in DELIST_POLICIES:
        raise ConfigError(f"backtest.delist_policy must be one of {DELIST_POLICIES}, got {p!r}")
    return str(p)


def _wide(prices: pd.DataFrame, col: str, dates: pd.DatetimeIndex, tickers: list[str]) -> np.ndarray:
    w = prices.pivot(index="date", columns="ticker", values=col).reindex(index=dates, columns=tickers)
    return w.to_numpy(dtype=float)


def run_v1(weights: pd.DataFrame, prices: pd.DataFrame, calendar: pd.DatetimeIndex, cost_model, cfg: dict,
           end=None) -> dict:
    """weights: long (as_of_date, ticker, weight; NaN = hold). prices: long (date, ticker, market, open, close,
    factor) with RAW open/close and the forward factor F. calendar: trading days. Simulation runs from the
    close of the first as_of date (NAV = 1) to `end` (default period.end; capped at the calendar end)."""
    w_long = validate_weights(weights)
    if w_long.empty:
        raise ValueError("no weights to backtest")
    calendar = pd.DatetimeIndex(calendar)
    sig_dates = pd.DatetimeIndex(sorted(w_long["as_of_date"].unique()))
    if not sig_dates.isin(calendar).all():
        bad = sig_dates[~sig_dates.isin(calendar)][:3]
        raise ValueError(f"as_of dates must be trading days, e.g. {[d.date() for d in bad]}")
    end = pd.Timestamp(end) if end is not None else pd.Timestamp(cfg_get(cfg, "period.end", calendar[-1]))
    end = min(end.normalize(), calendar[-1])
    start = sig_dates[0]
    days = calendar[(calendar > start) & (calendar <= end)]
    if len(days) == 0:
        raise ValueError(f"no trading days between the first as_of {start.date()} and end {end.date()}")

    tickers = sorted(w_long["ticker"].unique())
    px = prices[prices["ticker"].isin(tickers)].copy()
    missing = sorted(set(tickers) - set(px["ticker"].unique()))
    if missing:
        raise ValueError(f"no price rows for weight tickers {missing[:5]}")
    px["date"] = pd.to_datetime(px["date"]).dt.normalize()
    if "factor" not in px.columns:
        raise ValueError("prices need a `factor` column (forward adjustment factor F)")
    px["open_adj"] = px["open"] * px["factor"]
    px["close_adj"] = px["close"] * px["factor"]
    all_days = calendar[(calendar >= start) & (calendar <= end)]        # includes the start close
    open_adj = _wide(px, "open_adj", all_days, tickers)
    close_adj = _wide(px, "close_adj", all_days, tickers)
    market = px.drop_duplicates("ticker", keep="last").set_index("ticker")["market"].reindex(tickers).fillna("").to_numpy(dtype=object) \
        if "market" in px.columns else np.array([""] * len(tickers), dtype=object)
    market_by_day = px.pivot(index="date", columns="ticker", values="market").reindex(index=all_days, columns=tickers).ffill().bfill() \
        if "market" in px.columns else None
    last_bar = px.groupby("ticker")["date"].max().reindex(tickers)
    last_pos = np.array([all_days.searchsorted(d, side="right") - 1 for d in last_bar])   # index in all_days of the last bar
    pos_of = {d: i for i, d in enumerate(all_days)}
    tidx = {t: i for i, t in enumerate(tickers)}

    # fills: signal date -> fill date (first trading day after s, within the window)
    fills: dict[pd.Timestamp, pd.Timestamp] = {}
    unfilled = []
    for s in sig_dates:
        try:
            f = next_trading_day(calendar, s)
        except ValueError:
            unfilled.append(s); continue
        if f > end:
            unfilled.append(s); continue
        fills[f] = s
    tgt_by_sig = {s: g.set_index("ticker")["weight"] for s, g in w_long.groupby("as_of_date")}

    n = len(tickers)
    w = np.zeros(n)                      # weights at the previous close (fractions of NAV)
    nav = 1.0
    last_close = close_adj[0].copy()     # adjusted close known at the start (NaN if no bar)
    delist_policy = None
    daily_rows = [{"date": start, "nav": 1.0, "ret": 0.0, "cost": 0.0, "gross_ret": 0.0, "cash": 1.0, "n_holdings": 0, "turnover": 0.0, "buy_scale": 1.0}]
    holdings_rows, trade_rows, delisted_rows = [], [], []

    for d in days:
        i = pos_of[d]
        c = close_adj[i]
        o = open_adj[i]
        held = w > EPS
        # --- delistings: last bar passed and still held -> close the position at the previous close -------
        gone = held & (i > last_pos)
        w_before_delist = w.copy()
        if gone.any():
            delist_policy = delist_policy or _delist_policy(cfg)
            # last_close: the position becomes cash at its last close (weight -> 0, NAV unchanged);
            # zero: the position is written off (write_off below reduces the day's NAV)
            for j in np.flatnonzero(gone):
                delisted_rows.append({"date": d, "ticker": tickers[j], "weight": float(w[j]), "last_bar": last_bar.iloc[j], "policy": delist_policy})
            w[gone] = 0.0
            held = w > EPS
        # price available for the day: carry the previous close when a bar is missing (gap), halted days carry the reference close
        c_eff = np.where(np.isnan(c), last_close, c)
        r_day = np.where(held, c_eff / last_close - 1.0, 0.0)
        r_day = np.nan_to_num(r_day, nan=0.0)
        if gone.any() and delist_policy == "zero":
            write_off = float(w_before_delist[gone].sum())
        else:
            write_off = 0.0
        cost_frac = 0.0
        turnover = 0.0
        scale = 1.0
        if d in fills:
            s = fills[d]
            tgt = tgt_by_sig[s]
            # leg 1: previous close -> open with the old holdings (no open -> whole-day move, traded flag False)
            has_open = ~np.isnan(o)
            r1 = np.where(held & has_open, o / last_close - 1.0, np.where(held, c_eff / last_close - 1.0, 0.0))
            r1 = np.nan_to_num(r1, nan=0.0)
            g1 = 1.0 + float(np.dot(w, r1)) - write_off
            nav_open = nav * g1
            w_pre = w * (1.0 + r1) / g1
            # targets: absent -> 0 (sell), NaN -> hold
            t_ser = tgt.reindex(tickers)
            present = pd.Series(tickers).isin(tgt.index).to_numpy()
            hold_mask = present & t_ser.isna().to_numpy()
            t_arr = t_ser.fillna(0.0).to_numpy(dtype=float)
            tradeable = has_open & ~hold_mask             # hold and no-open tickers keep w_pre
            after_sell = np.where(tradeable, np.minimum(t_arr, w_pre), w_pre)
            buys_raw = np.where(tradeable, np.maximum(t_arr - w_pre, 0.0), 0.0)
            room = 1.0 - float(after_sell.sum())          # available weight = 1 - kept - post-sell weights
            scale = 1.0
            if buys_raw.sum() > 0 and buys_raw.sum() > room + SCALE_TOL:   # only a real excess scales (not float noise)
                scale = max(room, 0.0) / buys_raw.sum()
            buys = buys_raw * scale
            new_w = after_sell + buys
            sells = np.where(tradeable, np.maximum(w_pre - t_arr, 0.0), 0.0)
            mk = market_by_day.iloc[i].to_numpy(dtype=object) if market_by_day is not None else market
            buy_rate, sell_rate = cost_model.rates(d, mk)
            cost_vec = buys * buy_rate + sells * sell_rate
            cost_frac = float(cost_vec.sum())
            nav_after = nav_open * (1.0 - cost_frac)
            w_post = new_w / (1.0 - cost_frac) if cost_frac < 1 else new_w
            turnover = float(np.abs(new_w - w_pre).sum() / 2)
            # trades log: every ticker with a target, a hold, or a pre-trade position
            touched = present | (w_pre > EPS)
            for j in np.flatnonzero(touched):
                if hold_mask[j]:
                    status = "hold"
                elif not has_open[j]:
                    status = "no_price"
                elif buys_raw[j] > EPS and scale < 1.0:
                    status = "scaled"
                elif abs(new_w[j] - w_pre[j]) <= EPS:
                    status = "unchanged"
                else:
                    status = "filled"
                trade_rows.append((d, s, tickers[j], float(w_pre[j]), (np.nan if hold_mask[j] else float(t_arr[j])),
                                   float(new_w[j] - w_pre[j]), float(cost_vec[j]), status))
            # leg 2: open -> close with the new holdings (no open -> already moved in leg 1)
            r2 = np.where(has_open, c_eff / np.where(has_open, o, 1.0) - 1.0, 0.0)
            r2 = np.nan_to_num(r2, nan=0.0)
            g2 = 1.0 + float(np.dot(w_post, r2))
            nav_close = nav_after * g2
            w_new = w_post * (1.0 + r2) / g2
            gross = nav_open * g2 / nav - 1.0        # return before costs that day
            cost_amt = nav_open * cost_frac
        else:
            g = 1.0 + float(np.dot(w, r_day)) - write_off
            nav_close = nav * g
            w_new = w * (1.0 + r_day) / g
            gross = g - 1.0
            cost_amt = 0.0
        ret = nav_close / nav - 1.0
        nav = nav_close
        w = w_new
        w[np.abs(w) < EPS] = 0.0
        last_close = np.where(np.isnan(c), last_close, c)
        held_now = w > EPS
        daily_rows.append({"date": d, "nav": nav, "ret": ret, "cost": cost_amt, "gross_ret": gross,
                           "cash": 1.0 - float(w.sum()), "n_holdings": int(held_now.sum()), "turnover": turnover, "buy_scale": scale})
        if held_now.any():
            holdings_rows.append(pd.DataFrame({"date": d, "ticker": np.array(tickers, dtype=object)[held_now], "weight": w[held_now]}))

    daily = pd.DataFrame(daily_rows)
    nav_df = validate_nav(daily[["date", "nav", "ret", "cost"]])
    trades = pd.DataFrame(trade_rows, columns=TRADE_COLUMNS)
    if len(trades):
        assert_fill_after_signal(trades)
    holdings = pd.concat(holdings_rows, ignore_index=True) if holdings_rows else pd.DataFrame(columns=["date", "ticker", "weight"])
    delisted = pd.DataFrame(delisted_rows, columns=["date", "ticker", "weight", "last_bar", "policy"])
    return {"nav": nav_df, "daily": daily, "trades": trades, "holdings": holdings, "delisted": delisted,
            "unfilled_signal_dates": [pd.Timestamp(s) for s in unfilled], "delist_policy_used": delist_policy}
