#!/usr/bin/env python3
"""audit_core용 n8n 워크플로우 2종을 생성한다.

- wf1_regwatch.json     : DART 공시 수집 → 정정/주요사항 플래그 → Postgres upsert → Slack
- wf3_rules_engine.json : run_rules() 실행 → 요약 조회 → CRITICAL/HIGH 알림

실행: python3 n8n/build_audit_workflows.py
"""
import json
from pathlib import Path

HERE = Path(__file__).parent

PG = {"postgres": {"id": "PG_CRED", "name": "Postgres audit_db"}}
SLACK = {"slackApi": {"id": "SLACK_CRED", "name": "Slack"}}
SETTINGS = {"executionOrder": "v1", "timezone": "Asia/Seoul", "saveManualExecutions": True}


def node(nid, name, ntype, pos, params, **extra):
    n = {
        "id": nid,
        "name": name,
        "type": ntype,
        "typeVersion": extra.pop("typeVersion", 1),
        "position": pos,
        "parameters": params,
    }
    n.update(extra)
    return n


def chain(*names):
    return {
        a: {"main": [[{"node": b, "type": "main", "index": 0}]]}
        for a, b in zip(names, names[1:])
    }


# ---------------------------------------------------------------- WF-1
wf1_nodes = [
    node("wf1-1", "Schedule: 매일 07:00", "n8n-nodes-base.scheduleTrigger",
         [0, 0], {"rule": {"interval": [{"field": "days", "daysInterval": 1,
                                         "triggerAtHour": 7, "triggerAtMinute": 0}]}},
         typeVersion=1.2),
    node("wf1-2", "Code: 감시 대상", "n8n-nodes-base.code", [220, 0],
         {"jsCode": "// 실제 운영에서는 AEGIS_WATCH_TARGETS_JSON을 프로젝트 마스터에서 주입한다.\n"
                    "const raw = $env.AEGIS_WATCH_TARGETS_JSON;\n"
                    "if (!raw || !$env.DART_API_KEY) {\n"
                    "  throw new Error('DART_API_KEY and AEGIS_WATCH_TARGETS_JSON are required; refusing synthetic fallback');\n"
                    "}\n"
                    "let targets;\n"
                    "try { targets = JSON.parse(raw); } catch (e) { throw new Error('AEGIS_WATCH_TARGETS_JSON must be valid JSON'); }\n"
                    "if (!Array.isArray(targets) || targets.length === 0 || targets.some(t => !t.corp_code || !t.engagement_id)) {\n"
                    "  throw new Error('each DART watch target needs corp_code and engagement_id');\n"
                    "}\n"
                    "return targets.map(t => ({ json: t }));"},
         typeVersion=2),
    node("wf1-3", "HTTP: DART 공시목록", "n8n-nodes-base.httpRequest", [440, 0],
         {"url": "https://opendart.fss.or.kr/api/list.json",
          "sendQuery": True,
          "queryParameters": {"parameters": [
              {"name": "crtfc_key", "value": "={{ $env.DART_API_KEY }}"},
              {"name": "corp_code", "value": "={{ $json.corp_code }}"},
              {"name": "bgn_de", "value": "={{ $now.minus({ days: 7 }).toFormat('yyyyMMdd') }}"},
              {"name": "end_de", "value": "={{ $now.toFormat('yyyyMMdd') }}"},
              {"name": "page_count", "value": "100"},
          ]},
          "options": {"timeout": 30000}},
         typeVersion=4.2, retryOnFail=True, maxTries=3, waitBetweenTries=2000,
         onError="continueRegularOutput"),
    node("wf1-4", "Code: 정정·주요사항 플래그", "n8n-nodes-base.code", [660, 0],
         {"jsCode": "const body = $json || {};\n"
                    "if (body.status && body.status !== '000') {\n"
                    "  return [{ json: { status: body.status, message: body.message || 'DART error', rows: [] } }];\n"
                    "}\n"
                    "const rows = (body.list || []).map(r => ({\n"
                    "  rcept_no: r.rcept_no,\n"
                    "  dart_corp_code: r.corp_code,\n"
                    "  report_nm: r.report_nm,\n"
                    "  flr_nm: r.flr_nm,\n"
                    "  rcept_dt: r.rcept_dt,\n"
                    "  is_correction: /정정/.test(r.report_nm),\n"
                    "  is_major: /주요사항|최대주주|전환사채|유상증자/.test(r.report_nm),\n"
                    "  raw: r,\n"
                    "}));\n"
                    "return [{ json: { status: body.status || '000', count: rows.length, rows } }];"},
         typeVersion=2),
    node("wf1-5", "Postgres: 공시 이벤트 Upsert", "n8n-nodes-base.postgres", [880, 0],
         {"operation": "executeQuery",
          "query": "INSERT INTO audit_core.stg_disclosure_event\n"
                   "  (rcept_no, dart_corp_code, report_nm, flr_nm, rcept_dt, is_correction, is_major, raw)\n"
                   "SELECT * FROM jsonb_to_recordset($1::jsonb) AS x(\n"
                   "  rcept_no CHAR(14), dart_corp_code CHAR(8), report_nm TEXT, flr_nm TEXT,\n"
                   "  rcept_dt DATE, is_correction BOOLEAN, is_major BOOLEAN, raw JSONB)\n"
                   "ON CONFLICT (rcept_no) DO UPDATE SET\n"
                   "  report_nm = EXCLUDED.report_nm, is_correction = EXCLUDED.is_correction,\n"
                   "  is_major = EXCLUDED.is_major, raw = EXCLUDED.raw, loaded_at = NOW();",
          "options": {"queryReplacement": "={{ JSON.stringify($json.rows) }}"}},
         typeVersion=2.5, credentials=PG),
    node("wf1-6", "IF: 정정·주요사항 존재", "n8n-nodes-base.if", [1100, 0],
         {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
                         "conditions": [{"id": "c1", "leftValue": "={{ $json.rows.filter(r => r.is_correction || r.is_major).length }}",
                                         "rightValue": 0, "operator": {"type": "number", "operation": "gt"}}],
                         "combinator": "and"}},
         typeVersion=2.2),
    node("wf1-7", "Slack: 공시 알림", "n8n-nodes-base.slack", [1320, -80],
         {"resource": "message", "operation": "post",
          "select": "channel", "channelId": {"__rl": True, "value": "#audit-alerts", "mode": "name"},
          "text": "=[Aegis] 정정·주요사항 공시 {{ $json.rows.filter(r => r.is_correction || r.is_major).length }}건\n"
                  "{{ $json.rows.filter(r => r.is_correction || r.is_major).slice(0,5).map(r => '• ' + r.report_nm + ' (' + r.rcept_dt + ')').join('\\n') }}"},
         typeVersion=2.3, credentials=SLACK, onError="continueRegularOutput"),
    node("wf1-8", "NoOp: 종료", "n8n-nodes-base.noOp", [1320, 80], {}, typeVersion=1),
]

