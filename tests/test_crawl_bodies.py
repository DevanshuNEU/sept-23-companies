"""Stored bodies and the detail-request backfill (issue #21, Task 9)."""
import unittest

from outreach.crawl.base import JobRef
from outreach.crawl.boardstate import load_board
from outreach.crawl.bodies import (
    BodyFetch, fetch_bodies, fetch_missing_bodies, missing_body_ids, refetch_body_ids, save_body, stored_sha)
from outreach.crawl.polite import HostTripped
from outreach.crawl.postings import source_key
from outreach.rules import decide_and_store
from outreach.store import body_sha256
from unittest import mock
from tests.storetest import fresh_store, seed_board, seed_company

T = "2026-10-20T08:00:00Z"


class BodyTestCase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.company = seed_company(self.conn)
        self.board_id = seed_board(self.conn, self.company, "workday", "acme.wd5/Careers")
        self.board = load_board(self.conn, self.board_id)

    def posting(self, native_id, title="Software Engineer", *, posted_on=None, first_seen=T, decide=True,
                board_id=None, closed=False):
        cur = self.conn.execute(
            "INSERT INTO posting(company_id, board_id, source_key, title, location, country, url, posted_on, "
            "first_seen_at, last_seen_at, closed_at, close_reason) VALUES (?, ?, ?, ?, 'Boston, MA', 'US', ?, ?, ?, ?, ?, ?)",
            (self.company, board_id or self.board_id, source_key("workday", "acme.wd5/Careers", native_id), title,
             f"https://example.com/{native_id}", posted_on, first_seen, first_seen, T if closed else None,
             "gone_from_board" if closed else None))
        if decide:
            decide_and_store(self.conn, cur.lastrowid)
        return cur.lastrowid

    def has_body(self, posting_id):
        return self.conn.execute("SELECT 1 FROM posting_body WHERE posting_id = ?", (posting_id,)).fetchone() is not None


