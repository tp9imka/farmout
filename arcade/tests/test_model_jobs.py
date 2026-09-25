import datetime
import calendar
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

import model_jobs

NOW_MS = None  # set below, after _epoch_ms is defined


def _iso(y, mo, d, h, mi, s):
    return "%04d-%02d-%02dT%02d:%02d:%02dZ" % (y, mo, d, h, mi, s)


def _epoch_ms(y, mo, d, h, mi, s):
    return calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0)) * 1000


NOW_MS = _epoch_ms(2026, 9, 23, 15, 0, 0)


def _base_meta(job_id, **overrides):
    # Mirrors bin/farmout's meta_write literal exactly. New spec fields
    # (kind, title, model, effort, route, source_branch, parent_session,
    # parent_pid) are deliberately absent here, as they are for real old
    # jobs -- tests add them explicitly where needed.
    meta = {
        "id": job_id,
        "cli": "codex",
        "mode": "read",
        "repo": None,
        "base": None,
        "branch": None,
        "worktree": None,
        "pid": None,
        "pgid": None,
        "sup_pid": None,
        "started": _iso(2026, 9, 23, 14, 0, 0),
        "ended": None,
        "timeout_s": 1800,
        "status": "running",
        "exit_code": None,
        "untracked": 0,
        "survivors": 0,
        "read_job_modified": False,
        "land": None,
        "error": None,
    }
    meta.update(overrides)
    return meta


def _write_job(home, job_id, meta=None, brief=None, log_lines=None, diff_patch=None):
    job_dir = os.path.join(home, "jobs", job_id)
    os.makedirs(job_dir, exist_ok=True)
    if meta is not None:
        with open(os.path.join(job_dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f)
    if brief is not None:
        with open(os.path.join(job_dir, "brief.md"), "w", encoding="utf-8") as f:
            f.write(brief)
    if log_lines is not None:
        with open(os.path.join(job_dir, "log"), "w", encoding="utf-8") as f:
            for line in log_lines:
                f.write(line + "\n")
    if diff_patch is not None:
        mode = "wb" if isinstance(diff_patch, bytes) else "w"
        with open(os.path.join(job_dir, "diff.patch"), mode) as f:
            f.write(diff_patch)
    return job_dir


class ModelJobsTestCase(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="arcade-jobs-test-")

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)


class StatusLostTest(ModelJobsTestCase):
    def test_running_with_dead_sup_pid_is_lost(self):
        meta = _base_meta("j1", status="running", sup_pid=999999)
        _write_job(self.home, "j1", meta=meta, log_lines=[])
        model = model_jobs.JobsModel(self.home, pid_alive=lambda pid: False)
        rec = model.snapshot(NOW_MS, stall_min=10)["jobs"][0]
        self.assertEqual(rec["status"], "lost")
        self.assertEqual(rec["pose"], "lost")

    def test_running_with_alive_sup_pid_stays_running(self):
        meta = _base_meta("j1", status="running", sup_pid=123, started=_iso(2026, 9, 23, 14, 59, 0))
        _write_job(self.home, "j1", meta=meta, log_lines=[])
        model = model_jobs.JobsModel(self.home, pid_alive=lambda pid: True)
        rec = model.snapshot(NOW_MS, stall_min=10)["jobs"][0]
        self.assertEqual(rec["status"], "running")
        self.assertEqual(rec["pose"], "play")

    def test_running_missing_sup_pid_is_lost_without_consulting_seam(self):
        meta = _base_meta("j1", status="running", sup_pid=None)
        _write_job(self.home, "j1", meta=meta, log_lines=[])
        model = model_jobs.JobsModel(self.home, pid_alive=lambda pid: True)
        rec = model.snapshot(NOW_MS, stall_min=10)["jobs"][0]
        self.assertEqual(rec["status"], "lost")


class DefaultPidAliveTest(unittest.TestCase):
    def test_self_pid_is_alive(self):
        self.assertTrue(model_jobs.default_pid_alive(os.getpid()))

    def test_terminated_child_pid_is_dead(self):
        proc = subprocess.Popen(["true"])
        proc.wait()
        self.assertFalse(model_jobs.default_pid_alive(proc.pid))


def _ms_to_iso(ms):
    return datetime.datetime.utcfromtimestamp(ms / 1000.0).strftime("%Y-%m-%dT%H:%M:%SZ")


