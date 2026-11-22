"""포트폴리오 데모(portfolio 스키마·룰 미러·n8n JSON·bootstrap) 테스트.

DB 테스트는 환경변수 PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD를 쓰며, 테스트마다 고유 engagement를 만들고 정리한다.
"""
import csv
import importlib.util
import json
import os
import re
import uuid
from pathlib import Path

import psycopg2
import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("portfolio_demo", ROOT / "scripts" / "portfolio_demo.py")
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


# ----------------------------------------------------------------- 순수 Python(미러·산출물)
def test_fixture_counts():
    fx = demo.build_fixture()
    assert (len(fx["tx"]), len(fx["docs"]), len(fx["files"])) == (10, 10, 3)


def test_duplicate_document_has_same_hash():
    docs = {d[0]: d for d in demo.build_fixture()["docs"]}
    assert docs["DOC-003"][2] == docs["DOC-004"][2]


def test_finding_count_and_rules():
    f = demo.run_checks(demo.build_fixture())
    assert len(f) == 7
    assert {x["rule_id"] for x in f} == {"DQ_00", "DQ_01", "DQ_03", "EV_01", "EV_02", "EV_03", "AN_02"}


def test_severity_distribution():
    s = demo.summarize(demo.build_fixture(), demo.run_checks(demo.build_fixture()))
    assert s["severity_counts"] == {"CRITICAL": 4, "HIGH": 2, "MEDIUM": 1, "LOW": 0}
    assert s["critical_high_count"] == 6


def test_exposure_amount():
    s = demo.summarize(demo.build_fixture(), demo.run_checks(demo.build_fixture()))
    assert s["exposure_amount"] == 5270000


def test_mirror_is_deterministic():
    assert demo.run_checks(demo.build_fixture()) == demo.run_checks(demo.build_fixture())


def test_split_payment_flags_latest_only():
    f = [x for x in demo.run_checks(demo.build_fixture()) if x["rule_id"] == "AN_02"]
    assert [x["transaction_id"] for x in f] == ["T005"]


def test_missing_document_is_ev01_not_dq01():
    f = demo.run_checks(demo.build_fixture())
    assert any(x["rule_id"] == "EV_01" and x["transaction_id"] == "T001" for x in f)
    assert not any(x["rule_id"] == "DQ_01" and x["transaction_id"] == "T001" for x in f)


def test_priority_score_values():
    assert demo.priority_score("CRITICAL", 0) == 80
    assert demo.priority_score("CRITICAL", 2000000) == 90
    assert demo.priority_score("HIGH", 120000) == 60
    assert demo.priority_score("MEDIUM", 10 ** 9) == 59  # 금액 가산 상한 19


def test_review_items_sorted_by_priority_and_unreviewed():
    items = demo.review_items(demo.run_checks(demo.build_fixture()))
    pr = [i["suggested_priority"] for i in items]
    assert pr == sorted(pr, reverse=True)
    assert {i["review_status"] for i in items} == {"UNREVIEWED"}
    assert all(i["reviewer"] == "" for i in items)


def test_outputs_written(tmp_path):
    fx = demo.build_fixture()
    demo.write_outputs(fx, demo.run_checks(fx), tmp_path)
    for name in ("transactions.csv", "documents.csv", "dq_findings.csv", "review_items.csv", "dashboard_summary.json"):
        assert (tmp_path / name).exists()
    with (tmp_path / "dq_findings.csv").open(encoding="utf-8-sig") as fh:
        assert len(list(csv.DictReader(fh))) == 7


def test_committed_artifacts_match_regeneration(tmp_path):
    fx = demo.build_fixture()
    demo.write_outputs(fx, demo.run_checks(fx), tmp_path)
    for name in ("transactions.csv", "documents.csv", "dq_findings.csv", "review_items.csv", "dashboard_summary.json"):
        assert (tmp_path / name).read_bytes() == (demo.OUT_DIR / name).read_bytes(), name


