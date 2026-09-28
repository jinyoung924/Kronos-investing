"""Metric set. Section A: portfolio metrics (from daily returns). Section B: prediction metrics
(from a frame that joins signals with realized returns). Everything is implemented here directly;
quantstats is only ever used for optional plotting in report.py."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import stats

EULER_GAMMA = 0.5772156649015329


# ==============================================================================================
# A. portfolio metrics
# ==============================================================================================
def _clean(r: pd.Series) -> pd.Series:
    return pd.Series(r).astype(float).dropna()


def total_return(r: pd.Series) -> float:
    return float(np.prod(1.0 + _clean(r)) - 1.0)


def cagr(r: pd.Series, ppy: int = 252) -> float:
    r = _clean(r)
    if len(r) == 0:
        return float("nan")
    years = len(r) / ppy
    tot = float(np.prod(1.0 + r))
    return tot ** (1.0 / years) - 1.0 if tot > 0 and years > 0 else -1.0


def ann_vol(r: pd.Series, ppy: int = 252) -> float:
    r = _clean(r)
    return float(r.std(ddof=1) * math.sqrt(ppy)) if len(r) > 1 else float("nan")


def sharpe(r: pd.Series, ppy: int = 252, rf: float = 0.0) -> float:
    r = _clean(r) - rf / ppy
    sd = r.std(ddof=1)
    return float(r.mean() / sd * math.sqrt(ppy)) if len(r) > 1 and sd > 0 else float("nan")


def sortino(r: pd.Series, ppy: int = 252, rf: float = 0.0) -> float:
    r = _clean(r) - rf / ppy
    downside = np.sqrt(np.mean(np.minimum(r, 0.0) ** 2))
    return float(r.mean() / downside * math.sqrt(ppy)) if downside > 0 else float("nan")


def drawdown_series(nav: pd.Series) -> pd.Series:
    nav = pd.Series(nav).astype(float)
    return nav / nav.cummax() - 1.0


def max_drawdown(nav: pd.Series) -> dict:
    """MDD with peak, trough and recovery dates; recovery_days None if not recovered."""
    dd = drawdown_series(nav)
    if dd.empty:
        return {"mdd": float("nan")}
    trough = dd.idxmin()
    peak = nav.loc[:trough].idxmax()
    after = nav.loc[trough:]
    rec = after[after >= nav.loc[peak]]
    recovery = rec.index[0] if len(rec) else None
    return {
        "mdd": float(dd.min()), "peak": peak, "trough": trough, "recovery": recovery,
        "drawdown_days": int((nav.loc[peak:trough]).shape[0] - 1),
        "recovery_days": int(nav.loc[trough:recovery].shape[0] - 1) if recovery is not None else None,
    }


def calmar(r: pd.Series, nav: pd.Series, ppy: int = 252) -> float:
    mdd = max_drawdown(nav)["mdd"]
    return float(cagr(r, ppy) / abs(mdd)) if mdd < 0 else float("nan")


def monthly_returns(r: pd.Series) -> pd.Series:
    r = _clean(r)
    return (1.0 + r).groupby(r.index.to_period("M")).prod() - 1.0


def monthly_distribution(r: pd.Series) -> dict:
    m = monthly_returns(r)
    if m.empty:
        return {}
    return {
        "n_months": int(len(m)), "mean": float(m.mean()), "median": float(m.median()), "std": float(m.std(ddof=1)) if len(m) > 1 else float("nan"),
        "min": float(m.min()), "max": float(m.max()), "pct_positive": float((m > 0).mean()),
        "skew": float(stats.skew(m)) if len(m) > 2 else float("nan"),
        "by_month": {str(k): float(v) for k, v in m.items()},
    }


def relative_metrics(r: pd.Series, b: pd.Series, ppy: int = 252, rf: float = 0.0) -> dict:
    """Excess return, beta, (annualised Jensen) alpha, tracking error, IR, monthly hit ratio."""
    df = pd.concat([_clean(r).rename("r"), _clean(b).rename("b")], axis=1).dropna()
    if len(df) < 3:
        return {}
    x, y = df["b"] - rf / ppy, df["r"] - rf / ppy
    beta = float(np.cov(x, y, ddof=1)[0, 1] / x.var(ddof=1)) if x.var(ddof=1) > 0 else float("nan")
    alpha_daily = float(y.mean() - beta * x.mean())
    active = df["r"] - df["b"]
    te = float(active.std(ddof=1) * math.sqrt(ppy))
    mr, mb = monthly_returns(df["r"]), monthly_returns(df["b"])
    return {
        "excess_cagr": cagr(df["r"], ppy) - cagr(df["b"], ppy),
        "excess_total_return": total_return(df["r"]) - total_return(df["b"]),
        "beta": beta, "alpha_ann": alpha_daily * ppy,
        "tracking_error": te, "information_ratio": float(active.mean() * ppy / te) if te > 0 else float("nan"),
        "monthly_hit_ratio": float((mr > mb).mean()) if len(mr) else float("nan"),
        "correlation": float(df["r"].corr(df["b"])),
    }


def trading_metrics(turnover: pd.Series, n_holdings: pd.Series, r_net: pd.Series, r_gross: pd.Series,
                    ppy: int = 252) -> dict:
    years = len(_clean(r_net)) / ppy
    return {
        "annual_turnover": float(turnover.sum() / years) if years > 0 else float("nan"),
        "avg_holdings": float(n_holdings[n_holdings > 0].mean()) if (n_holdings > 0).any() else 0.0,
        "cagr_gross": cagr(r_gross, ppy), "cagr_net": cagr(r_net, ppy),
        "cost_drag_cagr": cagr(r_gross, ppy) - cagr(r_net, ppy),
    }


def block_bootstrap_sharpe_ci(r: pd.Series, n: int = 1000, block: int = 10, seed: int = 0,
                              ppy: int = 252, alpha: float = 0.05) -> dict:
    """Circular block bootstrap of the annualised Sharpe ratio."""
    x = _clean(r).to_numpy()
    T = len(x)
    if T < max(block, 5):
        return {"lo": float("nan"), "hi": float("nan"), "n": 0}
    rng = np.random.default_rng(seed)
    n_blocks = math.ceil(T / block)
    starts = rng.integers(0, T, size=(n, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n, -1)[:, :T] % T
    samples = x[idx]
    sd = samples.std(axis=1, ddof=1)
    sr = np.where(sd > 0, samples.mean(axis=1) / np.where(sd > 0, sd, 1.0) * math.sqrt(ppy), np.nan)
    return {"lo": float(np.nanpercentile(sr, 100 * alpha / 2)), "hi": float(np.nanpercentile(sr, 100 * (1 - alpha / 2))),
            "n": int(n), "block": int(block)}


def probabilistic_sharpe_ratio(r: pd.Series, sr_benchmark: float = 0.0) -> float:
    """PSR (Bailey & Lopez de Prado): P[true SR > sr_benchmark], all in per-period units."""
    x = _clean(r)
    T = len(x)
    if T < 3 or x.std(ddof=1) == 0:
        return float("nan")
    sr = float(x.mean() / x.std(ddof=1))
    g3 = float(stats.skew(x))
    g4 = float(stats.kurtosis(x, fisher=False))
    denom = math.sqrt(max(1.0 - g3 * sr + (g4 - 1.0) / 4.0 * sr ** 2, 1e-12))
    z = (sr - sr_benchmark) * math.sqrt(T - 1) / denom
    return float(stats.norm.cdf(z))


def deflated_sharpe_ratio(r: pd.Series, n_trials: int, sr_variance: float | None = None) -> dict:
    """DSR = PSR evaluated at the expected maximum Sharpe of `n_trials` unskilled trials.
    sr_variance: variance of the per-period Sharpe across trials; if None, use the standard-error
    proxy (1 + SR^2/2) / T of this series (conservative when only one trial is observed)."""
    x = _clean(r)
    T = len(x)
    if T < 3 or x.std(ddof=1) == 0:
        return {"dsr": float("nan")}
    sr = float(x.mean() / x.std(ddof=1))
    n_trials = max(int(n_trials), 1)
    if sr_variance is None:
        sr_variance = (1.0 + 0.5 * sr ** 2) / T
    if n_trials == 1:
        sr0 = 0.0
    else:
        sr0 = math.sqrt(sr_variance) * ((1 - EULER_GAMMA) * stats.norm.ppf(1 - 1.0 / n_trials)
                                        + EULER_GAMMA * stats.norm.ppf(1 - 1.0 / (n_trials * math.e)))
    return {"dsr": probabilistic_sharpe_ratio(x, sr0), "sr0_per_period": float(sr0), "n_trials": n_trials,
            "sr_variance_used": float(sr_variance), "psr_vs_zero": probabilistic_sharpe_ratio(x, 0.0)}


def portfolio_summary(result, bench_returns: pd.Series | None, cfg_metrics: dict) -> dict:
    """All section-A metrics for a BacktestResult."""
    ppy = int(cfg_metrics.get("periods_per_year", 252))
    rf = float(cfg_metrics.get("risk_free", 0.0))
    bs = cfg_metrics.get("bootstrap", {}) or {}
    r = result.returns.iloc[1:]  # first row has no return
    rg = result.returns_gross.iloc[1:]
    mdd = max_drawdown(result.nav)
    out = {
        "period": {"start": str(result.nav.index[0].date()), "end": str(result.nav.index[-1].date()), "n_days": int(len(r))},
        "total_return": total_return(r), "cagr": cagr(r, ppy), "ann_vol": ann_vol(r, ppy),
        "sharpe": sharpe(r, ppy, rf), "sortino": sortino(r, ppy, rf), "calmar": calmar(r, result.nav, ppy),
        "mdd": mdd["mdd"], "mdd_peak": str(mdd.get("peak", "")), "mdd_trough": str(mdd.get("trough", "")),
        "mdd_recovery": str(mdd["recovery"]) if mdd.get("recovery") is not None else None,
        "mdd_recovery_days": mdd.get("recovery_days"),
        "monthly": monthly_distribution(r),
        "trading": trading_metrics(result.turnover, result.n_holdings, r, rg, ppy),
        "significance": {
            "bootstrap_sharpe_ci95": block_bootstrap_sharpe_ci(r, int(bs.get("n", 1000)), int(bs.get("block", 10)), int(bs.get("seed", 0)), ppy),
            "deflated_sharpe": deflated_sharpe_ratio(r, int(cfg_metrics.get("n_trials", 1))),
        },
        "untradable_events": int(sum(len(v) for v in result.untradable.values())),
    }
    if bench_returns is not None:
        out["vs_benchmark"] = relative_metrics(r, bench_returns.iloc[1:], ppy, rf)
    return out


# ==============================================================================================
# B. prediction metrics (strategy-independent)
# ==============================================================================================
def rank_ic_series(df: pd.DataFrame, signal: str, target: str, min_n: int = 10) -> pd.Series:
    """Cross-sectional Spearman correlation per as_of_date."""
    def _ic(g):
        g = g[[signal, target]].dropna()
        if len(g) < min_n or g[signal].nunique() < 2:
            return np.nan
        return stats.spearmanr(g[signal], g[target]).statistic
    return df.groupby(level="as_of_date").apply(_ic).rename(f"ic_{signal}")


def ic_summary(ic: pd.Series) -> dict:
    ic = ic.dropna()
    if ic.empty:
        return {"mean": float("nan"), "n": 0}
    sd = ic.std(ddof=1) if len(ic) > 1 else float("nan")
    return {"mean": float(ic.mean()), "std": float(sd), "icir": float(ic.mean() / sd) if sd and sd > 0 else float("nan"),
            "t_stat": float(ic.mean() / sd * math.sqrt(len(ic))) if sd and sd > 0 else float("nan"),
            "pct_positive": float((ic > 0).mean()), "n": int(len(ic))}


def quantile_returns(df: pd.DataFrame, signal: str, target: str, q: int = 5) -> dict:
    """Mean realised return per signal quantile (per date, then averaged) and the Q_top - Q_bottom spread."""
    d = df[[signal, target]].dropna()

    def _bucket(g):
        if len(g) < q:
            return pd.Series(np.nan, index=g.index)
        return pd.qcut(g[signal].rank(method="first"), q, labels=False) + 1

    d = d.assign(q=d.groupby(level="as_of_date", group_keys=False).apply(_bucket))
    d = d.dropna(subset=["q"])
    if d.empty:
        return {}
    per_date = d.groupby([d.index.get_level_values("as_of_date"), "q"])[target].mean().unstack("q")
    spread = (per_date[q] - per_date[1]).rename("spread")
    return {
        "mean_by_quantile": {int(k): float(v) for k, v in per_date.mean().items()},
        "spread_mean": float(spread.mean()), "spread_std": float(spread.std(ddof=1)) if len(spread) > 1 else float("nan"),
        "spread_t_stat": float(spread.mean() / spread.std(ddof=1) * math.sqrt(len(spread))) if len(spread) > 1 and spread.std(ddof=1) > 0 else float("nan"),
        "spread_pct_positive": float((spread > 0).mean()), "n_dates": int(len(spread)),
        "per_date": per_date, "spread_series": spread,
    }


def direction_accuracy(df: pd.DataFrame, target: str = "ret_cc") -> dict:
    d = df[["p_up", "exp_ret", target]].dropna()
    if d.empty:
        return {}
    up = d[target] > 0
    return {"acc_p_up": float(((d["p_up"] > 0.5) == up).mean()), "acc_sign_exp_ret": float(((d["exp_ret"] > 0) == up).mean()),
            "base_rate_up": float(up.mean()), "n": int(len(d))}


def calibration(df: pd.DataFrame, target: str = "ret_cc") -> dict:
    """Does the predicted dispersion (std) track the realised absolute error?"""
    d = df[["std", "exp_ret", target]].dropna()
    if len(d) < 10:
        return {}
    err = (d[target] - d["exp_ret"]).abs()
    return {"spearman_std_abs_err": float(stats.spearmanr(d["std"], err).statistic),
            "pearson_std_abs_err": float(np.corrcoef(d["std"], err)[0, 1]),
            "mean_pred_std": float(d["std"].mean()), "mean_abs_err": float(err.mean()), "n": int(len(d))}


def naive_comparison(df: pd.DataFrame, target: str) -> dict:
    """IC of the model vs naive signals: random walk (exp_ret = 0 -> no ranking, IC undefined = 0),
    momentum (mom20), mean reversion (rev5)."""
    out = {"random_walk": {"mean": 0.0, "note": "prediction == current price gives a constant signal; IC is 0 by construction"}}
    for name, col in (("kronos_exp_ret", "exp_ret"), ("momentum20", "mom20"), ("mean_reversion5", "rev5")):
        if col in df.columns:
            out[name] = ic_summary(rank_ic_series(df, col, target))
    return out


def half_year_label(d: pd.Timestamp) -> str:
    return f"{d.year}H{1 if d.month <= 6 else 2}"


def regime_labels(df: pd.DataFrame, target: str = "ret_oo") -> pd.DataFrame:
    """Per as_of_date: half-year label and bull/bear (sign of the equal-weight realised return over
    the holding period). Ex-post classification used only for reporting."""
    ew = df.groupby(level="as_of_date")[target].mean()
    return pd.DataFrame({"half": [half_year_label(d) for d in ew.index], "regime": np.where(ew > 0, "bull", "bear")}, index=ew.index)


def prediction_report(df: pd.DataFrame, q: int = 5) -> dict:
    """df: (as_of_date, ticker) frame with signals + labels (ret_cc, ret_oo). Returns nested dict
    plus time series (ic_series, quantile spread) for plotting."""
    out: dict = {}
    for target in ("ret_cc", "ret_oo"):
        if target not in df.columns:
            continue
        ic = rank_ic_series(df, "exp_ret", target)
        out[target] = {
            "rank_ic": ic_summary(ic),
            "quantiles": {k: v for k, v in quantile_returns(df, "exp_ret", target, q).items() if k not in ("per_date", "spread_series")},
            "direction": direction_accuracy(df, target),
            "calibration": calibration(df, target),
            "naive_comparison": naive_comparison(df, target),
        }
        out[f"_ic_series_{target}"] = ic
        qr = quantile_returns(df, "exp_ret", target, q)
        out[f"_quantile_per_date_{target}"] = qr.get("per_date")
    # per exchange / index (cross-sections computed WITHIN each index)
    if "index" in df.columns:
        by_index: dict = {}
        for key, sub in df.groupby("index"):
            entry = {"n_obs": int(len(sub))}
            for target in ("ret_cc", "ret_oo"):
                if target in sub.columns:
                    entry[target] = {
                        "rank_ic": ic_summary(rank_ic_series(sub, "exp_ret", target)),
                        "quantiles": {k: v for k, v in quantile_returns(sub, "exp_ret", target, q).items() if k not in ("per_date", "spread_series")},
                        "direction": direction_accuracy(sub, target),
                        "naive_comparison": naive_comparison(sub, target),
                    }
            by_index[str(key)] = entry
        out["by_index"] = by_index
    # regimes
    if "ret_oo" in df.columns:
        reg = regime_labels(df, "ret_oo")
        ic = out["_ic_series_ret_cc"] if "_ic_series_ret_cc" in out else out["_ic_series_ret_oo"]
        by = {}
        for key in ("half", "regime"):
            by[key] = {str(k): ic_summary(ic.loc[idx.intersection(ic.index)]) for k, idx in reg.groupby(key).groups.items()}
        out["regimes"] = by
        out["_regime_labels"] = reg
    return out


def strategy_by_regime(returns: pd.Series, regime_labels_df: pd.DataFrame, fill_dates: pd.DatetimeIndex,
                       ppy: int = 252) -> dict:
    """Annualised return / Sharpe of daily strategy returns grouped by the regime of the holding
    period (fill date -> next fill date) and by half-year."""
    r = _clean(returns)
    if r.empty or regime_labels_df.empty:
        return {}
    as_of = regime_labels_df.index.sort_values()
    fills = pd.DatetimeIndex(fill_dates).sort_values()
    # holding period i runs from fills[i] (inclusive) to fills[i+1] (exclusive); align by order
    n = min(len(as_of), len(fills))
    labels = pd.Series(index=r.index, dtype=object)
    reg = regime_labels_df.loc[as_of[:n], "regime"].to_numpy()
    for i in range(n):
        lo = fills[i]
        hi = fills[i + 1] if i + 1 < n else r.index[-1] + pd.Timedelta(days=1)
        labels.loc[(labels.index >= lo) & (labels.index < hi)] = reg[i]
    half = pd.Series([half_year_label(d) for d in r.index], index=r.index)
    out = {"regime": {}, "half": {}}
    for k, idx in labels.dropna().groupby(labels.dropna()).groups.items():
        rr = r.loc[idx]
        out["regime"][str(k)] = {"n_days": int(len(rr)), "ann_return": float(rr.mean() * ppy), "sharpe": sharpe(rr, ppy), "total_return": total_return(rr)}
    for k, idx in half.groupby(half).groups.items():
        rr = r.loc[idx]
        out["half"][str(k)] = {"n_days": int(len(rr)), "ann_return": float(rr.mean() * ppy), "sharpe": sharpe(rr, ppy), "total_return": total_return(rr)}
    return out
