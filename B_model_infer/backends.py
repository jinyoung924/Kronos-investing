"""Prediction backends sharing one interface:

    backend.predict(batch: Batch, horizon: int, sample_count: int) -> np.ndarray
        shape (n_tickers, sample_count, horizon, 5)  columns: open, high, low, close, volume

`KronosBackend` wraps KronosPredictor.predict_batch. Kronos averages the `sample_count`
samples internally, so to keep RAW sample paths each series is replicated sample_count
times in the batch and predict_batch is called with sample_count=1.
`DummyBackend` draws random-walk paths (no torch needed) for local pipeline development.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from B_model_infer.build_batch import Batch


class DummyBackend:
    """Random-walk sampler. With prices + signal_strength > 0 it deliberately LEAKS the realized
    future close into the predicted mean; that mode exists only to validate the pipeline wiring
    (a leaky run must show large returns) and is labelled as such in the manifest."""

    name = "dummy"

    def __init__(self, seed: int = 0, daily_vol: float = 0.02, signal_strength: float = 0.0,
                 prices: pd.DataFrame | None = None):
        self.seed = seed
        self.daily_vol = daily_vol
        self.signal_strength = signal_strength
        self._future_close = None
        if signal_strength > 0:
            if prices is None:
                raise ValueError("signal_strength > 0 requires prices for the leaked future")
            adj = prices.pivot(index="date", columns="ticker", values="close").sort_index()
            fac = prices.pivot(index="date", columns="ticker", values="adj_factor").sort_index()
            self._future_close = (adj * fac).ffill()

    def _leaked_drift(self, batch: Batch, horizon: int) -> np.ndarray:
        if self._future_close is None:
            return np.zeros(len(batch))
        cal = self._future_close.index
        pos = cal.searchsorted(batch.as_of, side="right") - 1
        drift = np.zeros(len(batch))
        for i, t in enumerate(batch.tickers):
            if t not in self._future_close.columns or pos + horizon >= len(cal):
                continue
            p0 = self._future_close[t].iloc[pos]
            p1 = self._future_close[t].iloc[pos + horizon]
            if np.isfinite(p0) and np.isfinite(p1) and p0 > 0:
                drift[i] = (p1 / p0 - 1.0) * self.signal_strength
        return drift

    def predict(self, batch: Batch, horizon: int, sample_count: int) -> np.ndarray:
        rng = np.random.default_rng([self.seed, int(batch.as_of.value // 10**9)])
        n = len(batch)
        drift = self._leaked_drift(batch, horizon) / horizon
        eps = rng.normal(0.0, self.daily_vol, size=(n, sample_count, horizon))
        logret = np.cumsum(eps + drift[:, None, None], axis=2)
        close = batch.last_close[:, None, None] * np.exp(logret)
        prev = np.concatenate([np.repeat(batch.last_close[:, None, None], sample_count, axis=1), close[:, :, :-1]], axis=2)
        open_ = prev * np.exp(rng.normal(0, self.daily_vol / 4, size=close.shape))
        hi = np.maximum(open_, close) * np.exp(np.abs(rng.normal(0, self.daily_vol / 2, size=close.shape)))
        lo = np.minimum(open_, close) * np.exp(-np.abs(rng.normal(0, self.daily_vol / 2, size=close.shape)))
        last_vol = np.array([float(df["volume"].iloc[-1]) for df in batch.df_list])
        vol = last_vol[:, None, None] * np.exp(rng.normal(0, 0.3, size=close.shape))
        return np.stack([open_, hi, lo, close, vol], axis=-1)


class KronosBackend:
    name = "kronos"

    def __init__(self, model_name: str, tokenizer_name: str, revision: str | None = None,
                 kronos_repo: str | Path | None = None, device: str = "cuda:0", max_context: int = 512,
                 temperature: float = 1.0, top_p: float = 0.9, top_k: int = 0,
                 batch_size: int = 256, seed: int = 0, tokenizer_revision: str | None = None):
        if kronos_repo is not None:
            repo = str(Path(kronos_repo).resolve())
            if repo not in sys.path:
                sys.path.insert(0, repo)
        import torch  # noqa: WPS433 (lazy: only the pod has torch)
        from model import Kronos, KronosPredictor, KronosTokenizer  # provided by the Kronos repo

        self.torch = torch
        self.model_name, self.tokenizer_name, self.revision = model_name, tokenizer_name, revision
        self.tokenizer_revision = tokenizer_revision if tokenizer_revision is not None else revision
        self.temperature, self.top_p, self.top_k = temperature, top_p, top_k
        self.batch_size = max(1, int(batch_size))
        self.seed = seed
        self.device = device
        tokenizer = KronosTokenizer.from_pretrained(tokenizer_name, **({"revision": self.tokenizer_revision} if self.tokenizer_revision else {}))
        model = Kronos.from_pretrained(model_name, **({"revision": revision} if revision else {}))
        self.predictor = KronosPredictor(model, tokenizer, device=device, max_context=max_context)
        self.resolved_revision = self._resolve_revision()

    def _resolve_revision(self) -> dict:
        out = {}
        try:
            from huggingface_hub import model_info
            for key, name, rev in (("model", self.model_name, self.revision), ("tokenizer", self.tokenizer_name, self.tokenizer_revision)):
                if Path(name).exists():
                    out[key] = f"local:{Path(name).resolve()}"
                else:
                    out[key] = model_info(name, revision=rev).sha
        except Exception as e:  # offline pod, private repo, ...
            out["error"] = repr(e)
        return out

    def predict(self, batch: Batch, horizon: int, sample_count: int) -> np.ndarray:
        n = len(batch)
        self.torch.manual_seed(self.seed + int(batch.as_of.value // 10**9) % (2**31))
        per_chunk = max(1, self.batch_size // sample_count)
        out = np.empty((n, sample_count, horizon, 5), dtype=np.float64)
        for start in range(0, n, per_chunk):
            idx = list(range(start, min(n, start + per_chunk)))
            dfs = [batch.df_list[i] for i in idx for _ in range(sample_count)]
            xts = [batch.x_timestamps[i] for i in idx for _ in range(sample_count)]
            yts = [batch.y_timestamps[i] for i in idx for _ in range(sample_count)]
            preds = self.predictor.predict_batch(
                df_list=dfs, x_timestamp_list=xts, y_timestamp_list=yts, pred_len=horizon,
                T=self.temperature, top_k=self.top_k, top_p=self.top_p, sample_count=1, verbose=False,
            )
            arr = np.stack([p[["open", "high", "low", "close", "volume"]].to_numpy(dtype=np.float64) for p in preds])
            out[idx] = arr.reshape(len(idx), sample_count, horizon, 5)
        return out
