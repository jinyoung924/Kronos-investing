#!/usr/bin/env python
"""F_evaluate entry point: score the signals of a run_id and the engine results of its strategies.

    python -m F_evaluate.run_evaluate --run-id fake_oracle_base --strategy all|a,b [--engine v1] [--config configs/base.yaml] [--root .] [--set ...]

Signals  (data/C_signals/{run_id}) are scored against labels computed here (F_evaluate.labels, H from the manifest):
         rank IC per date for exp_ret, exp_ret_mean and the naive controls mom20 / rev5 (the random walk has exp_ret = 0
         everywhere, so its IC is undefined and reported as such), quantile returns, hit rate, calibration, regimes.
Strategies: every result folder under data/E_backtest/{run_id}/{engine}/ (folder names carry the cost suffix) or the
         ones named. Main benchmark = evaluate.benchmark (`equal_weight` -> the equal_weight folder of the same engine and
         cost scenario, or an index ticker); evaluate.paper_benchmark (index, D-16) gives a second set of relative metrics
         (*_vs_index). evaluate.reference_indices are reported as extra rows. evaluate.by_market (D-16): IC within each
         market's cross-section and the strategies' return contribution by market (portfolio_by_market.csv).
Outputs data/F_metrics/{run_id}/: signal_metrics.json, ic_timeseries.csv, quantile_returns.csv, portfolio_metrics.csv
         (engine x strategy x metric), portfolio_by_market.csv, trials.csv (per run), meta.json; and the global ledger
         data/F_metrics/trials.csv (D-17: Deflated Sharpe counts the real trials of the same profile).
The only module of the stage that touches files. Imports only `common`.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.config import cfg_get, cfg_override, load_config, parse_set_overrides, require  # noqa: E402
from common.meta import write_json_atomic, write_meta  # noqa: E402
from common.paths import Paths  # noqa: E402
from common.schema import validate_nav, validate_signals  # noqa: E402
from F_evaluate.labels import compute_labels  # noqa: E402
from F_evaluate.portfolio_metrics import performance_summary, relative_metrics, trading_metrics  # noqa: E402
from F_evaluate.signal_metrics import (calibration, hit_rate, ic_by_regime, ic_summary, quantile_returns, rank_ic,  # noqa: E402
                                       regime_labels)
from F_evaluate.significance import block_bootstrap_sharpe_ci, deflated_sharpe_from_returns  # noqa: E402

STAGE = "F_evaluate"
SIGNAL_COLS = ["exp_ret", "exp_ret_mean"]
NAIVE_COLS = ["mom20", "rev5"]
TRIALS_COLUMNS = ["run_id", "profile", "fake", "engine", "strategy", "config_hash", "sharpe", "cagr", "evaluated_at"]


# ---------------------------------------------------------------------------------------------
# signals
# ---------------------------------------------------------------------------------------------
def market_window_return(labels: pd.DataFrame, benchmark: str, bench_prices: pd.DataFrame | None, calendar: pd.DatetimeIndex) -> pd.Series:
    """Per as_of date: the benchmark's return over the label window (equal_weight -> mean label across tickers)."""
    if benchmark == "equal_weight":
        return labels.groupby("as_of_date")["label"].mean()
    if bench_prices is None:
        raise ValueError("index benchmark needs data/A_prepared/benchmark.parquet")
    close = bench_prices[bench_prices["ticker"] == benchmark].set_index("date")["close"].reindex(calendar).ffill()
    win = labels.drop_duplicates("as_of_date").set_index("as_of_date")[["fill_date", "end_date"]]
    return pd.Series({d: close.loc[r.end_date] / close.loc[r.fill_date] - 1.0 for d, r in win.iterrows()})


