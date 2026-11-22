#!/usr/bin/env bash
set -euo pipefail

: "${PGHOST:=127.0.0.1}"
: "${PGPORT:=5432}"
: "${PGUSER:=audit_admin}"
: "${PGDATABASE:=audit_db}"
: "${PGPASSWORD:?Set PGPASSWORD before running bootstrap}"
export PGHOST PGPORT PGUSER PGDATABASE PGPASSWORD

run_sql() {
  echo "[bootstrap] $1"
  psql --set ON_ERROR_STOP=1 --file="$1"
}

# 순서 주의: 06(rule_params 복원)은 02보다 앞서야 하고,
# 13(증빙 연결) 전에 audit_core.run_rules로 findings를 먼저 만들어야 한다.
for sql in \
  sql/01_init_and_verify.sql \
  sql/06_rule_params_restore.sql \
  sql/02_run_dq_engine.sql \
  sql/03_issue_review.sql \
  sql/04_demo_review.sql \
  sql/05_seoul_analysis.sql \
  sql/07_final_counts.sql \
  sql/08_forensic_case_schema.sql \
  sql/09_forensic_pbi_views.sql \
  sql/10_audit_core.sql \
  sql/11_seed_audit_core.sql \
  sql/12_rules_engine.sql; do
  run_sql "$sql"
done

echo "[bootstrap] audit_core.run_rules('ENG-2026-001')"
psql --set ON_ERROR_STOP=1 --command="SELECT * FROM audit_core.run_rules('ENG-2026-001')" >/dev/null

for sql in \
  sql/13_evidence_link.sql \
  sql/14_verify_audit_core.sql \
  sql/15_disclosure_events.sql \
  sql/16_ecl_simulator.sql \
  sql/17_ai_qa_goldset.sql \
  sql/18_source_registry.sql \
  sql/19_sanction_registry.sql \
  sql/20_procurement_fact.sql \
  sql/21_procurement_rules.sql \
  sql/22_procurement_evidence.sql \
  sql/23_pbc_intake.sql \
  sql/24_pbc_tieout_facts.sql \
  sql/25_pbc_finding_linkage.sql \
  sql/26_pbc_notification_log.sql \
  sql/27_portfolio_tracker.sql; do
  run_sql "$sql"
done

echo "[bootstrap] complete"
