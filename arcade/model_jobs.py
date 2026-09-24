"""Job model: turns `farmout` job directories into dashboard records.

Stdlib-only, targets /usr/bin/python3 (3.9): no match statements, no `X | Y`
types.
"""

import datetime
import json
import os
import re
import subprocess
import time

import logs

WRITE_MODE = "write"
READ_MODE = "read"

TITLE_MAX = 80
HOF_MAX = 10

_LAND_POSE = {None: "ready", "landed": "landed", "conflict": "conflict", "discarded": "discarded"}

_SENTENCE_RE = re.compile(r".*?[.!?](?=\s|$)")


def _is_today(started_ms, now_ms):
    if not isinstance(started_ms, int):
        return False
    return time.localtime(started_ms / 1000.0)[:3] == time.localtime(now_ms / 1000.0)[:3]


def _iso_to_ms(ts):
    if not ts:
        return None
    text = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
    try:
        dt = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    return int(dt.timestamp() * 1000)


def default_pid_alive(pid):
    """Same rule as bin/farmout's pid_alive: a zombie still answers, so the
    process state (not just liveness) decides."""
    try:
        proc = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except OSError:
        return False
    stat = proc.stdout.decode("utf-8", errors="replace").strip()
    if not stat:
        return False
    return not stat.startswith("Z")


