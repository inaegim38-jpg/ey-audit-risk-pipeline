#!/usr/bin/env python3
"""Load validated synthetic/client PBC CSV rows and run tie-out checks.

This module is deliberately separate from intake: intake validates provenance and
metadata; this phase loads normalized rows and stores reproducible tie-out results.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import os
from decimal import Decimal
from pathlib import Path

import psycopg2
import psycopg2.extras

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pbc_intake", ROOT / "scripts" / "pbc_intake.py")
PBC_INTAKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PBC_INTAKE)


def db():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "127.0.0.1"), port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "audit_db"), user=os.getenv("PGUSER", "audit_admin"),
        password=os.getenv("PGPASSWORD", ""),
    )


def dec(value: str) -> Decimal:
    return Decimal((value or "0").replace(",", "").strip() or "0")


def batch_id(conn, engagement_id: str, sha256: str) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT batch_id FROM audit_core.stg_ingestion_log WHERE engagement_id=%s AND sha256_hash=%s", (engagement_id, sha256))
        row = cur.fetchone()
    if not row:
        raise RuntimeError("validated intake batch not found")
    return str(row[0])


def load_file(path: Path, file_type: str, engagement_id: str, expected_sha256: str | None, ingested_by: str) -> None:
    PBC_INTAKE.ingest(path, file_type, engagement_id, expected_sha256, ingested_by)
    sha = PBC_INTAKE.sha256_file(path)
    with db() as conn:
        bid = batch_id(conn, engagement_id, sha)
        with path.open("r", encoding="utf-8-sig", newline="") as handle, conn.cursor() as cur:
            reader = csv.DictReader(handle)
            for raw in reader:
                row = {PBC_INTAKE.canonical_header(k): (v or "").strip() for k, v in raw.items()}
                if file_type == "GL":
                    cur.execute("""INSERT INTO audit_core.fact_pbc_gl_line
                        (batch_id, engagement_id, doc_no, posting_date, account_code, debit, credit)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (batch_id, doc_no) DO UPDATE SET posting_date=EXCLUDED.posting_date,
                        account_code=EXCLUDED.account_code, debit=EXCLUDED.debit, credit=EXCLUDED.credit""",
                        (bid, engagement_id, row["doc_no"], row["posting_date"], row["account_code"], dec(row["debit"]), dec(row["credit"])))
                elif file_type == "TB":
                    cur.execute("""INSERT INTO audit_core.fact_pbc_tb_balance
                        (batch_id, engagement_id, account_code, debit, credit) VALUES (%s,%s,%s,%s,%s)
                        ON CONFLICT (batch_id, account_code) DO UPDATE SET debit=EXCLUDED.debit, credit=EXCLUDED.credit""",
                        (bid, engagement_id, row["account_code"], dec(row["debit"]), dec(row["credit"])))
                elif file_type == "AR":
                    cur.execute("""INSERT INTO audit_core.fact_pbc_ar_item
                        (batch_id, engagement_id, invoice_no, customer_id, amount, currency) VALUES (%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (batch_id, invoice_no) DO UPDATE SET customer_id=EXCLUDED.customer_id,
                        amount=EXCLUDED.amount, currency=EXCLUDED.currency""",
                        (bid, engagement_id, row["invoice_no"], row["customer_id"], dec(row["amount"]), row["currency"]))
                else:
                    cur.execute("""INSERT INTO audit_core.fact_pbc_contract
                        (batch_id, engagement_id, contract_id, vendor_id, contract_amount) VALUES (%s,%s,%s,%s,%s)
                        ON CONFLICT (batch_id, contract_id) DO UPDATE SET vendor_id=EXCLUDED.vendor_id,
                        contract_amount=EXCLUDED.contract_amount""",
                        (bid, engagement_id, row["contract_id"], row["vendor_id"], dec(row["contract_amount"])))
    print(f"PBC facts loaded: type={file_type}, batch_id={bid}")


def run_tieouts(engagement_id: str) -> None:
    with db() as conn, conn.cursor() as cur:
        cur.execute("""SELECT DISTINCT ON (pbc_file_type) batch_id, pbc_file_type
            FROM audit_core.stg_ingestion_log WHERE engagement_id=%s AND pbc_file_type IS NOT NULL
            ORDER BY pbc_file_type, ingested_at DESC""", (engagement_id,))
        batches = {row[1]: str(row[0]) for row in cur.fetchall()}
        gl, tb = batches.get("GL"), batches.get("TB")
        if gl and tb:
            cur.execute("""WITH g AS (SELECT account_code, SUM(debit) debit, SUM(credit) credit
                         FROM audit_core.fact_pbc_gl_line WHERE batch_id=%s GROUP BY account_code),
                t AS (SELECT account_code, debit, credit FROM audit_core.fact_pbc_tb_balance WHERE batch_id=%s),
                d AS (SELECT COALESCE(g.account_code,t.account_code) account_code,
                             COALESCE(g.debit,0)-COALESCE(t.debit,0) debit_diff,
                             COALESCE(g.credit,0)-COALESCE(t.credit,0) credit_diff
                      FROM g FULL JOIN t USING (account_code))
                SELECT COALESCE(SUM(ABS(debit_diff)+ABS(credit_diff)),0),
                       COUNT(*) FILTER (WHERE ABS(debit_diff)+ABS(credit_diff) > 1)
                FROM d""", (gl, tb))
            difference, exceptions = cur.fetchone()
            status = "PASSED" if difference <= 1 else "FAILED"
            cur.execute("""INSERT INTO audit_core.pbc_tieout_result
                (engagement_id, rule_id, left_batch_id, right_batch_id, status, difference, item_count, detail)
                VALUES (%s,'PBC-TB-01',%s,%s,%s,%s,%s,%s)
                ON CONFLICT (engagement_id, rule_id, left_batch_id, right_batch_id) DO UPDATE SET
                  status=EXCLUDED.status, difference=EXCLUDED.difference, item_count=EXCLUDED.item_count,
                  detail=EXCLUDED.detail, created_at=NOW()""",
                (engagement_id, gl, tb, status, difference, exceptions,
                 psycopg2.extras.Json({"description": "GL account aggregate versus TB", "exception_accounts": exceptions})))
        for file_type, rule_id, table_name, amount_col in [
            ("AR", "PBC-AR-01", "fact_pbc_ar_item", "amount"),
            ("CONTRACT", "PBC-CON-01", "fact_pbc_contract", "contract_amount"),
        ]:
            bid = batches.get(file_type)
            if not bid:
                continue
            cur.execute(f"SELECT COUNT(*), COALESCE(SUM({amount_col}),0) FROM audit_core.{table_name} WHERE batch_id=%s", (bid,))
            count, total = cur.fetchone()
            cur.execute("""INSERT INTO audit_core.pbc_tieout_result
                (engagement_id, rule_id, left_batch_id, right_batch_id, status, item_count, detail)
                VALUES (%s,%s,%s,%s,'PENDING',%s,%s)
                ON CONFLICT (engagement_id, rule_id, left_batch_id, right_batch_id) DO UPDATE SET
                  item_count=EXCLUDED.item_count, detail=EXCLUDED.detail, created_at=NOW()""",
                (engagement_id, rule_id, bid, bid, count,
                 psycopg2.extras.Json({"description": "Detailed tie-out requires control-account mapping", "total_amount": str(total)})))
        cur.execute("SELECT rule_id, status, difference, item_count FROM audit_core.pbc_tieout_result WHERE engagement_id=%s ORDER BY rule_id", (engagement_id,))
        for row in cur.fetchall():
            print(f"{row[0]}: status={row[1]}, difference={row[2]}, items={row[3]}")
        cur.execute("SELECT * FROM audit_core.run_pbc_findings(%s)", (engagement_id,))
        for row in cur.fetchall():
            print(f"{row[0]}: candidates={row[1]}, requests={row[2]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    load = sub.add_parser("load")
    load.add_argument("--file", required=True)
    load.add_argument("--file-type", required=True, choices=["GL", "TB", "AR", "CONTRACT"])
    load.add_argument("--engagement-id", required=True)
    load.add_argument("--expected-sha256")
    load.add_argument("--ingested-by", default="pbc-tieout")
    tie = sub.add_parser("tieout")
    tie.add_argument("--engagement-id", required=True)
    args = parser.parse_args()
    if args.command == "load":
        load_file(Path(args.file), args.file_type, args.engagement_id, args.expected_sha256, args.ingested_by)
    else:
        run_tieouts(args.engagement_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
