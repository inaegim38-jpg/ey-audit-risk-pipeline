# 포트폴리오 재구성 완료 보고서 (실제 실행 결과만 기재)

기준: 저장소 HEAD `74c49c2` 위에 작업. 검증 환경은 **샌드박스 네이티브 PostgreSQL 16 + npm n8n 2.35.7**이며 **Docker는 사용하지 않았습니다.** Windows PC·Docker Desktop 환경에서의 재실행은 하지 않았습니다.

## 1. 시작 시점에서 발견한 문제
- `scripts/bootstrap.sh`가 HEAD에서 깨져 있었습니다. 02가 06보다 먼저 실행되어 `is_active` 컬럼 오류가 나고, `audit_core.run_rules` 호출 없이 13을 실행하면 증빙 커버리지가 0이 됩니다.
- 수정: 순서를 01, 06, 02, 03, 04, 05, 07–12 → `audit_core.run_rules('ENG-2026-001')` → 13–26 → 27로 변경.
- 기존 SQL 01–26 파일은 수정하지 않았습니다.

## 2. 검증 결과
| 항목 | 결과 |
|---|---|
| 신규 DB에서 `bootstrap.sh` (01–27) | 성공(rc=0), 연속 2회 실행도 성공(오류 0) |
| audit_core 기준값 회귀 | `fact_risk_finding` 40건, CRITICAL 5 / HIGH 11 / MEDIUM 22 / LOW 2 — 변동 없음 |
| `pytest -q` | **74 passed** (기존 23 + 신규 51), 0 failed |
| `python3 -m compileall -q scripts n8n tests` | 통과 |
| `bash -n scripts/bootstrap.sh` | 통과 |
| `n8n/*.json` JSON 검증 | 통과 |
| `git diff --check` | 통과 |
| `portfolio_demo.py --load-db` | DB 7건 == Python 미러 7건(`PARITY OK`), 재실행도 동일 |
| `run_checks` 재실행 | inserted 0 / updated 7, 후보·검토 항목 수 불변, 검토자 상태 보존(테스트로 확인) |

## 3. 데모 결과 (합성 데이터)
거래 10 · 증빙 10(중복 해시 1) · 수신 파일 3(1건 격리). 후보 **7건**: CRITICAL 4 / HIGH 2 / MEDIUM 1, 노출 금액 합계 5,270,000원, Critical+High 6건. 룰: DQ_00, DQ_01, DQ_03, EV_01, EV_02, EV_03, AN_02 각 1건.
이 수치는 픽스처가 룰에 맞춰 설계된 결과이며 탐지 성능이 아닙니다.

## 4. n8n 실행 확인 (CLI, 샌드박스 DB)
| 워크플로우 | 확인 |
|---|---|
| WF-A | 정상 manifest 실행 성공, 같은 입력 재실행 시 행 1개 유지(멱등). 환경변수 없음·잘못된 sha256·빈 배열은 각각 오류로 중단(fail-closed) |
| WF-B | 실행 성공, engagement 환경변수 없으면 중단. **버그 발견·수정**: `run_checks`가 룰마다 1행(10행)을 반환해 뒤 노드가 10회 실행되어 MOCKED 이력이 60행 생김 → `executeOnce` 적용 후 1회 실행 1행 |
| WF-C | 실행 성공(읽기 전용 쿼리만) |
세 워크플로우 모두 `active=false`, 외부 HTTP·Slack·메일 노드 없음, credential 값 미포함(참조 `PG_CRED`만).

## 5. 하지 않은 것 / 확인하지 못한 것
- Power BI `.pbix` 미제작. `powerbi_preview.png`는 matplotlib 모의 이미지이며 샌드박스에 한글 폰트가 없어 **영문 라벨**로 렌더링됨(한글 폰트가 있는 PC에서는 한글로 나옴).
- Power BI 클라우드 갱신: 미연결(`MOCKED`만 기록). Slack·메일 미연결. WF-5는 건드리지 않음.
- n8n 템플릿 14035, 14273, 14274, 7336은 존재·제목만 검색으로 확인했고 임포트·실행하지 않았으며, 8789는 내용을 확인하지 못했습니다(`docs/PORTFOLIO_REFRAME_V3.md` 표 참조).
- Docker 환경(`docker compose`)·n8n 컨테이너에서의 실행은 하지 않았습니다. `docker-compose.yml`에는 `N8N_BLOCK_ENV_ACCESS_IN_NODE=false`가 없어, 컨테이너 n8n에서 WF-A/B/C를 돌리려면 이 설정 추가가 필요합니다(샌드박스 테스트는 환경변수로 직접 지정).
- git 커밋·푸시는 하지 않았습니다(이 환경에서 GitHub 푸시 자격증명이 유효하지 않음).
