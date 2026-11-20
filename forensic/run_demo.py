#!/usr/bin/env python3
"""포렌식 케이스 워크벤치 데모.

코퍼스 생성 → 증거 수집·무결성 → 문서 검토 → 교차 분석 → 보고서까지
한 번에 실행하고, 검토자가 열어볼 수 있는 산출물만 demo/ 에 모은다.

    python3 forensic/run_demo.py --out demo

원본 코퍼스(demo/raw)는 저장소에 커밋하지 않는다. 대신 대표 문서 12건을
demo/samples/ 에 복사해 검토자가 문서 생김새를 바로 확인할 수 있게 한다.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
from pathlib import Path

from . import analytics, build_corpus, intake, report, review

SAMPLE_PATHS = [
    "approvals/AP-2026-0061.txt",
    "approvals/AP-2026-0064.txt",
    "quotes/Q-0081.txt",
    "quotes/Q-0082.txt",
    "invoices/INV-0081.txt",
    "invoices/INV-0083.txt",
    "card/card_transactions.csv",
    "minutes/MIN-001.txt",
]


def _pick_emails(raw: Path, keywords: list[str], limit: int = 4) -> list[str]:
    picks = []
    for path in sorted((raw / "emails").glob("*.eml")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(k in text for k in keywords):
            picks.append(f"emails/{path.name}")
        if len(picks) >= limit:
            break
    return picks


def run(out: Path, seed: int = build_corpus.SEED) -> dict:
    out = Path(out)
    raw = out / "raw"
    ev = out / "evidence"
    db = out / "case.db"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    # build_corpus 는 out_dir/raw 아래에 문서를, out_dir 에 ground_truth.json 을 만든다.
    gt = build_corpus.build(out, seed)
    intake_summary = intake.collect(raw, ev, custodian="forensic-intern", case_id=build_corpus.CASE_ID)
    integrity = intake.verify(ev / "evidence_manifest.csv", raw)
    stats = review.index(raw, ev / "evidence_manifest.csv", db)
    tag_stats = review.tag(db)
    console = review.export_console(db, out / "review_console.xlsx", top_n=80)
    analysis = analytics.analyze(db)
    qc = review.qc(db)
    (out / "qc_summary.json").write_text(json.dumps(qc, ensure_ascii=False, indent=2), encoding="utf-8")

    # findings.csv
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT * FROM findings ORDER BY finding_id").fetchall()
    cols = [c[1] for c in conn.execute("PRAGMA table_info(findings)")]
    conn.close()
    with (out / "findings.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        w.writerows(rows)

    rep = report.build(db, ev / "evidence_manifest.csv", out / "FINDINGS_REPORT.md",
                       out / "findings.xlsx", ev / "intake_summary.json", out / "qc_summary.json")

    # 대표 문서 표본
    samples = out / "samples"
    picked = [p for p in SAMPLE_PATHS if (raw / p).exists()]
    picked += _pick_emails(raw, ["한도 관련", "세영산업 견적", "주말에 정리", "재지급"], limit=4)
    for rel in picked:
        src = raw / rel
        dst = samples / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    summary = {
        "case_id": build_corpus.CASE_ID,
        "corpus": gt["files"],
        "intake": intake_summary,
        "integrity": integrity,
        "review": stats,
        "tagging": tag_stats,
        "console": console,
        "findings": len(analysis["findings"]),
        "benford_mad": analysis["benford"].get("mad"),
        "qc": qc,
        "samples": picked,
    }
    (out / "run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="포렌식 케이스 워크벤치 데모 실행")
    ap.add_argument("--out", default="demo")
    ap.add_argument("--seed", type=int, default=build_corpus.SEED)
    args = ap.parse_args()
    s = run(Path(args.out), args.seed)
    print(f"[run_demo] 케이스 {s['case_id']}")
    print(f"  증거 수집   : {s['intake']['file_count']}건, 무결성 {s['integrity']['result']}")
    print(f"  문서 검토   : {s['review']['documents']}건, 근접중복 클러스터 {s['review']['near_dup_clusters']}개, "
          f"스레드 {s['review']['threads']}개")
    print(f"  검토 큐     : {s['console']['queue_rows']}건 (review_console.xlsx)")
    print(f"  파인딩      : {s['findings']}건 (FINDINGS_REPORT.md)")
    print(f"  산출물 위치 : {args.out}/")


if __name__ == "__main__":
    main()
