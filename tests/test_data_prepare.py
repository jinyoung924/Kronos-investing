"""A_data_prepare unit tests on fabricated KRX rows (no network)."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from A_data_prepare.benchmark import index_prices
from A_data_prepare.env import load_api_key
from A_data_prepare.krx_client import KRXClient
from A_data_prepare.transform import (compute_adj_factor, detect_reference_price_events, filter_securities,
                                    is_common_stock, parse_number, rows_to_frame, short_code, stock_prices,
                                    to_project_format)
from A_data_prepare.universe import build_constituents
from A_data_prepare.validate import validate_dataset


def _row(d, code, name, o, h, l, c, chg, vol, val="1,000,000,000", mkt="KOSPI", sect=""):
    f = lambda x: f"{x:,}" if isinstance(x, (int, float)) else x
    base = c - chg
    fluc = f"{(c / base - 1) * 100:.2f}" if base > 0 else "0.00"
    return {"BAS_DD": d, "ISU_CD": code, "ISU_NM": name, "MKT_NM": mkt, "SECT_TP_NM": sect,
            "TDD_CLSPRC": f(c), "CMPPREVDD_PRC": f(chg), "FLUC_RT": fluc, "TDD_OPNPRC": f(o), "TDD_HGPRC": f(h),
            "TDD_LWPRC": f(l), "ACC_TRDVOL": f(vol), "ACC_TRDVAL": val, "MKTCAP": "1,000", "LIST_SHRS": "100"}


def _dataset(n_days=30):
    """3 names: A common (2:1 split on day 10), A-pref, B delisted after day 20, SPAC."""
    days = pd.bdate_range("2024-01-02", periods=n_days)
    rows = {}
    pa = 1000.0
    for i, d in enumerate(days):
        s = d.strftime("%Y%m%d")
        if s == "20240213":      # a holiday: no rows, prices do not move
            rows[s] = []
            continue
        r = []
        if i == 10:  # split: reference price = prev close / 2
            base = pa / 2
            pa = base * 1.01
            r.append(_row(s, "000010", "A", pa, pa, pa, pa, pa - base, 100))
        else:
            prev = pa
            pa = pa * (1.0 + 0.01 * (1 if i % 2 else -1))
            r.append(_row(s, "000010", "A", pa, pa, pa, pa, pa - prev, 100))
        r.append(_row(s, "000015", "A우", 500, 500, 500, 500, 0, 10))
        if i <= 20:
            r.append(_row(s, "000020", "B", 200, 200, 200, 200, 0, 50 if i != 5 else 0))  # day 5: no trade
        r.append(_row(s, "000030", "XX스팩1호", 2000, 2000, 2000, 2000, 0, 5))
        if i == 5:
            r[-2] = _row(s, "000020", "B", 0, 0, 0, 200, 0, 0)  # halted representation
        rows[s] = r
    return rows


def test_parse_helpers():
    assert parse_number("1,234") == 1234.0
    assert np.isnan(parse_number("")) and np.isnan(parse_number("-"))
    assert short_code("KR7005930003") == "005930" and short_code("005930") == "005930"
    assert is_common_stock("005930") and not is_common_stock("005935") and not is_common_stock("00593K")


def test_stock_prices_halt_and_adj_factor():
    prices = stock_prices(rows_to_frame(_dataset()))
    b5 = prices[(prices["ticker"] == "000020") & (prices["date"] == "20240109")].iloc[0]
    assert b5["halted"] and np.isnan(b5["open"]) and b5["close"] == 200
    ev = detect_reference_price_events(prices)
    assert list(ev["ticker"]) == ["000010"] and ev["ratio"].iloc[0] == pytest.approx(0.5)
    adj, events = compute_adj_factor(prices, ev)
    a = adj[adj["ticker"] == "000010"].sort_values("date")
    assert a["adj_factor"].iloc[-1] == 1.0
    assert (a["adj_factor"].iloc[:10] == 0.5).all() and (a["adj_factor"].iloc[10:] == 1.0).all()
    lr = np.log(a["close"] * a["adj_factor"]).diff().abs().max()
    assert lr < 0.02  # continuous after adjustment
    assert (adj.loc[adj["ticker"] != "000010", "adj_factor"] == 1.0).all()


def test_filter_securities():
    prices = stock_prices(rows_to_frame(_dataset()))
    out, stats = filter_securities(prices, True, ["스팩"])
    assert set(out["ticker"]) == {"000010", "000020"}
    assert stats["dropped_non_common"] == 1 and stats["dropped_name~스팩"] == 1


def test_constituents_point_in_time_delisting_and_halt():
    prices, _ = compute_adj_factor(filter_securities(stock_prices(rows_to_frame(_dataset())), True, ["스팩"])[0])
    dates = sorted(prices["date"].unique())
    cons, cov = build_constituents(prices, dates, min_history_rows=3, liquidity_window=2, min_avg_trdval=0)
    by = cons.groupby("date")["ticker"].apply(set)
    assert by.get(dates[0], set()) == set() and by.loc[dates[2]] == {"000010", "000020"}   # history filter
    assert "000020" not in by.loc[dates[5]]                                           # halted that day
    assert "000020" in by.loc[dates[20]] and "000020" not in by.loc[dates[21]]        # delisted, not removed before
    cons2, _ = build_constituents(prices, dates, min_history_rows=3, liquidity_window=2, min_avg_trdval=5e9)
    assert cons2.empty  # liquidity filter


def test_universe_filters_use_only_past(monkeypatch):
    prices, _ = compute_adj_factor(filter_securities(stock_prices(rows_to_frame(_dataset())), True, ["스팩"])[0])
    dates = sorted(prices["date"].unique())
    cut = dates[15]
    cons1, _ = build_constituents(prices, dates, min_history_rows=3, liquidity_window=5, min_avg_trdval=5e8)
    p2 = prices.copy()
    m = p2["date"] > cut
    p2.loc[m, "trdval"] = 0.0; p2.loc[m, "halted"] = True
    cons2, _ = build_constituents(p2, dates, min_history_rows=3, liquidity_window=5, min_avg_trdval=5e8)
    a = cons1[cons1["date"] <= cut].reset_index(drop=True)
    b = cons2[cons2["date"] <= cut].reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b)


def test_index_prices():
    raw = pd.DataFrame([{"date": "20240102", "IDX_NM": "코스피", "OPNPRC_IDX": "2,600.00", "HGPRC_IDX": "2,650.00",
                         "LWPRC_IDX": "2,590.00", "CLSPRC_IDX": "2,640.00", "ACC_TRDVOL": "500,000"},
                        {"date": "20240102", "IDX_NM": "코스피 200", "OPNPRC_IDX": "1", "HGPRC_IDX": "1", "LWPRC_IDX": "1", "CLSPRC_IDX": "1", "ACC_TRDVOL": "1"}])
    b = index_prices(raw, "코스피", "KOSPI")
    assert len(b) == 1 and b["ticker"].iloc[0] == "KOSPI" and b["close"].iloc[0] == 2640.0


def test_validate_dataset_reports_delisting():
    prices, ev = compute_adj_factor(filter_securities(stock_prices(rows_to_frame(_dataset(60))), True, ["스팩"])[0])
    dates = sorted(prices["date"].unique())
    cons, _ = build_constituents(prices, dates, min_history_rows=3, liquidity_window=2)
    summary, big = validate_dataset(prices, cons, ev, dates[10], dates[40], lookback=5, horizon=2)
    assert summary["hard_failures"] == []
    assert summary["delisted"]["n_during_run"] == 1 and summary["delisted"]["n_that_were_universe_members"] == 1
    assert summary["reference_price_events"]["n_applied"] == 1
    assert len(big) == 0


def test_env_key_formats(tmp_path, monkeypatch):
    monkeypatch.delenv("KRX_API_KEY", raising=False)
    (tmp_path / ".env").write_text("ABC123\n")
    assert load_api_key(tmp_path) == "ABC123"
    (tmp_path / ".env").write_text("# comment\nKRX_API_KEY='XYZ'\n")
    assert load_api_key(tmp_path) == "XYZ"
    monkeypatch.setenv("KRX_API_KEY", "ENVKEY")
    assert load_api_key(tmp_path) == "ENVKEY"


def test_client_cache_and_outblock(tmp_path, monkeypatch):
    c = KRXClient(api_key="k", cache_dir=tmp_path)
    calls = []
    monkeypatch.setattr(c, "_get", lambda api, d: calls.append(d) or ([{"ISU_CD": "005930"}] if d != "20240102" else []))
    assert c.fetch("stk_bydd_trd", "20240102") == []
    assert c.fetch("stk_bydd_trd", "20240103") == [{"ISU_CD": "005930"}]
    assert c.fetch("stk_bydd_trd", "20240103") == [{"ISU_CD": "005930"}]  # cached
    assert calls == ["20240102", "20240103"]
    assert json.loads(c.cache_path("stk_bydd_trd", "20240102").read_text()) == []
    assert KRXClient._rows({"OutBlock_1": [{"a": "1"}]}, "x", "d") == [{"a": "1"}]
    with pytest.raises(Exception):
        KRXClient._rows({"respCode": "9", "respMsg": "bad"}, "x", "d")


def test_exchange_column_and_sect_filter():
    rows = {"20240102": [
        _row("20240102", "000010", "A", 100, 100, 100, 100, 0, 10, mkt="KOSPI"),
        _row("20240102", "900010", "K", 100, 100, 100, 100, 0, 10, mkt="KOSDAQ", sect="우량기업부"),
        _row("20240102", "900020", "하나머스트7호", 2000, 2000, 2000, 2000, 0, 1, mkt="KOSDAQ", sect="SPAC(소속부없음)"),
        _row("20240102", "900030", "M", 100, 100, 100, 100, 0, 10, mkt="KOSDAQ", sect="관리종목(소속부없음)"),
    ], "20240103": [
        _row("20240103", "900030", "M", 100, 100, 100, 100, 0, 10, mkt="KOSDAQ", sect="벤처기업부"),  # label changed
    ]}
    prices = stock_prices(rows_to_frame(rows))
    assert set(prices["exchange"]) == {"KOSPI", "KOSDAQ"}
    out, stats = filter_securities(prices, True, ["스팩"], ["SPAC"])
    assert set(out["ticker"]) == {"000010", "900010", "900030"} and stats["dropped_sect~SPAC"] == 1
    out2, stats2 = filter_securities(prices, True, ["스팩"], ["SPAC", "관리종목"])
    assert "900030" not in set(out2["ticker"]), "a ticker flagged on any date is dropped for all dates"
    assert stats2["dropped_sect~관리종목"] == 1


def test_etf_prices_drop_holiday_rows():
    from A_data_prepare.benchmark import etf_prices
    raw = pd.DataFrame([
        {"date": "20240102", "ISU_CD": "226490", "ISU_NM": "KODEX 코스피", "TDD_OPNPRC": "10,000", "TDD_HGPRC": "10,100",
         "TDD_LWPRC": "9,900", "TDD_CLSPRC": "10,050", "ACC_TRDVOL": "1,000"},
        {"date": "20240101", "ISU_CD": "226490", "ISU_NM": "KODEX 코스피", "TDD_OPNPRC": "", "TDD_HGPRC": "",
         "TDD_LWPRC": "", "TDD_CLSPRC": "", "ACC_TRDVOL": ""},   # holiday row with empty prices
    ])
    out = etf_prices(raw, ["226490"])
    assert len(out) == 1 and out["close"].iloc[0] == 10050.0


def test_sanity_adj_vs_fluc_rt_and_index_reconstruction():
    from A_data_prepare.sanity import adj_vs_fluc_rt, reconstruct_index_return
    prices, ev = compute_adj_factor(filter_securities(stock_prices(rows_to_frame(_dataset(40))), True, ["스팩"])[0])
    prices = to_project_format(prices)
    chk = adj_vs_fluc_rt(prices, tol=1e-3)
    assert chk["n_checked"] > 30 and chk["n_bad"] == 0, chk["worst"]
    # break one adj_factor -> the gate catches it
    bad = prices.copy()
    i = bad.index[(bad["ticker"] == "000010")][:5]
    bad.loc[i, "adj_factor"] *= 2
    assert adj_vs_fluc_rt(bad)["n_bad"] >= 1
    # cap-weighted reconstruction with a single dominant name equals that name's adjusted return
    r = reconstruct_index_return(prices.assign(mktcap=np.where(prices["ticker"] == "000010", 1e12, 1.0)))
    a = prices[prices["ticker"] == "000010"].set_index("date")
    own = (a["close"] * a["adj_factor"]).pct_change().dropna()
    pd.testing.assert_series_equal(r.reindex(own.index).round(9), own.round(9), check_names=False)


def test_manifest_roundtrip(tmp_path):
    from A_data_prepare.manifest import verify, write
    d = tmp_path / "data"
    (d / "raw" / "kospi").mkdir(parents=True)
    (d / "raw" / "kospi" / "prices.parquet").write_bytes(b"abc")
    (d / "universe").mkdir()
    (d / "universe" / "constituents_kospi.parquet").write_bytes(b"xyz")
    msgs = []
    write(d, ["raw", "universe"], "2026-09-28", log=msgs.append)
    assert verify(d, log=msgs.append) == 0
    (d / "raw" / "kospi" / "prices.parquet").write_bytes(b"abcd")
    assert verify(d, log=msgs.append) == 1
    assert any("SIZE" in m for m in msgs)


def test_universe_variants_and_paths(cfg):
    from common.paths import Paths
    from A_data_prepare.run import universe_variants
    v = universe_variants(cfg)
    assert "base" in v and v["liq5"]["min_avg_trdval"] == 500000000 and v["base"]["min_avg_trdval"] == 1000000000
    assert v["clean"]["exclude_sect_patterns"] == ["SPAC", "관리종목", "투자주의환기"]
    p = Paths(cfg, ".")
    assert p.constituents_file("kospi").name == "constituents_kospi.parquet"
    assert p.constituents_file("kospi", "liq5").name == "constituents_kospi_liq5.parquet"
