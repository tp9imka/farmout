#!/bin/bash
# Real-subscription smoke: one read job and one write job per adapter.
# Spends a few requests per CLI. Usage: tests/smoke-real.sh [cli...]
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FARMOUT="$ROOT/bin/farmout"
T="$(cd "$(mktemp -d "${TMPDIR:-/tmp}/farmout-smoke.XXXXXX")" && pwd -P)"
trap 'rm -rf "$T"' EXIT
export FARMOUT_HOME="$T/home"
REPO="$T/repo"
mkdir -p "$REPO" && git -C "$REPO" init -q && echo hello > "$REPO/a.txt"
git -C "$REPO" add a.txt && git -C "$REPO" -c user.name=t -c user.email=t@t commit -qm init

printf '%s\n' 'Reply with exactly the word PONG and nothing else. Do not run any tools.' > "$T/read.md"
printf '%s\n' 'Create a file named SMOKE.txt in the current directory containing exactly the word SMOKE.' \
  'Do not modify any other file. Do not commit. Then reply with the word DONE.' > "$T/write.md"

bad=0
for cli in ${*:-codex kiro copilot cursor}; do
  out="$(cd "$REPO" && "$FARMOUT" run "$cli" --timeout 5m --brief "$T/read.md" 2>&1)"
  id="$(echo "$out" | head -1)"
  res="$(tr -d '[:space:]' < "$FARMOUT_HOME/jobs/$id/result.md" 2>/dev/null)"
  if [ "$(echo "$out" | tail -1)" = "$id ok" ] && [ "$res" = PONG ]; then echo "ok   $cli read"
  else echo "FAIL $cli read: $(echo "$out" | tail -1) result=[$res]"; tail -20 "$FARMOUT_HOME/jobs/$id/log"; bad=$((bad + 1)); fi

  out="$(cd "$REPO" && "$FARMOUT" run "$cli" --write --timeout 5m --brief "$T/write.md" 2>&1)"
  id="$(echo "$out" | head -1)"
  if [ "$(echo "$out" | tail -1)" = "$id ok" ] && grep -q '^+SMOKE' "$FARMOUT_HOME/jobs/$id/diff.patch" 2>/dev/null \
     && grep -q 'SMOKE.txt' "$FARMOUT_HOME/jobs/$id/diff.patch"; then echo "ok   $cli write"
  else echo "FAIL $cli write: $(echo "$out" | tail -1)"; tail -20 "$FARMOUT_HOME/jobs/$id/log"; bad=$((bad + 1)); fi
  "$FARMOUT" discard "$id" >/dev/null 2>&1
done
[ -z "$(git -C "$REPO" status --porcelain)" ] || { echo "FAIL checkout was modified"; bad=$((bad + 1)); }
echo "failures: $bad"
[ "$bad" -eq 0 ]
