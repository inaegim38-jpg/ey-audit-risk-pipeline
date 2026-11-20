# EY한영 감사부문 리스크관리 지원 포트폴리오
## 거래·증빙 데이터 정합성 검증 및 이상 징후 모니터링 자동화 파이프라인

> 감사부문 리스크관리 지원 인턴 직무의 핵심 과업(자료·데이터 관리, 문서·증빙 검토, 조사·분석 지원)을 데이터 파이프라인으로 구현한 프로젝트. 가상/합성 데이터 기반 프로토타입입니다.

## 1. 문제 정의
- **As-Is**: 거래-증빙을 엑셀로 반복 대조하며 결측·중복·금액 불일치를 놓치고, 단순 금액 필터로는 분할결제/통계적 이상치를 못 잡으며, 보고용 대시보드 갱신이 수작업.
- **To-Be**: n8n이 정규화·오류 분리 적재, PostgreSQL 검증 엔진이 멱등(`ON CONFLICT`)하게 탐지, Slack 경보와 Power BI 갱신 이력까지 일원화.

## 2. 아키텍처
```text
[Webhook] ─► [Code: 정규화·스키마 검증] ─► [If 오류?] ─ 예 ─► [PG: DQ_00 격리]  ─┐
[Schedule 6h] ───────────────────────────────────────── 아니오 ─► [PG: 원장 Upsert] ─┤
                                                                                   ▼
                                                              [PG: 9대 검증엔진 (executeOnce)]
                                                                                   ▼
                                                          [If Critical/High > 0] ─► [Slack 경보]
                                                                                   ▼
                                                  [HTTP: Power BI Refresh] ─► [PG: pbi_refresh_history]
```

## 3. 검증 룰
| 구분 | Rule | 내용 | 심각도 |
|---|---|---|---|
| DQ | DQ_00 | 스키마 파싱 오류 (n8n 단계에서 격리) | HIGH |
| DQ | DQ_01~03 | 필수값 결측 / 0원 이하 / 중복 거래·증빙 해시 | HIGH~CRITICAL |
| 증빙 | EV_01~03 | 50만원 이상 증빙 누락 / 금액 불일치 / 일자 괴리 | MEDIUM~CRITICAL |
| 이상 | AN_01~03 | Z-Score 3σ / 100만원 한도 회피 분할결제 / 심야·주말 고액 | MEDIUM~CRITICAL |

## 4. 구조
```text
docker-compose.yml          # PostgreSQL 16 + n8n
sql/01_init_and_verify.sql  # DDL, 기준 데이터, 20건 시나리오, 1차 검증 (02 포함)
sql/02_run_dq_engine.sql    # 재실행용 엔진 + Power BI 뷰 (tx_summary, priority_queue)
python/prepare_audit_dataset.py
n8n/ey_audit_risk_workflow.json
powerbi/dax_measures.txt
VERIFICATION_REPORT.md
```

## 5. 실행
```bash
docker compose up -d
docker exec -i ey_audit_postgres psql -U audit_admin -d audit_db < sql/01_init_and_verify.sql
python3 -m venv .venv && .venv/bin/pip install pandas numpy sqlalchemy psycopg2-binary
.venv/bin/python python/prepare_audit_dataset.py --rows 500
docker exec -i ey_audit_postgres psql -U audit_admin -d audit_db < sql/02_run_dq_engine.sql
```
n8n(http://localhost:5678)에서 워크플로우 Import 후 Postgres Credential(Host `postgres`, DB `audit_db`, User `audit_admin`)만 연결하면 실행됩니다. Slack/Power BI 노드는 자격증명과 `{{GROUP_ID}}`/`{{DATASET_ID}}` 교체가 필요합니다.
