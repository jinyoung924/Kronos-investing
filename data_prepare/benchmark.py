"""Benchmark series: the KOSPI index level (ticker "KOSPI") and optional ETFs, in the price format."""
from __future__ import annotations

import numpy as np
import pandas as pd

from data_prepare.transform import parse_number, short_code


def index_prices(raw_idx: pd.DataFrame, index_name: str = "코스피", ticker: str = "KOSPI") -> pd.DataFrame:
    if raw_idx.empty:
        return pd.DataFrame()
    r = raw_idx[raw_idx["IDX_NM"].astype(str).str.strip() == index_name]
    out = pd.DataFrame({
        "date": pd.to_datetime(r["date"]).dt.normalize(), "ticker": ticker,
        "open": r["OPNPRC_IDX"].map(parse_number), "high": r["HGPRC_IDX"].map(parse_number),
        "low": r["LWPRC_IDX"].map(parse_number), "close": r["CLSPRC_IDX"].map(parse_number),
        "volume": r["ACC_TRDVOL"].map(parse_number), "adj_factor": 1.0, "name": index_name, "market": "KR",
    })
    return out.sort_values("date").reset_index(drop=True)


def etf_prices(raw_etf: pd.DataFrame, codes: list[str]) -> pd.DataFrame:
    if raw_etf.empty or not codes:
        return pd.DataFrame()
    r = raw_etf[raw_etf["ISU_CD"].map(short_code).isin(codes)]
    out = pd.DataFrame({
        "date": pd.to_datetime(r["date"]).dt.normalize(), "ticker": r["ISU_CD"].map(short_code),
        "open": r["TDD_OPNPRC"].map(parse_number), "high": r["TDD_HGPRC"].map(parse_number),
        "low": r["TDD_LWPRC"].map(parse_number), "close": r["TDD_CLSPRC"].map(parse_number),
        "volume": r["ACC_TRDVOL"].map(parse_number), "adj_factor": 1.0,
        "name": r["ISU_NM"].astype(str).str.strip(), "market": "KR",
    })
    for c in ("open", "high", "low"):
        out.loc[out[c] <= 0, c] = np.nan
    # the ETF endpoint answers on holidays too, with empty price strings -> drop those rows
    out = out[out["close"].notna() & (out["close"] > 0)]
    return out.sort_values(["ticker", "date"]).drop_duplicates(["ticker", "date"]).reset_index(drop=True)
