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
  fail "job $1 never recorded a pid"
}

# ---- lib -------------------------------------------------------------------

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
  printf '%s\n' 'FAKE: echo-brief' "say \"hi\" \$HOME \`uname\` it's" '' 'last line' > "$T/brief.md"
  run_job fake
  # The worker gets farmout's ground rules first, then the brief byte for byte.
  local r="$FARMOUT_HOME/jobs/$ID/result.md"
  head -1 "$r" | grep -q '^# Ground rules (added by farmout)' || fail "ground rules missing"
  cmp -s "$T/brief.md" <(tail -c "$(wc -c < "$T/brief.md")" "$r") || fail "brief not passed verbatim"
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
  brief "FAKE: sleep 2" "FAKE: print holder"
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
tests="$(declare -F | awk '{print $3}' | grep '^test_' | grep -- "${1:-}")"
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
