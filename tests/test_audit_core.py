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


def test_rule_catalog_preserves_14_legacy_and_adds_procurement_and_pbc_rules():
    assert scalar("SELECT count(*) FROM audit_core.rule_catalog WHERE is_active") == 20
    assert scalar("SELECT count(*) FROM audit_core.rule_catalog WHERE is_active AND substring(rule_id, 1, 4) = 'PPS-'") == 3
    assert scalar("SELECT count(*) FROM audit_core.rule_catalog WHERE is_active AND substring(rule_id, 1, 4) = 'PBC-'") == 3


def test_finding_baseline_and_severity_distribution():
    assert scalar("SELECT count(*) FROM audit_core.fact_risk_finding WHERE rule_id NOT LIKE 'PPS-%%' AND rule_id NOT LIKE 'PBC-%%'") == 40
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT severity, count(*) FROM audit_core.fact_risk_finding WHERE rule_id NOT LIKE 'PPS-%%' AND rule_id NOT LIKE 'PBC-%%' GROUP BY 1 ORDER BY 1")
        assert dict(cur.fetchall()) == {"CRITICAL": 5, "HIGH": 11, "LOW": 2, "MEDIUM": 22}


def test_evidence_coverage_baseline():
    row = scalar("""
        SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE fe.finding_id IS NOT NULL)
                     / NULLIF(COUNT(*), 0), 2)
        FROM audit_core.fact_risk_finding f
        LEFT JOIN (SELECT DISTINCT finding_id FROM audit_core.finding_evidence) fe
          ON fe.finding_id = f.finding_id
        WHERE substring(f.rule_id, 1, 4) <> 'PPS-'
    """)
    assert float(row) == pytest.approx(67.5)


def test_finding_unique_key_is_not_duplicated():
    assert scalar("""
        SELECT count(*) FROM (
          SELECT engagement_id, rule_id, ref_id
          FROM audit_core.fact_risk_finding
          GROUP BY 1,2,3 HAVING count(*) > 1
        ) d
    """) == 0
