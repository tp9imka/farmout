#!/bin/bash
# Real-subscription smoke: read/write per adapter, plus Codex/Kiro in-place.
# Spends a few requests per CLI. Usage: tests/smoke-real.sh [cli...]
# --dry-run-in-place [codex kiro] shows the disposable fixture and launch only.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FARMOUT="$ROOT/bin/farmout"
T="$(cd "$(mktemp -d "${TMPDIR:-/tmp}/farmout-smoke.XXXXXX")" && pwd -P)"
trap 'if [ "${FARMOUT_SMOKE_KEEP:-0}" = 1 ]; then echo "smoke artifacts: $T"; else rm -rf "$T"; fi' EXIT
export FARMOUT_HOME="$T/home"
source_state() {
  GIT_OPTIONAL_LOCKS=0 git -C "$ROOT" rev-parse HEAD
  GIT_OPTIONAL_LOCKS=0 git -C "$ROOT" symbolic-ref --quiet HEAD
  GIT_OPTIONAL_LOCKS=0 git -C "$ROOT" status --porcelain --untracked-files=all
  GIT_OPTIONAL_LOCKS=0 git -C "$ROOT" diff --binary
  GIT_OPTIONAL_LOCKS=0 git -C "$ROOT" diff --cached --binary
}
SOURCE_BEFORE="$(source_state)"
REPO="$T/repo"
mkdir -p "$REPO" && git -C "$REPO" init -q && echo hello > "$REPO/a.txt"
git -C "$REPO" add a.txt && git -C "$REPO" -c user.name=t -c user.email=t@t commit -qm init

smoke_failure() { # cli job-id output reason
  echo "FAIL $1 in-place: $4"
  printf '%s\n' "$3"
  if [ -f "$FARMOUT_HOME/jobs/$2/meta.json" ]; then
    jq '{status,error,commits,uncommitted,outside_owns,unattributed}' "$FARMOUT_HOME/jobs/$2/meta.json"
    tail -20 "$FARMOUT_HOME/jobs/$2/log"
  fi
  return 1
}

