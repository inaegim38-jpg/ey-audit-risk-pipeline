# Phase 3 — AI QA 골드셋 하네스

## 목적

`sql/17_ai_qa_goldset.sql`은 AI 추출 결과를 사람의 최종 판단으로 오인하지 않도록, 모델 실행 기록을 독립된 평가 계층에서 재현 가능하게 검사한다. 하네스는 다음 네 가지를 고정한다.

1. 원문 식별: `source_doc_ref`와 `source_sha256`
2. 구조화 추출 일치: `ai_json`, `field_exact`, `field_total`
3. 환각 판정: `hallucination_yn`
4. 운영 상태: 정확도·환각 결과에 따른 `APPROVED` / `ESCALATED`

실패 또는 해시 불일치 결과는 모델의 최종 판단이 아니라 사람 검토 큐의 후보로 남긴다.

## 데이터 모델

| 객체 | 역할 |
|---|---|
| `dim_ai_qa_goldset` | 기대 JSON, 필드 정답 수, 환각 여부, 기대 상태를 버전과 함께 저장 |
| `fact_ai_qa_eval` | 실제 QA 실행과 골드셋 비교 결과를 저장 |
| `run_ai_qa_goldset()` | 멱등 평가 실행 함수 |
| `vw_ai_qa_goldset_summary` | pass/fail/review, 환각·저정확도 지표 |
| `vw_ai_qa_review_queue` | 실패·해시 불일치 검토 대상 |

## 합성 골드셋

현재 골드셋은 기존 합성 QA 40건 중 10건을 고정한다.

- 정상 추출 5건: `contract_001`~`005`
- 저정확도 2건: `contract_007`, `contract_021`
- 환각 포함 3건: `contract_013`, `contract_034`, `contract_039`

실행 결과는 **10건 평가, 10건 PASS, 0건 FAIL, 0건 REVIEW, pass rate 100.00%**였다. 환각 케이스 3건은 환각 플래그와 ESCALATED 상태를 모두 통과했고, 저정확도 케이스 2건도 ESCALATED 상태를 통과했다.

## 평가 규칙

- `source_sha256`가 다르면 원문 변경 또는 잘못된 문서 연결 가능성이므로 `REVIEW`
- `ai_json`, `field_exact`, `source_sha256`가 기대값과 모두 일치해야 exact-match gate PASS
- `hallucination_yn`이 골드셋 기대값과 같아야 hallucination gate PASS
- `exact_match_rate < 95` 또는 환각이면 기대 상태는 `ESCALATED`, 그 외에는 `APPROVED`
- 모든 gate가 통과해야 최종 `PASS`

테스트에서는 `contract_001.pdf`의 amount를 임시로 변경해 하네스가 FAIL을 검출하는지 확인한 뒤 transaction rollback으로 원상 복구했다.

## 실행

```bash
export PGPASSWORD='...'
./scripts/bootstrap.sh
# 기존 Phase 0~2 DB에서만 실행
psql -f sql/17_ai_qa_goldset.sql
```

재실행 시 골드셋과 평가 결과는 고유 키를 기준으로 upsert되며, 기존 `fact_ai_qa_run`과 `fact_risk_finding`을 변경하지 않는다.

## 한계와 실제 적용 전환

현재 골드셋은 합성 seed의 `truth_json`에서 파생되므로 모델의 실제 일반화 성능을 증명하지 않는다. 실제 업무에서는 원문 해시가 고정된 문서 표본을 선정하고, 독립된 2인 검토 또는 reviewer sign-off로 기대 JSON과 환각 라벨을 확정해야 한다. 이후 모델·프롬프트 버전별로 동일 골드셋을 반복 실행해 정확도, 환각률, escalation recall, reviewer override를 추적한다.

또한 현재 exact-match는 JSON 구조와 저장된 field count를 비교하는 계약이다. 숫자 단위 정규화, 날짜 표현의 동치성, 부분 추출의 업무 중요도 가중치, 필드별 tolerance는 다음 버전에서 도메인별 evaluator로 확장해야 한다.