class PoseStallTest(ModelJobsTestCase):
    MIN = 60 * 1000
    TURN = '{"type":"turn.started"}'
    TOOL_OPEN = '{"type":"item.started","item":{"id":"i1","type":"command_execution","command":"make test"}}'
    TOOL_DONE = '{"type":"item.completed","item":{"id":"i1","type":"command_execution"}}'

    def _job(self, started_min_ago, log_lines, cli="codex"):
        meta = _base_meta("j1", cli=cli, status="running", sup_pid=123,
                          started=_ms_to_iso(NOW_MS - started_min_ago * self.MIN))
        self.job_dir = _write_job(self.home, "j1", meta=meta, log_lines=log_lines)
        return model_jobs.JobsModel(self.home, pid_alive=lambda pid: True)

    def _pose(self, model, at_ms):
        return model.snapshot(at_ms, stall_min=10)["jobs"][0]["pose"]

    def _append(self, text):
        with open(os.path.join(self.job_dir, "log"), "a", encoding="utf-8") as f:
            f.write(text + "\n")

    def test_no_progress_since_start_is_paused(self):
        self.assertEqual(self._pose(self._job(20, ["not json"]), NOW_MS), "pause")

    def test_no_progress_but_just_started_is_playing(self):
        self.assertEqual(self._pose(self._job(1, []), NOW_MS), "play")

    def test_login_spinner_keeps_the_log_moving_but_is_still_a_stall(self):
        frames = "\r".join("Opening browser... | Press (^) + C to cancel" for _ in range(50))
        model = self._job(20, [frames])
        self.assertEqual(self._pose(model, NOW_MS), "pause")

    def test_silence_after_progress_pauses_only_after_stall_min(self):
        model = self._job(1, [self.TURN])
        self.assertEqual(self._pose(model, NOW_MS), "play")
        self.assertEqual(self._pose(model, NOW_MS + 9 * self.MIN), "play")
        self.assertEqual(self._pose(model, NOW_MS + 11 * self.MIN), "pause")

    def test_a_new_event_restarts_the_clock(self):
        model = self._job(1, [self.TURN])
        self._pose(model, NOW_MS)
        self._append(self.TOOL_OPEN)
        self._append(self.TOOL_DONE)
        self.assertEqual(self._pose(model, NOW_MS + 8 * self.MIN), "play")
        self.assertEqual(self._pose(model, NOW_MS + 16 * self.MIN), "play")
        self.assertEqual(self._pose(model, NOW_MS + 19 * self.MIN), "pause")

    def test_a_silent_open_tool_is_a_long_tool_not_a_stall(self):
        model = self._job(1, [self.TURN, self.TOOL_OPEN])
        self._pose(model, NOW_MS)
        self.assertEqual(self._pose(model, NOW_MS + 30 * self.MIN), "play")

    def test_cli_without_a_parser_is_never_paused(self):
        self.assertEqual(self._pose(self._job(60, [], cli="unknowncli"), NOW_MS), "play")


class PoseFinishedTest(ModelJobsTestCase):
    def _finished(self, job_id, mode, status, land=None):
        meta = _base_meta(job_id, mode=mode, status=status, land=land,
                           ended=_iso(2026, 9, 23, 14, 30, 0))
        _write_job(self.home, job_id, meta=meta, log_lines=[])

    def _only_record(self, snap):
        recs = snap["jobs"] + snap["hof"]
        self.assertEqual(len(recs), 1)
        return recs[0]

    def test_write_ok_unlanded_is_ready(self):
        self._finished("j1", "write", "ok", land=None)
        model = model_jobs.JobsModel(self.home)
        rec = self._only_record(model.snapshot(NOW_MS, stall_min=10))
        self.assertEqual(rec["pose"], "ready")

    def test_write_ok_landed_is_landed(self):
        self._finished("j1", "write", "ok", land="landed")
        model = model_jobs.JobsModel(self.home)
        rec = self._only_record(model.snapshot(NOW_MS, stall_min=10))
        self.assertEqual(rec["pose"], "landed")

    def test_write_ok_conflict_is_conflict(self):
        self._finished("j1", "write", "ok", land="conflict")
        model = model_jobs.JobsModel(self.home)
        rec = self._only_record(model.snapshot(NOW_MS, stall_min=10))
        self.assertEqual(rec["pose"], "conflict")

    def test_write_ok_discarded_is_discarded(self):
        self._finished("j1", "write", "ok", land="discarded")
        model = model_jobs.JobsModel(self.home)
        rec = self._only_record(model.snapshot(NOW_MS, stall_min=10))
        self.assertEqual(rec["pose"], "discarded")

    def test_read_ok_is_clear(self):
        self._finished("j1", "read", "ok")
        model = model_jobs.JobsModel(self.home)
        rec = self._only_record(model.snapshot(NOW_MS, stall_min=10))
        self.assertEqual(rec["pose"], "clear")


class PoseOverTest(ModelJobsTestCase):
    def test_each_finished_status_is_over(self):
        statuses = ["empty", "failed", "rate-limited", "timeout", "killed"]
        for i, status in enumerate(statuses):
            meta = _base_meta("j%d" % i, mode="read", status=status,
                               ended=_iso(2026, 9, 23, 14, 30, 0))
            _write_job(self.home, "j%d" % i, meta=meta, log_lines=[])
        model = model_jobs.JobsModel(self.home)
        snap = model.snapshot(NOW_MS, stall_min=10)
        self.assertEqual(len(snap["jobs"]), 0)
        self.assertEqual(len(snap["hof"]), len(statuses))
        for rec in snap["hof"]:
            self.assertEqual(rec["pose"], "over")

    def test_write_failed_job_is_over_not_land_refined(self):
        meta = _base_meta("jf", mode="write", status="failed", land=None,
                           ended=_iso(2026, 9, 23, 14, 30, 0))
        _write_job(self.home, "jf", meta=meta, log_lines=[])
        model = model_jobs.JobsModel(self.home)
        snap = model.snapshot(NOW_MS, stall_min=10)
        recs = snap["jobs"] + snap["hof"]
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["pose"], "over")