run_in_place_smoke() { # cli dry-run
  local cli="$1" dry="$2" repo="$T/in-place-$1" owned="$1.txt"
  local branch=feature/in-place-smoke brief="$T/in-place-$1.md"
  local before out id job sha status accept_out state_before state_after
  case "$cli" in codex|kiro) ;; *) echo "FAIL unsupported in-place smoke CLI: $cli"; return 1 ;; esac
  # All worker launches below must use a fresh fixture, never the source repo.
  case "$repo" in "$T"/in-place-*) ;; *) echo 'FAIL unsafe smoke checkout'; return 1 ;; esac
  [ "$repo" != "$ROOT" ] || { echo 'FAIL smoke targets source checkout'; return 1; }
  mkdir -p "$repo" && git -C "$repo" init -q || return 1
  git -C "$repo" symbolic-ref HEAD refs/heads/main || return 1
  git -C "$repo" config user.name smoke-worker
  git -C "$repo" config user.email smoke-worker@example.test
  git -C "$repo" config commit.gpgsign false
  printf '%s\n' hello > "$repo/$owned"
  printf '%s\n' untouched > "$repo/untouched.txt"
  git -C "$repo" add "$owned" untouched.txt && git -C "$repo" commit -qm init || return 1
  git -C "$repo" checkout -qb "$branch" || return 1
  printf 'Replace the content of %s with exactly SMOKE and commit only %s with message "test: in-place smoke". Do not edit any other path. Then reply with exactly DONE.\n' \
    "$owned" "$owned" > "$brief"
  if [ "$dry" = true ]; then
    printf 'checkout=%s branch=%s owns=%s\n' "$repo" "$(git -C "$repo" symbolic-ref --short HEAD)" "$owned"
    cat "$brief"
    printf 'launch: cd %q && ' "$repo"
    printf '%q ' "$FARMOUT" run "$cli" --in-place --owns "$owned" --timeout 5m --brief "$brief"
    printf '\n'
    return 0
  fi
  before="$(git -C "$repo" rev-parse HEAD)"
  out="$(cd "$repo" && "$FARMOUT" run "$cli" --in-place --owns "$owned" --timeout 5m --brief "$brief" 2>&1)"
  id="$(printf '%s\n' "$out" | head -1)"; job="$FARMOUT_HOME/jobs/$id"
  [ "$(printf '%s\n' "$out" | tail -1)" = "$id ok" ] || { smoke_failure "$cli" "$id" "$out" 'job was not ok'; return 1; }
  status="$("$FARMOUT" status "$id" | jq -r .status)"
  [ "$status" = ok ] && [ "$(tr -d '[:space:]' < "$job/result.md")" = DONE ] \
    || { smoke_failure "$cli" "$id" "$out" 'status/result mismatch'; return 1; }
  sha="$(git -C "$repo" rev-parse HEAD)"
  [ "$(git -C "$repo" rev-list --count "$before..$sha")" = 1 ] \
    && [ "$(git -C "$repo" rev-parse "$sha^")" = "$before" ] \
    && [ "$(git -C "$repo" show -s --format=%B "$sha")" = 'test: in-place smoke' ] \
    && [ "$(git -C "$repo" show -s --format='%an <%ae>' "$sha")" = 'smoke-worker <smoke-worker@example.test>' ] \
    && [ "$(git -C "$repo" diff-tree --no-commit-id --name-only -r "$sha")" = "$owned" ] \
    && [ "$(cat "$repo/$owned")" = SMOKE ] \
    && [ "$(cat "$repo/untouched.txt")" = untouched ] \
    || { smoke_failure "$cli" "$id" "$out" 'worker commit/content/author/owned paths mismatch'; return 1; }
  jq -e --arg repo "$repo" --arg branch "$branch" --arg owned "$owned" --arg sha "$sha" --arg base "$before" '
    .mode == "in-place" and .status == "ok" and .checkout == $repo and
    .source_branch == $branch and .branch_end == $branch and
    .worktree == null and .branch == null and .base == $base and .head_end == $sha and
    .owns == [$owned] and .land == null and (.commits | length) == 1 and
    .commits[0].sha == $sha and .commits[0].subject == "test: in-place smoke" and
    [.commits[0].files[].path] == [$owned] and .commits[0].shared == false and
    .uncommitted == [] and .outside_owns == [] and .unattributed == []
  ' "$job/meta.json" >/dev/null \
    || { smoke_failure "$cli" "$id" "$out" 'metadata/attribution mismatch'; return 1; }
  "$FARMOUT" result "$id" > "$T/in-place-$cli-result.txt" || return 1
  [ -d "$repo/.git" ] && [ "$(git -C "$repo" symbolic-ref --short HEAD)" = "$branch" ] \
    && [ -z "$(git -C "$repo" status --porcelain --untracked-files=all)" ] \
    && git -C "$repo" diff --cached --quiet && git -C "$repo" diff --quiet \
    || { smoke_failure "$cli" "$id" "$out" 'checkout/index not intact and clean'; return 1; }
  state_before="$(git -C "$repo" rev-parse HEAD; git -C "$repo" symbolic-ref HEAD; git -C "$repo" status --porcelain; git -C "$repo" worktree list --porcelain)"
  accept_out="$("$FARMOUT" accept "$id" 2>&1)" \
    || { smoke_failure "$cli" "$id" "$accept_out" 'accept failed'; return 1; }
  state_after="$(git -C "$repo" rev-parse HEAD; git -C "$repo" symbolic-ref HEAD; git -C "$repo" status --porcelain; git -C "$repo" worktree list --porcelain)"
  [ "$state_before" = "$state_after" ] && [ -d "$repo/.git" ] \
    && git -C "$repo" diff --cached --quiet && git -C "$repo" diff --quiet \
    && jq -e '.land == "accepted"' "$job/meta.json" >/dev/null \
    && [ "$(source_state)" = "$SOURCE_BEFORE" ] \
    || { smoke_failure "$cli" "$id" "$accept_out" 'accept changed Git or source checkout'; return 1; }
  printf 'ok   %s in-place job=%s status=ok commit=%s message="test: in-place smoke" author="smoke-worker <smoke-worker@example.test>" owns=%s attribution=verified accept=accepted checkout=intact index=clean source=unchanged\n' "$cli" "$id" "$sha" "$owned"
}

# Exercise the disposable-repo setup and show the exact launch without spending
# a subscription. This uses the same in-place path as the real smoke below.
if [ "${1:-}" = --dry-run-in-place ]; then
  shift
  for cli in ${*:-codex kiro}; do
    run_in_place_smoke "$cli" true || exit 1
  done
  exit 0
fi

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
  case "$cli" in
    codex|kiro) run_in_place_smoke "$cli" false || bad=$((bad + 1)) ;;
  esac
done
[ -z "$(git -C "$REPO" status --porcelain)" ] || { echo "FAIL checkout was modified"; bad=$((bad + 1)); }
[ "$(source_state)" = "$SOURCE_BEFORE" ] || { echo "FAIL source checkout was modified"; bad=$((bad + 1)); }
echo "failures: $bad"
[ "$bad" -eq 0 ]
