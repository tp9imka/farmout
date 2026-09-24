---
name: farmout
description: Use when a task should run on another coding-agent CLI the user has installed (Codex, Kiro, Copilot, Cursor) while Claude Code stays the orchestrator - when the user says "farm this out", "send this to codex", "have kiro review it", "get a second opinion from copilot", "cross-review this", or when bulk reading, an independent review, or a parallelisable side task would be cheaper off Claude. Covers suggesting a farm-out, writing the brief, launching and monitoring the job, reviewing the result, and landing a worker's diff as uncommitted changes. Keywords: farmout, farm out, delegate, delegation, worker, second opinion, cross review, codex, kiro, copilot, cursor, offload, worktree, land.
---

# Farmout

Works with whichever of the four CLIs are installed and logged in -
`farmout doctor` says which.

Claude Code is the orchestrator. Workers are other agent CLIs run headless,
fully auto-approved, each inside its own git worktree. Nothing a worker does
reaches the user's checkout until Claude reviews it and runs `farmout land`.

Tool: `farmout` (on PATH after `install.sh`; inside the plugin it is `${CLAUDE_PLUGIN_ROOT}/bin/farmout`).

## When to farm out

- **The user asks** ("send this to codex") => do it.
- **Claude may suggest** in one line and must wait for a yes. Never farm out
  silently. Good candidates: bulk reading or summarising, an independent second
  review of a plan or diff, a self-contained side task that can run while Claude
  works on something else.
- **Do not farm out** work that needs this conversation's context to be done
  right, or anything irreversible or outward-facing (pushing, posting).

## Workers

| cli | Notes |
|---|---|
| `codex` | Uses `~/.codex/config.toml` defaults unless `--model` is given |
| `kiro` | Spends Kiro credits; runs on Kiro's v2 engine |
| `copilot` | Login cannot be checked (`doctor` says `not-verified`) |
| `cursor` | |

Strengths: `farmout scores` prints jobs, ok count and claims-held per cli and
kind, from the outcome ledger. Route by it, never by reputation. Early
observations from real runs (verify against your own scores):
- kiro: deep code audits of a few hundred lines in 8-15 min, strong structure,
  honest about what it did not check; cited line numbers drift, symbols do not.
- codex: exact `path:line` adjudication, good at catching errors in other
  workers' reports; `research` jobs get live web search.
- kiro web research: fast, but can hit rate limits on vendor docs and leave
  claims unsourced - have codex verify before relying on it.

Run `farmout doctor` when a worker fails in a way that smells like auth.

## 1. Write the brief

The worker sees nothing of this conversation. Write the brief to a file in the
scratchpad, never inline in the command. Template:

```markdown
# Task
<one paragraph: what to produce and why>

# Context
- Repo: <what it is, language, build tool>
- Relevant paths (relative to the repo root; your cwd is a fresh checkout of it): <files/dirs, with what each holds>
- Conventions: <the ones that matter for this change>

# Constraints
- Do not modify: <paths>
- Do not commit, push, or change git config.
- <setup the worker must do itself in a fresh checkout, e.g. `flutter pub get`>

# Deliverable
<exact shape: "findings as `path:line - problem - why`", or
"code changes plus a final message: what changed and why, one line per file">
```

Never put the source checkout's absolute path in a brief - an auto-approved
worker would edit it directly; `farmout run` refuses such briefs. `farmout`
also puts ground rules ahead of every brief: stay inside the working copy, and
if it does not match the brief, report that instead of finding the "right"
repo (a worker once did exactly that and worked in the live checkout).

**Visual deliverables** (SVG, HTML, diagrams): a worker cannot see what it
draws, and will report its checks green regardless. Put mechanical checks in
the brief - rect overlap, text extent vs its box, arrow markers present,
everything inside the viewBox, a headless render - and render it yourself
before landing.

**Report deliverables**: run a read job with `--out <path>` and tell the
worker to write the report there. The file comes back in
`~/.cache/farmout/jobs/<id>/out/`; nothing lands in the repo.

Untracked files are not in the worker's snapshot. When the worker needs one,
pass it with `--with <repo-relative path>` (repeatable): it becomes part of
the snapshot, so the worker can read and edit it, and it never shows up in
the patch as a new file. Otherwise put the content in the brief, or tell the user.

