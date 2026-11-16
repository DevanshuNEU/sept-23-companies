"""The AppleScript runner, tested with a fake subprocess.run: no test starts osascript or touches Mail.app."""
import subprocess
import unittest
from unittest import mock

from outreach.sending import applescript
from outreach.sending.mail import KINDS, RETRYABLE, GmailError


def completed(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess(["osascript", "-"], code, stdout=stdout, stderr=stderr)


class RunnerTest(unittest.TestCase):
    def run_with(self, result):
        with mock.patch.object(applescript.subprocess, "run", side_effect=result if isinstance(result, BaseException)
                               else None, return_value=None if isinstance(result, BaseException) else result) as m:
            try:
                out = applescript.osascript_runner("tell app", ["a", "b"], 5)
            except GmailError as e:
                return e, m
            return out, m

    def test_script_on_stdin_args_after_dash(self):
        out, m = self.run_with(completed("ok\n"))
        self.assertEqual(out, "ok\n")
        args, kw = m.call_args
        self.assertEqual(args[0], ["osascript", "-", "a", "b"])
        self.assertEqual(kw["input"], "tell app")
        self.assertEqual(kw["timeout"], 5)

    def test_output_is_read_as_utf8_whatever_the_locale(self):
        _, m = self.run_with(completed("ok\n"))
        self.assertEqual(m.call_args[1]["encoding"], "utf-8")

    def test_timeout_maps_to_timeout(self):
        err, _ = self.run_with(subprocess.TimeoutExpired("osascript", 5))
        self.assertIsInstance(err, GmailError)
        self.assertEqual(err.kind, "timeout")

    def test_app_not_running_maps_to_app_closed(self):
        err, _ = self.run_with(completed(stderr="execution error: Application isn't running. (-600)", code=1))
        self.assertEqual(err.kind, "app_closed")
        self.assertTrue(err.retryable)

    def test_automation_not_allowed_maps_to_not_allowed_and_is_never_retried(self):
        err, _ = self.run_with(completed(stderr="execution error: Not authorized to send Apple events to Mail. (-1743)",
                                         code=1))
        self.assertEqual(err.kind, "not_allowed")
        self.assertIn("not_allowed", KINDS)
        self.assertFalse(err.retryable)

    def test_other_failure_maps_to_server_and_detail_is_short_and_scrubbed(self):
        err, _ = self.run_with(completed(stderr="boom Bearer abc123 " + "x" * 500, code=1))
        self.assertEqual(err.kind, "server")
        self.assertLessEqual(len(err.detail), 300)
        self.assertNotIn("abc123", err.detail)

    def test_missing_osascript_maps_to_server(self):
        err, _ = self.run_with(FileNotFoundError("osascript"))
        self.assertEqual(err.kind, "server")

    def test_app_closed_is_a_known_retryable_kind(self):
        self.assertIn("app_closed", KINDS)
        self.assertIn("app_closed", RETRYABLE)


class RecordsTest(unittest.TestCase):
    def test_fields_unescaped(self):
        out = "a\\tb\tc\\nd\te\\\\f\n\nx\ty\n"
        self.assertEqual(applescript.parse_records(out), [["a\tb", "c\nd", "e\\f"], ["x", "y"]])

    def test_escape_is_what_unescape_undoes(self):
        for text in ("plain", "a\tb", "x\\ty", "one\ntwo", "cr\r\nlf", "\\\\n", ""):
            escaped = applescript.escape_field(text)
            self.assertFalse({"\t", "\n", "\r"} & set(escaped))
            self.assertEqual(applescript.unescape_field(escaped), text.replace("\r\n", "\n").replace("\r", "\n"))

    def test_backslash_before_t_is_not_a_tab(self):
        self.assertEqual(applescript.unescape_field("\\\\t"), "\\t")

    def test_empty_output_has_no_records(self):
        self.assertEqual(applescript.parse_records(""), [])

    def test_empty_fields_are_kept(self):
        self.assertEqual(applescript.parse_records("a\t\tb\n"), [["a", "", "b"]])


if __name__ == "__main__":
    unittest.main()
