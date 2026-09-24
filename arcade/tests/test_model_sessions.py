import json
import os
import shutil
import signal
import subprocess
import tempfile
import unittest

import model_sessions

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

REGISTRY_FIXTURE = os.path.join(FIXTURES, "claude-registry.json")
TRANSCRIPT_FIXTURE = os.path.join(FIXTURES, "claude-transcript.jsonl")

FAKE_PID = 555555
SESSION_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PROC_START = "Wed Sep 23 06:29:13 2026"
NOW_MS = 1790168900000


def _fake_ps_sweep(mapping):
    def sweep():
        return dict(mapping)

    return sweep


def _restore_env(name, value):
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


def _pick_non_c_locale():
    preferred = ["ru_RU.UTF-8", "de_DE.UTF-8", "fr_FR.UTF-8", "es_ES.UTF-8", "ja_JP.UTF-8"]
    try:
        out = subprocess.check_output(["locale", "-a"]).decode("utf-8", errors="replace")
    except (OSError, subprocess.CalledProcessError):
        return None
    available = out.split()
    for loc in preferred:
        if loc in available:
            return loc
    return None


def _claude_style_proc_start(pid):
    # The exact command the real Claude binary uses to write procStart.
    out = subprocess.check_output(
        ["ps", "-o", "lstart=", "-p", str(pid)],
        env=dict(os.environ, LC_ALL="C", TZ="UTC"),
    )
    return out.decode("utf-8", errors="replace").strip()


