-- ============================================================================
-- 26. PBC notification delivery log
-- ============================================================================
-- A request is logged only after the notification provider node succeeds.
-- Missing credentials or failed email nodes therefore remain retryable.

CREATE TABLE IF NOT EXISTS audit_core.pbc_notification_log (
    notification_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id UUID NOT NULL REFERENCES audit_core.pbc_request_queue(request_id) ON DELETE CASCADE,
    channel VARCHAR(20) NOT NULL CHECK (channel IN ('EMAIL','SLACK')),
    recipient TEXT NOT NULL,
    provider_message_id TEXT,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (request_id, channel, recipient)
);

CREATE INDEX IF NOT EXISTS idx_pbc_notification_request
    ON audit_core.pbc_notification_log (request_id, channel, sent_at DESC);

INSERT INTO audit_core.change_log
    (object_type, object_name, version, changed_by, note)
SELECT 'SCHEMA', 'PBC notification delivery log', 'PBC-3.0', 'system',
       'Idempotent post-send log for client PBC request notifications'
WHERE NOT EXISTS (
    SELECT 1 FROM audit_core.change_log
    WHERE object_name='PBC notification delivery log' AND version='PBC-3.0'
);
