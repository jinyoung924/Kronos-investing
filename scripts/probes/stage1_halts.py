"""Stage 1 probe [확인 필요]: how trading halts appear in the cached KRX JSON (docs/data_pipeline.md §5-4).

Checks, without any network call, against data/krx_raw/<snapshot>/{stk,ksq}_bydd_trd/*.json:
  1. On days flagged `halted` in prices.parquet the raw row has TDD_OPNPRC/HGPRC/LWPRC = "0", ACC_TRDVOL = "0",
     and a valid TDD_CLSPRC (the reference price), CMPPREVDD_PRC "0", FLUC_RT "0.00".
  2. Whether any raw row has volume > 0 but open = 0 (or the reverse), i.e. cases the rule would mis-classify.
  3. Whether tickers ever disappear from the response between their first and last appearance (gaps other than
     delisting), and what those gaps look like.
  4. Row counts per day: dates where a ticker is absent although listed before and after.
Run:  python scripts/probes/stage1_halts.py
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

API = {"kospi": "stk_bydd_trd", "kosdaq": "ksq_bydd_trd"}


def raw_rows(cache: Path, api: str, d: pd.Timestamp) -> list[dict]:
    f = cache / api / f"{d.strftime('%Y%m%d')}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


def main() -> int:
    cfg = load_config(ROOT / "configs/base.yaml")
    paths = Paths(cfg, ROOT)
    cache = paths.krx_cache_dir()
    print(f"cache: {cache}")
    for idx in cfg["universe"]["indices"]:
        p = pd.read_parquet(paths.price_file(idx))
        p["date"] = pd.to_datetime(p["date"])
        cal = pd.DatetimeIndex(sorted(p["date"].unique()))
        halted = p[p["halted"]]
        print(f"\n===== {idx}: rows {len(p):,}, halted rows {len(halted):,} ({len(halted)/len(p):.2%}), tickers {p['ticker'].nunique()}")

        # 1. raw representation of a sample of halted rows: longest halt streaks + random rows
        streak = halted.sort_values(["ticker", "date"]).groupby("ticker").size().sort_values(ascending=False)
        sample = pd.concat([halted[halted["ticker"].isin(streak.index[:3])].groupby("ticker").head(1),
                            halted.sample(12, random_state=0)])
        pattern = {"ohl_zero": 0, "vol_zero": 0, "close_valid": 0, "chg_zero": 0, "n": 0, "odd": []}
        for _, r in sample.iterrows():
            rows = [x for x in raw_rows(cache, API[idx], r["date"]) if x.get("ISU_CD") == r["ticker"]]
            if not rows:
                pattern["odd"].append((str(r["date"].date()), r["ticker"], "missing in raw"))
                continue
            x = rows[0]
            pattern["n"] += 1
            ohl0 = all(x[k].replace(",", "") in ("0", "") for k in ("TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC"))
            v0 = x["ACC_TRDVOL"].replace(",", "") in ("0", "")
            c_ok = float(x["TDD_CLSPRC"].replace(",", "") or 0) > 0
            pattern["ohl_zero"] += ohl0; pattern["vol_zero"] += v0; pattern["close_valid"] += c_ok
            pattern["chg_zero"] += x["CMPPREVDD_PRC"].replace(",", "") in ("0", "")
            if not (ohl0 and c_ok):
                pattern["odd"].append((str(r["date"].date()), r["ticker"], x))
        print(f"1. raw rows of {pattern['n']} halted samples: OHL all '0': {pattern['ohl_zero']}, volume '0': {pattern['vol_zero']}, "
              f"close > 0: {pattern['close_valid']}, 전일대비 '0': {pattern['chg_zero']}; odd: {pattern['odd'][:3]}")
        ex = sample.iloc[0]
        x = [r for r in raw_rows(cache, API[idx], ex["date"]) if r.get("ISU_CD") == ex["ticker"]][0]
        print(f"   example {ex['ticker']} {ex['name']} {ex['date'].date()} (streak {streak.get(ex['ticker'], 0)} days): "
              f"{ {k: x[k] for k in ('TDD_OPNPRC','TDD_HGPRC','TDD_LWPRC','TDD_CLSPRC','CMPPREVDD_PRC','FLUC_RT','ACC_TRDVOL','ACC_TRDVAL')} }")

        # 2. mis-classification candidates in the parsed frame
        vol_pos_open_nan = p[(p["volume"] > 0) & p["open"].isna()]
        open_pos_vol_zero = p[(p["open"] > 0) & (p["volume"] == 0)]
        print(f"2. volume > 0 but open missing: {len(vol_pos_open_nan)} rows; open > 0 but volume 0: {len(open_pos_vol_zero)} rows")
        for _, r in vol_pos_open_nan.head(3).iterrows():
            x = [z for z in raw_rows(cache, API[idx], r["date"]) if z.get("ISU_CD") == r["ticker"]]
            print(f"   {r['date'].date()} {r['ticker']} {r['name']}: raw {x[0] if x else 'missing'}")
        nan_close = p["close"].isna().sum()
        print(f"   NaN close rows: {nan_close}; halted rows with NaN close: {int(halted['close'].isna().sum())}")

        # 3./4. gaps: ticker absent on a trading day between its first and last row
        g = p.groupby("ticker")["date"].agg(["min", "max", "size"])
        span = np.array([((cal >= a) & (cal <= b)).sum() for a, b in zip(g["min"], g["max"])])
        g["gap_days"] = span - g["size"].to_numpy()
        gaps = g[g["gap_days"] > 0].sort_values("gap_days", ascending=False)
        print(f"3. tickers with missing days inside their listing span: {len(gaps)} (total missing rows {int(gaps['gap_days'].sum())})")
        for t, r in gaps.head(5).iterrows():
            have = set(p.loc[p["ticker"] == t, "date"])
            miss = [d for d in cal[(cal >= r['min']) & (cal <= r['max'])] if d not in have]
            name = p.loc[p["ticker"] == t, "name"].iloc[-1]
            print(f"   {t} {name}: {int(r['gap_days'])} missing days, first gap {miss[0].date()} .. {miss[-1].date()} (listed {r['min'].date()} .. {r['max'].date()})")
            # is the ticker present in the raw file on the first missing day under another code / exchange?
            rows = raw_rows(cache, API[idx], miss[0])
            same_name = [z["ISU_CD"] for z in rows if z.get("ISU_NM") == name]
            print(f"      raw file {miss[0].date()}: rows {len(rows)}, same name under code(s) {same_name}")
        # delistings: last row before calendar end
        last = g["max"]
        delisted = last[last < cal[-1]]
        print(f"4. tickers whose last row precedes the calendar end (delisted / transferred): {len(delisted)}")
    print("\nconclusion: see the Stage 1 report; `halted` = (volume == 0) | (open <= 0) with NaN open/high/low and the close kept")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
