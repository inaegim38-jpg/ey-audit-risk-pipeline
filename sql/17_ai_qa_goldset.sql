-- ============================================================================
-- 17. AI QA 골드셋 하네스
-- ============================================================================
-- 목적: 모델 호출이 아니라, AI 실행 결과를 정답·환각·상태 기준으로
--       재현 가능하게 평가하고 재검토 대상을 만드는 계약 계층.
-- 합성 데이터 기준으로 source_doc_ref와 truth_json을 골드셋에 고정한다.
-- 실제 고객 적용 시 독립적인 human-reviewed goldset으로 교체해야 한다.
-- ============================================================================

CREATE TABLE IF NOT EXISTS audit_core.dim_ai_qa_goldset (
    gold_id                 VARCHAR(30) PRIMARY KEY,
    engagement_id           VARCHAR(40) NOT NULL
                            REFERENCES audit_core.dim_engagement(engagement_id),
    source_doc_ref           TEXT NOT NULL,
    source_sha256            CHAR(64) NOT NULL,
    expected_json            JSONB NOT NULL,
    expected_field_total     SMALLINT NOT NULL CHECK (expected_field_total > 0),
    expected_field_exact     SMALLINT NOT NULL CHECK (expected_field_exact >= 0),
    expected_hallucination_yn BOOLEAN NOT NULL,
    expected_qa_status       VARCHAR(20) NOT NULL
                            CHECK (expected_qa_status IN ('APPROVED','ESCALATED')),
    case_type                VARCHAR(30) NOT NULL
                            CHECK (case_type IN ('CLEAN_EXTRACTION','LOW_ACCURACY','HALLUCINATION')),
    gold_version             VARCHAR(20) NOT NULL DEFAULT 'GOLD-1.0',
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (engagement_id, source_doc_ref, gold_version)
);

CREATE TABLE IF NOT EXISTS audit_core.fact_ai_qa_eval (
    eval_id                 BIGSERIAL PRIMARY KEY,
    engagement_id           VARCHAR(40) NOT NULL,
    gold_id                 VARCHAR(30) NOT NULL
                            REFERENCES audit_core.dim_ai_qa_goldset(gold_id),
    qa_id                   UUID NOT NULL
                            REFERENCES audit_core.fact_ai_qa_run(qa_id),
    model_version           VARCHAR(40),
    prompt_version          VARCHAR(20),
    actual_field_total      SMALLINT,
    actual_field_exact      SMALLINT,
    actual_exact_match_rate NUMERIC(5,2),
    expected_field_exact    SMALLINT NOT NULL,
    expected_hallucination_yn BOOLEAN NOT NULL,
    actual_hallucination_yn BOOLEAN NOT NULL,
    exact_match_pass        BOOLEAN NOT NULL,
    hallucination_pass      BOOLEAN NOT NULL,
    status_pass             BOOLEAN NOT NULL,
    eval_status             VARCHAR(20) NOT NULL
                            CHECK (eval_status IN ('PASS','FAIL','REVIEW')),
    evaluated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (gold_id, qa_id)
);

CREATE INDEX IF NOT EXISTS idx_ai_gold_eng
    ON audit_core.dim_ai_qa_goldset (engagement_id, case_type);
CREATE INDEX IF NOT EXISTS idx_ai_eval_status
    ON audit_core.fact_ai_qa_eval (engagement_id, eval_status);

-- 시드된 합성 QA에서 정상 5건과 저정확도/환각 5건을 골드셋으로 고정한다.
-- source_sha256까지 함께 고정해 같은 문서가 바뀌면 즉시 REVIEW가 되도록 한다.
INSERT INTO audit_core.dim_ai_qa_goldset
    (gold_id, engagement_id, source_doc_ref, source_sha256, expected_json,
     expected_field_total, expected_field_exact, expected_hallucination_yn,
     expected_qa_status, case_type)