wf1 = {
    "id": "aegis-wf1-regwatch",
    "name": "[Aegis] WF-1 규제·공시 수집 → 정정·주요사항 조기경보",
    "active": False,
    "settings": SETTINGS,
    "tags": [{"name": "audit_core"}],
    "nodes": wf1_nodes,
    "connections": {
        **chain("Schedule: 매일 07:00", "Code: 감시 대상", "HTTP: DART 공시목록",
                "Code: 정정·주요사항 플래그", "Postgres: 공시 이벤트 Upsert", "IF: 정정·주요사항 존재"),
        "IF: 정정·주요사항 존재": {"main": [
            [{"node": "Slack: 공시 알림", "type": "main", "index": 0}],
            [{"node": "NoOp: 종료", "type": "main", "index": 0}],
        ]},
    },
}

# ---------------------------------------------------------------- WF-2
wf2_nodes = [
    node("wf2-1", "Schedule: PBC 06:30", "n8n-nodes-base.scheduleTrigger", [0, 0],
         {"rule": {"interval": [{"field": "days", "daysInterval": 1,
                                  "triggerAtHour": 6, "triggerAtMinute": 30}]}}, typeVersion=1.2),
    node("wf2-2", "Code: PBC intake preflight", "n8n-nodes-base.code", [220, 0],
         {"jsCode": "const raw = $env.AEGIS_PBC_INPUTS_JSON;\n"
                    "if (!raw) throw new Error('AEGIS_PBC_INPUTS_JSON is required; refusing untracked PBC input');\n"
                    "let inputs;\ntry { inputs = JSON.parse(raw); } catch (e) { throw new Error('AEGIS_PBC_INPUTS_JSON must be valid JSON'); }\n"
                    "if (!Array.isArray(inputs) || inputs.length === 0 || inputs.some(x => !x.file_name || !x.sha256_hash || x.row_count === undefined)) {\n"
                    "  throw new Error('each PBC input needs file_name, sha256_hash, and row_count');\n}\n"
                    "return inputs.map(x => ({ json: x }));"}, typeVersion=2),
    node("wf2-3", "Postgres: PBC 적재 이력", "n8n-nodes-base.postgres", [440, 0],
         {"operation": "executeQuery",
          "query": "INSERT INTO audit_core.stg_ingestion_log\n"
                   "  (engagement_id, file_name, sha256_hash, row_count, debit_credit_diff, tieout_status, ingested_by)\n"
                   "VALUES ($1, $2, $3, $4, $5, CASE WHEN ABS($5) <= 1 THEN 'PASSED' ELSE 'FAILED' END, 'n8n-wf2')\n"
                   "ON CONFLICT (engagement_id, sha256_hash) DO UPDATE SET\n"
                   "  row_count = EXCLUDED.row_count, debit_credit_diff = EXCLUDED.debit_credit_diff,\n"
                   "  tieout_status = EXCLUDED.tieout_status, ingested_at = NOW();",
          "options": {"queryReplacement": "={{ [$json.engagement_id, $json.file_name, $json.sha256_hash, $json.row_count, $json.debit_credit_diff || 0] }}"}},
         typeVersion=2.5, credentials=PG),
    node("wf2-4", "Postgres: PBC 예외 조회", "n8n-nodes-base.postgres", [660, 0],
         {"operation": "executeQuery",
          "query": "SELECT engagement_id, file_name, row_count, debit_credit_diff, tieout_status\n"
                   "FROM audit_core.stg_ingestion_log\n"
                   "WHERE engagement_id = $1 AND tieout_status <> 'PASSED'\n"
                   "ORDER BY ingested_at DESC;",
          "options": {"queryReplacement": "={{ $json.engagement_id || 'ENG-2026-001' }}"}},
         typeVersion=2.5, credentials=PG),
    node("wf2-5", "IF: PBC 예외 존재", "n8n-nodes-base.if", [880, 0],
         {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
                         "conditions": [{"id": "c1", "leftValue": "={{ $input.all().length }}",
                                         "rightValue": 0, "operator": {"type": "number", "operation": "gt"}}],
                         "combinator": "and"}}, typeVersion=2.2),
    node("wf2-6", "Slack: PBC 예외 알림", "n8n-nodes-base.slack", [1100, -80],
         {"resource": "message", "operation": "post", "select": "channel",
          "channelId": {"__rl": True, "value": "#audit-alerts", "mode": "name"},
          "text": "=[Aegis] PBC tie-out 예외 {{ $input.all().length }}건 — 원장·계약·시산표 대조 필요"},
         typeVersion=2.3, credentials=SLACK),
    node("wf2-7", "NoOp: PBC 종료", "n8n-nodes-base.noOp", [1100, 80], {}, typeVersion=1),
]
wf2 = {
    "id": "aegis-wf2-pbc-tieout", "name": "[Aegis] WF-2 PBC intake → GL/AR/계약 Tie-out",
    "active": False, "settings": SETTINGS, "tags": [{"name": "audit_core"}], "nodes": wf2_nodes,
    "connections": {**chain("Schedule: PBC 06:30", "Code: PBC intake preflight", "Postgres: PBC 적재 이력",
                              "Postgres: PBC 예외 조회", "IF: PBC 예외 존재"),
                     "IF: PBC 예외 존재": {"main": [[{"node": "Slack: PBC 예외 알림", "type": "main", "index": 0}],
                                                   [{"node": "NoOp: PBC 종료", "type": "main", "index": 0}]]}},
}

