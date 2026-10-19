import os
import tempfile
import unittest

from refresh import board_status

D = "2026-10-18"


class BoardStatusTests(unittest.TestCase):
    def test_ok_writes_and_resets(self):
        e = board_status.update({"empty": 1, "error": 2}, "ok", D)
        self.assertEqual(e, {"empty": 0, "error": 0, "last": "ok", "since": D})
        self.assertEqual(board_status.action(e), "write")

    def test_first_empty_is_kept(self):
        e = board_status.update({}, "empty", D)
        self.assertEqual(e["empty"], 1)
        self.assertEqual(board_status.action(e), "keep")

    def test_second_empty_in_a_row_writes(self):
        e = board_status.update({"empty": 1, "error": 0, "last": "empty"}, "empty", D)
        self.assertEqual(e["empty"], 2)
        self.assertEqual(board_status.action(e), "write")

    def test_error_never_writes(self):
        e = board_status.update({}, "error", D)
        self.assertEqual(e["error"], 1)
        self.assertEqual(board_status.action(e), "keep")

    def test_three_errors_need_review(self):
        e = {}
        for i in range(3):
            e = board_status.update(e, "error", D)
            self.assertEqual(board_status.needs_review(e), i == 2)

    def test_error_between_empties_keeps_empty_count(self):
        e = board_status.update({}, "empty", D)
        e = board_status.update(e, "error", D)
        e = board_status.update(e, "empty", D)
        self.assertEqual(e["empty"], 2)
        self.assertEqual(board_status.action(e), "write")

    def test_missing_or_corrupt_state_file_loads_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(board_status.load(os.path.join(d, "nope.json")), {})
            bad = os.path.join(d, "bad.json")
            with open(bad, "w") as f:
                f.write("{not json")
            self.assertEqual(board_status.load(bad), {})

    def test_save_then_load_round_trips(self):
        state = {"acme": {"empty": 1, "error": 0, "last": "empty", "since": D}}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "status.json")
            board_status.save(p, state)
            self.assertEqual(board_status.load(p), state)
            self.assertEqual(os.listdir(d), ["status.json"])

    def test_since_is_the_start_of_the_current_streak(self):
        e = board_status.update({}, "error", "2026-10-12")
        self.assertEqual(e["since"], "2026-10-12")
        e = board_status.update(e, "error", "2026-10-15")
        e = board_status.update(e, "error", "2026-10-18")
        self.assertEqual(e["since"], "2026-10-12")
        self.assertEqual(e["error"], 3)
        e = board_status.update(e, "ok", "2026-10-19")
        self.assertEqual(e["since"], "2026-10-19")

    def test_since_resets_when_the_read_kind_changes(self):
        e = board_status.update({}, "empty", "2026-10-12")
        e = board_status.update(e, "error", "2026-10-15")
        self.assertEqual(e["since"], "2026-10-15")

    def test_report_lines_lists_each_group(self):
        state = {"a": {"empty": 0, "error": 1, "last": "error", "since": "2026-10-18"},
                 "b": {"empty": 1, "error": 0, "last": "empty", "since": "2026-10-18"},
                 "c": {"empty": 0, "error": 3, "last": "error", "since": "2026-10-12"},
                 "d": {"empty": 0, "error": 0, "last": "ok", "since": "2026-10-18"}}
        names = {"a": "Acme", "b": "Beta", "c": "Corp", "d": "Delta"}
        text = "\n".join(board_status.report_lines(state, names))
        self.assertIn("could not be read this run, kept as before (2)", text)
        self.assertIn("Empty once, kept; closes if empty again next run (1)", text)
        self.assertIn("- Beta", text)
        self.assertIn("Needs review: unreadable 3 runs in a row (1)", text)
        self.assertIn("- Corp (since 2026-10-12)", text)
        self.assertNotIn("Delta", text)

    def test_report_lines_order_and_none(self):
        text = "\n".join(board_status.report_lines({}, {}))
        titles = [l for l in text.splitlines() if l.startswith("## ")]
        self.assertEqual(titles, [
            "## Boards that could not be read this run, kept as before (0)",
            "## Empty once, kept; closes if empty again next run (0)",
            "## Needs review: unreadable 3 runs in a row (0)"])
        self.assertEqual(text.count("None."), 3)

    def test_report_lines_falls_back_to_id_for_unknown_company(self):
        state = {"zed": {"empty": 0, "error": 1, "last": "error", "since": D}}
        text = "\n".join(board_status.report_lines(state, {}))
        self.assertIn("- zed", text)


if __name__ == "__main__":
    unittest.main()
