"""Portfolio metrics from an engine run (nav.csv / daily.csv / trades.parquet) and a benchmark return series
(docs/spec.md Stage 5 task 3). Conventions: returns are simple daily returns of the NAV at close, annualisation
factor `periods_per_year` (252), risk-free `rf` annual (default 0). Names used by the paper are kept: AER =
annualised excess return over the benchmark, IR = information ratio. quantstats (0.0.86) is used only in tests
to cross-check CAGR, volatility, Sharpe, Sortino and max drawdown. Pure functions.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _clean(ret) -> pd.Series:
    r = pd.Series(ret).astype(float)
    return r[np.isfinite(r)]


def total_return(ret) -> float:
    return float(np.prod(1.0 + _clean(ret).to_numpy()) - 1.0)


def cagr(ret, periods_per_year: int = 252) -> float:
    r = _clean(ret)
    if len(r) == 0:
        return np.nan
    return float((1.0 + total_return(r)) ** (periods_per_year / len(r)) - 1.0)


def ann_vol(ret, periods_per_year: int = 252) -> float:
    r = _clean(ret)
    return float(r.std(ddof=1) * np.sqrt(periods_per_year)) if len(r) > 1 else np.nan


def sharpe(ret, rf: float = 0.0, periods_per_year: int = 252) -> float:
    r = _clean(ret) - rf / periods_per_year
    sd = r.std(ddof=1) if len(r) > 1 else np.nan
    return float(r.mean() / sd * np.sqrt(periods_per_year)) if sd and sd > 0 else np.nan


def sortino(ret, rf: float = 0.0, periods_per_year: int = 252) -> float:
    """mean / downside deviation, downside deviation = sqrt(mean(min(r, 0)^2)) over ALL periods (quantstats convention)."""
    r = _clean(ret) - rf / periods_per_year
    if len(r) < 2:
        return np.nan
    dd = np.sqrt(np.mean(np.minimum(r.to_numpy(), 0.0) ** 2))
    return float(r.mean() / dd * np.sqrt(periods_per_year)) if dd > 0 else np.nan


def drawdown_series(nav: pd.Series) -> pd.Series:
    nav = pd.Series(nav).astype(float)
    return nav / nav.cummax() - 1.0


def max_drawdown(nav: pd.Series) -> dict:
    """Deepest peak-to-trough fall of the NAV with its dates and the recovery time in rows (None if not recovered)."""
    nav = pd.Series(nav).astype(float)
    dd = drawdown_series(nav)
    if len(dd) == 0:
        return {"max_drawdown": np.nan, "peak": None, "trough": None, "recovery": None, "recovery_rows": None}
    trough = dd.idxmin()
    peak = nav.loc[:trough].idxmax()
    after = nav.loc[trough:]
    rec = after[after >= nav.loc[peak]]
    recovery = rec.index[0] if len(rec) else None
    rows = int(nav.index.get_loc(recovery) - nav.index.get_loc(peak)) if recovery is not None else None
    return {"max_drawdown": float(dd.min()), "peak": peak, "trough": trough, "recovery": recovery, "recovery_rows": rows}


def calmar(ret, nav: pd.Series, periods_per_year: int = 252) -> float:
    mdd = max_drawdown(nav)["max_drawdown"]
    c = cagr(ret, periods_per_year)
    return float(c / abs(mdd)) if mdd and mdd < 0 else np.nan


def monthly_returns(ret: pd.Series) -> pd.Series:
    r = _clean(ret)
    r.index = pd.DatetimeIndex(r.index)
    return (1.0 + r).groupby(r.index.to_period("M")).prod() - 1.0


def monthly_distribution(ret: pd.Series) -> dict:
    m = monthly_returns(ret)
    if len(m) == 0:
        return {"n_months": 0}
    return {"n_months": int(len(m)), "mean": float(m.mean()), "median": float(m.median()), "std": float(m.std(ddof=1)) if len(m) > 1 else np.nan,
            "min": float(m.min()), "max": float(m.max()), "share_positive": float((m > 0).mean())}


def relative_metrics(ret: pd.Series, bench: pd.Series, rf: float = 0.0, periods_per_year: int = 252) -> dict:
    """Excess return (AER), beta, alpha (annualised, CAPM on daily returns), tracking error, IR, monthly hit ratio."""
    df = pd.concat([_clean(ret).rename("p"), _clean(bench).rename("b")], axis=1).dropna()
    if len(df) < 3:
        return {k: np.nan for k in ("aer", "beta", "alpha", "tracking_error", "ir", "monthly_hit_ratio", "n_days")}
    active = df["p"] - df["b"]
    te = active.std(ddof=1) * np.sqrt(periods_per_year)
    aer = cagr(df["p"], periods_per_year) - cagr(df["b"], periods_per_year)
    cov = np.cov(df["p"], df["b"], ddof=1)
    beta = cov[0, 1] / cov[1, 1] if cov[1, 1] > 0 else np.nan
    rf_d = rf / periods_per_year
    alpha = ((df["p"] - rf_d).mean() - beta * (df["b"] - rf_d).mean()) * periods_per_year if np.isfinite(beta) else np.nan
    ir = active.mean() / active.std(ddof=1) * np.sqrt(periods_per_year) if active.std(ddof=1) > 0 else np.nan
    mp, mb = monthly_returns(df["p"]), monthly_returns(df["b"])
    hit = float((mp > mb.reindex(mp.index)).mean()) if len(mp) else np.nan
    return {"aer": float(aer), "beta": float(beta), "alpha": float(alpha), "tracking_error": float(te), "ir": float(ir),
            "monthly_hit_ratio": hit, "n_days": int(len(df))}


def trading_metrics(daily: pd.DataFrame, trades: pd.DataFrame, periods_per_year: int = 252) -> dict:
    """Annual turnover (sum of fill-day turnover scaled to a year), mean holdings, mean names changed per fill and per day."""
    d = daily.iloc[1:] if len(daily) > 1 else daily
    n_days = max(len(d), 1)
    fills = d[d["turnover"] > 0]
    changed = trades[trades["trade_w"].abs() > 1e-12].groupby("fill_date")["ticker"].nunique() if len(trades) else pd.Series(dtype=float)
    return {"annual_turnover": float(d["turnover"].sum() * periods_per_year / n_days),
            "turnover_per_fill": float(fills["turnover"].mean()) if len(fills) else 0.0,
            "n_fills": int(len(fills)), "mean_holdings": float(d["n_holdings"].mean()),
            "names_changed_per_fill": float(changed.mean()) if len(changed) else 0.0,
            "names_changed_per_day": float(changed.sum() / n_days) if len(changed) else 0.0,
            "total_cost": float(d["cost"].sum())}


def performance_summary(ret: pd.Series, nav: pd.Series, rf: float = 0.0, periods_per_year: int = 252) -> dict:
    mdd = max_drawdown(nav)
    return {"total_return": total_return(ret), "cagr": cagr(ret, periods_per_year), "ann_vol": ann_vol(ret, periods_per_year),
            "sharpe": sharpe(ret, rf, periods_per_year), "sortino": sortino(ret, rf, periods_per_year),
            "calmar": calmar(ret, nav, periods_per_year), "max_drawdown": mdd["max_drawdown"],
            "mdd_peak": str(pd.Timestamp(mdd["peak"]).date()) if mdd["peak"] is not None else None,
            "mdd_trough": str(pd.Timestamp(mdd["trough"]).date()) if mdd["trough"] is not None else None,
            "mdd_recovery_days": mdd["recovery_rows"], "n_days": int(len(_clean(ret))), **{f"monthly_{k}": v for k, v in monthly_distribution(ret).items()}}
