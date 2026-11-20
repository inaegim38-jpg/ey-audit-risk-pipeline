-- ============================================================================
-- 09_forensic_pbi_views.sql : 포렌식 계층 Power BI 뷰
-- ============================================================================
-- 전제: 08_forensic_case_schema.sql 실행 후
-- 목적: 대시보드가 직접 조인하지 않도록 집계 단위를 뷰로 고정한다.
--   vw_pbi_evidence          증거 유형별 건수·용량·고유 해시
--   vw_pbi_review_progress   문서 유형·제안 관련성·검토 상태별 건수
--   vw_pbi_findings          파인딩별 증거 연결 수
--   vw_pbi_case_summary      케이스 단위 요약(카드 1장용)
-- ============================================================================

CREATE OR REPLACE VIEW forensic.vw_pbi_evidence AS
SELECT
    e.doc_type,
    COUNT(*)                        AS evidence_count,
    SUM(e.size_bytes)               AS total_bytes,
    COUNT(DISTINCT e.sha256)        AS unique_hashes,
    MIN(e.collected_at)             AS first_collected_at,
    MAX(e.collected_at)             AS last_collected_at
FROM forensic.evidence e
GROUP BY e.doc_type;

CREATE OR REPLACE VIEW forensic.vw_pbi_review_progress AS
SELECT
    d.doc_type,
    d.suggested_relevance,
    d.review_status,
    COUNT(*)                                                        AS doc_count,
    COUNT(*) FILTER (WHERE d.near_dup_cluster <> '')                AS near_dup_docs,
    COUNT(*) FILTER (WHERE d.exact_dup_group <> '')                 AS exact_dup_docs,
    COUNT(*) FILTER (WHERE d.thread_id <> '')                       AS threaded_docs,
    ROUND(AVG(d.priority), 2)                                       AS avg_priority
FROM forensic.documents d
GROUP BY d.doc_type, d.suggested_relevance, d.review_status;

CREATE OR REPLACE VIEW forensic.vw_pbi_findings AS
SELECT
    f.finding_id,
    f.category,
    f.severity,
    f.status,
    f.amount,
    f.item_count,
    COUNT(fe.evidence_id)   AS evidence_count,
    f.created_at
FROM forensic.findings f
LEFT JOIN forensic.finding_evidence fe ON fe.finding_id = f.finding_id
GROUP BY f.finding_id, f.category, f.severity, f.status, f.amount, f.item_count, f.created_at;

CREATE OR REPLACE VIEW forensic.vw_pbi_case_summary AS
SELECT
    c.case_id,
    c.case_name,
    (SELECT COUNT(*) FROM forensic.evidence e WHERE e.case_id = c.case_id)      AS evidence_count,
    (SELECT COUNT(*) FROM forensic.documents d WHERE d.case_id = c.case_id)     AS document_count,
    (SELECT COUNT(*) FROM forensic.findings f WHERE f.case_id = c.case_id)      AS finding_count,
    (SELECT COUNT(*) FROM forensic.findings f
      WHERE f.case_id = c.case_id AND f.severity = 'CRITICAL')                  AS critical_findings,
    (SELECT COUNT(*) FROM forensic.findings f
      WHERE f.case_id = c.case_id AND f.status = 'OPEN')                        AS open_findings,
    (SELECT COALESCE(SUM(f.amount), 0) FROM forensic.findings f
      WHERE f.case_id = c.case_id)                                              AS total_finding_amount
FROM forensic.cases c;
