import json
import os
import shutil
import tempfile
import unittest

import logs

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _feed_fixture(cli, name):
    state = logs.new_log_state(cli)
    path = os.path.join(FIXTURES, name)
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            state.feed(line)
    return state


class CodexParserTest(unittest.TestCase):
    def setUp(self):
        self.state = _feed_fixture("codex", "codex.log")

    def test_tool_is_bash(self):
        self.assertEqual(self.state.tool, "Bash")

    def test_tokens_is_input_plus_output(self):
        # turn.completed.usage: input_tokens 30012 + output_tokens 208
        self.assertEqual(self.state.tokens, 30012 + 208)

    def test_coins_is_none(self):
        self.assertIsNone(self.state.coins)

    def test_replay_has_one_entry_with_no_timestamp(self):
        self.assertEqual(len(self.state.replay), 1)
        entry = self.state.replay[0]
        self.assertIsNone(entry["t"])
        self.assertTrue(entry["m"].startswith("Bash "))
        self.assertIn("find .", entry["m"])

    def test_leading_noise_line_not_counted_as_bad(self):
        # "Reading additional input from stdin..." is not JSON at all.
        self.assertEqual(self.state.bad_lines, 0)


class CursorParserTest(unittest.TestCase):
    def setUp(self):
        self.state = _feed_fixture("cursor", "cursor.log")

    def test_tool_is_bash(self):
        self.assertEqual(self.state.tool, "Bash")

    def test_tokens_is_input_plus_output(self):
        # result.usage: inputTokens 11466 + outputTokens 464
        self.assertEqual(self.state.tokens, 11466 + 464)

    def test_coins_is_none(self):
        self.assertIsNone(self.state.coins)

    def test_replay_has_started_entry_with_timestamp(self):
        self.assertEqual(len(self.state.replay), 1)
        entry = self.state.replay[0]
        # startedAtMs "1790168920462", cast to int
        self.assertEqual(entry["t"], 1790168920462)
        self.assertTrue(entry["m"].startswith("Bash "))

    def test_no_bad_lines(self):
        self.assertEqual(self.state.bad_lines, 0)


class KiroParserTest(unittest.TestCase):
    def setUp(self):
        self.state = _feed_fixture("kiro", "kiro.log")

    def test_tool_is_bash(self):
        self.assertEqual(self.state.tool, "Bash")

    def test_tokens_is_none(self):
        self.assertIsNone(self.state.tokens)

    def test_coins_sum_and_unit(self):
        self.assertIsNotNone(self.state.coins)
        self.assertEqual(self.state.coins["unit"], "CR")
        self.assertAlmostEqual(
            self.state.coins["value"], 0.08371537064676618 + 0.04067140812603649
        )

    def test_replay_is_the_title_verbatim_not_tool_prefixed(self):
        # spec: "the replay line is the title" -- not "<Tool> <title>",
        # which would stutter ("Bash Running: find ...").
        self.assertEqual(len(self.state.replay), 1)
        self.assertEqual(
            self.state.replay[0]["m"],
            "Running: find . -type f -not -path './.git/*' | wc -l",
        )

    def test_progress_bar_noise_not_counted_as_bad(self):
        # ~227 non-JSON progress/login lines precede the JSONL stream.
        self.assertEqual(self.state.bad_lines, 0)


class CopilotParserTest(unittest.TestCase):
    def setUp(self):
        self.state = _feed_fixture("copilot", "copilot.log")

    def test_tool_is_bash(self):
        self.assertEqual(self.state.tool, "Bash")

    def test_tokens_is_none(self):
        self.assertIsNone(self.state.tokens)

    def test_coins_is_premium_requests(self):
        self.assertEqual(self.state.coins, {"value": 6, "unit": "PR"})

    def test_replay_timestamp_is_not_null(self):
        self.assertEqual(len(self.state.replay), 1)
        entry = self.state.replay[0]
        self.assertIsNotNone(entry["t"])
        self.assertIsInstance(entry["t"], int)

    def test_no_bad_lines(self):
        self.assertEqual(self.state.bad_lines, 0)


