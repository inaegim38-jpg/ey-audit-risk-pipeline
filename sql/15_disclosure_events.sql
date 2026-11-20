-- ============================================================================
-- 15. 공시 이벤트 스테이징 (WF-1 수집 대상)
-- ============================================================================
-- DART 공시목록 API는 금액이 아니라 "공시 이벤트"를 준다. 정형 재무제표
-- (fact_disclosure_line)와 섞지 않고, 이벤트 전용 스테이징에 적재한다.
-- ============================================================================

CREATE TABLE IF NOT EXISTS audit_core.stg_disclosure_event (
    rcept_no       CHAR(14) PRIMARY KEY,
    dart_corp_code CHAR(8) NOT NULL,
    report_nm      TEXT NOT NULL,
    flr_nm         TEXT,
    rcept_dt       DATE,
    is_correction  BOOLEAN DEFAULT FALSE,   -- 정정공시
    is_major       BOOLEAN DEFAULT FALSE,   -- 주요사항보고
    raw            JSONB,
    loaded_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_disc_event_corp ON audit_core.stg_disclosure_event (dart_corp_code, rcept_dt DESC);

CREATE OR REPLACE VIEW audit_core.vw_disclosure_watch AS
SELECT dart_corp_code,
       rcept_no,
       rcept_dt,
       report_nm,
       flr_nm,
       is_correction,
       is_major,
       CASE WHEN is_correction THEN 'HIGH'
            WHEN is_major THEN 'MEDIUM'
            ELSE 'LOW' END AS watch_level
FROM audit_core.stg_disclosure_event
WHERE is_correction OR is_major;
