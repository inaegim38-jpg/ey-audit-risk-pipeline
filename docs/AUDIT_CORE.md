# audit_core — 3계층 감사 리스크 엔진

거래·공시·거시 데이터를 한 스키마에 모아 **14종 룰**로 리스크 파인딩을 만들고, 각 파인딩에 **증거를 연결**해 워크페이퍼까지 추적하는 계층이다.
기존 `audit_risk`(거래 DQ), `forensic`(케이스·증거)와 나란히 있으며, `audit_core`는 그 둘을 **공시·거시·AI QA까지 포함하는 상위 그레인**으로 묶는다.

```
Staging            Conformed                Mart
─────────          ─────────────            ──────────────────────────────
stg_ingestion_log  dim_entity               fact_risk_finding  ← run_rules()
stg_disclosure_    dim_coa_mapping          finding_evidence   ← evidence_link
  event            dim_engagement           vw_finding_worklist
                   dim_industry_stats       vw_rule_run_summary
                   fact_journal (월 파티션)  vw_evidence_coverage
                   fact_disclosure_line     vw_disclosure_watch
                   fact_macro_ecos
                   fact_ai_qa_run
                   rule_catalog
                   change_log
```

## 1. 객체 목록 (17 테이블 + 4 뷰)

| 계층 | 객체 | 그레인 | 비고 |
|---|---|---|---|
| Staging | `stg_ingestion_log` | 적재 배치 1건 | 소스·행수·해시·상태 |
| Staging | `stg_disclosure_event` | 공시 이벤트 1건 | DART 공시 감시(WF-1) 적재처 |
| Conformed | `dim_entity` | 법인·거래처 1건 | 41건. 업종·상장구분·휴폐업 상태 |
| Conformed | `dim_coa_mapping` | 계정 1건 | 표준계정 ↔ 회사계정 매핑 |
| Conformed | `dim_engagement` | 감사 engagement 1건 | `ENG-2026-001` |
| Conformed | `dim_industry_stats` | 업종 1건 | 평가손실률 평균·표준편차(INV-01 기준선) |
| Conformed | `fact_journal` | 전표 1건 | 5,000건. `fiscal_period` 월 파티션 4개 |
| Conformed | `fact_disclosure_line` | 공시 계정 1건 | 8건. 재무제표 본문 수치 |
| Conformed | `fact_macro_ecos` | 통화·기간 1건 | 5건. 환율·금리 |
| Conformed | `fact_ai_qa_run` | AI 실행 1건 | 40건. 필드 일치율·환각 플래그 |
| Mart | `rule_catalog` | 룰 1건 | 14종. 임계값 JSONB + 버전 |
| Mart | `fact_risk_finding` | (engagement, rule, ref_id) 1건 | 40건. `UNIQUE`로 멱등 보장 |
| Mart | `finding_evidence` | 파인딩↔증거 1건 | 29행 / 파인딩 27건 커버 |
| Audit | `change_log` | 변경 1건 | 룰·임계값 변경 이력 |

뷰: `vw_finding_worklist`(검토 큐), `vw_rule_run_summary`(룰별 실행 요약), `vw_evidence_coverage`(증거 커버리지), `vw_disclosure_watch`(공시 감시).

## 2. 실행 순서

```bash
export PGPASSWORD=...   # 로컬 audit_db
psql -h 127.0.0.1 -U audit_admin -d audit_db -f sql/10_audit_core.sql          # 스키마
psql -h 127.0.0.1 -U audit_admin -d audit_db -f sql/11_seed_audit_core.sql     # 합성 시드
psql -h 127.0.0.1 -U audit_admin -d audit_db -f sql/12_rules_engine.sql        # 룰 14종 + run_rules()
psql -h 127.0.0.1 -U audit_admin -d audit_db -f sql/13_evidence_link.sql       # 증거 등록·연결
psql -h 127.0.0.1 -U audit_admin -d audit_db -f sql/14_verify_audit_core.sql   # PASS/FAIL 10종
psql -h 127.0.0.1 -U audit_admin -d audit_db -f sql/15_disclosure_events.sql   # 공시 감시
```

