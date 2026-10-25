import unittest

from outreach.review.fixes import Fix, apply_fix
from outreach.review.items import clear_item, raise_item
from outreach.review.queries import display_reason, reopened_note, settled_today
from tests import apiseed as s
from tests.storetest import fresh_store

NOW = "2026-11-10T15:00:00Z"
SINCE = "2026-11-05T08:00:00Z"


class QueriesTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.cid = s.company(self.conn, "Acme", "working")

    def row(self, rid):
        return self.conn.execute("SELECT * FROM review_item WHERE review_id = ?", (rid,)).fetchone()

    def board_item(self, kind, board, key=SINCE, reason="The Ashby board returned no jobs once.", created=SINCE):
        return raise_item(self.conn, kind=kind, company_id=self.cid, board_id=board, reason=reason, evidence_key=key,
                          raised_by="crawler", now=created).review_id

    def test_display_reason_for_a_board_is_built_from_live_state(self):
        board = s.board(self.conn, self.cid, "ashby", "acme", last_read="empty", last_read_at="2026-11-06T08:00:00Z",
                        empty_streak=2)
        self.conn.execute("UPDATE board SET streak_since = ? WHERE board_id = ?", (SINCE, board))
        for _ in range(3):
            s.posting(self.conn, self.cid, board_id=board, closed_at="2026-11-06T08:00:00Z",
                      close_reason="board_empty_twice")
        s.posting(self.conn, self.cid, board_id=board, closed_at="2026-11-01T08:00:00Z",
                  close_reason="board_empty_twice")  # an older run, before this streak
        rid = self.board_item("board_empty", board)
        text = display_reason(self.conn, self.row(rid))
        self.assertIn("twice in a row, so its 3 postings were closed", text)
        self.assertNotEqual(text, self.row(rid)["reason"])

    def test_display_reason_for_an_unreadable_board_names_the_error_and_the_last_good_read(self):
        board = s.board(self.conn, self.cid, "greenhouse", "acme", last_read="error",
                        last_read_at="2026-11-07T08:00:00Z", error_streak=3)
        self.conn.execute("UPDATE board SET streak_since = ?, last_error = 'HTTP 503' WHERE board_id = ?",
                          (SINCE, board))
        run = self.conn.execute("INSERT INTO job_run(job, started_at) VALUES ('read_boards', ?)",
                                ("2026-11-01T08:00:00Z",)).lastrowid
        self.conn.execute("INSERT INTO board_read(board_id, run_id, at, result, postings) VALUES (?, ?, ?, 'ok', 1)",
                          (board, run, "2026-11-01T08:00:00Z"))
        s.posting(self.conn, self.cid, board_id=board)
        rid = self.board_item("board_unreadable", board, reason="The board failed.")
        self.assertEqual(display_reason(self.conn, self.row(rid)),
                         "The Greenhouse board has answered with an error on every read since Nov 5 (HTTP 503). "
                         "An error never closes a company, so its 1 posting stays open with what we last saw on "
                         "Nov 1.")

    def test_display_reason_keeps_the_stored_reason_for_other_kinds(self):
        rid = raise_item(self.conn, kind="size_unknown", company_id=self.cid, reason="Nobody has its size.",
                         evidence_key="no-size", raised_by="daily_list").review_id
        self.assertEqual(display_reason(self.conn, self.row(rid)), "Nobody has its size.")

    def test_reopened_note_names_what_changed(self):
        board = s.board(self.conn, self.cid, "ashby", "acme", last_read="error", error_streak=3)
        old = self.board_item("board_unreadable", board, key="2026-10-20T08:00:00Z", created="2026-10-20T08:00:00Z")
        apply_fix(self.conn, old, Fix("ok"), revision=1, now="2026-10-21T15:00:00Z")
        new = self.board_item("board_unreadable", board, key=SINCE)
        self.assertEqual(reopened_note(self.conn, self.row(new)),
                         {"previous": "you marked it ok on Oct 21",
                          "changed": "a new run of failed reads started Nov 5"})

    def test_reopened_note_for_sponsorship_matches_without_the_posting(self):
        first = s.posting(self.conn, self.cid)
        second = s.posting(self.conn, self.cid)
        old = raise_item(self.conn, kind="sponsorship_unclear", company_id=self.cid, posting_id=first, reason="x.",
                         evidence_key="aaaa", raised_by="rules", now="2026-11-01T08:00:00Z").review_id
        clear_item(self.conn, old, "Gone.", "2026-11-02T08:00:00Z")
        new = raise_item(self.conn, kind="sponsorship_unclear", company_id=self.cid, posting_id=second, reason="y.",
                         evidence_key="bbbb", raised_by="rules", now=NOW).review_id
        self.assertEqual(reopened_note(self.conn, self.row(new)),
                         {"previous": "it cleared itself on Nov 2", "changed": "the posting wording changed"})

    def test_reopened_note_is_none_for_a_first_item(self):
        rid = raise_item(self.conn, kind="size_unknown", company_id=self.cid, reason="x.", evidence_key="no-size",
                         raised_by="daily_list").review_id
        self.assertIsNone(reopened_note(self.conn, self.row(rid)))
        other = s.company(self.conn, "Other")
        done = raise_item(self.conn, kind="size_unknown", company_id=other, reason="x.", evidence_key="old",
                          raised_by="daily_list", now="2026-11-01T08:00:00Z").review_id
        apply_fix(self.conn, done, Fix("ok"), revision=1, now=NOW)
        self.assertIsNone(reopened_note(self.conn, self.row(rid)))  # another company's item is no sibling

    def test_settled_today_counts_his_three_resolutions_by_new_york_date_and_not_cleared_ones(self):
        def resolved(resolution, at, key):
            return s._insert(self.conn, "review_item", {
                "kind": "flagged", "company_id": self.cid, "reason": "x.", "evidence_key": key,
                "raised_by": "claude", "state": "resolved", "resolution": resolution, "resolved_at": at,
                "created_at": "2026-11-01T08:00:00Z"})
        resolved("marked_ok", "2026-11-10T05:00:00Z", "a")  # Nov 10, 00:00 in New York
        resolved("marked_no_go", "2026-11-10T20:00:00Z", "b")
        resolved("field_edited", "2026-11-11T04:59:59Z", "c")  # still Nov 10 in New York
        resolved("cleared", "2026-11-10T16:00:00Z", "d")
        resolved("marked_ok", "2026-11-10T04:59:59Z", "e")  # Nov 9 in New York
        resolved("marked_ok", "2026-11-11T05:00:00Z", "f")  # Nov 11
        self.assertEqual(settled_today(self.conn, NOW), 3)
        self.assertEqual(settled_today(self.conn, "2026-11-11T03:00:00Z"), 3)  # 22:00 on Nov 10 in New York


if __name__ == "__main__":
    unittest.main()
