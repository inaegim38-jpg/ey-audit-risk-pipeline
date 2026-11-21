-- ============================================================================
-- 16. FX-AR / IFRS 9 ECL 시뮬레이터
-- ============================================================================
-- 목적: 기존 FX-02의 업종 ECL 커버리지 근사를 보완한다.
-- 원칙:
--   1) 기존 fact_risk_finding 40건과 FX-02 결과는 변경하지 않는다.
--   2) 시나리오 결과는 fact_ecl_recalc에 별도 저장한다.
--   3) 파인딩 방출은 run_ecl_scenarios(..., TRUE)로 명시적으로 요청한다.
--   4) 데이터가 합성인 만큼 PD/LGD 곡선은 모델 입력 가정이며 실제 고객 모델이 아니다.
-- ============================================================================

CREATE TABLE IF NOT EXISTS audit_core.dim_fx_scenario (
    scenario_code  VARCHAR(20) PRIMARY KEY,
    scenario_name  VARCHAR(80) NOT NULL,
    shock_pct      NUMERIC(7,3) NOT NULL,
    scenario_band  VARCHAR(20) NOT NULL,
    is_base        BOOLEAN NOT NULL DEFAULT FALSE,
    description    TEXT NOT NULL,
    version        VARCHAR(20) NOT NULL DEFAULT '1.0',
    CHECK (scenario_band IN ('BASE', 'STRESS_10', 'STRESS_20'))
);

INSERT INTO audit_core.dim_fx_scenario
    (scenario_code, scenario_name, shock_pct, scenario_band, is_base, description)
VALUES
    ('BASE',    '기준 환율',       0.000, 'BASE',     TRUE,  '관측 환율 유지'),
    ('FX_UP_10', '환율 상승 +10%',  10.000, 'STRESS_10', FALSE, '외화 AR의 원화 환산액 +10%'),
    ('FX_DN_10', '환율 하락 -10%', -10.000, 'STRESS_10', FALSE, '외화 AR의 원화 환산액 -10%'),
    ('FX_UP_20', '환율 상승 +20%',  20.000, 'STRESS_20', FALSE, '외화 AR의 원화 환산액 +20%'),
    ('FX_DN_20', '환율 하락 -20%', -20.000, 'STRESS_20', FALSE, '외화 AR의 원화 환산액 -20%')
ON CONFLICT (scenario_code) DO UPDATE SET
    scenario_name = EXCLUDED.scenario_name,
    shock_pct = EXCLUDED.shock_pct,
    scenario_band = EXCLUDED.scenario_band,
    is_base = EXCLUDED.is_base,
    description = EXCLUDED.description,
    version = EXCLUDED.version;

CREATE TABLE IF NOT EXISTS audit_core.dim_pd_lgd_curve (
    curve_id        BIGSERIAL PRIMARY KEY,
    ksic_code       VARCHAR(5) NOT NULL,
    rating_bucket   VARCHAR(20) NOT NULL,
    horizon_years  NUMERIC(6,3) NOT NULL DEFAULT 1.0,
    pd_rate         NUMERIC(9,6) NOT NULL CHECK (pd_rate >= 0 AND pd_rate <= 1),
    lgd_rate        NUMERIC(9,6) NOT NULL CHECK (lgd_rate >= 0 AND lgd_rate <= 1),
    discount_rate   NUMERIC(9,6) NOT NULL DEFAULT 0,
    as_of           DATE NOT NULL,
    source_note     TEXT NOT NULL,
    UNIQUE (ksic_code, rating_bucket, as_of)
);

INSERT INTO audit_core.dim_pd_lgd_curve
    (ksic_code, rating_bucket, horizon_years, pd_rate, lgd_rate,
     discount_rate, as_of, source_note)
VALUES
    ('29199', 'UNRATED', 1.000, 0.040000, 0.450000, 0.035000,
     DATE '2026-03-31', '합성 케이스 모델 입력 가정; 실제 내부등급·PD/LGD 승인값으로 교체 필요')
ON CONFLICT (ksic_code, rating_bucket, as_of) DO UPDATE SET
    horizon_years = EXCLUDED.horizon_years,
    pd_rate = EXCLUDED.pd_rate,
    lgd_rate = EXCLUDED.lgd_rate,
    discount_rate = EXCLUDED.discount_rate,
    source_note = EXCLUDED.source_note;

