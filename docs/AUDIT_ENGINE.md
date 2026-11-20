# EY한영 감사부문 리스크관리 지원 포트폴리오
## 거래·증빙 데이터 정합성 검증 및 이상 징후 모니터링 자동화 파이프라인

> 감사부문 리스크관리 지원 인턴 직무의 핵심 과업(자료·데이터 관리, 문서·증빙 검토, 조사·분석 지원)을 데이터 파이프라인으로 구현한 프로젝트입니다. **두 트랙**으로 운영합니다.
>
> | 트랙 | source_dataset | 거래 건수 | 성격 |
> |---|---|---:|---|
> | A | `BANKSIM_REAL` | 20000 | 공개 **시뮬레이션** 거래 + **합성 증빙**(결함은 기능 검증용 주입) |
> | B | `SEOUL_OA22156` | 178762 | 서울시 본청 업무추진비 **실제 공개 지출**, **증빙 없음** |
>
> 어떤 결과도 '이상 거래·부정 적발'이 아니라 룰 동작 확인과 추가 확인 후보 목록입니다. 상세: [reports/AUDIT_PORTFOLIO_REPORT.md](reports/AUDIT_PORTFOLIO_REPORT.md), 검증 내역: [VERIFICATION_REPORT.md](VERIFICATION_REPORT.md)

## 1. 문제 정의
- **As-Is**: 거래-증빙을 엑셀로 반복 대조하며 결측·중복·금액 불일치를 놓치고, 단순 금액 필터로는 분할결제/통계적 이상치를 못 잡으며, 보고용 대시보드 갱신이 수작업.
- **To-Be**: n8n이 정규화·오류 분리 적재, PostgreSQL 검증 엔진이 `validation_rules`의 파라미터를 읽어 멱등(`ON CONFLICT`)하게 탐지, 검토 목록 Excel과 Power BI로 전달.

## 2. 아키텍처
```text
[Webhook] ─► [Code: 정규화·스키마 검증] ─► [If 오류?] ─ 예 ─► [PG: DQ_00 격리] ──┐
                                                  └ 아니오 ─► [PG: 원장 Upsert] ──┤
                                                                                ▼
                                                                       [Merge 합류]
                                                                                ▼
                                                     [PG: DQ 검증엔진 (= sql/02 본문)]
                                                                                ▼
                                                          [If Critical/High > 0] ─► [Slack 경보(비활성)]
                                                                                ▼
                                                  [Power BI Refresh (Mock 202)] ─► [PG: pbi_refresh_history]
```
Schedule(6시간) 트리거는 Merge를 거치지 않고 검증 엔진으로 바로 연결됩니다.

## 3. 검증 룰 (threshold_value는 프로젝트 가정값, 법적 기준 아님)
| 구분 | Rule | 내용 | 심각도 | threshold_value |
|---|---|---|---|---:|
| DQ | DQ_00 | 스키마 파싱 오류 (n8n 단계 격리) | HIGH | 0.00 |
| DQ | DQ_01 | 필수값 결측 | HIGH | 0.00 |
| DQ | DQ_02 | 0원 이하 비정상 금액 | HIGH | 0.00 |
| DQ | DQ_03 | 중복 거래·증빙 해시 | CRITICAL | 2.00 |
| 증빙 | EV_01 | 고액 증빙 누락 (서울시 제외) | CRITICAL | 500000.00 |
| 증빙 | EV_02 | 거래-증빙 금액 불일치 (서울시 제외) | CRITICAL | 100.00 |
| 증빙 | EV_03 | 증빙 일자 괴리 (서울시 제외) | MEDIUM | 30.00 |
| 이상 | AN_01 | Z-Score 이상치 (σ) | HIGH | 3.00 |
| 이상 | AN_02 | 한도 회피 분할결제 | CRITICAL | 1000000.00 |
| 이상 | AN_03 | 업무 외 시간 고액 (00:00 시각 미상 건은 주말만 판정) | MEDIUM | 500000.00 |
| 이상 | AN_04 | 근접 일자 반복 집행 (LAG/LEAD, 일) | HIGH | 3.00 |

모든 룰 `is_active = t`. 엔진은 `is_active=FALSE`인 룰을 건너뜁니다.

## 4. 결과 요약 (DB 조회값, `sql/07_final_counts.sql`)
| 데이터셋 | 탐지 건수 | 탐지 고유 거래 |
|---|---:|---:|
| BANKSIM_REAL | 2592 | 2486 |
| SEOUL_OA22156 | 5685 | 4845 |
| 합계 | 8277 | |

룰별 표와 해석은 [보고서 4장](reports/AUDIT_PORTFOLIO_REPORT.md).

## 5. 데이터 출처와 한계
- **BankSim**(Kaggle `ealaxi/banksim1`): 공개 **시뮬레이션** 은행 거래. 실제 기업 거래가 아닙니다. 시각(12:00 고정), 금액(유로→원, 1,400원 가정), 부서(고객 연령대 코드)는 가정값입니다.
- **합성 증빙**: 공개 데이터에 증빙이 없어 거래에서 파생 생성하고, 결함(누락·금액 불일치·일자 괴리·해시 중복)을 기능 검증용으로 주입했습니다. BankSim의 EV_*·DQ_03 결과는 이 주입의 영향을 받습니다.
- **서울시 업무추진비**(OA-22156): 실제 공개 지출이지만 증빙·소명이 없어 EV 룰을 적용하지 않습니다. `00:00` 시각 1562건은 시각 미입력으로 보고 `has_exact_time=false`로 분리했습니다. 전기일이 1970-01-01인 60건은 원본 확인이 필요합니다.
- **연동 한계**: Slack 노드는 **비활성**, Power BI 갱신은 **Mock(202 모사)** 이며 실제 호출하지 않습니다.
- 이슈 상태 3건(IN_REVIEW/RESOLVED/FALSE_POSITIVE)은 **시연용 변경**(`sql/04_demo_review.sql`)이며 실제 검토 결과가 아닙니다.

