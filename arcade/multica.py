"""Multica source for the arcade: agents, their runs and autopilots.

Stdlib-only, targets /usr/bin/python3 (3.9). Reads the same profile the
`multica` CLI writes (~/.multica/config.json, or profiles/<name>/), so a
`multica login` is all the setup there is. The token stays on this side:
nothing in snapshot() or in an error string ever carries it.

A self-hosted server can be slow or down, so the HTTP work runs on its own
thread. snapshot() returns the last result at once and never blocks the
board's StateBuilder.
"""

import datetime
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

POLL_S = 3.0
REQUEST_TIMEOUT_S = 4.0
REPLAY_KEEP = 40
ISSUE_TTL_S = 60.0
# Issue lookups one poll may start; the rest wait for the next poll.
ISSUE_FETCHES_PER_POLL = 20
LINE_MAX = 200
ERROR_MAX = 300

ACTIVE_STATUSES = ("queued", "dispatched", "running", "waiting_local_directory")
RUNNING_STATUSES = ("dispatched", "running", "waiting_local_directory")
# A run counts as started once the trigger answers with one of these.
TRIGGER_STARTED = ("issue_created", "running")

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
PROFILE_RE = re.compile(r"[A-Za-z0-9._-]+")
_INPUT_KEYS = ("command", "file_path", "path", "pattern", "url", "query", "description", "prompt")


class MulticaError(Exception):
    """A request that failed. str() is safe to show: never the token."""


def default_config_dir():
    return os.path.expanduser(os.environ.get("MULTICA_CONFIG_DIR") or "~/.multica")


def read_profile(config_dir, profile=None):
    """The CLI profile's config dict, or {} when there is none."""
    if profile:
        path = os.path.join(config_dir, "profiles", profile, "config.json")
    else:
        path = os.path.join(config_dir, "config.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def resolve_settings(mcfg, config_dir=None, env=None):
    """Connection settings from the arcade's `multica` config section, the
    CLI profile and MULTICA_* env (env wins, as in the CLI).

    Returns {enabled, configured, server_url, app_url, workspace_id, token,
    profile, why}. `why` says, without secrets, what is missing."""
    env = os.environ if env is None else env
    mcfg = mcfg if isinstance(mcfg, dict) else {}
    profile = mcfg.get("profile") or env.get("MULTICA_PROFILE") or None
    prof = read_profile(config_dir or default_config_dir(), profile)

    def pick(env_key, prof_key):
        v = env.get(env_key) or prof.get(prof_key) or ""
        return v.strip() if isinstance(v, str) else ""

    out = {
        "server_url": pick("MULTICA_SERVER_URL", "server_url").rstrip("/"),
        "app_url": pick("MULTICA_APP_URL", "app_url").rstrip("/"),
        "workspace_id": pick("MULTICA_WORKSPACE_ID", "workspace_id"),
        "token": pick("MULTICA_TOKEN", "token"),
        "profile": profile,
    }
    missing = [k for k in ("server_url", "token", "workspace_id") if not out[k]]
    out["configured"] = not missing
    want = mcfg.get("enabled")
    # null = automatic: on whenever the CLI is logged in.
    out["enabled"] = out["configured"] if want is None else bool(want)
    if missing:
        hint = "run `multica login`" if "token" in missing else "run `multica setup self-host`"
        out["why"] = "no {} in the multica profile; {}".format(", ".join(missing), hint)
    elif out["workspace_id"] and not UUID_RE.fullmatch(out["workspace_id"]):
        out["configured"] = False
        out["why"] = "workspace_id in the multica profile is not a UUID"
    else:
        out["why"] = None
    if out["enabled"] and not out["configured"]:
        out["enabled"] = want is True
    return out


def _short(text, limit=LINE_MAX):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def parse_ts(value):
    """Epoch ms from an RFC 3339 timestamp, None if it isn't one."""
    if not isinstance(value, str) or not value:
        return None
    s = value.strip().replace("Z", "+00:00")
    # 3.9's fromisoformat takes at most 6 fractional digits; Go emits up to 9.
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    try:
        dt = datetime.datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp() * 1000)


