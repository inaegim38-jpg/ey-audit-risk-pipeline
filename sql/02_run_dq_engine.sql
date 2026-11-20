-- ============================================================================
-- 02_run_dq_engine.sql
-- 대량 데이터 적재 후 정합성/이상징후 검증 재실행 및 Power BI 뷰 갱신 (멱등)
-- ============================================================================

CREATE OR REPLACE VIEW audit_risk.vw_pbi_tx_summary AS
WITH tx_flags AS (
    SELECT
        t.tx_id,
        t.posting_date,
        COALESCE(t.department_code, 'UNKNOWN') AS department_code,
        t.merchant_category,
        t.tx_amount,
        MAX(CASE WHEN f.finding_id IS NOT NULL THEN 1 ELSE 0 END) AS is_flagged,
        MAX(CASE WHEN f.finding_type IN ('DQ_SCHEMA','DQ_INTEGRITY','EVIDENCE_MATCH') THEN 1 ELSE 0 END) AS is_dq_error,
        MAX(CASE WHEN f.finding_type = 'ANOMALY_RISK' THEN 1 ELSE 0 END) AS is_anomaly
    FROM audit_risk.raw_transactions t
    LEFT JOIN audit_risk.dq_anomaly_findings f ON t.tx_id = f.tx_id
    GROUP BY t.tx_id, t.posting_date, COALESCE(t.department_code, 'UNKNOWN'), t.merchant_category, t.tx_amount
)
SELECT
    posting_date,
    department_code,
    merchant_category,
    COUNT(*) AS total_tx_count,
    COALESCE(SUM(tx_amount), 0) AS total_tx_amount,
    SUM(is_flagged) AS flagged_tx_count,
    SUM(is_dq_error) AS dq_error_tx_count,
    SUM(is_anomaly) AS anomaly_tx_count,
    COALESCE(SUM(CASE WHEN is_anomaly = 1 THEN tx_amount ELSE 0 END), 0) AS anomaly_exposure_amount
FROM tx_flags
GROUP BY posting_date, department_code, merchant_category;

-- 우선 조치 큐: 미해결 건을 tx_id 단위로 집계, 위험점수 순
CREATE OR REPLACE VIEW audit_risk.vw_pbi_priority_queue AS
SELECT
    f.tx_id,
    MAX(f.risk_score) AS max_risk_score,
    COUNT(*) AS finding_count,
    STRING_AGG(DISTINCT f.rule_id, ',' ORDER BY f.rule_id) AS rule_ids,
    MAX(CASE f.severity WHEN 'CRITICAL' THEN 3 WHEN 'HIGH' THEN 2 WHEN 'MEDIUM' THEN 1 ELSE 0 END) AS severity_rank,
    COALESCE(MAX(t.tx_amount), 0) AS tx_amount,
    MAX(t.department_code) AS department_code,
    MAX(t.merchant_category) AS merchant_category,
    MAX(t.posting_date) AS posting_date
FROM audit_risk.dq_anomaly_findings f
LEFT JOIN audit_risk.raw_transactions t ON t.tx_id = f.tx_id
WHERE f.issue_status IN ('OPEN','IN_REVIEW')
GROUP BY f.tx_id
ORDER BY severity_rank DESC, max_risk_score DESC, tx_amount DESC;

