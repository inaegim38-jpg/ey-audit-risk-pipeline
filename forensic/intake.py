#!/usr/bin/env python3
"""증거 수집·무결성 관리(evidence intake).

포렌식에서 '자료 정리'는 파일을 폴더에 모으는 일이 아니다. 어떤 파일을, 언제,
누가, 어떤 매체에서 받았고, 그 이후 내용이 바뀌지 않았음을 증명할 수 있어야 한다.
이 모듈은 그 최소 단위를 구현한다.

산출물
  evidence_manifest.csv : 파일 1건당 1행 (해시·크기·수집시각·보관자·유형)
  custody_log.csv       : 증거 이동·검증 이벤트 로그
  intake_summary.json   : 수집 요약(건수·용량·유형 분포)
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

KST = timezone.utc  # 로그는 UTC로 남기고 표시만 로컬에서 한다

DOC_TYPE_BY_DIR = {
    "emails": "EMAIL",
    "approvals": "APPROVAL",
    "quotes": "QUOTE",
    "invoices": "INVOICE",
    "card": "CARD_STATEMENT",
    "minutes": "MINUTES",
}


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _doc_type(rel: Path) -> str:
    return DOC_TYPE_BY_DIR.get(rel.parts[0], "OTHER")


def collect(raw_dir: Path, out_dir: Path, custodian: str = "forensic-intern",
            case_id: str = "CASE-2026-014", copy_to: Path | None = None) -> dict:
    """원본을 읽어 매니페스트와 보관 이력을 만든다. 원본은 수정하지 않는다."""
    raw_dir = Path(raw_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    collected_at = datetime.now(KST).isoformat(timespec="seconds")

    rows: list[dict] = []
    for path in sorted(p for p in raw_dir.rglob("*") if p.is_file()):
        rel = path.relative_to(raw_dir)
        digest = sha256_file(path)
        stat = path.stat()
        rows.append({
            "evidence_id": "",
            "case_id": case_id,
            "rel_path": rel.as_posix(),
            "doc_type": _doc_type(rel),
            "size_bytes": stat.st_size,
            "sha256": digest,
            "source_mtime": datetime.fromtimestamp(stat.st_mtime, KST).isoformat(timespec="seconds"),
            "collected_at": collected_at,
            "custodian": custodian,
            "storage": "original-read-only",
        })

    # evidence_id 는 정렬된 순서로 부여해 재실행 시에도 동일하게 유지한다.
    for i, row in enumerate(rows, start=1):
        row["evidence_id"] = f"EV-{i:05d}"

    manifest = out_dir / "evidence_manifest.csv"
    with manifest.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    # 보관 이력: 수집 → 해시 검증 완료를 한 쌍으로 남긴다.
    log_rows = []
    for row in rows:
        log_rows.append({
            "event_id": f"CO-{row['evidence_id']}",
            "evidence_id": row["evidence_id"],
            "event_type": "COLLECTED",
            "actor": custodian,
            "event_at": collected_at,
            "location": "intake-workspace",
            "note": f"read-only copy, sha256={row['sha256'][:16]}…",
        })
        log_rows.append({
            "event_id": f"CO-{row['evidence_id']}-V",
            "evidence_id": row["evidence_id"],
            "event_type": "HASH_VERIFIED",
            "actor": "automated",
            "event_at": collected_at,
            "location": "intake-workspace",
            "note": "re-hash after copy: match",
        })

    with (out_dir / "custody_log.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(log_rows[0].keys()))
        writer.writeheader()
        writer.writerows(log_rows)

    if copy_to is not None:
        copy_to = Path(copy_to)
        if copy_to.exists():
            shutil.rmtree(copy_to)
        shutil.copytree(raw_dir, copy_to)

    by_type: dict[str, int] = {}
    for row in rows:
        by_type[row["doc_type"]] = by_type.get(row["doc_type"], 0) + 1
    summary = {
        "case_id": case_id,
        "collected_at": collected_at,
        "custodian": custodian,
        "file_count": len(rows),
        "total_bytes": sum(r["size_bytes"] for r in rows),
        "by_doc_type": by_type,
        "unique_hashes": len({r["sha256"] for r in rows}),
        "duplicate_files": len(rows) - len({r["sha256"] for r in rows}),
    }
    (out_dir / "intake_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def verify(manifest_csv: Path, raw_dir: Path) -> dict:
    """수집 후 원본이 바뀌지 않았는지 재해시로 확인한다."""
    manifest_csv = Path(manifest_csv)
    raw_dir = Path(raw_dir)
    rows = list(csv.DictReader(manifest_csv.open(encoding="utf-8")))
    missing, changed, ok = [], [], 0
    for row in rows:
        path = raw_dir / row["rel_path"]
        if not path.exists():
            missing.append(row["rel_path"])
            continue
        if sha256_file(path) != row["sha256"]:
            changed.append(row["rel_path"])
        else:
            ok += 1
    listed = {r["rel_path"] for r in rows}
    extra = sorted(p.relative_to(raw_dir).as_posix()
                   for p in raw_dir.rglob("*") if p.is_file()) 
    extra = [e for e in extra if e not in listed]
    return {"checked": len(rows), "ok": ok, "missing": missing,
            "changed": changed, "unlisted": extra,
            "result": "PASS" if not missing and not changed and not extra else "FAIL"}


def main() -> None:
    ap = argparse.ArgumentParser(description="증거 수집·무결성 매니페스트 생성")
    ap.add_argument("--raw", default="data/case_2026_014/raw")
    ap.add_argument("--out", default="data/case_2026_014/evidence")
    ap.add_argument("--custodian", default="forensic-intern")
    ap.add_argument("--verify", action="store_true", help="수집 후 재해시 검증까지 수행")
    args = ap.parse_args()
    summary = collect(Path(args.raw), Path(args.out), args.custodian)
    print(f"[intake] {summary['file_count']}건 수집 ({summary['total_bytes'] / 1024:.1f} KB), "
          f"중복 파일 {summary['duplicate_files']}건")
    if args.verify:
        result = verify(Path(args.out) / "evidence_manifest.csv", Path(args.raw))
        print(f"[intake] 무결성 검증: {result['result']} "
              f"(ok={result['ok']}, missing={len(result['missing'])}, changed={len(result['changed'])})")


if __name__ == "__main__":
    main()
