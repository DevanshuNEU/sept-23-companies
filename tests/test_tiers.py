"""Work tiers (#31 Task 8): the stored tier, the effective tier and a promotion no later run takes back."""
import json
import sqlite3
import unittest
from unittest import mock

from outreach.sources.tiers import effective_tier, promote, register_source, set_work_tier
from outreach.store import start_job, transaction
from tests.storetest import fresh_store, seed_company


class TierTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.company = seed_company(self.conn)

    def row(self):
        return self.conn.execute("SELECT * FROM work_tier WHERE company_id = ?", (self.company,)).fetchone()

    def test_set_work_tier_writes_only_on_change(self):
        self.assertTrue(set_work_tier(self.conn, self.company, 3, "excluded: NAICS 5613", "lca-1-aaaa"))
        first = dict(self.row())
        self.assertEqual((first["tier"], first["reason"], first["source_version"], first["promoted"]),
                         (3, "excluded: NAICS 5613", "lca-1-aaaa", 0))
        # the same tier and reason from a newer version writes nothing: the row still names the version that set it
        self.assertFalse(set_work_tier(self.conn, self.company, 3, "excluded: NAICS 5613", "lca-1-bbbb"))
        self.assertEqual(dict(self.row()), first)
        # a new tier, or a new reason, writes
        self.assertTrue(set_work_tier(self.conn, self.company, 1, "excluded: NAICS 5613", "lca-1-bbbb"))
        self.assertEqual((self.row()["tier"], self.row()["source_version"]), (1, "lca-1-bbbb"))
        self.assertTrue(set_work_tier(self.conn, self.company, 1, "low staffing risk", "lca-1-bbbb"))
        self.assertEqual(self.row()["reason"], "low staffing risk")

    def tier_events(self):
        return [(r["actor"], r["entity"], r["entity_id"], json.loads(r["detail"])) for r in self.conn.execute(
            "SELECT * FROM event WHERE kind = 'tier_changed' AND company_id = ? ORDER BY event_id", (self.company,))]

    def test_a_changed_tier_appends_one_event(self):
        set_work_tier(self.conn, self.company, 3, "excluded", "v1")
        self.assertEqual(self.tier_events(), [("source", "company", self.company, {
            "from": None, "to": 3, "reason": "excluded", "version": "v1"})])
        set_work_tier(self.conn, self.company, 1, "cleared", "v2")
        events = self.tier_events()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1][3], {"from": 3, "to": 1, "reason": "cleared", "version": "v2"})

    def test_an_unchanged_rerun_appends_no_event(self):
        set_work_tier(self.conn, self.company, 3, "excluded", "v1")
        set_work_tier(self.conn, self.company, 3, "excluded", "v2")
        self.assertEqual(len(self.tier_events()), 1)

    def test_set_work_tier_alone_is_atomic(self):
        with mock.patch("outreach.sources.tiers.append_event", side_effect=sqlite3.OperationalError("disk")):
            with self.assertRaises(sqlite3.OperationalError):
                set_work_tier(self.conn, self.company, 3, "excluded", "v1")
        self.assertIsNone(self.row())
        self.assertFalse(self.conn.in_transaction)

    def test_promote_alone_is_atomic(self):
        with mock.patch("outreach.sources.tiers.append_event", side_effect=sqlite3.OperationalError("disk")):
            with self.assertRaises(sqlite3.OperationalError):
                promote(self.conn, self.company, "devanshu")
        self.assertIsNone(self.row())  # neither the inserted row nor the flag stays
        self.assertFalse(self.conn.in_transaction)

    def test_inside_a_callers_transaction_a_failure_undoes_only_itself(self):
        other = seed_company(self.conn, "Other")
        with transaction(self.conn):
            set_work_tier(self.conn, other, 2, "kept", "v1")
            with mock.patch("outreach.sources.tiers.append_event", side_effect=sqlite3.OperationalError("disk")):
                with self.assertRaises(sqlite3.OperationalError):
                    set_work_tier(self.conn, self.company, 3, "lost", "v1")
        self.assertIsNone(self.row())
        self.assertEqual(self.conn.execute("SELECT tier FROM work_tier WHERE company_id = ?", (other,)).fetchone()[0],
                         2)

    def test_a_tier_outside_1_to_3_or_a_blank_reason_is_refused(self):
        with self.assertRaises(sqlite3.IntegrityError):
            set_work_tier(self.conn, self.company, 4, "why", "v")
        with self.assertRaises(sqlite3.IntegrityError):
            set_work_tier(self.conn, self.company, 2, "  ", "v")

    def test_effective_tier_defaults_to_1_without_a_row(self):
        self.assertEqual(effective_tier(self.conn, self.company), 1)
        set_work_tier(self.conn, self.company, 3, "staffing", "v")
        self.assertEqual(effective_tier(self.conn, self.company), 3)
        set_work_tier(self.conn, self.company, 2, "staffing", "v")
        self.assertEqual(effective_tier(self.conn, self.company), 2)

    def test_promote_survives_a_later_rerun_that_says_tier_3(self):
        set_work_tier(self.conn, self.company, 3, "excluded", "v1")
        promote(self.conn, self.company, "devanshu")
        self.assertEqual(effective_tier(self.conn, self.company), 1)
        self.assertEqual(self.row()["tier"], 3)  # the source's verdict is kept beside the override
        set_work_tier(self.conn, self.company, 3, "excluded again", "v2")
        self.assertEqual(self.row()["promoted"], 1)
        self.assertEqual(effective_tier(self.conn, self.company), 1)
        events = self.conn.execute("SELECT kind, actor, company_id FROM event WHERE company_id = ? "
                                   "AND kind = 'promoted'", (self.company,)).fetchall()
        self.assertEqual([tuple(e) for e in events], [("promoted", "devanshu", self.company)])

    def test_promote_twice_appends_one_event(self):
        set_work_tier(self.conn, self.company, 3, "excluded", "v1")
        promote(self.conn, self.company, "devanshu")
        promote(self.conn, self.company, "devanshu")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM event WHERE kind = 'promoted'").fetchone()[0], 1)

    def test_promote_before_any_source_ran_survives_a_tier_3(self):
        promote(self.conn, self.company, "devanshu")
        self.assertEqual(effective_tier(self.conn, self.company), 1)
        set_work_tier(self.conn, self.company, 3, "excluded", "v1")
        self.assertEqual((self.row()["tier"], self.row()["promoted"]), (3, 1))
        self.assertEqual(effective_tier(self.conn, self.company), 1)


class RegisterSourceTest(unittest.TestCase):
    def test_register_source_upserts_version_and_last_run(self):
        conn = fresh_store()
        first, second = start_job(conn, "discover"), start_job(conn, "discover")
        register_source(conn, "lca", "lca-1-aaaa", first)
        register_source(conn, "lca", "lca-1-bbbb", second)
        rows = conn.execute("SELECT source_name, version, last_run_id FROM source").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("lca", "lca-1-bbbb", second)])


if __name__ == "__main__":
    unittest.main()
