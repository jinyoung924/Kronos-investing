"""Fixed prediction parquet schema: the only contract between infer/ and backtest/."""
from __future__ import annotations

import numpy as np
import pandas as pd

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
    missing = [c for c in PREDICTION_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"prediction frame missing columns {missing}")
    out = coerce_predictions(df)
    if (out["horizon_step"] < 1).any():
        raise ValueError("horizon_step must be >= 1")
    if horizon is not None and (out["horizon_step"] > horizon).any():
        raise ValueError(f"horizon_step exceeds horizon={horizon}")
    if as_of is not None:
        as_of = pd.Timestamp(as_of).normalize()
        if len(out) and not (out["as_of_date"] == as_of).all():
            raise ValueError(f"prediction rows carry as_of_date != {as_of.date()}")
    dup = out.duplicated(["as_of_date", "ticker", "horizon_step", "sample_id"])
    if dup.any():
        raise ValueError(f"{int(dup.sum())} duplicate (as_of_date, ticker, horizon_step, sample_id) rows")
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
