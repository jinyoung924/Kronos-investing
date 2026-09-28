"""Turn raw prediction parquet files into per-(as_of_date, ticker) signals, plus price features.

Two kinds of frames come out of this module and must never be mixed:

* `build_signals(...)`  -> FEATURES known at the close of as_of_date. Safe to hand to strategies.
* `realized_returns(...)` -> LABELS that use future prices. Only for evaluation (metrics), never
  passed to Strategy.weights().
"""
from __future__ import annotations

import re
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from common.config import cfg_get
from common.data import adjusted_wide, ffill_wide, forward_returns, to_wide
from common.paths import Paths, as_of_from_filename
from common.schema import validate_predictions
from common.universe import combined_universe_at

PRED_FILE_RE = re.compile(r"^as_of=\d{4}-\d{2}-\d{2}\.parquet$")
FEATURE_COLS = ["exp_ret", "std", "p_up", "pred_range", "n_samples", "last_close", "mom20", "vol20", "rev5"]


# ----------------------------------------------------------------------------------------------
# predictions
# ----------------------------------------------------------------------------------------------
def load_predictions(cfg: dict, run_id: str, root: str | Path = ".", start=None, end=None) -> pd.DataFrame:
    """Read every as_of=*.parquet under data/predictions/{run_id}; validate file name == column."""
    pdir = Paths(cfg, root).predictions_dir(run_id)
    files = sorted(f for f in pdir.glob("as_of=*.parquet") if PRED_FILE_RE.match(f.name))
    stray = sorted(f.name for f in pdir.glob("*.parquet") if not PRED_FILE_RE.match(f.name))
    if stray:
        warnings.warn(f"ignoring {len(stray)} files that do not match as_of=YYYY-MM-DD.parquet under {pdir}: {stray[:3]}")
    if not files:
        raise FileNotFoundError(f"no prediction files under {pdir}")
    horizon = int(cfg_get(cfg, "model.horizon"))
    frames = []
    for f in files:
        d = as_of_from_filename(f)
        if start is not None and d < pd.Timestamp(start):
            continue
        if end is not None and d > pd.Timestamp(end):
            continue
        frames.append(validate_predictions(pd.read_parquet(f), as_of=d, horizon=horizon))
    if not frames:
        raise ValueError(f"no prediction files in [{start}, {end}] under {pdir}")
    return pd.concat(frames, ignore_index=True)


def last_close_at(close_raw: pd.DataFrame, dates, ffill_limit: int | None) -> pd.DataFrame:
    """Raw close known at each as_of date (forward-filled over holidays)."""
    filled = ffill_wide(close_raw, ffill_limit)
    return filled.reindex(pd.DatetimeIndex(dates))


def prediction_signals(preds: pd.DataFrame, close_raw: pd.DataFrame, horizon: int,
                       ffill_limit: int | None = 5) -> pd.DataFrame:
    """Aggregate sample paths per (as_of_date, ticker):
        exp_ret    = mean(pred_close[H]) / last_close - 1
        std        = std(pred_close[H]) / last_close
        p_up       = P(pred_close[H] > last_close)
        pred_range = mean(pred_high - pred_low) / last_close   (over all steps and samples)
    """
    if preds.empty:
        return pd.DataFrame(columns=["exp_ret", "std", "p_up", "pred_range", "n_samples", "last_close"])
    dates = sorted(preds["as_of_date"].unique())
    lc_wide = last_close_at(close_raw, dates, ffill_limit)
    lc = lc_wide.stack(future_stack=True).rename("last_close")
    lc.index.names = ["as_of_date", "ticker"]

    p = preds.join(lc, on=["as_of_date", "ticker"])
    missing = p["last_close"].isna()
    if missing.any():
        p = p[~missing]
    h = p[p["horizon_step"] == horizon]
    if h.empty:
        raise ValueError(f"no rows with horizon_step == {horizon}")
    g = h.groupby(["as_of_date", "ticker"])
    out = pd.DataFrame({
        "exp_ret": g["pred_close"].mean() / g["last_close"].first() - 1.0,
        "std": g["pred_close"].std(ddof=0) / g["last_close"].first(),
        "p_up": g.apply(lambda x: float((x["pred_close"] > x["last_close"]).mean()), include_groups=False),
        "n_samples": g["sample_id"].nunique(),
        "last_close": g["last_close"].first(),
    })
    rng = ((p["pred_high"] - p["pred_low"]) / p["last_close"]).groupby([p["as_of_date"], p["ticker"]]).mean()
    out["pred_range"] = rng.reindex(out.index)
    out.index.names = ["as_of_date", "ticker"]
    return out[["exp_ret", "std", "p_up", "pred_range", "n_samples", "last_close"]]


