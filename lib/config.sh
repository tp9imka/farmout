# shellcheck shell=bash
# farmout config file: ${FARMOUT_CONFIG:-$HOME/.config/farmout/config.json}.
# Read once via cfg_load into CFG_JSON; every accessor below queries that
# cached value, never re-reads the file. Requires common.sh (warn) and
# adapters.sh (adapter_known) sourced first.

CFG_JSON='{}'
CFG_KINDS='review bulk-read research implement second-opinion'
TIMEOUT_MIN_MAX=240
STALL_MIN_MAX=120
MAX_JOBS_MAX=32
# Used when the config has no routing key; an explicit routing array replaces it
# whole. Keep identical to DEFAULT_ROUTING in arcade/config.py (a test checks).
CFG_DEFAULT_ROUTING='[{"kind":"review","prefer":"codex","fallback":"copilot"},{"kind":"bulk-read","prefer":"kiro","fallback":"codex"},{"kind":"research","prefer":"kiro","fallback":"copilot"},{"kind":"implement","prefer":"cursor","fallback":"codex"},{"kind":"second-opinion","prefer":"copilot","fallback":"codex"}]'

# Missing file: defaults, no warning. Unparsable file: defaults, with a warning.
cfg_load() {
  local path="${FARMOUT_CONFIG:-$HOME/.config/farmout/config.json}"
  CFG_JSON='{}'
  [ -f "$path" ] || return 0
  local raw; raw="$(cat "$path" 2>/dev/null)"
  if ! printf '%s' "$raw" | jq . >/dev/null 2>&1; then
    warn "config $path is not valid JSON; using defaults"
    return 0
  fi
  CFG_JSON="$raw"
}

cfg_valid_model() { # value
  case "$1" in '') return 1 ;; -*) return 1 ;; esac
  [[ "$1" =~ ^[][A-Za-z0-9._:/=-]+$ ]]
}
cfg_valid_effort() { case "$1" in low|medium|high) return 0 ;; esac; return 1; }
cfg_valid_int_range() { # value min max
  case "$1" in ''|*[!0-9]*) return 1 ;; esac
  [ "$1" -ge "$2" ] && [ "$1" -le "$3" ]
}
cfg_valid_kind() { case " $CFG_KINDS " in *" $1 "*) return 0 ;; esac; return 1; }

# _cfg_typed <jq path args...> -> "<type> <value>" for a scalar, or "null" when
# null, missing, or unreachable (a non-object parent).
_cfg_typed() {
  local out
  out="$(printf '%s' "$CFG_JSON" | jq -r "$@" 2>/dev/null)"
  [ -n "$out" ] && echo "$out" || echo null
}
_CFG_TYPED_FILTER='if . == null then "null" else (type + " " + tostring) end'

# An integer is a JSON number or a string of digits (jq -r reads both the same).
_cfg_int_ok() { # type value min max
  case "$1" in number|string) cfg_valid_int_range "$2" "$3" "$4" ;; *) return 1 ;; esac
}

# cfg_worker <cli> <key> -> the validated value, or empty for null/missing/invalid.
cfg_worker() {
  local tv t v ok
  tv="$(_cfg_typed --arg c "$1" --arg k "$2" ".workers[\$c][\$k] | $_CFG_TYPED_FILTER")"
  [ "$tv" = null ] && { echo ""; return 0; }
  t="${tv%% *}"; v="${tv#* }"
  case "$2" in
    enabled) [ "$t" = boolean ] && ok=true || ok=false ;;
    model) [ "$t" = string ] && cfg_valid_model "$v" && ok=true || ok=false ;;
    effort) [ "$t" = string ] && cfg_valid_effort "$v" && ok=true || ok=false ;;
    timeout_min) _cfg_int_ok "$t" "$v" 1 "$TIMEOUT_MIN_MAX" && ok=true || ok=false ;;
    *) ok=true ;;
  esac
  $ok || { warn "invalid workers.$1.$2 '$v' in config; ignoring"; echo ""; return 0; }
  echo "$v"
}

# cfg_limit <key> -> the validated value, or empty for null/missing/invalid.
cfg_limit() {
  local tv t v max
  tv="$(_cfg_typed --arg k "$1" ".limits[\$k] | $_CFG_TYPED_FILTER")"
  [ "$tv" = null ] && { echo ""; return 0; }
  t="${tv%% *}"; v="${tv#* }"
  case "$1" in stall_min) max="$STALL_MIN_MAX" ;; max_jobs) max="$MAX_JOBS_MAX" ;; *) echo "$v"; return 0 ;; esac
  _cfg_int_ok "$t" "$v" 1 "$max" || { warn "invalid limits.$1 '$v' in config; ignoring"; echo ""; return 0; }
  echo "$v"
}

# cfg_routing_json -> the routing array as compact JSON: the config's own array
# if it has one, else CFG_DEFAULT_ROUTING. Invalid entries are dropped (with a
# warn) rather than failing the whole config.
cfg_routing_json() {
  local src n rows="" i=0 line kind prefer fallback ok
  src="$(printf '%s' "$CFG_JSON" | jq -c --argjson d "$CFG_DEFAULT_ROUTING" \
    'if .routing == null then $d elif (.routing | type) == "array" then .routing else "invalid" end' 2>/dev/null)"
  case "$src" in
    '') src="$CFG_DEFAULT_ROUTING" ;;
    '"invalid"') warn "invalid routing in config (expected an array); using defaults"; src="$CFG_DEFAULT_ROUTING" ;;
  esac
  n="$(printf '%s' "$src" | jq 'length' 2>/dev/null)"
  case "$n" in ''|*[!0-9]*) n=0 ;; esac
  while [ "$i" -lt "$n" ]; do
    line="$(printf '%s' "$src" | jq -c ".[$i]" 2>/dev/null)"
    kind="$(printf '%s' "$line" | jq -r '.kind // empty' 2>/dev/null)"
    prefer="$(printf '%s' "$line" | jq -r '.prefer // empty' 2>/dev/null)"
    fallback="$(printf '%s' "$line" | jq -r '.fallback // empty' 2>/dev/null)"
    ok=true
    cfg_valid_kind "$kind" || ok=false
    adapter_known "$prefer" || ok=false
    [ -n "$fallback" ] && { adapter_known "$fallback" || ok=false; }
    if $ok; then rows="${rows:+$rows,}$line"; else warn "invalid routing rule #$i in config; ignoring"; fi
    i=$((i + 1))
  done
  echo "[$rows]"
}

# cfg_routing_for_kind <kind> -> two lines, "prefer" then "fallback" (the
# fallback line is empty when null); returns 1 if no validated rule matches.
cfg_routing_for_kind() {
  local kind="$1" row
  row="$(cfg_routing_json | jq -c --arg k "$kind" 'map(select(.kind == $k)) | .[0] // empty' 2>/dev/null)"
  [ -n "$row" ] || return 1
  printf '%s\n' "$(printf '%s' "$row" | jq -r '.prefer')"
  printf '%s\n' "$(printf '%s' "$row" | jq -r '.fallback // empty')"
}
