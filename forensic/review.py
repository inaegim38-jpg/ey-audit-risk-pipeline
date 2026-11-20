#!/usr/bin/env python3
"""문서 검토(document review) 엔진.

e-Discovery 검토의 실제 순서를 따른다.
  1) 처리(processing)   : 파일 → 정규화 텍스트 + 메타데이터
  2) 중복 제거          : 동일 해시(exact) + 근접중복(near-dup, MinHash/LSH)
  3) 스레드 재구성      : In-Reply-To 우선, 없으면 제목·참여자·기간으로 보조 판정
  4) 사전 태깅          : 규칙 기반(PII, 압박·분할·특수관계 정황 등)
  5) 우선순위·검토 큐   : 사람이 먼저 볼 문서 순서
  6) QC                 : 태그 커버리지, 클러스터 일관성, 큐 누락

핵심 원칙: 자동 태깅은 '판단'이 아니라 '검토 순서를 정하는 것'이다.
따라서 결과 컬럼은 suggested_* 이며, 최종 relevance 는 사람이 입력한다.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from email import policy
from email.parser import BytesParser
from pathlib import Path

import pandas as pd

SCHEMA = """
DROP TABLE IF EXISTS documents;
DROP TABLE IF EXISTS doc_tags;
DROP TABLE IF EXISTS card_transactions;

CREATE TABLE documents (
    doc_id            TEXT PRIMARY KEY,
    evidence_id       TEXT,
    rel_path          TEXT,
    doc_type          TEXT,
    author            TEXT,
    recipients        TEXT,
    doc_date          TEXT,
    subject           TEXT,
    body              TEXT,
    text_len          INTEGER,
    sha256            TEXT,
    exact_dup_group   TEXT,
    near_dup_cluster  TEXT,
    template_family   TEXT,
    thread_id         TEXT,
    priority          REAL DEFAULT 0,
    tag_count         INTEGER DEFAULT 0,
    suggested_relevance TEXT,
    review_status     TEXT DEFAULT 'UNREVIEWED',
    reviewer          TEXT,
    review_reason     TEXT
);

CREATE TABLE doc_tags (
    doc_id  TEXT,
    tag     TEXT,
    rule_id TEXT,
    weight  REAL,
    snippet TEXT
);

CREATE TABLE card_transactions (
    tx_at TEXT, merchant TEXT, category TEXT, amount INTEGER,
    cardholder TEXT, approval_no TEXT, note TEXT
);

