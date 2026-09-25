import datetime
import http.client
import io
import json
import os
import re
import shutil
import signal
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock

import config
import server

FAKE_FARMOUT = os.path.join(os.path.dirname(__file__), "fake-farmout")

UI_STATIC_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "static"))
UI_INDEX_HTML = os.path.join(UI_STATIC_DIR, "index.html")

_STATIC_REF_RE = re.compile(r'/static/[A-Za-z0-9_./-]+')


def _job_id(suffix="a1b2"):
    return "20260923-141502-codex-" + suffix


def _iso_now():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


class _ServerTestCase(unittest.TestCase):
    """Boots a real ArcadeHTTPServer on an ephemeral port for each test."""

    static_ready = False

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="arcade-server-test-")
        self.farmout_home = os.path.join(self.tmp, "farmout_home")
        self.claude_home = os.path.join(self.tmp, "claude_home")
        self.static_dir = UI_STATIC_DIR if self.static_ready else os.path.join(self.tmp, "static")
        os.makedirs(os.path.join(self.farmout_home, "jobs"))
        os.makedirs(os.path.join(self.claude_home, "sessions"))
        os.makedirs(os.path.join(self.claude_home, "projects"))
        if not self.static_ready:
            os.makedirs(self.static_dir)
            with open(os.path.join(self.static_dir, "index.html"), "w", encoding="utf-8") as f:
                f.write("<html><body>ok</body></html>")

        self.config_path = os.path.join(self.tmp, "config.json")
        self.token = "test-token-" + self.id().rsplit(".", 1)[-1]
        self.log_path = os.path.join(self.tmp, "fake-farmout.log")

        self.srv = server.build_server(
            0, self.token,
            farmout_bin=FAKE_FARMOUT, farmout_home=self.farmout_home,
            claude_home=self.claude_home, config_path=self.config_path,
            static_dir=self.static_dir,
        )
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever)
        self.thread.daemon = True
        self.thread.start()

    def tearDown(self):
        self.srv.shutdown()
        self.thread.join(timeout=5)
        self.srv.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _conn(self):
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)

    def _request(self, method, path, headers=None, body=None):
        conn = self._conn()
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            data = resp.read()
        finally:
            conn.close()
        parsed = None
        if data:
            try:
                parsed = json.loads(data.decode("utf-8"))
            except ValueError:
                parsed = None
        return resp, parsed

    def _get(self, path, token=True, extra_headers=None):
        headers = dict(extra_headers or {})
        if token:
            headers["X-Arcade-Token"] = self.token
        return self._request("GET", path, headers=headers)

    def _raw_exchange(self, request_bytes, read_timeout=3):
        """Sends raw bytes on a fresh socket and reads until the peer
        closes or read_timeout elapses. Used where http.client's own
        encoding/validation would get in the way of the exact wire bytes
        a test needs to send."""
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        received = b""
        try:
            sock.sendall(request_bytes)
            sock.settimeout(read_timeout)
            try:
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    received += chunk
            except socket.timeout:
                pass
        finally:
            sock.close()
        return received

    def _write_job(self, job_id, meta, log_lines=None):
        job_dir = os.path.join(self.farmout_home, "jobs", job_id)
        os.makedirs(job_dir, exist_ok=True)
        with open(os.path.join(job_dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f)
        if log_lines is not None:
            with open(os.path.join(job_dir, "log"), "w", encoding="utf-8") as f:
                for line in log_lines:
                    f.write(line + "\n")
        return job_dir


def _job_meta(job_id, **overrides):
    meta = {
        "id": job_id,
        "cli": "codex",
        "mode": "read",
        "kind": "review",
        "repo": "/repo",
        "base": None,
        "source_branch": "main",
        "worktree": None,
        "pid": None,
        "pgid": None,
        "sup_pid": None,
        "started": _iso_now(),
        "ended": None,
        "timeout_s": 1800,
        "status": "running",
        "land": None,
        "error": None,
        "parent_session": None,
        "route": None,
        "model": None,
        "effort": None,
    }
    meta.update(overrides)
    return meta


# -- security ---------------------------------------------------------


class SecurityTestCase(_ServerTestCase):
    def test_wrong_host_gives_403(self):
        resp, _ = self._request(
            "GET", "/api/state",
            headers={"Host": "evil.example:9999", "X-Arcade-Token": self.token},
        )
        self.assertEqual(403, resp.status)

    def test_transfer_encoding_chunked_rejected_before_routing_no_smuggling(self):
        # The UI never sends a chunked body, and a loopback-only client has
        # no reason to either -- reject it outright instead of decoding it,
        # removing the whole smuggling-via-chunked-framing class rather
        # than trying to parse it safely. The chunk data itself carries a
        # second, fully-formed request; since we never even look at it,
        # exactly one response should come back.
        smuggled = (
            "GET /api/state HTTP/1.1\r\n"
            "Host: 127.0.0.1:{port}\r\n"
            "X-Arcade-Token: {token}\r\n"
            "\r\n"
        ).format(port=self.port, token=self.token).encode("ascii")
        chunked_body = "{:x}\r\n".format(len(smuggled)).encode("ascii") + smuggled + b"\r\n0\r\n\r\n"
        request = (
            "POST /api/doctor HTTP/1.1\r\n"
            "Host: 127.0.0.1:{port}\r\n"
            "X-Arcade-Token: {token}\r\n"
            "Transfer-Encoding: chunked\r\n"
            "\r\n"
        ).format(port=self.port, token=self.token).encode("ascii") + chunked_body

        received = self._raw_exchange(request)

        self.assertEqual(1, received.count(b"HTTP/1.1 "), received)
        self.assertIn(b" 400 ", received)
        self.assertNotIn(b'"now"', received)  # the smuggled /api/state was never served
        self.assertIn(b"connection: close", received.lower())

    def test_root_without_token_gives_403(self):
        resp, _ = self._request("GET", "/")
        self.assertEqual(403, resp.status)

    def test_root_with_wrong_token_gives_403(self):
        resp, _ = self._request("GET", "/?t=not-the-token")
        self.assertEqual(403, resp.status)

    def test_root_with_right_token_serves_index(self):
        resp, _ = self._request("GET", "/?t=" + self.token)
        self.assertEqual(200, resp.status)

    def test_api_without_token_gives_403(self):
        resp, _ = self._request("GET", "/api/state")
        self.assertEqual(403, resp.status)

    def test_foreign_origin_gives_403(self):
        resp, _ = self._get("/api/state", extra_headers={"Origin": "http://evil.example"})
        self.assertEqual(403, resp.status)

    def test_own_origin_is_allowed(self):
        resp, _ = self._get(
            "/api/state",
            extra_headers={"Origin": "http://127.0.0.1:{}".format(self.port)},
        )
        self.assertEqual(200, resp.status)

    def test_bad_job_id_gives_400(self):
        resp, _ = self._request(
            "POST", "/api/jobs/not-a-valid-id/kill",
            headers={"X-Arcade-Token": self.token},
        )
        self.assertEqual(400, resp.status)

    def test_no_cors_headers_anywhere(self):
        for method, path in (("GET", "/api/state"), ("GET", "/?t=" + self.token)):
            resp, _ = self._request(method, path, headers={"X-Arcade-Token": self.token})
            for name in resp.msg.keys():
                self.assertFalse(name.lower().startswith("access-control"), name)

    def test_static_needs_no_token(self):
        resp, _ = self._request("GET", "/static/index.html")
        self.assertEqual(200, resp.status)

    def test_static_path_traversal_dotdot_blocked(self):
        resp, _ = self._request("GET", "/static/../config.json")
        self.assertIn(resp.status, (403, 404))

    def test_static_path_traversal_encoded_dotdot_blocked(self):
        resp, _ = self._request("GET", "/static/%2e%2e/config.json")
        self.assertIn(resp.status, (403, 404))

    def test_static_symlink_escape_blocked(self):
        outside = os.path.join(self.tmp, "secret.txt")
        with open(outside, "w", encoding="utf-8") as f:
            f.write("secret-content")
        os.symlink(outside, os.path.join(self.static_dir, "escape-link"))
        resp, _ = self._request("GET", "/static/escape-link")
        self.assertIn(resp.status, (403, 404))

    def test_leftover_body_on_2xx_is_drained_no_desync(self):
        # kill/land/discard/doctor never read a body at all, and GET
        # /static doesn't either. A client that sends one anyway (odd, but
        # HTTP allows a body on any method) leaves it unread; on a 2xx
        # response the connection stays open, so the next
        # handle_one_request() would otherwise try to parse those leftover
        # bytes as the start of a pipelined second request.
        second = (
            "GET /api/state HTTP/1.1\r\n"
            "Host: 127.0.0.1:{port}\r\n"
            "X-Arcade-Token: {token}\r\n"
            "\r\n"
        ).format(port=self.port, token=self.token).encode("ascii")
        unread_body = b'{"ignored": "body"}'
        first = (
            "POST /api/jobs/{job}/kill HTTP/1.1\r\n"
            "Host: 127.0.0.1:{port}\r\n"
            "X-Arcade-Token: {token}\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: {n}\r\n"
            "\r\n"
        ).format(job=_job_id("aaaa"), port=self.port, token=self.token, n=len(unread_body)).encode("ascii") + unread_body

        received = self._raw_exchange(first + second, read_timeout=3)

        self.assertEqual(2, received.count(b"HTTP/1.1 "), received)
        self.assertEqual(2, received.count(b" 200 "), received)

    def test_denied_request_closes_connection_no_smuggling(self):
        # A denied PUT (bad Origin) whose body is a second, fully-formed
        # request the server never reads. Without closing the connection,
        # handle_one_request() loops back and reads those leftover body
        # bytes as if they were the NEXT request -- one that carries no
        # Origin header at all, sailing past the very check that just
        # denied its carrier.
        smuggled = (
            "GET /api/state HTTP/1.1\r\n"
            "Host: 127.0.0.1:{port}\r\n"
            "X-Arcade-Token: {token}\r\n"
            "\r\n"
        ).format(port=self.port, token=self.token).encode("ascii")
        request = (
            "PUT /api/config HTTP/1.1\r\n"
            "Host: 127.0.0.1:{port}\r\n"
            "Origin: http://evil.example\r\n"
            "X-Arcade-Token: {token}\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: {n}\r\n"
            "\r\n"
        ).format(port=self.port, token=self.token, n=len(smuggled)).encode("ascii") + smuggled

        received = self._raw_exchange(request)

        self.assertEqual(1, received.count(b"HTTP/1.1 "), received)
        self.assertIn(b" 403 ", received)
        self.assertNotIn(b'"now"', received)  # the smuggled /api/state was never served
        self.assertIn(b"connection: close", received.lower())

    def test_non_ascii_header_token_denied_not_crashed(self):
        request = (
            b"GET /api/state HTTP/1.1\r\n"
            b"Host: 127.0.0.1:" + str(self.port).encode("ascii") + b"\r\n"
            b"X-Arcade-Token: t\xc3\xb6ken\r\n"
            b"\r\n"
        )
        received = self._raw_exchange(request)
        self.assertIn(b" 403 ", received)

    def test_non_ascii_query_token_denied_not_crashed(self):
        resp, _ = self._request("GET", "/?t=" + "t%C3%B6ken")
        self.assertEqual(403, resp.status)

    def test_content_length_non_integer_gives_400(self):
        resp, _ = self._request(
            "PUT", "/api/config",
            headers={"X-Arcade-Token": self.token, "Content-Length": "abc"},
            body="{}",
        )
        self.assertEqual(400, resp.status)

    def test_content_length_negative_gives_400(self):
        resp, _ = self._request(
            "PUT", "/api/config",
            headers={"X-Arcade-Token": self.token, "Content-Length": "-5"},
            body="{}",
        )
        self.assertEqual(400, resp.status)

    def test_content_length_over_max_gives_413(self):
        resp, _ = self._request(
            "PUT", "/api/config",
            headers={"X-Arcade-Token": self.token, "Content-Length": str(server.MAX_BODY_BYTES + 1)},
            body="{}",
        )
        self.assertEqual(413, resp.status)

    def test_content_length_digits_only_enforced_after_header_parser_strips_leading_ows(self):
        # The header parser self.headers is built from (email/http.client's
        # own field-folding) strips leading OWS right after the colon
        # before we ever see the value, so a form with only a leading
        # space never even reaches this check (see the acceptance test
        # below). It does NOT strip trailing OWS, and int() itself is
        # separately lenient about "1_0" (digit-group separator) and "+2"
        # (an explicit sign) -- none of these are a valid Content-Length
        # on the wire, and isdigit() is what actually rejects them once
        # they reach us.
        for bad in ("1_0", "+2", "2 2", "2 "):
            resp, _ = self._request(
                "PUT", "/api/config",
                headers={"X-Arcade-Token": self.token, "Content-Length": bad},
                body="{}",
            )
            self.assertEqual(400, resp.status, "Content-Length={!r}".format(bad))

    def test_content_length_leading_whitespace_is_rfc_correct_and_accepted(self):
        # " 2" on the wire is stripped to "2" by the header parser before
        # _parse_content_length() ever runs -- correct per RFC 7230's OWS
        # handling, not something we enforce ourselves, so this stays
        # accepted rather than rejected.
        resp, data = self._get("/api/config")
        body = json.dumps(data["config"])
        resp2, _ = self._request(
            "PUT", "/api/config",
            headers={
                "X-Arcade-Token": self.token, "If-Match": data["etag"], "Content-Type": "application/json",
                "Content-Length": " " + str(len(body.encode("utf-8"))),
            },
            body=body,
        )
        self.assertEqual(200, resp2.status)

    def test_handler_timeout_constant_is_wired_up(self):
        self.assertEqual(server.HANDLER_TIMEOUT_S, server.ArcadeHandler.timeout)

    def test_idle_connection_is_closed_after_handler_timeout(self):
        with mock.patch.object(server.ArcadeHandler, "timeout", 0.3):
            srv = server.build_server(
                0, self.token, farmout_bin=FAKE_FARMOUT, farmout_home=self.farmout_home,
                claude_home=self.claude_home, config_path=self.config_path, static_dir=self.static_dir,
            )
            port = srv.server_address[1]
            thread = threading.Thread(target=srv.serve_forever)
            thread.daemon = True
            thread.start()
            try:
                sock = socket.create_connection(("127.0.0.1", port), timeout=5)
                try:
                    sock.sendall(b"GET ")  # partial request line, never completed
                    sock.settimeout(3)
                    data = sock.recv(10)
                finally:
                    sock.close()
            finally:
                srv.shutdown()
                thread.join(timeout=5)
                srv.server_close()
        self.assertEqual(b"", data)

    def test_job_id_regex_rejects_trailing_newline(self):
        # "$" alone accepts one trailing "\n"; fullmatch must not.
        self.assertIsNotNone(server.JOB_ID_RE.fullmatch(_job_id("a1b2")))
        self.assertIsNone(server.JOB_ID_RE.fullmatch(_job_id("a1b2") + "\n"))

    def test_drain_failure_closes_connection_no_traceback(self):
        # GET /static/* needs no token. A bogus Content-Length the client
        # never actually backs with real bytes makes _drain_unread_body()'s
        # own read time out; that must close the connection outright, not
        # leave it "open" for a second handle_one_request() cycle to hit
        # another failure that escapes as an unhandled traceback.
        with mock.patch.object(server.ArcadeHandler, "timeout", 0.3):
            srv = server.build_server(
                0, self.token, farmout_bin=FAKE_FARMOUT, farmout_home=self.farmout_home,
                claude_home=self.claude_home, config_path=self.config_path, static_dir=self.static_dir,
            )
            port = srv.server_address[1]
            thread = threading.Thread(target=srv.serve_forever)
            thread.daemon = True
            thread.start()
            captured = io.StringIO()
            try:
                with mock.patch("sys.stderr", captured):
                    request = (
                        "GET /static/index.html HTTP/1.1\r\n"
                        "Host: 127.0.0.1:{port}\r\n"
                        "Content-Length: 100\r\n"
                        "\r\n"
                    ).format(port=port).encode("ascii")
                    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
                    try:
                        sock.sendall(request)
                        sock.settimeout(3)
                        first_response = b""
                        try:
                            while True:
                                chunk = sock.recv(4096)
                                if not chunk:
                                    break
                                first_response += chunk
                        except socket.timeout:
                            pass
                        # Give the drain's own (0.3s) timeout time to fire
                        # and close_connection to take effect.
                        time.sleep(1.5)
                        sock.settimeout(2)
                        try:
                            trailing = sock.recv(10)
                        except socket.timeout:
                            trailing = None
                    finally:
                        sock.close()
            finally:
                srv.shutdown()
                thread.join(timeout=5)
                srv.server_close()

        self.assertIn(b" 200 ", first_response)
        self.assertEqual(b"", trailing, "connection was not closed after the drain failure")
        self.assertNotIn("Traceback", captured.getvalue())


# -- /api/state ---------------------------------------------------------


class StateSchemaTestCase(_ServerTestCase):
    def test_every_top_level_key_present(self):
        resp, data = self._get("/api/state")
        self.assertEqual(200, resp.status)
        self.assertEqual(
            set(data.keys()),
            {"now", "stall_min", "machine", "errors", "hud", "sessions", "jobs", "hof"},
        )
        self.assertEqual(set(data["hud"].keys()), {"score", "live", "shown", "credits", "premium"})
        self.assertIsInstance(data["sessions"], list)
        self.assertIsInstance(data["jobs"], list)
        self.assertIsInstance(data["hof"], list)
        self.assertIsInstance(data["errors"], list)

    def test_stall_min_comes_from_config(self):
        cfg, etag = config.load(self.config_path)
        cfg["limits"]["stall_min"] = 42
        config.save(self.config_path, cfg, etag)
        resp, data = self._get("/api/state")
        self.assertEqual(200, resp.status)
        self.assertEqual(42, data["stall_min"])

    def test_hud_sums_follow_spec(self):
        now_iso = _iso_now()
        # A: running codex job, started today, 150 tokens -- counts toward
        # score and "in play" (running).
        self._write_job(
            _job_id("a1b2"),
            _job_meta(_job_id("a1b2"), cli="codex", mode="read", status="running", started=now_iso),
            log_lines=[json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 50}})],
        )
        # B: running kiro job, started today, 0.5 CR -- counts toward
        # credits and "in play".
        self._write_job(
            _job_id("c3d4"),
            _job_meta(_job_id("c3d4"), cli="kiro", mode="read", status="running", started=now_iso),
            log_lines=[json.dumps({"type": "metadata", "data": {"meteringUsage": [{"value": 0.5, "unit": "credit"}]}})],
        )
        # C: finished (read, ok) copilot job -> lands in hof, 2 PR, started
        # today -- counts toward premium but not "in play" (not running/lost).
        self._write_job(
            _job_id("e5f6"),
            _job_meta(
                _job_id("e5f6"), cli="copilot", mode="read", status="ok",
                started=now_iso, ended=now_iso,
            ),
            log_lines=[json.dumps({"type": "result", "usage": {"premiumRequests": 2}})],
        )

        resp, data = self._get("/api/state")
        self.assertEqual(200, resp.status)
        hud = data["hud"]
        self.assertEqual(len(data["sessions"]), 0)
        self.assertEqual(len(data["jobs"]), 2)  # A and B; C is finished-read -> hof only
        self.assertEqual(len(data["hof"]), 1)
        self.assertEqual(hud["shown"], 2)
        self.assertEqual(hud["live"], 2)  # both A and B are running
        self.assertEqual(hud["score"], 150)  # only A has tokens
        self.assertEqual(hud["credits"], 0.5)
        self.assertEqual(hud["premium"], 2)

    def test_hud_totals_not_undercounted_past_ten_ended_jobs(self):
        # hof only ever shows the 10 most-recently-ended jobs; the HUD
        # aggregate must still count every one of them that ended today.
        for i in range(11):
            job_id = "copilot-hof-job-%02d" % i
            self._write_job(
                job_id,
                _job_meta(job_id, cli="copilot", mode="read", status="ok",
                          started=_iso_now(), ended=_iso_now()),
                log_lines=[json.dumps({"type": "result", "usage": {"premiumRequests": 1}})],
            )
        resp, data = self._get("/api/state")
        self.assertEqual(200, resp.status)
        self.assertEqual(len(data["hof"]), 10)
        self.assertEqual(data["hud"]["premium"], 11)