class _HomeTestCase(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="arcade-sessions-test-")
        self.sessions_dir = os.path.join(self.home, "sessions")
        self.projects_dir = os.path.join(self.home, "projects", "-tmp-fake-repo")
        os.makedirs(self.sessions_dir)
        os.makedirs(self.projects_dir)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def _write_registry(self, pid, obj):
        path = os.path.join(self.sessions_dir, "%d.json" % pid)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        return path

    def _write_transcript(self, session_id, lines):
        path = os.path.join(self.projects_dir, session_id + ".jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
        return path

    def _append_transcript(self, session_id, lines):
        path = os.path.join(self.projects_dir, session_id + ".jsonl")
        with open(path, "a", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
        return path

    def _base_registry(self, **overrides):
        obj = {
            "pid": FAKE_PID,
            "sessionId": SESSION_ID,
            "cwd": "/tmp/fake-repo",
            "startedAt": 1790144955082,
            "procStart": PROC_START,
            "name": "fake-session",
            "status": "busy",
            "updatedAt": NOW_MS,
            "statusUpdatedAt": NOW_MS,
            "kind": "interactive",
        }
        obj.update(overrides)
        return obj


class LiveGuardTest(_HomeTestCase):
    def test_matching_proc_start_keeps_session(self):
        self._write_registry(FAKE_PID, self._base_registry())
        model = model_sessions.SessionsModel(
            self.home, ps_sweep=_fake_ps_sweep({FAKE_PID: ("S", PROC_START)})
        )
        snap = model.snapshot(NOW_MS, [])
        self.assertEqual(len(snap["sessions"]), 1)
        self.assertEqual(snap["sessions"][0]["id"], SESSION_ID)

    def test_different_lstart_hides_session_pid_reuse(self):
        self._write_registry(FAKE_PID, self._base_registry())
        model = model_sessions.SessionsModel(
            self.home,
            ps_sweep=_fake_ps_sweep({FAKE_PID: ("S", "Wed Sep 23 09:00:00 2026")}),
        )
        snap = model.snapshot(NOW_MS, [])
        self.assertEqual(snap["sessions"], [])

    def test_missing_pid_hides_session(self):
        self._write_registry(FAKE_PID, self._base_registry())
        model = model_sessions.SessionsModel(self.home, ps_sweep=_fake_ps_sweep({}))
        snap = model.snapshot(NOW_MS, [])
        self.assertEqual(snap["sessions"], [])

    def test_zombie_stat_hides_session(self):
        self._write_registry(FAKE_PID, self._base_registry())
        model = model_sessions.SessionsModel(
            self.home, ps_sweep=_fake_ps_sweep({FAKE_PID: ("Z", PROC_START)})
        )
        snap = model.snapshot(NOW_MS, [])
        self.assertEqual(snap["sessions"], [])


class PsSweepLocaleTest(unittest.TestCase):
    """procStart is always built independently of ps_sweep() itself here
    (LC_ALL=C TZ=UTC ps -o lstart=, the exact command the real Claude binary
    uses), never by calling ps_sweep() under a clean env first -- that would
    let the same bug produce both sides of the comparison and pass anyway.
    """

    def setUp(self):
        self.locale = _pick_non_c_locale()
        if self.locale is None:
            self.skipTest("no non-C locale available (checked `locale -a`)")
        self.old_tz = os.environ.get("TZ")
        self.old_lc_all = os.environ.get("LC_ALL")

    def tearDown(self):
        _restore_env("TZ", self.old_tz)
        _restore_env("LC_ALL", self.old_lc_all)

    def test_ps_sweep_matches_claude_style_proc_start_under_hostile_env(self):
        pid = os.getpid()
        expected = _claude_style_proc_start(pid)

        os.environ["TZ"] = "Asia/Dubai"
        os.environ["LC_ALL"] = self.locale
        entry = model_sessions.ps_sweep().get(pid)

        self.assertIsNotNone(entry)
        self.assertEqual(entry[1], expected)

    def test_live_guard_holds_under_non_utc_tz_and_non_c_locale(self):
        pid = os.getpid()
        proc_start = _claude_style_proc_start(pid)

        home = tempfile.mkdtemp(prefix="arcade-sessions-locale-test-")
        try:
            os.environ["TZ"] = "Asia/Dubai"
            os.environ["LC_ALL"] = self.locale
            sessions_dir = os.path.join(home, "sessions")
            os.makedirs(sessions_dir)
            registry = {
                "pid": pid,
                "sessionId": SESSION_ID,
                "cwd": "/tmp/fake-repo",
                "startedAt": 1790144955082,
                "procStart": proc_start,
                "name": "fake-session",
                "status": "idle",
                "updatedAt": NOW_MS,
                "statusUpdatedAt": NOW_MS,
                "kind": "interactive",
            }
            with open(os.path.join(sessions_dir, "%d.json" % pid), "w", encoding="utf-8") as f:
                json.dump(registry, f)
            model = model_sessions.SessionsModel(home)
            snap = model.snapshot(NOW_MS, [])
            self.assertEqual(len(snap["sessions"]), 1)
        finally:
            shutil.rmtree(home, ignore_errors=True)


class StatusDerivationTest(_HomeTestCase):
    def _snapshot_with_transcript(self, status, status_updated_at, lines):
        self._write_registry(
            FAKE_PID,
            self._base_registry(status=status, statusUpdatedAt=status_updated_at),
        )
        self._write_transcript(SESSION_ID, lines)
        model = model_sessions.SessionsModel(
            self.home, ps_sweep=_fake_ps_sweep({FAKE_PID: ("S", PROC_START)})
        )
        snap = model.snapshot(NOW_MS, [])
        return snap["sessions"][0]

    def test_busy_with_open_tool_use_is_play(self):
        lines = [
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-09-23T06:30:00.000Z",
                    "message": {
                        "id": "m1",
                        "model": "claude-opus-5-5",
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                        "content": [
                            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "x"}}
                        ],
                    },
                }
            )
        ]
        session = self._snapshot_with_transcript("busy", NOW_MS, lines)
        self.assertEqual(session["status"], "play")

    def test_busy_with_two_parallel_tool_uses_both_resolved_is_think(self):
        lines = [
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-09-23T06:30:00.000Z",
                    "message": {
                        "id": "m1",
                        "model": "claude-opus-5-5",
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                        "content": [
                            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "x"}},
                            {"type": "tool_use", "id": "t2", "name": "Read", "input": {"file_path": "y"}},
                        ],
                    },
                }
            ),
            json.dumps(
                {
                    "type": "user",
                    "timestamp": "2026-09-23T06:30:01.000Z",
                    "message": {
                        "content": [
                            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
                            {"type": "tool_result", "tool_use_id": "t2", "content": "ok"},
                        ]
                    },
                }
            ),
        ]
        session = self._snapshot_with_transcript("busy", NOW_MS, lines)
        self.assertEqual(session["status"], "think")

    def test_idle_within_fifteen_min_is_turn(self):
        recent = NOW_MS - 5 * 60 * 1000
        session = self._snapshot_with_transcript("idle", recent, [])
        self.assertEqual(session["status"], "turn")

    def test_idle_older_than_fifteen_min_is_idle(self):
        old = NOW_MS - 20 * 60 * 1000
        session = self._snapshot_with_transcript("idle", old, [])
        self.assertEqual(session["status"], "idle")

    def test_shell_within_fifteen_min_is_turn(self):
        recent = NOW_MS - 3 * 60 * 1000
        session = self._snapshot_with_transcript("shell", recent, [])
        self.assertEqual(session["status"], "turn")

    def test_shell_older_than_fifteen_min_is_idle(self):
        old = NOW_MS - 30 * 60 * 1000
        session = self._snapshot_with_transcript("shell", old, [])
        self.assertEqual(session["status"], "idle")

    def test_unknown_status_passes_through_upper_cased(self):
        session = self._snapshot_with_transcript("weird-status", NOW_MS, [])
        self.assertEqual(session["status"], "WEIRD-STATUS")


