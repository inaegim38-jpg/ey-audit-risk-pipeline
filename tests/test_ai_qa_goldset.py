import os

import psycopg2


ENGAGEMENT = "ENG-2026-001"


def db():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "127.0.0.1"),
        port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "audit_db"),
        user=os.getenv("PGUSER", "audit_admin"),
        password=os.getenv("PGPASSWORD", "audit_password123"),
    )


def scalar(sql, params=()):
    with db() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()[0]


def test_goldset_has_ten_cases_and_full_pass_rate():
    row = scalar("""
        SELECT cases_evaluated::text || ':' || pass_count::text || ':' || pass_rate_pct::text
        FROM audit_core.vw_ai_qa_goldset_summary
        WHERE engagement_id=%s
    """, (ENGAGEMENT,))
    assert row == "10:10:100.00"


def test_goldset_covers_hallucination_and_low_accuracy_escalation():
    hallucination_pass = scalar("""
        SELECT hallucination_cases_pass
        FROM audit_core.vw_ai_qa_goldset_summary
        WHERE engagement_id=%s
    """, (ENGAGEMENT,))
    low_accuracy_escalated = scalar("""
        SELECT low_accuracy_escalated
        FROM audit_core.vw_ai_qa_goldset_summary
        WHERE engagement_id=%s
    """, (ENGAGEMENT,))
    assert hallucination_pass == 3
    assert low_accuracy_escalated == 2


def test_goldset_detects_mutated_ai_output_without_persisting_mutation():
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE audit_core.fact_ai_qa_run
                SET ai_json = jsonb_set(ai_json, '{amount}', '999999999'::jsonb)
                WHERE engagement_id=%s AND source_doc_ref='docs/contract_001.pdf'
            """, (ENGAGEMENT,))
            cur.execute("SELECT * FROM audit_core.run_ai_qa_goldset(%s)", (ENGAGEMENT,))
            result = cur.fetchone()
            assert result[2] == 1  # fail_count
        conn.rollback()
    finally:
        conn.close()


def test_goldset_does_not_change_finding_baseline():
    assert scalar("SELECT count(*) FROM audit_core.fact_risk_finding WHERE substring(rule_id, 1, 4) <> 'PPS-'") == 40
