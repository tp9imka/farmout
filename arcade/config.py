"""`farmout` config file: load/validate/save with etag-based concurrency.

Stdlib-only, targets /usr/bin/python3 (3.9): no match statements, no `X | Y`
types. Mirrors the validation rules in Part 1 of the design spec -- the same
rules `farmout` itself applies. Reading, an invalid value is dropped with a
warning, as `farmout` does; an invalid PUT is rejected with the offending
fields named.
"""

import hashlib
import json
import os
import re
import tempfile
import threading

CLIS = ("codex", "kiro", "copilot", "cursor")
KINDS = ("review", "bulk-read", "research", "implement", "second-opinion")
EFFORTS = ("low", "medium", "high")

DEFAULT_TIMEOUT_MIN = 30
DEFAULT_STALL_MIN = 10
DEFAULT_MAX_JOBS = 4
# Used when the config has no routing key; an explicit routing list replaces it
# whole. Keep identical to CFG_DEFAULT_ROUTING in lib/config.sh (a test checks).
DEFAULT_ROUTING = (
    {"kind": "review", "prefer": "codex", "fallback": "copilot"},
    {"kind": "bulk-read", "prefer": "kiro", "fallback": "codex"},
    {"kind": "research", "prefer": "kiro", "fallback": "copilot"},
    {"kind": "implement", "prefer": "cursor", "fallback": "codex"},
    {"kind": "second-opinion", "prefer": "copilot", "fallback": "codex"},
)

TIMEOUT_MIN_BOUNDS = (1, 240)
STALL_MIN_BOUNDS = (1, 120)
MAX_JOBS_BOUNDS = (1, 32)

_MODEL_RE = re.compile(r"^[A-Za-z0-9._:/=\[\]-]+$")
_DIGITS_RE = re.compile(r"[0-9]+")

# Serializes the whole check-current-etag-then-write critical section in
# save(), so two concurrent PUTs with the same (stale-once-one-lands) etag
# can't both read the pre-write etag and both think they matched it.
_SAVE_LOCK = threading.Lock()


class EtagMismatch(Exception):
    """Raised by save() when if_match doesn't match the file's current etag."""


def default_config_path():
    return os.path.expanduser(os.environ.get("FARMOUT_CONFIG", "~/.config/farmout/config.json"))


def _is_version_1(v):
    # True == 1 and 1.0 == 1 in Python; a plain `!= 1` would wrongly accept
    # both, so the version check needs the exact type, not just the value.
    return type(v) is int and v == 1


