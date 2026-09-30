"""The real bin/farmout, driven by the fake CLI, feeding the real JobsModel."""

import os
import shutil
import subprocess
import tempfile
import time
import unittest

import model_jobs

FARMOUT_BIN = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "bin", "farmout"))

_STR = (str,)
_OPT_STR = (str, type(None))
_OPT_INT = (int, type(None))

# The frozen /api/state job schema, field => the types it may carry.
JOB_FIELD_TYPES = {
    "id": _STR, "cli": _STR, "kind": _OPT_STR, "mode": _STR, "title": _OPT_STR, "brief": _OPT_STR,
    "repo": _OPT_STR, "branch": _OPT_STR, "model": _OPT_STR, "effort": _OPT_STR, "status": _STR,
    "pose": _STR, "land": _OPT_STR, "error": _OPT_STR, "elapsed_s": _OPT_INT, "timeout_s": _OPT_INT,
    "started": _OPT_INT, "ended": _OPT_INT, "tool": _OPT_STR, "tokens": _OPT_INT,
    "coins": (dict, type(None)), "replay": (list,), "files": (list, type(None)),
    "worktree": _OPT_STR, "parent_session": _OPT_STR, "route": _OPT_STR, "out": (list,),
    "commits": (list,), "owns": (list,), "uncommitted": (list,), "outside_owns": (list,),
    "unattributed": (list,), "checkout": _OPT_STR,
}


class RealFarmoutIntoJobsModelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="arcade-integration-"))
        self.home = os.path.join(self.tmp, "home")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self.env = dict(os.environ)
        self.env.update({
            "FARMOUT_HOME": self.home,
            "FARMOUT_CONFIG": os.path.join(self.tmp, "no-such-config.json"),
        })
        for name in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_PID", "FARMOUT_DOCTOR_STUB", "FARMOUT_TIMEOUT"):
            self.env.pop(name, None)
        self._git("init", "-q")
        with open(os.path.join(self.repo, "a.txt"), "w", encoding="utf-8") as f:
            f.write("one\n")
        self._git("add", "a.txt")
        self._git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")

    def tearDown(self):
        subprocess.run([FARMOUT_BIN, "clean", "--all"], cwd=self.repo, env=self.env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _git(self, *args):
        subprocess.check_call(["git"] + list(args), cwd=self.repo, env=self.env)

    def _run(self, brief_lines, *flags):
        brief = os.path.join(self.tmp, "brief.md")
        with open(brief, "w", encoding="utf-8") as f:
            f.write("\n".join(brief_lines) + "\n")
        proc = subprocess.run(
            [FARMOUT_BIN, "run", "fake"] + list(flags) + ["--brief", brief],
            cwd=self.repo, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(0, proc.returncode, proc.stderr.decode("utf-8", errors="replace"))
        return proc.stdout.decode("utf-8").splitlines()[0]

    def test_snapshot_records_match_the_frozen_job_schema(self):
        read_id = self._run(["Read the file.", "", "FAKE: print hi"])
        write_id = self._run(["Change the file.", "", "FAKE: write b.txt hello", "FAKE: print done"], "--write")

        snap = model_jobs.JobsModel(self.home).snapshot(int(time.time() * 1000), 10)
        records = {r["id"]: r for r in snap["jobs"] + snap["hof"]}
        self.assertEqual({read_id, write_id}, set(records))

        for job_id, rec in records.items():
            self.assertEqual(set(JOB_FIELD_TYPES), set(rec), job_id)
            for field, types in JOB_FIELD_TYPES.items():
                value = rec[field]
                self.assertIsInstance(value, types, "{}.{} = {!r}".format(job_id, field, value))
                self.assertNotIsInstance(value, bool, "{}.{}".format(job_id, field))

        read, write = records[read_id], records[write_id]
        for rec in (read, write):
            for field in ("commits", "owns", "uncommitted", "outside_owns", "unattributed"):
                self.assertEqual(rec[field], [], field)
            self.assertIsNone(rec["checkout"])
        self.assertEqual(("read", "ok", "clear"), (read["mode"], read["status"], read["pose"]))
        self.assertEqual(("write", "ok", "ready"), (write["mode"], write["status"], write["pose"]))
        self.assertIn(write_id, [r["id"] for r in snap["jobs"]])
        self.assertIn(read_id, [r["id"] for r in snap["hof"]])
        self.assertEqual("repo", read["repo"])
        self.assertIsNone(read["files"])
        self.assertEqual([{"n": "b.txt", "a": 1, "d": 0}], write["files"])
        self.assertTrue(os.path.isdir(write["worktree"]))
        self.assertEqual("Read the file.", read["title"])
        self.assertEqual([], snap["errors"])

    def test_in_place_commit_review_and_accept_feed_the_frozen_schema(self):
        self._git("branch", "-M", "feature/in-place-model")
        self._git("config", "user.name", "repo-user")
        self._git("config", "user.email", "repo@example.test")
        with open(os.path.join(self.repo, "user.txt"), "w") as f:
            f.write("untouched user work\n")
        job_id = self._run(["Commit the owned file.", "", "FAKE: commit worker-change a.txt"],
                           "--in-place", "--owns", "a.txt")
        model = model_jobs.JobsModel(self.home)
        snap = model.snapshot(int(time.time() * 1000), 10)
        self.assertEqual([r["id"] for r in snap["jobs"]], [job_id])
        rec = snap["jobs"][0]
        self.assertEqual(set(rec), set(JOB_FIELD_TYPES))
        for field, types in JOB_FIELD_TYPES.items():
            self.assertIsInstance(rec[field], types, field)
        self.assertEqual((rec["mode"], rec["status"], rec["pose"]), ("in-place", "ok", "review"))
        self.assertIsNone(rec["worktree"])
        self.assertEqual(rec["checkout"], self.repo)
        self.assertEqual(rec["owns"], ["a.txt"])
        self.assertEqual(rec["files"], [{"n": "a.txt", "a": 1, "d": 1}])
        self.assertEqual(len(rec["commits"]), 1)
        self.assertTrue(rec["commits"][0]["available"])
        before = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.repo)
        self.assertEqual(rec["commits"][0]["sha"], before.decode().strip())
        with open(os.path.join(self.repo, ".git", "index"), "rb") as f:
            index_before = f.read()
        accepted = subprocess.run([FARMOUT_BIN, "accept", job_id], cwd=self.repo, env=self.env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(accepted.returncode, 0, accepted.stderr.decode())
        snap = model.snapshot(int(time.time() * 1000), 10)
        self.assertEqual(snap["jobs"], [])
        self.assertEqual(snap["hof"][0]["pose"], "accepted")
        self.assertEqual(snap["hof"][0]["commits"], rec["commits"])
        self.assertEqual(subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.repo), before)
        with open(os.path.join(self.repo, ".git", "index"), "rb") as f:
            self.assertEqual(f.read(), index_before)
        with open(os.path.join(self.repo, "user.txt")) as f:
            self.assertEqual(f.read(), "untouched user work\n")
        self.assertEqual(snap["errors"], [])


if __name__ == "__main__":
    unittest.main()
