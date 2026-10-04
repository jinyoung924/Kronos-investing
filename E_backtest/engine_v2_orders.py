"""Engine v2: order-level, state = cash (KRW) + share counts, one loop over trading days, vectorised across tickers
(docs/outline.md E_backtest, docs/spec.md Stage 7). Same time rules as v1: the targets of signal date s fill at the
open of the next trading day, valuation at every close. Prices are RAW; corporate actions change the share count.

Order of a day
  1. corporate action: r = F(today) / F(previous bar); shares *= r (a split or bonus issue is exact; a rights issue is
     treated as value-preserving, which ignores the cash the holder would pay in). With integer_shares on, the
     fractional share left over is paid out in cash at the reference price.
  2. delisting: a held ticker whose last bar has passed is closed under backtest.delist_policy (last_close | zero).
  3. fill day: target shares = open NAV * target weight / raw open (NaN target = hold, no order; a ticker absent from
     the file = target 0). Orders = target - held.
  4. sells first, at open * (1 - slippage); commission and tax are taken from the proceeds.
  5. buys, at open * (1 + slippage) plus commission, from the cash that is left.
  6. valuation: raw close * shares + cash. A ticker without a bar is valued at its previous close.
An order that is rejected or cut is not resubmitted: the position stays as it is until the next rebalance.

Constraints (E_backtest/constraints.py), each on only when named in `constraints`:
  costs           off: every rate 0                       on: CostModel components
  integer_shares  off: fractional shares                  on: target shares (and every cut order) rounded down
  cash            off: buys fill even if cash goes < 0    on: buys above the cash are all scaled by one factor   scaled_cash
  price_limit     off: ignored                            on: open >= upper limit rejects a buy, open <= lower limit a sell   rejected_limit
  halt            off: no open price -> position kept (status no_price, as v1)   on: a halted ticker's order is rejected   rejected_halt
  liquidity       off: ignored                            on: order value <= that day's traded value * max_participation   partial_liquidity
Base rule kept from v1 in every scenario: when kept positions (hold, no price, rejected sells) leave less value free
than the buys need, all buys are scaled by one factor (status `scaled`). It ignores costs, so with costs on and cash off
the cash still goes negative by the costs paid; the cash constraint then also fits the costs in (status `scaled_cash`).
With every constraint off the engine reproduces v1.
Pure function: run_v2(weights, prices, halts, calendar, cost_model, cfg, constraints) -> dict.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from common.calendar import next_trading_day
from common.config import cfg_get, require
from common.lookahead import assert_fill_after_signal
from common.schema import validate_nav, validate_weights
from E_backtest import constraints as C
from E_backtest.engine_v1_weights import _delist_policy, _wide

TRADE_COLUMNS = ["date", "signal_date", "ticker", "side", "target_shares", "filled_shares", "price", "value", "commission", "tax", "slippage", "status"]
POSITION_COLUMNS = ["date", "ticker", "shares", "close", "value", "cash"]
EPS = 1e-9            # share counts / order sizes below this are zero
ROOM_TOL = 1e-9       # buys exceeding the free value by less than this share of NAV are not scaled (v1 SCALE_TOL)


def run_v2(weights: pd.DataFrame, prices: pd.DataFrame, halts: pd.DataFrame | None, calendar: pd.DatetimeIndex, cost_model, cfg: dict,
           constraints, end=None) -> dict:
    """weights: long (as_of_date, ticker, weight; NaN = hold). prices: long (date, ticker, market, open, close, value,
    factor), RAW prices, traded value in KRW, forward factor F. halts: long (date, ticker, is_halted) or None.
    constraints: iterable of names from constraints.CONSTRAINTS. NAV starts at backtest.init_cash on the first as_of close."""
    on = set(C.parse_scenario(C.scenario_name(constraints)))
    init_cash = float(require(cfg, "backtest.init_cash"))
    if init_cash <= 0:
        raise ValueError("backtest.init_cash must be > 0")
    pct = tick_table = max_part = None
    if "price_limit" in on:
        pct, tick_table = float(require(cfg, "backtest.v2.price_limit_pct")), cfg_get(cfg, "backtest.v2.tick_table")
        C.tick_size(np.array([1.0]), tick_table)          # an empty table stops here, not on the first limit check
    if "liquidity" in on:
        max_part = float(require(cfg, "backtest.v2.max_participation"))

    w_long = validate_weights(weights)
    if w_long.empty:
        raise ValueError("no weights to backtest")
    calendar = pd.DatetimeIndex(calendar)
    sig_dates = pd.DatetimeIndex(sorted(w_long["as_of_date"].unique()))
    if not sig_dates.isin(calendar).all():
        raise ValueError("as_of dates must be trading days")
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
    all_days = calendar[(calendar >= start) & (calendar <= end)]
    open_raw, close_raw, factor = (_wide(px, c, all_days, tickers) for c in ("open", "close", "factor"))
    value = _wide(px, "value", all_days, tickers) if "value" in px.columns else np.full(open_raw.shape, np.nan)
    if "liquidity" in on and "value" not in px.columns:
        raise ValueError("the liquidity constraint needs a `value` (traded value) column in prices")
    if halts is not None and len(halts):
        h = halts[halts["ticker"].isin(tickers)].copy()
        h["date"] = pd.to_datetime(h["date"]).dt.normalize()
        h["is_halted"] = h["is_halted"].astype(float)
        halted = h.pivot(index="date", columns="ticker", values="is_halted").reindex(index=all_days, columns=tickers).to_numpy(dtype=float)
        halted = np.nan_to_num(halted, nan=0.0) > 0
    else:
        halted = np.zeros(open_raw.shape, dtype=bool)
    market_by_day = px.pivot(index="date", columns="ticker", values="market").reindex(index=all_days, columns=tickers).ffill().bfill().to_numpy(dtype=object) \
        if "market" in px.columns else np.full(open_raw.shape, "", dtype=object)
    last_bar = px.groupby("ticker")["date"].max().reindex(tickers)
    last_pos = np.array([all_days.searchsorted(d, side="right") - 1 for d in last_bar])
    pos_of = {d: i for i, d in enumerate(all_days)}
    tick_arr = np.array(tickers, dtype=object)

    fills, unfilled = {}, []
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
    integer = "integer_shares" in on
    sh = np.zeros(n)                              # shares held
    cash = init_cash
    nav = init_cash
    last_close = close_raw[0].copy()              # raw close of the previous bar, in the share units of that bar
    last_factor = factor[0].copy()
    delist_policy = None
    daily_rows = [{"date": start, "nav": 1.0, "ret": 0.0, "cost": 0.0, "nav_krw": init_cash, "cash": init_cash, "n_holdings": 0, "turnover": 0.0}]
    trade_frames, position_frames, delisted_rows = [], [], []

    for d in days:
        i = pos_of[d]
        o, c, F = open_raw[i], close_raw[i], factor[i]
        # 1. corporate actions: shares follow the factor, the previous close is restated in today's share units
        r = np.where(np.isfinite(F) & np.isfinite(last_factor) & (last_factor > 0), F / np.where(last_factor > 0, last_factor, 1.0), 1.0)
        ref = last_close / r                      # reference price = previous close / r
        if (np.abs(r - 1.0) > 1e-12).any():
            sh = sh * r
            if integer:
                whole = C.floor_shares(sh)
                cash += float(np.nansum((sh - whole) * np.nan_to_num(ref)))     # fractional shares are paid out in cash
                sh = whole
        held = sh > EPS
        # 2. delistings
        gone = held & (i > last_pos)
        if gone.any():
            delist_policy = delist_policy or _delist_policy(cfg)
            for j in np.flatnonzero(gone):
                v = float(sh[j] * ref[j])
                delisted_rows.append({"date": d, "ticker": tickers[j], "weight": v / nav, "last_bar": last_bar.iloc[j], "policy": delist_policy})
                if delist_policy == "last_close":
                    cash += v
            sh[gone] = 0.0
            held = sh > EPS
        c_eff = np.where(np.isnan(c), ref, c)
        day_cost = 0.0
        turnover = 0.0
        if d in fills:
            s = fills[d]
            tgt = tgt_by_sig[s]
            has_open = np.isfinite(o) & (o > 0)
            p_open = np.where(has_open, o, c_eff)
            nav_open = cash + float(np.nansum(np.where(held, sh * p_open, 0.0)))
            # 3. orders
            t_ser = tgt.reindex(tickers)
            present = np.isin(tick_arr, tgt.index.to_numpy(dtype=object))
            hold_mask = present & t_ser.isna().to_numpy()
            t_arr = t_ser.fillna(0.0).to_numpy(dtype=float)
            candidate = (present | held) & ~hold_mask
            with np.errstate(divide="ignore", invalid="ignore"):
                tgt_sh = np.where(has_open, nav_open * t_arr / np.where(has_open, o, 1.0), sh)
            if integer:
                tgt_sh = np.where(has_open, C.floor_shares(tgt_sh), sh)
            delta = np.where(candidate, tgt_sh - sh, 0.0)
            wants = candidate & ~has_open & (np.abs(nav_open * t_arr - sh * p_open) > EPS)     # an order would exist if there were a price
            is_order = (candidate & (np.abs(delta) > EPS)) | wants
            buy = np.where(has_open, delta > 0, nav_open * t_arr > sh * p_open)
            status = np.full(n, "filled", dtype=object)
            rej_halt = is_order & halted[i] if "halt" in on else np.zeros(n, dtype=bool)
            no_px = is_order & ~has_open & ~rej_halt
            status[no_px] = "no_price"; status[rej_halt] = "rejected_halt"
            blocked = no_px | rej_halt
            if "price_limit" in on:
                base = C.base_price(last_close, r)
                ok = np.isfinite(base) & (base > 0)
                upper, lower = C.limit_prices(np.where(ok, base, 1.0), pct, tick_table)
                rej_lim = is_order & ~blocked & ok & ((buy & (o >= upper)) | (~buy & (o <= lower)))
                status[rej_lim] = "rejected_limit"
                blocked |= rej_lim
            q = np.where(is_order & ~blocked, np.abs(delta), 0.0)
            if "liquidity" in on:
                capq = C.liquidity_cap(q, np.where(has_open, o, 0.0), value[i], max_part)
                if integer:
                    capq = C.floor_shares(capq)
                cut = q > capq + EPS
                status[cut] = "partial_liquidity"
                q = np.minimum(q, capq)
            if "costs" in on:
                comp = cost_model.components(d, market_by_day[i])
                c_buy, c_sell, slip, tax = comp["commission_buy"], comp["commission_sell"], comp["slippage"], comp["tax"]
            else:
                c_buy = c_sell = slip = 0.0
                tax = np.zeros(n)
            o0 = np.where(has_open, o, 0.0)
            # 4. sells
            sell_q = np.where(~buy, q, 0.0)
            val_s = sell_q * o0 * (1.0 - slip)
            comm_s, tax_s = val_s * c_sell, val_s * tax
            cash += float((val_s - comm_s - tax_s).sum())
            sh = sh - sell_q
            # 5. buys
            buy_q = np.where(buy, q, 0.0)
            price_b = o0 * (1.0 + slip)
            # base rule shared with v1: buys fit in the value that is not invested after the sells (kept positions included)
            room = nav_open - float(np.nansum(np.where(sh > EPS, sh * p_open, 0.0)))
            k0 = C.cash_scale(buy_q * o0, room + ROOM_TOL * nav_open)
            if k0 < 1.0:
                status[(buy_q > EPS) & (status == "filled")] = "scaled"
                buy_q = buy_q * k0
                if integer:
                    buy_q = C.floor_shares(buy_q)
            if "cash" in on:
                k = C.cash_scale(buy_q * price_b * (1.0 + c_buy), cash)
                if k < 1.0:
                    scaled = buy_q > EPS
                    buy_q = buy_q * k
                    if integer:
                        buy_q = C.floor_shares(buy_q)
                    status[scaled] = "scaled_cash"
            val_b = buy_q * price_b
            comm_b = val_b * c_buy
            cash -= float((val_b + comm_b).sum())
            if "cash" in on and -1e-6 * init_cash < cash < 0:        # float noise of a fully scaled book
                cash = 0.0
            sh = sh + buy_q
            sh[np.abs(sh) < EPS] = 0.0
            filled = np.where(buy, buy_q, sell_q)
            slip_amt = filled * o0 * slip
            comm = np.where(buy, comm_b, comm_s)
            day_cost = float(comm.sum() + tax_s.sum() + slip_amt.sum())
            turnover = float((val_b.sum() + val_s.sum()) / 2 / nav_open) if nav_open > 0 else 0.0
            idx = np.flatnonzero(is_order)
            if len(idx):
                trade_frames.append(pd.DataFrame({
                    "date": d, "signal_date": s, "ticker": tick_arr[idx], "side": np.where(buy[idx], "buy", "sell"),
                    "target_shares": tgt_sh[idx], "filled_shares": filled[idx],
                    "price": np.where(buy[idx], price_b[idx], (o0 * (1.0 - slip))[idx]), "value": np.where(buy[idx], val_b[idx], val_s[idx]),
                    "commission": comm[idx], "tax": np.where(buy[idx], 0.0, tax_s[idx]), "slippage": slip_amt[idx], "status": status[idx]}))
        # 6. valuation at the close
        held = sh > EPS
        pos_val = np.where(held, sh * np.nan_to_num(c_eff), 0.0)
        nav_close = cash + float(pos_val.sum())
        ret = nav_close / nav - 1.0
        nav = nav_close
        last_close = c_eff
        last_factor = np.where(np.isfinite(F), F, last_factor)
        daily_rows.append({"date": d, "nav": nav / init_cash, "ret": ret, "cost": day_cost / init_cash, "nav_krw": nav, "cash": cash,
                           "n_holdings": int(held.sum()), "turnover": turnover})
        if held.any():
            position_frames.append(pd.DataFrame({"date": d, "ticker": tick_arr[held], "shares": sh[held], "close": c_eff[held], "value": pos_val[held], "cash": cash}))

    daily = pd.DataFrame(daily_rows)
    nav_df = validate_nav(daily[["date", "nav", "ret", "cost"]])
    trades = pd.concat(trade_frames, ignore_index=True)[TRADE_COLUMNS] if trade_frames else pd.DataFrame(columns=TRADE_COLUMNS)
    if len(trades):
        assert_fill_after_signal(trades, fill_col="date")
    positions = pd.concat(position_frames, ignore_index=True) if position_frames else pd.DataFrame(columns=POSITION_COLUMNS)
    delisted = pd.DataFrame(delisted_rows, columns=["date", "ticker", "weight", "last_bar", "policy"])
    return {"nav": nav_df, "daily": daily, "trades": trades, "positions": positions, "delisted": delisted, "scenario": C.scenario_name(on),
            "unfilled_signal_dates": [pd.Timestamp(s) for s in unfilled], "delist_policy_used": delist_policy}
