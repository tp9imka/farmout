# Changelog

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