def evaluate_signals(signals: pd.DataFrame, labels: pd.DataFrame, cfg: dict, market_ret: pd.Series | None,
                     market_of: pd.Series | None = None) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    """market_of: (as_of_date, ticker) -> market, for the within-market IC (D-16)."""
    q = int(require(cfg, "evaluate.quantiles"))
    min_obs = int(require(cfg, "evaluate.min_cross_section"))
    split = str(cfg_get(cfg, "evaluate.regimes.split", "half_year"))
    ic_frames, out = {}, {"ic": {}, "ic_by_regime": {}, "quantiles": {}, "naive": {}}
    for col in SIGNAL_COLS:
        label_col = "label_mean" if col == "exp_ret_mean" else "label"
        ic = rank_ic(signals, labels, col, label_col=label_col, min_obs=min_obs)
        ic_frames[f"ic_{col}"] = ic
        out["ic"][col] = {**ic_summary(ic), "label": label_col}
        qdf, qsum = quantile_returns(signals, labels, col, q, label_col=label_col, min_obs=min_obs)
        out["quantiles"][col] = qsum
        qdf.columns = [f"{col}:{c}" for c in qdf.columns]
        ic_frames[f"q_{col}"] = qdf
    for col in NAIVE_COLS:
        if col in signals.columns:
            ic = rank_ic(signals, labels, col, min_obs=min_obs)
            ic_frames[f"ic_{col}"] = ic
            out["naive"][col] = ic_summary(ic)
    out["naive"]["random_walk"] = {"note": "exp_ret = 0 for every ticker (prediction = current price): rank IC undefined"}
    if market_of is not None and bool(cfg_get(cfg, "evaluate.by_market", False)):
        out["ic_by_market"] = {}
        sig_m = signals.join(market_of.rename("market"), on=["as_of_date", "ticker"])
        for mk, g in sig_m.groupby("market"):
            out["ic_by_market"][str(mk)] = {}
            for col in SIGNAL_COLS + [c for c in NAIVE_COLS if c in signals.columns]:
                label_col = "label_mean" if col == "exp_ret_mean" else "label"
                ic = rank_ic(g, labels, col, label_col=label_col, min_obs=min_obs)
                ic_frames[f"ic_{col}@{mk}"] = ic
                out["ic_by_market"][str(mk)][col] = ic_summary(ic)
    out["hit_rate"] = hit_rate(signals, labels)
    out["calibration"] = calibration(signals, labels)
    regimes = regime_labels(sorted(signals["as_of_date"].unique()), market_ret, split)
    for col in SIGNAL_COLS:
        out["ic_by_regime"][col] = ic_by_regime(ic_frames[f"ic_{col}"], regimes)
    out["regime_counts"] = {k: regimes[k].value_counts().to_dict() for k in ("period", "market")}
    ic_ts = pd.concat([v for k, v in ic_frames.items() if k.startswith("ic_")], axis=1).sort_index()
    ic_ts.index.name = "as_of_date"
    ic_ts = ic_ts.join(regimes.set_index("as_of_date"))
    q_ts = pd.concat([v for k, v in ic_frames.items() if k.startswith("q_")], axis=1).sort_index()
    q_ts.index.name = "as_of_date"
    return out, ic_ts.reset_index(), q_ts.reset_index()


# ---------------------------------------------------------------------------------------------
# portfolios
# ---------------------------------------------------------------------------------------------
def result_folders(paths: Paths, run_id: str, engine: str) -> list[str]:
    base = paths.backtest_dir(run_id, engine, "x").parent
    return sorted(p.name for p in base.iterdir() if (p / "nav.csv").exists()) if base.exists() else []


def load_result(paths: Paths, run_id: str, engine: str, folder: str) -> dict:
    d = paths.backtest_dir(run_id, engine, folder)
    nav = validate_nav(pd.read_csv(d / "nav.csv", parse_dates=["date"]))
    daily = pd.read_csv(d / "daily.csv", parse_dates=["date"])
    trades = pd.read_parquet(d / "trades.parquet")
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    return {"nav": nav.set_index("date"), "daily": daily, "trades": trades, "meta": meta, "dir": d}


def cost_suffix(folder: str) -> str:
    return folder.split("@", 1)[1] if "@" in folder else ""


def strategy_name(folder: str) -> str:
    return folder.split("@", 1)[0]