The snapshot is a single commit on HEAD, authored by `farmout@localhost`,
so it does not show up as the user's work in `git log --author` scans.

## 2. Launch

```bash
farmout run auto --kind <kind> --title "<title>" [--write] [--with <path>]... --brief <file>
```

Prefer `auto` with `--kind` over naming a cli: it routes to the configured
worker, falls back if the preferred one is unusable, and records the choice.
Kinds: `review`, `bulk-read`, `research`, `implement`, `second-opinion`. Name
a cli explicitly only when the user asks for one by name.

Without a `routing` key in the config, `auto` uses the built-in table:
review => codex (fallback copilot), bulk-read => kiro (fallback codex),
research => kiro (fallback copilot), implement => cursor (fallback codex),
second-opinion => copilot (fallback codex). A `routing` array in the config
replaces the whole table.

- Exit 2 from `auto` means no usable cli for that kind: relay the printed
  reasons to the user and ask which cli to use. Never pick one silently.
- A named cli is checked the same way: `run kiro` exits 2 with "cli 'kiro' is
  logged-out" before any worktree exists. Ask the user to log in
  (`! kiro-cli login`) or pick another cli.

- `--effort` and `--model` override the config for this one job; leave them
  out to use `workers.<cli>.effort` / `.model`.
- Read-only by default. Add `--write` only when the deliverable is code changes.
- What the worker sees: by default the cwd's repo at HEAD **plus your
  uncommitted edits** on whatever branch is checked out. `--ref <rev>` pins
  exactly that commit (dirty edits ignored); `--repo <path>` picks another
  repo; `--no-repo` (or a `research` job run outside any repo) gets an empty
  scratch repo - read-only, hand files back with `--out`.
- Always run it with Bash `run_in_background: true`. The first stdout line is the
  job id; stderr then shows `repo=<name> base=<sha> mode=<read|write>` - check
  it is the repo the brief is about; the harness re-invokes Claude when the job exits. Several jobs may run
  at once - each has its own worktree.