CREATE TABLE IF NOT EXISTS audit_core.fact_ecl_recalc (
    recalc_id          BIGSERIAL PRIMARY KEY,
    engagement_id      VARCHAR(40) NOT NULL REFERENCES audit_core.dim_engagement(engagement_id),
    scenario_code      VARCHAR(20) NOT NULL REFERENCES audit_core.dim_fx_scenario(scenario_code),
    fiscal_period      CHAR(6) NOT NULL,
    entity_id          UUID REFERENCES audit_core.dim_entity(entity_id),
    exposure_ref       VARCHAR(80) NOT NULL,
    currency_code      CHAR(3) NOT NULL,
    base_ead_krw       NUMERIC(20,2) NOT NULL,
    stressed_ead_krw   NUMERIC(20,2) NOT NULL,
    shock_pct          NUMERIC(7,3) NOT NULL,
    pd_rate            NUMERIC(9,6) NOT NULL,
    lgd_rate           NUMERIC(9,6) NOT NULL,
    discount_factor    NUMERIC(12,8) NOT NULL,
    ecl_amount         NUMERIC(20,2) NOT NULL,
    booked_allowance   NUMERIC(20,2) NOT NULL,
    gap_amount         NUMERIC(20,2) NOT NULL,
    gap_pct            NUMERIC(12,4),
    calculation_version VARCHAR(20) NOT NULL DEFAULT 'ECL-1.0',
    calculated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (engagement_id, scenario_code, fiscal_period, exposure_ref)
);

CREATE INDEX IF NOT EXISTS idx_ecl_eng_scenario
    ON audit_core.fact_ecl_recalc (engagement_id, scenario_code, fiscal_period);
CREATE INDEX IF NOT EXISTS idx_ecl_gap
    ON audit_core.fact_ecl_recalc (gap_amount DESC);

CREATE OR REPLACE FUNCTION audit_core.run_ecl_scenarios(
    p_engagement_id VARCHAR(40),
    p_emit_findings BOOLEAN DEFAULT FALSE
)
RETURNS TABLE (scenario_code VARCHAR(20), rows_upserted BIGINT, findings_emitted BIGINT)
LANGUAGE plpgsql
AS $$
#variable_conflict use_column
DECLARE
    v_period CHAR(6);
    v_total_ead NUMERIC;
    v_booked NUMERIC;
    v_findings BIGINT;
