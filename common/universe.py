"""Point-in-time index membership.

data/universe/constituents_{index}.parquet has columns (date, ticker). Each distinct `date`
is a membership snapshot valid from that date until the next snapshot. `universe_at(d)` uses
the latest snapshot dated <= d, so a stock that enters later is invisible, and one that is
delisted later is still present (no survivorship filtering).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from common.config import cfg_get
from common.paths import Paths


def load_constituents(cfg: dict, root: str | Path = ".", indices=None) -> dict[str, pd.DataFrame]:
    paths = Paths(cfg, root)
    indices = list(indices) if indices is not None else list(cfg_get(cfg, "universe.indices", []))
    out = {}
    for idx in indices:
        f = paths.constituents_file(idx)
        if not f.exists():
            raise FileNotFoundError(f"missing {f}; provide (date, ticker) snapshots there")
        out[idx] = normalize_constituents(pd.read_parquet(f))
    return out


def normalize_constituents(df: pd.DataFrame) -> pd.DataFrame:
    if not {"date", "ticker"}.issubset(df.columns):
        raise ValueError("constituents need columns (date, ticker)")
    out = df[["date", "ticker"]].copy()
    out["date"] = pd.to_datetime(out["date"]).dt.normalize()
    out["ticker"] = out["ticker"].astype(str)
    return out.drop_duplicates().sort_values(["date", "ticker"]).reset_index(drop=True)


def universe_at(constituents: pd.DataFrame, date) -> list[str]:
    """Members according to the latest snapshot dated <= date. Empty list if none."""
    date = pd.Timestamp(date).normalize()
    snap_dates = constituents["date"].unique()
    valid = snap_dates[snap_dates <= date]
    if len(valid) == 0:
        return []
    snap = valid.max()
    return sorted(constituents.loc[constituents["date"] == snap, "ticker"].tolist())


def combined_universe_at(constituents_by_index: dict[str, pd.DataFrame], date) -> pd.DataFrame:
    """DataFrame(ticker, index) of all members across indices at `date`."""
    rows = []
    for idx, cons in constituents_by_index.items():
        for t in universe_at(cons, date):
            rows.append((t, idx))
    df = pd.DataFrame(rows, columns=["ticker", "index"])
    return df.drop_duplicates("ticker").reset_index(drop=True)


def membership_matrix(constituents_by_index: dict[str, pd.DataFrame], dates) -> pd.DataFrame:
    """Boolean date x ticker matrix of membership evaluated point-in-time at each date."""
    dates = pd.DatetimeIndex(dates)
    all_tickers = sorted({t for c in constituents_by_index.values() for t in c["ticker"].unique()})
    mat = pd.DataFrame(False, index=dates, columns=all_tickers)
    for d in dates:
        members = combined_universe_at(constituents_by_index, d)["ticker"]
        mat.loc[d, members] = True
    return mat


def all_tickers(constituents_by_index: dict[str, pd.DataFrame], start=None, end=None) -> dict[str, list[str]]:
    """Every ticker that is a member of any snapshot relevant to [start, end] (for downloading)."""
    out = {}
    for idx, cons in constituents_by_index.items():
        c = cons
        if start is not None:
            snaps = c["date"].unique()
            before = snaps[snaps <= pd.Timestamp(start)]
            lo = before.max() if len(before) else c["date"].min()
            c = c[c["date"] >= lo]
        if end is not None:
            c = c[c["date"] <= pd.Timestamp(end)]
        out[idx] = sorted(c["ticker"].unique().tolist())
    return out
