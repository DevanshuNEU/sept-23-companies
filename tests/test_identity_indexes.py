"""Identity's two hot lookups must use their index (#31 Task 1).

Matching 18,000 candidates against 100,000 evidence rows is a scan per candidate without these indexes. The
tests load enough rows to matter, then read SQLite's own query plan for the exact lookups identity runs.
"""
import unittest

from outreach.store.timefmt import utcnow
from tests.storetest import fresh_store

COMPANIES = 400
PER_COMPANY = 10  # evidence rows each


def plan(conn, sql: str, params: tuple) -> str:
    return " | ".join(row["detail"] for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params))


class IdentityIndexTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.now = utcnow()

    def seed_companies(self):
        self.conn.execute("BEGIN")
        self.conn.executemany(
            "INSERT INTO company(name, status_at, created_at) VALUES (?, ?, ?)",
            [(f"Company {i}", self.now, self.now) for i in range(COMPANIES)])
        self.conn.execute("COMMIT")
        return [r[0] for r in self.conn.execute("SELECT company_id FROM company ORDER BY company_id")]

    def test_domain_lookup_uses_evidence_domain_index(self):
        ids = self.seed_companies()
        rows = []
        for n, company_id in enumerate(ids):
            rows.append((company_id, "domain", f"host{n}.example.com", "website", f"ref{n}"))
            for k in range(PER_COMPANY - 1):
                rows.append((company_id, "hq", f"City {n} {k}", "website", f"ref{n}-{k}"))
        self.conn.execute("BEGIN")
        self.conn.executemany(
            "INSERT INTO evidence(company_id, fact, value, confidence, source_kind, source_ref, recorded_at) "
            "VALUES (?, ?, ?, 'estimate', ?, ?, ?)", [r + (self.now,) for r in rows])
        self.conn.execute("COMMIT")
        sql = "SELECT company_id FROM evidence WHERE fact = 'domain' AND value = ?"
        self.assertIn("evidence_domain", plan(self.conn, sql, ("host7.example.com",)))
        self.assertEqual([r[0] for r in self.conn.execute(sql, ("host7.example.com",))], [ids[7]])

    def test_company_source_lookup_uses_ref_index(self):
        ids = self.seed_companies()
        self.conn.execute("INSERT INTO source(source_name, version) VALUES ('lca', 'v1')")
        self.conn.execute("BEGIN")
        self.conn.executemany(
            "INSERT INTO company_source(company_id, source_name, source_ref, first_seen_at, last_seen_at) "
            "VALUES (?, 'lca', ?, ?, ?)", [(c, f"fein:{n:09d}", self.now, self.now) for n, c in enumerate(ids)])
        self.conn.execute("COMMIT")
        sql = "SELECT company_id FROM company_source WHERE source_name = ? AND source_ref = ?"
        self.assertIn("company_source_ref", plan(self.conn, sql, ("lca", "fein:000000007")))
        self.assertEqual([r[0] for r in self.conn.execute(sql, ("lca", "fein:000000007"))], [ids[7]])


if __name__ == "__main__":
    unittest.main()
