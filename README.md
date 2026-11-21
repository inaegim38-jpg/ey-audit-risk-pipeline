# 포렌식 조사 지원 워크벤치

증거를 모으고, 문서를 검토하고, 문서 사이의 관계에서 확인이 필요한 지점을 찾아 증거 인용이 붙은 보고서까지 만드는 파이프라인입니다. 감사부문 포렌식팀 인턴 직무(자료·데이터 관리, 문서 검토, 조사·분석 지원)를 실제로 돌아가는 절차로 옮겼습니다.

```
증거 563건 수집·해시 검증 PASS → 문서 563건 처리 → 근접중복 42개 클러스터
→ 검토 큐 80건 → 파인딩 8건 → 증거 인용 20건
```

주입한 패턴 6개(분할 발주·허위 견적·특수관계자·중복 지급·일자 조작·카드 사적 사용)가 모두 탐지되었습니다. 다만 **결과는 부정의 확정이 아니라 추가 확인 후보**입니다. 케이스 자체가 합성 코퍼스이기 때문입니다.

## 구성

두 계층으로 나뉩니다.

| 계층 | 질문 | 구성 |
|---|---|---|
| `forensic/` | 이 케이스에서 무엇을 확보·검토·확인했는가 | 증거 수집, 문서 검토, 교차 분석, 보고서 |
| `sql/` `python/` `n8n/` | 거래가 규칙에 맞는가 | 기존 거래·증빙 정합성 검증 엔진 (11개 룰, 8277건 탐지) |

기존 엔진은 없애지 않고 데이터 분석 모듈로 남겼습니다. 상세는 [docs/AUDIT_ENGINE.md](docs/AUDIT_ENGINE.md).

## 3분 실행

Docker도 DB도 필요 없습니다. 파이썬 3.10+ 와 pandas, openpyxl만 있으면 됩니다.

```bash
pip install pandas openpyxl
python3 -m forensic.run_demo --out demo
```

`demo/` 에 아래 산출물이 생깁니다. 원본 코퍼스와 DB는 재생성 가능하므로 저장소에 커밋하지 않습니다.

| 산출물 | 내용 |
|---|---|
| `evidence/evidence_manifest.csv` | 파일 563건의 해시·크기·수집시각·보관자 |
| `evidence/custody_log.csv` | 수집·해시검증 이벤트 1126건 |
| `review_console.xlsx` | 검토 큐 80건 (요약 / 검토큐 / 태깅규칙, 관련성 드롭다운) |
| `FINDINGS_REPORT.md` | 파인딩 8건 + 증거 인용 + 한계 + 타임라인 |
| `findings.xlsx` | 파인딩 / 증거연결 / 우선검토 시트 |
| `qc_summary.json` | 태그 커버리지·클러스터 일관성·PII 건수 |

## 케이스

| 항목 | 내용 |
|---|---|
| 케이스 | CASE-2026-014, 한빛물산(주) 구매팀 법인카드·협력사 지급 제보 |
| 기간 | 2025-09-01 ~ 2026-06-30 |
| 문서 | 메일 311, 품의서 64, 견적서 83, 계산서 84, 회의록 20, 카드내역 1 |
| 생성 | `forensic/build_corpus.py` (시드 고정, 재현 가능) |

생성기는 `ground_truth.json` 에 정답을 남기지만 **탐지 단계는 이 파일을 읽지 않습니다.** 정답을 보고 맞히는 구조가 아니라는 점을 코드로 보장했습니다.

| 주입 패턴 | 탐지 |
|---|---|
| 한도 직하 4건 분할 발주 (9일 내, 동일 업체) | F-002 |
| 3개 업체 견적 금액·연락처 동일 | F-008 |
| 특수관계 업체가 다른 업체와 주소·연락처 공유 | F-003 |
| 동일 승인번호 계산서 2개 일자 | F-001 |
| 계약일자 > 계산서 작성일자 | F-004 |
| 주말·심야 골프장·유흥주점 6건 | F-005 |

## 파이프라인

```
[코퍼스 생성] → [증거 수집·해시] → [문서 검토] → [교차 분석] → [보고서]
                   manifest          index/tag      findings      인용·한계
                   custody_log       review queue   evidence link
```

**증거 수집**은 원본을 읽기 전용으로 복사하고 SHA-256을 계산합니다. 수집 후 재해시로 원본 변경·누락·미등록 파일을 확인합니다. 데모는 563건 전부 `PASS` 입니다.

**문서 검토**는 파일을 정규화 텍스트로 바꾸고, 중복을 정리하고, 스레드를 재구성하고, 규칙 기반으로 사전 태깅합니다. 태깅은 판단이 아니라 **볼 순서를 정하는 장치**이므로 결과 컬럼은 `suggested_relevance` 이고 최종 관련성은 사람이 입력합니다.

