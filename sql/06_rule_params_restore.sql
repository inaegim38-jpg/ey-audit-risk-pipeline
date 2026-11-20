-- ============================================================================
-- 06_rule_params_restore.sql
-- 검증 룰 파라미터 열(threshold_value, is_active, remediation_hint)과 AN_04 룰,
-- raw_transactions.has_exact_time 열을 보장하고, 룰의 한글 텍스트를 UTF-8로 (재)기록합니다.
-- 01 실행 후, 데이터 적재·02 엔진 실행 전에 실행. 여러 번 실행해도 안전(멱등).
--
-- 배경: 이전에 PowerShell 파이프(Get-Content | docker exec psql)로 SQL을 넣어
--       validation_rules의 한글이 '?'(0x3F)로 덮였고, 이는 원래 바이트로 되돌릴 수 없음.
-- 복구 근거:
--   * DQ_00~AN_03 rule_name/description : sql/01_init_and_verify.sql 원문 그대로
--     (깨진 값의 글자 수·공백 패턴과 일치함을 확인)
--   * AN_03 description, AN_04 rule_name/description : 원문이 저장소·컨테이너 어디에도 없어
--     깨진 값의 글자 수 패턴과 02 엔진 로직을 근거로 재작성
--   * remediation_hint 전체 : 원문이 남아 있지 않아 새로 작성 (원문 복원 아님)
--   * threshold_value / is_active : 깨지지 않은 숫자값을 DB에서 조회한 그대로 유지
--     (threshold_value는 법적 기준이 아니라 프로젝트 가정값)
-- 실행: docker cp 후 psql -f 로만 실행할 것 (파이프 금지)
-- ============================================================================

ALTER TABLE audit_risk.validation_rules
    ADD COLUMN IF NOT EXISTS threshold_value  NUMERIC(18,2),
    ADD COLUMN IF NOT EXISTS is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS remediation_hint TEXT;

ALTER TABLE audit_risk.raw_transactions
    ADD COLUMN IF NOT EXISTS has_exact_time BOOLEAN DEFAULT TRUE;

INSERT INTO audit_risk.validation_rules
    (rule_id, rule_name, rule_category, severity, description, threshold_value, is_active, remediation_hint)
VALUES
('DQ_00','스키마 파싱 오류','DATA_QUALITY','HIGH','금액/ID 파싱 불가 행 격리',0,TRUE,'원천 파일 형식과 필드 매핑 재확인'),
('DQ_01','필수값 결측','DATA_QUALITY','HIGH','금액/시각/계정/부서 NULL',0,TRUE,'원천 시스템에서 누락 항목 보완 요청'),
('DQ_02','0원 이하 비정상 금액','DATA_QUALITY','HIGH','환불/취소 외 0 이하 금액',0,TRUE,'거래 유형(환불/취소 여부)과 원 전표 확인'),
('DQ_03','중복 거래·증빙 해시','DATA_QUALITY','CRITICAL','동일 거래키 또는 증빙 파일 해시 중복',2,TRUE,'중복 전기·증빙 재사용 여부 확인'),
('EV_01','고액 증빙 누락','VOUCHING','CRITICAL','50만원 이상 거래 증빙 미매칭',500000,TRUE,'증빙 원본 제출 요청 및 매칭 키 확인'),
('EV_02','거래-증빙 금액 불일치','VOUCHING','CRITICAL','금액 차이 100원 초과',100,TRUE,'원장 금액과 증빙 금액 차이 사유 확인'),
('EV_03','증빙 일자 괴리','VOUCHING','MEDIUM','발행일 역전 7일 초과/과거 30일 초과',30,TRUE,'증빙 발행일과 전기일 차이 사유 확인'),
('AN_01','Z-Score 이상치','ANOMALY','HIGH','카테고리 기준선 3σ 초과',3,TRUE,'거래 목적과 금액 산정 근거 확인'),
('AN_02','한도 회피 분할결제','ANOMALY','CRITICAL','100만원 직하 분할결제',1000000,TRUE,'동일일·동일처 분할 집행 사유 확인'),
('AN_03','업무 외 시간 고액','ANOMALY','MEDIUM','심야(23~06시)/주말 50만원 이상',500000,TRUE,'업무 관련성과 사전 승인 여부 확인'),
('AN_04','근접 일자 반복 집행','ANOMALY','HIGH','동일 부서·장소·금액 ±3일 내 반복 집행',3,TRUE,'반복 집행의 업무 필요성 확인')
ON CONFLICT (rule_id) DO UPDATE SET
    rule_name        = EXCLUDED.rule_name,
    rule_category    = EXCLUDED.rule_category,
    severity         = EXCLUDED.severity,
    description      = EXCLUDED.description,
    remediation_hint = EXCLUDED.remediation_hint,
    -- 이미 설정된 파라미터는 덮어쓰지 않음(민감도 조정값 보존)
    threshold_value  = COALESCE(audit_risk.validation_rules.threshold_value, EXCLUDED.threshold_value);

-- Power BI 요약 뷰에 데이터셋 구분 열 추가 (열 순서가 바뀌므로 DROP 후 재생성; 현재 DB 정의와 동일)
DROP VIEW IF EXISTS audit_risk.vw_pbi_tx_summary;
CREATE VIEW audit_risk.vw_pbi_tx_summary AS
WITH tx_flags AS (
    SELECT
        t.tx_id,
        COALESCE(t.source_dataset, 'UNKNOWN') AS source_dataset,
        t.posting_date,
        COALESCE(t.department_code, 'UNKNOWN') AS department_code,
        t.merchant_category,
        t.tx_amount,
        MAX(CASE WHEN f.finding_id IS NOT NULL THEN 1 ELSE 0 END) AS is_flagged,
        MAX(CASE WHEN f.finding_type IN ('DQ_SCHEMA','DQ_INTEGRITY','EVIDENCE_MATCH') THEN 1 ELSE 0 END) AS is_dq_error,
        MAX(CASE WHEN f.finding_type = 'ANOMALY_RISK' THEN 1 ELSE 0 END) AS is_anomaly
    FROM audit_risk.raw_transactions t
    LEFT JOIN audit_risk.dq_anomaly_findings f ON t.tx_id = f.tx_id
    GROUP BY t.tx_id, COALESCE(t.source_dataset, 'UNKNOWN'), t.posting_date,
             COALESCE(t.department_code, 'UNKNOWN'), t.merchant_category, t.tx_amount
)
SELECT
    source_dataset, posting_date, department_code, merchant_category,
    COUNT(*) AS total_tx_count,
    COALESCE(SUM(tx_amount), 0) AS total_tx_amount,
    SUM(is_flagged) AS flagged_tx_count,
    SUM(is_dq_error) AS dq_error_tx_count,
    SUM(is_anomaly) AS anomaly_tx_count,
    COALESCE(SUM(CASE WHEN is_anomaly = 1 THEN tx_amount ELSE 0 END), 0) AS anomaly_exposure_amount
FROM tx_flags
GROUP BY source_dataset, posting_date, department_code, merchant_category;

-- 검산: 한글이 '?'로 남은 행이 없어야 함 (0이어야 정상)
SELECT COUNT(*) AS rules_with_question_mark
FROM audit_risk.validation_rules
WHERE rule_name LIKE '%?%' OR description LIKE '%?%' OR remediation_hint LIKE '%?%';
