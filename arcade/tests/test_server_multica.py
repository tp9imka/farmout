import http.client
import json
import os
import shutil
import tempfile
import threading
import time
import unittest

import server
from fake_multica import AG_BUSY, AG_IDLE, AP, T_RUN, TOKEN, WS, FakeMultica

FAKE_FARMOUT = os.path.join(os.path.dirname(__file__), "fake-farmout")


class MulticaServerTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeMultica()
        self.tmp = tempfile.mkdtemp(prefix="arcade-mc-server-")
        self.mc_dir = os.path.join(self.tmp, "multica")
        os.makedirs(self.mc_dir)
        with open(os.path.join(self.mc_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"server_url": self.fake.url, "app_url": "http://app.local", "workspace_id": WS, "token": TOKEN}, f)
        for sub in ("farmout_home/jobs", "claude_home/sessions", "claude_home/projects", "static"):
            os.makedirs(os.path.join(self.tmp, sub))
        self.config_path = os.path.join(self.tmp, "config.json")
        self.token = "test-token"
        self.srv = server.build_server(
            0, self.token, farmout_bin=FAKE_FARMOUT, farmout_home=os.path.join(self.tmp, "farmout_home"),
            claude_home=os.path.join(self.tmp, "claude_home"), config_path=self.config_path,
            static_dir=os.path.join(self.tmp, "static"), state_wait_s=10.0,
            multica_config_dir=self.mc_dir, multica_poll_s=0.05,
        )
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever)
        self.thread.daemon = True
        self.thread.start()

    def tearDown(self):
        self.srv.shutdown()
        self.thread.join(timeout=5)
        self.srv.server_close()
        self.fake.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"X-Arcade-Token": self.token}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        try:
            conn.request(method, path, body=data, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
        finally:
            conn.close()
        return resp.status, (json.loads(raw.decode("utf-8")) if raw else None)

    def _state_with_agents(self):
        deadline = time.time() + 5
        while time.time() < deadline:
            status, state = self._request("GET", "/api/state")
            if status == 200 and state["multica"]["connected"]:
                return state
            time.sleep(0.05)
        self.fail("multica never connected")

    def _write_config(self, cfg):
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f)

    def test_state_carries_agents_and_quick_actions(self):
        state = self._state_with_agents()
        mc = state["multica"]
        self.assertEqual("Acme", mc["workspace"]["name"])
        self.assertEqual(["CHECK MY PRS", "CLEANUP & MERGE"], [q["label"] for q in mc["quick_actions"]])
        self.assertNotIn(TOKEN, json.dumps(state))
        self.assertEqual(1, state["hud"]["live"], "a working agent counts as in play")
        self.assertEqual(4, state["hud"]["shown"])

    def test_queue_quick_action(self):
        self._state_with_agents()
        status, body = self._request("POST", "/api/multica/agents/{}/queue".format(AG_IDLE), {"quick": 1})
        self.assertEqual(200, status, body)
        self.assertTrue(body["ok"])
        create = [r for r in self.fake.requests if r["method"] == "POST" and r["path"] == "/api/issues"][-1]
        self.assertEqual("Clean up and merge ready pull requests", create["body"]["title"])
        self.assertIn("approved, green", create["body"]["description"])
        self.assertEqual(AG_IDLE, create["body"]["assignee_id"])

    def test_queue_custom_task(self):
        self._state_with_agents()
        status, body = self._request("POST", "/api/multica/agents/{}/queue".format(AG_BUSY),
                                     {"title": "  Bump deps  ", "prompt": "minor versions only"})
        self.assertEqual(200, status, body)
        create = [r for r in self.fake.requests if r["path"] == "/api/issues"][-1]
        self.assertEqual(("Bump deps", "minor versions only"), (create["body"]["title"], create["body"]["description"]))

    def test_queue_validation(self):
        self._state_with_agents()
        unknown = "aaaaaaaa-0000-4000-8000-0000000000ff"
        cases = [
            (AG_IDLE, {"quick": 7}, "unknown quick action"),
            (AG_IDLE, {"quick": True}, "unknown quick action"),
            (AG_IDLE, {"title": ""}, "title must be"),
            (AG_IDLE, {"title": "x" * 201}, "title must be"),
            (unknown, {"title": "x"}, "unknown agent"),
        ]
        for agent, body, err in cases:
            status, resp = self._request("POST", "/api/multica/agents/{}/queue".format(agent), body)
            self.assertEqual(400, status, (body, resp))
            self.assertIn(err, resp["err"])
        self.assertEqual([], [r for r in self.fake.requests if r["path"] == "/api/issues"])

    def test_quick_action_limited_to_named_agents(self):
        self._write_config({"multica": {"quick_actions": [
            {"label": "NIGHTLY", "title": "Nightly sweep", "prompt": "sweep", "agents": ["janitor"]}]}})
        self._state_with_agents()
        status, resp = self._request("POST", "/api/multica/agents/{}/queue".format(AG_BUSY), {"quick": 0})
        self.assertEqual(400, status)
        self.assertIn("not for this agent", resp["err"])
        status, resp = self._request("POST", "/api/multica/agents/{}/queue".format(AG_IDLE), {"quick": 0})
        self.assertEqual(200, status, resp)

    def test_cancel_and_trigger(self):
        self._state_with_agents()
        status, body = self._request("POST", "/api/multica/tasks/{}/cancel".format(T_RUN))
        self.assertEqual((200, True), (status, body["ok"]))
        status, body = self._request("POST", "/api/multica/autopilots/{}/trigger".format(AP))
        self.assertEqual((200, True), (status, body["ok"]))
        self.fake.data["trigger_status"] = "skipped"
        status, body = self._request("POST", "/api/multica/autopilots/{}/trigger".format(AP))
        self.assertEqual(200, status)
        self.assertFalse(body["ok"])
        self.assertIn("SKIPPED", body["err"])

    def test_upstream_failure_is_502(self):
        self._state_with_agents()
        self.fake.fail[r"/cancel$"] = 403
        status, body = self._request("POST", "/api/multica/tasks/{}/cancel".format(T_RUN))
        self.assertEqual(502, status)
        self.assertIn("HTTP 403", body["err"])

    def test_bad_routes(self):
        self.assertEqual(400, self._request("POST", "/api/multica/tasks/not-a-uuid/cancel")[0])
        self.assertEqual(404, self._request("POST", "/api/multica/tasks/{}/trigger".format(T_RUN))[0])
        self.assertEqual(404, self._request("POST", "/api/multica/agents/{}/cancel".format(AG_IDLE))[0])

    def test_disabled_by_config(self):
        self._state_with_agents()
        self._write_config({"multica": {"enabled": False}})
        deadline = time.time() + 5
        while time.time() < deadline:
            _, state = self._request("GET", "/api/state")
            if not state["multica"]["enabled"]:
                break
            time.sleep(0.05)
        self.assertFalse(state["multica"]["enabled"])
        self.assertEqual([], state["multica"]["agents"])
        status, body = self._request("POST", "/api/multica/tasks/{}/cancel".format(T_RUN))
        self.assertEqual(502, status)
        self.assertIn("not connected", body["err"])


if __name__ == "__main__":
    unittest.main()
