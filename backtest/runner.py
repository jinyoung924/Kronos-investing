"""Glue: predictions + prices + universe -> signals -> Strategy -> engine -> metrics.

Strategies only ever see the FEATURE frame from `build_signals`. Labels (`realized_returns`)
are joined afterwards, exclusively for the prediction metrics.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from backtest.costs import CostModel
from backtest.engine import BacktestResult, run_backtest
from backtest.metrics import portfolio_summary, prediction_report, strategy_by_regime
from backtest.signals import FEATURE_COLS, build_signals, load_predictions, realized_returns, signals_for_date
from backtest.strategies import get_strategy, uses_predictions
from common.config import cfg_get
from common.data import load_prices, rebalance_dates, ticker_market_map, to_wide, trading_calendar
from common.universe import load_constituents

LABEL_COLS = ("ret_cc", "ret_oo")


@dataclass
class Context:
    cfg: dict
    prices: pd.DataFrame
    constituents: dict[str, pd.DataFrame]
    dates: pd.DatetimeIndex
    signals: pd.DataFrame            # features only
    preds: pd.DataFrame | None
    open_raw: pd.DataFrame
    adj_factor: pd.DataFrame
    cost_model: CostModel
    indices: list[str] | None = None   # universe restriction in effect (None = all configured indices)


def load_context(cfg: dict, run_id: str | None, root: str | Path = ".", need_predictions: bool = True,
                 indices: list[str] | None = None) -> Context:
    """indices: restrict the universe to these index keys (e.g. ["kosdaq"]). Prices for every index
    are still loaded so that predictions/labels line up; only the point-in-time universe (and hence
    signals, strategies and the equal-weight benchmark) is restricted, which keeps a per-exchange
    experiment comparable with the pooled one."""
    prices = load_prices(cfg, root)
    constituents = load_constituents(cfg, root)
    if indices:
        unknown = [i for i in indices if i not in constituents]
        if unknown:
            raise KeyError(f"unknown indices {unknown}; configured: {list(constituents)}")
        constituents = {k: v for k, v in constituents.items() if k in indices}
    start, end = cfg_get(cfg, "run.start"), cfg_get(cfg, "run.end")
    preds = None
    if run_id is not None:
        try:
            preds = load_predictions(cfg, run_id, root, start, end)
        except FileNotFoundError:
            if need_predictions:
                raise
    if preds is not None:
        dates = pd.DatetimeIndex(sorted(preds["as_of_date"].unique()))
    else:
        dates = rebalance_dates(trading_calendar(prices), start, end, int(cfg_get(cfg, "run.step")))
    signals = build_signals(cfg, prices, constituents, dates, preds)
    if indices:
        signals = signals[signals["index"].isin(indices)]
    assert not set(LABEL_COLS) & set(signals.columns), "labels leaked into the feature frame"
    cost_model = CostModel.from_config(cfg, ticker_market_map(prices))
    return Context(cfg, prices, constituents, dates, signals, preds, to_wide(prices, "open"),
                   to_wide(prices, "adj_factor"), cost_model, list(indices) if indices else None)


def strategy_params(cfg: dict, name: str, indices: list[str] | None = None) -> dict:
    params = dict(cfg_get(cfg, f"strategies.{name}", {}) or {})
    if name == "index_buy_hold" and not params.get("tickers"):
        idx_tickers = cfg_get(cfg, "benchmark.index_tickers", {}) or {}
        if indices:  # per-exchange experiment: hold only that exchange's index
            idx_tickers = {k: v for k, v in idx_tickers.items() if k in indices}
        params["tickers"] = list(idx_tickers.values())
    return params


def build_weight_matrix(strategy, signals: pd.DataFrame, dates) -> pd.DataFrame:
    """Call strategy.weights on each as_of date. Rows = as_of dates, columns = union of tickers."""
    rows: dict[pd.Timestamp, pd.Series] = {}
    prev = pd.Series(dtype=float)
    for d in pd.DatetimeIndex(dates):
        sig = signals_for_date(signals, d)
        w = strategy.weights(d, sig, prev.copy())
        w = pd.Series(w, dtype=float)
        if (w < 0).any() or w.sum() > 1 + 1e-9:
            raise ValueError(f"{strategy} returned invalid weights on {d.date()}")
        rows[d] = w
        prev = w
    W = pd.DataFrame(rows).T.fillna(0.0)
    W.index = pd.DatetimeIndex(W.index, name="as_of_date")
    return W


def run_strategy(ctx: Context, name: str, params: dict | None = None, cost_model: CostModel | None = None) -> tuple[BacktestResult, pd.DataFrame]:
    cfg = ctx.cfg
    strategy = get_strategy(name, **(params if params is not None else strategy_params(cfg, name, ctx.indices)))
    if uses_predictions(name) and ctx.preds is None:
        raise ValueError(f"strategy {name} needs predictions but none were loaded")
    W = build_weight_matrix(strategy, ctx.signals, ctx.dates)
    missing = [t for t in W.columns if t not in ctx.open_raw.columns]
    if missing:
        raise KeyError(f"strategy {name} targeted tickers without prices: {missing[:5]}")
    result = run_backtest(W, ctx.open_raw, ctx.adj_factor, cost_model or ctx.cost_model,
                          initial_nav=float(cfg_get(cfg, "backtest.initial_nav", 1.0)),
                          ffill_limit=cfg_get(cfg, "data.ffill_limit", 5))
    return result, W


def evaluate(ctx: Context, name: str, bench_name: str | None = None) -> dict:
    """Run `name` and the benchmark; return {"result", "weights", "metrics", "bench_result", "pred_report"}."""
    cfg = ctx.cfg
    bench_name = bench_name or cfg_get(cfg, "benchmark.strategy", "equal_weight")
    result, W = run_strategy(ctx, name)
    bench_result = None
    if bench_name and bench_name != name:
        bench_result, _ = run_strategy(ctx, bench_name)
    metrics = portfolio_summary(result, bench_result.returns if bench_result is not None else None, cfg_get(cfg, "metrics", {}))
    metrics["strategy"], metrics["benchmark"] = name, bench_name
    metrics["params"] = strategy_params(cfg, name, ctx.indices)
    metrics["indices"] = ctx.indices
    pred_report = None
    if ctx.preds is not None:
        labelled = ctx.signals.join(realized_returns(ctx.prices, ctx.dates, int(cfg_get(cfg, "model.horizon")),
                                                     cfg_get(cfg, "data.ffill_limit", 5)), how="left")
        if uses_predictions(name):
            pred_report = prediction_report(labelled, int(cfg_get(cfg, "metrics.n_quantiles", 5)))
            metrics["by_regime"] = strategy_by_regime(result.returns, pred_report["_regime_labels"], result.fill_dates,
                                                      int(cfg_get(cfg, "metrics.periods_per_year", 252)))
    return {"result": result, "weights": W, "metrics": metrics, "bench_result": bench_result, "pred_report": pred_report}
