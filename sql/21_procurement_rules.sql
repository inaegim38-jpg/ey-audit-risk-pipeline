-- ============================================================================
-- 21. Procurement candidate rules
-- ============================================================================
-- These rules create candidates only. Reviewer disposition remains authoritative.

INSERT INTO audit_core.rule_catalog
    (rule_id, rule_name, risk_domain, severity, threshold, description, version)
VALUES
    ('PPS-01', '조달 고액 공고', 'PROCUREMENT', 'MEDIUM',
     '{"min_estimated_amount": 100000000}'::jsonb,
     '추정금액이 임계값 이상인 입찰공고. 금액 자체는 위반 판정이 아니며 추가 증거 검토 후보다.', '1.0'),
    ('PPS-02', '조달 단기 입찰기간', 'PROCUREMENT', 'HIGH',
     '{"max_bid_window_days": 3}'::jsonb,
     '입찰 시작부터 마감까지 기간이 짧은 공고. 긴급성·경쟁성·공고변경 이력을 사람이 확인한다.', '1.0'),
    ('PPS-03', '조달 일정 필드 누락', 'DATA_QUALITY', 'MEDIUM',
     '{"require_bid_begin_and_close": true}'::jsonb,
     '입찰 시작일 또는 마감일이 누락된 공고. 원천 응답과 기관 확인이 필요하다.', '1.0')
ON CONFLICT (rule_id) DO UPDATE
    SET rule_name = EXCLUDED.rule_name,
        risk_domain = EXCLUDED.risk_domain,
        severity = EXCLUDED.severity,
        threshold = EXCLUDED.threshold,
        description = EXCLUDED.description,
        version = EXCLUDED.version,
        updated_at = NOW();

INSERT INTO audit_core.change_log
    (object_type, object_name, version, changed_by, note)
SELECT 'RULE', 'procurement_rules', '1.0', 'system',
       'PPS-01~PPS-03 procurement candidate rules; HITL disposition required'
WHERE NOT EXISTS (
    SELECT 1 FROM audit_core.change_log
    WHERE object_type = 'RULE' AND object_name = 'procurement_rules' AND version = '1.0'
);

CREATE OR REPLACE FUNCTION audit_core.run_procurement_rules(
    p_engagement_id VARCHAR DEFAULT 'ENG-2026-001'
)
RETURNS TABLE (rule_id VARCHAR, new_cnt BIGINT, upd_cnt BIGINT)
LANGUAGE plpgsql
AS $$
#variable_conflict use_column
DECLARE
    v_min_amount NUMERIC;
    v_max_days INT;
