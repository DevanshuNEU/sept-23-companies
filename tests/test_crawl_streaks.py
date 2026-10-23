"""The streak rule (issue #21, Task 7): a port of refresh/board_status.update, action and needs_review."""
import unittest

from outreach.crawl.streaks import Streaks, closes_postings, needs_review, next_streaks

D = "2026-10-18T08:00:00Z"


def step(prev: Streaks | None, read: str, now: str = D) -> Streaks:
    if prev is None:
        return next_streaks(None, 0, 0, None, read, now)
    return next_streaks(prev.last_read, prev.empty_streak, prev.error_streak, prev.streak_since, read, now)


class StreakTests(unittest.TestCase):
    def test_ok_resets_both_counts(self):
        s = next_streaks("error", 1, 2, D, "ok", D)
        self.assertEqual(s, Streaks(last_read="ok", empty_streak=0, error_streak=0, streak_since=D))
        self.assertFalse(closes_postings(s.last_read, s.empty_streak))

    def test_first_empty_is_kept(self):
        s = step(None, "empty")
        self.assertEqual(s.empty_streak, 1)
        self.assertFalse(closes_postings(s.last_read, s.empty_streak))

    def test_second_empty_in_a_row_closes(self):
        s = step(step(None, "empty"), "empty")
        self.assertEqual(s.empty_streak, 2)
        self.assertTrue(closes_postings(s.last_read, s.empty_streak))

    def test_error_never_closes(self):
        s = step(None, "error")
        self.assertEqual(s.error_streak, 1)
        self.assertFalse(closes_postings(s.last_read, s.empty_streak))
        # Not even with an empty count of two behind it.
        s = step(step(step(None, "empty"), "empty"), "error")
        self.assertEqual(s.empty_streak, 2)
        self.assertFalse(closes_postings(s.last_read, s.empty_streak))

    def test_three_errors_need_review(self):
        s = None
        for i in range(3):
            s = step(s, "error")
            self.assertEqual(needs_review(s.error_streak), i == 2)

    def test_error_between_empties_keeps_the_empty_count(self):
        s = step(step(step(None, "empty"), "error"), "empty")
        self.assertEqual(s.empty_streak, 2)
        self.assertEqual(s.error_streak, 0)
        self.assertTrue(closes_postings(s.last_read, s.empty_streak))

    def test_empty_clears_the_error_count(self):
        s = step(step(step(None, "error"), "error"), "empty")
        self.assertEqual((s.empty_streak, s.error_streak), (1, 0))

    def test_unknown_read_raises(self):
        with self.assertRaises(ValueError):
            next_streaks(None, 0, 0, None, "nope", D)

    def test_unchanged_clears_like_ok(self):
        s = next_streaks("error", 1, 2, D, "unchanged", D)
        self.assertEqual((s.last_read, s.empty_streak, s.error_streak), ("unchanged", 0, 0))
        self.assertFalse(closes_postings(s.last_read, s.empty_streak))
        self.assertFalse(needs_review(s.error_streak))

    def test_since_holds_while_the_read_repeats(self):
        s = step(None, "error", "2026-10-12T00:00:00Z")
        self.assertEqual(s.streak_since, "2026-10-12T00:00:00Z")
        s = step(s, "error", "2026-10-15T00:00:00Z")
        s = step(s, "error", "2026-10-18T00:00:00Z")
        self.assertEqual((s.streak_since, s.error_streak), ("2026-10-12T00:00:00Z", 3))
        s = step(s, "ok", "2026-10-19T00:00:00Z")
        self.assertEqual(s.streak_since, "2026-10-19T00:00:00Z")

    def test_since_resets_when_the_read_kind_changes(self):
        s = step(step(None, "empty", "2026-10-12T00:00:00Z"), "error", "2026-10-15T00:00:00Z")
        self.assertEqual(s.streak_since, "2026-10-15T00:00:00Z")

    def test_thresholds(self):
        self.assertFalse(closes_postings("empty", 1))
        self.assertTrue(closes_postings("empty", 2))
        self.assertTrue(closes_postings("empty", 3))
        self.assertFalse(closes_postings("ok", 2))
        self.assertFalse(closes_postings("unchanged", 2))
        self.assertFalse(needs_review(2))
        self.assertTrue(needs_review(3))
        self.assertTrue(needs_review(4))


if __name__ == "__main__":
    unittest.main()
