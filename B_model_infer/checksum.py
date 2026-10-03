"""Checksums of a prediction run (appendix C-1 task 4): data/B_predictions/{run_id}/checksums.json.

    python -m B_model_infer.checksum write  --run-id kronos_base_v1      # on the Pod after the run
    python -m B_model_infer.checksum verify --run-id kronos_base_v1      # locally after download; exit 1 on any mismatch
Covers every as_of=*.parquet file (name, bytes, sha256) plus the code commit. manifest.json is listed with its size only
(it is rewritten on resume). The parquet files are not committed; manifest.json and checksums.json are.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.config import load_config  # noqa: E402
from common.meta import file_hash, git_commit_hash, write_json_atomic  # noqa: E402
from common.paths import Paths  # noqa: E402


def scan(run_dir: Path) -> dict[str, dict]:
    files = sorted(run_dir.glob("as_of=*.parquet"))
    return {f.name: {"bytes": f.stat().st_size, "sha256": file_hash(f)} for f in files}


def write(paths: Paths, run_id: str, root: Path, log=print) -> Path:
    run_dir = paths.predictions_dir(run_id)
    entries = scan(run_dir)
    if not entries:
        raise FileNotFoundError(f"no prediction files under {run_dir}")
    doc = {"run_id": run_id, "n_files": len(entries), "total_bytes": int(sum(e["bytes"] for e in entries.values())),
           "code_commit": git_commit_hash(root), "manifest_bytes": paths.manifest_file(run_id).stat().st_size if paths.manifest_file(run_id).exists() else None,
           "files": entries}
    out = write_json_atomic(paths.checksums_file(run_id), doc)
    log(f"wrote {out}: {doc['n_files']} files, {doc['total_bytes']:,} bytes")
    return out


def verify(paths: Paths, run_id: str, log=print) -> int:
    f = paths.checksums_file(run_id)
    if not f.exists():
        log(f"missing {f}")
        return 1
    doc = json.loads(f.read_text(encoding="utf-8"))
    actual = scan(paths.predictions_dir(run_id))
    problems = []
    for name, e in doc["files"].items():
        a = actual.get(name)
        if a is None:
            problems.append(f"MISSING  {name}")
        elif a["bytes"] != e["bytes"]:
            problems.append(f"SIZE     {name}: expected {e['bytes']:,}, found {a['bytes']:,}")
        elif a["sha256"] != e["sha256"]:
            problems.append(f"SHA256   {name}: content differs")
    for name in actual:
        if name not in doc["files"]:
            problems.append(f"UNLISTED {name}")
    if len(actual) != doc["n_files"]:
        problems.append(f"COUNT    expected {doc['n_files']} files, found {len(actual)}")
    if problems:
        log("\n".join(problems))
        log(f"FAIL: {len(problems)} problem(s) in {paths.predictions_dir(run_id)}")
        return 1
    log(f"OK: {doc['n_files']} prediction files match {f}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("action", choices=["write", "verify"])
    p.add_argument("--run-id", required=True)
    p.add_argument("--config", default="configs/base.yaml")
    p.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    a = p.parse_args(argv)
    root = Path(a.root)
    paths = Paths(load_config(root / a.config), root)
    if a.action == "write":
        write(paths, a.run_id, root)
    else:
        sys.exit(verify(paths, a.run_id))


if __name__ == "__main__":
    main()