class FixtureIntegrationTest(_HomeTestCase):
    def setUp(self):
        super(FixtureIntegrationTest, self).setUp()
        with open(REGISTRY_FIXTURE, "r", encoding="utf-8") as f:
            registry = json.load(f)
        with open(os.path.join(self.sessions_dir, "%d.json" % registry["pid"]), "w", encoding="utf-8") as f:
            json.dump(registry, f)
        shutil.copy(
            TRANSCRIPT_FIXTURE,
            os.path.join(self.projects_dir, registry["sessionId"] + ".jsonl"),
        )
        self.registry = registry
        model = model_sessions.SessionsModel(
            self.home,
            ps_sweep=_fake_ps_sweep({registry["pid"]: ("S", registry["procStart"])}),
        )
        jobs = [
            {"id": "20260923-141502-codex-a1b2", "parent_session": SESSION_ID},
            {"id": "20260923-999999-cursor-zzzz", "parent_session": "someone-else"},
        ]
        self.snap = model.snapshot(NOW_MS, jobs)
        self.assertEqual(len(self.snap["sessions"]), 1)
        self.session = self.snap["sessions"][0]

    def test_status_is_play_because_a_tool_use_is_open(self):
        self.assertEqual(self.session["status"], "play")

    def test_repo_and_cwd_come_from_registry(self):
        self.assertEqual(self.session["cwd"], "/tmp/fake-repo")
        self.assertEqual(self.session["repo"], "fake-repo")

    def test_branch_is_the_last_git_branch_seen(self):
        self.assertEqual(self.session["branch"], "feature/xyz")

    def test_model_is_last_assistant_model(self):
        self.assertEqual(self.session["model"], "claude-opus-5-5")

    def test_title_is_last_ai_title(self):
        self.assertEqual(self.session["title"], "Final title")

    def test_prompt_is_last_last_prompt(self):
        self.assertEqual(self.session["prompt"], "final prompt text")

    def test_tokens_dedupe_message_id_keeping_last_usage(self):
        # msg_1 repeats 3x (100,10,5) (100,40,5) (100,70,5): last usage wins.
        # msg_2 (20,5,0) msg_3 (15,3,0) msg_4 (5,1,0).
        tokens = self.session["tokens"]
        self.assertEqual(tokens["in"], 100 + 20 + 15 + 5)
        self.assertEqual(tokens["out"], 70 + 5 + 3 + 1)
        self.assertEqual(tokens["cache"], 5 + 0 + 0 + 0)

    def test_score_is_in_plus_out(self):
        tokens = self.session["tokens"]
        self.assertEqual(self.session["score"], tokens["in"] + tokens["out"])

    def test_tool_is_the_last_tool_used(self):
        self.assertEqual(self.session["tool"], "Edit")

    def test_replay_has_one_entry_per_tool_use_in_order(self):
        replay = self.session["replay"]
        self.assertEqual(len(replay), 5)
        self.assertEqual(replay[0]["m"], "Bash echo hello")
        self.assertEqual(replay[1]["m"], "Edit src/a.py")
        self.assertEqual(replay[2]["m"], "MultiEdit src/b.py")
        self.assertEqual(replay[3]["m"], "Write src/c.py")
        self.assertEqual(replay[4]["m"], "Edit src/c.py")
        self.assertIsInstance(replay[0]["t"], int)

    def test_files_edit_uses_exact_difflib_counts(self):
        files = {f["n"]: f for f in self.session["files"]}
        self.assertEqual(files["src/a.py"], {"n": "src/a.py", "a": 1, "d": 1})

    def test_files_multiedit_sums_across_edits_for_one_path(self):
        files = {f["n"]: f for f in self.session["files"]}
        self.assertEqual(files["src/b.py"], {"n": "src/b.py", "a": 3, "d": 1})

    def test_files_write_then_edit_keeps_null_d(self):
        files = {f["n"]: f for f in self.session["files"]}
        self.assertEqual(files["src/c.py"], {"n": "src/c.py", "a": 3, "d": None})

    def test_crew_is_jobs_with_matching_parent_session(self):
        self.assertEqual(self.session["crew"], ["20260923-141502-codex-a1b2"])

    def test_bad_line_is_counted_in_errors(self):
        # Source-first wording, aligned with the job model's "kiro log: N
        # unparsable line in <jobid>" style.
        expected = "claude transcript: 1 unparsable line(s) in {}".format(SESSION_ID)
        self.assertIn(expected, self.snap["errors"])