## 6. 구조
```text
docker-compose.yml               # PostgreSQL 16 + n8n
sql/01_init_and_verify.sql       # DDL, 기준 데이터, 20건 시나리오, PBI 뷰 (스키마를 DROP 후 재생성)
sql/06_rule_params_restore.sql   # 룰 파라미터 열·AN_04·has_exact_time 보장, 룰 한글 텍스트 UTF-8 기록, PBI 요약 뷰(데이터셋 구분)
sql/02_run_dq_engine.sql         # 검증 엔진 (validation_rules의 threshold_value/is_active 사용, 멱등)
sql/03_issue_review.sql          # 이슈 검토 상태 추적(열, 함수, 뷰)
sql/04_demo_review.sql           # 시연용 상태 변경 예시 (실제 검토 아님)
sql/05_seoul_analysis.sql        # 서울시 기준선·민감도·샘플 조회
sql/07_final_counts.sql          # 문서 수치 검산용 조회(읽기 전용)
python/load_banksim_real.py      # BankSim 적재
python/prepare_audit_dataset.py  # 합성 데이터 생성기 (대체용)
python/load_seoul_expense.py     # 서울시 CSV(cp949) → 정규화 CSV
python/profile_seoul_expense.py  # 서울시 원본 프로파일링
python/export_review_sheet.py    # 데이터셋별 검토 목록 Excel/CSV
n8n/ey_audit_risk_workflow.json  # 엔진 노드 = sql/02 본문
powerbi/dax_measures.txt
reports/AUDIT_PORTFOLIO_REPORT.md
VERIFICATION_REPORT.md
```

## 7. 실행
> Windows PowerShell 파이프(`Get-Content x.sql | docker exec -i ... psql`)는 한글을 `?`로 바꿉니다. **반드시 `docker cp` 후 `psql -f`로 실행**하세요. 실행 순서: 01 → 06 → 데이터 적재 → 02 → 03.

```bash
docker compose up -d
docker cp sql/. ey_audit_postgres:/tmp/sql
docker exec ey_audit_postgres psql -U audit_admin -d audit_db -f /tmp/sql/01_init_and_verify.sql
docker exec ey_audit_postgres psql -U audit_admin -d audit_db -f /tmp/sql/06_rule_params_restore.sql
# 데이터 적재 (호스트 Python 3.8 32-bit에는 패키지가 없어 python:3.12-slim 컨테이너 사용)
#  - BankSim: python/load_banksim_real.py --csv data/.../bs140513_032310.csv --rows 20000
#  - 서울시: python/load_seoul_expense.py → data/seoul_normalized.csv → COPY로 raw_transactions 적재
docker exec ey_audit_postgres psql -U audit_admin -d audit_db -f /tmp/sql/02_run_dq_engine.sql
docker exec ey_audit_postgres psql -U audit_admin -d audit_db -f /tmp/sql/03_issue_review.sql
docker exec ey_audit_postgres psql -U audit_admin -d audit_db -f /tmp/sql/07_final_counts.sql
```

검토 시트 생성 (Docker Python, compose 네트워크 사용):
```bash
docker run --rm --network ey-audit_default -v "$PWD:/work" -w /work python:3.12-slim sh -c \
  "pip install -q pandas sqlalchemy psycopg2-binary openpyxl && \
   python python/export_review_sheet.py --db-uri postgresql+psycopg2://audit_admin:audit_password123@postgres:5432/audit_db"
```
출력 `review_sheet.xlsx`: 요약 / 검토목록_BankSim / 검토목록_서울시 / 안내 시트(데이터셋별 상위 50건, 상태 드롭다운).

n8n(http://localhost:5678)에서 워크플로우를 Import하고 Postgres Credential(Host `postgres`, DB `audit_db`, User `audit_admin`)을 연결하면 실행됩니다. 실제 Slack/Power BI 연동에는 Slack 자격증명과 Power BI REST(HTTP Request) 노드 교체가 필요합니다.

## 8. 검토 상태 추적
- `sql/03_issue_review.sql`: OPEN / IN_REVIEW / RESOLVED / FALSE_POSITIVE, 담당자, 소명메모. 엔진을 다시 실행해도 `issue_status`는 덮어쓰지 않습니다.
- Excel 입력은 DB에 자동 반영되지 않습니다. 반영은 `audit_risk.set_issue_status(...)`.

## 데이터 출처 표기
- **서울시 업무추진비**: 서울특별시 본청 업무추진비 목록(데이터셋 OA-22156, 서울 열린데이터광장). 다운로드한 파일명은 `서울시 본청 업무추진비 목록.csv`입니다. 이용 조건은 서울 열린데이터광장 해당 데이터셋 페이지의 이용허락범위를 따릅니다. 원본 CSV는 저장소에 포함하지 않았고, 이 저장소에는 집계 결과와 검토 목록 표본(데이터셋별 50건, 사람 이름·상호 열 없음)만 있습니다.
- **BankSim**: Kaggle `ealaxi/banksim1`의 공개 시뮬레이션 데이터입니다. 원본 CSV는 저장소에 포함하지 않았습니다.
