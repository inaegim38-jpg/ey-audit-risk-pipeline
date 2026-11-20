# -*- coding: utf-8 -*-
from __future__ import annotations
import csv
import re
from pathlib import Path

import sys
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else None
if SRC is None:
    raise SystemExit('사용법: python python/load_seoul_expense.py <서울시 업무추진비 CSV 경로>')
OUT = Path("data/seoul_normalized.csv")
TIME_RE = re.compile(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?")

def main() -> None:
    count = 0
    with SRC.open("r", encoding="cp949", errors="replace", newline="") as fin, \
         OUT.open("w", encoding="utf-8", newline="") as fout:
        reader = csv.DictReader(fin)
        writer = csv.writer(fout)
        writer.writerow([
            "tx_id", "batch_id", "tx_timestamp", "posting_date",
            "account_id", "merchant_id", "merchant_category", "tx_type",
            "currency", "tx_amount", "evidence_doc_no", "approver_id",
            "department_code", "source_dataset", "is_simulated_fraud", "has_exact_time"
        ])

        for idx, row in enumerate(reader, start=1):
            dept = (row.get("부서명") or "").strip()
            place = (row.get("집행장소") or "").strip() or "UNKNOWN_PLACE"
            bimok = (row.get("비목") or "기타").strip()
            method = (row.get("결제방법") or "").strip()
            doc_id = (row.get("문서고유id") or "").strip()
            writer_name = (row.get("작성자") or "PUBLIC").strip()

            amt_raw = (row.get("집행금액") or "").replace(",", "").strip()
            try:
                amt = str(int(float(amt_raw))) if amt_raw else ""
            except ValueError:
                amt = ""

            dt_raw = (row.get("집행일시") or "").strip()
            m = TIME_RE.search(dt_raw)
            if m:
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                has_raw_time = m.group(4) is not None
                hh = int(m.group(4)) if has_raw_time else 12
                mm = int(m.group(5)) if has_raw_time else 0
                if not (0 <= hh <= 23 and 0 <= mm <= 59):
                    hh, mm, has_time = 12, 0, False
                elif hh == 0 and mm == 0:
                    # 00:00은 공공데이터 시각 미입력(현금/격려금 등) 플레이스홀더이므로 심야 오탐 방지
                    has_time = False
                else:
                    has_time = has_raw_time
                posting_date = f"{y:04d}-{mo:02d}-{d:02d}"
                tx_ts = f"{posting_date} {hh:02d}:{mm:02d}:00+09"
            else:
                posting_date = ""
                tx_ts = ""
                has_time = False

            tx_type = "CASH" if method == "현금" else "EXPENSE"
            writer.writerow([
                f"SEOUL_{idx:06d}", "BATCH_SEOUL_OA22156", tx_ts, posting_date,
                dept, place, f"SEOUL_{bimok}", tx_type,
                "KRW", amt, doc_id, writer_name,
                dept, "SEOUL_OA22156", "false", "true" if has_time else "false"
            ])
            count += 1
    print(f"[OK] Normalized {count:,} rows -> {OUT}")

if __name__ == "__main__":
    main()

