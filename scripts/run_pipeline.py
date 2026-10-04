#!/usr/bin/env python
"""Run the stage CLIs C -> G in order for one run_id (docs/spec.md Stage 8 task 4).

    python scripts/run_pipeline.py --run-id <id> --strategy a,b|all [--engine v1|v2] [--costs kr|paper] [--from C] [--to G]
                                   [--set key=value ...] [--config configs/base.yaml]

    C  python -m C_signal.run_signal      --run-id
    D  python -m D_strategy.run_strategy  --run-id --strategy
    E  python -m E_backtest.run_backtest  --run-id --strategy            v1: once with costs, once with --no-costs (the cost-free twin)
                                                                         v2: the cost-free v1 run, then --engine v2 --shortfall
    F  python -m F_evaluate.run_evaluate  --run-id --strategy --engine   (v2: v1 first, so portfolio_metrics.csv exists for the report)
    G  python -m G_report.run_report      --run-id
Every step is a subprocess; no stage package is imported here. A failing step stops the run and is named. --set overrides
are passed to D, E, F and G (C takes no overrides).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGES = ["C", "D", "E", "F", "G"]


def build_steps(a) -> list[tuple[str, list[str]]]:
    py = [sys.executable, "-m"]
    common = ["--run-id", a.run_id, "--config", a.config]
    sets = [x for s in a.set for x in ("--set", s)]
    costs = ["--costs", a.costs] if a.costs else []
    strat = ["--strategy", a.strategy]
    steps = [("C", py + ["C_signal.run_signal"] + common),
             ("D", py + ["D_strategy.run_strategy"] + common + strat + sets)]
    back = py + ["E_backtest.run_backtest"] + common + strat + sets
    steps.append(("E", back + ["--no-costs"]))
    evaluate = py + ["F_evaluate.run_evaluate"] + common + strat + sets
    if a.engine == "v1":
        steps.append(("E", back + costs))
        steps.append(("F", evaluate + ["--engine", "v1"]))
    else:
        steps.append(("E", back + costs + ["--engine", "v2", "--shortfall"]))
        steps.append(("F", evaluate + ["--engine", "v1"]))
        steps.append(("F", evaluate + ["--engine", "v2"]))
    report = py + ["G_report.run_report"] + common + sets + (["--shortfall-strategy", a.strategy] if a.strategy != "all" else [])
    steps.append(("G", report))
    lo, hi = STAGES.index(a.from_stage), STAGES.index(a.to_stage)
    return [(s, cmd) for s, cmd in steps if lo <= STAGES.index(s) <= hi]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-id", required=True)
    p.add_argument("--strategy", required=True, help="comma-separated names or all")
    p.add_argument("--engine", default="v1", choices=["v1", "v2"])
    p.add_argument("--costs", choices=["kr", "paper"])
    p.add_argument("--from", dest="from_stage", default="C", choices=STAGES)
    p.add_argument("--to", dest="to_stage", default="G", choices=STAGES)
    p.add_argument("--config", default="configs/base.yaml")
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    a = p.parse_args(argv)
    if STAGES.index(a.from_stage) > STAGES.index(a.to_stage):
        p.error("--from must not come after --to")
    for stage, cmd in build_steps(a):
        t0 = time.time()
        print(f"[{stage}] {' '.join(cmd[1:])}", flush=True)
        r = subprocess.run(cmd, cwd=ROOT)
        if r.returncode != 0:
            print(f"pipeline stopped: stage {stage} failed with exit code {r.returncode} ({' '.join(cmd[2:4])})", file=sys.stderr)
            return r.returncode
        print(f"[{stage}] done in {time.time() - t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
