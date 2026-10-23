"""Batched decisions for the crawler (issue #21, Task 9)."""
import unittest
from unittest import mock

from outreach.crawl.decisions import decide_postings, reset_provisional
from outreach.rules import Decision, RulesetNotCurrent, ruleset_version
from tests.storetest import fresh_store, seed_company, seed_posting


class DecisionTestCase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.company = seed_company(self.conn)

    def postings(self, n, title="Software Engineer, New Grad", body="We sponsor work visas."):
        return [seed_posting(self.conn, self.company, title, body, "Boston, MA") for _ in range(n)]

    def current_rows(self, ids):
        marks = ", ".join("?" * len(ids))
        return self.conn.execute(f"SELECT * FROM current_decision WHERE posting_id IN ({marks}) ORDER BY posting_id",
                                 ids).fetchall()


class DecidePostingsTests(DecisionTestCase):
    def test_decide_postings_writes_current_ruleset_rows(self):
        ids = self.postings(3)
        got = decide_postings(self.conn, ids, batch=100)
        self.assertEqual(set(got), set(ids))
        self.assertTrue(all(isinstance(d, Decision) for d in got.values()))
        rows = self.current_rows(ids)
        self.assertEqual(len(rows), 3)
        self.assertEqual({r["ruleset_version"] for r in rows}, {ruleset_version()})
        self.assertEqual({r["lane"] for r in rows}, {"engineering"})
        self.assertFalse(self.conn.in_transaction)

    def test_decide_postings_batches(self):
        ids = self.postings(5)
        statements = []
        self.conn.set_trace_callback(statements.append)
        decide_postings(self.conn, ids, batch=2)
        self.conn.set_trace_callback(None)
        self.assertEqual(statements.count("COMMIT"), 3)
        self.assertEqual(statements.count("BEGIN IMMEDIATE"), 3)
        self.assertEqual(len(self.current_rows(ids)), 5)

    def test_every_decision_runs_inside_its_batch_transaction(self):
        ids = self.postings(4)
        seen = []
        real = decide_postings.__globals__["decide_and_store"]

        def spy(conn, posting_id, **kw):
            seen.append(conn.in_transaction)
            return real(conn, posting_id, **kw)

        with mock.patch.dict(decide_postings.__globals__, {"decide_and_store": spy}):
            decide_postings(self.conn, ids, batch=2)
        self.assertEqual(seen, [True] * 4)  # every call runs inside its batch's transaction

    def test_an_empty_list_does_nothing(self):
        self.assertEqual(decide_postings(self.conn, [], batch=100), {})

    def test_a_bad_batch_size_is_refused(self):
        with self.assertRaises(ValueError):
            decide_postings(self.conn, [1], batch=0)

    def test_ruleset_not_current_propagates(self):
        ids = self.postings(2)
        self.conn.execute("UPDATE ruleset SET is_current = 0")
        with self.assertRaises(RulesetNotCurrent):
            decide_postings(self.conn, ids, batch=1)
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM decision").fetchone()[0], 0)

    def test_decide_postings_passes_the_combiner_through(self):
        # decide_and_store takes `combiner` only once #32 lands, so the call is checked against a stand-in.
        ids = self.postings(2)
        combiner = object()
        calls = []

        def fake(conn, posting_id, **kw):
            calls.append((posting_id, kw))
            return Decision("engineering", "new_grad", True, "title", None, "", "yes", "silent", "", False, "", "ok",
                            None, "", False)

        with mock.patch.dict(decide_postings.__globals__, {"decide_and_store": fake}):
            decide_postings(self.conn, ids, batch=1, combiner=combiner, rules_cfg="CFG")
        self.assertEqual(calls, [(ids[0], {"combiner": combiner, "cfg": "CFG"}),
                                 (ids[1], {"combiner": combiner, "cfg": "CFG"})])

    def test_without_a_combiner_the_call_has_no_combiner_argument_and_the_row_is_provisional(self):
        ids = self.postings(1)
        calls = []
        real = decide_postings.__globals__["decide_and_store"]

        def spy(conn, posting_id, **kw):
            calls.append(kw)
            return real(conn, posting_id, **kw)

        with mock.patch.dict(decide_postings.__globals__, {"decide_and_store": spy}):
            decide_postings(self.conn, ids, batch=1)
        self.assertEqual(calls, [{"cfg": None}])
        row = self.current_rows(ids)[0]
        self.assertEqual((row["provisional"], row["reader_version"]), (1, None))

    def final(self, pid):
        self.conn.execute("UPDATE decision SET provisional = 0, reader_version = 'v1' WHERE posting_id = ?", (pid,))

    def state(self, pid):
        return tuple(self.conn.execute("SELECT provisional, reader_version FROM current_decision WHERE posting_id = ?",
                                       (pid,)).fetchone())

    def test_a_location_change_re_decides_provisional_when_there_is_no_combiner(self):
        (pid,) = self.postings(1)
        decide_postings(self.conn, [pid], batch=100)
        self.final(pid)
        self.conn.execute("UPDATE posting SET location = 'Berlin, Germany' WHERE posting_id = ?", (pid,))
        decide_postings(self.conn, [pid], batch=100)
        self.assertEqual(self.state(pid), (1, None))

    def test_a_title_change_re_decides_provisional_when_there_is_no_combiner(self):
        (pid,) = self.postings(1)
        decide_postings(self.conn, [pid], batch=100)
        self.final(pid)
        self.conn.execute("UPDATE posting SET title = 'Account Executive' WHERE posting_id = ?", (pid,))
        got = decide_postings(self.conn, [pid], batch=100)
        self.assertEqual(got[pid].lane, "not_engineering")
        self.assertEqual(self.state(pid), (1, None))

    def test_only_the_batch_postings_are_reset(self):
        a, b = self.postings(2)
        decide_postings(self.conn, [a, b], batch=100)
        self.final(a)
        self.final(b)
        decide_postings(self.conn, [a], batch=100)
        self.assertEqual((self.state(a), self.state(b)), ((1, None), (0, "v1")))

    def test_ids_are_decided_once_each(self):
        ids = self.postings(2)
        calls = []
        real = decide_postings.__globals__["decide_and_store"]

        def spy(conn, posting_id, **kw):
            calls.append(posting_id)
            return real(conn, posting_id, **kw)

        with mock.patch.dict(decide_postings.__globals__, {"decide_and_store": spy}):
            got = decide_postings(self.conn, [ids[0], ids[1], ids[0]], batch=2)
        self.assertEqual(calls, ids)
        self.assertEqual(set(got), set(ids))

    def test_a_combiner_may_leave_a_row_final(self):
        # The combiner (#32) owns the row's provisional and reader_version; with one, nothing here resets them.
        (pid,) = self.postings(1)
        real = decide_postings.__globals__["decide_and_store"]

        def with_combiner(conn, posting_id, **kw):
            decision = real(conn, posting_id, cfg=kw.get("cfg"))
            conn.execute("UPDATE decision SET provisional = 0, reader_version = 'rv' WHERE posting_id = ?",
                         (posting_id,))
            return decision

        with mock.patch.dict(decide_postings.__globals__, {"decide_and_store": with_combiner}):
            decide_postings(self.conn, [pid], batch=1, combiner=object())
        self.assertEqual(self.state(pid), (0, "rv"))


