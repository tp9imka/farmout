#!/bin/bash
# Deterministic suite for farmout, driven by tests/fake-cli. Uses no subscription.
# Usage: tests/run.sh [name-filter]
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FARMOUT="$ROOT/bin/farmout"
FIX="$ROOT/tests/fixtures"

setup() {
  T="$(cd "$(mktemp -d "${TMPDIR:-/tmp}/farmout-test.XXXXXX")" && pwd -P)"
  export FARMOUT_HOME="$T/home"
  # A nonexistent path by default: no test may see a real ~/.config/farmout/config.json.
  export FARMOUT_CONFIG="$T/no-such-config.json"
  REPO="$T/repo dir"
  mkdir -p "$REPO"
  git -C "$REPO" init -q
  printf 'one\ntwo\nthree\n' > "$REPO/a.txt"
  git -C "$REPO" add a.txt
  git -C "$REPO" -c user.name=t -c user.email=t@t commit -qm init
}
teardown() {
  local m pgid
  for m in "$T"/home/jobs/*/meta.json; do
    [ -f "$m" ] || continue
    pgid="$(jq -r '.pgid // empty' "$m" 2>/dev/null)"
    case "$pgid" in ''|*[!0-9]*) continue ;; esac
    [ -n "$(pgrep -g "$pgid" 2>/dev/null)" ] && kill -KILL -- "-$pgid" 2>/dev/null
  done
  rm -rf "$T"
}

fail() { echo "    $*"; exit 1; }
assert_eq() { [ "$1" = "$2" ] || fail "expected [$2] got [$1]${3:+ ($3)}"; }
assert_contains() { case "$1" in *"$2"*) ;; *) fail "expected to contain [$2] in [$1]" ;; esac; }

brief() { printf '%s\n' "$@" > "$T/brief.md"; }
# run_job <args...>: sets OUT (stdout), ERR (stderr), RC, ID
run_job() {
  (cd "$REPO" && "$FARMOUT" run "$@" --brief "$T/brief.md") >"$T/out" 2>"$T/err"
  RC=$?; OUT="$(cat "$T/out")"; ERR="$(cat "$T/err")"; ID="$(head -1 "$T/out")"
}
meta() { jq -r ".$2" "$FARMOUT_HOME/jobs/$1/meta.json"; }
result() { cat "$FARMOUT_HOME/jobs/$1/result.md"; }
wait_for_meta_pid() { # id
  local i=0
  while [ $i -lt 50 ]; do
    [ "$(meta "$1" pid 2>/dev/null)" != null ] && [ -n "$(meta "$1" pid 2>/dev/null)" ] && return 0
    sleep 0.2; i=$((i + 1))
  done
  local diagnostic="" log
  for log in "$T/holder-$1-err" "$T/$1-err"; do
    [ ! -f "$log" ] || diagnostic="$diagnostic $(cat "$log")"
  done
  [ ! -f "$FARMOUT_HOME/jobs/$1/meta.json" ] \
    || diagnostic="$diagnostic $(jq -c '{admitted,status,error}' "$FARMOUT_HOME/jobs/$1/meta.json")"
  fail "job $1 never recorded a pid${diagnostic:+:$diagnostic}"
}

# ---- lib -------------------------------------------------------------------

make_feature_branch() { git -C "$REPO" branch -M feature/in-place-test; }

start_in_place_holder() { # id owns (sets HOLDER_RUNNER)
  local id="$1" owns="$2"
  printf '%s\n' 'FAKE: hang' > "$T/holder-$id.md"
  ( export FARMOUT_TEST_JOB_ID="$id"
    cd "$REPO" && "$FARMOUT" run fake --in-place --owns "$owns" --timeout 60s --brief "$T/holder-$id.md"
  ) > "$T/holder-$id-out" 2> "$T/holder-$id-err" &
  HOLDER_RUNNER=$!
  wait_for_meta_pid "$id"
}

stop_in_place_holder() { # id runner
  "$FARMOUT" kill "$1" >/dev/null
  wait "$2" 2>/dev/null
  return 0
}

track_in_place_paths() {
  mkdir -p "$REPO/dir"
  printf '%s\n' b > "$REPO/dir/b.txt"
  printf '%s\n' c > "$REPO/c.txt"
  git -C "$REPO" add dir/b.txt c.txt
  git -C "$REPO" -c user.name=t -c user.email=t@t commit -qm 'lane fixtures'
}

configure_in_place_author() {
  git -C "$REPO" config user.name repo-user
  git -C "$REPO" config user.email repo@example.test
}

# Lifecycle commands must preserve even staged/unstaged user work and linked
# checkouts. Optional locks are disabled so the probes do not refresh the index.
lifecycle_git_snapshot() { # checkout destination
  local repo="$1" dest="$2" index path
  mkdir -p "$dest/untracked"
  GIT_OPTIONAL_LOCKS=0 git -C "$repo" rev-parse HEAD > "$dest/head"
  GIT_OPTIONAL_LOCKS=0 git -C "$repo" symbolic-ref --quiet --short HEAD > "$dest/branch"
  index="$(GIT_OPTIONAL_LOCKS=0 git -C "$repo" rev-parse --git-path index)"
  case "$index" in /*) ;; *) index="$repo/$index" ;; esac
  cp "$index" "$dest/index"
  GIT_OPTIONAL_LOCKS=0 git -C "$repo" diff --cached --binary > "$dest/index.diff"
  GIT_OPTIONAL_LOCKS=0 git -C "$repo" diff --binary > "$dest/worktree.diff"
  GIT_OPTIONAL_LOCKS=0 git -C "$repo" status --porcelain=v1 -z --untracked-files=all > "$dest/status"
  GIT_OPTIONAL_LOCKS=0 git -C "$repo" worktree list --porcelain > "$dest/worktrees"
  while IFS= read -r -d '' path; do
    mkdir -p "$dest/untracked/$(dirname "$path")"
    cp "$repo/$path" "$dest/untracked/$path"
  done < <(GIT_OPTIONAL_LOCKS=0 git -C "$repo" ls-files --others --exclude-standard -z)
}

lifecycle_snapshot() { # destination
  lifecycle_git_snapshot "$REPO" "$1/checkout"
  lifecycle_git_snapshot "$T/lifecycle-linked" "$1/linked"
}

assert_lifecycle_git_unchanged() { # baseline
  lifecycle_snapshot "$T/lifecycle-after"
  diff -r "$1" "$T/lifecycle-after" || fail 'lifecycle changed Git or checkout state'
  rm -rf "$T/lifecycle-after"
  [ ! -s "$T/git-calls" ] || fail "lifecycle invoked Git: $(cat "$T/git-calls")"
}

prepare_lifecycle_checkout() {
  make_feature_branch
  git -C "$REPO" worktree add -q -b lifecycle-peer "$T/lifecycle-linked"
  printf 'staged user edit\n' >> "$REPO/a.txt"; git -C "$REPO" add a.txt
  printf 'unstaged user edit\n' >> "$REPO/a.txt"
  printf 'user untracked\n' > "$REPO/user.txt"
  printf 'linked user edit\n' >> "$T/lifecycle-linked/a.txt"
  # No lifecycle command under test may even inspect Git. Stored hashes are
  # snapshots, so accepting a record cannot depend on their current existence.
  mkdir -p "$T/no-git"
  printf '%s\n' '#!/bin/bash' 'printf "%s\n" "$*" >> "$FARMOUT_TEST_GIT_CALLS"' 'exit 99' > "$T/no-git/git"
  chmod +x "$T/no-git/git"
  export FARMOUT_TEST_GIT_CALLS="$T/git-calls"
}

lifecycle_record() { # id mode status [land-json]
  local job="$FARMOUT_HOME/jobs/$1"
  mkdir -p "$job"
  jq -n --arg r "$REPO" --arg mode "$2" --arg status "$3" --argjson land "${4:-null}" \
    --argjson pid "$$" '{mode:$mode,status:$status,land:$land,repo:$r,checkout:$r,
      source_branch:"feature/in-place-test",branch:null,worktree:null,cli:"fake",
      admitted:true,sup_pid:$pid,pgid:null,result_read:true,
      base:"missing-base-after-rebase",head_end:"missing-head-after-rebase",
      commits:[{sha:"missing-sha-after-rebase",subject:"stored snapshot",files:[],shared:false}],
      owns:["a.txt"],uncommitted:[],outside_owns:[],unattributed:[]}' > "$job/meta.json"
  printf 'stored result\n' > "$job/result.md"
  printf 'stored log\n' > "$job/log"
}

run_lifecycle() {
  PATH="$T/no-git:$PATH" "$FARMOUT" "$@" > "$T/action-out" 2> "$T/action-err"
  RC=$?; OUT="$(cat "$T/action-out")"; ERR="$(cat "$T/action-err")"
}

test_accept_marks_in_place_reviewed_without_touching_git() {
  prepare_lifecycle_checkout; lifecycle_record reviewed in-place ok
  cp -R "$FARMOUT_HOME/jobs/reviewed" "$T/expected-record"
  jq '.land = "accepted"' "$T/expected-record/meta.json" > "$T/expected-meta"
  mv "$T/expected-meta" "$T/expected-record/meta.json"
  lifecycle_snapshot "$T/before"
  run_lifecycle accept reviewed
  assert_eq "$RC" 0 "$ERR"; assert_eq "$(meta reviewed land)" accepted
  diff -r "$T/expected-record" "$FARMOUT_HOME/jobs/reviewed" || fail 'accept changed more than land'
  assert_lifecycle_git_unchanged "$T/before"
  assert_contains "$("$FARMOUT" help)" 'farmout accept <job>'
}

test_accept_refuses_write_job() {
  prepare_lifecycle_checkout
  local mode
  for mode in write read; do
    lifecycle_record "$mode" "$mode" ok
    cp -R "$FARMOUT_HOME/jobs/$mode" "$T/expected-$mode"
    lifecycle_snapshot "$T/before-$mode"
    run_lifecycle accept "$mode"
    assert_eq "$RC" 2 "$mode"; assert_contains "$ERR" 'only in-place jobs'
    diff -r "$T/expected-$mode" "$FARMOUT_HOME/jobs/$mode" || fail 'refusal changed record'
    assert_lifecycle_git_unchanged "$T/before-$mode"
  done
}

test_accept_refuses_running_job() {
  prepare_lifecycle_checkout
  local status ticket
  for status in running queued; do
    lifecycle_record "$status" in-place "$status"
    cp -R "$FARMOUT_HOME/jobs/$status" "$T/expected-$status"
    lifecycle_snapshot "$T/before-$status"
    run_lifecycle accept "$status"
    assert_eq "$RC" 2 "$status"; assert_contains "$ERR" "$status"; assert_contains "$ERR" 'finish'
    diff -r "$T/expected-$status" "$FARMOUT_HOME/jobs/$status" || fail 'live refusal changed record'
    assert_lifecycle_git_unchanged "$T/before-$status"
  done
  # A queued waiter can have a ticket but no job dir during admission retries.
  mkdir -p "$FARMOUT_HOME/queue"; ticket="$FARMOUT_HOME/queue/000-ticket-only"
  { printf '%s\n' "$$"; cat "$FARMOUT_HOME/jobs/queued/meta.json"; } > "$ticket"
  cp "$ticket" "$T/expected-ticket"
  lifecycle_snapshot "$T/before-ticket"
  run_lifecycle accept ticket-only
  assert_eq "$RC" 2; assert_contains "$ERR" queued; assert_contains "$ERR" finish
  cmp "$ticket" "$T/expected-ticket" || fail 'accept changed queue ticket'
  [ ! -e "$FARMOUT_HOME/jobs/ticket-only" ] || fail 'accept created queued job record'
  assert_lifecycle_git_unchanged "$T/before-ticket"
}

test_accept_refuses_already_accepted_job() {
  prepare_lifecycle_checkout; lifecycle_record reviewed in-place ok '"accepted"'
  cp -R "$FARMOUT_HOME/jobs/reviewed" "$T/expected-record"
  lifecycle_snapshot "$T/before"
  run_lifecycle accept reviewed
  assert_eq "$RC" 2; assert_contains "$ERR" 'already accepted'
  diff -r "$T/expected-record" "$FARMOUT_HOME/jobs/reviewed" || fail 'repeated accept changed record'
  assert_lifecycle_git_unchanged "$T/before"
}

test_clean_keeps_unaccepted_in_place_job() {
  prepare_lifecycle_checkout; lifecycle_record unreviewed in-place ok
  cp -R "$FARMOUT_HOME/jobs/unreviewed" "$T/expected-record"
  lifecycle_snapshot "$T/before"
  run_lifecycle clean
  assert_eq "$RC" 0 "$ERR"; assert_contains "$OUT" 'unaccepted in-place job'
  assert_contains "$OUT" 'farmout accept unreviewed'; assert_contains "$OUT" 'removed 0 job(s)'
  diff -r "$T/expected-record" "$FARMOUT_HOME/jobs/unreviewed" || fail 'clean changed unreviewed record'
  assert_lifecycle_git_unchanged "$T/before"
  run_lifecycle clean --all
  assert_eq "$RC" 0 "$ERR"; [ ! -d "$FARMOUT_HOME/jobs/unreviewed" ] || fail '--all retained record'
  assert_lifecycle_git_unchanged "$T/before"
}

test_clean_removes_accepted_record_without_touching_checkout() {
  prepare_lifecycle_checkout; lifecycle_record reviewed in-place ok '"accepted"'
  # Even corrupted historical worktree/branch fields must never reach cleanup.
  jq --arg c "$T/lifecycle-linked" '.worktree = $c | .branch = "lifecycle-peer"' \
    "$FARMOUT_HOME/jobs/reviewed/meta.json" > "$T/meta"
  mv "$T/meta" "$FARMOUT_HOME/jobs/reviewed/meta.json"
  lifecycle_snapshot "$T/before"
  run_lifecycle clean
  assert_eq "$RC" 0 "$ERR"; assert_contains "$OUT" 'removed 1 job(s)'
  [ ! -d "$FARMOUT_HOME/jobs/reviewed" ] || fail 'accepted record retained'
  assert_lifecycle_git_unchanged "$T/before"
  # Non-ok records follow shared unread-result protection, never worktree
  # cleanup: read/empty records can go, meaningful unread output stays.
  local status job
  for status in failed killed empty rate-limited timeout; do
    lifecycle_record "$status" in-place "$status"
    lifecycle_record "unread-$status" in-place "$status"
    job="$FARMOUT_HOME/jobs/unread-$status"
    jq '.result_read = false' "$job/meta.json" > "$T/meta"; mv "$T/meta" "$job/meta.json"
    cp -R "$job" "$T/expected-unread-$status"
  done
  lifecycle_record no-output in-place empty
  jq '.result_read = false' "$FARMOUT_HOME/jobs/no-output/meta.json" > "$T/meta"
  mv "$T/meta" "$FARMOUT_HOME/jobs/no-output/meta.json"
  : > "$FARMOUT_HOME/jobs/no-output/result.md"; : > "$FARMOUT_HOME/jobs/no-output/log"
  run_lifecycle clean
  assert_eq "$RC" 0 "$ERR"; assert_contains "$OUT" 'removed 6 job(s)'
  for status in failed killed empty rate-limited timeout; do
    [ ! -d "$FARMOUT_HOME/jobs/$status" ] || fail "clean kept read $status record"
    diff -r "$T/expected-unread-$status" "$FARMOUT_HOME/jobs/unread-$status" || fail 'clean changed unread record'
  done
  assert_lifecycle_git_unchanged "$T/before"
  run_lifecycle clean --all
  assert_eq "$RC" 0 "$ERR"; assert_contains "$OUT" 'removed 5 job(s)'
  assert_lifecycle_git_unchanged "$T/before"
}

test_status_shows_in_place_and_accepted() {
  prepare_lifecycle_checkout
  lifecycle_record unreviewed in-place ok; lifecycle_record reviewed in-place ok '"accepted"'
  lifecycle_record reader read ok; lifecycle_record writer write ok
  mkdir -p "$FARMOUT_HOME/queue"
  { printf '%s\n' "$$"; cat "$FARMOUT_HOME/jobs/unreviewed/meta.json"; } > "$FARMOUT_HOME/queue/000-waiting"
  cp "$FARMOUT_HOME/queue/000-waiting" "$T/expected-ticket"
  cp -R "$FARMOUT_HOME/jobs" "$T/expected-jobs"
  lifecycle_snapshot "$T/before"
  run_lifecycle status
  assert_eq "$RC" 0 "$ERR"
  assert_eq "$(printf '%s\n' "$OUT" | head -1 | awk '{print $6}')" REPO
  assert_eq "$(printf '%s\n' "$OUT" | awk '$1 == "unreviewed" {print $4,$5}')" 'in-place -'
  assert_eq "$(printf '%s\n' "$OUT" | awk '$1 == "reviewed" {print $4,$5}')" 'in-place accepted'
  assert_eq "$(printf '%s\n' "$OUT" | awk '$1 == "reader" {print $4,$5}')" 'read -'
  assert_eq "$(printf '%s\n' "$OUT" | awk '$1 == "writer" {print $4,$5}')" 'write -'
  assert_eq "$(printf '%s\n' "$OUT" | awk '$1 == "waiting" {print $2}')" queued
  run_lifecycle status waiting
  assert_eq "$RC" 0 "$ERR"; assert_eq "$(printf '%s\n' "$OUT" | jq -r .status)" queued
  assert_eq "$(printf '%s\n' "$OUT" | jq -r .mode)" in-place
  cmp "$FARMOUT_HOME/queue/000-waiting" "$T/expected-ticket" || fail 'status mutated queue ticket'
  diff -r "$T/expected-jobs" "$FARMOUT_HOME/jobs" || fail 'status mutated records'
  assert_lifecycle_git_unchanged "$T/before"
}

test_kill_in_place_leaves_partial_checkout_changes() {
  make_feature_branch
  git -C "$REPO" worktree add -q -b lifecycle-peer "$T/lifecycle-linked"
  brief 'FAKE: write a.txt partial-worker-change' "FAKE: write $T/partial-ready ready" 'FAKE: spawn' 'FAKE: hang'
  (cd "$REPO" && FARMOUT_TEST_JOB_ID=partial "$FARMOUT" run fake --in-place --owns a.txt \
    --timeout 60s --brief "$T/brief.md") > "$T/partial-out" 2> "$T/partial-err" &
  local runner=$!
  wait_for_meta_pid partial; wait_for_attribution_marker "$T/partial-ready"
  # The worker has finished edits; only supervisor finalization follows kill.
  printf 'staged user file\n' > "$REPO/user-staged.txt"; git -C "$REPO" add user-staged.txt
  printf 'linked user edit\n' >> "$T/lifecycle-linked/a.txt"
  lifecycle_snapshot "$T/before"
  cp "$FARMOUT_HOME/jobs/partial/meta.json" "$T/meta-before-kill"
  "$FARMOUT" kill partial > "$T/action-out" 2> "$T/action-err"; RC=$?
  assert_eq "$RC" 0 "$(cat "$T/action-err")"
  wait "$runner"; RC=$?; assert_eq "$RC" 1 "$(cat "$T/partial-err")"
  assert_lifecycle_git_unchanged "$T/before"
  assert_eq "$(cat "$REPO/a.txt")" partial-worker-change
  assert_eq "$(meta partial status)" killed
  assert_eq "$(meta partial land)" null
  assert_eq "$(meta partial head_end)" "$(git -C "$REPO" rev-parse HEAD)"
  assert_eq "$(meta partial branch_end)" feature/in-place-test
  assert_eq "$(jq -c '.uncommitted' "$FARMOUT_HOME/jobs/partial/meta.json")" '["a.txt"]'
  assert_eq "$(jq -c '.commits' "$FARMOUT_HOME/jobs/partial/meta.json")" '[]'
  assert_eq "$(jq -Sc 'del(.status,.exit_code,.survivors,.ended,.head_end,.branch_end,.commits,.unattributed,.uncommitted,.outside_owns)' \
    "$FARMOUT_HOME/jobs/partial/meta.json")" \
    "$(jq -Sc 'del(.status,.exit_code,.survivors,.ended,.head_end,.branch_end,.commits,.unattributed,.uncommitted,.outside_owns)' "$T/meta-before-kill")" \
    'kill changed metadata beyond shared finalization fields'
  [ -f "$FARMOUT_HOME/jobs/partial/kill.requested" ] || fail 'kill request not recorded'
  [ ! -e "$FARMOUT_HOME/jobs/partial/diff.patch" ] || fail 'kill captured a patch'
  [ -n "$(meta partial ended)" ] && [ "$(meta partial ended)" != null ] || fail 'kill has no final timestamp'
  [ -z "$(pgrep -g "$(meta partial pgid)")" ] || fail 'kill left worker group alive'
}

wait_for_attribution_marker() { # path
  local i=0
  while [ "$i" -lt 100 ]; do
    [ -f "$1" ] && return 0
    sleep 0.1; i=$((i + 1))
  done
  fail "worker did not reach $1"
}

start_attribution_lane() { # id owns [commit-path [additional-owns...]]; sets ATTRIBUTION_RUNNER
  local id="$1" owns="$2" commit_path="${3:-}"
  local additional=() pathspec
  if [ "$#" -gt 3 ]; then
    shift 3
    for pathspec in "$@"; do additional+=(--owns "$pathspec"); done
  fi
  mkfifo "$T/$id-start" "$T/$id-end"
  printf 'FAKE: cat %s\n' "$T/$id-start" > "$T/$id-brief.md"
  [ -z "$commit_path" ] || printf 'FAKE: commit %s-change %s\n' "$id" "$commit_path" >> "$T/$id-brief.md"
  printf 'FAKE: write %s ready\nFAKE: cat %s\nFAKE: print %s-done\n' \
    "$T/$id-ready" "$T/$id-end" "$id" >> "$T/$id-brief.md"
  ( export FARMOUT_TEST_JOB_ID="$id"
    cd "$REPO" && "$FARMOUT" run fake --in-place --owns "$owns" "${additional[@]}" --timeout 60s --brief "$T/$id-brief.md"
  ) > "$T/$id-out" 2> "$T/$id-err" &
  ATTRIBUTION_RUNNER=$!
  wait_for_meta_pid "$id"
}

release_attribution_lane() { # id
  printf '\n' > "$T/$1-start"
  wait_for_attribution_marker "$T/$1-ready"
}

finish_attribution_lane() { # id runner
  printf '\n' > "$T/$1-end"
  wait "$2" || fail "lane $1 failed: $(cat "$T/$1-err")"
}

test_in_place_commit_attributed() {
  make_feature_branch; configure_in_place_author
  brief 'FAKE: commit worker-change a.txt'; run_job fake --in-place --owns a.txt
  assert_eq "$RC" 0 "$ERR"
  local sha; sha="$(git -C "$REPO" rev-parse HEAD)"
  assert_eq "$(jq -c '.commits' "$FARMOUT_HOME/jobs/$ID/meta.json")" \
    "[{\"sha\":\"$sha\",\"subject\":\"worker-change\",\"files\":[{\"path\":\"a.txt\",\"added\":1,\"deleted\":3}],\"shared\":false}]"
  assert_eq "$(git -C "$REPO" show -s --format='%an <%ae>' "$sha")" 'repo-user <repo@example.test>'
  assert_eq "$(meta "$ID" head_end)" "$sha"
  assert_eq "$(meta "$ID" branch_end)" feature/in-place-test
  assert_eq "$(jq -c '[.unattributed,.uncommitted,.outside_owns]' "$FARMOUT_HOME/jobs/$ID/meta.json")" '[[],[],[]]'
}

test_in_place_result_lists_commits_and_heuristic() {
  make_feature_branch; configure_in_place_author
  brief 'FAKE: commit worker-change a.txt' 'FAKE: print final-worker-message'
  run_job fake --in-place --owns a.txt; assert_eq "$RC" 0 "$ERR"
  local output sha; sha="$(git -C "$REPO" rev-parse --short=7 HEAD)"
  output="$("$FARMOUT" result "$ID")"
  assert_contains "$output" 'final-worker-message'
  assert_contains "$output" '--- commits attributed heuristically'
  assert_contains "$output" "$sha worker-change (+1 -3, 1 files)"
  assert_contains "$output" 'Attribution is heuristic: exact when concurrent lanes keep to their --owns paths; hashes may change after rebase.'
  case "$output" in *'--- warning:'*) fail 'printed empty warning section' ;; esac
  assert_eq "$(meta "$ID" result_read)" true
}

test_in_place_uncommitted_reported() {
  make_feature_branch
  brief 'FAKE: write a.txt uncommitted' 'FAKE: print done'; run_job fake --in-place --owns a.txt
  assert_eq "$RC" 0 "$ERR"; assert_eq "$(meta "$ID" status)" ok
  assert_eq "$(jq -c .uncommitted "$FARMOUT_HOME/jobs/$ID/meta.json")" '["a.txt"]'
  assert_contains "$("$FARMOUT" result "$ID")" '--- warning: uncommitted owned paths'
  git -C "$REPO" -c user.name=t -c user.email=t@t commit -qam 'prepare rename'
  start_in_place_holder dirty .; local runner="$HOLDER_RUNNER" unusual=$'new space\nfile.txt' index
  git -C "$REPO" mv a.txt 'renamed space.txt'
  printf dirty > "$REPO/$unusual"
  git -C "$REPO" config status.showUntrackedFiles no
  index="$(git hash-object "$REPO/.git/index")"
  stop_in_place_holder dirty "$runner"
  assert_eq "$(jq -c .uncommitted "$FARMOUT_HOME/jobs/dirty/meta.json")" \
    "$(jq -nc --arg p "$unusual" '["a.txt",$p,"renamed space.txt"]')"
  assert_eq "$(git hash-object "$REPO/.git/index")" "$index" 'finalization rewrote index'
}

test_in_place_outside_owns_reported() {
  make_feature_branch; configure_in_place_author; track_in_place_paths
  # An old completed lane must not turn a subsequent solo lane concurrent.
  mkdir -p "$FARMOUT_HOME/jobs/old"
  jq -n --arg r "$REPO" '{mode:"in-place",checkout:$r,owns:["c.txt"],admitted:true,
    started:"2000-01-01T00:00:00Z",ended:"2000-01-01T00:00:01Z",status:"ok"}' > "$FARMOUT_HOME/jobs/old/meta.json"
  brief 'FAKE: commit went-outside c.txt' 'FAKE: commit outside-again c.txt'
  run_job fake --in-place --owns a.txt; assert_eq "$RC" 0 "$ERR"
  assert_eq "$(jq -c .outside_owns "$FARMOUT_HOME/jobs/$ID/meta.json")" '["c.txt"]'
  assert_eq "$(jq '.commits | length' "$FARMOUT_HOME/jobs/$ID/meta.json")" 2
  assert_eq "$(jq -c '[.commits[].subject]' "$FARMOUT_HOME/jobs/$ID/meta.json")" '["went-outside","outside-again"]'
  assert_contains "$("$FARMOUT" result "$ID")" '--- warning: paths outside --owns'
}

test_in_place_attribution_two_lanes() {
  make_feature_branch; configure_in_place_author; track_in_place_paths
  start_attribution_lane first a.txt a.txt; local first="$ATTRIBUTION_RUNNER"
  start_attribution_lane second dir/b.txt dir/b.txt; local second="$ATTRIBUTION_RUNNER"
  release_attribution_lane first; local first_sha; first_sha="$(git -C "$REPO" rev-parse HEAD)"
  release_attribution_lane second; local second_sha; second_sha="$(git -C "$REPO" rev-parse HEAD)"
  finish_attribution_lane first "$first"
  finish_attribution_lane second "$second"
  assert_eq "$(jq -c '[.commits[].sha]' "$FARMOUT_HOME/jobs/first/meta.json")" "[\"$first_sha\"]"
  assert_eq "$(jq -c '[.commits[].sha]' "$FARMOUT_HOME/jobs/second/meta.json")" "[\"$second_sha\"]"
  assert_eq "$(jq -c '[.commits[].shared]' "$FARMOUT_HOME/jobs/second/meta.json")" '[false]'
}

assert_clean_preserves_in_place_attribution() { # optional --all
  make_feature_branch; configure_in_place_author; track_in_place_paths
  start_attribution_lane first a.txt a.txt; local first="$ATTRIBUTION_RUNNER"
  start_attribution_lane second dir/b.txt dir/b.txt; local second="$ATTRIBUTION_RUNNER"
  release_attribution_lane first; local first_sha second_sha output
  first_sha="$(git -C "$REPO" rev-parse HEAD)"
  finish_attribution_lane first "$first"
  "$FARMOUT" result first >/dev/null; "$FARMOUT" accept first >/dev/null
  assert_eq "$(meta second admitted)" true; assert_eq "$(meta second status)" running
  cp -R "$FARMOUT_HOME/jobs/first" "$T/first-before-clean"
  lifecycle_git_snapshot "$REPO" "$T/git-before-clean"
  output="$("$FARMOUT" clean ${1:+"$1"})"
  [ -d "$FARMOUT_HOME/jobs/first" ] || fail 'clean removed attribution evidence while second was running'
  assert_contains "$output" 'attribution needed by live in-place job second'
  assert_contains "$output" 'retry clean after second finishes'
  assert_contains "$output" 'removed 0 job(s)'
  diff -r "$T/first-before-clean" "$FARMOUT_HOME/jobs/first" || fail 'retention changed first record'
  lifecycle_git_snapshot "$REPO" "$T/git-after-clean"
  diff -r "$T/git-before-clean" "$T/git-after-clean" || fail 'clean changed checkout/index/worktrees'
  release_attribution_lane second; second_sha="$(git -C "$REPO" rev-parse HEAD)"
  finish_attribution_lane second "$second"
  assert_eq "$(jq -c '[.commits[].sha]' "$FARMOUT_HOME/jobs/second/meta.json")" "[\"$second_sha\"]"
  assert_eq "$(jq -c '[.outside_owns,.unattributed]' "$FARMOUT_HOME/jobs/second/meta.json")" '[[],[]]'
  assert_eq "$(jq -c '[.commits[].sha]' "$FARMOUT_HOME/jobs/first/meta.json")" "[\"$first_sha\"]"
  output="$("$FARMOUT" clean)"
  assert_contains "$output" 'removed 1 job(s)'
  [ ! -d "$FARMOUT_HOME/jobs/first" ] || fail 'clean kept first after second finalized'
  [ -d "$FARMOUT_HOME/jobs/second" ] || fail 'clean removed unaccepted second record'
}

test_clean_preserves_in_place_attribution() { assert_clean_preserves_in_place_attribution; }
test_clean_all_preserves_in_place_attribution() { assert_clean_preserves_in_place_attribution --all; }

cleanup_overlap_records() {
  lifecycle_record candidate in-place ok '"accepted"'
  lifecycle_record z-peer in-place running
  jq '.started = "2026-09-30T12:00:00Z" | .admitted_at = .started | .ended = "2026-09-30T12:00:10Z"' \
    "$FARMOUT_HOME/jobs/candidate/meta.json" > "$T/meta"
  mv "$T/meta" "$FARMOUT_HOME/jobs/candidate/meta.json"
  jq '.started = "2026-09-30T12:00:05Z" | .admitted_at = .started | .ended = null' \
    "$FARMOUT_HOME/jobs/z-peer/meta.json" > "$T/meta"
  mv "$T/meta" "$FARMOUT_HOME/jobs/z-peer/meta.json"
}

test_clean_retains_unreadable_in_place_overlap_evidence() {
  prepare_lifecycle_checkout; cleanup_overlap_records
  printf 'incomplete metadata\n' > "$FARMOUT_HOME/jobs/z-peer/meta.json"
  cp -R "$FARMOUT_HOME/jobs" "$T/expected-records"
  lifecycle_snapshot "$T/before"
  run_lifecycle clean --all
  assert_eq "$RC" 0 "$ERR"; assert_contains "$OUT" 'overlap evidence unreadable'
  assert_contains "$OUT" 'retry clean when metadata is available'
  diff -r "$T/expected-records" "$FARMOUT_HOME/jobs" || fail 'clean removed unreadable overlap evidence'
  assert_lifecycle_git_unchanged "$T/before"
}

test_clean_retains_finalizing_in_place_peer() {
  prepare_lifecycle_checkout; cleanup_overlap_records
  ln -s "$REPO" "$T/checkout-alias"
  jq --arg checkout "$T/checkout-alias" '.checkout = $checkout | .ended = "2026-09-30T12:00:08Z"' \
    "$FARMOUT_HOME/jobs/z-peer/meta.json" > "$T/meta"
  mv "$T/meta" "$FARMOUT_HOME/jobs/z-peer/meta.json"
  jq '.land = null | .result_read = false' "$FARMOUT_HOME/jobs/candidate/meta.json" > "$T/meta"
  mv "$T/meta" "$FARMOUT_HOME/jobs/candidate/meta.json"
  cp -R "$FARMOUT_HOME/jobs" "$T/expected-records"
  lifecycle_snapshot "$T/before"
  run_lifecycle clean --all
  assert_eq "$RC" 0 "$ERR"; assert_contains "$OUT" 'attribution needed by live in-place job z-peer'
  diff -r "$T/expected-records" "$FARMOUT_HOME/jobs" || fail 'clean removed evidence during finalization'
  assert_lifecycle_git_unchanged "$T/before"
}

test_clean_retention_ignores_ineligible_peers() {
  prepare_lifecycle_checkout; lifecycle_snapshot "$T/before"
  local scenario filter
  for scenario in queued unadmitted ended lost non-overlapping linked reader writer; do
    cleanup_overlap_records
    case "$scenario" in
      queued) filter='.status = "queued" | .admitted = false' ;;
      unadmitted) filter='.admitted = false' ;;
      ended) filter='.status = "ok" | .ended = "2026-09-30T12:00:08Z"' ;;
      lost) filter='.sup_pid = 99999999' ;;
      non-overlapping) filter='.admitted_at = "2026-09-30T12:00:11Z"' ;;
      linked) filter='.checkout = $linked' ;;
      reader) filter='.mode = "read"' ;;
      writer) filter='.mode = "write"' ;;
    esac
    jq --arg linked "$T/lifecycle-linked" "$filter" "$FARMOUT_HOME/jobs/z-peer/meta.json" > "$T/meta"
    mv "$T/meta" "$FARMOUT_HOME/jobs/z-peer/meta.json"
    run_lifecycle clean --all
    assert_eq "$RC" 0 "$scenario: $ERR"
    [ ! -d "$FARMOUT_HOME/jobs/candidate" ] || fail "retained candidate for $scenario peer"
    assert_lifecycle_git_unchanged "$T/before"
    rm -rf "$FARMOUT_HOME/jobs/z-peer"
  done
}

test_clean_retains_unreliable_in_place_intervals() {
  prepare_lifecycle_checkout; lifecycle_snapshot "$T/before"
  local scenario filter
  for scenario in interval checkout; do
    cleanup_overlap_records
    case "$scenario" in
      interval) filter='.admitted_at = null | .started = null' ;;
      checkout) filter='.checkout = "/missing/farmout-checkout"' ;;
    esac
    jq "$filter" "$FARMOUT_HOME/jobs/z-peer/meta.json" > "$T/meta"
    mv "$T/meta" "$FARMOUT_HOME/jobs/z-peer/meta.json"
    cp -R "$FARMOUT_HOME/jobs" "$T/expected-$scenario"
    run_lifecycle clean --all
    assert_eq "$RC" 0 "$ERR"; assert_contains "$OUT" 'evidence unreadable'
    assert_contains "$OUT" 'retry clean'
    diff -r "$T/expected-$scenario" "$FARMOUT_HOME/jobs" || fail "removed $scenario evidence"
    assert_lifecycle_git_unchanged "$T/before"
  done
}

test_in_place_shared_commit_attributed_to_each_lane() {
  make_feature_branch; configure_in_place_author; track_in_place_paths
  start_attribution_lane first a.txt; local first="$ATTRIBUTION_RUNNER"
  start_attribution_lane second dir/b.txt; local second="$ATTRIBUTION_RUNNER"
  release_attribution_lane first; release_attribution_lane second
  printf shared > "$REPO/a.txt"; printf shared > "$REPO/dir/b.txt"
  git -C "$REPO" add -- a.txt dir/b.txt
  git -C "$REPO" commit -qm shared -- a.txt dir/b.txt
  local sha; sha="$(git -C "$REPO" rev-parse HEAD)"
  finish_attribution_lane first "$first"; finish_attribution_lane second "$second"
  local lane expected
  for lane in first second; do
    assert_eq "$(jq -c '[.commits[] | {sha,shared}]' "$FARMOUT_HOME/jobs/$lane/meta.json")" "[{\"sha\":\"$sha\",\"shared\":true}]"
  done
  assert_eq "$(jq -c .outside_owns "$FARMOUT_HOME/jobs/first/meta.json")" '["dir/b.txt"]'
  assert_eq "$(jq -c .outside_owns "$FARMOUT_HOME/jobs/second/meta.json")" '["a.txt"]'
}

test_in_place_hand_commit_unattributed_during_overlap() {
  make_feature_branch; configure_in_place_author; track_in_place_paths
  start_attribution_lane first a.txt; local first="$ATTRIBUTION_RUNNER"
  start_attribution_lane second dir/b.txt; local second="$ATTRIBUTION_RUNNER"
  release_attribution_lane first; release_attribution_lane second
  printf manual > "$REPO/c.txt"; git -C "$REPO" add -- c.txt
  git -C "$REPO" commit -qm manual -- c.txt
  local sha; sha="$(git -C "$REPO" rev-parse HEAD)"
  finish_attribution_lane first "$first"; finish_attribution_lane second "$second"
  local lane
  for lane in first second; do
    assert_eq "$(jq -c '[.unattributed[] | {sha,subject,shared}]' "$FARMOUT_HOME/jobs/$lane/meta.json")" \
      "[{\"sha\":\"$sha\",\"subject\":\"manual\",\"shared\":false}]"
    assert_eq "$(jq -c .commits "$FARMOUT_HOME/jobs/$lane/meta.json")" '[]'
    assert_contains "$("$FARMOUT" result "$lane")" '--- warning: unattributed commits'
  done
}

test_in_place_historical_deleted_binary_and_nul_paths() {
  make_feature_branch; configure_in_place_author; track_in_place_paths
  local deleted=$'odd/-space\tand\nnewline.txt' renamed=$'-renamed outside\nfile.txt'
  mkdir -p "$REPO/odd"
  printf 'old\n' > "$REPO/$deleted"
  printf 'rename\n' > "$REPO/odd/source space.txt"
  printf 'excluded\n' > "$REPO/odd/excluded.txt"
  printf '\0old\0' > "$REPO/odd/binary.bin"
  git -C "$REPO" add -- odd; git -C "$REPO" commit -qm 'odd path fixtures'
  start_attribution_lane odd ':(glob)odd/*' '' ':(exclude)odd/excluded.txt'; local odd_runner="$ATTRIBUTION_RUNNER"
  start_attribution_lane peer c.txt; local peer_runner="$ATTRIBUTION_RUNNER"
  release_attribution_lane odd; release_attribution_lane peer
  git -C "$REPO" rm -q -- "$deleted"
  git -C "$REPO" commit -qm 'historical delete' -- "$deleted"
  local deleted_sha; deleted_sha="$(git -C "$REPO" rev-parse HEAD)"
  git -C "$REPO" mv -- 'odd/source space.txt' "$renamed"
  git -C "$REPO" commit -qm 'rename outside' -- 'odd/source space.txt' "$renamed"
  local renamed_sha; renamed_sha="$(git -C "$REPO" rev-parse HEAD)"
  printf '\0new\0' > "$REPO/odd/binary.bin"; printf 'breach\n' > "$REPO/odd/excluded.txt"
  git -C "$REPO" add -- odd/binary.bin odd/excluded.txt
  git -C "$REPO" commit -qm 'binary and excluded breach' -- odd/binary.bin odd/excluded.txt
  local binary_sha index; binary_sha="$(git -C "$REPO" rev-parse HEAD)"; index="$(git hash-object "$REPO/.git/index")"
  finish_attribution_lane odd "$odd_runner"; finish_attribution_lane peer "$peer_runner"
  assert_eq "$(jq -c '[.commits[].sha]' "$FARMOUT_HOME/jobs/odd/meta.json")" \
    "[\"$deleted_sha\",\"$renamed_sha\",\"$binary_sha\"]"
  assert_eq "$(jq -c '.commits[0].files' "$FARMOUT_HOME/jobs/odd/meta.json")" \
    "$(jq -nc --arg p "$deleted" '[{path:$p,added:0,deleted:1}]')"
  assert_eq "$(jq -c '[.commits[2].files[] | select(.path == "odd/binary.bin")]' "$FARMOUT_HOME/jobs/odd/meta.json")" \
    '[{"path":"odd/binary.bin","added":null,"deleted":null}]'
  assert_eq "$(jq -c .outside_owns "$FARMOUT_HOME/jobs/odd/meta.json")" \
    "$(jq -nc --arg p "$renamed" '[$p,"odd/excluded.txt"]')"
  assert_eq "$(jq -c '[.commits,.unattributed]' "$FARMOUT_HOME/jobs/peer/meta.json")" '[[],[]]'
  assert_eq "$(git hash-object "$REPO/.git/index")" "$index" 'finalization touched index'
  assert_eq "$(git -C "$REPO" rev-parse HEAD)" "$binary_sha" 'finalization moved HEAD'
  local output; output="$("$FARMOUT" result odd)"
  assert_contains "$output" 'binary and excluded breach (+? -?, 2 files)'
  assert_contains "$output" "$(jq -nc --arg p "$renamed" '$p')"
}

test_in_place_historical_attribute_pathspec_survives_later_changes() {
  local scenario
  for scenario in concurrent solo; do
    (
      setup; trap teardown EXIT
      make_feature_branch; configure_in_place_author; track_in_place_paths
      printf 'owned.txt lane\n' > "$REPO/.gitattributes"
      printf 'before\n' > "$REPO/owned.txt"
      git -C "$REPO" add -- .gitattributes owned.txt
      git -C "$REPO" commit -qm 'attribute ownership fixture'
      start_attribution_lane lane ':(attr:lane)*.txt'; local lane_runner="$ATTRIBUTION_RUNNER" peer_runner=""
      if [ "$scenario" = concurrent ]; then
        start_attribution_lane peer c.txt; peer_runner="$ATTRIBUTION_RUNNER"
        release_attribution_lane peer
      fi
      release_attribution_lane lane
      printf 'historical change\n' > "$REPO/owned.txt"
      git -C "$REPO" add -- owned.txt; git -C "$REPO" commit -qm 'owned change' -- owned.txt
      local changed_sha; changed_sha="$(git -C "$REPO" rev-parse HEAD)"
      git -C "$REPO" rm -q -- owned.txt; git -C "$REPO" commit -qm 'owned deletion' -- owned.txt
      local deleted_sha; deleted_sha="$(git -C "$REPO" rev-parse HEAD)"
      printf 'owned.txt -lane\n' > "$REPO/.gitattributes"
      git -C "$REPO" add -- .gitattributes; git -C "$REPO" commit -qm 'later attribute change' -- .gitattributes
      local attribute_sha index attributes
      attribute_sha="$(git -C "$REPO" rev-parse HEAD)"
      index="$(git hash-object "$REPO/.git/index")"; attributes="$(git hash-object "$REPO/.gitattributes")"
      finish_attribution_lane lane "$lane_runner"
      [ -z "$peer_runner" ] || finish_attribution_lane peer "$peer_runner"
      if [ "$scenario" = concurrent ]; then
        assert_eq "$(jq -c '[.commits[].sha]' "$FARMOUT_HOME/jobs/lane/meta.json")" \
          "[\"$changed_sha\",\"$deleted_sha\"]" 'historical attribute-owned commits omitted'
        assert_eq "$(jq -c '[.unattributed[].sha]' "$FARMOUT_HOME/jobs/lane/meta.json")" "[\"$attribute_sha\"]"
        assert_eq "$(jq -c .outside_owns "$FARMOUT_HOME/jobs/lane/meta.json")" '[]'
        assert_eq "$(jq -c '[.unattributed[].sha]' "$FARMOUT_HOME/jobs/peer/meta.json")" "[\"$attribute_sha\"]"
      else
        assert_eq "$(jq -c '[.commits[].sha]' "$FARMOUT_HOME/jobs/lane/meta.json")" \
          "[\"$changed_sha\",\"$deleted_sha\",\"$attribute_sha\"]"
        assert_eq "$(jq -c .outside_owns "$FARMOUT_HOME/jobs/lane/meta.json")" '[".gitattributes"]' \
          'historically owned file falsely outside owns'
        assert_eq "$(jq -c .unattributed "$FARMOUT_HOME/jobs/lane/meta.json")" '[]'
      fi
      assert_eq "$(jq -c .commits[1].files "$FARMOUT_HOME/jobs/lane/meta.json")" \
        '[{"path":"owned.txt","added":0,"deleted":1}]'
      assert_eq "$(git hash-object "$REPO/.git/index")" "$index" 'historical attribute probe touched index'
      assert_eq "$(git hash-object "$REPO/.gitattributes")" "$attributes" 'historical attribute probe rewrote checkout attributes'
      assert_eq "$(git -C "$REPO" rev-parse HEAD)" "$attribute_sha"
      [ ! -e "$REPO/owned.txt" ] || fail 'historical probe restored deleted file'
    ) || fail "$scenario historical attribute-pathspec scenario failed"
  done
}

test_in_place_temporal_overlap_uses_intervals_and_checkout_identity() {
  make_feature_branch; configure_in_place_author; track_in_place_paths
  local alias="$T/checkout-alias" foreign="$T/foreign"
  ln -s "$REPO" "$alias"
  git -C "$REPO" worktree add -qb other-checkout "$foreign"
  start_attribution_lane lane a.txt; local runner="$ATTRIBUTION_RUNNER"
  release_attribution_lane lane
  local id checkout start end admitted owns
  # Completed alias peer really overlapped. Other records are a future run,
  # a finished pre-run peer, an unadmitted waiter, and a linked worktree.
  for id in completed future past waiting foreign; do
    checkout="$REPO"; start='2000-01-01T00:00:00Z'; end='2999-01-01T00:00:00Z'; admitted=true; owns=dir/b.txt
    case "$id" in
      completed) checkout="$alias"; owns=c.txt ;;
      future) start='2999-01-01T00:00:00Z'; end='' ;;
      past) end='2000-01-01T00:00:01Z' ;;
      waiting) admitted=false ;;
      foreign) checkout="$foreign" ;;
    esac
    mkdir -p "$FARMOUT_HOME/jobs/$id"
    jq -n --arg r "$checkout" --arg start "$start" --arg end "$end" --arg owns "$owns" --argjson admitted "$admitted" \
      '{mode:"in-place",checkout:$r,owns:[$owns],admitted:$admitted,
        admitted_at:$start,started:"1900-01-01T00:00:00Z",
        ended:(if $end == "" then null else $end end),status:"ok"}' > "$FARMOUT_HOME/jobs/$id/meta.json"
  done
  printf peer > "$REPO/c.txt"; git -C "$REPO" add -- c.txt; git -C "$REPO" commit -qm 'completed peer path' -- c.txt
  printf unowned > "$REPO/dir/b.txt"; git -C "$REPO" add -- dir/b.txt; git -C "$REPO" commit -qm unowned -- dir/b.txt
  local sha; sha="$(git -C "$REPO" rev-parse HEAD)"
  finish_attribution_lane lane "$runner"
  assert_eq "$(jq -c .commits "$FARMOUT_HOME/jobs/lane/meta.json")" '[]'
  assert_eq "$(jq -c '[.unattributed[].sha]' "$FARMOUT_HOME/jobs/lane/meta.json")" "[\"$sha\"]"
}

test_in_place_branch_failure_keeps_final_commit_snapshot() {
  make_feature_branch; configure_in_place_author
  git -C "$REPO" branch switched-lane
  brief 'FAKE: checkout switched-lane' 'FAKE: commit switched-change a.txt' 'FAKE: print done'
  run_job fake --in-place --owns a.txt
  assert_eq "$RC" 1 "$ERR"
  assert_eq "$(meta "$ID" status)" failed
  assert_eq "$(meta "$ID" branch_end)" switched-lane
  local sha; sha="$(git -C "$REPO" rev-parse HEAD)"
  assert_eq "$(meta "$ID" head_end)" "$sha"
  assert_eq "$(jq -c '[.commits[].sha]' "$FARMOUT_HOME/jobs/$ID/meta.json")" "[\"$sha\"]"
  assert_contains "$(meta "$ID" error)" "worker switched branch from 'feature/in-place-test' to 'switched-lane'"
}

test_config_in_place_max_default_and_validation() {
  . "$ROOT/lib/common.sh"; . "$ROOT/lib/adapters.sh"; . "$ROOT/lib/config.sh"
  assert_eq "$DEFAULT_IN_PLACE_MAX" 3
  assert_eq "$(cfg_limit in_place_max)" ''
  local value
  for value in 1 32 '"3"'; do
    CFG_JSON="{\"limits\":{\"in_place_max\":$value}}"
    assert_eq "$(cfg_limit in_place_max)" "${value//\"/}"
  done
  for value in 0 33 -1 1.5 true false '"no"' '{}' '[]'; do
    CFG_JSON="{\"limits\":{\"in_place_max\":$value}}"
    assert_eq "$(cfg_limit in_place_max 2> "$T/config-err")" '' "$value"
    assert_contains "$(cat "$T/config-err")" invalid\ limits.in_place_max
  done
}

test_in_place_refuses_overlapping_owns() {
  make_feature_branch; track_in_place_paths
  start_in_place_holder first a.txt; local first_runner="$HOLDER_RUNNER"
  brief 'FAKE: print ok'; run_job fake --in-place --owns a.txt
  assert_eq "$RC" 2 "$ERR"
  assert_contains "$ERR" first
  assert_contains "$ERR" 'untracked-only future files cannot be detected'
  assert_eq "$(ls "$FARMOUT_HOME/jobs" | wc -l | tr -d ' ')" 1 'overlap claimed a job'
  stop_in_place_holder first "$first_runner"
  start_in_place_holder glob 'dir/**'; local glob_runner="$HOLDER_RUNNER"
  run_job fake --in-place --owns dir/b.txt
  assert_eq "$RC" 2 "$ERR"; assert_contains "$ERR" glob
  stop_in_place_holder glob "$glob_runner"
}

test_in_place_allows_disjoint_owns() {
  make_feature_branch; track_in_place_paths
  start_in_place_holder first a.txt; local runner="$HOLDER_RUNNER"
  brief 'FAKE: print ok'; run_job fake --in-place --owns c.txt
  assert_eq "$RC" 0 "$ERR"
  assert_eq "$(meta "$ID" admitted)" true
  stop_in_place_holder first "$runner"
}

test_in_place_owns_leading_dash_pathspec() {
  make_feature_branch
  printf '%s\n' tracked > "$REPO/-odd.txt"
  git -C "$REPO" add -- -odd.txt
  git -C "$REPO" -c user.name=t -c user.email=t@t commit -qm 'leading dash path'
  start_in_place_holder ordinary a.txt; local runner="$HOLDER_RUNNER"
  local index; index="$(git hash-object "$REPO/.git/index")"
  brief 'FAKE: print ok'; run_job fake --in-place --owns -odd.txt
  assert_eq "$RC" 0 "$ERR"
  assert_eq "$(jq -c .owns "$FARMOUT_HOME/jobs/$ID/meta.json")" '["-odd.txt"]'
  assert_eq "$(git hash-object "$REPO/.git/index")" "$index"
  stop_in_place_holder ordinary "$runner"
  start_in_place_holder dashed -odd.txt; runner="$HOLDER_RUNNER"
  run_job fake --in-place --owns -odd.txt
  assert_eq "$RC" 2 "$ERR"
  assert_contains "$ERR" 'ownership overlaps live in-place job dashed'
  assert_contains "$ERR" 'untracked-only future files cannot be detected'
  assert_eq "$(git hash-object "$REPO/.git/index")" "$index"
  stop_in_place_holder dashed "$runner"
}

test_in_place_ownership_serialization_failure_is_safe() {
  make_feature_branch; brief 'FAKE: print must-not-run'
  local index head payload
  index="$(git hash-object "$REPO/.git/index")"; head="$(git -C "$REPO" rev-parse HEAD)"
  # Inject only the ownership serializer; all validation/other jq calls use
  # the real executable. Exported functions work with the target Bash 3.2.
  jq() {
    local arg
    for arg in "$@"; do
      if [ "$arg" = '$ARGS.positional' ]; then
        [ "$FAKE_JQ_OWNS_PAYLOAD" != fail ] || return 7
        printf '%s' "$FAKE_JQ_OWNS_PAYLOAD"; return 0
      fi
    done
    command jq "$@"
  }
  export -f jq
  for payload in fail '' null '[]' '{}' '[42]' '[""]' '["a.txt"] ["a.txt"]' invalid-json; do
    export FAKE_JQ_OWNS_PAYLOAD="$payload"
    run_job fake --in-place --owns a.txt
    assert_eq "$RC" 2 "serialization payload [$payload]: $ERR"
    assert_contains "$ERR" 'ownership pathspecs'
    [ ! -d "$FARMOUT_HOME/jobs" ] || fail 'claimed a job after failed ownership serialization'
    [ -z "$OUT" ] || fail 'launched a worker after failed ownership serialization'
    assert_eq "$(git hash-object "$REPO/.git/index")" "$index"
    assert_eq "$(git -C "$REPO" rev-parse HEAD)" "$head"
  done
}

test_in_place_refuses_invalid_live_ownership_json() {
  make_feature_branch; track_in_place_paths
  local bad="$FARMOUT_HOME/jobs/invalid-owner" index
  mkdir -p "$bad"
  jq -n --arg r "$REPO" --argjson pid "$$" \
    '{mode:"in-place",checkout:$r,owns:42,admitted:true,status:"running",sup_pid:$pid}' > "$bad/meta.json"
  index="$(git hash-object "$REPO/.git/index")"
  brief 'FAKE: print must-not-run'; run_job fake --in-place --owns c.txt
  assert_eq "$RC" 2 "$ERR"
  assert_contains "$ERR" 'invalid ownership pathspecs JSON'
  assert_contains "$ERR" 'invalid-owner'
  assert_eq "$(ls "$FARMOUT_HOME/jobs" | wc -l | tr -d ' ')" 1 'claimed a job despite invalid peer ownership'
  assert_eq "$(git hash-object "$REPO/.git/index")" "$index"
}

test_in_place_ownership_decoder_failure_is_safe() {
  make_feature_branch; brief 'FAKE: print must-not-run'
  local index; index="$(git hash-object "$REPO/.git/index")"
  jq() {
    local arg
    for arg in "$@"; do
      if [ "$arg" = '.[] | ., "\u0000"' ]; then return 7; fi
    done
    command jq "$@"
  }
  export -f jq
  run_job fake --in-place --owns a.txt
  assert_eq "$RC" 2 "$ERR"
  assert_contains "$ERR" 'could not decode ownership pathspecs JSON'
  [ ! -d "$FARMOUT_HOME/jobs" ] || fail 'claimed a job after failed ownership decoding'
  [ -z "$OUT" ] || fail 'launched a worker after failed ownership decoding'
  assert_eq "$(git hash-object "$REPO/.git/index")" "$index"
}

test_in_place_ownership_uses_git_magic_and_nul_paths() {
  make_feature_branch; track_in_place_paths
  local unusual=$'odd\nname.txt'
  printf '%s\n' tracked > "$REPO/$unusual"
  printf '%s\n' tracked > "$REPO/odd"
  printf '%s\n' tracked > "$REPO/name.txt"
  git -C "$REPO" add -- "$unusual" odd name.txt
  git -C "$REPO" -c user.name=t -c user.email=t@t commit -qm 'unusual paths'
  start_in_place_holder newline "$(printf ':(literal)%s' "$unusual")"; local runner="$HOLDER_RUNNER"
  brief 'FAKE: print ok'; run_job fake --in-place --owns odd --owns name.txt
  assert_eq "$RC" 0 "$ERR"
  run_job fake --in-place --owns "$(printf ':(literal)%s' "$unusual")"
  assert_eq "$RC" 2 "$ERR"; assert_contains "$ERR" newline
  stop_in_place_holder newline "$runner"
  start_in_place_holder magic ':(glob)dir/*.txt'; runner="$HOLDER_RUNNER"
  # An exclusion applies to the whole pathspec set, not to a shell glob union.
  run_job fake --in-place --owns ':(top)**' --owns ':(exclude)dir/b.txt'
  assert_eq "$RC" 0 "$ERR"
  run_job fake --in-place --owns ':(top,literal)dir/b.txt'
  assert_eq "$RC" 2 "$ERR"; assert_contains "$ERR" magic
  stop_in_place_holder magic "$runner"
}

test_in_place_guards_canonical_checkout_aliases() {
  make_feature_branch; track_in_place_paths
  printf '%s\n' '{"limits":{"max_jobs":4,"in_place_max":1}}' > "$FARMOUT_CONFIG"
  local alias="$T/checkout-alias"
  ln -s "$REPO" "$alias"
  start_in_place_holder aliased a.txt; local runner="$HOLDER_RUNNER"
  local m="$FARMOUT_HOME/jobs/aliased/meta.json"
  jq --arg r "$alias" '.repo=$r | .checkout=$r' "$m" > "$m.tmp" && mv "$m.tmp" "$m"
  brief 'FAKE: print ok'; run_job fake --in-place --owns a.txt --repo "$alias"
  assert_eq "$RC" 2 "$ERR"; assert_contains "$ERR" aliased
  run_job fake --in-place --owns c.txt --repo "$alias"
  assert_eq "$RC" 4 "$ERR"; assert_contains "$ERR" 'max in-place jobs for checkout'
  stop_in_place_holder aliased "$runner"
}

test_in_place_cap_defaults_to_three() {
  make_feature_branch; track_in_place_paths
  printf '%s\n' fourth > "$REPO/fourth.txt"
  git -C "$REPO" add fourth.txt
  git -C "$REPO" -c user.name=t -c user.email=t@t commit -qm 'fourth lane'
  local path id=0
  for path in a.txt dir/b.txt c.txt; do
    id=$((id + 1)); mkdir -p "$FARMOUT_HOME/jobs/$id"
    jq -n --arg r "$REPO" --arg p "$path" --argjson pid "$$" \
      '{mode:"in-place",checkout:$r,owns:[$p],admitted:true,status:"running",sup_pid:$pid}' > "$FARMOUT_HOME/jobs/$id/meta.json"
  done
  brief 'FAKE: print ok'; run_job fake --in-place --owns fourth.txt
  assert_eq "$RC" 4 "$ERR"
  assert_contains "$ERR" 'max in-place jobs for checkout (3)'
}

test_in_place_cap_per_checkout() {
  make_feature_branch; track_in_place_paths
  printf '%s\n' '{"limits":{"max_jobs":4,"in_place_max":1}}' > "$FARMOUT_CONFIG"
  start_in_place_holder first a.txt; local runner="$HOLDER_RUNNER"
  brief 'FAKE: print ok'; run_job fake --in-place --owns c.txt
  assert_eq "$RC" 4 "$ERR"
  assert_contains "$ERR" 'max in-place jobs for checkout'
  assert_eq "$(ls "$FARMOUT_HOME/jobs" | wc -l | tr -d ' ')" 1 'cap refusal left a dir'
  # Queue waits for this cap even with spare overall capacity.
  ( export FARMOUT_QUEUE_POLL_S=0.1 FARMOUT_TEST_JOB_ID=checkout-waiter
    cd "$REPO" && "$FARMOUT" run fake --in-place --owns c.txt --queue --brief "$T/brief.md"
  ) > "$T/wait-out" 2> "$T/wait-err" &
  local waiter=$! i=0
  while ! grep -q 'queued .* for a slot' "$T/wait-err" && [ "$i" -lt 100 ]; do sleep 0.1; i=$((i + 1)); done
  grep -q 'queued .* for a slot' "$T/wait-err" || fail 'checkout cap did not queue'
  stop_in_place_holder first "$runner"
  wait "$waiter"; assert_eq "$?" 0 "$(cat "$T/wait-err")"
  assert_eq "$(meta checkout-waiter admitted)" true
}

test_in_place_cap_does_not_cross_checkouts() {
  make_feature_branch
  printf '%s\n' '{"limits":{"max_jobs":4,"in_place_max":1}}' > "$FARMOUT_CONFIG"
  local linked="$T/linked"
  git -C "$REPO" worktree add -q -b linked-lane "$linked"
  start_in_place_holder first a.txt; local runner="$HOLDER_RUNNER"
  brief 'FAKE: print ok'
  (cd "$linked" && "$FARMOUT" run fake --in-place --owns a.txt --brief "$T/brief.md") > "$T/linked-out" 2> "$T/linked-err"
  assert_eq "$?" 0 "$(cat "$T/linked-err")"
  assert_eq "$(meta "$(head -1 "$T/linked-out")" checkout)" "$linked"
  stop_in_place_holder first "$runner"
}

assert_queued_in_place_refused() {
  wait "$QUEUED_RUNNER"; local rc=$?
  assert_eq "$rc" 2 "$(cat "$T/queued-err")"
  [ ! -e "$FARMOUT_HOME/jobs/queued-in-place" ] || fail 'refused queued job left its dir'
  [ -z "$(find "$FARMOUT_HOME/queue" -type f -print)" ] || fail 'refused queued job left its ticket'
  [ ! -s "$T/queued-out" ] || fail 'refused queued job launched a worker'
}

test_queued_in_place_rechecks_dirty_owned_paths() {
  make_feature_branch; start_queued_in_place
  printf '%s\n' 'user change while queued' > "$REPO/a.txt"
  local head index work
  head="$(git -C "$REPO" rev-parse HEAD)"; index="$(git hash-object "$REPO/.git/index")"
  work="$(git -C "$REPO" diff)"
  rm -rf "$FARMOUT_HOME/jobs/blocker"
  assert_queued_in_place_refused
  assert_contains "$(cat "$T/queued-err")" a.txt
  assert_eq "$(git -C "$REPO" rev-parse HEAD)" "$head"
  assert_eq "$(git hash-object "$REPO/.git/index")" "$index"
  assert_eq "$(git -C "$REPO" diff)" "$work"
}

test_queued_in_place_rechecks_overlap() {
  make_feature_branch; start_queued_in_place 2
  local other="$FARMOUT_HOME/jobs/later-owner"
  mkdir -p "$other"
  jq -n --arg r "$REPO" --argjson pid "$$" \
    '{mode:"in-place",checkout:$r,owns:["a.txt"],admitted:true,status:"running",sup_pid:$pid}' > "$other/meta.json"
  local index; index="$(git hash-object "$REPO/.git/index")"
  rm -rf "$FARMOUT_HOME/jobs/blocker" "$FARMOUT_HOME/jobs/blocker2"
  assert_queued_in_place_refused
  assert_contains "$(cat "$T/queued-err")" later-owner
  assert_contains "$(cat "$T/queued-err")" 'untracked-only future files cannot be detected'
  assert_eq "$(git hash-object "$REPO/.git/index")" "$index"
}

test_queued_in_place_base_unset_until_admission() {
  make_feature_branch; start_queued_in_place
  local state; state="$("$FARMOUT" status queued-in-place)"
  # Release the waiter even when the assertion fails, to keep the fixture bounded.
  rm -rf "$FARMOUT_HOME/jobs/blocker"
  wait "$QUEUED_RUNNER"; assert_eq "$?" 0
  assert_eq "$(printf '%s' "$state" | jq -r .base)" null 'queued base was recorded before guards'
}

test_queued_in_place_rechecks_branch_and_default() {
  local branch
  for branch in detached main different-feature; do
    make_feature_branch; start_queued_in_place
    case "$branch" in
      detached) git -C "$REPO" checkout -q --detach ;;
      *) git -C "$REPO" checkout -q -b "$branch" ;;
    esac
    local index head
    index="$(git hash-object "$REPO/.git/index")"; head="$(git -C "$REPO" rev-parse HEAD)"
    rm -rf "$FARMOUT_HOME/jobs/blocker"
    assert_queued_in_place_refused
    case "$branch" in
      detached) assert_contains "$(cat "$T/queued-err")" 'detached HEAD' ;;
      main) assert_contains "$(cat "$T/queued-err")" 'default branch' ;;
      *) assert_contains "$(cat "$T/queued-err")" 'source branch' ;;
    esac
    assert_eq "$(git hash-object "$REPO/.git/index")" "$index"
    assert_eq "$(git -C "$REPO" rev-parse HEAD)" "$head"
    git -C "$REPO" checkout -q feature/in-place-test
    [ "$branch" = detached ] || git -C "$REPO" branch -D "$branch" >/dev/null
  done
}

test_queued_in_place_rechecks_remote_default() {
  make_feature_branch; start_queued_in_place
  git -C "$REPO" symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/feature/in-place-test
  rm -rf "$FARMOUT_HOME/jobs/blocker"
  assert_queued_in_place_refused
  assert_contains "$(cat "$T/queued-err")" 'default branch'
}

test_queued_in_place_preserves_default_branch_override() {
  make_feature_branch
  git -C "$REPO" branch -M main
  start_queued_in_place 1 --allow-default-branch
  rm -rf "$FARMOUT_HOME/jobs/blocker"
  wait "$QUEUED_RUNNER"; assert_eq "$?" 0 "$(cat "$T/queued-err")"
  assert_eq "$(meta queued-in-place admitted)" true
  assert_eq "$(meta queued-in-place source_branch)" main
}

test_in_place_checkout_cap_counts_claimed_unadmitted() {
  make_feature_branch; track_in_place_paths
  printf '%s\n' '{"limits":{"max_jobs":4,"in_place_max":1}}' > "$FARMOUT_CONFIG"
  local pause="$T/admit-pause"; mkfifo "$pause"
  brief 'FAKE: hang'
  ( export FARMOUT_TEST_JOB_ID=claimed FARMOUT_TEST_ADMIT_PAUSE="$pause"
    cd "$REPO" && "$FARMOUT" run fake --in-place --owns a.txt --timeout 60s --brief "$T/brief.md"
  ) > "$T/claimed-out" 2> "$T/claimed-err" &
  local runner=$! i=0
  while [ ! -f "$pause.paused" ] && [ "$i" -lt 100 ]; do sleep 0.1; i=$((i + 1)); done
  [ -f "$pause.paused" ] || fail 'claim did not pause'
  brief 'FAKE: print ok'; run_job fake --in-place --owns c.txt
  local rc="$RC" err="$ERR"
  echo go > "$pause"
  wait_for_meta_pid claimed
  stop_in_place_holder claimed "$runner"
  assert_eq "$rc" 4 "$err"
  assert_contains "$err" 'max in-place jobs for checkout'
}

test_in_place_overlapping_claims_admit_exactly_one() {
  make_feature_branch
  local pause="$T/overlap-pause"; mkfifo "$pause"
  brief 'FAKE: hang'
  ( export FARMOUT_TEST_JOB_ID=claim-first FARMOUT_TEST_ADMIT_PAUSE="$pause"
    cd "$REPO" && "$FARMOUT" run fake --in-place --owns a.txt --timeout 60s --brief "$T/brief.md"
  ) > "$T/first-out" 2> "$T/first-err" &
  local first=$! i=0
  while [ ! -f "$pause.paused" ] && [ "$i" -lt 100 ]; do sleep 0.1; i=$((i + 1)); done
  [ -f "$pause.paused" ] || fail 'first claimant never paused'
  ( export FARMOUT_TEST_JOB_ID=claim-second
    cd "$REPO" && "$FARMOUT" run fake --in-place --owns a.txt --timeout 60s --brief "$T/brief.md"
  ) > "$T/second-out" 2> "$T/second-err" &
  local second=$! before_release=false
  i=0
  while [ "$i" -lt 100 ]; do
    if grep -q 'overlapping claimed job' "$T/second-err"; then break; fi
    if [ -n "$(meta claim-second pid 2>/dev/null)" ] && [ "$(meta claim-second pid 2>/dev/null)" != null ]; then before_release=true; break; fi
    sleep 0.1; i=$((i + 1))
  done
  echo go > "$pause"
  i=0
  while [ "$i" -lt 150 ]; do
    if grep -q 'ownership overlaps' "$T/first-err" "$T/second-err"; then break; fi
    if [ "$(meta claim-first admitted 2>/dev/null)" = true ] && [ "$(meta claim-second admitted 2>/dev/null)" = true ]; then break; fi
    sleep 0.1; i=$((i + 1))
  done
  local admitted=0 id winner='' loser=''
  for id in claim-first claim-second; do
    if [ "$(meta "$id" admitted 2>/dev/null)" = true ]; then
      admitted=$((admitted + 1)); winner="$id"
      "$FARMOUT" kill "$id" >/dev/null
    else loser="$id"; fi
  done
  wait "$first"; local first_rc=$?
  wait "$second"; local second_rc=$?
  assert_eq "$admitted" 1 'overlapping concurrent claimants must have exactly one winner'
  $before_release && fail 'second admitted while first was claimed but not admitted'
  if [ "$loser" = claim-first ]; then
    assert_eq "$first_rc" 2; assert_contains "$(cat "$T/first-err")" "$winner"
  else
    assert_eq "$second_rc" 2; assert_contains "$(cat "$T/second-err")" "$winner"
  fi
}

test_in_place_adapter_argument_preserves_trailing_newlines() {
  . "$ROOT/lib/common.sh"; . "$ROOT/lib/adapters.sh"
  local job="$T/argv-job" cli arg found
  local text=$'BRIEF TEXT\n\n\n'
  mkdir -p "$job"; printf '%s' "$text" > "$job/brief.md"
  printf '{}\n' > "$job/meta.json"
  for cli in codex kiro copilot cursor fake; do
    _adapter_build_argv "$cli" "$job" '/checkout with spaces' model high
    found=false
    for arg in "${ADAPTER_ARGV[@]}"; do
      [ "$arg" = "$text" ] && found=true
    done
    $found || fail "$cli adapter stripped trailing newlines from the actual brief argument"
  done
}

test_in_place_refuses_untracked_owned_path_when_status_hides_it() {
  make_feature_branch
  git -C "$REPO" config status.showUntrackedFiles no
  printf '%s\n' 'user-owned untracked work' > "$REPO/user-new.txt"
  brief 'FAKE: print ok'; run_job fake --in-place --owns user-new.txt
  assert_eq "$RC" 2 "$ERR"
  assert_contains "$ERR" user-new.txt
  [ ! -d "$FARMOUT_HOME/jobs" ] || fail 'claimed a job before untracked-file refusal'
}

test_in_place_preflight_preserves_clean_index_bytes() {
  make_feature_branch
  local index="$REPO/.git/index" before
  before="$(git hash-object "$index")"
  # Same contents, different stat information: ordinary status refreshes it.
  touch -t 200001010000 "$REPO/a.txt"
  brief 'FAKE: print ok'; run_job fake --in-place --owns a.txt
  assert_eq "$RC" 0 "$ERR"
  assert_eq "$(git hash-object "$index")" "$before" 'preflight rewrote clean index bytes'
}

test_in_place_never_runs_best_effort_commit() {
  # Verify the supervisor's stage/commit commands stay inside mode=write.
  awk '
    /^  if \[ "\$mode" = write \]; then/ { write_block=1 }
    /^  (elif|else|fi)/ { write_block=0 }
    /git -C "\$wt" add -A|commit -q --no-verify -m "farmout \$id"/ {
      if (!write_block) exit 1
      seen++
    }
    END { if (seen != 2) exit 1 }
  ' "$ROOT/bin/farmout" || fail 'supervisor staging/commit escaped mode=write'
  make_feature_branch
  local head; head="$(git -C "$REPO" rev-parse HEAD)"
  brief 'FAKE: write a.txt worker-left-uncommitted' 'FAKE: print done'
  run_job fake --in-place --owns a.txt
  assert_eq "$RC" 0 "$ERR"
  assert_eq "$(git -C "$REPO" rev-parse HEAD)" "$head"
  assert_eq "$(git -C "$REPO" diff --cached)" ''
  assert_contains "$(git -C "$REPO" status --porcelain)" ' M a.txt'
  [ ! -e "$FARMOUT_HOME/jobs/$ID/diff.patch" ] || fail 'captured an in-place patch'
}

test_in_place_allows_absolute_checkout_path_in_brief() {
  make_feature_branch
  brief "Read $REPO/a.txt." 'FAKE: print ok'
  run_job fake --in-place --owns a.txt
  assert_eq "$RC" 0 "$ERR"
}

test_remove_worktree_refuses_unowned_write_meta() {
  local linked="$T/linked"; git -C "$REPO" worktree add -q -b linked-test "$linked"
  local job="$FARMOUT_HOME/jobs/manual"; mkdir -p "$job" "$FARMOUT_HOME/worktrees"
  jq -n --arg r "$REPO" --arg c "$linked" \
    '{mode:"write",repo:$r,worktree:$c,branch:"linked-test"}' > "$job/meta.json"
  "$FARMOUT" discard manual >/dev/null
  [ -d "$linked" ] || fail 'discard removed an unowned linked worktree'
  git -C "$REPO" show-ref --verify --quiet refs/heads/linked-test || fail 'discard deleted an unowned branch'
}

test_in_place_allows_explicit_current_repo() {
  make_feature_branch; brief 'FAKE: print ok'
  run_job fake --in-place --owns a.txt --repo "$REPO"
  assert_eq "$RC" 0 "$ERR"
}

start_queued_in_place() {
  local max="${1:-1}"
  local extra=(); [ $# -lt 2 ] || extra+=("$2")
  printf '{"limits":{"max_jobs":%s}}\n' "$max" > "$FARMOUT_CONFIG"
  mkdir -p "$FARMOUT_HOME/jobs/blocker"
  jq -n --argjson pid "$$" '{admitted:true,status:"running",sup_pid:$pid}' > "$FARMOUT_HOME/jobs/blocker/meta.json"
  if [ "$max" = 2 ]; then
    mkdir -p "$FARMOUT_HOME/jobs/blocker2"
    cp "$FARMOUT_HOME/jobs/blocker/meta.json" "$FARMOUT_HOME/jobs/blocker2/meta.json"
  fi
  brief 'FAKE: print ok'
  ( export FARMOUT_QUEUE_POLL_S=0.1 FARMOUT_TEST_JOB_ID=queued-in-place
    cd "$REPO" && "$FARMOUT" run fake --in-place --owns a.txt --queue "${extra[@]}" --brief "$T/brief.md"
  ) > "$T/queued-out" 2> "$T/queued-err" &
  QUEUED_RUNNER=$!
  local i=0
  while ! grep -q 'queued .* for a slot' "$T/queued-err" && [ "$i" -lt 200 ]; do sleep 0.1; i=$((i + 1)); done
  grep -q 'queued .* for a slot' "$T/queued-err" || fail 'job never queued'
}

test_in_place_queued_base_is_admission_head() {
  make_feature_branch; start_queued_in_place
  printf '%s\n' 'user commit while waiting' > "$REPO/other.txt"
  git -C "$REPO" add other.txt
  git -C "$REPO" -c user.name=t -c user.email=t@t commit -qm 'user commit while waiting'
  local head; head="$(git -C "$REPO" rev-parse HEAD)"
  rm -rf "$FARMOUT_HOME/jobs/blocker"
  wait "$QUEUED_RUNNER"; local rc=$?
  assert_eq "$rc" 0 "$(cat "$T/queued-err")"
  assert_eq "$(meta queued-in-place base)" "$head"
}

test_in_place_land_and_discard_refused_without_git_change() {
  make_feature_branch
  git -C "$REPO" config user.name Worker; git -C "$REPO" config user.email worker@example.test
  brief 'FAKE: commit worker-change a.txt'; run_job fake --in-place --owns a.txt
  assert_eq "$RC" 0 "$ERR"
  local head cmd m="$FARMOUT_HOME/jobs/$ID/meta.json"
  head="$(git -C "$REPO" rev-parse HEAD)"
  # Task 3 supplies these records; pin the command safety contract now.
  jq --arg sha "$head" '.commits = [{sha:$sha}]' "$m" > "$m.tmp" && mv "$m.tmp" "$m"
  prepare_lifecycle_checkout
  lifecycle_snapshot "$T/before"
  cp -R "$FARMOUT_HOME/jobs/$ID" "$T/expected-record"
  for cmd in land discard; do
    run_lifecycle "$cmd" "$ID"
    assert_eq "$RC" 2 "$cmd"
    assert_lifecycle_git_unchanged "$T/before"
    diff -r "$T/expected-record" "$FARMOUT_HOME/jobs/$ID" || fail "$cmd changed record"
    assert_eq "$(meta "$ID" land)" null
    if [ "$cmd" = land ]; then
      assert_contains "$ERR" feature/in-place-test; assert_contains "$ERR" "farmout accept $ID"
    else assert_contains "$ERR" "git revert $head"; fi
  done
}

test_in_place_branch_switch_fails() {
  make_feature_branch; git -C "$REPO" branch other-lane
  brief 'FAKE: checkout other-lane'; run_job fake --in-place --owns a.txt
  assert_eq "$RC" 1 "$ERR"; assert_eq "$(meta "$ID" status)" failed
  assert_eq "$(meta "$ID" branch_end)" other-lane
  assert_contains "$(meta "$ID" error)" feature/in-place-test
  assert_contains "$(meta "$ID" error)" other-lane
}

test_in_place_brief_verbatim() {
  make_feature_branch
  printf '%s\n' 'FAKE: echo-brief' "say \"hi\" \$HOME \`uname\` it's" '' 'last line' '' '' > "$T/brief.md"
  run_job fake --in-place --owns a.txt --owns 'space file.txt'
  assert_eq "$RC" 0 "$ERR"
  local r="$FARMOUT_HOME/jobs/$ID/brief.md"
  head -1 "$r" | grep -q '^# Ground rules (added by farmout)' || fail 'ground rules missing'
  assert_contains "$(cat "$r")" "user's live checkout, on branch feature/in-place-test"
  assert_contains "$(cat "$r")" 'git add -- a.txt space\ file.txt'
  assert_contains "$(cat "$r")" 'Never delete the lock.'
  assert_eq "$(grep -c '^- ' "$r")" 6
  local expected; expected="$(cat <<'EOF'
# Ground rules (added by farmout)
- Your working directory is the user's live checkout, on branch feature/in-place-test. Other agents may be working in this same checkout at the same time. It is not a copy: every change is real.
- You own only these paths: a.txt space\ file.txt. Edit nothing else. If the task needs a file outside them, stop and say so in your final message.
- Commit your own paths by name: `git add -- a.txt space\ file.txt` then `git commit -m "<msg>" -- a.txt space\ file.txt`. Never `git add -A`, `git add .`, `git add -u` or `git commit -a`.
- Never `checkout`/`switch` a branch, `stash`, `reset`, `rebase`, `merge`, `pull`, `push`, `commit --amend`, `clean`, or `restore` a path you did not change. Leave other agents' uncommitted changes alone.
- If git reports that `index.lock` exists, wait a few seconds and retry. Never delete the lock.
- If this checkout does not match what the brief describes, stop and report that as your first finding.
EOF
)"
  assert_eq "$(head -7 "$r")" "$expected" exact-preamble
  cmp -s "$T/brief.md" <(tail -c "$(wc -c < "$T/brief.md")" "$r") || fail 'brief not passed verbatim'
  # echo-brief prints the exact worker argument plus one printf newline.
  cmp -s <(cat "$r"; printf '\n') "$FARMOUT_HOME/jobs/$ID/result.md" \
    || fail 'actual worker brief argument lost trailing blank lines'
}

assert_in_place_brief_leading_dash_path() { # owned pathspec
  make_feature_branch; brief 'FAKE: print done'
  local p="$1" expected r
  run_job fake --in-place --owns "$p" --owns 'space file.txt'
  assert_eq "$RC" 0 "$ERR"
  r="$FARMOUT_HOME/jobs/$ID/brief.md"
  expected="$(cat <<EOF
# Ground rules (added by farmout)
- Your working directory is the user's live checkout, on branch feature/in-place-test. Other agents may be working in this same checkout at the same time. It is not a copy: every change is real.
- You own only these paths: $p space\\ file.txt. Edit nothing else. If the task needs a file outside them, stop and say so in your final message.
- Commit your own paths by name: \`git add -- $p space\\ file.txt\` then \`git commit -m "<msg>" -- $p space\\ file.txt\`. Never \`git add -A\`, \`git add .\`, \`git add -u\` or \`git commit -a\`.
- Never \`checkout\`/\`switch\` a branch, \`stash\`, \`reset\`, \`rebase\`, \`merge\`, \`pull\`, \`push\`, \`commit --amend\`, \`clean\`, or \`restore\` a path you did not change. Leave other agents' uncommitted changes alone.
- If git reports that \`index.lock\` exists, wait a few seconds and retry. Never delete the lock.
- If this checkout does not match what the brief describes, stop and report that as your first finding.
EOF
)"
  assert_eq "$(head -7 "$r")" "$expected" "$p exact-preamble"
}

test_in_place_brief_leading_dash_all_option() { assert_in_place_brief_leading_dash_path -A; }
test_in_place_brief_leading_dash_filename() { assert_in_place_brief_leading_dash_path -odd.txt; }

test_in_place_owns_must_be_repo_relative() {
  make_feature_branch; brief 'FAKE: print ok'
  local p
  for p in /tmp/outside ../outside ':(top)../outside' ''; do
    run_job fake --in-place --owns "$p"
    assert_eq "$RC" 2 "$p: $ERR"
    [ ! -d "$FARMOUT_HOME/jobs" ] || fail 'claimed a job for invalid owns'
  done
}

test_in_place_allows_dirty_path_outside_owns() {
  make_feature_branch; printf 'user work\n' > "$REPO/user.txt"
  git -C "$REPO" add user.txt
  local index; index="$(git -C "$REPO" diff --cached)"
  brief 'FAKE: print ok'; run_job fake --in-place --owns a.txt
  assert_eq "$RC" 0 "$ERR"
  assert_eq "$(git -C "$REPO" diff --cached)" "$index"
  assert_eq "$(cat "$REPO/user.txt")" 'user work'
}

test_in_place_repo_must_be_current_checkout() {
  make_feature_branch
  local linked="$T/linked"; git -C "$REPO" worktree add -q -b linked-test "$linked"
  brief 'FAKE: print ok'; run_job fake --in-place --owns a.txt --repo "$linked"
  assert_eq "$RC" 2 "$ERR"; assert_contains "$ERR" 'current checkout'
  [ ! -d "$FARMOUT_HOME/jobs" ] || fail 'claimed a job before preflight'
}

test_in_place_allows_default_branch_with_override() {
  git -C "$REPO" branch -M main
  brief 'FAKE: print ok'; run_job fake --in-place --owns a.txt --allow-default-branch
  assert_eq "$RC" 0 "$ERR"
}

test_in_place_refuses_dirty_owned_path() {
  make_feature_branch; printf dirty >> "$REPO/a.txt"
  brief 'FAKE: print ok'; run_job fake --in-place --owns a.txt
  assert_eq "$RC" 2 "$ERR"; assert_contains "$ERR" a.txt
  [ ! -d "$FARMOUT_HOME/jobs" ] || fail 'claimed a job before preflight'
}

test_in_place_refuses_default_branch() {
  git -C "$REPO" branch -M main
  brief 'FAKE: print ok'; run_job fake --in-place --owns a.txt
  assert_eq "$RC" 2 "$ERR"; assert_contains "$ERR" '--allow-default-branch'
  [ ! -d "$FARMOUT_HOME/jobs" ] || fail 'claimed a job before preflight'
}

test_in_place_refuses_detached_head() {
  git -C "$REPO" checkout -q --detach
  brief 'FAKE: print ok'; run_job fake --in-place --owns a.txt
  assert_eq "$RC" 2 "$ERR"
  assert_contains "$ERR" 'detached HEAD'
  [ ! -d "$FARMOUT_HOME/jobs" ] || fail 'claimed a job before preflight'
}

test_in_place_runs_in_checkout() {
  make_feature_branch
  git -C "$REPO" config user.name Worker
  git -C "$REPO" config user.email worker@example.test
  brief 'FAKE: commit worker-change a.txt'
  local before after; before="$(git -C "$REPO" rev-parse HEAD)"
  run_job fake --in-place --owns a.txt
  assert_eq "$RC" 0 "$ERR"
  after="$(git -C "$REPO" rev-parse HEAD)"
  [ "$before" != "$after" ] || fail 'in-place worker did not advance checkout HEAD'
  assert_eq "$(meta "$ID" mode)" in-place
  assert_eq "$(meta "$ID" worktree)" null
  assert_eq "$(meta "$ID" branch)" null
  assert_eq "$(meta "$ID" checkout)" "$REPO"
  assert_eq "$(meta "$ID" base)" "$before"
  assert_eq "$(meta "$ID" head_end)" "$after"
  assert_eq "$(meta "$ID" branch_end)" feature/in-place-test
  assert_eq "$(jq -c .owns "$FARMOUT_HOME/jobs/$ID/meta.json")" '["a.txt"]'
  [ ! -e "$FARMOUT_HOME/worktrees" ] || [ -z "$(find "$FARMOUT_HOME/worktrees" -mindepth 1 -print -quit)" ] || fail 'created a worktree'
  [ ! -e "$FARMOUT_HOME/jobs/$ID/diff.patch" ] || fail 'captured an in-place patch'
  assert_eq "$(git -C "$REPO" log -1 --format=%an)" Worker
}

test_in_place_flag_conflicts() {
  make_feature_branch; brief 'FAKE: ok'
  run_job fake --in-place --owns a.txt --ref HEAD
  assert_eq "$RC" 2 ref
  run_job fake --in-place --owns a.txt --no-repo
  assert_eq "$RC" 2 no-repo
  run_job fake --in-place --owns a.txt --with a.txt
  assert_eq "$RC" 2 with
  run_job fake --in-place --owns a.txt --out out.md
  assert_eq "$RC" 2 out
  run_job fake --in-place
  assert_eq "$RC" 2 missing-owns
}

test_remove_worktree_refuses_in_place_meta() {
  local linked="$T/linked"; git -C "$REPO" worktree add -q -b linked-test "$linked"
  local job="$FARMOUT_HOME/jobs/manual"; mkdir -p "$job"
  jq -n --arg r "$REPO" --arg c "$linked" \
    '{mode:"in-place",repo:$r,checkout:$c,worktree:$c,branch:"linked-test"}' > "$job/meta.json"
  FARMOUT_HOME="$FARMOUT_HOME" "$FARMOUT" clean --all >/dev/null
  [ -d "$linked" ] || fail 'clean removed the live linked worktree'
  git -C "$REPO" show-ref --verify --quiet refs/heads/linked-test || fail 'clean deleted the live branch'
}

test_parse_duration() {
  . "$ROOT/lib/common.sh"
  assert_eq "$(parse_duration 90s)" 90
  assert_eq "$(parse_duration 30m)" 1800
  assert_eq "$(parse_duration 2h)" 7200
  assert_eq "$(parse_duration 45)" 45
  parse_duration 3m3 >/dev/null && fail "3m3 accepted"
  parse_duration "" >/dev/null && fail "empty accepted"
  return 0
}

test_farmout_home_relativized() {
  local out; out="$(cd "$T" && FARMOUT_HOME=relhome bash -c '. "$1"; echo "$FARMOUT_HOME"' bash "$ROOT/lib/common.sh")"
  assert_eq "$out" "$T/relhome"
}

test_extract_fixtures() {
  . "$ROOT/lib/common.sh"; . "$ROOT/lib/adapters.sh"
  local j="$T/job"; mkdir -p "$j"
  cp "$FIX/kiro.log" "$j/log"; adapter_extract kiro "$j"
  assert_eq "$(cat "$j/result.md")" "$(printf 'LINE1\nLINE2')" kiro
  cp "$FIX/kiro-truncated.log" "$j/log"; adapter_extract kiro "$j"
  assert_eq "$(cat "$j/result.md")" "$(printf 'LINE1\nLINE2')" kiro-truncated
  cp "$FIX/kiro-narration.log" "$j/log"; adapter_extract kiro "$j"
  assert_eq "$(cat "$j/result.md")" "Final answer." kiro-narration
  cp "$FIX/copilot.log" "$j/log"; adapter_extract copilot "$j"
  assert_eq "$(cat "$j/result.md")" PONG copilot
  # cursor has its own dedicated test against the real stream-json fixture.
  : > "$j/log"; echo PONG > "$j/codex.last"; adapter_extract codex "$j"
  assert_eq "$(cat "$j/result.md")" PONG codex
  rm "$j/codex.last"; adapter_extract codex "$j"
  assert_eq "$(cat "$j/result.md")" "" codex-missing
}

# The real capture has tool_call/thinking/system/assistant events before the
# result; the final message must still come from the type=="result" event.
test_extract_cursor_stream_fixture() {
  . "$ROOT/lib/common.sh"; . "$ROOT/lib/adapters.sh"
  local j="$T/job"; mkdir -p "$j"
  cp "$FIX/cursor.log" "$j/log"
  adapter_extract cursor "$j"
  local expect; expect="$(tail -1 "$FIX/cursor.log" | jq -r '.result')"
  [ -n "$expect" ] || fail "fixture's own result field is empty; test is not meaningful"
  assert_eq "$(cat "$j/result.md")" "$expect" cursor-stream-fixture
}

# codex's final message always comes from -o's codex.last, never from the
# log; a real --json stream in the log must not change that.
test_extract_codex_json_fixture() {
  . "$ROOT/lib/common.sh"; . "$ROOT/lib/adapters.sh"
  local j="$T/job"; mkdir -p "$j"
  cp "$FIX/codex-json.log" "$j/log"
  echo FINAL > "$j/codex.last"
  adapter_extract codex "$j"
  assert_eq "$(cat "$j/result.md")" FINAL codex-json-fixture
}

test_meta_tmp_is_per_process() {
  grep -Fq 'meta.json.tmp.$$' "$ROOT/lib/common.sh" \
    || fail "meta_write/meta_set temp file name does not include the pid"
}

test_effort_argv_per_cli() {
  . "$ROOT/lib/common.sh"; . "$ROOT/lib/adapters.sh"
  local j="$T/job"; mkdir -p "$j"; printf 'BRIEF TEXT\n' > "$j/brief.md"
  local out

  out="$(adapter_argv codex "$j" /wt m high 2>/dev/null)"
  assert_contains "$out" $'\n-m\nm' codex-model
  assert_contains "$out" $'\n-c\nmodel_reasoning_effort=high' codex-effort
  assert_contains "$out" $'\n--json' codex-json
  out="$(adapter_argv codex "$j" /wt "" high 2>/dev/null)"
  assert_contains "$out" $'\n-c\nmodel_reasoning_effort=high' codex-effort-nomodel
  case "$out" in *$'\n-m\n'*) fail "codex passed -m without a model" ;; esac

  out="$(adapter_argv copilot "$j" /wt m high 2>/dev/null)"
  assert_contains "$out" $'\n--model\nm' copilot-model
  assert_contains "$out" $'\n--reasoning-effort\nhigh' copilot-effort

  out="$(adapter_argv kiro "$j" /wt m high 2>/dev/null)"
  assert_contains "$out" $'\n--model\nm' kiro-model
  assert_contains "$out" $'\n--effort\nhigh' kiro-effort
  assert_contains "$out" $'\n--agent-engine\nv2\n' kiro-engine-default
  printf '%s\n' '{"engine":"v3"}' > "$j/meta.json"
  out="$(adapter_argv kiro "$j" /wt "" "" 2>/dev/null)"
  assert_contains "$out" $'\n--agent-engine\nv3\n' kiro-engine-from-meta
  rm -f "$j/meta.json"

  out="$(adapter_argv cursor "$j" /wt m high 2>/dev/null)"
  assert_contains "$out" $'\n--model\nm[effort=high]' cursor-model-effort
  assert_contains "$out" $'\n--output-format\nstream-json' cursor-stream

  # Effort is resolved once, by adapter_effective_effort, before argv is
  # built; _adapter_build_argv receives the already-resolved value.
  local resolved; resolved="$(adapter_effective_effort cursor "" high 2>"$T/cursor-err")"
  assert_eq "$resolved" "" cursor-effort-dropped-without-model
  assert_contains "$(cat "$T/cursor-err")" effort cursor-warns

  out="$(adapter_argv cursor "$j" /wt "" "$resolved" 2>/dev/null)"
  case "$out" in *'[effort='*) fail "cursor printed effort without a model" ;; esac
}

test_build_argv_resolves_effort_once() {
  awk '/^_adapter_build_argv\(\) \{/{f=1} f{print} f && /^}/{exit}' "$ROOT/lib/adapters.sh" \
    | grep -q 'adapter_effective_effort' \
    && fail "_adapter_build_argv must not call adapter_effective_effort; effort is resolved once by the caller"
  return 0
}

# ---- config -----------------------------------------------------------------

test_config_missing_uses_defaults() {
  . "$ROOT/lib/common.sh"; . "$ROOT/lib/adapters.sh"; . "$ROOT/lib/config.sh"
  export FARMOUT_CONFIG="$T/still-no-such-config.json"
  cfg_load
  assert_eq "$(cfg_worker fake model)" ""
  assert_eq "$(cfg_worker fake enabled)" ""
  assert_eq "$(cfg_limit max_jobs)" ""
  assert_eq "$(cfg_routing_json)" "$CFG_DEFAULT_ROUTING"
  assert_eq "$(cfg_routing_json | jq -r '[.[].kind] | join(" ")')" "$CFG_KINDS" every-kind-has-a-default
}

test_config_routing_array_replaces_defaults() {
  . "$ROOT/lib/common.sh"; . "$ROOT/lib/adapters.sh"; . "$ROOT/lib/config.sh"
  export FARMOUT_CONFIG="$T/config.json"
  printf '%s\n' '{"routing":[{"kind":"review","prefer":"kiro","fallback":null}]}' > "$FARMOUT_CONFIG"
  cfg_load
  assert_eq "$(cfg_routing_json)" '[{"kind":"review","prefer":"kiro","fallback":null}]'
  cfg_routing_for_kind implement >/dev/null && fail "implement still routed after an explicit routing array"
  printf '%s\n' '{"routing":[]}' > "$FARMOUT_CONFIG"
  cfg_load
  assert_eq "$(cfg_routing_json)" "[]" empty-array
}

test_config_digit_strings_and_typed_values() {
  . "$ROOT/lib/common.sh"; . "$ROOT/lib/adapters.sh"; . "$ROOT/lib/config.sh"
  export FARMOUT_CONFIG="$T/config.json"
  printf '%s\n' '{"workers":{"fake":{"timeout_min":"20","enabled":"false","model":5}},"limits":{"stall_min":"10","max_jobs":0}}' > "$FARMOUT_CONFIG"
  cfg_load
  assert_eq "$(cfg_worker fake timeout_min)" 20 digit-string-timeout
  assert_eq "$(cfg_limit stall_min)" 10 digit-string-stall
  assert_eq "$(cfg_limit max_jobs 2>"$T/e1")" "" out-of-range
  assert_contains "$(cat "$T/e1")" "invalid limits.max_jobs"
  assert_eq "$(cfg_worker fake enabled 2>"$T/e2")" "" string-enabled
  assert_contains "$(cat "$T/e2")" "invalid workers.fake.enabled"
  assert_eq "$(cfg_worker fake model 2>/dev/null)" "" number-model
}

test_config_kiro_engine() {
  . "$ROOT/lib/common.sh"; . "$ROOT/lib/adapters.sh"; . "$ROOT/lib/config.sh"
  export FARMOUT_CONFIG="$T/config.json"
  printf '%s\n' '{"workers":{"kiro":{"engine":"v3"},"fake":{"engine":"v3"}}}' > "$FARMOUT_CONFIG"
  cfg_load
  assert_eq "$(cfg_worker kiro engine)" v3 kiro-engine
  assert_eq "$(cfg_worker fake engine 2>"$T/e1")" "" engine-is-kiro-only
  assert_contains "$(cat "$T/e1")" "invalid workers.fake.engine"
  printf '%s\n' '{"workers":{"kiro":{"engine":"v9"}}}' > "$FARMOUT_CONFIG"
  cfg_load
  assert_eq "$(cfg_worker kiro engine 2>/dev/null)" "" unknown-engine
}

test_config_timeout_precedence() {
  brief "FAKE: print hi"
  export FARMOUT_CONFIG="$T/still-no-such-config.json"
  run_job fake
  assert_eq "$(meta "$ID" timeout_s)" 1800 default

  FARMOUT_TIMEOUT=50s run_job fake
  assert_eq "$(meta "$ID" timeout_s)" 50 env

  local cfg="$T/config.json"
  printf '%s\n' '{"workers":{"fake":{"timeout_min":2}}}' > "$cfg"
  export FARMOUT_CONFIG="$cfg"
  FARMOUT_TIMEOUT=50s run_job fake
  assert_eq "$(meta "$ID" timeout_s)" 120 config

  FARMOUT_TIMEOUT=50s run_job fake --timeout 9s
  assert_eq "$(meta "$ID" timeout_s)" 9 flag
}

test_config_invalid_value_warns_and_defaults() {
  local cfg="$T/config.json"
  printf '%s\n' '{"workers":{"fake":{"model":"-x","effort":"turbo"}}}' > "$cfg"
  export FARMOUT_CONFIG="$cfg"
  brief "FAKE: print hi"
  run_job fake
  assert_eq "$RC" 0 "$ERR"
  assert_contains "$ERR" "invalid workers.fake.model"
  assert_contains "$ERR" "invalid workers.fake.effort"
  assert_eq "$(meta "$ID" model)" null
  assert_eq "$(meta "$ID" effort)" null
}

test_disabled_cli_refused() {
  local cfg="$T/config.json"
  printf '%s\n' '{"workers":{"fake":{"enabled":false}}}' > "$cfg"
  export FARMOUT_CONFIG="$cfg"
  brief "FAKE: print hi"
  run_job fake
  assert_eq "$RC" 2
  assert_contains "$ERR" "disabled"
  [ -d "$FARMOUT_HOME/jobs" ] && fail "a job dir was created"
  return 0
}

# ---- run: new flags (kind, title, model/effort recording, parentage) ------

test_kind_recorded_and_validated() {
  brief "FAKE: print hi"
  run_job fake --kind review
  assert_eq "$RC" 0 "$ERR"
  assert_eq "$(meta "$ID" kind)" review
  run_job fake --kind bogus
  assert_eq "$RC" 2
  assert_contains "$ERR" "kind"
}

test_title_flag_and_derivation() {
  printf '%s\n' '# Task' '' 'Do the thing properly. Then celebrate.' '' 'FAKE: print hi' > "$T/brief.md"
  run_job fake
  assert_eq "$RC" 0 "$ERR"
  assert_eq "$(meta "$ID" title)" "Do the thing properly."

  run_job fake --title "Explicit Title"
  assert_eq "$(meta "$ID" title)" "Explicit Title"

  local long; long="$(printf 'word%02d ' $(seq 1 20))"
  printf '%s\n' "$long" '' 'FAKE: print hi' > "$T/brief.md"
  run_job fake
  local t; t="$(meta "$ID" title)"
  [ "${#t}" -le 80 ] || fail "title not capped at 80 (len=${#t})"
}

test_title_headings_only_gives_null() {
  printf '%s\n' '# Heading' '' '## Sub heading' '' > "$T/brief.md"
  run_job fake
  assert_eq "$(meta "$ID" title)" null
}

test_parent_session_recorded() {
  brief "FAKE: print hi"
  CLAUDE_CODE_SESSION_ID=sess-123 CLAUDE_PID=4242 run_job fake
  assert_eq "$RC" 0 "$ERR"
  assert_eq "$(meta "$ID" parent_session)" sess-123
  assert_eq "$(meta "$ID" parent_pid)" 4242

  # The harness itself may run inside a Claude Code session (these vars set
  # ambiently); clear them to test the true "unset" case.
  unset CLAUDE_CODE_SESSION_ID CLAUDE_PID
  run_job fake
  assert_eq "$(meta "$ID" parent_session)" null
  assert_eq "$(meta "$ID" parent_pid)" null
}

test_source_branch_recorded() {
  brief "FAKE: print hi"
  local trunk; trunk="$(git -C "$REPO" symbolic-ref --short HEAD)"
  run_job fake
  assert_eq "$(meta "$ID" source_branch)" "$trunk"

  git -C "$REPO" checkout -q --detach
  run_job fake
  assert_eq "$(meta "$ID" source_branch)" null
  git -C "$REPO" checkout -q "$trunk"
}

# ---- run auto: routing ------------------------------------------------------

test_auto_prefers_usable() {
  local cfg="$T/config.json"
  printf '%s\n' '{"routing":[{"kind":"review","prefer":"fake","fallback":null}]}' > "$cfg"
  export FARMOUT_CONFIG="$cfg"
  brief "FAKE: print hi"
  run_job auto --kind review
  assert_eq "$RC" 0 "$ERR"
  assert_eq "$(meta "$ID" cli)" fake
  assert_eq "$(meta "$ID" route)" "prefer fake"
}

test_auto_falls_back_when_prefer_logged_out() {
  local cfg="$T/config.json"
  printf '%s\n' '{"routing":[{"kind":"review","prefer":"codex","fallback":"fake"}]}' > "$cfg"
  export FARMOUT_CONFIG="$cfg"
  export FARMOUT_DOCTOR_STUB="codex=logged-out"
  brief "FAKE: print hi"
  run_job auto --kind review
  assert_eq "$RC" 0 "$ERR"
  assert_eq "$(meta "$ID" cli)" fake
  assert_contains "$(meta "$ID" route)" "fallback fake"
  assert_contains "$(meta "$ID" route)" "logged-out"
  assert_contains "$ERR" "logged-out"
  unset FARMOUT_DOCTOR_STUB
}

test_auto_falls_back_when_prefer_disabled() {
  local cfg="$T/config.json"
  printf '%s\n' '{"workers":{"codex":{"enabled":false}},"routing":[{"kind":"review","prefer":"codex","fallback":"fake"}]}' > "$cfg"
  export FARMOUT_CONFIG="$cfg"
  brief "FAKE: print hi"
  run_job auto --kind review
  assert_eq "$RC" 0 "$ERR"
  assert_eq "$(meta "$ID" cli)" fake
  assert_eq "$(meta "$ID" route)" "fallback fake: disabled"
}

test_auto_all_unusable_exits_2_with_reasons() {
  local cfg="$T/config.json"
  printf '%s\n' '{"workers":{"codex":{"enabled":false}},"routing":[{"kind":"review","prefer":"codex","fallback":"kiro"}]}' > "$cfg"
  export FARMOUT_CONFIG="$cfg"
  export FARMOUT_DOCTOR_STUB="kiro=logged-out"
  brief "FAKE: print hi"
  run_job auto --kind review
  assert_eq "$RC" 2
  assert_contains "$ERR" codex
  assert_contains "$ERR" disabled
  assert_contains "$ERR" kiro
  assert_contains "$ERR" logged-out
  [ -d "$FARMOUT_HOME/jobs" ] && fail "a job dir was created"
  unset FARMOUT_DOCTOR_STUB
  return 0
}

# No config file: auto uses the built-in routing table (implement => cursor).
# cursor-agent is a PATH stub, so no real CLI runs.
test_auto_default_routing_without_config() {
  mkdir -p "$T/bin"
  printf '%s\n' '#!/bin/bash' 'echo '"'"'{"type":"result","result":"PONG"}'"'"'' > "$T/bin/cursor-agent"
  chmod +x "$T/bin/cursor-agent"
  export PATH="$T/bin:$PATH"
  export FARMOUT_DOCTOR_STUB="cursor=ok"
  brief "Implement the thing."
  run_job auto --kind implement
  assert_eq "$RC" 0 "$ERR"
  assert_eq "$(meta "$ID" cli)" cursor
  assert_eq "$(meta "$ID" route)" "prefer cursor"
  unset FARMOUT_DOCTOR_STUB
}

test_auto_requires_kind() {
  brief "FAKE: print hi"
  run_job auto
  assert_eq "$RC" 2
  assert_contains "$ERR" "requires --kind"
  return 0
}

test_route_recorded() {
  local cfg="$T/config.json"
  printf '%s\n' '{"routing":[{"kind":"review","prefer":"fake","fallback":null}]}' > "$cfg"
  export FARMOUT_CONFIG="$cfg"
  brief "FAKE: print hi"
  run_job auto --kind review
  assert_eq "$(meta "$ID" route)" "prefer fake" auto-prefer

  printf '%s\n' '{"workers":{"codex":{"enabled":false}},"routing":[{"kind":"review","prefer":"codex","fallback":"fake"}]}' > "$cfg"
  run_job auto --kind review
  assert_eq "$(meta "$ID" route)" "fallback fake: disabled" auto-fallback

  run_job fake
  assert_eq "$(meta "$ID" route)" null explicit-cli
}

# ---- run: max jobs / admission ---------------------------------------------

test_max_jobs_refuses_with_exit_4() {
  local cfg="$T/config.json"
  printf '%s\n' '{"limits":{"max_jobs":1}}' > "$cfg"
  export FARMOUT_CONFIG="$cfg"
  brief "FAKE: spawn" "FAKE: hang"
  (cd "$REPO" && "$FARMOUT" run fake --timeout 60s --brief "$T/brief.md") >"$T/out1" 2>/dev/null &
  local runner=$!
  local i=0; while [ ! -s "$T/out1" ] && [ $i -lt 50 ]; do sleep 0.2; i=$((i + 1)); done
  local id1; id1="$(head -1 "$T/out1")"; wait_for_meta_pid "$id1"

  brief "FAKE: print hi"
  (cd "$REPO" && "$FARMOUT" run fake --brief "$T/brief.md") >"$T/out2" 2>"$T/err2"
  local rc2=$?
  assert_eq "$rc2" 4
  assert_contains "$(cat "$T/err2")" "max concurrent jobs"
  assert_eq "$(ls "$FARMOUT_HOME/jobs" | wc -l | tr -d ' ')" 1 "rejected job left a dir behind"

  "$FARMOUT" kill "$id1" >/dev/null
  wait "$runner" 2>/dev/null
  return 0
}

# Admission by counting peers must not care about id order at all: force A's
# id lexically *larger* than B's, with A already running - B must still be
# refused. (The id-ordered algorithm this replaced would have wrongly
# admitted B here; this is the architect's reproduction, pinned.)
test_admission_ignores_id_order() {
  local cfg="$T/config.json"
  printf '%s\n' '{"limits":{"max_jobs":1}}' > "$cfg"
  export FARMOUT_CONFIG="$cfg"
  local a_id="20260101-000000-fake-ffff" b_id="20260101-000000-fake-0000"
  brief "FAKE: spawn" "FAKE: hang"
  ( export FARMOUT_TEST_JOB_ID="$a_id"
    cd "$REPO" && "$FARMOUT" run fake --timeout 60s --brief "$T/brief.md" ) >"$T/aout" 2>/dev/null &
  local arunner=$!
  local i=0; while [ ! -s "$T/aout" ] && [ $i -lt 50 ]; do sleep 0.2; i=$((i + 1)); done
  wait_for_meta_pid "$a_id"

  brief "FAKE: print hi"
  ( export FARMOUT_TEST_JOB_ID="$b_id"
    cd "$REPO" && "$FARMOUT" run fake --brief "$T/brief.md" ) >"$T/bout" 2>"$T/berr"
  local brc=$?
  assert_eq "$brc" 4 "b (lexically-smaller id) must be refused although a (lexically-larger id) is running: $(cat "$T/berr")"
  assert_contains "$(cat "$T/berr")" "max concurrent jobs"
  assert_eq "$(ls "$FARMOUT_HOME/jobs" | wc -l | tr -d ' ')" 1 "b left a dir behind"

  "$FARMOUT" kill "$a_id" >/dev/null
  wait "$arunner" 2>/dev/null
  return 0
}

# The architect's counterexample, made deterministic: Q claims first and
# pauses (via the test seam) after deciding it is under the limit but
# *before* flipping admitted:true - exactly the window where a naive
# check-then-act counter would double-admit. P starts and scans while Q is
# still sitting in that window (unadmitted, but its sup_pid is alive) and
# must never get in. Only after P has given up is Q released.
test_admission_single_slot_counterexample() {
  local cfg="$T/config.json"
  printf '%s\n' '{"limits":{"max_jobs":1}}' > "$cfg"
  export FARMOUT_CONFIG="$cfg"
  local q_id="20260101-010101-fake-cafe" p_id="20260101-010101-fake-0001"
  local pause="$T/q.pause"; mkfifo "$pause"

  brief "FAKE: hang"
  ( export FARMOUT_TEST_JOB_ID="$q_id" FARMOUT_TEST_ADMIT_PAUSE="$pause"
    cd "$REPO" && "$FARMOUT" run fake --timeout 30s --brief "$T/brief.md" ) >"$T/qout" 2>"$T/qerr" &
  local qrunner=$!
  local i=0; while [ ! -f "$pause.paused" ] && [ $i -lt 100 ]; do sleep 0.1; i=$((i + 1)); done
  [ -f "$pause.paused" ] || fail "q never reached the admit pause"

  brief "FAKE: print hi"
  ( export FARMOUT_TEST_JOB_ID="$p_id"
    cd "$REPO" && "$FARMOUT" run fake --brief "$T/brief.md" ) >"$T/pout" 2>"$T/perr"
  local prc=$?

  echo go > "$pause"
  wait_for_meta_pid "$q_id"

  assert_eq "$prc" 4 "p must not slip in while q is unadmitted-but-alive: $(cat "$T/perr")"
  assert_contains "$(cat "$T/perr")" "max concurrent jobs"
  assert_eq "$(meta "$q_id" admitted)" true
  assert_eq "$("$FARMOUT" status "$q_id" | jq -r .status)" running
  assert_eq "$(ls "$FARMOUT_HOME/jobs" | wc -l | tr -d ' ')" 1 "p left a dir behind"

  "$FARMOUT" kill "$q_id" >/dev/null
  wait "$qrunner" 2>/dev/null
  return 0
}

test_admission_ignores_dir_without_meta() {
  local cfg="$T/config.json"
  printf '%s\n' '{"limits":{"max_jobs":1}}' > "$cfg"
  export FARMOUT_CONFIG="$cfg"
  mkdir -p "$FARMOUT_HOME/jobs/20260101-000000-fake-dead1"
  brief "FAKE: print hi"
  run_job fake
  assert_eq "$RC" 0 "$ERR"
  assert_eq "$(meta "$ID" status)" ok
}

test_admission_ignores_unadmitted_dead_sup_pid() {
  local cfg="$T/config.json"
  printf '%s\n' '{"limits":{"max_jobs":1}}' > "$cfg"
  export FARMOUT_CONFIG="$cfg"
  local stale="$FARMOUT_HOME/jobs/20260101-000000-fake-dead2"
  mkdir -p "$stale"
  ( : ) & local deadpid=$!; wait "$deadpid" 2>/dev/null
  jq -n --argjson sup "$deadpid" \
    '{id:"stale", cli:"fake", mode:"read", status:"running", admitted:false, sup_pid:$sup}' \
    > "$stale/meta.json"
  brief "FAKE: print hi"
  run_job fake
  assert_eq "$RC" 0 "$ERR"
  assert_eq "$(meta "$ID" status)" ok
}

# An old job dir from before the "admitted" field existed: unadmitted (field
# missing) but finished (status "ok"), with sup_pid pointing at a pid the OS
# has since reused for something else that happens to be alive right now
# (here, this shell). It must not count as a peer: only a *running* unadmitted
# job with a live sup_pid should.
test_admission_ignores_unadmitted_finished_reused_pid() {
  local cfg="$T/config.json"
  printf '%s\n' '{"limits":{"max_jobs":1}}' > "$cfg"
  export FARMOUT_CONFIG="$cfg"
  local stale="$FARMOUT_HOME/jobs/20260101-000000-fake-dead4"
  mkdir -p "$stale"
  jq -n --argjson sup "$$" \
    '{id:"stale", cli:"fake", mode:"read", status:"ok", sup_pid:$sup}' \
    > "$stale/meta.json"
  brief "FAKE: print hi"
  run_job fake
  assert_eq "$RC" 0 "$ERR"
  assert_eq "$(meta "$ID" status)" ok
}

test_admission_counts_unparsable_meta() {
  local cfg="$T/config.json"
  printf '%s\n' '{"limits":{"max_jobs":1}}' > "$cfg"
  export FARMOUT_CONFIG="$cfg"
  local torn="$FARMOUT_HOME/jobs/20260101-000000-fake-dead3"
  mkdir -p "$torn"
  printf '{"status": "running", "sup_pid": ' > "$torn/meta.json"
  brief "FAKE: print hi"
  (cd "$REPO" && "$FARMOUT" run fake --brief "$T/brief.md") >"$T/out" 2>"$T/err"
  local rc=$?
  assert_eq "$rc" 4 "an unparsable meta.json must be counted, to be safe: $(cat "$T/err")"
  assert_contains "$(cat "$T/err")" "max concurrent jobs"
}

# Run this one several times when verifying: it is a concurrency stress
# test. Every iteration must admit at least one and at most `max_jobs`, and
# every refused start must leave no dir behind.
test_admission_stress_5_starts_max_2() {
  local cfg="$T/config.json"
  printf '%s\n' '{"limits":{"max_jobs":2}}' > "$cfg"
  export FARMOUT_CONFIG="$cfg"
  # Each admitted job holds its slot for 8s, longer than launch jitter on a
  # loaded machine: a late launch must meet two running jobs, never a freed slot.
  brief "FAKE: sleep 8" "FAKE: print a"
  local iterations=2 iter
  for iter in $(seq 1 "$iterations"); do
    local k
    for k in 1 2 3 4 5; do
      (cd "$REPO" && "$FARMOUT" run fake --brief "$T/brief.md") >"$T/o$k" 2>"$T/e$k" &
    done
    wait
    local admitted=0 refused=0
    for k in 1 2 3 4 5; do
      if grep -q "at max concurrent jobs" "$T/e$k"; then refused=$((refused + 1))
      else admitted=$((admitted + 1)); fi
    done
    [ "$admitted" -ge 1 ] || fail "iteration $iter: 0 admitted"
    [ "$admitted" -le 2 ] || fail "iteration $iter: $admitted admitted (max 2)"
    assert_eq "$(ls "$FARMOUT_HOME/jobs" 2>/dev/null | wc -l | tr -d ' ')" "$admitted" "iteration $iter: leftover dirs from refused jobs"
    "$FARMOUT" clean --all >/dev/null 2>&1
  done
}

# ---- doctor -----------------------------------------------------------------

test_doctor_json_shape() {
  export FARMOUT_DOCTOR_STUB="codex=ok,kiro=ok,copilot=not-verified,cursor=ok"
  local out; out="$("$FARMOUT" doctor --json)"
  echo "$out" | jq -e 'type == "array"' >/dev/null || fail "doctor --json is not a JSON array: $out"
  assert_eq "$(echo "$out" | jq 'length')" 4 doctor-count
  local cli
  for cli in codex kiro copilot cursor; do
    echo "$out" | jq -e --arg c "$cli" 'map(select(.cli == $c)) | length == 1' >/dev/null \
      || fail "doctor --json missing entry for $cli"
    echo "$out" | jq -e --arg c "$cli" 'map(select(.cli == $c))[0] | has("status") and has("version")' >/dev/null \
      || fail "doctor --json entry for $cli missing keys"
  done
  assert_eq "$(echo "$out" | jq -r 'map(select(.cli=="codex"))[0].status')" ok
  unset FARMOUT_DOCTOR_STUB
}

# ---- arcade -----------------------------------------------------------------

test_arcade_verb_execs_server() {
  local stub="$T/python-stub" rec="$T/argv.out"
  cat > "$stub" <<EOF
#!/bin/bash
printf '%s\n' "\$@" > "$rec"
EOF
  chmod +x "$stub"
  FARMOUT_PYTHON="$stub" "$FARMOUT" arcade --port 9999 --no-open >/dev/null 2>"$T/err"
  assert_eq "$?" 0 "$(cat "$T/err")"
  assert_contains "$(cat "$rec")" "$ROOT/arcade/server.py"
  assert_contains "$(cat "$rec")" "--port"
  assert_contains "$(cat "$rec")" "9999"
  assert_contains "$(cat "$rec")" "--no-open"
}

# ---- run: guards -----------------------------------------------------------

test_rejects_unknown_cli() {
  brief "FAKE: print hi"
  run_job nope
  assert_eq "$RC" 2
  assert_contains "$ERR" "unknown cli"
}

test_refuses_outside_git() {
  brief "FAKE: print hi"
  (cd "$T" && "$FARMOUT" run fake --brief "$T/brief.md") >/dev/null 2>"$T/err"
  assert_eq "$?" 2
  assert_contains "$(cat "$T/err")" "not inside a git repository"
  [ -d "$FARMOUT_HOME/jobs" ] && fail "a job dir was created"
  return 0
}

test_rejects_absolute_repo_path_in_brief() {
  brief "FAKE: cat $REPO/a.txt"
  run_job fake
  assert_eq "$RC" 2
  assert_contains "$ERR" "source checkout's absolute path"
  [ -d "$FARMOUT_HOME/jobs" ] && fail "a job dir was created"
  return 0
}

test_symlinked_entrypoint() {
  ln -s "$FARMOUT" "$T/farmout-link"
  brief "FAKE: print via-link"
  (cd "$REPO" && "$T/farmout-link" run fake --brief "$T/brief.md") >"$T/out" 2>&1
  assert_eq "$?" 0 "$(cat "$T/out")"
}

# ---- run: read jobs --------------------------------------------------------

test_read_job_ok() {
  brief "FAKE: print hello"
  run_job fake
  assert_eq "$RC" 0 "$ERR"
  assert_eq "$(tail -1 "$T/out")" "$ID ok"
  assert_eq "$(result "$ID")" hello
  assert_eq "$(meta "$ID" status)" ok
  assert_eq "$(meta "$ID" exit_code)" 0
  assert_eq "$(meta "$ID" mode)" read
  assert_eq "$(meta "$ID" branch)" null
  assert_eq "$(meta "$ID" pid)" "$(meta "$ID" pgid)"
  [ "$(meta "$ID" ended)" != null ] || fail "ended not set"
  [ -d "$(meta "$ID" worktree)" ] && fail "read worktree not removed"
  assert_eq "$(git -C "$REPO" worktree list | wc -l | tr -d ' ')" 1
}

test_snapshot_includes_uncommitted() {
  printf 'ONE\ntwo\nthree\n' > "$REPO/a.txt"
  brief "FAKE: cat a.txt"
  run_job fake
  assert_eq "$(head -1 "$FARMOUT_HOME/jobs/$ID/result.md")" ONE
  assert_eq "$(meta "$ID" untracked)" 0
}

test_untracked_warning() {
  echo x > "$REPO/new.txt"
  brief "FAKE: print hi"
  run_job fake
  # Counted in meta, but no warning: the brief does not name the file.
  case "$ERR" in *untracked*) fail "warned about an untracked file the brief does not name" ;; esac
  assert_eq "$(meta "$ID" untracked)" 1
  # A harness may merge stderr into stdout: the job id must still be the
  # first line, so a caller can always take line 1 as the id.
  local merged first second
  merged="$(cd "$REPO" && "$FARMOUT" run fake --brief "$T/brief.md" 2>&1)"
  first="$(printf '%s\n' "$merged" | sed -n 1p)"
  second="$(printf '%s\n' "$merged" | sed -n 2p)"
  case "$first" in farmout:*) fail "job id line came after stderr output: $first" ;; esac
  assert_contains "$second" "repo="
}

test_stash_create_failure_warns_and_falls_back() {
  local trunk; trunk="$(git -C "$REPO" symbolic-ref --short HEAD)"
  git -C "$REPO" checkout -qb other
  printf 'one\ntwo\nOTHER\n' > "$REPO/a.txt"
  git -C "$REPO" -c user.name=t -c user.email=t@t commit -qam other
  git -C "$REPO" checkout -q "$trunk"
  printf 'one\ntwo\nMASTER\n' > "$REPO/a.txt"
  git -C "$REPO" -c user.name=t -c user.email=t@t commit -qam master
  git -C "$REPO" merge other -q >/dev/null 2>&1
  local head; head="$(git -C "$REPO" rev-parse HEAD)"
  brief "FAKE: print hi"
  run_job fake
  assert_contains "$ERR" "could not snapshot uncommitted changes"
  assert_eq "$(meta "$ID" base)" "$head"
}

test_read_job_leaves_checkout_untouched() {
  local before; before="$(cat "$REPO/a.txt"; git -C "$REPO" status --porcelain)"
  brief "FAKE: write a.txt hacked" "FAKE: print done"
  run_job fake
  assert_eq "$(cat "$REPO/a.txt"; git -C "$REPO" status --porcelain)" "$before"
  assert_eq "$(meta "$ID" read_job_modified)" true
}

test_brief_verbatim() {
  printf '%s\n' 'FAKE: echo-brief' "say \"hi\" \$HOME \`uname\` it's" '' 'last line' '' '' > "$T/brief.md"
  run_job fake
  # The worker gets farmout's ground rules first, then the brief byte for byte.
  local r="$FARMOUT_HOME/jobs/$ID/brief.md"
  head -1 "$r" | grep -q '^# Ground rules (added by farmout)' || fail "ground rules missing"
  cmp -s "$T/brief.md" <(tail -c "$(wc -c < "$T/brief.md")" "$r") || fail "brief not passed verbatim"
  cmp -s <(cat "$r"; printf '\n') "$FARMOUT_HOME/jobs/$ID/result.md" \
    || fail 'actual worker brief argument lost trailing blank lines'
}

test_launch_prints_repo_and_quiet_untracked() {
  echo scratch > "$REPO/unrelated.txt"; echo notes > "$REPO/notes.md"
  brief "FAKE: print hi" "Read notes.md for context."
  run_job fake
  assert_contains "$ERR" "repo=$(basename "$REPO")"
  assert_contains "$ERR" "mode=read"
  assert_contains "$ERR" "names untracked 'notes.md'"
  case "$ERR" in *unrelated.txt*) fail "warned about an untracked file the brief does not name" ;; esac
  run_job fake --with notes.md
  case "$ERR" in *"names untracked"*) fail "warned about a file passed with --with" ;; esac
  rm -f "$REPO/unrelated.txt" "$REPO/notes.md"
}

test_cargo_target_dir() {
  echo '[package]' > "$REPO/Cargo.toml"
  git -C "$REPO" add Cargo.toml && git -C "$REPO" -c user.name=t -c user.email=t@t commit -qm cargo
  brief "FAKE: env CARGO_TARGET_DIR"
  (unset CARGO_TARGET_DIR; run_job fake; assert_eq "$(result "$ID")" "$REPO/target") || exit 1
  mkdir -p "$REPO/.cargo" && printf '[build]\ntarget-dir = "/elsewhere"\n' > "$REPO/.cargo/config.toml"
  (unset CARGO_TARGET_DIR; run_job fake; assert_eq "$(result "$ID")" "") || exit 1
  (export CARGO_TARGET_DIR=/mine; run_job fake; assert_eq "$(result "$ID")" /mine) || exit 1
}

test_concurrent_read_jobs() {
  brief "FAKE: sleep 1" "FAKE: print a"
  local k
  for k in 1 2 3 4; do
    (cd "$REPO" && "$FARMOUT" run fake --brief "$T/brief.md") >"$T/o$k" 2>"$T/e$k" &
  done
  wait
  for k in 1 2 3 4; do
    assert_contains "$(tail -1 "$T/o$k")" " ok" "$(cat "$T/e$k")"
  done
  assert_eq "$(for k in 1 2 3 4; do head -1 "$T/o$k"; done | sort -u | wc -l | tr -d ' ')" 4 "job ids collided"
}

# ---- statuses --------------------------------------------------------------

test_status_failed() {
  brief "FAKE: stderr boom" "FAKE: exit 1"
  run_job fake
  [ "$RC" -ne 0 ] || fail "run exited 0 for a failed job"
  assert_eq "$(meta "$ID" status)" failed
  assert_eq "$(meta "$ID" exit_code)" 1
  assert_eq "$(meta "$ID" error)" "worker exited with code 1"
}

test_snapshot_commit_is_authored_by_farmout() {
  echo dirty >> "$REPO/a.txt"
  brief "FAKE: print hi"
  run_job fake
  local base; base="$(meta "$ID" base)"
  assert_eq "$(git -C "$REPO" log -1 --format=%ae "$base")" "farmout@localhost"
  assert_eq "$(git -C "$REPO" rev-parse "$base^")" "$(git -C "$REPO" rev-parse HEAD)"
  assert_eq "$(git -C "$REPO" rev-list --count --no-walk=unsorted "$base^@")" 1
  assert_contains "$(git -C "$REPO" show "$base:a.txt")" dirty
  git -C "$REPO" checkout -q -- a.txt
}

test_with_puts_untracked_file_in_snapshot() {
  echo "prev journal" > "$REPO/notes.md"
  brief "FAKE: cat notes.md" "FAKE: write out.md done"
  run_job fake --write --with notes.md
  assert_eq "$RC" 0 "$ERR"
  assert_contains "$(result "$ID")" "prev journal"
  local base; base="$(meta "$ID" base)"
  assert_eq "$(git -C "$REPO" log -1 --format=%ae "$base")" "farmout@localhost"
  grep -q 'notes.md' "$FARMOUT_HOME/jobs/$ID/diff.patch" && fail "--with file leaked into the patch"
  grep -q 'out.md' "$FARMOUT_HOME/jobs/$ID/diff.patch" || fail "worker output missing from the patch"
  (cd "$REPO" && "$FARMOUT" land "$ID") >/dev/null 2>&1 || fail "land failed"
  assert_eq "$(cat "$REPO/out.md")" done
  rm -f "$REPO/notes.md" "$REPO/out.md"
}

test_with_rejects_paths_outside_repo() {
  brief "FAKE: print hi"
  run_job fake --with ../escape.md
  assert_eq "$RC" 2
  assert_contains "$ERR" "relative to the repo root"
  run_job fake --with missing.md
  assert_eq "$RC" 2
  assert_contains "$ERR" "not a file"
  [ -d "$FARMOUT_HOME/jobs" ] && fail "a job dir was created"
  return 0
}

test_clean_tree_snapshot_is_head() {
  brief "FAKE: print hi"
  run_job fake
  assert_eq "$(meta "$ID" base)" "$(git -C "$REPO" rev-parse HEAD)"
}

test_progress_verb_uses_the_shared_parser() {
  brief "FAKE: print hi"
  run_job fake
  # fake has no progress parser: honest unknown, never a guessed count.
  assert_contains "$("$FARMOUT" progress "$ID")" '"unknown"'
  cp "$(dirname "$FARMOUT")/../arcade/tests/fixtures/kiro.log" "$FARMOUT_HOME/jobs/$ID/log"
  jq '.cli = "kiro"' "$FARMOUT_HOME/jobs/$ID/meta.json" > "$T/m" && mv "$T/m" "$FARMOUT_HOME/jobs/$ID/meta.json"
  assert_eq "$("$FARMOUT" progress "$ID")" '{"events": 4, "open_tools": 0}'
}

test_scores_count_runs_and_verified_claims() {
  brief "FAKE: print one"; run_job fake; local ok="$ID"
  brief "FAKE: exit 1"; run_job fake
  (cd "$REPO" && "$FARMOUT" rate "$ok" 3/4 "two cites checked") >/dev/null || fail "rate failed"
  (cd "$REPO" && "$FARMOUT" rate "$ok" 4/4) >/dev/null || fail "re-rate failed"
  assert_eq "$(meta "$ok" review.verified)" 4
  (cd "$REPO" && "$FARMOUT" clean --all) >/dev/null 2>&1
  local line; line="$("$FARMOUT" scores | grep '^fake')"
  assert_eq "$(echo "$line" | awk '{print $3, $4, $5}')" "2 1 4/4"
}

test_rate_rejects_bad_ratios() {
  brief "FAKE: print one"; run_job fake
  local r
  for r in 5/4 x/2 3 0/0; do
    "$FARMOUT" rate "$ID" "$r" >/dev/null 2>&1 && fail "rate accepted '$r'"
  done
  assert_eq "$(meta "$ID" review)" null
}

test_result_diffstat_ignores_caller_subdirectory() {
  mkdir -p "$REPO/sub"
  brief "FAKE: sed s/three/THREE/ a.txt"
  run_job fake --write
  local out; out="$(cd "$REPO/sub" && "$FARMOUT" result "$ID")"
  assert_contains "$out" "1 file changed"
  rmdir "$REPO/sub"
}

test_status_table_shows_land_state() {
  brief "FAKE: sed s/three/THREE/ a.txt" "FAKE: print changed"; run_job fake --write; local landed="$ID"
  (cd "$REPO" && "$FARMOUT" land "$landed") >/dev/null 2>&1 || fail "land failed"
  brief "FAKE: print hi"; run_job fake; local readonly_job="$ID"
  local table; table="$("$FARMOUT" status)"
  assert_contains "$(echo "$table" | head -1)" "LAND"
  assert_eq "$(echo "$table" | awk -v j="$landed" '$1 == j {print $5}')" landed
  assert_eq "$(echo "$table" | awk -v j="$readonly_job" '$1 == j {print $5}')" -
  git -C "$REPO" checkout -q -- a.txt
}

test_codex_search_only_for_research_jobs() {
  . "$ROOT/lib/common.sh"; . "$ROOT/lib/adapters.sh"
  local j="$T/job"; mkdir -p "$j"; printf 'BRIEF\n' > "$j/brief.md"
  printf '{"kind":"research"}\n' > "$j/meta.json"
  assert_eq "$(adapter_argv codex "$j" /wt "" "" 2>/dev/null | sed -n 2,3p | tr '\n' ' ')" "--search exec "
  printf '{"kind":"review"}\n' > "$j/meta.json"
  case "$(adapter_argv codex "$j" /wt "" "" 2>/dev/null)" in *--search*) fail "--search on a review job" ;; esac
  return 0
}

test_clean_mine_keeps_other_sessions_jobs() {
  brief "FAKE: print a"; CLAUDE_CODE_SESSION_ID=me run_job fake; local mine="$ID"
  brief "FAKE: print b"; CLAUDE_CODE_SESSION_ID=other run_job fake; local theirs="$ID"
  "$FARMOUT" result "$mine" >/dev/null; "$FARMOUT" result "$theirs" >/dev/null
  (cd "$REPO" && CLAUDE_CODE_SESSION_ID=me "$FARMOUT" clean --mine) >/dev/null
  [ -d "$FARMOUT_HOME/jobs/$mine" ] && fail "own job not cleaned"
  [ -d "$FARMOUT_HOME/jobs/$theirs" ] || fail "another session's job was cleaned"
  (cd "$REPO" && env -u CLAUDE_CODE_SESSION_ID "$FARMOUT" clean --mine) >/dev/null 2>&1 && fail "--mine without a session id ran"
  [ -d "$FARMOUT_HOME/jobs/$theirs" ] || fail "--mine without a session id cleaned jobs"
  return 0
}

test_out_returns_report_files_from_a_read_job() {
  brief "FAKE: write report.md findings here" "FAKE: print done"
  run_job fake --out report.md --out 'notes/*.md'
  assert_eq "$(meta "$ID" status)" ok "$ERR"
  assert_eq "$(meta "$ID" mode)" read
  assert_eq "$(cat "$FARMOUT_HOME/jobs/$ID/out/report.md")" "findings here"
  assert_eq "$(jq -c .out "$FARMOUT_HOME/jobs/$ID/meta.json")" '["report.md"]'
  assert_eq "$(jq -c .out_missing "$FARMOUT_HOME/jobs/$ID/meta.json")" '["notes/*.md"]'
  assert_contains "$ERR" "matched nothing"
  assert_eq "$(meta "$ID" read_job_modified)" false
  [ -s "$FARMOUT_HOME/jobs/$ID/diff.patch" ] && fail "a read job produced a patch"
  assert_contains "$("$FARMOUT" result "$ID")" "report.md"
}

test_out_skips_symlinks_and_oversized_files() {
  echo secret > "$T/outside.txt"; mkdir -p "$T/outdir"; echo secret > "$T/outdir/r.md"
  brief "FAKE: ln $T/outside.txt link.md" "FAKE: ln $T/outdir linked" \
        "FAKE: write big.md 0123456789012345678901234567890" "FAKE: write ok.md fine" "FAKE: print done"
  FARMOUT_OUT_MAX_BYTES=20 run_job fake --out link.md --out 'linked/*.md' --out big.md --out ok.md
  assert_eq "$(meta "$ID" status)" ok "$ERR"
  assert_eq "$(jq -c .out "$FARMOUT_HOME/jobs/$ID/meta.json")" '["ok.md"]'
  assert_contains "$ERR" "skipped 'link.md'"
  assert_contains "$ERR" "skipped 'linked/r.md'"
  assert_contains "$ERR" "skipped 'big.md' (over 20 bytes)"
  [ -e "$FARMOUT_HOME/jobs/$ID/out/link.md" ] && fail "a symlinked file was copied out"
  return 0
}

test_out_refuses_write_jobs_and_escaping_paths() {
  brief "FAKE: print hi"
  run_job fake --write --out report.md
  assert_eq "$RC" 2; assert_contains "$ERR" "read jobs"
  run_job fake --out ../escape.md
  assert_eq "$RC" 2; assert_contains "$ERR" "inside it"
  run_job fake --out /tmp/x.md
  assert_eq "$RC" 2
  [ -d "$FARMOUT_HOME/jobs" ] && fail "a job dir was created"
  return 0
}

test_ref_snapshots_that_commit_not_the_dirty_tree() {
  local first; first="$(git -C "$REPO" rev-parse HEAD)"
  echo four >> "$REPO/a.txt"
  brief "FAKE: cat a.txt"
  run_job fake --ref "$first"
  assert_eq "$RC" 0 "$ERR"
  assert_eq "$(meta "$ID" base)" "$first"
  assert_eq "$(meta "$ID" base_ref)" "$first"
  case "$(result "$ID")" in *four*) fail "--ref job saw the dirty edit" ;; esac
  run_job fake --ref no-such-ref
  assert_eq "$RC" 2
  git -C "$REPO" checkout -q -- a.txt
}

test_repo_flag_works_from_an_unrelated_cwd() {
  brief "FAKE: cat a.txt"
  (cd "$T" && "$FARMOUT" run fake --repo "$REPO" --brief "$T/brief.md") > "$T/out" 2>&1 || fail "run --repo failed: $(cat "$T/out")"
  local id; id="$(head -1 "$T/out")"
  assert_eq "$(meta "$id" repo)" "$(cd "$REPO" && pwd -P)"
  assert_contains "$(result "$id")" three
}

test_no_repo_job_runs_in_a_scratch_repo_and_cleans_it() {
  brief "FAKE: write report.md web findings" "FAKE: print done"
  (cd "$T" && "$FARMOUT" run fake --no-repo --out report.md --brief "$T/brief.md") > "$T/out" 2>"$T/err" || fail "no-repo run failed: $(cat "$T/err")"
  local id; id="$(head -1 "$T/out")"
  assert_eq "$(meta "$id" status)" ok
  assert_eq "$(cat "$FARMOUT_HOME/jobs/$id/out/report.md")" "web findings"
  [ -d "$(meta "$id" scratch)" ] && fail "scratch repo left behind"
  (cd "$T" && "$FARMOUT" run fake --no-repo --write --brief "$T/brief.md") >/dev/null 2>&1 && fail "--no-repo --write was accepted"
  (cd "$T" && "$FARMOUT" run fake --brief "$T/brief.md") >/dev/null 2>&1 && fail "a non-research job ran outside a repo"
  return 0
}

test_queue_admits_waiters_in_enqueue_order() {
  printf '%s\n' '{"limits":{"max_jobs":1}}' > "$T/config.json"; export FARMOUT_CONFIG="$T/config.json"
  export FARMOUT_QUEUE_POLL_S=0.2
  brief "FAKE: sleep 6" "FAKE: print holder"
  (cd "$REPO" && "$FARMOUT" run fake --brief "$T/brief.md") > "$T/o1" 2>&1 &
  local holder=$!; sleep 0.5
  printf '%s\n' "FAKE: sleep 1" "FAKE: print second" > "$T/b2.md"
  printf '%s\n' "FAKE: print third" > "$T/b3.md"
  # Without --queue a full cap is still exit 4.
  (cd "$REPO" && "$FARMOUT" run fake --brief "$T/b3.md") >/dev/null 2>&1; assert_eq "$?" 4
  (cd "$REPO" && "$FARMOUT" run fake --queue --brief "$T/b2.md") > "$T/o2" 2>"$T/e2" &
  local w2=$!; sleep 0.5
  (cd "$REPO" && "$FARMOUT" run fake --queue --brief "$T/b3.md") > "$T/o3" 2>/dev/null &
  local w3=$!
  local i=0; until "$FARMOUT" status | grep -q queued || [ $i -ge 50 ]; do sleep 0.1; i=$((i + 1)); done
  assert_contains "$("$FARMOUT" status)" queued
  # The per-job lookup agrees with the list: a waiter is "queued", not "no such job".
  local qid; qid="$("$FARMOUT" status | awk '$2 == "queued" {print $1; exit}')"
  assert_eq "$("$FARMOUT" status "$qid" | jq -r .status)" queued
  assert_eq "$("$FARMOUT" status "$qid" | jq -r .cli)" fake
  wait $holder $w2 $w3
  assert_contains "$(cat "$T/e2")" "queued"
  local id2 id3; id2="$(head -1 "$T/o2")"; id3="$(head -1 "$T/o3")"
  assert_eq "$(meta "$id2" status)" ok; assert_eq "$(meta "$id3" status)" ok
  # Queue time is not run time: started moves to admission.
  assert_eq "$(meta "$id3" started)" "$(meta "$id3" admitted_at)"
  [ "$(meta "$id3" queued_at)" \< "$(meta "$id3" started)" ] || fail "queued_at not before started"
  # The second job sleeps 1s holding the only slot, so strict order is observable.
  [ "$(meta "$id2" admitted_at)" \< "$(meta "$id3" admitted_at)" ] \
    || fail "third admitted before second: $(meta "$id2" admitted_at) vs $(meta "$id3" admitted_at)"
  unset FARMOUT_CONFIG FARMOUT_QUEUE_POLL_S
}

test_queued_job_runs_the_brief_it_was_launched_with() {
  printf '%s\n' '{"limits":{"max_jobs":1}}' > "$T/config.json"; export FARMOUT_CONFIG="$T/config.json"
  export FARMOUT_QUEUE_POLL_S=0.2
  brief "FAKE: sleep 2" "FAKE: print holder"
  (cd "$REPO" && "$FARMOUT" run fake --brief "$T/brief.md") >/dev/null 2>&1 &
  local holder=$!; sleep 0.5
  brief "FAKE: print original"
  (cd "$REPO" && "$FARMOUT" run fake --queue --brief "$T/brief.md") > "$T/oq" 2>/dev/null &
  local waiter=$!; sleep 0.5
  brief "FAKE: print replaced"   # the caller reuses the file while the job waits
  wait $holder $waiter
  assert_contains "$(result "$(head -1 "$T/oq")")" original
  unset FARMOUT_CONFIG FARMOUT_QUEUE_POLL_S
}

test_dead_waiter_does_not_block_the_queue() {
  printf '%s\n' '{"limits":{"max_jobs":1}}' > "$T/config.json"; export FARMOUT_CONFIG="$T/config.json"
  mkdir -p "$FARMOUT_HOME/queue"
  # A ticket older than anyone else's, whose process is gone.
  echo 999999 > "$FARMOUT_HOME/queue/0000000000.000001-20260101-000000-fake-dead"
  brief "FAKE: print hi"
  run_job fake --queue
  assert_eq "$RC" 0 "$ERR"
  [ -e "$FARMOUT_HOME/queue/0000000000.000001-20260101-000000-fake-dead" ] && fail "dead ticket not pruned"
  unset FARMOUT_CONFIG
  return 0
}

test_result_recovers_partial_output_from_log() {
  brief "FAKE: print hi"; run_job fake
  local j="$FARMOUT_HOME/jobs/$ID"
  cp "$ROOT/arcade/tests/fixtures/codex.log" "$j/log"; : > "$j/result.md"
  jq '.cli = "codex" | .status = "killed"' "$j/meta.json" > "$T/m" && mv "$T/m" "$j/meta.json"
  local out; out="$("$FARMOUT" result "$ID")"
  assert_contains "$out" "partial result"
  assert_contains "$out" "$(jq -r 'select(.type=="item.completed" and .item.type=="agent_message") | .item.text' "$ROOT/arcade/tests/fixtures/codex.log" | tail -1)"
}

test_clean_keeps_unread_results() {
  brief "FAKE: print findings"; run_job fake; local unread="$ID"
  brief "FAKE: print other"; run_job fake; local read_one="$ID"
  "$FARMOUT" result "$read_one" >/dev/null
  local out; out="$(cd "$REPO" && "$FARMOUT" clean)"
  assert_contains "$out" "result never read"
  [ -d "$FARMOUT_HOME/jobs/$unread" ] || fail "unread job was cleaned"
  [ -d "$FARMOUT_HOME/jobs/$read_one" ] && fail "read job was kept"
  (cd "$REPO" && "$FARMOUT" clean --all) >/dev/null
  [ -d "$FARMOUT_HOME/jobs/$unread" ] && fail "--all kept an unread job"
  return 0
}

test_discard_shows_what_the_job_said() {
  brief "FAKE: sed s/three/THREE/ a.txt" "FAKE: print the verdict line"; run_job fake --write
  local out; out="$(cd "$REPO" && "$FARMOUT" discard "$ID")"
  assert_contains "$out" "the verdict line"
  [ -f "$FARMOUT_HOME/jobs/$ID/result.md" ] || fail "discard removed the result"
}

test_land_warns_when_ref_is_not_in_the_checkout() {
  local first; first="$(git -C "$REPO" rev-parse HEAD)"
  git -C "$REPO" checkout -q -b elsewhere "$first"
  echo side > "$REPO/side.txt"; git -C "$REPO" add side.txt
  git -C "$REPO" -c user.name=t -c user.email=t@t commit -qm side
  local tip; tip="$(git -C "$REPO" rev-parse HEAD)"
  git -C "$REPO" checkout -q "$first"
  brief "FAKE: sed s/three/THREE/ a.txt" "FAKE: print ok"
  run_job fake --write --ref "$tip"
  local err; err="$(cd "$REPO" && "$FARMOUT" land "$ID" 2>&1 >/dev/null)"
  assert_contains "$err" "does not contain"
  git -C "$REPO" checkout -q -- a.txt
}

test_explicit_logged_out_cli_refused() {
  export FARMOUT_DOCTOR_STUB="kiro=logged-out"
  brief "FAKE: print hi"
  run_job kiro
  unset FARMOUT_DOCTOR_STUB
  assert_eq "$RC" 2
  assert_contains "$ERR" "kiro"
  assert_contains "$ERR" "logged-out"
  [ -d "$FARMOUT_HOME/jobs" ] && fail "a job dir was created"
  return 0
}

test_explicit_not_verified_cli_runs() {
  export FARMOUT_DOCTOR_STUB="fake=not-verified"
  brief "FAKE: print hi"
  run_job fake
  unset FARMOUT_DOCTOR_STUB
  assert_eq "$RC" 0 "$ERR"
}

test_failed_after_losing_login_names_auth() {
  printf 'ok\n' > "$T/doctor"
  export FARMOUT_DOCTOR_STUB="fake=@$T/doctor"
  brief "FAKE: write $T/doctor logged-out" "FAKE: exit 1"
  run_job fake
  unset FARMOUT_DOCTOR_STUB
  assert_eq "$(meta "$ID" status)" failed "$ERR"
  assert_eq "$(meta "$ID" error)" "worker authentication failed: cli 'fake' is logged-out"
}

test_worker_error_keeps_patch_capture_error() {
  brief "FAKE: sed s/three/THREE/ a.txt" "FAKE: lock-index" "FAKE: exit 1"
  run_job fake --write
  assert_eq "$(meta "$ID" status)" failed "$ERR"
  assert_eq "$(meta "$ID" error)" "patch capture failed"
}

test_status_rate_limited() {
  brief "FAKE: stderr Error: 429 Too Many Requests" "FAKE: exit 1"
  run_job fake
  assert_eq "$(meta "$ID" status)" rate-limited
}

test_rate_limit_needs_failure() {
  brief "FAKE: print we discussed the rate limit"
  run_job fake
  assert_eq "$(meta "$ID" status)" ok
}

test_status_empty() {
  brief "FAKE: exit 0"
  run_job fake
  [ "$RC" -ne 0 ] || fail "run exited 0 for an empty job"
  assert_eq "$(meta "$ID" status)" empty
}

test_timeout_kills_group() {
  brief "FAKE: spawn" "FAKE: hang"
  local t0; t0=$(date +%s)
  run_job fake --timeout 2s
  assert_eq "$(meta "$ID" status)" timeout
  [ -z "$(pgrep -g "$(meta "$ID" pgid)")" ] || fail "process group survived"
  [ $(( $(date +%s) - t0 )) -lt 20 ] || fail "timeout took too long"
}

test_kill_command() {
  brief "FAKE: spawn" "FAKE: hang"
  (cd "$REPO" && "$FARMOUT" run fake --timeout 60s --brief "$T/brief.md") >"$T/out" 2>/dev/null &
  local runner=$!
  local i=0; while [ ! -s "$T/out" ] && [ $i -lt 50 ]; do sleep 0.2; i=$((i + 1)); done
  ID="$(head -1 "$T/out")"; wait_for_meta_pid "$ID"
  "$FARMOUT" kill "$ID" >/dev/null || fail "kill command failed"
  wait "$runner"
  assert_eq "$(meta "$ID" status)" killed
  [ -z "$(pgrep -g "$(meta "$ID" pgid)")" ] || fail "process group survived"
}

# M5: a survivor after kill_group must warn and exit 1. SIGKILL cannot be
# blocked by any process we can spawn in this suite, so there is no way to
# make a real process survive it deterministically; check the code path
# structurally instead (same rationale as test_commit_never_runs_outside_worktree).
test_kill_warns_on_survivors() {
  grep -Fq 'warn "processes in group $pgid survived kill"' "$ROOT/bin/farmout" \
    || fail "cmd_kill does not warn+exit on a group_members check after kill_group"
}

test_leftover_children_reaped() {
  brief "FAKE: spawn" "FAKE: print done"
  run_job fake
  assert_eq "$(meta "$ID" status)" ok
  assert_eq "$(meta "$ID" survivors)" 1
  [ -z "$(pgrep -g "$(meta "$ID" pgid)")" ] || fail "leftover child not reaped"
}

test_meta_valid_while_running() {
  brief "FAKE: sleep 2" "FAKE: print x"
  (cd "$REPO" && "$FARMOUT" run fake --brief "$T/brief.md") >"$T/out" 2>/dev/null &
  local runner=$! i=0
  while [ ! -s "$T/out" ] && [ $i -lt 50 ]; do sleep 0.2; i=$((i + 1)); done
  ID="$(head -1 "$T/out")"; wait_for_meta_pid "$ID"
  jq -e . "$FARMOUT_HOME/jobs/$ID/meta.json" >/dev/null || fail "meta.json invalid while running"
  assert_eq "$(meta "$ID" status)" running
  assert_eq "$("$FARMOUT" status "$ID" | jq -r .status)" running
  wait "$runner"
  jq -e . "$FARMOUT_HOME/jobs/$ID/meta.json" >/dev/null || fail "meta.json invalid after run"
}

test_lost_status() {
  brief "FAKE: print x"
  run_job fake
  sleep 0 & local dead=$!; wait "$dead"
  local m="$FARMOUT_HOME/jobs/$ID/meta.json"
  jq --argjson p "$dead" '.status = "running" | .sup_pid = $p' "$m" > "$m.tmp" && mv "$m.tmp" "$m"
  assert_eq "$("$FARMOUT" status "$ID" | jq -r .status)" lost
  assert_contains "$("$FARMOUT" status)" lost
}

# ---- write jobs ------------------------------------------------------------

test_write_job_land() {
  local head; head="$(git -C "$REPO" rev-parse HEAD)"
  brief "FAKE: sed s/three/THREE/ a.txt" "FAKE: print changed"
  run_job fake --write
  assert_eq "$(meta "$ID" status)" ok "$ERR"
  [ -s "$FARMOUT_HOME/jobs/$ID/diff.patch" ] || fail "empty diff.patch"
  git -C "$REPO" show-ref --quiet "refs/heads/$(meta "$ID" branch)" || fail "job branch missing"
  (cd "$REPO" && "$FARMOUT" land "$ID") >/dev/null 2>&1 || fail "land failed"
  assert_eq "$(cat "$REPO/a.txt")" "$(printf 'one\ntwo\nTHREE')"
  git -C "$REPO" diff --cached --quiet || fail "land staged changes"
  assert_eq "$(git -C "$REPO" rev-parse HEAD)" "$head" "HEAD moved"
  git -C "$REPO" show-ref --quiet "refs/heads/$(meta "$ID" branch)" && fail "branch not removed"
  [ -d "$(meta "$ID" worktree)" ] && fail "worktree not removed"
  assert_eq "$(meta "$ID" land)" landed
}

# A1: the patch must not depend on the user's or repo's diff.* config. Set
# noprefix/color config on the `farmout run` process only; `land` (with none
# of that env) must still apply the patch cleanly, and the patch itself must
# carry no ANSI color escape.
test_patch_ignores_diff_config() {
  brief "FAKE: sed s/three/THREE/ a.txt" "FAKE: print changed"
  ( cd "$REPO" && GIT_CONFIG_COUNT=3 \
      GIT_CONFIG_KEY_0=diff.noprefix GIT_CONFIG_VALUE_0=true \
      GIT_CONFIG_KEY_1=color.diff GIT_CONFIG_VALUE_1=always \
      GIT_CONFIG_KEY_2=color.ui GIT_CONFIG_VALUE_2=always \
      "$FARMOUT" run fake --write --brief "$T/brief.md" ) >"$T/out" 2>"$T/err"
  RC=$?; ID="$(head -1 "$T/out")"
  assert_eq "$(meta "$ID" status)" ok "$(cat "$T/err")"
  [ "$(grep -c $'\033' "$FARMOUT_HOME/jobs/$ID/diff.patch")" = 0 ] \
    || fail "diff.patch contains an ESC byte"
  (cd "$REPO" && "$FARMOUT" land "$ID") >/dev/null 2>&1
  assert_eq "$?" 0 "land failed"
  assert_eq "$(cat "$REPO/a.txt")" "$(printf 'one\ntwo\nTHREE')"
}

test_land_ignores_apply_whitespace_config() {
  brief "FAKE: trailing-ws a.txt" "FAKE: print changed"
  run_job fake --write
  assert_eq "$(meta "$ID" status)" ok "$ERR"
  git -C "$REPO" config apply.whitespace error
  (cd "$REPO" && "$FARMOUT" land "$ID") >"$T/lout" 2>"$T/lerr"
  assert_eq "$?" 0 "$(cat "$T/lerr")"
  grep -q '^four ' "$REPO/a.txt" || fail "trailing-ws line missing after land"
}

test_land_keeps_user_uncommitted_edits() {
  printf 'ONE\ntwo\nthree\n' > "$REPO/a.txt"
  brief "FAKE: sed s/three/THREE/ a.txt" "FAKE: print changed"
  run_job fake --write
  "$FARMOUT" land "$ID" >/dev/null 2>&1 || fail "land failed"
  assert_eq "$(cat "$REPO/a.txt")" "$(printf 'ONE\ntwo\nTHREE')"
}

test_land_conflict_leaves_checkout_untouched() {
  brief "FAKE: sed s/three/THREE/ a.txt" "FAKE: print changed"
  run_job fake --write
  printf 'one\ntwo\nthree-user\n' > "$REPO/a.txt"
  "$FARMOUT" land "$ID" >/dev/null 2>&1
  assert_eq "$?" 3
  assert_eq "$(cat "$REPO/a.txt")" "$(printf 'one\ntwo\nthree-user')"
  assert_eq "$(meta "$ID" land)" conflict
  [ -d "$(meta "$ID" worktree)" ] || fail "worktree removed on conflict"
}

test_land_twice_refused() {
  brief "FAKE: sed s/three/THREE/ a.txt" "FAKE: print changed"
  run_job fake --write
  "$FARMOUT" land "$ID" >/dev/null 2>&1 || fail "first land failed"
  "$FARMOUT" land "$ID" >/dev/null 2>&1
  assert_eq "$?" 2
}

test_kill_finished_refused() {
  brief "FAKE: print x"
  run_job fake
  "$FARMOUT" kill "$ID" >/dev/null 2>&1
  assert_eq "$?" 2
}

test_land_rejects_read_job() {
  brief "FAKE: print x"
  run_job fake
  "$FARMOUT" land "$ID" >/dev/null 2>&1
  assert_eq "$?" 2
}

test_discard() {
  brief "FAKE: sed s/three/THREE/ a.txt" "FAKE: print changed"
  run_job fake --write
  "$FARMOUT" discard "$ID" >/dev/null || fail "discard failed"
  assert_eq "$(cat "$REPO/a.txt")" "$(printf 'one\ntwo\nthree')"
  [ -d "$(meta "$ID" worktree)" ] && fail "worktree not removed"
  git -C "$REPO" show-ref --quiet "refs/heads/$(meta "$ID" branch)" && fail "branch not removed"
  assert_eq "$(meta "$ID" land)" discarded
}

test_discard_refuses_landed_job() {
  brief "FAKE: sed s/three/THREE/ a.txt" "FAKE: print changed"
  run_job fake --write
  "$FARMOUT" land "$ID" >/dev/null 2>&1 || fail "land failed"
  "$FARMOUT" discard "$ID" >"$T/out2" 2>"$T/err2"
  assert_eq "$?" 2
  assert_contains "$(cat "$T/err2")" "already landed"
}

# A2: clean must not throw away an unlanded write job's diff - only a job's
# reviewer decides to land or discard it. `--all` overrides.
test_clean() {
  brief "FAKE: print x"
  run_job fake
  local read_id="$ID"
  brief "FAKE: sed s/three/THREE/ a.txt" "FAKE: print changed"
  run_job fake --write
  local write_id="$ID"
  "$FARMOUT" result "$read_id" >/dev/null
  local out; out="$("$FARMOUT" clean)"
  assert_contains "$out" "kept $write_id (unlanded write job; use --all)"
  assert_contains "$out" "removed 1 job(s)"
  assert_eq "$(ls "$FARMOUT_HOME/jobs" | wc -l | tr -d ' ')" 1
  [ -d "$FARMOUT_HOME/jobs/$read_id" ] && fail "the ok read job was kept"
  [ -d "$FARMOUT_HOME/jobs/$write_id" ] || fail "the unlanded write job was removed by plain clean"
  git -C "$REPO" show-ref --quiet "refs/heads/$(meta "$write_id" branch)" || fail "unlanded job's branch removed early"
  assert_eq "$("$FARMOUT" clean --all)" "removed 1 job(s)"
  assert_eq "$(ls "$FARMOUT_HOME/jobs" | wc -l | tr -d ' ')" 0
  assert_eq "$(git -C "$REPO" worktree list | wc -l | tr -d ' ')" 1
  assert_eq "$(git -C "$REPO" branch --list 'farmout/*' | wc -l | tr -d ' ')" 0
}

# ---- fix round 1: supervisor lifecycle & failure handling ------------------

test_supervisor_term_kills_worker() {
  brief "FAKE: spawn" "FAKE: hang"
  ( cd "$REPO" && exec "$FARMOUT" run fake --timeout 60s --brief "$T/brief.md" ) >"$T/out" 2>/dev/null &
  local runner=$!
  local i=0; while [ ! -s "$T/out" ] && [ $i -lt 50 ]; do sleep 0.2; i=$((i + 1)); done
  ID="$(head -1 "$T/out")"; wait_for_meta_pid "$ID"
  kill -TERM "$runner"
  wait "$runner"
  assert_eq "$(meta "$ID" status)" killed
  [ -z "$(pgrep -g "$(meta "$ID" pgid)")" ] || fail "worker process group survived"
}

test_supervisor_sigkill_is_lost_and_clean_reaps() {
  brief "FAKE: spawn" "FAKE: hang"
  ( cd "$REPO" && exec "$FARMOUT" run fake --timeout 60s --brief "$T/brief.md" ) >"$T/out" 2>/dev/null &
  local runner=$!
  local i=0; while [ ! -s "$T/out" ] && [ $i -lt 50 ]; do sleep 0.2; i=$((i + 1)); done
  ID="$(head -1 "$T/out")"; wait_for_meta_pid "$ID"
  local pgid; pgid="$(meta "$ID" pgid)"
  kill -KILL "$runner"
  wait "$runner" 2>/dev/null
  assert_eq "$("$FARMOUT" status "$ID" | jq -r .status)" lost
  [ -n "$(pgrep -g "$pgid")" ] || fail "worker group already empty before clean"
  "$FARMOUT" clean >/dev/null
  [ -z "$(pgrep -g "$pgid")" ] || fail "worker process group survived clean"
  [ -d "$FARMOUT_HOME/jobs/$ID" ] && fail "job dir not removed by clean"
  return 0
}

test_clean_skips_live_run() {
  brief "FAKE: sleep 3" "FAKE: print x"
  (cd "$REPO" && "$FARMOUT" run fake --brief "$T/brief.md") >"$T/out" 2>/dev/null &
  local runner=$!
  local i=0; while [ ! -s "$T/out" ] && [ $i -lt 50 ]; do sleep 0.2; i=$((i + 1)); done
  ID="$(head -1 "$T/out")"
  "$FARMOUT" clean >/dev/null
  wait "$runner"
  assert_eq "$(meta "$ID" status)" ok
  [ -d "$FARMOUT_HOME/jobs/$ID" ] || fail "job dir removed while its supervisor was still running"
}

test_write_patch_survives_commit_failure() {
  brief "FAKE: sed s/three/THREE/ a.txt" "FAKE: print changed"
  ( cd "$REPO" && GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=true \
      GIT_CONFIG_KEY_1=gpg.program GIT_CONFIG_VALUE_1=false \
      "$FARMOUT" run fake --write --brief "$T/brief.md" ) >"$T/out" 2>"$T/err"
  RC=$?; ID="$(head -1 "$T/out")"
  [ -s "$FARMOUT_HOME/jobs/$ID/diff.patch" ] || fail "empty diff.patch after commit failure ($(cat "$T/err"))"
  (cd "$REPO" && "$FARMOUT" land "$ID") >/dev/null 2>&1 || fail "land failed"
  assert_eq "$(cat "$REPO/a.txt")" "$(printf 'one\ntwo\nTHREE')"
}

test_worktree_add_failure_marks_failed() {
  mkdir -p "$FARMOUT_HOME"; : > "$FARMOUT_HOME/worktrees"
  brief "FAKE: print hi"
  run_job fake
  assert_eq "$RC" 2
  assert_eq "$(ls "$FARMOUT_HOME/jobs" | wc -l | tr -d ' ')" 1
  assert_eq "$(meta "$ID" status)" failed
  [ "$(meta "$ID" error)" != null ] || fail "error not set"
}

test_rejects_zero_timeout() {
  brief "FAKE: print hi"
  (cd "$REPO" && "$FARMOUT" run fake --timeout 0s --brief "$T/brief.md") >/dev/null 2>"$T/err"
  assert_eq "$?" 2
  [ -d "$FARMOUT_HOME/jobs" ] && fail "a job dir was created"
  return 0
}

# ---- fix round 2: group-safety, commit-cwd, patch-capture failures --------

test_clean_leaves_finished_job_groups_alone() {
  brief "FAKE: print x"
  run_job fake
  assert_eq "$(meta "$ID" status)" ok "$ERR"
  perl -e 'setpgrp(0, 0); exec @ARGV' sleep 30 &
  local unrelated=$!
  local m="$FARMOUT_HOME/jobs/$ID/meta.json"
  jq --argjson p "$unrelated" '.pgid = $p' "$m" > "$m.tmp" && mv "$m.tmp" "$m"
  "$FARMOUT" clean >/dev/null
  # kill -0 still succeeds against a zombie; check the reported state instead
  # so a process clean SIGKILLed-then-reaped-as-zombie is not mistaken for alive.
  local st; st="$(ps -o stat= -p "$unrelated" 2>/dev/null)"
  case "$st" in ''|Z*) fail "clean killed an unrelated process group" ;; esac
  kill -KILL "$unrelated" 2>/dev/null
  wait "$unrelated" 2>/dev/null
  return 0
}

# Structural check, not a behavioural one: forcing a real `cd "$wt"` failure
# from outside cmd_run, at exactly that instant, would be racy and could
# corrupt other invariants (e.g. deleting the worktree mid-run). F2's fix is
# verified by reading the source: the commit must be grouped as
# `cd "$wt" && { ...; }` so a failed cd short-circuits the whole block instead
# of leaving `||` to run the commit unconditionally in the supervisor's cwd.
test_commit_never_runs_outside_worktree() {
  grep -Fq 'cd "$wt" && { git diff --cached --quiet ||' "$ROOT/bin/farmout" \
    || fail "commit block is not grouped behind a successful cd \"\$wt\""
}

test_patch_capture_failure_marks_failed() {
  brief "FAKE: sed s/three/THREE/ a.txt" "FAKE: lock-index"
  run_job fake --write
  assert_eq "$(meta "$ID" status)" failed "$ERR"
  assert_eq "$(meta "$ID" error)" "patch capture failed"
  (cd "$REPO" && "$FARMOUT" land "$ID") >/dev/null 2>&1
  assert_eq "$?" 2
}

# F4 precedence: a patch-capture failure must not clobber a more specific
# status (timeout here) that was already determined.
test_patch_capture_failure_keeps_timeout() {
  brief "FAKE: lock-index" "FAKE: hang"
  run_job fake --write --timeout 2s
  assert_eq "$(meta "$ID" status)" timeout "$ERR"
  assert_eq "$(meta "$ID" error)" "patch capture failed"
}

# ---- runner ----------------------------------------------------------------

pass=0; failed=""
tests="$(declare -F | awk '{print $3}' | grep '^test_' | grep -E -- "${1:-}")"
for t in $tests; do
  if ( setup; trap teardown EXIT; "$t" ); then
    pass=$((pass + 1)); echo "ok   $t"
  else
    failed="$failed $t"; echo "FAIL $t"
  fi
done
total=$(echo "$tests" | grep -c .)
echo "$pass/$total passed"
[ -z "$failed" ] || { echo "failed:$failed"; exit 1; }
