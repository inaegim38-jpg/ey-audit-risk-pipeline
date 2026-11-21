-- ============================================================================
-- 24. PBC conformed facts and tie-out result contract
-- ============================================================================

CREATE TABLE IF NOT EXISTS audit_core.fact_pbc_gl_line (
    batch_id UUID NOT NULL REFERENCES audit_core.stg_ingestion_log(batch_id) ON DELETE CASCADE,
    engagement_id VARCHAR(40) NOT NULL REFERENCES audit_core.dim_engagement(engagement_id),
    doc_no TEXT NOT NULL,
    posting_date DATE NOT NULL,
    account_code TEXT NOT NULL,
    debit NUMERIC(20,2) NOT NULL DEFAULT 0,
    credit NUMERIC(20,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (batch_id, doc_no)
);

CREATE TABLE IF NOT EXISTS audit_core.fact_pbc_tb_balance (
    batch_id UUID NOT NULL REFERENCES audit_core.stg_ingestion_log(batch_id) ON DELETE CASCADE,
    engagement_id VARCHAR(40) NOT NULL REFERENCES audit_core.dim_engagement(engagement_id),
    account_code TEXT NOT NULL,
    debit NUMERIC(20,2) NOT NULL DEFAULT 0,
    credit NUMERIC(20,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (batch_id, account_code)
);

CREATE TABLE IF NOT EXISTS audit_core.fact_pbc_ar_item (
    batch_id UUID NOT NULL REFERENCES audit_core.stg_ingestion_log(batch_id) ON DELETE CASCADE,
    engagement_id VARCHAR(40) NOT NULL REFERENCES audit_core.dim_engagement(engagement_id),
    invoice_no TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    amount NUMERIC(20,2) NOT NULL,
    currency VARCHAR(3) NOT NULL,
    PRIMARY KEY (batch_id, invoice_no)
);

CREATE TABLE IF NOT EXISTS audit_core.fact_pbc_contract (
    batch_id UUID NOT NULL REFERENCES audit_core.stg_ingestion_log(batch_id) ON DELETE CASCADE,
    engagement_id VARCHAR(40) NOT NULL REFERENCES audit_core.dim_engagement(engagement_id),
    contract_id TEXT NOT NULL,
    vendor_id TEXT NOT NULL,
    contract_amount NUMERIC(20,2) NOT NULL,
    PRIMARY KEY (batch_id, contract_id)
);

CREATE TABLE IF NOT EXISTS audit_core.pbc_tieout_result (
    tieout_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    engagement_id VARCHAR(40) NOT NULL REFERENCES audit_core.dim_engagement(engagement_id),
    rule_id VARCHAR(30) NOT NULL,
    left_batch_id UUID REFERENCES audit_core.stg_ingestion_log(batch_id),
    right_batch_id UUID REFERENCES audit_core.stg_ingestion_log(batch_id),
    status VARCHAR(10) NOT NULL CHECK (status IN ('PASSED','FAILED','PENDING')),
    difference NUMERIC(20,2),
    item_count INT NOT NULL DEFAULT 0,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (engagement_id, rule_id, left_batch_id, right_batch_id)
);

CREATE INDEX IF NOT EXISTS idx_pbc_tieout_engagement
    ON audit_core.pbc_tieout_result (engagement_id, status, created_at DESC);

INSERT INTO audit_core.change_log
    (object_type, object_name, version, changed_by, note)
SELECT 'SCHEMA', 'PBC conformed facts and tie-out', 'PBC-2.0', 'system',
       'Normalized GL/TB/AR/contract rows and persisted tie-out results'
WHERE NOT EXISTS (
    SELECT 1 FROM audit_core.change_log
    WHERE object_name = 'PBC conformed facts and tie-out' AND version = 'PBC-2.0'
);
