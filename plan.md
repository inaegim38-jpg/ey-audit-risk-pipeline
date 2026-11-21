# Aegis-Guard 콘솔 구현 계획

## 목표

기존 `audit_core` PostgreSQL 판정 결과를 사람이 검토할 수 있는 경량 웹 콘솔로 노출한다. JD ② 문서 검토·워킹페이퍼와 JD ④ AI Agent 테스트를 우선 화면으로 증명하고, JD ① 데이터 정합성과 JD ③ 조사·분석은 기존 SQL·뷰 결과를 화면에서 연결한다.

## 구현 결정

- 백엔드: FastAPI + Jinja2
- DB: 기존 PostgreSQL `audit_db`, `psycopg2-binary`
- 프론트엔드: 서버 렌더링 HTML + 최소 CSS/vanilla JS. 별도 SPA 빌드 도구는 도입하지 않는다.
- 4개 화면: 워크리스트, 3-Pane 파인딩 상세, 증거 커버리지, 공시 감시
- 상태 변경: API에서 트랜잭션으로 `fact_risk_finding`과 `change_log`를 함께 갱신
- 데모 인증: `CONSOLE_USER`/`CONSOLE_PASSWORD` 환경변수 기반 Basic Auth는 선택적으로 활성화
- API: `/api/findings`, `/api/findings/{finding_id}`, `/api/findings/{finding_id}/disposition`, `/api/evidence/{evidence_id}`, `/api/coverage`, `/api/disclosures`

## 디자인

**Audit Control Room** — 법무·감사 조서의 차분한 네이비를 바탕으로, 위험 신호만 앰버·레드로 표시한다.

- 원칙: 근거 우선, 정보 밀도, 판정과 사실의 분리, 모든 화면에서 현재 engagement 표시
- 색상: 잉크 네이비(#0b1728), 종이색(#f6f7f9), 앰버(#d97706), 위험 레드(#b42318), 안전 청록(#0f766e)
- 레이아웃: 상단 컨텍스트 바 + 좌측 좁은 내비게이션 + 넓은 데이터 작업면
- 상징 요소: 해시/증거 배지, severity 라벨, 상태 전이 타임라인
- 상호작용: 표에서 파인딩을 고르고, 상세에서 근거를 확인한 뒤 상태를 바꾼다. 자동 확정처럼 보이는 버튼은 만들지 않는다.
- 타이포그래피: 시스템 산세리프, 제목은 굵은 네이비, 본문은 높은 대비의 회색

## 프로젝트 구조

```text
console/
  __init__.py
  app.py              FastAPI 라우트·API
  db.py               PostgreSQL 연결·쿼리
  templates/          Jinja2 화면
  static/             CSS·JS
scripts/
  bootstrap.sh        SQL 01~15 순차 실행(Phase 0)
tests/
  test_audit_core.py  현재 수치·멱등성 계약 테스트
```
