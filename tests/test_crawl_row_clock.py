"""The list time of a posting row, and how a repost is found (#193, Part 3)."""
import itertools
import unittest

from outreach.crawl.cadence import add_seconds
from outreach.crawl.row_clock import DATE_TRUSTED, GAP_TAG_S, REPOST_DAYS, find_repost, list_at, repost_key
from tests.storetest import fresh_store, seed_company

NOW = "2026-11-10T15:00:00Z"
_keys = itertools.count(1)


class ListAtTests(unittest.TestCase):
    def test_the_numbers_are_the_spec_numbers(self):
        self.assertEqual((REPOST_DAYS, GAP_TAG_S), (120, 7200))
        self.assertNotIn("lever", DATE_TRUSTED)
        self.assertNotIn("greenhouse", DATE_TRUSTED)
        self.assertNotIn("ashby", DATE_TRUSTED)

    def test_list_at_uses_posted_at(self):
        self.assertEqual(list_at("greenhouse", "2026-11-09T15:44:00Z", "2026-11-09T15:38:00Z", "2026-11-09", None),
                         "2026-11-09T15:38:00Z")
        # The board's time wins over a date and over the first sighting, gap or no gap.
        self.assertEqual(list_at("workday", "2026-11-09T15:44:00Z", "2026-03-02T10:00:00Z", "2026-11-01", 300),
                         "2026-03-02T10:00:00Z")

    def test_list_at_caps_posted_at_at_first_seen(self):
        # The board says it went up after Knock saw it: the first sighting wins.
        self.assertEqual(list_at("ashby", "2026-11-09T15:00:00Z", "2026-11-09T18:00:00Z", None, 300),
                         "2026-11-09T15:00:00Z")

    def test_list_at_date_only_is_ny_midnight(self):
        self.assertEqual(list_at("workday", "2026-11-09T15:00:00Z", None, "2026-11-09", None), "2026-11-09T05:00:00Z")
        self.assertEqual(list_at("workday", "2026-07-02T15:00:00Z", None, "2026-07-01", None), "2026-07-01T04:00:00Z")
        # A date that is later than the first sighting is capped there too.
        self.assertEqual(list_at("workable", "2026-11-09T03:00:00Z", None, "2026-11-09", None), "2026-11-09T03:00:00Z")

    def test_list_at_lever_date_not_trusted(self):
        self.assertEqual(list_at("lever", "2026-11-09T15:00:00Z", None, "2025-12-01", 300), "2026-11-09T15:00:00Z")

    def test_list_at_first_read_no_time_is_none(self):
        self.assertIsNone(list_at("lever", "2026-11-09T15:00:00Z", None, "2025-12-01", None))
        self.assertIsNone(list_at("rippling", "2026-11-09T15:00:00Z", None, None, None))
        # A gap of zero seconds is still a gap: the board had been read.
        self.assertEqual(list_at("rippling", "2026-11-09T15:00:00Z", None, None, 0), "2026-11-09T15:00:00Z")


class RepostKeyTests(unittest.TestCase):
    def test_repost_key_ignores_case_and_punctuation(self):
        self.assertEqual(repost_key("Software Engineer, New Grad (2026)"),
                         repost_key("software engineer new grad 2026"))
        self.assertEqual(repost_key("  Software   Engineer -- II  "), "software engineer ii")
        self.assertNotEqual(repost_key("Software Engineer I"), repost_key("Software Engineer II"))


class FindRepostTests(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.company = seed_company(self.conn, "Acme")

    def posting(self, title, *, company=None, closed_days_ago=None, first_seen="2026-06-01T12:00:00Z"):
        closed = None if closed_days_ago is None else add_seconds(NOW, -closed_days_ago * 86400)
        return self.conn.execute(
            "INSERT INTO posting(company_id, source_key, title, first_seen_at, last_seen_at, closed_at, close_reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (company or self.company, f"url:{next(_keys)}", title, first_seen, first_seen, closed,
             None if closed is None else "gone_from_board")).lastrowid

    def test_find_repost_within_120_days(self):
        pid = self.posting("Software Engineer, New Grad (2026)", closed_days_ago=120)
        self.assertEqual(find_repost(self.conn, self.company, "software engineer new grad 2026", NOW), pid)

    def test_find_repost_ignores_121_days(self):
        self.posting("Software Engineer", closed_days_ago=121)
        self.assertIsNone(find_repost(self.conn, self.company, "Software Engineer", NOW))

    def test_find_repost_other_company_ignored(self):
        other = seed_company(self.conn, "Other")
        self.posting("Software Engineer", company=other, closed_days_ago=3)
        self.assertIsNone(find_repost(self.conn, self.company, "Software Engineer", NOW))

    def test_find_repost_open_posting_ignored(self):
        self.posting("Software Engineer")
        self.assertIsNone(find_repost(self.conn, self.company, "Software Engineer", NOW))

    def test_find_repost_follows_a_merge(self):
        old = seed_company(self.conn, "Acme Inc")
        pid = self.posting("Software Engineer", company=old, closed_days_ago=10)
        self.conn.execute("UPDATE company SET status = 'removed', remove_reason = 'merged', merged_into = ? "
                          "WHERE company_id = ?",
                          (self.company, old))
        self.assertEqual(find_repost(self.conn, self.company, "Software Engineer", NOW), pid)
        # Asked from the merged-away id, the answer is the same.
        self.assertEqual(find_repost(self.conn, old, "Software Engineer", NOW), pid)

    def test_find_repost_takes_the_newest(self):
        self.posting("Software Engineer", closed_days_ago=90, first_seen="2026-05-01T12:00:00Z")
        newest = self.posting("Software Engineer", closed_days_ago=30, first_seen="2026-07-01T12:00:00Z")
        self.posting("Software Engineer II", closed_days_ago=5, first_seen="2026-10-01T12:00:00Z")
        self.assertEqual(find_repost(self.conn, self.company, "Software Engineer", NOW), newest)


if __name__ == "__main__":
    unittest.main()
