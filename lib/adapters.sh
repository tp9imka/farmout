# shellcheck shell=bash
# One entry per worker CLI. Adding a CLI = one case in each function below.
# Flags and output shapes probed 2026-09-23: codex 0.155.1, kiro-cli 2.21.2,
# copilot 1.0.88, cursor-agent 2026.09.10.

ADAPTERS="codex kiro copilot cursor fake"

adapter_known() {
  case " $ADAPTERS " in *" $1 "*) return 0 ;; esac
  return 1
}

adapter_bin() {
  case "$1" in
    codex) echo codex ;;
    kiro) echo kiro-cli ;;
    copilot) echo copilot ;;
    cursor) echo cursor-agent ;;
    fake) echo "$FARMOUT_ROOT/tests/fake-cli" ;;
  esac
}

# The effort that will actually be passed for <cli> given <model>: cursor can
# only carry effort inside a model id, so without a model it warns and drops it.
adapter_effective_effort() { # cli model effort
  local cli="$1" model="$2" effort="$3"
  [ -n "$effort" ] || { echo ""; return 0; }
  if [ "$cli" = cursor ] && [ -z "$model" ]; then
    warn "cursor effort requires a model; not passed"
    echo ""
    return 0
  fi
  echo "$effort"
}

# Builds the argv for <cli> into the global ADAPTER_ARGV array. Shared by
# adapter_argv (prints it, one element per line, for inspection/tests) and
# adapter_exec (execs the array directly) so a brief containing embedded
# newlines is never round-tripped through text. `effort` is already resolved
# (adapter_effective_effort is called once, by the caller) - this function
# must not call it again.
_adapter_build_argv() { # cli jobdir worktree model effort
  local cli="$1" job="$2" wt="$3" model="$4" effort="$5" brief
  brief="$(cat "$job/brief.md")"
  ADAPTER_ARGV=()
  case "$cli" in
    codex)
      ADAPTER_ARGV=(codex)
      # --search is a top-level flag (`codex exec --search` is rejected).
      [ "$(jq -r '.kind // empty' "$job/meta.json" 2>/dev/null)" = research ] && ADAPTER_ARGV+=(--search)
      ADAPTER_ARGV+=(exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check \
        -C "$wt" -o "$job/codex.last" --json)
      [ -n "$model" ] && ADAPTER_ARGV+=(-m "$model")
      [ -n "$effort" ] && ADAPTER_ARGV+=(-c "model_reasoning_effort=$effort")
      ADAPTER_ARGV+=("$brief") ;;
    kiro)
      ADAPTER_ARGV=(kiro-cli chat -a --agent-engine v2 --output-format stream-json)
      [ -n "$model" ] && ADAPTER_ARGV+=(--model "$model")
      [ -n "$effort" ] && ADAPTER_ARGV+=(--effort "$effort")
      ADAPTER_ARGV+=("$brief") ;;
    copilot)
      ADAPTER_ARGV=(copilot -p "$brief" --allow-all --output-format json)
      [ -n "$model" ] && ADAPTER_ARGV+=(--model "$model")
      [ -n "$effort" ] && ADAPTER_ARGV+=(--reasoning-effort "$effort") ;;
    cursor)
      ADAPTER_ARGV=(cursor-agent -p --yolo --trust --output-format stream-json --workspace "$wt")
      if [ -n "$model" ]; then
        if [ -n "$effort" ]; then ADAPTER_ARGV+=(--model "$model[effort=$effort]")
        else ADAPTER_ARGV+=(--model "$model"); fi
      fi
      ADAPTER_ARGV+=("$brief") ;;
    fake)
      ADAPTER_ARGV=("$FARMOUT_ROOT/tests/fake-cli" "$brief") ;;
  esac
}

# Prints the argv for <cli>, one element per line (inspection/tests only: a
# brief with embedded newlines will not round-trip through this text form).
adapter_argv() { # cli jobdir worktree model effort
  _adapter_build_argv "$@"
  printf '%s\n' "${ADAPTER_ARGV[@]}"
}

# Replaces the current process with the worker. cwd is already the worktree.
adapter_exec() { # cli jobdir worktree model effort
  _adapter_build_argv "$@"
  exec "${ADAPTER_ARGV[@]}"
}

# Prints every assistant message found in <job>/log, for a job that never
# wrote a final one (killed, timed out). Empty when there is nothing.
adapter_partial() { # cli jobdir
  local log="$2/log"
  [ -f "$log" ] || return 0
  case "$1" in
    codex) jq -R -r 'fromjson? | select(.type == "item.completed" and .item.type == "agent_message") | .item.text // empty' "$log" ;;
    kiro) jq -R -s -j '[split("\n")[] | split("\r")[] | fromjson? | select(.type == "sessionUpdate")
                        | .data.update | select(.sessionUpdate == "agent_message_chunk") | .content.text // empty] | join("")' "$log" ;;
    copilot) jq -R -r 'fromjson? | select(.type == "assistant.message") | .data.content // empty | select(. != "")' "$log" ;;
    cursor) jq -R -r 'fromjson? | select(.type == "assistant") | .message.content[]?.text // empty' "$log" ;;
  esac 2>/dev/null
  return 0
}

