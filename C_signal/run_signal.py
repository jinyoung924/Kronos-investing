#!/usr/bin/env python
"""C_signal entry point: data/B_predictions/{run_id}/ + data/A_prepared/ -> data/C_signals/{run_id}/signals.parquet.

    python -m C_signal.run_signal --run-id fake_oracle_base [--config configs/base.yaml] [--root .]

Per as_of date (D-3): signal rows = that day's backtest universe (data/A_prepared/universe, universe.variant)
∩ tickers present in the prediction file. Baseline features are attached to those rows only. H and N come
from the run_id's manifest; signal.n_samples (D-11) samples are used. last_close = as_of close on
data.price_basis (D-1: raw). meta.json copies profile / H / N and keeps per-date universe, prediction and
exclusion counts. The only module of the stage that touches files; imports only `common`.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from C_signal.aggregate import aggregate_predictions  # noqa: E402
from C_signal.baseline_features import baseline_features, feature_names  # noqa: E402
from common.config import cfg_get, load_config, require  # noqa: E402
from common.meta import atomic_parquet, write_meta  # noqa: E402
from common.paths import Paths, as_of_from_filename  # noqa: E402
from common.schema import validate_predictions, validate_signals  # noqa: E402
from common.universe import get_universe  # noqa: E402

STAGE = "C_signal"


def load_prediction_files(paths: Paths, run_id: str, horizon: int) -> tuple[pd.DataFrame, dict[str, Path]]:
    pdir = paths.predictions_dir(run_id)
    files = sorted(pdir.glob("as_of=*.parquet"))
    if not files:
        raise FileNotFoundError(f"no prediction files under {pdir}")
    frames, inputs = [], {}
    for f in files:
        d = as_of_from_filename(f)
        frames.append(validate_predictions(pd.read_parquet(f), as_of=d, horizon=horizon))
        inputs[f.name] = f
    return pd.concat(frames, ignore_index=True), inputs


def last_close_table(prices: pd.DataFrame, adj_factor: pd.DataFrame, dates, price_basis: str) -> pd.DataFrame:
    """(as_of_date, ticker, last_close): as_of close on the configured basis."""
    px = prices.loc[prices["date"].isin(dates), ["date", "ticker", "close"]].rename(columns={"date": "as_of_date", "close": "last_close"})
    if price_basis == "raw":
        return px
    if price_basis == "adjusted":
        f = adj_factor.rename(columns={"date": "as_of_date"})
        px = px.merge(f, on=["as_of_date", "ticker"], how="left", validate="one_to_one")
        px["last_close"] = px["last_close"] * px["factor"]
        return px[["as_of_date", "ticker", "last_close"]]
    raise ValueError(f"data.price_basis must be raw or adjusted, got {price_basis!r}")


def intersect_universe(preds: pd.DataFrame, universe: pd.DataFrame, manifest: dict) -> tuple[pd.DataFrame, dict]:
    """Keep prediction rows whose ticker is in that day's universe (D-3). Returns (rows, per-date stats)."""
    preds = preds.reset_index(drop=True)
    keep = pd.Series(False, index=preds.index)
    by_date = {}
    skipped_by_date = manifest.get("skipped_by_date", {}) or {}
    for d, grp in preds.groupby("as_of_date", sort=True):
        uni = set(get_universe(universe, d))
        predicted = set(grp["ticker"].unique())
        both = uni & predicted
        keep.loc[grp.index[grp["ticker"].isin(both)]] = True
        sk = skipped_by_date.get(str(pd.Timestamp(d).date()), {})
        by_date[str(pd.Timestamp(d).date())] = {
            "n_universe": len(uni), "n_predicted": len(predicted), "n_signal": len(both),
            "n_universe_without_prediction": len(uni - predicted),
            "n_predicted_outside_universe": len(predicted - uni),
            "skip_reasons": sk.get("skip_reasons") or sk.get("skipped") and {"n": len(sk["skipped"])} or {},
        }
    return preds[keep.to_numpy()], by_date


