"""Stage 1: forward cumulative adjustment factor F and the events table behind it.

The collected file stores a BACKWARD factor (last row 1, earlier rows multiplied by the ratios of
later events) and data/raw/{index}/adjustment_events.csv with
    date, ticker, name, prev_close, base_price, ratio (= 조정 후 기준가 / 조정 전 기준가, 2:1 split -> 0.5), applied.
Here F starts at 1.0 on each ticker's first row and changes only on an applied ex-date:
    F_t = F_{t-1} * r_t,   r_t = 1 / ratio_t   (r_t = 1 when there is no event)
so F_t only depends on events dated <= t (no lookahead). Adjusted price = raw * F, and for any two dates
F_t / F_s equals adj_factor_t / adj_factor_s of the collected file (tested in tests/test_stage1_data.py).
Pure functions only; file IO lives in run_prepare.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

EVENT_COLUMNS = ["ticker", "ex_date", "ratio", "r", "applied", "source", "name", "prev_close", "base_price"]
EVENT_SOURCE = "krx_reference_price"   # CMPPREVDD_PRC-based reference-price reset (docs/data_pipeline.md §5-2)


def build_events(raw_events: pd.DataFrame) -> pd.DataFrame:
    """Normalise the collected adjustment_events.csv rows into the Stage 1 events table."""
    need = {"date", "ticker", "ratio", "applied"}
    if not need.issubset(raw_events.columns):
        raise ValueError(f"events need columns {sorted(need)}")
    ev = pd.DataFrame({
        "ticker": raw_events["ticker"].astype(str),
        "ex_date": pd.to_datetime(raw_events["date"]).dt.normalize(),
        "ratio": raw_events["ratio"].astype(float),
        "applied": raw_events["applied"].astype(bool),
        "source": EVENT_SOURCE,
        "name": raw_events["name"].astype(str) if "name" in raw_events.columns else "",
        "prev_close": raw_events["prev_close"].astype(float) if "prev_close" in raw_events.columns else np.nan,
        "base_price": raw_events["base_price"].astype(float) if "base_price" in raw_events.columns else np.nan,
    })
    if (ev["ratio"] <= 0).any() or not np.isfinite(ev["ratio"]).all():
        raise ValueError("event ratio must be finite and > 0")
    ev["r"] = np.where(ev["applied"], 1.0 / ev["ratio"], 1.0)
    ev = ev.sort_values(["ticker", "ex_date"]).drop_duplicates(["ticker", "ex_date"], keep="last").reset_index(drop=True)
    return ev[EVENT_COLUMNS]


def build_adj_factor(prices: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """DataFrame(date, ticker, factor): forward cumulative F per ticker from the applied events."""
    p = prices[["date", "ticker"]].sort_values(["ticker", "date"]).reset_index(drop=True)
    if p.duplicated().any():
        raise ValueError("prices have duplicate (date, ticker) rows")
    applied = events[events["applied"]]
    key = pd.MultiIndex.from_frame(p[["ticker", "date"]])
    r = pd.Series(applied["r"].to_numpy(), index=pd.MultiIndex.from_arrays([applied["ticker"], applied["ex_date"]]))
    missing = ~r.index.isin(key)
    if missing.any():
        bad = r.index[missing][:5].tolist()
        raise ValueError(f"{int(missing.sum())} applied events fall on dates without a price row, e.g. {bad}")
    r_rows = r.reindex(key).fillna(1.0).to_numpy()
    log_f = pd.Series(np.log(r_rows)).groupby(p["ticker"].to_numpy(), sort=False).cumsum().to_numpy()
    out = p.assign(factor=np.exp(log_f))
    if not np.isfinite(out["factor"]).all() or (out["factor"] <= 0).any():
        raise ValueError("non-finite or non-positive adjustment factor")
    return out


def adjusted_close(prices: pd.DataFrame, adj_factor: pd.DataFrame) -> pd.Series:
    """raw close * F aligned to `prices` rows (same (ticker, date) order)."""
    f = adj_factor.set_index(["ticker", "date"])["factor"]
    idx = pd.MultiIndex.from_frame(prices[["ticker", "date"]])
    return (prices["close"].to_numpy() * f.reindex(idx).to_numpy())


def unexplained_moves(prices: pd.DataFrame, adj_factor: pd.DataFrame, halts: pd.DataFrame,
                      max_move: float, delist_window: int = 14) -> pd.DataFrame:
    """Rows whose adjusted close-to-close simple return exceeds +-max_move, classified.

    Excluded (not listed): a ticker's first row (new listing) and days whose previous row was halted (halt
    resumption, where the reference close may be stale). Categories of the rest:
      liquidation  - within `delist_window` trading days of the ticker's last row, which precedes the
                     calendar end (정리매매: no price limit, docs/data_pipeline.md §4)
      unexplained  - everything else; each needs a judgement in the Stage report."""
    p = prices[["date", "ticker", "close"]].sort_values(["ticker", "date"]).reset_index(drop=True)
    p["adj_close"] = adjusted_close(p, adj_factor)
    h = halts.set_index(["ticker", "date"])["is_halted"]
    p["is_halted"] = h.reindex(pd.MultiIndex.from_frame(p[["ticker", "date"]])).to_numpy()
    g = p.groupby("ticker", sort=False)
    p["prev_date"] = g["date"].shift(1)
    p["prev_halted"] = g["is_halted"].shift(1)
    p["ret"] = g["adj_close"].pct_change()
    p["rows_to_last"] = g.cumcount(ascending=False)
    last = g["date"].transform("max")
    cal_end = p["date"].max()
    hit = (p["ret"].abs() > max_move) & p["prev_date"].notna() & (p["prev_halted"] == False)  # noqa: E712
    out = p.loc[hit, ["date", "ticker", "prev_date", "ret", "rows_to_last"]].copy()
    out["last_date"] = last[hit].to_numpy()
    out["category"] = np.where((out["last_date"] < cal_end) & (out["rows_to_last"] < delist_window), "liquidation", "unexplained")
    return out.sort_values(["category", "ticker", "date"]).reset_index(drop=True)
