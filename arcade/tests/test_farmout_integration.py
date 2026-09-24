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


if __name__ == "__main__":
    unittest.main()
