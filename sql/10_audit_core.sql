-- ============================================================================
-- 10. audit_core : Aegis-Guard 3-Layer 스키마
-- ============================================================================
-- 기존 스키마와의 역할 분담
--   audit_risk : 거래·증빙 정합성 검증(기존 프로젝트, tx 단위)
--   forensic   : 케이스 단위 증거 수집·문서 검토·파인딩(포렌식 계층)
--   audit_core : 감사·리스크 검증의 기준정보·사실·판정(이 파일)
--
-- 설계 원칙
--   1) 기준정보(Layer 1)는 이력(valid_from/valid_to)을 보존한다.
--   2) 사실(Layer 2)은 적재 배치와 연결되고, 키는 업무 그레인을 따른다.
--   3) 판정(Layer 3)은 룰 ID·증거·상태전이를 남겨 재현 가능하게 한다.
-- ============================================================================

DROP SCHEMA IF EXISTS audit_core CASCADE;
CREATE SCHEMA audit_core;

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ============================================================ Layer 1. 기준정보
-- 법인번호·사업자번호가 1차 키, 명칭은 trgm 보조 키로만 쓴다.
CREATE TABLE audit_core.dim_entity (
    entity_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    corp_reg_no      CHAR(13),
    biz_reg_no       CHAR(10),
    dart_corp_code   CHAR(8),
    name             TEXT NOT NULL,
    name_norm        TEXT NOT NULL,                 -- (주)/㈜/공백 제거
    ksic_code        VARCHAR(5),
    biz_status       VARCHAR(10) DEFAULT 'ACTIVE'
                     CHECK (biz_status IN ('ACTIVE','DORMANT','CLOSED')),
    is_related_party BOOLEAN DEFAULT FALSE,
    related_source   TEXT,                          -- DART / 공정위 / 수동
    country_code     CHAR(2) DEFAULT 'KR',
    valid_from       DATE NOT NULL DEFAULT CURRENT_DATE,
    valid_to         DATE,
    UNIQUE (corp_reg_no, valid_from),
    UNIQUE (biz_reg_no, valid_from)
);
CREATE INDEX idx_entity_trgm ON audit_core.dim_entity
    USING GIN (name_norm gin_trgm_ops);

-- 계정체계 개편 이력을 보존하려면 valid_from이 PK에 들어가야 한다.
CREATE TABLE audit_core.dim_coa_mapping (
    source_system   VARCHAR(30) NOT NULL,           -- SAP / DOUZONE / KAGGLE
    local_acct_code VARCHAR(30) NOT NULL,
    std_kifrs_code  VARCHAR(30) NOT NULL,
    fs_category     VARCHAR(20),                    -- REVENUE_FX / AR_FX / INVENTORY / PROVISION
    valid_from      DATE NOT NULL DEFAULT CURRENT_DATE,
    valid_to        DATE,
    PRIMARY KEY (source_system, local_acct_code, valid_from)
);

CREATE TABLE audit_core.dim_engagement (
    engagement_id   VARCHAR(40) PRIMARY KEY,
    entity_id       UUID NOT NULL REFERENCES audit_core.dim_entity(entity_id),
    engagement_type VARCHAR(20) NOT NULL
        CHECK (engagement_type IN ('AUDIT','FORENSIC','CONSULTING')),
    fiscal_year     SMALLINT NOT NULL,
    partner_id      TEXT,
    status          VARCHAR(20) DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE','CLOSED','ON_HOLD')),
    opened_at       DATE DEFAULT CURRENT_DATE
);

-- 업종 분포(재고 평가손실률 등)를 룰 임계값으로 쓰기 위한 참조 테이블
CREATE TABLE audit_core.dim_industry_stats (
    ksic_code       VARCHAR(5) NOT NULL,
    metric          VARCHAR(40) NOT NULL,           -- INVENTORY_LOSS_RATIO 등
    avg_ratio       NUMERIC(9,4) NOT NULL,
    sd_ratio        NUMERIC(9,4) NOT NULL,
    sample_n        INT,
    as_of           DATE NOT NULL DEFAULT CURRENT_DATE,
    PRIMARY KEY (ksic_code, metric, as_of)
);