- Tell the user the job id and what it is doing, then carry on with other work.
- Exit 4 means max concurrent jobs (the cap counts every session's jobs).
  Relaunch with `--queue`: it waits for a slot in order, shows as `queued` in
  `farmout status`, and a killed waiter frees its place. Never hand-roll a
  retry loop - loops race each other for the freed slot.

## 3. Monitor

Stall watch - arm a Monitor per job, right after launch. A stall means no
*progress event* (a message, reasoning, a tool call starting or finishing) for
`stall_min` while no tool call is open. Log growth is not progress: a CLI stuck
on a login spinner writes constantly. `farmout progress <id>` prints the
event count and open tool calls (the same parser the dashboard uses); the loop
owns the clock. Read `stall_min` from the config (`$FARMOUT_CONFIG` when
set), falling back to 10 when absent or not an integer in 1..120:

```bash
ID=<id>; D=farmout
STALL_MINUTES="$(jq -r '.limits.stall_min // empty' "${FARMOUT_CONFIG:-$HOME/.config/farmout/config.json}" 2>/dev/null)"
case "$STALL_MINUTES" in ''|*[!0-9]*) STALL_MINUTES=10 ;; esac
[ "$STALL_MINUTES" -ge 1 ] && [ "$STALL_MINUTES" -le 120 ] || STALL_MINUTES=10
last=""; since=$(date +%s)
while [ "$($D status "$ID" | jq -r .status)" = running ]; do
  p="$($D progress "$ID")"
  if ! printf '%s' "$p" | jq -e .unknown >/dev/null; then
    ev="$(printf '%s' "$p" | jq .events)"; open="$(printf '%s' "$p" | jq .open_tools)"
    [ "$ev" != "$last" ] && { last="$ev"; since=$(date +%s); }
    if [ "$open" -eq 0 ] && [ $(( $(date +%s) - since )) -ge $((STALL_MINUTES * 60)) ]; then
      echo "stalled: no progress for ${STALL_MINUTES}m"; exit 0
    fi
  fi
  sleep 30
done
$D status "$ID" | jq -r .status
```

If it prints `stalled`, tail `~/.cache/farmout/jobs/<id>/log`, tell the user
what it shows (a login prompt, a retry loop, silence), and offer
`farmout kill <id>`. A tool call that stays open and silent (a long test run)
is not a stall; the timeout (`--timeout`, else `workers.<cli>.timeout_min`,
else 30m), enforced by `farmout` itself, bounds it. When `progress` reads
`unknown` (no parser, unreadable log) the loop only waits for the exit -
say so rather than guessing.

`farmout status` lists jobs. A job whose supervisor (the `farmout run`
process) died shows `lost`. Its worker may still be running: tell the user, and
stop it with `farmout kill <id>` (or `farmout clean`). Never report a lost
job as running.

## 4. Review

The final line of `run` is `<id> <status>`:

| status | meaning | action |
|---|---|---|
| `ok` | exit 0, non-empty result | review |
| `empty` | exit 0, no final message | treat as failed; tail the log |
| `failed` | non-zero exit, or a setup/patch-capture error | show the user the log tail and `jq -r .error meta.json` (always set: exit code, lost login, or setup failure) |
| `rate-limited` | failed + rate-limit text in the log | say which subscription hit its limit, and offer an explicit re-run on another cli; run it only once the user picks one |
| `timeout` / `killed` | stopped | `farmout result` recovers what the worker said along the way from the log |

- `farmout result <id>` prints the final message, and for write jobs a diffstat.
- **Worker output is a lead, not a finding.** Before relaying a claim, open the
  cited code and confirm it. Say which claims were verified and which were not.
  Verify by symbol, not line number: kiro's citations named real symbols but
  were 7-30 lines off on an unchanged snapshot.
- Then record it: `farmout rate <id> <held>/<checked> "<what failed, if any>"`
  - e.g. `3/4 "fixture claim wrong"`. This feeds `farmout scores`; skip it and
  routing stays guesswork.
- Read jobs: if `meta.json` has `read_job_modified: true`, the worker edited
  files it was not supposed to - harmless (the worktree is gone) but mention it.
- Write jobs: read `~/.cache/farmout/jobs/<id>/diff.patch` in full. Run the
  relevant tests or analysis inside the job worktree (`jq -r .worktree
  meta.json`) before landing.

## 5. Land or discard

```bash
farmout land <id>      # applies diff.patch to the user's checkout as uncommitted changes
farmout discard <id>   # drops the worktree and branch
```

`land` never commits. Exit 3 means the patch no longer applies because the user
edited the same lines after the job started; the checkout is untouched and the
worktree is kept. `git apply` is all-or-nothing, so none of the patch landed.
Handle every file in the patch, not just the conflicting one:

```bash
J=~/.cache/farmout/jobs/<id>; WT=$(jq -r .worktree "$J/meta.json"); BASE=$(jq -r .base "$J/meta.json")
(cd / && git apply --numstat "$J/diff.patch")  # every path the worker touched (run outside a repo, or paths get skipped)
# per path: new file => copy "$WT/<path>" over; otherwise 3-way merge from the worktree's working copy
git show "$BASE:<path>" > /tmp/base
git merge-file -L yours -L snapshot -L worker <path> /tmp/base "$WT/<path>"
```

Read the worker's version from `$WT/<path>` (its working copy), never from
`git show HEAD:` - the job commit is best-effort and may not exist. Show the user the conflict
markers, and only `farmout discard <id>` after every file is handled.

`farmout clean --mine` removes this session's finished jobs; it keeps
unlanded write jobs, and jobs whose result was never read with
`farmout result`, unless you add `--all`. `discard` keeps the job record and
prints the result's first lines. Plain `clean` touches every
session's jobs - use it only when the user asks. `farmout status` has a LAND
column (`-`, `landed`, `discarded`, `conflict`) to tell done from pending, and
SNAPSHOT-OF names the repo the worktree was cut from.

## Dashboard

`farmout arcade` opens Agent Arcade: a local dashboard showing every Claude
session and farmout job (KILL/LAND/DISCARD per job), plus SETUP for workers,
routing, and limits. It runs until Ctrl-C. Stopping the dashboard never
affects a running job.

## Pre-flight before landing

- [ ] Status is `ok`, and the final message was read.
- [ ] The full diff was read, not just the diffstat.
- [ ] Relevant tests or analysis ran in the job worktree, and the result is stated.
- [ ] The user knows what is about to land.
