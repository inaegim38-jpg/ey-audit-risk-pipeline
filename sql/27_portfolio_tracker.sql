-- ============================================================================
-- 27_portfolio_tracker.sql : 거래·증빙 정합성 검증과 검토 이슈 트래커 (portfolio 스키마)
--
-- 목적: 자료 접수 → 정규화 → DQ/증빙/이상 후보 생성 → 검토·이슈 상태 추적 → Power BI mart
-- 원칙
--   1. 기존 audit_risk / forensic / audit_core 객체는 건드리지 않는다(별도 스키마).
--   2. 결과는 '후보'다. 부정 확정 표현을 쓰지 않으며 최종 판정은 reviewer가 한다.
--   3. 임계값은 코드가 아니라 rule_param 테이블에 둔다. 값이 없으면 fail-closed.
--   4. run_checks()는 멱등이다. 재실행해도 건수가 늘지 않고, reviewer가 바꾼 상태는 덮어쓰지 않는다.
--   5. 이 파일은 여러 번 실행해도 안전하다(IF NOT EXISTS / OR REPLACE / ON CONFLICT).
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS portfolio;

-- ----------------------------------------------------------------------------
-- 0. 룰 카탈로그와 파라미터 (임계값은 DB에 둔다)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS portfolio.rule_catalog (
    rule_id       TEXT PRIMARY KEY,
    rule_name     TEXT NOT NULL,
    finding_type  TEXT NOT NULL,
    severity      TEXT NOT NULL CHECK (severity IN ('CRITICAL','HIGH','MEDIUM','LOW')),
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    description   TEXT
);

CREATE TABLE IF NOT EXISTS portfolio.rule_param (
    rule_id      TEXT NOT NULL REFERENCES portfolio.rule_catalog(rule_id),
    param_name   TEXT NOT NULL,
    param_value  NUMERIC NOT NULL,
    description  TEXT,
    PRIMARY KEY (rule_id, param_name)
);

INSERT INTO portfolio.rule_catalog (rule_id, rule_name, finding_type, severity, description) VALUES
 ('DQ_00', '접수 파일 검증 실패',      'INTAKE_VALIDATION',      'HIGH',     '격리(QUARANTINED) 또는 거부(REJECTED)된 수신 파일'),
 ('DQ_01', '필수값 누락',              'REQUIRED_FIELD_MISSING', 'HIGH',     '증빙 ID·거래처·거래일시 중 하나 이상 누락'),
 ('DQ_02', '유효하지 않은 금액',        'INVALID_AMOUNT',         'HIGH',     '금액이 없거나 0 이하'),
 ('DQ_03', '중복 문서 해시',            'DUPLICATE_DOCUMENT',     'CRITICAL', '이미 접수된 문서와 SHA-256이 동일한 문서'),
 ('EV_01', '증빙 문서 미수신',          'DOCUMENT_MISSING',       'CRITICAL', '고액 거래가 참조한 증빙 문서가 접수되지 않음'),
 ('EV_02', '거래-증빙 금액 불일치',     'AMOUNT_MISMATCH',        'CRITICAL', '거래 금액과 증빙 금액의 차이가 허용 오차 초과'),
 ('EV_03', '거래-증빙 일자 괴리',       'DATE_MISMATCH',          'MEDIUM',   '증빙 일자가 거래일과 허용 범위를 벗어남'),
 ('AN_02', '근접 반복·분할 결제 후보',  'SPLIT_PAYMENT_CANDIDATE','CRITICAL', '같은 거래처·같은 날 여러 건의 합계가 한도 이상'),
 ('AN_03', '업무 외 시간 후보',         'OFF_HOURS_CANDIDATE',    'MEDIUM',   '주말 또는 심야의 고액 거래'),
 ('AN_04', '근접 일자 동일 금액 반복',  'NEAR_DATE_REPEAT',       'MEDIUM',   '같은 거래처·같은 금액이 가까운 일자에 반복')
ON CONFLICT (rule_id) DO NOTHING;

INSERT INTO portfolio.rule_param (rule_id, param_name, param_value, description) VALUES
 ('EV_01', 'amount_threshold', 500000,  '이 금액 이상 거래는 증빙이 접수되어야 함(원)'),
 ('EV_02', 'amount_tolerance', 100,     '거래-증빙 금액 허용 오차(원)'),
 ('EV_03', 'future_days',      7,       '증빙 일자가 거래일보다 이만큼 이후면 괴리(일)'),
 ('EV_03', 'past_days',        30,      '증빙 일자가 거래일보다 이만큼 이전이면 괴리(일)'),
 ('AN_02', 'band_low',         400000,  '분할 결제로 볼 개별 금액 하한(원, 이상)'),
 ('AN_02', 'limit_amount',     1000000, '한도(원). 개별 금액은 이 값 미만이고 합계는 이 값 이상이면 후보'),
 ('AN_02', 'min_count',        2,       '분할 결제로 볼 최소 건수'),
 ('AN_03', 'amount_threshold', 500000,  '업무 외 시간 점검 대상 최소 금액(원)'),
 ('AN_03', 'night_start_hour', 23,      '이 시각(시) 이후를 심야로 간주'),
 ('AN_03', 'night_end_hour',   5,       '이 시각(시) 이전을 심야로 간주'),
 ('AN_04', 'repeat_days',      3,       '같은 거래처·같은 금액이 이 일수 이내에 반복되면 후보')
