"""Logger writing to stdout and logs/<name>.log with timestamps (same pattern for every step)."""
from __future__ import annotations

import logging
import sys
from pathlib import Path


def get_logger(name: str, logs_dir: str | Path = "logs") -> logging.Logger:
    log = logging.getLogger(name)
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s | %(name)s | %(levelname)s | %(message)s", "%H:%M:%S")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)
    Path(logs_dir).mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(Path(logs_dir) / f"{name}.log", encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    log.propagate = False
    return log
