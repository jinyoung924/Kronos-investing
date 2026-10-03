"""Run metadata and atomic file writes shared by every stage.

Every output folder of a stage run carries a meta.json (docs/spec.md, common rule 13):
run_id, stage, strategy, engine, config hash, git commit, input file hashes, run time.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from common.config import config_hash

META_FILENAME = "meta.json"
_CHUNK = 1 << 22


def file_hash(path: str | Path) -> str:
    """sha256 of a file's bytes (streamed)."""
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def git_commit_hash(root: str | Path = ".") -> str | None:
    """HEAD commit of the repo at `root`, or None when git is unavailable."""
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(root), text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except Exception:
        return None


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, Path):
        return obj.as_posix()
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.isoformat()
    if hasattr(obj, "item"):  # numpy scalars
        return obj.item()
    return str(obj)


def write_json_atomic(path: str | Path, doc: Mapping[str, Any]) -> Path:
    """Write JSON via a temp file + os.replace so a crash never leaves a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False, default=_jsonable), encoding="utf-8")
    os.replace(tmp, path)
    return path


def atomic_parquet(df: pd.DataFrame, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)
    return path


def input_hashes(inputs: Iterable[str | Path] | Mapping[str, str | Path] | None) -> dict[str, dict]:
    """{name: {path, sha256, bytes}} for every input file. Missing files are recorded, not raised."""
    if not inputs:
        return {}
    items = inputs.items() if isinstance(inputs, Mapping) else ((Path(p).name, p) for p in inputs)
    out: dict[str, dict] = {}
    for name, p in items:
        p = Path(p)
        if p.is_file():
            out[str(name)] = {"path": p.as_posix(), "sha256": file_hash(p), "bytes": p.stat().st_size}
        else:
            out[str(name)] = {"path": p.as_posix(), "sha256": None, "bytes": None, "missing": True}
    return out


def write_meta(out_dir: str | Path, cfg: dict, inputs=None, extra: Mapping[str, Any] | None = None,
               root: str | Path = ".", filename: str = META_FILENAME) -> Path:
    """Write out_dir/<filename> (default meta.json). `extra` carries run_id / stage / strategy / engine (and anything else)."""
    doc: dict[str, Any] = {
        "run_id": None, "stage": None, "strategy": None, "engine": None,
        "config_hash": config_hash(cfg),
        "git_commit": git_commit_hash(root),
        "inputs": input_hashes(inputs),
        "run_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    doc.update(dict(extra or {}))
    return write_json_atomic(Path(out_dir) / filename, doc)


def read_meta(out_dir: str | Path, filename: str = META_FILENAME) -> dict:
    return json.loads((Path(out_dir) / filename).read_text(encoding="utf-8"))
