-- ============================================================================
-- 01_init_and_verify.sql : 스키마/기준데이터/20건 시나리오 + 1차 검증
-- ============================================================================
DROP SCHEMA IF EXISTS audit_risk CASCADE;
CREATE SCHEMA audit_risk;

CREATE TABLE audit_risk.etl_batch_log (
    batch_id VARCHAR(64) PRIMARY KEY,
    source_system VARCHAR(50),
    file_name VARCHAR(200),
    total_rows INT,
    valid_rows INT,
    status VARCHAR(20) DEFAULT 'RUNNING',
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE audit_risk.validation_rules (
    rule_id VARCHAR(10) PRIMARY KEY,
    rule_name VARCHAR(100) NOT NULL,
    rule_category VARCHAR(30) NOT NULL,
    severity VARCHAR(10) NOT NULL,
    description TEXT
);
INSERT INTO audit_risk.validation_rules VALUES
('DQ_00','스키마 파싱 오류','DATA_QUALITY','HIGH','금액/ID 파싱 불가 행 격리'),
('DQ_01','필수값 결측','DATA_QUALITY','HIGH','금액/시각/계정/부서 NULL'),
('DQ_02','0원 이하 비정상 금액','DATA_QUALITY','HIGH','환불/취소 외 0 이하 금액'),
('DQ_03','중복 거래·증빙 해시','DATA_QUALITY','CRITICAL','동일 거래키 또는 증빙 파일 해시 중복'),
('EV_01','고액 증빙 누락','VOUCHING','CRITICAL','50만원 이상 거래 증빙 미매칭'),
('EV_02','거래-증빙 금액 불일치','VOUCHING','CRITICAL','금액 차이 100원 초과'),
('EV_03','증빙 일자 괴리','VOUCHING','MEDIUM','발행일 역전 7일 초과/과거 30일 초과'),
('AN_01','Z-Score 이상치','ANOMALY','HIGH','카테고리 기준선 3σ 초과'),
('AN_02','한도 회피 분할결제','ANOMALY','CRITICAL','100만원 직하 분할결제'),
('AN_03','업무 외 시간 고액','ANOMALY','MEDIUM','심야/주말 50만원 이상');

CREATE TABLE audit_risk.baseline_stats (
    merchant_category VARCHAR(40) PRIMARY KEY,
    avg_amount NUMERIC(18,2) NOT NULL,
    stddev_amount NUMERIC(18,2) NOT NULL
);
INSERT INTO audit_risk.baseline_stats VALUES
('IT_CONSULTING',  200000, 90000),
('TRAVEL',         150000, 70000),
('ENTERTAINMENT',  150000, 80000),
('OFFICE_SUPPLY',  140000, 80000),
('LOGISTICS',      160000, 80000);

CREATE TABLE audit_risk.raw_transactions (
    tx_id VARCHAR(64) PRIMARY KEY,
    batch_id VARCHAR(64),
    tx_timestamp TIMESTAMPTZ,
    posting_date DATE,
    account_id VARCHAR(50),
    merchant_id VARCHAR(50),
    merchant_category VARCHAR(40),
    tx_type VARCHAR(20) DEFAULT 'EXPENSE',
    currency VARCHAR(5) DEFAULT 'KRW',
    tx_amount NUMERIC(18,2),
    department_code VARCHAR(30),
    evidence_doc_no VARCHAR(80),
    approver_id VARCHAR(30),
    source_dataset VARCHAR(30),
    is_simulated_fraud BOOLEAN DEFAULT FALSE,
    loaded_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE audit_risk.raw_evidences (
    evidence_id VARCHAR(80) PRIMARY KEY,
    evidence_doc_no VARCHAR(80),
    tx_id VARCHAR(64),
    doc_type VARCHAR(30),
    issue_date DATE,
    evidence_amount NUMERIC(18,2),
    currency VARCHAR(5) DEFAULT 'KRW',
    vendor_name VARCHAR(100),
    file_hash VARCHAR(64),
    ocr_confidence NUMERIC(5,2),
    uploaded_by VARCHAR(50),
    loaded_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE audit_risk.dq_anomaly_findings (
    finding_id BIGSERIAL PRIMARY KEY,
    finding_key VARCHAR(120) NOT NULL UNIQUE,
    batch_id VARCHAR(64),
    run_id VARCHAR(40),
    rule_id VARCHAR(10) REFERENCES audit_risk.validation_rules(rule_id),
    tx_id VARCHAR(64),
    evidence_doc_no VARCHAR(80),
    finding_type VARCHAR(20),
    severity VARCHAR(10),
    risk_score NUMERIC(5,2),
    detected_amount NUMERIC(18,2),
    expected_amount NUMERIC(18,2),
    discrepancy_amount NUMERIC(18,2),
    detail_message TEXT,
    issue_status VARCHAR(20) DEFAULT 'OPEN',
    detected_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE audit_risk.pbi_refresh_history (
    refresh_id BIGSERIAL PRIMARY KEY,
    run_id VARCHAR(40),
    dataset_id VARCHAR(80),
    status VARCHAR(20),
    http_status INT,
    message TEXT,
    requested_at TIMESTAMPTZ DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- 20건 시나리오: 정상 9 + 룰별 케이스
-- ---------------------------------------------------------------------------
INSERT INTO audit_risk.etl_batch_log(batch_id, source_system, file_name, total_rows, valid_rows, status, completed_at)
VALUES ('BATCH_SEED_20','SQL_SEED','scenario_20',20,20,'COMPLETED',NOW());

-- 정상 9건 (S01~S09): 금액은 카테고리 기준선 평균, 업무시간 평일
INSERT INTO audit_risk.raw_transactions
(tx_id,batch_id,tx_timestamp,posting_date,account_id,merchant_id,merchant_category,tx_amount,department_code,evidence_doc_no,approver_id,source_dataset)
SELECT 'S'||LPAD(g::TEXT,2,'0'),'BATCH_SEED_20',
       TIMESTAMPTZ '2026-11-02 10:00:00+09' + (g||' hours')::INTERVAL,
       DATE '2026-11-02',
       'ACC_'||g,'MER_'||g, b.merchant_category, b.avg_amount, 'AUDIT_01','EV_S'||LPAD(g::TEXT,2,'0'),'APR_01','SEED'
FROM generate_series(1,9) g
JOIN (SELECT merchant_category, avg_amount, ROW_NUMBER() OVER (ORDER BY merchant_category) rn FROM audit_risk.baseline_stats) b
  ON b.rn = 1 + (g % 5);

-- 오류/이상 케이스 11건
INSERT INTO audit_risk.raw_transactions
(tx_id,batch_id,tx_timestamp,posting_date,account_id,merchant_id,merchant_category,tx_type,tx_amount,department_code,evidence_doc_no,approver_id,source_dataset) VALUES
('S10','BATCH_SEED_20','2026-11-03 11:00+09','2026-11-03','ACC_10','MER_10','TRAVEL','EXPENSE',120000,NULL,'EV_S10','APR_01','SEED'),            -- DQ_01
('S11','BATCH_SEED_20','2026-11-03 12:00+09','2026-11-03','ACC_11','MER_11','TRAVEL','EXPENSE',-35000,'AUDIT_01','EV_S11','APR_01','SEED'),     -- DQ_02
('S12','BATCH_SEED_20','2026-11-03 13:00+09','2026-11-03','ACC_12','MER_12','LOGISTICS','EXPENSE',130000,'AUDIT_01','EV_S12','APR_01','SEED'),  -- DQ_03 (해시 중복)
('S13','BATCH_SEED_20','2026-11-03 14:00+09','2026-11-03','ACC_13','MER_13','LOGISTICS','EXPENSE',130000,'AUDIT_01','EV_S13','APR_01','SEED'),
('S14','BATCH_SEED_20','2026-11-04 10:00+09','2026-11-04','ACC_14','MER_14','IT_CONSULTING','EXPENSE',780000,'RISK_TEAM','EV_S14','APR_01','SEED'), -- EV_01 (증빙 없음)
('S15','BATCH_SEED_20','2026-11-04 11:00+09','2026-11-04','ACC_15','MER_15','IT_CONSULTING','EXPENSE',640000,'RISK_TEAM','EV_S15','APR_01','SEED'), -- EV_02
('S16','BATCH_SEED_20','2026-11-04 12:00+09','2026-11-04','ACC_16','MER_16','TRAVEL','EXPENSE',200000,'FINANCE','EV_S16','APR_01','SEED'),       -- EV_03
('S17','BATCH_SEED_20','2026-11-05 10:00+09','2026-11-05','ACC_17','MER_17','ENTERTAINMENT','EXPENSE',450000,'FINANCE','EV_S17','APR_01','SEED'),-- AN_01
('S18','BATCH_SEED_20','2026-11-05 14:10+09','2026-11-05','ACC_SPLIT','MER_SPLIT','OFFICE_SUPPLY','EXPENSE',940000,'OPS_TEAM','EV_S18','APR_01','SEED'), -- AN_02
('S19','BATCH_SEED_20','2026-11-05 14:14+09','2026-11-05','ACC_SPLIT','MER_SPLIT','OFFICE_SUPPLY','EXPENSE',910000,'OPS_TEAM','EV_S19','APR_01','SEED'),
('S20','BATCH_SEED_20','2026-11-06 02:15+09','2026-11-06','ACC_20','MER_20','LOGISTICS','EXPENSE',520000,'OPS_TEAM','EV_S20','APR_01','SEED'); -- AN_03

-- 증빙 (S14 없음)
INSERT INTO audit_risk.raw_evidences(evidence_id,evidence_doc_no,tx_id,doc_type,issue_date,evidence_amount,vendor_name,file_hash,ocr_confidence,uploaded_by)
SELECT 'EVID_'||tx_id,'EV_'||tx_id,tx_id,'RECEIPT',posting_date,
       CASE tx_id WHEN 'S15' THEN 410000 ELSE tx_amount END,
       'VENDOR_'||merchant_id,
       CASE WHEN tx_id IN ('S12','S13') THEN 'SHARED_DUP_HASH_SEED' ELSE md5(tx_id) END,
       97.5,'seed'
FROM audit_risk.raw_transactions
WHERE tx_id <> 'S14' AND tx_amount IS NOT NULL;
UPDATE audit_risk.raw_evidences SET issue_date = DATE '2026-09-19' WHERE tx_id='S16';
UPDATE audit_risk.raw_evidences SET issue_date = DATE '2026-11-05' WHERE tx_id='S17';
UPDATE audit_risk.raw_evidences SET issue_date = DATE '2026-11-05' WHERE tx_id='S20';
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
