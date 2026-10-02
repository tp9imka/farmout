import json
import os
import shutil
import tempfile
import unittest

import multica
from fake_multica import (
    AG_BUSY, AG_IDLE, AG_OFF, AG_QUEUED, AP, I_RUN, T_RUN, TOKEN, WS, FakeMultica,
)


def _write_profile(root, data, profile=None):
    d = os.path.join(root, "profiles", profile) if profile else root
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "config.json"), "w", encoding="utf-8") as f:
        json.dump(data, f)


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="arcade-multica-")
        self.addCleanup(shutil.rmtree, self.dir)

    def test_reads_cli_profile_and_auto_enables(self):
        _write_profile(self.dir, {"server_url": "http://mc.local:8080/", "app_url": "http://mc.local:3000",
                                  "workspace_id": WS, "token": TOKEN})
        s = multica.resolve_settings({}, config_dir=self.dir, env={})
        self.assertTrue(s["enabled"])
        self.assertTrue(s["configured"])
        self.assertEqual("http://mc.local:8080", s["server_url"])
        self.assertIsNone(s["why"])

    def test_named_profile(self):
        _write_profile(self.dir, {"server_url": "http://x", "workspace_id": WS, "token": TOKEN}, profile="staging")
        self.assertFalse(multica.resolve_settings({}, config_dir=self.dir, env={})["enabled"])
        s = multica.resolve_settings({"profile": "staging"}, config_dir=self.dir, env={})
        self.assertTrue(s["enabled"])

    def test_env_overrides_profile(self):
        _write_profile(self.dir, {"server_url": "http://x", "workspace_id": WS, "token": "mul_old"})
        s = multica.resolve_settings({}, config_dir=self.dir, env={"MULTICA_TOKEN": TOKEN, "MULTICA_SERVER_URL": "http://y"})
        self.assertEqual(TOKEN, s["token"])
        self.assertEqual("http://y", s["server_url"])

    def test_missing_login_stays_off_unless_asked(self):
        s = multica.resolve_settings({}, config_dir=self.dir, env={})
        self.assertFalse(s["enabled"])
        self.assertIn("multica login", s["why"])
        forced = multica.resolve_settings({"enabled": True}, config_dir=self.dir, env={})
        self.assertTrue(forced["enabled"])
        self.assertFalse(forced["configured"])

    def test_disabled_by_config(self):
        _write_profile(self.dir, {"server_url": "http://x", "workspace_id": WS, "token": TOKEN})
        self.assertFalse(multica.resolve_settings({"enabled": False}, config_dir=self.dir, env={})["enabled"])

    def test_non_uuid_workspace_is_not_configured(self):
        _write_profile(self.dir, {"server_url": "http://x", "workspace_id": "../etc", "token": TOKEN})
        s = multica.resolve_settings({}, config_dir=self.dir, env={})
        self.assertFalse(s["enabled"])
        self.assertIn("UUID", s["why"])


class HelpersTest(unittest.TestCase):
    def test_parse_ts_nanoseconds_and_offsets(self):
        self.assertEqual(multica.parse_ts("2026-10-02T09:00:00Z"), multica.parse_ts("2026-10-02T11:00:00+02:00"))
        self.assertEqual(multica.parse_ts("2026-10-02T09:00:00.123Z") + 0, multica.parse_ts("2026-10-02T09:00:00.123456789Z"))
        self.assertIsNone(multica.parse_ts("yesterday"))
        self.assertIsNone(multica.parse_ts(None))

    def test_message_lines(self):
        self.assertEqual("▸ Bash npm test", multica.message_line({"type": "tool_use", "tool": "Bash", "input": {"command": "npm test"}}))
        self.assertEqual("hello world", multica.message_line({"type": "text", "content": "hello\n  world"}))
        self.assertTrue(multica.message_line({"type": "error", "content": "x"}).startswith("ERROR"))
        self.assertIsNone(multica.message_line({"type": "tool_result", "output": "ok"}))
        self.assertIsNone(multica.message_line({"type": "text", "content": "  "}))
        self.assertEqual(multica.LINE_MAX, len(multica.message_line({"type": "text", "content": "x" * 1000})))


class SourceTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeMultica()
        self.addCleanup(self.fake.close)
        self.settings = {"enabled": True, "configured": True, "server_url": self.fake.url,
                         "app_url": "http://app.local", "workspace_id": WS, "token": TOKEN, "why": None}
        self.src = multica.MulticaSource(lambda: self.settings, autostart=False)

    def agent(self, aid):
        return next(a for a in self.src.snapshot()["agents"] if a["id"] == aid)

    def test_agents_statuses_and_tasks(self):
        self.src.poll_once()
        snap = self.src.snapshot()
        self.assertTrue(snap["connected"])
        self.assertIsNone(snap["error"])
        self.assertEqual({"id": WS, "name": "Acme", "slug": "acme"}, snap["workspace"])
        self.assertEqual(4, len(snap["agents"]), "archived agents are left out")

        busy = self.agent(AG_BUSY)
        self.assertEqual("working", busy["status"])
        self.assertEqual("claude", busy["provider"])
        self.assertEqual({"name": "mbp", "status": "online"}, busy["runtime"])
        self.assertEqual(T_RUN, busy["task"]["id"])
        self.assertEqual({"id": I_RUN, "identifier": "ACM-7", "title": "Fix the flaky export"}, busy["task"]["issue"])
        self.assertEqual("agent/builder/fix", busy["task"]["branch"])
        self.assertEqual(["Looking at the export job", "▸ Bash npm test"], [l["m"] for l in busy["replay"]])
        self.assertEqual("Bash", busy["tool"])
        self.assertEqual("http://app.local/acme/agents/" + AG_BUSY, busy["url"])
        self.assertEqual("http://app.local/acme/issues/", busy["issue_base"])

        queued = self.agent(AG_QUEUED)
        self.assertEqual("queued", queued["status"])
        self.assertEqual(["ACM-8"], [t["issue"]["identifier"] for t in queued["queue"]])

        idle = self.agent(AG_IDLE)
        self.assertEqual("idle", idle["status"])
        self.assertEqual("failed", idle["last"]["status"])
        self.assertEqual("tests failed", idle["last"]["error"])

        self.assertEqual("offline", self.agent(AG_OFF)["status"])

        ap = snap["autopilots"][0]
        self.assertEqual((AP, "Janitor", ["schedule"]), (ap["id"], ap["agent"], ap["triggers"]))
        self.assertIsNotNone(ap["next_run"])

    def test_token_never_leaves_the_source(self):
        self.src.poll_once()
        self.assertNotIn(TOKEN, json.dumps(self.src.snapshot()))
        for req in self.fake.requests:
            headers = {k.lower(): v for k, v in req["headers"].items()}
            self.assertEqual("Bearer " + TOKEN, headers["authorization"])
            self.assertEqual(WS, headers["x-workspace-id"])

    def test_messages_are_fetched_incrementally(self):
        self.src.poll_once()
        self.fake.data["messages"][T_RUN].append({"seq": 4, "type": "text", "content": "All green"})
        self.src.poll_once()
        self.assertEqual("All green", self.agent(AG_BUSY)["replay"][-1]["m"])
        msg_reqs = [r for r in self.fake.requests if r["path"].endswith("/messages")]
        self.assertEqual(["", "since=3"], [r["query"] for r in msg_reqs])

    def test_issue_titles_are_cached(self):
        self.src.poll_once()
        self.src.poll_once()
        issue_reqs = [r for r in self.fake.requests if r["path"].startswith("/api/issues/")]
        self.assertEqual(3, len(issue_reqs))

    def test_finished_task_drops_its_replay(self):
        self.src.poll_once()
        self.fake.data["tasks"][0]["status"] = "completed"
        self.src.poll_once()
        busy = self.agent(AG_BUSY)
        self.assertEqual("idle", busy["status"])
        self.assertEqual([], busy["replay"])
        self.assertEqual({}, {k: v for k, v in self.src._replay.items() if k == T_RUN})

    def test_server_error_keeps_last_board_and_reports(self):
        self.src.poll_once()
        self.fake.fail[r"^/api/agents$"] = 500
        self.src.poll_once()
        snap = self.src.snapshot()
        self.assertFalse(snap["connected"])
        self.assertIn("GET /api/agents: HTTP 500 · boom", snap["error"])
        self.assertEqual(4, len(snap["agents"]), "the last good agents stay on the board")

    def test_unreachable_server(self):
        self.settings["server_url"] = "http://127.0.0.1:9"
        self.src.poll_once()
        snap = self.src.snapshot()
        self.assertFalse(snap["connected"])
        self.assertTrue(snap["error"].startswith("GET /api/workspaces/:id:"))

    def test_bad_token_is_reported_without_the_token(self):
        self.settings["token"] = "mul_wrong_token"
        self.src.poll_once()
        snap = self.src.snapshot()
        self.assertIn("HTTP 401", snap["error"])
        self.assertNotIn("mul_wrong_token", json.dumps(snap))

    def test_disabled_and_unconfigured(self):
        self.settings = {"enabled": False, "configured": True, "why": None}
        self.src.poll_once()
        self.assertFalse(self.src.snapshot()["enabled"])
        self.settings = {"enabled": True, "configured": False, "why": "no token in the multica profile; run `multica login`"}
        self.src.poll_once()
        snap = self.src.snapshot()
        self.assertTrue(snap["enabled"])
        self.assertIn("multica login", snap["error"])
        with self.assertRaises(multica.MulticaError):
            self.src.cancel_task(T_RUN)

    def test_actions(self):
        self.src.poll_once()
        self.assertEqual({"ok": True}, self.src.cancel_task(T_RUN))
        self.assertEqual({"ok": True, "status": "issue_created"}, self.src.trigger_autopilot(AP))
        self.fake.data["trigger_status"] = "skipped"
        res = self.src.trigger_autopilot(AP)
        self.assertFalse(res["ok"])
        self.assertIn("runtime offline", res["err"])
        res = self.src.queue_task(AG_IDLE, "Tidy up", "Delete merged branches")
        self.assertEqual("ACM-9", res["issue"]["identifier"])
        create = [r for r in self.fake.requests if r["method"] == "POST" and r["path"] == "/api/issues"][0]
        self.assertEqual({"title": "Tidy up", "description": "Delete merged branches", "status": "todo",
                          "assignee_type": "agent", "assignee_id": AG_IDLE}, create["body"])


if __name__ == "__main__":
    unittest.main()