def index_returns(bench_prices: pd.DataFrame, ticker: str, dates: pd.DatetimeIndex) -> pd.Series:
    close = bench_prices[bench_prices["ticker"] == ticker].set_index("date")["close"].sort_index()
    close = close.reindex(dates).ffill()
    return close.pct_change().fillna(0.0)


def market_contribution(holdings: pd.DataFrame, close_ret: pd.DataFrame, market_of: pd.Series, ppy: int) -> list[dict]:
    """Return contribution by market (D-16): sum over tickers of the previous-close weight x that day's adjusted close
    return, per market. Approximation: fill-day legs and costs are not split by market. Returns one dict per market."""
    if holdings.empty:
        return []
    w = holdings.pivot(index="date", columns="ticker", values="weight").sort_index()
    r = close_ret.reindex(index=w.index, columns=w.columns)
    contrib = w.shift(1).fillna(0.0) * r.fillna(0.0)                       # weight at the previous close x today's return
    mk = market_of.reindex(w.columns).fillna("UNKNOWN")
    total_days = max(len(w) - 1, 1)
    rows = []
    for m in sorted(mk.unique()):
        cols = mk.index[mk == m]
        c = contrib[cols].sum(axis=1).iloc[1:]
        share = w[cols].sum(axis=1).iloc[1:]
        sub = (c / share.replace(0, np.nan)).fillna(0.0)                     # return of the market sub-book
        rows.append({"market": str(m), "weight_share_mean": float(share.mean()), "contribution_total": float(c.sum()),
                     "contribution_ann": float(c.sum() * ppy / total_days), "subbook_cagr": float(np.prod(1 + sub) ** (ppy / total_days) - 1),
                     "n_tickers_mean": float((w[cols] > 0).sum(axis=1).iloc[1:].mean())})
    return rows


