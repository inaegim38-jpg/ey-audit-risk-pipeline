# Aegis-Guard 구현 TODO

## Phase 0 — 재현성과 기준선

- [x] `scripts/bootstrap.sh`가 `sql/01`부터 `sql/15`까지 순서대로 실행된다.
- [x] audit_core 계약 테스트가 룰 14종, 파인딩 40건, 심각도 분포, 증거 커버리지 67.5%를 확인한다.
- [x] 테스트에서 멱등 재실행(`new=0`)과 `14_verify` 10/10 PASS를 확인한다.

## Phase 1 — 통합 콘솔 3-Pane

- [x] `uvicorn console.app:app` 한 줄로 실행된다.
- [x] 워크리스트 화면에서 40건을 조회하고 severity/domain/disposition 필터를 적용한다.
- [x] 파인딩 상세 3-Pane에서 원문·AI QA·룰 판정·증거 해시를 함께 본다.
- [x] disposition 변경이 DB에 반영되고 `audit_core.change_log`에 남는다.
- [x] 증거 커버리지 화면에서 27/40, 67.5%와 미연결 요청 큐를 확인한다.
- [x] 공시 감시 화면에서 `vw_disclosure_watch`를 조회한다.
- [x] API smoke test와 화면 라우트 테스트가 통과한다.

## 다음 Phase

- [x] Phase 2: FX-AR/ECL 시뮬레이터
- [x] Phase 3: AI QA 골드셋 하네스

## Architecture v2 구현 트랙

- [x] 원천·자동화·DB·검증·HITL·산출물 계층을 Architecture v2 문서로 고정한다.
- [x] WF-1 DART 수집·조기경보의 runtime credential 주입과 fail-closed를 구현한다.
- [ ] WF-2 PBC GL/AR/계약 intake·tie-out·예외 큐를 구현한다.
- [ ] WF-3 JET·FX-AR·ECL 통합 workflow와 scenario summary를 구현한다.
- [ ] WF-4 Canvas/Solar AI QA·3-Pane·조서 export workflow를 구현한다.
- [ ] DART XBRL, ECOS, 공정위/조달, PBC 원천 adapter와 source registry를 연결한다.
- [ ] Power BI Quality/JET/ECL/AI 4페이지 mart와 Word·Excel 조서 export를 연결한다.

## 다음 Phase

- [ ] Phase 4: WF-2/4 조서 자동화와 승인 버전 export
- [ ] Phase 5: 실데이터·50만건 성능 검증
- [ ] Phase 6: Architecture v2 기준 5분 데모·RFP PoC 킷·면접 자산 정리