BEGIN
    SELECT (r.threshold->>'min_estimated_amount')::numeric INTO v_min_amount
    FROM audit_core.rule_catalog r WHERE r.rule_id = 'PPS-01';
    SELECT (r.threshold->>'max_bid_window_days')::int INTO v_max_days
    FROM audit_core.rule_catalog r WHERE r.rule_id = 'PPS-02';

    CREATE TEMP TABLE IF NOT EXISTS _procurement_rule_run (
        rule_id VARCHAR, new_cnt BIGINT, upd_cnt BIGINT
    );
    TRUNCATE _procurement_rule_run;

    WITH up AS (
        INSERT INTO audit_core.fact_risk_finding
            (engagement_id, rule_id, risk_domain, severity, severity_score,
             ref_id, item_count, amount, detail, disposition)
        SELECT p_engagement_id, r.rule_id, r.risk_domain, r.severity, 50,
               'PPS:' || f.bid_notice_no || ':' || COALESCE(f.bid_notice_ord, '0'),
               1, f.estimated_amount,
               jsonb_build_object(
                   'source_payload_id', f.source_payload_id,
                   'bid_notice_no', f.bid_notice_no,
                   'bid_notice_ord', f.bid_notice_ord,
                   'notice_name', f.notice_name,
                   'notice_date', f.notice_date,
                   'estimated_amount', f.estimated_amount,
                   'source_locator', f.source_locator,
                   'candidate_only', true,
                   'review_required', true
               ), 'OPEN'
        FROM audit_core.fact_procurement_event f
        JOIN audit_core.rule_catalog r ON r.rule_id = 'PPS-01'
        WHERE f.estimated_amount >= v_min_amount
        ON CONFLICT (engagement_id, rule_id, ref_id) DO UPDATE
            SET detail = EXCLUDED.detail, amount = EXCLUDED.amount,
                updated_at = NOW()
        RETURNING (xmax = 0) AS is_new
    )
    INSERT INTO _procurement_rule_run
    SELECT 'PPS-01', COUNT(*) FILTER (WHERE is_new), COUNT(*) FILTER (WHERE NOT is_new) FROM up;

    WITH up AS (
        INSERT INTO audit_core.fact_risk_finding
            (engagement_id, rule_id, risk_domain, severity, severity_score,
             ref_id, item_count, amount, detail, disposition)
        SELECT p_engagement_id, r.rule_id, r.risk_domain, r.severity, 65,
               'PPS:' || f.bid_notice_no || ':' || COALESCE(f.bid_notice_ord, '0'),
               1, f.estimated_amount,
               jsonb_build_object(
                   'source_payload_id', f.source_payload_id,
                   'bid_notice_no', f.bid_notice_no,
                   'bid_window_hours', EXTRACT(EPOCH FROM (f.bid_close_at - f.bid_begin_at)) / 3600,
                   'notice_name', f.notice_name,
                   'candidate_only', true,
                   'review_required', true
               ), 'OPEN'
        FROM audit_core.fact_procurement_event f
        JOIN audit_core.rule_catalog r ON r.rule_id = 'PPS-02'
        WHERE f.bid_begin_at IS NOT NULL
          AND f.bid_close_at IS NOT NULL
          AND f.bid_close_at >= f.bid_begin_at
          AND f.bid_close_at - f.bid_begin_at < make_interval(days => v_max_days)
        ON CONFLICT (engagement_id, rule_id, ref_id) DO UPDATE
            SET detail = EXCLUDED.detail, amount = EXCLUDED.amount,
                updated_at = NOW()
        RETURNING (xmax = 0) AS is_new
    )
    INSERT INTO _procurement_rule_run
    SELECT 'PPS-02', COUNT(*) FILTER (WHERE is_new), COUNT(*) FILTER (WHERE NOT is_new) FROM up;

    WITH up AS (
        INSERT INTO audit_core.fact_risk_finding
            (engagement_id, rule_id, risk_domain, severity, severity_score,
             ref_id, item_count, amount, detail, disposition)
        SELECT p_engagement_id, r.rule_id, r.risk_domain, r.severity, 35,
               'PPS:' || f.bid_notice_no || ':' || COALESCE(f.bid_notice_ord, '0'),
               1, f.estimated_amount,
               jsonb_build_object(
                   'source_payload_id', f.source_payload_id,
                   'bid_notice_no', f.bid_notice_no,
                   'missing_bid_begin', f.bid_begin_at IS NULL,
                   'missing_bid_close', f.bid_close_at IS NULL,
                   'notice_name', f.notice_name,
                   'candidate_only', true,
                   'review_required', true
               ), 'OPEN'
        FROM audit_core.fact_procurement_event f
        JOIN audit_core.rule_catalog r ON r.rule_id = 'PPS-03'
        WHERE f.bid_begin_at IS NULL OR f.bid_close_at IS NULL
        ON CONFLICT (engagement_id, rule_id, ref_id) DO UPDATE
            SET detail = EXCLUDED.detail, amount = EXCLUDED.amount,
                updated_at = NOW()
        RETURNING (xmax = 0) AS is_new
    )
    INSERT INTO _procurement_rule_run
    SELECT 'PPS-03', COUNT(*) FILTER (WHERE is_new), COUNT(*) FILTER (WHERE NOT is_new) FROM up;

    RETURN QUERY SELECT t.rule_id, t.new_cnt, t.upd_cnt
    FROM _procurement_rule_run t ORDER BY t.rule_id;
END;
$$;

COMMENT ON FUNCTION audit_core.run_procurement_rules(VARCHAR)
    IS 'Procurement candidate rules PPS-01~PPS-03. Human disposition remains required.';