**교차 분석**은 문서 1건으로는 알 수 없는 패턴을 찾습니다. 중복 지급, 분할 발주, 업체 마스터 이상, 일자 역전, 카드 이상, 벤포드, 결재 집중, 라운드 금액, 동일 견적의 9개 규칙입니다.

**보고서**는 파인딩마다 증거 ID·파일·해시 앞 12자리를 붙입니다. 제3자가 같은 결론에 도달할 수 있는지 검증할 수 있게 하려는 것입니다.

## 중복 판정은 문서 유형별로 다르게

모든 문서에 본문 유사도를 적용하면 틀립니다. 품의서·견적서·계산서는 본문이 템플릿이라 서로 다른 문서도 유사도가 0.9를 넘습니다. 처음 이 방식으로 돌렸을 때 563건 중 559건이 26개 클러스터로 뭉쳤고, 그 "근접중복 99%"는 아무 의미가 없었습니다.

| 유형 | 판정 기준 |
|---|---|
| 메일 | 본문 shingle Jaccard ≥ 0.90 (MinHash + LSH로 후보 축소) |
| 계산서 | 승인번호 일치 |
| 견적서 | 합계금액 + 담당자 연락처 일치 |
| 품의서 | 협력사 + 금액 + 품명 일치 |

바꾼 뒤 클러스터는 42개로 줄었고, 의미 있는 묶음(동일 견적 3건, 동일 승인번호 2건)이 남았습니다. 판단 근거는 [docs/FORENSIC_WORKBENCH.md](docs/FORENSIC_WORKBENCH.md) 4장에 정리했습니다.

## 검토 큐

```
priority = Σ(태그 가중치) + 문서유형 가중치 + 근접중복 1.0 + 동일문서 1.5
```

태그 가중치는 정황 강도에 따라 5(분할·견적통제), 4(은폐·특수관계·중복지급), 3(사적카드), 2(한도근접), 1(PII)입니다. 교차 분석에서 파인딩과 연결된 문서는 +3을 더합니다. 문서 1건만 봐서는 알 수 없던 사실이 파인딩으로 확정되면 그 문서를 다시 볼 이유가 생기기 때문입니다.

결과적으로 상위 15건이 HIGH로 올라오고, 그 안에 분할 발주 4건·동일 견적 3건·중복 지급 2건이 모두 들어옵니다.

## QC

| 지표 | 값 | 의미 |
|---|---:|---|
| 태그 커버리지 | 29.5% | 위험 태그가 붙은 문서 비율. 나머지는 정상 문서 |
| 클러스터 내 제안 일관성 | 92.9% | 같은 클러스터가 같은 관련성 제안을 받았는지 |
| PII 포함 문서 | 83 | 콘솔 본문 발췌에서 주민번호·계좌번호 마스킹 |
| 파인딩 증거 연결 | 20 | 파인딩 8건에 연결된 증거 파일 수 (F-006은 모집단 통계라 인용 없음) |

## 한계

가장 큰 한계는 케이스가 **합성**이라는 점입니다. 규칙이 의도한 패턴을 잡는지는 확인했지만, 실제 데이터의 노이즈·예외를 견디는지는 검증하지 못했습니다.

계산서 84건으로 계산한 벤포드 MAD는 0.0498로 Nigrini 기준(0.015)을 넘지만 표본이 작아 단독 근거가 될 수 없습니다. 그래서 MEDIUM으로 낮추고 참고 지표로 표기했습니다.

중복 지급(F-001)은 계산서가 두 번 존재한다는 사실만 보여줍니다. 실제 이체 내역이 없으면 지급이 두 번 이루어졌는지는 확정할 수 없습니다. 이런 한계를 파인딩마다 명시했습니다.

접근 통제·보존·파기 정책은 스키마 필드만 있고 구현하지 않았습니다.

## Power BI · n8n 연결

포렌식 계층은 SQLite 데모로 끝나지 않습니다. 같은 산출물을 PostgreSQL에 적재해 Power BI로 보고, n8n 스케줄로 매일 다시 돌리는 경로를 함께 두었습니다.

| 파일 | 역할 |
|---|---|
| `sql/09_forensic_pbi_views.sql` | Power BI용 뷰 4종 (증거·검토진행·파인딩·케이스요약) |
| `powerbi/forensic_dax_measures.txt` | 측정값 22개 (증거 건수, 고유 해시율, 검토 진행률, 근접중복 비율, 증거 인용 커버리지 등) |
| `forensic/export_pg.py` | 파인딩을 NDJSON으로 내보내 n8n이 바로 소비 |
| `n8n/forensic_case_workflow.json` | 8개 노드: 스케줄 → 파이프라인 실행 → NDJSON 파싱 → Upsert → 심각도 요약 → CRITICAL 경보 |