def test_summary_declares_synthetic_and_no_fraud_claim():
    s = demo.summarize(demo.build_fixture(), demo.run_checks(demo.build_fixture()))
    assert "SYNTHETIC" in s["data_nature"]
    text = json.dumps(s, ensure_ascii=False) + (demo.OUT_DIR / "dq_findings.csv").read_text(encoding="utf-8-sig")
    for banned in ("fraud confirmed", "부정 확정", "적발", "횡령"):
        assert banned not in text.replace("부정 확정이 아님", "").replace("부정 여부를 확정하지 않는다", "")


def test_preview_png_exists():
    assert (demo.OUT_DIR / "powerbi_preview.png").stat().st_size > 10000


# ----------------------------------------------------------------- n8n JSON
WF = {k: json.loads((ROOT / "n8n" / f"wf{k}_portfolio_{n}.json").read_text(encoding="utf-8"))
      for k, n in (("A", "intake"), ("B", "risk_queue"), ("C", "review_tracker"))}


@pytest.mark.parametrize("k", "ABC")
def test_workflow_inactive(k):
    assert WF[k]["active"] is False


@pytest.mark.parametrize("k", "ABC")
def test_workflow_connections_reference_real_nodes(k):
    names = {n["name"] for n in WF[k]["nodes"]}
    for src, c in WF[k]["connections"].items():
        assert src in names
        for branch in c["main"]:
            for t in branch:
                assert t["node"] in names


@pytest.mark.parametrize("k", "ABC")
def test_workflow_has_no_secret_values(k):
    text = json.dumps(WF[k])
    assert not re.search(r"password|api[_-]?key|secret|token|smtp", text, re.I)
    assert all(n.get("credentials", {"postgres": {"id": "PG_CRED"}})["postgres"]["id"] == "PG_CRED"
               for n in WF[k]["nodes"] if n["type"] == "n8n-nodes-base.postgres")


@pytest.mark.parametrize("k", "ABC")
def test_workflow_makes_no_external_calls(k):
    types = {n["type"] for n in WF[k]["nodes"]}
    assert not types & {"n8n-nodes-base.httpRequest", "n8n-nodes-base.slack", "n8n-nodes-base.emailSend", "n8n-nodes-base.gmail"}


def test_wfA_manifest_is_fail_closed():
    js = next(n for n in WF["A"]["nodes"] if n["type"] == "n8n-nodes-base.code")["parameters"]["jsCode"]
    for needle in ("is not set (fail-closed", "manifest is empty", "invalid sha256_hash", "invalid row_count", "must be a JSON array"):
        assert needle in js
    assert "synthetic" not in js.lower().replace("no synthetic fallback", "")


def test_wfB_never_updates_review_status():
    sql = " ".join(n["parameters"].get("query", "") for n in WF["B"]["nodes"])
    assert "set_review_status" not in sql and not re.search(r"UPDATE\s+portfolio\.review_item", sql, re.I)
    assert "MOCKED" in sql


def test_wfC_is_read_only():
    sql = " ".join(n["parameters"].get("query", "") for n in WF["C"]["nodes"])
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|DROP)\b", sql, re.I)


# ----------------------------------------------------------------- bootstrap
def test_bootstrap_order():
    order = re.findall(r"sql/(\d\d)_", (ROOT / "scripts" / "bootstrap.sh").read_text())
    assert order.index("06") < order.index("02")
    assert order[-1] == "27"
    assert sorted(order) == [f"{i:02d}" for i in range(1, 28)]
    text = (ROOT / "scripts" / "bootstrap.sh").read_text()
    assert text.index("audit_core.run_rules") < text.index("sql/13_evidence_link.sql")


def test_gitignore_whitelists_demo_csv():
    assert "!artifacts/portfolio_demo/*.csv" in (ROOT / ".gitignore").read_text()