룰만 다시 돌릴 때는 `SELECT audit_core.run_rules('ENG-2026-001');` 한 줄이면 된다.

## 3. 룰 14종

| 룰 | 명칭 | 도메인 | 심각도 | 임계값 | 파인딩 | 대상 항목 |
|---|---|---|---|---|---:|---:|
| AI-01 | AI 추출 정확도 미달 | AI_QA | MEDIUM | 일치율 < 95% | 3 | 5 |
| AI-02 | AI 환각 | AI_QA | CRITICAL | `hallucination = true` | 3 | 3 |
| FX-01 | 환율 충격 국외채권 노출 | FX_AR_ECL | HIGH | 충격 10%, 손실 ≥ 5천만원 | 1 | 1 |
| FX-02 | ECL 재산출 갭 | FX_AR_ECL | HIGH | 갭 ≥ 15%, LGD 45% | 1 | 1 |
| INV-01 | 재고 평가손실률 이상 | INVENTORY | MEDIUM | 업종 평균 + 3σ | 1 | 1 |
| INV-02 | 투자부동산 평가 방향 불일치 | INVESTMENT_PROPERTY | MEDIUM | 평가방향 역전 | 1 | 1 |
| JET-01 | 벤포드 1자리 편차 | FORENSIC_JET | LOW | MAD > 0.015 | 2 | 2 |
| JET-02 | 직무분리(SoD) 위반 | FORENSIC_JET | HIGH | 기안자 = 승인자 | 3 | 60 |
| JET-03 | 주말·심야 전표 | FORENSIC_JET | MEDIUM | 22:00–06:00, 비영업일 | 3 | 1,509 |
| JET-04 | 계정 내 다변량 이상 | FORENSIC_JET | MEDIUM | z-score > 3.5 | 14 | 14 |
| PROV-01 | 우발부채 미계상 | PROVISION | HIGH | 소송 ≥ 1억원 & 충당부채 0 | 1 | 1 |
| REL-01 | 미공시 특수관계자 | RELATED_PARTY | HIGH | 거래 有 & 주석 無 | 1 | 125 |
| REL-02 | 휴·폐업 거래처와 거래 | RELATED_PARTY | HIGH | 상태 ∈ {DORMANT, CLOSED} | 4 | 500 |
| TIE-01 | 3-Way Tie-out 불일치 | DATA_QUALITY | CRITICAL | 차이 > 1,000원 | 2 | 2 |
| **합계** | | | | | **40** | **2,225** |

심각도 분포: **CRITICAL 5 / HIGH 11 / MEDIUM 22 / LOW 2**.
파인딩 1건이 여러 전표를 묶는 구조라 `item_count` 합계(2,225)가 실제 검토 대상 건수다. JET-03이 1,509건으로 가장 크다.

## 4. run_rules 설계 포인트

- **멱등**: `fact_risk_finding`의 `UNIQUE (engagement_id, rule_id, ref_id)`에 `ON CONFLICT ... DO UPDATE`. 2회차 실행 시 `new = 0`, `updated = 40`.
- **ref_id 안정화**: `ref_id`에 `now()`나 실행 시각을 넣으면 매번 새 행이 생긴다. 공시 최대 `fiscal_period`를 `v_period`로 고정해 `rule_id || ':' || period` 형태로 만든다.
- **컬럼명 충돌**: PL/pgSQL에서 `RETURNS TABLE` 컬럼과 테이블 컬럼이 겹치면 `#variable_conflict use_column` 지시어가 필요하다. 없으면 `rule_id` 참조가 모호해져 0건이 나온다.
- **명칭 정규화**: REL-01은 거래처명 공백·㈜·(주) 표기를 제거한 뒤 비교한다. 정규화 전에는 43건, 후에는 40건으로 오탐 3건이 줄었다.
- **룰 추가**: `rule_catalog`에 행을 넣고 `run_rules()` 안에 `INSERT ... SELECT` 블록 하나를 더하면 된다. 임계값은 코드가 아니라 `threshold` JSONB에서 읽는다.