def evaluate_portfolios(paths: Paths, run_id: str, engine: str, folders: list[str], cfg: dict, bench_prices: pd.DataFrame | None,
                        log=print, close_ret: pd.DataFrame | None = None, market_of: pd.Series | None = None,
                        profile: str | None = None, fake: bool = False) -> tuple[pd.DataFrame, list[dict], pd.DataFrame]:
    ppy = int(require(cfg, "evaluate.periods_per_year"))
    rf = float(cfg_get(cfg, "evaluate.risk_free", 0.0) or 0.0)
    benchmark = str(require(cfg, "evaluate.benchmark"))
    paper_bench = cfg_get(cfg, "evaluate.paper_benchmark")
    by_market = bool(cfg_get(cfg, "evaluate.by_market", False)) and close_ret is not None and market_of is not None
    boot = require(cfg, "evaluate.bootstrap")
    seed = int(cfg_get(cfg, "project.seed", 0))
    results = {f: load_result(paths, run_id, engine, f) for f in folders}
    all_folders = result_folders(paths, run_id, engine)
    rows, trials, mrows = [], [], []
    for f, res in results.items():
        ret = res["nav"]["ret"].iloc[1:]
        nav = res["nav"]["nav"]
        dates = res["nav"].index
        # benchmark series on the same dates
        if benchmark == "equal_weight":
            bf = "equal_weight" + ("@" + cost_suffix(f) if cost_suffix(f) else "")
            if bf in results:
                bench = results[bf]["nav"]["ret"].reindex(dates).iloc[1:]
            elif bf in all_folders:
                bench = load_result(paths, run_id, engine, bf)["nav"]["ret"].reindex(dates).iloc[1:]
            else:
                raise FileNotFoundError(f"benchmark equal_weight result {bf} not found under {paths.backtest_dir(run_id, engine, 'x').parent}")
            bench_name = bf
        else:
            if bench_prices is None:
                raise ValueError("index benchmark needs data/A_prepared/benchmark.parquet")
            bench = index_returns(bench_prices, benchmark, dates).iloc[1:]
            bench_name = benchmark
        perf = performance_summary(ret, nav, rf, ppy)
        rel = relative_metrics(ret, bench, rf, ppy)
        rel_idx = {}
        if paper_bench:
            if bench_prices is None or paper_bench not in set(bench_prices["ticker"]):
                raise ValueError(f"evaluate.paper_benchmark {paper_bench!r} not in data/A_prepared/benchmark.parquet")
            ib = index_returns(bench_prices, paper_bench, dates).iloc[1:]
            ri = relative_metrics(ret, ib, rf, ppy)
            rel_idx = {"paper_benchmark": paper_bench, **{f"{k}_vs_index": v for k, v in ri.items() if k != "n_days"}}
        trd = trading_metrics(res["daily"], res["trades"], ppy)
        if by_market:
            hold = pd.read_parquet(res["dir"] / "holdings.parquet")
            for mr in market_contribution(hold, close_ret, market_of, ppy):
                mrows.append({"run_id": run_id, "engine": engine, "strategy": f, **mr})
        ci = block_bootstrap_sharpe_ci(ret, int(boot["n"]), int(boot["block"]), seed, ppy)
        # cost-free twin for the before/after cost comparison
        twin = strategy_name(f) + "@no_costs"
        twin_res = results.get(twin) or (load_result(paths, run_id, engine, twin) if twin in all_folders else None)
        cagr_gross = performance_summary(twin_res["nav"]["ret"].iloc[1:], twin_res["nav"]["nav"], rf, ppy)["cagr"] if twin_res else np.nan
        row = {"run_id": run_id, "engine": engine, "strategy": f, "strategy_base": strategy_name(f), "costs": cost_suffix(f) or "kr",
               "benchmark": bench_name, **perf, **rel, **rel_idx, **trd,
               "cagr_before_costs": cagr_gross, "cost_drag_cagr": (cagr_gross - perf["cagr"]) if np.isfinite(cagr_gross) else np.nan,
               "sharpe_ci_lower": ci["lower"], "sharpe_ci_upper": ci["upper"], "config_hash": res["meta"].get("config_hash")}
        rows.append(row)
        trials.append({"run_id": run_id, "profile": profile, "fake": bool(fake), "engine": engine, "strategy": f, "config_hash": res["meta"].get("config_hash"),
                       "sharpe": perf["sharpe"], "cagr": perf["cagr"], "evaluated_at": pd.Timestamp.utcnow().isoformat(timespec="seconds")})
        log(f"{run_id}/{engine}/{f}: CAGR {perf['cagr']:+.2%} vol {perf['ann_vol']:.2%} Sharpe {perf['sharpe']:+.2f} MDD {perf['max_drawdown']:+.2%} "
            f"AER {rel['aer']:+.2%} IR {rel['ir']:+.2f} turnover/yr {trd['annual_turnover']:.1f} vs {bench_name}")
    # reference index lines (D-6): buy & hold on the dates of the first result
    if bench_prices is not None and results:
        dates = next(iter(results.values()))["nav"].index
        for tk in cfg_get(cfg, "evaluate.reference_indices", []) or []:
            if tk not in set(bench_prices["ticker"]):
                continue
            r = index_returns(bench_prices, tk, dates)
            nav = (1.0 + r).cumprod()
            perf = performance_summary(r.iloc[1:], nav, rf, ppy)
            rows.append({"run_id": run_id, "engine": engine, "strategy": f"index:{tk}", "strategy_base": f"index:{tk}", "costs": "none",
                         "benchmark": None, **perf})
    return pd.DataFrame(rows), trials, pd.DataFrame(mrows)


def n_trials_for(ledger: pd.DataFrame, run_id: str, profile: str | None, fake: bool, scope: str = "profile") -> int:
    """D-17: a fake run_id counts only its own trials; a real run_id counts every real trial of the same profile
    (scope `profile`) or every real trial (scope `all`)."""
    if len(ledger) == 0:
        return 0
    if fake:
        return int((ledger["run_id"].astype(str) == run_id).sum())
    real = ledger[~ledger["fake"].astype(str).str.lower().isin(["true", "1"])]
    if scope == "profile":
        real = real[real["profile"].astype(str) == str(profile)]
    return int(len(real))


