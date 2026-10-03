"""All file locations derive from the config and a run_id. Nothing else builds paths.

Layout (docs/outline.md "전체 흐름과 디렉토리 구조"):
    data/krx_raw/<snapshot>/      raw KRX JSON vintage        (A_data_prepare, docs/data_pipeline.md)
    data/raw/ data/universe/      collected prices, universe  (A_data_prepare, docs/data_pipeline.md)
    data/A_prepared/              Stage 1 outputs             prepared_path(name)
    data/B_predictions/{run_id}/  prediction samples          predictions_dir / prediction_file / manifest_file
    data/C_signals/{run_id}/      signals.parquet             signals_path(run_id)
    data/D_weights/{run_id}/      {strategy}.parquet          weights_path(run_id, strategy)
    data/E_backtest/{run_id}/{engine}/{strategy}/             backtest_dir(run_id, engine, strategy)
    data/F_metrics/{run_id}/      metrics, trials.csv         metrics_dir(run_id)
    reports/{run_id}/             human-readable reports      report_dir(run_id)
"""
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

    # ---- collected data (docs/data_pipeline.md; paths are fixed, do not move) ---------------------
    def raw_dir(self) -> Path:
        return self._dir("data.raw_dir", "data/raw")

    def price_file(self, index: str) -> Path:
        return self.raw_dir() / index / cfg_get(self.cfg, "data.price_file", "prices.parquet")

    def universe_dir(self) -> Path:
        return self._dir("data.universe_dir", "data/universe")

    def constituents_file(self, index: str, variant: str | None = None) -> Path:
        """constituents_{index}.parquet for the base variant, constituents_{index}_{variant}.parquet otherwise.
        variant=None reads cfg universe.variant (default "base")."""
        v = variant if variant is not None else cfg_get(self.cfg, "universe.variant", "base") or "base"
        sfx = "" if v == "base" else f"_{v}"
        return self.universe_dir() / f"constituents_{index}{sfx}.parquet"

    def krx_cache_dir(self, snapshot: str | None = None) -> Path:
        snap = snapshot or cfg_get(self.cfg, "krx.snapshot")
        base = self._dir("krx.cache_dir", "data/krx_raw")
        return base / snap if snap else base

    def logs_dir(self) -> Path:
        return self._dir("data.logs_dir", "logs")

    # ---- A: prepared data -------------------------------------------------------------------------
    def prepared_dir(self) -> Path:
        return self._dir("data.prepared_dir", "data/A_prepared")

    def prepared_path(self, name: str) -> Path:
        """data/A_prepared/{name}.parquet (prices, calendar, halts, adj_factor, events, universe)."""
        return self.prepared_dir() / f"{name}.parquet"

    # ---- B: predictions ---------------------------------------------------------------------------
    def predictions_dir(self, run_id: str) -> Path:
        return self._dir("data.predictions_dir", "data/B_predictions") / run_id

    def prediction_file(self, run_id: str, as_of) -> Path:
        d = pd.Timestamp(as_of).strftime("%Y-%m-%d")
        return self.predictions_dir(run_id) / f"as_of={d}.parquet"

    def manifest_file(self, run_id: str) -> Path:
        return self.predictions_dir(run_id) / "manifest.json"

    # ---- C: signals -------------------------------------------------------------------------------
    def signals_dir(self, run_id: str) -> Path:
        return self._dir("data.signals_dir", "data/C_signals") / run_id

    def signals_path(self, run_id: str) -> Path:
        return self.signals_dir(run_id) / "signals.parquet"

    # ---- D: weights -------------------------------------------------------------------------------
    def weights_dir(self, run_id: str) -> Path:
        return self._dir("data.weights_dir", "data/D_weights") / run_id

    def weights_path(self, run_id: str, strategy: str) -> Path:
        return self.weights_dir(run_id) / f"{strategy}.parquet"

    def weights_meta_path(self, run_id: str, strategy: str) -> Path:
        """One meta file per strategy, next to its weights (several strategies share the run_id folder)."""
        return self.weights_dir(run_id) / f"{strategy}.meta.json"

    # ---- E: backtest ------------------------------------------------------------------------------
    def backtest_dir(self, run_id: str, engine: str, strategy: str) -> Path:
        return self._dir("data.backtest_dir", "data/E_backtest") / run_id / engine / strategy

    # ---- F: metrics -------------------------------------------------------------------------------
    def metrics_dir(self, run_id: str) -> Path:
        return self._dir("data.metrics_dir", "data/F_metrics") / run_id

    # ---- G: reports -------------------------------------------------------------------------------
    def report_dir(self, run_id: str) -> Path:
        return self._dir("data.reports_dir", "reports") / run_id


def as_of_from_filename(path: str | Path) -> pd.Timestamp:
    name = Path(path).stem  # as_of=YYYY-MM-DD
    if not name.startswith("as_of="):
        raise ValueError(f"unexpected prediction filename {path}")
    return pd.Timestamp(name.split("=", 1)[1])