## 5. 증거 연결

`sql/13_evidence_link.sql`은 `forensic` 스키마의 케이스(`CASE-2026-AUD`)에 증거 8건을 등록하고, 규칙 기반으로 파인딩에 연결한다.

| 증거 | 유형 | 연결 대상 |
|---|---|---|
| EV-AUD-001~005 | 공시·계약·회의록 | TIE-01, PROV-01, REL-01/02, AI-02 |
| EV-AUD-GL | 총계정원장 추출 | JET-01~04, FX-01/02 |
| EV-AUD-TB | 시산표 | TIE-01, INV-01/02 |

커버리지 **67.5% (27/40)**. 나머지 13건은 증거가 아직 없는 파인딩으로, 검토 큐에서 `OPEN` 상태로 남아 증거 요청 대상이 된다. 커버리지를 100%로 올리는 것은 목적이 아니다 — **증거 없는 파인딩을 드러내는 것**이 이 뷰의 역할이다.

## 6. n8n 워크플로우

| ID | 이름 | 노드 | 트리거 | 역할 |
|---|---|---|---:|---|
| `aegis-wf1-regwatch` | WF-1 공시 감시 | 8 | Schedule 07:30 | DART 공시 조회 → 신규 이벤트 판별 → `stg_disclosure_event` 적재 → Slack 알림 |
| `aegis-wf3-rules` | WF-3 룰 엔진 | 8 | Schedule 08:00 + Execute Workflow | `run_rules()` 호출 → 심각도 집계 → Slack 요약 |

WF-3은 `Execute Workflow Trigger`를 함께 두어 다른 워크플로우에서 호출하거나 `n8n execute --id aegis-wf3-rules`로 단독 실행할 수 있다. 실행 결과 `status = success`, 메시지 `[Aegis] 룰 실행 완료: 14종 / CRITICAL 2 · HIGH 6`이 생성되는 것을 확인했다(여기서 2·6은 **룰 종류 수**, 파인딩 건수는 40).

## 7. 한계와 보완

| 한계 | 영향 | 보완 |
|---|---|---|
| ECL은 업종 커버리지 근사 | 실제 PD·LGD 커브 미사용 | `fact_macro_ecos`에 통화별 커브를 붙이고 FX-02를 시나리오 3종으로 확장 |
| REL-01 명칭 정규화가 휴리스틱 | 상호 변경·영문 표기 미포함 | 거래처 마스터에 사업자번호를 키로 추가, 명칭은 보조 지표로만 |
| JET-03이 전체의 68% | 노이즈가 검토 큐를 압도 | 마감일 전후 3일 가중치, 반복 기표자 필터 추가 |
| 합성 데이터 5,000건 | 실데이터 규모 효과 미검증 | 파티셔닝·인덱스는 월 단위로 설계되어 있어 확장 가능 |
| 증거 커버리지 67.5% | 13건은 증거 미확보 | 커버리지를 KPI로 두지 않고, 미확보 목록을 요청 큐로 운영 |

## 8. 검증

`sql/14_verify_audit_core.sql`의 10개 체크가 모두 PASS다. 상세 기록은 [VERIFICATION.md](VERIFICATION.md) 7절 참조.

- 룰 활성 14종
- 파인딩 40건, 중복 0
- 멱등: 2회차 `new = 0`, `updated = 40`
- SoD 대상 60건 = 원본 60행
- 해시 단독 유니크 없음(해시 충돌 오탐 방지)
- 공시 그레인 1행/계정
- 파티션 4개
