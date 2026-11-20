# 검증 기록

2026-11-21, 로컬 환경에서 실제로 실행한 결과만 적었습니다. 실행하지 못한 항목은 마지막 장에 따로 모았습니다.

| 항목 | 값 |
|---|---|
| Python | 3.12.3 |
| PostgreSQL | 16.15 (Ubuntu 24.04 패키지) |
| DB 계정 | `audit_admin` / `audit_db` (docker-compose와 동일) |
| Docker | 미사용 (샌드박스에 없음) |

## 1. 기존 거래 검증 엔진

`sql/01_init_and_verify.sql` → `06_rule_params_restore.sql` → `02_run_dq_engine.sql` → `03_issue_review.sql` → `07_final_counts.sql` 순으로 실행했고 모두 `ON_ERROR_STOP=1` 에서 exit 0입니다.

| 확인 | 결과 |
|---|---|
| `raw_transactions` 적재 | 20건 |
| `dq_anomaly_findings` | 16건 / 9개 룰 |
| CRITICAL·HIGH 이면서 OPEN | 14건 |
| `vw_pbi_tx_summary` | 13행 |

룰별 건수는 AN_01 6, AN_02 2, AN_03 1, DQ_01 1, DQ_02 1, DQ_03 2, EV_01 1, EV_02 1, EV_03 1입니다. 시드 데이터에 해당 거래가 없어 DQ_00과 AN_04는 0건입니다.

## 2. n8n 기존 워크플로우

`n8n/ey_audit_risk_workflow.json` 은 노드 12개, 단일 시작점(Webhook·Schedule 2개), 분기 후 Merge 합류 구조입니다. `Slack Alert` 노드만 disabled 이고, `Power BI Refresh (Mock)` 은 실제 API 호출이 아니라 202를 반환하는 Code 노드입니다.

`Postgres: DQ 검증엔진` 노드의 쿼리와 `sql/02_run_dq_engine.sql` 을 줄 단위로 비교했습니다.

| 구간 | 결과 |
|---|---|
| 1~153줄 (테이블 정리·룰 실행·파인딩 적재) | **동일** |
| 앞부분 | sql/02 에만 `ANALYZE audit_risk.raw_transactions;` `ANALYZE audit_risk.raw_evidences;` 2줄 |
| 마지막 SELECT | n8n은 요약 1개, sql/02는 `source_dataset` 집계 + AN_02 상세 2개 |

즉 탐지 로직은 두 곳이 같고, 실행 후 통계 갱신과 결과 조회만 다릅니다. n8n 쪽에 `ANALYZE` 가 빠진 것은 적재 직후 플래너 통계가 비어 있다는 뜻이라, 대량 배치에서는 후속 쿼리가 느려질 수 있습니다. 개선 대상으로 남깁니다.

## 3. Power BI 기존 DAX

`powerbi/dax_measures.txt` 의 측정값 8개가 참조하는 컬럼을 실제 DB에서 확인했습니다.

| 테이블(별칭) | 참조 컬럼 | 존재 |
|---|---|---|
| `vw_pbi_tx_summary` (Summary) | total_tx_count, total_tx_amount, dq_error_tx_count, anomaly_exposure_amount | 4/4 |
| `dq_anomaly_findings` (Findings) | finding_type, severity, issue_status | 3/3 |

측정값은 Total Tx Count, Total Tx Amount, DQ Error Tx Count, DQ Error Rate, Anomaly Issue Count, Anomaly Exposure Amount, Open Critical Issues, Issue Resolution Rate 입니다. 모두 참조 컬럼이 있어 모델 갱신 시 깨지는 측정값은 없습니다.

## 4. 포렌식 계층 적재와 뷰

`sql/08_forensic_case_schema.sql` 로 스키마를 만들고 데모 산출물을 적재한 뒤 `sql/09_forensic_pbi_views.sql` 을 실행했습니다. 세 단계 모두 exit 0입니다.

| 테이블 | 적재 건수 |
|---|---:|
| `forensic.evidence` | 563 |
| `forensic.documents` | 563 |
| `forensic.findings` | 8 |
| `forensic.finding_evidence` | 20 |

뷰 조회 결과는 다음과 같습니다.

