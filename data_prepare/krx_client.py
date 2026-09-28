"""Minimal KRX Open API client with an on-disk JSON cache.

  GET https://data-dbg.krx.co.kr/svc/apis/{category}/{api_id}.json?basDd=YYYYMMDD
  header AUTH_KEY: <key>            response {"OutBlock_1": [ {field: "string", ...}, ... ]}

* Every value KRX returns is a string ("1,234"); parsing happens in transform.py.
* Quota: 10,000 calls per key per day (HTTP 429 when exceeded). Each (api_id, date) is cached
  under data/krx_raw/{api_id}/{YYYYMMDD}.json so a re-run costs zero calls.
* A weekday with an empty OutBlock is a KRX holiday (or unpublished data); it is cached as [].
  (The ETF endpoint is the exception: on holidays it returns rows whose price fields are "".)
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

BASE_URL = "https://data-dbg.krx.co.kr/svc/apis"
DAILY_QUOTA = 10_000

ENDPOINTS: dict[str, str] = {           # api_id -> category
    "stk_bydd_trd": "sto",              # 유가증권 일별매매정보 (전종목, 하루)
    "ksq_bydd_trd": "sto",              # 코스닥 일별매매정보
    "stk_isu_base_info": "sto",         # 유가증권 종목기본정보 (현재 상장 종목만)
    "kospi_dd_trd": "idx",              # KOSPI 시리즈 일별시세정보
    "kosdaq_dd_trd": "idx",             # KOSDAQ 시리즈 일별시세정보
    "etf_bydd_trd": "etp",              # ETF 일별매매정보
}


class KRXError(RuntimeError):
    pass


class KRXRateLimitError(KRXError):
    pass


@dataclass
class KRXClient:
    api_key: str
    cache_dir: Path
    min_interval: float = 0.15
    timeout: int = 30
    max_retries: int = 5
    log: callable = print
    calls: int = field(default=0, init=False)
    _last_call: float = field(default=0.0, init=False)

    def __post_init__(self):
        self.cache_dir = Path(self.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ---- cache ------------------------------------------------------------------------
    def cache_path(self, api_id: str, bas_dd: str) -> Path:
        return self.cache_dir / api_id / f"{bas_dd}.json"

    def cached(self, api_id: str, bas_dd: str) -> list[dict] | None:
        p = self.cache_path(api_id, bas_dd)
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    # ---- HTTP -------------------------------------------------------------------------
    def _get(self, api_id: str, bas_dd: str) -> list[dict]:
        if api_id not in ENDPOINTS:
            raise KeyError(f"unknown api_id {api_id}; known: {sorted(ENDPOINTS)}")
        url = f"{BASE_URL}/{ENDPOINTS[api_id]}/{api_id}.json?" + urllib.parse.urlencode({"basDd": bas_dd})
        req = urllib.request.Request(url, headers={"AUTH_KEY": self.api_key, "User-Agent": "kronos-invest/0.1"})
        delay = 1.0
        for attempt in range(1, self.max_retries + 1):
            wait = self.min_interval - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            try:
                self._last_call = time.time()
                self.calls += 1
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                return self._rows(body, api_id, bas_dd)
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    raise KRXRateLimitError("KRX daily quota (10,000 calls/key) exceeded; resets at midnight KST") from None
                if e.code in (401, 403):
                    raise KRXError(f"HTTP {e.code}: check the AUTH_KEY and that the '{api_id}' service was applied for on openapi.krx.co.kr") from None
                if attempt == self.max_retries:
                    raise KRXError(f"HTTP {e.code} for {api_id} {bas_dd}") from None
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
                if attempt == self.max_retries:
                    raise KRXError(f"{type(e).__name__} for {api_id} {bas_dd}") from None
            self.log(f"  retry {attempt}/{self.max_retries} {api_id} {bas_dd} in {delay:.0f}s")
            time.sleep(delay)
            delay = min(delay * 2, 30)
        raise KRXError("unreachable")

    @staticmethod
    def _rows(body: dict, api_id: str, bas_dd: str) -> list[dict]:
        if isinstance(body, dict) and str(body.get("respCode", "")).strip() not in ("", "0", "200"):
            raise KRXError(f"{api_id} {bas_dd}: respCode={body.get('respCode')} {body.get('respMsg', '')}")
        for k, v in (body.items() if isinstance(body, dict) else []):
            if k.startswith("OutBlock") and isinstance(v, list):
                return v
        raise KRXError(f"{api_id} {bas_dd}: no OutBlock in response ({list(body)[:5] if isinstance(body, dict) else type(body)})")

    # ---- public ---------------------------------------------------------------------
    def fetch(self, api_id: str, bas_dd: str, refetch_empty: bool = False) -> list[dict]:
        rows = self.cached(api_id, bas_dd)
        if rows is not None and not (refetch_empty and rows == []):
            return rows
        rows = self._get(api_id, bas_dd)
        p = self.cache_path(api_id, bas_dd)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")   # unique per process: parallel fetchers are safe
        tmp.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
        return rows

    def fetch_range(self, api_id: str, start, end, refetch_empty: bool = False, max_calls: int | None = None) -> dict[str, list[dict]]:
        """All weekdays in [start, end]; returns {YYYYMMDD: rows}. Weekends are never requested.
        Records the pull in {cache_dir}/PULL_METADATA.json (vintage bookkeeping)."""
        out: dict[str, list[dict]] = {}
        days = [d.strftime("%Y%m%d") for d in pd.bdate_range(start, end)]
        todo = [d for d in days if self.cached(api_id, d) is None or (refetch_empty and self.cached(api_id, d) == [])]
        self.log(f"{api_id}: {len(days)} weekdays, {len(todo)} to fetch, {len(days) - len(todo)} cached")
        if max_calls is not None and len(todo) > max_calls:
            raise KRXRateLimitError(f"{len(todo)} calls needed for {api_id} but max_calls={max_calls}")
        n_empty, calls0 = 0, self.calls
        for i, d in enumerate(days):
            rows = self.fetch(api_id, d, refetch_empty)
            out[d] = rows
            n_empty += rows == []
            if d in todo and (self.calls % 50 == 0):
                self.log(f"  {api_id} {d} ({i + 1}/{len(days)}) calls={self.calls}")
        self.log(f"{api_id}: done, {n_empty} empty weekdays (holidays), total calls this session={self.calls}")
        self.record_pull(api_id, start, end, len(days), n_empty, self.calls - calls0,
                         sum(len(r) for r in out.values()))
        return out

    # ---- vintage bookkeeping ------------------------------------------------------------
    def metadata_path(self) -> Path:
        return self.cache_dir / "PULL_METADATA.json"

    def record_pull(self, api_id: str, start, end, n_days: int, n_empty: int, n_calls: int, n_rows: int) -> None:
        meta = json.loads(self.metadata_path().read_text(encoding="utf-8")) if self.metadata_path().exists() else {}
        entry = meta.get(api_id, {})
        entry.update({
            "source": f"{BASE_URL}/{ENDPOINTS[api_id]}/{api_id}.json", "date_range": [str(pd.Timestamp(start).date()), str(pd.Timestamp(end).date())],
            "n_weekdays": n_days, "n_empty_weekdays": n_empty, "n_rows": n_rows,
            "first_pulled_at_utc": entry.get("first_pulled_at_utc") or pd.Timestamp.utcnow().isoformat(),
            "last_completed_at_utc": pd.Timestamp.utcnow().isoformat(),
            "calls_last_run": n_calls, "calls_total": int(entry.get("calls_total", 0)) + n_calls,
        })
        meta[api_id] = entry
        tmp = self.metadata_path().with_name(f"PULL_METADATA.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.metadata_path())
