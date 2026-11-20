#!/usr/bin/env python3
"""교차 문서 포렌식 분석(forensic data analytics).

문서 1건만 봐서는 알 수 없는 패턴을 문서 간 관계에서 찾는다.
  A1 중복 지급          : 동일 승인번호가 서로 다른 날짜에 두 번 지급
  A2 분할 발주          : 동일 업체·짧은 기간·한도 직하 금액의 반복
  A3 업체 마스터 이상   : 서로 다른 업체가 같은 주소·연락처를 사용
  A4 일자 역전          : 계약일자가 세금계산서 작성일자보다 늦음
  A5 카드 사적 사용 정황: 심야·주말 + 고위험 업종
  A6 벤포드 1자리 편차  : 금액 분포의 선행자릿수 이탈(참고 지표)
  A7 결재 집중          : 특정 업체 발주가 특정 1인 결재에 집중
  A8 라운드 금액 비중   : 100만원 단위 금액의 과다

모든 결과는 '확인 후보'이며 부정의 확정이 아니다. 이 문구를 산출물에도 그대로 남긴다.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sqlite3
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

THRESHOLD = 10_000_000          # 발주 결재 한도(프로젝트 가정값)
SPLIT_WINDOW_DAYS = 21
SPLIT_NEAR = 0.90               # 한도의 90% 이상 ~ 한도 미만
DUP_WINDOW_DAYS = 120

FINDING_SCHEMA = """
DROP TABLE IF EXISTS findings;
DROP TABLE IF EXISTS finding_evidence;

CREATE TABLE findings (
    finding_id   TEXT PRIMARY KEY,
    category     TEXT,
    title        TEXT,
    severity     TEXT,
    amount       INTEGER,
    item_count   INTEGER,
    description  TEXT,
    method       TEXT,
    limitation   TEXT,
    status       TEXT DEFAULT 'OPEN',
    created_at   TEXT
);

