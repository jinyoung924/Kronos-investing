"""Statistical significance of a Sharpe ratio: circular block bootstrap CI and the Deflated Sharpe Ratio.

Deflated Sharpe Ratio: Bailey, D. H. & López de Prado, M. (2014), "The Deflated Sharpe Ratio: Correcting for
Selection Bias, Backtest Overfitting and Non-Normality", Journal of Portfolio Management 40(5).
    PSR(SR*) = Phi( (SR - SR*) * sqrt(T - 1) / sqrt(1 - skew*SR + (kurt - 1)/4 * SR^2) )
    SR*      = sqrt(V[SR]) * ( (1 - gamma) * Phi^-1(1 - 1/N) + gamma * Phi^-1(1 - 1/(N e)) ),  gamma = Euler-Mascheroni
with SR the (non-annualised, per-period) Sharpe ratio, T observations, kurt the (non-excess) kurtosis, N trials and
V[SR] the variance of the trials' Sharpe ratios ((1 + SR^2 / 2) / T when unknown). Pure functions.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import stats

EULER_GAMMA = 0.5772156649015329


def sharpe_per_period(ret: np.ndarray) -> float:
    r = np.asarray(ret, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 2 or r.std(ddof=1) == 0:
        return float("nan")
    return float(r.mean() / r.std(ddof=1))


def block_bootstrap_sharpe_ci(ret, n: int, block: int, seed: int, periods_per_year: int = 252, alpha: float = 0.05) -> dict:
    """Circular block bootstrap of the annualised Sharpe ratio (appendix B): draw ceil(T/block) start points, chain the
    blocks (wrapping at the end), cut to T, repeat n times. Returns lower/upper bounds, the point estimate and n."""
    r = np.asarray(pd.Series(ret).dropna(), dtype=float)
    T = len(r)
    if T < max(2 * block, 10):
        return {"lower": float("nan"), "upper": float("nan"), "point": float("nan"), "n": 0, "block": block, "T": T}
    rng = np.random.default_rng(seed)
    n_blocks = math.ceil(T / block)
    starts = rng.integers(0, T, size=(int(n), n_blocks))
    offsets = np.arange(block)
    idx = (starts[:, :, None] + offsets[None, None, :]).reshape(int(n), -1)[:, :T] % T
    samples = r[idx]
    mu = samples.mean(axis=1)
    sd = samples.std(axis=1, ddof=1)
    sr = np.where(sd > 0, mu / sd, np.nan) * math.sqrt(periods_per_year)
    sr = sr[np.isfinite(sr)]
    lo, hi = np.percentile(sr, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return {"lower": float(lo), "upper": float(hi), "point": sharpe_per_period(r) * math.sqrt(periods_per_year),
            "n": int(len(sr)), "block": int(block), "T": int(T)}


def probabilistic_sharpe(sr: float, sr_star: float, T: int, skew: float, kurt: float) -> float:
    """PSR: probability that the true per-period Sharpe exceeds sr_star (kurt = non-excess kurtosis)."""
    denom = math.sqrt(max(1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr ** 2, 1e-12))
    return float(stats.norm.cdf((sr - sr_star) * math.sqrt(T - 1) / denom))


def expected_max_sharpe(n_trials: int, sr_var: float) -> float:
    n = max(int(n_trials), 1)
    if n == 1:
        return 0.0
    return math.sqrt(max(sr_var, 0.0)) * ((1 - EULER_GAMMA) * stats.norm.ppf(1 - 1 / n) + EULER_GAMMA * stats.norm.ppf(1 - 1 / (n * math.e)))


def deflated_sharpe(sr: float, n_trials: int, T: int, skew: float, kurt: float, sr_var: float | None = None) -> dict:
    """DSR = PSR(SR*) with SR* the expected maximum Sharpe of n_trials independent trials. All Sharpe inputs per period."""
    if not np.isfinite(sr) or T < 3:
        return {"dsr": float("nan"), "sr_star": float("nan"), "sr": sr, "n_trials": int(n_trials), "T": int(T)}
    v = (1.0 + sr ** 2 / 2.0) / T if sr_var is None else float(sr_var)
    sr_star = expected_max_sharpe(n_trials, v)
    return {"dsr": probabilistic_sharpe(sr, sr_star, T, skew, kurt), "sr_star": sr_star, "sr": float(sr),
            "n_trials": int(n_trials), "T": int(T), "sr_var": v}


def deflated_sharpe_from_returns(ret, n_trials: int, sr_var: float | None = None) -> dict:
    r = np.asarray(pd.Series(ret).dropna(), dtype=float)
    if len(r) < 3:
        return deflated_sharpe(float("nan"), n_trials, len(r), 0.0, 3.0, sr_var)
    return deflated_sharpe(sharpe_per_period(r), n_trials, len(r), float(stats.skew(r)), float(stats.kurtosis(r, fisher=False)), sr_var)
