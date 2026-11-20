#!/usr/bin/env python3
"""파이프라인 스모크 테스트.

정답(ground_truth.json)에 기록한 주입 패턴이 탐지되는지 확인한다.
탐지 코드는 ground_truth 를 읽지 않으므로, 이 테스트는 사후 대조다.

    python3 tests/smoke_test.py
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forensic import analytics, build_corpus, intake, review  # noqa: E402

EXPECTED_CATEGORIES = {
    "A1": "중복 지급",
    "A2": "분할 발주",
    "A3": "업체 마스터 이상",
    "A4": "일자 역전",
    "A5": "카드 사적 사용",
    "A9": "동일 견적",
}


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        raw = out / "raw"
        gt = build_corpus.build(out, seed=build_corpus.SEED)
        summary = intake.collect(raw, out / "evidence")
        integrity = intake.verify(out / "evidence" / "evidence_manifest.csv", raw)
        review.index(raw, out / "evidence" / "evidence_manifest.csv", out / "case.db")
        review.tag(out / "case.db")
        analytics.analyze(out / "case.db")

        conn = sqlite3.connect(out / "case.db")
        found = {r[0] for r in conn.execute("SELECT DISTINCT category FROM findings")}
        findings = conn.execute("SELECT finding_id, category, title FROM findings").fetchall()
        conn.close()

        failures = []
        if integrity["result"] != "PASS":
            failures.append(f"무결성 검증 실패: {integrity}")
        if summary["file_count"] != sum(gt["files"].values()):
            failures.append(f"수집 건수 불일치: {summary['file_count']} vs {gt['files']}")
        for cat, name in EXPECTED_CATEGORIES.items():
            if cat not in found:
                failures.append(f"{cat}({name}) 미탐지")

        print(f"[smoke] 수집 {summary['file_count']}건, 무결성 {integrity['result']}")
        for fid, cat, title in findings:
            print(f"  {fid} [{cat}] {title}")
        if failures:
            print("\n[smoke] FAIL")
            for f in failures:
                print(f"  - {f}")
            return 1
        print(f"\n[smoke] PASS — 주입 패턴 {len(EXPECTED_CATEGORIES)}종 모두 탐지")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