def append_trials(path: Path, new_rows: list[dict]) -> tuple[pd.DataFrame, int]:
    """Append rows whose (run_id, engine, strategy, config_hash) is not yet in trials.csv. Returns (table, n_added)."""
    old = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=TRIALS_COLUMNS)
    for c in TRIALS_COLUMNS:
        if c not in old.columns:
            old[c] = np.nan
    key = ["run_id", "engine", "strategy", "config_hash"]
    have = set(map(tuple, old[key].astype(str).to_numpy())) if len(old) else set()
    add = [r for r in new_rows if tuple(str(r[k]) for k in key) not in have]
    new = pd.DataFrame(add, columns=TRIALS_COLUMNS)
    out = new if len(old) == 0 else (pd.concat([old, new], ignore_index=True) if add else old)
    out.to_csv(path, index=False)
    return out, len(add)


# ---------------------------------------------------------------------------------------------
# entry
# ---------------------------------------------------------------------------------------------
def run_evaluate(cfg: dict, run_id: str, strategy_spec: str, engine: str, root: Path, log=print) -> dict:
    paths = Paths(cfg, root)
    t0 = time.time()
    manifest = json.loads(paths.manifest_file(run_id).read_text(encoding="utf-8"))
    H = int(manifest["pred_len"])
    signals = validate_signals(pd.read_parquet(paths.signals_path(run_id)))
    prices = pd.read_parquet(paths.prepared_path("prices"), columns=["date", "ticker", "open"])
    adj = pd.read_parquet(paths.prepared_path("adj_factor"))
    calendar = pd.DatetimeIndex(pd.read_parquet(paths.prepared_path("calendar"))["date"])
    bench_file = paths.prepared_path("benchmark")
    bench_prices = pd.read_parquet(bench_file) if bench_file.exists() else None
    labels = compute_labels(prices, adj, calendar, sorted(signals["as_of_date"].unique()), H, tickers=signals["ticker"].unique())
    benchmark = str(require(cfg, "evaluate.benchmark"))
    market_ret = market_window_return(labels, benchmark, bench_prices, calendar)
    px_m = pd.read_parquet(paths.prepared_path("prices"), columns=["date", "ticker", "market", "close"])
    market_of = px_m.set_index(["date", "ticker"])["market"]
    market_of.index.names = ["as_of_date", "ticker"]
    sig_metrics, ic_ts, q_ts = evaluate_signals(signals, labels, cfg, market_ret, market_of)
    fake = bool(manifest.get("fake", False))
    sig_metrics.update({"run_id": run_id, "profile": manifest.get("profile"), "H": H, "n_signal_rows": int(len(signals)),
                        "n_labelled_rows": int(len(labels)), "label_definition": "adjusted open(f + H) / open(f) - 1, f = next trading day after as_of",
                        "limitation": "tickers with a halted day inside the inference lookback have no prediction and are excluded from every strategy (D-3)"})
    log(f"{run_id}: IC exp_ret mean {sig_metrics['ic']['exp_ret']['mean']:+.3f} (ICIR {sig_metrics['ic']['exp_ret']['icir']:+.2f}, n {sig_metrics['ic']['exp_ret']['n_dates']}), "
        f"exp_ret_mean {sig_metrics['ic']['exp_ret_mean']['mean']:+.3f}; Q{int(require(cfg, 'evaluate.quantiles'))}-Q1 spread {sig_metrics['quantiles']['exp_ret']['mean_spread']:+.4f}; "
        f"hit rate {sig_metrics['hit_rate']['hit_rate']:.3f}; naive mom20 {sig_metrics['naive'].get('mom20', {}).get('mean', float('nan')):+.3f}")

    folders = result_folders(paths, run_id, engine)
    if strategy_spec.strip() != "all":
        want = [s.strip() for s in strategy_spec.split(",") if s.strip()]
        chosen = [f for f in folders if f in want or strategy_name(f) in want]
        missing = [w for w in want if not any(f == w or strategy_name(f) == w for f in folders)]
        if missing:
            raise FileNotFoundError(f"no {engine} results for {missing} under run_id {run_id}; have {folders}")
        folders = chosen
    if folders and bool(cfg_get(cfg, "evaluate.by_market", False)):
        adj_close = (px_m.pivot(index="date", columns="ticker", values="close") * adj.pivot(index="date", columns="ticker", values="factor"))
        close_ret = adj_close.pct_change(fill_method=None)
        ticker_market = px_m.drop_duplicates("ticker", keep="last").set_index("ticker")["market"]
    else:
        close_ret, ticker_market = None, None
    port, trials, by_market = (evaluate_portfolios(paths, run_id, engine, folders, cfg, bench_prices, log, close_ret, ticker_market,
                                                   manifest.get("profile"), fake) if folders else (pd.DataFrame(), [], pd.DataFrame()))
    if not folders:
        log(f"{run_id}: no {engine} results to score (run E_backtest.run_backtest first)")

    out_dir = paths.metrics_dir(run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(out_dir / "signal_metrics.json", sig_metrics)
    ic_ts.to_csv(out_dir / "ic_timeseries.csv", index=False)
    q_ts.to_csv(out_dir / "quantile_returns.csv", index=False)
    port.to_csv(out_dir / "portfolio_metrics.csv", index=False)
    by_market.to_csv(out_dir / "portfolio_by_market.csv", index=False)
    trials_df, n_added = append_trials(out_dir / "trials.csv", trials)
    ledger, _ = append_trials(paths.trials_ledger(), trials)
    # Deflated Sharpe (D-17): real trials of the same profile from the global ledger; a fake run_id counts only itself
    n_trials = max(n_trials_for(ledger, run_id, manifest.get("profile"), fake, str(cfg_get(cfg, "evaluate.dsr_scope", "profile"))), 1)
    if len(port):
        dsr = []
        for f in port["strategy"]:
            if f.startswith("index:"):
                dsr.append(np.nan); continue
            r = load_result(paths, run_id, engine, f)["nav"]["ret"].iloc[1:]
            dsr.append(deflated_sharpe_from_returns(r, n_trials)["dsr"])
        port["deflated_sharpe"] = dsr
        port["n_trials"] = n_trials
        port.to_csv(out_dir / "portfolio_metrics.csv", index=False)
    summary = {"stage": STAGE, "run_id": run_id, "strategy": strategy_spec, "engine": engine, "profile": manifest.get("profile"), "H": H,
               "benchmark": benchmark, "n_strategies": int(len(folders)), "folders": folders, "n_trials": n_trials, "trials_added": n_added,
               "ic_exp_ret_mean": sig_metrics["ic"]["exp_ret"]["mean"], "elapsed_sec": round(time.time() - t0, 1)}
    inputs = {"signals": paths.signals_path(run_id), "manifest": paths.manifest_file(run_id), "prices": paths.prepared_path("prices"),
              "adj_factor": paths.prepared_path("adj_factor"), **{f"nav:{f}": paths.backtest_dir(run_id, engine, f) / "nav.csv" for f in folders}}
    write_meta(out_dir, cfg, inputs=inputs, extra=summary, root=root)
    log(f"wrote {out_dir}: signal_metrics.json, ic_timeseries.csv ({len(ic_ts)} dates), quantile_returns.csv, portfolio_metrics.csv ({len(port)} rows), "
        f"portfolio_by_market.csv ({len(by_market)} rows), trials.csv (+{n_added}; DSR n_trials {n_trials}, scope {cfg_get(cfg, 'evaluate.dsr_scope', 'profile')}) ({summary['elapsed_sec']}s)")
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-id", required=True)
    p.add_argument("--strategy", default="all")
    p.add_argument("--engine", default="v1")
    p.add_argument("--config", default="configs/base.yaml")
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    a = p.parse_args(argv)
    root = Path(a.root)
    cfg = cfg_override(load_config(root / a.config), parse_set_overrides(a.set))
    run_evaluate(cfg, a.run_id, a.strategy, a.engine, root)


if __name__ == "__main__":
    main()