| 뷰 | 결과 |
|---|---|
| `vw_pbi_case_summary` | 증거 563 / 문서 563 / 파인딩 8 / CRITICAL 3 / OPEN 8 / 금액 127,740,000원 |
| `vw_pbi_evidence` | 6개 문서유형, 총 334,452 bytes, 고유 해시 563 |
| `vw_pbi_review_progress` | 15행, 문서 563, 근접중복 91, 평균 priority 5.5 |
| `vw_pbi_findings` | 8건, CRITICAL 3 · HIGH 3 · MEDIUM 2, 증거 인용 7건(87.5%), 미인용 1건(F-006) |

수치는 `demo/run_summary.json` 과 일치합니다. 적재 과정에서 회의록 날짜가 `2026-01-21 10:00~11:00` 같은 범위 표기라 `DATE` 캐스팅이 실패했고, 날짜 부분만 남기도록 적재 스크립트에서 정규화했습니다. 실제 원본을 다룰 때도 같은 정규화가 필요합니다.

### 이 과정에서 찾은 결함

뷰를 처음 조회했을 때 `vw_pbi_findings` 의 F-005 증거 건수가 0으로 나왔습니다. 원본을 보니 `finding_evidence` 에 F-005 행이 6개 있었지만 모두 `evidence_id` 가 NULL 이었고, 같은 파일(`card/card_transactions.csv`)을 가리키는 중복 행이었습니다. 카드 거래 6건을 각각 인용 행으로 넣으면서 파일 단위 증거 ID를 채우지 않은 것이 원인입니다.

보고서(`report.py`)는 rel_path 로 ID를 되살려 표시했기 때문에 문서만 봐서는 드러나지 않았고, Power BI 뷰처럼 SQL로 직접 세는 쪽에서만 틀린 값이 나왔습니다. `analytics.py` 에서 매니페스트를 읽어 `rel_path → evidence_id` 를 채우고 같은 파일 인용은 1행으로 합치도록 고쳤습니다(개별 건수는 note에 "외 5건"으로 유지). 수정 후 연결 행은 25 → 20, F-005는 1건, 인용 커버리지는 87.5%가 되었습니다.

F-006(벤포드)은 특정 문서가 아니라 금액 분포 전체를 보는 검사라 인용이 없는 것이 정상입니다. 커버리지 100%가 아닌 이유를 지표가 설명해 줍니다.

## 5. n8n 포렌식 워크플로우

`n8n/forensic_case_workflow.json` 은 8개 노드입니다.

```
Schedule(매일 07:00) → Execute Command(파이프라인 실행)
→ Code(NDJSON 파싱) → Postgres(파인딩 Upsert)
→ Code(심각도 요약) → If(CRITICAL > 0) → Slack / NoOp
```

검증한 것과 못 한 것을 나눕니다.

| 검증 | 방법 | 결과 |
|---|---|---|
| JSON 파싱·노드 필수 필드 | 스크립트 | PASS (노드 8, 연결 8) |
| 연결 무결성·도달성 | 모든 링크의 대상 노드 존재, 시작 노드 1개, 미도달 노드 0개 | PASS |
| Upsert 컬럼 vs 스키마 | `08` 의 `findings` 컬럼 12개 대조 | 일치 |
| 플레이스홀더·치환 배열 | `$1`~`$12`, 배열 12개 | 일치 |
| Upsert 실제 실행 | 같은 SQL을 값으로 치환해 psql 2회 실행 | 8행 유지(멱등) |
| n8n import | n8n 2.35.7 에 `import:workflow` 실행 후 `export:workflow` 로 재확인 | 1건 import 성공, 노드 8개 유지 |
| Code 노드 로직 | `jsCode` 를 그대로 꺼내 Node.js에서 실행 | 8건 파싱, 요약 CRITICAL 3 · HIGH 3 · MEDIUM 2 · 127,740,000원, 빈 출력은 예외 처리 확인 |

2회 실행 후에도 `findings` 는 8행, CRITICAL 3건, 합계 127,740,000원으로 변하지 않았습니다. `ON CONFLICT (finding_id) DO UPDATE` 가 의도대로 동작합니다.

## 6. 검증하지 못한 것

Power BI Desktop이 없어 DAX 계산 자체는 평가하지 못했습니다. 대신 측정값이 참조하는 컬럼이 실 DB에 있는지, 뷰가 같은 수치를 내는지까지만 확인했습니다.