# ----------------------------------------------------------------- DB
def _connect():
    return psycopg2.connect(host=os.getenv("PGHOST", "127.0.0.1"), port=os.getenv("PGPORT", "5432"),
                            dbname=os.getenv("PGDATABASE", "audit_db"), user=os.getenv("PGUSER", "audit_admin"),
                            password=os.getenv("PGPASSWORD", "audit_password123"))


@pytest.fixture()
def eng():
    e = "TEST-PF-" + uuid.uuid4().hex[:8]
    yield e
    c = _connect()
    cur = c.cursor()
    for tbl in ("issue_log", "review_item", "dq_finding", "document", "transaction", "source_file"):
        cur.execute(f"DELETE FROM portfolio.{tbl} WHERE engagement_id = %s", (e,))
    c.commit()
    c.close()


def _q(sql, args=()):
    c = _connect()
    cur = c.cursor()
    cur.execute(sql, args)
    rows = cur.fetchall() if cur.description else []
    c.commit()
    c.close()
    return rows


def _expect_error(sql, args=()):
    c = _connect()
    cur = c.cursor()
    with pytest.raises(psycopg2.Error) as ei:
        cur.execute(sql, args)
    c.rollback()
    c.close()
    return str(ei.value)


def test_db_parity_with_python_mirror(eng):
    rows = demo.load_db(demo.build_fixture(), eng)
    py = sorted((f["rule_id"], f["transaction_id"], f["document_id"], int(f["amount"]), f["severity"]) for f in demo.run_checks(demo.build_fixture()))
    assert sorted(rows) == py


def test_db_rerun_is_idempotent(eng):
    demo.load_db(demo.build_fixture(), eng)
    again = _q("SELECT COALESCE(sum(inserted_count),0), COALESCE(sum(updated_count),0) FROM portfolio.run_checks(%s)", (eng,))[0]
    assert again == (0, 7)
    assert _q("SELECT count(*) FROM portfolio.dq_finding WHERE engagement_id=%s", (eng,))[0][0] == 7
    assert _q("SELECT count(*) FROM portfolio.review_item WHERE engagement_id=%s", (eng,))[0][0] == 7


def test_db_issue_proposals_created(eng):
    demo.load_db(demo.build_fixture(), eng)
    got = dict(_q("SELECT issue_type, count(*) FROM portfolio.issue_log WHERE engagement_id=%s GROUP BY 1", (eng,)))
    assert got == {"DOCUMENT_REQUEST": 1, "FIELD_COMPLETION_REQUEST": 1, "DUPLICATE_CONFIRMATION": 1}
    assert _q("SELECT DISTINCT disposition FROM portfolio.issue_log WHERE engagement_id=%s", (eng,)) == [("PROPOSED",)]


def test_db_review_status_preserved_after_rerun(eng):
    demo.load_db(demo.build_fixture(), eng)
    rid = _q("SELECT min(review_item_id) FROM portfolio.review_item WHERE engagement_id=%s", (eng,))[0][0]
    _q("SELECT portfolio.set_review_status(%s,'FALSE_POSITIVE','tester','메모')", (rid,))
    _q("SELECT * FROM portfolio.run_checks(%s)", (eng,))
    assert _q("SELECT review_status, reviewer FROM portfolio.review_item WHERE review_item_id=%s", (rid,)) == [("FALSE_POSITIVE", "tester")]
    assert _q("SELECT f.status FROM portfolio.dq_finding f JOIN portfolio.review_item r USING(finding_id) WHERE r.review_item_id=%s", (rid,)) == [("FALSE_POSITIVE",)]


def test_db_set_status_requires_reviewer(eng):
    demo.load_db(demo.build_fixture(), eng)
    rid = _q("SELECT min(review_item_id) FROM portfolio.review_item WHERE engagement_id=%s", (eng,))[0][0]
    assert "reviewer is required" in _expect_error("SELECT portfolio.set_review_status(%s,'RESOLVED','  ')", (rid,))