ON CONFLICT (rule_id, param_name) DO NOTHING;

-- ----------------------------------------------------------------------------
-- 1. 수신 파일
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS portfolio.source_file (
    file_id        BIGSERIAL PRIMARY KEY,
    engagement_id  TEXT NOT NULL CHECK (btrim(engagement_id) <> ''),
    file_name      TEXT NOT NULL,
    file_type      TEXT NOT NULL,
    sha256_hash    TEXT NOT NULL CHECK (sha256_hash ~ '^[0-9a-f]{64}$'),
    row_count      INTEGER NOT NULL CHECK (row_count >= 0),
    intake_status  TEXT NOT NULL DEFAULT 'RECEIVED'
                   CHECK (intake_status IN ('RECEIVED','PASSED','QUARANTINED','REJECTED')),
    source_path    TEXT NOT NULL,
    received_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (engagement_id, sha256_hash)
);

-- ----------------------------------------------------------------------------
-- 2. 정규화된 거래
--    transaction_date는 시각까지 담는 timestamp다(업무 외 시간 점검에 필요).
--    amount·document_id·counterparty는 NULL을 허용한다. 비어 있는 값을 적재 단계에서
--    버리지 않고 DQ_01·DQ_02 후보로 남기기 위해서다.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS portfolio.transaction (
    engagement_id     TEXT NOT NULL,
    transaction_id    TEXT NOT NULL,
    file_id           BIGINT NOT NULL REFERENCES portfolio.source_file(file_id),
    transaction_date  TIMESTAMP,
    amount            NUMERIC(18,2),
    counterparty      TEXT,
    document_id       TEXT,
    transaction_hash  TEXT NOT NULL,
    raw_reference     TEXT,
    loaded_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (engagement_id, transaction_id)
);
CREATE INDEX IF NOT EXISTS ix_pf_tx_counterparty ON portfolio.transaction (engagement_id, counterparty, transaction_date);

CREATE OR REPLACE FUNCTION portfolio.fill_transaction_hash() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.transaction_hash IS NULL OR btrim(NEW.transaction_hash) = '' THEN
        NEW.transaction_hash := encode(sha256(convert_to(concat_ws('|',
            NEW.engagement_id, NEW.transaction_id, NEW.transaction_date::text, NEW.amount::text,
            NEW.counterparty, NEW.document_id), 'UTF8')), 'hex');
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_pf_transaction_hash ON portfolio.transaction;
CREATE TRIGGER trg_pf_transaction_hash BEFORE INSERT ON portfolio.transaction
    FOR EACH ROW EXECUTE FUNCTION portfolio.fill_transaction_hash();

