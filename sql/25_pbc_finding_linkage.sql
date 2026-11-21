-- ============================================================================
-- 25. PBC tie-out -> candidate finding/evidence/HITL boundary
-- ============================================================================
-- Failed tie-outs create candidates only. Pending tie-outs create requests only.

INSERT INTO audit_core.rule_catalog
    (rule_id, rule_name, risk_domain, severity, threshold, description, version)
VALUES
    ('PBC-TB-01', 'GL-TB 계정별 대사 불일치', 'PBC_TIEOUT', 'HIGH',
     '{"max_abs_difference": 1}'::jsonb,
     '계정별 GL aggregate와 TB balance 차이. 원인·기간귀속·조정분개를 사람이 확인한다.', '1.0'),
    ('PBC-AR-01', 'AR 상세 대사 추가자료 요청', 'PBC_TIEOUT', 'MEDIUM',
     '{}'::jsonb,
     'AR control account mapping 또는 후속수금 자료가 없어 추가 대사가 필요한 상태.', '1.0'),
    ('PBC-CON-01', '계약 상세 대사 추가자료 요청', 'PBC_TIEOUT', 'MEDIUM',
     '{}'::jsonb,
     '계약 register와 GL/AP 또는 commitment mapping이 없어 추가 대사가 필요한 상태.', '1.0')
ON CONFLICT (rule_id) DO UPDATE SET
    rule_name=EXCLUDED.rule_name, risk_domain=EXCLUDED.risk_domain,
    severity=EXCLUDED.severity, threshold=EXCLUDED.threshold,
    description=EXCLUDED.description, version=EXCLUDED.version, updated_at=NOW();

CREATE TABLE IF NOT EXISTS audit_core.pbc_request_queue (
    request_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    engagement_id VARCHAR(40) NOT NULL REFERENCES audit_core.dim_engagement(engagement_id),
    rule_id VARCHAR(30) NOT NULL REFERENCES audit_core.rule_catalog(rule_id),
    tieout_id UUID NOT NULL REFERENCES audit_core.pbc_tieout_result(tieout_id) ON DELETE CASCADE,
    status VARCHAR(20) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','RECEIVED','CLOSED')),
    request_reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tieout_id)
);

INSERT INTO forensic.evidence
    (evidence_id, case_id, rel_path, doc_type, size_bytes, sha256, custodian, storage)
SELECT 'EV-PBC-' || left(replace(l.batch_id::text, '-', ''), 12),
       'CASE-2026-AUD', l.source_path,
       'PBC_' || l.pbc_file_type,
       NULL, l.sha256_hash, l.ingested_by, 'pbc-read-only'
FROM audit_core.stg_ingestion_log l
WHERE l.pbc_file_type IS NOT NULL
ON CONFLICT (evidence_id) DO UPDATE SET
    rel_path=EXCLUDED.rel_path, doc_type=EXCLUDED.doc_type,
    sha256=EXCLUDED.sha256, custodian=EXCLUDED.custodian, storage=EXCLUDED.storage;

DROP FUNCTION IF EXISTS audit_core.run_pbc_findings(VARCHAR);