def test_db_set_status_rejects_invalid_value(eng):
    demo.load_db(demo.build_fixture(), eng)
    rid = _q("SELECT min(review_item_id) FROM portfolio.review_item WHERE engagement_id=%s", (eng,))[0][0]
    assert "invalid review_status" in _expect_error("SELECT portfolio.set_review_status(%s,'CONFIRMED_FRAUD','x')", (rid,))


def test_db_reset_to_unreviewed_clears_reviewer(eng):
    demo.load_db(demo.build_fixture(), eng)
    rid = _q("SELECT min(review_item_id) FROM portfolio.review_item WHERE engagement_id=%s", (eng,))[0][0]
    _q("SELECT portfolio.set_review_status(%s,'IN_REVIEW','a')", (rid,))
    _q("SELECT portfolio.set_review_status(%s,'UNREVIEWED',NULL)", (rid,))
    assert _q("SELECT review_status, reviewer, reviewed_at FROM portfolio.review_item WHERE review_item_id=%s", (rid,)) == [("UNREVIEWED", None, None)]


def test_db_duplicate_document_marked_by_trigger(eng):
    demo.load_db(demo.build_fixture(), eng)
    got = dict(_q("SELECT document_id, duplicate_of FROM portfolio.document WHERE engagement_id=%s", (eng,)))
    assert got["DOC-004"] == "DOC-003" and got["DOC-003"] is None
    assert sum(1 for v in got.values() if v) == 1


def test_db_duplicate_of_cannot_be_forged(eng):
    demo.load_db(demo.build_fixture(), eng)
    fid = _q("SELECT file_id FROM portfolio.source_file WHERE engagement_id=%s LIMIT 1", (eng,))[0][0]
    h = "a" * 64
    _q("INSERT INTO portfolio.document (engagement_id,document_id,file_id,document_type,document_hash,duplicate_of) VALUES (%s,'X1',%s,'INV',%s,'FORGED')", (eng, fid, h))
    assert _q("SELECT duplicate_of FROM portfolio.document WHERE engagement_id=%s AND document_id='X1'", (eng,)) == [(None,)]
    _q("INSERT INTO portfolio.document (engagement_id,document_id,file_id,document_type,document_hash,duplicate_of) VALUES (%s,'X2',%s,'INV',%s,NULL)", (eng, fid, h))
    assert _q("SELECT duplicate_of FROM portfolio.document WHERE engagement_id=%s AND document_id='X2'", (eng,)) == [("X1",)]


def test_db_two_originals_with_same_hash_blocked_by_index():
    idx = _q("SELECT indexdef FROM pg_indexes WHERE schemaname='portfolio' AND indexname='ux_pf_document_hash_original'")[0][0]
    assert "UNIQUE" in idx and "duplicate_of IS NULL" in idx


def test_db_missing_param_fails_closed(eng):
    demo.load_db(demo.build_fixture(), eng)
    c = _connect()
    cur = c.cursor()
    cur.execute("DELETE FROM portfolio.rule_param WHERE rule_id='EV_02' AND param_name='amount_tolerance'")
    with pytest.raises(psycopg2.Error) as ei:
        cur.execute("SELECT * FROM portfolio.run_checks(%s)", (eng,))
    assert "missing rule_param EV_02.amount_tolerance" in str(ei.value)
    c.rollback()
    c.close()


def test_db_params_match_python_mirror():
    got = {(r, n): float(v) for r, n, v in _q("SELECT rule_id, param_name, param_value FROM portfolio.rule_param")}
    expected = {("EV_01", "amount_threshold"): "ev01_threshold", ("EV_02", "amount_tolerance"): "ev02_tolerance",
                ("EV_03", "future_days"): "ev03_future", ("EV_03", "past_days"): "ev03_past",
                ("AN_02", "band_low"): "an02_band_low", ("AN_02", "limit_amount"): "an02_limit", ("AN_02", "min_count"): "an02_min_count",
                ("AN_03", "amount_threshold"): "an03_threshold", ("AN_03", "night_start_hour"): "an03_night_start",
                ("AN_03", "night_end_hour"): "an03_night_end", ("AN_04", "repeat_days"): "an04_days"}
    for key, pname in expected.items():
        assert got[key] == demo.PARAMS[pname], key


