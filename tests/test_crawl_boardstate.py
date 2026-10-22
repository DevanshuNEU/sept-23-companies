"""Board state writes after a read (issue #21, Task 7): streaks, validators, board_read, review items, events."""
import unittest

from outreach.crawl.base import JobRef, ReadResult, Validators
from outreach.crawl.boardstate import BoardRow, load_board, write_board_state
from outreach.store import start_job
from tests.storetest import fresh_store, seed_board, seed_company

JOB = JobRef("1", "Software Engineer", "https://example.com/1", "Boston, MA", "US", None, None)
OK = ReadResult("ok", (JOB,), Validators("etag-1", "fp-1"), http_status=200)
EMPTY = ReadResult("empty", (), Validators("etag-empty", "fp-empty"), http_status=200)
ERROR = ReadResult("error", (), Validators(), http_status=500, error="HTTP 500")
UNCHANGED = ReadResult("unchanged", (), Validators("etag-1", "fp-1"), http_status=304)


def at(i: int) -> str:
    return f"2026-10-20T{i:02d}:00:00Z"


class BoardStateTests(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.company = seed_company(self.conn)
        self.board = seed_board(self.conn, self.company)
        self.n = 0

    def read(self, result, *, board_id=None, open_postings=0, interval_s=None, run_id=None):
        self.n += 1
        now = at(self.n)
        board = load_board(self.conn, board_id or self.board)
        run = run_id if run_id is not None else start_job(self.conn, "read_boards")
        return write_board_state(self.conn, board, result, run_id=run, now=now, open_postings=open_postings,
                                 next_read_at=at(self.n + 6), interval_s=interval_s)

    def items(self, kind=None):
        sql = "SELECT * FROM review_item WHERE board_id = ?" + (" AND kind = ?" if kind else "") + " ORDER BY review_id"
        return self.conn.execute(sql, (self.board, kind) if kind else (self.board,)).fetchall()

    def test_load_board_reads_a_never_read_board(self):
        b = load_board(self.conn, self.board)
        self.assertIsInstance(b, BoardRow)
        self.assertEqual((b.company_id, b.ats, b.token, b.last_read, b.empty_streak, b.error_streak, b.etag),
                         (self.company, "greenhouse", "acme", None, 0, 0, ""))
        with self.assertRaises(LookupError):
            load_board(self.conn, self.board + 99)

    def test_ok_stores_validators(self):
        s = self.read(OK, interval_s=21600)
        b = load_board(self.conn, self.board)
        self.assertEqual((b.etag, b.ids_fingerprint), ("etag-1", "fp-1"))
        self.assertEqual((b.last_read, b.last_read_at, b.streak_since, b.next_read_at), ("ok", at(1), at(1), at(7)))
        self.assertEqual(b.change_interval_s, 21600)
        self.assertEqual((s.last_read, s.empty_streak, s.error_streak, s.streak_since), ("ok", 0, 0, at(1)))
        row = self.conn.execute("SELECT * FROM board_read WHERE board_id = ?", (self.board,)).fetchone()
        self.assertEqual((row["result"], row["http_status"], row["postings"], row["error"], row["at"]),
                         ("ok", 200, 1, "", at(1)))

    def test_unchanged_keeps_validators_and_clears_streaks(self):
        self.read(OK, interval_s=21600)
        self.read(ERROR)
        self.read(ERROR)
        s = self.read(ReadResult("unchanged", (), Validators(), http_status=304), open_postings=7)
        b = load_board(self.conn, self.board)
        self.assertEqual((b.etag, b.ids_fingerprint), ("etag-1", "fp-1"))
        self.assertEqual((s.empty_streak, s.error_streak), (0, 0))
        self.assertEqual((b.last_read, b.empty_streak, b.error_streak, b.last_error), ("unchanged", 0, 0, ""))
        self.assertEqual(b.change_interval_s, 21600)  # interval_s None leaves it alone
        last = self.conn.execute("SELECT result, postings FROM board_read WHERE board_id = ? ORDER BY at DESC",
                                 (self.board,)).fetchone()
        self.assertEqual(tuple(last), ("unchanged", 7))

    def test_empty_clears_validators(self):
        self.read(OK)
        s = self.read(EMPTY)
        b = load_board(self.conn, self.board)
        self.assertEqual((b.etag, b.ids_fingerprint), ("", ""))
        self.assertEqual((s.last_read, s.empty_streak), ("empty", 1))
        # A second empty read leaves them cleared too, whatever validators it carried.
        self.read(EMPTY)
        b = load_board(self.conn, self.board)
        self.assertEqual((b.etag, b.ids_fingerprint, b.empty_streak), ("", "", 2))

    def test_error_keeps_validators_and_last_error_is_cleared_by_ok(self):
        self.read(OK)
        s = self.read(ERROR)
        b = load_board(self.conn, self.board)
        self.assertEqual((b.etag, b.ids_fingerprint), ("etag-1", "fp-1"))
        self.assertEqual((b.last_read, b.error_streak, b.last_error), ("error", 1, "HTTP 500"))
        self.assertEqual(s.error_streak, 1)
        row = self.conn.execute("SELECT error, postings FROM board_read WHERE board_id = ? AND result = 'error'",
                                (self.board,)).fetchone()
        self.assertEqual(tuple(row), ("HTTP 500", 0))
        self.read(ReadResult("ok", (JOB,), Validators("etag-2", "fp-2"), http_status=200))
        b = load_board(self.conn, self.board)
        self.assertEqual((b.last_error, b.etag, b.ids_fingerprint), ("", "etag-2", "fp-2"))

    def test_board_read_keeps_the_last_ten(self):
        other = seed_board(self.conn, self.company, token="other")
        self.read(OK, board_id=other)
        runs = []
        for _ in range(12):
            run = start_job(self.conn, "read_boards")
            runs.append(run)
            self.read(OK, run_id=run)
        kept = [r[0] for r in self.conn.execute(
            "SELECT run_id FROM board_read WHERE board_id = ? ORDER BY at", (self.board,))]
        self.assertEqual(kept, runs[2:])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM board_read WHERE board_id = ?",
                                           (other,)).fetchone()[0], 1)

    def test_a_second_write_in_the_same_run_keeps_the_first_board_read(self):
        run = start_job(self.conn, "read_boards")
        self.read(OK, run_id=run)
        self.read(ERROR, run_id=run)
        rows = self.conn.execute("SELECT result FROM board_read WHERE board_id = ?", (self.board,)).fetchall()
        self.assertEqual([r[0] for r in rows], ["ok"])

    def test_two_errors_raise_no_item_the_third_does(self):
        self.read(OK)
        self.read(ERROR)
        self.read(ERROR)
        self.assertEqual(self.items(), [])
        s = self.read(ERROR)
        items = self.items()
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual((item["kind"], item["company_id"], item["raised_by"], item["state"]),
                         ("board_unreadable", self.company, "crawler", "open"))
        self.assertEqual(item["evidence_key"], s.streak_since)
        self.assertEqual(s.streak_since, at(2))
        self.assertTrue(item["reason"].strip())
        # A fourth error in the same streak raises nothing new.
        self.read(ERROR)
        self.assertEqual(len(self.items()), 1)

    def test_an_ok_read_clears_the_open_items(self):
        self.read(EMPTY)
        for _ in range(3):
            self.read(ERROR)
        self.assertEqual(sorted(i["kind"] for i in self.items() if i["state"] == "open"),
                         ["board_empty", "board_unreadable"])
        self.read(OK)
        for item in self.items():
            self.assertEqual((item["state"], item["resolution"], item["resolved_at"]), ("resolved", "cleared", at(5)))
            self.assertEqual(item["revision"], 2)

    def test_an_unchanged_read_clears_the_open_items(self):
        self.read(OK)
        for _ in range(3):
            self.read(ERROR)
        self.read(UNCHANGED)
        self.assertEqual([(i["kind"], i["state"]) for i in self.items()], [("board_unreadable", "resolved")])

    def test_an_empty_read_clears_only_board_unreadable(self):
        self.read(EMPTY)
        for _ in range(3):
            self.read(ERROR)
        self.read(EMPTY)
        states = {(i["kind"], i["state"]) for i in self.items()}
        self.assertIn(("board_unreadable", "resolved"), states)
        self.assertIn(("board_empty", "open"), states)

    def test_a_resolved_item_stays_resolved_while_the_same_streak_continues(self):
        for _ in range(3):
            self.read(ERROR)
        self.conn.execute("UPDATE review_item SET state = 'resolved', resolution = 'marked_ok', resolved_at = ? "
                          "WHERE board_id = ?", (at(3), self.board))
        self.read(ERROR)
        items = self.items()
        self.assertEqual([(i["state"], i["resolution"]) for i in items], [("resolved", "marked_ok")])
        # A new streak is new evidence: an ok read, then three more errors, raise a new item.
        self.read(OK)
        self.assertEqual(self.items()[0]["resolution"], "marked_ok")  # cleared only touches open items
        for _ in range(3):
            self.read(ERROR)
        self.assertEqual([i["state"] for i in self.items()], ["resolved", "open"])

    def test_first_empty_raises_board_empty(self):
        self.read(OK)
        s = self.read(EMPTY)
        items = self.items()
        self.assertEqual([(i["kind"], i["raised_by"], i["evidence_key"]) for i in items],
                         [("board_empty", "crawler", s.streak_since)])
        self.read(EMPTY)
        self.assertEqual(len(self.items()), 1)

    def test_an_error_between_empties_raises_no_second_board_empty(self):
        self.read(EMPTY)
        self.read(ERROR)
        self.read(EMPTY)
        self.assertEqual([(i["kind"], i["state"]) for i in self.items()], [("board_empty", "open")])

    def test_a_resolved_board_empty_is_not_raised_again_inside_the_same_empty_run(self):
        self.read(EMPTY)
        self.conn.execute("UPDATE review_item SET state = 'resolved', resolution = 'marked_ok', resolved_at = ? "
                          "WHERE board_id = ?", (at(1), self.board))
        self.read(ERROR)
        self.read(EMPTY)  # the empty run goes on (an error does not reset it): the postings close, no new item
        self.assertEqual([(i["kind"], i["state"]) for i in self.items()], [("board_empty", "resolved")])
        # An ok read ends the run; the next empty starts a new one and raises a new item.
        self.read(OK)
        self.read(EMPTY)
        self.assertEqual([i["state"] for i in self.items()], ["resolved", "open"])

    def test_the_board_empty_reason_says_whether_postings_closed(self):
        from outreach.crawl.boardstate import board_empty_reason
        board = load_board(self.conn, self.board)
        self.assertIn("close if the next read is empty", board_empty_reason(board, 1, at(1)))
        self.assertIn("its postings closed", board_empty_reason(board, 2, at(1)))

    def test_found_by_is_loaded_and_has_no_default(self):
        self.assertEqual(load_board(self.conn, self.board).found_by, "import")
        fields = {f.name: f for f in __import__("dataclasses").fields(BoardRow)}
        self.assertIs(fields["found_by"].default, __import__("dataclasses").MISSING)

    def test_event_only_when_the_state_changed(self):
        for r in (OK, OK, ERROR, ERROR, UNCHANGED, UNCHANGED, OK):
            self.read(r)
        rows = self.conn.execute("SELECT entity, entity_id, company_id, kind, actor, detail FROM event "
                                 "WHERE entity = 'board' AND kind = 'read_state' ORDER BY event_id").fetchall()
        # none to ok, ok to error, error to unchanged; ok and unchanged are one state, so unchanged to ok is none.
        self.assertEqual(len(rows), 3)
        self.assertEqual({(r[0], r[1], r[2], r[3], r[4]) for r in rows},
                         {("board", self.board, self.company, "read_state", "crawler")})
        self.assertIn('"to": "error"', rows[1]["detail"])

    def test_no_transaction_is_left_open(self):
        self.read(OK)
        self.assertFalse(self.conn.in_transaction)

    def test_a_failed_write_changes_nothing(self):
        self.read(OK)
        before = tuple(self.conn.execute("SELECT * FROM board WHERE board_id = ?", (self.board,)).fetchone())
        board = load_board(self.conn, self.board)
        with self.assertRaises(Exception):
            # run 999999 does not exist: the board_read foreign key fails after the board update.
            write_board_state(self.conn, board, ERROR, run_id=999999, now=at(20), open_postings=0,
                              next_read_at=at(21), interval_s=None)
        self.assertFalse(self.conn.in_transaction)
        after = tuple(self.conn.execute("SELECT * FROM board WHERE board_id = ?", (self.board,)).fetchone())
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