def _as_int(v):
    """An int, or a string of ASCII digits (bash reads both alike via jq -r);
    None for anything else."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and _DIGITS_RE.fullmatch(v):
        return int(v)
    return None


def _shown(v):
    return "'{}'".format(v) if isinstance(v, str) else json.dumps(v)


class _Sanitiser(object):
    """bash's rule for the effective config: null or an invalid value means
    the default, and every dropped value leaves a warning."""

    def __init__(self):
        self.warnings = []

    def drop(self, where, value, expected):
        self.warnings.append("{} {} ignored (expected {})".format(where, _shown(value), expected))

    def int_in(self, where, value, bounds, default):
        if value is None:
            return default
        n = _as_int(value)
        if n is not None and bounds[0] <= n <= bounds[1]:
            return n
        self.drop(where, value, "{}..{}".format(*bounds))
        return default

    def worker(self, cli, w):
        where = "workers." + cli
        if w is None:
            w = {}
        elif not isinstance(w, dict):
            self.drop(where, w, "an object")
            w = {}

        enabled = w.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            self.drop(where + ".enabled", enabled, "true or false")
            enabled = None

        model = w.get("model")
        if model is not None and not _valid_model(model):
            self.drop(where + ".model", model, "a model name")
            model = None

        effort = w.get("effort")
        if effort is not None and effort not in EFFORTS:
            self.drop(where + ".effort", effort, ", ".join(EFFORTS))
            effort = None

        models = w.get("models")
        kept = []
        if isinstance(models, list):
            for m in models:
                if _valid_model(m):
                    kept.append(m)
                else:
                    self.drop(where + ".models[]", m, "a model name")
        elif models is not None:
            self.drop(where + ".models", models, "a list")

        return {
            "enabled": True if enabled is None else enabled,
            "model": model,
            "effort": effort,
            "timeout_min": self.int_in(where + ".timeout_min", w.get("timeout_min"),
                                       TIMEOUT_MIN_BOUNDS, DEFAULT_TIMEOUT_MIN),
            "models": kept,
        }

    def routing(self, routing):
        if routing is None:
            return [dict(r) for r in DEFAULT_ROUTING]
        if not isinstance(routing, list):
            self.drop("routing", routing, "a list")
            return [dict(r) for r in DEFAULT_ROUTING]
        rules = []
        for i, rule in enumerate(routing):
            if _validate_rule(i, rule):
                self.drop("routing[{}]".format(i), rule, "a kind, a known prefer cli and fallback cli or null")
                continue
            rules.append({"kind": rule["kind"], "prefer": rule["prefer"], "fallback": rule.get("fallback")})
        return rules

    def limits(self, limits):
        if limits is None:
            limits = {}
        elif not isinstance(limits, dict):
            self.drop("limits", limits, "an object")
            limits = {}
        return {
            "stall_min": self.int_in("limits.stall_min", limits.get("stall_min"),
                                     STALL_MIN_BOUNDS, DEFAULT_STALL_MIN),
            "max_jobs": self.int_in("limits.max_jobs", limits.get("max_jobs"),
                                    MAX_JOBS_BOUNDS, DEFAULT_MAX_JOBS),
        }


def _sanitise(raw):
    raw = raw if isinstance(raw, dict) else {}
    san = _Sanitiser()

    version = raw.get("version")
    if version is not None and not _is_version_1(version):
        san.warnings.append("version {!r} ignored (expected 1)".format(version))

    workers_raw = raw.get("workers")
    if workers_raw is not None and not isinstance(workers_raw, dict):
        san.drop("workers", workers_raw, "an object")
    workers_raw = workers_raw if isinstance(workers_raw, dict) else {}
    for key in sorted(workers_raw):
        if key not in CLIS:
            san.warnings.append("workers.{} ignored (unknown cli)".format(key))
    workers = {}
    for cli in CLIS:
        workers[cli] = san.worker(cli, workers_raw.get(cli))

    cfg = {
        "version": 1,
        "workers": workers,
        "routing": san.routing(raw.get("routing")),
        "limits": san.limits(raw.get("limits")),
    }
    return cfg, san.warnings


def merge_defaults(raw):
    """The effective config: every key filled in, every invalid or null value
    replaced by its default (see raw_warnings() for what was dropped). Always
    something validate() accepts, so a round-trip GET -> unmodified PUT saves."""
    return _sanitise(raw)[0]


def raw_warnings(raw):
    """One warning per value merge_defaults() dropped or normalized. Meant
    for /api/state's errors[], read from the raw (pre-merge) file content."""
    return _sanitise(raw)[1]


def _valid_model(m):
    # fullmatch, not match: "$" alone still accepts one trailing "\n".
    return isinstance(m, str) and bool(_MODEL_RE.fullmatch(m)) and not m.startswith("-")


