-- [1] 서울시 데이터(2023~2024 기준선) 요약
SELECT merchant_category, avg_amount, stddev_amount
FROM audit_risk.baseline_stats
WHERE merchant_category LIKE 'SEOUL_%'
ORDER BY merchant_category;

-- [2] 순수 Out-of-Time(2025~2026, 84,225건) 기준 AN_01(Z-Score) 임계값 민감도
SELECT
    s.sigma_threshold,
    COUNT(t.tx_id) AS flagged_count,
    ROUND(100.0 * COUNT(t.tx_id) / NULLIF((SELECT COUNT(*) FROM audit_risk.raw_transactions WHERE source_dataset='SEOUL_OA22156' AND posting_date >= DATE '2025-01-01' AND tx_amount > 0), 0), 2) AS flagged_pct,
    COALESCE(SUM(t.tx_amount), 0) AS total_exposure_krw
FROM (VALUES (2.0), (2.5), (3.0), (3.5), (4.0)) AS s(sigma_threshold)
LEFT JOIN audit_risk.raw_transactions t
  JOIN audit_risk.baseline_stats b ON t.merchant_category = b.merchant_category
  ON t.source_dataset = 'SEOUL_OA22156'
 AND t.posting_date >= DATE '2025-01-01'
 AND t.tx_amount > 0
 AND (t.tx_amount - b.avg_amount) / NULLIF(b.stddev_amount, 0) >= s.sigma_threshold
GROUP BY s.sigma_threshold
ORDER BY s.sigma_threshold;

-- [3] 서울시 데이터(전체 178,762건) 고액 기준(30만 / 50만 / 100만) 민감도
SELECT
    th.amt_threshold,
    COUNT(*) FILTER (WHERE t.tx_amount >= th.amt_threshold) AS tx_over_threshold,
    COUNT(*) FILTER (
        WHERE t.tx_amount >= th.amt_threshold
          AND (EXTRACT(ISODOW FROM t.tx_timestamp AT TIME ZONE 'Asia/Seoul') IN (6,7)
               OR EXTRACT(HOUR FROM t.tx_timestamp AT TIME ZONE 'Asia/Seoul') >= 23
               OR EXTRACT(HOUR FROM t.tx_timestamp AT TIME ZONE 'Asia/Seoul') < 6)
    ) AS off_hours_weekend_count
FROM (VALUES (300000), (500000), (1000000)) AS th(amt_threshold)
CROSS JOIN audit_risk.raw_transactions t
WHERE t.source_dataset = 'SEOUL_OA22156'
GROUP BY th.amt_threshold
ORDER BY th.amt_threshold;

-- [4] 실제 탐지 샘플 확인 (AN_02 분할결제 상위 6건 / DQ_03 중복 상위 6건 / DQ_02 5건)
SELECT f.rule_id, f.tx_id, t.posting_date, TO_CHAR(t.tx_timestamp AT TIME ZONE 'Asia/Seoul', 'HH24:MI') AS hhmm,
       t.department_code, t.merchant_id, t.tx_amount, f.detail_message
FROM audit_risk.dq_anomaly_findings f
JOIN audit_risk.raw_transactions t ON f.tx_id = t.tx_id
WHERE t.source_dataset = 'SEOUL_OA22156' AND f.rule_id = 'AN_02'
ORDER BY t.posting_date DESC, t.department_code, t.merchant_id
LIMIT 6;

SELECT f.rule_id, f.tx_id, t.posting_date, TO_CHAR(t.tx_timestamp AT TIME ZONE 'Asia/Seoul', 'HH24:MI') AS hhmm,
       t.department_code, t.merchant_id, t.tx_amount
FROM audit_risk.dq_anomaly_findings f
JOIN audit_risk.raw_transactions t ON f.tx_id = t.tx_id
WHERE t.source_dataset = 'SEOUL_OA22156' AND f.rule_id = 'DQ_03'
ORDER BY t.tx_amount DESC, t.posting_date DESC
LIMIT 6;

SELECT f.rule_id, f.tx_id, t.posting_date, t.department_code, t.merchant_id, t.tx_amount, t.tx_type
FROM audit_risk.dq_anomaly_findings f
JOIN audit_risk.raw_transactions t ON f.tx_id = t.tx_id
WHERE t.source_dataset = 'SEOUL_OA22156' AND f.rule_id = 'DQ_02';
