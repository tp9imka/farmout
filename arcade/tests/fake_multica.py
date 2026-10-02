"""A tiny in-process Multica API for tests: canned workspace data, every
request recorded with its headers and JSON body."""

import http.server
import json
import re
import threading
import urllib.parse

WS = "11111111-1111-4111-8111-111111111111"
AG_BUSY = "aaaaaaaa-0000-4000-8000-000000000001"
AG_QUEUED = "aaaaaaaa-0000-4000-8000-000000000002"
AG_IDLE = "aaaaaaaa-0000-4000-8000-000000000003"
AG_OFF = "aaaaaaaa-0000-4000-8000-000000000004"
RT_ON = "bbbbbbbb-0000-4000-8000-000000000001"
RT_OFF = "bbbbbbbb-0000-4000-8000-000000000002"
T_RUN = "cccccccc-0000-4000-8000-000000000001"
T_Q1 = "cccccccc-0000-4000-8000-000000000002"
T_DONE = "cccccccc-0000-4000-8000-000000000003"
I_RUN = "dddddddd-0000-4000-8000-000000000001"
I_Q1 = "dddddddd-0000-4000-8000-000000000002"
I_DONE = "dddddddd-0000-4000-8000-000000000003"
AP = "eeeeeeee-0000-4000-8000-000000000001"
TOKEN = "mul_secret_test_token_value"


def default_data():
    return {
        "workspace": {"id": WS, "name": "Acme", "slug": "acme"},
        "agents": [
            {"id": AG_BUSY, "name": "Builder", "runtime_id": RT_ON, "runtime_bound": True, "model": "opus", "max_concurrent_tasks": 1},
            {"id": AG_QUEUED, "name": "Reviewer", "runtime_id": RT_ON, "runtime_bound": True, "model": ""},
            {"id": AG_IDLE, "name": "Janitor", "runtime_id": RT_ON, "runtime_bound": True},
            {"id": AG_OFF, "name": "Nightly", "runtime_id": RT_OFF, "runtime_bound": True},
            {"id": "aaaaaaaa-0000-4000-8000-000000000009", "name": "Old", "archived_at": "2026-01-01T00:00:00Z"},
        ],
        "runtimes": [
            {"id": RT_ON, "name": "mbp", "custom_name": None, "provider": "claude", "status": "online"},
            {"id": RT_OFF, "name": "box", "provider": "codex", "status": "offline"},
        ],
        "tasks": [
            {"id": T_RUN, "agent_id": AG_BUSY, "issue_id": I_RUN, "status": "running",
             "started_at": "2026-10-02T09:00:00.123456789Z", "created_at": "2026-10-02T08:59:00Z",
             "branch_name": "agent/builder/fix", "attempt": 1, "max_attempts": 2},
            {"id": T_Q1, "agent_id": AG_QUEUED, "issue_id": I_Q1, "status": "queued", "created_at": "2026-10-02T09:01:00Z"},
            {"id": T_DONE, "agent_id": AG_IDLE, "issue_id": I_DONE, "status": "failed",
             "completed_at": "2026-10-02T08:00:00Z", "error": "tests failed"},
        ],
        "issues": {
            I_RUN: {"id": I_RUN, "identifier": "ACM-7", "title": "Fix the flaky export", "status": "in_progress"},
            I_Q1: {"id": I_Q1, "identifier": "ACM-8", "title": "Review payments PR", "status": "todo"},
            I_DONE: {"id": I_DONE, "identifier": "ACM-3", "title": "Prune branches", "status": "todo"},
        },
        "messages": {
            T_RUN: [
                {"seq": 1, "type": "text", "content": "Looking at the export job", "created_at": "2026-10-02T09:00:01Z"},
                {"seq": 2, "type": "tool_use", "tool": "Bash", "input": {"command": "npm test"}, "created_at": "2026-10-02T09:00:02Z"},
                {"seq": 3, "type": "tool_result", "tool": "Bash", "output": "ok"},
            ],
        },
        "autopilots": [
            {"id": AP, "title": "Check my PRs", "status": "active", "assignee_type": "agent", "assignee_id": AG_IDLE,
             "execution_mode": "create_issue", "trigger_kinds": ["schedule"], "next_run_at": "2026-10-03T09:00:00Z",
             "last_run_at": None, "description": "Look at open PRs"},
        ],
        "trigger_status": "issue_created",
    }


class FakeMultica(object):
    def __init__(self):
        self.data = default_data()
        self.requests = []
        self.fail = {}  # route regex -> HTTP status
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, code, payload):
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _handle(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else None
                parsed = urllib.parse.urlsplit(self.path)
                fake.requests.append({"method": method, "path": parsed.path, "query": parsed.query,
                                      "headers": dict(self.headers), "body": body})
                if self.headers.get("Authorization") != "Bearer " + TOKEN or self.headers.get("X-Workspace-ID") != WS:
                    self._reply(401, {"error": "unauthorized"})
                    return
                for pattern, code in fake.fail.items():
                    if re.search(pattern, parsed.path):
                        self._reply(code, {"error": "boom"})
                        return
                code, payload = fake.route(method, parsed.path, urllib.parse.parse_qs(parsed.query), body)
                self._reply(code, payload)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:{}".format(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever)
        self.thread.daemon = True
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def route(self, method, path, query, body):
        d = self.data
        if method == "GET":
            if path == "/api/workspaces/" + WS:
                return 200, d["workspace"]
            if path == "/api/agents":
                return 200, d["agents"]
            if path == "/api/runtimes":
                return 200, d["runtimes"]
            if path == "/api/agent-task-snapshot":
                return 200, d["tasks"]
            if path == "/api/autopilots":
                return 200, {"autopilots": d["autopilots"], "total": len(d["autopilots"])}
            m = re.fullmatch(r"/api/issues/([^/]+)", path)
            if m and m.group(1) in d["issues"]:
                return 200, d["issues"][m.group(1)]
            m = re.fullmatch(r"/api/tasks/([^/]+)/messages", path)
            if m:
                msgs = d["messages"].get(m.group(1), [])
                since = int(query["since"][0]) if "since" in query else None
                return 200, [x for x in msgs if since is None or x["seq"] > since]
        if method == "POST":
            if re.fullmatch(r"/api/tasks/[^/]+/cancel", path):
                return 200, {"status": "cancelled"}
            if re.fullmatch(r"/api/autopilots/[^/]+/trigger", path):
                st = d["trigger_status"]
                return 200, {"id": "run-1", "status": st,
                             "failure_reason": None if st in ("issue_created", "running") else "runtime offline"}
            if path == "/api/issues":
                return 201, {"id": "ffffffff-0000-4000-8000-000000000001", "identifier": "ACM-9", "title": body.get("title")}
        return 404, {"error": "not found"}