def default_run_git(args, cwd):
    """Every server-side git call must never take a live worker's index.lock.

    None signals the call itself failed (missing binary or nonzero exit) --
    distinct from "" (ran fine, produced no output), so a caller can tell
    "unmeasurable" from "measured: no changes".
    """
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        proc = subprocess.run(
            ["git"] + list(args), cwd=cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", errors="replace")


def _first_prose_paragraph(text):
    lines = []
    started = False
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not started:
            if not line or line.startswith("#"):
                continue
            started = True
        elif not line:
            break
        lines.append(line)
    return " ".join(lines)


def _first_sentence(paragraph, cap):
    if not paragraph:
        return None
    m = _SENTENCE_RE.match(paragraph)
    sentence = m.group(0) if m else paragraph
    return sentence[:cap]


def _read_brief(job_dir):
    path = os.path.join(job_dir, "brief.md")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    return _first_prose_paragraph(_strip_preamble(text)) or None


_PREAMBLE_HEADING = "# Ground rules (added by farmout)"


def _strip_preamble(text):
    """Drop the ground-rules block farmout puts ahead of every brief."""
    if not text.startswith(_PREAMBLE_HEADING):
        return text
    head, sep, rest = text.partition("\n\n")
    return rest if sep else ""


def _parse_numstat(text):
    files = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added, deleted, name = parts[0], parts[1], parts[2]
        files.append({
            "n": name,
            "a": None if added == "-" else int(added),
            "d": None if deleted == "-" else int(deleted),
        })
    return files


UNTRACKED_COUNT_MAX_BYTES = 2 * 1024 * 1024
_BINARY_SNIFF_BYTES = 8000


def _count_lines(path):
    """None when the file is too large to read on every poll, unreadable, or
    looks binary (a NUL byte in its first 8000 bytes -- git's own binary
    heuristic): a binary file's line count is not a meaningful number.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if size > UNTRACKED_COUNT_MAX_BYTES:
        return None
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if b"\x00" in data[:_BINARY_SNIFF_BYTES]:
        return None
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


class JobsModel(object):
    """Snapshots every job dir under `$FARMOUT_HOME/jobs` into dashboard
    records. Instances hold per-job caches (log parse state, files[] result)
    that persist across snapshot() calls, so repeated polls stay cheap."""

    def __init__(self, farmout_home, pid_alive=None, run_git=None):
        self.farmout_home = farmout_home
        self._pid_alive = pid_alive or default_pid_alive
        self._run_git = run_git or default_run_git
        self._readers = {}
        self._log_states = {}
        self._log_stat = {}
        self._files_cache = {}
        # job_id -> (progress event count, ms when that count was first seen)
        self._progress_seen = {}

    def snapshot(self, now_ms, stall_min):
        errors = []
        records = []
        root = os.path.join(self.farmout_home, "jobs")
        try:
            names = sorted(os.listdir(root))
        except OSError:
            names = []

        live_ids = set()
        for name in names:
            job_dir = os.path.join(root, name)
            meta_path = os.path.join(job_dir, "meta.json")
            if not os.path.isfile(meta_path):
                continue
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except (OSError, ValueError) as exc:
                # A rejected admission removes its job dir; a vanished file is
                # that transient, not a broken meta.
                if not os.path.exists(meta_path):
                    continue
                errors.append("job meta: cannot parse {}: {}".format(name, exc))
                continue
            # Claimed but not yet admitted (or about to be rejected). A meta
            # from before admission existed has no field and stays visible.
            if isinstance(meta, dict) and meta.get("admitted") is False:
                continue
            live_ids.add(name)
            records.append(self._build_record(job_dir, meta, now_ms, stall_min, errors))

        self._prune(live_ids)

        jobs = [r for r in records if self._in_jobs(r)]
        ended = [r for r in records if r["ended"] is not None and not self._in_jobs(r)]
        ended.sort(key=lambda r: r["ended"], reverse=True)
        hof = ended[:HOF_MAX]
        today = self._today_totals(records, now_ms)

        return {"jobs": jobs, "hof": hof, "errors": errors, "today": today}

    @staticmethod
    def _today_totals(records, now_ms):
        """score/credits/premium over every job started today, not just the
        HOF_MAX most recently ended -- HOF is a capped display list, this
        isn't."""
        score = 0
        credits = 0.0
        premium = 0
        for r in records:
            if not _is_today(r["started"], now_ms):
                continue
            if isinstance(r["tokens"], int):
                score += r["tokens"]
            coins = r["coins"]
            if coins and isinstance(coins.get("value"), (int, float)):
                if coins.get("unit") == "CR":
                    credits += coins["value"]
                elif coins.get("unit") == "PR":
                    premium += coins["value"]
        return {"score": score, "credits": round(credits, 2), "premium": int(premium)}

    @staticmethod
    def _in_jobs(record):
        if record["status"] in ("running", "lost"):
            return True
        return (
            record["mode"] == WRITE_MODE
            and record["status"] == "ok"
            and record["land"] in (None, "conflict")
        )

    def _prune(self, live_ids):
        caches = (self._readers, self._log_states, self._log_stat, self._files_cache, self._progress_seen)
        for cache in caches:
            for job_id in list(cache):
                if job_id not in live_ids:
                    del cache[job_id]

    def _build_record(self, job_dir, meta, now_ms, stall_min, errors):
        job_id = meta.get("id") or os.path.basename(job_dir)
        cli = meta.get("cli")
        mode = meta.get("mode")

        status = meta.get("status")
        if status == "running":
            sup_pid = meta.get("sup_pid")
            if not sup_pid or not self._pid_alive(sup_pid):
                status = "lost"

        started_ms = _iso_to_ms(meta.get("started"))
        ended_ms = _iso_to_ms(meta.get("ended"))
        is_ended = ended_ms is not None
        elapsed_s = None
        if started_ms is not None:
            end_ref = ended_ms if is_ended else now_ms
            elapsed_s = int((end_ref - started_ms) / 1000)

        log_path = os.path.join(job_dir, "log")
        log_state = self._feed_log(job_id, cli, log_path, errors)

        land = meta.get("land")
        pose = self._pose(status, mode, land, self._stalled(job_id, log_state, started_ms, now_ms, stall_min))

        title = meta.get("title")
        brief_text = _read_brief(job_dir)
        if not title:
            title = _first_sentence(brief_text, TITLE_MAX)

        repo = meta.get("repo")
        files = self._files_for(job_id, job_dir, meta, mode, is_ended, log_path)

        return {
            "id": job_id,
            "cli": cli,
            "kind": meta.get("kind"),
            "mode": mode,
            "title": title,
            "brief": brief_text,
            "repo": os.path.basename(repo) if repo else None,
            "branch": meta.get("source_branch"),
            "model": meta.get("model"),
            "effort": meta.get("effort"),
            "status": status,
            "pose": pose,
            "land": land,
            "error": meta.get("error"),
            "elapsed_s": elapsed_s,
            "timeout_s": meta.get("timeout_s"),
            "started": started_ms,
            "ended": ended_ms,
            "tool": None if is_ended or log_state is None else log_state.tool,
            "tokens": log_state.tokens if log_state else None,
            "coins": log_state.coins if log_state else None,
            "replay": log_state.replay if log_state else [],
            "files": files,
            "worktree": meta.get("worktree") if mode == WRITE_MODE else None,
            "parent_session": meta.get("parent_session"),
            "route": meta.get("route"),
            # Report files a read job handed back (--out), as absolute paths.
            "out": [os.path.join(job_dir, "out", rel) for rel in (meta.get("out") or [])
                    if isinstance(rel, str)],
        }

    def _feed_log(self, job_id, cli, log_path, errors):
        if job_id not in self._log_states:
            self._new_log_state(job_id, cli)

        state = self._log_states[job_id]
        if state is None:
            return None

        # IncrementalReader resets its own offset on an inode change or a
        # truncation, but a LogState built on top of it does not know that
        # happened -- mirror the same two conditions here so replay/tokens/
        # coins reset in step with it, instead of double-counting whatever
        # the reset reader re-delivers from byte 0.
        try:
            st = os.stat(log_path)
            current_stat = (st.st_ino, st.st_size)
        except OSError:
            current_stat = None

        previous_stat = self._log_stat.get(job_id)
        if current_stat is not None and previous_stat is not None:
            prev_ino, prev_size = previous_stat
            if current_stat[0] != prev_ino or current_stat[1] < prev_size:
                self._new_log_state(job_id, cli)
                state = self._log_states[job_id]
        if current_stat is not None:
            self._log_stat[job_id] = current_stat

        for line in self._readers[job_id].read_new(log_path):
            state.feed(line)

        if state.bad_lines:
            errors.append("{} log: {} unparsable line{} in {}".format(
                cli, state.bad_lines, "" if state.bad_lines == 1 else "s", job_id
            ))
        return state

    def _new_log_state(self, job_id, cli):
        try:
            state = logs.new_log_state(cli)
        except ValueError:
            state = None
        self._log_states[job_id] = state
        self._readers[job_id] = logs.IncrementalReader() if state else None

    def _pose(self, status, mode, land, stalled):
        if status == "running":
            return "pause" if stalled else "play"
        if status == "lost":
            return "lost"
        if status == "ok":
            if mode == WRITE_MODE:
                return _LAND_POSE.get(land, "ready")
            return "clear"
        return "over"

    def _stalled(self, job_id, log_state, started_ms, now_ms, stall_min):
        """No progress event for stall_min and no tool call open.

        The clock is when the progress count last changed. For a job first
        seen with no progress yet it runs from the job's start (a CLI stuck
        before its first event, e.g. on a login spinner); a job first seen
        mid-run (dashboard restart) restarts it, since when its last event
        happened is unknowable. A silent open tool call is a long tool, not a
        stall; the job timeout bounds it. No parser => never paused.
        """
        if log_state is None:
            return False
        count = log_state.progress.events
        seen = self._progress_seen.get(job_id)
        if seen is None or seen[0] != count:
            since = started_ms if seen is None and count == 0 and started_ms else now_ms
            seen = (count, since)
            self._progress_seen[job_id] = seen
        if log_state.progress.open_tools:
            return False
        return (now_ms - seen[1]) / 60000.0 > stall_min

    def _files_for(self, job_id, job_dir, meta, mode, is_ended, log_path):
        if mode != WRITE_MODE:
            return None
        cache = self._files_cache.get(job_id)

        if is_ended:
            if cache and cache.get("final"):
                return cache["files"]
            files = self._files_from_patch(job_dir, meta)
            self._files_cache[job_id] = {"final": True, "files": files}
            return files

        try:
            size = os.stat(log_path).st_size
        except OSError:
            size = None
        if cache and not cache.get("final") and cache.get("size") == size:
            return cache["files"]
        files = self._files_from_worktree(meta)
        self._files_cache[job_id] = {"final": False, "size": size, "files": files}
        return files

    def _files_from_worktree(self, meta):
        worktree = meta.get("worktree")
        base = meta.get("base")
        if not worktree or not base:
            return None
        diff_out = self._run_git(["diff", "--numstat", base], worktree)
        if diff_out is None:
            return None
        files = _parse_numstat(diff_out)
        untracked_out = self._run_git(["ls-files", "--others", "--exclude-standard"], worktree)
        if untracked_out is None:
            return None
        for name in untracked_out.splitlines():
            name = name.strip()
            if not name:
                continue
            count = _count_lines(os.path.join(worktree, name))
            files.append({"n": name, "a": count, "d": None if count is None else 0})
        return files

    def _files_from_patch(self, job_dir, meta):
        patch_path = os.path.join(job_dir, "diff.patch")
        if not os.path.isfile(patch_path):
            return None
        cwd = meta.get("repo") or job_dir
        out = self._run_git(["apply", "--numstat", patch_path], cwd)
        if out is None:
            return None
        return _parse_numstat(out)