class StateRobustnessTestCase(_ServerTestCase):
    def _write_config(self, obj):
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(obj, f)

    def _running_job_with_log_age(self, suffix, age_min):
        # No progress event in the log, so the stall clock runs from start.
        job_id = _job_id(suffix)
        started = datetime.datetime.utcfromtimestamp(time.time() - age_min * 60)
        self._write_job(
            job_id, _job_meta(job_id, status="running", sup_pid=os.getpid(),
                              started=started.strftime("%Y-%m-%dT%H:%M:%SZ")),
            log_lines=["{}"],
        )
        return job_id

    def _poses(self, data):
        return {j["id"]: j["pose"] for j in data["jobs"]}

    def test_null_and_digit_string_stall_min_use_ten(self):
        quiet = self._running_job_with_log_age("a1b2", 11)
        fresh = self._running_job_with_log_age("c3d4", 9)
        for value in (None, "10"):
            self._write_config({"limits": {"stall_min": value}})
            resp, data = self._get("/api/state")
            self.assertEqual(200, resp.status, value)
            self.assertEqual(10, data["stall_min"], value)
            self.assertEqual(self._poses(data)[quiet], "pause", value)
            self.assertEqual(self._poses(data)[fresh], "play", value)
            self.assertFalse(any("stall_min" in e for e in data["errors"]), value)

    def test_out_of_range_stall_min_is_default_with_warning(self):
        self._write_config({"limits": {"stall_min": 0}})
        resp, data = self._get("/api/state")
        self.assertEqual(200, resp.status)
        self.assertEqual(config.DEFAULT_STALL_MIN, data["stall_min"])
        self.assertIn("config: limits.stall_min 0 ignored (expected 1..120)", data["errors"])

    def test_state_failure_is_a_500_not_a_dropped_connection(self):
        with mock.patch.object(self.srv.jobs_model, "snapshot", side_effect=KeyError("boom")):
            resp, data = self._get("/api/state")
        self.assertEqual(500, resp.status)
        self.assertEqual({"errors": ["state: KeyError"]}, data)

    def test_state_answers_while_worktree_git_is_slow(self):
        # 8 running write jobs whose git calls take 2 s each: the browser's
        # 5 s poll timeout must still see an answer.
        def slow_git(args, cwd):
            time.sleep(2)
            return ""

        self.srv.jobs_model._run_git = slow_git
        for i in range(8):
            job_id = _job_id("%04x" % i)
            self._write_job(job_id, _job_meta(job_id, mode="write", sup_pid=os.getpid(),
                                              base="abc", worktree="/wt/" + job_id))
        for _ in range(3):
            started = time.monotonic()
            resp, data = self._get("/api/state")
            self.assertEqual(200, resp.status)
            self.assertLess(time.monotonic() - started, 1.0)
            self.assertEqual(8, len(data["jobs"]))

    def test_crew_includes_finished_jobs_from_hof(self):
        session_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        pid, proc_start = 555555, "Wed Sep 23 06:29:13 2026"
        with open(os.path.join(self.claude_home, "sessions", "%d.json" % pid), "w", encoding="utf-8") as f:
            json.dump({"pid": pid, "sessionId": session_id, "cwd": "/tmp/fake-repo", "procStart": proc_start,
                       "status": "idle", "startedAt": 1790144955082}, f)
        self.srv.sessions_model._ps_sweep = lambda: {pid: ("S", proc_start)}
        done = _job_id("e5f6")
        self._write_job(done, _job_meta(done, status="ok", ended=_iso_now(), parent_session=session_id))

        resp, data = self._get("/api/state")
        self.assertEqual(200, resp.status)
        self.assertEqual([done], [j["id"] for j in data["hof"]])
        self.assertEqual(1, len(data["sessions"]))
        self.assertEqual([done], data["sessions"][0]["crew"])


