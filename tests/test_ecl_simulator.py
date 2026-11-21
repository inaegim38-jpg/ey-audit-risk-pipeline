import os

import psycopg2
import pytest


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


def rows(sql, params=()):
    with db() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def test_ecl_has_five_scenarios_and_curve():
    assert scalar("SELECT count(*) FROM audit_core.dim_fx_scenario") == 5
    assert scalar("SELECT count(*) FROM audit_core.dim_pd_lgd_curve") >= 1


def test_ecl_recalc_has_same_grain_per_scenario():
    result = rows("""
        SELECT scenario_code, count(*)
        FROM audit_core.fact_ecl_recalc
        WHERE engagement_id = %s
        GROUP BY scenario_code
        ORDER BY scenario_code
    """, (ENGAGEMENT,))
    assert len(result) == 5
    assert {count for _, count in result} == {66}


def test_ecl_summary_has_five_scenarios_and_baseline_is_preserved():
    assert scalar("SELECT count(*) FROM audit_core.vw_ecl_scenario_summary WHERE engagement_id=%s", (ENGAGEMENT,)) == 5
    assert scalar("SELECT count(*) FROM audit_core.fact_risk_finding") == 40


def test_fx_shock_direction_changes_only_non_krw_exposure():
    base, up, down = rows("""
        SELECT scenario_code, stressed_ead_krw
        FROM audit_core.vw_ecl_scenario_summary
        WHERE engagement_id=%s AND scenario_code IN ('BASE', 'FX_UP_20', 'FX_DN_20')
        ORDER BY scenario_code
    """, (ENGAGEMENT,))
    values = dict((code, float(amount)) for code, amount in (base, up, down))
    assert values["FX_UP_20"] > values["BASE"] > values["FX_DN_20"]

    krw_variance = scalar("""
        SELECT max(exposure_variance)
        FROM (
          SELECT exposure_ref, max(stressed_ead_krw) - min(stressed_ead_krw) AS exposure_variance
          FROM audit_core.fact_ecl_recalc
          WHERE engagement_id=%s AND currency_code='KRW'
          GROUP BY exposure_ref
        ) v
    """, (ENGAGEMENT,))
    assert float(krw_variance) == pytest.approx(0)


def test_ecl_top_gaps_is_populated():
    assert scalar("SELECT count(*) FROM audit_core.vw_ecl_top_gaps WHERE engagement_id=%s", (ENGAGEMENT,)) > 5