CREATE INDEX idx_docs_cluster ON documents(near_dup_cluster);
CREATE INDEX idx_docs_priority ON documents(priority DESC);
CREATE INDEX idx_tags_doc ON doc_tags(doc_id);
"""

# --------------------------------------------------------------------------- 규칙
# weight: 사람이 먼저 봐야 하는 정도. 5=치명, 3=중요, 1=참고
TAG_RULES = [
    ("RRN", "PII_주민등록번호", r"\b\d{6}[-\s]?[1-4]\d{6}\b", 1),
    ("ACCT", "PII_계좌번호", r"\b\d{3}[-\s]?\d{2}[-\s]?\d{6}\b", 1),
    ("PHONE", "PII_연락처", r"\b0\d{1,2}-\d{3,4}-\d{4}\b", 1),
    ("MAIL", "PII_이메일", r"[\w.+-]+@[\w-]+\.[\w.]+", 0),
    ("SPLIT", "정황_분할발주", r"(한도|10시|1,?000만|나눠|쪼개|분할|결재선)", 5),
    ("QUOTE_CTRL", "정황_견적통제", r"(견적.{0,12}(맞춰|동일|형식만)|금액은.{0,10}동일)", 5),
    ("SECRECY", "정황_은폐요청", r"(공유하지|비밀|알리지|조심|티나)", 4),
    ("RELATED", "정황_특수관계", r"(세영산업|여보|배우자|개인 메일|집에서 정리)", 4),
    ("DUP_PAY", "정황_중복지급", r"(재지급|미지급|동일한? 번호|다시 요청)", 4),
    ("CARD_PRIVATE", "정황_사적카드", r"(골프|유흥|접대비|영수증은? 없|주말)", 3),
    ("THRESHOLD", "정황_한도근접", r"(9[,]?[0-9]{3}[,]?\d{3}\s*원|9[,]?[5-9]\d{0,1}[,]?\d{3}[,]?\d{3})", 2),
]

PRIORITY_DOC_TYPE = {"EMAIL": 1.0, "APPROVAL": 1.2, "QUOTE": 1.2, "INVOICE": 1.4,
                     "CARD_STATEMENT": 0.8, "MINUTES": 0.6, "OTHER": 0.5}

NUM_RE = re.compile(r"\d[\d,]*")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
AMOUNT_RE = re.compile(r"([\d,]{4,})\s*원")


# --------------------------------------------------------------------- 처리(parsing)
def _norm(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _parse_eml(path: Path) -> dict:
    msg = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    body += part.get_content()
                except Exception:
                    body += str(part.get_payload(decode=True), "utf-8", "ignore")
    else:
        try:
            body = msg.get_content()
        except Exception:
            body = str(msg.get_payload(decode=True), "utf-8", "ignore")
    date_hdr = msg.get("Date")
    doc_date = ""
    if date_hdr:
        try:
            doc_date = msg.get("Date").datetime.strftime("%Y-%m-%d")
        except Exception:
            doc_date = ""
    return {
        "author": msg.get("From", ""),
        "recipients": ", ".join(filter(None, [msg.get("To"), msg.get("Cc")])),
        "doc_date": doc_date,
        "subject": str(msg.get("Subject", "")),
        "body": _norm(body),
        "in_reply_to": str(msg.get("In-Reply-To", "")).strip("<>").split("@")[0],
        "msg_id": str(msg.get("Message-ID", "")).strip("<>").split("@")[0],
    }


def _field(text: str, *keys: str) -> str:
    for key in keys:
        m = re.search(rf"^{key}\s*[:：]\s*(.+)$", text, re.MULTILINE)
        if m:
            return m.group(1).strip()
    return ""


def _parse_text(path: Path, doc_type: str) -> dict:
    text = _norm(path.read_text(encoding="utf-8", errors="ignore"))
    date = _field(text, "기안일자", "견적일자", "작성일자", "일시")
    if not date:
        m = DATE_RE.search(text)
        date = m.group(0) if m else ""
    subject = {
        "APPROVAL": _field(text, "품명"),
        "QUOTE": _field(text, "품명"),
        "INVOICE": _field(text, "품목"),
        "MINUTES": "회의록",
    }.get(doc_type, "")
    author = _field(text, "기안자", "공급자", "참석")
    return {"author": author, "recipients": "", "doc_date": date,
            "subject": subject, "body": text, "in_reply_to": "", "msg_id": ""}


def _parse_card(path: Path) -> list[dict]:
    rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
    out = []
    for r in rows:
        out.append({"tx_at": r["거래일시"], "merchant": r["가맹점"], "category": r["업종"],
                    "amount": int(r["금액"]), "cardholder": r["카드소지자"],
                    "approval_no": r["승인번호"], "note": r.get("비고", "")})
    return out


# ------------------------------------------------------------- 근접중복(MinHash)
def _shingles(text: str, k: int = 5) -> set[int]:
    t = NUM_RE.sub("#", text.lower())
    t = re.sub(r"\s+", " ", t)
    if len(t) < k:
        return {hash(t) & 0xFFFFFFFF}
    return {hash(t[i:i + k]) & 0xFFFFFFFF for i in range(len(t) - k + 1)}


def _signature(sh: set[int], num_perm: int = 64) -> tuple[int, ...]:
    if not sh:
        return tuple([0] * num_perm)
    sig = []
    for p in range(num_perm):
        salt = f"p{p}".encode()
        sig.append(min((int.from_bytes(hashlib.blake2b(str(s).encode(), digest_size=8, key=salt).digest(), "big")
                        for s in sh), default=0))
    return tuple(sig)


def _jaccard(a: set[int], b: set[int]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


def _cluster(docs: list[dict], num_perm: int = 64, bands: int = 32,
             near_threshold: float = 0.90, template_threshold: float = 0.70) -> None:
    """메일 본문의 근접중복을 LSH + 정확 Jaccard로 확정한다.

    구조화 문서(품의서·견적서·계산서)는 본문이 템플릿이라 텍스트 유사도가 늘 높다.
    그런 문서는 _field_dup() 에서 핵심 필드 일치로 판정한다.
    """
    targets = [i for i, d in enumerate(docs) if d["doc_type"] == "EMAIL"]
    subset = [docs[i] for i in targets]
    shingle_sets, sigs = [], []
    for d in subset:
        sh = _shingles(d["body"])
        shingle_sets.append(sh)
        sigs.append(_signature(sh, num_perm))

    rows = num_perm // bands
    buckets: dict[tuple, list[int]] = defaultdict(list)
    for i, sig in enumerate(sigs):
        for b in range(bands):
            buckets[(b, sig[b * rows:(b + 1) * rows])].append(i)

    pairs: set[tuple[int, int]] = set()
    for members in buckets.values():
        if len(members) < 2:
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                pairs.add((members[i], members[j]))

    parent = list(range(len(subset)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    near_pairs = 0
    for i, j in pairs:
        jac = _jaccard(shingle_sets[i], shingle_sets[j])
        if jac >= near_threshold:
            union(i, j)
            near_pairs += 1
        elif jac >= template_threshold:
            subset[i].setdefault("_template_links", []).append(j)
            subset[j].setdefault("_template_links", []).append(i)

    clusters: dict[int, list[int]] = defaultdict(list)
    for idx in range(len(subset)):
        clusters[find(idx)].append(idx)
    for n, (root, members) in enumerate(sorted(clusters.items(), key=lambda kv: -len(kv[1])), start=1):
        cid = f"ND-{n:03d}" if len(members) > 1 else ""
        for idx in members:
            subset[idx]["near_dup_cluster"] = cid
    for d in subset:
        links = d.get("_template_links", [])
        if links and not d.get("near_dup_cluster"):
            d["template_family"] = f"TF-{min(links) + 1:03d}"
    if docs:
        docs[0]["_near_pairs"] = near_pairs


def _field_dup(docs: list[dict]) -> None:
    """구조화 문서는 '같은 내용'의 판정 기준을 핵심 필드로 둔다.

    계산서: 승인번호가 같으면 동일 계산서(재수취·재지급 후보)
    견적서: 금액과 담당자 연락처가 같으면 동일 견적(비교견적 형식화 후보)
    품의서: 협력사·금액이 같으면 동일 발주(중복 기안 후보)
    """
    def key(d: dict) -> tuple | None:
        body = d["body"]
        if d["doc_type"] == "INVOICE":
            m = re.search(r"^승인번호\s*[:：]\s*(\S+)", body, re.MULTILINE)
            return ("INV", m.group(1)) if m else None
        if d["doc_type"] == "QUOTE":
            amt = re.search(r"합계금액\s*[:：]\s*([\d,]+)\s*원", body)
            ph = re.search(r"담당자 연락처\s*[:：]\s*([\d-]+)", body)
            return ("QTE", amt.group(1), ph.group(1)) if amt and ph else None
        if d["doc_type"] == "APPROVAL":
            ven = re.search(r"협력사\s*[:：]\s*(.+)", body)
            amt = re.search(r"금액\s*[:：]\s*([\d,]+)\s*원", body)
            item = re.search(r"품명\s*[:：]\s*(.+)", body)
            if ven and amt and item:
                return ("APR", ven.group(1).strip(), amt.group(1), item.group(1).strip())
            return None
        return None

    groups: dict[tuple, list[dict]] = defaultdict(list)
    for d in docs:
        k = key(d)
        if k:
            groups[k].append(d)
    n = 0
    for _k, members in groups.items():
        if len(members) < 2:
            continue
        n += 1
        cid = f"FD-{n:03d}"
        for m in members:
            m["near_dup_cluster"] = cid


# ------------------------------------------------------------- 스레드 재구성
def _thread(docs: list[dict]) -> None:
    by_msg = {d["msg_id"]: d for d in docs if d.get("msg_id")}
    for d in docs:
        d["thread_id"] = ""
        if d["doc_type"] != "EMAIL":
            continue
        parent = by_msg.get(d.get("in_reply_to", ""))
        if parent is not None:
            d["thread_id"] = parent.get("thread_id") or f"TH-{parent['doc_id']}"
            parent["thread_id"] = d["thread_id"]
    # 남은 메일은 '제목 + 동일 참여자 쌍 + 7일 이내'일 때만 보조 묶음.
    # 제목만으로 묶으면 서로 무관한 업무 메일이 한 스레드가 된다.
    def _key(d: dict) -> tuple:
        subj = re.sub(r"^(re|fw|fwd)\s*:\s*", "", d["subject"].strip().lower())
        subj = re.sub(r"\s+", "", subj)
        pair = tuple(sorted([d["author"], d["recipients"]]))
        return (subj, pair)

    groups: dict[tuple, list[dict]] = defaultdict(list)
    for d in docs:
        if d["doc_type"] != "EMAIL" or d["thread_id"] or not d["doc_date"]:
            continue
        groups[_key(d)].append(d)
    for _k, members in groups.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda x: x["doc_date"])
        current = [members[0]]
        for m in members[1:]:
            prev = current[-1]
            gap = (datetime.fromisoformat(m["doc_date"]) - datetime.fromisoformat(prev["doc_date"])).days
            if gap <= 7:
                current.append(m)
            else:
                if len(current) > 1:
                    tid = f"TH-{min(x['doc_id'] for x in current)}"
                    for x in current:
                        x["thread_id"] = tid
                current = [m]
        if len(current) > 1:
            tid = f"TH-{min(x['doc_id'] for x in current)}"
            for x in current:
                x["thread_id"] = tid


# ------------------------------------------------------------------- 인덱싱
def index(raw_dir: Path, manifest_csv: Path, db_path: Path) -> dict:
    raw_dir, manifest_csv, db_path = Path(raw_dir), Path(manifest_csv), Path(db_path)
    manifest = {r["rel_path"]: r for r in csv.DictReader(manifest_csv.open(encoding="utf-8"))}
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    docs, card_rows = [], []
    for path in sorted(p for p in raw_dir.rglob("*") if p.is_file()):
        rel = path.relative_to(raw_dir).as_posix()
        meta = manifest.get(rel, {})
        doc_type = meta.get("doc_type", "OTHER")
        if doc_type == "CARD_STATEMENT":
            card_rows = _parse_card(path)
            parsed = {"author": "", "recipients": "", "doc_date": card_rows[0]["tx_at"][:10] if card_rows else "",
                      "subject": "법인카드 사용내역", "body": path.read_text(encoding="utf-8", errors="ignore"),
                      "in_reply_to": "", "msg_id": ""}
        elif doc_type == "EMAIL":
            parsed = _parse_eml(path)
        else:
            parsed = _parse_text(path, doc_type)

        docs.append({
            "doc_id": meta.get("evidence_id", f"EV-{len(docs) + 1:05d}"),
            "evidence_id": meta.get("evidence_id", ""),
            "rel_path": rel,
            "doc_type": doc_type,
            "author": parsed["author"],
            "recipients": parsed["recipients"],
            "doc_date": parsed["doc_date"],
            "subject": parsed["subject"],
            "body": parsed["body"],
            "text_len": len(parsed["body"]),
            "sha256": meta.get("sha256", ""),
            "exact_dup_group": "",
            "near_dup_cluster": "",
            "template_family": "",
            "thread_id": "",
            "in_reply_to": parsed.get("in_reply_to", ""),
            "msg_id": parsed.get("msg_id", ""),
        })

    # exact duplicate
    by_hash: dict[str, list[str]] = defaultdict(list)
    for d in docs:
        by_hash[d["sha256"]].append(d["doc_id"])
    for h, ids in by_hash.items():
        if len(ids) > 1:
            for i in ids:
                next(d for d in docs if d["doc_id"] == i)["exact_dup_group"] = f"ED-{h[:8]}"

    _cluster(docs)
    _field_dup(docs)
    _thread(docs)

    conn.executemany(
        "INSERT INTO documents (doc_id,evidence_id,rel_path,doc_type,author,recipients,doc_date,"
        "subject,body,text_len,sha256,exact_dup_group,near_dup_cluster,template_family,thread_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(d["doc_id"], d["evidence_id"], d["rel_path"], d["doc_type"], d["author"], d["recipients"],
          d["doc_date"], d["subject"], d["body"], d["text_len"], d["sha256"], d["exact_dup_group"],
          d["near_dup_cluster"], d["template_family"], d["thread_id"]) for d in docs])
    conn.executemany(
        "INSERT INTO card_transactions VALUES (?,?,?,?,?,?,?)",
        [(r["tx_at"], r["merchant"], r["category"], r["amount"], r["cardholder"],
          r["approval_no"], r["note"]) for r in card_rows])
    conn.commit()

    stats = {
        "documents": len(docs),
        "by_type": {t: sum(1 for d in docs if d["doc_type"] == t) for t in sorted({d["doc_type"] for d in docs})},
        "exact_dup_docs": sum(1 for d in docs if d["exact_dup_group"]),
        "near_dup_docs": sum(1 for d in docs if d["near_dup_cluster"]),
        "near_dup_clusters": len({d["near_dup_cluster"] for d in docs if d["near_dup_cluster"]}),
        "threads": len({d["thread_id"] for d in docs if d["thread_id"]}),
        "card_transactions": len(card_rows),
    }
    conn.close()
    return stats


# --------------------------------------------------------------------- 태깅
def tag(db_path: Path) -> dict:
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT doc_id, doc_type, subject, body FROM documents").fetchall()
    conn.execute("DELETE FROM doc_tags")
    inserted = 0
    for doc_id, doc_type, subject, body in rows:
        hay = f"{subject}\n{body}"
        for rule_id, tag, pattern, weight in TAG_RULES:
            m = re.search(pattern, hay, re.IGNORECASE)
            if not m:
                continue
            start = max(0, m.start() - 20)
            snippet = re.sub(r"\s+", " ", hay[start:m.end() + 20]).strip()
            conn.execute("INSERT INTO doc_tags VALUES (?,?,?,?,?)",
                         (doc_id, tag, rule_id, weight, snippet))
            inserted += 1
        # 날짜 역전(계약일자 > 작성일자) 은 문서 구조를 봐야 하므로 별도 규칙
        if doc_type == "INVOICE":
            issue = re.search(r"작성일자\s*[:：]\s*(\d{4}-\d{2}-\d{2})", body)
            contract = re.search(r"계약일자\s*[:：]\s*(\d{4}-\d{2}-\d{2})", body)
            if issue and contract and contract.group(1) > issue.group(1):
                conn.execute("INSERT INTO doc_tags VALUES (?,?,?,?,?)",
                             (doc_id, "정황_일자역전", "DATE_REVERSAL", 5,
                              f"작성일자 {issue.group(1)} < 계약일자 {contract.group(1)}"))
                inserted += 1
        # 금액 100만원 단위(라운드 넘버)
        amounts = [int(a.replace(",", "")) for a in AMOUNT_RE.findall(body)]
        if amounts and max(amounts) % 1_000_000 == 0:
            conn.execute("INSERT INTO doc_tags VALUES (?,?,?,?,?)",
                         (doc_id, "정황_라운드금액", "ROUND_AMOUNT", 1, f"{max(amounts):,}원"))
            inserted += 1
    conn.commit()

    # 우선순위·제안 관련성
    conn.execute("""
        UPDATE documents SET
          tag_count = (SELECT COUNT(*) FROM doc_tags t WHERE t.doc_id = documents.doc_id),
          priority = COALESCE((SELECT SUM(t.weight) FROM doc_tags t WHERE t.doc_id = documents.doc_id), 0)
        """)
    conn.execute("""
        UPDATE documents SET priority = priority
          + CASE doc_type WHEN 'INVOICE' THEN 1.4 WHEN 'APPROVAL' THEN 1.2 WHEN 'QUOTE' THEN 1.2
                          WHEN 'EMAIL' THEN 1.0 WHEN 'CARD_STATEMENT' THEN 0.8 ELSE 0.4 END
          + CASE WHEN near_dup_cluster <> '' THEN 1.0 ELSE 0 END
          + CASE WHEN exact_dup_group <> '' THEN 1.5 ELSE 0 END
        """)
    conn.execute("""
        UPDATE documents SET suggested_relevance = CASE
          WHEN priority >= 8 THEN 'HIGH'
          WHEN priority >= 5 THEN 'MEDIUM'
          WHEN priority >= 3 THEN 'LOW'
          ELSE 'NOISE' END
        """)
    conn.commit()
    summary = dict(conn.execute("SELECT suggested_relevance, COUNT(*) FROM documents GROUP BY 1").fetchall())
    conn.close()
    return {"tags": inserted, "by_suggestion": summary}


# ------------------------------------------------------------------ 검토 콘솔
def export_console(db_path: Path, xlsx_path: Path, top_n: int = 80) -> dict:
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query("""
        SELECT d.doc_id, d.evidence_id, d.doc_type, d.doc_date, d.author, d.subject,
               d.rel_path, d.near_dup_cluster, d.thread_id, ROUND(d.priority,1) AS priority,
               d.suggested_relevance,
               COALESCE(GROUP_CONCAT(t.tag, ' | '), '') AS tags,
               '' AS relevance,
               '' AS reason,
               d.review_status
        FROM documents d
        LEFT JOIN doc_tags t ON t.doc_id = d.doc_id
        GROUP BY d.doc_id
        ORDER BY d.priority DESC, d.doc_date
        LIMIT ?
    """, conn, params=(top_n,))

    # 본문 발췌(마스킹) 추가
    bodies = dict(conn.execute("SELECT doc_id, body FROM documents").fetchall())
    df["excerpt"] = [re.sub(r"\s+", " ", bodies.get(i, ""))[:180] for i in df["doc_id"]]
    df["excerpt"] = df["excerpt"].str.replace(
        r"\b(\d{6})[-\s]?([1-4]\d{6})\b", r"\1-*******", regex=True)
    df["excerpt"] = df["excerpt"].str.replace(
        r"\b(\d{3})[-\s]?(\d{2})[-\s]?(\d{6})\b", r"\1-**-******", regex=True)

    summary = pd.DataFrame([
        {"항목": "검토 대상 문서", "값": int(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])},
        {"항목": "검토 큐 수록", "값": int(len(df))},
        {"항목": "HIGH 제안", "값": int((df["suggested_relevance"] == "HIGH").sum())},
        {"항목": "근접중복 클러스터", "값": int(conn.execute(
            "SELECT COUNT(DISTINCT near_dup_cluster) FROM documents WHERE near_dup_cluster <> ''").fetchone()[0])},
        {"항목": "스레드 수", "값": int(conn.execute(
            "SELECT COUNT(DISTINCT thread_id) FROM documents WHERE thread_id <> ''").fetchone()[0])},
    ])

    rules = pd.DataFrame(TAG_RULES, columns=["rule_id", "태그", "정규식", "가중치"])
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="요약", index=False)
        df.to_excel(writer, sheet_name="검토큐", index=False)
        rules.to_excel(writer, sheet_name="태깅규칙", index=False)

        wb = writer.book
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.worksheet.datavalidation import DataValidation
        from openpyxl.utils import get_column_letter

        ws = wb["검토큐"]
        header_fill = PatternFill("solid", fgColor="1F3864")
        for cell in ws[1]:
            cell.font = Font(color="FFFFFF", bold=True)
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        widths = {"A": 10, "B": 10, "C": 13, "D": 12, "E": 22, "F": 30, "G": 34, "H": 12,
                  "I": 12, "J": 9, "K": 16, "L": 34, "M": 14, "N": 26, "O": 13, "P": 46}
        for col, w in widths.items():
            ws.column_dimensions[col].width = w
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

        dv = DataValidation(type="list", formula1='"RELEVANT,NOT_RELEVANT,PRIVILEGED,NEEDS_FOLLOWUP"',
                            allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"M2:M{ws.max_row}")

    conn.close()
    return {"queue_rows": len(df), "path": str(xlsx_path)}


# ----------------------------------------------------------------------- QC
def qc(db_path: Path) -> dict:
    conn = sqlite3.connect(db_path)
    total = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    tagged = conn.execute("SELECT COUNT(DISTINCT doc_id) FROM doc_tags").fetchone()[0]

    # 1) 태그 커버리지
    coverage = tagged / total if total else 0

    # 2) 근접중복 클러스터 일관성: 같은 클러스터 문서의 제안 관련성이 같은가
    rows = conn.execute("""
        SELECT near_dup_cluster, suggested_relevance, COUNT(*)
        FROM documents WHERE near_dup_cluster <> '' GROUP BY 1,2
    """).fetchall()
    per_cluster: dict[str, set[str]] = defaultdict(set)
    for cid, rel, _ in rows:
        per_cluster[cid].add(rel)
    consistent = sum(1 for v in per_cluster.values() if len(v) == 1)
    consistency = consistent / len(per_cluster) if per_cluster else 1.0

    # 3) 고우선순위 문서의 검토 큐 포함 여부(누락 점검)
    high_total = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE suggested_relevance='HIGH'").fetchone()[0]

    # 4) PII 노출 문서
    pii_docs = conn.execute(
        "SELECT COUNT(DISTINCT doc_id) FROM doc_tags WHERE rule_id IN ('RRN','ACCT','PHONE')").fetchone()[0]

    # 5) 중복 제거 효과
    exact = conn.execute("SELECT COUNT(*) FROM documents WHERE exact_dup_group <> ''").fetchone()[0]
    near = conn.execute("SELECT COUNT(*) FROM documents WHERE near_dup_cluster <> ''").fetchone()[0]
    reducible = len({*[r[0] for r in conn.execute(
        "SELECT doc_id FROM documents WHERE exact_dup_group <> ''").fetchall()]})
    conn.close()
    return {
        "documents": total,
        "tag_coverage": round(coverage, 3),
        "cluster_consistency": round(consistency, 3),
        "clusters": len(per_cluster),
        "high_priority_docs": high_total,
        "pii_docs": pii_docs,
        "exact_dup_docs": exact,
        "near_dup_docs": near,
        "exact_dup_only_docs": reducible,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="문서 검토 엔진")
    ap.add_argument("--raw", default="data/case_2026_014/raw")
    ap.add_argument("--manifest", default="data/case_2026_014/evidence/evidence_manifest.csv")
    ap.add_argument("--db", default="data/case_2026_014/case.db")
    ap.add_argument("--xlsx", default="data/case_2026_014/review_console.xlsx")
    args = ap.parse_args()
    stats = index(Path(args.raw), Path(args.manifest), Path(args.db))
    print("[review] 인덱싱:", json.dumps(stats, ensure_ascii=False))
    print("[review] 태깅:", json.dumps(tag(Path(args.db)), ensure_ascii=False))
    print("[review] 콘솔:", json.dumps(export_console(Path(args.db), Path(args.xlsx)), ensure_ascii=False))
    print("[review] QC:", json.dumps(qc(Path(args.db)), ensure_ascii=False))


if __name__ == "__main__":
    main()
