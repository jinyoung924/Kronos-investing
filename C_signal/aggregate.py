"""Collapse prediction sample paths into per-(as_of_date, ticker) signals (docs/outline.md C_signal table).

    exp_ret      = mean_s(pred_close[H]) / last_close - 1            (step H only)
    exp_ret_mean = mean_{s, h=1..H}(pred_close) / last_close - 1     (paper signal R_{t->t+H})
    std          = std_s(pred_close[H]) / last_close                 (population std, ddof 0)
    p_up         = share of samples with pred_close[H] > last_close
    pred_range   = mean_{s, h}(pred_high - pred_low) / last_close
    n_samples    = samples used = signal.n_samples (D-11: the first n_samples sample_ids, ascending; fewer -> error)
last_close is the as_of_date close on the same price basis as the predictions (D-1: raw). Pure functions only.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from common.schema import validate_predictions, validate_signals

KEY = ["as_of_date", "ticker"]


def select_samples(preds: pd.DataFrame, n_samples: int) -> pd.DataFrame:
    """Keep, per (as_of_date, ticker), the rows of the first n_samples distinct sample_ids. Raise when fewer exist."""
    n_samples = int(n_samples)
    if n_samples < 1:
        raise ValueError("n_samples must be >= 1")
    counts = preds.groupby(KEY)["sample_id"].nunique()
    short = counts[counts < n_samples]
    if len(short):
        ex = short.head(3).reset_index().to_dict("records")
        raise ValueError(f"{len(short)} (as_of_date, ticker) groups have fewer than n_samples={n_samples} samples, e.g. {ex}")
    rank = preds.groupby(KEY)["sample_id"].rank(method="dense")
    return preds[rank <= n_samples]


def aggregate_predictions(preds: pd.DataFrame, last_close: pd.DataFrame, horizon: int, n_samples: int) -> pd.DataFrame:
    """preds: prediction schema rows (any number of as_of dates). last_close: (as_of_date, ticker, last_close).
    Returns SIGNAL_COLUMNS + last_close, validated."""
    p = validate_predictions(preds, horizon=horizon)
    if p.empty:
        raise ValueError("no prediction rows to aggregate")
    p = select_samples(p, n_samples)
    lc = last_close[KEY + ["last_close"]].copy()
    lc["as_of_date"] = pd.to_datetime(lc["as_of_date"]).dt.normalize()
    lc["ticker"] = lc["ticker"].astype(str)
    if lc.duplicated(KEY).any():
        raise ValueError("last_close has duplicate (as_of_date, ticker) rows")
    p = p.merge(lc, on=KEY, how="left", validate="many_to_one")
    miss = p["last_close"].isna() | (p["last_close"] <= 0)
    if miss.any():
        ex = p.loc[miss, KEY].drop_duplicates().head(3).to_dict("records")
        raise ValueError(f"missing or non-positive last_close for {int(p.loc[miss, KEY].drop_duplicates().shape[0])} (as_of_date, ticker), e.g. {ex}")

    g_all = p.groupby(KEY, sort=True)
    lc_first = g_all["last_close"].first()
    exp_ret_mean = g_all["pred_close"].mean() / lc_first - 1.0
    pred_range = ((p["pred_high"] - p["pred_low"]) / p["last_close"]).groupby([p["as_of_date"], p["ticker"]], sort=True).mean()
    n_steps = g_all["horizon_step"].nunique()
    if (n_steps != horizon).any():
        bad = n_steps[n_steps != horizon].head(3).reset_index().to_dict("records")
        raise ValueError(f"groups without all horizon steps 1..{horizon}, e.g. {bad}")

    h = p[p["horizon_step"] == int(horizon)]
    g = h.groupby(KEY, sort=True)
    lc_h = g["last_close"].first()
    out = pd.DataFrame({
        "exp_ret": g["pred_close"].mean() / lc_h - 1.0,
        "exp_ret_mean": exp_ret_mean.reindex(lc_h.index),
        "std": g["pred_close"].std(ddof=0) / lc_h,
        "p_up": (h["pred_close"] > h["last_close"]).groupby([h["as_of_date"], h["ticker"]], sort=True).mean().reindex(lc_h.index),
        "pred_range": pred_range.reindex(lc_h.index),
        "n_samples": g["sample_id"].nunique().astype("int64"),
        "last_close": lc_h,
    }).reset_index()
    out = out[KEY + ["exp_ret", "exp_ret_mean", "std", "p_up", "pred_range", "n_samples", "last_close"]]
    return validate_signals(out)