class ReplayCapTest(_HomeTestCase):
    def test_replay_capped_at_fifty(self):
        self._write_registry(FAKE_PID, self._base_registry())
        lines = []
        for i in range(model_sessions.REPLAY_MAX + 10):
            lines.append(
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": "2026-09-23T06:30:00.000Z",
                        "message": {
                            "id": "m%d" % i,
                            "model": "claude-opus-5-5",
                            "usage": {"input_tokens": 1, "output_tokens": 1},
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "t%d" % i,
                                    "name": "Bash",
                                    "input": {"command": "echo %d" % i},
                                }
                            ],
                        },
                    }
                )
            )
        self._write_transcript(SESSION_ID, lines)
        model = model_sessions.SessionsModel(
            self.home, ps_sweep=_fake_ps_sweep({FAKE_PID: ("S", PROC_START)})
        )
        snap = model.snapshot(NOW_MS, [])
        replay = snap["sessions"][0]["replay"]
        self.assertEqual(len(replay), model_sessions.REPLAY_MAX)
        last = model_sessions.REPLAY_MAX + 9
        self.assertTrue(replay[-1]["m"].endswith("echo %d" % last))


class IncrementalTranscriptReadTest(_HomeTestCase):
    def test_read_incrementally_across_two_snapshots(self):
        self._write_registry(FAKE_PID, self._base_registry())
        first_half = [
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-09-23T06:30:00.000Z",
                    "message": {
                        "id": "m1",
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                        "content": [
                            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "one"}}
                        ],
                    },
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-09-23T06:30:01.000Z",
                    "message": {
                        "id": "m2",
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                        "content": [
                            {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "two"}}
                        ],
                    },
                }
            ),
        ]
        second_half = [
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-09-23T06:30:02.000Z",
                    "message": {
                        "id": "m3",
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                        "content": [
                            {"type": "tool_use", "id": "t3", "name": "Bash", "input": {"command": "three"}}
                        ],
                    },
                }
            )
        ]
        self._write_transcript(SESSION_ID, first_half)
        model = model_sessions.SessionsModel(
            self.home, ps_sweep=_fake_ps_sweep({FAKE_PID: ("S", PROC_START)})
        )

        snap1 = model.snapshot(NOW_MS, [])
        replay1 = snap1["sessions"][0]["replay"]
        self.assertEqual(len(replay1), 2)

        # re-polling with no new bytes must not re-process (no duplicates)
        snap1b = model.snapshot(NOW_MS, [])
        self.assertEqual(snap1b["sessions"][0]["replay"], replay1)

        self._append_transcript(SESSION_ID, second_half)
        snap2 = model.snapshot(NOW_MS, [])
        replay2 = snap2["sessions"][0]["replay"]
        self.assertEqual(len(replay2), 3)
        self.assertEqual(replay2[:2], replay1)
        self.assertTrue(replay2[2]["m"].endswith("three"))


