import hashlib
import json
import os
import shutil
import tempfile
import threading
import subprocess
import time
import unittest
from unittest import mock

import config

FARMOUT_LIB = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "lib"))


class ConfigTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="arcade-config-test-")
        self.path = os.path.join(self.tmp, "sub", "config.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_raw(self, obj_or_bytes):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        data = obj_or_bytes if isinstance(obj_or_bytes, bytes) else json.dumps(obj_or_bytes).encode("utf-8")
        with open(self.path, "wb") as f:
            f.write(data)
        return data

    # -- load / defaults ---------------------------------------------------

    def test_load_missing_file_returns_defaults_and_empty_etag(self):
        cfg, etag = config.load(self.path)
        self.assertEqual(etag, hashlib.sha256(b"").hexdigest())
        self.assertEqual(cfg["version"], 1)
        for cli in config.CLIS:
            self.assertIn(cli, cfg["workers"])
            self.assertTrue(cfg["workers"][cli]["enabled"])
            self.assertIsNone(cfg["workers"][cli]["model"])
            self.assertIsNone(cfg["workers"][cli]["effort"])
            self.assertEqual(cfg["workers"][cli]["timeout_min"], 30)
            self.assertEqual(cfg["workers"][cli]["models"], [])
        self.assertEqual(cfg["limits"], {"stall_min": 10, "max_jobs": 4})
        self.assertEqual(cfg["routing"], [
            {"kind": "review", "prefer": "codex", "fallback": "copilot"},
            {"kind": "bulk-read", "prefer": "kiro", "fallback": "codex"},
            {"kind": "research", "prefer": "kiro", "fallback": "copilot"},
            {"kind": "implement", "prefer": "cursor", "fallback": "codex"},
            {"kind": "second-opinion", "prefer": "copilot", "fallback": "codex"},
        ])

    def test_load_partial_file_merges_defaults(self):
        data = self._write_raw({"workers": {"codex": {"enabled": False}}})
        cfg, etag = config.load(self.path)
        self.assertEqual(etag, hashlib.sha256(data).hexdigest())
        self.assertFalse(cfg["workers"]["codex"]["enabled"])
        self.assertEqual(cfg["workers"]["codex"]["timeout_min"], 30)
        self.assertTrue(cfg["workers"]["kiro"]["enabled"])

    def test_load_unparsable_file_falls_back_to_defaults(self):
        data = self._write_raw(b"{not json")
        cfg, etag = config.load(self.path)
        self.assertEqual(cfg["limits"]["max_jobs"], 4)
        self.assertEqual(etag, hashlib.sha256(data).hexdigest())

    def test_etag_differs_for_different_content(self):
        self._write_raw({"limits": {"max_jobs": 4}})
        _, etag_a = config.load(self.path)
        self._write_raw({"limits": {"max_jobs": 5}})
        _, etag_b = config.load(self.path)
        self.assertNotEqual(etag_a, etag_b)

    # -- validate ---------------------------------------------------------

    def test_validate_accepts_defaults(self):
        cfg, _ = config.load(self.path)
        self.assertEqual(config.validate(cfg), [])

    def test_validate_rejects_bad_model(self):
        cfg, _ = config.load(self.path)
        cfg["workers"]["codex"]["model"] = "-rm -rf"
        errors = config.validate(cfg)
        self.assertEqual(len(errors), 1)
        self.assertIn("workers.codex.model", errors[0])

    def test_validate_rejects_bad_effort(self):
        cfg, _ = config.load(self.path)
        cfg["workers"]["codex"]["effort"] = "extreme"
        errors = config.validate(cfg)
        self.assertEqual(len(errors), 1)
        self.assertIn("effort", errors[0])

    def test_validate_bounds(self):
        cfg, _ = config.load(self.path)
        cfg["workers"]["codex"]["timeout_min"] = 0
        cfg["limits"]["stall_min"] = 999
        cfg["limits"]["max_jobs"] = 0
        errors = config.validate(cfg)
        self.assertEqual(len(errors), 3)

    def test_validate_rejects_unknown_routing_cli(self):
        cfg, _ = config.load(self.path)
        cfg["routing"] = [{"kind": "review", "prefer": "nope", "fallback": "also-nope"}]
        errors = config.validate(cfg)
        self.assertEqual(len(errors), 2)

    def test_validate_rejects_unknown_kind(self):
        cfg, _ = config.load(self.path)
        cfg["routing"] = [{"kind": "nope", "prefer": "codex", "fallback": None}]
        errors = config.validate(cfg)
        self.assertEqual(len(errors), 1)

    def test_validate_allows_null_fallback(self):
        cfg, _ = config.load(self.path)
        cfg["routing"] = [{"kind": "review", "prefer": "codex", "fallback": None}]
        self.assertEqual(config.validate(cfg), [])

    def test_validate_rejects_model_with_trailing_newline(self):
        # "$" alone accepts one trailing "\n"; fullmatch must not.
        cfg, _ = config.load(self.path)
        cfg["workers"]["codex"]["model"] = "codex\n"
        errors = config.validate(cfg)
        self.assertEqual(len(errors), 1)
        self.assertIn("model", errors[0])

    def test_validate_rejects_non_bool_enabled(self):
        cfg, _ = config.load(self.path)
        cfg["workers"]["codex"]["enabled"] = "yes"
        errors = config.validate(cfg)
        self.assertEqual(len(errors), 1)
        self.assertIn("enabled", errors[0])

    def test_validate_rejects_unknown_worker_key(self):
        cfg, _ = config.load(self.path)
        cfg["workers"]["not-a-real-cli"] = {"enabled": True}
        errors = config.validate(cfg)
        self.assertEqual(len(errors), 1)
        self.assertIn("not-a-real-cli", errors[0])

    def test_validate_rejects_wrong_version(self):
        cfg, _ = config.load(self.path)
        cfg["version"] = 2
        errors = config.validate(cfg)
        self.assertEqual(len(errors), 1)
        self.assertIn("version", errors[0])

    def test_validate_rejects_models_field_that_is_not_a_list(self):
        cfg, _ = config.load(self.path)
        cfg["workers"]["codex"]["models"] = "notalist"
        errors = config.validate(cfg)
        self.assertEqual(len(errors), 1)
        self.assertIn("models", errors[0])

    def test_validate_rejects_bool_version(self):
        # True == 1 in Python; a plain != check would wrongly accept it.
        cfg, _ = config.load(self.path)
        cfg["version"] = True
        errors = config.validate(cfg)
        self.assertEqual(len(errors), 1)
        self.assertIn("version", errors[0])

    def test_validate_rejects_float_version(self):
        cfg, _ = config.load(self.path)
        cfg["version"] = 1.0
        errors = config.validate(cfg)
        self.assertEqual(len(errors), 1)
        self.assertIn("version", errors[0])

    # -- effective config: unknown keys dropped, version normalized --------

    def test_merge_defaults_drops_unknown_worker_keys(self):
        cfg = config.merge_defaults({"workers": {"codx": {"enabled": True}}})
        self.assertNotIn("codx", cfg["workers"])
        self.assertEqual(set(cfg["workers"]), set(config.CLIS))

    def test_merge_defaults_normalizes_bad_version_to_1(self):
        self.assertEqual(config.merge_defaults({"version": 2})["version"], 1)
        self.assertEqual(config.merge_defaults({"version": True})["version"], 1)
        self.assertEqual(config.merge_defaults({})["version"], 1)

    def test_raw_warnings_reports_unknown_worker_and_bad_version(self):
        warnings = config.raw_warnings({"workers": {"codx": {}}, "version": 2})
        self.assertEqual(len(warnings), 2)
        self.assertTrue(any("codx" in w and "unknown cli" in w for w in warnings))
        self.assertTrue(any("version 2" in w and "expected 1" in w for w in warnings))

    def test_raw_warnings_empty_for_a_clean_config(self):
        cfg, _ = config.load(self.path)
        self.assertEqual(config.raw_warnings(cfg), [])

    def test_load_with_warnings_round_trips_a_dirty_file_to_a_clean_one(self):
        self._write_raw({"workers": {"codx": {"enabled": True}}, "version": 2})
        cfg, etag, warnings = config.load_with_warnings(self.path)
        self.assertEqual(len(warnings), 2)
        self.assertNotIn("codx", cfg["workers"])
        self.assertEqual(cfg["version"], 1)
        self.assertEqual(config.validate(cfg), [])  # the cleaned config PUTs back fine

        new_etag = config.save(self.path, cfg, etag)
        _, _, warnings_after = config.load_with_warnings(self.path)
        self.assertEqual(warnings_after, [])
        self.assertNotEqual(etag, new_etag)

    # -- save / etag ---------------------------------------------------------

    # -- sanitised effective config (bash's rule: invalid or null => default) --

    def test_default_routing_matches_bash_table(self):
        script = ('. "$1/common.sh"; . "$1/adapters.sh"; . "$1/config.sh"; cfg_load; cfg_routing_json')
        env = dict(os.environ)
        env["FARMOUT_CONFIG"] = os.path.join(self.tmp, "no-such-config.json")
        out = subprocess.check_output(["/bin/bash", "-c", script, "bash", FARMOUT_LIB], env=env)
        self.assertEqual(json.loads(out.decode("utf-8")), config.merge_defaults({})["routing"])
        self.assertEqual(len(config.merge_defaults({})["routing"]), len(config.KINDS))

    def test_explicit_routing_replaces_defaults(self):
        rule = {"kind": "review", "prefer": "kiro", "fallback": None}
        self.assertEqual(config.merge_defaults({"routing": [rule]})["routing"], [rule])
        self.assertEqual(config.merge_defaults({"routing": []})["routing"], [])

    def test_null_and_digit_string_limits(self):
        cfg = config.merge_defaults({"limits": {"stall_min": None, "max_jobs": "10"}})
        self.assertEqual(cfg["limits"], {"stall_min": 10, "max_jobs": 10})
        self.assertEqual(config.raw_warnings({"limits": {"stall_min": None, "max_jobs": "10"}}), [])
        self.assertEqual(config.merge_defaults({"limits": {"stall_min": "7"}})["limits"]["stall_min"], 7)

    def test_out_of_range_or_wrong_type_limit_is_default_with_warning(self):
        for bad in (0, 121, "x", " 10", 10.0, True, "\u0661\u0660"):
            raw = {"limits": {"stall_min": bad}}
            self.assertEqual(config.merge_defaults(raw)["limits"]["stall_min"], config.DEFAULT_STALL_MIN, bad)
            warnings = config.raw_warnings(raw)
            self.assertEqual(len(warnings), 1, bad)
            self.assertIn("limits.stall_min", warnings[0])
            self.assertIn("expected 1..120", warnings[0])
        self.assertEqual(config.raw_warnings({"limits": {"stall_min": "x"}}),
                         ["limits.stall_min 'x' ignored (expected 1..120)"])

    def test_every_worker_field_sanitised(self):
        raw = {"workers": {"codex": {
            "enabled": "false", "model": "-x", "effort": "turbo", "timeout_min": 999,
            "models": ["good-1", "-bad", 5, "good-2"],
        }, "kiro": {"timeout_min": "45", "model": None, "effort": None}}}
        cfg = config.merge_defaults(raw)
        self.assertEqual(cfg["workers"]["codex"], {
            "enabled": True, "model": None, "effort": None, "timeout_min": 30, "models": ["good-1", "good-2"],
        })
        self.assertEqual(cfg["workers"]["kiro"]["timeout_min"], 45)
        warnings = config.raw_warnings(raw)
        for field in ("enabled", "model", "effort", "timeout_min"):
            self.assertTrue(any("workers.codex." + field + " " in w for w in warnings), field)
        self.assertEqual(sum(1 for w in warnings if "workers.codex.models[]" in w), 2)
        self.assertEqual(config.validate(cfg), [])

    def test_invalid_routing_rule_dropped_others_kept(self):
        good_a = {"kind": "review", "prefer": "codex", "fallback": None}
        good_b = {"kind": "implement", "prefer": "cursor", "fallback": "codex"}
        raw = {"routing": [good_a, {"kind": "review", "prefer": "fake", "fallback": None}, "x", good_b]}
        cfg = config.merge_defaults(raw)
        self.assertEqual(cfg["routing"], [good_a, good_b])
        warnings = config.raw_warnings(raw)
        self.assertEqual([w.split(" ")[0] for w in warnings], ["routing[1]", "routing[2]"])

    def test_non_list_routing_uses_defaults_with_warning(self):
        cfg = config.merge_defaults({"routing": "codex"})
        self.assertEqual(len(cfg["routing"]), len(config.KINDS))
        self.assertEqual(len(config.raw_warnings({"routing": "codex"})), 1)

    def test_save_atomic_and_new_etag(self):
        cfg, etag = config.load(self.path)
        new_etag = config.save(self.path, cfg, etag)
        self.assertNotEqual(etag, new_etag)
        with open(self.path, "rb") as f:
            data = f.read()
        self.assertEqual(new_etag, hashlib.sha256(data).hexdigest())
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["config.json"])

    def test_save_stale_etag_raises(self):
        cfg, etag = config.load(self.path)
        config.save(self.path, cfg, etag)
        with self.assertRaises(config.EtagMismatch):
            config.save(self.path, cfg, etag)

    def test_save_creates_parent_dirs(self):
        cfg, etag = config.load(self.path)
        config.save(self.path, cfg, etag)
        self.assertTrue(os.path.isfile(self.path))

    def test_concurrent_saves_with_same_etag_only_one_succeeds(self):
        # Widens the check-then-write window deterministically: without a
        # lock around it, both threads read the same pre-write etag and both
        # think they're first.
        cfg, etag = config.load(self.path)
        original_file_bytes = config._file_bytes

        def slow_file_bytes(path):
            data = original_file_bytes(path)
            time.sleep(0.05)
            return data

        results = []

        def worker():
            try:
                config.save(self.path, cfg, etag)
                results.append("ok")
            except config.EtagMismatch:
                results.append("mismatch")

        with mock.patch.object(config, "_file_bytes", side_effect=slow_file_bytes):
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

        self.assertEqual(sorted(results), ["mismatch", "ok"])


if __name__ == "__main__":
    unittest.main()
