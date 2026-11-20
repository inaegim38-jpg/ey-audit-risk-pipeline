# VERIFICATION REPORT

최종 검증일: 2026-11-21 · 대상: `BANKSIM_REAL` 20000건(+합성 증빙 19983건), `SEOUL_OA22156` 178762건
수치는 모두 `sql/07_final_counts.sql`로 DB에서 직접 조회한 값입니다.

## 1. 실행 환경
- Windows 10 + Docker Desktop(WSL2): `postgres:16`(`ey_audit_postgres`, healthy), `n8nio/n8n:latest`(2.48.7, `ey_audit_n8n`).
- SQL은 `docker cp` 후 `psql -f`로만 실행. (PowerShell 파이프로 넣으면 한글이 `?`로 바뀜 — 실제로 이 문제로 `validation_rules` 한글과 `detail_message`가 깨졌었음, 2장 참고)
- 파이썬: 호스트 Python 3.8(32-bit)·`.venv`(같은 3.8)에는 pandas 등이 없어 `python:3.12-slim` 컨테이너에서 실행.

## 2. 이번(2026-11-21)에 실제 실행해 확인한 항목

| # | 항목 | 결과 |
|---|---|---|
| 1 | 한글 깨짐 진단 | 복구 전 `validation_rules` 11행 전부와 `detail_message` 8277건 전부가 `?` 포함 |
| 2 | 룰 텍스트 복구 (`06_rule_params_restore.sql`) | 실행 후 `?` 포함 룰 0건. UTF-8 바이트 확인(DQ_01 `ed9584ec8898eab09220eab2b0ecb8a1`) |
| 3 | 엔진 재실행 (`02_run_dq_engine.sql`, psql -f) | `INSERT 0 8277`, 실행 후 `detail_message`의 `?` 0건, 최신 run_id가 아닌 잔존 탐지 0건 |
| 4 | 탐지 합계 검산 | BANKSIM_REAL 2592 + SEOUL_OA22156 5685 = 8277 (전체 행 수와 일치), 금액 합계 3277906017.00 |
| 5 | 서울 시각 플레이스홀더 분리 | `has_exact_time=false` 1562건, `true` 177200건 |
| 6 | 검토 시트 생성 (`export_review_sheet.py`, Docker Python) | 요약 / 검토목록_BankSim 50행 / 검토목록_서울시 50행 / 안내 시트 생성. 모든 시트 `?` 포함 셀 0개, 상태 드롭다운(M2:M51) 확인. **단, 기존 `review_sheet.xlsx/.csv`가 다른 프로그램에 잠겨 있어 `review_sheet_new.*`로 생성됨(교체 대기)** |
| 7 | n8n 엔진 노드 동기화 | 엔진 노드 SQL을 `02` 본문에서 추출해 교체(threshold_value·is_active 사용, AN_04 LAG/LEAD, EV_01~03 서울 제외, AN_03 has_exact_time, DQ_03 데이터셋 파티션) |
| 8 | n8n 실행 (서울 포함 전체 DB) | 워크플로우 임포트 → publish → n8n 재시작 → Webhook에 스키마 오류 1행 POST → DQ_00 격리 → Merge → 엔진 → If → Mock → 이력 저장까지 완료. 엔진 결과가 02 SQL과 룰·데이터셋별로 동일(합계 8277). 테스트로 생긴 DQ_00 1건은 삭제, 워크플로우는 다시 unpublish. `pbi_refresh_history`에 이번 실행 1행(run_id `RUN_20261121_022729`, Mock 202) 남음 |
| 9 | 03 뷰 정의 동기화 | DB에만 있던 `vw_review_worklist.source_dataset` 열을 `03_issue_review.sql`에 반영, 06/03 재실행 후 건수 불변(8277) |

## 3. 결과 (DB 조회값)

