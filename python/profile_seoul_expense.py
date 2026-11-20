# -*- coding: utf-8 -*-
from __future__ import annotations
import argparse
import csv
import math
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BENFORD_EXPECTED = {d: math.log10(1.0 + 1.0 / d) for d in range(1, 10)}
TIME_RE = re.compile(r"\b\d{1,2}:\d{2}(:\d{2})?\b")
DATE_RE = re.compile(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})")

def detect_encoding(path: Path) -> str:
    for enc in ("utf-8-sig", "cp949", "euc-kr", "utf-8"):
        try:
            with path.open("r", encoding=enc, newline="") as f:
                for _ in range(5000):
                    if not f.readline():
                        break
            return enc
        except UnicodeDecodeError:
            continue
    return "cp949"

def find_col(cols: List[str], candidates: List[str]) -> Optional[int]:
    norm = {c.replace(" ", "").strip(): i for i, c in enumerate(cols)}
    for cand in candidates:
        key = cand.replace(" ", "")
        if key in norm:
            return norm[key]
    for i, c in enumerate(cols):
        c_norm = c.replace(" ", "")
        for cand in candidates:
            if cand.replace(" ", "") in c_norm:
                return i
    return None

def parse_amount(raw: str) -> Optional[float]:
    s = raw.replace(",", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--out", default="SEOUL_EXPENSE_PROFILE.md")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    used_enc = detect_encoding(csv_path)

    total_rows = 0
    null_counts: Dict[str, int] = defaultdict(int)
    has_time = 0
    date_only = 0
    dt_samples: List[str] = []

    amt_fail = 0
    amt_le_zero = 0
    pos_amounts: List[float] = []
    round_10k = 0
    round_100k = 0
    first_digits: Counter = Counter()
    near_dup_groups: Dict[Tuple[str, int], List[int]] = defaultdict(list)

    with csv_path.open("r", encoding=used_enc, errors="replace", newline="") as f:
        reader = csv.reader(f)
        headers = next(reader, [])
        idx_dept = find_col(headers, ["부서명", "부서"])
        idx_dt = find_col(headers, ["사용일시", "사용일자", "집행일시"])
        idx_place = find_col(headers, ["사용장소", "사용 장소", "가맹점"])
        idx_amt = find_col(headers, ["집행금액", "사용금액", "금액"])
        idx_purpose = find_col(headers, ["집행목적", "사용목적"])
        idx_method = find_col(headers, ["결재방법", "결제방법"])

        role_map = [
            ("부서명", idx_dept), ("사용일시", idx_dt), ("사용장소", idx_place),
            ("집행금액", idx_amt), ("집행목적", idx_purpose), ("결재방법", idx_method),
        ]

        for row in reader:
            total_rows += 1
            rlen = len(row)

            for role, idx in role_map:
                if idx is not None:
                    val = row[idx].strip() if idx < rlen else ""
                    if val == "":
                        null_counts[role] += 1

            dt_val = row[idx_dt].strip() if (idx_dt is not None and idx_dt < rlen) else ""
            d_ord: Optional[int] = None
            if dt_val:
                if len(dt_samples) < 5:
                    dt_samples.append(dt_val)
                if TIME_RE.search(dt_val):
                    has_time += 1
                elif DATE_RE.search(dt_val):
                    date_only += 1
                m = DATE_RE.search(dt_val)
                if m:
                    try:
                        d_ord = date(int(m.group(1)), int(m.group(2)), int(m.group(3))).toordinal()
                    except ValueError:
                        d_ord = None

            amt_val: Optional[float] = None
            if idx_amt is not None:
                raw_a = row[idx_amt] if idx_amt < rlen else ""
                amt_val = parse_amount(raw_a)
                if amt_val is None:
                    amt_fail += 1
                else:
                    if amt_val <= 0:
                        amt_le_zero += 1
                    else:
                        pos_amounts.append(amt_val)
                        int_amt = int(round(amt_val))
                        if int_amt % 10000 == 0:
                            round_10k += 1
                        if int_amt % 100000 == 0:
                            round_100k += 1
                        if int_amt >= 10:
                            fd = int(str(int_amt)[0])
                            if 1 <= fd <= 9:
                                first_digits[fd] += 1

            if d_ord is not None and amt_val is not None and amt_val > 0 and idx_place is not None:
                place_val = row[idx_place].strip() if idx_place < rlen else ""
                if place_val:
                    near_dup_groups[(place_val, int(round(amt_val)))].append(d_ord)

    lines = [
        "# 서울시 업무추진비 데이터 프로파일링 리포트",
        f"- 파일: `{csv_path.name}` (인코딩: `{used_enc}`, 총 행수: `{total_rows:,}`)",
        f"- 전체 컬럼 목록: `{headers}`",
        "",
        "## 1. 컬럼 매핑 및 결측률",
        "| 역할 | 탐지된 원본 컬럼 | 결측 건수 | 결측률(%) |",
        "| :--- | :--- | ---: | ---: |",
    ]
    for role, idx in role_map:
        if idx is not None:
            nc = null_counts[role]
            lines.append(f"| {role} | `{headers[idx]}` | {nc:,} | {nc / max(total_rows, 1) * 100:.2f}% |")
        else:
            lines.append(f"| {role} | **미탐지** | - | - |")

    if idx_dt is not None:
        other_dt = total_rows - has_time - date_only
        lines += [
            "",
            "## 2. `사용일시` 시각(HH:MM) 포함 여부 (심야 룰 `AN_03` 적용성 판단)",
            f"- `HAS_TIME` (시:분 포함): **{has_time:,}건**",
            f"- `DATE_ONLY` (일자만 존재): **{date_only:,}건**",
            f"- `MISSING` / 기타: **{other_dt:,}건**",
            f"- 샘플 값(상위 5개): `{dt_samples}`",
        ]

    if idx_amt is not None and pos_amounts:
        pos_amounts.sort()
        n_pos = len(pos_amounts)
        n_valid_all = n_pos + amt_le_zero
        p50 = pos_amounts[n_pos // 2]
        p95 = pos_amounts[int(n_pos * 0.95)]
        lines += [
            "",
            "## 3. 집행금액 분포 및 라운드 금액 현황",
            f"- 숫자 변환 실패/결측: **{amt_fail:,}건** | 0원 이하: **{amt_le_zero:,}건** | 양수 건수: **{n_pos:,}건**",
            f"- 최솟값(양수): `{pos_amounts[0]:,.0f}원` | 중앙값(p50): `{p50:,.0f}원` | p95: `{p95:,.0f}원` | 최댓값: `{pos_amounts[-1]:,.0f}원`",
            f"- 1만원 단위 라운드 금액 비율(양수 중): **{round_10k / n_pos * 100:.2f}%**",
            f"- 10만원 단위 라운드 금액 비율(양수 중): **{round_100k / n_pos * 100:.2f}%**",
        ]

        n_bf = sum(first_digits.values())
        if n_bf >= 100:
            obs_pct = {d: round(first_digits[d] / n_bf * 100, 2) for d in range(1, 10)}
            exp_pct = {d: round(BENFORD_EXPECTED[d] * 100, 2) for d in range(1, 10)}
            mad = sum(abs(first_digits[d] / n_bf - BENFORD_EXPECTED[d]) for d in range(1, 10)) / 9.0
            chi2 = sum(((first_digits[d] - n_bf * BENFORD_EXPECTED[d]) ** 2) / (n_bf * BENFORD_EXPECTED[d]) for d in range(1, 10))
            span = math.log10(pos_amounts[-1]) - math.log10(pos_amounts[0]) if pos_amounts[0] > 0 else 0.0
            lines += [
                "",
                "## 4. 벤포드 법칙(1차 자릿수) 사전 적합성 검정",
                f"- 표본 수: `{n_bf:,}건` | 자릿수 폭(`log10(max/min)`): `{span:.2f} orders`",
                f"- 전제 충족 여부(N>=500 & 폭>=2.0): **`{n_bf >= 500 and span >= 2.0}`**",
                f"- MAD: `{mad:.5f}` (Nigrini 기준 <=0.015 적합) | Chi-Square: `{chi2:.2f}`",
                f"- 실제 분포(%): `{obs_pct}`",
                f"- 이론 분포(%): `{exp_pct}`",
            ]

    near_dup_cnt = 0
    for dates in near_dup_groups.values():
        if len(dates) > 1:
            dates.sort()
            for i in range(1, len(dates)):
                if dates[i] - dates[i - 1] <= 3:
                    near_dup_cnt += 1
    lines += [
        "",
        "## 5. 근접 일자(±3일) 동일 사용장소·금액 반복 후보",
        f"- 탐지 후보 건수: **{near_dup_cnt:,}건**",
    ]

    Path(args.out).write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] Saved {args.out}")

if __name__ == "__main__":
    main()
