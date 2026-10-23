"""The reader's queue: eligibility, priorities and leases (issue #32, Task 2)."""
import json
import unittest
from dataclasses import replace

from outreach.reader import queue
from outreach.reader.config import load_reader_config
from outreach.reader.types import ReadRequest
from outreach.reader.version import reader_version
from outreach.store import body_sha256
from tests.ranking_store import decision
from tests.storetest import fresh_store, seed_company, seed_posting

VERSION = "rv-000000000001"
T0 = "2026-10-23T08:00:00Z"


def body_of(length: int = 600, seed: str = "We build software.") -> str:
    return (seed + " ") * (length // (len(seed) + 1) + 1)


class QueueCase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.cfg = load_reader_config()
        self.company = seed_company(self.conn, "Acme")

    def posting(self, title="Software Engineer", body=None, *, company=None, location="Austin, TX", lane="engineering",
                decide=True, **fields) -> int:
        body = body_of() if body is None else body
        pid = seed_posting(self.conn, company or self.company, title, body, location)
        if decide:
            decision(self.conn, pid, lane=lane, **fields)
        return pid

    def rows(self, posting_id=None):
        sql = "SELECT * FROM read_queue" + ("" if posting_id is None else " WHERE posting_id = ?")
        return self.conn.execute(sql + " ORDER BY posting_id, enqueued_at, body_sha256",
                                 () if posting_id is None else (posting_id,)).fetchall()

    def row(self, posting_id, version=VERSION):
        return self.conn.execute("SELECT * FROM read_queue WHERE posting_id = ? AND reader_version = ?",
                                 (posting_id, version)).fetchone()

    def enqueue(self, posting_id, **kw):
        return queue.enqueue(self.conn, posting_id, cfg=self.cfg, version=VERSION, **kw)

    def batch(self, limit, now=T0, **kw):
        return queue.next_batch(self.conn, limit, cfg=kw.pop("cfg", self.cfg), version=VERSION, now=now, **kw)

    def score(self, company, score, *, hide=None, version="rk-1", at="2026-10-22T08:00:00Z"):
        signals = {"hide": hide} if hide is not None else {}
        self.conn.execute(
            "INSERT INTO company_score(company_id, ranker_version, score, signals, lanes, scored_at) "
            "VALUES (?, ?, ?, ?, '[]', ?)", (company, version, score, json.dumps(signals), at))


class EnqueueTests(QueueCase):
    def test_enqueue_adds_one_row_per_body_hash(self):
        pid = self.posting()
        self.assertTrue(self.enqueue(pid))
        self.assertFalse(self.enqueue(pid))
        (row,) = self.rows()
        self.assertEqual(row["state"], "queued")
        self.assertRegex(row["body_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(row["body_sha256"], body_sha256(body_of()))
        self.assertEqual((row["reader_version"], row["attempts"], row["lease_until"]), (VERSION, 0, None))

    def test_enqueue_defaults_to_the_files_version(self):
        pid = self.posting()
        self.assertTrue(queue.enqueue(self.conn, pid))
        self.assertEqual(self.rows()[0]["reader_version"], reader_version())

    def test_body_change_requeues(self):
        pid = self.posting()
        self.assertTrue(self.enqueue(pid))
        self.conn.execute("UPDATE posting_body SET body = ? WHERE posting_id = ?", (body_of(seed="Now we build more."), pid))
        self.assertTrue(self.enqueue(pid))
        rows = self.rows(pid)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({r["body_sha256"] for r in rows}), 2)

    def test_a_body_that_only_changes_its_whitespace_is_not_a_new_key(self):
        pid = self.posting(body="We build software.   " * 40)
        self.assertTrue(self.enqueue(pid))
        self.conn.execute("UPDATE posting_body SET body = ? WHERE posting_id = ?", (("We build software.\n" * 40) + "\n", pid))
        self.assertFalse(self.enqueue(pid))

    def test_short_or_empty_body_is_never_enqueued(self):
        short = self.posting(body="x" * 499)
        empty = self.posting(body="")
        self.assertFalse(self.enqueue(short))
        self.assertFalse(self.enqueue(empty))
        exact = self.posting(body="x" * 500)
        self.assertEqual(queue.enqueue_missing(self.conn, cfg=self.cfg, version=VERSION), 1)
        self.assertEqual([r["posting_id"] for r in self.rows()], [exact])

    def test_closed_or_off_lane_posting_is_not_enqueued(self):
        closed = self.posting()
        self.conn.execute("UPDATE posting SET closed_at = '2026-10-22T00:00:00Z', close_reason = 'hand' "
                          "WHERE posting_id = ?", (closed,))
        off = self.posting("Account Executive", lane="off_lane")
        title_lane = self.posting("Recruiter", lane="not_engineering")
        general = self.posting("General Application", lane="general_application")
        undecided = self.posting(decide=False)
        for pid in (closed, off, title_lane, general, undecided):
            self.assertFalse(self.enqueue(pid), pid)
        self.assertEqual(queue.enqueue_missing(self.conn, cfg=self.cfg, version=VERSION), 0)
        self.assertEqual(self.rows(), [])

    def test_an_intern_posting_is_enqueued(self):
        pid = self.posting("Software Engineering Intern", lane="intern", level_tier="not_stated")
        self.assertTrue(self.enqueue(pid))

    def test_an_unknown_posting_is_not_enqueued(self):
        self.assertFalse(self.enqueue(999))

    def test_enqueue_missing_covers_backlog_and_new_version(self):
        a, b, c = (self.posting() for _ in range(3))
        self.assertTrue(self.enqueue(b))
        self.assertEqual(queue.enqueue_missing(self.conn, cfg=self.cfg, version=VERSION), 2)
        self.assertEqual(queue.enqueue_missing(self.conn, cfg=self.cfg, version=VERSION), 0)
        self.assertEqual(queue.enqueue_missing(self.conn, cfg=self.cfg, version="rv-000000000002"), 3)
        self.assertEqual(len(self.rows()), 6)
        self.assertEqual({r["posting_id"] for r in self.rows()}, {a, b, c})

    def test_enqueue_missing_requeues_a_changed_body_and_takes_the_newest_under_a_limit(self):
        ids = [self.posting() for _ in range(4)]
        self.assertEqual(queue.enqueue_missing(self.conn, cfg=self.cfg, version=VERSION, limit=2), 2)
        self.assertEqual({r["posting_id"] for r in self.rows()}, set(ids[2:]))
        self.conn.execute("UPDATE posting_body SET body = ? WHERE posting_id = ?", (body_of(seed="Changed text."), ids[3]))
        self.assertEqual(queue.enqueue_missing(self.conn, cfg=self.cfg, version=VERSION), 3)

    def test_enqueue_writes_no_open_transaction(self):
        self.enqueue(self.posting())
        queue.enqueue_missing(self.conn, cfg=self.cfg, version=VERSION)
        self.assertFalse(self.conn.in_transaction)


class PriorityTests(QueueCase):
    def order(self, limit=10, **kw):
        return [r.posting_id for r in self.batch(limit, **kw)]

    def enqueue_all(self, *ids):
        for pid in ids:
            self.assertTrue(self.enqueue(pid))

    def test_priority_order(self):
        top, low = seed_company(self.conn, "Top"), seed_company(self.conn, "Low")
        plain = seed_company(self.conn, "Plain")
        self.score(top, 0.9)
        self.score(low, 0.2)
        d = self.posting(company=plain, level_tier="senior")        # older ordinary
        e = self.posting(company=plain, level_tier="senior")        # newer ordinary
        c = self.posting(company=low)                               # lane 1, lower company
        b = self.posting(company=top)                               # lane 1, top company
        a = self.posting(company=plain, sponsorship="no")           # provisional hide
        self.enqueue_all(d, e, c, b, a)
        self.assertEqual(self.order(5), [a, b, c, e, d])
        self.assertEqual(self.row(a)["priority"], 3_000_000)
        self.assertEqual(self.row(b)["priority"], 2_000_000 + 900)
        self.assertEqual(self.row(c)["priority"], 2_000_000 + 200)
        self.assertEqual((self.row(d)["priority"], self.row(e)["priority"]), (1_000_000, 1_000_000))

    def test_priority_without_company_score_puts_lane_one_above_the_rest(self):
        plain = self.posting(level_tier="senior")
        lane_one_old = self.posting()
        lane_one_new = self.posting()
        newest_plain = self.posting(level_tier="senior")
        self.enqueue_all(plain, lane_one_old, lane_one_new, newest_plain)
        self.assertEqual(self.order(4), [lane_one_new, lane_one_old, newest_plain, plain])
        self.assertEqual(self.row(lane_one_new)["priority"], 2_000_000)

    def test_lane_one_needs_a_level_that_fits(self):
        no_level = self.posting(level_tier="not_stated")
        lane_one = self.posting()
        self.enqueue_all(no_level, lane_one)
        self.assertEqual(self.order(2), [lane_one, no_level])

    def test_a_posting_whose_company_has_no_score_among_scored_ones_is_ordinary(self):
        scored, unscored = seed_company(self.conn, "Scored"), seed_company(self.conn, "Unscored")
        self.score(scored, 0.4)
        a = self.posting(company=unscored)
        b = self.posting(company=scored)
        self.enqueue_all(a, b)
        self.assertEqual(self.order(2), [b, a])
        self.assertEqual(self.row(a)["priority"], 1_000_000)

    def test_only_the_top_companies_count_as_top(self):
        companies = [seed_company(self.conn, f"C{i}") for i in range(3)]
        for company, score in zip(companies, (0.9, 0.5, 0.1)):
            self.score(company, score)
        ids = [self.posting(company=c) for c in companies]
        self.enqueue_all(*ids)
        queue.refresh_priorities(self.conn, cfg=replace(self.cfg, top_companies=2))
        self.assertEqual([self.row(p)["priority"] for p in ids], [2_000_900, 2_000_500, 1_000_000])

    def test_points_are_clamped(self):
        huge, negative = seed_company(self.conn, "Huge"), seed_company(self.conn, "Negative")
        self.score(huge, 5000.0)
        self.score(negative, -3.0)
        a, b = self.posting(company=huge), self.posting(company=negative)
        self.enqueue_all(a, b)
        queue.refresh_priorities(self.conn, cfg=self.cfg)
        self.assertEqual(self.row(a)["priority"], 2_000_000 + 999_999)
        self.assertEqual(self.row(b)["priority"], 2_000_000)

    def test_the_newest_scored_version_is_the_one_read(self):
        old, new = seed_company(self.conn, "Old"), seed_company(self.conn, "New")
        self.score(old, 0.9, version="rk-1", at="2026-10-20T08:00:00Z")
        self.score(new, 0.1, version="rk-1", at="2026-10-20T08:00:00Z")
        self.score(old, 0.1, version="rk-2", at="2026-10-22T08:00:00Z")
        self.score(new, 0.8, version="rk-2", at="2026-10-22T08:00:00Z")
        a, b = self.posting(company=old), self.posting(company=new)
        self.enqueue_all(a, b)
        self.assertEqual(self.order(2), [b, a])

    def test_a_merged_company_takes_its_survivors_score(self):
        survivor, other = seed_company(self.conn, "Survivor"), seed_company(self.conn, "Other")
        gone = seed_company(self.conn, "Gone")
        self.conn.execute("UPDATE company SET status = 'removed', remove_reason = 'merged', merged_into = ? "
                          "WHERE company_id = ?", (survivor, gone))
        self.score(survivor, 0.7)
        self.score(other, 0.3)
        a = self.posting(company=other)
        b = self.posting(company=gone)
        self.enqueue_all(a, b)
        queue.refresh_priorities(self.conn, cfg=self.cfg)
        self.assertEqual((self.row(a)["priority"], self.row(b)["priority"]), (2_000_300, 2_000_700))

    def test_an_intern_posting_that_shows_is_not_a_provisional_hide(self):
        intern = self.posting("Software Engineering Intern", lane="intern", level_tier="not_stated")
        hidden_intern = self.posting("Software Engineering Intern", lane="intern", level_tier="not_stated",
                                     sponsorship="no")
        self.enqueue_all(intern, hidden_intern)
        queue.refresh_priorities(self.conn, cfg=self.cfg)
        self.assertEqual(self.row(intern)["priority"], 1_000_000)
        self.assertEqual(self.row(hidden_intern)["priority"], 3_000_000)

    def test_a_final_decision_is_not_a_provisional_hide(self):
        final = self.posting(sponsorship="no", provisional=0)
        self.enqueue_all(final)
        queue.refresh_priorities(self.conn, cfg=self.cfg)
        self.assertEqual(self.row(final)["priority"], 2_000_000)

    def test_refresh_counts_what_changed_and_leaves_leased_rows_alone(self):
        a, b = self.posting(), self.posting()
        self.enqueue_all(a, b)
        self.assertEqual(queue.refresh_priorities(self.conn, cfg=self.cfg), 2)
        self.assertEqual(queue.refresh_priorities(self.conn, cfg=self.cfg), 0)
        (leased,) = self.batch(1)
        self.score(self.company, 0.5)
        self.assertEqual(queue.refresh_priorities(self.conn, cfg=self.cfg), 1)
        self.assertEqual(self.row(leased.posting_id)["priority"], 2_000_000)

    def test_a_company_with_a_final_hide_is_held_and_takes_no_top_slot(self):
        hidden, shown = seed_company(self.conn, "Hidden"), seed_company(self.conn, "Shown")
        self.score(hidden, 0.99, hide={"kind": "size", "reason": "too big", "provisional": False})
        self.score(shown, 0.10)
        h, s = self.posting(company=hidden), self.posting(company=shown)
        self.enqueue_all(h, s)
        queue.refresh_priorities(self.conn, cfg=replace(self.cfg, top_companies=1))
        self.assertEqual(self.row(h)["priority"], 0)
        self.assertEqual(self.row(s)["priority"], 2_000_100)  # the one slot went to the company he can see
        self.assertEqual(self.order(5), [s])
        self.assertEqual(self.row(h)["state"], "queued")

    def test_a_provisional_hide_on_the_company_does_not_hold_it(self):
        company = seed_company(self.conn, "Maybe")
        self.score(company, 0.5, hide={"kind": "no_sponsorship", "reason": "x", "provisional": True})
        pid = self.posting(company=company)
        self.enqueue_all(pid)
        self.assertEqual(self.order(5), [pid])
        self.assertEqual(self.row(pid)["priority"], 2_000_500)

    def test_a_lifted_hide_puts_the_company_back_in_the_queue(self):
        company = seed_company(self.conn, "Back")
        self.score(company, 0.5, hide={"kind": "size", "reason": "x", "provisional": False})
        pid = self.posting(company=company)
        self.enqueue_all(pid)
        self.assertEqual(self.order(5), [])
        self.conn.execute("UPDATE company_score SET signals = '{}'")
        self.assertEqual(self.order(5), [pid])

    def test_only_the_newest_score_decides_a_hide(self):
        company = seed_company(self.conn, "Once hidden")
        self.score(company, 0.5, hide={"kind": "size", "reason": "x", "provisional": False}, version="rk-1",
                   at="2026-10-20T08:00:00Z")
        self.score(company, 0.5, version="rk-2", at="2026-10-22T08:00:00Z")
        pid = self.posting(company=company)
        self.enqueue_all(pid)
        self.assertEqual(self.order(5), [pid])


class LeaseTests(QueueCase):
    def test_next_batch_leases_and_does_not_double_hand_out(self):
        ids = [self.posting() for _ in range(5)]
        for pid in ids:
            self.enqueue(pid)
        first = self.batch(2)
        self.assertEqual([r.posting_id for r in first], [ids[4], ids[3]])
        second = self.batch(10)
        self.assertEqual([r.posting_id for r in second], [ids[2], ids[1], ids[0]])
        self.assertEqual(self.batch(10), [])
        leased = [self.row(p) for p in ids]
        self.assertTrue(all(r["state"] == "leased" and r["attempts"] == 1 for r in leased))
        self.assertTrue(all(r["lease_until"] == "2026-10-23T08:30:00Z" for r in leased))
        self.assertFalse(self.conn.in_transaction)

    def test_next_batch_returns_what_the_reader_needs(self):
        pid = self.posting("Backend Engineer", body_of(seed="Real text."))
        self.enqueue(pid)
        (req,) = self.batch(1)
        self.assertEqual(req, ReadRequest(pid, body_sha256(body_of(seed="Real text.")), VERSION, "Backend Engineer",
                                          body_of(seed="Real text.")))

    def test_next_batch_only_hands_out_the_asked_version(self):
        pid = self.posting()
        queue.enqueue(self.conn, pid, cfg=self.cfg, version="rv-000000000002")
        self.assertEqual(self.batch(5), [])

    def test_expired_lease_is_handed_out_again(self):
        pid = self.posting()
        self.enqueue(pid)
        self.assertEqual([r.posting_id for r in self.batch(1, now="2026-10-23T08:00:00Z")], [pid])
        self.assertEqual(self.batch(1, now="2026-10-23T08:29:59Z"), [])
        self.assertEqual(self.batch(1, now="2026-10-23T08:30:00Z"), [])  # until is not yet in the past
        again = self.batch(1, now="2026-10-23T08:30:01Z")
        self.assertEqual([r.posting_id for r in again], [pid])
        row = self.row(pid)
        self.assertEqual((row["attempts"], row["state"], row["lease_until"]), (2, "leased", "2026-10-23T09:00:01Z"))

    def test_poison_posting_ends_refused_at_the_attempt_limit(self):
        cfg = replace(self.cfg, max_attempts=3)
        pid, other = self.posting(), self.posting()
        self.enqueue(pid)
        for n, now in enumerate(("2026-10-23T08:00:00Z", "2026-10-23T08:31:00Z", "2026-10-23T09:02:00Z"), start=1):
            self.assertEqual([r.posting_id for r in self.batch(1, now=now, cfg=cfg)], [pid])
            self.assertEqual(self.row(pid)["attempts"], n)
        self.enqueue(other)
        got = self.batch(5, now="2026-10-23T09:40:00Z", cfg=cfg)
        self.assertEqual([r.posting_id for r in got], [other])  # the refused one does not use a slot
        row = self.row(pid)
        self.assertEqual(row["state"], "refused")
        self.assertEqual(row["last_error"], "gave up after 3 leases")
        self.assertIsNone(row["lease_until"])
        self.assertEqual(self.batch(5, now="2026-10-24T09:40:00Z", cfg=cfg)[0].posting_id, other)  # still never again
        self.assertEqual(self.row(pid)["state"], "refused")

    def test_the_attempt_limit_is_taken_from_the_config(self):
        cfg = replace(self.cfg, max_attempts=1)
        pid = self.posting()
        self.enqueue(pid)
        self.assertEqual(len(self.batch(1, cfg=cfg)), 1)
        self.assertEqual(self.batch(1, now="2026-10-23T09:00:00Z", cfg=cfg), [])
        self.assertEqual(self.row(pid)["last_error"], "gave up after 1 leases")

    def test_release_gives_back_the_attempt(self):
        a, b = self.posting(), self.posting()
        self.enqueue(a)
        self.enqueue(b)
        got = self.batch(2)
        queue.release(self.conn, [r for r in got if r.posting_id == a])
        row = self.row(a)
        self.assertEqual((row["state"], row["lease_until"], row["attempts"]), ("queued", None, 0))
        self.assertEqual(self.row(b)["state"], "leased")
        self.assertEqual([r.posting_id for r in self.batch(2)], [a])
        self.assertEqual(self.row(a)["attempts"], 1)

    def test_release_never_goes_below_zero_and_never_revives_a_finished_row(self):
        a, b = self.posting(), self.posting()
        self.enqueue(a)
        self.enqueue(b)
        got = self.batch(2)
        self.conn.execute("UPDATE read_queue SET state = 'done', lease_until = NULL WHERE posting_id = ?", (a,))
        queue.release(self.conn, got)
        queue.release(self.conn, got)  # a second release changes nothing
        self.assertEqual(self.row(a)["state"], "done")
        self.assertEqual((self.row(b)["state"], self.row(b)["attempts"]), ("queued", 0))
        self.conn.execute("UPDATE read_queue SET state = 'leased', lease_until = ?, attempts = 0 WHERE posting_id = ?",
                          (T0, b))
        queue.release(self.conn, got)
        self.assertEqual(self.row(b)["attempts"], 0)

    def test_release_of_nothing_is_fine(self):
        queue.release(self.conn, [])
        self.assertFalse(self.conn.in_transaction)

    def test_a_body_that_changed_since_the_enqueue_is_never_handed_out_under_the_old_key(self):
        pid = self.posting()
        self.enqueue(pid)
        self.conn.execute("UPDATE posting_body SET body = ? WHERE posting_id = ?", (body_of(seed="Newer text."), pid))
        self.assertEqual(self.batch(1), [])
        row = self.row(pid)
        self.assertEqual((row["state"], row["last_error"]), ("refused", "body changed"))
        self.assertTrue(self.enqueue(pid))  # the new body is a new key
        (req,) = self.batch(1)
        self.assertEqual(req.body_sha256, body_sha256(body_of(seed="Newer text.")))

    def test_a_deleted_body_ends_refused_and_the_batch_still_fills(self):
        kept, gone = self.posting(), self.posting()  # gone is the newest, so it is tried first
        self.enqueue(kept)
        self.enqueue(gone)
        self.conn.execute("DELETE FROM posting_body WHERE posting_id = ?", (gone,))
        self.assertEqual([r.posting_id for r in self.batch(1)], [kept])
        self.assertEqual((self.row(gone)["state"], self.row(gone)["last_error"]), ("refused", "no stored body"))

    def test_the_lease_takes_the_write_lock_before_it_reads(self):
        self.enqueue(self.posting())
        statements = []
        self.conn.set_trace_callback(statements.append)
        self.batch(1)
        self.conn.set_trace_callback(None)
        begin = statements.index("BEGIN IMMEDIATE")
        first_read = next(i for i, s in enumerate(statements) if "FROM read_queue q JOIN posting p" in s
                          and "LIMIT" in s)
        self.assertLess(begin, first_read)
        self.assertEqual(statements[-1], "COMMIT")

    def test_two_connections_never_share_a_lease(self):
        from outreach.store import connect
        path = self.conn.execute("PRAGMA database_list").fetchone()[2]
        other = connect(path)
        self.addCleanup(other.close)
        ids = [self.posting() for _ in range(4)]
        for pid in ids:
            self.enqueue(pid)
        a = queue.next_batch(self.conn, 2, cfg=self.cfg, version=VERSION, now=T0)
        b = queue.next_batch(other, 2, cfg=self.cfg, version=VERSION, now=T0)
        self.assertEqual(len({r.posting_id for r in a} | {r.posting_id for r in b}), 4)


class PayloadTests(QueueCase):
    def test_batch_payload_has_location_and_version(self):
        a = self.posting("Backend Engineer", location="Austin, TX")
        b = self.posting("Data Engineer", location="Remote, US")
        self.enqueue(a)
        self.enqueue(b)
        reqs = self.batch(2)
        payload = queue.batch_payload(self.conn, reqs)
        self.assertEqual(payload["reader_version"], VERSION)
        self.assertEqual([p["posting_id"] for p in payload["postings"]], [r.posting_id for r in reqs])
        by_id = {p["posting_id"]: p for p in payload["postings"]}
        self.assertEqual(by_id[a]["location"], "Austin, TX")
        self.assertEqual(by_id[b]["location"], "Remote, US")
        for req in reqs:
            self.assertEqual(by_id[req.posting_id],
                             {"posting_id": req.posting_id, "body_sha256": req.body_sha256,
                              "reader_version": VERSION, "title": req.title, "location": by_id[req.posting_id]["location"],
                              "body": req.body})
        json.dumps(payload)  # plain data: it goes to the model as text

    def test_an_empty_batch_has_no_postings(self):
        self.assertEqual(queue.batch_payload(self.conn, []), {"reader_version": None, "postings": []})

    def test_one_batch_is_one_version(self):
        pid = self.posting()
        reqs = [ReadRequest(pid, "a" * 64, "rv-1", "t", "b"), ReadRequest(pid, "a" * 64, "rv-2", "t", "b")]
        with self.assertRaises(ValueError):
            queue.batch_payload(self.conn, reqs)


if __name__ == "__main__":
    unittest.main()
