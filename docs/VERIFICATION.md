

## 8. Phase 1 통합 콘솔

`console/`에 FastAPI + Jinja2 기반 검토 콘솔을 추가했습니다. Power BI Desktop 없이도 브라우저에서 `audit_core` 결과를 검토할 수 있도록 서버 렌더링 방식으로 구현했습니다.

| 검증 | 명령·방법 | 결과 |
|---|---|---|
| 기준선 계약 테스트 | `PGPASSWORD=... pytest -q tests/test_audit_core.py` | PASS 4개 |
| Python 컴파일 | `python3 -m compileall -q console tests` | PASS |
| 서버 health | `curl http://127.0.0.1:8010/health` | 200 / `status=ok` |
| 워크리스트 API | `GET /api/findings?limit=2` | 200, finding_id·룰·심각도·detail 반환 |
| 워크리스트 HTML | `GET /findings` | 200, 40건 기반 표·필터·severity 배지 |
| 3-Pane 상세 | `GET /findings/{finding_id}` | 200, Source & Evidence·AI QA Output·Rule Decision |
| 증거 커버리지 | `GET /coverage`, `/api/coverage` | 200, 27/40·67.5%·미연결 13건 |
| 공시 감시 | `GET /disclosures`, `/api/disclosures` | 200 |
| disposition 전이 | `PATCH /api/findings/{id}/disposition` | 200, `IN_REVIEW` 전이 후 `OPEN` 복원 |
| 감사 추적 | `change_log` 조회 | `console.smoke` 변경 2행 기록 |

구현 중 `vw_finding_worklist`에 `finding_id`가 노출되지 않는 것을 확인해, 콘솔 목록 조회는 해당 뷰가 아니라 `fact_risk_finding`과 `rule_catalog`을 직접 조인하도록 조정했습니다. 화면에서 상세 링크가 실제 UUID를 가리키는지까지 확인했습니다.

현재 콘솔은 데모용 단일 환경이며 인증·RLS는 구현하지 않았습니다. 다음 단계에서 실제 프로젝트 사용자·engagement별 접근 통제를 붙일 수 있습니다.

## 9. Phase 2 FX-AR / IFRS 9 ECL 시뮬레이터

`sql/16_ecl_simulator.sql`을 Phase 0/1 데이터베이스에 실행하고, 기본 옵션(`p_emit_findings = FALSE`)으로 분석 테이블만 갱신했습니다.

| 검증 | 명령·방법 | 결과 |
|---|---|---|
| SQL 실행 | `psql --set ON_ERROR_STOP=1 -f sql/16_ecl_simulator.sql` | PASS; 5개 scenario × 66개 exposure upsert |
| 시나리오 | `SELECT * FROM audit_core.dim_fx_scenario` 및 summary view | BASE, ±10%, ±20% 5개 |
| PD/LGD curve | `SELECT * FROM audit_core.dim_pd_lgd_curve` | KSIC 29199 / UNRATED / PD 4% / LGD 45% 1행 |
| 결과 grain | `fact_ecl_recalc` | scenario별 66행, FX exposure 33행 |
| 방향성 | summary의 stressed EAD·ECL 비교 | FX 상승 시 증가, 하락 시 감소; KRW exposure 불변 |
| 기준선 보존 | `SELECT count(*) FROM audit_core.fact_risk_finding` | 40건 유지 |

실행 결과의 BASE summary는 EAD 12,204,065,000원, ECL 212,244,609원, gap -107,755,391원(50.77%)이다. `docs/ECL_MODEL.md`에 전체 scenario 결과와 입력 가정을 기록했다. 데이터와 PD/LGD는 합성 입력이므로 실제 IFRS 9 보고 모델로 사용하지 않는다.

## 10. Phase 3 AI QA 골드셋 하네스

`sql/17_ai_qa_goldset.sql`을 실행해 기존 합성 AI QA 40건 중 10건을 골드셋으로 고정하고, exact-match·원문 해시·환각·승격 상태를 평가했습니다.

| 검증 | 명령·방법 | 결과 |
|---|---|---|
| SQL 실행 | `psql --set ON_ERROR_STOP=1 -f sql/17_ai_qa_goldset.sql` | PASS |
| 골드셋 범위 | `dim_ai_qa_goldset` | 10건: clean 5 / low accuracy 2 / hallucination 3 |
| 평가 결과 | `vw_ai_qa_goldset_summary` | 10 evaluated / 10 PASS / 0 FAIL / 0 REVIEW |
| 환각 gate | `hallucination_cases_pass` | 3/3 |
| escalation gate | `low_accuracy_escalated` | 2/2 |
| 변조 검출 | `contract_001` amount 임시 변경 후 transaction rollback | FAIL 1건 검출, 원상 복구 |
| 기준선 보존 | `fact_risk_finding` count | 40건 유지 |

