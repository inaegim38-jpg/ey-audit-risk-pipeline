-- ============================================================================
-- 23. PBC file intake contract
-- ============================================================================

ALTER TABLE audit_core.stg_ingestion_log
    ADD COLUMN IF NOT EXISTS pbc_file_type VARCHAR(20);
ALTER TABLE audit_core.stg_ingestion_log
    ADD COLUMN IF NOT EXISTS source_path TEXT;
ALTER TABLE audit_core.stg_ingestion_log
    ADD COLUMN IF NOT EXISTS header_sha256 CHAR(64);

ALTER TABLE audit_core.stg_ingestion_log
    DROP CONSTRAINT IF EXISTS stg_ingestion_log_pbc_file_type_check;
ALTER TABLE audit_core.stg_ingestion_log
    ADD CONSTRAINT stg_ingestion_log_pbc_file_type_check
    CHECK (pbc_file_type IS NULL OR pbc_file_type IN ('GL','AR','CONTRACT','TB'));

CREATE INDEX IF NOT EXISTS idx_ingestion_pbc_type
    ON audit_core.stg_ingestion_log (engagement_id, pbc_file_type, ingested_at DESC);

INSERT INTO audit_core.change_log
    (object_type, object_name, version, changed_by, note)
SELECT 'SCHEMA', 'audit_core.stg_ingestion_log', 'PBC-1.0', 'system',
       'PBC file type, path, and header hash intake contract'
WHERE NOT EXISTS (
    SELECT 1 FROM audit_core.change_log
    WHERE object_name = 'audit_core.stg_ingestion_log' AND version = 'PBC-1.0'
);
