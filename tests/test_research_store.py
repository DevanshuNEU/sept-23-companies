import sqlite3
import unittest

from outreach.research.store import FRESH_DAYS, Fact, fresh_company_facts, store_company_facts
from outreach.store.migrate import latest_version
from tests import apiseed as seed
from tests.storetest import fresh_store

NOW = "2026-11-10T15:14:00Z"


def fact(text="Ships a build cache", quote="We ship a build cache", url="https://acme.example/blog/cache", page_date="2026-11-01"):
    return Fact(fact=text, quote=quote, url=url, page_date=page_date)


class ResearchStoreTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.company = seed.company(self.conn, "Acme")

    def test_migration_creates_research_fact(self):
        self.assertGreaterEqual(latest_version(), 8)
        self.assertIn(8, [r[0] for r in self.conn.execute("SELECT version FROM schema_migration")])
        columns = [r["name"] for r in self.conn.execute("PRAGMA table_info(research_fact)")]
        self.assertEqual(columns, ["fact_id", "company_id", "fact", "quote", "url", "page_date", "found_by", "verified_at"])
        self.assertEqual(FRESH_DAYS, 14)

    def test_store_and_read_fresh_facts(self):
        self.assertEqual(store_company_facts(self.conn, self.company, [fact(), fact("Hires in Berlin", "We hire in Berlin", "https://acme.example/jobs", None)],
                                             found_by="gpt", now="2026-11-09T10:00:00Z"), 2)
        self.assertEqual(store_company_facts(self.conn, self.company, [fact("Newer", "A newer quote", "https://acme.example/new")],
                                             found_by="claude", now=NOW), 1)
        rows = fresh_company_facts(self.conn, self.company, now=NOW)
        self.assertEqual([r["fact"] for r in rows][0], "Newer")
        self.assertEqual(len(rows), 3)
        self.assertEqual(set(rows[0]), {"fact", "quote", "url", "page_date", "found_by", "verified_at"})
        self.assertEqual(rows[0]["found_by"], "claude")
        self.assertEqual(rows[0]["verified_at"], NOW)

    def test_facts_older_than_14_days_are_not_fresh(self):
        store_company_facts(self.conn, self.company, [fact()], found_by="gpt", now="2026-10-27T15:14:00Z")
        self.assertEqual(len(fresh_company_facts(self.conn, self.company, now="2026-11-10T15:14:00Z")), 1)
        self.assertEqual(fresh_company_facts(self.conn, self.company, now="2026-11-10T15:14:01Z"), [])

    def test_same_fact_twice_is_stored_once(self):
        self.assertEqual(store_company_facts(self.conn, self.company, [fact()], found_by="gpt", now=NOW), 1)
        self.assertEqual(store_company_facts(self.conn, self.company, [fact()], found_by="hand", now=NOW), 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM research_fact").fetchone()[0], 1)

    def test_merged_company_uses_the_survivor(self):
        survivor = seed.company(self.conn, "Acme Inc")
        self.conn.execute("UPDATE company SET merged_into = ?, status = 'removed', remove_reason = 'duplicate' "
                          "WHERE company_id = ?", (survivor, self.company))
        self.assertEqual(store_company_facts(self.conn, self.company, [fact()], found_by="gpt", now=NOW), 1)
        self.assertEqual(self.conn.execute("SELECT company_id FROM research_fact").fetchone()[0], survivor)
        self.assertEqual(len(fresh_company_facts(self.conn, self.company, now=NOW)), 1)
        self.assertEqual(len(fresh_company_facts(self.conn, survivor, now=NOW)), 1)

    def test_linkedin_url_is_refused_by_the_table(self):
        for url in ("https://www.linkedin.com/company/acme", "https://lnkd.in/x", "http://acme.example/a", "https://"):
            with self.assertRaises(sqlite3.IntegrityError, msg=url):
                store_company_facts(self.conn, self.company, [fact(url=url)], found_by="gpt", now=NOW)
        with self.assertRaises(sqlite3.IntegrityError):
            store_company_facts(self.conn, self.company, [fact()], found_by="bot", now=NOW)


if __name__ == "__main__":
    unittest.main()
