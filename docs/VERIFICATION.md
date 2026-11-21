

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