class ResetProvisionalTests(DecisionTestCase):
    def test_reset_provisional_after_a_final_decision(self):
        a, b, c = self.postings(3)
        decide_postings(self.conn, [a, b, c], batch=100)
        self.conn.execute("UPDATE decision SET provisional = 0, reader_version = 'v1' WHERE posting_id IN (?, ?)",
                          (a, b))
        self.assertEqual(reset_provisional(self.conn, [a, c]), 1)  # c was already provisional
        rows = {r["posting_id"]: (r["provisional"], r["reader_version"]) for r in self.current_rows([a, b, c])}
        self.assertEqual(rows, {a: (1, None), b: (0, "v1"), c: (1, None)})
        self.assertFalse(self.conn.in_transaction)

    def test_reset_provisional_touches_only_the_current_ruleset(self):
        (a,) = self.postings(1)
        decide_postings(self.conn, [a], batch=100)
        self.conn.execute(
            "INSERT INTO ruleset(ruleset_version, config_toml, created_at, is_current) "
            "VALUES ('r-000000000001', '', '2026-10-01T00:00:00Z', 0)")
        self.conn.execute(
            "INSERT INTO decision SELECT posting_id, 'r-000000000001', decided_at, lane, level_tier, level_ok, "
            "level_basis, years_asked, years_quote, us, sponsorship, sponsorship_quote, citizenship, "
            "citizenship_quote, eligibility, eligibility_reason, eligibility_quote, start_2027, 0, 'v1' "
            "FROM decision WHERE posting_id = ?", (a,))
        self.assertEqual(reset_provisional(self.conn, [a]), 0)
        old = self.conn.execute("SELECT provisional, reader_version FROM decision WHERE ruleset_version = "
                                "'r-000000000001'").fetchone()
        self.assertEqual(tuple(old), (0, "v1"))

    def test_reset_provisional_of_nothing(self):
        self.assertEqual(reset_provisional(self.conn, []), 0)


if __name__ == "__main__":
    unittest.main()
