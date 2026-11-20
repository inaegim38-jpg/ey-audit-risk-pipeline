-- ============================================================================
-- 03_issue_review.sql : 이슈 검토 상태 추적 (OPEN → IN_REVIEW → RESOLVED / FALSE_POSITIVE)
-- 01, 02 실행 후에 실행. 여러 번 실행해도 안전(멱등). 01은 스키마를 DROP하므로 01 이후 다시 실행할 것.
-- ============================================================================

ALTER TABLE audit_risk.dq_anomaly_findings
    ADD COLUMN IF NOT EXISTS reviewer     VARCHAR(50),
    ADD COLUMN IF NOT EXISTS review_memo  TEXT,
    ADD COLUMN IF NOT EXISTS reviewed_at  TIMESTAMPTZ;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_issue_status') THEN
        ALTER TABLE audit_risk.dq_anomaly_findings
            ADD CONSTRAINT chk_issue_status
            CHECK (issue_status IN ('OPEN', 'IN_REVIEW', 'RESOLVED', 'FALSE_POSITIVE'));
    END IF;
END $$;

-- 담당자가 이슈 상태를 바꾸는 함수 (없는 키나 허용되지 않는 상태값은 오류)
CREATE OR REPLACE FUNCTION audit_risk.set_issue_status(
    p_finding_key TEXT, p_status TEXT, p_reviewer TEXT, p_memo TEXT
) RETURNS VOID AS $$
BEGIN
    UPDATE audit_risk.dq_anomaly_findings
       SET issue_status = p_status,
           reviewer     = p_reviewer,
           review_memo  = p_memo,
           reviewed_at  = NOW(),
           updated_at   = NOW()
     WHERE finding_key = p_finding_key;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'finding_key not found: %', p_finding_key;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- 검토 작업 목록: 미종결 CRITICAL/HIGH 건을 위험점수 순으로
CREATE OR REPLACE VIEW audit_risk.vw_review_worklist AS
SELECT
    f.finding_key,
    f.rule_id,
    f.severity,
    f.risk_score,
    f.tx_id,
    t.tx_amount,
    t.department_code,
    t.merchant_category,
    t.posting_date,
    f.detail_message,
    f.issue_status,
    f.reviewer,
    f.review_memo
FROM audit_risk.dq_anomaly_findings f
LEFT JOIN audit_risk.raw_transactions t ON t.tx_id = f.tx_id
WHERE f.issue_status IN ('OPEN', 'IN_REVIEW')
  AND f.severity IN ('CRITICAL', 'HIGH')
ORDER BY CASE f.severity WHEN 'CRITICAL' THEN 1 ELSE 2 END, f.risk_score DESC, t.tx_amount DESC;

-- 상태별 현황 요약
CREATE OR REPLACE VIEW audit_risk.vw_issue_status_summary AS
SELECT issue_status, severity, COUNT(*) AS issue_count
FROM audit_risk.dq_anomaly_findings
GROUP BY issue_status, severity;
