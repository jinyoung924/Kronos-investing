#!/usr/bin/env python
"""Generate prediction parquet files WITHOUT a GPU, using the fixed schema, so the local
backtest pipeline can be developed and validated before running Kronos on the pod.

    python scripts/make_dummy_predictions.py --run-id dummy_rw                    # pure random walk
    python scripts/make_dummy_predictions.py --run-id dummy_leak --signal-strength 1.0   # LEAKS the future (wiring check only)
    python scripts/make_dummy_predictions.py --run-id dummy_rw --synthetic-data   # also create synthetic prices/universe

--signal-strength > 0 deliberately uses future prices and is recorded in manifest.json as
"leaky": true. Never interpret such a run as a result.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401
from _bootstrap import ROOT

from common.config import cfg_override, load_config
from common.data import load_prices
from common.synthetic import write_synthetic_dataset
from infer.backends import DummyBackend
from infer.run_inference import run


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-id", required=True)
    p.add_argument("--config", default="configs/base.yaml")
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--signal-strength", type=float, default=0.0)
    p.add_argument("--daily-vol", type=float, default=0.02)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--sample-count", type=int); p.add_argument("--lookback", type=int)
    p.add_argument("--synthetic-data", action="store_true", help="write synthetic prices + constituents into data/ first")
    p.add_argument("--n-per-index", type=int, default=30)
    a = p.parse_args(argv)
    root = Path(a.root)
    cfg = cfg_override(load_config(root / a.config), {"model.sample_count": a.sample_count, "model.lookback": a.lookback})
    if a.synthetic_data:
        write_synthetic_dataset(cfg, root, n_per_index=a.n_per_index, seed=a.seed)
        print(f"synthetic prices/universe written under {root/'data'}")
    prices = load_prices(cfg, root, include_benchmark=False)
    backend = DummyBackend(seed=a.seed, daily_vol=a.daily_vol, signal_strength=a.signal_strength,
                           prices=prices if a.signal_strength > 0 else None)
    written = run(cfg, a.run_id, backend, root=root, prices=prices,
                  extra_manifest={"leaky": a.signal_strength > 0, "signal_strength": a.signal_strength, "note": "DUMMY predictions"})
    print(f"wrote {len(written)} date files for run_id={a.run_id}")


if __name__ == "__main__":
    main()