SELECT
    'GOLD-AI-' || lpad(row_number() OVER (ORDER BY q.source_doc_ref)::text, 3, '0'),
    q.engagement_id,
    q.source_doc_ref,
    q.source_sha256,
    q.truth_json,
    q.field_total,
    q.field_exact,
    q.hallucination_yn,
    CASE WHEN q.hallucination_yn OR q.exact_match_rate < 95
         THEN 'ESCALATED' ELSE 'APPROVED' END,
    CASE WHEN q.hallucination_yn THEN 'HALLUCINATION'
         WHEN q.exact_match_rate < 95 THEN 'LOW_ACCURACY'
         ELSE 'CLEAN_EXTRACTION' END
FROM audit_core.fact_ai_qa_run q
WHERE q.engagement_id = 'ENG-2026-001'
  AND q.source_doc_ref IN (
      'docs/contract_001.pdf', 'docs/contract_002.pdf',
      'docs/contract_003.pdf', 'docs/contract_004.pdf',
      'docs/contract_005.pdf', 'docs/contract_007.pdf',
      'docs/contract_013.pdf', 'docs/contract_021.pdf',
      'docs/contract_034.pdf', 'docs/contract_039.pdf'
  )
ON CONFLICT (engagement_id, source_doc_ref, gold_version) DO UPDATE SET
    source_sha256 = EXCLUDED.source_sha256,
    expected_json = EXCLUDED.expected_json,
    expected_field_total = EXCLUDED.expected_field_total,
    expected_field_exact = EXCLUDED.expected_field_exact,
    expected_hallucination_yn = EXCLUDED.expected_hallucination_yn,
    expected_qa_status = EXCLUDED.expected_qa_status,
    case_type = EXCLUDED.case_type;

CREATE OR REPLACE FUNCTION audit_core.run_ai_qa_goldset(
    p_engagement_id VARCHAR(40),
    p_gold_version VARCHAR(20) DEFAULT 'GOLD-1.0'
)
RETURNS TABLE (
    cases_evaluated BIGINT,
    pass_count BIGINT,
    fail_count BIGINT,
    review_count BIGINT
)
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO audit_core.fact_ai_qa_eval
        (engagement_id, gold_id, qa_id, model_version, prompt_version,
         actual_field_total, actual_field_exact, actual_exact_match_rate,
         expected_field_exact, expected_hallucination_yn, actual_hallucination_yn,
         exact_match_pass, hallucination_pass, status_pass, eval_status)
    SELECT
        g.engagement_id,
        g.gold_id,
        q.qa_id,
        q.model_version,
        q.prompt_version,
        q.field_total,
        q.field_exact,
        q.exact_match_rate,
        g.expected_field_exact,
        g.expected_hallucination_yn,
        q.hallucination_yn,
        (q.source_sha256 = g.source_sha256
         AND q.ai_json = g.expected_json
         AND q.field_exact = g.expected_field_exact),
        (q.hallucination_yn = g.expected_hallucination_yn),
        (CASE WHEN q.hallucination_yn OR q.exact_match_rate < 95
              THEN 'ESCALATED' ELSE 'APPROVED' END = g.expected_qa_status),
        CASE
          WHEN q.source_sha256 <> g.source_sha256 THEN 'REVIEW'
          WHEN q.ai_json = g.expected_json
           AND q.field_exact = g.expected_field_exact
           AND q.hallucination_yn = g.expected_hallucination_yn
           AND (CASE WHEN q.hallucination_yn OR q.exact_match_rate < 95
                     THEN 'ESCALATED' ELSE 'APPROVED' END = g.expected_qa_status)
            THEN 'PASS'
          ELSE 'FAIL'
        END
    FROM audit_core.dim_ai_qa_goldset g
    JOIN audit_core.fact_ai_qa_run q
      ON q.engagement_id = g.engagement_id
     AND q.source_doc_ref = g.source_doc_ref
    WHERE g.engagement_id = p_engagement_id
      AND g.gold_version = p_gold_version
    ON CONFLICT (gold_id, qa_id) DO UPDATE SET
        model_version = EXCLUDED.model_version,
        prompt_version = EXCLUDED.prompt_version,
        actual_field_total = EXCLUDED.actual_field_total,
        actual_field_exact = EXCLUDED.actual_field_exact,
        actual_exact_match_rate = EXCLUDED.actual_exact_match_rate,
        expected_field_exact = EXCLUDED.expected_field_exact,
        expected_hallucination_yn = EXCLUDED.expected_hallucination_yn,
        actual_hallucination_yn = EXCLUDED.actual_hallucination_yn,
        exact_match_pass = EXCLUDED.exact_match_pass,
        hallucination_pass = EXCLUDED.hallucination_pass,
        status_pass = EXCLUDED.status_pass,
        eval_status = EXCLUDED.eval_status,
        evaluated_at = NOW();

    RETURN QUERY
    SELECT COUNT(*)::BIGINT,
           COUNT(*) FILTER (WHERE eval_status = 'PASS')::BIGINT,
           COUNT(*) FILTER (WHERE eval_status = 'FAIL')::BIGINT,
           COUNT(*) FILTER (WHERE eval_status = 'REVIEW')::BIGINT
    FROM audit_core.fact_ai_qa_eval
    WHERE engagement_id = p_engagement_id
      AND gold_id IN (
          SELECT gold_id FROM audit_core.dim_ai_qa_goldset
          WHERE engagement_id = p_engagement_id AND gold_version = p_gold_version
      );
