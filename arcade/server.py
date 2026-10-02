#!/usr/bin/env python3
"""Local dashboard server for `farmout` (Farmout Arcade).

Stdlib-only, targets /usr/bin/python3 (3.9). Run as a script -- its own
directory is sys.path[0], so sibling modules import as plain `import config`,
`import model_jobs`, `import model_sessions`.
"""

import argparse
import hmac
import http.server
import json
import math
import mimetypes
import os
import re
import socket
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser

import config
import model_jobs
import multica
import model_sessions

DEFAULT_PORT = 7456
PORT_TRIES = 10
ACTION_TIMEOUT_S = 120
KILL_REAP_TIMEOUT_S = 5
HANDLER_TIMEOUT_S = 30
LIVE_SESSION_STATUSES = ("play", "think", "turn", "idle")
MAX_BODY_BYTES = 64 * 1024
# How long /api/state waits for a fresh build before serving the last one.
# Well under the page's 5 s poll timeout, whatever a build costs.
STATE_WAIT_S = 1.0
# A build still running after this long is reported as behind_s.
STATE_BEHIND_AFTER_S = 10

TOKEN_HEADER = "X-Arcade-Token"

JOB_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z]+-[0-9a-f]{4}$")
_JOB_ACTION_RE = re.compile(r"^/api/jobs/([^/]+)/(kill|land|discard|accept)$")
_SESSION_END_RE = re.compile(r"^/api/sessions/([^/]+)/end$")
_MULTICA_ACTION_RE = re.compile(r"^/api/multica/(tasks|autopilots|agents)/([^/]+)/(cancel|trigger|queue)$")
_MULTICA_VERBS = {"tasks": "cancel", "autopilots": "trigger", "agents": "queue"}
QUEUE_TITLE_MAX = config.QUICK_TITLE_MAX
QUEUE_PROMPT_MAX = config.QUICK_PROMPT_MAX
SESSION_ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_STATE_KEYS = ("now", "stall_min", "machine", "errors", "hud", "sessions", "jobs", "hof", "multica", "behind_s")

_EXTRA_CONTENT_TYPES = {".mjs": "text/javascript", ".woff2": "font/woff2", ".ttf": "font/ttf"}


def _content_type(path):
    ext = os.path.splitext(path)[1]
    if ext in _EXTRA_CONTENT_TYPES:
        return _EXTRA_CONTENT_TYPES[ext]
    ctype, _ = mimetypes.guess_type(path)
    return ctype or "application/octet-stream"


def _quote_etag(etag):
    return '"{}"'.format(etag)


def _unquote_etag(value):
    # A weak etag (W/"...") or the wildcard "*" is unsupported on purpose:
    # both fall through as opaque strings that will never equal a real
    # etag, so they correctly 409 rather than getting special semantics.
    if value is None:
        return None
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def _read_machine():
    """Label for this machine in SETUP: $FARMOUT_MACHINE, else the short hostname."""
    name = os.environ.get("FARMOUT_MACHINE") or socket.gethostname().split(".")[0]
    return name.strip() or None


