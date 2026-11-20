-- ============================================================================
-- 07_final_counts.sql : 문서에 쓰는 수치를 DB에서 직접 조회하는 검산 쿼리 (읽기 전용)
-- 실행: docker cp sql/. ey_audit_postgres:/tmp/sql
--       docker exec ey_audit_postgres psql -U audit_admin -d audit_db -f /tmp/sql/07_final_counts.sql
-- ============================================================================
\pset pager off

-- (1) source_dataset별 거래 건수
SELECT COALESCE(source_dataset, 'UNKNOWN') AS source_dataset, COUNT(*) AS tx_count
FROM audit_risk.raw_transactions
GROUP BY 1 ORDER BY 1;

-- (2) source_dataset·severity·rule_id·rule_name별 탐지 건수와 금액
SELECT COALESCE(t.source_dataset, 'UNKNOWN') AS source_dataset, f.severity, f.rule_id, r.rule_name,
       COUNT(*) AS issue_count, SUM(f.detected_amount) AS total_detected_amount
FROM audit_risk.dq_anomaly_findings f
JOIN audit_risk.validation_rules r ON r.rule_id = f.rule_id
LEFT JOIN audit_risk.raw_transactions t ON t.tx_id = f.tx_id
GROUP BY 1, 2, 3, 4
ORDER BY 1, CASE f.severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 WHEN 'MEDIUM' THEN 3 ELSE 4 END, 5 DESC;

-- (2-검산) 데이터셋별 소계와 전체 합계
SELECT COALESCE(t.source_dataset, 'UNKNOWN') AS source_dataset, COUNT(*) AS issue_count, SUM(f.detected_amount) AS total_detected_amount
FROM audit_risk.dq_anomaly_findings f
LEFT JOIN audit_risk.raw_transactions t ON t.tx_id = f.tx_id
GROUP BY ROLLUP (1) ORDER BY 1 NULLS LAST;

-- (3) validation_rules 전체
SELECT rule_id, rule_name, severity, threshold_value, is_active
FROM audit_risk.validation_rules ORDER BY rule_id;

-- (4) SEOUL_OA22156 시각 정보: has_exact_time=false(00:00 플레이스홀더 분리) 건수
SELECT has_exact_time, COUNT(*) AS tx_count
FROM audit_risk.raw_transactions
WHERE source_dataset = 'SEOUL_OA22156'
GROUP BY 1 ORDER BY 1;

-- (5) 증빙 건수, 검토 상태 분포(시연용 변경 포함), 이번 실행에서 갱신되지 않은 잔존 탐지
SELECT COUNT(*) AS evidence_count FROM audit_risk.raw_evidences;
SELECT issue_status, COUNT(*) FROM audit_risk.dq_anomaly_findings GROUP BY 1 ORDER BY 1;
SELECT COUNT(*) AS stale_findings_not_in_latest_run
FROM audit_risk.dq_anomaly_findings
WHERE run_id IS DISTINCT FROM (SELECT MAX(run_id) FROM audit_risk.dq_anomaly_findings WHERE run_id LIKE 'RUN_%');

-- (6) 한글 깨짐 검산: '?'가 포함된 텍스트 건수 (0이어야 정상)
SELECT 'findings.detail_message' AS col, COUNT(*) AS question_mark_rows
FROM audit_risk.dq_anomaly_findings WHERE detail_message LIKE '%?%'
UNION ALL
SELECT 'validation_rules.*', COUNT(*) FROM audit_risk.validation_rules
WHERE rule_name LIKE '%?%' OR description LIKE '%?%' OR remediation_hint LIKE '%?%';

-- (7) 프로파일링(적재 후 DB 기준): 데이터셋별 기간·금액 분포·결측
SELECT source_dataset, MIN(posting_date) AS min_date, MAX(posting_date) AS max_date,
       COUNT(*) FILTER (WHERE tx_amount IS NULL) AS amount_null,
       COUNT(*) FILTER (WHERE tx_amount <= 0) AS amount_le_0,
       MIN(tx_amount) FILTER (WHERE tx_amount > 0) AS min_pos_amount,
       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY tx_amount) AS median_amount,
       MAX(tx_amount) AS max_amount,
       COUNT(*) FILTER (WHERE department_code IS NULL) AS dept_null,
       COUNT(DISTINCT department_code) AS dept_distinct
FROM audit_risk.raw_transactions GROUP BY 1 ORDER BY 1;

-- (8) 룰 간 중복: 데이터셋별 탐지된 고유 거래 수
SELECT COALESCE(t.source_dataset, 'UNKNOWN') AS source_dataset, COUNT(DISTINCT f.tx_id) AS distinct_flagged_tx
FROM audit_risk.dq_anomaly_findings f
LEFT JOIN audit_risk.raw_transactions t ON t.tx_id = f.tx_id
GROUP BY 1 ORDER BY 1;

-- (9) UTF-8 바이트 확인 (예: '필수값 결측' → ED 95 84 ...)
SELECT rule_id, encode(convert_to(rule_name, 'UTF8'), 'hex') AS utf8_hex
FROM audit_risk.validation_rules WHERE rule_id IN ('DQ_01', 'AN_04');

-- (10) SEOUL_OA22156 연도별 건수 (1970-01-01은 일자 파싱/원본 플레이스홀더 의심, 원본 확인 필요)
SELECT EXTRACT(YEAR FROM posting_date)::INT AS yr, COUNT(*) AS tx_count
FROM audit_risk.raw_transactions WHERE source_dataset = 'SEOUL_OA22156'
GROUP BY 1 ORDER BY 1;
