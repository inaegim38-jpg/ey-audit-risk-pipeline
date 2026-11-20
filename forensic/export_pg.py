#!/usr/bin/env python3
"""파이프라인 산출물을 n8n이 바로 소비할 수 있는 NDJSON으로 내보낸다.

n8n Execute Command 노드의 stdout을 Code 노드에서 한 줄씩 JSON.parse 하는 구조를
전제로, 로그는 stderr로 보내고 stdout에는 레코드만 출력한다.

    python3 -m forensic.export_pg --db demo/case.db > findings.ndjson
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

DEFAULT_CASE = "CASE-2026-014"


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def export(db: Path, case_id: str) -> list[dict]:
    conn = sqlite3.connect(db)
    findings = _rows(
        conn,
        """
        SELECT f.finding_id, f.category, f.title, f.severity, f.amount, f.item_count,
               f.description, f.method, f.limitation, f.status, f.created_at,
               COUNT(fe.evidence_id) AS evidence_count
          FROM findings f
          LEFT JOIN finding_evidence fe ON fe.finding_id = f.finding_id
         GROUP BY f.finding_id
         ORDER BY f.finding_id
        """,
    )
    conn.close()
    for row in findings:
        row["case_id"] = case_id
        row["amount"] = row["amount"] if row["amount"] is not None else 0
    return findings


def main() -> None:
    ap = argparse.ArgumentParser(description="포렌식 파인딩 NDJSON 내보내기")
    ap.add_argument("--db", default="demo/case.db")
    ap.add_argument("--case", default=DEFAULT_CASE)
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        sys.exit(f"[export_pg] DB 없음: {db} — 먼저 python3 -m forensic.run_demo --out demo")

    rows = export(db, args.case)
    for row in rows:
        sys.stdout.write(json.dumps(row, ensure_ascii=False) + "\n")
    sys.stderr.write(f"[export_pg] {len(rows)}건 내보냄 (case {args.case})\n")


if __name__ == "__main__":
    main()