def build_signals(preds: pd.DataFrame, prices: pd.DataFrame, adj_factor: pd.DataFrame, universe: pd.DataFrame,
                  manifest: dict, cfg: dict) -> tuple[pd.DataFrame, dict]:
    """Pure core of the stage: predictions + prepared tables -> (signals, per-date stats)."""
    horizon = int(manifest["pred_len"])
    n_samples = int(require(cfg, "signal.n_samples"))
    if int(manifest["sample_count"]) < n_samples:
        raise ValueError(f"run manifest sample_count={manifest['sample_count']} < signal.n_samples={n_samples} (D-11)")
    kept, by_date = intersect_universe(preds, universe, manifest)
    if kept.empty:
        raise ValueError("no prediction rows inside the universe")
    dates = sorted(kept["as_of_date"].unique())
    lc = last_close_table(prices, adj_factor, dates, require(cfg, "data.price_basis"))
    sig = aggregate_predictions(kept, lc, horizon, n_samples)
    mw, vw, rw = (int(require(cfg, f"signal.{k}")) for k in ("mom_window", "vol_window", "rev_window"))
    feats = baseline_features(prices, adj_factor, dates, mw, vw, rw, ffill_limit=cfg_get(cfg, "data.ffill_limit"))
    out = sig.merge(feats, on=["as_of_date", "ticker"], how="left", validate="one_to_one")
    names = feature_names(mw, vw, rw)
    out = out[["as_of_date", "ticker", "exp_ret", "exp_ret_mean", "std", "p_up", "pred_range", "n_samples", "last_close",
               names["mom"], names["vol"], names["rev"]]]
    out = validate_signals(out.sort_values(["as_of_date", "ticker"]).reset_index(drop=True))
    # D-3 guarantee: nothing outside the universe, nothing duplicated
    for d, grp in out.groupby("as_of_date"):
        if not set(grp["ticker"]) <= set(get_universe(universe, d)):
            raise AssertionError(f"signal rows outside the universe on {pd.Timestamp(d).date()}")
    return out, by_date


def run_signal(cfg: dict, run_id: str, root: Path, log=print) -> dict:
    paths = Paths(cfg, root)
    mpath = paths.manifest_file(run_id)
    if not mpath.exists():
        raise FileNotFoundError(f"missing {mpath}")
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    t0 = time.time()
    preds, pred_inputs = load_prediction_files(paths, run_id, int(manifest["pred_len"]))
    prices = pd.read_parquet(paths.prepared_path("prices"), columns=["date", "ticker", "market", "close"])
    adj = pd.read_parquet(paths.prepared_path("adj_factor"))
    universe = pd.read_parquet(paths.prepared_path("universe"))
    log(f"{run_id}: profile {manifest.get('profile')}, H {manifest['pred_len']}, N {manifest['sample_count']}, "
        f"{len(pred_inputs)} as_of files, {len(preds):,} prediction rows")
    signals, by_date = build_signals(preds, prices, adj, universe, manifest, cfg)
    out_dir = paths.signals_dir(run_id)
    atomic_parquet(signals, paths.signals_path(run_id))
    n_sig = signals.groupby("as_of_date").size()
    summary = {
        "stage": STAGE, "run_id": run_id, "strategy": None, "engine": None,
        "profile": manifest.get("profile"), "H": int(manifest["pred_len"]), "N": int(manifest["sample_count"]),
        "n_samples_used": int(require(cfg, "signal.n_samples")), "price_basis": require(cfg, "data.price_basis"),
        "prediction_kind": manifest.get("kind", "model"), "leaky": bool(manifest.get("leaky", False)),
        "universe_variant": cfg_get(cfg, "universe.variant"), "n_as_of": int(len(n_sig)),
        "as_of_first": str(pd.Timestamp(n_sig.index.min()).date()), "as_of_last": str(pd.Timestamp(n_sig.index.max()).date()),
        "n_rows": int(len(signals)), "signal_tickers_per_date": {"min": int(n_sig.min()), "max": int(n_sig.max())},
        "totals": {k: int(sum(v[k] for v in by_date.values())) for k in
                   ("n_universe", "n_predicted", "n_signal", "n_universe_without_prediction", "n_predicted_outside_universe")},
        "limitation": "tickers with a halted day inside the inference lookback have no prediction and are excluded from every strategy (D-3)",
        "by_date": by_date, "elapsed_sec": round(time.time() - t0, 1),
    }
    inputs = {"manifest": mpath, "prices": paths.prepared_path("prices"), "adj_factor": paths.prepared_path("adj_factor"),
              "universe": paths.prepared_path("universe"), **pred_inputs}
    write_meta(out_dir, cfg, inputs=inputs, extra=summary, root=root)
    log(f"wrote {paths.signals_path(run_id)}: {len(signals):,} rows, {len(n_sig)} dates, tickers/date {n_sig.min()}..{n_sig.max()}, "
        f"excluded (universe without prediction) {summary['totals']['n_universe_without_prediction']} ({summary['elapsed_sec']}s)")
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-id", required=True)
    p.add_argument("--config", default="configs/base.yaml")
    p.add_argument("--root", default=str(ROOT))
    a = p.parse_args(argv)
    root = Path(a.root)
    run_signal(load_config(root / a.config), a.run_id, root)


if __name__ == "__main__":
    main()
