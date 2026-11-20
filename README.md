# EY한영 감사부문 리스크관리 지원 포트폴리오
## 거래·증빙 데이터 정합성 검증 및 이상 징후 모니터링 자동화 파이프라인

> 감사부문 리스크관리 지원 인턴 직무의 핵심 과업(자료·데이터 관리, 문서·증빙 검토, 조사·분석 지원)을 데이터 파이프라인으로 구현한 프로젝트. 가상/합성 데이터 기반 프로토타입입니다.

## 1. 문제 정의
- **As-Is**: 거래-증빙을 엑셀로 반복 대조하며 결측·중복·금액 불일치를 놓치고, 단순 금액 필터로는 분할결제/통계적 이상치를 못 잡으며, 보고용 대시보드 갱신이 수작업.
- **To-Be**: n8n이 정규화·오류 분리 적재, PostgreSQL 검증 엔진이 멱등(`ON CONFLICT`)하게 탐지, Slack 경보와 Power BI 갱신 이력까지 일원화.

## 2. 아키텍처
```text
[Webhook] ─► [Code: 정규화·스키마 검증] ─► [If 오류?] ─ 예 ─► [PG: DQ_00 격리] ──┐
                                                  └ 아니오 ─► [PG: 원장 Upsert] ──┤
                                                                                ▼
                                                                       [Merge 합류]
                                                                                ▼
                                                              [PG: 9대 검증엔진]
                                                                                ▼
                                                          [If Critical/High > 0] ─► [Slack 경보(비활성)]
                                                                                ▼
                                                  [Power BI Refresh (Mock)] ─► [PG: pbi_refresh_history]
```
Schedule(6시간) 트리거는 Merge를 거치지 않고 검증 엔진으로 바로 연결됩니다.

## 3. 검증 룰
| 구분 | Rule | 내용 | 심각도 |
|---|---|---|---|
| DQ | DQ_00 | 스키마 파싱 오류 (n8n 단계에서 격리) | HIGH |
| DQ | DQ_01~03 | 필수값 결측 / 0원 이하 / 중복 거래·증빙 해시 | HIGH~CRITICAL |
| 증빙 | EV_01~03 | 50만원 이상 증빙 누락 / 금액 불일치 / 일자 괴리 | MEDIUM~CRITICAL |
| 이상 | AN_01~03 | Z-Score 3σ / 100만원 한도 회피 분할결제 / 심야·주말 고액 | MEDIUM~CRITICAL |

## 4. 데이터 출처와 한계
- **거래 데이터**: BankSim(Kaggle `ealaxi/banksim1`, 공개 **시뮬레이션** 은행 거래 59만 행)의 실제 값(고객·가맹점·카테고리·금액·사기 라벨)을 사용합니다. 실제 기업의 거래가 아닙니다.
- **가정으로 채운 부분**: 시각(원본은 일 단위 step, 12:00 고정), 금액(유로→원화, 환율 1,400 가정), 부서(고객 연령대 코드로 대체).
- **합성 증빙**: 공개 데이터에 증빙이 없어 거래에서 파생해 생성하고, 증빙 결함(누락·금액 불일치·날짜 괴리·해시 중복)을 소량 시뮬레이션으로 주입합니다(`--no-evidence-defects`로 끌 수 있음). EV_*, DQ_03 결과는 이 시뮬레이션의 영향을 받습니다.
- **기준선**: `baseline_stats`는 적재된 실제 데이터에서 카테고리별 평균·표준편차를 계산합니다.
- **미검증**: Slack 알림과 Power BI REST 갱신은 자격증명이 없어 실제 호출하지 않았습니다.
- 탐지 건수는 성능 지표가 아니라 룰 동작 확인용 결과입니다.

## 5. 구조
```text
docker-compose.yml          # PostgreSQL 16 + n8n
sql/01_init_and_verify.sql  # DDL, 기준 데이터, 20건 시나리오, 1차 검증 (02 포함)
sql/02_run_dq_engine.sql    # 재실행용 엔진 + Power BI 뷰 (tx_summary, priority_queue)
sql/03_issue_review.sql     # 이슈 검토 상태 추적(열, 함수, 뷰)
sql/04_demo_review.sql      # 시연용 상태 변경 예시
python/load_banksim_real.py # 실제 BankSim 적재 (권장)
python/prepare_audit_dataset.py  # 합성 데이터 생성기 (대체용)
python/export_review_sheet.py    # 검토 작업 목록 Excel/CSV 내보내기
n8n/ey_audit_risk_workflow.json
powerbi/dax_measures.txt
VERIFICATION_REPORT.md
```

## 6. 실행
```bash
docker compose up -d
docker cp sql/. ey_audit_postgres:/tmp/sql
docker exec -i ey_audit_postgres psql -U audit_admin -d audit_db -f /tmp/sql/01_init_and_verify.sql
# BankSim CSV를 data/ 아래에 내려받은 뒤 (kagglehub: ealaxi/banksim1)
python python/load_banksim_real.py --csv data/.../bs140513_032310.csv --rows 20000
docker exec -i ey_audit_postgres psql -U audit_admin -d audit_db -f /tmp/sql/02_run_dq_engine.sql
docker exec -i ey_audit_postgres psql -U audit_admin -d audit_db -f /tmp/sql/03_issue_review.sql
```
n8n(http://localhost:5678)에서 워크플로우를 Import하고 Postgres Credential(Host `postgres`, DB `audit_db`, User `audit_admin`)을 연결하면 실행됩니다. Slack 노드는 비활성이고, Power BI 갱신 노드는 Mock입니다. 실제 연동하려면 Slack 자격증명과 Power BI REST(HTTP Request) 노드로 교체가 필요합니다.

## 7. 검토 상태 추적과 검토 산출물
- `sql/03_issue_review.sql`: 이슈 검토 상태(OPEN / IN_REVIEW / RESOLVED / FALSE_POSITIVE), 담당자, 소명메모를 기록하는 열·함수·뷰를 추가합니다. 엔진(02)을 다시 실행해도 담당자가 바꾼 상태는 덮어쓰지 않습니다(`ON CONFLICT`가 `issue_status`를 갱신하지 않음). BankSim 20,000건 환경에서 상태 3건을 바꾼 뒤 엔진을 재실행해 유지되는 것을 확인했습니다.
- `python/export_review_sheet.py`: 미종결 CRITICAL/HIGH 건을 담당자·검토상태·소명메모 열이 있는 Excel/CSV로 내보냅니다(상태 드롭다운 포함).
- `sql/04_demo_review.sql`은 **시연용 처리**입니다. 실제 검토 결과가 아니며, 대시보드의 해결률은 실제 업무 성과를 뜻하지 않습니다.