class TitleAndBriefTest(ModelJobsTestCase):
    def test_title_present_in_meta_is_used_verbatim(self):
        meta = _base_meta("j1", title="Explicit title")
        _write_job(self.home, "j1", meta=meta,
                   brief="# Heading\n\nSome brief paragraph.\n", log_lines=[])
        model = model_jobs.JobsModel(self.home)
        rec = model.snapshot(NOW_MS, stall_min=10)["jobs"][0]
        self.assertEqual(rec["title"], "Explicit title")

    def test_title_falls_back_to_first_sentence_of_brief(self):
        meta = _base_meta("j1")
        brief = "Fix the frontmatter parser to handle empty files gracefully. Extra detail follows.\n"
        _write_job(self.home, "j1", meta=meta, brief=brief, log_lines=[])
        model = model_jobs.JobsModel(self.home)
        rec = model.snapshot(NOW_MS, stall_min=10)["jobs"][0]
        self.assertEqual(rec["title"],
                          "Fix the frontmatter parser to handle empty files gracefully.")

    def test_title_fallback_is_capped_at_80_chars(self):
        meta = _base_meta("j1")
        long_line = "x" * 200
        _write_job(self.home, "j1", meta=meta, brief=long_line + "\n", log_lines=[])
        model = model_jobs.JobsModel(self.home)
        rec = model.snapshot(NOW_MS, stall_min=10)["jobs"][0]
        self.assertEqual(len(rec["title"]), 80)

    def test_brief_is_first_prose_paragraph_skipping_heading(self):
        meta = _base_meta("j1")
        text = ("# Heading\n\nFirst paragraph line one.\nFirst paragraph line two.\n\n"
                "Second paragraph excluded.\n")
        _write_job(self.home, "j1", meta=meta, brief=text, log_lines=[])
        model = model_jobs.JobsModel(self.home)
        rec = model.snapshot(NOW_MS, stall_min=10)["jobs"][0]
        self.assertEqual(rec["brief"], "First paragraph line one. First paragraph line two.")

    def test_no_title_and_no_brief_is_null(self):
        meta = _base_meta("j1")
        _write_job(self.home, "j1", meta=meta, log_lines=[])
        model = model_jobs.JobsModel(self.home)
        rec = model.snapshot(NOW_MS, stall_min=10)["jobs"][0]
        self.assertIsNone(rec["title"])
        self.assertIsNone(rec["brief"])


class ElapsedAndTimesTest(ModelJobsTestCase):
    def test_running_job_elapsed_uses_now(self):
        meta = _base_meta("j1", started=_iso(2026, 9, 23, 14, 0, 0),
                           status="running", sup_pid=123)
        _write_job(self.home, "j1", meta=meta, log_lines=[])
        model = model_jobs.JobsModel(self.home, pid_alive=lambda pid: True)
        now = _epoch_ms(2026, 9, 23, 14, 10, 0)
        rec = model.snapshot(now, stall_min=60)["jobs"][0]
        self.assertEqual(rec["elapsed_s"], 600)
        self.assertEqual(rec["started"], _epoch_ms(2026, 9, 23, 14, 0, 0))
        self.assertIsNone(rec["ended"])

    def test_finished_job_elapsed_uses_ended_not_now(self):
        meta = _base_meta("j1", mode="read",
                           started=_iso(2026, 9, 23, 14, 0, 0),
                           ended=_iso(2026, 9, 23, 14, 5, 0), status="ok")
        _write_job(self.home, "j1", meta=meta, log_lines=[])
        model = model_jobs.JobsModel(self.home)
        now = _epoch_ms(2026, 9, 23, 15, 0, 0)
        rec = model.snapshot(now, stall_min=10)["hof"][0]
        self.assertEqual(rec["elapsed_s"], 300)
        self.assertEqual(rec["ended"], _epoch_ms(2026, 9, 23, 14, 5, 0))


class MissingMetaTest(ModelJobsTestCase):
    def test_dir_without_meta_json_is_skipped(self):
        os.makedirs(os.path.join(self.home, "jobs", "no-meta"))
        meta = _base_meta("has-meta", status="ok", mode="read",
                           ended=_iso(2026, 9, 23, 14, 5, 0))
        _write_job(self.home, "has-meta", meta=meta, log_lines=[])
        model = model_jobs.JobsModel(self.home)
        snap = model.snapshot(NOW_MS, stall_min=10)
        ids = [r["id"] for r in snap["jobs"] + snap["hof"]]
        self.assertEqual(ids, ["has-meta"])
        self.assertEqual(snap["errors"], [])


class AdmissionTransientTest(ModelJobsTestCase):
    def _ids(self, snap):
        return [r["id"] for r in snap["jobs"] + snap["hof"]]

    def test_unadmitted_job_is_skipped_old_meta_without_field_stays(self):
        _write_job(self.home, "claimed", meta=_base_meta("claimed", admitted=False, sup_pid=1))
        _write_job(self.home, "admitted", meta=_base_meta("admitted", admitted=True, sup_pid=1))
        _write_job(self.home, "old", meta=_base_meta("old", sup_pid=1))
        model = model_jobs.JobsModel(self.home, pid_alive=lambda pid: True)
        snap = model.snapshot(NOW_MS, stall_min=10)
        self.assertEqual(sorted(self._ids(snap)), ["admitted", "old"])
        self.assertEqual(snap["errors"], [])

    def test_meta_vanishing_mid_read_is_skipped_silently(self):
        job_dir = _write_job(self.home, "rejected", meta=_base_meta("rejected", admitted=False))
        real_open = open

        def racing_open(path, *args, **kwargs):
            if path == os.path.join(job_dir, "meta.json"):
                shutil.rmtree(job_dir)
            return real_open(path, *args, **kwargs)

        model = model_jobs.JobsModel(self.home)
        with mock.patch("builtins.open", side_effect=racing_open):
            snap = model.snapshot(NOW_MS, stall_min=10)
        self.assertEqual(self._ids(snap), [])
        self.assertEqual(snap["errors"], [])


class LogErrorsTest(ModelJobsTestCase):
    def test_unparsable_log_line_is_reported_in_errors(self):
        meta = _base_meta("j1", cli="codex", status="running", sup_pid=123)
        _write_job(self.home, "j1", meta=meta,
                   log_lines=['{"type": "turn.completed", "usage": BROKEN}'])
        model = model_jobs.JobsModel(self.home, pid_alive=lambda pid: True)
        snap = model.snapshot(NOW_MS, stall_min=60)
        self.assertEqual(len(snap["errors"]), 1)
        self.assertIn("codex", snap["errors"][0])
        self.assertIn("j1", snap["errors"][0])