def _in_bounds(v, bounds):
    lo, hi = bounds
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def _validate_worker(cli, w):
    if not isinstance(w, dict):
        return ["workers.{}: must be an object".format(cli)]
    errors = []
    enabled = w.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        errors.append("workers.{}.enabled: must be a boolean".format(cli))
    model = w.get("model")
    if model is not None and not _valid_model(model):
        errors.append("workers.{}.model: invalid model name {!r}".format(cli, model))
    models = w.get("models")
    if isinstance(models, list):
        for m in models:
            if not _valid_model(m):
                errors.append("workers.{}.models: invalid model name {!r}".format(cli, m))
    elif models is not None:
        errors.append("workers.{}.models: must be a list".format(cli))
    effort = w.get("effort")
    if effort is not None and effort not in EFFORTS:
        errors.append("workers.{}.effort: must be low, medium, high or null".format(cli))
    timeout_min = w.get("timeout_min")
    if timeout_min is not None and not _in_bounds(timeout_min, TIMEOUT_MIN_BOUNDS):
        errors.append("workers.{}.timeout_min: must be {}..{}".format(cli, *TIMEOUT_MIN_BOUNDS))
    return errors


def _validate_rule(i, rule):
    if not isinstance(rule, dict):
        return ["routing[{}]: must be an object".format(i)]
    errors = []
    if rule.get("kind") not in KINDS:
        errors.append("routing[{}].kind: must be one of {}".format(i, ", ".join(KINDS)))
    if rule.get("prefer") not in CLIS:
        errors.append("routing[{}].prefer: unknown cli {!r}".format(i, rule.get("prefer")))
    fallback = rule.get("fallback")
    if fallback is not None and fallback not in CLIS:
        errors.append("routing[{}].fallback: unknown cli {!r}".format(i, fallback))
    return errors


def _validate_limits(limits):
    errors = []
    stall_min = limits.get("stall_min")
    if stall_min is not None and not _in_bounds(stall_min, STALL_MIN_BOUNDS):
        errors.append("limits.stall_min: must be {}..{}".format(*STALL_MIN_BOUNDS))
    max_jobs = limits.get("max_jobs")
    if max_jobs is not None and not _in_bounds(max_jobs, MAX_JOBS_BOUNDS):
        errors.append("limits.max_jobs: must be {}..{}".format(*MAX_JOBS_BOUNDS))
    return errors


def validate(cfg):
    if not isinstance(cfg, dict):
        return ["config: must be an object"]
    errors = []

    version = cfg.get("version")
    if version is not None and not _is_version_1(version):
        errors.append("version: must be 1")

    workers = cfg.get("workers")
    if isinstance(workers, dict):
        for cli, w in sorted(workers.items()):
            if cli not in CLIS:
                errors.append("workers.{}: unknown cli".format(cli))
                continue
            errors.extend(_validate_worker(cli, w))
    elif workers is not None:
        errors.append("workers: must be an object")

    routing = cfg.get("routing")
    if isinstance(routing, list):
        for i, rule in enumerate(routing):
            errors.extend(_validate_rule(i, rule))
    elif routing is not None:
        errors.append("routing: must be a list")

    limits = cfg.get("limits")
    if isinstance(limits, dict):
        errors.extend(_validate_limits(limits))
    elif limits is not None:
        errors.append("limits: must be an object")

    return errors


def _file_bytes(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _etag_of(data):
    return hashlib.sha256(data if data is not None else b"").hexdigest()


def _read_raw(path):
    """(raw_or_None, etag). raw is None for a missing or unparsable file --
    both mean "start from defaults" for merge_defaults(), but only a
    genuinely parsed dict has real warnings to report."""
    data = _file_bytes(path)
    etag = _etag_of(data)
    if data is None:
        return None, etag
    try:
        raw = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, etag
    return raw, etag


def load(path):
    raw, etag = _read_raw(path)
    return merge_defaults(raw), etag


def load_with_warnings(path):
    raw, etag = _read_raw(path)
    cfg, warnings = _sanitise(raw)
    return cfg, etag, warnings


def _atomic_write(path, data):
    dir_name = os.path.dirname(path) or "."
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=".config.json.tmp.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def save(path, cfg, if_match):
    with _SAVE_LOCK:
        current_etag = _etag_of(_file_bytes(path))
        if if_match != current_etag:
            raise EtagMismatch()
        data = json.dumps(cfg, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        _atomic_write(path, data)
        return _etag_of(data)