def message_line(msg):
    """One replay line for a task message, or None for one not worth a line."""
    if not isinstance(msg, dict):
        return None
    kind = msg.get("type")
    if kind == "tool_use":
        tool = msg.get("tool") or "tool"
        inp = msg.get("input") if isinstance(msg.get("input"), dict) else {}
        arg = next((inp[k] for k in _INPUT_KEYS if isinstance(inp.get(k), str) and inp[k].strip()), "")
        return _short("▸ {} {}".format(tool, arg).strip())
    if kind == "text":
        content = (msg.get("content") or "").strip()
        return _short(content) if content else None
    if kind == "error":
        return _short("ERROR " + (msg.get("content") or msg.get("output") or ""))
    return None


class Client(object):
    """Bearer-token JSON client for one Multica workspace."""

    def __init__(self, server_url, token, workspace_id, timeout=REQUEST_TIMEOUT_S):
        self.server_url = server_url
        self._token = token
        self.workspace_id = workspace_id
        self.timeout = timeout

    def request(self, method, path, body=None):
        url = self.server_url + path
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", "Bearer " + self._token)
        req.add_header("X-Workspace-ID", self.workspace_id)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise MulticaError("{} {}: HTTP {}{}".format(method, _route(path), exc.code, _error_detail(exc)))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", None) or exc
            raise MulticaError("{} {}: {}".format(method, _route(path), _short(reason, 120)))
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise MulticaError("{} {}: bad JSON".format(method, _route(path)))

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, body=None):
        return self.request("POST", path, {} if body is None else body)


def _route(path):
    # Ids make error lines noisy and differ every poll: keep the shape only.
    return UUID_RE.sub(":id", path.split("?", 1)[0])


def _error_detail(exc):
    try:
        body = json.loads(exc.read().decode("utf-8"))
    except Exception:
        return ""
    msg = body.get("error") if isinstance(body, dict) else None
    return " · " + _short(msg, 160) if isinstance(msg, str) and msg else ""


def _ids(value):
    return value if isinstance(value, list) else []


