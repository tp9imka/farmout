"""Claude Code session model: registry + transcript -> dashboard records.

Stdlib-only, targets /usr/bin/python3 (3.9): no match statements, no `X | Y`
types.
"""

import datetime
import difflib
import glob
import json
import os
import re
import signal
import subprocess
import time

import logs

IDLE_AFTER_MIN = 15
# How long END waits for a signalled session to exit before reporting it alive.
END_WAIT_S = 5.0
END_POLL_S = 0.25
REPLAY_MAX = logs.REPLAY_MAX
REPLAY_TEXT_MAX = logs.REPLAY_TEXT_MAX

# Priority order for a tool_use's "main arg" in the replay line.
_MAIN_ARG_KEYS = ("command", "file_path", "pattern", "url", "description", "skill")

_PS_LINE_RE = re.compile(r"^\s*(\d+)\s+(\S+)\s+(.*)$")


def ps_sweep():
    """One sweep of pid -> (stat, lstart_utc), forcing TZ=UTC on the ps call.

    lstart is a ctime-style string ("Wed Sep 23 06:29:13 2026"), the same
    format the registry's procStart uses (Claude writes it with
    `LC_ALL=C TZ=UTC ps -o lstart=`), so the pid-reuse guard can compare them
    as plain strings regardless of the caller's own ambient TZ or locale.
    LC_ALL matters as much as TZ: under a non-C locale, ps localises the
    weekday/month names (and even the whole format), so without forcing it
    every live session would wrongly look pid-reused.
    """
    env = dict(os.environ)
    env["TZ"] = "UTC"
    env["LC_ALL"] = "C"
    try:
        out = subprocess.check_output(
            ["ps", "-A", "-o", "pid=,stat=,lstart="], env=env
        ).decode("utf-8", errors="replace")
    except (OSError, subprocess.CalledProcessError):
        return {}

    result = {}
    for line in out.splitlines():
        m = _PS_LINE_RE.match(line)
        if not m:
            continue
        result[int(m.group(1))] = (m.group(2), m.group(3).rstrip())
    return result


def _liveness(pid, proc_start, ps_map):
    """(is_live, stat) for pid. Zombie and pid-reuse both mean not live."""
    entry = ps_map.get(pid)
    if entry is None:
        return False, None
    stat, lstart = entry
    if stat[:1] == "Z":
        return False, stat
    return lstart == proc_start, stat


def _iso_to_ms(ts):
    if not ts:
        return None
    text = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
    try:
        dt = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    return int(dt.timestamp() * 1000)


def _difflib_counts(old_text, new_text):
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
    added = 0
    removed = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        removed += i2 - i1
        added += j2 - j1
    return added, removed



def _title(reg, ai_title):
    """A name the user set (/rename) beats the AI title; a derived name
    (repo-hash) is only a fallback when there is no AI title yet."""
    name = reg.get("name")
    if name and reg.get("nameSource") == "user":
        return name
    return ai_title or name