# Writes the worker's final message to <job>/result.md. Never fails the job by itself.
adapter_extract() { # cli jobdir
  local cli="$1" job="$2" out="$2/result.md" log="$2/log"
  : > "$out"
  case "$cli" in
    codex)
      [ -f "$job/codex.last" ] && cp "$job/codex.last" "$out" ;;
    kiro)
      # finalText is every assistant message of the run glued together; the
      # answer is the message after the last tool call. Records are split on
      # \r too, since a login spinner can prefix the first JSON line.
      jq -R -s -j '[split("\n")[] | split("\r")[] | fromjson? | select(.type == "sessionUpdate")
                   | .data.update] as $u
                   | ([$u | to_entries[] | select(.value.sessionUpdate | test("^tool_call")) | .key] | max // -1) as $last
                   | [$u[($last + 1):][] | select(.sessionUpdate == "agent_message_chunk") | .content.text // empty]
                   | join("")' "$log" > "$out" 2>/dev/null
      if ! grep -q '[^[:space:]]' "$out" 2>/dev/null; then
        jq -R -r 'fromjson? | select(.type == "runFinished" and .data.finalTextTruncated == false)
                  | .data.finalText // empty' "$log" > "$out" 2>/dev/null
      fi ;;
    copilot)
      jq -R -s -r '[split("\n")[] | fromjson? | select(.type == "assistant.message")
                   | .data.content // "" | select(. != "")] | last // empty' "$log" > "$out" 2>/dev/null ;;
    cursor)
      jq -R -r 'fromjson? | select(.type == "result") | .result // empty' "$log" > "$out" 2>/dev/null ;;
    fake)
      cp "$log" "$out" ;;
  esac
  return 0
}

# Prints: ok | not-verified | missing | logged-out
adapter_doctor() { # cli
  local cli="$1" bin entry
  # Test-only seam: FARMOUT_DOCTOR_STUB="cli=status,cli=status,..." skips the
  # live check below and returns the stubbed status for a listed cli.
  # "cli=@file" reads the status from file on each call, so a test can flip it.
  if [ -n "${FARMOUT_DOCTOR_STUB:-}" ]; then
    for entry in $(printf '%s' "$FARMOUT_DOCTOR_STUB" | tr ',' ' '); do
      case "$entry" in
        "$cli=@"*) cat "${entry#*=@}"; return 0 ;;
        "$cli="*) echo "${entry#*=}"; return 0 ;;
      esac
    done
  fi
  bin="$(adapter_bin "$cli")"
  command -v "$bin" >/dev/null 2>&1 || { echo missing; return; }
  case "$cli" in
    codex) codex login status </dev/null >/dev/null 2>&1 && echo ok || echo logged-out ;;
    kiro) kiro-cli whoami </dev/null >/dev/null 2>&1 && echo ok || echo logged-out ;;
    cursor) cursor-agent status </dev/null 2>&1 | grep -q 'Logged in' && echo ok || echo logged-out ;;
    *) echo not-verified ;;
  esac
}

# Usable: enabled in the config, its binary on PATH, and a live
# doctor status that is not logged-out/missing. Sets ADAPTER_UNUSABLE_REASON
# on failure (requires config.sh's cfg_worker, sourced by the time this runs).
adapter_usable() { # cli
  local cli="$1" bin st entry stubbed=false
  if [ "$(cfg_worker "$cli" enabled)" = false ]; then
    ADAPTER_UNUSABLE_REASON="disabled"; return 1
  fi
  bin="$(adapter_bin "$cli")"
  # Test-only: a cli named in FARMOUT_DOCTOR_STUB counts as present on PATH,
  # so a doctor-status test does not also need the real binary installed.
  if [ -n "${FARMOUT_DOCTOR_STUB:-}" ]; then
    for entry in $(printf '%s' "$FARMOUT_DOCTOR_STUB" | tr ',' ' '); do
      case "$entry" in "$cli="*) stubbed=true ;; esac
    done
  fi
  if ! $stubbed && ! command -v "$bin" >/dev/null 2>&1; then
    ADAPTER_UNUSABLE_REASON="not on PATH"; return 1
  fi
  st="$(adapter_doctor "$cli")"
  case "$st" in
    logged-out|missing) ADAPTER_UNUSABLE_REASON="$st"; return 1 ;;
  esac
  return 0
}