def test_db_rule_catalog_matches_python_mirror():
    got = {r: (t, s) for r, t, s in _q("SELECT rule_id, finding_type, severity FROM portfolio.rule_catalog")}
    assert got == demo.RULES


def test_db_blank_engagement_rejected():
    assert "engagement_id is required" in _expect_error("SELECT * FROM portfolio.run_checks('  ')")


def test_db_source_file_rejects_bad_hash(eng):
    assert "sha256_hash" in _expect_error(
        "INSERT INTO portfolio.source_file (engagement_id,file_name,file_type,sha256_hash,row_count,source_path) VALUES (%s,'a','b','nothex',1,'p')", (eng,))


def test_db_source_file_upsert_is_idempotent(eng):
    q = ("INSERT INTO portfolio.source_file (engagement_id,file_name,file_type,sha256_hash,row_count,source_path) "
         "VALUES (%s,'a','b',%s,1,'p') ON CONFLICT (engagement_id, sha256_hash) DO UPDATE SET row_count=EXCLUDED.row_count")
    _q(q, (eng, "b" * 64))
    _q(q, (eng, "b" * 64))
    assert _q("SELECT count(*) FROM portfolio.source_file WHERE engagement_id=%s", (eng,))[0][0] == 1


def test_db_views_for_power_bi(eng):
    demo.load_db(demo.build_fixture(), eng)
    assert _q("SELECT count(*) FROM portfolio.vw_pbi_dq_risk_queue WHERE engagement_id=%s", (eng,))[0][0] == 7
    assert _q("SELECT count(*) FROM portfolio.vw_pbi_review_tracker WHERE engagement_id=%s", (eng,))[0][0] == 7
    assert _q("SELECT count(*) FROM portfolio.vw_pbi_issue_log WHERE engagement_id=%s", (eng,))[0][0] == 3
    k = _q("SELECT finding_count, exposure_amount, critical_high_count, open_review_count, review_completion_pct FROM portfolio.vw_pbi_kpi_summary WHERE engagement_id=%s", (eng,))[0]
    assert (k[0], int(k[1]), k[2], k[3], float(k[4])) == (7, 5270000, 6, 7, 0.0)


def test_db_kpi_completion_updates_after_review(eng):
    demo.load_db(demo.build_fixture(), eng)
    ids = [r[0] for r in _q("SELECT review_item_id FROM portfolio.review_item WHERE engagement_id=%s ORDER BY 1 LIMIT 2", (eng,))]
    _q("SELECT portfolio.set_review_status(%s,'RESOLVED','a')", (ids[0],))
    _q("SELECT portfolio.set_review_status(%s,'FALSE_POSITIVE','a')", (ids[1],))
    k = _q("SELECT open_review_count, review_completion_pct FROM portfolio.vw_pbi_kpi_summary WHERE engagement_id=%s", (eng,))[0]
    assert (k[0], float(k[1])) == (5, 28.57)


def test_db_quarantined_file_yields_dq00_with_zero_exposure(eng):
    demo.load_db(demo.build_fixture(), eng)
    assert _q("SELECT amount::int, severity FROM portfolio.dq_finding WHERE engagement_id=%s AND rule_id='DQ_00'", (eng,)) == [(0, "HIGH")]


def test_db_rule_subset_runs_only_requested(eng):
    demo.load_db(demo.build_fixture(), eng)
    rows = _q("SELECT rule_id FROM portfolio.run_checks(%s, ARRAY['EV_01'])", (eng,))
    assert rows == [("EV_01",)]