워크플로우는 n8n 2.35.7 에 import까지 성공했지만, 스케줄러가 실제로 돌려 Execute Command 로 파이프라인을 실행한 것은 아닙니다. Execute Command 의 저장소 경로(`/data/ey-audit-risk-pipeline`)는 n8n 호스트 마운트 위치에 맞춰 바꿔야 하고, Postgres·Slack 자격증명도 각자 환경에서 연결해야 합니다. 노드 파라미터·SQL·Code 로직은 검증했지만, 자격증명이 붙은 상태의 엔드투엔드 실행은 남아 있습니다. 이 항목은 포렌식 케이스 워크플로우에 해당하며, audit_core의 WF-1·WF-3은 자격증명을 붙여 실행까지 확인했습니다(7절).

포렌식 케이스는 합성 코퍼스입니다. 파이프라인이 규칙대로 동작한다는 증거이지, 실제 조사에서 같은 정확도가 나온다는 뜻이 아닙니다.

## 7. audit_core 3계층 엔진

`sql/10`~`15`를 순서대로 실행한 뒤 `sql/14_verify_audit_core.sql`의 10개 체크로 확인했습니다.

| 체크 | 기준 | 결과 |
|---|---|---|
| 룰 카탈로그 | 활성 룰 14종 | PASS (14) |
| 파인딩 적재 | `ENG-2026-001` 파인딩 ≥ 1 | PASS (40) |
| 중복 파인딩 | `(engagement, rule, ref_id)` 중복 0 | PASS (0) |
| 멱등성 | `run_rules()` 2회차 `new = 0`, `updated = 40` | PASS |
| SoD 대상 | JET-02 `item_count` 합계 = 원본 SoD 60행 | PASS (60 = 60) |
| 해시 단독 유니크 | 해시만으로 유니크 제약을 걸지 않음(충돌 오탐 방지) | PASS |
| 공시 그레인 | 계정·기간당 1행 | PASS |
| 파티션 | `fact_journal` 월 파티션 4개 | PASS |
| 증거 연결 | `finding_evidence` FK 유효 | PASS (29행) |
| 공시 감시 뷰 | `vw_disclosure_watch` 조회 가능 | PASS |

룰 실행 결과는 **14종 / 40건**, 심각도는 CRITICAL 5 · HIGH 11 · MEDIUM 22 · LOW 2입니다. `item_count` 합계는 2,225건으로, 파인딩 1건이 여러 전표를 묶는 구조이기 때문에 건수와 항목 수를 나눠 표기합니다.

멱등성은 같은 함수를 연속 두 번 호출해 확인했습니다. 1회차 40건 `INSERT`, 2회차는 `new = 0` / `updated = 40`으로 행 수가 변하지 않았습니다. 처음 구현에서는 `ref_id`에 실행 시각이 섞여 2회차에 80건이 되는 문제가 있었고, 공시 최대 `fiscal_period`를 고정해 해결했습니다.

증거 커버리지는 `vw_evidence_coverage` 기준 **67.5%(27/40)** 입니다. 커버리지를 100%로 맞추는 대신, 증거가 없는 13건을 `OPEN`으로 남겨 요청 큐로 쓰는 쪽을 택했습니다.

### n8n 실행

| 항목 | 결과 |
|---|---|
| WF-1 import | 성공, 노드 8개 |
| WF-3 import | 성공, 노드 8개 |
| Postgres 자격증명 | `PG_CRED` 등록, WF-3에서 사용 |
| WF-3 단독 실행 | `n8n execute --id aegis-wf3-rules` → `status = success` |
| WF-3 출력 | `[Aegis] 룰 실행 완료: 14종 / CRITICAL 2 · HIGH 6` (룰 종류 수) |
| DB 반영 | `fact_risk_finding` 40건, `updated_at` 갱신 확인 |

Schedule Trigger만 있으면 CLI 실행이 `Missing node to start execution`으로 실패합니다. WF-3에 `Execute Workflow Trigger`를 추가해 CLI 실행과 상위 워크플로우 호출을 모두 가능하게 했고, 이 경로로 엔드투엔드 실행을 확인했습니다.

### 이 과정에서 찾은 결함

- **REL-01 명칭 정규화**: 거래처명의 공백·`(주)`·`㈜` 표기를 제거하지 않아 동일 거래처를 다른 곳으로 인식, 43건으로 과탐. 정규화 후 40건.
- **`#variable_conflict use_column`**: `RETURNS TABLE`의 `rule_id`와 테이블 컬럼이 충돌해 룰 조회가 0건이 되던 문제. 지시어 추가로 해결.
- **`ref_id` 비결정성**: 실행 시각 기반 `ref_id` 때문에 멱등성이 깨지던 문제. 기간 기반 키로 교체.
