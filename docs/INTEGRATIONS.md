# 외부 연동·Credential 계약

Aegis-Guard는 저장소에 API key를 넣지 않는다. 워크플로우와 코드에는 환경변수명·credential 이름·실패 조건만 정의하고, 실제 값은 실행 환경에서 주입한다.

## 필수 연동

### OpenDART

WF-1 실제 실행에는 다음 두 값이 모두 필요하다.

```bash
DART_API_KEY=...
AEGIS_WATCH_TARGETS_JSON='[{"corp_code":"00123456","engagement_id":"ENG-2026-001"}]'
```

`DART_API_KEY`가 없거나 감시 대상 JSON이 비어 있으면 WF-1은 합성 데이터로 조용히 대체하지 않고 즉시 실패한다. 감시 대상 법인 코드는 workflow source에 하드코딩하지 않는다.

```bash
python3 scripts/integration_preflight.py --integration dart
```

키 값은 출력·로그·Git에 남기지 않는다. n8n에서는 환경변수 또는 n8n credential 정책으로 주입하고, Postgres와 Slack은 각각 n8n credential(`PG_CRED`, `SLACK_CRED`)로 연결한다.

## AI 모델

Phase 3 골드셋 하네스는 모델 API 없이도 평가 계약을 검증한다. 실제 AI 추출을 활성화할 때만 선택한 모델 provider key를 runtime secret으로 추가한다. 모델 출력은 반드시 `fact_ai_qa_run`에 모델 버전·프롬프트 버전·원문 해시와 함께 적재하고, 이후 `run_ai_qa_goldset()`을 통과해야 사람 검토 큐로 전달한다.

## DART·ECOS source adapter

`sql/18_source_registry.sql`은 source system registry와 `stg_source_payload` raw lineage를 만든다. 실제 API 응답은 정규화 전에 request fingerprint와 payload SHA-256으로 저장한다.

```bash
python3 scripts/source_adapters.py dart \
  --corp-code 00123456 --start 20260101 --end 20260331 \
  --engagement-id ENG-2026-001

python3 scripts/source_adapters.py ecos \
  --stat-code 731Y001 --cycle D --start 2026-01-01 --end 2026-03-31 \
  --item 0000001 --engagement-id ENG-2026-001

python3 scripts/source_adapters.py procurement-bid \
  --start 202611140000 --end 202611142359 \
  --engagement-id ENG-2026-001
```

위 명령은 `DART_API_KEY` 또는 `ECOS_API_KEY`가 없으면 HTTP 요청 전에 실패한다. 성공 시에도 key는 fingerprint, 로그, raw payload에 포함하지 않는다. DART list endpoint의 `crtfc_key`, `corp_code`, 기간·페이지 파라미터는 [OpenDART 공식 개발가이드](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001) 기준이며, ECOS는 [한국은행 ECOS Open API](https://ecos.bok.or.kr/api/)의 `StatisticSearch/{key}/json/kr/...` 경로 계약을 사용한다.

## 현재 상태

- WF-1: OpenDART HTTP node와 `DART_API_KEY` 참조 구현 완료
- WF-1: credential/target preflight와 fail-closed 동작 구현 완료
- DART·ECOS: raw payload adapter와 source registry 구현 완료; 실제 key 주입 후 end-to-end 호출 대상
- live verification: DART 100건·ECOS 일별 환율 59건을 raw payload로 저장하고 각각 staging/fact에 100% lineage 연결
- live verification: 조달청 나라장터 입찰공고 API에서 2026-11-14 하루 범위 58건을 수신하고 `fact_procurement_event`에 58건 정규화·lineage 연결
- 로컬 샌드박스: credential은 세션 runtime으로만 주입했으며 파일·로그·저장소에는 저장하지 않음
- 합성 seed: 룰·ECL·AI QA 계약 테스트용으로만 사용

## 공정위·조달 공공데이터 계약

공정위·조달 제재/처분의 공통 정규화 대상은 `fact_sanction_event`이며, 원천 응답은 먼저 `stg_source_payload`에 저장한다. 필수 lineage는 `source_payload_id`, `source_code`, `source_record_id`, 사업자번호/법인번호, 처분일, 제재유형, 사건번호, 원문 locator다. 공공데이터포털 인증키가 없을 때는 호출하지 않고 실패한다.

현재 확인된 공식 조달 서비스는 [나라장터 입찰공고정보서비스](https://www.data.go.kr/data/15129394/openapi.do), [나라장터 계약과정통합공개서비스](https://www.data.go.kr/data/15129459/openapi.do), [나라장터 공공데이터개방표준서비스](https://www.data.go.kr/data/15058815/openapi.do)다. 이 중 계약과정통합공개서비스는 `apis.data.go.kr/1230000/ao/CntrctProcssIntgOpenService` base URL과 식별번호 기반 조회 계약이 확인됐으며, 최신 포털 정책상 서비스별 조회기간 제한도 적용될 수 있다. 공정위 제재 dataset과 조달 제재 dataset의 정확한 endpoint·오퍼레이션은 API key 수령 및 활용신청 상태 확인 후 확정한다.

실제 키가 없는 상태에서 성공으로 표시하는 것은 금지한다. credential 주입 후에는 preflight → DART test call → `stg_disclosure_event` upsert → `run_rules()` → Slack 알림 순서로 end-to-end 검증한다.
