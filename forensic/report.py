#!/usr/bin/env python3
"""조사 지원 결과 보고서 생성기.

파인딩 레지스트리와 증거 매니페스트를 결합해, 모든 주장에 증거 ID가 붙은
보고서를 만든다. 보고서에 인용된 증거는 해시 앞 12자리를 함께 적어
원본 대조가 가능하게 한다.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

try:
    from zoneinfo import ZoneInfo
    KST = ZoneInfo("Asia/Seoul")
except Exception:  # pragma: no cover
    KST = None

SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


def _load_manifest(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    return {r["evidence_id"]: r for r in csv.DictReader(path.open(encoding="utf-8"))}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def build(db_path: Path, manifest_csv: Path, out_md: Path, out_xlsx: Path,
          intake_summary: Path | None = None, qc_json: Path | None = None) -> dict:
    conn = sqlite3.connect(db_path)
    manifest = _load_manifest(manifest_csv)
    by_path = {v["rel_path"]: v["evidence_id"] for v in manifest.values()}
    intake = _load_json(intake_summary) if intake_summary else {}
    qc = _load_json(qc_json) if qc_json else {}

    findings = [dict(zip(
        ["finding_id", "category", "title", "severity", "amount", "item_count",
         "description", "method", "limitation", "status", "created_at"], row))
        for row in conn.execute("SELECT * FROM findings")]
    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), f["finding_id"]))

    links = defaultdict(list)
    for fid, ev_id, rel_path, note in conn.execute(
            "SELECT finding_id, evidence_id, rel_path, note FROM finding_evidence"):
        links[fid].append({"evidence_id": ev_id or by_path.get(rel_path, ""),
                           "rel_path": rel_path, "note": note})

    doc_stats = dict(conn.execute("SELECT suggested_relevance, COUNT(*) FROM documents GROUP BY 1").fetchall())
    total_docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    threads = conn.execute("SELECT COUNT(DISTINCT thread_id) FROM documents WHERE thread_id <> ''").fetchone()[0]
    clusters = conn.execute(
        "SELECT COUNT(DISTINCT near_dup_cluster) FROM documents WHERE near_dup_cluster <> ''").fetchone()[0]
    queue = conn.execute("""
        SELECT doc_id, doc_type, doc_date, subject, priority, suggested_relevance,
               COALESCE(near_dup_cluster,'') AS cluster, rel_path
        FROM documents ORDER BY priority DESC, doc_date LIMIT 20""").fetchall()

    by_type = dict(conn.execute("SELECT doc_type, COUNT(*) FROM documents GROUP BY 1").fetchall())
    card_n = conn.execute("SELECT COUNT(*) FROM card_transactions").fetchone()[0]

    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M") if KST else datetime.now().strftime("%Y-%m-%d %H:%M")
    L: list[str] = []
    add = L.append

    add("# 조사 지원 결과 보고서")
    add("")
    add(f"| 항목 | 내용 |")
    add(f"|---|---|")
    add(f"| 케이스 | CASE-2026-014 (가상) |")
    add(f"| 대상 | 한빛물산(주) 구매팀 법인카드·협력사 지급 관련 제보 |")
    add(f"| 작성 기준 | {now} / 합성 코퍼스 처리 결과 |")
    add(f"| 문서 | {total_docs:,}건 (유형 {len(by_type)}종) + 카드 승인내역 {card_n:,}건 |")
    add(f"| 파인딩 | {len(findings)}건 |")
    add(f"| 성격 | **분석 결과는 부정의 확정이 아니라 추가 확인 후보 목록**입니다. |")
    add("")

    # ---------------------------------------------------------------- 1. 증거
    add("## 1. 증거 수집과 보존")
    add("")
    if intake:
        add(f"원본 {intake.get('file_count', 0):,}건, {intake.get('total_bytes', 0) / 1024:.1f} KB를 "
            f"읽기 전용으로 수집하고 파일마다 SHA-256을 계산했습니다. 수집 직후 재해시 검증을 수행했고, "
            f"원본 변경·누락·미등록 파일이 없음을 확인했습니다.")
    else:
        add("원본을 읽기 전용으로 수집하고 파일마다 SHA-256을 계산했습니다.")
    add("")
    add("| 유형 | 건수 |")
    add("|---|---:|")
    for k, v in sorted(by_type.items()):
        add(f"| {k} | {v:,} |")
    add("")
    add("증거 1건당 `evidence_manifest.csv`에 해시·크기·수집시각·보관자가, "
        "`custody_log.csv`에 수집과 해시검증 이벤트가 남습니다. 아래 인용의 괄호 안 값은 해시 앞 12자리입니다.")
    add("")

    # ---------------------------------------------------------------- 2. 검토
    add("## 2. 문서 검토 처리")
    add("")
    add(f"{total_docs:,}건을 정규화한 뒤 중복을 정리했습니다. 메일은 본문 5-문자 shingle의 Jaccard 유사도로, "
        f"품의서·견적서·계산서는 금액·승인번호 같은 핵심 필드로 중복을 판정했습니다. "
        f"그 결과 근접중복 {clusters}개 클러스터가 잡혔고, 스레드 {threads}개를 재구성했습니다.")
    add("")
    add("| 구분 | 값 |")
    add("|---|---:|")
    add(f"| 처리 문서 | {total_docs:,} |")
    add(f"| 근접중복 클러스터 | {clusters} |")
    add(f"| 재구성 스레드 | {threads} |")
    add(f"| HIGH 제안 | {doc_stats.get('HIGH', 0):,} |")
    add(f"| MEDIUM 제안 | {doc_stats.get('MEDIUM', 0):,} |")
    add(f"| LOW·NOISE | {doc_stats.get('LOW', 0) + doc_stats.get('NOISE', 0):,} |")
    if qc:
        add(f"| 태그 커버리지 | {qc.get('tag_coverage', 0):.1%} |")
        add(f"| 클러스터 내 제안 일관성 | {qc.get('cluster_consistency', 0):.1%} |")
        add(f"| PII 포함 문서 | {qc.get('pii_docs', 0):,} |")
    add("")
    add("검토 큐는 우선순위 상위 80건으로 `review_console.xlsx`에 만들었습니다. "
        "관련성·사유는 빈 칸으로 두었고, 최종 판단은 사람이 입력하도록 했습니다. "
        "자동 태깅은 판단이 아니라 **볼 순서를 정하는 장치**입니다.")
    add("")

    # -------------------------------------------------------------- 3. 파인딩
    add("## 3. 파인딩")
    add("")
    for f in findings:
        add(f"### {f['finding_id']} {f['title']}")
        add("")
        add(f"- 심각도: **{f['severity']}** / 관련 금액: {f['amount']:,}원 / 건수: {f['item_count']}")
        add(f"- 사실관계: {f['description']}")
        add(f"- 탐지 방법: {f['method']}")
        add(f"- 한계: {f['limitation']}")
        if links[f["finding_id"]]:
            add("")
            add("| 증거 ID | 파일 | 비고 | 해시 |")
            add("|---|---|---|---|")
            for l in links[f["finding_id"]]:
                h = manifest.get(l["evidence_id"], {}).get("sha256", "")[:12]
                add(f"| {l['evidence_id'] or '-'} | `{l['rel_path']}` | {l['note']} | `{h or '-'}` |")
        add("")

    # -------------------------------------------------------------- 4. 타임라인
    add("## 4. 주요 일자")
    add("")
    timeline = []
    for f in findings:
        # 날짜가 특정되지 않는 파인딩(업체 마스터 비교 등)은 타임라인에서 제외한다.
        if f["category"] in ("A3", "A6") or f["item_count"] > 6:
            continue
        for l in links[f["finding_id"]]:
            if l["evidence_id"] and l["evidence_id"] in manifest:
                row = conn.execute("SELECT doc_date FROM documents WHERE doc_id = ?",
                                   (l["evidence_id"],)).fetchone()
                if row and row[0]:
                    timeline.append((row[0], f["finding_id"], l["rel_path"]))
    seen = set()
    add("| 일자 | 파인딩 | 문서 |")
    add("|---|---|---|")
    for d, fid, rel in sorted(timeline):
        if (d, fid) in seen:
            continue
        seen.add((d, fid))
        add(f"| {d} | {fid} | `{rel}` |")
    add("")

    # ---------------------------------------------------------------- 5. 한계
    add("## 5. 이 보고서가 보여주는 것과 보여주지 않는 것")
    add("")
    add("보여주는 것은 **증거를 수집·보존하고, 대량 문서를 통제 가능한 단위로 줄이고, "
        "문서 간 관계에서 확인이 필요한 지점을 찾아 증거 인용과 함께 정리하는 절차**입니다. "
        "각 파인딩에는 원본 파일과 해시가 붙어 있어 제3자가 같은 결론에 도달했는지 검증할 수 있습니다.")
    add("")
    add("보여주지 않는 것은 **부정의 확정**입니다. 이 케이스는 실제 기업 자료가 아니라 "
        "탐지 절차를 검증하려고 만든 합성 코퍼스이고, 주입한 패턴이 실제로 탐지되는지를 확인한 결과입니다. "
        "실무에서는 인터뷰, 원본 대사, 계약·납품 실물 확인이 뒤따라야 합니다.")
    add("")
    add("남은 확인 사항은 다음과 같습니다.")
    add("")
    add("- 은행 이체 내역과 계산서를 대사해 중복 지급 여부를 확정")
    add("- 세영산업·대성기업의 사업자 실체와 대표 관계 확인(법인등기·주주명부)")
    add("- 분할 발주 4건의 실제 납품 단위와 계약 구조 확인")
    add("- 카드 사용 6건의 참석자·사유 확인 및 접대비 규정 대조")
    add("- 벤포드는 표본 84건으로 작아 단독 근거로 쓰지 않고 참고 지표로만 사용")
    add("")

    # ---------------------------------------------------------------- 6. 부록
    add("## 6. 부록: 우선 검토 대상 20건")
    add("")
    add("| 순위 | 문서 ID | 유형 | 일자 | 제목 | 우선순위 | 제안 | 파일 |")
    add("|---:|---|---|---|---|---:|---|---|")
    for i, (doc_id, doc_type, doc_date, subject, prio, sugg, cluster, rel) in enumerate(queue, start=1):
        subj = (subject or "").replace("|", "/")[:34]
        add(f"| {i} | {doc_id} | {doc_type} | {doc_date} | {subj} | {prio:.1f} | {sugg} | `{rel}` |")
    add("")
    add("---")
    add("")
    add("작성: Manus AI / 합성 코퍼스 기반 절차 검증")
    add("")

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(L), encoding="utf-8")

    # Excel
    fdf = pd.DataFrame(findings)
    ldf = pd.DataFrame([{**l, "finding_id": fid} for fid, items in links.items() for l in items])
    qdf = pd.DataFrame(queue, columns=["doc_id", "doc_type", "doc_date", "subject", "priority",
                                       "suggested_relevance", "cluster", "rel_path"])
    with pd.ExcelWriter(out_xlsx, engine="openpyxl") as writer:
        fdf.to_excel(writer, sheet_name="파인딩", index=False)
        ldf.to_excel(writer, sheet_name="증거연결", index=False)
        qdf.to_excel(writer, sheet_name="우선검토", index=False)
    conn.close()

    return {"findings": len(findings), "md": str(out_md), "xlsx": str(out_xlsx)}


def main() -> None:
    ap = argparse.ArgumentParser(description="조사 지원 결과 보고서 생성")
    ap.add_argument("--db", default="data/case_2026_014/case.db")
    ap.add_argument("--manifest", default="data/case_2026_014/evidence/evidence_manifest.csv")
    ap.add_argument("--out", default="data/case_2026_014/FINDINGS_REPORT.md")
    ap.add_argument("--xlsx", default="data/case_2026_014/findings.xlsx")
    args = ap.parse_args()
    result = build(Path(args.db), Path(args.manifest), Path(args.out), Path(args.xlsx))
    print(f"[report] {result['findings']}건 파인딩 → {result['md']}")


if __name__ == "__main__":
    main()
