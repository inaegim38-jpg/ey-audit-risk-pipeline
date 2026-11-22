#!/usr/bin/env python3
"""포트폴리오(거래·증빙 정합성·검토 이슈 트래커)용 n8n 워크플로우 3종을 생성한다.

- wfA_portfolio_intake.json        : 입력 manifest 검증(fail-closed) → source_file 멱등 적재
- wfB_portfolio_risk_queue.json    : portfolio.run_checks() → 검토 큐 조회 → Power BI 갱신(MOCKED) 이력
- wfC_portfolio_review_tracker.json: 검토·이슈 현황 요약 조회(읽기 전용)

설계 원칙
- 자격증명 값은 JSON에 넣지 않는다. Postgres는 credential 참조(PG_CRED)만 사용한다.
- 환경변수(PORTFOLIO_INPUT_MANIFEST_JSON, PORTFOLIO_ENGAGEMENT_ID)가 없으면 합성 값으로 대체하지 않고 실패한다.
- 어떤 워크플로우도 review_status를 바꾸거나 외부로 메일·메시지를 보내지 않는다(판단은 사람이 한다).
- Power BI 갱신은 자격증명이 없으므로 MOCKED 상태로만 기록한다.
- 세 워크플로우 모두 active=false.

실행: python3 n8n/build_portfolio_workflows.py   (build_audit_workflows.py를 import하지 않는다: 모듈 로드 시 파일을 쓰기 때문)
"""
import json
from pathlib import Path

HERE = Path(__file__).parent
PG = {"postgres": {"id": "PG_CRED", "name": "Postgres audit_db"}}
SETTINGS = {"executionOrder": "v1", "timezone": "Asia/Seoul", "saveManualExecutions": True}
TAGS = [{"name": "portfolio"}]


def node(nid, name, ntype, pos, params, **extra):
    n = {"id": nid, "name": name, "type": ntype, "typeVersion": extra.pop("typeVersion", 1),
         "position": pos, "parameters": params}
    n.update(extra)
    return n


def chain(*names):
    return {a: {"main": [[{"node": b, "type": "main", "index": 0}]]} for a, b in zip(names, names[1:])}


def pg(nid, name, pos, query, replacement=None, once=False):
    params = {"operation": "executeQuery", "query": query, "options": {}}
    if replacement:
        params["options"] = {"queryReplacement": replacement}
    extra = {"executeOnce": True} if once else {}  # 앞 노드가 여러 행을 내도 한 번만 실행(run_checks는 룰마다 1행 반환)
    return node(nid, name, "n8n-nodes-base.postgres", pos, params, typeVersion=2.5, credentials=PG, **extra)


def workflow(wid, name, nodes):
    return {"id": wid, "name": name, "active": False, "settings": SETTINGS, "tags": TAGS, "nodes": nodes,
            "connections": chain(*[n["name"] for n in nodes])}


def manual(nid):
    return node(nid, "Manual Trigger", "n8n-nodes-base.manualTrigger", [-240, 0], {})


# ------------------------------------------------------------------ WF-A: 접수
MANIFEST_JS = r"""
// 입력 manifest(JSON 배열)를 환경변수에서 읽는다. 없거나 잘못되면 합성 값으로 대체하지 않고 즉시 실패한다.
const raw = $env.PORTFOLIO_INPUT_MANIFEST_JSON;
if (!raw) throw new Error('PORTFOLIO_INPUT_MANIFEST_JSON is not set (fail-closed: no synthetic fallback)');
let arr;
try { arr = JSON.parse(raw); } catch (e) { throw new Error('PORTFOLIO_INPUT_MANIFEST_JSON is not valid JSON: ' + e.message); }
if (!Array.isArray(arr)) throw new Error('manifest must be a JSON array');
if (arr.length === 0) throw new Error('manifest is empty');
const required = ['engagement_id', 'file_name', 'file_type', 'sha256_hash', 'row_count', 'source_path'];
return arr.map((f, i) => {
  for (const k of required) {
    if (f[k] === undefined || f[k] === null || String(f[k]).trim() === '') throw new Error(`manifest[${i}] missing ${k}`);
  }
  if (!/^[0-9a-f]{64}$/.test(f.sha256_hash)) throw new Error(`manifest[${i}] invalid sha256_hash`);
  const n = Number(f.row_count);
  if (!Number.isInteger(n) || n < 0) throw new Error(`manifest[${i}] invalid row_count`);
  return { json: { ...f, row_count: n } };
});
""".strip()