-- ============================================================ Layer 2. 적재·사실
-- 해시 단독 유니크는 같은 PBC 파일이 다른 프로젝트에서 재사용될 때 적재를 막는다.
CREATE TABLE audit_core.stg_ingestion_log (
    batch_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    engagement_id     VARCHAR(40) NOT NULL
                      REFERENCES audit_core.dim_engagement(engagement_id),
    file_name         TEXT NOT NULL,
    sha256_hash       CHAR(64) NOT NULL,
    row_count         INT NOT NULL DEFAULT 0,
    debit_credit_diff NUMERIC(18,2) DEFAULT 0,
    tieout_status     VARCHAR(10) DEFAULT 'PENDING'
        CHECK (tieout_status IN ('PASSED','FAILED','PENDING')),
    ingested_by       TEXT,
    ingested_at       TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (engagement_id, sha256_hash)
);

-- 공시 1건 : 계정 N건. 접수번호 단독 유니크 금지.
CREATE TABLE audit_core.fact_disclosure_line (
    disclosure_id   BIGSERIAL PRIMARY KEY,
    rcept_no        CHAR(14) NOT NULL,
    dart_corp_code  CHAR(8) NOT NULL,
    fiscal_period   CHAR(6) NOT NULL,               -- YYYYMM
    fs_div          CHAR(1) NOT NULL,               -- C(연결) / O(별도)
    std_kifrs_code  VARCHAR(30) NOT NULL,
    amount          NUMERIC(20,2),
    footnote_json   JSONB,                          -- AI 추출 주석(특수관계자·우발부채)
    corrected_yn    BOOLEAN DEFAULT FALSE,
    synced_at       TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (rcept_no, fs_div, std_kifrs_code)
);

CREATE TABLE audit_core.fact_macro_ecos (
    obs_date       DATE NOT NULL,
    indicator_code VARCHAR(30) NOT NULL,            -- FX_USD / BASE_RATE / DELINQ_RATE
    obs_value      NUMERIC(14,4) NOT NULL,
    PRIMARY KEY (obs_date, indicator_code)
);

-- 전표는 월 단위 파티션. 수천만 건이 쌓여도 룰 SQL이 같은 속도로 돈다.
CREATE TABLE audit_core.fact_journal (
    je_id           BIGSERIAL,
    engagement_id   VARCHAR(40) NOT NULL,
    fiscal_period   CHAR(6) NOT NULL,
    posting_date    DATE NOT NULL,
    posting_time    TIME,
    doc_no          VARCHAR(40) NOT NULL,
    std_kifrs_code  VARCHAR(30) NOT NULL,
    counterparty_id UUID REFERENCES audit_core.dim_entity(entity_id),
    currency_code   CHAR(3) DEFAULT 'KRW',
    fx_amount       NUMERIC(20,2),
    krw_amount      NUMERIC(20,2) NOT NULL,
    preparer_id     TEXT,
    approver_id     TEXT,
    description     TEXT,
    source_batch_id UUID REFERENCES audit_core.stg_ingestion_log(batch_id),
    PRIMARY KEY (je_id, fiscal_period)
) PARTITION BY RANGE (fiscal_period);

CREATE TABLE audit_core.fact_journal_2026m01 PARTITION OF audit_core.fact_journal
    FOR VALUES FROM ('202601') TO ('202602');
CREATE TABLE audit_core.fact_journal_2026m02 PARTITION OF audit_core.fact_journal
    FOR VALUES FROM ('202602') TO ('202603');
CREATE TABLE audit_core.fact_journal_2026m03 PARTITION OF audit_core.fact_journal
    FOR VALUES FROM ('202603') TO ('202604');
CREATE TABLE audit_core.fact_journal_default PARTITION OF audit_core.fact_journal DEFAULT;

CREATE INDEX idx_journal_eng_date ON audit_core.fact_journal (engagement_id, posting_date);
CREATE INDEX idx_journal_code ON audit_core.fact_journal (std_kifrs_code);
CREATE INDEX idx_journal_doc ON audit_core.fact_journal (doc_no);

