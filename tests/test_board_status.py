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


if __name__ == "__main__":
    unittest.main()
