"""Pod inputs (appendix C-1 task 3): the data files a GPU pod needs, pinned by a committed sha256 list.

    python -m B_model_infer.pod_inputs write                    # local: (re)write RunPod/inputs.sha256.json, then commit it
    python -m B_model_infer.pod_inputs list                     # repo-relative paths, one per line (rsync --files-from)
    python -m B_model_infer.pod_inputs verify [--src DIR]       # local before upload / pod before the run; exit 1 on any mismatch

The code reaches the pod by `git clone`; the data files are not in git, so RunPod/local.sh uploads them over SSH and the pod
checks them against the list that came with the clone. Inputs = data/raw/{index}/prices.parquet for universe.indices and
data/universe/constituents_{index}[_{variant}].parquet for every inference profile's universe_variant (D-5).
data/krx_raw and stage outputs are never inputs. Paths come from common/paths.py.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.config import cfg_get, load_config  # noqa: E402
from common.meta import file_hash, write_json_atomic  # noqa: E402
from common.paths import Paths  # noqa: E402


def input_files(cfg: dict, paths: Paths) -> list[str]:
    """Repo-relative posix paths of the pod inputs, sorted."""
    profiles = cfg_get(cfg, "infer.profiles", {}) or {}
    variants = sorted({(p or {}).get("universe_variant") or cfg_get(cfg, "universe.variant", "base") for p in profiles.values()})
    files = []
    for idx in cfg_get(cfg, "universe.indices", []):
        files.append(paths.price_file(idx))
        files += [paths.constituents_file(idx, v) for v in variants]
    return sorted(f.relative_to(paths.root).as_posix() for f in files)


def write(cfg: dict, root: Path, log=print) -> Path:
    paths = Paths(cfg, root)
    rels = input_files(cfg, paths)
    missing = [r for r in rels if not (paths.root / r).exists()]
    if missing:
        raise FileNotFoundError(f"pod inputs missing: {missing}")
    entries = {r: {"bytes": (paths.root / r).stat().st_size, "sha256": file_hash(paths.root / r)} for r in rels}
    doc = {"n_files": len(entries), "total_bytes": int(sum(e["bytes"] for e in entries.values())), "files": entries}
    out = write_json_atomic(paths.pod_inputs_file(), doc)
    log(f"wrote {out}: {doc['n_files']} files, {doc['total_bytes']:,} bytes")
    return out


def verify(cfg: dict, root: Path, src: Path | None = None, log=print) -> int:
    """Compare the files under `src` (default: the repo root) with the committed list."""
    paths = Paths(cfg, root)
    f = paths.pod_inputs_file()
    if not f.exists():
        log(f"missing {f}: run `python -m B_model_infer.pod_inputs write` locally and commit it")
        return 1
    doc = json.loads(f.read_text(encoding="utf-8"))
    base = Path(src).resolve() if src else paths.root
    problems = []
    for rel in input_files(cfg, paths):
        if rel not in doc["files"]:
            problems.append(f"UNLISTED {rel} (config asks for it; rewrite the list)")
    for rel, e in doc["files"].items():
        p = base / rel
        if not p.exists():
            problems.append(f"MISSING  {rel}")
        elif p.stat().st_size != e["bytes"]:
            problems.append(f"SIZE     {rel}: expected {e['bytes']:,}, found {p.stat().st_size:,}")
        elif file_hash(p) != e["sha256"]:
            problems.append(f"SHA256   {rel}: content differs")
    if problems:
        log("\n".join(problems))
        log(f"FAIL: {len(problems)} problem(s) under {base}")
        return 1
    log(f"OK: {doc['n_files']} input files under {base} match {f}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("action", choices=["write", "list", "verify"])
    p.add_argument("--src", help="verify: directory holding the uploaded files (default: the repo root)")
    p.add_argument("--config", default="configs/base.yaml")
    p.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    a = p.parse_args(argv)
    root = Path(a.root)
    cfg = load_config(root / a.config)
    if a.action == "write":
        write(cfg, root)
    elif a.action == "list":
        print("\n".join(input_files(cfg, Paths(cfg, root))))
    else:
        sys.exit(verify(cfg, root, Path(a.src) if a.src else None))


if __name__ == "__main__":
    main()