-- ============================================================ Layer 3. AI QA·판정
-- 프롬프트·모델 버전과 원문 해시를 남겨야 "그때 그 조건"으로 재현할 수 있다.
CREATE TABLE audit_core.fact_ai_qa_run (
    qa_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    engagement_id    VARCHAR(40) NOT NULL,
    agent_name       VARCHAR(50) NOT NULL,          -- EY_CANVAS / SOLAR_LLM
    model_version    VARCHAR(40),
    prompt_version   VARCHAR(20),
    source_doc_ref   TEXT NOT NULL,
    source_sha256    CHAR(64) NOT NULL,
    ai_json          JSONB NOT NULL,
    truth_json       JSONB,
    field_total      SMALLINT,
    field_exact      SMALLINT,
    exact_match_rate NUMERIC(5,2),
    hallucination_yn BOOLEAN DEFAULT FALSE,
    reviewer_id      TEXT,
    qa_status        VARCHAR(20) DEFAULT 'PENDING_REVIEW'
        CHECK (qa_status IN ('PENDING_REVIEW','APPROVED','REJECTED','ESCALATED')),
    created_at       TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_qa_eng ON audit_core.fact_ai_qa_run (engagement_id, exact_match_rate);

-- 룰 카탈로그: 룰 ID·버전·임계값을 코드 밖에서 관리한다.
CREATE TABLE audit_core.rule_catalog (
    rule_id      VARCHAR(20) PRIMARY KEY,
    rule_name    VARCHAR(120) NOT NULL,
    risk_domain  VARCHAR(30) NOT NULL,
    severity     VARCHAR(10) NOT NULL CHECK (severity IN ('CRITICAL','HIGH','MEDIUM','LOW')),
    threshold    JSONB NOT NULL DEFAULT '{}'::jsonb,
    description  TEXT,
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    version      VARCHAR(10) NOT NULL DEFAULT '1.0',
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE audit_core.fact_risk_finding (
    finding_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    engagement_id  VARCHAR(40) NOT NULL
                   REFERENCES audit_core.dim_engagement(engagement_id),
    rule_id        VARCHAR(20) NOT NULL REFERENCES audit_core.rule_catalog(rule_id),
    risk_domain    VARCHAR(30) NOT NULL,
    severity       VARCHAR(10) NOT NULL
        CHECK (severity IN ('CRITICAL','HIGH','MEDIUM','LOW')),
    severity_score NUMERIC(5,2),
    ref_id         VARCHAR(120) NOT NULL,           -- 룰이 지목한 대상(문서·계정·기간)
    item_count     INT NOT NULL DEFAULT 1,
    amount         NUMERIC(20,2),
    detail         JSONB NOT NULL DEFAULT '{}'::jsonb,
    disposition    VARCHAR(25) NOT NULL DEFAULT 'OPEN'
        CHECK (disposition IN ('OPEN','IN_REVIEW','EXPLAINED','ESCALATED','FALSE_POSITIVE')),
    reviewer_id    TEXT,
    reviewed_at    TIMESTAMPTZ,
    workpaper_id   UUID,
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    updated_at     TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (engagement_id, rule_id, ref_id)
);
CREATE INDEX idx_finding_eng ON audit_core.fact_risk_finding (engagement_id, disposition);
CREATE INDEX idx_finding_sev ON audit_core.fact_risk_finding (severity, risk_domain);

-- 증거 연결은 다대다. JSONB는 화면 표시용 스냅샷으로만 쓴다.
CREATE TABLE audit_core.finding_evidence (
    finding_id  UUID NOT NULL
        REFERENCES audit_core.fact_risk_finding(finding_id) ON DELETE CASCADE,
    evidence_id VARCHAR(20) NOT NULL
        REFERENCES forensic.evidence(evidence_id),
    ref_table   VARCHAR(40),
    ref_pk      TEXT,
    note        TEXT,
    PRIMARY KEY (finding_id, evidence_id)
);

-- 룰·프롬프트·스키마 변경 이력. "당시 룰"을 설명하기 위한 최소 장치.
CREATE TABLE audit_core.change_log (
    change_id   BIGSERIAL PRIMARY KEY,
    object_type VARCHAR(20) NOT NULL,               -- RULE / PROMPT / SCHEMA
    object_name VARCHAR(120) NOT NULL,
    version     VARCHAR(20) NOT NULL,
    changed_by  TEXT,
    changed_at  TIMESTAMPTZ DEFAULT NOW(),
    note        TEXT
);

COMMENT ON SCHEMA audit_core IS 'Aegis-Guard 3-Layer: 기준정보·사실·판정';
COMMENT ON TABLE audit_core.fact_risk_finding IS '룰 실행 결과. (engagement_id, rule_id, ref_id) 멱등';
