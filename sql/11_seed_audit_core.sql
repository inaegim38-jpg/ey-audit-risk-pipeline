-- ============================================================================
-- 11. audit_core 데모 시드 (합성 케이스 1건)
-- ============================================================================
-- 목적: 룰 14종이 실제로 파인딩을 만들어내는지 검증할 수 있는 최소 데이터셋.
-- 원칙:
--   - 실데이터 반입 없이 돌아가야 한다(샌드박스 검증).
--   - 패턴을 심어 두고, 룰이 그 패턴만 잡는지 확인한다(오탐 측정의 기준선).
--   - random()은 setseed로 고정해 재실행 시 같은 분포를 만든다.
-- ============================================================================

TRUNCATE audit_core.finding_evidence,
         audit_core.fact_risk_finding,
         audit_core.fact_ai_qa_run,
         audit_core.fact_journal,
         audit_core.fact_macro_ecos,
         audit_core.fact_disclosure_line,
         audit_core.stg_ingestion_log,
         audit_core.dim_industry_stats,
         audit_core.dim_coa_mapping,
         audit_core.dim_engagement,
         audit_core.dim_entity
         CASCADE;

SELECT setseed(0.42);

-- ---------------------------------------------------------------- 기준정보
INSERT INTO audit_core.dim_entity
    (entity_id, corp_reg_no, biz_reg_no, dart_corp_code, name, name_norm,
     ksic_code, biz_status, is_related_party, related_source, country_code)
VALUES
    ('11111111-1111-1111-1111-111111111111', '1101111234567', '1234567890', '00123456',
     '한영정밀(주)', '한영정밀', '29199', 'ACTIVE', FALSE, NULL, 'KR');

-- 거래처 40개: 6개는 특수관계자, 3개는 휴·폐업
INSERT INTO audit_core.dim_entity
    (entity_id, biz_reg_no, name, name_norm, ksic_code, biz_status,
     is_related_party, related_source, country_code)
SELECT
    ('22222222-2222-2222-2222-' || lpad(g::text, 12, '0'))::uuid,
    lpad((100000000 + g * 137)::text, 10, '0'),
    '거래처' || lpad(g::text, 2, '0') || '(주)',
    '거래처' || lpad(g::text, 2, '0'),
    CASE WHEN g <= 10 THEN '29199' ELSE '46' || lpad(g::text, 3, '0') END,
    CASE WHEN g IN (38, 39, 40) THEN 'CLOSED'
         WHEN g IN (36, 37) THEN 'DORMANT'
         ELSE 'ACTIVE' END,
    (g <= 6),
    CASE WHEN g <= 6 THEN 'DART' END,
    CASE WHEN g IN (11, 12, 13) THEN 'US' ELSE 'KR' END
FROM generate_series(1, 40) AS g;

INSERT INTO audit_core.dim_coa_mapping
    (source_system, local_acct_code, std_kifrs_code, fs_category)
VALUES
    ('SAP', '1000', 'CASH',            'CASH'),
    ('SAP', '1100', 'AR',              'AR'),
    ('SAP', '1150', 'AR_FX',           'AR_FX'),
    ('SAP', '1200', 'INVENTORY',       'INVENTORY'),
    ('SAP', '1500', 'INVEST_PROPERTY', 'INVEST_PROPERTY'),
    ('SAP', '2000', 'AP',              'AP'),
    ('SAP', '2100', 'PROVISION',       'PROVISION'),
    ('SAP', '3000', 'EQUITY',          'EQUITY'),
    ('SAP', '4000', 'REVENUE',         'REVENUE'),
    ('SAP', '4100', 'REVENUE_FX',      'REVENUE_FX'),
    ('SAP', '5000', 'COGS',            'COGS'),
    ('SAP', '5100', 'SG&A',            'OPEX'),
    ('SAP', '5200', 'INVENTORY_LOSS',  'INVENTORY_LOSS'),
    ('SAP', '5300', 'IMPAIRMENT',      'IMPAIRMENT');

INSERT INTO audit_core.dim_engagement
    (engagement_id, entity_id, engagement_type, fiscal_year, partner_id, status, opened_at)
VALUES
    ('ENG-2026-001', '11111111-1111-1111-1111-111111111111',
     'AUDIT', 2026, 'P-001', 'ACTIVE', DATE '2026-01-05');