class IncrementalLogTest(ModelJobsTestCase):
    def test_log_is_fed_incrementally_across_snapshots(self):
        meta = _base_meta("j1", cli="codex", status="running", sup_pid=123)
        job_dir = _write_job(self.home, "j1", meta=meta, log_lines=[])
        model = model_jobs.JobsModel(self.home, pid_alive=lambda pid: True)

        ev1 = json.dumps({"type": "turn.completed",
                           "usage": {"input_tokens": 100, "output_tokens": 10}})
        with open(os.path.join(job_dir, "log"), "a") as f:
            f.write(ev1 + "\n")
        rec1 = model.snapshot(NOW_MS, stall_min=60)["jobs"][0]
        self.assertEqual(rec1["tokens"], 110)

        ev2 = json.dumps({"type": "turn.completed",
                           "usage": {"input_tokens": 50, "output_tokens": 5}})
        with open(os.path.join(job_dir, "log"), "a") as f:
            f.write(ev2 + "\n")
        rec2 = model.snapshot(NOW_MS, stall_min=60)["jobs"][0]
        self.assertEqual(rec2["tokens"], 165)

    def test_tool_is_null_once_job_ended_even_with_replay(self):
        meta = _base_meta("j1", cli="codex", status="ok",
                           ended=_iso(2026, 9, 23, 14, 30, 0))
        item = json.dumps({"type": "item.started",
                            "item": {"id": "i1", "type": "command_execution", "command": "ls"}})
        _write_job(self.home, "j1", meta=meta, log_lines=[item])
        model = model_jobs.JobsModel(self.home)
        rec = model.snapshot(NOW_MS, stall_min=60)["hof"][0]
        self.assertIsNone(rec["tool"])
        self.assertEqual(len(rec["replay"]), 1)


class GitRepoTestCase(ModelJobsTestCase):
    def _git(self, args, cwd, capture=False):
        proc = subprocess.run(
            ["git"] + args, cwd=cwd, check=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE,
        )
        return proc.stdout.decode("utf-8").strip() if capture else None

    def _init_repo(self):
        repo = os.path.join(self.home, "repo")
        os.makedirs(repo)
        self._git(["init", "-q"], repo)
        self._git(["config", "user.email", "t@example.com"], repo)
        self._git(["config", "user.name", "Test"], repo)
        with open(os.path.join(repo, "tracked.txt"), "w") as f:
            f.write("line1\nline2\n")
        self._git(["add", "."], repo)
        self._git(["commit", "-q", "-m", "init"], repo)
        base = self._git(["rev-parse", "HEAD"], repo, capture=True)
        return repo, base


