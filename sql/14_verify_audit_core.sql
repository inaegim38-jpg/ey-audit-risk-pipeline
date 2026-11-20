-- ============================================================================
-- 14. audit_core 검증 (PASS/FAIL)
-- ============================================================================
-- 실행: psql -f sql/14_verify_audit_core.sql
-- 목적: 룰 14종·멱등성·증거 커버리지·스키마 제약을 한 번에 확인한다.
-- ============================================================================

\echo '=== 1. 룰 카탈로그 ==='
SELECT CASE WHEN COUNT(*) = 14 THEN 'PASS' ELSE 'FAIL' END AS result,
       COUNT(*) AS active_rules
FROM audit_core.rule_catalog WHERE is_active;

\echo '=== 2. 룰 실행 (1회차) ==='
SELECT SUM(new_cnt) AS new_findings, SUM(upd_cnt) AS updated
FROM audit_core.run_rules('ENG-2026-001');

\echo '=== 3. 멱등성 (2회차: new=0이어야 PASS) ==='
SELECT CASE WHEN SUM(new_cnt) = 0 THEN 'PASS' ELSE 'FAIL' END AS result,
       SUM(new_cnt) AS new_findings, SUM(upd_cnt) AS updated
FROM audit_core.run_rules('ENG-2026-001');

\echo '=== 4. 룰별 파인딩 ==='
SELECT rule_id, finding_count, item_count, open_count, severity
FROM audit_core.vw_rule_run_summary
ORDER BY rule_id;

\echo '=== 5. 심각도 분포 ==='
SELECT severity, COUNT(*) AS findings
FROM audit_core.fact_risk_finding
GROUP BY 1
ORDER BY CASE severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2
                       WHEN 'MEDIUM' THEN 3 ELSE 4 END;

\echo '=== 6. 증거 인용 커버리지 ==='
SELECT * FROM audit_core.vw_evidence_coverage;

\echo '=== 7. 핵심 제약: 해시 단독 유니크가 아니어야 PASS ==='
SELECT CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END AS result
FROM pg_indexes
WHERE schemaname = 'audit_core'
  AND tablename = 'stg_ingestion_log'
  AND indexdef LIKE '%(sha256_hash)%';

\echo '=== 8. 공시 그레인: (rcept_no, fs_div, std_kifrs_code) ==='
SELECT CASE WHEN COUNT(*) = 1 THEN 'PASS' ELSE 'FAIL' END AS result
FROM pg_indexes
WHERE schemaname = 'audit_core'
  AND tablename = 'fact_disclosure_line'
  AND indexdef LIKE '%UNIQUE%rcept_no%fs_div%std_kifrs_code%';

\echo '=== 9. 파티션: fact_journal 파티션 수 ==='
SELECT COUNT(*) AS partitions
FROM pg_inherits i
JOIN pg_class c ON c.oid = i.inhparent
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'audit_core' AND c.relname = 'fact_journal';

\echo '=== 10. SoD 파인딩의 실제 위반 건수 대조 ==='
SELECT (SELECT SUM(item_count) FROM audit_core.fact_risk_finding WHERE rule_id = 'JET-02') AS rule_items,
       (SELECT COUNT(*) FROM audit_core.fact_journal
        WHERE engagement_id = 'ENG-2026-001'
          AND preparer_id = approver_id) AS raw_rows,
       CASE WHEN (SELECT SUM(item_count) FROM audit_core.fact_risk_finding WHERE rule_id = 'JET-02')
               = (SELECT COUNT(*) FROM audit_core.fact_journal
                  WHERE engagement_id = 'ENG-2026-001' AND preparer_id = approver_id)
            THEN 'PASS' ELSE 'FAIL' END AS result;