# ----------------------------------------------------------------------------------------------
# price-based features (only past data per row)
# ----------------------------------------------------------------------------------------------
def price_features(prices: pd.DataFrame, dates, cfg: dict) -> pd.DataFrame:
    """mom{W}: adjusted close ratio over W rows - 1; vol{W}: annualised std of daily log returns;
    rev{W}: negative trailing return (mean-reversion signal). Index (as_of_date, ticker)."""
    ffl = cfg_get(cfg, "data.ffill_limit", 5)
    ppy = int(cfg_get(cfg, "metrics.periods_per_year", 252))
    mw, vw, rw = (int(cfg_get(cfg, f"features.{k}", d)) for k, d in
                  (("mom_window", 20), ("vol_window", 20), ("rev_window", 5)))
    close = adjusted_wide(prices, "close", ffl)
    raw_close = ffill_wide(to_wide(prices, "close"), ffl)
    logret = np.log(close).diff()
    feats = {
        "mom20": close / close.shift(mw) - 1.0,
        "vol20": logret.rolling(vw).std() * np.sqrt(ppy),
        "rev5": -(close / close.shift(rw) - 1.0),
        "last_close": raw_close,
    }
    dates = pd.DatetimeIndex(dates)
    parts = []
    for name, wide in feats.items():
        s = wide.reindex(dates).stack(future_stack=True).rename(name)
        parts.append(s)
    out = pd.concat(parts, axis=1)
    out.index.names = ["as_of_date", "ticker"]
    return out


# ----------------------------------------------------------------------------------------------
# assembled signals (features only)
# ----------------------------------------------------------------------------------------------
def build_signals(cfg: dict, prices: pd.DataFrame, constituents: dict[str, pd.DataFrame], dates,
                  preds: pd.DataFrame | None = None) -> pd.DataFrame:
    """MultiIndex (as_of_date, ticker) frame restricted to the point-in-time universe at each date.
    Contains prediction signals (if preds given) + price features + market/index labels."""
    dates = pd.DatetimeIndex(sorted(pd.DatetimeIndex(dates).unique()))
    pf = price_features(prices, dates, cfg)
    if preds is not None and not preds.empty:
        horizon = int(cfg_get(cfg, "model.horizon"))
        ps = prediction_signals(preds, to_wide(prices, "close"), horizon, cfg_get(cfg, "data.ffill_limit", 5))
        sig = pf.drop(columns=["last_close"]).join(ps, how="outer")
        sig["last_close"] = sig["last_close"].fillna(pf["last_close"].reindex(sig.index))
    else:
        sig = pf
    # point-in-time universe filter
    rows = []
    for d in dates:
        u = combined_universe_at(constituents, d)
        rows.append(pd.DataFrame({"as_of_date": d, "ticker": u["ticker"], "index": u["index"]}))
    uni = pd.concat(rows, ignore_index=True).set_index(["as_of_date", "ticker"]) if rows else \
        pd.DataFrame(columns=["index"]).set_index(pd.MultiIndex.from_arrays([[], []], names=["as_of_date", "ticker"]))
    sig = uni.join(sig, how="left")
    markets = cfg_get(cfg, "universe.markets", {})
    sig["market"] = sig["index"].map(markets).fillna(cfg_get(cfg, "backtest.default_market", "US"))
    return sig.sort_index()


def signals_for_date(signals: pd.DataFrame, date) -> pd.DataFrame:
    """ticker x feature frame for one date. Guards: only that date's rows."""
    d = pd.Timestamp(date)
    if d not in signals.index.get_level_values(0):
        return signals.iloc[0:0].droplevel(0)
    return signals.xs(d, level="as_of_date")


# ----------------------------------------------------------------------------------------------
# LABELS (future information) - evaluation only
# ----------------------------------------------------------------------------------------------
def realized_returns(prices: pd.DataFrame, dates, horizon: int, ffill_limit: int | None = 5) -> pd.DataFrame:
    """Per (as_of_date, ticker):
        ret_cc : adj close[t+H] / adj close[t] - 1         (what the model is asked to forecast)
        ret_oo : adj open[t+1+H] / adj open[t+1] - 1       (what a strategy filled at t+1 open earns)
    NEVER pass this frame to a Strategy."""
    close = adjusted_wide(prices, "close", ffill_limit)
    open_ = adjusted_wide(prices, "open", ffill_limit)
    dates = pd.DatetimeIndex(dates)
    cc = forward_returns(close, horizon).reindex(dates)
    oo = forward_returns(open_, horizon).shift(-1).reindex(dates)  # row t = open[t+1] -> open[t+1+H]
    out = pd.concat([cc.stack(future_stack=True).rename("ret_cc"), oo.stack(future_stack=True).rename("ret_oo")], axis=1)
    out.index.names = ["as_of_date", "ticker"]
    return out