END;
$$;

CREATE OR REPLACE VIEW audit_core.vw_ai_qa_goldset_summary AS
SELECT
    e.engagement_id,
    COUNT(*) AS cases_evaluated,
    COUNT(*) FILTER (WHERE e.eval_status = 'PASS') AS pass_count,
    COUNT(*) FILTER (WHERE e.eval_status = 'FAIL') AS fail_count,
    COUNT(*) FILTER (WHERE e.eval_status = 'REVIEW') AS review_count,
    ROUND((COUNT(*) FILTER (WHERE e.eval_status = 'PASS')::numeric
           / NULLIF(COUNT(*), 0) * 100), 2) AS pass_rate_pct,
    COUNT(*) FILTER (WHERE g.case_type = 'HALLUCINATION'
                      AND e.hallucination_pass) AS hallucination_cases_pass,
    COUNT(*) FILTER (WHERE g.case_type = 'LOW_ACCURACY'
                      AND e.status_pass) AS low_accuracy_escalated
FROM audit_core.fact_ai_qa_eval e
JOIN audit_core.dim_ai_qa_goldset g USING (gold_id)
GROUP BY e.engagement_id;

CREATE OR REPLACE VIEW audit_core.vw_ai_qa_review_queue AS
SELECT
    e.eval_id,
    e.engagement_id,
    g.gold_id,
    g.source_doc_ref,
    g.case_type,
    e.qa_id,
    e.model_version,
    e.prompt_version,
    e.eval_status,
    e.exact_match_pass,
    e.hallucination_pass,
    e.status_pass,
    e.evaluated_at
FROM audit_core.fact_ai_qa_eval e
JOIN audit_core.dim_ai_qa_goldset g USING (gold_id)
WHERE e.eval_status IN ('FAIL', 'REVIEW')
ORDER BY e.evaluated_at DESC;

INSERT INTO audit_core.change_log
    (object_type, object_name, version, changed_by, note)
SELECT 'PROMPT', 'audit_core.ai_qa_goldset', 'GOLD-1.0', 'system',
       'Synthetic goldset with exact-match, hallucination, and escalation gates'
WHERE NOT EXISTS (
    SELECT 1 FROM audit_core.change_log
    WHERE object_name = 'audit_core.ai_qa_goldset' AND version = 'GOLD-1.0'
);

-- 기본 실행: 10건 골드셋 평가만 수행하며 원래 AI QA/finding을 변경하지 않는다.
SELECT * FROM audit_core.run_ai_qa_goldset('ENG-2026-001');
SELECT * FROM audit_core.vw_ai_qa_goldset_summary
WHERE engagement_id = 'ENG-2026-001';
