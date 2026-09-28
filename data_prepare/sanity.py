"""Sanity gates on the BUILT dataset (after build + benchmark). Hard gates raise AssertionError;
soft checks log warnings. Everything is written to data/raw/sanity_report.json.

Gates
  1. keys: unique (ticker, date) per file, strictly positive adj_factor, no NaN close on traded rows (hard)
  2. adj_factor vs KRX FLUC_RT: the adjusted close-to-close return must equal KRX's own daily
     fluctuation rate (which is quoted against the reset reference price) on every traded row
     -> |ret_adj - FLUC_RT/100| <= 1e-3 for >= 99.9% of rows (hard). This validates the split /
     rights-issue adjustment independently of our detection logic.
  3. calendar: stock files, index files and ETFs share one trading calendar (hard)
  4. universe reconciliation: every constituent on date d has a listed, non-halted bar on d and
     >= min_history_rows prior bars; snapshot dates are trading days (hard)
  5. end-to-end vs official index: cap-weighted daily return of all common stocks on the exchange
     (weights = previous-day MKTCAP, prices adjusted) vs the official index return.
     corr >= 0.97 expected; hard gate at 0.90 (KOSPI/KOSDAQ are total-cap weighted indices of
     common stocks, so a reconstruction from our data must track them closely)
  6. ETF vs index: KODEX 코스피 vs KOSPI, KODEX 코스닥150 vs KOSDAQ daily return corr (soft, logged)
  7. diagnostics: N listed / members / halted / delisted per month, delisting losses realised in
     the data for former universe members, extreme adjusted moves (written, not gated)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from common.config import cfg_get
from common.paths import Paths

ETF_INDEX = {"226490": "KOSPI", "229200": "KOSDAQ"}   # ETF -> the index it tracks (코스닥150 ~ KOSDAQ, soft only)


class GateError(AssertionError):
    pass


def _gate(cond: bool, msg: str, log, hard: bool = True) -> bool:
    if cond:
        log(f"PASS: {msg}")
    elif hard:
        log(f"FAIL: {msg}")
        raise GateError(msg)
    else:
        log(f"WARN: {msg}")
    return bool(cond)


def adj_vs_fluc_rt(p: pd.DataFrame, tol: float = 1e-3) -> dict:
    p = p.sort_values(["ticker", "date"])
    adj_close = p["close"] * p["adj_factor"]
    ret = adj_close.groupby(p["ticker"]).pct_change()
    prev_halted = p.groupby("ticker")["halted"].shift(1)
    ok_rows = ret.notna() & ~p["halted"] & (prev_halted == False) & p["fluc_rt"].notna()  # noqa: E712
    diff = (ret - p["fluc_rt"] / 100.0).abs()
    bad = ok_rows & (diff > tol)
    return {"n_checked": int(ok_rows.sum()), "n_bad": int(bad.sum()),
            "share_ok": float(1 - bad.sum() / max(ok_rows.sum(), 1)),
            "worst": p.loc[bad, ["date", "ticker", "name"]].assign(ret_adj=ret[bad], fluc_rt=p.loc[bad, "fluc_rt"] / 100).head(10)}


def reconstruct_index_return(p: pd.DataFrame) -> pd.Series:
    """Cap-weighted daily return of all rows (weights = previous-day MKTCAP), adjusted prices."""
    p = p.sort_values(["ticker", "date"])
    adj_close = p["close"] * p["adj_factor"]
    ret = adj_close.groupby(p["ticker"]).pct_change()
    w = p.groupby("ticker")["mktcap"].shift(1)
    d = pd.DataFrame({"date": p["date"], "wr": w * ret, "w": w}).dropna()
    g = d.groupby("date").sum()
    return (g["wr"] / g["w"]).rename("recon")


def official_index_return(bench: pd.DataFrame, ticker: str) -> pd.Series:
    s = bench[bench["ticker"] == ticker].set_index("date")["close"].sort_index()
    return s.pct_change().dropna().rename(ticker)


def run(cfg: dict, root: str | Path, log=print, all_prices: pd.DataFrame | None = None) -> dict:
    paths = Paths(cfg, root)
    indices = list(cfg_get(cfg, "universe.indices", []))
    uni = cfg_get(cfg, "universe", {}) or {}
    report: dict = {"gates": {}, "diag": {}}
    bench = pd.read_parquet(paths.price_file("benchmark")) if paths.price_file("benchmark").exists() else pd.DataFrame()
    index_tickers = cfg_get(cfg, "benchmark.index_tickers", {}) or {}
    calendars = {}

    for key in indices:
        p = pd.read_parquet(paths.price_file(key))
        p["date"] = pd.to_datetime(p["date"])
        cons = pd.read_parquet(paths.constituents_file(key, "base"))
        cons["date"] = pd.to_datetime(cons["date"])
        cal = pd.DatetimeIndex(sorted(p["date"].unique()))
        calendars[key] = cal
        g = report["gates"].setdefault(key, {})

        # 1. keys
        traded = p[~p["halted"]]
        _gate(not p.duplicated(["ticker", "date"]).any(), f"[{key}] unique (ticker, date)", log)
        _gate(bool((p["adj_factor"] > 0).all()), f"[{key}] adj_factor > 0", log)
        _gate(bool(traded["close"].notna().all() and (traded["close"] > 0).all()), f"[{key}] close > 0 on traded rows", log)

        # 2. adj_factor vs FLUC_RT
        chk = adj_vs_fluc_rt(p)
        g["adj_vs_fluc_rt"] = {k: v for k, v in chk.items() if k != "worst"}
        if chk["n_bad"]:
            log(f"[{key}] adj/FLUC_RT mismatches (first 10):\n{chk['worst'].to_string()}")
        _gate(chk["share_ok"] >= 0.999, f"[{key}] adjusted return == KRX FLUC_RT on {chk['share_ok']:.4%} of rows (>= 99.9%)", log)

        # 4. universe reconciliation
        listed = p.set_index(["ticker", "date"])
        idx = pd.MultiIndex.from_frame(cons[["ticker", "date"]])
        present = idx.isin(listed.index)
        _gate(bool(present.all()), f"[{key}] every constituent has a bar on its snapshot date ({(~present).sum()} missing)", log)
        halted_members = listed.loc[idx[present], "halted"].to_numpy()
        _gate(not halted_members.any(), f"[{key}] no constituent is halted on its snapshot date ({int(halted_members.sum())} halted)", log)
        _gate(bool(cons["date"].isin(cal).all()), f"[{key}] snapshot dates are trading days", log)
        hist = p.groupby("ticker").cumcount() + 1
        hist_map = pd.Series(hist.to_numpy(), index=listed.index)
        min_hist = int(uni.get("min_history_rows", cfg_get(cfg, "model.lookback", 400)))
        h = hist_map.reindex(idx[present]).to_numpy()
        _gate(bool((h >= min_hist).all()), f"[{key}] every constituent has >= {min_hist} prior bars (min {int(np.nanmin(h)) if len(h) else 0})", log)

        # 5. end-to-end index reconstruction
        tk = index_tickers.get(key)
        if tk and not bench.empty and tk in set(bench["ticker"]):
            recon = reconstruct_index_return(p)
            off = official_index_return(bench.assign(date=pd.to_datetime(bench["date"])), tk)
            both = pd.concat([recon, off], axis=1).dropna()
            rho = float(both.corr().iloc[0, 1])
            beta = float(np.polyfit(both[tk], both["recon"], 1)[0])
            te = float((both["recon"] - both[tk]).std() * np.sqrt(252))
            g["index_reconstruction"] = {"index": tk, "corr": rho, "beta": beta, "tracking_error_ann": te, "n_days": int(len(both))}
            log(f"[{key}] reconstructed cap-weighted return vs {tk}: corr={rho:.4f} beta={beta:.3f} TE={te:.3%} n={len(both)}")
            _gate(rho >= 0.97, f"[{key}] index reconstruction corr {rho:.4f} >= 0.97", log, hard=False)
            _gate(rho >= 0.90, f"[{key}] index reconstruction corr {rho:.4f} >= 0.90 (hard)", log)
        else:
            log(f"WARN: [{key}] no benchmark index series for end-to-end check")

        # 7. diagnostics
        m = p.assign(month=p["date"].dt.to_period("M").astype(str))
        last = p.groupby("ticker")["date"].max()
        delist_month = last[last < cal[-1] - pd.Timedelta(days=10)].dt.to_period("M").astype(str).value_counts()
        cm = cons.assign(month=cons["date"].dt.to_period("M").astype(str)).groupby("month")["ticker"].nunique()
        diag = pd.DataFrame({
            "n_listed": m.groupby("month")["ticker"].nunique(),
            "n_halted_rows": m.groupby("month")["halted"].sum().astype(int),
            "n_members": cm, "n_delisted": delist_month,
        }).fillna(0).astype(int)
        diag.to_csv(paths.price_file(key).parent / "diag_universe_by_month.csv")
        # delisting losses realised for former members: last membership close -> last available close (adjusted)
        members = set(cons["ticker"])
        rows = []
        for t, d_last in last[last < cal[-1] - pd.Timedelta(days=10)].items():
            if t not in members:
                continue
            last_member_date = cons.loc[cons["ticker"] == t, "date"].max()
            pt = p[p["ticker"] == t].set_index("date")
            a0 = pt.loc[last_member_date, "close"] * pt.loc[last_member_date, "adj_factor"]
            a1 = pt.loc[d_last, "close"] * pt.loc[d_last, "adj_factor"]
            rows.append({"ticker": t, "name": pt["name"].iloc[-1], "last_member_date": last_member_date.date(),
                         "last_price_date": d_last.date(), "ret_after_exit": a1 / a0 - 1})
        dl = pd.DataFrame(rows)
        dl.to_csv(paths.price_file(key).parent / "diag_delisted_members.csv", index=False)
        report["diag"][key] = {"n_delisted_members": int(len(dl)),
                               "mean_ret_after_exit": float(dl["ret_after_exit"].mean()) if len(dl) else None,
                               "members_per_month": {"min": int(cm.min()), "max": int(cm.max())}}
        if len(dl):
            log(f"[{key}] former members that delisted: {len(dl)}, mean return after last membership -> last price: {dl['ret_after_exit'].mean():+.1%}")

    # 3. shared calendar
    if len(calendars) > 1:
        cals = list(calendars.values())
        same = all(c.equals(cals[0]) for c in cals[1:])
        _gate(same, "stock files share one trading calendar", log)
    if not bench.empty:
        b = bench.assign(date=pd.to_datetime(bench["date"]))
        for tk in b["ticker"].unique():
            bc = pd.DatetimeIndex(sorted(b.loc[b["ticker"] == tk, "date"].unique()))
            first = next(iter(calendars.values()))
            _gate(bc.equals(first), f"benchmark {tk} shares the stock calendar", log)
        # 6. ETF vs index
        for etf, idx_tk in ETF_INDEX.items():
            if etf in set(b["ticker"]) and idx_tk in set(b["ticker"]):
                both = pd.concat([official_index_return(b, etf), official_index_return(b, idx_tk)], axis=1).dropna()
                rho = float(both.corr().iloc[0, 1])
                report["gates"][f"etf_{etf}_vs_{idx_tk}"] = {"corr": rho, "n_days": int(len(both))}
                _gate(rho >= 0.9, f"ETF {etf} vs {idx_tk} daily return corr {rho:.3f} >= 0.9", log, hard=False)

    out = Path(paths.raw_dir()) / "sanity_report.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    log(f"sanity report -> {out}")
    return report