측정값은 뷰 컬럼명과 1:1로 맞췄습니다. `Evidence Citation Coverage (%)` 처럼 증거가 연결되지 않은 파인딩을 드러내는 지표를 넣은 이유는, 인용 없는 파인딩이 검토에서 빠졌다는 신호이기 때문입니다.

기존 엔진 쪽도 같이 점검했습니다. n8n의 DQ 검증 노드는 `sql/02` 와 1~153줄이 같고 마지막 조회만 다르며, Power BI 기존 측정값 8개가 참조하는 컬럼 7개는 모두 실제 DB에 있습니다. 다만 n8n 쿼리에는 `ANALYZE` 가 빠져 있어 적재 직후 통계가 비는 문제가 남아 있습니다. 실행 로그와 검증 범위는 [docs/VERIFICATION.md](docs/VERIFICATION.md)에 정리했습니다.

## audit_core — 3계층 리스크 엔진

거래·공시·거시·AI QA 데이터를 `audit_core` 스키마 한 곳에 모아 14종 룰로 파인딩을 만들고, 각 파인딩에 증거를 연결해 워크페이퍼까지 추적합니다. 설계 판단은 [docs/AUDIT_CORE.md](docs/AUDIT_CORE.md).

| 파일 | 역할 |
|---|---|
| `sql/10_audit_core.sql` | Staging·Conformed·Mart 3계층, 테이블 17개 + 뷰 4개 |
| `sql/11_seed_audit_core.sql` | 합성 시드: 전표 5,000건(월 파티션 4개), 공시 8건, 거시 5건, AI QA 40건 |
| `sql/12_rules_engine.sql` | 룰 14종 카탈로그 + `audit_core.run_rules()` + 워크리스트 뷰 |
| `sql/13_evidence_link.sql` | `forensic` 케이스에 증거 8건 등록, 파인딩 27건 연결(커버리지 67.5%) |
| `sql/14_verify_audit_core.sql` | PASS/FAIL 검증 10종 |
| `sql/15_disclosure_events.sql` | 공시 이벤트 스테이징 + `vw_disclosure_watch` |
| `n8n/wf1_regwatch.json` | WF-1 공시 감시 8노드 (Schedule 07:30) |
| `n8n/wf3_rules_engine.json` | WF-3 룰 엔진 8노드 (Schedule 08:00 + Execute Workflow) |
| `n8n/build_audit_workflows.py` | 두 워크플로우 JSON 생성기 (재현용) |

실행 순서는 `10 → 11 → 12 → 13 → 14 → 15`이고, 룰만 다시 돌릴 때는 `SELECT audit_core.run_rules('ENG-2026-001');` 한 줄이면 됩니다. `run_rules()`는 `(engagement_id, rule_id, ref_id)` 유니크 키에 `ON CONFLICT DO UPDATE`를 걸어 두 번 돌려도 파인딩이 늘지 않습니다.

현재 결과는 **룰 14종 / 파인딩 40건(CRITICAL 5 · HIGH 11 · MEDIUM 22 · LOW 2) / 대상 항목 2,225건**입니다. WF-3은 n8n 2.35.7에서 `n8n execute`로 단독 실행해 `status = success`까지 확인했습니다.

## FX-AR / IFRS 9 ECL 시뮬레이터 — Phase 2

`sql/16_ecl_simulator.sql`은 AR·AR_FX를 counterparty·통화별로 집계하고 BASE 및 ±10%·±20% FX shock을 적용해 PD/LGD 기반 ECL을 재산출합니다. 결과는 `dim_fx_scenario`, `dim_pd_lgd_curve`, `fact_ecl_recalc`, `vw_ecl_scenario_summary`, `vw_ecl_top_gaps`로 제공되며, 기본 실행은 기존 40건 finding을 보존합니다. 모델 공식과 가정·한계는 [docs/ECL_MODEL.md](docs/ECL_MODEL.md)에 정리했습니다.

```bash
PGPASSWORD=audit_password123 ./scripts/bootstrap.sh
# 또는 Phase 0/1 DB에서
PGPASSWORD=audit_password123 psql -f sql/16_ecl_simulator.sql
```

## 통합 콘솔 — Phase 1

`console/`은 `audit_core` 결과를 사람이 검토하는 경량 FastAPI 콘솔입니다. 워크리스트에서 파인딩을 필터링하고, 상세 화면의 3-Pane에서 연결 증거의 SHA-256·AI QA 출력·룰 임계값을 함께 확인한 뒤 disposition을 변경할 수 있습니다. 상태 변경은 `fact_risk_finding`과 `change_log`에 한 트랜잭션으로 기록됩니다.

