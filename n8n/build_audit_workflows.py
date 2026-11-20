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
         {"jsCode": "// 감시 대상 법인. 실무에서는 프로젝트 마스터에서 조회한다.\n"
                    "return [{ json: { corp_code: '00123456', engagement_id: 'ENG-2026-001' } }];"},
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

for name, wf in [("wf1_regwatch.json", wf1), ("wf3_rules_engine.json", wf3)]:
    path = HERE / name
    path.write_text(json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", path, len(wf["nodes"]), "nodes")
