#!/usr/bin/env python3
"""Architecture v2 source adapters.

The adapters fail closed when credentials are absent or upstream responses are
not successful. Raw JSON is stored before normalization with request and payload
hashes so every derived fact can be traced back to an observed response.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import psycopg2

DART_URL = "https://opendart.fss.or.kr/api/list.json"
ECOS_TEMPLATE = "https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/100/{stat_code}/{cycle}/{start}/{end}/{item}"


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value or value.startswith("replace-"):
        raise RuntimeError(f"missing required integration setting: {name}")
    return value


def fetch_json(url: str, timeout: int = 30) -> tuple[int, dict]:
    request = Request(url, headers={"User-Agent": "Aegis-Guard/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"upstream HTTP error: {exc.code}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError("upstream request or JSON decoding failed") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("upstream response must be a JSON object")
    return status, payload


def store_payload(source_code: str, payload: dict, status: int, request_params: dict,
                  engagement_id: str | None, period_start: date | None,
                  period_end: date | None, unit_code: str | None,
                  currency_code: str | None) -> int:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    request_fingerprint = hashlib.sha256(
        json.dumps(request_params, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    payload_sha256 = hashlib.sha256(raw.encode()).hexdigest()
    with psycopg2.connect(
        host=os.getenv("PGHOST", "127.0.0.1"),
        port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "audit_db"),
        user=os.getenv("PGUSER", "audit_admin"),
        password=required_env("PGPASSWORD"),
    ) as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO audit_core.stg_source_payload
                (source_code, engagement_id, request_fingerprint, period_start, period_end,
                 unit_code, currency_code, http_status, response_status, raw_payload,
                 payload_sha256, parse_status, parse_note)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, 'RECEIVED', NULL)
            ON CONFLICT (source_code, request_fingerprint, payload_sha256) DO UPDATE SET
                observed_at = NOW(), http_status = EXCLUDED.http_status,
                response_status = EXCLUDED.response_status
            RETURNING payload_id
        """, (source_code, engagement_id, request_fingerprint, period_start, period_end,
              unit_code, currency_code, status, str(payload.get("status", "")), raw,
              payload_sha256))
        return cur.fetchone()[0]


def fetch_dart(corp_code: str, start: str, end: str, engagement_id: str | None) -> int:
    key = required_env("DART_API_KEY")
    params = {"crtfc_key": key, "corp_code": corp_code, "bgn_de": start,
              "end_de": end, "page_count": "100"}
    # The key is used only in the request and is never included in the fingerprint.
    fingerprint_params = {k: v for k, v in params.items() if k != "crtfc_key"}
    status, payload = fetch_json(f"{DART_URL}?{urlencode(params)}")
    if payload.get("status") != "000":
        raise RuntimeError(f"OpenDART rejected request with status {payload.get('status')}")
    payload_id = store_payload("DART_DISCLOSURE", payload, status, fingerprint_params,
                               engagement_id, date.fromisoformat(f"{start[:4]}-{start[4:6]}-{start[6:]}"),
                               date.fromisoformat(f"{end[:4]}-{end[4:6]}-{end[6:]}"),
                               "event", "KRW")
    print(f"DART payload stored: payload_id={payload_id}, rows={len(payload.get('list', []))}")
    return payload_id


def fetch_ecos(stat_code: str, cycle: str, start: str, end: str, item: str,
               engagement_id: str | None) -> int:
    key = required_env("ECOS_API_KEY")
    url = ECOS_TEMPLATE.format(key=quote(key, safe=""), stat_code=quote(stat_code, safe=""),
                               cycle=quote(cycle, safe=""), start=quote(start, safe=""),
                               end=quote(end, safe=""), item=quote(item, safe=""))
    status, payload = fetch_json(url)
    result = payload.get("StatisticSearch", {})
    if result.get("RESULT", {}).get("CODE") not in (None, "INFO-000"):
        raise RuntimeError(f"ECOS rejected request with code {result.get('RESULT', {}).get('CODE')}")
    payload_id = store_payload("ECOS_STAT", payload, status,
                               {"stat_code": stat_code, "cycle": cycle, "start": start,
                                "end": end, "item": item}, engagement_id,
                               date.fromisoformat(start.replace("/", "-")),
                               date.fromisoformat(end.replace("/", "-")),
                               stat_code, "KRW")
    rows = result.get("row", [])
    print(f"ECOS payload stored: payload_id={payload_id}, rows={len(rows)}")
    return payload_id


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="source", required=True)
    dart = sub.add_parser("dart")
    dart.add_argument("--corp-code", required=True)
    dart.add_argument("--start", required=True, help="YYYYMMDD")
    dart.add_argument("--end", required=True, help="YYYYMMDD")
    dart.add_argument("--engagement-id")
    ecos = sub.add_parser("ecos")
    ecos.add_argument("--stat-code", required=True)
    ecos.add_argument("--cycle", required=True)
    ecos.add_argument("--start", required=True, help="YYYY-MM-DD or YYYY/MM/DD")
    ecos.add_argument("--end", required=True, help="YYYY-MM-DD or YYYY/MM/DD")
    ecos.add_argument("--item", required=True)
    ecos.add_argument("--engagement-id")
    args = parser.parse_args()
    try:
        if args.source == "dart":
            fetch_dart(args.corp_code, args.start, args.end, args.engagement_id)
        else:
            fetch_ecos(args.stat_code, args.cycle, args.start, args.end, args.item, args.engagement_id)
        return 0
    except RuntimeError as exc:
        print(f"source adapter: FAIL ({exc})", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