class _TranscriptState(object):
    """Cumulative, incrementally-updated view of one session's transcript."""

    def __init__(self):
        self.reader = logs.IncrementalReader()
        self.usage_by_id = {}
        self.open_tool_ids = {}
        self.tool = None
        self.replay = []
        self.title = None
        self.prompt = None
        self.model = None
        self.branch = None
        self.files = {}
        self.bad_lines = 0

    def feed_line(self, raw_line):
        text = raw_line.strip()
        if not text or text[0] != "{":
            return
        try:
            entry = json.loads(text)
        except ValueError:
            self.bad_lines += 1
            return
        self._apply(entry)

    def _apply(self, entry):
        if "gitBranch" in entry:
            self.branch = entry["gitBranch"]

        kind = entry.get("type")
        if kind == "ai-title":
            if "aiTitle" in entry:
                self.title = entry["aiTitle"]
        elif kind == "last-prompt":
            if "lastPrompt" in entry:
                self.prompt = entry["lastPrompt"]
        elif kind == "assistant":
            self._apply_assistant(entry)
        elif kind == "user":
            self._apply_user(entry)

    def _apply_assistant(self, entry):
        message = entry.get("message") or {}
        model = message.get("model")
        if model:
            self.model = model

        message_id = message.get("id")
        usage = message.get("usage")
        if message_id and usage:
            self.usage_by_id[message_id] = {
                "in": usage.get("input_tokens") or 0,
                "out": usage.get("output_tokens") or 0,
                "cache": usage.get("cache_read_input_tokens") or 0,
            }

        timestamp_ms = _iso_to_ms(entry.get("timestamp"))
        for block in message.get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            tool_id = block.get("id")
            name = block.get("name") or ""
            tool_input = block.get("input") or {}
            if tool_id:
                self.open_tool_ids[tool_id] = True
            self._add_replay(name, tool_input, timestamp_ms)
            self._add_files(name, tool_input)

    def _apply_user(self, entry):
        message = entry.get("message") or {}
        content = message.get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tool_id = block.get("tool_use_id")
                if tool_id in self.open_tool_ids:
                    del self.open_tool_ids[tool_id]

    def _add_replay(self, name, tool_input, timestamp_ms):
        arg = ""
        for key in _MAIN_ARG_KEYS:
            value = tool_input.get(key)
            if value:
                arg = value
                break
        text = "{} {}".format(name, arg) if arg else name
        self.tool = name
        self.replay.append({"t": timestamp_ms, "m": text[:REPLAY_TEXT_MAX]})
        if len(self.replay) > REPLAY_MAX:
            del self.replay[: len(self.replay) - REPLAY_MAX]

    def _add_files(self, name, tool_input):
        path = tool_input.get("file_path")
        if not path:
            return
        if name == "Edit":
            added, removed = _difflib_counts(
                tool_input.get("old_string") or "", tool_input.get("new_string") or ""
            )
            self._merge_file(path, added, removed)
        elif name == "MultiEdit":
            for edit in tool_input.get("edits") or []:
                added, removed = _difflib_counts(
                    edit.get("old_string") or "", edit.get("new_string") or ""
                )
                self._merge_file(path, added, removed)
        elif name == "Write":
            content = tool_input.get("content") or ""
            self._merge_file(path, len(content.splitlines()), None)

    def _merge_file(self, path, added, removed):
        entry = self.files.setdefault(path, {"a": 0, "d": 0})
        entry["a"] += added
        if removed is None or entry["d"] is None:
            entry["d"] = None
        else:
            entry["d"] += removed


def _sum_usage(usage_by_id):
    totals = {"in": 0, "out": 0, "cache": 0}
    for usage in usage_by_id.values():
        totals["in"] += usage["in"]
        totals["out"] += usage["out"]
        totals["cache"] += usage["cache"]
    return totals


def _files_list(files_dict):
    return [
        {"n": path, "a": entry["a"], "d": entry["d"]}
        for path, entry in sorted(files_dict.items())
    ]


def _derive_status(status, status_updated_at, now_ms, has_open_tool_use):
    if status == "busy":
        return "play" if has_open_tool_use else "think"
    if status in ("idle", "shell"):
        if isinstance(status_updated_at, int) and (
            now_ms - status_updated_at <= IDLE_AFTER_MIN * 60 * 1000
        ):
            return "turn"
        return "idle"
    if not status:
        return None
    return status.upper()


