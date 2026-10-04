"""Fixed on-disk schemas: the only contracts between stages (stages never import each other).

    predictions  B -> C   as_of=YYYY-MM-DD.parquet   PREDICTION_COLUMNS   validate_predictions
    prices       A -> *   A_prepared/prices.parquet  PRICE_COLUMNS        validate_prices
    signals      C -> D   C_signals/.../signals      SIGNAL_COLUMNS       validate_signals
    weights      D -> E   D_weights/.../{strategy}   WEIGHT_COLUMNS       validate_weights
    nav          E -> F   E_backtest/.../nav.csv     NAV_COLUMNS          validate_nav

Every validate_* checks required columns, coerces dtypes, checks key uniqueness and value ranges,
and returns the coerced frame. Extra columns are kept (validate_predictions drops them, as before).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------
def _require_columns(df: pd.DataFrame, columns, what: str) -> None:
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ValueError(f"{what} frame missing columns {missing}")


def _coerce(df: pd.DataFrame, dtypes: dict[str, str], what: str) -> pd.DataFrame:
    out = df.copy()
    for c, t in dtypes.items():
        if c not in out.columns:
            continue
        try:
            if t.startswith("datetime64"):
                out[c] = pd.to_datetime(out[c]).dt.normalize()
            elif t == "object":
                out[c] = out[c].astype(str)
            elif t == "bool":
                out[c] = out[c].astype(bool)
            elif t == "int64":
                if out[c].isna().any():
                    raise ValueError("NaN in integer column")
                out[c] = out[c].astype("int64")
            else:
                out[c] = out[c].astype(t)
        except (TypeError, ValueError) as e:
            raise ValueError(f"{what} column '{c}' is not coercible to {t}: {e}") from e
    return out


def _unique(df: pd.DataFrame, keys: list[str], what: str) -> None:
    dup = df.duplicated(keys)
    if dup.any():
        raise ValueError(f"{what}: {int(dup.sum())} duplicate {tuple(keys)} rows")


def _non_negative(df: pd.DataFrame, cols, what: str, allow_nan: bool = True) -> None:
    for c in cols:
        if c not in df.columns:
            continue
        v = df[c].to_numpy(dtype=float)
        if not allow_nan and np.isnan(v).any():
            raise ValueError(f"{what}: NaN in '{c}'")
        if np.any(v[~np.isnan(v)] < 0):
            raise ValueError(f"{what}: negative values in '{c}'")


def _finite(df: pd.DataFrame, cols, what: str) -> None:
    for c in cols:
        if c in df.columns and not np.isfinite(df[c].to_numpy(dtype=float)).all():
            raise ValueError(f"{what}: non-finite values in '{c}'")


# ---------------------------------------------------------------------------------------------
# predictions (B -> C). Unchanged since the first design.
# ---------------------------------------------------------------------------------------------
PREDICTION_COLUMNS: dict[str, str] = {
    "as_of_date": "datetime64[ns]",
    "ticker": "object",
    "horizon_step": "int64",
    "sample_id": "int64",
    "pred_open": "float64",
    "pred_high": "float64",
    "pred_low": "float64",
    "pred_close": "float64",
    "pred_volume": "float64",
}


def empty_predictions() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=t) for c, t in PREDICTION_COLUMNS.items()})


def coerce_predictions(df: pd.DataFrame) -> pd.DataFrame:
    out = df[list(PREDICTION_COLUMNS)].copy()
    out["as_of_date"] = pd.to_datetime(out["as_of_date"]).dt.normalize()
    out["ticker"] = out["ticker"].astype(str)
    for c in ("horizon_step", "sample_id"):
        out[c] = out[c].astype("int64")
    for c in ("pred_open", "pred_high", "pred_low", "pred_close", "pred_volume"):
        out[c] = out[c].astype("float64")
    return out


def validate_predictions(df: pd.DataFrame, as_of=None, horizon: int | None = None) -> pd.DataFrame:
    """Check schema, uniqueness and (optionally) that all rows carry the expected as_of_date."""
    _require_columns(df, PREDICTION_COLUMNS, "prediction")
    out = coerce_predictions(df)
    if (out["horizon_step"] < 1).any():
        raise ValueError("horizon_step must be >= 1")
    if horizon is not None and (out["horizon_step"] > horizon).any():
        raise ValueError(f"horizon_step exceeds horizon={horizon}")
    if as_of is not None:
        as_of = pd.Timestamp(as_of).normalize()
        if len(out) and not (out["as_of_date"] == as_of).all():
            raise ValueError(f"prediction rows carry as_of_date != {as_of.date()}")
    _unique(out, ["as_of_date", "ticker", "horizon_step", "sample_id"], "prediction")
    if not np.isfinite(out[["pred_open", "pred_high", "pred_low", "pred_close"]].to_numpy()).all():
        raise ValueError("non-finite predicted prices")
    return out


def predictions_from_array(as_of, tickers, samples: np.ndarray) -> pd.DataFrame:
    """samples: (n_tickers, sample_count, horizon, 5) in column order open, high, low, close, volume."""
    n_t, n_s, h, n_f = samples.shape
    if n_f < 5:
        raise ValueError("expected at least 5 features (open, high, low, close, volume)")
    t_idx, s_idx, h_idx = np.meshgrid(np.arange(n_t), np.arange(n_s), np.arange(h), indexing="ij")
    flat = samples[:, :, :, :5].reshape(-1, 5)
    df = pd.DataFrame(
        {
            "as_of_date": pd.Timestamp(as_of).normalize(),
            "ticker": np.asarray(tickers, dtype=object)[t_idx.ravel()],
            "horizon_step": h_idx.ravel() + 1,
            "sample_id": s_idx.ravel(),
            "pred_open": flat[:, 0],
            "pred_high": flat[:, 1],
            "pred_low": flat[:, 2],
            "pred_close": flat[:, 3],
            "pred_volume": flat[:, 4],
        }
    )
    return coerce_predictions(df)


# ---------------------------------------------------------------------------------------------
# prices (A_prepared/prices, Stage 1). Raw (unadjusted) prices; halted days carry NaN open/high/low.
# ---------------------------------------------------------------------------------------------
PRICE_COLUMNS: dict[str, str] = {
    "date": "datetime64[ns]",
    "ticker": "object",
    "market": "object",      # KOSPI | KOSDAQ (exchange of the listing)
    "open": "float64",
    "high": "float64",
    "low": "float64",
    "close": "float64",
    "volume": "float64",
    "value": "float64",      # traded value (KRW)
}
PRICE_OPTIONAL_COLUMNS: dict[str, str] = {"listed_shares": "float64"}


def validate_prices(df: pd.DataFrame) -> pd.DataFrame:
    _require_columns(df, PRICE_COLUMNS, "price")
    out = _coerce(df, {**PRICE_COLUMNS, **PRICE_OPTIONAL_COLUMNS}, "price")
    _unique(out, ["date", "ticker"], "price")
    _non_negative(out, ["open", "high", "low", "close", "volume", "value", "listed_shares"], "price")
    if out["close"].isna().any():
        raise ValueError("price: NaN close (halted days keep the reference close)")
    ohl = out[["open", "high", "low"]]
    traded = ohl.notna().all(axis=1)
    bad = traded & ((out["high"] < out["low"]) | (out["high"] < out[["open", "close"]].max(axis=1))
                    | (out["low"] > out[["open", "close"]].min(axis=1)))
    if bad.any():
        raise ValueError(f"price: {int(bad.sum())} rows violate low <= open, close <= high")
    return out


# ---------------------------------------------------------------------------------------------
# signals (C -> D). One row per (as_of_date, ticker). Baseline features are optional extras.
# ---------------------------------------------------------------------------------------------
SIGNAL_COLUMNS: dict[str, str] = {
    "as_of_date": "datetime64[ns]",
    "ticker": "object",
    "exp_ret": "float64",        # mean(pred_close[H]) / last_close - 1
    "exp_ret_mean": "float64",   # mean over samples and steps 1..H of pred_close / last_close - 1
    "std": "float64",            # std(pred_close[H]) / last_close
    "p_up": "float64",           # share of samples with pred_close[H] > last_close
    "pred_range": "float64",     # mean(pred_high - pred_low) / last_close
    "n_samples": "int64",
}
SIGNAL_FEATURE_COLUMNS: dict[str, str] = {"mom20": "float64", "vol20": "float64", "rev5": "float64", "last_close": "float64"}


def validate_signals(df: pd.DataFrame) -> pd.DataFrame:
    _require_columns(df, SIGNAL_COLUMNS, "signal")
    out = _coerce(df, {**SIGNAL_COLUMNS, **SIGNAL_FEATURE_COLUMNS}, "signal")
    _unique(out, ["as_of_date", "ticker"], "signal")
    _finite(out, ["exp_ret", "exp_ret_mean", "std", "p_up", "pred_range"], "signal")
    # pred_range may be negative: real Kronos samples do not guarantee pred_high >= pred_low (Stage 6 report)
    _non_negative(out, ["std", "last_close"], "signal", allow_nan=False)
    if ((out["p_up"] < 0) | (out["p_up"] > 1)).any():
        raise ValueError("signal: p_up must be within [0, 1]")
    if (out["n_samples"] < 1).any():
        raise ValueError("signal: n_samples must be >= 1")
    return out


# ---------------------------------------------------------------------------------------------
# weights (D -> E). Long format; NaN weight = hold (keep the drifted position, no trade).
# ---------------------------------------------------------------------------------------------
WEIGHT_COLUMNS: dict[str, str] = {
    "as_of_date": "datetime64[ns]",
    "ticker": "object",
    "weight": "float64",
}
WEIGHT_SUM_TOL = 1e-9


def validate_weights(df: pd.DataFrame) -> pd.DataFrame:
    _require_columns(df, WEIGHT_COLUMNS, "weight")
    out = _coerce(df, WEIGHT_COLUMNS, "weight")
    _unique(out, ["as_of_date", "ticker"], "weight")
    w = out["weight"]
    if np.isinf(w.to_numpy(dtype=float)).any():
        raise ValueError("weight: infinite weight")
    if (w.dropna() < 0).any():
        raise ValueError("weight: negative weight (long-only)")
    sums = w.groupby(out["as_of_date"]).sum(min_count=1).dropna()
    over = sums[sums > 1 + WEIGHT_SUM_TOL]
    if len(over):
        d = over.index[0]
        raise ValueError(f"weight: non-NaN weights sum to {over.iloc[0]:.6f} > 1 on {pd.Timestamp(d).date()}")
    return out


def weights_to_wide(df: pd.DataFrame) -> pd.DataFrame:
    """as_of_date x ticker matrix (NaN where a ticker is not mentioned on that date)."""
    return validate_weights(df).pivot(index="as_of_date", columns="ticker", values="weight").sort_index()


# ---------------------------------------------------------------------------------------------
# NAV (E -> F). One row per trading day; ret is the daily net return, cost the cost paid that day.
# ---------------------------------------------------------------------------------------------
NAV_COLUMNS: dict[str, str] = {
    "date": "datetime64[ns]",
    "nav": "float64",
    "ret": "float64",
    "cost": "float64",
}


def validate_nav(df: pd.DataFrame) -> pd.DataFrame:
    _require_columns(df, NAV_COLUMNS, "nav")
    out = _coerce(df, NAV_COLUMNS, "nav")
    _unique(out, ["date"], "nav")
    if not out["date"].is_monotonic_increasing:
        raise ValueError("nav: dates must be sorted ascending")
    _finite(out, ["nav", "ret", "cost"], "nav")
    if (out["nav"] <= 0).any():
        raise ValueError("nav: nav must be > 0")
    _non_negative(out, ["cost"], "nav", allow_nan=False)
    return out
