"""Pod bundle (appendix C-1 task 3): everything the GPU pod needs, in one tar.gz, with an inputs.sha256.json inside.

    python -m B_model_infer.pod_bundle pack   --run-id kronos_base_v1 [--profile base] [--allow-dirty]
    python -m B_model_infer.pod_bundle verify --dir <unpacked bundle dir>          # exit 1 on any mismatch

pack  -> data/pod_bundles/{run_id}.tar.gz containing
         repo/            project code from `git archive HEAD` minus data/, docs/, reports/ (refused when tracked files are modified
                          unless --allow-dirty; untracked files are never included), with the WORKING copies of configs/base.yaml, requirements.txt
                          and requirements-infer.txt on top so the config that is packed is the one that will run
         repo/data/raw/{index}/prices.parquet, repo/data/universe/constituents_{index}[_{variant}].parquet for the
                          profile's universe_variant (D-5)
         repo/inputs.sha256.json   path, bytes, sha256 of every packed file + code commit + dirty flag + run_id/profile
         data/krx_raw is never packed. Paths come from common/paths.py.
verify -> recomputes every entry of inputs.sha256.json under --dir and exits 1 on a missing / changed / extra data file.
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.config import cfg_get, load_config  # noqa: E402
from common.meta import file_hash, git_commit_hash  # noqa: E402
from common.paths import Paths  # noqa: E402

INPUTS_FILE = "inputs.sha256.json"
WORKING_COPIES = ["configs/base.yaml", "requirements.txt", "requirements-infer.txt"]
ARCHIVE_DROP = ("data", "docs", "reports")
FORBIDDEN_PREFIXES = ("data/krx_raw", "data/B_predictions", "data/C_signals", "data/D_weights", "data/E_backtest", "data/F_metrics", "data/A_prepared", "reports/")


def git_dirty(root: Path) -> list[str]:
    out = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root, text=True)
    return [line for line in out.splitlines() if line.strip()]


def data_files(cfg: dict, paths: Paths, profile: str) -> list[Path]:
    variant = cfg_get(cfg, f"infer.profiles.{profile}.universe_variant") or cfg_get(cfg, "universe.variant", "base")
    files = []
    for idx in cfg_get(cfg, "universe.indices", []):
        files += [paths.price_file(idx), paths.constituents_file(idx, variant)]
    missing = [f for f in files if not f.exists()]
    if missing:
        raise FileNotFoundError(f"bundle inputs missing: {[str(m) for m in missing]}")
    return files


def pack(cfg: dict, run_id: str, profile: str, root: Path, allow_dirty: bool = False, log=print) -> Path:
    paths = Paths(cfg, root)
    dirty = git_dirty(root)
    if dirty and not allow_dirty:
        raise RuntimeError(f"working tree has {len(dirty)} modified tracked file(s) (e.g. {dirty[:3]}); commit them or pass --allow-dirty")
    commit = git_commit_hash(root)
    out = paths.bundle_path(run_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "repo"
        stage.mkdir()
        archive = subprocess.check_output(["git", "archive", "--format=tar", "HEAD"], cwd=root)
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tf:
            tf.extractall(stage)
        for sub in ARCHIVE_DROP:                      # tracked metadata under data/ (manifests, .gitkeep) and docs are not pod inputs
            shutil.rmtree(stage / sub, ignore_errors=True)
        for rel in WORKING_COPIES:
            src = root / rel
            if src.exists():
                (stage / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, stage / rel)
        for f in data_files(cfg, paths, profile):
            rel = f.resolve().relative_to(root.resolve())
            dst = stage / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst)
        entries = {}
        for p in sorted(x for x in stage.rglob("*") if x.is_file()):
            rel = p.relative_to(stage).as_posix()
            if rel.startswith(FORBIDDEN_PREFIXES):
                raise RuntimeError(f"refusing to pack {rel}")
            entries[rel] = {"bytes": p.stat().st_size, "sha256": file_hash(p)}
        doc = {"run_id": run_id, "profile": profile, "code_commit": commit, "git_dirty": bool(dirty), "dirty_files": dirty,
               "n_files": len(entries), "total_bytes": int(sum(e["bytes"] for e in entries.values())), "files": entries}
        (stage / INPUTS_FILE).write_text(json.dumps(doc, indent=2), encoding="utf-8")
        with tarfile.open(out, "w:gz") as tf:
            tf.add(stage, arcname="repo")
    log(f"wrote {out}: {doc['n_files']} files, {doc['total_bytes']:,} bytes, commit {commit}{' (dirty)' if dirty else ''}")
    return out


def verify(bundle_dir: Path, log=print) -> int:
    f = bundle_dir / INPUTS_FILE
    if not f.exists():
        log(f"missing {f}")
        return 1
    doc = json.loads(f.read_text(encoding="utf-8"))
    problems = []
    for rel, e in doc["files"].items():
        p = bundle_dir / rel
        if not p.exists():
            problems.append(f"MISSING  {rel}")
        elif p.stat().st_size != e["bytes"]:
            problems.append(f"SIZE     {rel}")
        elif file_hash(p) != e["sha256"]:
            problems.append(f"SHA256   {rel}")
    for p in bundle_dir.rglob("*.parquet"):
        rel = p.relative_to(bundle_dir).as_posix()
        if rel not in doc["files"]:
            problems.append(f"UNLISTED {rel}")
    if problems:
        log("\n".join(problems))
        log(f"FAIL: {len(problems)} problem(s) under {bundle_dir}")
        return 1
    log(f"OK: {doc['n_files']} files match {f} (commit {doc['code_commit']}{', dirty' if doc.get('git_dirty') else ''})")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("action", choices=["pack", "verify"])
    p.add_argument("--run-id")
    p.add_argument("--profile")
    p.add_argument("--allow-dirty", action="store_true")
    p.add_argument("--dir", help="verify: directory where the bundle's repo/ was unpacked (default: this repo root)")
    p.add_argument("--config", default="configs/base.yaml")
    p.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    a = p.parse_args(argv)
    root = Path(a.root)
    if a.action == "pack":
        if not a.run_id:
            raise SystemExit("pack needs --run-id")
        cfg = load_config(root / a.config)
        pack(cfg, a.run_id, a.profile or cfg_get(cfg, "infer.default_profile", "base"), root, a.allow_dirty)
    else:
        sys.exit(verify(Path(a.dir) if a.dir else root))


if __name__ == "__main__":
    main()
