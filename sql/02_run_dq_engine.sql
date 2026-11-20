-- ============================================================================
-- sql/02_run_dq_engine.sql (2-Track High-Speed & Refined Edition)
-- ============================================================================
ANALYZE audit_risk.raw_transactions;
ANALYZE audit_risk.raw_evidences;

WITH run_meta AS (
    SELECT 'RUN_' || TO_CHAR(NOW(), 'YYYYMMDD_HH24MISS') AS run_id
),
detected_issues AS (
    SELECT 'DQ_01:' || t.tx_id AS finding_key, t.batch_id, rm.run_id, r.rule_id,
           t.tx_id, t.evidence_doc_no, 'DQ_INTEGRITY' AS finding_type, r.severity,
           75.00::NUMERIC(5,2) AS risk_score, COALESCE(t.tx_amount, 0) AS detected_amount, 0::NUMERIC AS expected_amount, 0::NUMERIC AS discrepancy_amount,
           '필수값 누락: amount=' || COALESCE(t.tx_amount::TEXT, 'NULL') || ', dept=' || COALESCE(t.department_code, 'NULL') AS detail_message
    FROM audit_risk.raw_transactions t
    JOIN audit_risk.validation_rules r ON r.rule_id = 'DQ_01' AND r.is_active = TRUE
    CROSS JOIN run_meta rm
    WHERE t.tx_amount IS NULL OR t.tx_timestamp IS NULL OR t.account_id IS NULL OR t.department_code IS NULL

    UNION ALL
    SELECT 'DQ_02:' || t.tx_id, t.batch_id, rm.run_id, r.rule_id,
           t.tx_id, t.evidence_doc_no, 'DQ_INTEGRITY', r.severity,
           80.00, t.tx_amount, 0, ABS(t.tx_amount),
           '비정상 ' || r.threshold_value || '원 이하 금액(' || t.tx_amount || '원, 유형=' || t.tx_type || ')'
    FROM audit_risk.raw_transactions t
    JOIN audit_risk.validation_rules r ON r.rule_id = 'DQ_02' AND r.is_active = TRUE
    CROSS JOIN run_meta rm
    WHERE t.tx_type NOT IN ('REFUND', 'CANCEL') AND t.tx_amount <= r.threshold_value

    UNION ALL
    SELECT 'DQ_03:' || d.tx_id, d.batch_id, rm.run_id, r.rule_id,
           d.tx_id, d.evidence_doc_no, 'DQ_INTEGRITY', r.severity,
           95.00, d.tx_amount, 0, d.tx_amount,
           '중복 거래/증빙 해시 탐지 (건수=' || d.dup_cnt || ', hash=' || COALESCE(d.file_hash, 'N/A') || ')'
    FROM (
        SELECT t.*, e.file_hash,
               GREATEST(
                   COUNT(*) OVER (PARTITION BY t.source_dataset, t.account_id, t.merchant_id, t.tx_amount, t.tx_timestamp),
                   CASE WHEN e.file_hash IS NOT NULL THEN COUNT(*) OVER (PARTITION BY e.file_hash) ELSE 1 END
               ) AS dup_cnt
        FROM audit_risk.raw_transactions t
        LEFT JOIN audit_risk.raw_evidences e ON t.tx_id = e.tx_id
    ) d
    JOIN audit_risk.validation_rules r ON r.rule_id = 'DQ_03' AND r.is_active = TRUE
    CROSS JOIN run_meta rm
    WHERE d.dup_cnt >= r.threshold_value

    UNION ALL
    SELECT 'EV_01:' || t.tx_id, t.batch_id, rm.run_id, r.rule_id,
           t.tx_id, t.evidence_doc_no, 'EVIDENCE_MATCH', r.severity,
           92.00, t.tx_amount, 0, t.tx_amount,
           '기준(' || r.threshold_value || '원) 이상 거래(' || t.tx_amount || '원) 증빙 미매칭'
    FROM audit_risk.raw_transactions t
    JOIN audit_risk.validation_rules r ON r.rule_id = 'EV_01' AND r.is_active = TRUE
    CROSS JOIN run_meta rm
    WHERE COALESCE(t.source_dataset, '') <> 'SEOUL_OA22156'
      AND t.tx_amount >= r.threshold_value
      AND NOT EXISTS (SELECT 1 FROM audit_risk.raw_evidences e WHERE e.tx_id = t.tx_id)
      AND NOT EXISTS (SELECT 1 FROM audit_risk.raw_evidences e WHERE t.evidence_doc_no IS NOT NULL AND e.evidence_doc_no = t.evidence_doc_no)

    UNION ALL
    SELECT 'EV_02:' || t.tx_id, t.batch_id, rm.run_id, r.rule_id,
           t.tx_id, e.evidence_doc_no, 'EVIDENCE_MATCH', r.severity,
           94.00, t.tx_amount, e.evidence_amount, ABS(t.tx_amount - e.evidence_amount),
           '금액 불일치: 원장(' || t.tx_amount || ') vs 증빙(' || e.evidence_amount || ')'
    FROM audit_risk.raw_transactions t
    JOIN audit_risk.raw_evidences e ON t.tx_id = e.tx_id
    JOIN audit_risk.validation_rules r ON r.rule_id = 'EV_02' AND r.is_active = TRUE
    CROSS JOIN run_meta rm
    WHERE COALESCE(t.source_dataset, '') <> 'SEOUL_OA22156'
      AND ABS(t.tx_amount - e.evidence_amount) > r.threshold_value

    UNION ALL
    SELECT 'EV_03:' || t.tx_id, t.batch_id, rm.run_id, r.rule_id,
           t.tx_id, e.evidence_doc_no, 'EVIDENCE_MATCH', r.severity,
           60.00, t.tx_amount, e.evidence_amount, 0,
           '일자 괴리: 전기일(' || t.posting_date || ') vs 증빙(' || e.issue_date || ')'
    FROM audit_risk.raw_transactions t
    JOIN audit_risk.raw_evidences e ON t.tx_id = e.tx_id
    JOIN audit_risk.validation_rules r ON r.rule_id = 'EV_03' AND r.is_active = TRUE
    CROSS JOIN run_meta rm
    WHERE COALESCE(t.source_dataset, '') <> 'SEOUL_OA22156'
      AND (e.issue_date > t.posting_date + INTERVAL '7 days' OR (t.posting_date - e.issue_date) > r.threshold_value::INTEGER)

    UNION ALL
    SELECT 'AN_01:' || t.tx_id, t.batch_id, rm.run_id, r.rule_id,
           t.tx_id, t.evidence_doc_no, 'ANOMALY_RISK', r.severity,
           LEAST(99.00, 70.00 + ((t.tx_amount - b.avg_amount) / NULLIF(b.stddev_amount, 0)) * 2.5)::NUMERIC(5,2),
           t.tx_amount, b.avg_amount, (t.tx_amount - b.avg_amount),
           'Z-Score 이상치(기준 ' || r.threshold_value || 'σ): ' || ROUND((t.tx_amount - b.avg_amount) / NULLIF(b.stddev_amount, 0), 2) || 'σ'
    FROM audit_risk.raw_transactions t
    JOIN audit_risk.baseline_stats b ON t.merchant_category = b.merchant_category
    JOIN audit_risk.validation_rules r ON r.rule_id = 'AN_01' AND r.is_active = TRUE
    CROSS JOIN run_meta rm
    WHERE t.tx_amount > 0 AND (t.tx_amount - b.avg_amount) / NULLIF(b.stddev_amount, 0) >= r.threshold_value

    UNION ALL
    -- [AN_02] 서로 다른 (일시, 금액)이 2건 이상인 진짜 분할결제만 선별 (동일 분금액 중복은 DQ_03 전담)
    SELECT 'AN_02:' || t.tx_id, t.batch_id, rm.run_id, r.rule_id,
           t.tx_id, t.evidence_doc_no, 'ANOMALY_RISK', r.severity,
           98.00, t.tx_amount, r.threshold_value, sc.daily_sum - r.threshold_value,
           '한도(' || r.threshold_value || '원) 회피 분할결제 의심: ' || sc.tx_cnt || '건 합계 ' || sc.daily_sum || '원'
    FROM audit_risk.validation_rules r
    JOIN (
        SELECT t2.source_dataset, t2.account_id, t2.merchant_id, t2.posting_date, COUNT(*) AS tx_cnt, SUM(t2.tx_amount) AS daily_sum
        FROM audit_risk.raw_transactions t2
        JOIN audit_risk.validation_rules r2 ON r2.rule_id = 'AN_02' AND r2.is_active = TRUE
        WHERE t2.tx_amount >= r2.threshold_value * 0.70 AND t2.tx_amount < r2.threshold_value
        GROUP BY t2.source_dataset, t2.account_id, t2.merchant_id, t2.posting_date
        HAVING COUNT(*) >= 2
           AND SUM(t2.tx_amount) >= MAX(r2.threshold_value)
           AND COUNT(DISTINCT t2.tx_timestamp::TEXT || '|' || t2.tx_amount::TEXT) >= 2
    ) sc ON r.rule_id = 'AN_02' AND r.is_active = TRUE
    JOIN audit_risk.raw_transactions t
      ON t.source_dataset = sc.source_dataset AND t.account_id = sc.account_id AND t.merchant_id = sc.merchant_id AND t.posting_date = sc.posting_date
    CROSS JOIN run_meta rm
    WHERE t.tx_amount >= r.threshold_value * 0.70 AND t.tx_amount < r.threshold_value

    UNION ALL
    SELECT 'AN_03:' || t.tx_id, t.batch_id, rm.run_id, r.rule_id,
           t.tx_id, t.evidence_doc_no, 'ANOMALY_RISK', r.severity,
           65.00, t.tx_amount, r.threshold_value, t.tx_amount,
           '업무 외 시간대 고액(' || TO_CHAR(t.tx_timestamp AT TIME ZONE 'Asia/Seoul', 'YYYY-MM-DD HH24:MI Dy') ||
           CASE WHEN t.has_exact_time THEN '' ELSE ' [시각미상-주말기준]' END || ')'
    FROM audit_risk.raw_transactions t
    JOIN audit_risk.validation_rules r ON r.rule_id = 'AN_03' AND r.is_active = TRUE
    CROSS JOIN run_meta rm
    WHERE t.tx_amount >= r.threshold_value
      AND (
          EXTRACT(ISODOW FROM t.tx_timestamp AT TIME ZONE 'Asia/Seoul') IN (6, 7)
          OR (t.has_exact_time = TRUE AND (
              EXTRACT(HOUR FROM t.tx_timestamp AT TIME ZONE 'Asia/Seoul') >= 23
              OR EXTRACT(HOUR FROM t.tx_timestamp AT TIME ZONE 'Asia/Seoul') < 6
          ))
      )

    UNION ALL
    SELECT 'AN_04:' || w.tx_id, w.batch_id, rm.run_id, r.rule_id,
           w.tx_id, w.evidence_doc_no, 'ANOMALY_RISK', r.severity,
           84.00, w.tx_amount, w.tx_amount, w.tx_amount,
           '근접 일자(' || r.threshold_value::INTEGER || '일) 동일 부서장소금액 반복'
    FROM (
        SELECT t.*,
               LAG(t.posting_date)  OVER (PARTITION BY COALESCE(t.source_dataset,''), t.department_code, t.merchant_id, t.tx_amount ORDER BY t.posting_date, t.tx_id) AS prev_dt,
               LEAD(t.posting_date) OVER (PARTITION BY COALESCE(t.source_dataset,''), t.department_code, t.merchant_id, t.tx_amount ORDER BY t.posting_date, t.tx_id) AS next_dt
        FROM audit_risk.raw_transactions t
        WHERE t.tx_amount > 0 AND t.department_code IS NOT NULL AND t.posting_date IS NOT NULL
    ) w
    JOIN audit_risk.validation_rules r ON r.rule_id = 'AN_04' AND r.is_active = TRUE
    CROSS JOIN run_meta rm
    WHERE (w.prev_dt IS NOT NULL AND (w.posting_date - w.prev_dt) <= r.threshold_value::INTEGER)
       OR (w.next_dt IS NOT NULL AND (w.next_dt - w.posting_date) <= r.threshold_value::INTEGER)
),
deduped_issues AS (
    SELECT DISTINCT ON (finding_key) *
    FROM detected_issues
    ORDER BY finding_key, risk_score DESC
)
INSERT INTO audit_risk.dq_anomaly_findings
(finding_key, batch_id, run_id, rule_id, tx_id, evidence_doc_no, finding_type, severity, risk_score, detected_amount, expected_amount, discrepancy_amount, detail_message)
SELECT finding_key, batch_id, run_id, rule_id, tx_id, evidence_doc_no, finding_type, severity, risk_score, detected_amount, expected_amount, discrepancy_amount, detail_message
FROM deduped_issues
ON CONFLICT (finding_key) DO UPDATE SET
    run_id = EXCLUDED.run_id,
    risk_score = EXCLUDED.risk_score,
    detected_amount = EXCLUDED.detected_amount,
    discrepancy_amount = EXCLUDED.discrepancy_amount,
    detail_message = EXCLUDED.detail_message,
    updated_at = NOW();