INSERT INTO audit_core.dim_industry_stats (ksic_code, metric, avg_ratio, sd_ratio, sample_n, as_of)
VALUES
    ('29199', 'INVENTORY_LOSS_RATIO', 0.0200, 0.0100, 128, DATE '2026-03-31'),
    ('29199', 'ECL_COVERAGE_RATIO',   0.0180, 0.0060, 128, DATE '2026-03-31');

-- ---------------------------------------------------------------- 적재 배치
INSERT INTO audit_core.stg_ingestion_log
    (batch_id, engagement_id, file_name, sha256_hash, row_count,
     debit_credit_diff, tieout_status, ingested_by)
VALUES
    ('33333333-3333-3333-3333-333333333333', 'ENG-2026-001',
     'GL_2026Q1.csv', repeat('a', 64), 5000, 0, 'PASSED', 'intern'),
    ('33333333-3333-3333-3333-333333333334', 'ENG-2026-001',
     'TB_2026Q1.xlsx', repeat('b', 64), 14, 0, 'PASSED', 'intern');

-- ---------------------------------------------------------------- 전표 5,000건
-- 계정 분포: 매출·매출원가·판관비 중심, 외화매출/재고/충당부채 포함
INSERT INTO audit_core.fact_journal
    (engagement_id, fiscal_period, posting_date, posting_time, doc_no,
     std_kifrs_code, counterparty_id, currency_code, fx_amount, krw_amount,
     preparer_id, approver_id, description, source_batch_id)
SELECT
    'ENG-2026-001',
    to_char(d, 'YYYYMM'),
    d,
    CASE WHEN g % 97 = 0 THEN TIME '23:10' + (g % 40) * INTERVAL '1 minute'
         WHEN g % 89 = 0 THEN TIME '05:20' + (g % 30) * INTERVAL '1 minute'
         ELSE TIME '09:00' + (random() * 8) * INTERVAL '1 hour' END,
    'JE-2026-' || lpad(g::text, 6, '0'),
    acct,
    CASE WHEN g % 5 = 0 THEN NULL
         ELSE ('22222222-2222-2222-2222-' || lpad((1 + (g % 40))::text, 12, '0'))::uuid END,
    CASE WHEN acct IN ('AR_FX','REVENUE_FX') THEN 'USD' ELSE 'KRW' END,
    CASE WHEN acct IN ('AR_FX','REVENUE_FX') THEN ROUND((amt / 1350)::numeric, 2) END,
    CASE
        -- 벤포드 이탈 구간: 3,900~4,699는 10만원 단위 딱 떨어지는 금액
        WHEN g BETWEEN 3900 AND 4699 THEN (1 + (g % 9)) * 100000
        -- 다변량 이상 구간: 4,800~4,824는 60배 과대
        WHEN g BETWEEN 4800 AND 4824 THEN ROUND((amt * 60)::numeric, -3)
        ELSE amt
    END,
    'U' || lpad((1 + (g % 12))::text, 2, '0'),
    -- SoD 위반 60건: 기안자와 승인자가 동일
    CASE WHEN g % 83 = 0 THEN 'U' || lpad((1 + (g % 12))::text, 2, '0')
         ELSE 'U' || lpad((1 + ((g + 1) % 12))::text, 2, '0') END,
    '분개 ' || g,
    '33333333-3333-3333-3333-333333333333'
FROM (
    SELECT g,
           DATE '2026-01-01' + (g % 90) AS d,
           (ARRAY['AR','AR_FX','REVENUE','REVENUE_FX','COGS','SG&A',
                  'INVENTORY','PROVISION','CASH','AP','EQUITY'])[1 + (g % 11)] AS acct,
           ROUND((power(random(), 3) * 50000000)::numeric, -3) AS amt
    FROM generate_series(1, 5000) AS g
) s;

-- ---------------------------------------------------------------- 공시·주석
-- 매출·AR·재고·충당부채는 전표 합계와 일부러 3% 어긋나게 둔다(TIE-01 검증용).
INSERT INTO audit_core.fact_disclosure_line
    (rcept_no, dart_corp_code, fiscal_period, fs_div, std_kifrs_code, amount, footnote_json)
SELECT
    '20260331000123', '00123456', '202603', 'C',
    code,
    ROUND(COALESCE(j.total, 0) * CASE WHEN code IN ('REVENUE','AR') THEN 1.03 ELSE 1.00 END, 2),
    fn.json