WITH run_meta AS (
    SELECT 'RUN_' || TO_CHAR(NOW(), 'YYYYMMDD_HH24MISS') AS run_id
),
detected_issues AS (
    SELECT 'DQ_01:' || t.tx_id AS finding_key, t.batch_id, rm.run_id, 'DQ_01' AS rule_id,
           t.tx_id, t.evidence_doc_no, 'DQ_INTEGRITY' AS finding_type, 'HIGH' AS severity,
           75.00::NUMERIC(5,2) AS risk_score, COALESCE(t.tx_amount, 0) AS detected_amount, 0::NUMERIC AS expected_amount, 0::NUMERIC AS discrepancy_amount,
           '필수값 누락: amount=' || COALESCE(t.tx_amount::TEXT, 'NULL') || ', dept=' || COALESCE(t.department_code, 'NULL') AS detail_message
    FROM audit_risk.raw_transactions t CROSS JOIN run_meta rm
    WHERE t.tx_amount IS NULL OR t.tx_timestamp IS NULL OR t.account_id IS NULL OR t.department_code IS NULL

    UNION ALL
    SELECT 'DQ_02:' || t.tx_id, t.batch_id, rm.run_id, 'DQ_02',
           t.tx_id, t.evidence_doc_no, 'DQ_INTEGRITY', 'HIGH',
           80.00, t.tx_amount, 0, ABS(t.tx_amount),
           '비정상 0원 이하 금액(' || t.tx_amount || '원, 유형=' || t.tx_type || ')'
    FROM audit_risk.raw_transactions t CROSS JOIN run_meta rm
    WHERE t.tx_type NOT IN ('REFUND', 'CANCEL') AND t.tx_amount <= 0

    UNION ALL
    SELECT 'DQ_03:' || d.tx_id, d.batch_id, rm.run_id, 'DQ_03',
           d.tx_id, d.evidence_doc_no, 'DQ_INTEGRITY', 'CRITICAL',
           95.00, d.tx_amount, 0, d.tx_amount,
           '중복 거래/증빙 파일 해시 탐지 (건수=' || d.dup_cnt || ', hash=' || COALESCE(d.file_hash, 'N/A') || ')'
    FROM (
        SELECT t.*, e.file_hash,
               GREATEST(
                   COUNT(*) OVER (PARTITION BY t.account_id, t.merchant_id, t.tx_amount, t.tx_timestamp),
                   CASE WHEN e.file_hash IS NOT NULL THEN COUNT(*) OVER (PARTITION BY e.file_hash) ELSE 1 END
               ) AS dup_cnt
        FROM audit_risk.raw_transactions t
        LEFT JOIN audit_risk.raw_evidences e ON t.tx_id = e.tx_id
    ) d CROSS JOIN run_meta rm
    WHERE d.dup_cnt > 1

    UNION ALL
    SELECT 'EV_01:' || t.tx_id, t.batch_id, rm.run_id, 'EV_01',
           t.tx_id, t.evidence_doc_no, 'EVIDENCE_MATCH', 'CRITICAL',
           92.00, t.tx_amount, 0, t.tx_amount,
           '고액 거래(' || t.tx_amount || '원) 증빙 문서 미매칭'
    FROM audit_risk.raw_transactions t
    LEFT JOIN audit_risk.raw_evidences e ON t.tx_id = e.tx_id OR (t.evidence_doc_no IS NOT NULL AND t.evidence_doc_no = e.evidence_doc_no)
    CROSS JOIN run_meta rm
    WHERE t.tx_amount >= 500000 AND e.evidence_id IS NULL

    UNION ALL
    SELECT 'EV_02:' || t.tx_id, t.batch_id, rm.run_id, 'EV_02',
           t.tx_id, e.evidence_doc_no, 'EVIDENCE_MATCH', 'CRITICAL',
           94.00, t.tx_amount, e.evidence_amount, ABS(t.tx_amount - e.evidence_amount),
           '금액 불일치: 원장(' || t.tx_amount || ') vs 증빙(' || e.evidence_amount || ')'
    FROM audit_risk.raw_transactions t
    JOIN audit_risk.raw_evidences e ON t.tx_id = e.tx_id
    CROSS JOIN run_meta rm
    WHERE ABS(t.tx_amount - e.evidence_amount) > 100

    UNION ALL
    SELECT 'EV_03:' || t.tx_id, t.batch_id, rm.run_id, 'EV_03',
           t.tx_id, e.evidence_doc_no, 'EVIDENCE_MATCH', 'MEDIUM',
           60.00, t.tx_amount, e.evidence_amount, 0,
           '일자 괴리: 전기일(' || t.posting_date || ') vs 증빙발행일(' || e.issue_date || ')'
    FROM audit_risk.raw_transactions t
    JOIN audit_risk.raw_evidences e ON t.tx_id = e.tx_id
    CROSS JOIN run_meta rm
    WHERE e.issue_date > t.posting_date + INTERVAL '7 days' OR (t.posting_date - e.issue_date) > 30

    UNION ALL
    SELECT 'AN_01:' || t.tx_id, t.batch_id, rm.run_id, 'AN_01',
           t.tx_id, t.evidence_doc_no, 'ANOMALY_RISK', 'HIGH',
           LEAST(99.00, 70.00 + ((t.tx_amount - b.avg_amount) / NULLIF(b.stddev_amount, 0)) * 2.5)::NUMERIC(5,2),
           t.tx_amount, b.avg_amount, (t.tx_amount - b.avg_amount),
           'Z-Score 이상치: ' || ROUND((t.tx_amount - b.avg_amount) / NULLIF(b.stddev_amount, 0), 2) || 'σ'
    FROM audit_risk.raw_transactions t
    JOIN audit_risk.baseline_stats b ON t.merchant_category = b.merchant_category
    CROSS JOIN run_meta rm
    WHERE t.tx_amount > 0 AND (t.tx_amount - b.avg_amount) / NULLIF(b.stddev_amount, 0) >= 3.0

    UNION ALL
    SELECT 'AN_02:' || t.tx_id, t.batch_id, rm.run_id, 'AN_02',
           t.tx_id, t.evidence_doc_no, 'ANOMALY_RISK', 'CRITICAL',
           98.00, t.tx_amount, 1000000, sc.daily_sum - 1000000,
           '한도 회피 분할결제 의심: 동일 일자/가맹점 ' || sc.tx_cnt || '건 합계 ' || sc.daily_sum || '원'
    FROM (
        SELECT account_id, merchant_id, posting_date, COUNT(*) AS tx_cnt, SUM(tx_amount) AS daily_sum
        FROM audit_risk.raw_transactions
        WHERE tx_amount BETWEEN 700000 AND 999999
        GROUP BY account_id, merchant_id, posting_date
        HAVING COUNT(*) >= 2 AND SUM(tx_amount) >= 1000000
    ) sc
    JOIN audit_risk.raw_transactions t
      ON t.account_id = sc.account_id AND t.merchant_id = sc.merchant_id AND t.posting_date = sc.posting_date
    CROSS JOIN run_meta rm
    WHERE t.tx_amount BETWEEN 700000 AND 999999

    UNION ALL
    SELECT 'AN_03:' || t.tx_id, t.batch_id, rm.run_id, 'AN_03',
           t.tx_id, t.evidence_doc_no, 'ANOMALY_RISK', 'MEDIUM',
           65.00, t.tx_amount, 500000, t.tx_amount,
           '업무 외 시간대 고액 결제(' || TO_CHAR(t.tx_timestamp AT TIME ZONE 'Asia/Seoul', 'YYYY-MM-DD HH24:MI Dy') || ')'
    FROM audit_risk.raw_transactions t CROSS JOIN run_meta rm
    WHERE t.tx_amount >= 500000
      AND (
          EXTRACT(HOUR FROM t.tx_timestamp AT TIME ZONE 'Asia/Seoul') >= 23
          OR EXTRACT(HOUR FROM t.tx_timestamp AT TIME ZONE 'Asia/Seoul') < 5
          OR EXTRACT(ISODOW FROM t.tx_timestamp AT TIME ZONE 'Asia/Seoul') IN (6, 7)
      )
)
INSERT INTO audit_risk.dq_anomaly_findings
(finding_key, batch_id, run_id, rule_id, tx_id, evidence_doc_no, finding_type, severity, risk_score, detected_amount, expected_amount, discrepancy_amount, detail_message)
SELECT finding_key, batch_id, run_id, rule_id, tx_id, evidence_doc_no, finding_type, severity, risk_score, detected_amount, expected_amount, discrepancy_amount, detail_message
FROM detected_issues
ON CONFLICT (finding_key) DO UPDATE SET
    run_id = EXCLUDED.run_id,
    risk_score = EXCLUDED.risk_score,
    detected_amount = EXCLUDED.detected_amount,
    discrepancy_amount = EXCLUDED.discrepancy_amount,
    detail_message = EXCLUDED.detail_message,
    updated_at = NOW();

-- 실행 결과 요약 출력
SELECT f.severity, f.rule_id, r.rule_name, COUNT(*) AS issue_count, SUM(f.detected_amount) AS total_detected_amount
FROM audit_risk.dq_anomaly_findings f
JOIN audit_risk.validation_rules r ON f.rule_id = r.rule_id
GROUP BY f.severity, f.rule_id, r.rule_name
ORDER BY CASE f.severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 WHEN 'MEDIUM' THEN 3 ELSE 4 END, issue_count DESC;
