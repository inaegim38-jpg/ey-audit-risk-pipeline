#!/usr/bin/env bash
set -euo pipefail

: "${PGHOST:=127.0.0.1}"
: "${PGPORT:=5432}"
: "${PGUSER:=audit_admin}"
: "${PGDATABASE:=audit_db}"
: "${PGPASSWORD:?Set PGPASSWORD before running bootstrap}"
export PGHOST PGPORT PGUSER PGDATABASE PGPASSWORD

for sql in \
  sql/01_init_and_verify.sql \
  sql/02_run_dq_engine.sql \
  sql/03_issue_review.sql \
  sql/04_demo_review.sql \
  sql/05_seoul_analysis.sql \
  sql/06_rule_params_restore.sql \
  sql/07_final_counts.sql \
  sql/08_forensic_case_schema.sql \
  sql/09_forensic_pbi_views.sql \
  sql/10_audit_core.sql \
  sql/11_seed_audit_core.sql \
  sql/12_rules_engine.sql \
  sql/13_evidence_link.sql \
  sql/14_verify_audit_core.sql \
  sql/15_disclosure_events.sql \
  sql/16_ecl_simulator.sql \
  sql/17_ai_qa_goldset.sql \
  sql/18_source_registry.sql \
  sql/19_sanction_registry.sql \
  sql/20_procurement_fact.sql; do
  echo "[bootstrap] $sql"
  psql --set ON_ERROR_STOP=1 --file="$sql"
done

echo "[bootstrap] complete"