FROM (VALUES
    ('REVENUE',   NULL),
    ('REVENUE_FX',NULL),
    ('AR',        NULL),
    ('AR_FX',     NULL),
    ('INVENTORY', NULL),
    ('PROVISION', NULL),
    ('INVEST_PROPERTY', NULL),
    ('INVENTORY_LOSS', NULL)
) AS v(code, dummy)
LEFT JOIN (
    SELECT std_kifrs_code, SUM(krw_amount) AS total
    FROM audit_core.fact_journal
    WHERE engagement_id = 'ENG-2026-001'
    GROUP BY std_kifrs_code
) j ON j.std_kifrs_code = v.code
LEFT JOIN LATERAL (
    SELECT CASE
        WHEN v.code = 'AR' THEN jsonb_build_object(
            'related_parties', jsonb_build_array('거래처01','거래처02','거래처03','거래처04'),
            'related_party_sales', 1250000000)
        WHEN v.code = 'INVENTORY' THEN jsonb_build_object(
            'inventory_cost', 8200000000,
            'valuation_loss', 740000000,
            'nrv_basis', '시장가격 하락 반영')
        WHEN v.code = 'PROVISION' THEN jsonb_build_object(
            'ecl_allowance', 320000000,
            'litigation_claim', 3500000000,
            'litigation_provision', 0,
            'contingent_note', '진행 중 소송 2건, 패소 가능성은 낮음으로 평가')
        WHEN v.code = 'INVEST_PROPERTY' THEN jsonb_build_object(
            'fair_value_change', 1200000000,
            'valuation_loss_booked', 800000000,
            'appraisal_firm', '한국감정원')
        ELSE NULL END AS json
) fn ON TRUE;

-- 거시지표: 월별 환율·기준금리·연체율
INSERT INTO audit_core.fact_macro_ecos (obs_date, indicator_code, obs_value)
SELECT d, 'FX_USD', 1300 + (ROW_NUMBER() OVER (ORDER BY d) * 12)
FROM generate_series(DATE '2026-01-31', DATE '2026-03-31', INTERVAL '1 month') AS d;

INSERT INTO audit_core.fact_macro_ecos (obs_date, indicator_code, obs_value)
VALUES (DATE '2026-03-31', 'BASE_RATE', 3.50),
       (DATE '2026-03-31', 'DELINQ_RATE', 0.42);

-- ---------------------------------------------------------------- AI QA 40건
INSERT INTO audit_core.fact_ai_qa_run
    (engagement_id, agent_name, model_version, prompt_version, source_doc_ref,
     source_sha256, ai_json, truth_json, field_total, field_exact,
     exact_match_rate, hallucination_yn, qa_status)
SELECT
    'ENG-2026-001',
    CASE WHEN g % 2 = 0 THEN 'EY_CANVAS' ELSE 'SOLAR_LLM' END,
    'v2026.04',
    'p1.' || (1 + g % 3),
    'docs/contract_' || lpad(g::text, 3, '0') || '.pdf',
    repeat(md5(g::text), 2),
    jsonb_build_object('counterparty', '거래처' || lpad((1 + g % 40)::text, 2, '0'),
                       'amount', 1000000 + g * 10000,
                       'period', '2026-0' || (1 + g % 3)),
    jsonb_build_object('counterparty', '거래처' || lpad((1 + g % 40)::text, 2, '0'),
                       'amount', 1000000 + g * 10000,
                       'period', '2026-0' || (1 + g % 3)),
    12,
    CASE WHEN g IN (7, 13, 21, 34, 39) THEN 9 ELSE 12 END,
    CASE WHEN g IN (7, 13, 21, 34, 39) THEN 75.00 ELSE 100.00 END,
    (g IN (13, 34, 39)),
    CASE WHEN g IN (7, 13, 21, 34, 39) THEN 'ESCALATED' ELSE 'APPROVED' END
FROM generate_series(1, 40) AS g;

-- ---------------------------------------------------------------- 확인
SELECT 'entity' AS obj, COUNT(*) FROM audit_core.dim_entity
UNION ALL SELECT 'journal', COUNT(*) FROM audit_core.fact_journal
UNION ALL SELECT 'disclosure', COUNT(*) FROM audit_core.fact_disclosure_line
UNION ALL SELECT 'macro', COUNT(*) FROM audit_core.fact_macro_ecos
UNION ALL SELECT 'ai_qa', COUNT(*) FROM audit_core.fact_ai_qa_run;