CREATE TABLE finding_evidence (
    finding_id TEXT,
    evidence_id TEXT,
    rel_path   TEXT,
    note       TEXT
);
"""


def _num(s: str) -> int:
    return int(re.sub(r"[^\d]", "", s) or 0)


def _parse_fields(body: str) -> dict:
    def f(*keys: str) -> str:
        for k in keys:
            m = re.search(rf"^{k}\s*[:：]\s*(.+)$", body, re.MULTILINE)
            if m:
                return m.group(1).strip()
        return ""
    return {
        "vendor": f("공급자", "협력사").replace("(주)", "").strip(),
        "biz_no": f("사업자번호", "공급자"),
        "addr": f("주소"),
        "phone": f("담당자 연락처"),
        "amount": _num(f("합계금액", "합계", "금액")),
        "doc_date": f("견적일자", "작성일자", "기안일자"),
        "contract_date": f("계약일자"),
        "invoice_no": f("승인번호"),
        "item": f("품명", "품목"),
    }


class Registry:
    def __init__(self, by_path: dict[str, str] | None = None) -> None:
        self.items: list[dict] = []
        self.links: list[dict] = []
        # rel_path → evidence_id. 카드내역처럼 파일 1건에서 여러 거래가 나오는 경우
        # evidence_id 없이 행이 여러 개 생기는 것을 막는다.
        self.by_path = by_path or {}

    def add(self, category: str, title: str, severity: str, amount: int, count: int,
            description: str, method: str, limitation: str, evidence: list[tuple[str, str, str]]) -> str:
        fid = f"F-{len(self.items) + 1:03d}"
        self.items.append({
            "finding_id": fid, "category": category, "title": title, "severity": severity,
            "amount": amount, "item_count": count, "description": description,
            "method": method, "limitation": limitation,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        })
        # 같은 파일을 가리키는 인용은 1행으로 합치고, 개별 건수는 note에 남긴다.
        merged: dict[str, dict] = {}
        for ev_id, rel_path, note in evidence:
            eid = ev_id or self.by_path.get(rel_path, "")
            row = merged.get(rel_path)
            if row is None:
                merged[rel_path] = {"evidence_id": eid, "notes": [note]}
            else:
                row["notes"].append(note)
                if not row["evidence_id"] and eid:
                    row["evidence_id"] = eid
        for rel_path, row in merged.items():
            notes = row["notes"]
            note = notes[0] if len(notes) == 1 else f"{notes[0]} 외 {len(notes) - 1}건"
            self.links.append({"finding_id": fid, "evidence_id": row["evidence_id"],
                               "rel_path": rel_path, "note": note})
        return fid


def _manifest_index(db_path: Path) -> dict[str, str]:
    """수집 매니페스트에서 rel_path → evidence_id 색인을 만든다."""
    path = db_path.parent / "evidence" / "evidence_manifest.csv"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return {r["rel_path"]: r["evidence_id"] for r in csv.DictReader(fh)}


def analyze(db_path: Path) -> dict:
    conn = sqlite3.connect(db_path)
    conn.executescript(FINDING_SCHEMA)
    reg = Registry(_manifest_index(db_path))

    docs = conn.execute("SELECT doc_id, doc_type, rel_path, doc_date, subject, body FROM documents").fetchall()
    by_type: dict[str, list] = defaultdict(list)
    for d in docs:
        by_type[d[1]].append(d)

    # 문서 필드 캐시
    parsed = {}
    for doc_id, doc_type, rel_path, doc_date, subject, body in docs:
        parsed[doc_id] = {**_parse_fields(body), "doc_type": doc_type, "rel_path": rel_path,
                          "doc_id": doc_id, "doc_date": doc_date, "subject": subject, "body": body}

    # ---------------------------------------------------------------- A1 중복 지급
    inv_by_no: dict[str, list] = defaultdict(list)
    for doc_id, p in parsed.items():
        if p["doc_type"] == "INVOICE" and p["invoice_no"]:
            inv_by_no[p["invoice_no"]].append(p)
    for inv_no, rows in inv_by_no.items():
        dates = sorted({r["doc_date"] for r in rows})
        if len(dates) < 2:
            continue
        amount = max(r["amount"] for r in rows)
        reg.add(
            "A1", f"동일 승인번호 세금계산서가 {len(dates)}개 일자에 존재",
            "CRITICAL", amount, len(rows),
            f"승인번호 {inv_no} 가 {', '.join(dates)} 에 각각 나타난다. "
            f"동일 계산서를 두 번 수취·지급했다면 중복 지급이고, 재발행이라면 원본과 대사가 필요하다.",
            "INVOICE 문서의 '승인번호' 필드 그룹화, 일자 distinct count ≥ 2",
            "실제 지급 이력(은행 이체 내역)이 없어 지급 중복 여부는 확정할 수 없다.",
            [(r["doc_id"], r["rel_path"], f"작성일자 {r['doc_date']}") for r in rows],
        )

    # -------------------------------------------------------------- A2 분할 발주
    appr = [p for p in parsed.values() if p["doc_type"] == "APPROVAL"]
    by_vendor: dict[str, list] = defaultdict(list)
    for p in appr:
        by_vendor[p["vendor"]].append(p)
    for vendor, rows in by_vendor.items():
        rows = sorted(rows, key=lambda x: x["doc_date"])
        near = [r for r in rows if THRESHOLD * SPLIT_NEAR <= r["amount"] < THRESHOLD]
        if len(near) < 2:
            continue
        for i, base in enumerate(near):
            window = [r for r in near[i:]
                      if (date.fromisoformat(r["doc_date"]) - date.fromisoformat(base["doc_date"])).days
                      <= SPLIT_WINDOW_DAYS]
            total = sum(r["amount"] for r in window)
            if len(window) >= 2 and total >= THRESHOLD:
                reg.add(
                    "A2", f"{vendor} 발주가 {len(window)}건으로 분할된 정황",
                    "CRITICAL", total, len(window),
                    f"{window[0]['doc_date']}~{window[-1]['doc_date']} 사이 {vendor} 건으로 "
                    f"한도({THRESHOLD:,}원) 직하 금액이 {len(window)}회 반복되었다. 합계 {total:,}원. "
                    f"품명은 {', '.join(sorted({r['item'] for r in window}))} 로 표기만 나뉘어 있다.",
                    f"동일 업체·기안 후 {SPLIT_WINDOW_DAYS}일 이내·한도 90~100% 구간 금액·2건 이상·합계 ≥ 한도",
                    "분할 발주 자체가 위법은 아니다. 예산 항목·계약 단위·실제 납품 단위를 확인해야 한다.",
                    [(r["doc_id"], r["rel_path"], f"{r['doc_date']} {r['amount']:,}원") for r in window],
                )
                break

    # ------------------------------------------------------- A3 업체 마스터 이상
    vendor_attr: dict[str, dict] = {}
    for p in parsed.values():
        if p["doc_type"] == "QUOTE" and p["vendor"]:
            vendor_attr.setdefault(p["vendor"], {"addr": p["addr"], "phone": p["phone"], "biz_no": p["biz_no"]})
    keys = list(vendor_attr)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = vendor_attr[keys[i]], vendor_attr[keys[j]]
            same = []
            if a["addr"] and a["addr"] == b["addr"]:
                same.append("주소")
            if a["phone"] and a["phone"] == b["phone"]:
                same.append("연락처")
            if not same:
                continue
            ev = []
            for vendor_name in (keys[i], keys[j]):
                picks = [p for p in parsed.values()
                         if p["doc_type"] == "QUOTE" and p["vendor"] == vendor_name]
                picks.sort(key=lambda x: x["doc_date"])
                ev += [(p["doc_id"], p["rel_path"], f"{p['vendor']} 견적") for p in picks[:3]]
            reg.add(
                "A3", f"서로 다른 업체 2곳({keys[i]}, {keys[j]})의 {'·'.join(same)}가 동일",
                "HIGH", 0, len(ev),
                f"견적서상 {keys[i]}({a['biz_no']}), {keys[j]}({b['biz_no']}) 두 업체의 "
                f"{'·'.join(same)}가 동일하다. 독립 업체 간 비교견적 요건을 충족하지 못할 가능성이 있다.",
                "QUOTE 문서의 주소·연락처 필드 교차 비교",
                "동일 주소는 사무실 공유·대리점 구조일 수 있어 사업자 실체 확인이 필요하다.",
                ev,
            )

    # ------------------------------------------------------------- A4 일자 역전
    reversed_docs = []
    for p in parsed.values():
        if p["doc_type"] != "INVOICE" or not p["contract_date"]:
            continue
        if p["contract_date"] > p["doc_date"]:
            reversed_docs.append(p)
    if reversed_docs:
        reg.add(
            "A4", f"계약일자가 세금계산서 작성일자보다 늦은 건 {len(reversed_docs)}건",
            "HIGH", sum(p["amount"] for p in reversed_docs), len(reversed_docs),
            "계약 체결 전에 세금계산서가 발행된 형태로, 선발행·소급계약·서류 재작성 가능성을 확인해야 한다. "
            + " / ".join(f"{p['invoice_no']}: 작성 {p['doc_date']} < 계약 {p['contract_date']}"
                         for p in reversed_docs),
            "INVOICE 문서의 '작성일자' < '계약일자' 비교",
            "계약일자 필드가 실제 체결일이 아니라 등록일일 수 있다.",
            [(p["doc_id"], p["rel_path"], f"작성 {p['doc_date']} / 계약 {p['contract_date']}")
             for p in reversed_docs],
        )

    # --------------------------------------------------------- A5 카드 사적 사용
    card = conn.execute("SELECT tx_at, merchant, category, amount, approval_no FROM card_transactions").fetchall()
    suspicious = []
    for tx_at, merchant, category, amount, approval_no in card:
        dt = datetime.fromisoformat(tx_at)
        high_risk_cat = category in ("유흥주점", "골프장")
        off_hours = dt.hour >= 21 or dt.hour <= 6
        weekend = dt.weekday() >= 5
        if ((high_risk_cat or off_hours) and weekend) or (high_risk_cat and amount >= 300_000):
            suspicious.append((tx_at, merchant, category, amount, approval_no))
    if suspicious:
        reg.add(
            "A5", f"심야·주말 고위험 업종 카드 사용 {len(suspicious)}건",
            "HIGH", sum(s[3] for s in suspicious), len(suspicious),
            "골프장·유흥주점에서 주말·심야에 결제된 건으로, 접대비 처리 요청 메일과 함께 검토가 필요하다. "
            f"최대 건은 {max(suspicious, key=lambda x: x[3])[1]} {max(s[3] for s in suspicious):,}원.",
            "카드 승인내역의 요일·시각·업종·금액 조건 필터",
            "법인카드 사용 규정과 참석자·사유 확인 없이는 사적 사용으로 단정할 수 없다.",
            [("", "card/card_transactions.csv", f"{s[0]} {s[1]} {s[3]:,}원") for s in suspicious[:6]],
        )

    # ------------------------------------------------------------- A6 벤포드
    amounts = [p["amount"] for p in parsed.values() if p["doc_type"] == "INVOICE" and p["amount"] > 0]
    benford = {"n": len(amounts)}
    if len(amounts) >= 30:
        counts = defaultdict(int)
        for a in amounts:
            lead = int(str(a).lstrip("0")[0])
            counts[lead] += 1
        mad = 0.0
        detail = []
        for d in range(1, 10):
            expected = math.log10(1 + 1 / d)
            observed = counts[d] / len(amounts)
            mad += abs(observed - expected)
            detail.append({"digit": d, "observed": round(observed, 4), "expected": round(expected, 4)})
        mad /= 9
        benford.update({"mad": round(mad, 4), "detail": detail})
        # Nigrini 기준: 0.015 초과면 유의
        if mad > 0.015:
            reg.add(
                "A6", "세금계산서 금액의 선행자릿수 분포가 벤포드 기대에서 벗어남",
                "MEDIUM", 0, len(amounts),
                f"1자리 숫자 분포의 평균절대편차(MAD)가 {mad:.4f}로 Nigrini 기준(0.015)을 넘는다. "
                "특정 금액대에 인위적 집중이 있었을 가능성을 시사하나, 표본이 작아 단독 근거로 쓰기 어렵다.",
                "세금계산서 합계금액의 선행자릿수 관측빈도 vs log10(1+1/d)",
                f"표본 {len(amounts)}건으로 작고, 품목 단가가 획일적이면 정상 데이터도 이탈한다.",
                [],
            )

    # --------------------------------------------------------------- A7 결재 집중
    approver_by_vendor: dict[str, list] = defaultdict(list)
    for p in parsed.values():
        if p["doc_type"] == "APPROVAL":
            approver_by_vendor[p["vendor"]].append(p)
    for vendor, rows in approver_by_vendor.items():
        if len(rows) < 3:
            continue
        # 결재란에 팀장 1인만 있는 비율
        solo = sum(1 for r in rows if "결재:" in r["body"] and "재무팀" not in r["body"])
        if solo / len(rows) >= 0.8 and len(rows) >= 3:
            reg.add(
                "A7", f"{vendor} 발주 {len(rows)}건 중 {solo}건이 팀장 단독 결재",
                "MEDIUM", sum(r["amount"] for r in rows), len(rows),
                f"{vendor} 관련 품의서 대부분이 팀장 1인 결재로 끝나 있다. 금액 규모에 비해 통제가 약하다.",
                "품의서 '결재' 필드의 결재자 수 파싱",
                "전결 규정상 팀장 전결 한도 내라면 정상일 수 있다.",
                [(r["doc_id"], r["rel_path"], f"{r['doc_date']} {r['amount']:,}원") for r in rows[:5]],
            )

    # ------------------------------------------------------------ A8 라운드 금액
    round_docs = [p for p in parsed.values() if p["doc_type"] in ("INVOICE", "QUOTE")
                  and p["amount"] > 0 and p["amount"] % 1_000_000 == 0]
    total_docs = [p for p in parsed.values() if p["doc_type"] in ("INVOICE", "QUOTE") and p["amount"] > 0]
    ratio = len(round_docs) / len(total_docs) if total_docs else 0
    if ratio >= 0.10:
        reg.add(
            "A8", f"100만원 단위 라운드 금액 비중 {ratio:.1%}",
            "LOW", sum(p["amount"] for p in round_docs), len(round_docs),
            "견적·계산서 금액이 정확히 100만원 단위로 떨어지는 비중이 높다. "
            "단가 산정 근거가 아니라 목표 금액에 맞춘 역산일 수 있다.",
            "금액 % 1,000,000 == 0 비율",
            "용역·시공 계약은 라운드 금액이 흔하다.",
            [(p["doc_id"], p["rel_path"], f"{p['amount']:,}원") for p in round_docs[:5]],
        )

    # ------------------------------------------------- A9 비교견적 형식화(동일 견적)
    quote_groups: dict[tuple, list] = defaultdict(list)
    for p in parsed.values():
        if p["doc_type"] == "QUOTE" and p["amount"] and p["phone"]:
            quote_groups[(p["amount"], p["phone"])].append(p)
    for (amount, phone), rows in quote_groups.items():
        vendors = sorted({r["vendor"] for r in rows})
        if len(vendors) < 2:
            continue
        reg.add(
            "A9", f"서로 다른 {len(vendors)}개 업체 견적이 금액·연락처까지 동일",
            "CRITICAL", amount * len(rows), len(rows),
            f"{', '.join(vendors)} 의 견적 금액이 모두 {amount:,}원이고 담당자 연락처도 {phone} 으로 같다. "
            "독립적으로 작성된 비교견적이라면 이 조합이 나오기 어렵다.",
            "QUOTE 문서의 (합계금액, 담당자 연락처) 조합 그룹화, 업체 2개 이상",
            "대리점·총판이 동일 견적 양식을 공유하는 경우 정상일 수 있다.",
            [(r["doc_id"], r["rel_path"], f"{r['vendor']} {amount:,}원") for r in rows],
        )

    # 저장
    now = datetime.now().isoformat(timespec="seconds")
    conn.executemany(
        "INSERT INTO findings VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [(f["finding_id"], f["category"], f["title"], f["severity"], f["amount"], f["item_count"],
          f["description"], f["method"], f["limitation"], "OPEN", now) for f in reg.items])
    conn.executemany(
        "INSERT INTO finding_evidence VALUES (?,?,?,?)",
        [(l["finding_id"], l["evidence_id"], l["rel_path"], l["note"]) for l in reg.links])

    # 파인딩과 연결된 문서의 검토 우선순위를 올린다(검토 큐 반영).
    for link in reg.links:
        if link["evidence_id"]:
            conn.execute("UPDATE documents SET priority = priority + 3 WHERE doc_id = ?",
                         (link["evidence_id"],))
    conn.execute("""UPDATE documents SET suggested_relevance = CASE
        WHEN priority >= 8 THEN 'HIGH' WHEN priority >= 5 THEN 'MEDIUM'
        WHEN priority >= 3 THEN 'LOW' ELSE 'NOISE' END""")
    conn.commit()
    conn.close()

    return {"findings": reg.items, "links": len(reg.links), "benford": benford}


def main() -> None:
    ap = argparse.ArgumentParser(description="교차 문서 포렌식 분석")
    ap.add_argument("--db", default="data/case_2026_014/case.db")
    args = ap.parse_args()
    result = analyze(Path(args.db))
    print(f"[analytics] findings {len(result['findings'])}건, evidence links {result['links']}건")
    for f in result["findings"]:
        print(f"  {f['finding_id']} [{f['severity']:8s}] {f['title']}")
    if result["benford"].get("mad") is not None:
        print(f"  Benford MAD={result['benford']['mad']} (n={result['benford']['n']})")


if __name__ == "__main__":
    main()
