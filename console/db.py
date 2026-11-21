import json
import os
from contextlib import contextmanager
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

DB_CONFIG = {
    "host": os.getenv("PGHOST", "127.0.0.1"),
    "port": int(os.getenv("PGPORT", "5432")),
    "dbname": os.getenv("PGDATABASE", "audit_db"),
    "user": os.getenv("PGUSER", "audit_admin"),
    "password": os.getenv("PGPASSWORD", "audit_password123"),
}

@contextmanager
def connection():
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def fetch_all(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

def fetch_one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    rows = fetch_all(sql, params)
    return rows[0] if rows else None

def jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [jsonable(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value

def get_findings(severity: str | None = None, risk_domain: str | None = None,
                 disposition: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    conditions, params = [], []
    if severity:
        conditions.append("f.severity = %s"); params.append(severity)
    if risk_domain:
        conditions.append("f.risk_domain = %s"); params.append(risk_domain)
    if disposition:
        conditions.append("f.disposition = %s"); params.append(disposition)
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    params.append(min(max(limit, 1), 500))
    return fetch_all(f"""
        SELECT f.finding_id, f.engagement_id, f.rule_id, r.rule_name, f.risk_domain,
               f.severity,
               CASE f.severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2
                    WHEN 'MEDIUM' THEN 3 ELSE 4 END AS severity_rank,
               f.ref_id, f.item_count, f.amount, f.detail,
               f.disposition, f.reviewer_id, f.created_at
        FROM audit_core.fact_risk_finding f
        JOIN audit_core.rule_catalog r USING (rule_id)
        {where}
        ORDER BY severity_rank ASC, f.created_at ASC
        LIMIT %s
    """, tuple(params))

def get_finding(finding_id: str) -> dict[str, Any] | None:
    finding = fetch_one("""
        SELECT f.finding_id, f.engagement_id, f.rule_id, r.rule_name,
               f.risk_domain, f.severity, f.severity_score, f.ref_id,
               f.item_count, f.amount, f.detail, f.disposition,
               f.reviewer_id, f.reviewed_at, f.created_at, f.updated_at,
               r.threshold, r.version AS rule_version, r.description
        FROM audit_core.fact_risk_finding f
        JOIN audit_core.rule_catalog r USING (rule_id)
        WHERE f.finding_id = %s
    """, (finding_id,))
    if not finding:
        return None
    finding["evidence"] = fetch_all("""
        SELECT e.evidence_id, e.rel_path, e.doc_type, e.sha256,
               fe.ref_table, fe.ref_pk, fe.note
        FROM audit_core.finding_evidence fe
        JOIN forensic.evidence e USING (evidence_id)
        WHERE fe.finding_id = %s
        ORDER BY e.evidence_id
    """, (finding_id,))
    finding["ai_qa"] = fetch_all("""
        SELECT qa_id, agent_name, model_version, prompt_version,
               source_doc_ref, source_sha256, ai_json, truth_json,
               field_total, field_exact, exact_match_rate,
               hallucination_yn, qa_status, created_at
        FROM audit_core.fact_ai_qa_run
        WHERE engagement_id = %s
        ORDER BY created_at DESC
        LIMIT 10
    """, (finding["engagement_id"],))
    finding["procurement"] = []
    if finding["rule_id"].startswith("PPS-"):
        source_payload_id = finding.get("detail", {}).get("source_payload_id")
        bid_notice_no = finding.get("detail", {}).get("bid_notice_no")
        finding["procurement"] = fetch_all("""
            SELECT p.bid_notice_no, p.bid_notice_ord, p.notice_name,
                   p.business_division, p.notice_status, p.notice_date,
                   p.bid_begin_at, p.bid_close_at, p.opening_at,
                   p.demand_agency_name, p.notice_agency_name,
                   p.estimated_amount, p.presumption_price, p.contract_method,
                   p.source_locator, p.source_payload_id,
                   s.payload_sha256, s.request_fingerprint,
                   s.period_start, s.period_end
            FROM audit_core.fact_procurement_event p
            JOIN audit_core.stg_source_payload s ON s.payload_id = p.source_payload_id
            WHERE p.source_payload_id = %s AND p.bid_notice_no = %s
            ORDER BY p.bid_notice_ord
        """, (source_payload_id, bid_notice_no))
    return finding

def update_disposition(finding_id: str, disposition: str, reviewer_id: str) -> dict[str, Any] | None:
    allowed = {"OPEN", "IN_REVIEW", "EXPLAINED", "ESCALATED", "FALSE_POSITIVE"}
    if disposition not in allowed:
        raise ValueError("invalid disposition")
    with connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            UPDATE audit_core.fact_risk_finding
            SET disposition = %s, reviewer_id = %s, reviewed_at = NOW(), updated_at = NOW()
            WHERE finding_id = %s
            RETURNING finding_id, disposition, reviewer_id, reviewed_at
        """, (disposition, reviewer_id[:120], finding_id))
        result = cur.fetchone()
        if not result:
            return None
        cur.execute("""
            INSERT INTO audit_core.change_log
                (object_type, object_name, version, changed_by, note)
            VALUES ('FINDING', %s, '1.0', %s, %s)
        """, (str(finding_id), reviewer_id[:120], f"disposition -> {disposition}"))
        return dict(result)

def get_evidence(evidence_id: str) -> dict[str, Any] | None:
    return fetch_one("""
        SELECT evidence_id, case_id, rel_path, doc_type, size_bytes,
               sha256, source_mtime, collected_at, custodian, storage
        FROM forensic.evidence WHERE evidence_id = %s
    """, (evidence_id,))

def get_coverage() -> dict[str, Any] | None:
    return fetch_one("SELECT * FROM audit_core.vw_evidence_coverage LIMIT 1")

def get_unlinked_findings() -> list[dict[str, Any]]:
    return fetch_all("""
        SELECT f.finding_id, f.rule_id, r.rule_name, f.severity,
               f.risk_domain, f.ref_id, f.item_count, f.disposition
        FROM audit_core.fact_risk_finding f
        JOIN audit_core.rule_catalog r USING (rule_id)
        LEFT JOIN audit_core.finding_evidence fe USING (finding_id)
        WHERE fe.finding_id IS NULL
        ORDER BY CASE f.severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 WHEN 'MEDIUM' THEN 3 ELSE 4 END,
                 f.rule_id, f.ref_id
    """)

def get_disclosures() -> list[dict[str, Any]]:
    return fetch_all("""
        SELECT dart_corp_code, rcept_no, rcept_dt, report_nm, flr_nm,
               is_correction, is_major, watch_level
        FROM audit_core.vw_disclosure_watch
        ORDER BY rcept_dt DESC NULLS LAST, watch_level DESC
    """)