# -- /api/config ---------------------------------------------------------


class ConfigApiTestCase(_ServerTestCase):
    def test_get_returns_defaults_merged_with_etag(self):
        resp, data = self._get("/api/config")
        self.assertEqual(200, resp.status)
        self.assertIn("config", data)
        self.assertIn("etag", data)
        self.assertEqual(resp.getheader("ETag"), data["etag"])
        self.assertEqual(data["config"]["limits"], {"stall_min": 10, "max_jobs": 4})

    def test_dirty_config_on_disk_round_trips_clean_through_get_and_put(self):
        # workers.codx is a typo the UI would never send, and version 2 is
        # foreign to this server -- but they can land on disk by hand, and
        # `farmout` itself just ignores them with a warning. GET must not
        # carry them back into what the UI would PUT.
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump({"workers": {"codx": {"enabled": True}}, "version": 2}, f)

        resp, state_before = self._get("/api/state")
        self.assertEqual(200, resp.status)
        self.assertTrue(any("codx" in e and "unknown cli" in e for e in state_before["errors"]))
        self.assertTrue(any("version 2" in e and "expected 1" in e for e in state_before["errors"]))

        resp, data = self._get("/api/config")
        self.assertEqual(200, resp.status)
        cfg = data["config"]
        self.assertNotIn("codx", cfg["workers"])
        self.assertEqual(cfg["version"], 1)

        resp2, data2 = self._request(
            "PUT", "/api/config",
            headers={"X-Arcade-Token": self.token, "If-Match": data["etag"], "Content-Type": "application/json"},
            body=json.dumps(cfg),
        )
        self.assertEqual(200, resp2.status, data2)

        with open(self.config_path, "r", encoding="utf-8") as f:
            on_disk = json.load(f)
        self.assertNotIn("codx", on_disk["workers"])
        self.assertEqual(on_disk["version"], 1)

        resp, state_after = self._get("/api/state")
        self.assertEqual(200, resp.status)
        self.assertFalse(any("codx" in e for e in state_after["errors"]))
        self.assertFalse(any("version" in e for e in state_after["errors"]))

    def test_get_without_file_returns_every_default_routing_rule(self):
        resp, data = self._get("/api/config")
        self.assertEqual(200, resp.status)
        self.assertEqual([r["kind"] for r in data["config"]["routing"]], list(config.KINDS))
        self.assertEqual(data["config"]["routing"], [dict(r) for r in config.DEFAULT_ROUTING])

    def _round_trip(self):
        resp, data = self._get("/api/config")
        self.assertEqual(200, resp.status)
        resp2, data2 = self._request(
            "PUT", "/api/config",
            headers={"X-Arcade-Token": self.token, "If-Match": data["etag"], "Content-Type": "application/json"},
            body=json.dumps(data["config"]),
        )
        self.assertEqual(200, resp2.status, data2)
        return data["config"]

    def test_invalid_models_entry_dropped_with_warning_and_round_trips(self):
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump({"workers": {"codex": {"models": ["gpt-5", "-bad"]}}}, f)
        _, state = self._get("/api/state")
        self.assertTrue(any("workers.codex.models[] '-bad' ignored" in e for e in state["errors"]), state["errors"])
        cfg = self._round_trip()
        self.assertEqual(cfg["workers"]["codex"]["models"], ["gpt-5"])

    def test_invalid_routing_rule_dropped_with_warning_others_kept(self):
        good = [{"kind": "review", "prefer": "codex", "fallback": None},
                {"kind": "research", "prefer": "kiro", "fallback": "copilot"}]
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump({"routing": [good[0], {"kind": "review", "prefer": "fake", "fallback": None}, good[1]]}, f)
        _, state = self._get("/api/state")
        self.assertTrue(any(e.startswith("config: routing[1] ") for e in state["errors"]), state["errors"])
        cfg = self._round_trip()
        self.assertEqual(cfg["routing"], good)

    def test_put_stale_etag_gives_409(self):
        resp, data = self._get("/api/config")
        good_etag = data["etag"]
        cfg = data["config"]

        # Land one valid write first so the on-disk etag moves on.
        self._request(
            "PUT", "/api/config",
            headers={"X-Arcade-Token": self.token, "If-Match": good_etag, "Content-Type": "application/json"},
            body=json.dumps(cfg),
        )

        # Reusing the now-stale etag must 409.
        resp2, data2 = self._request(
            "PUT", "/api/config",
            headers={"X-Arcade-Token": self.token, "If-Match": good_etag, "Content-Type": "application/json"},
            body=json.dumps(cfg),
        )
        self.assertEqual(409, resp2.status)

    def test_put_invalid_model_gives_400_with_errors(self):
        resp, data = self._get("/api/config")
        cfg = data["config"]
        cfg["workers"]["codex"]["model"] = "-rm -rf"
        resp2, data2 = self._request(
            "PUT", "/api/config",
            headers={"X-Arcade-Token": self.token, "If-Match": data["etag"], "Content-Type": "application/json"},
            body=json.dumps(cfg),
        )
        self.assertEqual(400, resp2.status)
        self.assertIn("errors", data2)
        self.assertTrue(len(data2["errors"]) >= 1)

    def test_valid_put_writes_atomically_and_returns_new_etag(self):
        resp, data = self._get("/api/config")
        cfg = data["config"]
        cfg["limits"]["max_jobs"] = 7
        resp2, data2 = self._request(
            "PUT", "/api/config",
            headers={"X-Arcade-Token": self.token, "If-Match": data["etag"], "Content-Type": "application/json"},
            body=json.dumps(cfg),
        )
        self.assertEqual(200, resp2.status)
        self.assertNotEqual(data["etag"], data2["etag"])
        self.assertEqual(data2["config"]["limits"]["max_jobs"], 7)

        with open(self.config_path, "r", encoding="utf-8") as f:
            on_disk = json.load(f)
        self.assertEqual(on_disk["limits"]["max_jobs"], 7)

    def test_two_concurrent_puts_same_etag_exactly_one_409(self):
        # Same delay-injection technique as the config.py unit test, at the
        # HTTP layer: without it, two real threads racing over a loopback
        # PUT usually still serialize by luck (GIL, scheduler), so this
        # would pass even if server.py's lock were missing. Widening the
        # check-then-write window deterministically is what actually
        # proves the lock, not just "it passed this run."
        resp, data = self._get("/api/config")
        etag = data["etag"]
        cfg_a = json.loads(json.dumps(data["config"]))
        cfg_a["limits"]["max_jobs"] = 5
        cfg_b = json.loads(json.dumps(data["config"]))
        cfg_b["limits"]["max_jobs"] = 6
        results = []

        def put(cfg):
            r, _ = self._request(
                "PUT", "/api/config",
                headers={"X-Arcade-Token": self.token, "If-Match": etag, "Content-Type": "application/json"},
                body=json.dumps(cfg),
            )
            results.append(r.status)

        original_file_bytes = config._file_bytes

        def slow_file_bytes(path):
            data = original_file_bytes(path)
            time.sleep(0.05)
            return data

        with mock.patch.object(config, "_file_bytes", side_effect=slow_file_bytes):
            threads = [threading.Thread(target=put, args=(cfg_a,)), threading.Thread(target=put, args=(cfg_b,))]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

        self.assertEqual(sorted(results), [200, 409])

    def test_etag_is_quoted_and_unquoted_if_match_also_accepted(self):
        resp, data = self._get("/api/config")
        self.assertTrue(data["etag"].startswith('"') and data["etag"].endswith('"'))
        self.assertEqual(resp.getheader("ETag"), data["etag"])

        bare_etag = data["etag"].strip('"')
        cfg = data["config"]
        resp2, data2 = self._request(
            "PUT", "/api/config",
            headers={"X-Arcade-Token": self.token, "If-Match": bare_etag, "Content-Type": "application/json"},
            body=json.dumps(cfg),
        )
        self.assertEqual(200, resp2.status)
        self.assertTrue(data2["etag"].startswith('"') and data2["etag"].endswith('"'))

    def test_weak_etag_and_wildcard_if_match_are_unsupported_and_409(self):
        resp, data = self._get("/api/config")
        cfg = data["config"]
        for if_match in ('W/"' + data["etag"].strip('"') + '"', "*"):
            resp2, _ = self._request(
                "PUT", "/api/config",
                headers={"X-Arcade-Token": self.token, "If-Match": if_match, "Content-Type": "application/json"},
                body=json.dumps(cfg),
            )
            self.assertEqual(409, resp2.status, "If-Match={!r}".format(if_match))

    def test_put_non_dict_bodies_give_400(self):
        for body in ("[]", '"x"', ""):
            resp, data = self._request(
                "PUT", "/api/config",
                headers={"X-Arcade-Token": self.token, "If-Match": "irrelevant", "Content-Type": "application/json"},
                body=body,
            )
            self.assertEqual(400, resp.status, "body={!r}".format(body))
            self.assertIn("errors", data)

    def test_put_rejects_models_field_wrong_type_instead_of_coercing(self):
        resp, data = self._get("/api/config")
        cfg = data["config"]
        cfg["workers"]["codex"]["models"] = "notalist"
        resp2, data2 = self._request(
            "PUT", "/api/config",
            headers={"X-Arcade-Token": self.token, "If-Match": data["etag"], "Content-Type": "application/json"},
            body=json.dumps(cfg),
        )
        self.assertEqual(400, resp2.status)
        self.assertTrue(any("models" in e for e in data2["errors"]))

    def test_put_rejects_unknown_worker_key(self):
        resp, data = self._get("/api/config")
        cfg = data["config"]
        cfg["workers"]["not-a-real-cli"] = {"enabled": True}
        resp2, data2 = self._request(
            "PUT", "/api/config",
            headers={"X-Arcade-Token": self.token, "If-Match": data["etag"], "Content-Type": "application/json"},
            body=json.dumps(cfg),
        )
        self.assertEqual(400, resp2.status)

    def test_put_rejects_non_bool_enabled(self):
        resp, data = self._get("/api/config")
        cfg = data["config"]
        cfg["workers"]["codex"]["enabled"] = "yes"
        resp2, data2 = self._request(
            "PUT", "/api/config",
            headers={"X-Arcade-Token": self.token, "If-Match": data["etag"], "Content-Type": "application/json"},
            body=json.dumps(cfg),
        )
        self.assertEqual(400, resp2.status)

    def test_put_rejects_wrong_version(self):
        resp, data = self._get("/api/config")
        cfg = data["config"]
        cfg["version"] = 2
        resp2, data2 = self._request(
            "PUT", "/api/config",
            headers={"X-Arcade-Token": self.token, "If-Match": data["etag"], "Content-Type": "application/json"},
            body=json.dumps(cfg),
        )
        self.assertEqual(400, resp2.status)


