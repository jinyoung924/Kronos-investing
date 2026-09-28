"""All file locations derive from the config and a run_id. Nothing else builds paths."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from common.config import cfg_get


class Paths:
    def __init__(self, cfg: dict, root: str | Path = "."):
        self.root = Path(root).resolve()
        self.cfg = cfg

    def _dir(self, key: str, default: str) -> Path:
        return self.root / cfg_get(self.cfg, key, default)

    # ---- raw prices -------------------------------------------------------
    def raw_dir(self) -> Path:
        return self._dir("data.raw_dir", "data/raw")

    def price_file(self, index: str) -> Path:
        return self.raw_dir() / index / cfg_get(self.cfg, "data.price_file", "prices.parquet")

    # ---- universe ---------------------------------------------------------
    def universe_dir(self) -> Path:
        return self._dir("data.universe_dir", "data/universe")

    def constituents_file(self, index: str, variant: str | None = None) -> Path:
        """constituents_{index}.parquet for the base variant, constituents_{index}_{variant}.parquet otherwise.
        variant=None reads cfg universe.variant (default "base")."""
        v = variant if variant is not None else cfg_get(self.cfg, "universe.variant", "base") or "base"
        sfx = "" if v == "base" else f"_{v}"
        return self.universe_dir() / f"constituents_{index}{sfx}.parquet"

    # ---- raw KRX cache / vintage ----------------------------------------
    def krx_cache_dir(self, snapshot: str | None = None) -> Path:
        snap = snapshot or cfg_get(self.cfg, "krx.snapshot")
        base = self._dir("krx.cache_dir", "data/krx_raw")
        return base / snap if snap else base

    def logs_dir(self) -> Path:
        return self._dir("data.logs_dir", "logs")

    # ---- predictions ------------------------------------------------------
    def predictions_dir(self, run_id: str) -> Path:
        return self._dir("data.predictions_dir", "data/predictions") / run_id

    def prediction_file(self, run_id: str, as_of) -> Path:
        d = pd.Timestamp(as_of).strftime("%Y-%m-%d")
        return self.predictions_dir(run_id) / f"as_of={d}.parquet"

    def manifest_file(self, run_id: str) -> Path:
        return self.predictions_dir(run_id) / "manifest.json"

    # ---- results ----------------------------------------------------------
    def results_dir(self, run_id: str, strategy: str | None = None) -> Path:
        base = self._dir("data.results_dir", "results") / run_id
        return base / strategy if strategy else base


def as_of_from_filename(path: str | Path) -> pd.Timestamp:
    name = Path(path).stem  # as_of=YYYY-MM-DD
    if not name.startswith("as_of="):
        raise ValueError(f"unexpected prediction filename {path}")
    return pd.Timestamp(name.split("=", 1)[1])
