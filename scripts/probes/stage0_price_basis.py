"""Stage 0 probe: on the REAL data, confirm the price basis of the inference input (spec appendix A, build_batch).

Claim under test: build_batch rebases the lookback window with adj_factor / adj_factor[last row], so
  (1) the last close of every window equals the raw close at as_of  -> predictions are on the as_of raw scale,
  (2) a split inside the window leaves no jump in the input series,
  (3) a window never contains a row dated after as_of.
Also counts, for two as_of dates, how many universe members build_batch skips and why (halt days carry
NaN open/high/low, and `nan_in_window` drops the whole ticker).
Read-only; prints a summary for the Stage report.   python scripts/probes/stage0_price_basis.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from B_model_infer.build_batch import build_batch  # noqa: E402
from B_model_infer.run_inference import profile_cfg  # noqa: E402
from common.config import load_config  # noqa: E402
from common.data import load_prices, rebalance_dates, trading_calendar  # noqa: E402
from common.lookahead import slice_as_of  # noqa: E402
from common.universe import combined_universe_at, load_constituents  # noqa: E402


def window_checks(prices: pd.DataFrame, batch, as_of) -> dict:
    """(1) last close == as_of raw close, (3) no future stamps, and how many windows contain an adj_factor change."""
    hist = slice_as_of(prices[prices["ticker"].isin(batch.tickers)], as_of, "date").sort_values(["ticker", "date"])
    last_raw = hist.groupby("ticker")["close"].last()
    n_mismatch = sum(not np.isclose(df["close"].iloc[-1], last_raw[t]) for t, df in zip(batch.tickers, batch.df_list))
    n_future = sum(int((xs > as_of).any()) for xs in batch.x_timestamps)
    n_factor_change, max_jump = 0, 0.0
    for t, df, xs in zip(batch.tickers, batch.df_list, batch.x_timestamps):
        f = hist.loc[hist["ticker"] == t].set_index("date").loc[xs.to_numpy(), "adj_factor"].to_numpy()
        if f.min() != f.max():
            n_factor_change += 1
            max_jump = max(max_jump, float(np.abs(np.diff(np.log(df["close"].to_numpy()))).max()))
    return {"n_windows": len(batch), "last_close_mismatch": n_mismatch, "future_rows": n_future,
            "windows_with_adj_change": n_factor_change, "max_abs_dlog_close_in_those": round(max_jump, 4)}


def main() -> int:
    cfg = load_config(ROOT / "configs/base.yaml")
    prof = profile_cfg(cfg)
    lookback, horizon, step = int(prof["lookback"]), int(prof["pred_len"]), int(prof["step"])
    prices = load_prices(cfg, ROOT, include_benchmark=False)
    cons = load_constituents(cfg, ROOT)
    cal = trading_calendar(prices)
    dates = rebalance_dates(cal, cfg["period"]["start"], cfg["period"]["end"], step)
    print(f"profile {prof['profile']}: lookback {lookback}, pred_len {horizon}, step {step}; rebalance dates {len(dates)} "
          f"({dates[0].date()} .. {dates[-1].date()})")

    ok = True
    for as_of in (dates[0], dates[len(dates) // 2], dates[-1]):
        members = combined_universe_at(cons, as_of)["ticker"].tolist()
        b = build_batch(prices, members, as_of, lookback, horizon, max_stale_days=int(cfg["universe"]["max_stale_days"]))
        reasons = pd.Series([r.split("(")[0] for r in b.skipped.values()]).value_counts().to_dict()
        chk = window_checks(prices, b, as_of)
        ok &= chk["last_close_mismatch"] == 0 and chk["future_rows"] == 0
        print(f"\nas_of {as_of.date()}: universe {len(members)} -> windows {len(b)}, skipped {len(b.skipped)} {reasons}")
        print(f"  {chk}")
        # tickers skipped for NaN: how many of them have a halt day inside the lookback window
        nan_t = [t for t, r in b.skipped.items() if r == "nan_in_window"]
        if nan_t:
            h = slice_as_of(prices[prices["ticker"].isin(nan_t)], as_of, "date").sort_values(["ticker", "date"])
            tail = h.groupby("ticker").tail(lookback)
            n_halt = tail.groupby("ticker")["halted"].any().sum() if "halted" in tail else None
            n_adj = (tail.groupby("ticker")["adj_factor"].nunique() > 1).sum()
            print(f"  nan_in_window tickers: {len(nan_t)}; with a halted day in the window: {int(n_halt)}; "
                  f"with an adj_factor change in the window: {int(n_adj)}")

    verdict = "raw (as_of raw-price scale; last close == raw close for every window)" if ok else "INCONSISTENT - inspect"
    print(f"\nprice basis of the inference input: {verdict}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