class FilesFieldTest(GitRepoTestCase):
    def test_read_job_files_is_null(self):
        meta = _base_meta("jr", mode="read", status="running", sup_pid=123)
        _write_job(self.home, "jr", meta=meta, log_lines=[])
        model = model_jobs.JobsModel(self.home, pid_alive=lambda pid: True)
        rec = model.snapshot(NOW_MS, stall_min=60)["jobs"][0]
        self.assertIsNone(rec["files"])

    def test_running_write_job_numstat_includes_untracked_file(self):
        repo, base = self._init_repo()
        worktree = os.path.join(self.home, "wt")
        self._git(["worktree", "add", "-q", "--detach", worktree, base], repo)
        with open(os.path.join(worktree, "tracked.txt"), "a") as f:
            f.write("line3\n")
        with open(os.path.join(worktree, "new.txt"), "w") as f:
            f.write("a\nb\nc\n")
        meta = _base_meta("jw", mode="write", status="running", sup_pid=123,
                           repo=repo, base=base, worktree=worktree)
        _write_job(self.home, "jw", meta=meta, log_lines=[])
        model = model_jobs.JobsModel(self.home, pid_alive=lambda pid: True)
        rec = model.snapshot(NOW_MS, stall_min=60)["jobs"][0]
        by_name = {f["n"]: f for f in rec["files"]}
        self.assertEqual(by_name["tracked.txt"], {"n": "tracked.txt", "a": 1, "d": 0})
        self.assertEqual(by_name["new.txt"], {"n": "new.txt", "a": 3, "d": 0})

    def test_running_write_job_files_cached_until_refresh_interval(self):
        repo, base = self._init_repo()
        worktree = os.path.join(self.home, "wt")
        self._git(["worktree", "add", "-q", "--detach", worktree, base], repo)

        calls = []

        def counting_git(args, cwd):
            calls.append(args)
            return model_jobs.default_run_git(args, cwd)

        clock = [1000.0]
        meta = _base_meta("jw", mode="write", status="running", sup_pid=123,
                           repo=repo, base=base, worktree=worktree)
        job_dir = _write_job(self.home, "jw", meta=meta, log_lines=["a"])
        model = model_jobs.JobsModel(self.home, pid_alive=lambda pid: True, run_git=counting_git,
                                     clock=lambda: clock[0])

        model.snapshot(NOW_MS, stall_min=60)
        first_count = len(calls)
        self.assertGreater(first_count, 0)

        with open(os.path.join(job_dir, "log"), "a") as f:
            f.write("b\n")
        clock[0] += model_jobs.FILES_REFRESH_S - 1
        model.snapshot(NOW_MS, stall_min=60)
        self.assertEqual(len(calls), first_count, "a growing log alone must not re-run git")

        clock[0] += 1
        model.snapshot(NOW_MS, stall_min=60)
        self.assertGreater(len(calls), first_count, "an expired measurement must re-run git")

    def test_finished_write_job_files_from_real_diff_patch(self):
        repo, base = self._init_repo()
        worktree = os.path.join(self.home, "wt2")
        self._git(["worktree", "add", "-q", "-b", "farmout/test", worktree, base], repo)
        with open(os.path.join(worktree, "tracked.txt"), "a") as f:
            f.write("line3\nline4\n")
        with open(os.path.join(worktree, "extra.txt"), "w") as f:
            f.write("x\n")
        self._git(["add", "-A"], worktree)
        patch = subprocess.run(
            ["git", "diff-index", "--cached", "-p", "--binary", base],
            cwd=worktree, stdout=subprocess.PIPE, check=True
        ).stdout
        meta = _base_meta("jw2", mode="write", status="ok", land=None,
                           ended=_iso(2026, 9, 23, 14, 30, 0),
                           repo=repo, base=base, worktree=worktree)
        job_dir = _write_job(self.home, "jw2", meta=meta, log_lines=[])
        with open(os.path.join(job_dir, "diff.patch"), "wb") as f:
            f.write(patch)
        model = model_jobs.JobsModel(self.home)
        rec = model.snapshot(NOW_MS, stall_min=10)["jobs"][0]
        by_name = {f["n"]: f for f in rec["files"]}
        self.assertEqual(by_name["tracked.txt"], {"n": "tracked.txt", "a": 2, "d": 0})
        self.assertEqual(by_name["extra.txt"], {"n": "extra.txt", "a": 1, "d": 0})

    def test_finished_write_job_files_from_diff_patch_cached_after_first_poll(self):
        calls = []

        def fake_git(args, cwd):
            calls.append(args)
            return "3\t1\tfoo.py\n"

        meta = _base_meta("jw", mode="write", status="ok", land=None,
                           ended=_iso(2026, 9, 23, 14, 30, 0), repo="/does/not/matter")
        _write_job(self.home, "jw", meta=meta, log_lines=[], diff_patch="dummy patch content\n")
        model = model_jobs.JobsModel(self.home, run_git=fake_git)

        rec1 = model.snapshot(NOW_MS, stall_min=10)["jobs"][0]
        self.assertEqual(rec1["files"], [{"n": "foo.py", "a": 3, "d": 1}])
        self.assertEqual(len(calls), 1)

        rec2 = model.snapshot(NOW_MS, stall_min=10)["jobs"][0]
        self.assertEqual(rec2["files"], rec1["files"])
        self.assertEqual(len(calls), 1, "a finished job's files must be cached for good")

    def test_finished_write_job_missing_diff_patch_is_null(self):
        meta = _base_meta("jw", mode="write", status="ok", land=None,
                           ended=_iso(2026, 9, 23, 14, 30, 0))
        _write_job(self.home, "jw", meta=meta, log_lines=[])
        model = model_jobs.JobsModel(self.home)
        rec = model.snapshot(NOW_MS, stall_min=10)["jobs"][0]
        self.assertIsNone(rec["files"])


class DefaultRunGitTest(unittest.TestCase):
    @mock.patch("model_jobs.subprocess.run")
    def test_default_run_git_sets_optional_locks_env(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=["git"], returncode=0, stdout=b"ok\n")
        out = model_jobs.default_run_git(["status"], "/tmp")
        _, kwargs = mock_run.call_args
        self.assertEqual(kwargs["env"]["GIT_OPTIONAL_LOCKS"], "0")
        self.assertEqual(out, "ok\n")


class MembershipTest(ModelJobsTestCase):
    def _job(self, job_id, mode, status, land=None, ended=None, sup_pid=123):
        meta = _base_meta(job_id, mode=mode, status=status, land=land,
                           sup_pid=sup_pid, ended=ended)
        _write_job(self.home, job_id, meta=meta, log_lines=[])

    def test_membership_matches_schema_notes(self):
        self._job("running1", "read", "running")
        self._job("lost1", "read", "running", sup_pid=None)
        self._job("ready1", "write", "ok", land=None, ended=_iso(2026, 9, 23, 14, 10, 0))
        self._job("conflict1", "write", "ok", land="conflict", ended=_iso(2026, 9, 23, 14, 11, 0))
        self._job("landed1", "write", "ok", land="landed", ended=_iso(2026, 9, 23, 14, 12, 0))
        self._job("discarded1", "write", "ok", land="discarded", ended=_iso(2026, 9, 23, 14, 13, 0))
        self._job("clear1", "read", "ok", ended=_iso(2026, 9, 23, 14, 14, 0))
        self._job("over1", "read", "failed", ended=_iso(2026, 9, 23, 14, 15, 0))

        model = model_jobs.JobsModel(self.home, pid_alive=lambda pid: True)
        snap = model.snapshot(NOW_MS, stall_min=60)
        job_ids = set(r["id"] for r in snap["jobs"])
        hof_ids = [r["id"] for r in snap["hof"]]

        self.assertEqual(job_ids, {"running1", "lost1", "ready1", "conflict1"})
        self.assertEqual(set(hof_ids), {"landed1", "discarded1", "clear1", "over1"})
        self.assertEqual(hof_ids, ["over1", "clear1", "discarded1", "landed1"])

    def test_hof_caps_at_ten_most_recent(self):
        for i in range(12):
            self._job("j%02d" % i, "read", "ok", ended=_iso(2026, 9, 23, 14, 0, i))
        model = model_jobs.JobsModel(self.home)
        snap = model.snapshot(NOW_MS, stall_min=60)
        self.assertEqual(len(snap["hof"]), 10)
        ids = [r["id"] for r in snap["hof"]]
        self.assertEqual(ids, ["j%02d" % i for i in range(11, 1, -1)])


