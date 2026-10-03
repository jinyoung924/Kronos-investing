#!/usr/bin/env python
"""Fake predictions for local pipeline tests, in the exact prediction schema (docs/spec.md Stage 2).

    python -m B_model_infer.make_fake_predictions --kind dummy|oracle [--profile base] [--run-id fake_<kind>_<profile>]
                                                  [--start --end] [--config configs/base.yaml] [--root .]

Both kinds go through B_model_infer.run_inference.run, so as_of dates (rebalance_dates of the profile), the
universe, the eligibility rule (qualify_window, D-3) and the manifest are exactly those of a real run.

dummy  : DummyBackend random walk, pred_close = last_close * exp(cumsum N(0, SIGMA_DUMMY)) per sample and step
         (no information about the future). SIGMA_DUMMY is a test constant.
oracle : the realised future (D-10), deliberately leaky, for engine wiring tests only:
         price at horizon_step h = raw(t_h) * F(t_h) / F(as_of), t_h = h-th trading day after as_of, so the
         prediction stays on the as_of RAW scale and a future split does not look like a crash. Future halted
         days (no open/high/low) take the day's reference close with volume 0; days without a bar (delisted)
         carry the last valid close with volume 0 and are counted in the manifest. Multiplicative noise
         exp(N(0, EPS_ORACLE)) keeps std > 0. The F ratio is taken from the collected adj_factor column
         (adj_t / adj_s == F_t / F_s, tests/test_stage1_data.py).
Seeds derive from project.seed (D-7).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from B_model_infer.backends import DummyBackend  # noqa: E402
from B_model_infer.build_batch import Batch  # noqa: E402
from B_model_infer.run_inference import profile_cfg, run  # noqa: E402
from common.config import cfg_get, cfg_override, load_config  # noqa: E402
from common.data import load_prices, to_wide, trading_calendar  # noqa: E402
from common.meta import write_json_atomic  # noqa: E402
from common.paths import Paths  # noqa: E402
from common.universe import load_constituents  # noqa: E402

SIGMA_DUMMY = 0.02     # daily log-vol of the dummy random walk (test constant, spec Stage 2 task 2)
EPS_ORACLE = 1e-4      # oracle multiplicative noise scale (test constant, D-10)
KINDS = ("dummy", "oracle")


class OracleBackend:
    """predict() returns the realised future path of every ticker in the batch (see module docstring)."""

    name = "oracle"

    def __init__(self, prices: pd.DataFrame, seed: int = 0, eps: float = EPS_ORACLE):
        self.seed, self.eps = int(seed), float(eps)
        self.cal = trading_calendar(prices)
        wide = {c: to_wide(prices, c).reindex(self.cal) for c in ("open", "high", "low", "close", "volume", "adj_factor")}
        self.has_bar = wide["close"].notna()
        self.orig_open = wide["open"]                       # NaN on halted days and days without a bar
        self.close = wide["close"].ffill()                 # no bar (delisted) -> last valid close
        self.adj = wide["adj_factor"].ffill()
        self.open = wide["open"].where(wide["open"].notna(), self.close)   # halted / no bar -> reference close
        self.high = wide["high"].where(wide["high"].notna(), self.close)
        self.low = wide["low"].where(wide["low"].notna(), self.close)
        self.volume = wide["volume"].fillna(0.0)
        self.fill_by_date: dict[str, dict] = {}

    def predict(self, batch: Batch, horizon: int, sample_count: int) -> np.ndarray:
        as_of = batch.as_of
        pos = self.cal.searchsorted(as_of, side="right") - 1      # last trading day <= as_of
        if pos < 0 or pos + horizon >= len(self.cal):
            raise ValueError(f"oracle needs {horizon} trading days after {as_of.date()}, calendar ends {self.cal[-1].date()}")
        future = self.cal[pos + 1: pos + 1 + horizon]
        cols = batch.tickers
        n = len(cols)
        adj0 = self.adj.iloc[pos][cols].to_numpy()               # F(as_of) (ffilled for a ticker holiday)
        scale = self.adj.loc[future, cols].to_numpy() / adj0[None, :]        # (H, n): F(t_h) / F(as_of)
        o, h, l, c = (w.loc[future, cols].to_numpy() * scale for w in (self.open, self.high, self.low, self.close))
        v = self.volume.loc[future, cols].to_numpy()
        no_bar = ~self.has_bar.loc[future, cols].to_numpy()
        halted = self.has_bar.loc[future, cols].to_numpy() & self.orig_open.loc[future, cols].isna().to_numpy()
        self.fill_by_date[str(as_of.date())] = {
            "no_bar_fill_tickers": int(no_bar.any(axis=0).sum()), "no_bar_fill_cells": int(no_bar.sum()),
            "halted_fill_tickers": int(halted.any(axis=0).sum()), "halted_fill_cells": int(halted.sum()),
        }
        rng = np.random.default_rng([self.seed, int(as_of.value // 10**9)])
        noise = np.exp(rng.normal(0.0, self.eps, size=(n, sample_count, horizon)))   # same factor for O/H/L/C of a step
        out = np.empty((n, sample_count, horizon, 5), dtype=np.float64)
        for k, arr in enumerate((o, h, l, c)):
            out[:, :, :, k] = arr.T[:, None, :] * noise
        out[:, :, :, 4] = np.repeat(v.T[:, None, :], sample_count, axis=1)
        if not np.isfinite(out[:, :, :, :4]).all() or (out[:, :, :, :4] <= 0).any():
            raise ValueError(f"oracle produced non-positive or non-finite prices on {as_of.date()}")
        return out


def make_backend(kind: str, cfg: dict, prices: pd.DataFrame):
    seed = int(cfg_get(cfg, "project.seed", 0))
    if kind == "dummy":
        return DummyBackend(seed=seed, daily_vol=SIGMA_DUMMY, signal_strength=0.0)
    if kind == "oracle":
        return OracleBackend(prices, seed=seed, eps=EPS_ORACLE)
    raise ValueError(f"kind must be one of {KINDS}")


def make_fake(cfg: dict, kind: str, profile: str | None = None, run_id: str | None = None, root: Path = Path("."),
              prices: pd.DataFrame | None = None, constituents: dict | None = None, log=print) -> tuple[str, list[pd.Timestamp]]:
    """Write fake predictions for one profile. Returns (run_id, dates written)."""
    prof = profile_cfg(cfg, profile)
    run_id = run_id or f"fake_{kind}_{prof['profile']}"
    if prices is None:
        prices = load_prices(cfg, root, include_benchmark=False)
    if constituents is None:
        constituents = load_constituents(cfg, root)
    backend = make_backend(kind, cfg, prices)
    extra = {"kind": kind, "fake": True, "leaky": kind == "oracle",
             "note": ("oracle: realised future prices on the as_of raw scale + exp(N(0, eps)) noise; for engine/wiring tests only"
                      if kind == "oracle" else "dummy: random walk around last_close, carries no information about the future"),
             "sigma_dummy": SIGMA_DUMMY if kind == "dummy" else None, "eps_oracle": EPS_ORACLE if kind == "oracle" else None}
    written = run(cfg, run_id, backend, root=root, prices=prices, constituents=constituents, log=log,
                  extra_manifest=extra, profile=prof["profile"])
    if kind == "oracle":
        mpath = Paths(cfg, root).manifest_file(run_id)
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
        manifest["oracle_fill_by_date"] = backend.fill_by_date
        manifest["oracle_fill_total"] = {k: int(sum(d[k] for d in backend.fill_by_date.values())) for k in
                                         ("no_bar_fill_tickers", "no_bar_fill_cells", "halted_fill_tickers", "halted_fill_cells")}
        write_json_atomic(mpath, manifest)
    return run_id, written


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--kind", required=True, choices=KINDS)
    p.add_argument("--profile", help="inference profile; default infer.default_profile (D-12: base)")
    p.add_argument("--run-id", help="default fake_<kind>_<profile>")
    p.add_argument("--config", default="configs/base.yaml")
    p.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    p.add_argument("--start"); p.add_argument("--end")
    a = p.parse_args(argv)
    root = Path(a.root)
    cfg = cfg_override(load_config(root / a.config), {"period.start": a.start, "period.end": a.end})
    run_id, written = make_fake(cfg, a.kind, a.profile, a.run_id, root=root)
    print(f"done: {a.kind} run_id={run_id}: {len(written)} new date files in {Paths(cfg, root).predictions_dir(run_id)}")


if __name__ == "__main__":
    main()
