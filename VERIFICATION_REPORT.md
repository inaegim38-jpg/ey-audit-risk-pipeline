# VERIFICATION REPORT

실행일: 2026-11-20

## 실행 환경 (중요)
- 이 검증 환경에서 Docker Hub가 429(rate limit)를 반환해 `postgres:16`, `n8nio/n8n` 이미지를 받지 못했습니다. `docker compose up`은 **실행 검증되지 않았습니다**(compose 파일은 작성만 됨).
- 대체: 동일 계정/DB/포트/TZ(Asia/Seoul)의 **네이티브 PostgreSQL 16.15**로 Step C~D 수행, n8n은 **npm 설치 n8n 로컬 CLI**로 `import:workflow` 검증. Docker가 되는 로컬 PC에서는 README의 명령 그대로 재현하세요.

## 결과 요약
| 단계 | 결과 |
|---|---|
| 01 (20건, 9룰) | 통과: 9개 룰 모두 1건 이상 탐지 (DQ_00은 n8n 전용) |
| Python 500건 적재 + 02 | 통과: 9개 룰 모두 탐지, 02 재실행 시 findings 94건 유지(멱등) |
| n8n import:workflow | 통과: 11노드 워크플로우 정상 등록 (Slack/Power BI 실호출은 미검증) |
| n8n 노드 SQL(DQ_00/원장/이력) | PG에서 PREPARE/EXECUTE로 직접 검증, DQ_00 이중 실행 시 1건 (롤백함) |

## 1) 총 건수
     raw_transactions | raw_evidences | dq_anomaly_findings 
    ------------------+---------------+---------------------
                  520 |           513 |                  94
    (1 row)
    

## 2) rule_id별 탐지 건수 및 노출 금액 (20건 시드 + 500건 합성 누적)
     rule_id |       rule_name       | severity | issue_count | total_detected_amount 
    ---------+-----------------------+----------+-------------+-----------------------
     AN_01   | Z-Score 이상치        | HIGH     |          36 |           28609700.00
     AN_02   | 한도 회피 분할결제    | CRITICAL |           6 |            5550000.00
     AN_03   | 업무 외 시간 고액     | MEDIUM   |           8 |            5040000.00
     DQ_01   | 필수값 결측           | HIGH     |           6 |             964600.00
     DQ_02   | 0원 이하 비정상 금액  | HIGH     |           6 |            -210000.00
     DQ_03   | 중복 거래·증빙 해시   | CRITICAL |           6 |             858700.00
     EV_01   | 고액 증빙 누락        | CRITICAL |           7 |            5460000.00
     EV_02   | 거래-증빙 금액 불일치 | CRITICAL |          12 |            4305000.00
     EV_03   | 증빙 일자 괴리        | MEDIUM   |           7 |            1085700.00
    (9 rows)
    

## 3) vw_pbi_priority_queue 상위 5건
          tx_id       | max_risk_score | finding_count |  rule_ids   | tx_amount 
    ------------------+----------------+---------------+-------------+-----------
     SYN_105501_00040 |          98.21 |             2 | AN_01,AN_02 | 940000.00
     S18              |          98.00 |             2 | AN_01,AN_02 | 940000.00
     SYN_105501_00038 |          98.00 |             2 | AN_01,AN_02 | 940000.00
     SYN_105501_00039 |          98.00 |             2 | AN_01,AN_02 | 910000.00
     S19              |          98.00 |             2 | AN_01,AN_02 | 910000.00
    (5 rows)
    

## 참고/한계
- `baseline_stats` 값은 제가 정한 가정치입니다. 처음 값(평균 6~25만 원)은 합성 데이터에 비해 낮아 AN_01이 84건 과탐지되어, 합성 분포에 맞춰 재보정했습니다(→36건). 실제 데이터에는 실측 통계를 써야 합니다.
- AN_01 건수에는 다른 룰용으로 주입한 고액 거래(64만·78만·145만 원 등)가 자연스럽게 중복 포함됩니다.
- Slack, Power BI REST 호출은 자격증명이 없어 미검증입니다. HTTP 노드의 `{{GROUP_ID}}`/`{{DATASET_ID}}`는 자리표시자입니다.
- n8n Postgres 노드 `queryReplacement` 동작은 SQL 레벨까지만 검증했고, 실제 n8n UI 실행은 하지 않았습니다.