| source_dataset | severity | rule_id | rule_name | 건수 | 금액 합계(원) |
|---|---|---|---|---:|---:|
| BANKSIM_REAL | CRITICAL | EV_02 | 거래-증빙 금액 불일치 | 375 | 19505500.00 |
| BANKSIM_REAL | CRITICAL | DQ_03 | 중복 거래·증빙 해시 | 101 | 5075100.00 |
| BANKSIM_REAL | CRITICAL | EV_01 | 고액 증빙 누락 | 17 | 30423800.00 |
| BANKSIM_REAL | HIGH | AN_04 | 근접 일자 반복 집행 | 1547 | 48568800.00 |
| BANKSIM_REAL | HIGH | AN_01 | Z-Score 이상치 | 98 | 41262200.00 |
| BANKSIM_REAL | HIGH | DQ_02 | 0원 이하 비정상 금액 | 9 | 0.00 |
| BANKSIM_REAL | MEDIUM | EV_03 | 증빙 일자 괴리 | 411 | 25673800.00 |
| BANKSIM_REAL | MEDIUM | AN_03 | 업무 외 시간 고액 | 34 | 49243400.00 |
| SEOUL_OA22156 | CRITICAL | DQ_03 | 중복 거래·증빙 해시 | 707 | 85217760.00 |
| SEOUL_OA22156 | CRITICAL | AN_02 | 한도 회피 분할결제 | 14 | 11828900.00 |
| SEOUL_OA22156 | HIGH | AN_01 | Z-Score 이상치 | 2712 | 2602824427.00 |
| SEOUL_OA22156 | HIGH | AN_04 | 근접 일자 반복 집행 | 2224 | 336000440.00 |
| SEOUL_OA22156 | HIGH | DQ_02 | 0원 이하 비정상 금액 | 5 | 0.00 |
| SEOUL_OA22156 | MEDIUM | AN_03 | 업무 외 시간 고액 | 23 | 22281890.00 |
| **합계** | | | | **8277** | **3277906017.00** |

BankSim 단독 결과는 이전 기록(2026-11-20, 1045건)에 이번에 추가된 AN_04 1547건을 더한 2592건으로, 나머지 룰 건수는 이전과 같습니다.

## 4. 이전(2026-11-20) 세션에서 확인했고 이번에 재실행하지 않은 항목
- 20건 시나리오(01 SQL) 룰 탐지, 합성 500건 멱등성.
- n8n 정상 행 가지(원장 Upsert)와 정상/오류 혼합 입력 — 이번에는 오류 행 1건만 보내 정상 행 가지는 재실행하지 않음(원장 오염 방지).
- 이슈 상태 유지(엔진 재실행 시 `issue_status` 비갱신): 이번 재실행 후에도 상태 3건 유지된 것은 확인함(OPEN 8274, IN_REVIEW 1, RESOLVED 1, FALSE_POSITIVE 1).
- Power BI Desktop `localhost:5432` 연결과 시각화 구성 — 이번에 새 뷰(`vw_pbi_tx_summary`에 `source_dataset` 추가)로 새로 고침했는지는 **미확인**.

## 5. 미확인 / 미검증 항목
- **Slack 알림**: 노드 비활성(자격증명 없음). 실제 발송 미검증.
- **Power BI REST 갱신**: Mock 노드가 HTTP 202를 모사할 뿐 실제 호출 없음.
- **룰 텍스트 일부는 재작성**: AN_04 rule_name·description, AN_03 description, 전체 remediation_hint는 원문이 남아 있지 않아 새로 작성(원문 복원 아님).
- 서울시 전기일 1970-01-01 60건: 원본 값인지 파싱 문제인지 미확인.
- 서울시 DQ_03 707건: 원본 중복 게시인지 실제 복수 집행인지 미확인.
- `SEOUL_EXPENSE_PROFILE.md`(Git 제외)의 "근접 반복 0건"은 프로파일 스크립트가 '사용장소' 열을 찾지 못한 결과로, DB의 AN_04 결과(2224건)와 다르며 신뢰하지 않음.
- 담당자 검토는 수행하지 않음. 상태 3건은 **시연용**이며 실제 검토 결과가 아님.

## 6. 해석상 주의
- BankSim은 **시뮬레이션**, 증빙은 **합성**(결함은 기능 검증용 주입), 서울시는 **증빙 없는 실제 공개 지출**입니다. 어떤 탐지도 부정·위반 적발이 아닙니다.
- threshold_value는 근거 있는 법적 기준이 아니라 프로젝트 가정값입니다.
- 탐지 건수는 룰 동작 확인용이며 정확도·재현율 같은 성능 지표가 아닙니다. 금액 합계는 룰 간 중복 집계입니다.