# -- job actions / doctor ---------------------------------------------------------


class ActionsTestCase(_ServerTestCase):
    def _post(self, path, headers=None):
        headers = dict(headers or {})
        headers["X-Arcade-Token"] = self.token
        return self._request("POST", path, headers=headers)

    def test_land_success(self):
        resp, data = self._post("/api/jobs/{}/land".format(_job_id("a1b2")))
        self.assertEqual(200, resp.status)
        self.assertEqual({"ok": True, "code": 0, "timed_out": False, "out": "", "err": ""}, data)

    def test_land_conflict_exit_3_is_200_ok_false_conflict_true(self):
        os.environ["FAKE_FARMOUT_LOG"] = self.log_path
        os.environ["FAKE_FARMOUT_LAND_EXIT"] = "3"
        try:
            resp, data = self._post("/api/jobs/{}/land".format(_job_id("a1b2")))
        finally:
            os.environ.pop("FAKE_FARMOUT_LAND_EXIT", None)
            os.environ.pop("FAKE_FARMOUT_LOG", None)
        self.assertEqual(200, resp.status)
        self.assertEqual(data["ok"], False)
        self.assertEqual(data["conflict"], True)
        self.assertEqual(data["code"], 3)

        with open(self.log_path, "r", encoding="utf-8") as f:
            entries = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(1, len(entries))
        self.assertEqual(entries[0]["argv"], ["land", _job_id("a1b2")])

    def test_two_concurrent_land_posts_one_gets_409(self):
        job_id = _job_id("9999")
        os.environ["FAKE_FARMOUT_LOG"] = self.log_path
        os.environ["FAKE_FARMOUT_SLEEP"] = "0.4"
        results = []

        def fire():
            resp, data = self._post("/api/jobs/{}/land".format(job_id))
            results.append(resp.status)

        try:
            threads = [threading.Thread(target=fire) for _ in range(2)]
            for t in threads:
                t.start()
            time.sleep(0.05)
            for t in threads:
                t.join(timeout=5)
        finally:
            os.environ.pop("FAKE_FARMOUT_SLEEP", None)
            os.environ.pop("FAKE_FARMOUT_LOG", None)

        self.assertEqual(sorted(results), [200, 409])
        with open(self.log_path, "r", encoding="utf-8") as f:
            entries = [line for line in f if line.strip()]
        self.assertEqual(1, len(entries))

    def test_doctor_returns_fakes_json(self):
        payload = [{"cli": "codex", "status": "ok", "version": "0.155.1"}]
        os.environ["FAKE_FARMOUT_DOCTOR_JSON"] = json.dumps(payload)
        try:
            resp, data = self._post("/api/doctor")
        finally:
            os.environ.pop("FAKE_FARMOUT_DOCTOR_JSON", None)
        self.assertEqual(200, resp.status)
        self.assertEqual(payload, data)

    def test_timeout_kills_whole_process_group_not_just_direct_child(self):
        # fake-farmout spawns a real `sleep 60` grandchild and outlives the
        # patched-short ACTION_TIMEOUT_S itself (FAKE_FARMOUT_SLEEP). If the
        # server only killed its direct child, the grandchild -- farmout's
        # own git children in real life -- would survive.
        pidfile = os.path.join(self.tmp, "grandchild.pid")
        os.environ["FAKE_FARMOUT_GRANDCHILD_PIDFILE"] = pidfile
        os.environ["FAKE_FARMOUT_SLEEP"] = "5"
        try:
            with mock.patch.object(server, "ACTION_TIMEOUT_S", 1.0):
                resp, data = self._post("/api/jobs/{}/kill".format(_job_id("aaaa")))
        finally:
            os.environ.pop("FAKE_FARMOUT_GRANDCHILD_PIDFILE", None)
            os.environ.pop("FAKE_FARMOUT_SLEEP", None)

        self.assertEqual(200, resp.status)
        self.assertEqual(data["ok"], False)
        self.assertIn("timed out", data["err"])
        self.assertIsNone(data["code"])
        self.assertTrue(data["timed_out"])

        # Interpreter-startup jitter under load can occasionally leave the
        # pidfile not-yet-written the instant the response comes back, even
        # though fake-farmout writes it before sleeping; a missing file
        # gets the same retry as a still-alive process.
        grandchild_pid = None
        for _ in range(30):
            if os.path.isfile(pidfile):
                with open(pidfile, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if content:
                    grandchild_pid = int(content)
                    try:
                        os.kill(grandchild_pid, 0)
                    except ProcessLookupError:
                        break
            time.sleep(0.1)
        else:
            self.fail("grandchild process {} outlived the timeout".format(grandchild_pid))

    def test_reap_after_killpg_has_its_own_timeout_and_gives_up(self):
        # A detached grandchild escapes the killpg (its own session) but
        # still holds the pipe's write end open, so the post-kill
        # communicate() would otherwise block until IT exits on its own --
        # here, up to fake-farmout's own 8s sleep. The server must give up
        # well before that, release the lock, and report timed out instead
        # of hanging the handler thread.
        pidfile = os.path.join(self.tmp, "detached.pid")
        os.environ["FAKE_FARMOUT_DETACHED_GRANDCHILD_PIDFILE"] = pidfile
        os.environ["FAKE_FARMOUT_SLEEP"] = "5"
        detached_pid = None
        try:
            with mock.patch.object(server, "ACTION_TIMEOUT_S", 0.5), \
                 mock.patch.object(server, "KILL_REAP_TIMEOUT_S", 0.5):
                start = time.time()
                resp, data = self._post("/api/jobs/{}/kill".format(_job_id("bbbb")))
                elapsed = time.time() - start
        finally:
            os.environ.pop("FAKE_FARMOUT_DETACHED_GRANDCHILD_PIDFILE", None)
            os.environ.pop("FAKE_FARMOUT_SLEEP", None)
            for _ in range(20):
                if os.path.isfile(pidfile):
                    with open(pidfile, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                    if content:
                        detached_pid = int(content)
                        break
                time.sleep(0.1)
            if detached_pid:
                try:
                    os.kill(detached_pid, signal.SIGKILL)
                except OSError:
                    pass

        self.assertEqual(200, resp.status)
        self.assertEqual(data["ok"], False)
        self.assertIn("timed out", data["err"])
        self.assertLess(elapsed, 5.0)


# -- static assets referenced by the real UI ---------------------------------------------------------


@unittest.skipUnless(os.path.isfile(UI_INDEX_HTML), "static assets not present")
class StaticAssetsTestCase(_ServerTestCase):
    static_ready = True

    def test_every_static_reference_in_index_resolves(self):
        resp, _ = self._request("GET", "/?t=" + self.token)
        self.assertEqual(200, resp.status)
        conn = self._conn()
        try:
            conn.request("GET", "/?t=" + self.token)
            body = conn.getresponse().read().decode("utf-8")
        finally:
            conn.close()

        refs = sorted(set(_STATIC_REF_RE.findall(body)))
        self.assertTrue(refs, "index.html referenced no /static/ paths")
        for ref in refs:
            r, _ = self._request("GET", ref)
            self.assertEqual(200, r.status, "static ref {} did not resolve".format(ref))


# -- quiet handler errors ---------------------------------------------------------


class HandleErrorTestCase(_ServerTestCase):
    def test_writes_one_line_no_traceback_no_token(self):
        captured = io.StringIO()
        try:
            raise ValueError("boom")
        except ValueError:
            with mock.patch("sys.stderr", captured):
                self.srv.handle_error(None, ("127.0.0.1", 12345))

        output = captured.getvalue()
        self.assertEqual(1, output.count("\n"))
        self.assertIn("arcade: connection error:", output)
        self.assertIn("ValueError", output)
        self.assertNotIn("Traceback", output)
        self.assertNotIn(self.token, output)
        self.assertNotIn("boom", output)


# -- server.py helpers (port retry / URL format) ---------------------------------------------------------


class BindWithRetryTestCase(unittest.TestCase):
    def test_listen_url_format(self):
        self.assertEqual("http://127.0.0.1:7456/?t=abc", server._listen_url(7456, "abc"))

    def test_retries_next_port_when_preferred_is_busy(self):
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        busy_port = blocker.getsockname()[1]
        tmp = tempfile.mkdtemp(prefix="arcade-bind-test-")
        try:
            srv = server._bind_with_retry(
                busy_port, "tok",
                farmout_bin=FAKE_FARMOUT, farmout_home=tmp, claude_home=tmp,
                config_path=os.path.join(tmp, "config.json"), static_dir=tmp,
            )
            try:
                self.assertNotEqual(busy_port, srv.server_address[1])
                self.assertLessEqual(srv.server_address[1], busy_port + server.PORT_TRIES)
            finally:
                srv.server_close()
        finally:
            blocker.close()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()


class ComputeHudLiveTests(unittest.TestCase):
    def test_suspended_sessions_are_shown_but_not_live(self):
        sessions = [{"status": s, "score": 0} for s in ("play", "turn", "idle", "SUSPENDED")]
        hud = server.compute_hud(sessions, [], {})
        self.assertEqual(hud["live"], 3)
        self.assertEqual(hud["shown"], 4)


class SessionEndApiTestCase(_ServerTestCase):
    SID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def _post(self, path, token=True):
        headers = {"X-Arcade-Token": self.token} if token else {}
        return self._request("POST", path, headers=headers)

    def test_needs_token(self):
        resp, _ = self._post("/api/sessions/{}/end".format(self.SID), token=False)
        self.assertEqual(403, resp.status)

    def test_rejects_non_uuid_id(self):
        resp, _ = self._post("/api/sessions/..%2Fx/end")
        self.assertEqual(400, resp.status)

    def test_nothing_suspended_is_409(self):
        resp, data = self._post("/api/sessions/{}/end".format(self.SID))
        self.assertEqual(409, resp.status)
        self.assertFalse(data["ok"])

    def test_ended_and_survivor_shapes(self):
        self.srv.sessions_model.end_suspended = lambda sid: ([123], [])
        resp, data = self._post("/api/sessions/{}/end".format(self.SID))
        self.assertEqual((200, True, [123]), (resp.status, data["ok"], data["ended"]))
        self.srv.sessions_model.end_suspended = lambda sid: ([], [456])
        resp, data = self._post("/api/sessions/{}/end".format(self.SID))
        self.assertEqual((200, False, [456]), (resp.status, data["ok"], data["survivors"]))


class DemoModeTestCase(_ServerTestCase):
    def setUp(self):
        super(DemoModeTestCase, self).setUp()
        self.srv.demo_state = os.path.join(os.path.dirname(UI_STATIC_DIR), "demo", "state.json")

    def test_state_is_the_sample_board_with_a_fresh_clock(self):
        resp, data = self._get("/api/state")
        self.assertEqual(200, resp.status)
        self.assertGreater(len(data["sessions"]) + len(data["jobs"]), 0)
        self.assertLess(abs(data["now"] - time.time() * 1000), 60000)

    def test_actions_are_refused(self):
        resp, data = self._request("POST", "/api/jobs/20260923-130500-cursor-0a1b/land",
                                   headers={"X-Arcade-Token": self.token})
        self.assertEqual(403, resp.status)
        self.assertIn("demo", data["err"])

    def test_config_save_is_refused(self):
        resp, data = self._request("PUT", "/api/config", body=b"{}",
                                   headers={"X-Arcade-Token": self.token, "Content-Type": "application/json"})
        self.assertEqual(403, resp.status)
