-- ============================================================================
-- 08_forensic_case_schema.sql : 포렌식 케이스 계층(증거·문서·검토·파인딩)
-- ============================================================================
-- 기존 audit_risk 스키마(거래 데이터)와 별도로 forensic 스키마를 둔다.
--   audit_risk : 거래·증빙 정합성 검증(기존 프로젝트)
--   forensic   : 케이스 단위 증거 수집·문서 검토·파인딩(포렌식 계층)
-- 데모 파이프라인(forensic/*.py)은 이식성을 위해 SQLite에 같은 구조를 만든다.
-- 이 파일은 동일 구조를 PostgreSQL에 올릴 때 사용한다.
-- ============================================================================

DROP SCHEMA IF EXISTS forensic CASCADE;
CREATE SCHEMA forensic;

-- ---------------------------------------------------------------- 1. 케이스
CREATE TABLE forensic.cases (
    case_id        VARCHAR(32) PRIMARY KEY,
    case_name      VARCHAR(200) NOT NULL,
    client_code    VARCHAR(50),
    scope          TEXT,
    opened_at      DATE NOT NULL,
    closed_at      DATE,
    legal_hold     BOOLEAN DEFAULT TRUE,
    lead_counsel   VARCHAR(120),
    note           TEXT
);

-- ---------------------------------------------------------------- 2. 증거
CREATE TABLE forensic.evidence (
    evidence_id    VARCHAR(20) PRIMARY KEY,
    case_id        VARCHAR(32) REFERENCES forensic.cases(case_id),
    rel_path       TEXT NOT NULL,
    doc_type       VARCHAR(30),
    size_bytes     BIGINT,
    sha256         CHAR(64) NOT NULL,
    source_mtime   TIMESTAMPTZ,
    collected_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    custodian      VARCHAR(80),
    storage        VARCHAR(40) DEFAULT 'original-read-only',
    UNIQUE (case_id, rel_path)
);

CREATE TABLE forensic.custody_log (
    event_id       VARCHAR(40) PRIMARY KEY,
    evidence_id    VARCHAR(20) REFERENCES forensic.evidence(evidence_id),
    event_type     VARCHAR(30) NOT NULL,   -- COLLECTED / HASH_VERIFIED / TRANSFERRED / ACCESSED / RETURNED
    actor          VARCHAR(80),
    event_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    location       VARCHAR(80),
    note           TEXT
);

-- ---------------------------------------------------------------- 3. 문서
CREATE TABLE forensic.documents (
    doc_id              VARCHAR(20) PRIMARY KEY REFERENCES forensic.evidence(evidence_id),
    case_id             VARCHAR(32) REFERENCES forensic.cases(case_id),
    doc_type            VARCHAR(30),
    author              VARCHAR(200),
    recipients          TEXT,
    doc_date            DATE,
    subject             TEXT,
    body                TEXT,
    text_len            INT,
    exact_dup_group     VARCHAR(40),
    near_dup_cluster    VARCHAR(40),
    template_family     VARCHAR(40),
    thread_id           VARCHAR(40),
    priority            NUMERIC(6,2) DEFAULT 0,
    suggested_relevance VARCHAR(16),
    review_status       VARCHAR(20) DEFAULT 'UNREVIEWED',
    reviewer            VARCHAR(80),
    review_reason       TEXT
);

CREATE TABLE forensic.doc_tags (
    doc_id   VARCHAR(20) REFERENCES forensic.documents(doc_id),
    tag      VARCHAR(60),
    rule_id  VARCHAR(30),
    weight   NUMERIC(4,1),
    snippet  TEXT
);

CREATE TABLE forensic.review_log (
    log_id         BIGSERIAL PRIMARY KEY,
    doc_id         VARCHAR(20) REFERENCES forensic.documents(doc_id),
    reviewer       VARCHAR(80),
    decided_at     TIMESTAMPTZ DEFAULT NOW(),
    relevance      VARCHAR(20),   -- RELEVANT / NOT_RELEVANT / PRIVILEGED / NEEDS_FOLLOWUP
    reason         TEXT,
    seconds_spent  INT
);

-- ---------------------------------------------------------------- 4. 파인딩
CREATE TABLE forensic.findings (
    finding_id   VARCHAR(10) PRIMARY KEY,
    case_id      VARCHAR(32) REFERENCES forensic.cases(case_id),
    category     VARCHAR(10),
    title        TEXT NOT NULL,
    severity     VARCHAR(10) NOT NULL,
    amount       NUMERIC(18,2) DEFAULT 0,
    item_count   INT DEFAULT 0,
    description  TEXT,
    method       TEXT,
    limitation   TEXT,
    status       VARCHAR(20) DEFAULT 'OPEN',
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE forensic.finding_evidence (
    finding_id  VARCHAR(10) REFERENCES forensic.findings(finding_id),
    evidence_id VARCHAR(20) REFERENCES forensic.evidence(evidence_id),
    rel_path    TEXT,
    note        TEXT
);

-- ---------------------------------------------------------------- 5. 뷰
-- 검토 큐: 우선순위 상위 문서와 태그를 한 번에 본다.
CREATE OR REPLACE VIEW forensic.v_review_queue AS
SELECT d.doc_id,
       d.doc_type,
       d.doc_date,
       d.subject,
       d.priority,
       d.suggested_relevance,
       d.near_dup_cluster,
       d.thread_id,
       d.review_status,
       STRING_AGG(t.tag, ' | ' ORDER BY t.weight DESC) AS tags
FROM forensic.documents d
LEFT JOIN forensic.doc_tags t ON t.doc_id = d.doc_id
GROUP BY d.doc_id;

-- 파인딩별 증거 건수와 해시 검증 상태
CREATE OR REPLACE VIEW forensic.v_finding_coverage AS
SELECT f.finding_id,
       f.severity,
       f.title,
       COUNT(fe.evidence_id)                        AS evidence_count,
       COUNT(e.evidence_id) FILTER (WHERE e.sha256 IS NOT NULL) AS verified_count
FROM forensic.findings f
LEFT JOIN forensic.finding_evidence fe ON fe.finding_id = f.finding_id
LEFT JOIN forensic.evidence e ON e.evidence_id = fe.evidence_id
GROUP BY f.finding_id, f.severity, f.title;

-- ---------------------------------------------------------------- 6. 예시
INSERT INTO forensic.cases (case_id, case_name, client_code, scope, opened_at, note)
VALUES ('CASE-2026-014', '한빛물산 구매팀 법인카드·협력사 지급 제보', 'CLIENT-A',
        '2025-09-01 ~ 2026-06-30 구매·지급 관련 문서 및 카드 승인내역', DATE '2026-06-30',
        '합성 코퍼스 기반 절차 검증 케이스');
