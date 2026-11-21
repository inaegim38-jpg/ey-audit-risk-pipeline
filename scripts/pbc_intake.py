#!/usr/bin/env python3
"""Fail-closed PBC file intake for GL, AR, TB, and contract CSV files.

The intake records metadata only. It refuses missing files, hash mismatches,
empty files, unsupported file types, and missing required columns. Normalized
GL/AR/contract facts are loaded only in the subsequent tie-out phase.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

import psycopg2

REQUIRED_COLUMNS = {
    "GL": {"doc_no", "posting_date", "account_code", "debit", "credit"},
    "TB": {"account_code", "debit", "credit"},
    "AR": {"invoice_no", "customer_id", "amount", "currency"},
    "CONTRACT": {"contract_id", "vendor_id", "contract_amount"},
}


def canonical_header(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def header_sha256(headers: list[str]) -> str:
    canonical = "|".join(sorted(canonical_header(h) for h in headers))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def decimal_value(value: str, field: str, row_number: int) -> Decimal:
    try:
        return Decimal((value or "0").replace(",", "").strip() or "0")
    except (InvalidOperation, AttributeError) as exc:
        raise RuntimeError(f"{field} must be numeric at row {row_number}") from exc


def inspect_csv(path: Path, pbc_file_type: str) -> tuple[int, str, Decimal]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise RuntimeError("CSV header is required")
            headers = [canonical_header(h) for h in reader.fieldnames]
            missing = sorted(REQUIRED_COLUMNS[pbc_file_type] - set(headers))
            if missing:
                raise RuntimeError(f"missing required columns: {', '.join(missing)}")
            row_count = 0
            debit_credit_diff = Decimal("0")
            for row_number, raw_row in enumerate(reader, start=2):
                row = {canonical_header(k): (v or "").strip() for k, v in raw_row.items()}
                row_count += 1
                if pbc_file_type in {"GL", "TB"}:
                    debit_credit_diff += decimal_value(row.get("debit", "0"), "debit", row_number)
                    debit_credit_diff -= decimal_value(row.get("credit", "0"), "credit", row_number)
            if row_count == 0:
                raise RuntimeError("at least one data row is required")
            return row_count, header_sha256(headers), debit_credit_diff
    except UnicodeDecodeError as exc:
        raise RuntimeError("PBC CSV must be UTF-8 or UTF-8 with BOM") from exc


def ingest(path: Path, pbc_file_type: str, engagement_id: str,
           expected_sha256: str | None, ingested_by: str) -> None:
    if pbc_file_type not in REQUIRED_COLUMNS:
        raise RuntimeError("file type must be GL, TB, AR, or CONTRACT")
    if not path.is_file():
        raise RuntimeError(f"PBC file not found: {path}")
    if path.suffix.lower() != ".csv":
        raise RuntimeError("only UTF-8 CSV is supported by this intake phase")
    content_sha256 = sha256_file(path)
    if expected_sha256 and content_sha256.lower() != expected_sha256.lower():
        raise RuntimeError("SHA-256 mismatch; refusing PBC intake")
    row_count, header_hash, diff = inspect_csv(path, pbc_file_type)
    tieout_status = "PASSED" if pbc_file_type in {"GL", "TB"} and abs(diff) <= Decimal("1") else (
        "FAILED" if pbc_file_type in {"GL", "TB"} else "PENDING"
    )
    with psycopg2.connect(
        host=os.getenv("PGHOST", "127.0.0.1"), port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "audit_db"), user=os.getenv("PGUSER", "audit_admin"),
        password=os.getenv("PGPASSWORD", ""),
    ) as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO audit_core.stg_ingestion_log
                (engagement_id, file_name, sha256_hash, row_count, debit_credit_diff,
                 tieout_status, ingested_by, pbc_file_type, source_path, header_sha256)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (engagement_id, sha256_hash) DO UPDATE SET
                row_count = EXCLUDED.row_count,
                debit_credit_diff = EXCLUDED.debit_credit_diff,
                tieout_status = EXCLUDED.tieout_status,
                ingested_by = EXCLUDED.ingested_by,
                pbc_file_type = EXCLUDED.pbc_file_type,
                source_path = EXCLUDED.source_path,
                header_sha256 = EXCLUDED.header_sha256,
                ingested_at = NOW()
        """, (engagement_id, path.name, content_sha256, row_count, diff,
              tieout_status, ingested_by, pbc_file_type, str(path.resolve()), header_hash))
    print(f"PBC intake stored: type={pbc_file_type}, rows={row_count}, tieout={tieout_status}, sha256={content_sha256}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    parser.add_argument("--file-type", required=True, choices=sorted(REQUIRED_COLUMNS))
    parser.add_argument("--engagement-id", required=True)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--ingested-by", default="pbc-intake")
    args = parser.parse_args()
    try:
        ingest(Path(args.file), args.file_type, args.engagement_id, args.expected_sha256, args.ingested_by)
        return 0
    except RuntimeError as exc:
        print(f"PBC intake: FAIL ({exc})", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
