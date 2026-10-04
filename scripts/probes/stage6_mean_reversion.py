"""Stage 6 probe: how much of the Kronos signal is "revert to the mean of the input window"?

For every (as_of, ticker) of a run_id: z = (last close - window mean) / window std over the profile's lookback
(adjusted closes up to as_of, the same window the model sees after z-scoring). Prints the Spearman correlation of
exp_ret with z, exp_ret by z quintile, and (first as_of date only) the regression of the predicted z-level on the
last z-level per horizon step. No realised return is used.   python scripts/probes/stage6_mean_reversion.py [run_id]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from common.config import load_config  # noqa: E402
from common.paths import Paths  # noqa: E402


def window_stats(paths: Paths, dates, lookback: int) -> pd.DataFrame:
    px = pd.read_parquet(paths.prepared_path("prices"), columns=["date", "ticker", "close"]).merge(pd.read_parquet(paths.prepared_path("adj_factor")), on=["date", "ticker"])
    px["c"] = px["close"] * px["factor"]
    wide = px.pivot(index="date", columns="ticker", values="c").sort_index()
    rows = []
    for d in dates:
        win = wide.loc[:d].tail(lookback)
        last = win.iloc[-1]
        rows.append(pd.DataFrame({"as_of_date": d, "ticker": win.columns, "z": ((last - win.mean()) / win.std()).to_numpy(),
                                  "cv": (win.std() / last).to_numpy(), "w_mean": win.mean().to_numpy(), "w_std": win.std().to_numpy(), "last": last.to_numpy()}))
    return pd.concat(rows, ignore_index=True)


def main(run_id: str = "kronos_base_v1") -> int:
    cfg = load_config(ROOT / "configs/base.yaml")
    paths = Paths(cfg, ROOT)
    manifest = json.loads(paths.manifest_file(run_id).read_text())
    sig = pd.read_parquet(paths.signals_path(run_id))
    dates = sorted(sig["as_of_date"].unique())
    a = sig.merge(window_stats(paths, dates, int(manifest["lookback"])), on=["as_of_date", "ticker"], how="left").dropna(subset=["z"])
    print(f"{run_id}: {len(a):,} signal rows, lookback {manifest['lookback']}, H {manifest['pred_len']}")
    print(f"Spearman(exp_ret, z) = {a['exp_ret'].corr(a['z'], method='spearman'):+.3f}   Spearman(exp_ret, -z*cv) = {a['exp_ret'].corr(-a['z'] * a['cv'], method='spearman'):+.3f}"
          f"   Spearman(signal std, window cv) = {a['std'].corr(a['cv'], method='spearman'):+.3f}")
    a["z_quintile"] = pd.qcut(a["z"], 5, labels=["Q1 (low)", "Q2", "Q3", "Q4", "Q5 (high)"])
    print(a.groupby("z_quintile", observed=True).agg(z_median=("z", "median"), exp_ret_mean=("exp_ret", "mean"), exp_ret_median=("exp_ret", "median"),
                                                      p_up_mean=("p_up", "mean"), n=("z", "size")).round(4).to_string())
    q = a["exp_ret"].quantile([0.01, 0.25, 0.5, 0.75, 0.99]).round(4).to_dict()
    print(f"exp_ret quantiles {q}; |exp_ret| > 50%: {int((a['exp_ret'].abs() > 0.5).sum()):,} rows ({(a['exp_ret'].abs() > 0.5).mean():.1%})")
    d0 = dates[0]
    p = pd.read_parquet(paths.prediction_file(run_id, d0))
    m = p.groupby(["ticker", "horizon_step"])["pred_close"].mean().unstack()
    st = a[a["as_of_date"] == d0].set_index("ticker").reindex(m.index).dropna(subset=["z"])
    m = m.reindex(st.index)
    # predictions are on the as_of raw scale, the window above on the adjusted scale: rescale through the as_of raw close
    scale = pd.read_parquet(paths.prepared_path("prices"), columns=["date", "ticker", "close"])
    raw_last = scale[scale["date"] == d0].set_index("ticker")["close"].reindex(st.index)
    for h in m.columns:
        zp = (m[h] / raw_last * st["last"] - st["w_mean"]) / st["w_std"]
        slope, icpt = np.polyfit(st["z"], zp, 1)
        print(f"{pd.Timestamp(d0).date()} step {h}: predicted z = {slope:.3f} * last z {icpt:+.3f}   median |pred / last - 1| = {(m[h] / raw_last - 1).abs().median():.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:2]))
