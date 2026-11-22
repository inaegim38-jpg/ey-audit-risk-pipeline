# Power BI 대시보드 명세 — 감사·리스크 자료 정합성 및 검토 이슈 트래커

> 상태: **명세만 작성됨. `.pbix` 파일은 이 저장소에 없고, Power BI Desktop에서 이 명세대로 만드는 작업은 아직 하지 않았습니다.**
> `artifacts/portfolio_demo/powerbi_preview.png`는 matplotlib로 그린 구성 미리보기이며 Power BI 화면이 아닙니다.
> 데이터는 합성(가상)이고 결과는 검토 후보입니다.

## 데이터 연결
PostgreSQL `localhost:5432` / DB `audit_db` / 스키마 `portfolio`. 가져오기(Import) 모드. 모델에 넣을 뷰:

| 뷰 | 단위 | 용도 |
|---|---|---|
| `portfolio.vw_pbi_dq_risk_queue` | 후보 1건 | Page 1 |
| `portfolio.vw_pbi_review_tracker` | 검토 항목 1건 | Page 2 |
| `portfolio.vw_pbi_issue_log` | 이슈(보완 요청) 1건 | Page 2 보조 |
| `portfolio.vw_pbi_kpi_summary` | engagement 1행 | 카드 검산용 |

관계: `vw_pbi_dq_risk_queue[review_item_id]` 1 — 1 `vw_pbi_review_tracker[review_item_id]`, `vw_pbi_review_tracker[review_item_id]` 1 — * `vw_pbi_issue_log[review_item_id]`.

## DAX 측정값 (5개)
```DAX
Finding Count = COUNTROWS(vw_pbi_dq_risk_queue)

Exposure Amount = SUM(vw_pbi_dq_risk_queue[amount])

Critical High Count =
CALCULATE([Finding Count], vw_pbi_dq_risk_queue[severity] IN {"CRITICAL", "HIGH"})

Open Review Count =
CALCULATE(COUNTROWS(vw_pbi_review_tracker), vw_pbi_review_tracker[is_open_review] = TRUE())

Review Completion Rate (%) =
DIVIDE(
    CALCULATE(COUNTROWS(vw_pbi_review_tracker),
              vw_pbi_review_tracker[review_status] IN {"RESOLVED", "FALSE_POSITIVE"}),
    COUNTROWS(vw_pbi_review_tracker)
) * 100
```
`Exposure Amount`는 후보에 걸린 거래 금액의 합이며 손실액이 아닙니다. 한 거래가 여러 룰에 걸리면 중복 합산될 수 있어, 카드에는 "노출 금액(후보 기준)"이라고 표기합니다.

## Page 1 — Data Quality & Risk Queue
- 카드 4: `Finding Count`, `Exposure Amount`, `Critical High Count`, `Open Review Count`
- 막대: 축 `severity`(정렬 `severity_rank`), 값 `Finding Count`
- 막대: 축 `rule_id`, 값 `Finding Count`
- 표: `suggested_priority`(내림차순), `rule_id`, `severity`, `transaction_id`, `document_id`, `amount`, `review_status`, `detail`
- 슬라이서: `engagement_id`, `severity`, `rule_id`

## Page 2 — Review & Issue Tracker
- 카드: `Open Review Count`, `Review Completion Rate (%)`
- 도넛: 범례 `review_status`, 값 개수
- 표(검토 항목): `review_item_id`, `rule_id`, `severity`, `review_status`, `reviewer`, `age_days`, `open_issue_count`, `issue_types`, `next_due_date`
- 표(이슈): `vw_pbi_issue_log`의 `issue_type`, `owner`, `due_date`, `disposition`, `deliverable_reflected`
- 슬라이서: `reviewer`, `disposition`

## 표기 문구 (모든 페이지 하단)
"합성(가상) 데이터 기반 데모입니다. 탐지 결과는 검토 후보이며 부정 여부를 확정하지 않습니다. 최종 판단은 검토자가 합니다."

## 갱신(Refresh) 상태
- 로컬 Power BI Desktop에서는 `새로 고침`으로 DB를 다시 읽습니다.
- Power BI Service(클라우드) 갱신은 서비스 주체 권한·게시된 데이터셋·게이트웨이가 필요하며 **연결하지 않았습니다.** WF-B는 갱신 이력을 `portfolio.refresh_log`에 `MOCKED`로만 남깁니다.