def _numeric(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def compute_hud(sessions, jobs, today_totals, agents=()):
    """HI-SCORE/IN PLAY/COINS for the header. `today_totals` is
    JobsModel.snapshot()'s {score, credits, premium} aggregate over every
    job started today -- not just jobs[] or the HOF_MAX-capped hof[], so
    the totals don't drop once more than HOF_MAX jobs have ended today."""
    score = sum(s["score"] for s in sessions if _numeric(s.get("score")))
    score += today_totals.get("score", 0)

    # A suspended (stopped) process is not in play, whatever else it looks like.
    live = sum(1 for s in sessions if s.get("status") in LIVE_SESSION_STATUSES)
    live += sum(1 for j in jobs if j.get("status") in ("running", "lost"))
    live += sum(1 for a in agents if a.get("status") == "working")
    shown = len(sessions) + len(jobs) + len(agents)

    return {
        "score": int(score),
        "live": live,
        "shown": shown,
        "credits": round(today_totals.get("credits", 0.0), 2),
        "premium": int(today_totals.get("premium", 0)),
    }


class _JobLocks(object):
    """One lock per job id, held for the duration of one action."""

    def __init__(self):
        self._guard = threading.Lock()
        self._locks = {}

    def try_acquire(self, job_id):
        with self._guard:
            lock = self._locks.setdefault(job_id, threading.Lock())
        return lock.acquire(blocking=False)

    def release(self, job_id):
        with self._guard:
            lock = self._locks.get(job_id)
        if lock is not None:
            lock.release()


class StateBuilder(object):
    """Builds /api/state on its own thread, one build at a time, on demand.

    A request asks for a build that starts after it arrived and waits at most
    wait_s for it; past that it gets the last good state. A poll's latency is
    then bounded by wait_s however long a build takes (many jobs, big logs, a
    disk busy with the workers' builds). Concurrent requests share one build.
    """

    def __init__(self, build, wait_s=STATE_WAIT_S):
        self._build = build
        self._wait_s = wait_s
        self._cond = threading.Condition()
        self._started = 0
        self._done = 0
        self._wanted = 0
        self._building_since = None
        self._state = None  # last good build
        self._error = None  # exception type of the latest build, if it failed
        self._closed = False
        t = threading.Thread(target=self._loop, name="arcade-state")
        t.daemon = True
        t.start()

    def close(self):
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def get(self):
        """(state, error, fresh, behind_s). state is None until a build succeeds;
        fresh means a build begun after this call finished in time."""
        with self._cond:
            target = self._started + 1
            self._wanted = max(self._wanted, target)
            self._cond.notify_all()
            deadline = time.monotonic() + self._wait_s
            while self._done < target and not self._closed:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                self._cond.wait(left)
            since = self._building_since
            behind = 0.0 if since is None else time.monotonic() - since
            return self._state, self._error, self._done >= target, behind

    def _loop(self):
        while True:
            with self._cond:
                while not self._closed and self._wanted <= self._started:
                    self._cond.wait()
                if self._closed:
                    return
                self._started += 1
                self._building_since = time.monotonic()
            state, error = None, None
            try:
                state = self._build()
            except Exception as exc:  # the builder must outlive one bad build
                error = type(exc).__name__
            with self._cond:
                if state is not None:
                    self._state = state
                self._error = error
                self._done = self._started
                self._building_since = None
                self._cond.notify_all()


class ArcadeHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler_cls, *, token, farmout_bin, farmout_home,
                 claude_home, config_path, static_dir, state_wait_s=STATE_WAIT_S,
                 multica_config_dir=None, multica_poll_s=multica.POLL_S):
        # Before the bind: a failed bind calls server_close(), which closes them.
        self.config_path = config_path
        self.multica_config_dir = multica_config_dir
        self.multica = multica.MulticaSource(self._multica_settings, poll_s=multica_poll_s)
        self.jobs_model = model_jobs.JobsModel(farmout_home, background_files=True)
        self.state_builder = StateBuilder(self.build_state, state_wait_s)
        super().__init__(addr, handler_cls)
        self.token = token
        self.farmout_bin = farmout_bin
        self.farmout_home = farmout_home
        self.claude_home = claude_home
        self.config_path = config_path
        self.static_dir = os.path.realpath(static_dir)
        self.sessions_model = model_sessions.SessionsModel(claude_home)
        self.job_locks = _JobLocks()

    def server_close(self):
        self.multica.close()
        self.jobs_model.close()
        self.state_builder.close()
        super().server_close()

    def build_state(self):
        # Runs only on the StateBuilder thread, so the models see one caller.
        now_ms = int(time.time() * 1000)
        cfg, _, cfg_warnings = config.load_with_warnings(self.config_path)
        stall_min = cfg["limits"]["stall_min"]
        jobs_snap = self.jobs_model.snapshot(now_ms, stall_min)
        sessions_snap = self.sessions_model.snapshot(
            now_ms, jobs_snap["jobs"] + jobs_snap["hof"])
        errors = (
            jobs_snap["errors"]
            + sessions_snap["errors"]
            + ["config: " + w for w in cfg_warnings]
            + ["config: " + e for e in config.validate(cfg)]
        )
        mc = self.multica_state(cfg)
        if mc["enabled"] and mc["error"]:
            errors.append("multica: " + mc["error"])
        hud = compute_hud(sessions_snap["sessions"], jobs_snap["jobs"], jobs_snap["today"], mc["agents"])
        state = {
            "now": now_ms,
            "stall_min": stall_min,
            "machine": _read_machine(),
            "errors": errors,
            "hud": hud,
            "sessions": sessions_snap["sessions"],
            "jobs": jobs_snap["jobs"],
            "hof": jobs_snap["hof"],
            "multica": mc,
            "behind_s": 0,
        }
        assert set(state) == set(_STATE_KEYS)
        return state

    def _multica_settings(self):
        cfg, _ = config.load(self.config_path)
        return multica.resolve_settings(cfg["multica"], config_dir=self.multica_config_dir)

    def multica_state(self, cfg):
        """The Multica poller's last view plus the configured quick actions."""
        mc = dict(self.multica.snapshot())
        mc["quick_actions"] = [
            {"i": i, "label": qa["label"], "title": qa["title"], "prompt": qa["prompt"], "agents": qa["agents"]}
            for i, qa in enumerate(cfg["multica"]["quick_actions"])
        ]
        return mc

    def handle_error(self, request, client_address):
        # The stdlib default prints the full traceback (request included)
        # to stderr. That's the wrong default for a server whose requests
        # carry a bearer token in a header: one line, the exception type
        # only, no request data, never the token.
        exc_type = sys.exc_info()[0]
        name = exc_type.__name__ if exc_type is not None else "unknown"
        print("arcade: connection error: {}".format(name), file=sys.stderr)