class TodayTotalsTest(ModelJobsTestCase):
    def _write_finished_copilot(self, job_id, premium, started=None, ended=None):
        meta = _base_meta(
            job_id, cli="copilot", mode="read", status="ok",
            started=started or _iso(2026, 9, 23, 14, 0, 0),
            ended=ended or _iso(2026, 9, 23, 14, 30, 0),
        )
        log_lines = ['{"type": "result", "usage": {"premiumRequests": %d}}' % premium]
        _write_job(self.home, job_id, meta=meta, log_lines=log_lines)

    def test_today_totals_are_not_capped_by_hof_size(self):
        for i in range(12):
            self._write_finished_copilot("j%02d" % i, premium=1, ended=_iso(2026, 9, 23, 14, 0, i))
        model = model_jobs.JobsModel(self.home)
        snap = model.snapshot(NOW_MS, stall_min=60)
        self.assertEqual(len(snap["hof"]), 10)  # display list still capped
        self.assertEqual(snap["today"]["premium"], 12)  # aggregate is not

    def test_today_totals_exclude_jobs_not_started_today(self):
        self._write_finished_copilot(
            "yesterday1", premium=5,
            started=_iso(2026, 9, 22, 12, 0, 0), ended=_iso(2026, 9, 22, 14, 0, 0),
        )
        model = model_jobs.JobsModel(self.home)
        snap = model.snapshot(NOW_MS, stall_min=60)
        self.assertEqual(snap["today"], {"score": 0, "credits": 0.0, "premium": 0})

    def test_today_totals_sum_score_and_credits_too(self):
        codex_meta = _base_meta("codexA", cli="codex", mode="read", status="running")
        _write_job(self.home, "codexA", meta=codex_meta, log_lines=[
            '{"type": "turn.completed", "usage": {"input_tokens": 40, "output_tokens": 10}}'
        ])
        kiro_meta = _base_meta("kiroA", cli="kiro", mode="read", status="running")
        _write_job(self.home, "kiroA", meta=kiro_meta, log_lines=[
            '{"type": "metadata", "data": {"meteringUsage": [{"value": 0.25, "unit": "credit"}]}}'
        ])
        model = model_jobs.JobsModel(self.home)
        snap = model.snapshot(NOW_MS, stall_min=60)
        self.assertEqual(snap["today"], {"score": 50, "credits": 0.25, "premium": 0})


class CorruptMetaTest(ModelJobsTestCase):
    def test_corrupt_meta_json_is_reported_in_errors_not_silently_dropped(self):
        job_dir = os.path.join(self.home, "jobs", "j1")
        os.makedirs(job_dir)
        with open(os.path.join(job_dir, "meta.json"), "w", encoding="utf-8") as f:
            f.write("{not valid json")
        model = model_jobs.JobsModel(self.home)
        snap = model.snapshot(NOW_MS, stall_min=10)
        self.assertEqual(snap["jobs"], [])
        self.assertEqual(snap["hof"], [])
        self.assertEqual(len(snap["errors"]), 1)
        self.assertIn("j1", snap["errors"][0])

    def test_missing_meta_json_is_still_silently_skipped(self):
        os.makedirs(os.path.join(self.home, "jobs", "no-meta"))
        model = model_jobs.JobsModel(self.home)
        snap = model.snapshot(NOW_MS, stall_min=10)
        self.assertEqual(snap["jobs"] + snap["hof"], [])
        self.assertEqual(snap["errors"], [])