# ---------------------------------------------------------------- WF-3
wf3_nodes = [
    node("wf3-0", "Execute Workflow Trigger", "n8n-nodes-base.executeWorkflowTrigger", [-240, 0],
         {"workflowInputs": {"values": [{"name": "engagement_id", "type": "string"}]}},
         typeVersion=1.1),
    node("wf3-1", "Schedule: 매일 08:00", "n8n-nodes-base.scheduleTrigger", [0, 0],
         {"rule": {"interval": [{"field": "days", "daysInterval": 1,
                                 "triggerAtHour": 8, "triggerAtMinute": 0}]}},
         typeVersion=1.2),
    node("wf3-2", "Postgres: 룰 14종 실행", "n8n-nodes-base.postgres", [240, 0],
         {"operation": "executeQuery",
          "query": "SELECT * FROM audit_core.run_rules($1);",
          "options": {"queryReplacement": "ENG-2026-001"}},
         typeVersion=2.5, credentials=PG),
    node("wf3-3", "Postgres: 룰별 요약", "n8n-nodes-base.postgres", [480, 0],
         {"operation": "executeQuery",
          "query": "SELECT rule_id, rule_name, severity, finding_count, item_count, amount_sum\n"
                   "FROM audit_core.vw_rule_run_summary\n"
                   "ORDER BY CASE severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2\n"
                   "                       WHEN 'MEDIUM' THEN 3 ELSE 4 END, rule_id;",
          "options": {}},
         typeVersion=2.5, credentials=PG, executeOnce=True),
    node("wf3-4", "Code: 알림 메시지", "n8n-nodes-base.code", [720, 0],
         {"jsCode": "const rows = $input.all().map(i => i.json);\n"
                    "const critical = rows.filter(r => r.severity === 'CRITICAL');\n"
                    "const high = rows.filter(r => r.severity === 'HIGH');\n"
                    "const top = [...critical, ...high].slice(0, 5)\n"
                    "  .map(r => `• [${r.severity}] ${r.rule_id} ${r.rule_name} — ${r.finding_count}건`).join('\\n');\n"
                    "return [{ json: {\n"
                    "  total_rules: rows.length,\n"
                    "  critical: critical.length,\n"
                    "  high: high.length,\n"
                    "  message: `[Aegis] 룰 실행 완료: ${rows.length}종\\nCRITICAL ${critical.length} / HIGH ${high.length}\\n${top}`,\n"
                    "} }];"},
         typeVersion=2),
    node("wf3-5", "IF: CRITICAL 존재", "n8n-nodes-base.if", [960, 0],
         {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
                         "conditions": [{"id": "c1", "leftValue": "={{ $json.critical }}",
                                         "rightValue": 0, "operator": {"type": "number", "operation": "gt"}}],
                         "combinator": "and"}},
         typeVersion=2.2),
    node("wf3-6", "Slack: CRITICAL 알림", "n8n-nodes-base.slack", [1180, -80],
         {"resource": "message", "operation": "post",
          "select": "channel", "channelId": {"__rl": True, "value": "#audit-alerts", "mode": "name"},
          "text": "={{ $json.message }}"},
         typeVersion=2.3, credentials=SLACK, onError="continueRegularOutput"),
    node("wf3-7", "NoOp: 종료", "n8n-nodes-base.noOp", [1180, 80], {}, typeVersion=1),
]