BEGIN
    SELECT MAX(fiscal_period) INTO v_period
    FROM audit_core.fact_journal
    WHERE engagement_id = p_engagement_id;

    SELECT COALESCE(SUM(krw_amount), 0) INTO v_total_ead
    FROM audit_core.fact_journal
    WHERE engagement_id = p_engagement_id
      AND std_kifrs_code IN ('AR', 'AR_FX');

    SELECT COALESCE(MAX((footnote_json->>'ecl_allowance')::numeric), 0) INTO v_booked
    FROM audit_core.fact_disclosure_line
    WHERE std_kifrs_code = 'PROVISION' AND fs_div = 'C';

    CREATE TEMP TABLE IF NOT EXISTS _ecl_run_result (
        scenario_code VARCHAR(20), rows_upserted BIGINT, findings_emitted BIGINT
    ) ON COMMIT DROP;
    TRUNCATE _ecl_run_result;

    INSERT INTO audit_core.fact_ecl_recalc (
        engagement_id, scenario_code, fiscal_period, entity_id, exposure_ref,
        currency_code, base_ead_krw, stressed_ead_krw, shock_pct,
        pd_rate, lgd_rate, discount_factor, ecl_amount, booked_allowance,
        gap_amount, gap_pct, calculation_version
    )
    SELECT
        p_engagement_id, s.scenario_code, v_period, j.counterparty_id,
        COALESCE(j.counterparty_id::text || ':' || j.currency_code,
                 'UNMAPPED:' || j.currency_code),
        j.currency_code,
        SUM(j.krw_amount),
        SUM(j.krw_amount) * CASE WHEN j.currency_code <> 'KRW'
                                 THEN 1 + s.shock_pct / 100 ELSE 1 END,
        s.shock_pct,
        c.pd_rate, c.lgd_rate,
        POWER(1 + c.discount_rate, -c.horizon_years),
        SUM(j.krw_amount) * CASE WHEN j.currency_code <> 'KRW'
                                 THEN 1 + s.shock_pct / 100 ELSE 1 END
            * c.pd_rate * c.lgd_rate
            * POWER(1 + c.discount_rate, -c.horizon_years),
        CASE WHEN v_total_ead = 0 THEN 0
             ELSE v_booked * SUM(j.krw_amount) / v_total_ead END,
        (
          SUM(j.krw_amount) * CASE WHEN j.currency_code <> 'KRW'
                                   THEN 1 + s.shock_pct / 100 ELSE 1 END
            * c.pd_rate * c.lgd_rate
            * POWER(1 + c.discount_rate, -c.horizon_years)
        ) - CASE WHEN v_total_ead = 0 THEN 0
                 ELSE v_booked * SUM(j.krw_amount) / v_total_ead END,
        CASE WHEN (
          SUM(j.krw_amount) * CASE WHEN j.currency_code <> 'KRW'
                                   THEN 1 + s.shock_pct / 100 ELSE 1 END
            * c.pd_rate * c.lgd_rate
            * POWER(1 + c.discount_rate, -c.horizon_years)
        ) = 0 THEN NULL ELSE ROUND((ABS(
          (
            SUM(j.krw_amount) * CASE WHEN j.currency_code <> 'KRW'
                                     THEN 1 + s.shock_pct / 100 ELSE 1 END
              * c.pd_rate * c.lgd_rate
              * POWER(1 + c.discount_rate, -c.horizon_years)
          ) - CASE WHEN v_total_ead = 0 THEN 0
                   ELSE v_booked * SUM(j.krw_amount) / v_total_ead END
        ) / NULLIF((
          SUM(j.krw_amount) * CASE WHEN j.currency_code <> 'KRW'
                                   THEN 1 + s.shock_pct / 100 ELSE 1 END
            * c.pd_rate * c.lgd_rate
            * POWER(1 + c.discount_rate, -c.horizon_years)
        ), 0) * 100)::numeric, 4) END,
        'ECL-1.0'
    FROM audit_core.fact_journal j
    CROSS JOIN audit_core.dim_fx_scenario s
    JOIN audit_core.dim_engagement e ON e.engagement_id = p_engagement_id
    JOIN audit_core.dim_entity ent ON ent.entity_id = e.entity_id
    JOIN audit_core.dim_pd_lgd_curve c
      ON c.ksic_code = ent.ksic_code
     AND c.rating_bucket = 'UNRATED'
     AND c.as_of = (SELECT MAX(as_of) FROM audit_core.dim_pd_lgd_curve)
    WHERE j.engagement_id = p_engagement_id
      AND j.std_kifrs_code IN ('AR', 'AR_FX')
    GROUP BY s.scenario_code, s.shock_pct, j.counterparty_id, j.currency_code,
             c.pd_rate, c.lgd_rate, c.discount_rate, c.horizon_years
    ON CONFLICT (engagement_id, scenario_code, fiscal_period, exposure_ref) DO UPDATE SET
        entity_id = EXCLUDED.entity_id,
        currency_code = EXCLUDED.currency_code,
        base_ead_krw = EXCLUDED.base_ead_krw,
        stressed_ead_krw = EXCLUDED.stressed_ead_krw,
        shock_pct = EXCLUDED.shock_pct,
        pd_rate = EXCLUDED.pd_rate,
        lgd_rate = EXCLUDED.lgd_rate,
        discount_factor = EXCLUDED.discount_factor,
        ecl_amount = EXCLUDED.ecl_amount,
        booked_allowance = EXCLUDED.booked_allowance,
        gap_amount = EXCLUDED.gap_amount,
        gap_pct = EXCLUDED.gap_pct,
        calculated_at = NOW();

    INSERT INTO _ecl_run_result
    SELECT s.scenario_code,
           (SELECT COUNT(*) FROM audit_core.fact_ecl_recalc f
            WHERE f.engagement_id = p_engagement_id AND f.scenario_code = s.scenario_code
              AND f.fiscal_period = v_period),
           0
    FROM audit_core.dim_fx_scenario s;

    IF p_emit_findings THEN
        INSERT INTO audit_core.fact_risk_finding
            (engagement_id, rule_id, risk_domain, severity, severity_score,
             ref_id, item_count, amount, detail, disposition)
        SELECT e.engagement_id, 'FX-02', r.risk_domain, r.severity, 82,
               'ECL:' || e.scenario_code || ':' || e.fiscal_period,
               COUNT(*)::int, SUM(ABS(e.gap_amount)),
               jsonb_build_object(
                   'scenario_code', e.scenario_code,
                   'shock_pct', MAX(e.shock_pct),
                   'ecl_amount', ROUND(SUM(e.ecl_amount), 0),
                   'booked_allowance', ROUND(SUM(e.booked_allowance), 0),
                   'gap_amount', ROUND(SUM(e.gap_amount), 0),
                   'model', 'EAD × PD × LGD × discount_factor',
                   'calculation_version', 'ECL-1.0'),
               'OPEN'
        FROM audit_core.fact_ecl_recalc e
        JOIN audit_core.rule_catalog r ON r.rule_id = 'FX-02'
        WHERE e.engagement_id = p_engagement_id
          AND e.fiscal_period = v_period
        GROUP BY e.engagement_id, e.scenario_code, e.fiscal_period,
                 r.risk_domain, r.severity
        HAVING ABS(SUM(e.gap_amount)) / NULLIF(SUM(e.ecl_amount), 0) * 100
               > (r.threshold->>'gap_pct')::numeric
        ON CONFLICT (engagement_id, rule_id, ref_id) DO UPDATE SET
            amount = EXCLUDED.amount,
            detail = EXCLUDED.detail,
            updated_at = NOW();

        GET DIAGNOSTICS v_findings = ROW_COUNT;
        UPDATE _ecl_run_result SET findings_emitted = v_findings;
    END IF;

    RETURN QUERY SELECT * FROM _ecl_run_result ORDER BY scenario_code;