```bash
python3 -m pip install -r requirements-console.txt
PGPASSWORD=audit_password123 uvicorn console.app:app --host 127.0.0.1 --port 8010
```

| 화면/API | 역할 |
|---|---|
| `/findings` · `/api/findings` | 40건 워크리스트, severity/domain/disposition 필터 |
| `/findings/{finding_id}` · `/api/findings/{id}` | Source & Evidence / AI QA / Rule Decision 3-Pane |
| `/coverage` · `/api/coverage` | 증거 커버리지 27/40, 미연결 13건 요청 큐 |
| `/disclosures` · `/api/disclosures` | WF-1 공시 감시 이벤트 |
| `PATCH /api/findings/{id}/disposition` | 사람 검토 상태 전이 + change_log |

화면은 AI가 결론을 확정하는 방식이 아니라, **AI·룰은 후보와 근거를 제시하고 사람이 최종 disposition을 남기는 방식**으로 설계했습니다.

## AI QA 골드셋 하네스 — Phase 3

`sql/17_ai_qa_goldset.sql`은 AI 실행 결과를 독립 골드셋과 대조합니다. 원문 SHA-256, `ai_json`, 필드 일치 수, 환각 플래그, `APPROVED/ESCALATED` 상태를 함께 평가하고 실패 결과는 검토 큐로 보냅니다. 합성 골드셋 10건에서 10건 PASS, 0건 FAIL, 0건 REVIEW를 확인했습니다. 설계와 실제 적용 한계는 [docs/AI_QA_GOLDSET.md](docs/AI_QA_GOLDSET.md)에 정리했습니다.

```bash
PGPASSWORD=audit_password123 psql -f sql/17_ai_qa_goldset.sql
```

## 외부 연동과 credential 계약

실제 제안서 아키텍처의 외부 연동은 저장소에 비밀값을 넣지 않고 runtime secret으로 주입합니다. WF-1은 `DART_API_KEY`와 `AEGIS_WATCH_TARGETS_JSON`이 없으면 합성 데이터로 넘어가지 않고 실패합니다. 감시 대상 법인도 workflow 코드에 하드코딩하지 않습니다. 설정 예시는 [.env.example](.env.example), 운영 전환 절차는 [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md)에 있습니다.

```bash
cp .env.example .env
# .env에 실제 OpenDART key와 감시 대상 JSON을 주입한 뒤
set -a; . ./.env; set +a
python3 scripts/integration_preflight.py --integration dart
```

## 구조

```text
forensic/
  build_corpus.py     합성 케이스 생성기 (시드 고정)
  intake.py           증거 수집·SHA-256·보관 이력·무결성 검증
  review.py           문서 인덱싱·중복·스레드·태깅·검토 큐·QC
  analytics.py        교차 문서 분석 9개 규칙 → 파인딩 레지스트리
  report.py           증거 인용 보고서·Excel 산출물
  export_pg.py        파인딩 NDJSON 내보내기 (n8n 연동)
  run_demo.py         전체 파이프라인 실행
docs/
  FORENSIC_WORKBENCH.md   설계 판단과 한계
  AUDIT_ENGINE.md         기존 거래 검증 엔진(11개 룰, 8277건)
  VERIFICATION.md         실행·대조 검증 기록
  AUDIT_CORE.md           audit_core 3계층·룰 14종·증거 커버리지·한계
  INTEGRATIONS.md         DART·AI·Postgres·Slack credential 계약
console/                 FastAPI 3-Pane 검토 콘솔 (워크리스트·증거·공시)
scripts/bootstrap.sh     PostgreSQL sql/01~17 재현 실행기
tests/test_audit_core.py audit_core 기준선 계약 테스트
sql/
  01~07                   거래 검증 엔진 (PostgreSQL)
  08_forensic_case_schema.sql  포렌식 계층 스키마
  09_forensic_pbi_views.sql    포렌식 Power BI 뷰 4종
  10~17                   audit_core 3계층·룰 14종·증거 연결·검증·공시 감시·ECL·AI QA 골드셋
python/                 BankSim·서울시 데이터 적재, 검토 시트 생성
n8n/                    수집·검증 워크플로우, 포렌식 케이스, 공시 감시(WF-1), 룰 엔진(WF-3)
powerbi/                DAX 측정값 (거래 검증 8개, 포렌식 22개)
reports/                기존 분석 보고서
```

## 데이터 출처

`forensic` 계층의 문서는 전부 `build_corpus.py` 가 만든 합성 자료이며 실제 기업·개인과 무관합니다. 기존 엔진이 쓰는 BankSim(공개 시뮬레이션)과 서울시 본청 업무추진비(OA-22156, 공개 데이터)의 출처와 이용 조건은 [docs/AUDIT_ENGINE.md](docs/AUDIT_ENGINE.md)에 그대로 두었습니다.
