"""Step 0 - probe the KRX Open API: one call per service on a known trading day.

Read-only and tiny (<= 5 calls, all cached afterwards). Reports which services the key is approved
for, the response schema, the code format, the share of no-trade rows and the KOSDAQ 소속부 labels.
Findings -> {cache_dir}/probe_findings.json and probe.txt. Run this before a full fetch so a missing
service approval (HTTP 401) is found in seconds, not after thousands of calls.
"""
from __future__ import annotations

import collections
import io
import json
from pathlib import Path

from data_prepare.krx_client import ENDPOINTS, KRXClient, KRXError

PROBE_DATE = "20240702"


def main(client: KRXClient, apis: list[str], log=print) -> dict:
    buf = io.StringIO()
    findings: dict = {"probe_date": PROBE_DATE, "services": {}}

    def P(*a):
        s = " ".join(str(x) for x in a)
        log(s)
        buf.write(s + "\n")

    for api in apis:
        try:
            rows = client.fetch(api, PROBE_DATE)
        except KRXError as e:
            P(f"{api}: NOT AVAILABLE -> {e}")
            findings["services"][api] = {"ok": False, "error": str(e)}
            continue
        info = {"ok": True, "n_rows": len(rows), "fields": list(rows[0].keys()) if rows else []}
        P(f"{api}: OK rows={len(rows)} fields={info['fields']}")
        if rows and "ISU_CD" in rows[0]:
            info["isu_cd_lengths"] = dict(collections.Counter(len(r["ISU_CD"]) for r in rows))
            info["n_no_trade"] = sum(str(r.get("TDD_OPNPRC", "")).replace(",", "") in ("0", "") for r in rows)
            P(f"  ISU_CD lengths={info['isu_cd_lengths']} no-trade rows={info['n_no_trade']}")
        if rows and "MKT_NM" in rows[0]:
            info["mkt_nm"] = dict(collections.Counter(r["MKT_NM"] for r in rows))
            info["sect_tp_nm"] = dict(collections.Counter(r.get("SECT_TP_NM", "") for r in rows).most_common(12))
            info["n_spac_by_name"] = sum("스팩" in r["ISU_NM"] for r in rows)
            P(f"  MKT_NM={info['mkt_nm']} SECT_TP_NM={info['sect_tp_nm']} spac(name)={info['n_spac_by_name']}")
        if rows and "IDX_NM" in rows[0]:
            info["index_names"] = [r["IDX_NM"] for r in rows][:10]
            P(f"  index names (first 10)={info['index_names']}")
        findings["services"][api] = info

    missing = [a for a, v in findings["services"].items() if not v["ok"]]
    findings["not_approved"] = missing
    P(f"services not approved for this key: {missing or 'none'}")
    client.cache_dir.mkdir(parents=True, exist_ok=True)
    (client.cache_dir / "probe_findings.json").write_text(json.dumps(findings, indent=2, ensure_ascii=False), encoding="utf-8")
    (client.cache_dir / "probe.txt").write_text(buf.getvalue(), encoding="utf-8")
    return findings
