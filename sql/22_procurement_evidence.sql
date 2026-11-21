-- ============================================================================
-- 22. Procurement raw-payload evidence linkage
-- ============================================================================
-- The raw API payload is evidence, not a conclusion. Every PPS candidate points
-- to the payload hash and the normalized procurement record locator.

INSERT INTO forensic.evidence
    (evidence_id, case_id, rel_path, doc_type, size_bytes, sha256, custodian, storage)
SELECT 'EV-PPS-' || lpad(p.payload_id::text, 6, '0'),
       'CASE-2026-AUD',
       'source://stg_source_payload/' || p.payload_id,
       'PPS_API_JSON',
       octet_length(p.raw_payload::text),
       p.payload_sha256,
       'system',
       'raw-api-read-only'
FROM audit_core.stg_source_payload p
WHERE p.source_code = 'PPS_BID_CONTRACT'
ON CONFLICT (evidence_id) DO UPDATE SET
    rel_path = EXCLUDED.rel_path,
    size_bytes = EXCLUDED.size_bytes,
    sha256 = EXCLUDED.sha256,
    storage = EXCLUDED.storage;

INSERT INTO audit_core.finding_evidence
    (finding_id, evidence_id, ref_table, ref_pk, note)
SELECT f.finding_id,
       'EV-PPS-' || lpad((f.detail->>'source_payload_id')::bigint::text, 6, '0'),
       'fact_procurement_event',
       f.detail->>'bid_notice_no',
       '조달 API 원문 payload 및 정규화 공고 레코드'
FROM audit_core.fact_risk_finding f
WHERE substring(f.rule_id, 1, 4) = 'PPS-'
  AND f.detail ? 'source_payload_id'
ON CONFLICT (finding_id, evidence_id) DO UPDATE SET
    ref_table = EXCLUDED.ref_table,
    ref_pk = EXCLUDED.ref_pk,
    note = EXCLUDED.note;

INSERT INTO audit_core.change_log
    (object_type, object_name, version, changed_by, note)
SELECT 'EVIDENCE', 'procurement_payload_link', '1.0', 'system',
       'PPS candidate findings linked to raw API payload evidence'
WHERE NOT EXISTS (
    SELECT 1 FROM audit_core.change_log
    WHERE object_name = 'procurement_payload_link' AND version = '1.0'
);
