"""Stage 1: collected per-exchange price files -> one long prepared price table (raw prices).

Input  (per index key): data/raw/{index}/prices.parquet as written by `A_data_prepare.run build`
        date, ticker, open, high, low, close, volume, adj_factor, exchange, name, sect, trdval, mktcap,
        list_shrs, chg, fluc_rt, halted
Output: common.schema.PRICE_COLUMNS (+ listed_shares)
        date, ticker, market, open, high, low, close, volume, value, listed_shares
Prices stay RAW (unadjusted). Halted days keep NaN open/high/low (nothing printed) and the reference close.
Pure functions only; file IO lives in run_prepare.py.
"""
from __future__ import annotations

import pandas as pd

from common.schema import validate_prices

RAW_TO_PREPARED = {"exchange": "market", "trdval": "value", "list_shrs": "listed_shares"}
OUTPUT_COLUMNS = ["date", "ticker", "market", "open", "high", "low", "close", "volume", "value", "listed_shares"]


def build_prices(raw_by_index: dict[str, pd.DataFrame], markets: list[str] | None = None) -> pd.DataFrame:
    """Concatenate the exchanges, rename to the spec columns and validate.

    markets: allowed `market` labels (configs data.markets, D-2). Any other label raises."""
    frames = []
    for idx, raw in raw_by_index.items():
        missing = [c for c in ("date", "ticker", "open", "high", "low", "close", "volume", "trdval") if c not in raw.columns]
        if missing:
            raise ValueError(f"raw price file for {idx} lacks columns {missing}")
        df = raw.rename(columns=RAW_TO_PREPARED).copy()
        if "market" not in df.columns:
            df["market"] = idx.upper()
        if "listed_shares" not in df.columns:
            df["listed_shares"] = float("nan")
        frames.append(df[OUTPUT_COLUMNS])
    if not frames:
        raise ValueError("no raw price frames given")
    out = pd.concat(frames, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"]).dt.normalize()
    out["ticker"] = out["ticker"].astype(str)
    out["market"] = out["market"].astype(str).str.upper()
    if markets is not None:
        bad = sorted(set(out["market"]) - set(markets))
        if bad:
            raise ValueError(f"market labels {bad} are not in data.markets {list(markets)}")
    out = out.sort_values(["ticker", "date"]).reset_index(drop=True)
    return validate_prices(out)


def listing_span(prices: pd.DataFrame) -> pd.DataFrame:
    """Per ticker: first_date, last_date, n_rows (used by diagnostics and the universe builder)."""
    g = prices.groupby("ticker")["date"].agg(first_date="min", last_date="max", n_rows="size")
    return g.reset_index()
