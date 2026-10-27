"""Browse companies (#120), Task 1: `load_prospects(only=...)` scans named companies past the scan limit, and
`bench_pick_of` is the one place that decides a prospect's bench opening."""
import dataclasses
import unittest
from datetime import date

from outreach.plan.config import default_plan_config
from outreach.plan.outbound import load_outbound
from outreach.plan.prospects import load_prospects
from outreach.plan.select import bench, bench_pick_of
from outreach.ranking.common import canonical_ids
from outreach.ranking.scores import ranked_list
from tests.plan_seed import seed_scored_company
from tests.storetest import fresh_store

CFG = default_plan_config()
TUE = date(2026, 11, 10)


def narrow(cfg, scan_limit):
    return dataclasses.replace(cfg, plan={**cfg.plan, "scan_limit": scan_limit})


def prospects(conn, cfg=CFG, only=None):
    canon = canonical_ids(conn)
    outbound = load_outbound(conn, canon, tz=cfg.get("plan", "timezone"))
    return load_prospects(conn, ranked_list(conn, laned_only=True), canon, TUE, cfg, outbound, only=only)


class OnlyTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.a = seed_scored_company(self.conn, "Acme", 80, ["level_role"])
        self.b = seed_scored_company(self.conn, "Bolt", 70, ["level_role"])
        self.c = seed_scored_company(self.conn, "Corp", 60, ["level_role"])

    def test_only_scans_a_company_past_the_scan_limit(self):
        cfg = narrow(CFG, 1)
        self.assertEqual([p.company_id for p in prospects(self.conn, cfg)], [self.a])
        got = prospects(self.conn, cfg, only={self.c})
        self.assertEqual([(p.company_id, p.position) for p in got], [(self.c, 3)])

    def test_only_scans_just_the_named_companies(self):
        self.assertEqual([p.company_id for p in prospects(self.conn, only={self.a, self.c})], [self.a, self.c])
        self.assertEqual(prospects(self.conn, only=set()), [])

    def test_only_still_drops_a_no_go_company(self):
        self.conn.execute("INSERT INTO no_go(company_id, reason, source, created_at) "
                          "VALUES (?, 'no_sponsorship', 'devanshu', '2026-11-01T12:00:00Z')", (self.c,))
        self.assertEqual(prospects(self.conn, only={self.c}), [])

    def test_only_still_drops_a_paused_company(self):
        self.conn.execute("UPDATE company SET status = 'working', paused_at ='2026-11-01T12:00:00Z' WHERE company_id = ?", (self.b,))
        self.assertEqual(prospects(self.conn, only={self.b}), [])


class BenchPickOfTest(unittest.TestCase):
    def test_bench_pick_of_matches_the_bench(self):
        conn = fresh_store()
        for i, name in enumerate(("Acme", "Bolt", "Corp")):
            seed_scored_company(conn, name, 80 - i, ["level_role"])
        found = prospects(conn)
        self.assertEqual(bench(found, set(), CFG), sorted(
            [bench_pick_of(p, CFG) for p in found], key=lambda b: -b.prospect.score))


if __name__ == "__main__":
    unittest.main()
