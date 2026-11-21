# Aegis-Guard 구현 TODO

## Phase 0 — 재현성과 기준선

- [ ] `scripts/bootstrap.sh`가 `sql/01`부터 `sql/15`까지 순서대로 실행된다.
- [ ] audit_core 계약 테스트가 룰 14종, 파인딩 40건, 심각도 분포, 증거 커버리지 67.5%를 확인한다.
- [ ] 테스트에서 멱등 재실행(`new=0`)과 `14_verify` 10/10 PASS를 확인한다.

## Phase 1 — 통합 콘솔 3-Pane

- [ ] `uvicorn console.app:app` 한 줄로 실행된다.
- [ ] 워크리스트 화면에서 40건을 조회하고 severity/domain/disposition 필터를 적용한다.
- [ ] 파인딩 상세 3-Pane에서 원문·AI QA·룰 판정·증거 해시를 함께 본다.
- [ ] disposition 변경이 DB에 반영되고 `audit_core.change_log`에 남는다.
- [ ] 증거 커버리지 화면에서 27/40, 67.5%와 미연결 요청 큐를 확인한다.
- [ ] 공시 감시 화면에서 `vw_disclosure_watch`를 조회한다.
- [ ] API smoke test와 화면 라우트 테스트가 통과한다.

## 다음 Phase

- [ ] Phase 2: FX-AR/ECL 시뮬레이터
- [ ] Phase 3: AI QA 골드셋 하네스
- [ ] Phase 4: 조서 자동화
- [ ] Phase 5: 실데이터·50만건 성능 검증
- [ ] Phase 6: 5분 데모·면접 자산 정리