class DirectoryMissingTest(unittest.TestCase):
    def test_missing_claude_home_returns_empty_without_crashing(self):
        model = model_sessions.SessionsModel(
            os.path.join(tempfile.gettempdir(), "arcade-nonexistent-home"),
            ps_sweep=_fake_ps_sweep({}),
        )
        snap = model.snapshot(NOW_MS, [])
        self.assertEqual(snap, {"sessions": [], "errors": []})


class MissingStatusTest(_HomeTestCase):
    def test_missing_status_field_gives_null_not_unknown(self):
        registry = self._base_registry()
        del registry["status"]
        self._write_registry(FAKE_PID, registry)
        model = model_sessions.SessionsModel(
            self.home, ps_sweep=_fake_ps_sweep({FAKE_PID: ("S", PROC_START)})
        )
        snap = model.snapshot(NOW_MS, [])
        self.assertIsNone(snap["sessions"][0]["status"])


class SuspendedStatusTest(_HomeTestCase):
    def test_suspended_stat_overrides_busy_with_open_tool_use(self):
        # Without the override this registry (busy + would-be-open tool_use)
        # would classify as "play" forever, even though the process is
        # frozen at the OS level (Ctrl-Z) and will never resume on its own.
        self._write_registry(FAKE_PID, self._base_registry(status="busy"))
        lines = [
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-09-23T06:30:00.000Z",
                    "message": {
                        "id": "m1",
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                        "content": [
                            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "x"}}
                        ],
                    },
                }
            )
        ]
        self._write_transcript(SESSION_ID, lines)
        model = model_sessions.SessionsModel(
            self.home, ps_sweep=_fake_ps_sweep({FAKE_PID: ("T", PROC_START)})
        )
        snap = model.snapshot(NOW_MS, [])
        self.assertEqual(snap["sessions"][0]["status"], "SUSPENDED")

    def test_suspended_stat_with_modifier_suffix_is_still_suspended(self):
        self._write_registry(FAKE_PID, self._base_registry(status="idle"))
        model = model_sessions.SessionsModel(
            self.home, ps_sweep=_fake_ps_sweep({FAKE_PID: ("T+", PROC_START)})
        )
        snap = model.snapshot(NOW_MS, [])
        self.assertEqual(snap["sessions"][0]["status"], "SUSPENDED")


