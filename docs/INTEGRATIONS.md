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
- procurement candidate rules: `SELECT * FROM audit_core.run_procurement_rules('ENG-2026-001');` 실행 시 PPS-01 7건, PPS-02 4건, PPS-03 5건 후보 생성; 모두 `OPEN`이며 HITL disposition 전 확정하지 않음
- procurement 3-Pane: `EV-PPS-*` raw payload evidence와 SHA-256·공고번호·공식 locator를 `finding_evidence`와 detail pane에서 함께 조회
- 로컬 샌드박스: credential은 세션 runtime으로만 주입했으며 파일·로그·저장소에는 저장하지 않음
- 합성 seed: 룰·ECL·AI QA 계약 테스트용으로만 사용

## PBC CSV intake 계약

PBC 원본 파일은 저장소에 복사하지 않고, 보안 파일 경로에서 `scripts/pbc_intake.py`로 fail-closed 검증한다. 지원 파일 유형은 `GL`, `TB`, `AR`, `CONTRACT`이며, 실행 전에 engagement와 파일 경로를 명시해야 한다.

```bash
python3 scripts/pbc_intake.py \
  --file /secure/PBC/GL_202603.csv \
  --file-type GL \
  --engagement-id ENG-2026-001 \
  --expected-sha256 <64-char-sha256> \
  --ingested-by reviewer@example.com
```

이 단계는 파일 전체 SHA-256, 정규화된 header SHA-256, 행수, 절대 경로, 파일 유형을 `audit_core.stg_ingestion_log`에 기록한다. `GL`·`TB`는 debit-credit 차이를 계산하고 절대값이 1 이하이면 `PASSED`, 초과하면 `FAILED`로 기록한다. `AR`·`CONTRACT`는 구조와 행수 검증 후 실제 업무 tie-out 전까지 `PENDING`으로 남긴다. 파일 누락, 비CSV, 빈 파일, UTF-8 오류, 필수 컬럼 누락, 숫자 오류, expected hash 불일치는 성공으로 기록하지 않고 종료 코드 2로 실패한다.

필수 컬럼은 `GL`: `doc_no, posting_date, account_code, debit, credit`, `TB`: `account_code, debit, credit`, `AR`: `invoice_no, customer_id, amount, currency`, `CONTRACT`: `contract_id, vendor_id, contract_amount`이다. 동일 engagement와 동일 파일 hash를 재실행하면 ingestion log를 upsert해 중복 배치를 만들지 않는다. 실제 파일이 제공되기 전에는 live PBC 행수나 tie-out 결과를 주장하지 않는다.

WF-2 n8n은 `AEGIS_PBC_INPUTS_JSON`을 intake 실행 결과 manifest로만 소비한다. 각 항목에 `engagement_id`, `file_name`, `pbc_file_type`, `sha256_hash`, `source_path`, `header_sha256`, `row_count`, `debit_credit_diff`, `tieout_status`가 모두 있어야 하며, 허용 상태는 `PASSED`, `FAILED`, `PENDING`뿐이다. WF-2는 사용자가 임의로 입력한 행수나 차대조 결과를 성공값으로 승격하지 않고, 검증된 metadata와 lineage를 `stg_ingestion_log`에 upsert한다.

수령한 CSV의 행을 conformed fact로 적재하려면 다음 단계를 사용한다.

```bash
python3 scripts/pbc_load_and_tieout.py load \
  --file /secure/PBC/GL_202603.csv \
  --file-type GL \
  --engagement-id ENG-2026-001 \
  --expected-sha256 <64-char-sha256>

python3 scripts/pbc_load_and_tieout.py tieout \
  --engagement-id ENG-2026-001
```

`sql/24_pbc_tieout_facts.sql`은 GL line, TB balance, AR item, contract fact와 `pbc_tieout_result`를 만든다. 현재 `PBC-TB-01`은 계정별 GL–TB debit/credit aggregate를 비교해 `PASSED`/`FAILED`를 기록하고, `PBC-AR-01`과 `PBC-CON-01`은 control-account 또는 상세 계약 대사 계약이 들어오기 전까지 총액·행수와 함께 `PENDING`으로 남긴다. 모든 적재와 tie-out 결과는 batch UUID와 파일 hash로 원본 intake에 연결된다.

## 공정위·조달 공공데이터 계약

공정위·조달 제재/처분의 공통 정규화 대상은 `fact_sanction_event`이며, 원천 응답은 먼저 `stg_source_payload`에 저장한다. 필수 lineage는 `source_payload_id`, `source_code`, `source_record_id`, 사업자번호/법인번호, 처분일, 제재유형, 사건번호, 원문 locator다. 공공데이터포털 인증키가 없을 때는 호출하지 않고 실패한다.

현재 확인된 공식 조달 서비스는 [나라장터 입찰공고정보서비스](https://www.data.go.kr/data/15129394/openapi.do), [나라장터 계약과정통합공개서비스](https://www.data.go.kr/data/15129459/openapi.do), [나라장터 공공데이터개방표준서비스](https://www.data.go.kr/data/15058815/openapi.do)다. 이 중 계약과정통합공개서비스는 `apis.data.go.kr/1230000/ao/CntrctProcssIntgOpenService` base URL과 식별번호 기반 조회 계약이 확인됐으며, 최신 포털 정책상 서비스별 조회기간 제한도 적용될 수 있다. 공정위 제재 dataset과 조달 제재 dataset의 정확한 endpoint·오퍼레이션은 API key 수령 및 활용신청 상태 확인 후 확정한다.

실제 키가 없는 상태에서 성공으로 표시하는 것은 금지한다. credential 주입 후에는 preflight → DART test call → `stg_disclosure_event` upsert → `run_rules()` → Slack 알림 순서로 end-to-end 검증한다.
