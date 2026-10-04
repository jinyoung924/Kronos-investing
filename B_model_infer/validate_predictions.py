#!/usr/bin/env python
"""Check a real prediction folder and write the summary the Stage 6 report quotes (docs/spec.md Stage 6 task 4).

    python -m B_model_infer.validate_predictions --run-id kronos_base_v1      # -> data/B_predictions/{run_id}/validation.json

1. manifest vs configs: profile, lookback, pred_len, step, sample_count, temperature, top_p of infer.profiles.<profile>
2. as_of dates vs rebalance_dates(period.start, period.end, step): missing / unexpected dates
3. per date: predicted tickers vs the inference universe (manifest universe_variant) and vs the backtest universe
   (data/A_prepared/universe): members without predictions, predictions outside
4. every file: horizon_step = 1..H for every ticker, sample_count samples per ticker
5. price basis: for tickers whose adjusted close differs from the raw close on as_of (a split or other reference-price
   event lies after as_of), is the step-1 predicted close nearer the raw or the adjusted close?
No realised return is read: structure only. Exit 1 when a hard check (1, 2, 4) fails.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.calendar import rebalance_dates  # noqa: E402
from common.config import cfg_get, load_config  # noqa: E402
from common.data import load_prices, trading_calendar  # noqa: E402
from common.meta import write_json_atomic  # noqa: E402
from common.paths import Paths, as_of_from_filename  # noqa: E402
from common.schema import validate_predictions  # noqa: E402
from common.universe import combined_universe_at, get_universe, load_constituents  # noqa: E402

PROFILE_KEYS = ("profile", "lookback", "pred_len", "step", "sample_count", "temperature", "top_p")
SPLIT_TOL = 0.01          # |adjusted / raw - 1| above this = the two bases are distinguishable for that ticker
LARGE_TOL = 0.20          # ... above this = a real split / large event, where one week of predicted drift cannot blur the answer


def check_manifest(manifest: dict, profile: dict) -> dict:
    """{key: {manifest, config}} for every profile key that differs (empty = match)."""
    return {k: {"manifest": manifest.get(k), "config": profile.get(k)} for k in PROFILE_KEYS if manifest.get(k) != profile.get(k)}


def check_dates(found: pd.DatetimeIndex, expected: pd.DatetimeIndex) -> dict:
    fmt = lambda idx: [str(pd.Timestamp(d).date()) for d in idx]  # noqa: E731
    return {"n_expected": int(len(expected)), "n_found": int(len(found)),
            "missing": fmt(expected.difference(found)), "unexpected": fmt(found.difference(expected))}


def check_shape(df: pd.DataFrame, horizon: int, sample_count: int) -> dict:
    """Tickers whose horizon steps are not exactly 1..H / whose sample count is not sample_count."""
    g = df.groupby("ticker")
    steps = g["horizon_step"].agg(lambda s: sorted(set(int(x) for x in s)) == list(range(1, int(horizon) + 1)))
    n_samples = g["sample_id"].nunique()
    return {"n_tickers": int(len(steps)), "bad_steps": int((~steps).sum()), "bad_sample_count": int((n_samples != int(sample_count)).sum())}


def price_basis(step1: pd.DataFrame, closes: pd.DataFrame) -> dict:
    """step1: (ticker, pred_close) = mean step-1 predicted close. closes: (ticker, raw_close, adj_close) on as_of.
    Only tickers whose two closes differ by more than SPLIT_TOL can tell the bases apart."""
    m = step1.merge(closes, on="ticker", how="inner")
    m = m[(m["raw_close"] > 0) & (m["adj_close"] > 0)]
    split = m[(m["adj_close"] / m["raw_close"] - 1).abs() > SPLIT_TOL]
    d_raw = np.log(split["pred_close"] / split["raw_close"]).abs()
    d_adj = np.log(split["pred_close"] / split["adj_close"]).abs()
    med = lambda s: float(s.median()) if len(s) else None  # noqa: E731
    large = ((split["adj_close"] / split["raw_close"] - 1).abs() > LARGE_TOL).to_numpy()
    return {"n_tickers": int(len(m)), "n_split_tickers": int(len(split)),
            "n_large": int(large.sum()), "large_closer_to_raw": int((d_raw < d_adj).to_numpy()[large].sum()),
            "closer_to_raw": int((d_raw < d_adj).sum()), "closer_to_adjusted": int((d_adj <= d_raw).sum()),
            "median_ratio_to_raw": med(split["pred_close"] / split["raw_close"]),
            "median_ratio_to_adjusted": med(split["pred_close"] / split["adj_close"]),
            "median_ratio_to_raw_all": med(m["pred_close"] / m["raw_close"])}


def validate_run(cfg: dict, run_id: str, root: Path, log=print) -> dict:
    paths = Paths(cfg, root)
    manifest = json.loads(paths.manifest_file(run_id).read_text(encoding="utf-8"))
    name = manifest.get("profile")
    profile = {"profile": name, **(cfg_get(cfg, f"infer.profiles.{name}") or {})}
    horizon, sample_count = int(manifest["pred_len"]), int(manifest["sample_count"])

    prices = load_prices(cfg, root, include_benchmark=False)
    calendar = trading_calendar(prices)
    expected = rebalance_dates(calendar, cfg_get(cfg, "period.start"), cfg_get(cfg, "period.end"), int(manifest["step"]))
    files = sorted(paths.predictions_dir(run_id).glob("as_of=*.parquet"))
    found = pd.DatetimeIndex([as_of_from_filename(f) for f in files])

    infer_cons = load_constituents(cfg, root, variant=manifest.get("universe_variant"))
    bt_universe = pd.read_parquet(paths.prepared_path("universe"))
    px = prices[["date", "ticker", "close", "adj_factor"]].copy()
    px["adj_close"] = px["close"] * px["adj_factor"]

    shape_bad, per_date, step1_all, closes_all = [], [], [], []
    for f, d in zip(files, found):
        df = validate_predictions(pd.read_parquet(f), as_of=d, horizon=horizon)
        sh = check_shape(df, horizon, sample_count)
        if sh["bad_steps"] or sh["bad_sample_count"]:
            shape_bad.append({"date": str(d.date()), **sh})
        pred = set(df["ticker"])
        infer_u = set(combined_universe_at(infer_cons, d)["ticker"])
        bt_u = set(get_universe(bt_universe, d))
        per_date.append({"date": d, "n_pred": len(pred), "infer_universe": len(infer_u), "infer_missing": len(infer_u - pred),
                         "pred_outside_infer": len(pred - infer_u), "backtest_universe": len(bt_u), "backtest_missing": len(bt_u - pred)})
        s1 = df[df["horizon_step"] == 1].groupby("ticker", as_index=False)["pred_close"].mean()
        day = px[px["date"] == d].rename(columns={"close": "raw_close"})[["ticker", "raw_close", "adj_close"]]
        step1_all.append(s1.assign(date=d)); closes_all.append(day.assign(date=d))
    pdx = pd.DataFrame(per_date)
    s1 = pd.concat(step1_all, ignore_index=True); cl = pd.concat(closes_all, ignore_index=True)
    s1["ticker"] = s1["ticker"] + "@" + s1["date"].dt.strftime("%Y-%m-%d"); cl["ticker"] = cl["ticker"] + "@" + cl["date"].dt.strftime("%Y-%m-%d")
    stat = lambda c: {"min": int(pdx[c].min()), "mean": round(float(pdx[c].mean()), 1), "max": int(pdx[c].max()), "total": int(pdx[c].sum())}  # noqa: E731
    out = {
        "run_id": run_id, "profile": name, "manifest_vs_config": check_manifest(manifest, profile),
        "dates": check_dates(found, expected), "shape_problems": shape_bad,
        "universe": {c: stat(c) for c in ("n_pred", "infer_universe", "infer_missing", "pred_outside_infer", "backtest_universe", "backtest_missing")},
        "universe_variant": manifest.get("universe_variant"), "skip_reasons": _sum_reasons(manifest),
        "price_basis": {**price_basis(s1[["ticker", "pred_close"]], cl[["ticker", "raw_close", "adj_close"]]), "configured": cfg_get(cfg, "data.price_basis")},
        "hf_revision_resolved": manifest.get("hf_revision_resolved"), "code_commit": manifest.get("code_commit"),
    }
    out["ok"] = not out["manifest_vs_config"] and not out["dates"]["missing"] and not out["dates"]["unexpected"] and not shape_bad
    write_json_atomic(paths.validation_file(run_id), out)
    log(json.dumps(out, indent=2, ensure_ascii=False))
    return out


def _sum_reasons(manifest: dict) -> dict:
    total: dict[str, int] = {}
    for s in (manifest.get("skipped_by_date") or {}).values():
        for k, v in (s.get("skip_reasons") or {}).items():
            total[k] = total.get(k, 0) + int(v)
    return total


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-id", required=True)
    p.add_argument("--config", default="configs/base.yaml")
    p.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    a = p.parse_args(argv)
    root = Path(a.root)
    out = validate_run(load_config(root / a.config), a.run_id, root)
    sys.exit(0 if out["ok"] else 1)


if __name__ == "__main__":
    main()