class MissingTranscriptTest(_HomeTestCase):
    def test_missing_transcript_gives_null_tokens_and_score(self):
        self._write_registry(FAKE_PID, self._base_registry())
        # deliberately no transcript file written anywhere under projects/
        model = model_sessions.SessionsModel(
            self.home, ps_sweep=_fake_ps_sweep({FAKE_PID: ("S", PROC_START)})
        )
        snap = model.snapshot(NOW_MS, [])
        session = snap["sessions"][0]
        self.assertIsNone(session["tokens"])
        self.assertIsNone(session["score"])


class RegistryFileErrorTest(_HomeTestCase):
    def test_unparsable_registry_file_reports_error(self):
        path = os.path.join(self.sessions_dir, "%d.json" % FAKE_PID)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not valid json")
        model = model_sessions.SessionsModel(self.home, ps_sweep=_fake_ps_sweep({}))
        snap = model.snapshot(NOW_MS, [])
        self.assertEqual(snap["sessions"], [])
        joined = " ".join(snap["errors"])
        self.assertIn("claude sessions: cannot read", joined)
        self.assertIn(path, joined)

    def test_unreadable_registry_file_reports_error(self):
        path = os.path.join(self.sessions_dir, "%d.json" % FAKE_PID)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._base_registry(), f)
        os.chmod(path, 0o000)
        try:
            if os.access(path, os.R_OK):
                self.skipTest("current user bypasses file permission bits")
            model = model_sessions.SessionsModel(self.home, ps_sweep=_fake_ps_sweep({}))
            snap = model.snapshot(NOW_MS, [])
            self.assertEqual(snap["sessions"], [])
            joined = " ".join(snap["errors"])
            self.assertIn("claude sessions: cannot read", joined)
        finally:
            os.chmod(path, 0o600)


class TranscriptResetTest(_HomeTestCase):
    def test_new_inode_discards_stale_accumulation(self):
        self._write_registry(FAKE_PID, self._base_registry())
        first = [
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-09-23T06:30:00.000Z",
                    "message": {
                        "id": "m1",
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "t1",
                                "name": "Edit",
                                "input": {"file_path": "old/path.py", "old_string": "a", "new_string": "b"},
                            }
                        ],
                    },
                }
            ),
            '{"type": "assistant", "message": BROKEN}',
        ]
        path = self._write_transcript(SESSION_ID, first)
        model = model_sessions.SessionsModel(
            self.home, ps_sweep=_fake_ps_sweep({FAKE_PID: ("S", PROC_START)})
        )

        snap1 = model.snapshot(NOW_MS, [])
        session1 = snap1["sessions"][0]
        self.assertEqual(session1["files"], [{"n": "old/path.py", "a": 1, "d": 1}])
        self.assertEqual(len(session1["replay"]), 1)
        self.assertTrue(any("unparsable" in e for e in snap1["errors"]))

        os.remove(path)
        second = [
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-09-23T06:30:05.000Z",
                    "message": {
                        "id": "m2",
                        "usage": {"input_tokens": 2, "output_tokens": 2},
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "t2",
                                "name": "Write",
                                "input": {"file_path": "new/path.py", "content": "x\ny"},
                            }
                        ],
                    },
                }
            )
        ]
        self._write_transcript(SESSION_ID, second)

        snap2 = model.snapshot(NOW_MS, [])
        session2 = snap2["sessions"][0]
        # old/path.py must be gone, not merged with new/path.py
        self.assertEqual(session2["files"], [{"n": "new/path.py", "a": 2, "d": None}])
        self.assertEqual(len(session2["replay"]), 1)
        self.assertEqual(session2["replay"][0]["m"], "Write new/path.py")
        # the stale bad-line count must not carry over past the reset
        self.assertEqual(snap2["errors"], [])


