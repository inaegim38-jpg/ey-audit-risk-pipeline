-- ============================================================================
-- 12. audit_core 룰 엔진 (14종)
-- ============================================================================
-- 구조
--   rule_catalog      : 룰 ID·영역·심각도·임계값(JSONB)·버전
--   run_rules(eng_id) : 14종을 한 번에 실행하고 (신규/갱신) 건수를 반환
--   fact_risk_finding : (engagement_id, rule_id, ref_id) 유니크 → 재실행 멱등
--
-- 설계 메모
--   - 임계값은 코드가 아니라 rule_catalog.threshold에 둔다. 룰을 고치면
--     change_log에 버전을 남기고, 조서에는 그 버전을 인용한다.
--   - 룰 SQL은 "후보"만 만든다. 확정/오탐 판정은 사람이 disposition으로 남긴다.
-- ============================================================================

INSERT INTO audit_core.rule_catalog
    (rule_id, rule_name, risk_domain, severity, threshold, description, version)
VALUES
    ('JET-01', '벤포드 1자리 편차',        'FORENSIC_JET', 'LOW',
     '{"mad": 0.015, "min_amount": 1000}'::jsonb,
     '기간별 첫자리 분포와 벤포드 기대분포의 평균절대편차(MAD)가 임계값을 넘으면 조작 후보', '1.0'),
    ('JET-02', '직무분리(SoD) 위반',        'FORENSIC_JET', 'HIGH',
     '{"same_user": true}'::jsonb,
     '기안자와 승인자가 동일한 전표. 내부통제 실패의 1차 신호', '1.0'),
    ('JET-03', '주말·심야 전표',            'FORENSIC_JET', 'MEDIUM',
     '{"night_from": "22:00", "night_to": "06:00"}'::jsonb,
     '비영업일 또는 심야에 기표된 전표. 허위 증빙·마감 조작의 전형적 흔적', '1.0'),
    ('JET-04', '계정 내 다변량 이상',       'FORENSIC_JET', 'MEDIUM',
     '{"zscore": 3.5}'::jsonb,
     '계정별 금액 분포의 z-score가 임계값을 넘는 전표', '1.0'),
    ('TIE-01', '3-Way Tie-out 불일치',      'DATA_QUALITY', 'CRITICAL',
     '{"tolerance_krw": 1000}'::jsonb,
     '전표 합계와 공시 금액의 차이가 허용오차를 넘는 계정', '1.0'),
    ('REL-01', '미공시 특수관계자',         'RELATED_PARTY', 'HIGH',
     '{"require_disclosure": true}'::jsonb,
     '특수관계자로 등록된 거래처와 거래가 있으나 주석 목록에 없는 경우', '1.0'),
    ('REL-02', '휴·폐업 거래처와 거래',     'RELATED_PARTY', 'HIGH',
     '{"status": ["DORMANT","CLOSED"]}'::jsonb,
     '휴업·폐업 상태 거래처와 당기 거래가 발생. 페이퍼컴퍼니 후보', '1.0'),
    ('FX-01', '환율 충격 국외채권 노출',    'FX_AR_ECL', 'HIGH',
     '{"shock_pct": 10, "min_delta_krw": 50000000}'::jsonb,
     '환율 10% 변동 시 국외 매출채권 손실액이 기준을 넘는 통화', '1.0'),
    ('FX-02', 'ECL 재산출 갭',              'FX_AR_ECL', 'HIGH',
     '{"gap_pct": 15, "lgd": 0.45}'::jsonb,
     '기대신용손실 재산출액과 계상 충당금의 괴리가 임계값을 넘는 경우', '1.0'),
    ('INV-01', '재고 평가손실률 이상',      'INVENTORY', 'MEDIUM',
     '{"sigma": 3}'::jsonb,
     '평가손실률이 업종 평균 + 3σ를 넘는 경우', '1.0'),
    ('INV-02', '투자부동산 평가 방향 불일치','INVESTMENT_PROPERTY', 'MEDIUM',
     '{"direction_check": true}'::jsonb,
     '공정가치 변동이 플러스인데 평가손실을 계상한 경우(또는 반대)', '1.0'),
    ('PROV-01', '우발부채 미계상',          'PROVISION', 'HIGH',
     '{"min_claim_krw": 100000000}'::jsonb,
     '소송·과징금 주석은 있으나 충당부채 계상액이 0인 경우', '1.0'),
    ('AI-01', 'AI 추출 정확도 미달',        'AI_QA', 'MEDIUM',
     '{"min_match_rate": 95}'::jsonb,
     '프롬프트·에이전트 조합의 필드 일치율이 기준 미만', '1.0'),
    ('AI-02', 'AI 환각',                    'AI_QA', 'CRITICAL',
     '{"hallucination": true}'::jsonb,
     '원문에 없는 값을 생성한 실행 건', '1.0')
