"""Semantic progress of a worker job, read from its log.

"Progress" is a structured event that proves the agent did something: a
message or reasoning chunk, a tool call starting or finishing, a turn
completing. Everything else - non-JSON text (spinners, auth prompts, retry
notices), setup events, usage/context snapshots - is ignored, so a CLI stuck
on an OAuth spinner shows no progress however fast its log grows.

There are no timestamps here on purpose: log lines carry none that all CLIs
agree on. The caller owns the clock - it notes when `events` last changed,
and a job is stalled once that is older than stall_min with no tool open.

Stdlib-only, targets /usr/bin/python3 (3.9).

CLI: python3 progress.py <cli> <log>  ->  {"events": N, "open_tools": K}
     or {"unknown": "<reason>"}; exit 0 either way.
"""

import json
import os
import re
import sys

# A one-shot scan reads at most this much from the end of the log; a longer
# log reports unknown rather than a count missing its beginning.
MAX_SCAN_BYTES = 8 * 1024 * 1024

_CODEX_TOOL_ITEMS = ("command_execution", "mcp_tool_call", "web_search", "file_change")
_KIRO_PROGRESS = ("agent_message_chunk", "agent_thought_chunk", "plan")
_COPILOT_PROGRESS = (
    "assistant.turn_start", "assistant.message", "assistant.message_delta",
    "assistant.reasoning", "assistant.reasoning_delta", "assistant.turn_end",
)

# Split on \n, \r\n and a bare \r: spinners redraw with bare \r and never
# send \n, so splitting on \n alone would grow one unbounded "line".
LINE_BREAK = re.compile(r"\r\n|\r|\n")


def _codex(obj):
    kind = obj.get("type")
    item = obj.get("item") or {}
    tool = item.get("id") if item.get("type") in _CODEX_TOOL_ITEMS else None
    if kind == "item.started":
        return True, tool, None
    if kind == "item.completed":
        return True, None, tool
    return kind in ("turn.started", "item.updated", "turn.completed"), None, None


def _kiro(obj):
    if obj.get("type") != "sessionUpdate":
        return False, None, None
    update = (obj.get("data") or {}).get("update") or {}
    kind = update.get("sessionUpdate")
    tool = update.get("toolCallId")
    if kind == "tool_call":
        return True, tool, None
    if kind == "tool_call_update":
        done = update.get("status") in ("completed", "failed")
        return True, None, tool if done else None
    return kind in _KIRO_PROGRESS, None, None


def _copilot(obj):
    kind = obj.get("type")
    tool = (obj.get("data") or {}).get("toolCallId")
    if kind == "tool.execution_start":
        return True, tool, None
    if kind == "tool.execution_complete":
        return True, None, tool
    return kind in _COPILOT_PROGRESS or kind == "tool.execution_partial_result", None, None


def _cursor(obj):
    kind = obj.get("type")
    if kind == "tool_call":
        tool = obj.get("call_id")
        if obj.get("subtype") == "started":
            return True, tool, None
        if obj.get("subtype") == "completed":
            return True, None, tool
        return False, None, None
    return kind in ("thinking", "assistant", "result"), None, None


_PREDICATES = {"codex": _codex, "kiro": _kiro, "copilot": _copilot, "cursor": _cursor}


class Progress(object):
    """Counts progress events and tracks open tool calls, line by line."""

    def __init__(self, cli):
        if cli not in _PREDICATES:
            raise ValueError("unknown cli: {}".format(cli))
        self._predicate = _PREDICATES[cli]
        self.events = 0
        self._open = set()

    @property
    def open_tools(self):
        return len(self._open)

    def feed(self, line):
        text = line.strip()
        if not text or text[0] != "{":
            return
        try:
            obj = json.loads(text)
        except ValueError:
            return
        if not isinstance(obj, dict):
            return
        progressed, opened, closed = self._predicate(obj)
        if opened:
            self._open.add(opened)
        if closed:
            self._open.discard(closed)
        if progressed:
            self.events += 1


def scan(cli, log_path):
    """One-shot full scan: {"events", "open_tools"} or {"unknown": reason}."""
    if cli not in _PREDICATES:
        return {"unknown": "no progress parser for {}".format(cli)}
    try:
        size = os.path.getsize(log_path)
        if size > MAX_SCAN_BYTES:
            return {"unknown": "log larger than {} bytes".format(MAX_SCAN_BYTES)}
        with open(log_path, "rb") as f:
            text = f.read().decode("utf-8", errors="replace")
    except OSError as exc:
        return {"unknown": "cannot read log: {}".format(exc.strerror or exc)}
    progress = Progress(cli)
    for line in LINE_BREAK.split(text):
        progress.feed(line)
    return {"events": progress.events, "open_tools": progress.open_tools}


def main(argv):
    if len(argv) != 3:
        sys.stderr.write("usage: progress.py <cli> <log>\n")
        return 2
    sys.stdout.write(json.dumps(scan(argv[1], argv[2])) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