class MalformedAndReplayCapTest(unittest.TestCase):
    def test_malformed_json_line_increments_bad_lines(self):
        state = logs.new_log_state("codex")
        state.feed('{"type": "turn.completed", "usage": BROKEN}\n')
        self.assertEqual(state.bad_lines, 1)
        self.assertIsNone(state.tokens)

    def test_non_json_noise_line_does_not_increment_bad_lines(self):
        state = logs.new_log_state("codex")
        state.feed("just some stray text, not json at all\n")
        self.assertEqual(state.bad_lines, 0)

    def test_blank_line_is_ignored(self):
        state = logs.new_log_state("codex")
        state.feed("\n")
        self.assertEqual(state.bad_lines, 0)
        self.assertEqual(state.replay, [])

    def test_replay_cap_is_fifty(self):
        state = logs.new_log_state("codex")
        for i in range(logs.REPLAY_MAX + 10):
            item = {
                "id": "item_%d" % i,
                "type": "command_execution",
                "command": "echo %d" % i,
            }
            state.feed(json.dumps({"type": "item.started", "item": item}))
        self.assertEqual(len(state.replay), logs.REPLAY_MAX)
        # the cap keeps the most recent entries
        last = logs.REPLAY_MAX + 9
        self.assertTrue(state.replay[-1]["m"].endswith("echo %d" % last))

    def test_replay_text_is_truncated_to_120_chars(self):
        state = logs.new_log_state("codex")
        long_command = "x" * 200
        item = {"id": "item_0", "type": "command_execution", "command": long_command}
        state.feed(json.dumps({"type": "item.started", "item": item}))
        self.assertEqual(len(state.replay[0]["m"]), 120)


class IncrementalReaderTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="arcade-reader-test-")
        self.path = os.path.join(self.tmpdir, "log.txt")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_append_in_two_chunks_holds_back_partial_line(self):
        reader = logs.IncrementalReader()
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("line1\nline2 partial")
        lines = reader.read_new(self.path)
        self.assertEqual(lines, ["line1"])

        with open(self.path, "a", encoding="utf-8") as f:
            f.write(" continued\nline3\n")
        lines = reader.read_new(self.path)
        self.assertEqual(lines, ["line2 partial continued", "line3"])

    def test_no_new_lines_returns_empty(self):
        reader = logs.IncrementalReader()
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("line1\n")
        reader.read_new(self.path)
        self.assertEqual(reader.read_new(self.path), [])

    def test_truncate_resets(self):
        reader = logs.IncrementalReader()
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("line1\nline2\nline3\n")
        reader.read_new(self.path)

        with open(self.path, "w", encoding="utf-8") as f:
            f.write("fresh1\n")
        lines = reader.read_new(self.path)
        self.assertEqual(lines, ["fresh1"])

    def test_replace_file_new_inode_resets(self):
        reader = logs.IncrementalReader()
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("line1\nline2\n")
        reader.read_new(self.path)

        os.remove(self.path)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("newfile1\n")
        lines = reader.read_new(self.path)
        self.assertEqual(lines, ["newfile1"])

    def test_missing_file_returns_empty(self):
        reader = logs.IncrementalReader()
        self.assertEqual(reader.read_new(os.path.join(self.tmpdir, "nope.txt")), [])

    def test_crlf_lines_have_trailing_cr_stripped(self):
        reader = logs.IncrementalReader()
        with open(self.path, "wb") as f:
            f.write(b"line1\r\nline2\r\n")
        lines = reader.read_new(self.path)
        self.assertEqual(lines, ["line1", "line2"])


