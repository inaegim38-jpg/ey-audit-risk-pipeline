# artifacts/portfolio_demo

`python scripts/portfolio_demo.py`로 재생성되는 **합성(가상) 데모 산출물**입니다. 실제 고객·기업 자료가 아닙니다.

| 파일 | 내용 |
|---|---|
| `transactions.csv` | 합성 거래 10건 |
| `documents.csv` | 합성 증빙 10건(중복 해시 1건 포함) |
| `dq_findings.csv` | 룰 검증 결과 후보 7건 |
| `review_items.csv` | 검토 큐(전부 UNREVIEWED로 시작) |
| `dashboard_summary.json` | 카드용 요약 |
| `powerbi_preview.png` | `scripts/render_portfolio_preview.py`가 그린 구성 미리보기(Power BI 화면 아님) |

결과는 검토 후보이며 부정 여부를 확정하지 않습니다. 테스트(`tests/test_portfolio_demo.py`)가 이 파일들이 코드로 재생성한 결과와 같은지 검사합니다.