ON CONFLICT (rule_id) DO UPDATE
    SET rule_name = EXCLUDED.rule_name,
        risk_domain = EXCLUDED.risk_domain,
        severity = EXCLUDED.severity,
        threshold = EXCLUDED.threshold,
        description = EXCLUDED.description,
        version = EXCLUDED.version,
        updated_at = NOW();

INSERT INTO audit_core.change_log (object_type, object_name, version, changed_by, note)
SELECT 'RULE', 'rule_catalog', '1.0', 'system', '룰 14종 최초 등록'
WHERE NOT EXISTS (
    SELECT 1 FROM audit_core.change_log
    WHERE object_type = 'RULE' AND object_name = 'rule_catalog' AND version = '1.0'
);

-- ---------------------------------------------------------------- 실행 함수
CREATE OR REPLACE FUNCTION audit_core.run_rules(
    p_engagement_id VARCHAR DEFAULT 'ENG-2026-001'
)
RETURNS TABLE (rule_id VARCHAR, new_cnt BIGINT, upd_cnt BIGINT)
LANGUAGE plpgsql
AS $$
#variable_conflict use_column
DECLARE
    v_period CHAR(6);
BEGIN
    SELECT MAX(fiscal_period) INTO v_period
    FROM audit_core.fact_disclosure_line WHERE fs_div = 'C';

    CREATE TEMP TABLE IF NOT EXISTS _rule_run (
        rule_id VARCHAR,
        new_cnt BIGINT,
        upd_cnt BIGINT
    );
    TRUNCATE _rule_run;

    -- JET-01 벤포드 ---------------------------------------------------------
    WITH d AS (
        SELECT fiscal_period,
               substr(krw_amount::bigint::text, 1, 1)::int AS digit,
               COUNT(*)::numeric AS n
        FROM audit_core.fact_journal
        WHERE engagement_id = p_engagement_id
          AND krw_amount >= 1000
        GROUP BY 1, 2
    ), p AS (
        SELECT fiscal_period, digit,
               n / SUM(n) OVER (PARTITION BY fiscal_period) AS obs
        FROM d
    ), m AS (
        SELECT fiscal_period,
               AVG(ABS(obs - ln(1 + 1.0 / digit) / ln(10)))::numeric(10,6) AS mad,
               COUNT(*) AS digits
        FROM p
        GROUP BY 1
    ), up AS (
        INSERT INTO audit_core.fact_risk_finding
            (engagement_id, rule_id, risk_domain, severity, severity_score,
             ref_id, item_count, amount, detail, disposition)
        SELECT p_engagement_id, 'JET-01', r.risk_domain, r.severity, 25,
               'BENFORD:' || m.fiscal_period, 1, NULL,
               jsonb_build_object('mad', m.mad,
                                  'threshold', (r.threshold->>'mad')::numeric,
                                  'digits_used', m.digits),
               'OPEN'
        FROM m
        JOIN audit_core.rule_catalog r ON r.rule_id = 'JET-01'
        WHERE m.mad > (r.threshold->>'mad')::numeric
        ON CONFLICT (engagement_id, rule_id, ref_id) DO UPDATE
            SET severity = EXCLUDED.severity,
                detail = EXCLUDED.detail,
                updated_at = NOW()
        RETURNING (xmax = 0) AS is_new
    )
    INSERT INTO _rule_run
    SELECT 'JET-01', COUNT(*) FILTER (WHERE is_new), COUNT(*) FILTER (WHERE NOT is_new) FROM up;

    -- JET-02 SoD ------------------------------------------------------------
    WITH s AS (
        SELECT fiscal_period,
               COUNT(*) AS n,
               SUM(krw_amount) AS amt,
               MIN(doc_no) AS first_doc,
               MAX(doc_no) AS last_doc
        FROM audit_core.fact_journal
        WHERE engagement_id = p_engagement_id
          AND preparer_id IS NOT NULL
          AND preparer_id = approver_id
        GROUP BY 1
    ), up AS (
        INSERT INTO audit_core.fact_risk_finding
            (engagement_id, rule_id, risk_domain, severity, severity_score,
             ref_id, item_count, amount, detail, disposition)
        SELECT p_engagement_id, 'JET-02', r.risk_domain, r.severity, 75,
               'SOD:' || s.fiscal_period, s.n, s.amt,
               jsonb_build_object('first_doc', s.first_doc,
                                  'last_doc', s.last_doc,
                                  'rule_version', r.version),
               'OPEN'
        FROM s
        JOIN audit_core.rule_catalog r ON r.rule_id = 'JET-02'
        ON CONFLICT (engagement_id, rule_id, ref_id) DO UPDATE
            SET item_count = EXCLUDED.item_count,
                amount = EXCLUDED.amount,
                detail = EXCLUDED.detail,
                updated_at = NOW()
        RETURNING (xmax = 0) AS is_new
    )
    INSERT INTO _rule_run
    SELECT 'JET-02', COUNT(*) FILTER (WHERE is_new), COUNT(*) FILTER (WHERE NOT is_new) FROM up;

    -- JET-03 주말·심야 ------------------------------------------------------
    WITH s AS (
        SELECT fiscal_period,
               COUNT(*) AS n,
               SUM(krw_amount) AS amt,
               COUNT(*) FILTER (WHERE EXTRACT(ISODOW FROM posting_date) IN (6, 7)) AS weekend_n,
               COUNT(*) FILTER (WHERE posting_time >= TIME '22:00'
                                   OR posting_time < TIME '06:00') AS night_n
        FROM audit_core.fact_journal
        WHERE engagement_id = p_engagement_id
          AND (EXTRACT(ISODOW FROM posting_date) IN (6, 7)
               OR posting_time >= TIME '22:00'
               OR posting_time < TIME '06:00')
        GROUP BY 1
    ), up AS (
        INSERT INTO audit_core.fact_risk_finding
            (engagement_id, rule_id, risk_domain, severity, severity_score,
             ref_id, item_count, amount, detail, disposition)
        SELECT p_engagement_id, 'JET-03', r.risk_domain, r.severity, 50,
               'OFFHOUR:' || s.fiscal_period, s.n, s.amt,
               jsonb_build_object('weekend', s.weekend_n, 'night', s.night_n),
               'OPEN'
        FROM s
        JOIN audit_core.rule_catalog r ON r.rule_id = 'JET-03'
        ON CONFLICT (engagement_id, rule_id, ref_id) DO UPDATE
            SET item_count = EXCLUDED.item_count,
                amount = EXCLUDED.amount,
                detail = EXCLUDED.detail,
                updated_at = NOW()
        RETURNING (xmax = 0) AS is_new
    )
    INSERT INTO _rule_run
    SELECT 'JET-03', COUNT(*) FILTER (WHERE is_new), COUNT(*) FILTER (WHERE NOT is_new) FROM up;

    -- JET-04 다변량 ---------------------------------------------------------
    WITH base AS (
        SELECT std_kifrs_code,
               AVG(krw_amount) AS mu,
               STDDEV_SAMP(krw_amount) AS sd
        FROM audit_core.fact_journal
        WHERE engagement_id = p_engagement_id
        GROUP BY 1
    ), z AS (
        SELECT j.je_id::text AS rid,
               j.std_kifrs_code,
               j.krw_amount,
               (j.krw_amount - b.mu) / NULLIF(b.sd, 0) AS zscore
        FROM audit_core.fact_journal j
        JOIN base b USING (std_kifrs_code)
        WHERE j.engagement_id = p_engagement_id
    ), up AS (
        INSERT INTO audit_core.fact_risk_finding
            (engagement_id, rule_id, risk_domain, severity, severity_score,
             ref_id, item_count, amount, detail, disposition)
        SELECT p_engagement_id, 'JET-04', r.risk_domain, r.severity, 55,
               'JE:' || z.rid, 1, z.krw_amount,
               jsonb_build_object('account', z.std_kifrs_code,
                                  'zscore', ROUND(z.zscore::numeric, 2)),
               'OPEN'
        FROM z
        JOIN audit_core.rule_catalog r ON r.rule_id = 'JET-04'
        WHERE ABS(z.zscore) > (r.threshold->>'zscore')::numeric
        ON CONFLICT (engagement_id, rule_id, ref_id) DO UPDATE
            SET amount = EXCLUDED.amount,
                detail = EXCLUDED.detail,
                updated_at = NOW()
        RETURNING (xmax = 0) AS is_new
    )
    INSERT INTO _rule_run
    SELECT 'JET-04', COUNT(*) FILTER (WHERE is_new), COUNT(*) FILTER (WHERE NOT is_new) FROM up;

    -- TIE-01 3-Way ----------------------------------------------------------
    WITH j AS (
        SELECT std_kifrs_code, SUM(krw_amount) AS amt
        FROM audit_core.fact_journal
        WHERE engagement_id = p_engagement_id
        GROUP BY 1
    ), d AS (
        SELECT std_kifrs_code, amount
        FROM audit_core.fact_disclosure_line
        WHERE fs_div = 'C'
          AND fiscal_period = (SELECT MAX(fiscal_period)
                               FROM audit_core.fact_disclosure_line
                               WHERE fs_div = 'C')
    ), diff AS (
        SELECT j.std_kifrs_code, j.amt AS journal_amt, d.amount AS disclosure_amt,
               j.amt - d.amount AS gap
        FROM j JOIN d USING (std_kifrs_code)
    ), up AS (
        INSERT INTO audit_core.fact_risk_finding
            (engagement_id, rule_id, risk_domain, severity, severity_score,
             ref_id, item_count, amount, detail, disposition)
        SELECT p_engagement_id, 'TIE-01', r.risk_domain, r.severity, 90,
               'TIE:' || diff.std_kifrs_code, 1, ABS(diff.gap),
               jsonb_build_object('journal', diff.journal_amt,
                                  'disclosure', diff.disclosure_amt,
                                  'gap', diff.gap,
                                  'gap_pct', ROUND((diff.gap / NULLIF(diff.disclosure_amt, 0) * 100)::numeric, 2)),
               'OPEN'
        FROM diff
        JOIN audit_core.rule_catalog r ON r.rule_id = 'TIE-01'
        WHERE ABS(diff.gap) > (r.threshold->>'tolerance_krw')::numeric
        ON CONFLICT (engagement_id, rule_id, ref_id) DO UPDATE
            SET amount = EXCLUDED.amount,
                detail = EXCLUDED.detail,
                updated_at = NOW()
        RETURNING (xmax = 0) AS is_new
    )
    INSERT INTO _rule_run
    SELECT 'TIE-01', COUNT(*) FILTER (WHERE is_new), COUNT(*) FILTER (WHERE NOT is_new) FROM up;

    -- REL-01 미공시 특수관계자 ----------------------------------------------
    WITH fp AS (
        SELECT footnote_json->'related_parties' AS rp
        FROM audit_core.fact_disclosure_line
        WHERE std_kifrs_code = 'AR' AND fs_div = 'C'
        LIMIT 1
    ), rel AS (
        -- 주석의 상호와 비교하려면 (주)/㈜/공백을 제거한 정규화 명칭이 필요하다
        SELECT regexp_replace(e.name, '\(주\)|㈜|\s', '', 'g') AS name,
               SUM(j.krw_amount) AS amt, COUNT(*) AS n
        FROM audit_core.fact_journal j
        JOIN audit_core.dim_entity e ON e.entity_id = j.counterparty_id
        WHERE j.engagement_id = p_engagement_id
          AND e.is_related_party
        GROUP BY 1
    ), up AS (
        INSERT INTO audit_core.fact_risk_finding
            (engagement_id, rule_id, risk_domain, severity, severity_score,
             ref_id, item_count, amount, detail, disposition)
        SELECT p_engagement_id, 'REL-01', r.risk_domain, r.severity, 75,
               'REL:' || rel.name, rel.n, rel.amt,
               jsonb_build_object('disclosed', fp.rp,
                                  'source', 'dim_entity.is_related_party'),
               'OPEN'
        FROM rel, fp
        JOIN audit_core.rule_catalog r ON r.rule_id = 'REL-01'
        WHERE NOT (fp.rp ? rel.name)
        ON CONFLICT (engagement_id, rule_id, ref_id) DO UPDATE
            SET item_count = EXCLUDED.item_count,
                amount = EXCLUDED.amount,
                detail = EXCLUDED.detail,
                updated_at = NOW()
        RETURNING (xmax = 0) AS is_new
    )
    INSERT INTO _rule_run
    SELECT 'REL-01', COUNT(*) FILTER (WHERE is_new), COUNT(*) FILTER (WHERE NOT is_new) FROM up;

    -- REL-02 휴·폐업 거래처 -------------------------------------------------
    WITH rel AS (
        SELECT e.name, e.biz_status, SUM(j.krw_amount) AS amt, COUNT(*) AS n
        FROM audit_core.fact_journal j
        JOIN audit_core.dim_entity e ON e.entity_id = j.counterparty_id
        WHERE j.engagement_id = p_engagement_id
          AND e.biz_status IN ('DORMANT', 'CLOSED')
        GROUP BY 1, 2
    ), up AS (
        INSERT INTO audit_core.fact_risk_finding
            (engagement_id, rule_id, risk_domain, severity, severity_score,
             ref_id, item_count, amount, detail, disposition)
        SELECT p_engagement_id, 'REL-02', r.risk_domain, r.severity, 78,
               'DORM:' || rel.name, rel.n, rel.amt,
               jsonb_build_object('biz_status', rel.biz_status),
               'OPEN'
        FROM rel
        JOIN audit_core.rule_catalog r ON r.rule_id = 'REL-02'
        ON CONFLICT (engagement_id, rule_id, ref_id) DO UPDATE
            SET item_count = EXCLUDED.item_count,
                amount = EXCLUDED.amount,
                detail = EXCLUDED.detail,
                updated_at = NOW()
        RETURNING (xmax = 0) AS is_new
    )
    INSERT INTO _rule_run
    SELECT 'REL-02', COUNT(*) FILTER (WHERE is_new), COUNT(*) FILTER (WHERE NOT is_new) FROM up;

    -- FX-01 환율 충격 -------------------------------------------------------
    WITH ar AS (
        SELECT COALESCE(SUM(krw_amount), 0) AS amt
        FROM audit_core.fact_journal
        WHERE engagement_id = p_engagement_id
          AND std_kifrs_code = 'AR_FX'
    ), rate AS (
        SELECT obs_value
        FROM audit_core.fact_macro_ecos
        WHERE indicator_code = 'FX_USD'
        ORDER BY obs_date DESC
        LIMIT 1
    ), calc AS (
        SELECT ar.amt, rate.obs_value,
               ar.amt * 0.10 AS delta
        FROM ar, rate
    ), up AS (
        INSERT INTO audit_core.fact_risk_finding
            (engagement_id, rule_id, risk_domain, severity, severity_score,
             ref_id, item_count, amount, detail, disposition)
        SELECT p_engagement_id, 'FX-01', r.risk_domain, r.severity, 76,
               'FX:USD', 1, calc.delta,
               jsonb_build_object('exposure_krw', calc.amt,
                                  'shock_pct', 10,
                                  'rate', calc.obs_value,
                                  'delta_krw', calc.delta),
               'OPEN'
        FROM calc
        JOIN audit_core.rule_catalog r ON r.rule_id = 'FX-01'
        WHERE calc.delta > (r.threshold->>'min_delta_krw')::numeric
        ON CONFLICT (engagement_id, rule_id, ref_id) DO UPDATE
            SET amount = EXCLUDED.amount,
                detail = EXCLUDED.detail,
                updated_at = NOW()
        RETURNING (xmax = 0) AS is_new
    )
    INSERT INTO _rule_run
    SELECT 'FX-01', COUNT(*) FILTER (WHERE is_new), COUNT(*) FILTER (WHERE NOT is_new) FROM up;

    -- FX-02 ECL 갭 ----------------------------------------------------------
    WITH ead AS (
        SELECT COALESCE(SUM(krw_amount), 0) AS amt
        FROM audit_core.fact_journal
        WHERE engagement_id = p_engagement_id
          AND std_kifrs_code IN ('AR', 'AR_FX')
    ), pd AS (
        SELECT avg_ratio
        FROM audit_core.dim_industry_stats
        WHERE metric = 'ECL_COVERAGE_RATIO'
        ORDER BY as_of DESC
        LIMIT 1
    ), bk AS (
        SELECT (footnote_json->>'ecl_allowance')::numeric AS booked
        FROM audit_core.fact_disclosure_line
        WHERE std_kifrs_code = 'PROVISION' AND fs_div = 'C'
        LIMIT 1
    ), calc AS (
        SELECT ead.amt AS ead, pd.avg_ratio, bk.booked,
               ead.amt * pd.avg_ratio AS recomputed
        FROM ead, pd, bk
    ), up AS (
        INSERT INTO audit_core.fact_risk_finding
            (engagement_id, rule_id, risk_domain, severity, severity_score,
             ref_id, item_count, amount, detail, disposition)
        SELECT p_engagement_id, 'FX-02', r.risk_domain, r.severity, 80,
               'ECL:' || v_period, 1,
               ABS(calc.recomputed - calc.booked),
               jsonb_build_object('ead', calc.ead,
                                  'coverage_ratio', calc.avg_ratio,
                                  'recomputed', ROUND(calc.recomputed, 0),
                                  'booked', calc.booked,
                                  'gap_pct', ROUND((ABS(calc.recomputed - calc.booked)
                                                    / NULLIF(calc.recomputed, 0) * 100)::numeric, 2),
                                  'note', 'PD×LGD를 업종 커버리지 비율로 근사'),
               'OPEN'
        FROM calc
        JOIN audit_core.rule_catalog r ON r.rule_id = 'FX-02'
        WHERE calc.recomputed > 0
          AND ABS(calc.recomputed - calc.booked) / calc.recomputed * 100
              > (r.threshold->>'gap_pct')::numeric
        ON CONFLICT (engagement_id, rule_id, ref_id) DO UPDATE
            SET amount = EXCLUDED.amount,
                detail = EXCLUDED.detail,
                updated_at = NOW()
        RETURNING (xmax = 0) AS is_new
    )
    INSERT INTO _rule_run
    SELECT 'FX-02', COUNT(*) FILTER (WHERE is_new), COUNT(*) FILTER (WHERE NOT is_new) FROM up;

    -- INV-01 재고 평가손실률 ------------------------------------------------
    WITH inv AS (
        SELECT (footnote_json->>'inventory_cost')::numeric AS cost,
               (footnote_json->>'valuation_loss')::numeric AS loss
        FROM audit_core.fact_disclosure_line
        WHERE std_kifrs_code = 'INVENTORY' AND fs_div = 'C'
        LIMIT 1
    ), st AS (
        SELECT avg_ratio, sd_ratio
        FROM audit_core.dim_industry_stats
        WHERE metric = 'INVENTORY_LOSS_RATIO'
        ORDER BY as_of DESC
        LIMIT 1
    ), calc AS (
        SELECT inv.cost, inv.loss, st.avg_ratio, st.sd_ratio,
               inv.loss / NULLIF(inv.cost, 0) AS ratio
        FROM inv, st
    ), up AS (
        INSERT INTO audit_core.fact_risk_finding
            (engagement_id, rule_id, risk_domain, severity, severity_score,
             ref_id, item_count, amount, detail, disposition)
        SELECT p_engagement_id, 'INV-01', r.risk_domain, r.severity, 52,
               'INV:29199', 1, calc.loss,
               jsonb_build_object('loss_ratio', ROUND(calc.ratio::numeric, 4),
                                  'industry_avg', calc.avg_ratio,
                                  'industry_sd', calc.sd_ratio,
                                  'sigma', ROUND(((calc.ratio - calc.avg_ratio)
                                                  / NULLIF(calc.sd_ratio, 0))::numeric, 1)),
               'OPEN'
        FROM calc
        JOIN audit_core.rule_catalog r ON r.rule_id = 'INV-01'
        WHERE calc.ratio > calc.avg_ratio
              + (r.threshold->>'sigma')::numeric * calc.sd_ratio
        ON CONFLICT (engagement_id, rule_id, ref_id) DO UPDATE
            SET amount = EXCLUDED.amount,
                detail = EXCLUDED.detail,
                updated_at = NOW()
        RETURNING (xmax = 0) AS is_new
    )
    INSERT INTO _rule_run
    SELECT 'INV-01', COUNT(*) FILTER (WHERE is_new), COUNT(*) FILTER (WHERE NOT is_new) FROM up;

    -- INV-02 투자부동산 방향 불일치 ----------------------------------------
    WITH ip AS (
        SELECT (footnote_json->>'fair_value_change')::numeric AS fv_change,
               (footnote_json->>'valuation_loss_booked')::numeric AS loss_booked
        FROM audit_core.fact_disclosure_line
        WHERE std_kifrs_code = 'INVEST_PROPERTY' AND fs_div = 'C'
        LIMIT 1
    ), up AS (
        INSERT INTO audit_core.fact_risk_finding
            (engagement_id, rule_id, risk_domain, severity, severity_score,
             ref_id, item_count, amount, detail, disposition)
        SELECT p_engagement_id, 'INV-02', r.risk_domain, r.severity, 50,
               'INVP:' || v_period, 1, ip.loss_booked,
               jsonb_build_object('fair_value_change', ip.fv_change,
                                  'valuation_loss_booked', ip.loss_booked),
               'OPEN'
        FROM ip
        JOIN audit_core.rule_catalog r ON r.rule_id = 'INV-02'
        WHERE ip.fv_change > 0 AND ip.loss_booked > 0
        ON CONFLICT (engagement_id, rule_id, ref_id) DO UPDATE
            SET amount = EXCLUDED.amount,
                detail = EXCLUDED.detail,
                updated_at = NOW()
        RETURNING (xmax = 0) AS is_new
    )
    INSERT INTO _rule_run
    SELECT 'INV-02', COUNT(*) FILTER (WHERE is_new), COUNT(*) FILTER (WHERE NOT is_new) FROM up;

    -- PROV-01 우발부채 미계상 ----------------------------------------------
    WITH pv AS (
        SELECT (footnote_json->>'litigation_claim')::numeric AS claim,
               COALESCE((footnote_json->>'litigation_provision')::numeric, 0) AS provision,
               footnote_json->>'contingent_note' AS note
        FROM audit_core.fact_disclosure_line
        WHERE std_kifrs_code = 'PROVISION' AND fs_div = 'C'
        LIMIT 1
    ), up AS (
        INSERT INTO audit_core.fact_risk_finding
            (engagement_id, rule_id, risk_domain, severity, severity_score,
             ref_id, item_count, amount, detail, disposition)
        SELECT p_engagement_id, 'PROV-01', r.risk_domain, r.severity, 77,
               'PROV:' || v_period, 1, pv.claim,
               jsonb_build_object('claim', pv.claim,
                                  'provision', pv.provision,
                                  'note', pv.note),
               'OPEN'
        FROM pv
        JOIN audit_core.rule_catalog r ON r.rule_id = 'PROV-01'
        WHERE pv.claim > (r.threshold->>'min_claim_krw')::numeric
          AND pv.provision = 0
        ON CONFLICT (engagement_id, rule_id, ref_id) DO UPDATE
            SET amount = EXCLUDED.amount,
                detail = EXCLUDED.detail,
                updated_at = NOW()
        RETURNING (xmax = 0) AS is_new
    )
    INSERT INTO _rule_run
    SELECT 'PROV-01', COUNT(*) FILTER (WHERE is_new), COUNT(*) FILTER (WHERE NOT is_new) FROM up;

    -- AI-01 추출 정확도 -----------------------------------------------------
    WITH q AS (
        SELECT agent_name, prompt_version,
               COUNT(*) AS n,
               MIN(exact_match_rate) AS min_rate,
               AVG(exact_match_rate) AS avg_rate
        FROM audit_core.fact_ai_qa_run
        WHERE engagement_id = p_engagement_id
          AND exact_match_rate < 95
        GROUP BY 1, 2
    ), up AS (
        INSERT INTO audit_core.fact_risk_finding
            (engagement_id, rule_id, risk_domain, severity, severity_score,
             ref_id, item_count, amount, detail, disposition)
        SELECT p_engagement_id, 'AI-01', r.risk_domain, r.severity, 48,
               'AI01:' || q.agent_name || ':' || q.prompt_version, q.n, NULL,
               jsonb_build_object('min_match_rate', q.min_rate,
                                  'avg_match_rate', ROUND(q.avg_rate::numeric, 2)),
               'OPEN'
        FROM q
        JOIN audit_core.rule_catalog r ON r.rule_id = 'AI-01'
        ON CONFLICT (engagement_id, rule_id, ref_id) DO UPDATE
            SET item_count = EXCLUDED.item_count,
                detail = EXCLUDED.detail,
                updated_at = NOW()
        RETURNING (xmax = 0) AS is_new
    )
    INSERT INTO _rule_run
    SELECT 'AI-01', COUNT(*) FILTER (WHERE is_new), COUNT(*) FILTER (WHERE NOT is_new) FROM up;

    -- AI-02 환각 ------------------------------------------------------------
    WITH q AS (
        SELECT qa_id::text AS qid, agent_name, prompt_version, source_doc_ref,
               exact_match_rate
        FROM audit_core.fact_ai_qa_run
        WHERE engagement_id = p_engagement_id
          AND hallucination_yn
    ), up AS (
        INSERT INTO audit_core.fact_risk_finding
            (engagement_id, rule_id, risk_domain, severity, severity_score,
             ref_id, item_count, amount, detail, disposition)
        SELECT p_engagement_id, 'AI-02', r.risk_domain, r.severity, 92,
               'AI02:' || q.qid, 1, NULL,
               jsonb_build_object('agent', q.agent_name,
                                  'prompt_version', q.prompt_version,
                                  'doc', q.source_doc_ref,
                                  'match_rate', q.exact_match_rate),
               'OPEN'
        FROM q
        JOIN audit_core.rule_catalog r ON r.rule_id = 'AI-02'
        ON CONFLICT (engagement_id, rule_id, ref_id) DO UPDATE
            SET detail = EXCLUDED.detail,
                updated_at = NOW()
        RETURNING (xmax = 0) AS is_new
    )
    INSERT INTO _rule_run
    SELECT 'AI-02', COUNT(*) FILTER (WHERE is_new), COUNT(*) FILTER (WHERE NOT is_new) FROM up;

    RETURN QUERY
    SELECT t.rule_id, t.new_cnt, t.upd_cnt
    FROM _rule_run t
    ORDER BY t.rule_id;