class IncrementalAccumulationTest(_HomeTestCase):
    def _model(self):
        return model_sessions.SessionsModel(
            self.home, ps_sweep=_fake_ps_sweep({FAKE_PID: ("S", PROC_START)})
        )

    def test_tokens_and_files_accumulate_across_two_reads(self):
        self._write_registry(FAKE_PID, self._base_registry())
        first = [
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-09-23T06:30:00.000Z",
                    "message": {
                        "id": "m1",
                        "usage": {"input_tokens": 10, "output_tokens": 2},
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "t1",
                                "name": "Edit",
                                "input": {"file_path": "a.py", "old_string": "a", "new_string": "b"},
                            }
                        ],
                    },
                }
            )
        ]
        self._write_transcript(SESSION_ID, first)
        model = self._model()

        snap1 = model.snapshot(NOW_MS, [])
        session1 = snap1["sessions"][0]
        self.assertEqual(session1["tokens"], {"in": 10, "out": 2, "cache": 0})
        self.assertEqual(session1["files"], [{"n": "a.py", "a": 1, "d": 1}])

        second = [
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-09-23T06:30:05.000Z",
                    "message": {
                        "id": "m2",
                        "usage": {"input_tokens": 5, "output_tokens": 1},
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "t2",
                                "name": "Write",
                                "input": {"file_path": "b.py", "content": "x\ny\nz"},
                            }
                        ],
                    },
                }
            )
        ]
        self._append_transcript(SESSION_ID, second)
        snap2 = model.snapshot(NOW_MS, [])
        session2 = snap2["sessions"][0]
        self.assertEqual(session2["tokens"], {"in": 15, "out": 3, "cache": 0})
        files = {f["n"]: f for f in session2["files"]}
        self.assertEqual(files["a.py"], {"n": "a.py", "a": 1, "d": 1})
        self.assertEqual(files["b.py"], {"n": "b.py", "a": 3, "d": None})

    def test_partial_line_held_back_until_newline_arrives(self):
        self._write_registry(FAKE_PID, self._base_registry())
        full_line = json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-09-23T06:30:00.000Z",
                "message": {
                    "id": "m1",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                    "content": [
                        {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "echo hi"}}
                    ],
                },
            }
        )
        path = os.path.join(self.projects_dir, SESSION_ID + ".jsonl")
        split = len(full_line) // 2
        with open(path, "w", encoding="utf-8") as f:
            f.write(full_line[:split])

        model = self._model()
        snap1 = model.snapshot(NOW_MS, [])
        session1 = snap1["sessions"][0]
        self.assertEqual(session1["replay"], [])
        self.assertEqual(session1["tokens"], {"in": 0, "out": 0, "cache": 0})

        with open(path, "a", encoding="utf-8") as f:
            f.write(full_line[split:] + "\n")
        snap2 = model.snapshot(NOW_MS, [])
        session2 = snap2["sessions"][0]
        self.assertEqual(len(session2["replay"]), 1)
        self.assertEqual(session2["tokens"], {"in": 1, "out": 1, "cache": 0})

    def test_message_id_spanning_reads_keeps_last_usage_not_sum(self):
        self._write_registry(FAKE_PID, self._base_registry())
        first = [
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-09-23T06:30:00.000Z",
                    "message": {
                        "id": "m1",
                        "usage": {"input_tokens": 100, "output_tokens": 10},
                        "content": [{"type": "text", "text": "partial"}],
                    },
                }
            )
        ]
        self._write_transcript(SESSION_ID, first)
        model = self._model()
        snap1 = model.snapshot(NOW_MS, [])
        self.assertEqual(snap1["sessions"][0]["tokens"], {"in": 100, "out": 10, "cache": 0})

        second = [
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-09-23T06:30:05.000Z",
                    "message": {
                        "id": "m1",
                        "usage": {"input_tokens": 100, "output_tokens": 55},
                        "content": [{"type": "text", "text": "grew"}],
                    },
                }
            )
        ]
        self._append_transcript(SESSION_ID, second)
        snap2 = model.snapshot(NOW_MS, [])
        # last-seen usage for m1 wins (65 total), not summed across both
        # sightings of the same message.id (which would wrongly give 165).
        self.assertEqual(snap2["sessions"][0]["tokens"], {"in": 100, "out": 55, "cache": 0})


