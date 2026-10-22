"""Stored bodies and the detail-request backfill (issue #21, Task 9)."""
import unittest

from outreach.crawl.base import JobRef
from outreach.crawl.boardstate import load_board
from outreach.crawl.bodies import fetch_missing_bodies, missing_body_ids, save_body, stored_sha
from outreach.crawl.polite import HostTripped
from outreach.crawl.postings import source_key
from outreach.rules import decide_and_store
from outreach.store import body_sha256
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

    def test_save_body_is_one_statement(self):
        pid = self.posting("1")
        statements = []
        self.conn.set_trace_callback(statements.append)
        save_body(self.conn, pid, "Body")
        self.conn.set_trace_callback(None)
        self.assertEqual(len(statements), 1, statements)

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
        self.assertEqual(got, {a: "Body A", d: "Body D"})  # b raised, c came back empty: both stay missing
        self.assertEqual([j.native_id for _, j in adapter.calls], ["a", "b", "c", "d"])
        self.assertEqual({p for p, _ in missing_body_ids(self.conn, self.board, 10)} & {c}, {c})
        limited = FakeAdapter(self.conn, {"a": "Body A", "b": "Body B", "c": "Body C", "d": "Body D"})
        self.assertEqual(len(fetch_missing_bodies(self.conn, limited, self.board, limit=2)), 2)
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


if __name__ == "__main__":
    unittest.main()
