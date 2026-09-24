"""Incremental log reader and per-CLI log parsers for the Arcade job model.

Stdlib-only, targets /usr/bin/python3 (3.9): no match statements, no `X | Y`
types.
"""

import datetime
import json
import os
import re

import progress

REPLAY_MAX = 50
REPLAY_TEXT_MAX = 120


class IncrementalReader(object):
    """Reads only the bytes appended to a file since the last read.

    Reads in binary and keeps the offset in bytes, so a multi-byte UTF-8
    character split across two reads is never decoded mid-sequence: the raw
    byte tail after the last b"\\n" is held back until its newline arrives,
    and only complete lines get decoded. Resets (starts over from offset 0)
    when the file's inode changes or its size drops below the stored offset
    -- both signal the file isn't the one we were tailing.
    """

    def __init__(self):
        self._inode = None
        self._offset = 0
        self._partial = b""

    def read_new(self, path):
        try:
            st = os.stat(path)
        except OSError:
            return []

        if self._inode is None or st.st_ino != self._inode or st.st_size < self._offset:
            self._inode = st.st_ino
            self._offset = 0
            self._partial = b""

        with open(path, "rb") as f:
            f.seek(self._offset)
            chunk = f.read()
            self._offset = f.tell()

        # Split on \n, \r\n and a bare \r: spinners redraw with a bare \r and
        # never send \n, which would otherwise pile up in _partial forever.
        parts = _LINE_BREAK.split(self._partial + chunk)
        self._partial = parts.pop()
        return [p.decode("utf-8", errors="replace") for p in parts if p]


class LogState(object):
    """Accumulated, incrementally-updated view of one job's log."""

    def __init__(self, cli):
        self.cli = cli
        self.tool = None
        self.replay = []
        self.tokens = None
        self.coins = None
        self.bad_lines = 0
        self.progress = progress.Progress(cli)
        self._parse = _PARSERS[cli]

    def feed(self, line):
        self.progress.feed(line)
        text = line.strip()
        if not text or text[0] != "{":
            return
        try:
            obj = json.loads(text)
        except ValueError:
            self.bad_lines += 1
            return
        self._parse(self, obj)

    def _add_replay(self, tool, main_arg, t=None):
        text = "{} {}".format(tool, main_arg) if main_arg else tool
        self._push_replay(tool, text, t)

    def _add_replay_verbatim(self, tool, text, t=None):
        # kiro supplies its own human-readable title ("Running: <cmd>"); the
        # replay line IS that title, not "<Tool>
        # <title>" (which would stutter: "Bash Running: ...").
        self._push_replay(tool, text, t)

    def _push_replay(self, tool, text, t):
        self.tool = tool
        self.replay.append({"t": t, "m": text[:REPLAY_TEXT_MAX]})
        if len(self.replay) > REPLAY_MAX:
            del self.replay[: len(self.replay) - REPLAY_MAX]


def new_log_state(cli):
    if cli not in _PARSERS:
        raise ValueError("unknown cli: {}".format(cli))
    return LogState(cli)


def _iso_to_ms(ts):
    if not ts:
        return None
    text = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
    try:
        dt = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    return int(dt.timestamp() * 1000)


_LINE_BREAK = re.compile(rb"\r\n|\r|\n")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _pascal_case(name):
    """Title-case a snake_case or camelCase identifier word by word.

    "todoWrite" -> "TodoWrite", "file_change" -> "FileChange". Plain
    str.title() gets both wrong ("Todowrite", "File_Change").
    """
    if not name:
        return ""
    words = []
    for chunk in re.split(r"[_\-]+", name):
        if chunk:
            words.extend(_CAMEL_BOUNDARY.sub(" ", chunk).split(" "))
    return "".join(w.capitalize() for w in words if w)


# --- codex: JSONL, item.started/command_execution, turn.completed.usage ---

def _codex_parse(state, obj):
    kind = obj.get("type")
    if kind == "item.started":
        item = obj.get("item") or {}
        item_type = item.get("type") or ""
        if item_type == "command_execution":
            tool = "Bash"
            arg = item.get("command") or ""
        else:
            tool = _pascal_case(item_type)
            arg = item.get("command") or item.get("path") or item.get("pattern") or ""
        state._add_replay(tool, arg)
    elif kind == "turn.completed":
        usage = obj.get("usage") or {}
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        if input_tokens is not None and output_tokens is not None:
            state.tokens = (state.tokens or 0) + input_tokens + output_tokens


# --- cursor: JSONL, tool_call {started,completed}, final result.usage ---

def _cursor_tool_call_key(tool_call):
    for key in tool_call:
        if key.endswith("ToolCall"):
            return key
    return None


def _cursor_parse(state, obj):
    kind = obj.get("type")
    if kind == "tool_call" and obj.get("subtype") == "started":
        tool_call = obj.get("tool_call") or {}
        key = _cursor_tool_call_key(tool_call)
        if key is not None:
            name = key[: -len("ToolCall")]
            tool = "Bash" if name == "shell" else _pascal_case(name)
            inner = tool_call.get(key) or {}
            args = inner.get("args") or {}
            arg = (
                args.get("command")
                or args.get("path")
                or args.get("pattern")
                or args.get("query")
                or ""
            )
            started_at = tool_call.get("startedAtMs")
            t = int(started_at) if started_at is not None else None
            state._add_replay(tool, arg, t)
    elif kind == "result":
        usage = obj.get("usage") or {}
        input_tokens = usage.get("inputTokens")
        output_tokens = usage.get("outputTokens")
        if input_tokens is not None and output_tokens is not None:
            state.tokens = input_tokens + output_tokens


# --- kiro: ACP stream-json, sessionUpdate/tool_call, metadata.meteringUsage ---

_KIRO_KIND_TOOL = {
    "execute": "Bash",
    "read": "Read",
    "edit": "Edit",
    "search": "Grep",
    "fetch": "Web",
}


def _kiro_parse(state, obj):
    kind = obj.get("type")
    if kind == "sessionUpdate":
        update = (obj.get("data") or {}).get("update") or {}
        if update.get("sessionUpdate") == "tool_call":
            tool_kind = update.get("kind") or ""
            tool = _KIRO_KIND_TOOL.get(tool_kind, _pascal_case(tool_kind))
            title = update.get("title") or ""
            state._add_replay_verbatim(tool, title)
    elif kind == "metadata":
        for usage in (obj.get("data") or {}).get("meteringUsage") or []:
            value = usage.get("value")
            if value is None:
                continue
            if state.coins is None:
                state.coins = {"value": 0, "unit": "CR"}
            state.coins["value"] += value


# --- copilot: JSONL, tool.execution_start, final result.usage.premiumRequests ---

def _copilot_parse(state, obj):
    kind = obj.get("type")
    if kind == "tool.execution_start":
        data = obj.get("data") or {}
        name = data.get("toolName") or ""
        tool = _pascal_case(name)
        args = data.get("arguments") or {}
        arg = args.get("command") or args.get("path") or args.get("pattern") or ""
        t = _iso_to_ms(obj.get("timestamp"))
        state._add_replay(tool, arg, t)
    elif kind == "result":
        usage = obj.get("usage") or {}
        premium_requests = usage.get("premiumRequests")
        if premium_requests is not None:
            state.coins = {"value": premium_requests, "unit": "PR"}


_PARSERS = {
    "codex": _codex_parse,
    "cursor": _cursor_parse,
    "kiro": _kiro_parse,
    "copilot": _copilot_parse,
}