if __name__ == "__main__":
    unittest.main()


class TitleSourceTest(_HomeTestCase):
    def _title(self, **reg):
        self._write_registry(FAKE_PID, self._base_registry(**reg))
        self._append_transcript(SESSION_ID, [json.dumps({"type": "ai-title", "aiTitle": "AI title"})])
        model = model_sessions.SessionsModel(
            self.home, ps_sweep=_fake_ps_sweep({FAKE_PID: ("S", PROC_START)})
        )
        return model.snapshot(NOW_MS, [])["sessions"][0]["title"]

    def test_user_set_name_beats_ai_title(self):
        self.assertEqual(self._title(name="CHAT-BUBBLES", nameSource="user"), "CHAT-BUBBLES")

    def test_derived_name_loses_to_ai_title(self):
        self.assertEqual(self._title(name="repo-0e", nameSource="derived"), "AI title")

    def test_missing_name_source_keeps_ai_title(self):
        self.assertEqual(self._title(name="repo-0e"), "AI title")


class EndSuspendedTest(_HomeTestCase):
    OTHER_PID = 666666

    def _model(self, sweeps):
        # One ps map per sweep call; the last one repeats.
        calls = {"n": 0}

        def sweep():
            i = min(calls["n"], len(sweeps) - 1)
            calls["n"] += 1
            return dict(sweeps[i])

        self.signals = []
        model = model_sessions.SessionsModel(self.home, ps_sweep=sweep)
        return model

    def _end(self, model):
        return model.end_suspended(
            SESSION_ID, kill=lambda pid, sig: self.signals.append((pid, sig)), sleep=lambda s: None
        )

    def test_suspended_process_gets_term_then_cont_and_ends(self):
        self._write_registry(FAKE_PID, self._base_registry())
        model = self._model([{FAKE_PID: ("T", PROC_START)}, {}])
        ended, survivors = self._end(model)
        self.assertEqual(self.signals, [(FAKE_PID, signal.SIGTERM), (FAKE_PID, signal.SIGCONT)])
        self.assertEqual((ended, survivors), ([FAKE_PID], []))

    def test_running_process_is_never_signalled(self):
        self._write_registry(FAKE_PID, self._base_registry())
        model = self._model([{FAKE_PID: ("S", PROC_START)}])
        self.assertEqual(self._end(model), ([], []))
        self.assertEqual(self.signals, [])

    def test_reused_pid_is_never_signalled(self):
        self._write_registry(FAKE_PID, self._base_registry())
        model = self._model([{FAKE_PID: ("T", "Thu Sep 24 01:00:00 2026")}])
        self.assertEqual(self._end(model), ([], []))
        self.assertEqual(self.signals, [])

    def test_only_the_suspended_copy_of_a_resumed_session_is_ended(self):
        self._write_registry(FAKE_PID, self._base_registry())
        self._write_registry(self.OTHER_PID, self._base_registry(pid=self.OTHER_PID))
        model = self._model([{FAKE_PID: ("T", PROC_START), self.OTHER_PID: ("S", PROC_START)},
                             {self.OTHER_PID: ("S", PROC_START)}])
        ended, survivors = self._end(model)
        self.assertEqual([p for p, _ in self.signals], [FAKE_PID, FAKE_PID])
        self.assertEqual((ended, survivors), ([FAKE_PID], []))

    def test_process_that_ignores_term_is_reported_not_killed(self):
        self._write_registry(FAKE_PID, self._base_registry())
        model = self._model([{FAKE_PID: ("T", PROC_START)}, {FAKE_PID: ("S", PROC_START)}])
        ended, survivors = self._end(model)
        self.assertEqual((ended, survivors), ([], [FAKE_PID]))
        self.assertNotIn(signal.SIGKILL, [s for _, s in self.signals])