CREATE OR REPLACE FUNCTION audit_core.run_pbc_findings(
    p_engagement_id VARCHAR DEFAULT 'ENG-2026-001'
)
RETURNS TABLE (p_rule_id VARCHAR, candidate_cnt BIGINT, request_cnt BIGINT)
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO forensic.evidence
        (evidence_id, case_id, rel_path, doc_type, size_bytes, sha256, custodian, storage)
    SELECT 'EV-PBC-' || left(replace(l.batch_id::text, '-', ''), 12),
           'CASE-2026-AUD', l.source_path, 'PBC_' || l.pbc_file_type,
           NULL, l.sha256_hash, l.ingested_by, 'pbc-read-only'
    FROM audit_core.stg_ingestion_log l
    WHERE l.engagement_id = p_engagement_id AND l.pbc_file_type IS NOT NULL
    ON CONFLICT (evidence_id) DO UPDATE SET
        rel_path=EXCLUDED.rel_path, doc_type=EXCLUDED.doc_type,
        sha256=EXCLUDED.sha256, custodian=EXCLUDED.custodian, storage=EXCLUDED.storage;

    RETURN QUERY WITH failed AS (
        INSERT INTO audit_core.fact_risk_finding
            (engagement_id, rule_id, risk_domain, severity, severity_score,
             ref_id, item_count, amount, detail, disposition)
        SELECT r.engagement_id, t.rule_id, c.risk_domain, c.severity,
               CASE WHEN t.rule_id = 'PBC-TB-01' THEN 75 ELSE 45 END,
               'PBC:' || t.tieout_id::text, t.item_count, ABS(COALESCE(t.difference, 0)),
               jsonb_build_object(
                   'tieout_id', t.tieout_id, 'left_batch_id', t.left_batch_id,
                   'right_batch_id', t.right_batch_id, 'status', t.status,
                   'difference', t.difference, 'candidate_only', true,
                   'review_required', true
               ), 'OPEN'
        FROM audit_core.pbc_tieout_result t
        JOIN audit_core.rule_catalog c ON c.rule_id = t.rule_id
        JOIN audit_core.stg_ingestion_log r ON r.batch_id = t.left_batch_id
        WHERE t.engagement_id = p_engagement_id AND t.status = 'FAILED'
        ON CONFLICT (engagement_id, rule_id, ref_id) DO UPDATE SET
            item_count=EXCLUDED.item_count, amount=EXCLUDED.amount,
            detail=EXCLUDED.detail, updated_at=NOW()
        RETURNING rule_id
    )
    SELECT f.rule_id, COUNT(*)::BIGINT, 0::BIGINT FROM failed f GROUP BY f.rule_id;

    INSERT INTO audit_core.pbc_request_queue
        (engagement_id, rule_id, tieout_id, request_reason)
    SELECT t.engagement_id, t.rule_id, t.tieout_id,
           CASE t.rule_id
             WHEN 'PBC-AR-01' THEN 'AR control account mapping 또는 후속수금 자료 필요'
             WHEN 'PBC-CON-01' THEN '계약별 GL/AP/commitment mapping 및 주요 계약 원문 필요'
             ELSE 'PBC tie-out 추가자료 필요'
           END
    FROM audit_core.pbc_tieout_result t
    WHERE t.engagement_id = p_engagement_id AND t.status = 'PENDING'
    ON CONFLICT (tieout_id) DO UPDATE SET request_reason=EXCLUDED.request_reason;

    INSERT INTO audit_core.finding_evidence (finding_id, evidence_id, ref_table, ref_pk, note)
    SELECT f.finding_id, e.evidence_id, 'pbc_tieout_result', t.tieout_id::text,
           'PBC 원본 파일 hash와 tie-out batch evidence'
    FROM audit_core.fact_risk_finding f
    JOIN audit_core.pbc_tieout_result t ON t.tieout_id = (f.detail->>'tieout_id')::uuid
    JOIN audit_core.stg_ingestion_log l ON l.batch_id IN (t.left_batch_id, t.right_batch_id)
    JOIN forensic.evidence e ON e.evidence_id = 'EV-PBC-' || left(replace(l.batch_id::text, '-', ''), 12)
    WHERE f.engagement_id = p_engagement_id AND t.status = 'FAILED'
    ON CONFLICT (finding_id, evidence_id) DO UPDATE SET
        ref_table=EXCLUDED.ref_table, ref_pk=EXCLUDED.ref_pk, note=EXCLUDED.note;

    RETURN QUERY
    SELECT c.rule_id, COUNT(DISTINCT f.finding_id), COUNT(DISTINCT q.request_id)
    FROM audit_core.rule_catalog c
    LEFT JOIN audit_core.fact_risk_finding f
      ON f.rule_id=c.rule_id AND f.engagement_id=p_engagement_id
    LEFT JOIN audit_core.pbc_request_queue q
      ON q.rule_id=c.rule_id AND q.engagement_id=p_engagement_id
    WHERE c.rule_id LIKE 'PBC-%'
    GROUP BY c.rule_id ORDER BY c.rule_id;
END;
$$;

INSERT INTO audit_core.change_log
    (object_type, object_name, version, changed_by, note)
SELECT 'RULE', 'pbc_finding_linkage', '1.0', 'system',
       'Failed PBC tie-outs create candidate findings; pending results create request queue items'
WHERE NOT EXISTS (
    SELECT 1 FROM audit_core.change_log
    WHERE object_name='pbc_finding_linkage' AND version='1.0'
);
