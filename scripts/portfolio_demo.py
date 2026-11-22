#!/usr/bin/env python3
"""포트폴리오 데모: 합성 거래·증빙 픽스처 → 룰 검증(Python 미러) → CSV/JSON 산출 (+ 선택적 DB 적재·정합 비교).

모든 데이터는 이 스크립트가 만든 합성(가상) 데이터이며 실제 고객·기업 자료가 아니다.
결과는 '검토 후보'이며 부정 여부를 확정하지 않는다.

사용:
  python scripts/portfolio_demo.py                      # artifacts/portfolio_demo/ 생성
  python scripts/portfolio_demo.py --load-db            # + DB(portfolio 스키마)에 적재하고 Python 결과와 비교
환경변수: PGHOST PGPORT PGDATABASE PGUSER PGPASSWORD (기본 127.0.0.1/5432/audit_db/audit_admin)
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

ENGAGEMENT = "SYN-PORTFOLIO-001"
OUT_DIR = Path(__file__).resolve().parent.parent / "artifacts" / "portfolio_demo"

# 룰 카탈로그·파라미터: sql/27_portfolio_tracker.sql의 seed와 같은 값이어야 한다(테스트가 DB와 비교).
RULES = {
    "DQ_00": ("INTAKE_VALIDATION", "HIGH"), "DQ_01": ("REQUIRED_FIELD_MISSING", "HIGH"),
    "DQ_02": ("INVALID_AMOUNT", "HIGH"), "DQ_03": ("DUPLICATE_DOCUMENT", "CRITICAL"),
    "EV_01": ("DOCUMENT_MISSING", "CRITICAL"), "EV_02": ("AMOUNT_MISMATCH", "CRITICAL"),
    "EV_03": ("DATE_MISMATCH", "MEDIUM"), "AN_02": ("SPLIT_PAYMENT_CANDIDATE", "CRITICAL"),
    "AN_03": ("OFF_HOURS_CANDIDATE", "MEDIUM"), "AN_04": ("NEAR_DATE_REPEAT", "MEDIUM"),
}
PARAMS = {
    "ev01_threshold": 500000, "ev02_tolerance": 100, "ev03_future": 7, "ev03_past": 30,
    "an02_band_low": 400000, "an02_limit": 1000000, "an02_min_count": 2,
    "an03_threshold": 500000, "an03_night_start": 23, "an03_night_end": 5, "an04_days": 3,
}
SEVERITY_BASE = {"CRITICAL": 80, "HIGH": 60, "MEDIUM": 40, "LOW": 20}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def priority_score(severity: str, amount: float) -> int:
    return SEVERITY_BASE[severity] + min(19, math.floor(abs(amount or 0) / 200000))


# --------------------------------------------------------------------------- 픽스처
def build_fixture() -> dict:
    """10건 거래, 10건 증빙(중복 해시 1건 포함), 3개 수신 파일(1개 격리). 모두 합성."""
    tx = [  # id, 일시, 거래처, 금액, 증빙ID
        ("T001", "2026-03-03 10:00", "VendorA", 1500000, "DOC-001"),  # 증빙 미수신
        ("T002", "2026-03-04 11:00", "VendorB", 2000000, "DOC-002"),  # 금액 불일치
        ("T003", "2026-03-05 14:00", "VendorC", 900000, "DOC-004"),   # 중복 해시 문서 참조
        ("T004", "2026-03-06 09:30", "VendorD", 600000, "DOC-005"),   # 분할 결제 후보(1/2)
        ("T005", "2026-03-06 15:30", "VendorD", 450000, "DOC-006"),   # 분할 결제 후보(2/2)
        ("T006", "2026-03-09 13:00", "VendorE", 300000, "DOC-007"),   # 일자 괴리
        ("T007", "2026-03-10 10:00", "", 120000, "DOC-008"),          # 거래처 누락
        ("T008", "2026-03-11 10:00", "VendorF", 250000, "DOC-009"),   # 정상
        ("T009", "2026-03-12 10:00", "VendorG", 700000, "DOC-010"),   # 정상
        ("T010", "2026-03-13 16:00", "VendorH", 100000, "DOC-011"),   # 정상
    ]
    doc_amount = {"DOC-002": 1700000}
    doc_date = {"DOC-007": "2026-01-20"}
    tx_by_doc = {t[4]: t for t in tx}
    docs = []
    for d in ["DOC-002", "DOC-003", "DOC-004", "DOC-005", "DOC-006", "DOC-007", "DOC-008", "DOC-009", "DOC-010", "DOC-011"]:
        t = tx_by_doc.get(d)
        amount = doc_amount.get(d, t[3] if t else 900000)
        dt = doc_date.get(d, t[1][:10] if t else "2026-03-05")
        h = sha("DOC-003" if d == "DOC-004" else d)  # DOC-004는 DOC-003과 같은 파일(중복)
        docs.append((d, "INVOICE", h, dt, amount))
    files = [
        ("transactions.csv", "TRANSACTION", sha("syn-transactions-v1"), len(tx), "PASSED", "synthetic://transactions.csv"),
        ("documents.csv", "DOCUMENT_INDEX", sha("syn-documents-v1"), len(docs), "PASSED", "synthetic://documents.csv"),
        ("vendor_ledger_bad.csv", "TRANSACTION", sha("syn-bad-v1"), 5, "QUARANTINED", "synthetic://vendor_ledger_bad.csv"),
    ]
    return {"tx": tx, "docs": docs, "files": files}


# --------------------------------------------------------------------------- 룰 미러
def run_checks(fx: dict) -> list[dict]:
    P = PARAMS
    tx = [dict(id=t[0], dt=datetime.strptime(t[1], "%Y-%m-%d %H:%M"), cp=t[2].strip(), amount=float(t[3]), doc=t[4].strip()) for t in fx["tx"]]
    docs = {}
    original = {}
    for d in fx["docs"]:
        dup = original.get(d[2])
        docs[d[0]] = dict(id=d[0], hash=d[2], date=date.fromisoformat(d[3]) if d[3] else None, amount=d[4], dup_of=dup)
        original.setdefault(d[2], d[0])
    out = []

    def add(rule, tid, did, amount, detail, fname=None):
        out.append(dict(rule_id=rule, finding_type=RULES[rule][0], severity=RULES[rule][1], transaction_id=tid or "",
                        document_id=did or "", amount=float(amount or 0), detail=detail, file_name=fname or ""))

    for f in fx["files"]:
        if f[4] in ("QUARANTINED", "REJECTED"):
            add("DQ_00", None, None, 0, f"수신 파일 {f[0]} 상태 {f[4]} (행수 {f[3]})", f[0])
    for t in tx:
        missing = [n for n, bad in (("document_id", not t["doc"]), ("counterparty", not t["cp"]), ("transaction_date", t["dt"] is None)) if bad]
        if missing:
            add("DQ_01", t["id"], t["doc"], abs(t["amount"]), "누락 필드: " + ", ".join(missing))
        if t["amount"] is None or t["amount"] <= 0:
            add("DQ_02", t["id"], t["doc"], abs(t["amount"] or 0), f"유효하지 않은 금액: {t['amount']}")
        d = docs.get(t["doc"])
        if d and d["dup_of"]:
            add("DQ_03", t["id"], d["id"], t["amount"], f"문서 {d['id']}의 SHA-256이 이미 접수된 문서 {d['dup_of']}와 동일")
        if t["amount"] >= P["ev01_threshold"] and t["doc"] and not d:
            add("EV_01", t["id"], t["doc"], t["amount"], f"금액 {t['amount']:.0f}원 거래가 참조한 증빙 {t['doc']}가 접수되지 않음")
        if d and d["amount"] is not None and abs(t["amount"] - d["amount"]) > P["ev02_tolerance"]:
            add("EV_02", t["id"], d["id"], t["amount"], f"거래 {t['amount']:.0f}원 vs 증빙 {d['amount']:.0f}원")
        if d and d["date"]:
            diff = (d["date"] - t["dt"].date()).days
            if diff > P["ev03_future"] or -diff > P["ev03_past"]:
                add("EV_03", t["id"], d["id"], t["amount"], f"거래일 {t['dt'].date()} vs 증빙일 {d['date']}")
        weekend = t["dt"].isoweekday() in (6, 7)
        if t["amount"] >= P["an03_threshold"] and (weekend or t["dt"].hour >= P["an03_night_start"] or t["dt"].hour < P["an03_night_end"]):
            add("AN_03", t["id"], t["doc"], t["amount"], f"{t['dt']:%Y-%m-%d %H:%M}")
    # AN_02: 같은 거래처·같은 날, 개별은 한도 미만(하한 이상), 합계 한도 이상 → 가장 늦은 거래에 1건
    groups = defaultdict(list)
    for t in tx:
        if t["cp"] and P["an02_band_low"] <= t["amount"] < P["an02_limit"]:
            groups[(t["cp"], t["dt"].date())].append(t)
    for g in groups.values():
        if len(g) >= P["an02_min_count"] and sum(x["amount"] for x in g) >= P["an02_limit"]:
            last = max(g, key=lambda x: (x["dt"], x["id"]))
            add("AN_02", last["id"], last["doc"], last["amount"], f"같은 거래처·같은 날 {len(g)}건 합계 {sum(x['amount'] for x in g):.0f}원")
    # AN_04: 같은 거래처·같은 금액이 가까운 일자에 반복 → 뒤의 거래에 1건
    seq = defaultdict(list)
    for t in tx:
        if t["cp"] and t["amount"] > 0:
            seq[(t["cp"], t["amount"])].append(t)
    for g in seq.values():
        g.sort(key=lambda x: (x["dt"], x["id"]))
        for prev, cur in zip(g, g[1:]):
            if (cur["dt"].date() - prev["dt"].date()).days <= P["an04_days"]:
                add("AN_04", cur["id"], cur["doc"], cur["amount"], f"직전 동일 금액 거래({prev['id']})와 간격")
    out.sort(key=lambda r: (r["rule_id"], r["transaction_id"], r["document_id"]))
    return out


def review_items(findings: list[dict]) -> list[dict]:
    items = []
    for i, f in enumerate(sorted(findings, key=lambda r: (-priority_score(r["severity"], r["amount"]), r["rule_id"], r["transaction_id"])), 1):
        items.append(dict(review_no=i, rule_id=f["rule_id"], transaction_id=f["transaction_id"], document_id=f["document_id"],
                          severity=f["severity"], amount=f"{f['amount']:.0f}", suggested_priority=priority_score(f["severity"], f["amount"]),
                          review_status="UNREVIEWED", reviewer="", review_note=""))
    return items


def summarize(fx: dict, findings: list[dict]) -> dict:
    sev = defaultdict(int)
    for f in findings:
        sev[f["severity"]] += 1
    return {
        "engagement_id": ENGAGEMENT, "data_nature": "SYNTHETIC_DEMO (실제 고객·기업 자료 아님)",
        "transaction_count": len(fx["tx"]), "document_count": len(fx["docs"]), "source_file_count": len(fx["files"]),
        "finding_count": len(findings), "exposure_amount": int(sum(f["amount"] for f in findings)),
        "severity_counts": {k: sev.get(k, 0) for k in ("CRITICAL", "HIGH", "MEDIUM", "LOW")},
        "critical_high_count": sev["CRITICAL"] + sev["HIGH"],
        "open_review_count": len(findings), "review_completion_pct": 0.0,
        "note": "결과는 검토 후보이며 부정 여부를 확정하지 않는다. 검토 상태는 모두 UNREVIEWED로 시작한다.",
    }


def write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def write_outputs(fx: dict, findings: list[dict], out_dir: Path = OUT_DIR) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "transactions.csv", ["transaction_id", "transaction_datetime", "counterparty", "amount_krw", "document_id"], fx["tx"])
    write_csv(out_dir / "documents.csv", ["document_id", "document_type", "document_sha256", "document_date", "document_amount_krw"], fx["docs"])
    write_csv(out_dir / "dq_findings.csv", ["rule_id", "finding_type", "severity", "transaction_id", "document_id", "amount", "detail", "file_name"],
              [[f["rule_id"], f["finding_type"], f["severity"], f["transaction_id"], f["document_id"], f"{f['amount']:.0f}", f["detail"], f["file_name"]] for f in findings])
    items = review_items(findings)
    write_csv(out_dir / "review_items.csv", list(items[0].keys()), [list(i.values()) for i in items])
    summary = summarize(fx, findings)
    (out_dir / "dashboard_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


# --------------------------------------------------------------------------- DB 적재·비교
def load_db(fx: dict, engagement: str = ENGAGEMENT) -> list[tuple]:
    import psycopg2  # 지연 import: DB 없이도 데모 산출은 가능

    conn = psycopg2.connect(host=os.getenv("PGHOST", "127.0.0.1"), port=os.getenv("PGPORT", "5432"), dbname=os.getenv("PGDATABASE", "audit_db"),
                            user=os.getenv("PGUSER", "audit_admin"), password=os.environ["PGPASSWORD"])
    conn.autocommit = False
    cur = conn.cursor()
    ids = {}
    for f in fx["files"]:
        cur.execute("""INSERT INTO portfolio.source_file (engagement_id,file_name,file_type,sha256_hash,row_count,intake_status,source_path)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (engagement_id, sha256_hash) DO UPDATE SET intake_status = EXCLUDED.intake_status
                       RETURNING file_id""", (engagement, f[0], f[1], f[2], f[3], f[4], f[5]))
        ids[f[0]] = cur.fetchone()[0]
    for t in fx["tx"]:
        cur.execute("""INSERT INTO portfolio.transaction (engagement_id,transaction_id,file_id,transaction_date,amount,counterparty,document_id,transaction_hash)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,'')
                       ON CONFLICT (engagement_id, transaction_id) DO NOTHING""",
                    (engagement, t[0], ids["transactions.csv"], t[1], t[3], t[2] or None, t[4] or None))
    for d in fx["docs"]:
        cur.execute("""INSERT INTO portfolio.document (engagement_id,document_id,file_id,document_type,document_hash,document_date,document_amount)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (engagement_id, document_id) DO NOTHING""",
                    (engagement, d[0], ids["documents.csv"], d[1], d[2], d[3], d[4]))
    cur.execute("SELECT rule_id, inserted_count, updated_count FROM portfolio.run_checks(%s)", (engagement,))
    run = cur.fetchall()
    cur.execute("""SELECT rule_id, COALESCE(transaction_id,''), COALESCE(document_id,''), amount::bigint, severity
                     FROM portfolio.dq_finding WHERE engagement_id=%s ORDER BY 1,2,3""", (engagement,))
    rows = cur.fetchall()
    conn.commit()
    conn.close()
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--load-db", action="store_true", help="DB에 적재하고 Python 미러와 결과를 비교")
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = ap.parse_args(argv)

    fx = build_fixture()
    findings = run_checks(fx)
    summary = write_outputs(fx, findings, args.out_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.load_db:
        db_rows = load_db(fx)
        py_rows = sorted((f["rule_id"], f["transaction_id"], f["document_id"], int(f["amount"]), f["severity"]) for f in findings)
        if sorted(db_rows) != py_rows:
            print("PARITY FAIL\n DB:", sorted(db_rows), "\n PY:", py_rows, file=sys.stderr)
            return 1
        print(f"PARITY OK: DB {len(db_rows)}건 == Python {len(py_rows)}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