class MultiByteUtf8ReaderTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="arcade-reader-utf8-test-")
        self.path = os.path.join(self.tmpdir, "log.txt")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_three_byte_char_split_across_reads_decodes_intact(self):
        # U+2019 RIGHT SINGLE QUOTATION MARK is 3 bytes in UTF-8.
        quote = "’".encode("utf-8")
        self.assertEqual(len(quote), 3)
        reader = logs.IncrementalReader()
        with open(self.path, "wb") as f:
            f.write(b"partial" + quote[:2])
        # no newline yet: the whole (still-incomplete) tail is held back raw
        self.assertEqual(reader.read_new(self.path), [])

        with open(self.path, "ab") as f:
            f.write(quote[2:] + b" done\n")
        lines = reader.read_new(self.path)
        self.assertEqual(lines, ["partial’ done"])

    def test_four_byte_emoji_split_across_reads_decodes_intact(self):
        # U+1F600 GRINNING FACE is 4 bytes in UTF-8.
        emoji = "\U0001F600".encode("utf-8")
        self.assertEqual(len(emoji), 4)
        reader = logs.IncrementalReader()
        with open(self.path, "wb") as f:
            f.write(b"hi " + emoji[:2])
        self.assertEqual(reader.read_new(self.path), [])

        with open(self.path, "ab") as f:
            f.write(emoji[2:] + b" bye\n")
        lines = reader.read_new(self.path)
        self.assertEqual(lines, ["hi \U0001F600 bye"])

    def test_offset_stays_byte_accurate_after_multibyte_line(self):
        reader = logs.IncrementalReader()
        with open(self.path, "wb") as f:
            f.write("café menu\n".encode("utf-8"))
        first = reader.read_new(self.path)
        self.assertEqual(first, ["café menu"])

        with open(self.path, "ab") as f:
            f.write(b"next line\n")
        second = reader.read_new(self.path)
        self.assertEqual(second, ["next line"])


class AccumulationSemanticsTest(unittest.TestCase):
    def test_codex_tokens_accumulate_across_turn_completed_events(self):
        state = logs.new_log_state("codex")
        state.feed(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 10}}))
        state.feed(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 50, "output_tokens": 5}}))
        self.assertEqual(state.tokens, (100 + 10) + (50 + 5))

    def test_kiro_coins_accumulate_across_metadata_events(self):
        state = logs.new_log_state("kiro")
        ev1 = {"type": "metadata", "data": {"meteringUsage": [{"value": 0.01, "unit": "credit"}]}}
        ev2 = {"type": "metadata", "data": {"meteringUsage": [{"value": 0.02, "unit": "credit"}]}}
        state.feed(json.dumps(ev1))
        state.feed(json.dumps(ev2))
        self.assertEqual(state.coins["unit"], "CR")
        self.assertAlmostEqual(state.coins["value"], 0.03)

    def test_cursor_tokens_take_latest_result_not_sum(self):
        state = logs.new_log_state("cursor")
        r1 = {"type": "result", "usage": {"inputTokens": 100, "outputTokens": 10}}
        r2 = {"type": "result", "usage": {"inputTokens": 5, "outputTokens": 1}}
        state.feed(json.dumps(r1))
        state.feed(json.dumps(r2))
        self.assertEqual(state.tokens, 5 + 1)

    def test_copilot_coins_take_latest_result_not_sum(self):
        state = logs.new_log_state("copilot")
        r1 = {"type": "result", "usage": {"premiumRequests": 3}}
        r2 = {"type": "result", "usage": {"premiumRequests": 9}}
        state.feed(json.dumps(r1))
        state.feed(json.dumps(r2))
        self.assertEqual(state.coins, {"value": 9, "unit": "PR"})


class ToolNameCasingTest(unittest.TestCase):
    def test_cursor_camel_case_tool_call_key(self):
        state = logs.new_log_state("cursor")
        obj = {
            "type": "tool_call",
            "subtype": "started",
            "tool_call": {
                "todoWriteToolCall": {"args": {}},
                "startedAtMs": "1",
            },
        }
        state.feed(json.dumps(obj))
        self.assertEqual(state.tool, "TodoWrite")

    def test_codex_snake_case_item_type_fallback(self):
        state = logs.new_log_state("codex")
        item = {"id": "item_9", "type": "file_change", "path": "foo.txt"}
        state.feed(json.dumps({"type": "item.started", "item": item}))
        self.assertEqual(state.tool, "FileChange")


if __name__ == "__main__":
    unittest.main()
