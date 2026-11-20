-- ============================================================================
-- 13. 파인딩-증거 연결 (증거 인용 커버리지)
-- ============================================================================
-- AI QA 파인딩은 원문 문서가 곧 증거다. 문서를 forensic.evidence에 등록하고
-- finding_evidence로 연결해야 "이 판단은 이 파일에서 나왔다"를 말할 수 있다.
-- ============================================================================

INSERT INTO forensic.cases (case_id, case_name, client_code, scope, opened_at, note)
VALUES ('CASE-2026-AUD', '한영정밀 2026 감사', 'ENG-2026-001',
        'AI 추출 검증 대상 문서 5건', DATE '2026-01-05',
        'audit_core 룰 엔진 검증용 증거 등록')
ON CONFLICT (case_id) DO NOTHING;

-- AI QA가 검증한 문서 중 환각·저정확도 5건을 증거로 등록
INSERT INTO forensic.evidence
    (evidence_id, case_id, rel_path, doc_type, size_bytes, sha256, custodian, storage)
SELECT 'EV-AUD-' || lpad(ROW_NUMBER() OVER (ORDER BY q.source_doc_ref)::text, 3, '0'),
       'CASE-2026-AUD',
       q.source_doc_ref,
       'CONTRACT',
       120000 + (ROW_NUMBER() OVER (ORDER BY q.source_doc_ref)) * 1000,
       repeat(md5(q.source_doc_ref), 2),
       'intern',
       'original-read-only'
FROM (
    SELECT DISTINCT source_doc_ref
    FROM audit_core.fact_ai_qa_run
    WHERE engagement_id = 'ENG-2026-001'
      AND (hallucination_yn OR exact_match_rate < 95)
) q
ON CONFLICT (evidence_id) DO NOTHING;

-- AI-02(환각) 파인딩을 해당 원문 증거와 연결
INSERT INTO audit_core.finding_evidence (finding_id, evidence_id, ref_table, ref_pk, note)
SELECT f.finding_id,
       e.evidence_id,
       'fact_ai_qa_run',
       q.qa_id::text,
       '환각 판정 원문'
FROM audit_core.fact_risk_finding f
JOIN audit_core.fact_ai_qa_run q ON f.ref_id = 'AI02:' || q.qa_id::text
JOIN forensic.evidence e ON e.rel_path = q.source_doc_ref
WHERE f.rule_id = 'AI-02'
ON CONFLICT (finding_id, evidence_id) DO NOTHING;


-- 전표 기반 파인딩의 원천은 GL·TB 파일이다. 적재 해시와 동일한 해시로 등록해
-- "이 파인딩이 인용한 파일이 실제 적재된 파일"임을 증명한다.
INSERT INTO forensic.evidence
    (evidence_id, case_id, rel_path, doc_type, size_bytes, sha256, custodian, storage)
SELECT 'EV-AUD-GL', 'CASE-2026-AUD', b.file_name, 'GL',
       4800000, b.sha256_hash, 'intern', 'original-read-only'
FROM audit_core.stg_ingestion_log b
WHERE b.batch_id = '33333333-3333-3333-3333-333333333333'
ON CONFLICT (evidence_id) DO NOTHING;

INSERT INTO forensic.evidence
    (evidence_id, case_id, rel_path, doc_type, size_bytes, sha256, custodian, storage)
SELECT 'EV-AUD-TB', 'CASE-2026-AUD', b.file_name, 'TB',
       96000, b.sha256_hash, 'intern', 'original-read-only'
FROM audit_core.stg_ingestion_log b
WHERE b.batch_id = '33333333-3333-3333-3333-333333333334'
ON CONFLICT (evidence_id) DO NOTHING;

-- 전표·공시 대조에서 나온 파인딩은 GL/TB를 인용한다
INSERT INTO audit_core.finding_evidence (finding_id, evidence_id, ref_table, ref_pk, note)
SELECT f.finding_id, 'EV-AUD-GL', 'fact_journal', f.ref_id, '전표 원본'
FROM audit_core.fact_risk_finding f
WHERE f.rule_id IN ('JET-01','JET-02','JET-03','JET-04','TIE-01')
ON CONFLICT (finding_id, evidence_id) DO NOTHING;

INSERT INTO audit_core.finding_evidence (finding_id, evidence_id, ref_table, ref_pk, note)
SELECT f.finding_id, 'EV-AUD-TB', 'fact_disclosure_line', f.ref_id, '시산표·공시 대조'
FROM audit_core.fact_risk_finding f
WHERE f.rule_id = 'TIE-01'
ON CONFLICT (finding_id, evidence_id) DO NOTHING;

-- 증거 인용 커버리지: 파인딩 중 최소 1개 증거가 연결된 비율
CREATE OR REPLACE VIEW audit_core.vw_evidence_coverage AS
SELECT f.engagement_id,
       COUNT(*) AS finding_count,
       COUNT(*) FILTER (WHERE fe.finding_id IS NOT NULL) AS linked_count,
       ROUND(100.0 * COUNT(*) FILTER (WHERE fe.finding_id IS NOT NULL)
             / NULLIF(COUNT(*), 0), 2) AS coverage_pct
FROM audit_core.fact_risk_finding f
LEFT JOIN (SELECT DISTINCT finding_id FROM audit_core.finding_evidence) fe
       ON fe.finding_id = f.finding_id
GROUP BY 1;
