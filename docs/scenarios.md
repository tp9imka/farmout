# Usage scenarios

Each scenario shows two things: what you would say to Claude Code (the `farmout` skill takes it from there), and the command it ends up running, in case you'd rather drive by hand. Every brief is a small Markdown file in the shape the skill uses: **Task / Context / Constraints / Deliverable**. The worker sees nothing but that file and the repo snapshot.

---

## 1. Cross-review: a different model reviews Claude's work

You and Claude just finished a change. Before you push, get an independent review from a model that didn't write it.

> *"Farm out a review of the current diff to codex. Focus on error paths and data loss."*

```bash
farmout run auto --kind review --title "review: export error paths" --brief review.md
```

The job is read-only. It sees your uncommitted edits, because the snapshot includes them. The skill doesn't relay its findings blind: it opens each cited `file:line`, confirms or rejects the finding, and tells you which is which. Then it records the verdict:

```bash
farmout rate <id> 4/5 "one finding was about code that is already fixed"
```

Over time, `farmout scores` shows which CLI's reviews actually hold up.

## 2. Second opinion on a disagreement

Claude and Codex disagree about an approach. Ask a third model, and don't tell it who said what.

> *"Get a second opinion from copilot on the streaming approach. Give it both options neutrally."*

```bash
farmout run auto --kind second-opinion --brief options.md
```

Tip: write the options in the brief as **A** and **B** with no attribution. The point of a second opinion is that it isn't anchored to either side.

## 3. Bulk reading while you keep working

Some questions need a lot of reading and very little judgement: *"List every tenant field the legacy mappers use"*, or *"Which endpoints still call the v1 client?"* Farm the reading out, and keep building with Claude in the meantime.

> *"Have kiro go through `src/legacy/mappers` and list every tenant field name with file and line. We'll keep going on the endpoint."*

```bash
farmout run auto --kind bulk-read --brief mappers.md
```

The dashboard tile shows the job's progress. When it finishes, the skill checks a sample of the citations before Claude uses the list.

## 4. Web research, with no repo at all

```bash
farmout run codex --kind research --no-repo --out findings.md --brief research.md
```

`--no-repo` gives the worker an empty scratch repo, so no source code is exposed. For Codex, `research` jobs switch on live web search. `--out findings.md` copies the report back to `~/.cache/farmout/jobs/<id>/out/`. Research workers can state things confidently without a source, so ask for a URL on every claim, and have a second worker verify anything you'll act on.

## 5. Parallel implementation, then land

Two independent changes, two workers, one review pass each:

> *"Have cursor add the last-write-wins strategy, and codex rename matrixRow to tenantRow. Both in parallel."*

```bash
farmout run auto --kind implement --write --brief lww.md
farmout run codex --write --brief rename.md
```

Write jobs come back as a `diff.patch`. The skill reads the whole diff, runs the tests inside the job's worktree, and then asks you:

```bash
farmout land <id>      # applied as uncommitted changes, nothing is committed
farmout discard <id>   # or throw it away
```

If you've edited the same lines since the job started, `land` refuses (exit 3) and applies nothing. The skill then merges each file with `git merge-file` and shows you the conflicts.

## 6. An audit that returns a report, on a pinned commit

You want an audit of `origin/main`, not your half-finished feature branch, and you want a report file rather than code changes.

```bash
farmout run kiro --ref origin/main --out 'audit/*.md' --timeout 45m --brief audit.md
```

`--ref` snapshots exactly that commit and ignores your working tree. `--out` hands back the report. Nothing can land in the repo, because it's a read-only job. For audits of other repos, add `--repo ~/src/other-service`.

## 7. Fan-out with a queue

Five lanes and a cap of four concurrent jobs, a cap that counts every Claude session on the machine:

```bash
for lane in client server keys certs docs; do
  farmout run auto --kind bulk-read --queue --title "audit: $lane" --brief "lanes/$lane.md" &
done
wait
```

`--queue` waits for a slot in order instead of failing with exit 4. `farmout status` lists the waiting jobs as `queued`. If a waiter is killed, even with SIGKILL, its place in line is freed.

## 8. The morning routine, on a subscription you'd otherwise not use

Any repeatable "read three systems and write a note" task can run headless on a spare subscription. Pass notes the snapshot can't see with `--with`:

```bash
farmout run kiro --write --with notes/yesterday.md --brief morning.md
```

The worker writes today's note into its worktree. You look at it in the arcade, then press LAND.

---

## Writing briefs that work

- **Self-contained.** The worker saw none of your conversation. Name the paths, conventions and setup it needs.
- **Never put your checkout's absolute path in a brief.** An auto-approved worker would edit it directly. `farmout run` refuses such briefs.
- **Ask for a checkable deliverable.** For example `path:line - problem - why`, or "a final message listing each changed file and why".
- **Visual work needs mechanical checks.** A worker can't see what it draws, so put checks in the brief: element overlap, text overflow, arrowheads present, a headless render. Render it yourself before landing.
- **Invite the worker to overturn the brief.** Add "if a premise here is wrong, say so first". The best findings are often that your framing was off.