SELECT
    COALESCE(t.source_dataset, 'UNKNOWN') AS source_dataset,
    f.severity,
    f.rule_id,
    r.rule_name,
    COUNT(*) AS issue_count,
    SUM(f.detected_amount) AS total_detected_amount
FROM audit_risk.dq_anomaly_findings f
JOIN audit_risk.validation_rules r ON f.rule_id = r.rule_id
LEFT JOIN audit_risk.raw_transactions t ON f.tx_id = t.tx_id
GROUP BY COALESCE(t.source_dataset, 'UNKNOWN'), f.severity, f.rule_id, r.rule_name
ORDER BY source_dataset,
    CASE f.severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 WHEN 'MEDIUM' THEN 3 ELSE 4 END,
    issue_count DESC;

SELECT f.rule_id, f.tx_id, t.posting_date, TO_CHAR(t.tx_timestamp AT TIME ZONE 'Asia/Seoul', 'HH24:MI') AS hhmm,
       t.department_code, t.merchant_id, t.tx_amount
FROM audit_risk.dq_anomaly_findings f
JOIN audit_risk.raw_transactions t ON f.tx_id = t.tx_id
WHERE t.source_dataset = 'SEOUL_OA22156' AND f.rule_id = 'AN_02'
ORDER BY t.posting_date DESC, t.department_code, t.merchant_id, hhmm;
