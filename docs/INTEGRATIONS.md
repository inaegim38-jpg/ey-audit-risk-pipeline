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

## 현재 상태

- WF-1: OpenDART HTTP node와 `DART_API_KEY` 참조 구현 완료
- WF-1: credential/target preflight와 fail-closed 동작 구현 완료
- 로컬 샌드박스: 실제 DART key가 주입되지 않아 외부 호출은 의도적으로 실행하지 않음
- 합성 seed: 룰·ECL·AI QA 계약 테스트용으로만 사용

실제 키가 없는 상태에서 성공으로 표시하는 것은 금지한다. credential 주입 후에는 preflight → DART test call → `stg_disclosure_event` upsert → `run_rules()` → Slack 알림 순서로 end-to-end 검증한다.