-- ----------------------------------------------------------------------------
-- 3. 증빙 문서
--    같은 engagement에서 같은 document_hash를 가진 '원본'은 하나만 존재할 수 있다(부분 유니크 인덱스).
--    두 번째 이후 문서는 거부하지 않고 duplicate_of에 원본 ID를 기록해 DQ_03 후보로 남긴다.
--    거부하면 '중복 문서가 들어왔다'는 사실이 기록에서 사라지기 때문이다.
--    duplicate_of는 트리거가 항상 다시 계산하므로 호출자가 값을 위조할 수 없다.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS portfolio.document (
    engagement_id    TEXT NOT NULL,
    document_id      TEXT NOT NULL,
    file_id          BIGINT NOT NULL REFERENCES portfolio.source_file(file_id),
    document_type    TEXT NOT NULL,
    document_hash    TEXT NOT NULL CHECK (document_hash ~ '^[0-9a-f]{64}$'),
    document_date    DATE,
    document_amount  NUMERIC(18,2),
    received_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    duplicate_of     TEXT,
    PRIMARY KEY (engagement_id, document_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_pf_document_hash_original
    ON portfolio.document (engagement_id, document_hash) WHERE duplicate_of IS NULL;

CREATE OR REPLACE FUNCTION portfolio.mark_duplicate_document() RETURNS TRIGGER AS $$
BEGIN
    SELECT d.document_id INTO NEW.duplicate_of
      FROM portfolio.document d
     WHERE d.engagement_id = NEW.engagement_id
       AND d.document_hash = NEW.document_hash
       AND d.duplicate_of IS NULL
       AND d.document_id <> NEW.document_id
     ORDER BY d.received_at, d.document_id
     LIMIT 1;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_pf_document_duplicate ON portfolio.document;
CREATE TRIGGER trg_pf_document_duplicate BEFORE INSERT ON portfolio.document
    FOR EACH ROW EXECUTE FUNCTION portfolio.mark_duplicate_document();

-- ----------------------------------------------------------------------------
-- 4. 검증 후보와 검토 항목, 이슈, 갱신 로그
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS portfolio.dq_finding (
    finding_id     BIGSERIAL PRIMARY KEY,
    engagement_id  TEXT NOT NULL,
    file_id        BIGINT REFERENCES portfolio.source_file(file_id),
    transaction_id TEXT,
    document_id    TEXT,
    rule_id        TEXT NOT NULL REFERENCES portfolio.rule_catalog(rule_id),
    finding_type   TEXT NOT NULL,
    severity       TEXT NOT NULL CHECK (severity IN ('CRITICAL','HIGH','MEDIUM','LOW')),
    amount         NUMERIC(18,2) NOT NULL DEFAULT 0,
    detail         TEXT,
    status         TEXT NOT NULL DEFAULT 'OPEN'
                   CHECK (status IN ('OPEN','IN_REVIEW','RESOLVED','FALSE_POSITIVE')),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 같은 대상에 같은 룰이 두 번 기록되지 않게 하는 식별 키
CREATE UNIQUE INDEX IF NOT EXISTS ux_pf_dq_finding_identity ON portfolio.dq_finding
    (engagement_id, rule_id, (COALESCE(transaction_id, '')), (COALESCE(document_id, '')), (COALESCE(file_id, 0)));

CREATE TABLE IF NOT EXISTS portfolio.review_item (
    review_item_id      BIGSERIAL PRIMARY KEY,
    engagement_id       TEXT NOT NULL,
    finding_id          BIGINT NOT NULL UNIQUE REFERENCES portfolio.dq_finding(finding_id),
    document_id         TEXT,
    suggested_priority  INTEGER NOT NULL CHECK (suggested_priority BETWEEN 0 AND 100),
    review_status       TEXT NOT NULL DEFAULT 'UNREVIEWED'
                        CHECK (review_status IN ('UNREVIEWED','IN_REVIEW','RESOLVED','FALSE_POSITIVE')),
    reviewer            TEXT,
    reviewed_at         TIMESTAMPTZ,
    review_note         TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS portfolio.issue_log (
    issue_id               BIGSERIAL PRIMARY KEY,
    engagement_id          TEXT NOT NULL,
    review_item_id         BIGINT NOT NULL REFERENCES portfolio.review_item(review_item_id),
    issue_type             TEXT NOT NULL,
    request_reason         TEXT,
    owner                  TEXT,
    due_date               DATE,
    disposition            TEXT NOT NULL DEFAULT 'PROPOSED'
                           CHECK (disposition IN ('PROPOSED','REQUESTED','RESOLVED','CLOSED_NO_ACTION')),
    resolution_note        TEXT,
    deliverable_reflected  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at            TIMESTAMPTZ,
    UNIQUE (review_item_id, issue_type)
);

CREATE TABLE IF NOT EXISTS portfolio.refresh_log (
    refresh_id       BIGSERIAL PRIMARY KEY,
    dataset_name     TEXT NOT NULL,
    requested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at     TIMESTAMPTZ,
    status           TEXT NOT NULL CHECK (status IN ('REQUESTED','SUCCEEDED','FAILED','MOCKED')),
    provider_run_id  TEXT,
    error_message    TEXT
);

-- ----------------------------------------------------------------------------
-- 5. 헬퍼 함수
-- ----------------------------------------------------------------------------
-- 파라미터가 없으면 조용히 기본값으로 대체하지 않고 실패한다(fail-closed).
CREATE OR REPLACE FUNCTION portfolio.param(p_rule TEXT, p_name TEXT) RETURNS NUMERIC AS $$
DECLARE
    v NUMERIC;
BEGIN
    SELECT param_value INTO v FROM portfolio.rule_param WHERE rule_id = p_rule AND param_name = p_name;
    IF v IS NULL THEN
        RAISE EXCEPTION 'missing rule_param %.%', p_rule, p_name;
    END IF;
    RETURN v;
END;
$$ LANGUAGE plpgsql STABLE;

-- 검토 우선순위 점수(0~100, 높을수록 먼저 검토). 심각도 기준점 + 금액 가산(최대 19).
CREATE OR REPLACE FUNCTION portfolio.priority_score(p_severity TEXT, p_amount NUMERIC) RETURNS INTEGER AS $$
    SELECT (CASE p_severity WHEN 'CRITICAL' THEN 80 WHEN 'HIGH' THEN 60 WHEN 'MEDIUM' THEN 40 ELSE 20 END)
         + LEAST(19, FLOOR(COALESCE(ABS(p_amount), 0) / 200000))::INTEGER;
$$ LANGUAGE sql IMMUTABLE;

-- ----------------------------------------------------------------------------
-- 6. 검증 실행: run_checks(engagement, [룰 목록])
--    결과는 모두 '후보'다. 상태는 OPEN으로만 만들고, 판정은 set_review_status()를 통해 사람이 바꾼다.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION portfolio.run_checks(p_engagement TEXT, p_rules TEXT[] DEFAULT NULL)
RETURNS TABLE (rule_id TEXT, inserted_count INTEGER, updated_count INTEGER)
LANGUAGE plpgsql AS $fn$
#variable_conflict use_column
DECLARE
    v_amount_ev01   NUMERIC;
    v_tol           NUMERIC;
    v_future        NUMERIC;
    v_past          NUMERIC;
    v_band_low      NUMERIC;
    v_limit         NUMERIC;
    v_min_count     NUMERIC;
    v_amount_an03   NUMERIC;
    v_night_start   NUMERIC;
    v_night_end     NUMERIC;
    v_repeat_days   NUMERIC;
BEGIN
    IF p_engagement IS NULL OR btrim(p_engagement) = '' THEN
        RAISE EXCEPTION 'engagement_id is required; refusing to run without scope';
    END IF;

    CREATE TEMP TABLE IF NOT EXISTS _pf_cand (
        rule_id TEXT, file_id BIGINT, transaction_id TEXT, document_id TEXT, amount NUMERIC, detail TEXT
    ) ON COMMIT DROP;
    TRUNCATE _pf_cand;

    -- DQ_00 접수 파일 검증 실패
    IF (p_rules IS NULL OR 'DQ_00' = ANY(p_rules)) AND EXISTS (SELECT 1 FROM portfolio.rule_catalog rc WHERE rc.rule_id = 'DQ_00' AND rc.is_active) THEN
        INSERT INTO _pf_cand
        SELECT 'DQ_00', sf.file_id, NULL, NULL, 0,
               format('수신 파일 %s 상태 %s (행수 %s)', sf.file_name, sf.intake_status, sf.row_count)
          FROM portfolio.source_file sf
         WHERE sf.engagement_id = p_engagement AND sf.intake_status IN ('QUARANTINED', 'REJECTED');
    END IF;

    -- DQ_01 필수값 누락 (증빙 ID, 거래처, 거래일시)
    IF (p_rules IS NULL OR 'DQ_01' = ANY(p_rules)) AND EXISTS (SELECT 1 FROM portfolio.rule_catalog rc WHERE rc.rule_id = 'DQ_01' AND rc.is_active) THEN
        INSERT INTO _pf_cand
        SELECT 'DQ_01', t.file_id, t.transaction_id, NULLIF(btrim(COALESCE(t.document_id, '')), ''),
               ABS(COALESCE(t.amount, 0)),
               '누락 필드: ' || array_to_string(array_remove(ARRAY[
                   CASE WHEN btrim(COALESCE(t.document_id, '')) = '' THEN 'document_id' END,
                   CASE WHEN btrim(COALESCE(t.counterparty, '')) = '' THEN 'counterparty' END,
                   CASE WHEN t.transaction_date IS NULL THEN 'transaction_date' END], NULL), ', ')
          FROM portfolio.transaction t
         WHERE t.engagement_id = p_engagement
           AND (btrim(COALESCE(t.document_id, '')) = '' OR btrim(COALESCE(t.counterparty, '')) = '' OR t.transaction_date IS NULL);
    END IF;

    -- DQ_02 유효하지 않은 금액
    IF (p_rules IS NULL OR 'DQ_02' = ANY(p_rules)) AND EXISTS (SELECT 1 FROM portfolio.rule_catalog rc WHERE rc.rule_id = 'DQ_02' AND rc.is_active) THEN
        INSERT INTO _pf_cand
        SELECT 'DQ_02', t.file_id, t.transaction_id, NULLIF(btrim(COALESCE(t.document_id, '')), ''),
               ABS(COALESCE(t.amount, 0)),
               format('유효하지 않은 금액: %s', COALESCE(t.amount::text, 'NULL'))
          FROM portfolio.transaction t
         WHERE t.engagement_id = p_engagement AND (t.amount IS NULL OR t.amount <= 0);
    END IF;

    -- DQ_03 중복 문서 해시: 거래가 중복 문서를 참조하면 거래 단위, 참조가 없으면 문서 단위로 기록
    IF (p_rules IS NULL OR 'DQ_03' = ANY(p_rules)) AND EXISTS (SELECT 1 FROM portfolio.rule_catalog rc WHERE rc.rule_id = 'DQ_03' AND rc.is_active) THEN
        INSERT INTO _pf_cand
        SELECT 'DQ_03', t.file_id, t.transaction_id, d.document_id, COALESCE(t.amount, 0),
               format('문서 %s의 SHA-256이 이미 접수된 문서 %s와 동일', d.document_id, d.duplicate_of)
          FROM portfolio.document d
          JOIN portfolio.transaction t ON t.engagement_id = d.engagement_id AND t.document_id = d.document_id
         WHERE d.engagement_id = p_engagement AND d.duplicate_of IS NOT NULL;
        INSERT INTO _pf_cand
        SELECT 'DQ_03', d.file_id, NULL, d.document_id, COALESCE(d.document_amount, 0),
               format('문서 %s의 SHA-256이 이미 접수된 문서 %s와 동일(참조 거래 없음)', d.document_id, d.duplicate_of)
          FROM portfolio.document d
         WHERE d.engagement_id = p_engagement AND d.duplicate_of IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM portfolio.transaction t
                            WHERE t.engagement_id = d.engagement_id AND t.document_id = d.document_id);
    END IF;

    -- EV_01 증빙 문서 미수신 (참조 ID는 있으나 접수된 문서가 없음. ID 자체가 없으면 DQ_01이 맡는다)
    IF (p_rules IS NULL OR 'EV_01' = ANY(p_rules)) AND EXISTS (SELECT 1 FROM portfolio.rule_catalog rc WHERE rc.rule_id = 'EV_01' AND rc.is_active) THEN
        v_amount_ev01 := portfolio.param('EV_01', 'amount_threshold');
        INSERT INTO _pf_cand
        SELECT 'EV_01', t.file_id, t.transaction_id, btrim(t.document_id), t.amount,
               format('금액 %s원 거래가 참조한 증빙 %s가 접수되지 않음', t.amount, btrim(t.document_id))
          FROM portfolio.transaction t
         WHERE t.engagement_id = p_engagement
           AND t.amount >= v_amount_ev01
           AND btrim(COALESCE(t.document_id, '')) <> ''
           AND NOT EXISTS (SELECT 1 FROM portfolio.document d
                            WHERE d.engagement_id = t.engagement_id AND d.document_id = btrim(t.document_id));
    END IF;

    -- EV_02 거래-증빙 금액 불일치
    IF (p_rules IS NULL OR 'EV_02' = ANY(p_rules)) AND EXISTS (SELECT 1 FROM portfolio.rule_catalog rc WHERE rc.rule_id = 'EV_02' AND rc.is_active) THEN
        v_tol := portfolio.param('EV_02', 'amount_tolerance');
        INSERT INTO _pf_cand
        SELECT 'EV_02', t.file_id, t.transaction_id, d.document_id, t.amount,
               format('거래 %s원 vs 증빙 %s원 (차이 %s원)', t.amount, d.document_amount, ABS(t.amount - d.document_amount))
          FROM portfolio.transaction t
          JOIN portfolio.document d ON d.engagement_id = t.engagement_id AND d.document_id = btrim(t.document_id)
         WHERE t.engagement_id = p_engagement
           AND t.amount IS NOT NULL AND d.document_amount IS NOT NULL
           AND ABS(t.amount - d.document_amount) > v_tol;
    END IF;

    -- EV_03 거래-증빙 일자 괴리
    IF (p_rules IS NULL OR 'EV_03' = ANY(p_rules)) AND EXISTS (SELECT 1 FROM portfolio.rule_catalog rc WHERE rc.rule_id = 'EV_03' AND rc.is_active) THEN
        v_future := portfolio.param('EV_03', 'future_days');
        v_past := portfolio.param('EV_03', 'past_days');
        INSERT INTO _pf_cand
        SELECT 'EV_03', t.file_id, t.transaction_id, d.document_id, COALESCE(t.amount, 0),
               format('거래일 %s vs 증빙일 %s', t.transaction_date::date, d.document_date)
          FROM portfolio.transaction t
          JOIN portfolio.document d ON d.engagement_id = t.engagement_id AND d.document_id = btrim(t.document_id)
         WHERE t.engagement_id = p_engagement
           AND t.transaction_date IS NOT NULL AND d.document_date IS NOT NULL
           AND ((d.document_date - t.transaction_date::date) > v_future
                OR (t.transaction_date::date - d.document_date) > v_past);
    END IF;

    -- AN_02 근접 반복·분할 결제 후보: 같은 거래처·같은 날, 개별은 한도 미만이고 합계는 한도 이상.
    -- 묶음당 가장 늦은 거래 1건에만 후보를 남긴다.
    IF (p_rules IS NULL OR 'AN_02' = ANY(p_rules)) AND EXISTS (SELECT 1 FROM portfolio.rule_catalog rc WHERE rc.rule_id = 'AN_02' AND rc.is_active) THEN
        v_band_low := portfolio.param('AN_02', 'band_low');
        v_limit := portfolio.param('AN_02', 'limit_amount');
        v_min_count := portfolio.param('AN_02', 'min_count');
        INSERT INTO _pf_cand
        SELECT 'AN_02', a.file_id, a.transaction_id, NULLIF(btrim(COALESCE(a.document_id, '')), ''), a.amount,
               format('같은 거래처·같은 날 %s건 합계 %s원 (한도 %s원)', a.cnt, a.total, v_limit)
          FROM (
                SELECT t.*, count(*) OVER w AS cnt, sum(t.amount) OVER w AS total,
                       row_number() OVER (PARTITION BY t.counterparty, t.transaction_date::date
                                          ORDER BY t.transaction_date DESC, t.transaction_id DESC) AS rn
                  FROM portfolio.transaction t
                 WHERE t.engagement_id = p_engagement
                   AND btrim(COALESCE(t.counterparty, '')) <> '' AND t.transaction_date IS NOT NULL
                   AND t.amount >= v_band_low AND t.amount < v_limit
                WINDOW w AS (PARTITION BY t.counterparty, t.transaction_date::date)
               ) a
         WHERE a.rn = 1 AND a.cnt >= v_min_count AND a.total >= v_limit;
    END IF;

    -- AN_03 업무 외 시간 후보: 주말 또는 심야의 고액 거래
    IF (p_rules IS NULL OR 'AN_03' = ANY(p_rules)) AND EXISTS (SELECT 1 FROM portfolio.rule_catalog rc WHERE rc.rule_id = 'AN_03' AND rc.is_active) THEN
        v_amount_an03 := portfolio.param('AN_03', 'amount_threshold');
        v_night_start := portfolio.param('AN_03', 'night_start_hour');
        v_night_end := portfolio.param('AN_03', 'night_end_hour');
        INSERT INTO _pf_cand
        SELECT 'AN_03', t.file_id, t.transaction_id, NULLIF(btrim(COALESCE(t.document_id, '')), ''), t.amount,
               format('%s %s', to_char(t.transaction_date, 'YYYY-MM-DD HH24:MI'),
                      CASE WHEN EXTRACT(ISODOW FROM t.transaction_date) IN (6, 7) THEN '(주말)' ELSE '(심야)' END)
          FROM portfolio.transaction t
         WHERE t.engagement_id = p_engagement AND t.transaction_date IS NOT NULL
           AND t.amount >= v_amount_an03
           AND (EXTRACT(ISODOW FROM t.transaction_date) IN (6, 7)
                OR EXTRACT(HOUR FROM t.transaction_date) >= v_night_start
                OR EXTRACT(HOUR FROM t.transaction_date) < v_night_end);
    END IF;

    -- AN_04 근접 일자 동일 금액 반복: 직전 동일 거래처·동일 금액 거래와의 간격이 기준 이내면 뒤의 거래에 후보
    IF (p_rules IS NULL OR 'AN_04' = ANY(p_rules)) AND EXISTS (SELECT 1 FROM portfolio.rule_catalog rc WHERE rc.rule_id = 'AN_04' AND rc.is_active) THEN
        v_repeat_days := portfolio.param('AN_04', 'repeat_days');
        INSERT INTO _pf_cand
        SELECT 'AN_04', a.file_id, a.transaction_id, NULLIF(btrim(COALESCE(a.document_id, '')), ''), a.amount,
               format('직전 동일 금액 거래(%s)와 %s일 간격', a.prev_id, (a.transaction_date::date - a.prev_date::date))
          FROM (
                SELECT t.*,
                       lag(t.transaction_id) OVER w AS prev_id,
                       lag(t.transaction_date) OVER w AS prev_date
                  FROM portfolio.transaction t
                 WHERE t.engagement_id = p_engagement
                   AND btrim(COALESCE(t.counterparty, '')) <> '' AND t.transaction_date IS NOT NULL AND t.amount > 0
                WINDOW w AS (PARTITION BY t.counterparty, t.amount ORDER BY t.transaction_date, t.transaction_id)
               ) a
         WHERE a.prev_date IS NOT NULL
           AND (a.transaction_date::date - a.prev_date::date) <= v_repeat_days;
    END IF;

    -- 후보 적재: 같은 실행을 반복해도 새 행이 생기지 않는다. 상태(status)는 덮어쓰지 않는다.
    CREATE TEMP TABLE IF NOT EXISTS _pf_run (rule_id TEXT, is_new BOOLEAN) ON COMMIT DROP;
    TRUNCATE _pf_run;
    WITH ins AS (
        INSERT INTO portfolio.dq_finding AS f
            (engagement_id, file_id, transaction_id, document_id, rule_id, finding_type, severity, amount, detail)
        SELECT p_engagement, c.file_id, c.transaction_id, c.document_id, c.rule_id,
               rc.finding_type, rc.severity, c.amount, c.detail
          FROM _pf_cand c
          JOIN portfolio.rule_catalog rc ON rc.rule_id = c.rule_id
        ON CONFLICT (engagement_id, rule_id, (COALESCE(transaction_id, '')), (COALESCE(document_id, '')), (COALESCE(file_id, 0)))
        DO UPDATE SET amount = EXCLUDED.amount, detail = EXCLUDED.detail, severity = EXCLUDED.severity
        RETURNING f.rule_id AS r_id, (xmax = 0) AS is_new
    )
    INSERT INTO _pf_run SELECT r_id, is_new FROM ins;

    -- 새 후보마다 검토 항목을 하나 만든다.
    INSERT INTO portfolio.review_item (engagement_id, finding_id, document_id, suggested_priority)
    SELECT f.engagement_id, f.finding_id, f.document_id, portfolio.priority_score(f.severity, f.amount)
      FROM portfolio.dq_finding f
     WHERE f.engagement_id = p_engagement
       AND NOT EXISTS (SELECT 1 FROM portfolio.review_item r WHERE r.finding_id = f.finding_id);

    -- 보완 요청 '제안'(PROPOSED). 발송·확정은 사람이 한다.
    INSERT INTO portfolio.issue_log (engagement_id, review_item_id, issue_type, request_reason)
    SELECT f.engagement_id, r.review_item_id,
           CASE f.rule_id WHEN 'EV_01' THEN 'DOCUMENT_REQUEST'
                          WHEN 'DQ_01' THEN 'FIELD_COMPLETION_REQUEST'
                          ELSE 'DUPLICATE_CONFIRMATION' END,
           f.detail
      FROM portfolio.dq_finding f
      JOIN portfolio.review_item r ON r.finding_id = f.finding_id
     WHERE f.engagement_id = p_engagement AND f.rule_id IN ('EV_01', 'DQ_01', 'DQ_03')
    ON CONFLICT (review_item_id, issue_type) DO NOTHING;

    RETURN QUERY
    SELECT rc.rule_id,
           COALESCE(count(*) FILTER (WHERE x.is_new), 0)::INTEGER,
           COALESCE(count(*) FILTER (WHERE NOT x.is_new), 0)::INTEGER
      FROM portfolio.rule_catalog rc
      LEFT JOIN _pf_run x ON x.rule_id = rc.rule_id
     WHERE rc.is_active AND (p_rules IS NULL OR rc.rule_id = ANY(p_rules))
     GROUP BY rc.rule_id
     ORDER BY rc.rule_id;
END;
$fn$;

-- ----------------------------------------------------------------------------
-- 7. 사람이 판정을 기록하는 유일한 경로
--    UNREVIEWED가 아닌 상태에는 reviewer가 필요하다. 워크플로우는 이 함수를 호출하지 않는다.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION portfolio.set_review_status(
    p_review_item_id BIGINT, p_status TEXT, p_reviewer TEXT, p_note TEXT DEFAULT NULL
) RETURNS VOID AS $$
BEGIN
    IF p_status NOT IN ('UNREVIEWED', 'IN_REVIEW', 'RESOLVED', 'FALSE_POSITIVE') THEN
        RAISE EXCEPTION 'invalid review_status: %', p_status;
    END IF;
    IF p_status <> 'UNREVIEWED' AND (p_reviewer IS NULL OR btrim(p_reviewer) = '') THEN
        RAISE EXCEPTION 'reviewer is required to set status %', p_status;
    END IF;

    UPDATE portfolio.review_item
       SET review_status = p_status,
           reviewer      = CASE WHEN p_status = 'UNREVIEWED' THEN NULL ELSE btrim(p_reviewer) END,
           reviewed_at   = CASE WHEN p_status = 'UNREVIEWED' THEN NULL ELSE now() END,
           review_note   = p_note
     WHERE review_item_id = p_review_item_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'review_item not found: %', p_review_item_id;
    END IF;

    UPDATE portfolio.dq_finding f
       SET status = CASE p_status WHEN 'UNREVIEWED' THEN 'OPEN' ELSE p_status END
      FROM portfolio.review_item r
     WHERE r.review_item_id = p_review_item_id AND f.finding_id = r.finding_id;
END;
$$ LANGUAGE plpgsql;

-- ----------------------------------------------------------------------------
-- 8. Power BI mart 뷰
-- ----------------------------------------------------------------------------
-- Page 1: Data Quality & Risk Queue (후보 1건 = 1행)
CREATE OR REPLACE VIEW portfolio.vw_pbi_dq_risk_queue AS
SELECT f.finding_id, f.engagement_id, f.rule_id, rc.rule_name, f.finding_type, f.severity,
       CASE f.severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 WHEN 'MEDIUM' THEN 3 ELSE 4 END AS severity_rank,
       f.amount, f.status, f.detail,
       f.transaction_id, t.transaction_date, t.counterparty, f.document_id,
       f.file_id, sf.file_name, sf.sha256_hash, sf.source_path,
       r.review_item_id, r.suggested_priority, r.review_status,
       f.created_at, f.created_at::date AS created_date
  FROM portfolio.dq_finding f
  JOIN portfolio.rule_catalog rc ON rc.rule_id = f.rule_id
  LEFT JOIN portfolio.transaction t ON t.engagement_id = f.engagement_id AND t.transaction_id = f.transaction_id
  LEFT JOIN portfolio.source_file sf ON sf.file_id = COALESCE(f.file_id, t.file_id)
  LEFT JOIN portfolio.review_item r ON r.finding_id = f.finding_id;

-- Page 2: Review & Issue Tracker (검토 항목 1건 = 1행. 이슈는 요약 집계)
CREATE OR REPLACE VIEW portfolio.vw_pbi_review_tracker AS
SELECT r.review_item_id, r.engagement_id, r.finding_id, f.rule_id, f.severity, f.amount,
       r.document_id, r.suggested_priority, r.review_status, r.reviewer, r.reviewed_at, r.review_note,
       r.created_at, (CURRENT_DATE - r.created_at::date) AS age_days,
       (r.review_status IN ('UNREVIEWED', 'IN_REVIEW')) AS is_open_review,
       COALESCE(i.open_issue_count, 0) AS open_issue_count,
       COALESCE(i.closed_issue_count, 0) AS closed_issue_count,
       i.issue_types, i.next_due_date, i.all_deliverables_reflected
  FROM portfolio.review_item r
  JOIN portfolio.dq_finding f ON f.finding_id = r.finding_id
  LEFT JOIN LATERAL (
        SELECT count(*) FILTER (WHERE l.disposition IN ('PROPOSED', 'REQUESTED'))::INTEGER AS open_issue_count,
               count(*) FILTER (WHERE l.disposition IN ('RESOLVED', 'CLOSED_NO_ACTION'))::INTEGER AS closed_issue_count,
               string_agg(DISTINCT l.issue_type, ', ') AS issue_types,
               min(l.due_date) AS next_due_date,
               bool_and(l.deliverable_reflected) AS all_deliverables_reflected
          FROM portfolio.issue_log l WHERE l.review_item_id = r.review_item_id
       ) i ON TRUE;

-- Page 2 보조: 이슈 단위 목록(보완 요청, owner별 미결, 산출물 반영 여부)
CREATE OR REPLACE VIEW portfolio.vw_pbi_issue_log AS
SELECT l.issue_id, l.engagement_id, l.review_item_id, f.rule_id, f.severity, l.issue_type, l.request_reason,
       l.owner, l.due_date, l.disposition,
       (l.disposition IN ('PROPOSED', 'REQUESTED')) AS is_open_issue,
       l.deliverable_reflected, l.created_at, l.resolved_at,
       (CURRENT_DATE - l.created_at::date) AS age_days
  FROM portfolio.issue_log l
  JOIN portfolio.review_item r ON r.review_item_id = l.review_item_id
  JOIN portfolio.dq_finding f ON f.finding_id = r.finding_id;

-- KPI 요약(engagement당 1행)
CREATE OR REPLACE VIEW portfolio.vw_pbi_kpi_summary AS
SELECT e.engagement_id,
       (SELECT count(*) FROM portfolio.source_file s WHERE s.engagement_id = e.engagement_id) AS source_file_count,
       (SELECT count(*) FROM portfolio.transaction t WHERE t.engagement_id = e.engagement_id) AS transaction_count,
       (SELECT count(*) FROM portfolio.document d WHERE d.engagement_id = e.engagement_id) AS document_count,
       (SELECT count(*) FROM portfolio.dq_finding f WHERE f.engagement_id = e.engagement_id) AS finding_count,
       (SELECT COALESCE(sum(f.amount), 0) FROM portfolio.dq_finding f WHERE f.engagement_id = e.engagement_id) AS exposure_amount,
       (SELECT count(*) FROM portfolio.dq_finding f WHERE f.engagement_id = e.engagement_id AND f.severity IN ('CRITICAL', 'HIGH')) AS critical_high_count,
       (SELECT count(*) FROM portfolio.review_item r WHERE r.engagement_id = e.engagement_id) AS review_item_count,
       (SELECT count(*) FROM portfolio.review_item r WHERE r.engagement_id = e.engagement_id AND r.review_status IN ('UNREVIEWED', 'IN_REVIEW')) AS open_review_count,
       (SELECT ROUND(100.0 * count(*) FILTER (WHERE r.review_status IN ('RESOLVED', 'FALSE_POSITIVE')) / NULLIF(count(*), 0), 2)
          FROM portfolio.review_item r WHERE r.engagement_id = e.engagement_id) AS review_completion_pct,
       (SELECT ROUND(avg(r.suggested_priority), 2) FROM portfolio.review_item r WHERE r.engagement_id = e.engagement_id) AS average_priority,
       (SELECT count(*) FROM portfolio.issue_log l WHERE l.engagement_id = e.engagement_id AND l.disposition IN ('PROPOSED', 'REQUESTED')) AS open_issue_count,
       (SELECT count(*) FROM portfolio.issue_log l WHERE l.engagement_id = e.engagement_id AND l.disposition IN ('RESOLVED', 'CLOSED_NO_ACTION')) AS closed_issue_count
  FROM (SELECT engagement_id FROM portfolio.source_file
        UNION SELECT engagement_id FROM portfolio.dq_finding) e;

COMMENT ON SCHEMA portfolio IS '거래·증빙 정합성 검증과 검토 이슈 트래커. 결과는 후보이며 최종 판단은 reviewer가 한다.';
COMMENT ON FUNCTION portfolio.run_checks(TEXT, TEXT[]) IS '룰 실행(멱등). 후보와 검토 항목, 보완 요청 제안을 만들고 상태는 덮어쓰지 않는다.';
