"""Structure check of a finished prediction run (appendix C-2 step 5), before `checksum write`.

    python -m B_model_infer.verify_run --run-id kronos_base_v1      # exit 1 on any problem
Checks: one as_of file per rebalance date of the manifest (dates logged with n_pred 0 have no file), every file passes
validate_predictions(as_of from the file name) and holds horizon_step 1..pred_len of the manifest. Prints skip reasons summed over dates.
No performance numbers are read (appendix C principle 4).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.config import load_config  # noqa: E402
from common.paths import Paths  # noqa: E402
from common.schema import validate_predictions  # noqa: E402


def verify(paths: Paths, run_id: str, log=print) -> int:
    mf = paths.manifest_file(run_id)
    if not mf.exists():
        log(f"missing {mf}")
        return 1
    m = json.loads(mf.read_text(encoding="utf-8"))
    files = sorted(paths.predictions_dir(run_id).glob("as_of=*.parquet"))
    skipped = m.get("skipped_by_date", {})
    empty = sorted(d for d, s in skipped.items() if s.get("n_pred") == 0)
    expected = int(m["n_rebalance_dates"]) - len(empty)
    problems = []
    if len(files) != expected:
        problems.append(f"COUNT    expected {expected} files ({m['n_rebalance_dates']} rebalance dates - {len(empty)} empty), found {len(files)}")
    for f in files:
        as_of = f.stem.split("=", 1)[1]
        try:
            df = validate_predictions(pd.read_parquet(f), as_of=pd.Timestamp(as_of), horizon=int(m["pred_len"]))
            steps = sorted(int(x) for x in df["horizon_step"].unique())
            if steps != list(range(1, int(m["pred_len"]) + 1)):
                raise ValueError(f"horizon_step values {steps} != 1..{m['pred_len']}")
        except Exception as e:  # noqa: BLE001
            problems.append(f"INVALID  {f.name}: {e}")
    reasons: dict[str, int] = {}
    for s in skipped.values():
        for k, v in (s.get("skip_reasons") or {}).items():
            reasons[k] = reasons.get(k, 0) + int(v)
    log(f"{run_id}: {len(files)} files, {m['n_rebalance_dates']} rebalance dates, {len(empty)} dates without eligible tickers {empty[:5]}")
    log(f"skip reasons (summed over dates): {reasons}")
    if problems:
        log("\n".join(problems))
        log(f"FAIL: {len(problems)} problem(s) in {paths.predictions_dir(run_id)}")
        return 1
    log("OK: file count and validate_predictions")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-id", required=True)
    p.add_argument("--config", default="configs/base.yaml")
    p.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    a = p.parse_args(argv)
    root = Path(a.root)
    sys.exit(verify(Paths(load_config(root / a.config), root), a.run_id))


if __name__ == "__main__":
    main()
