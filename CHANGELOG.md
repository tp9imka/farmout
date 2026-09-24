# Changelog

## 1.0.1 - 2026-09-24

- `farmout status <id>` answers `queued` for a job waiting with `--queue`
  (it said "no such job"), so the skill's monitor no longer treats a queued
  job as finished. The monitor recipe waits through `queued` and starts the
  stall clock at admission.
- The dashboard shows queued jobs (QUEUED tag); queue tickets now carry the
  job's metadata.
- `run` reads the brief once at launch: a queued job could otherwise run
  whatever the file held when it finally got a slot.
- `status` no longer lists a launch that is still claiming its slot, and no
  longer prints jq errors when such a job dir vanishes.

## 1.0.0 - 2026-09-24

First public release.

- `farmout run <cli|auto>`: headless jobs on codex, kiro-cli, copilot and
  cursor-agent, each in its own git worktree cut from a snapshot of your repo.
  `auto --kind` routes by config, with a fallback when a CLI is logged out.
- Read jobs return the final message (and `--out` files); write jobs return a
  patch that `land` applies as uncommitted changes, all or nothing.
- `--ref`, `--repo`, `--no-repo`, `--with`, `--queue`, `--timeout`,
  `--model`, `--effort`.
- Stall detection from each CLI's structured events (`farmout progress`);
  partial results recovered from killed jobs.
- Outcome ledger: `farmout rate` and `farmout scores`.
- Farmout Arcade dashboard (`farmout arcade`, `--demo`): Claude sessions and
  jobs as arcade tiles, KILL / LAND / DISCARD, END for suspended sessions,
  bench, Hall of Fame, and a SETUP editor for workers, routing and limits.
- Claude Code skill and plugin manifest; one-line installer.
