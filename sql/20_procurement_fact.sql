-- ============================================================================
-- 20. Architecture v2 procurement bid/contract fact contract
-- ============================================================================

CREATE TABLE IF NOT EXISTS audit_core.fact_procurement_event (
    procurement_event_id BIGSERIAL PRIMARY KEY,
    source_payload_id BIGINT NOT NULL
        REFERENCES audit_core.stg_source_payload(payload_id),
    source_code VARCHAR(30) NOT NULL
        REFERENCES audit_core.dim_source_system(source_code),
    bid_notice_no VARCHAR(80) NOT NULL,
    bid_notice_ord VARCHAR(20),
    notice_name TEXT,
    business_division VARCHAR(80),
    notice_status VARCHAR(80),
    notice_date TIMESTAMPTZ,
    bid_begin_at TIMESTAMPTZ,
    bid_close_at TIMESTAMPTZ,
    opening_at TIMESTAMPTZ,
    demand_agency_code VARCHAR(40),
    demand_agency_name TEXT,
    notice_agency_code VARCHAR(40),
    notice_agency_name TEXT,
    estimated_amount NUMERIC(20,2),
    presumption_price NUMERIC(20,2),
    contract_method TEXT,
    source_locator TEXT,
    raw_record JSONB NOT NULL,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_code, bid_notice_no, bid_notice_ord)
);

CREATE INDEX IF NOT EXISTS idx_procurement_notice_date
    ON audit_core.fact_procurement_event (notice_date DESC);
CREATE INDEX IF NOT EXISTS idx_procurement_demand_agency
    ON audit_core.fact_procurement_event (demand_agency_code, notice_date DESC);
CREATE INDEX IF NOT EXISTS idx_procurement_payload
    ON audit_core.fact_procurement_event (source_payload_id);

INSERT INTO audit_core.change_log
    (object_type, object_name, version, changed_by, note)
SELECT 'SCHEMA', 'audit_core.fact_procurement_event', 'PROCUREMENT-1.0', 'system',
       'Normalized 나라장터 bid/notice fact with raw payload lineage'
WHERE NOT EXISTS (
    SELECT 1 FROM audit_core.change_log
    WHERE object_name = 'audit_core.fact_procurement_event' AND version = 'PROCUREMENT-1.0'
);
