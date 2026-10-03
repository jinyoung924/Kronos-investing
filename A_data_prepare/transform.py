"""Raw KRX rows -> long price frame in the project format, with adj_factor.

Columns produced (data/raw/kospi/prices.parquet):
    date, ticker, name, open, high, low, close, volume, trdval, mktcap, list_shrs,
    chg (전일대비), halted (bool), adj_factor

Rules
-----
* KRX numbers are strings with thousands separators; "" / "-" -> NaN.
* A day with no trade (거래정지 or simply no fill) comes back with open/high/low = 0 and
  volume = 0 while close carries the reference price. open/high/low are set to NaN and
  `halted=True`: the engine cannot fill at a price that never printed.
* adj_factor: KRX publishes CMPPREVDD_PRC (close - 기준가). When the reference price is
  reset for a corporate action (액면분할/병합, 무상증자, 유상증자 권리락, 감자 ...),
  close - CMPPREVDD_PRC != previous close. ratio = (close - chg) / prev_close is the
  price adjustment for everything before that day; adj_factor[t] = prod(ratio of events after t).
  Cash dividends do not reset the reference price in Korea and are NOT in adj_factor.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

RAW_NUM = {
    "TDD_OPNPRC": "open", "TDD_HGPRC": "high", "TDD_LWPRC": "low", "TDD_CLSPRC": "close",
    "ACC_TRDVOL": "volume", "ACC_TRDVAL": "trdval", "MKTCAP": "mktcap", "LIST_SHRS": "list_shrs",
    "CMPPREVDD_PRC": "chg", "FLUC_RT": "fluc_rt",
}


def parse_number(s) -> float:
    if s is None:
        return np.nan
    s = str(s).replace(",", "").strip()
    if s in ("", "-", "None", "null"):
        return np.nan
    try:
        return float(s)
    except ValueError:
        return np.nan


def short_code(isu_cd: str) -> str:
    """ISU_CD is the 6-digit short code in the daily endpoints; 12-char ISIN in base info."""
    s = str(isu_cd).strip()
    return s[3:9] if len(s) == 12 else s


def rows_to_frame(rows_by_date: dict[str, list[dict]]) -> pd.DataFrame:
    frames = []
    for d, rows in rows_by_date.items():
        if not rows:
            continue
        df = pd.DataFrame(rows)
        df["date"] = pd.Timestamp(d)
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def stock_prices(raw: pd.DataFrame) -> pd.DataFrame:
    """KRX stk_bydd_trd rows (all dates) -> long OHLCV frame (no adj_factor yet)."""
    if raw.empty:
        return pd.DataFrame(columns=["date", "ticker", "name", "exchange", "sect", *RAW_NUM.values(), "halted"])
    out = pd.DataFrame({
        "date": pd.to_datetime(raw["date"]).dt.normalize(),
        "ticker": raw["ISU_CD"].map(short_code),
        "name": raw["ISU_NM"].astype(str).str.strip(),
        "exchange": raw["MKT_NM"].astype(str).str.strip().str.upper() if "MKT_NM" in raw.columns else "KOSPI",
        "sect": raw["SECT_TP_NM"].astype(str).str.strip() if "SECT_TP_NM" in raw.columns else "",
    })
    for k, c in RAW_NUM.items():
        out[c] = raw[k].map(parse_number) if k in raw.columns else np.nan
    no_trade = (out["volume"].fillna(0) == 0) | (out["open"].fillna(0) <= 0)
    out["halted"] = no_trade
    for c in ("open", "high", "low"):
        out.loc[no_trade | (out[c] <= 0), c] = np.nan
    out.loc[out["close"] <= 0, "close"] = np.nan
    out = out.sort_values(["ticker", "date"]).drop_duplicates(["ticker", "date"], keep="last")
    return out.reset_index(drop=True)


def detect_reference_price_events(prices: pd.DataFrame, min_abs: float = 1.0, min_rel: float = 1e-4) -> pd.DataFrame:
    """Rows where close - chg (= today's reference price) differs from the previous close.
    Returns (date, ticker, prev_close, base_price, ratio)."""
    p = prices.sort_values(["ticker", "date"])
    prev_close = p.groupby("ticker")["close"].shift(1)
    base = p["close"] - p["chg"]
    ok = prev_close.notna() & base.notna() & p["chg"].notna() & (prev_close > 0)
    diff = (base - prev_close).abs()
    hit = ok & (diff >= min_abs) & (diff / prev_close >= min_rel)
    ev = pd.DataFrame({
        "date": p.loc[hit, "date"], "ticker": p.loc[hit, "ticker"], "name": p.loc[hit, "name"],
        "prev_close": prev_close[hit], "base_price": base[hit],
    })
    ev["ratio"] = ev["base_price"] / ev["prev_close"]
    return ev.reset_index(drop=True)


def compute_adj_factor(prices: pd.DataFrame, events: pd.DataFrame | None = None,
                       ratio_bounds: tuple[float, float] = (0.01, 100.0)) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Attach adj_factor (=1 on the last row of every ticker; earlier rows scaled by the product
    of ratios of later events). Events with a ratio outside `ratio_bounds` are flagged and ignored."""
    if events is None:
        events = detect_reference_price_events(prices)
    events = events.copy()
    lo, hi = ratio_bounds
    events["applied"] = (events["ratio"] >= lo) & (events["ratio"] <= hi)
    p = prices.sort_values(["ticker", "date"]).copy()
    p["adj_factor"] = 1.0
    if not events.empty:
        applied = events[events["applied"]]
        # ratio applies to all rows strictly BEFORE the event date -> reverse cumulative product
        key = pd.MultiIndex.from_frame(p[["ticker", "date"]])
        ev_ratio = pd.Series(applied["ratio"].to_numpy(), index=pd.MultiIndex.from_frame(applied[["ticker", "date"]]))
        r = ev_ratio.reindex(key).fillna(1.0).to_numpy()
        # factor[t] = prod_{s > t} ratio_s  (per ticker)
        log_r = np.log(r)
        g = p["ticker"].to_numpy()
        # reverse cumsum within ticker, excluding own row
        rev = pd.Series(log_r[::-1]).groupby(g[::-1]).cumsum().to_numpy()[::-1]
        p["adj_factor"] = np.exp(rev - log_r)
    return p.reset_index(drop=True), events


def is_common_stock(ticker: str) -> bool:
    """KRX 6-digit codes: last digit 0 = 보통주; 5/7/9/K/L/M... = 우선주 and other classes."""
    t = str(ticker)
    return len(t) == 6 and t[-1] == "0"


def filter_securities(prices: pd.DataFrame, common_only: bool = True,
                      exclude_name_patterns: list[str] | None = None,
                      exclude_sect_patterns: list[str] | None = None) -> tuple[pd.DataFrame, dict]:
    """Drop preferred shares, names matching patterns (e.g. 스팩) and KOSDAQ 소속부 labels matching
    patterns (e.g. SPAC, 관리종목). A ticker is dropped for ALL dates once it matches on any date, so
    the same security cannot flip in and out of the sample. Returns (frame, counts)."""
    drop = pd.Series(False, index=prices.index)
    stats = {}
    if common_only:
        m = ~prices["ticker"].map(is_common_stock)
        stats["dropped_non_common"] = int(prices.loc[m, "ticker"].nunique())
        drop |= m
    for pat in exclude_name_patterns or []:
        hit = prices["name"].str.contains(pat, regex=True, na=False)
        m = prices["ticker"].isin(prices.loc[hit, "ticker"].unique())
        stats[f"dropped_name~{pat}"] = int(prices.loc[m, "ticker"].nunique())
        drop |= m
    if exclude_sect_patterns and "sect" in prices.columns:
        for pat in exclude_sect_patterns:
            hit = prices["sect"].str.contains(pat, regex=True, na=False)
            m = prices["ticker"].isin(prices.loc[hit, "ticker"].unique())
            stats[f"dropped_sect~{pat}"] = int(prices.loc[m, "ticker"].nunique())
            drop |= m
    return prices[~drop].reset_index(drop=True), stats


def to_project_format(prices: pd.DataFrame) -> pd.DataFrame:
    cols = ["date", "ticker", "open", "high", "low", "close", "volume", "adj_factor",
            "exchange", "name", "sect", "trdval", "mktcap", "list_shrs", "chg", "fluc_rt", "halted"]
    return prices[[c for c in cols if c in prices.columns]].sort_values(["ticker", "date"]).reset_index(drop=True)
