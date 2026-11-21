import os
import psycopg2
import pytest


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


def test_rule_catalog_has_14_active_rules():
    assert scalar("SELECT count(*) FROM audit_core.rule_catalog WHERE is_active") == 14


def test_finding_baseline_and_severity_distribution():
    assert scalar("SELECT count(*) FROM audit_core.fact_risk_finding") == 40
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT severity, count(*) FROM audit_core.fact_risk_finding GROUP BY 1 ORDER BY 1")
        assert dict(cur.fetchall()) == {"CRITICAL": 5, "HIGH": 11, "LOW": 2, "MEDIUM": 22}


def test_evidence_coverage_baseline():
    row = scalar("SELECT coverage_pct FROM audit_core.vw_evidence_coverage LIMIT 1")
    assert float(row) == pytest.approx(67.5)


def test_finding_unique_key_is_not_duplicated():
    assert scalar("""
        SELECT count(*) FROM (
          SELECT engagement_id, rule_id, ref_id
          FROM audit_core.fact_risk_finding
          GROUP BY 1,2,3 HAVING count(*) > 1
        ) d
    """) == 0