wf3 = {
    "id": "aegis-wf3-rules",
    "name": "[Aegis] WF-3 룰 엔진 실행 → CRITICAL/HIGH 알림",
    "active": False,
    "settings": SETTINGS,
    "tags": [{"name": "audit_core"}],
    "nodes": wf3_nodes,
    "connections": {
        "Execute Workflow Trigger": {"main": [[{"node": "Postgres: 룰 14종 실행", "type": "main", "index": 0}]]},
        **chain("Schedule: 매일 08:00", "Postgres: 룰 14종 실행", "Postgres: 룰별 요약",
                "Code: 알림 메시지", "IF: CRITICAL 존재"),
        "IF: CRITICAL 존재": {"main": [
            [{"node": "Slack: CRITICAL 알림", "type": "main", "index": 0}],
            [{"node": "NoOp: 종료", "type": "main", "index": 0}],
        ]},
    },
}

# ---------------------------------------------------------------- WF-4
wf4_nodes = [
    node("wf4-1", "Schedule: AI QA 09:00", "n8n-nodes-base.scheduleTrigger", [0, 0],
         {"rule": {"interval": [{"field": "days", "daysInterval": 1,
                                  "triggerAtHour": 9, "triggerAtMinute": 0}]}}, typeVersion=1.2),
    node("wf4-2", "Postgres: 골드셋 QA 실행", "n8n-nodes-base.postgres", [240, 0],
         {"operation": "executeQuery", "query": "SELECT * FROM audit_core.run_ai_qa_goldset($1);",
          "options": {"queryReplacement": "ENG-2026-001"}}, typeVersion=2.5, credentials=PG),
    node("wf4-3", "Postgres: AI QA 요약", "n8n-nodes-base.postgres", [480, 0],
         {"operation": "executeQuery",
          "query": "SELECT * FROM audit_core.vw_ai_qa_goldset_summary WHERE engagement_id = $1;",
          "options": {"queryReplacement": "ENG-2026-001"}}, typeVersion=2.5, credentials=PG),
    node("wf4-4", "Code: 조서 export manifest", "n8n-nodes-base.code", [720, 0],
         {"jsCode": "const summary = $json;\nreturn [{ json: {\n"
                    "  export_type: 'AI_QA_WORKPAPER',\n  engagement_id: summary.engagement_id,\n"
                    "  generated_at: new Date().toISOString(),\n  qa_summary: summary,\n"
                    "  human_review_required: Number(summary.fail_count || 0) > 0 || Number(summary.review_count || 0) > 0,\n"
                    "} }];"}, typeVersion=2),
    node("wf4-5", "IF: QA 예외 존재", "n8n-nodes-base.if", [960, 0],
         {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
                         "conditions": [{"id": "c1", "leftValue": "={{ $json.human_review_required }}",
                                         "rightValue": True, "operator": {"type": "boolean", "operation": "isTrue"}}],
                         "combinator": "and"}}, typeVersion=2.2),
    node("wf4-6", "Slack: AI QA 검토 요청", "n8n-nodes-base.slack", [1180, -80],
         {"resource": "message", "operation": "post", "select": "channel",
          "channelId": {"__rl": True, "value": "#audit-alerts", "mode": "name"},
          "text": "=[Aegis] AI QA 예외 — 3-Pane 검토 및 조서 보류 필요"}, typeVersion=2.3, credentials=SLACK),
    node("wf4-7", "NoOp: 조서 manifest 보관", "n8n-nodes-base.noOp", [1180, 80], {}, typeVersion=1),
]
wf4 = {
    "id": "aegis-wf4-aiqa-workpaper", "name": "[Aegis] WF-4 AI QA → 예외 큐 → 조서 manifest",
    "active": False, "settings": SETTINGS, "tags": [{"name": "audit_core"}], "nodes": wf4_nodes,
    "connections": {**chain("Schedule: AI QA 09:00", "Postgres: 골드셋 QA 실행", "Postgres: AI QA 요약",
                              "Code: 조서 export manifest", "IF: QA 예외 존재"),
                     "IF: QA 예외 존재": {"main": [[{"node": "Slack: AI QA 검토 요청", "type": "main", "index": 0}],
                                                   [{"node": "NoOp: 조서 manifest 보관", "type": "main", "index": 0}]]}},
}

for name, wf in [("wf1_regwatch.json", wf1), ("wf2_pbc_tieout.json", wf2),
                 ("wf3_rules_engine.json", wf3), ("wf4_aiqa_workpaper.json", wf4)]:
    path = HERE / name
    path.write_text(json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", path, len(wf["nodes"]), "nodes")
