"""Data manifest: record or verify path / byte size / sha256 for every dataset file.

    python -m data_prepare.manifest            # (re)write data/MANIFEST.json
    python -m data_prepare.manifest --verify   # recompute and compare; exit 1 on any mismatch

Covers data/raw, data/universe and the raw KRX cache of the configured snapshot. Predictions and
results are NOT covered (they carry their own manifest.json per run_id). The manifest is meant to be
committed; the data files are not. A collaborator who copies data/ runs --verify to confirm they hold
the same vintage byte-for-byte. Standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

CHUNK = 1 << 22
SKIP_NAMES = {".DS_Store", ".gitkeep", "MANIFEST.json"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def scan(data_dir: Path, subdirs: list[str], log=lambda *_: None) -> dict[str, dict]:
    files = []
    for sub in subdirs:
        d = data_dir / sub
        if d.exists():
            files += [p for p in d.rglob("*") if p.is_file() and p.name not in SKIP_NAMES and not p.name.endswith(".tmp")]
    files = sorted(set(files))
    out = {}
    for i, p in enumerate(files, 1):
        rel = p.relative_to(data_dir).as_posix()
        if i % 500 == 0 or i == len(files):
            log(f"  hashing [{i}/{len(files)}] {rel}")
        out[rel] = {"bytes": p.stat().st_size, "sha256": sha256(p)}
    return out


def write(data_dir: Path, subdirs: list[str], snapshot: str | None, log=print) -> Path:
    entries = scan(data_dir, subdirs, log)
    doc = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "snapshot": snapshot, "root": "data/", "subdirs": subdirs,
        "n_files": len(entries), "total_bytes": sum(e["bytes"] for e in entries.values()),
        "files": entries,
    }
    out = data_dir / "MANIFEST.json"
    out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    log(f"wrote {out}: {doc['n_files']} files, {doc['total_bytes']:,} bytes")
    return out


def verify(data_dir: Path, log=print) -> int:
    mf = data_dir / "MANIFEST.json"
    if not mf.exists():
        log(f"missing {mf}; write it first")
        return 1
    doc = json.loads(mf.read_text(encoding="utf-8"))
    actual = scan(data_dir, doc["subdirs"], log)
    expected = doc["files"]
    problems = []
    for rel, e in expected.items():
        a = actual.get(rel)
        if a is None:
            problems.append(f"MISSING   {rel}")
        elif a["bytes"] != e["bytes"]:
            problems.append(f"SIZE      {rel}: expected {e['bytes']:,}, found {a['bytes']:,}")
        elif a["sha256"] != e["sha256"]:
            problems.append(f"SHA256    {rel}: content differs")
    for rel in actual:
        if rel not in expected:
            problems.append(f"UNLISTED  {rel}")
    if problems:
        log("\n".join(problems))
        log(f"FAIL: {len(problems)} problem(s) across {len(expected)} listed files")
        return 1
    log(f"OK: {len(expected)} files match {mf} (snapshot {doc.get('snapshot')})")
    return 0


def default_subdirs(cfg: dict) -> list[str]:
    from common.config import cfg_get
    subs = [str(Path(cfg_get(cfg, "data.raw_dir", "data/raw")).relative_to("data")),
            str(Path(cfg_get(cfg, "data.universe_dir", "data/universe")).relative_to("data"))]
    snap = cfg_get(cfg, "krx.snapshot")
    cache = Path(cfg_get(cfg, "krx.cache_dir", "data/krx_raw")).relative_to("data")
    subs.append(str(cache / snap) if snap else str(cache))
    return subs


def main(argv=None):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from common.config import cfg_get, load_config
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--config", default="configs/base.yaml")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    a = ap.parse_args(argv)
    root = Path(a.root)
    cfg = load_config(root / a.config)
    data_dir = root / "data"
    if a.verify:
        sys.exit(verify(data_dir))
    write(data_dir, default_subdirs(cfg), cfg_get(cfg, "krx.snapshot"))


if __name__ == "__main__":
    main()
