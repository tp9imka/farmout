import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import logs
import progress

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures")
PROGRESS_PY = os.path.join(os.path.dirname(HERE), "progress.py")


class FixtureScanTest(unittest.TestCase):
    """Real captured runs: every tool call closes, and progress is counted."""

    def test_each_fixture_counts_progress_and_closes_its_tools(self):
        for cli in ("codex", "kiro", "copilot", "cursor"):
            got = progress.scan(cli, os.path.join(FIXTURES, cli + ".log"))
            self.assertNotIn("unknown", got, cli)
            self.assertGreater(got["events"], 0, cli)
            self.assertEqual(got["open_tools"], 0, cli)

    def test_kiro_login_spinner_and_metadata_are_not_progress(self):
        # The fixture carries ~50 OAuth spinner frames and four metadata
        # snapshots; only the tool call, its two updates and the message count.
        got = progress.scan("kiro", os.path.join(FIXTURES, "kiro.log"))
        self.assertEqual(got["events"], 4)


class PredicateTest(unittest.TestCase):
    def _feed(self, cli, *objs):
        p = progress.Progress(cli)
        for o in objs:
            p.feed(o if isinstance(o, str) else json.dumps(o))
        return p

    def test_open_tool_stays_open_until_completed(self):
        p = self._feed("codex", {"type": "item.started", "item": {"id": "a", "type": "command_execution"}})
        self.assertEqual(p.open_tools, 1)
        p.feed(json.dumps({"type": "item.completed", "item": {"id": "a", "type": "command_execution"}}))
        self.assertEqual(p.open_tools, 0)

    def test_parallel_tools_close_independently(self):
        p = self._feed("copilot",
                       {"type": "tool.execution_start", "data": {"toolCallId": "x"}},
                       {"type": "tool.execution_start", "data": {"toolCallId": "y"}},
                       {"type": "tool.execution_complete", "data": {"toolCallId": "x"}})
        self.assertEqual((p.events, p.open_tools), (3, 1))

    def test_kiro_in_progress_update_keeps_the_tool_open(self):
        p = self._feed("kiro",
                       {"type": "sessionUpdate", "data": {"update": {"sessionUpdate": "tool_call", "toolCallId": "t"}}},
                       {"type": "sessionUpdate", "data": {"update": {"sessionUpdate": "tool_call_update", "toolCallId": "t"}}})
        self.assertEqual(p.open_tools, 1)
        p.feed(json.dumps({"type": "sessionUpdate", "data": {"update": {
            "sessionUpdate": "tool_call_update", "toolCallId": "t", "status": "failed"}}}))
        self.assertEqual(p.open_tools, 0)

    def test_setup_and_bookkeeping_events_are_not_progress(self):
        self.assertEqual(self._feed("codex", {"type": "thread.started"}).events, 0)
        self.assertEqual(self._feed("copilot", {"type": "session.usage_checkpoint"}, {"type": "assistant.idle"}).events, 0)
        self.assertEqual(self._feed("cursor", {"type": "system"}, {"type": "user"}).events, 0)
        self.assertEqual(self._feed("kiro", {"type": "metadata", "data": {"contextUsagePercentage": 3}}).events, 0)

    def test_garbage_is_ignored(self):
        self.assertEqual(self._feed("codex", "", "Reading additional input from stdin...", "{not json", "[1,2]").events, 0)


class ScanEdgesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="progress-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unknown_cli_and_missing_log_read_unknown(self):
        self.assertIn("unknown", progress.scan("unknowncli", os.path.join(FIXTURES, "codex.log")))
        self.assertIn("unknown", progress.scan("codex", os.path.join(self.tmp, "absent")))

    def test_oversized_log_reads_unknown_not_a_partial_count(self):
        path = os.path.join(self.tmp, "log")
        with open(path, "wb") as f:
            f.truncate(progress.MAX_SCAN_BYTES + 1)
        self.assertIn("unknown", progress.scan("codex", path))

    def test_command_line_prints_json(self):
        out = subprocess.check_output([sys.executable, PROGRESS_PY, "kiro", os.path.join(FIXTURES, "kiro.log")])
        self.assertEqual(json.loads(out.decode()), {"events": 4, "open_tools": 0})


class ReaderCarriageReturnTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="reader-cr-test-")
        self.path = os.path.join(self.tmp, "log")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _append(self, data):
        with open(self.path, "ab") as f:
            f.write(data)

    def test_bare_cr_frames_do_not_pile_up_in_partial(self):
        reader = logs.IncrementalReader()
        self._append(b"Opening browser...\r" * 1000)
        reader.read_new(self.path)
        self.assertEqual(reader._partial, b"")

    def test_cr_split_across_reads_and_crlf_yield_clean_lines(self):
        reader = logs.IncrementalReader()
        self._append(b'spin\r{"type":"turn.started"}\r')
        first = reader.read_new(self.path)
        self._append(b'\n{"type":"turn.completed"}\r\n')
        second = reader.read_new(self.path)
        self.assertEqual(first + second, ["spin", '{"type":"turn.started"}', '{"type":"turn.completed"}'])


if __name__ == "__main__":
    unittest.main()