class UntrackedFileHonestyTest(GitRepoTestCase):
    def _running_write_job(self, job_id="jw"):
        repo, base = self._init_repo()
        worktree = os.path.join(self.home, "wt")
        self._git(["worktree", "add", "-q", "--detach", worktree, base], repo)
        meta = _base_meta(job_id, mode="write", status="running", sup_pid=123,
                           repo=repo, base=base, worktree=worktree)
        _write_job(self.home, job_id, meta=meta, log_lines=[])
        return worktree

    def test_untracked_binary_file_reports_null_added_and_removed(self):
        worktree = self._running_write_job()
        with open(os.path.join(worktree, "blob.bin"), "wb") as f:
            f.write(b"\x00" * 10240)
        model = model_jobs.JobsModel(self.home, pid_alive=lambda pid: True)
        rec = model.snapshot(NOW_MS, stall_min=60)["jobs"][0]
        entry = next(f for f in rec["files"] if f["n"] == "blob.bin")
        self.assertIsNone(entry["a"])
        self.assertIsNone(entry["d"])

    def test_untracked_file_over_size_cap_reports_null_added(self):
        worktree = self._running_write_job()
        big_path = os.path.join(worktree, "big.txt")
        line_count = (model_jobs.UNTRACKED_COUNT_MAX_BYTES // 2) + 1
        with open(big_path, "w") as f:
            f.write("x\n" * line_count)
        self.assertGreater(os.path.getsize(big_path), model_jobs.UNTRACKED_COUNT_MAX_BYTES)
        model = model_jobs.JobsModel(self.home, pid_alive=lambda pid: True)
        rec = model.snapshot(NOW_MS, stall_min=60)["jobs"][0]
        entry = next(f for f in rec["files"] if f["n"] == "big.txt")
        self.assertIsNone(entry["a"])

    def test_untracked_small_text_file_still_counts_normally(self):
        worktree = self._running_write_job()
        with open(os.path.join(worktree, "small.txt"), "w") as f:
            f.write("a\nb\nc\n")
        model = model_jobs.JobsModel(self.home, pid_alive=lambda pid: True)
        rec = model.snapshot(NOW_MS, stall_min=60)["jobs"][0]
        entry = next(f for f in rec["files"] if f["n"] == "small.txt")
        self.assertEqual(entry, {"n": "small.txt", "a": 3, "d": 0})


class GitFailureTest(ModelJobsTestCase):
    def test_running_write_job_files_null_when_git_fails(self):
        meta = _base_meta("jw", mode="write", status="running", sup_pid=123,
                           repo="/repo", base="deadbeef", worktree="/wt")
        _write_job(self.home, "jw", meta=meta, log_lines=[])
        model = model_jobs.JobsModel(self.home, pid_alive=lambda pid: True,
                                      run_git=lambda args, cwd: None)
        rec = model.snapshot(NOW_MS, stall_min=60)["jobs"][0]
        self.assertIsNone(rec["files"])

    def test_finished_write_job_files_null_when_git_fails(self):
        meta = _base_meta("jw", mode="write", status="ok", land=None,
                           ended=_iso(2026, 9, 23, 14, 30, 0), repo="/repo")
        _write_job(self.home, "jw", meta=meta, log_lines=[], diff_patch="dummy\n")
        model = model_jobs.JobsModel(self.home, run_git=lambda args, cwd: None)
        rec = model.snapshot(NOW_MS, stall_min=10)["jobs"][0]
        self.assertIsNone(rec["files"])

    @mock.patch("model_jobs.subprocess.run",
                side_effect=subprocess.TimeoutExpired(["git"], model_jobs.GIT_TIMEOUT_S))
    def test_default_run_git_returns_none_on_timeout(self, mock_run):
        self.assertIsNone(model_jobs.default_run_git(["status"], "/tmp"))
        self.assertEqual(model_jobs.GIT_TIMEOUT_S, mock_run.call_args[1]["timeout"])

    def test_default_run_git_returns_none_on_nonzero_exit(self):
        out = model_jobs.default_run_git(["this-is-not-a-git-subcommand"], "/tmp")
        self.assertIsNone(out)

    def test_default_run_git_returns_none_when_binary_missing(self):
        with mock.patch("model_jobs.subprocess.run", side_effect=OSError("no such file")):
            out = model_jobs.default_run_git(["status"], "/tmp")
        self.assertIsNone(out)


class LogResetTest(ModelJobsTestCase):
    def test_log_state_resets_when_file_inode_changes(self):
        meta = _base_meta("j1", cli="codex", status="running", sup_pid=123)
        job_dir = _write_job(self.home, "j1", meta=meta, log_lines=[])
        log_path = os.path.join(job_dir, "log")
        ev1 = json.dumps({"type": "turn.completed",
                           "usage": {"input_tokens": 100, "output_tokens": 10}})
        with open(log_path, "w") as f:
            f.write(ev1 + "\n")

        model = model_jobs.JobsModel(self.home, pid_alive=lambda pid: True)
        rec1 = model.snapshot(NOW_MS, stall_min=60)["jobs"][0]
        self.assertEqual(rec1["tokens"], 110)

        os.remove(log_path)  # recreated below with a new inode
        ev2 = json.dumps({"type": "turn.completed",
                           "usage": {"input_tokens": 5, "output_tokens": 1}})
        with open(log_path, "w") as f:
            f.write(ev2 + "\n")

        rec2 = model.snapshot(NOW_MS, stall_min=60)["jobs"][0]
        self.assertEqual(rec2["tokens"], 6, "must not double-count across a log reset")

    def test_log_state_resets_when_file_is_truncated_in_place(self):
        meta = _base_meta("j1", cli="codex", status="running", sup_pid=123)
        job_dir = _write_job(self.home, "j1", meta=meta, log_lines=[])
        log_path = os.path.join(job_dir, "log")
        ev1 = json.dumps({"type": "turn.completed",
                           "usage": {"input_tokens": 100, "output_tokens": 10}})
        with open(log_path, "w") as f:
            f.write(ev1 + "\n" + ("padding line\n" * 5))

        model = model_jobs.JobsModel(self.home, pid_alive=lambda pid: True)
        rec1 = model.snapshot(NOW_MS, stall_min=60)["jobs"][0]
        self.assertEqual(rec1["tokens"], 110)

        ev2 = json.dumps({"type": "turn.completed",
                           "usage": {"input_tokens": 5, "output_tokens": 1}})
        with open(log_path, "w") as f:  # "w" truncates in place, same inode
            f.write(ev2 + "\n")

        rec2 = model.snapshot(NOW_MS, stall_min=60)["jobs"][0]
        self.assertEqual(rec2["tokens"], 6, "must not double-count across a truncation")


class RunningToFinishedTransitionTest(ModelJobsTestCase):
    def test_files_switches_from_worktree_cache_to_diff_patch_after_job_ends(self):
        meta = _base_meta("j1", mode="write", status="running", sup_pid=123,
                           repo="/repo", base="deadbeef", worktree="/wt")
        job_dir = _write_job(self.home, "j1", meta=meta, log_lines=["a"])

        def fake_git(args, cwd):
            if args[0] == "diff":
                return "5\t2\tbar.py\n"
            if args[0] == "ls-files":
                return ""
            if args[0] == "apply":
                return "9\t1\tbaz.py\n"
            return ""

        model = model_jobs.JobsModel(self.home, pid_alive=lambda pid: True, run_git=fake_git)
        rec1 = model.snapshot(NOW_MS, stall_min=60)["jobs"][0]
        self.assertEqual(rec1["files"], [{"n": "bar.py", "a": 5, "d": 2}])

        meta["status"] = "ok"
        meta["ended"] = _iso(2026, 9, 23, 14, 30, 0)
        with open(os.path.join(job_dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f)
        with open(os.path.join(job_dir, "diff.patch"), "w", encoding="utf-8") as f:
            f.write("dummy\n")

        rec2 = model.snapshot(NOW_MS, stall_min=60)["jobs"][0]
        self.assertEqual(rec2["files"], [{"n": "baz.py", "a": 9, "d": 1}])


class BackgroundFilesTest(ModelJobsTestCase):
    """With background_files a snapshot never calls git; the refresher does."""

    SETTLE_S = 5

    def _running_write_job(self, job_id):
        meta = _base_meta(job_id, mode="write", status="running", sup_pid=123,
                          repo="/repo", base="abc", worktree="/wt/" + job_id)
        _write_job(self.home, job_id, meta=meta, log_lines=[])

    def _model(self, run_git):
        model = model_jobs.JobsModel(self.home, pid_alive=lambda pid: True, run_git=run_git,
                                     background_files=True)
        self.addCleanup(model.close)
        return model

    def _wait_for(self, predicate):
        deadline = time.monotonic() + self.SETTLE_S
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return False

    def test_snapshot_does_not_wait_on_slow_git(self):
        release = threading.Event()
        callers = []

        def blocking_git(args, cwd):
            callers.append(threading.current_thread())
            release.wait(self.SETTLE_S)
            return ""

        for i in range(8):
            self._running_write_job("jw%d" % i)
        model = self._model(blocking_git)
        started = time.monotonic()
        snap = model.snapshot(NOW_MS, stall_min=60)
        elapsed = time.monotonic() - started
        release.set()

        self.assertLess(elapsed, 1.0)
        self.assertEqual([None] * 8, [r["files"] for r in snap["jobs"]], "unmeasured until the refresher reports")
        self.assertTrue(self._wait_for(lambda: callers))
        self.assertNotIn(threading.current_thread(), callers)

    def test_refresher_result_is_served_on_the_next_snapshot(self):
        def fake_git(args, cwd):
            return "2\t1\tsrc.txt\n" if args[0] == "diff" else ""

        self._running_write_job("jw")
        model = self._model(fake_git)
        model.snapshot(NOW_MS, stall_min=60)
        self.assertTrue(self._wait_for(
            lambda: model.snapshot(NOW_MS, stall_min=60)["jobs"][0]["files"] is not None))
        self.assertEqual([{"n": "src.txt", "a": 2, "d": 1}],
                         model.snapshot(NOW_MS, stall_min=60)["jobs"][0]["files"])

    def test_close_stops_the_refresher(self):
        model = self._model(lambda args, cwd: "")
        model.close()
        self.assertTrue(self._wait_for(
            lambda: not any(t.name == "arcade-files" and t.is_alive() for t in threading.enumerate())))


if __name__ == "__main__":
    unittest.main()


class OutFilesTest(ModelJobsTestCase):
    def test_out_files_become_absolute_paths_under_the_job(self):
        meta = _base_meta("j1", status="ok", ended=_iso(2026, 9, 23, 14, 30, 0), out=["report.md", 7])
        job_dir = _write_job(self.home, "j1", meta=meta, log_lines=[])
        rec = (lambda s: (s["jobs"] + s["hof"])[0])(model_jobs.JobsModel(self.home).snapshot(NOW_MS, stall_min=10))
        self.assertEqual(rec["out"], [os.path.join(job_dir, "out", "report.md")])

    def test_no_out_field_is_an_empty_list(self):
        _write_job(self.home, "j1", meta=_base_meta("j1", status="ok", ended=_iso(2026, 9, 23, 14, 30, 0)), log_lines=[])
        rec = (lambda s: (s["jobs"] + s["hof"])[0])(model_jobs.JobsModel(self.home).snapshot(NOW_MS, stall_min=10))
        self.assertEqual(rec["out"], [])


class QueuedTicketTest(ModelJobsTestCase):
    def _ticket(self, pid, job_id, name):
        qdir = os.path.join(self.home, "queue")
        os.makedirs(qdir, exist_ok=True)
        meta = _base_meta(job_id, status="running", sup_pid=pid, title="waiting lane")
        with open(os.path.join(qdir, name), "w", encoding="utf-8") as f:
            f.write("{}\n{}\n".format(pid, json.dumps(meta)))

    def test_live_ticket_is_a_queued_job(self):
        self._ticket(4242, "20260924-100000-codex-aaaa", "0001.000000-20260924-100000-codex-aaaa")
        snap = model_jobs.JobsModel(self.home, pid_alive=lambda pid: pid == 4242).snapshot(NOW_MS, stall_min=10)
        rec = snap["jobs"][0]
        self.assertEqual((rec["status"], rec["pose"], rec["title"]), ("queued", "queued", "waiting lane"))

    def test_dead_waiter_and_garbage_tickets_are_ignored(self):
        self._ticket(4242, "20260924-100000-codex-aaaa", "0001.000000-20260924-100000-codex-aaaa")
        qdir = os.path.join(self.home, "queue")
        with open(os.path.join(qdir, "0002.000000-junk"), "w") as f:
            f.write("not a pid\n{")
        snap = model_jobs.JobsModel(self.home, pid_alive=lambda pid: False).snapshot(NOW_MS, stall_min=10)
        self.assertEqual(snap["jobs"], [])
        self.assertEqual(snap["errors"], [])
