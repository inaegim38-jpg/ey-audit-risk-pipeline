"""
python/export_review_sheet.py
검토 작업 목록(vw_review_worklist: 미종결 CRITICAL/HIGH)을 데이터셋별로 나눠
담당자·검토상태·소명메모 열이 있는 Excel/CSV로 내보냅니다.

- 데이터셋(source_dataset)마다 별도 시트를 만들고, 각 시트에 상위 N건을 넣습니다.
    * BANKSIM_REAL  : BankSim 공개 '시뮬레이션' 거래 + 합성 증빙(결함은 기능 검증용으로 주입)
    * SEOUL_OA22156 : 서울시 본청 업무추진비 공개 데이터(실제 공개 지출, 증빙 없음 → EV 룰 미적용)
- '요약' 시트에는 데이터셋·룰별 미종결 CRITICAL/HIGH 건수(DB 조회값)를 넣습니다.
- 담당자/검토상태/소명메모는 사람이 채우는 열입니다. 이 파일의 입력은 DB에 자동 반영되지 않습니다
  (반영은 audit_risk.set_issue_status 사용).
- 실행 환경: 호스트 Python 3.8(32-bit)에는 패키지가 없어 python:3.12-slim 컨테이너에서 실행합니다(README 참고).
"""
import argparse

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from sqlalchemy import create_engine, text

DEFAULT_DB_URI = "postgresql+psycopg2://audit_admin:audit_password123@localhost:5432/audit_db"
STATUSES = ["OPEN", "IN_REVIEW", "RESOLVED", "FALSE_POSITIVE"]
DATASETS = {
    "BANKSIM_REAL": ("BankSim", "BankSim 공개 시뮬레이션 거래 + 합성 증빙. 실제 기업 거래가 아니며, "
                                "증빙 결함(누락·금액 불일치·일자 괴리·해시 중복)은 기능 검증용으로 주입한 것입니다."),
    "SEOUL_OA22156": ("서울시", "서울시 본청 업무추진비 공개 데이터(OA-22156). 실제 공개 지출이지만 증빙이 없어 "
                               "EV 룰은 적용하지 않았습니다. 탐지는 '추가 확인 후보'일 뿐 부정·위반 판정이 아닙니다."),
}
COLS = {
    "finding_key": "이슈키", "source_dataset": "데이터셋", "rule_id": "룰", "rule_name": "룰명",
    "severity": "심각도", "risk_score": "위험점수", "tx_id": "거래ID", "tx_amount": "금액(원)",
    "department_code": "부서", "merchant_category": "카테고리", "posting_date": "전기일",
    "detail_message": "탐지 내용", "issue_status": "검토상태", "reviewer": "담당자", "review_memo": "소명메모",
}
NOTE = ("본 시트는 포트폴리오용 검토 목록 예시입니다. threshold_value는 법적 기준이 아닌 프로젝트 가정값입니다. "
        "검토상태가 OPEN이 아닌 행이 있다면 시연용 처리(sql/04_demo_review.sql)이며 실제 검토 결과가 아닙니다.")

WORKLIST_SQL = text("""
    SELECT w.*, r.rule_name
    FROM audit_risk.vw_review_worklist w
    JOIN audit_risk.validation_rules r ON r.rule_id = w.rule_id
    WHERE w.source_dataset = :ds
    ORDER BY CASE w.severity WHEN 'CRITICAL' THEN 1 ELSE 2 END, w.risk_score DESC, w.tx_amount DESC, w.finding_key
    LIMIT :top
""")
SUMMARY_SQL = text("""
    SELECT w.source_dataset AS "데이터셋", w.severity AS "심각도", w.rule_id AS "룰", r.rule_name AS "룰명",
           COUNT(*) AS "미종결 건수"
    FROM audit_risk.vw_review_worklist w
    JOIN audit_risk.validation_rules r ON r.rule_id = w.rule_id
    GROUP BY 1, 2, 3, 4
    ORDER BY 1, CASE w.severity WHEN 'CRITICAL' THEN 1 ELSE 2 END, 5 DESC
""")


def style_sheet(ws, df, with_status=True):
    head = PatternFill("solid", fgColor="1F3864")
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = head
        c.alignment = Alignment(horizontal="center", vertical="center")
    for i, name in enumerate(df.columns, 1):
        longest = int(df[name].astype(str).str.len().max() or 0) if len(df) else 0
        ws.column_dimensions[ws.cell(1, i).column_letter].width = max(len(str(name)) * 2, min(60, longest + 2))
    ws.freeze_panes = "A2"
    if with_status:
        n = len(df) + 1
        col = ws.cell(1, list(df.columns).index("검토상태") + 1).column_letter
        dv = DataValidation(type="list", formula1='"' + ",".join(STATUSES) + '"', allow_blank=False)
        ws.add_data_validation(dv)
        dv.add(f"{col}2:{col}{max(n, 2)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=50, help="데이터셋별 상위 N건")
    ap.add_argument("--out", default="review_sheet")
    ap.add_argument("--db-uri", default=DEFAULT_DB_URI)
    a = ap.parse_args()

    eng = create_engine(a.db_uri)
    frames = {}
    with eng.connect() as con:
        for ds in DATASETS:
            df = pd.read_sql(WORKLIST_SQL, con, params={"ds": ds, "top": int(a.top)})
            df = df[list(COLS)].rename(columns=COLS)
            df["담당자"] = df["담당자"].fillna("")
            df["소명메모"] = df["소명메모"].fillna("")
            frames[ds] = df
        summary = pd.read_sql(SUMMARY_SQL, con)

    combined = pd.concat(frames.values(), ignore_index=True)
    combined.to_csv(f"{a.out}.csv", index=False, encoding="utf-8-sig")  # Excel 한글 깨짐 방지(BOM)

    with pd.ExcelWriter(f"{a.out}.xlsx", engine="openpyxl") as w:
        summary.to_excel(w, sheet_name="요약", index=False)
        style_sheet(w.sheets["요약"], summary, with_status=False)
        for ds, df in frames.items():
            sheet = f"검토목록_{DATASETS[ds][0]}"
            df.to_excel(w, sheet_name=sheet, index=False)
            style_sheet(w.sheets[sheet], df)
        info = w.book.create_sheet("안내")
        info["A1"] = NOTE
        for i, (ds, (_, desc)) in enumerate(DATASETS.items(), start=2):
            info[f"A{i}"] = f"{ds}: {desc}"
        info[f"A{len(DATASETS) + 2}"] = "검토상태 허용값: " + ", ".join(STATUSES)
        info[f"A{len(DATASETS) + 3}"] = ("이 파일의 입력은 DB에 자동 반영되지 않습니다. "
                                         "반영은 audit_risk.set_issue_status(...) 로 합니다.")
        info.column_dimensions["A"].width = 140

    counts = ", ".join(f"{ds} {len(df)}건" for ds, df in frames.items())
    print(f"[OK] {counts} → {a.out}.xlsx / {a.out}.csv")


if __name__ == "__main__":
    main()
