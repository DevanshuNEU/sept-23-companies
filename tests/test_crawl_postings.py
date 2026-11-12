"""Posting diff and writes (issue #21, Task 8): new, changed, present, reopened and gone postings."""
import sqlite3
import unittest

from outreach.crawl.base import JobRef
from outreach.crawl.boardstate import load_board
from outreach.crawl.postings import (
    DiffPlan, Existing, close_postings, load_existing, native_id_of, plan_diff, source_key, write_postings)
from tests.storetest import fresh_store, seed_board, seed_company

T1 = "2026-10-20T08:00:00Z"
T2 = "2026-10-21T08:00:00Z"
T3 = "2026-10-22T08:00:00Z"


def job(native_id: str, title: str = "Software Engineer", *, location: str = "Boston, MA", country: str | None = "US",
        url: str | None = None, posted_on: str | None = None, posted_at: str | None = None) -> JobRef:
    return JobRef(native_id, title, url or f"https://example.com/{native_id}", location, country, posted_on, None,
                  posted_at)


class PostingTests(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.company = seed_company(self.conn)
        self.board_id = seed_board(self.conn, self.company)
        self.board = load_board(self.conn, self.board_id)

    def sync(self, jobs, now, batch_rows=2000):
        """One ok read: diff, write, close the gone. Returns the plan and the id map."""
        plan = plan_diff(load_existing(self.conn, self.board_id), jobs, "greenhouse", "acme")
        ids = write_postings(self.conn, self.board, plan, now, batch_rows=batch_rows)
        close_postings(self.conn, plan.gone, "gone_from_board", now, batch_rows=batch_rows)
        return plan, ids

    def row(self, native_id):
        return self.conn.execute("SELECT * FROM posting WHERE source_key = ?",
                                 (source_key("greenhouse", "acme", native_id),)).fetchone()

    def test_source_key_round_trips(self):
        key = source_key("workday", "acme.wd5/Careers", "R-1:2")
        self.assertEqual(key, "workday:acme.wd5/Careers:R-1:2")
        self.assertEqual(native_id_of(key, "workday", "acme.wd5/Careers"), "R-1:2")
        with self.assertRaises(ValueError):
            native_id_of("lever:acme:1", "greenhouse", "acme")

    def test_new_posting_is_inserted_with_board_and_company(self):
        plan, ids = self.sync([job("1", posted_on="2026-10-01")], T1)
        self.assertEqual([j.native_id for j in plan.new], ["1"])
        r = self.row("1")
        self.assertEqual(ids, {"1": r["posting_id"]})
        self.assertEqual((r["board_id"], r["company_id"], r["title"], r["location"], r["country"], r["url"],
                          r["posted_on"], r["first_seen_at"], r["last_seen_at"], r["closed_at"]),
                         (self.board_id, self.company, "Software Engineer", "Boston, MA", "US",
                          "https://example.com/1", "2026-10-01", T1, T1, None))
        self.assertFalse(self.conn.in_transaction)

    def test_a_second_write_of_the_same_plan_inserts_nothing_new(self):
        plan = plan_diff(load_existing(self.conn, self.board_id), [job("1")], "greenhouse", "acme")
        first = write_postings(self.conn, self.board, plan, T1, batch_rows=2000)
        again = write_postings(self.conn, self.board, plan, T2, batch_rows=2000)
        self.assertEqual(first, again)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM posting").fetchone()[0], 1)
        self.assertEqual(self.row("1")["first_seen_at"], T1)

    def test_present_posting_last_seen_is_touched(self):
        self.sync([job("1")], T1)
        plan, ids = self.sync([job("1")], T2)
        r = self.row("1")
        self.assertEqual((plan.new, plan.changed, plan.present), ((), (), (r["posting_id"],)))
        self.assertEqual((r["first_seen_at"], r["last_seen_at"]), (T1, T2))
        self.assertEqual(ids, {"1": r["posting_id"]})

    def test_changed_title_updates_the_row_and_is_listed_changed(self):
        self.sync([job("1"), job("2")], T1)
        pid = self.row("1")["posting_id"]
        moved = job("1", "Senior Software Engineer", location="Remote", country=None, url="https://example.com/x")
        plan, _ = self.sync([moved, job("2")], T2)
        self.assertEqual(plan.changed, ((pid, moved),))
        r = self.row("1")
        self.assertEqual((r["title"], r["location"], r["country"], r["url"], r["last_seen_at"]),
                         ("Senior Software Engineer", "Remote", None, "https://example.com/x", T2))
        for field in ({"location": "NYC"}, {"country": "CA"}, {"url": "https://example.com/y"}):
            existing = load_existing(self.conn, self.board_id)
            changed = job("2", **field)
            self.assertEqual(plan_diff(existing, [changed], "greenhouse", "acme").changed,
                             ((self.row("2")["posting_id"], changed),), field)

    def test_gone_posting_is_in_gone_and_close_sets_reason(self):
        self.sync([job("1"), job("2")], T1)
        pid = self.row("2")["posting_id"]
        plan = plan_diff(load_existing(self.conn, self.board_id), [job("1")], "greenhouse", "acme")
        self.assertEqual(plan.gone, (pid,))
        write_postings(self.conn, self.board, plan, T2, batch_rows=2000)
        self.assertEqual(close_postings(self.conn, plan.gone, "gone_from_board", T2, batch_rows=2000), 1)
        r = self.row("2")
        self.assertEqual((r["closed_at"], r["close_reason"], r["last_seen_at"]), (T2, "gone_from_board", T1))
        # Closing again changes nothing: the first close stands.
        self.assertEqual(close_postings(self.conn, [pid], "board_empty_twice", T3, batch_rows=2000), 0)
        self.assertEqual(self.row("2")["close_reason"], "gone_from_board")
        # A closed posting that is still not listed is not gone again.
        self.assertEqual(plan_diff(load_existing(self.conn, self.board_id), [job("1")], "greenhouse", "acme").gone, ())

    def test_hand_closed_posting_stays_closed_when_listed(self):
        self.sync([job("1"), job("2")], T1)
        for nid, reason in (("1", "hand"), ("2", "page_closed")):
            self.conn.execute("UPDATE posting SET closed_at = ?, close_reason = ? WHERE source_key = ?",
                              (T1, reason, source_key("greenhouse", "acme", nid)))
        plan, ids = self.sync([job("1", "Renamed"), job("2")], T2)
        self.assertEqual((plan.new, plan.changed, plan.present, plan.reopen, plan.gone), ((), (), (), (), ()))
        self.assertEqual(ids, {})
        for nid in ("1", "2"):
            r = self.row(nid)
            self.assertIsNotNone(r["closed_at"])
            self.assertEqual((r["title"], r["last_seen_at"]), ("Software Engineer", T1))

    def test_a_posting_closed_on_import_reopens_when_listed(self):
        # The old system's close was its crawler's, so a board listing the job again overrules it (2026-11-12: Amazon
        # listed 975 jobs the import had closed, among them "Software Development Engineer, Early Career 2027").
        self.sync([job("1"), job("2")], T1)
        self.conn.execute("UPDATE posting SET closed_at = ?, close_reason = 'imported_closed' WHERE source_key = ?",
                          (T1, source_key("greenhouse", "acme", "1")))
        pid = self.row("1")["posting_id"]
        plan, ids = self.sync([job("1", "Renamed"), job("2")], T2)
        self.assertEqual(plan.reopen, (pid,))
        self.assertIn(pid, plan.present)
        r = self.row("1")
        self.assertEqual((r["closed_at"], r["close_reason"], r["title"], r["last_seen_at"]),
                         (None, None, "Renamed", T2))
        self.assertEqual(ids["1"], pid)

    def test_gone_posting_that_returns_is_reopened(self):
        self.sync([job("1"), job("2")], T1)
        self.sync([job("1")], T2)  # 2 closes gone_from_board
        self.conn.execute("UPDATE posting SET closed_at = ?, close_reason = 'board_empty_twice' WHERE source_key = ?",
                          (T2, source_key("greenhouse", "acme", "1")))
        p1, p2 = self.row("1")["posting_id"], self.row("2")["posting_id"]
        plan, ids = self.sync([job("1"), job("2")], T3)
        self.assertEqual(set(plan.reopen), {p1, p2})
        self.assertEqual(set(plan.present), {p1, p2})
        self.assertEqual(ids, {"1": p1, "2": p2})
        for nid in ("1", "2"):
            r = self.row(nid)
            self.assertEqual((r["closed_at"], r["close_reason"], r["last_seen_at"]), (None, None, T3))

    def test_a_hand_close_made_after_the_diff_is_not_reopened(self):
        self.sync([job("1"), job("2")], T1)
        self.sync([job("1")], T2)
        plan = plan_diff(load_existing(self.conn, self.board_id), [job("1"), job("2")], "greenhouse", "acme")
        self.assertEqual(plan.reopen, (self.row("2")["posting_id"],))
        self.conn.execute("UPDATE posting SET close_reason = 'hand' WHERE source_key = ?",
                          (source_key("greenhouse", "acme", "2"),))
        write_postings(self.conn, self.board, plan, T3, batch_rows=2000)
        self.assertEqual((self.row("2")["close_reason"], self.row("2")["closed_at"]), ("hand", T2))

    def test_posted_on_is_not_overwritten(self):
        self.sync([job("1", posted_on="2026-09-01"), job("2")], T1)
        self.sync([job("1", "New title", posted_on="2026-10-15"), job("2", "New title", posted_on="2026-10-15")], T2)
        self.assertEqual(self.row("1")["posted_on"], "2026-09-01")
        self.assertEqual(self.row("2")["posted_on"], "2026-10-15")  # was NULL, so it is filled

    def test_last_seen_never_moves_back(self):
        self.sync([job("1")], T1)
        self.sync([job("1")], T3)
        self.sync([job("1")], T2)
        r = self.row("1")
        self.assertEqual((r["first_seen_at"], r["last_seen_at"]), (T1, T3))

    def test_a_posting_hand_closed_after_the_diff_is_not_touched(self):
        self.sync([job("1")], T1)
        plan = plan_diff(load_existing(self.conn, self.board_id), [job("1", "Renamed")], "greenhouse", "acme")
        self.assertEqual(len(plan.changed), 1)
        self.conn.execute("UPDATE posting SET closed_at = ?, close_reason = 'hand' WHERE source_key = ?",
                          (T1, source_key("greenhouse", "acme", "1")))
        write_postings(self.conn, self.board, plan, T2, batch_rows=2000)
        r = self.row("1")
        self.assertEqual((r["title"], r["last_seen_at"], r["close_reason"]), ("Software Engineer", T1, "hand"))

    def test_last_seen_never_before_first_seen(self):
        self.sync([job("1")], T2)
        self.sync([job("1")], T1)  # a clock that went back
        r = self.row("1")
        self.assertEqual((r["first_seen_at"], r["last_seen_at"]), (T2, T2))

    def test_an_empty_job_list_is_refused(self):
        self.sync([job("1")], T1)
        with self.assertRaises(ValueError):
            plan_diff(load_existing(self.conn, self.board_id), [], "greenhouse", "acme")
        self.assertIsNone(self.row("1")["closed_at"])

    def test_postings_are_never_deleted(self):
        self.sync([job("1")], T1)
        close_postings(self.conn, [self.row("1")["posting_id"]], "board_empty_twice", T2, batch_rows=2000)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM posting").fetchone()[0], 1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("DELETE FROM posting")

    def test_batches_commit_separately(self):
        statements = []
        self.conn.set_trace_callback(statements.append)
        try:
            _, ids = self.sync([job(str(i)) for i in range(5)], T1, batch_rows=2)
        finally:
            self.conn.set_trace_callback(None)
        self.assertEqual(len(ids), 5)
        self.assertEqual(sum(s.strip().upper() == "COMMIT" for s in statements), 3)
        self.assertEqual(sum(s.strip().upper().startswith("BEGIN IMMEDIATE") for s in statements), 3)

    def test_close_batches_commit_separately(self):
        self.sync([job(str(i)) for i in range(5)], T1)
        ids = [r[0] for r in self.conn.execute("SELECT posting_id FROM posting")]
        statements = []
        self.conn.set_trace_callback(statements.append)
        try:
            closed = close_postings(self.conn, ids, "board_empty_twice", T2, batch_rows=2)
        finally:
            self.conn.set_trace_callback(None)
        self.assertEqual(closed, 5)
        self.assertEqual(sum(s.strip().upper() == "COMMIT" for s in statements), 3)

    def test_no_transaction_is_left_open(self):
        self.sync([job("1"), job("2")], T1)
        self.assertFalse(self.conn.in_transaction)
        self.sync([job("1"), job("3")], T2)
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(close_postings(self.conn, [], "gone_from_board", T3, batch_rows=2000), 0)
        self.assertFalse(self.conn.in_transaction)

    def test_a_listed_id_twice_is_one_posting(self):
        plan, ids = self.sync([job("1"), job("1", "Other")], T1)
        self.assertEqual(len(plan.new), 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM posting").fetchone()[0], 1)

    def test_load_existing_reads_only_this_board(self):
        self.sync([job("1")], T1)
        other = seed_board(self.conn, self.company, token="other")
        self.conn.execute("INSERT INTO posting(company_id, board_id, source_key, title, first_seen_at, last_seen_at) "
                          "VALUES (?, ?, 'greenhouse:other:9', 'X', ?, ?)", (self.company, other, T1, T1))
        existing = load_existing(self.conn, self.board_id)
        self.assertEqual(list(existing), [source_key("greenhouse", "acme", "1")])
        e = existing[source_key("greenhouse", "acme", "1")]
        self.assertIsInstance(e, Existing)
        self.assertEqual((e.title, e.closed_at, e.close_reason), ("Software Engineer", None, None))

    def test_plan_diff_is_pure(self):
        existing = {source_key("greenhouse", "acme", "1"): Existing(5, "A", "", None, "u", None, None),
                    source_key("greenhouse", "acme", "2"): Existing(6, "B", "", None, "u", T1, "gone_from_board")}
        plan = plan_diff(existing, [job("2", "B", location="", country=None, url="u"), job("3")], "greenhouse", "acme")
        self.assertIsInstance(plan, DiffPlan)
        self.assertEqual(([j.native_id for j in plan.new], plan.changed, plan.present, plan.reopen, plan.gone),
                         (["3"], (), (6,), (6,), (5,)))


class ListTimeWriteTests(unittest.TestCase):
    """#193: a new posting keeps its board time, its seen gap, its list time and its repost link; a change never
    moves them."""

    def setUp(self):
        self.conn = fresh_store()
        self.company = seed_company(self.conn)
        self.board_id = seed_board(self.conn, self.company)
        self.board = load_board(self.conn, self.board_id)

    def write(self, jobs, now, seen_gap_s=None):
        plan = plan_diff(load_existing(self.conn, self.board_id), jobs, "greenhouse", "acme")
        write_postings(self.conn, self.board, plan, now, batch_rows=2000, seen_gap_s=seen_gap_s)
        close_postings(self.conn, plan.gone, "gone_from_board", now, batch_rows=2000)

    def row(self, native_id):
        return self.conn.execute("SELECT * FROM posting WHERE source_key = ?",
                                 (source_key("greenhouse", "acme", native_id),)).fetchone()

    def test_a_new_posting_keeps_its_board_time_and_gap(self):
        self.write([job("1", posted_at="2026-10-19T14:00:00Z"), job("2")], T1, seen_gap_s=600)
        one, two = self.row("1"), self.row("2")
        self.assertEqual((one["posted_at"], one["seen_gap_s"], one["list_at"], one["repost_of"]),
                         ("2026-10-19T14:00:00Z", 600, "2026-10-19T14:00:00Z", None))
        self.assertEqual((two["posted_at"], two["seen_gap_s"], two["list_at"]), (None, 600, T1))

    def test_a_first_read_row_has_no_gap(self):
        self.write([job("1", posted_at="2026-03-02T14:00:00Z"), job("2")], T1)
        self.assertEqual((self.row("1")["seen_gap_s"], self.row("1")["list_at"]), (None, "2026-03-02T14:00:00Z"))
        self.assertEqual((self.row("2")["seen_gap_s"], self.row("2")["list_at"]), (None, None))

    def test_a_change_never_moves_the_list_time(self):
        self.write([job("1", posted_at="2026-10-19T14:00:00Z")], T1, seen_gap_s=600)
        self.write([job("1", "Renamed", posted_at="2026-10-21T14:00:00Z")], T2, seen_gap_s=86400)
        r = self.row("1")
        self.assertEqual((r["title"], r["posted_at"], r["seen_gap_s"], r["list_at"]),
                         ("Renamed", "2026-10-19T14:00:00Z", 600, "2026-10-19T14:00:00Z"))

    def test_a_repost_is_linked_only_after_a_good_read(self):
        self.write([job("1", "Software Engineer, New Grad"), job("2", "Data Engineer")], T1)
        self.write([job("2", "Data Engineer")], T2, seen_gap_s=86400)  # 1 is gone
        self.write([job("2", "Data Engineer"), job("3", "software engineer new grad")], T3, seen_gap_s=86400)
        self.assertEqual(self.row("3")["repost_of"], self.row("1")["posting_id"])
        # On a first read nothing is looked up, so nothing is linked.
        other = seed_board(self.conn, self.company, token="other")
        board = load_board(self.conn, other)
        plan = plan_diff(load_existing(self.conn, other), [job("9", "Software Engineer, New Grad")], "greenhouse",
                         "other")
        write_postings(self.conn, board, plan, T3, batch_rows=2000)
        self.assertIsNone(self.conn.execute("SELECT repost_of FROM posting WHERE source_key = ?",
                                            (source_key("greenhouse", "other", "9"),)).fetchone()[0])

if __name__ == "__main__":
    unittest.main()
