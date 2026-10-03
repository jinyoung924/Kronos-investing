#!/usr/bin/env python
"""E_backtest entry point: data/D_weights/{run_id}/{strategy}.parquet + data/A_prepared -> data/E_backtest/{run_id}/{engine}/{strategy}/.

    python -m E_backtest.run_backtest --run-id fake_dummy_base --strategy all|a,b [--engine v1] [--costs kr|paper] [--no-costs]
                                      [--config configs/base.yaml] [--root .]

Costs: --costs kr (default, costs.scenario) needs costs.sell_tax_table; --costs paper uses the paper's qlib
costs and appends `@paper_costs` to the strategy folder; --no-costs sets every rate to 0 and appends `@no_costs`.
`all` = every weights file present for the run_id (the engine never imports D_strategy).
Outputs per strategy: nav.csv (date, nav, ret, cost), daily.csv, trades.parquet, holdings.parquet, delisted.csv (if any), meta.json.
The only module of the stage that touches files.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.config import cfg_get, cfg_override, load_config, parse_set_overrides  # noqa: E402
from common.meta import atomic_parquet, write_meta  # noqa: E402
from common.paths import Paths  # noqa: E402
from E_backtest.costs import CostModel  # noqa: E402
from E_backtest.engine_v1_weights import run_v1  # noqa: E402

STAGE = "E_backtest"
ENGINES = {"v1": run_v1}
SUFFIX = {"kr": "", "paper": "@paper_costs", "none": "@no_costs"}


def available_strategies(paths: Paths, run_id: str) -> list[str]:
    return sorted(p.stem for p in paths.weights_dir(run_id).glob("*.parquet"))


def resolve(spec: str, paths: Paths, run_id: str) -> list[str]:
    have = available_strategies(paths, run_id)
    if not have:
        raise FileNotFoundError(f"no weights under {paths.weights_dir(run_id)}; run D_strategy.run_strategy first")
    if spec.strip() == "all":
        return have
    names = [s.strip() for s in spec.split(",") if s.strip()]
    unknown = [n for n in names if n not in have]
    if unknown:
        raise ValueError(f"no weights for {unknown}; available: {have}")
    return list(dict.fromkeys(names))


def load_prices(paths: Paths) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    prices = pd.read_parquet(paths.prepared_path("prices"), columns=["date", "ticker", "market", "open", "close"])
    adj = pd.read_parquet(paths.prepared_path("adj_factor"))
    prices = prices.merge(adj, on=["date", "ticker"], how="left", validate="one_to_one")
    if prices["factor"].isna().any():
        raise ValueError("adj_factor rows missing for some price rows")
    calendar = pd.DatetimeIndex(pd.read_parquet(paths.prepared_path("calendar"))["date"])
    return prices, calendar


def run_backtests(cfg: dict, run_id: str, names: list[str], engine: str, scenario: str, root: Path, log=print) -> dict:
    if engine not in ENGINES:
        raise ValueError(f"engine must be one of {sorted(ENGINES)}")
    paths = Paths(cfg, root)
    cost_model = CostModel(cfg, scenario)
    prices, calendar = load_prices(paths)
    results = {}
    for name in names:
        t0 = time.time()
        wpath = paths.weights_path(run_id, name)
        weights = pd.read_parquet(wpath)
        out = ENGINES[engine](weights, prices, calendar, cost_model, cfg)
        out_dir = paths.backtest_dir(run_id, engine, name + SUFFIX[cost_model.scenario])
        out_dir.mkdir(parents=True, exist_ok=True)
        out["nav"].to_csv(out_dir / "nav.csv", index=False)
        out["daily"].to_csv(out_dir / "daily.csv", index=False)
        atomic_parquet(out["trades"], out_dir / "trades.parquet")
        atomic_parquet(out["holdings"], out_dir / "holdings.parquet")
        if len(out["delisted"]):
            out["delisted"].to_csv(out_dir / "delisted.csv", index=False)
        nav = out["nav"]
        n_days = len(nav) - 1
        summary = {
            "stage": STAGE, "run_id": run_id, "strategy": name, "engine": engine, "costs": cost_model.describe(),
            "start": str(nav["date"].iloc[0].date()), "end": str(nav["date"].iloc[-1].date()), "n_days": int(n_days),
            "nav_end": float(nav["nav"].iloc[-1]), "total_return": float(nav["nav"].iloc[-1] - 1.0),
            "total_cost": float(nav["cost"].sum()),
            "n_fills": int(out["trades"]["fill_date"].nunique()), "n_trade_rows": int(len(out["trades"])),
            "trade_status_counts": out["trades"]["status"].value_counts().to_dict() if len(out["trades"]) else {},
            "mean_turnover_per_fill": float(out["daily"].loc[out["daily"]["turnover"] > 0, "turnover"].mean()) if (out["daily"]["turnover"] > 0).any() else 0.0,
            "mean_holdings": float(out["daily"]["n_holdings"].iloc[1:].mean()) if n_days else 0.0,
            "unfilled_signal_dates": [str(d.date()) for d in out["unfilled_signal_dates"]],
            "delist_policy_used": out["delist_policy_used"],
            "delisted_positions": [{"date": str(r.date.date()), "ticker": r.ticker, "weight": round(float(r.weight), 6), "last_bar": str(pd.Timestamp(r.last_bar).date())}
                                   for r in out["delisted"].itertuples()],
            "elapsed_sec": round(time.time() - t0, 1),
        }
        write_meta(out_dir, cfg, inputs={"weights": wpath, "prices": paths.prepared_path("prices"), "adj_factor": paths.prepared_path("adj_factor"),
                                         "calendar": paths.prepared_path("calendar")}, extra=summary, root=root)
        results[name] = summary
        log(f"{run_id}/{engine}/{name}{SUFFIX[cost_model.scenario]}: {summary['start']}..{summary['end']} nav_end {summary['nav_end']:.4f} "
            f"total_cost {summary['total_cost']:.4f} fills {summary['n_fills']} status {summary['trade_status_counts']} ({summary['elapsed_sec']}s)")
    return results


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-id", required=True)
    p.add_argument("--strategy", required=True)
    p.add_argument("--engine", default="v1", choices=sorted(ENGINES))
    p.add_argument("--costs", choices=["kr", "paper"], help="cost scenario (default costs.scenario)")
    p.add_argument("--no-costs", action="store_true", help="all cost rates 0; output folder gets @no_costs")
    p.add_argument("--config", default="configs/base.yaml")
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    a = p.parse_args(argv)
    root = Path(a.root)
    cfg = cfg_override(load_config(root / a.config), parse_set_overrides(a.set))
    scenario = "none" if a.no_costs else (a.costs or cfg_get(cfg, "costs.scenario", "kr"))
    paths = Paths(cfg, root)
    run_backtests(cfg, a.run_id, resolve(a.strategy, paths, a.run_id), a.engine, scenario, root)


if __name__ == "__main__":
    main()