class MulticaSource(object):
    """Polls one workspace on its own thread; snapshot() never blocks on it."""

    def __init__(self, settings_fn, poll_s=POLL_S, client_factory=Client, autostart=True):
        self._settings_fn = settings_fn
        self._poll_s = poll_s
        self._client_factory = client_factory
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._closed = False
        self._client = None
        self._client_key = None
        self._workspace = None
        self._issues = {}  # issue id -> (fetched monotonic, {identifier, title, status})
        self._replay = {}  # task id -> {"seq": int, "lines": [...], "tool": str|None}
        self._state = self._empty(None)
        self._thread = None
        if autostart:
            self._thread = threading.Thread(target=self._loop, name="arcade-multica")
            self._thread.daemon = True
            self._thread.start()

    # -- public -----------------------------------------------------------

    def snapshot(self):
        with self._lock:
            return self._state

    def poke(self):
        """Poll again now (after an action), instead of at the next tick."""
        self._wake.set()

    def close(self):
        self._closed = True
        self._wake.set()

    def agent(self, agent_id):
        for a in self.snapshot().get("agents", []):
            if a["id"] == agent_id:
                return a
        return None

    def cancel_task(self, task_id):
        client = self._require_client()
        client.post("/api/tasks/{}/cancel".format(task_id))
        self.poke()
        return {"ok": True}

    def trigger_autopilot(self, autopilot_id):
        client = self._require_client()
        run = client.post("/api/autopilots/{}/trigger".format(autopilot_id)) or {}
        self.poke()
        status = run.get("status") if isinstance(run, dict) else None
        if status in TRIGGER_STARTED:
            return {"ok": True, "status": status}
        reason = run.get("failure_reason") or run.get("reason_code") or status or "no run started"
        return {"ok": False, "status": status, "err": "SKIPPED · " + _short(reason, 160)}

    def queue_task(self, agent_id, title, description):
        """Creates a todo issue assigned to the agent, which queues its run."""
        client = self._require_client()
        body = {"title": title, "status": "todo", "assignee_type": "agent", "assignee_id": agent_id}
        if description:
            body["description"] = description
        issue = client.post("/api/issues", body) or {}
        self.poke()
        return {"ok": True, "issue": {
            "id": issue.get("id"), "identifier": issue.get("identifier"), "title": issue.get("title") or title,
        }}

    # -- polling ------------------------------------------------------------

    def _require_client(self):
        with self._lock:
            client = self._client
        if client is None:
            raise MulticaError("multica is not connected")
        return client

    def _loop(self):
        while not self._closed:
            self.poll_once()
            self._wake.wait(self._poll_s)
            self._wake.clear()

    def poll_once(self):
        try:
            settings = self._settings_fn()
        except Exception as exc:  # a bad config must not kill the thread
            settings = {"enabled": False, "configured": False, "why": "settings: {}".format(type(exc).__name__)}
        if not settings.get("enabled"):
            with self._lock:
                self._client = None
                self._client_key = None
                self._state = self._empty(settings)
            return
        if not settings.get("configured"):
            with self._lock:
                self._client = None
                self._client_key = None
                self._state = dict(self._empty(settings), enabled=True, error=settings.get("why"))
            return
        key = (settings["server_url"], settings["token"], settings["workspace_id"])
        with self._lock:
            if key != self._client_key:
                self._client = self._client_factory(*key)
                self._client_key = key
                self._workspace = None
                self._issues = {}
                self._replay = {}
            client = self._client
        try:
            state = self._build(client, settings)
        except MulticaError as exc:
            prev = self.snapshot()
            state = dict(prev, enabled=True, connected=False, error=str(exc),
                         server=settings["server_url"], app_url=settings["app_url"] or None)
        except Exception as exc:  # anything unexpected stays a status line
            prev = self.snapshot()
            state = dict(prev, enabled=True, connected=False, error="multica: {}".format(type(exc).__name__))
        with self._lock:
            self._state = state

    @staticmethod
    def _empty(settings):
        settings = settings or {}
        return {
            "enabled": False, "connected": False, "error": None, "why": settings.get("why"),
            "server": settings.get("server_url") or None, "app_url": settings.get("app_url") or None,
            "workspace": None, "agents": [], "autopilots": [], "updated": None,
        }

    def _build(self, client, settings):
        now_ms = int(time.time() * 1000)
        if self._workspace is None:
            ws = client.get("/api/workspaces/{}".format(client.workspace_id)) or {}
            self._workspace = {"id": client.workspace_id, "name": ws.get("name"), "slug": ws.get("slug")}
        agents = [a for a in _ids(client.get("/api/agents")) if isinstance(a, dict) and not a.get("archived_at")]
        runtimes = {r.get("id"): r for r in _ids(client.get("/api/runtimes")) if isinstance(r, dict)}
        tasks = [t for t in _ids(client.get("/api/agent-task-snapshot")) if isinstance(t, dict)]
        ap_body = client.get("/api/autopilots") or {}
        autopilots = ap_body.get("autopilots") if isinstance(ap_body, dict) else ap_body

        self._fetch_issues(client, tasks)
        live_ids = set()
        for t in tasks:
            if t.get("status") in RUNNING_STATUSES and t.get("id"):
                live_ids.add(t["id"])
                self._fetch_messages(client, t["id"])
        for gone in [k for k in self._replay if k not in live_ids]:
            del self._replay[gone]

        app_base = self._app_base(settings)
        names = {a.get("id"): a.get("name") for a in agents}
        return {
            "enabled": True, "connected": True, "error": None, "why": None,
            "server": settings["server_url"], "app_url": settings["app_url"] or None,
            "workspace": self._workspace,
            "agents": [self._agent_vm(a, tasks, runtimes, now_ms, app_base) for a in agents],
            "autopilots": [self._autopilot_vm(ap, names) for ap in _ids(autopilots) if isinstance(ap, dict)],
            "updated": now_ms,
        }

    def _app_base(self, settings):
        slug = (self._workspace or {}).get("slug")
        if not settings.get("app_url") or not slug:
            return None
        return "{}/{}".format(settings["app_url"], urllib.parse.quote(slug, safe=""))

    def _fetch_issues(self, client, tasks):
        now = time.monotonic()
        budget = ISSUE_FETCHES_PER_POLL
        for t in tasks:
            iid = t.get("issue_id")
            if not iid or not UUID_RE.fullmatch(iid):
                continue
            cached = self._issues.get(iid)
            if cached and now - cached[0] < ISSUE_TTL_S:
                continue
            if budget <= 0:
                break
            budget -= 1
            try:
                issue = client.get("/api/issues/{}".format(iid)) or {}
            except MulticaError:
                # A deleted or private issue must not fail the whole board.
                self._issues[iid] = (now, cached[1] if cached else {})
                continue
            self._issues[iid] = (now, {
                "identifier": issue.get("identifier"), "title": issue.get("title"), "status": issue.get("status"),
            })

    def _fetch_messages(self, client, task_id):
        entry = self._replay.setdefault(task_id, {"seq": None, "lines": [], "tool": None})
        path = "/api/tasks/{}/messages".format(task_id)
        if entry["seq"] is not None:
            path += "?since={}".format(entry["seq"])
        try:
            msgs = _ids(client.get(path))
        except MulticaError:
            return
        for m in msgs:
            if not isinstance(m, dict):
                continue
            seq = m.get("seq")
            if isinstance(seq, int) and (entry["seq"] is None or seq > entry["seq"]):
                entry["seq"] = seq
            if m.get("type") == "tool_use" and m.get("tool"):
                entry["tool"] = m["tool"]
            line = message_line(m)
            if line:
                entry["lines"].append({"t": parse_ts(m.get("created_at")), "m": line})
        del entry["lines"][:-REPLAY_KEEP]

    def _issue_ref(self, issue_id):
        info = (self._issues.get(issue_id) or (None, {}))[1]
        return {"id": issue_id or None, "identifier": info.get("identifier"), "title": info.get("title")}

    def _task_vm(self, t, now_ms):
        started = parse_ts(t.get("started_at"))
        created = parse_ts(t.get("created_at"))
        elapsed = (now_ms - started) // 1000 if started else None
        return {
            "id": t.get("id"), "status": t.get("status"), "issue": self._issue_ref(t.get("issue_id")),
            "started": started, "created": created, "elapsed_s": elapsed,
            "completed": parse_ts(t.get("completed_at")),
            "branch": t.get("branch_name") or None, "work_dir": t.get("work_dir") or None,
            "attempt": t.get("attempt"), "max_attempts": t.get("max_attempts"),
            "kind": t.get("kind") or None,
            "error": _short(t.get("error") or t.get("failure_reason") or "", ERROR_MAX) or None,
        }

    def _agent_vm(self, a, tasks, runtimes, now_ms, app_base):
        aid = a.get("id")
        mine = [t for t in tasks if t.get("agent_id") == aid]
        active = [t for t in mine if t.get("status") in ACTIVE_STATUSES]
        running = [t for t in active if t.get("status") in RUNNING_STATUSES]
        queued = [t for t in active if t.get("status") == "queued"]
        done = [t for t in mine if t.get("status") in ("completed", "failed")]
        rt = runtimes.get(a.get("runtime_id")) or {}
        rt_status = rt.get("status") or a.get("runtime_availability") or None
        current = sorted(running, key=lambda t: t.get("started_at") or "")[-1] if running else None
        if current:
            status = "working"
        elif queued:
            status = "queued"
        elif not a.get("runtime_bound", True):
            status = "unbound"
        elif rt_status and rt_status != "online":
            status = "offline"
        else:
            status = "idle"
        replay = self._replay.get(current["id"]) if current else None
        return {
            "id": aid, "name": a.get("name") or "agent", "description": a.get("description") or None,
            "status": status, "model": a.get("model") or None,
            "provider": rt.get("provider") or None,
            "runtime": {"name": rt.get("custom_name") or rt.get("name") or None, "status": rt_status},
            "max_concurrent": a.get("max_concurrent_tasks"),
            "task": self._task_vm(current, now_ms) if current else None,
            "queue": [self._task_vm(t, now_ms) for t in sorted(queued, key=lambda t: t.get("created_at") or "")],
            "last": self._task_vm(done[0], now_ms) if done else None,
            "replay": list(replay["lines"]) if replay else [],
            "tool": replay["tool"] if replay else None,
            "url": "{}/agents/{}".format(app_base, aid) if app_base and aid else None,
            "issue_base": "{}/issues/".format(app_base) if app_base else None,
        }

    @staticmethod
    def _autopilot_vm(ap, names):
        assignee = ap.get("assignee_id")
        return {
            "id": ap.get("id"), "title": ap.get("title") or "autopilot", "status": ap.get("status"),
            "description": _short(ap.get("description") or "", 400) or None,
            "agent": names.get(assignee) if ap.get("assignee_type") == "agent" else None,
            "assignee_type": ap.get("assignee_type"),
            "mode": ap.get("execution_mode"),
            "triggers": [k for k in _ids(ap.get("trigger_kinds")) if isinstance(k, str)],
            "next_run": parse_ts(ap.get("next_run_at")), "last_run": parse_ts(ap.get("last_run_at")),
        }
