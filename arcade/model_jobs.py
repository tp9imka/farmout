"""Job model: turns `farmout` job directories into dashboard records.

Stdlib-only, targets /usr/bin/python3 (3.9): no match statements, no `X | Y`
types.
"""

import datetime
import json
import os
import re
import subprocess
import threading
import time

import logs

WRITE_MODE = "write"
READ_MODE = "read"

TITLE_MAX = 80
HOF_MAX = 10

_LAND_POSE = {None: "ready", "landed": "landed", "conflict": "conflict", "discarded": "discarded"}

_SENTENCE_RE = re.compile(r".*?[.!?](?=\s|$)")

# A running write job's files[] is re-measured at most this often. Its log
# grows every second, so log growth says nothing about the worktree changing.
FILES_REFRESH_S = 30
# How often the background refresher looks for due jobs.
FILES_TICK_S = 2
# A git call past this reads as unmeasured instead of blocking the refresher.
GIT_TIMEOUT_S = 20


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

    None signals the call itself failed (missing binary, nonzero exit or
    timeout) -- distinct from "" (ran fine, produced no output), so a caller
    can tell "unmeasurable" from "measured: no changes".
    """
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        proc = subprocess.run(
            ["git"] + list(args), cwd=cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=GIT_TIMEOUT_S
        )
    except (OSError, subprocess.TimeoutExpired):
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
    that persist across snapshot() calls, so repeated polls stay cheap.

    files[] costs git calls that take seconds in a large worktree. With
    background_files a refresher thread measures them and snapshot() only
    reads its last result (null until the first measurement), so a snapshot
    never waits on git. Without it snapshot() measures inline when due.
    """

    def __init__(self, farmout_home, pid_alive=None, run_git=None,
                 background_files=False, clock=None):
        self.farmout_home = farmout_home
        self._pid_alive = pid_alive or default_pid_alive
        self._run_git = run_git or default_run_git
        self._clock = clock or time.monotonic
        self._readers = {}
        self._log_states = {}
        self._log_stat = {}
        # job_id -> (progress event count, ms when that count was first seen)
        self._progress_seen = {}

        # Shared with the refresher thread; guarded by _files_lock.
        # _files_wanted: job_id -> spec tuple from the latest snapshot.
        # _files_cache: job_id -> {"final", "at", "files"}.
        self._files_lock = threading.Lock()
        self._files_wanted = {}
        self._files_cache = {}
        self._files_wake = threading.Event()
        self._closed = threading.Event()
        self._background = background_files
        if background_files:
            t = threading.Thread(target=self._refresh_loop, name="arcade-files")
            t.daemon = True
            t.start()

    def close(self):
        self._closed.set()
        self._files_wake.set()

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

        records.extend(self._queued_records(live_ids, now_ms, stall_min, errors))
        self._prune(live_ids)

        jobs = [r for r in records if self._in_jobs(r)]
        ended = [r for r in records if r["ended"] is not None and not self._in_jobs(r)]
        ended.sort(key=lambda r: r["ended"], reverse=True)
        hof = ended[:HOF_MAX]
        today = self._today_totals(records, now_ms)

        return {"jobs": jobs, "hof": hof, "errors": errors, "today": today}

    def _queued_records(self, live_ids, now_ms, stall_min, errors):
        """Waiters from `farmout run --queue`: a ticket per job (line 1 the
        waiter's pid, then its meta). They have no job dir until admitted."""
        root = os.path.join(self.farmout_home, "queue")
        try:
            names = sorted(os.listdir(root))
        except OSError:
            return []
        out = []
        for name in names:
            try:
                with open(os.path.join(root, name), "r", encoding="utf-8") as f:
                    pid_line, _, rest = f.read().partition("\n")
                meta = json.loads(rest)
                pid = int(pid_line.strip())
            except (OSError, ValueError):
                continue
            if not isinstance(meta, dict) or meta.get("id") in live_ids or not self._pid_alive(pid):
                continue
            job_dir = os.path.join(self.farmout_home, "jobs", str(meta.get("id")))
            rec = self._build_record(job_dir, meta, now_ms, stall_min, errors)
            rec.update({"status": "queued", "pose": "queued", "tool": None})
            out.append(rec)
        return out

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
        if record["status"] in ("running", "lost", "queued"):
            return True
        return (
            record["mode"] == WRITE_MODE
            and record["status"] == "ok"
            and record["land"] in (None, "conflict")
        )

    def _prune(self, live_ids):
        caches = (self._readers, self._log_states, self._log_stat, self._progress_seen)
        with self._files_lock:
            for cache in caches + (self._files_wanted, self._files_cache):
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
        files = self._files_for(job_id, job_dir, meta, mode, is_ended)

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

    def _files_for(self, job_id, job_dir, meta, mode, is_ended):
        if mode != WRITE_MODE:
            return None
        spec = (job_dir, meta.get("worktree"), meta.get("base"), meta.get("repo"), is_ended)
        with self._files_lock:
            self._files_wanted[job_id] = spec
            cached = self._files_cache.get(job_id)
            due = self._files_due(cached, is_ended)
        if due:
            if self._background:
                self._files_wake.set()
            else:
                self._measure_files(job_id, spec)
                with self._files_lock:
                    cached = self._files_cache.get(job_id)
        return cached["files"] if cached else None

    def _files_due(self, cached, is_ended):
        if cached is None:
            return True
        if cached["final"]:
            return False
        return is_ended or self._clock() - cached["at"] >= FILES_REFRESH_S

    def _measure_files(self, job_id, spec):
        job_dir, worktree, base, repo, is_ended = spec
        if is_ended:
            files = self._files_from_patch(job_dir, repo)
        else:
            files = self._files_from_worktree(worktree, base)
        with self._files_lock:
            # A job pruned while git ran must not be resurrected.
            if self._files_wanted.get(job_id) == spec:
                self._files_cache[job_id] = {"final": is_ended, "at": self._clock(), "files": files}

    def _refresh_due(self):
        with self._files_lock:
            due = [(job_id, spec) for job_id, spec in self._files_wanted.items()
                   if self._files_due(self._files_cache.get(job_id), spec[4])]
        for job_id, spec in due:
            if self._closed.is_set():
                return
            self._measure_files(job_id, spec)

    def _refresh_loop(self):
        while not self._closed.is_set():
            try:
                self._refresh_due()
            except Exception:  # the refresher must outlive one bad job
                pass
            self._files_wake.wait(FILES_TICK_S)
            self._files_wake.clear()

    def _files_from_worktree(self, worktree, base):
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

    def _files_from_patch(self, job_dir, repo):
        patch_path = os.path.join(job_dir, "diff.patch")
        if not os.path.isfile(patch_path):
            return None
        cwd = repo or job_dir
        out = self._run_git(["apply", "--numstat", patch_path], cwd)
        if out is None:
            return None
        return _parse_numstat(out)