class ArcadeHandler(http.server.BaseHTTPRequestHandler):
    server_version = "Arcade/1.0"
    protocol_version = "HTTP/1.1"
    timeout = HANDLER_TIMEOUT_S

    def log_message(self, fmt, *args):
        pass

    def handle_one_request(self):
        # kill/land/discard/doctor and every GET never read a body at all;
        # PUT /api/config reads exactly Content-Length bytes. Either way,
        # anything left unread on a response that keeps the connection open
        # would otherwise be misparsed by the *next* handle_one_request()
        # as the start of a new, smuggled request -- the same desync class
        # as an unread body on a denied (>=400) request, just on the
        # success path instead.
        self._body_consumed = 0
        http.server.BaseHTTPRequestHandler.handle_one_request(self)
        if not self.close_connection:
            self._drain_unread_body()

    def _read_body(self, length):
        data = self.rfile.read(length) if length else b""
        self._body_consumed += len(data)
        return data

    def _drain_unread_body(self):
        raw = self.headers.get("Content-Length")
        if raw is None:
            return
        if not raw.isdigit():
            self.close_connection = True
            return
        remaining = int(raw) - self._body_consumed
        if remaining <= 0:
            return
        if remaining > MAX_BODY_BYTES:
            # Too large to safely drain (and an odd thing to see on a 2xx
            # path at all) -- close instead of reading an unbounded body.
            self.close_connection = True
            return
        try:
            self.rfile.read(remaining)
        except OSError:
            # A read that fails here (timed out, reset, whatever) leaves
            # the socket in a state a *second* handle_one_request() cycle
            # can't safely read from either -- observed in practice as a
            # plain OSError("cannot read from timed out object") that
            # isn't the socket.timeout the base class's own handler
            # catches, so it would otherwise escape as an unhandled
            # traceback. Close instead of trying again.
            self.close_connection = True

    # -- security ---------------------------------------------------------

    def _allowed_hosts(self):
        port = self.server.server_address[1]
        return ("127.0.0.1:{}".format(port), "localhost:{}".format(port))

    def _allowed_origins(self):
        port = self.server.server_address[1]
        return ("http://127.0.0.1:{}".format(port), "http://localhost:{}".format(port))

    def _host_ok(self):
        return self.headers.get("Host", "") in self._allowed_hosts()

    def _origin_ok(self):
        origin = self.headers.get("Origin")
        return origin is None or origin in self._allowed_origins()

    def _security_ok(self):
        if not self._host_ok():
            self._deny(403, "bad host")
            return False
        if not self._origin_ok():
            self._deny(403, "bad origin")
            return False
        return True

    def _token_matches(self, supplied):
        # str-to-str hmac.compare_digest raises TypeError on any non-ASCII
        # input; compare as bytes so a malformed token denies instead of
        # crashing the request.
        supplied_bytes = (supplied or "").encode("utf-8", "surrogateescape")
        token_bytes = self.server.token.encode("utf-8")
        return hmac.compare_digest(supplied_bytes, token_bytes)

    def _token_ok(self):
        return self._token_matches(self.headers.get(TOKEN_HEADER, ""))

    # -- response helpers ---------------------------------------------------

    def _json(self, code, payload, extra_headers=None):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        if code >= 400:
            # An unread request body (denied before we ever touch rfile)
            # must not be left for the next handle_one_request() to
            # misparse as a smuggled second request on the same connection.
            self.close_connection = True
            self.send_header("Connection", "close")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _deny(self, code, reason):
        self._json(code, {"error": reason})

    def _parse_content_length(self):
        """(length, error) where error is None, "invalid" or "too_large".

        By the time we see it, the header parser has already stripped any
        leading OWS right after the colon (correct per RFC 7230 -- a
        leading-whitespace form is not something we reject). It does not
        strip trailing OWS, though, and int() itself is separately lenient
        about "1_0" (digit grouping) and "+2" (an explicit sign) -- none of
        those are a valid Content-Length on the wire, and a wrong reading
        of it is exactly the kind of mismatch a request-smuggling attack
        leans on. Plain digits only.
        """
        raw = self.headers.get("Content-Length")
        if raw is None:
            return 0, None
        if not raw.isdigit():
            return None, "invalid"
        length = int(raw)
        if length > MAX_BODY_BYTES:
            return None, "too_large"
        return length, None

    # -- static ---------------------------------------------------------

    def _serve_static_file(self, rel_path, extra_headers=None):
        static_dir = self.server.static_dir
        target = os.path.normpath(os.path.join(static_dir, rel_path.lstrip("/")))
        if target != static_dir and not target.startswith(static_dir + os.sep):
            self._deny(403, "forbidden path")
            return
        # normpath alone doesn't follow symlinks: a link inside static_dir
        # pointing outside it would pass the check above but resolve
        # elsewhere when opened, so re-check after resolving it for real.
        real_target = os.path.realpath(target)
        if real_target != static_dir and not real_target.startswith(static_dir + os.sep):
            self._deny(403, "forbidden path")
            return
        if not os.path.isfile(real_target):
            self._deny(404, "not found")
            return
        try:
            with open(real_target, "rb") as f:
                data = f.read()
        except OSError:
            self._deny(404, "not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", _content_type(real_target))
        self.send_header("Content-Length", str(len(data)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    # -- routing ---------------------------------------------------------

    def _reject_transfer_encoding(self):
        # Nobody legitimate sends one: the UI never emits a chunked body,
        # and there's no other client on a loopback-only server. Rejecting
        # it outright -- before any routing or security check -- removes
        # the whole chunked-framing-smuggling class instead of trying to
        # decode it safely.
        if self.headers.get("Transfer-Encoding") is not None:
            self._json(400, {"error": "Transfer-Encoding not supported"})
            return True
        return False

    def do_GET(self):
        if self._reject_transfer_encoding():
            return
        if not self._security_ok():
            return
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path

        if path == "/":
            query = urllib.parse.parse_qs(parsed.query)
            token = (query.get("t") or [""])[0]
            if not self._token_matches(token):
                self._deny(403, "bad token")
                return
            self._serve_static_file("index.html", extra_headers={"Referrer-Policy": "no-referrer"})
            return

        if path.startswith("/static/"):
            self._serve_static_file(urllib.parse.unquote(path[len("/static/"):]))
            return

        if path.startswith("/api/"):
            if not self._token_ok():
                self._deny(403, "missing token")
                return
            if path == "/api/state":
                self._handle_state()
            elif path == "/api/config":
                self._handle_config_get()
            else:
                self._deny(404, "not found")
            return

        self._deny(404, "not found")

    def do_PUT(self):
        if self._reject_transfer_encoding():
            return
        if not self._security_ok():
            return
        path = urllib.parse.urlsplit(self.path).path
        if not path.startswith("/api/"):
            self._deny(404, "not found")
            return
        if not self._token_ok():
            self._deny(403, "missing token")
            return
        if getattr(self.server, "demo_state", None):
            self._json(403, {"ok": False, "errors": ["demo mode: config is read-only"]})
            return
        if path == "/api/config":
            self._handle_config_put()
        else:
            self._deny(404, "not found")

    def do_POST(self):
        if self._reject_transfer_encoding():
            return
        if not self._security_ok():
            return
        path = urllib.parse.urlsplit(self.path).path
        if not path.startswith("/api/"):
            self._deny(404, "not found")
            return
        if not self._token_ok():
            self._deny(403, "missing token")
            return
        if getattr(self.server, "demo_state", None) and path != "/api/doctor":
            self._json(403, {"ok": False, "err": "demo mode: actions are disabled"})
            return

        if path == "/api/doctor":
            self._handle_doctor()
            return

        m = _MULTICA_ACTION_RE.match(path)
        if m:
            kind, target, verb = m.groups()
            if _MULTICA_VERBS[kind] != verb:
                self._deny(404, "not found")
                return
            if not multica.UUID_RE.fullmatch(target):
                self._deny(400, "bad id")
                return
            self._handle_multica_action(kind, target, verb)
            return

        m = _SESSION_END_RE.match(path)
        if m:
            if not SESSION_ID_RE.fullmatch(m.group(1)):
                self._deny(400, "bad session id")
                return
            self._handle_session_end(m.group(1))
            return

        m = _JOB_ACTION_RE.match(path)
        if not m:
            self._deny(404, "not found")
            return
        job_id, verb = m.group(1), m.group(2)
        if not JOB_ID_RE.fullmatch(job_id):
            self._deny(400, "bad job id")
            return
        self._handle_job_action(job_id, verb)

    def _handle_session_end(self, session_id):
        # Sessions are otherwise watch-only: this only ever touches processes
        # that are both registered for this session and currently stopped.
        # Runs on the handler thread: it may wait seconds for the process
        # to exit, and /api/state is served by the StateBuilder meanwhile.
        ended, survivors = self.server.sessions_model.end_suspended(session_id)
        if not ended and not survivors:
            self._json(409, {"ok": False, "err": "no suspended process for this session"})
            return
        self._json(200, {"ok": not survivors, "ended": ended, "survivors": survivors,
                         "err": "still running after SIGTERM" if survivors else None})

    # -- /api/state ---------------------------------------------------------

    def _handle_state(self):
        demo = getattr(self.server, "demo_state", None)
        if demo:
            self._handle_demo_state(demo)
            return
        state, error, fresh, behind = self.server.state_builder.get()
        # The type only: a message could carry paths or file content.
        if error and (fresh or state is None):
            self._json(500, {"errors": ["state: {}".format(error)]})
            return
        if state is None:
            # The first build is still running. Not a failure: the page shows
            # WARMING UP instead of counting it toward NO SIGNAL.
            self._json(503, {"warming": True})
            return
        errors = list(state["errors"])
        if error:
            errors.append("state: {}".format(error))
        behind_s = int(math.ceil(behind)) if behind >= STATE_BEHIND_AFTER_S else 0
        self._json(200, dict(state, errors=errors, behind_s=behind_s))

    def _handle_demo_state(self, path):
        # --demo: a fixed sample board (README screenshots, a first look
        # before any job exists). Nothing on this machine is read.
        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception as exc:
            self._json(500, {"errors": ["state: {}".format(type(exc).__name__)]})
            return
        state["now"] = int(time.time() * 1000)
        state.setdefault("behind_s", 0)
        self._json(200, state)

    # -- /api/config ---------------------------------------------------------

    def _handle_config_get(self):
        cfg, etag = config.load(self.server.config_path)
        quoted = _quote_etag(etag)
        self._json(200, {"config": cfg, "etag": quoted}, extra_headers={"ETag": quoted})

    def _handle_config_put(self):
        if_match = _unquote_etag(self.headers.get("If-Match"))

        length, length_err = self._parse_content_length()
        if length_err == "too_large":
            self._json(413, {"errors": ["request body exceeds {} bytes".format(MAX_BODY_BYTES)]})
            return
        if length_err == "invalid":
            self._json(400, {"errors": ["invalid Content-Length"]})
            return

        raw_bytes = self._read_body(length)
        if not raw_bytes:
            self._json(400, {"errors": ["empty request body"]})
            return
        try:
            body = json.loads(raw_bytes.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"errors": ["invalid JSON body"]})
            return
        if not isinstance(body, dict):
            self._json(400, {"errors": ["config: must be an object"]})
            return

        # Validate the raw body, before merge_defaults can launder a wrong
        # type (e.g. models: "notalist") into a silently-fixed-up default.
        errors = config.validate(body)
        if errors:
            self._json(400, {"errors": errors})
            return

        merged = config.merge_defaults(body)
        try:
            new_etag = config.save(self.server.config_path, merged, if_match)
        except config.EtagMismatch:
            self._json(409, {"error": "etag mismatch"})
            return
        quoted = _quote_etag(new_etag)
        self._json(200, {"config": merged, "etag": quoted}, extra_headers={"ETag": quoted})

    # -- job actions / doctor ---------------------------------------------------------

    def _run_farmout(self, args):
        argv = [self.server.farmout_bin] + list(args)
        env = dict(os.environ)
        env["FARMOUT_HOME"] = self.server.farmout_home
        try:
            # start_new_session makes the child its own process group
            # leader (pgid == pid), so a timeout can kill the whole group
            # -- farmout's own git grandchildren included -- not just the
            # one direct child subprocess.run()'s own timeout path would
            # otherwise leave running.
            proc = subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=env, start_new_session=True,
            )
        except OSError as exc:
            return None, "", str(exc), False

        try:
            out, err = proc.communicate(timeout=ACTION_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                out, err = proc.communicate(timeout=KILL_REAP_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                # A detached descendant (its own session, so killpg never
                # reached it) can still hold the pipe's write end open,
                # which would otherwise block this communicate() until IT
                # exits on its own. Give up instead of hanging the handler
                # thread and the per-job lock with it; best-effort reap our
                # own (already-SIGKILLed) direct child and close our end of
                # the pipes communicate() didn't get to close.
                out, err = b"", b""
                proc.poll()
                for pipe in (proc.stdout, proc.stderr):
                    if pipe is not None:
                        pipe.close()
            err_text = (err or b"").decode("utf-8", errors="replace")
            err_text += "\n(timed out after {}s)".format(ACTION_TIMEOUT_S)
            return None, (out or b"").decode("utf-8", errors="replace"), err_text, True

        return proc.returncode, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace"), False

    def _handle_job_action(self, job_id, verb):
        if not self.server.job_locks.try_acquire(job_id):
            self._json(409, {"error": "another action is running on this job"})
            return
        try:
            code, out, err, timed_out = self._run_farmout([verb, job_id])
        finally:
            self.server.job_locks.release(job_id)

        if verb == "land" and code == 3:
            self._json(200, {"ok": False, "conflict": True, "code": code, "out": out, "err": err})
            return
        self._json(200, {"ok": code == 0, "code": code, "timed_out": timed_out, "out": out, "err": err})

    # -- multica ---------------------------------------------------------

    def _read_json_body(self):
        """(body_dict, error_response). An absent body reads as {}."""
        length, length_err = self._parse_content_length()
        if length_err == "too_large":
            return None, (413, "request body exceeds {} bytes".format(MAX_BODY_BYTES))
        if length_err == "invalid":
            return None, (400, "invalid Content-Length")
        raw = self._read_body(length)
        if not raw:
            return {}, None
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None, (400, "invalid JSON body")
        if not isinstance(body, dict):
            return None, (400, "body must be an object")
        return body, None

    def _queue_request(self, agent_id, body):
        """(title, prompt, error) for a queue request: a configured quick
        action by index, or a custom title and prompt."""
        agent = self.server.multica.agent(agent_id)
        if agent is None:
            return None, None, "unknown agent"
        if "quick" in body:
            cfg, _ = config.load(self.server.config_path)
            qas = cfg["multica"]["quick_actions"]
            i = body.get("quick")
            if not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < len(qas):
                return None, None, "unknown quick action"
            qa = qas[i]
            if qa["agents"] and agent["name"].lower() not in [a.lower() for a in qa["agents"]]:
                return None, None, "quick action is not for this agent"
            return qa["title"], qa["prompt"], None
        title, prompt = body.get("title"), body.get("prompt") or ""
        if not isinstance(title, str) or not title.strip() or len(title) > QUEUE_TITLE_MAX:
            return None, None, "title must be 1..{} characters".format(QUEUE_TITLE_MAX)
        if not isinstance(prompt, str) or len(prompt) > QUEUE_PROMPT_MAX:
            return None, None, "prompt must be at most {} characters".format(QUEUE_PROMPT_MAX)
        return title.strip(), prompt, None

    def _handle_multica_action(self, kind, target, verb):
        body, body_err = self._read_json_body()
        if body_err:
            self._json(body_err[0], {"ok": False, "err": body_err[1]})
            return
        key = "multica:" + target
        if not self.server.job_locks.try_acquire(key):
            self._json(409, {"ok": False, "err": "another action is running on this target"})
            return
        try:
            src = self.server.multica
            if verb == "cancel":
                result = src.cancel_task(target)
            elif verb == "trigger":
                result = src.trigger_autopilot(target)
            else:
                title, prompt, err = self._queue_request(target, body)
                if err:
                    self._json(400, {"ok": False, "err": err})
                    return
                result = src.queue_task(target, title, prompt)
        except multica.MulticaError as exc:
            self._json(502, {"ok": False, "err": str(exc)})
            return
        finally:
            self.server.job_locks.release(key)
        self._json(200, result)

    def _handle_doctor(self):
        code, out, err, _ = self._run_farmout(["doctor", "--json"])
        try:
            payload = json.loads(out)
        except (ValueError, TypeError):
            self._json(502, {"errors": ["farmout doctor --json: {}".format(err or "exit {}".format(code))]})
            return
        self._json(200, payload)


def build_server(port, token, *, farmout_bin, farmout_home, claude_home, config_path, static_dir,
                 state_wait_s=STATE_WAIT_S, multica_config_dir=None, multica_poll_s=multica.POLL_S):
    return ArcadeHTTPServer(
        ("127.0.0.1", port), ArcadeHandler,
        token=token, farmout_bin=farmout_bin, farmout_home=farmout_home,
        claude_home=claude_home, config_path=config_path, static_dir=static_dir,
        state_wait_s=state_wait_s, multica_config_dir=multica_config_dir, multica_poll_s=multica_poll_s,
    )


def _default_farmout_bin():
    arcade_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(arcade_dir, "..", "bin", "farmout"))


def _listen_url(port, token):
    return "http://127.0.0.1:{}/?t={}".format(port, token)


def _bind_with_retry(preferred_port, token, **build_kwargs):
    last_exc = None
    for candidate in range(preferred_port, preferred_port + PORT_TRIES + 1):
        try:
            return build_server(candidate, token, **build_kwargs)
        except OSError as exc:
            last_exc = exc
            continue
    raise last_exc


def _parse_args(argv):
    parser = argparse.ArgumentParser(prog="arcade-server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--demo", action="store_true", help="serve a fixed sample board instead of this machine's jobs")
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    token = secrets.token_urlsafe(32)
    server = _bind_with_retry(
        args.port, token,
        farmout_bin=os.environ.get("FARMOUT_BIN") or _default_farmout_bin(),
        farmout_home=os.environ.get("FARMOUT_HOME") or os.path.expanduser("~/.cache/farmout"),
        claude_home=os.environ.get("CLAUDE_HOME") or os.path.expanduser("~/.claude"),
        config_path=config.default_config_path(),
        static_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)), "static"),
    )
    if args.demo:
        server.demo_state = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo", "state.json")
    port = server.server_address[1]
    url = _listen_url(port, token)
    print(url, flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