END;
$$;

CREATE OR REPLACE VIEW audit_core.vw_ecl_scenario_summary AS
SELECT
    e.engagement_id,
    e.scenario_code,
    s.scenario_name,
    s.scenario_band,
    s.shock_pct,
    e.fiscal_period,
    COUNT(*) AS exposure_count,
    COUNT(*) FILTER (WHERE e.currency_code <> 'KRW') AS fx_exposure_count,
    ROUND(SUM(e.base_ead_krw), 0) AS base_ead_krw,
    ROUND(SUM(e.stressed_ead_krw), 0) AS stressed_ead_krw,
    ROUND(SUM(e.ecl_amount), 0) AS ecl_amount,
    ROUND(SUM(e.booked_allowance), 0) AS booked_allowance,
    ROUND(SUM(e.gap_amount), 0) AS gap_amount,
    ROUND((ABS(SUM(e.gap_amount)) / NULLIF(SUM(e.ecl_amount), 0) * 100)::numeric, 2) AS gap_pct
FROM audit_core.fact_ecl_recalc e
JOIN audit_core.dim_fx_scenario s USING (scenario_code)
GROUP BY e.engagement_id, e.scenario_code, s.scenario_name, s.scenario_band,
         s.shock_pct, e.fiscal_period;

CREATE OR REPLACE VIEW audit_core.vw_ecl_top_gaps AS
SELECT e.*, ent.name AS counterparty_name
FROM audit_core.fact_ecl_recalc e
LEFT JOIN audit_core.dim_entity ent ON ent.entity_id = e.entity_id
ORDER BY ABS(e.gap_amount) DESC;

INSERT INTO audit_core.change_log (object_type, object_name, version, changed_by, note)
SELECT 'SCHEMA', 'audit_core.ecl_simulator', 'ECL-1.0', 'system',
       'FX scenario 5-point grid + PD/LGD ECL recalc'
WHERE NOT EXISTS (
    SELECT 1 FROM audit_core.change_log
    WHERE object_name = 'audit_core.ecl_simulator' AND version = 'ECL-1.0'
);

-- 기본 실행: 기존 파인딩은 건드리지 않고 분석 테이블만 갱신한다.
SELECT * FROM audit_core.run_ecl_scenarios('ENG-2026-001', FALSE);
SELECT * FROM audit_core.vw_ecl_scenario_summary ORDER BY shock_pct;