class StoredShaTests(BodyTestCase):
    def test_stored_sha_is_none_without_a_body(self):
        self.assertIsNone(stored_sha(self.conn, self.posting("1")))

    def test_stored_sha_hashes_an_imported_body_lazily(self):
        pid = self.posting("1")
        self.conn.execute("INSERT INTO posting_body(posting_id, body) VALUES (?, ?)", (pid, "We  sponsor\nvisas."))
        self.assertEqual(self.conn.execute("SELECT body_sha256 FROM posting_body").fetchone()[0], "")
        self.assertEqual(stored_sha(self.conn, pid), body_sha256("We  sponsor\nvisas."))

    def test_stored_sha_returns_the_stored_hash(self):
        pid = self.posting("1")
        sha = save_body(self.conn, pid, "Hello")
        self.assertEqual(stored_sha(self.conn, pid), sha)

    def test_save_body_replaces_text_and_hash(self):
        pid = self.posting("1")
        first = save_body(self.conn, pid, "First body")
        second = save_body(self.conn, pid, "Second body")
        self.assertEqual(first, body_sha256("First body"))
        self.assertEqual(second, body_sha256("Second body"))
        self.assertNotEqual(first, second)
        row = self.conn.execute("SELECT body, body_sha256, fetched_at FROM posting_body WHERE posting_id = ?",
                                (pid,)).fetchall()
        self.assertEqual(len(row), 1)
        self.assertEqual((row[0]["body"], row[0]["body_sha256"]), ("Second body", second))
        self.assertRegex(row[0]["fetched_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertFalse(self.conn.in_transaction)

    def final(self, pid, version="v1"):
        self.conn.execute("UPDATE decision SET provisional = 0, reader_version = ? WHERE posting_id = ?",
                          (version, pid))

    def decision_state(self, pid):
        return tuple(self.conn.execute("SELECT provisional, reader_version FROM current_decision WHERE posting_id = ?",
                                       (pid,)).fetchone())

    def test_a_changed_body_makes_the_final_decision_provisional_in_the_same_unit(self):
        pid = self.posting("1")
        save_body(self.conn, pid, "First body")
        self.final(pid)
        save_body(self.conn, pid, "A different body")
        self.assertEqual(self.decision_state(pid), (1, None))

    def test_a_whitespace_only_body_change_keeps_the_final_decision(self):
        pid = self.posting("1")
        save_body(self.conn, pid, "First  body")
        self.final(pid)
        save_body(self.conn, pid, "First body\n")
        self.assertEqual(self.decision_state(pid), (0, "v1"))

    def test_the_first_body_over_a_final_decision_makes_it_provisional(self):
        pid = self.posting("1")
        self.final(pid)
        save_body(self.conn, pid, "A body arrives")
        self.assertEqual(self.decision_state(pid), (1, None))

    def test_a_failure_after_the_reset_leaves_old_body_and_decision_together(self):
        # The reset and the upsert are one unit: a crash between them can never leave a new body under an old final
        # decision. Here the failure undoes the reset too, so the old body and its decision stay as they were, and the
        # next replace still works.
        pid = self.posting("1")
        save_body(self.conn, pid, "First body")
        self.final(pid)
        real = self.conn

        class Boom(Exception):
            pass

        with mock.patch("outreach.crawl.bodies._upsert_body", side_effect=Boom):
            with self.assertRaises(Boom):
                save_body(real, pid, "A different body")
        self.assertFalse(real.in_transaction)
        self.assertEqual(real.execute("SELECT body FROM posting_body WHERE posting_id = ?", (pid,)).fetchone()[0],
                         "First body")
        self.assertEqual(self.decision_state(pid), (0, "v1"))
        sha = save_body(real, pid, "A different body")
        self.assertEqual(stored_sha(real, pid), sha)
        self.assertEqual(self.decision_state(pid), (1, None))

    def test_save_body_joins_the_callers_transaction(self):
        pid = self.posting("1")
        self.conn.execute("BEGIN IMMEDIATE")
        save_body(self.conn, pid, "Body")
        self.assertTrue(self.conn.in_transaction)
        self.conn.execute("ROLLBACK")
        self.assertIsNone(stored_sha(self.conn, pid))

    def test_whitespace_only_change_keeps_the_hash(self):
        pid = self.posting("1")
        self.assertEqual(save_body(self.conn, pid, "A  b\n c"), save_body(self.conn, pid, "A b c "))


class MissingBodyTests(BodyTestCase):
    def test_missing_body_ids_only_engineering_without_a_body_newest_first(self):
        old = self.posting("old", posted_on="2026-09-01")
        newest = self.posting("new", posted_on="2026-10-15")
        undated_late = self.posting("undated-late", first_seen="2026-10-19T00:00:00Z")
        undated_early = self.posting("undated-early", first_seen="2026-10-01T00:00:00Z")
        intern = self.posting("intern", "Software Engineering Intern, Summer 2027", posted_on="2026-10-10")
        self.posting("sales", "Account Executive", posted_on="2026-10-16")  # not engineering or intern
        has_body = self.posting("has-body", posted_on="2026-10-17")
        save_body(self.conn, has_body, "Already here")
        self.posting("closed", posted_on="2026-10-18", closed=True)
        self.posting("undecided", posted_on="2026-10-19", decide=False)
        other = seed_board(self.conn, self.company, "greenhouse", "other")
        self.posting("elsewhere", posted_on="2026-10-20", board_id=other)
        got = missing_body_ids(self.conn, self.board, 10)
        self.assertEqual(got, [(newest, "new"), (intern, "intern"), (old, "old"), (undated_late, "undated-late"),
                               (undated_early, "undated-early")])

    def test_missing_body_ids_respects_the_limit(self):
        for i in range(5):
            self.posting(str(i), posted_on=f"2026-10-0{i + 1}")
        self.assertEqual([n for _, n in missing_body_ids(self.conn, self.board, 2)], ["4", "3"])


class FakeAdapter:
    ats = "workday"

    def __init__(self, conn, bodies):
        self.conn, self.bodies, self.calls = conn, bodies, []

    def list(self, token, validators):
        raise AssertionError("not used")

    def body(self, token, job):
        assert not self.conn.in_transaction, "a transaction was open during a body request"
        self.calls.append((token, job))
        value = self.bodies[job.native_id]
        if isinstance(value, BaseException):
            raise value
        return value


class FetchMissingBodyTests(BodyTestCase):
    def test_fetch_missing_bodies_respects_limit_and_skips_failures(self):
        a = self.posting("a", posted_on="2026-10-05")
        self.posting("b", posted_on="2026-10-04")
        c = self.posting("c", posted_on="2026-10-03")
        d = self.posting("d", posted_on="2026-10-02")
        adapter = FakeAdapter(self.conn, {"a": "Body A", "b": RuntimeError("boom"), "c": "", "d": "Body D"})
        got = fetch_missing_bodies(self.conn, adapter, self.board, limit=4)
        self.assertEqual(got, BodyFetch({a: "Body A", d: "Body D"}, failed=1))  # b raised, c came back empty: both stay missing
        self.assertEqual([j.native_id for _, j in adapter.calls], ["a", "b", "c", "d"])
        self.assertEqual({p for p, _ in missing_body_ids(self.conn, self.board, 10)} & {c}, {c})
        limited = FakeAdapter(self.conn, {"a": "Body A", "b": "Body B", "c": "Body C", "d": "Body D"})
        self.assertEqual(len(fetch_missing_bodies(self.conn, limited, self.board, limit=2).bodies), 2)
        self.assertEqual(len(limited.calls), 2)

    def test_fetch_missing_bodies_does_not_store_anything(self):
        pid = self.posting("a")
        fetch_missing_bodies(self.conn, FakeAdapter(self.conn, {"a": "Body A"}), self.board, limit=5)
        self.assertFalse(self.has_body(pid))

    def test_the_adapter_gets_a_job_built_from_the_row(self):
        self.posting("a", "Backend Engineer", posted_on="2026-10-05")
        adapter = FakeAdapter(self.conn, {"a": "Body"})
        fetch_missing_bodies(self.conn, adapter, self.board, limit=1)
        token, job = adapter.calls[0]
        self.assertEqual(token, "acme.wd5/Careers")
        self.assertEqual(job, JobRef("a", "Backend Engineer", "https://example.com/a", "Boston, MA", "US",
                                     "2026-10-05", None))

    def test_no_transaction_is_open_during_a_body_request(self):
        self.posting("a")
        adapter = FakeAdapter(self.conn, {"a": "Body"})  # its body() asserts the connection is idle
        fetch_missing_bodies(self.conn, adapter, self.board, limit=1)
        self.assertEqual(len(adapter.calls), 1)
        self.assertFalse(self.conn.in_transaction)

    def test_a_tripped_host_stops_the_backfill(self):
        self.posting("a")
        self.posting("b")
        adapter = FakeAdapter(self.conn, {"a": "Body", "b": HostTripped("myworkdayjobs.com")})
        with self.assertRaises(HostTripped):
            fetch_missing_bodies(self.conn, adapter, self.board, limit=5)
        self.assertEqual(len(adapter.calls), 1)

    def test_bodies_fetched_before_a_tripped_host_are_stored_as_we_go(self):
        a = self.posting("a", posted_on="2026-10-03")
        b = self.posting("b", posted_on="2026-10-02")
        c = self.posting("c", posted_on="2026-10-01")
        adapter = FakeAdapter(self.conn, {"a": "Body A", "b": "Body B", "c": HostTripped("myworkdayjobs.com")})
        with self.assertRaises(HostTripped):
            fetch_missing_bodies(self.conn, adapter, self.board, limit=5,
                                 on_body=lambda pid, text: save_body(self.conn, pid, text))
        self.assertTrue(self.has_body(a) and self.has_body(b))
        self.assertFalse(self.has_body(c))
        self.assertFalse(self.conn.in_transaction)

    def test_on_body_is_called_between_requests_with_no_transaction_open(self):
        self.posting("a")
        self.posting("b")
        idle = []
        adapter = FakeAdapter(self.conn, {"a": "Body A", "b": "Body B"})
        fetch_missing_bodies(self.conn, adapter, self.board, limit=5,
                             on_body=lambda pid, text: idle.append(self.conn.in_transaction))
        self.assertEqual(idle, [False, False])

    def test_failures_are_counted_for_the_caller(self):
        self.posting("a")
        self.posting("b")
        adapter = FakeAdapter(self.conn, {"a": RuntimeError("x"), "b": ValueError("y")})
        self.assertEqual(fetch_missing_bodies(self.conn, adapter, self.board, limit=5), BodyFetch({}, failed=2))

    def test_fetch_bodies_takes_an_explicit_id_list(self):
        a = self.posting("a")
        adapter = FakeAdapter(self.conn, {"a": "Body A"})
        got = fetch_bodies(self.conn, adapter, self.board, [(a, "a")])
        self.assertEqual(got, BodyFetch({a: "Body A"}, failed=0))


NOW = "2026-10-22T12:00:00Z"


class RefetchTests(BodyTestCase):
    def set_age(self, pid, days):
        self.conn.execute(
            "UPDATE posting_body SET fetched_at = strftime('%Y-%m-%dT%H:%M:%SZ', ?, ?) WHERE posting_id = ?",
            (NOW, f"-{days} days", pid))

    def test_a_changed_posting_is_refetched_even_with_a_fresh_body(self):
        changed = self.posting("changed")
        save_body(self.conn, changed, "Old body")
        self.set_age(changed, 1)
        quiet = self.posting("quiet")
        save_body(self.conn, quiet, "Quiet body")
        self.set_age(quiet, 1)
        got = refetch_body_ids(self.conn, self.board, [changed], NOW, 30, 10)
        self.assertEqual(got, [(changed, "changed")])

    def test_a_lane_one_body_is_refetched_after_31_days_not_after_29(self):
        old = self.posting("old", "Software Engineer, New Grad")
        fresh = self.posting("fresh", "Software Engineer, New Grad")
        for pid, days in ((old, 31), (fresh, 29)):
            save_body(self.conn, pid, "Body")
            self.set_age(pid, days)
        self.assertEqual(self.conn.execute("SELECT level_ok FROM current_decision WHERE posting_id = ?",
                                           (old,)).fetchone()[0], 1)
        self.assertEqual(refetch_body_ids(self.conn, self.board, [], NOW, 30, 10), [(old, "old")])

    def test_an_old_body_that_is_not_lane_one_is_left_alone(self):
        senior = self.posting("senior", "Senior Software Engineer")
        intern = self.posting("intern", "Software Engineering Intern, Summer 2027")
        for pid in (senior, intern):
            save_body(self.conn, pid, "Body")
            self.set_age(pid, 60)
        self.assertEqual(refetch_body_ids(self.conn, self.board, [], NOW, 30, 10), [])

    def test_changed_come_first_then_oldest_and_the_limit_applies(self):
        a = self.posting("a", "Software Engineer, New Grad")
        b = self.posting("b", "Software Engineer, New Grad")
        c = self.posting("c")
        for pid, days in ((a, 40), (b, 90), (c, 2)):
            save_body(self.conn, pid, "Body")
            self.set_age(pid, days)
        self.assertEqual(refetch_body_ids(self.conn, self.board, [c], NOW, 30, 10),
                         [(c, "c"), (b, "b"), (a, "a")])
        self.assertEqual(refetch_body_ids(self.conn, self.board, [c], NOW, 30, 2), [(c, "c"), (b, "b")])

    def test_closed_and_other_board_postings_are_not_refetched(self):
        closed = self.posting("closed", "Software Engineer, New Grad", closed=True)
        other = seed_board(self.conn, self.company, "greenhouse", "other")
        elsewhere = self.posting("elsewhere", "Software Engineer, New Grad", board_id=other)
        for pid in (closed, elsewhere):
            save_body(self.conn, pid, "Body")
            self.set_age(pid, 90)
        self.assertEqual(refetch_body_ids(self.conn, self.board, [closed, elsewhere], NOW, 30, 10), [])

    def test_a_changed_posting_without_a_body_is_left_to_the_missing_pass(self):
        pid = self.posting("a")
        self.assertEqual(refetch_body_ids(self.conn, self.board, [pid], NOW, 30, 10), [])


if __name__ == "__main__":
    unittest.main()
