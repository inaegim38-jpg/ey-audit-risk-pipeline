-- ============================================================================
-- 18. Architecture v2 source registry and raw payload lineage
-- ============================================================================

CREATE TABLE IF NOT EXISTS audit_core.dim_source_system (
    source_code        VARCHAR(30) PRIMARY KEY,
    source_name        TEXT NOT NULL,
    source_type        VARCHAR(20) NOT NULL
                       CHECK (source_type IN ('REGULATORY','MACRO','SANCTION','PBC','AI')),
    endpoint_template  TEXT,
    credential_env     VARCHAR(80),
    is_active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS audit_core.stg_source_payload (
    payload_id         BIGSERIAL PRIMARY KEY,
    source_code        VARCHAR(30) NOT NULL REFERENCES audit_core.dim_source_system(source_code),
    engagement_id      VARCHAR(40) REFERENCES audit_core.dim_engagement(engagement_id),
    request_fingerprint CHAR(64) NOT NULL,
    requested_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    observed_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    period_start       DATE,
    period_end         DATE,
    unit_code          VARCHAR(30),
    currency_code      CHAR(3),
    http_status        INTEGER NOT NULL,
    response_status    VARCHAR(30),
    raw_payload        JSONB NOT NULL,
    payload_sha256     CHAR(64) NOT NULL,
    parse_status       VARCHAR(20) NOT NULL DEFAULT 'RECEIVED'
                       CHECK (parse_status IN ('RECEIVED','PARSED','REJECTED')),
    parse_note         TEXT,
    UNIQUE (source_code, request_fingerprint, payload_sha256)
);

CREATE INDEX IF NOT EXISTS idx_source_payload_source_time
    ON audit_core.stg_source_payload (source_code, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_source_payload_engagement
    ON audit_core.stg_source_payload (engagement_id, source_code, observed_at DESC);

ALTER TABLE audit_core.stg_disclosure_event
    ADD COLUMN IF NOT EXISTS source_payload_id BIGINT
    REFERENCES audit_core.stg_source_payload(payload_id);
ALTER TABLE audit_core.fact_macro_ecos
    ADD COLUMN IF NOT EXISTS source_payload_id BIGINT
    REFERENCES audit_core.stg_source_payload(payload_id);

INSERT INTO audit_core.dim_source_system
    (source_code, source_name, source_type, endpoint_template, credential_env)
VALUES
    ('DART_DISCLOSURE', 'OpenDART disclosure list', 'REGULATORY',
     'https://opendart.fss.or.kr/api/list.json', 'DART_API_KEY'),
    ('ECOS_STAT', 'Bank of Korea ECOS statistics', 'MACRO',
     'https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/{start}/{end}/{stat_code}/{cycle}/{from}/{to}/{item}',
     'ECOS_API_KEY'),
    ('FTC_SANCTION', 'Fair Trade Commission / procurement sanctions', 'SANCTION',
     NULL, 'FTC_API_KEY'),
    ('PBC_FILE', 'PBC GL / AR / contract files', 'PBC', NULL, NULL),
    ('AI_EXTRACTION', 'Canvas / Solar extraction JSON', 'AI', NULL, 'AI_PROVIDER_KEY')
ON CONFLICT (source_code) DO UPDATE SET
    source_name = EXCLUDED.source_name,
    endpoint_template = EXCLUDED.endpoint_template,
    credential_env = EXCLUDED.credential_env;

INSERT INTO audit_core.change_log
    (object_type, object_name, version, changed_by, note)
SELECT 'SCHEMA', 'audit_core.source_registry', 'SOURCE-1.0', 'system',
       'Architecture v2 raw source payload lineage and credential registry'
WHERE NOT EXISTS (
    SELECT 1 FROM audit_core.change_log
    WHERE object_name = 'audit_core.source_registry' AND version = 'SOURCE-1.0'
);
