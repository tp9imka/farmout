# shellcheck shell=bash
# Shared helpers. Targets macOS /bin/bash 3.2: no associative arrays, no mapfile.

FARMOUT_HOME="${FARMOUT_HOME:-$HOME/.cache/farmout}"
case "$FARMOUT_HOME" in /*) ;; *) FARMOUT_HOME="$PWD/$FARMOUT_HOME" ;; esac
DEFAULT_TIMEOUT="${FARMOUT_TIMEOUT:-30m}"
DEFAULT_MAX_JOBS=4
ADMIT_ATTEMPTS=20
ADMIT_JITTER_MAX_S=0.3
QUEUE_POLL_S="${FARMOUT_QUEUE_POLL_S:-2}"
OUT_MAX_BYTES="${FARMOUT_OUT_MAX_BYTES:-20000000}"
KILL_GRACE_S=5
RATE_LIMIT_RE='rate[ -]?limit|usage limit|quota|too many requests|(^|[^0-9])429([^0-9]|$)'

die() { echo "farmout: $*" >&2; exit 2; }
warn() { echo "farmout: warning: $*" >&2; }
now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# 90s | 30m | 2h | 45 (seconds) -> seconds on stdout; non-zero on bad input
parse_duration() {
  [[ "$1" =~ ^([0-9]+)([smh]?)$ ]] || return 1
  local n="${BASH_REMATCH[1]}"
  case "${BASH_REMATCH[2]}" in
    m) echo $((n * 60)) ;;
    h) echo $((n * 3600)) ;;
    *) echo "$n" ;;
  esac
}

job_dir() { echo "$FARMOUT_HOME/jobs/$1"; }

# Claims a fresh job id by creating its dir; mkdir is atomic, so concurrent runs
# can never share one. ($RANDOM is not enough: bash 3.2 seeds it identically for
# processes started in the same second.)
claim_job_id() { # cli -> id on stdout
  mkdir -p "$FARMOUT_HOME/jobs"
  # Test-only seam: forces the claimed id instead of generating one, to
  # construct deterministic admission scenarios.
  if [ -n "${FARMOUT_TEST_JOB_ID:-}" ]; then
    mkdir "$(job_dir "$FARMOUT_TEST_JOB_ID")" 2>/dev/null && { echo "$FARMOUT_TEST_JOB_ID"; return 0; }
    return 1
  fi
  local id i=0
  while [ $i -lt 20 ]; do
    id="$(date +%Y%m%d-%H%M%S)-$1-$(od -An -N2 -tx1 /dev/urandom | tr -d ' \n')"
    mkdir "$(job_dir "$id")" 2>/dev/null && { echo "$id"; return 0; }
    i=$((i + 1))
  done
  return 1
}

# meta.json is the dashboard contract; every write is temp file + mv. The temp
# name carries this process's pid, so two concurrent writers never share one.
meta_write() { # dir json
  local tmp="$1/meta.json.tmp.$$"
  printf '%s\n' "$2" > "$tmp" && mv "$tmp" "$1/meta.json"
}
meta_set() { # dir [jq options...] filter
  local dir="$1"; shift
  local tmp="$dir/meta.json.tmp.$$"
  jq "$@" "$dir/meta.json" > "$tmp" && mv "$tmp" "$dir/meta.json"
}
meta_get() { # dir key - empty when the job dir is gone (a peer's admission retry)
  jq -r --arg k "$2" '.[$k] // empty' "$1/meta.json" 2>/dev/null
}

# A zombie still answers kill -0, so check the process state instead.
pid_alive() {
  local s
  s=$(ps -o stat= -p "$1" 2>/dev/null) || return 1
  case "$s" in Z*) return 1 ;; esac
  return 0
}
group_members() { pgrep -g "$1" 2>/dev/null; }

kill_group() { # pgid: TERM, grace, then KILL
  kill -TERM -- "-$1" 2>/dev/null
  local i=0
  while [ "$i" -lt "$KILL_GRACE_S" ] && [ -n "$(group_members "$1")" ]; do
    sleep 1; i=$((i + 1))
  done
  [ -n "$(group_members "$1")" ] && kill -KILL -- "-$1" 2>/dev/null
  return 0
}