현재 골드셋은 합성 seed의 truth에서 파생된 개발용 계약 테스트이며, 실제 고객 적용 전에는 독립 human-reviewed goldset과 reviewer sign-off로 교체해야 합니다.

## 11. 외부 연동 credential 계약

수정 제안서의 운영 아키텍처에 맞춰 외부 연동을 fail-closed 방식으로 정리했습니다.

| 검증 | 명령·방법 | 결과 |
|---|---|---|
| 대상·키 preflight | `python3 scripts/integration_preflight.py --integration dart` | 실제 key 미주입 환경에서는 의도적으로 FAIL; 합성 fallback 없음 |
| 대상 JSON 검증 | `pytest -q tests/test_integration_preflight.py` | PASS; 빈 배열·필드 누락·malformed JSON 차단 |
| workflow 재생성 | `python3 n8n/build_audit_workflows.py` | PASS; `DART_API_KEY`와 `AEGIS_WATCH_TARGETS_JSON` runtime 주입 |
| 하드코딩 점검 | `wf1_regwatch.json` 검색 | corp code를 workflow source에서 제거 |

실제 OpenDART end-to-end 호출은 credential 주입 후 별도 실행해야 하며, 현재 샌드박스에는 DART key가 없어 성공으로 가장하지 않았다.

## 12. Architecture v2 정합성 기준

사용자가 제공한 Architecture v2를 구현 기준선으로 고정했습니다. 원천 데이터 → WF-1~WF-4 → PostgreSQL master/staging/fact/mart → 검증 엔진·예외 큐 → HITL → Power BI/RFP/Word·Excel 산출물의 추적성은 [docs/ARCHITECTURE_V2.md](ARCHITECTURE_V2.md)에 기록했습니다.

| 검증 | 명령·방법 | 결과 |
|---|---|---|
| WF-1~WF-4 산출물 | `python3 n8n/build_audit_workflows.py` | PASS; 4 workflow JSON 생성 |
| JSON 구조 | `python3 -m json.tool n8n/wf*.json` | PASS |
| WF-2 PBC 경계 | PBC input preflight → ingestion log → tie-out 예외 → Slack | 구현 완료, 실제 PBC 파일 연결 대상 |
| WF-4 AI/조서 경계 | goldset QA → summary → export manifest → HITL 예외 | 구현 완료, 실제 Canvas/Solar adapter·Word/Excel writer 대상 |
| 원천 adapter | DART credential contract | WF-1 runtime key·target 주입 및 fail-closed; ECOS/공정위/XBRL 추가 대상 |

공정위·조달 영역은 `sql/19_sanction_registry.sql`로 source-neutral `fact_sanction_event`와 원천 payload FK·사업자번호·처분일·제재유형·사건번호·원문 locator 계약을 추가했습니다. 공공데이터포털 API key가 아직 없으므로 외부 호출과 합성 성공 처리는 하지 않았고, 정확한 dataset별 endpoint·요청변수·응답 필드 확정 및 live adapter는 key 수령 후 진행합니다.

실제 credential을 세션에 비노출 주입해 DART·ECOS를 end-to-end 검증했습니다. OpenDART는 공개 법인코드로 공시 100건을 수신해 `stg_source_payload`와 `stg_disclosure_event`에 100건 연결했고, ECOS는 일별 원/미국달러 환율 59건을 수신해 `stg_source_payload`와 `fact_macro_ecos`에 59건 연결했습니다. 키 값은 파일·로그·Git에 저장하지 않았습니다.

조달청은 두 credential fragment를 결합한 runtime 값으로 공식 `PubDataOpnStdService/getDataSetOpnStdBidPblancInfo`를 호출했습니다. 공식 문서의 `YYYYMMDDHHMM` 기간 형식으로 2026-11-14 하루 범위를 조회해 58건을 수신했고, `stg_source_payload` payload_id=6과 `fact_procurement_event` 58건에 연결했습니다. API key는 파일·로그·Git에 저장하지 않았습니다.

조달 후보 룰 `PPS-01~PPS-03`도 실행했습니다. 각각 7건·4건·5건의 후보를 생성해 기존 40건과 분리된 총 56건의 finding을 만들었고, 16건 모두 `OPEN`으로 남겼습니다. 동일 함수를 재실행하면 신규 0건·갱신 16건으로 멱등성을 확인했습니다. 확정·오탐 판정은 아직 수행하지 않았습니다.

조달 후보 16건에는 `EV-PPS-000006` raw API payload evidence를 연결했습니다. evidence SHA-256, payload ID, 공고번호, 공식 locator가 3-Pane 조회에서 함께 반환되는 것을 smoke-test했고, 전체 coverage는 56건 중 43건, 76.79%로 확인했습니다. 기존 40건의 legacy evidence 기준선은 67.50%로 유지됩니다.