class SessionsModel(object):
    def __init__(self, claude_home, ps_sweep=ps_sweep):
        self.claude_home = claude_home
        self.sessions_dir = os.path.join(claude_home, "sessions")
        self.projects_dir = os.path.join(claude_home, "projects")
        self._ps_sweep = ps_sweep
        self._transcripts = {}
        self._transcript_paths = {}
        self._transcript_stat = {}

    def snapshot(self, now_ms, jobs):
        errors = []
        sessions = []
        registries = self._read_registries(errors)
        ps_map = self._ps_sweep()
        live_ids = set()

        for reg in registries:
            pid = reg.get("pid")
            session_id = reg.get("sessionId")
            if not session_id or not isinstance(pid, int):
                continue
            is_live, stat = _liveness(pid, reg.get("procStart"), ps_map)
            if not is_live:
                continue
            live_ids.add(session_id)
            sessions.append(self._build_session(reg, now_ms, jobs, errors, stat))

        self._prune_dead(live_ids)
        return {"sessions": sessions, "errors": errors}

    def end_suspended(self, session_id, kill=os.kill, sleep=time.sleep):
        """End every suspended process registered for session_id.

        Only a process that is still the registered one (start time matches)
        and still stopped (stat T) is signalled, so a pid reused since the last
        poll, or a live copy of the same session, is never touched. SIGTERM
        goes first so it is pending when SIGCONT wakes the process. No SIGKILL:
        a survivor is reported, not escalated.
        Returns (ended_pids, survivor_pids); both empty if nothing matched.
        """
        targets = []
        ps_map = self._ps_sweep()
        for reg in self._read_registries([]):
            pid = reg.get("pid")
            if reg.get("sessionId") != session_id or not isinstance(pid, int):
                continue
            is_live, stat = _liveness(pid, reg.get("procStart"), ps_map)
            if is_live and stat[:1] == "T":
                targets.append((pid, reg.get("procStart")))
        for pid, _ in targets:
            try:
                kill(pid, signal.SIGTERM)
                kill(pid, signal.SIGCONT)
            except ProcessLookupError:
                pass
        waited = 0.0
        pending = list(targets)
        while pending and waited < END_WAIT_S:
            sleep(END_POLL_S)
            waited += END_POLL_S
            ps_map = self._ps_sweep()
            pending = [(pid, start) for pid, start in pending if _liveness(pid, start, ps_map)[0]]
        survivors = [pid for pid, _ in pending]
        ended = [pid for pid, _ in targets if pid not in survivors]
        return ended, survivors

    def _read_registries(self, errors):
        try:
            names = os.listdir(self.sessions_dir)
        except FileNotFoundError:
            return []
        except OSError as exc:
            errors.append("claude sessions: {}".format(exc.strerror or exc))
            return []

        registries = []
        for name in names:
            if not name.endswith(".json"):
                continue
            path = os.path.join(self.sessions_dir, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    registries.append(json.load(f))
            except (OSError, ValueError) as exc:
                reason = getattr(exc, "strerror", None) or str(exc)
                errors.append(
                    "claude sessions: cannot read {}: {}".format(path, reason)
                )
        return registries

    def _transcript_path(self, session_id):
        cached = self._transcript_paths.get(session_id)
        if cached:
            return cached
        pattern = os.path.join(self.projects_dir, "*", session_id + ".jsonl")
        matches = glob.glob(pattern)
        if not matches:
            return None
        self._transcript_paths[session_id] = matches[0]
        return matches[0]

    def _prune_dead(self, live_ids):
        for session_id in list(self._transcripts):
            if session_id not in live_ids:
                del self._transcripts[session_id]
                self._transcript_paths.pop(session_id, None)
                self._transcript_stat.pop(session_id, None)

    def _maybe_reset_transcript_state(self, session_id, path):
        # Mirrors logs.IncrementalReader's own reset condition (new inode,
        # or size dropped below what we already consumed) so our cumulative
        # state resets in lockstep with the reader instead of double
        # counting stale replay/files/usage against a rotated or truncated
        # transcript. Tracked independently here rather than in logs.py.
        try:
            st = os.stat(path)
        except OSError:
            return
        last = self._transcript_stat.get(session_id)
        if last is None:
            return
        last_ino, last_size = last
        if st.st_ino != last_ino or st.st_size < last_size:
            self._transcripts.pop(session_id, None)
            self._transcript_stat.pop(session_id, None)

    def _remember_transcript_stat(self, session_id, path):
        try:
            st = os.stat(path)
        except OSError:
            return
        self._transcript_stat[session_id] = (st.st_ino, st.st_size)

    def _build_session(self, reg, now_ms, jobs, errors, stat):
        session_id = reg["sessionId"]
        path = self._transcript_path(session_id)
        if path:
            self._maybe_reset_transcript_state(session_id, path)

        state = self._transcripts.get(session_id)
        if state is None:
            state = _TranscriptState()
            self._transcripts[session_id] = state

        if path:
            for raw_line in state.reader.read_new(path):
                state.feed_line(raw_line)
            self._remember_transcript_stat(session_id, path)

        if state.bad_lines:
            errors.append(
                "claude transcript: {} unparsable line(s) in {}".format(
                    state.bad_lines, session_id
                )
            )

        if path is None:
            tokens = None
            score = None
        else:
            tokens = _sum_usage(state.usage_by_id)
            score = tokens["in"] + tokens["out"]

        cwd = reg.get("cwd")
        started = reg.get("startedAt")
        crew = [j.get("id") for j in jobs if j.get("parent_session") == session_id]
        if stat[:1] == "T":
            status = "SUSPENDED"
        else:
            status = _derive_status(
                reg.get("status"),
                reg.get("statusUpdatedAt"),
                now_ms,
                bool(state.open_tool_ids),
            )

        return {
            "id": session_id,
            "name": reg.get("name"),
            "repo": os.path.basename(cwd) if cwd else None,
            "branch": state.branch,
            "cwd": cwd,
            "model": state.model,
            "status": status,
            "title": _title(reg, state.title),
            "prompt": state.prompt,
            "tool": state.tool,
            "started": started,
            "elapsed_s": int((now_ms - started) / 1000) if isinstance(started, int) else None,
            "tokens": tokens,
            "score": score,
            "replay": list(state.replay),
            "files": _files_list(state.files),
            "crew": crew,
        }
