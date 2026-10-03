"""Stage 1: point-in-time universe (date, ticker, market) from the collected constituent snapshots.

Input: data/universe/constituents_{index}[_{variant}].parquet per index key (date, ticker), one snapshot per
trading day (docs/data_pipeline.md §4). The exchanges are pooled; `market` is taken from the prepared prices on
the same (date, ticker), so a transferred ticker carries the exchange it traded on that day (D-2: no
index -> market mapping key is read). `top_n_mktcap` keeps only the N largest names per date by
close * listed_shares, both known at that date's close (universe.top_n_mktcap, paper CSI 300 analogue).
Pure functions only; file IO lives in run_prepare.py.
"""
from __future__ import annotations

import pandas as pd

from common.universe import normalize_constituents


def build_universe(constituents_by_index: dict[str, pd.DataFrame], prices: pd.DataFrame,
                   top_n_mktcap: int | None = None) -> pd.DataFrame:
    frames = [normalize_constituents(df) for df in constituents_by_index.values()]
    if not frames:
        raise ValueError("no constituents given")
    uni = pd.concat(frames, ignore_index=True).drop_duplicates(["date", "ticker"])
    cols = ["date", "ticker", "market", "close", "listed_shares"]
    px = prices[[c for c in cols if c in prices.columns]]
    merged = uni.merge(px, on=["date", "ticker"], how="left", validate="one_to_one")
    no_bar = merged["market"].isna()
    if no_bar.any():
        ex = merged.loc[no_bar, ["date", "ticker"]].head(5).to_dict("records")
        raise ValueError(f"{int(no_bar.sum())} universe rows have no price bar on their snapshot date, e.g. {ex}")
    if top_n_mktcap is not None:
        n = int(top_n_mktcap)
        if n < 1:
            raise ValueError("top_n_mktcap must be >= 1")
        if "listed_shares" not in merged.columns or merged["listed_shares"].isna().any():
            raise ValueError("top_n_mktcap needs listed_shares for every universe row")
        merged["mktcap"] = merged["close"] * merged["listed_shares"]
        merged = (merged.sort_values(["date", "mktcap", "ticker"], ascending=[True, False, True])
                        .groupby("date", sort=False).head(n))
    out = merged[["date", "ticker", "market"]].sort_values(["date", "ticker"]).reset_index(drop=True)
    return out


def members_per_date(universe: pd.DataFrame) -> pd.DataFrame:
    """DataFrame(date, n_members, n_<market>...) for diagnostics and the user-check chart."""
    tot = universe.groupby("date").size().rename("n_members")
    by_m = universe.groupby(["date", "market"]).size().unstack(fill_value=0)
    by_m.columns = [f"n_{str(c).lower()}" for c in by_m.columns]
    return pd.concat([tot, by_m], axis=1).reset_index()
