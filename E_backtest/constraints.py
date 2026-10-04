"""Execution constraints of engine v2 (docs/spec.md Stage 7 "제약별 규칙"). Pure numpy functions, one per rule;
the engine decides which ones are switched on (configs backtest.v2.constraints).

    costs           CostModel.components (E_backtest/costs.py)
    integer_shares  floor_shares
    cash            cash_scale
    price_limit     limit_prices + the reference price base_price (previous raw close / r on an ex-date)
    halt            the halts table (no function: a halted ticker's order is rejected)
    liquidity       liquidity_cap
"""
from __future__ import annotations

import numpy as np

from common.config import ConfigError

CONSTRAINTS = ("costs", "integer_shares", "cash", "price_limit", "halt", "liquidity")     # canonical order of scenario names
SHARE_TOL = 1e-9          # a share count this close below an integer is that integer (float noise of nav * w / price)


def scenario_name(enabled) -> str:
    """'all_off' or the enabled constraints joined with '+', in canonical order."""
    on = [c for c in CONSTRAINTS if c in set(enabled)]
    unknown = sorted(set(enabled) - set(CONSTRAINTS))
    if unknown:
        raise ValueError(f"unknown constraints {unknown}; known: {CONSTRAINTS}")
    return "+".join(on) if on else "all_off"


def parse_scenario(name: str) -> list[str]:
    name = (name or "").strip()
    if name in ("", "all_off"):
        return []
    on = [c.strip() for c in name.replace(",", "+").split("+") if c.strip()]
    scenario_name(on)       # validates
    return [c for c in CONSTRAINTS if c in on]


def floor_shares(shares: np.ndarray) -> np.ndarray:
    """Round share counts down to whole shares."""
    return np.floor(np.asarray(shares, dtype=float) + SHARE_TOL)


def tick_size(price: np.ndarray, tick_table: list[dict]) -> np.ndarray:
    """Tick of each price: the `tick` of the last row whose min_price <= price (rows sorted by min_price)."""
    if not tick_table:
        raise ConfigError("backtest.v2.tick_table is empty: fill [{min_price, tick}, ...] in configs/base.yaml (exchange tick sizes, user-verified)")
    rows = sorted(((float(r["min_price"]), float(r["tick"])) for r in tick_table))
    mins = np.array([m for m, _ in rows]); ticks = np.array([t for _, t in rows])
    idx = np.clip(np.searchsorted(mins, np.asarray(price, dtype=float), side="right") - 1, 0, len(rows) - 1)
    return ticks[idx]


def limit_prices(base_price: np.ndarray, pct: float, tick_table: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """(upper, lower) daily price limits from the reference price: base * (1 +- pct), the upper limit rounded DOWN and
    the lower limit rounded UP to the tick of that price level, so both stay inside the band."""
    base = np.asarray(base_price, dtype=float)
    up_raw, lo_raw = base * (1.0 + pct), base * (1.0 - pct)
    t_up, t_lo = tick_size(up_raw, tick_table), tick_size(lo_raw, tick_table)
    return np.floor(up_raw / t_up + SHARE_TOL) * t_up, np.ceil(lo_raw / t_lo - SHARE_TOL) * t_lo


def base_price(prev_close: np.ndarray, r: np.ndarray) -> np.ndarray:
    """Reference price of the day: previous raw close, divided by the share multiplier r on an ex-date (r = 1 otherwise)."""
    return np.asarray(prev_close, dtype=float) / np.asarray(r, dtype=float)


def liquidity_cap(order_shares: np.ndarray, price: np.ndarray, traded_value: np.ndarray, max_participation: float) -> np.ndarray:
    """Shares that fit in traded_value * max_participation at `price` (never more than the order)."""
    cap_value = np.nan_to_num(np.asarray(traded_value, dtype=float), nan=0.0) * float(max_participation)
    with np.errstate(divide="ignore", invalid="ignore"):
        cap_shares = np.where(price > 0, cap_value / price, 0.0)
    return np.minimum(np.asarray(order_shares, dtype=float), cap_shares)


def cash_scale(buy_cash_needed: np.ndarray, cash: float) -> float:
    """Common factor (<= 1) that makes the buys fit in the available cash; 1 when they already fit."""
    need = float(np.sum(buy_cash_needed))
    if need <= 0 or need <= cash:
        return 1.0
    return max(cash, 0.0) / need
