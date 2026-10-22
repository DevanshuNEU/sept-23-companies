"""Work tiers (#31 Task 8): the stored tier, the effective tier and a promotion no later run takes back."""
import sqlite3
import unittest

from outreach.sources.tiers import effective_tier, promote, register_source, set_work_tier
from outreach.store import start_job
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
        events = self.conn.execute("SELECT kind, actor, company_id FROM event WHERE company_id = ?",
                                   (self.company,)).fetchall()
        self.assertEqual([tuple(e) for e in events], [("promoted", "devanshu", self.company)])

    def test_promote_twice_appends_one_event(self):
        set_work_tier(self.conn, self.company, 3, "excluded", "v1")
        promote(self.conn, self.company, "devanshu")
        promote(self.conn, self.company, "devanshu")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM event").fetchone()[0], 1)

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
