"""
python/export_review_sheet.py
검토 작업 목록(vw_review_worklist 상위 N건)을 담당자·검토상태·소명메모 열이 있는 Excel/CSV로 내보냅니다.
- 담당자/검토상태/소명메모는 사람이 채우는 열입니다.
- 검토상태 열에는 허용값(OPEN, IN_REVIEW, RESOLVED, FALSE_POSITIVE) 드롭다운이 들어갑니다.
- 이 파일에 적은 내용은 DB에 자동 반영되지 않습니다(반영은 audit_risk.set_issue_status 사용).
"""
import argparse
import pandas as pd
from sqlalchemy import create_engine
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.worksheet.datavalidation import DataValidation

DEFAULT_DB_URI = "postgresql+psycopg2://audit_admin:audit_password123@localhost:5432/audit_db"
STATUSES = ["OPEN", "IN_REVIEW", "RESOLVED", "FALSE_POSITIVE"]
COLS = {
    "finding_key": "이슈키", "rule_id": "룰", "severity": "심각도", "risk_score": "위험점수",
    "tx_id": "거래ID", "tx_amount": "금액(원)", "department_code": "부서", "merchant_category": "카테고리",
    "posting_date": "전기일", "detail_message": "탐지 내용", "issue_status": "검토상태",
    "reviewer": "담당자", "review_memo": "소명메모",
}
NOTE = ("본 시트는 BankSim 공개 시뮬레이션 데이터와 합성 증빙으로 만든 포트폴리오용 예시입니다. "
        "실제 기업 거래가 아니며, 검토상태가 채워진 행이 있다면 시연용 처리입니다.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--out", default="review_sheet")
    ap.add_argument("--db-uri", default=DEFAULT_DB_URI)
    a = ap.parse_args()

    eng = create_engine(a.db_uri)
    df = pd.read_sql(f"SELECT * FROM audit_risk.vw_review_worklist LIMIT {int(a.top)}", eng)
    df = df[list(COLS)].rename(columns=COLS)
    df["담당자"] = df["담당자"].fillna("")
    df["소명메모"] = df["소명메모"].fillna("")

    df.to_csv(f"{a.out}.csv", index=False, encoding="utf-8-sig")  # Excel에서 한글이 깨지지 않도록 BOM 포함
    with pd.ExcelWriter(f"{a.out}.xlsx", engine="openpyxl") as w:
        df.to_excel(w, sheet_name="검토목록", index=False)
        ws = w.sheets["검토목록"]
        head = PatternFill("solid", fgColor="1F3864")
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF"); c.fill = head
            c.alignment = Alignment(horizontal="center", vertical="center")
        for i, name in enumerate(df.columns, 1):
            width = max(len(str(name)) * 2, min(60, int(df[name].astype(str).str.len().max() or 0) + 2))
            ws.column_dimensions[ws.cell(1, i).column_letter].width = width
        ws.freeze_panes = "A2"
        n = len(df) + 1
        status_col = ws.cell(1, list(df.columns).index("검토상태") + 1).column_letter
        dv = DataValidation(type="list", formula1='"' + ",".join(STATUSES) + '"', allow_blank=False)
        ws.add_data_validation(dv)
        dv.add(f"{status_col}2:{status_col}{max(n, 2)}")
        info = w.book.create_sheet("안내")
        info["A1"] = NOTE
        info["A2"] = "검토상태 허용값: " + ", ".join(STATUSES)
        info["A3"] = "이 파일의 입력은 DB에 자동 반영되지 않습니다. 반영은 audit_risk.set_issue_status(...) 로 합니다."
        info.column_dimensions["A"].width = 120
    print(f"[OK] {len(df)}건 → {a.out}.xlsx / {a.out}.csv")


if __name__ == "__main__":
    main()