wfA = workflow("portfolioWfA01", "Portfolio A - 자료 접수", [
    manual("pfA-0"),
    node("pfA-1", "Code: manifest 검증(fail-closed)", "n8n-nodes-base.code", [0, 0],
         {"language": "javaScript", "jsCode": MANIFEST_JS}, typeVersion=2),
    pg("pfA-2", "PG: source_file 멱등 적재", [260, 0],
       "INSERT INTO portfolio.source_file (engagement_id, file_name, file_type, sha256_hash, row_count, source_path)\n"
       "VALUES ($1, $2, $3, $4, $5, $6)\n"
       "ON CONFLICT (engagement_id, sha256_hash) DO UPDATE SET row_count = EXCLUDED.row_count\n"
       "RETURNING file_id, file_name, intake_status, (xmax = 0) AS newly_received",
       "={{ [$json.engagement_id, $json.file_name, $json.file_type, $json.sha256_hash, $json.row_count, $json.source_path] }}"),
])

# ------------------------------------------------------------------ WF-B: 리스크 큐
ENGAGEMENT_JS = r"""
const e = $env.PORTFOLIO_ENGAGEMENT_ID;
if (!e || !String(e).trim()) throw new Error('PORTFOLIO_ENGAGEMENT_ID is not set (fail-closed)');
return [{ json: { engagement_id: String(e).trim() } }];
""".strip()

wfB = workflow("portfolioWfB01", "Portfolio B - 리스크 후보 검토 큐", [
    manual("pfB-0"),
    node("pfB-1", "Code: engagement 확인(fail-closed)", "n8n-nodes-base.code", [0, 0],
         {"language": "javaScript", "jsCode": ENGAGEMENT_JS}, typeVersion=2),
    pg("pfB-2", "PG: run_checks 실행(멱등)", [260, 0],
       "SELECT rule_id, inserted_count, updated_count FROM portfolio.run_checks($1)",
       "={{ [$json.engagement_id] }}"),
    pg("pfB-3", "PG: 검토 큐 상위 20건", [520, 0],
       "SELECT review_item_id, rule_id, severity, amount, suggested_priority, review_status, detail\n"
       "FROM portfolio.vw_pbi_dq_risk_queue\n"
       "WHERE engagement_id = $1 AND review_status IN ('UNREVIEWED', 'IN_REVIEW')\n"
       "ORDER BY suggested_priority DESC, finding_id LIMIT 20",
       "={{ [$('Code: engagement 확인(fail-closed)').first().json.engagement_id] }}", once=True),
    pg("pfB-4", "PG: Power BI 갱신 MOCKED 이력", [780, 0],
       "INSERT INTO portfolio.refresh_log (dataset_name, status, completed_at, error_message)\n"
       "VALUES ($1, 'MOCKED', now(), 'MOCKED: Power BI credential not configured; no refresh was requested')\n"
       "RETURNING refresh_id, status",
       "={{ [$env.POWERBI_DATASET_NAME || 'portfolio_dataset (unset)'] }}", once=True),
])

# ------------------------------------------------------------------ WF-C: 검토 트래커(읽기 전용)
wfC = workflow("portfolioWfC01", "Portfolio C - 검토·이슈 트래커 현황", [
    manual("pfC-0"),
    node("pfC-1", "Code: engagement 확인(fail-closed)", "n8n-nodes-base.code", [0, 0],
         {"language": "javaScript", "jsCode": ENGAGEMENT_JS}, typeVersion=2),
    pg("pfC-2", "PG: KPI 요약", [260, 0],
       "SELECT * FROM portfolio.vw_pbi_kpi_summary WHERE engagement_id = $1",
       "={{ [$json.engagement_id] }}"),
    pg("pfC-3", "PG: 미결 이슈(오래된 순)", [520, 0],
       "SELECT issue_id, rule_id, severity, issue_type, owner, due_date, disposition, age_days\n"
       "FROM portfolio.vw_pbi_issue_log\n"
       "WHERE engagement_id = $1 AND is_open_issue\n"
       "ORDER BY age_days DESC, issue_id LIMIT 50",
       "={{ [$('Code: engagement 확인(fail-closed)').first().json.engagement_id] }}"),
])

for fname, wf in (("wfA_portfolio_intake.json", wfA), ("wfB_portfolio_risk_queue.json", wfB),
                  ("wfC_portfolio_review_tracker.json", wfC)):
    (HERE / fname).write_text(json.dumps(wf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", fname, len(wf["nodes"]), "nodes, active =", wf["active"])
