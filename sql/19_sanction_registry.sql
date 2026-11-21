-- ============================================================================
-- 19. Architecture v2 sanction/disposition fact contract
-- ============================================================================
-- Upstream-specific adapters must store raw responses in stg_source_payload first.
-- This table is deliberately source-neutral so FTC and procurement records can
-- feed the same finding/rule layer without pretending their schemas are equal.

ALTER TABLE audit_core.stg_source_payload
    ADD COLUMN IF NOT EXISTS source_record_count INTEGER;

CREATE TABLE IF NOT EXISTS audit_core.fact_sanction_event (
    sanction_event_id BIGSERIAL PRIMARY KEY,
    source_payload_id BIGINT NOT NULL
        REFERENCES audit_core.stg_source_payload(payload_id),
    source_code VARCHAR(30) NOT NULL
        REFERENCES audit_core.dim_source_system(source_code),
    source_record_id VARCHAR(160) NOT NULL,
    subject_name TEXT,
    subject_biz_reg_no CHAR(10),
    subject_corp_reg_no CHAR(13),
    action_authority VARCHAR(120) NOT NULL,
    action_date DATE,
    action_type VARCHAR(80) NOT NULL,
    action_status VARCHAR(40),
    case_no VARCHAR(120),
    penalty_amount NUMERIC(20,2),
    effective_from DATE,
    effective_to DATE,
    source_locator TEXT,
    raw_record JSONB NOT NULL,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_code, source_record_id, action_date)
);

CREATE INDEX IF NOT EXISTS idx_sanction_subject_biz
    ON audit_core.fact_sanction_event (subject_biz_reg_no, action_date DESC);
CREATE INDEX IF NOT EXISTS idx_sanction_subject_corp
    ON audit_core.fact_sanction_event (subject_corp_reg_no, action_date DESC);
CREATE INDEX IF NOT EXISTS idx_sanction_authority_date
    ON audit_core.fact_sanction_event (action_authority, action_date DESC);
CREATE INDEX IF NOT EXISTS idx_sanction_payload
    ON audit_core.fact_sanction_event (source_payload_id);

INSERT INTO audit_core.dim_source_system
    (source_code, source_name, source_type, endpoint_template, credential_env)
VALUES
    ('FTC_SANCTION', 'Fair Trade Commission sanctions/dispositions', 'SANCTION',
     NULL, 'DATA_GO_KR_API_KEY'),
    ('PPS_SANCTION', 'Procurement sanctions/dispositions', 'SANCTION',
     NULL, 'DATA_GO_KR_API_KEY'),
    ('PPS_BID_CONTRACT', '나라장터 bid/award/contract public data', 'SANCTION',
     'https://apis.data.go.kr/1230000/ao/CntrctProcssIntgOpenService',
     'DATA_GO_KR_API_KEY')
ON CONFLICT (source_code) DO UPDATE SET
    source_name = EXCLUDED.source_name,
    endpoint_template = EXCLUDED.endpoint_template,
    credential_env = EXCLUDED.credential_env;

INSERT INTO audit_core.change_log
    (object_type, object_name, version, changed_by, note)
SELECT 'SCHEMA', 'audit_core.fact_sanction_event', 'SANCTION-1.0', 'system',
       'Source-neutral sanction/disposition fact contract; upstream adapters remain fail-closed'
WHERE NOT EXISTS (
    SELECT 1 FROM audit_core.change_log
    WHERE object_name = 'audit_core.fact_sanction_event' AND version = 'SANCTION-1.0'
);
