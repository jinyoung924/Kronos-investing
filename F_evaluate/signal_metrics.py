"""Prediction quality, independent of any strategy (docs/outline.md F_evaluate, docs/spec.md Stage 5 task 2).

All functions take long frames keyed by (as_of_date, ticker): `signals` (C_signal columns) and `labels`
(F_evaluate.labels). Pure functions; nothing here reads files.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

KEY = ["as_of_date", "ticker"]


def join(signals: pd.DataFrame, labels: pd.DataFrame, label_col: str = "label") -> pd.DataFrame:
    m = signals.merge(labels[KEY + [label_col]], on=KEY, how="inner", validate="one_to_one")
    return m[np.isfinite(m[label_col])]


def rank_ic(signals: pd.DataFrame, labels: pd.DataFrame, col: str, label_col: str = "label", min_obs: int = 10) -> pd.Series:
    """Spearman rank correlation per as_of date between `col` and the label (dates with < min_obs pairs are skipped)."""
    m = join(signals, labels, label_col)
    m = m[np.isfinite(m[col])]
    out = {}
    for d, g in m.groupby("as_of_date", sort=True):
        if len(g) < min_obs or g[col].nunique() < 2 or g[label_col].nunique() < 2:
            continue
        out[d] = float(stats.spearmanr(g[col], g[label_col]).statistic)
    return pd.Series(out, name=f"ic_{col}", dtype=float)


def ic_summary(ic: pd.Series) -> dict:
    ic = ic.dropna()
    n = int(len(ic))
    if n == 0:
        return {"mean": np.nan, "std": np.nan, "icir": np.nan, "t": np.nan, "n_dates": 0, "share_positive": np.nan}
    mean, sd = float(ic.mean()), float(ic.std(ddof=1)) if n > 1 else np.nan
    icir = mean / sd if sd and sd > 0 else np.nan
    return {"mean": mean, "std": sd, "icir": icir, "t": icir * np.sqrt(n) if np.isfinite(icir) else np.nan,
            "n_dates": n, "share_positive": float((ic > 0).mean())}


def quantile_returns(signals: pd.DataFrame, labels: pd.DataFrame, col: str, q: int = 5, label_col: str = "label",
                     min_obs: int = 10) -> tuple[pd.DataFrame, dict]:
    """Per date: mean label by signal quantile (1 = lowest `col`, q = highest). Returns (date x quantile frame with a
    `spread` = Q_q - Q_1 column, summary with mean spread and its t-stat across dates)."""
    m = join(signals, labels, label_col)
    m = m[np.isfinite(m[col])]
    rows = {}
    for d, g in m.groupby("as_of_date", sort=True):
        if len(g) < max(min_obs, q):
            continue
        r = g[col].rank(method="first")
        bins = pd.qcut(r, q, labels=False) + 1
        means = g.groupby(bins)[label_col].mean()
        if len(means) < q:
            continue
        rows[d] = means.reindex(range(1, q + 1)).to_numpy()
    if not rows:
        return pd.DataFrame(columns=[f"Q{i}" for i in range(1, q + 1)] + ["spread"]), {"mean_spread": np.nan, "t": np.nan, "n_dates": 0}
    df = pd.DataFrame.from_dict(rows, orient="index", columns=[f"Q{i}" for i in range(1, q + 1)])
    df.index.name = "as_of_date"
    df["spread"] = df[f"Q{q}"] - df["Q1"]
    n = len(df)
    sd = df["spread"].std(ddof=1) if n > 1 else np.nan
    t = float(df["spread"].mean() / sd * np.sqrt(n)) if sd and sd > 0 else np.nan
    summary = {"mean_spread": float(df["spread"].mean()), "t": t, "n_dates": int(n),
               "mean_by_quantile": {c: float(df[c].mean()) for c in df.columns if c != "spread"}}
    return df, summary


def hit_rate(signals: pd.DataFrame, labels: pd.DataFrame, label_col: str = "label") -> dict:
    """Share of (date, ticker) where the predicted direction (p_up > 0.5) matches the sign of the label."""
    m = join(signals, labels, label_col)
    m = m[m["label"].abs() > 0] if label_col == "label" else m
    pred_up = m["p_up"] > 0.5
    real_up = m[label_col] > 0
    return {"hit_rate": float((pred_up == real_up).mean()) if len(m) else np.nan, "n": int(len(m)),
            "share_pred_up": float(pred_up.mean()) if len(m) else np.nan, "share_real_up": float(real_up.mean()) if len(m) else np.nan}


def calibration(signals: pd.DataFrame, labels: pd.DataFrame, label_col: str = "label") -> dict:
    """Spearman correlation between predicted std and the absolute error |label - exp_ret| (higher = better calibrated)."""
    m = join(signals, labels, label_col)
    m = m[np.isfinite(m["std"]) & np.isfinite(m["exp_ret"])]
    if len(m) < 10 or m["std"].nunique() < 2:
        return {"spearman": np.nan, "n": int(len(m))}
    err = (m[label_col] - m["exp_ret"]).abs()
    return {"spearman": float(stats.spearmanr(m["std"], err).statistic), "n": int(len(m))}


def regime_labels(dates, market_ret: pd.Series | None, split: str = "half_year") -> pd.DataFrame:
    """Per as_of date: calendar regime (half-year) and market regime (up / down by the sign of the benchmark's
    return over the same label window; NaN -> 'unknown')."""
    d = pd.DatetimeIndex(pd.to_datetime(list(dates))).normalize()
    if split != "half_year":
        raise ValueError(f"unsupported regime split {split!r}")
    period = [f"{x.year}H{1 if x.month <= 6 else 2}" for x in d]
    if market_ret is None:
        market = ["unknown"] * len(d)
    else:
        mr = market_ret.reindex(d)
        market = np.where(mr > 0, "up", np.where(mr <= 0, "down", "unknown")).tolist()
    return pd.DataFrame({"as_of_date": d, "period": period, "market": market})


def ic_by_regime(ic: pd.Series, regimes: pd.DataFrame) -> dict:
    df = regimes.set_index("as_of_date").join(ic.rename("ic"), how="inner")
    out = {}
    for kind in ("period", "market"):
        out[kind] = {str(k): ic_summary(g["ic"]) for k, g in df.groupby(kind)}
    return out