END;
$$;

COMMENT ON FUNCTION audit_core.run_rules(VARCHAR)
    IS '룰 14종 실행. (engagement_id, rule_id, ref_id) 유니크로 멱등';

-- ---------------------------------------------------------------- 조회 뷰
CREATE OR REPLACE VIEW audit_core.vw_finding_worklist AS
SELECT f.engagement_id,
       f.rule_id,
       r.rule_name,
       f.risk_domain,
       f.severity,
       CASE f.severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2
                       WHEN 'MEDIUM' THEN 3 ELSE 4 END AS severity_rank,
       f.ref_id,
       f.item_count,
       f.amount,
       f.detail,
       f.disposition,
       f.reviewer_id,
       f.created_at
FROM audit_core.fact_risk_finding f
JOIN audit_core.rule_catalog r USING (rule_id);

CREATE OR REPLACE VIEW audit_core.vw_rule_run_summary AS
SELECT f.engagement_id,
       f.rule_id,
       r.rule_name,
       r.risk_domain,
       f.severity,
       COUNT(*) AS finding_count,
       SUM(f.item_count) AS item_count,
       SUM(COALESCE(f.amount, 0)) AS amount_sum,
       COUNT(*) FILTER (WHERE f.disposition = 'OPEN') AS open_count
FROM audit_core.fact_risk_finding f
JOIN audit_core.rule_catalog r USING (rule_id)
GROUP BY 1, 2, 3, 4, 5;
