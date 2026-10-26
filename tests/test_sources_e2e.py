"""Sources end to end (#31 Task 16): the production order through the CLI, on one scratch store and the fixtures.

LCA discover, Form D discover, HN discover, one by-hand add, resolve, find-boards. The three Done-when lines of #31
are the three tests named for them. Fixtures only: a `FakeClient` answers HN, SEC and the careers pages, the stapply
folder is the fixture copy, and the store is a scratch file. The CLI wiring (`main`, its guard, run-due and the
fetch commands) is tested in test_sources_cli.py.

Where the numbers differ from the plan's first draft, a ruling explains it (each is said where it is asserted):
the HN fixture grew two posts (Veltrix Bio on a shared domain, PrairieLearn with only a github link), so the store
has 19 companies, not 17, and one more `size_check`. The `domain_missing` items the first draft counted are no longer
raised on a fresh store: the crawler asks only for companies in play or ranked near the top (#9).
"""
import json
import unittest

from outreach.crawl.base import valid_token
from outreach.store import is_shared_key
from tests.storetest import table_marks
from tests.test_lca_source import FILES
from tests.test_formd import DATASET
from tests.test_sources_cli import CliCase, count

LCA_NAMES = {
    "Bridgeway Staffing Solutions LLC", "Clearpath Inc", "Depco Systems LLC", "Greenfield AI Inc", "Lumen Apps Inc",
    "Mesh Systems Inc", "Nimbus Robotics, Inc.", "Orchard", "Quill Data Services Inc", "Riverside University",
    "Solo Dev Studio Inc", "Sundial Health Inc", "Veltrix Diagnostics Inc", "Veltrix Pharma Inc"}
OTHER_NAMES = {"Tidepool Apps", "Mesh Systems", "Veltrix Bio", "PrairieLearn", "Zephyr Works"}


class SourcesE2E(CliCase):
    def run_sequence(self):
        """The production order, each command through `main`; every one must exit 0 (every job `ok`)."""
        self.ok("discover", "lca", "--files", *FILES)
        self.ok("discover", "formd", "--dataset", DATASET)
        self.ok("discover", "hn")
        self.ok("add", "--name", "Zephyr Works", "--reason", "met the founder at a meetup",
                "--domain", "zephyr.dev", "--ats", "lever:zephyr")
        self.ok("resolve")
        self.ok("find-boards")
        self.assertEqual(self.client.unrouted, [])

    def names(self, conn):
        return {r["company_id"]: r["name"] for r in conn.execute("SELECT company_id, name FROM company")}

    # --- the first Done-when line

    def test_expected_store(self):
        self.run_sequence()
        conn = self.conn()
        names = self.names(conn)
        by_name = {n: i for i, n in names.items()}
        # 14 LCA companies, plus Tidepool, the HN Mesh Systems (a weak match, so its own company), Zephyr, and two
        # more HN posts the fixture grew: Veltrix Bio (a post on the shared veltrix.com attaches to neither Veltrix
        # company, review focus 1) and PrairieLearn (only a github link: a platform is never a domain, but the post
        # is still a company). The plan's first draft said 17.
        self.assertEqual(set(names.values()), LCA_NAMES | OTHER_NAMES)
        self.assertEqual(len(names), 19)

        lca_ids = {r[0] for r in conn.execute("SELECT company_id FROM company_source WHERE source_name = 'lca'")}
        self.assertEqual({names[i] for i in lca_ids}, LCA_NAMES)
        for company_id in lca_ids:
            self.assertEqual(count(conn, "SELECT count(*) FROM evidence WHERE company_id = ? AND fact = 'lca' "
                                         "AND source_kind = 'lca'", company_id), 1, names[company_id])
            self.assertGreaterEqual(count(conn, "SELECT count(*) FROM company_alias WHERE company_id = ? "
                                                "AND source = 'name'", company_id), 1, names[company_id])

        fein = {names[r["company_id"]]: r["alias_key"] for r in conn.execute(
            "SELECT company_id, alias_key FROM company_alias WHERE source = 'fein'")}
        self.assertEqual(fein, {"Nimbus Robotics, Inc.": "111111111", "Orchard": "222222222",
                                "Riverside University": "444444444", "Mesh Systems Inc": "555555555"})

        funding = [(names[r["company_id"]], json.loads(r["value"])["kind"]) for r in conn.execute(
            "SELECT company_id, value FROM evidence WHERE fact = 'funding' ORDER BY evidence_id")]
        self.assertEqual(sorted(funding), [("Nimbus Robotics, Inc.", "form_d"), ("Orchard", "form_d"),
                                           ("Orchard", "form_d_amendment")])
        stage = conn.execute("SELECT company_id, value, source_kind FROM evidence WHERE fact = 'stage'").fetchall()
        self.assertEqual([(names[r["company_id"]], r["value"], r["source_kind"]) for r in stage],
                         [("Orchard", "yc", "hn")])

        items = [(r["kind"], names[r["company_id"]], r["state"]) for r in conn.execute(
            "SELECT kind, company_id, state FROM review_item ORDER BY review_id")]
        self.assertEqual(sorted(items), sorted([
            ("merge_candidate", "Mesh Systems Inc", "open"),  # the LCA Mesh Systems Inc and the HN Mesh Systems
            ("size_check", "Veltrix Diagnostics Inc", "open"), ("size_check", "Veltrix Pharma Inc", "open"),
            ("size_check", "Veltrix Bio", "open"),  # the third Veltrix: the plan's draft counted the pair
            # No domain_missing item: Solo Dev, PrairieLearn and the three Veltrix companies have no usable domain,
            # but nobody is in play and nothing is ranked yet, so the crawler leaves them quiet (#9, company-first
            # ruling; test_jobs_find_boards covers the scope and the cap).
            # tier 1, no board and a definite "no careers page" (404 on both requests)
            ("flagged", "Greenfield AI Inc", "open"), ("flagged", "Sundial Health Inc", "open")]))
        (merge,) = conn.execute("SELECT reason FROM review_item WHERE kind = 'merge_candidate'").fetchall()
        self.assertIn("Mesh Systems Inc", merge["reason"])
        self.assertIn(f"#{by_name['Mesh Systems']}", merge["reason"])

        boards = {(names[r["company_id"]], r["ats"], r["token"]) for r in conn.execute("SELECT * FROM board")}
        self.assertEqual(boards, {
            ("Nimbus Robotics, Inc.", "greenhouse", "nimbusrobotics"), ("Orchard", "ashby", "orchard"),
            ("Mesh Systems Inc", "greenhouse", "meshsystems"), ("Tidepool Apps", "lever", "tidepoolapps"),
            ("Zephyr Works", "lever", "zephyr"), ("Clearpath Inc", "ashby", "clearpath"),
            ("Riverside University", "workday", "riverside.wd5/External")})
        for r in conn.execute("SELECT * FROM board"):
            self.assertTrue(valid_token(r["ats"], r["token"]), r["token"])
            alias = conn.execute("SELECT company_id, source FROM company_alias WHERE alias_key = ?",
                                 (f"{r['ats']}:{r['token']}".lower(),)).fetchone()
            self.assertEqual((alias["company_id"], alias["source"]), (r["company_id"], "ats"), r["token"])

        watched = conn.execute("SELECT company_id, url FROM watched_page").fetchall()
        self.assertEqual([(names[r["company_id"]], r["url"]) for r in watched],
                         [("Mesh Systems", "https://meshsystems.dev/careers")])  # the HN Mesh: the LCA one has the board

        runs = [(r["job"], r["status"]) for r in conn.execute("SELECT job, status FROM job_run ORDER BY run_id")]
        self.assertEqual(runs, [("rebuild", "ok"), ("discover", "ok"), ("discover", "ok"), ("discover", "ok"),
                                ("resolve", "ok"), ("find_boards", "ok")])  # every run ok, none partial or failed

    # --- the second Done-when line

    def snapshot(self, conn):
        """Every table a source run writes, row by row; `company_source.last_seen_at` is left out (it moves)."""
        tables = {}
        for table in ("company", "company_alias", "company_name_key", "company_source", "evidence", "board",
                      "review_item", "event", "watched_page", "work_tier", "company_post", "shared_key"):
            rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]
            for row in rows:
                if table == "company_source":
                    row.pop("last_seen_at")
            tables[table] = rows
        return tables

    def test_rerun_writes_nothing_new(self):
        self.run_sequence()
        conn = self.conn()
        marks, snapshot = table_marks(conn), self.snapshot(conn)
        jobs = count(conn, "SELECT count(*) FROM job_run")
        self.run_sequence()
        self.assertEqual(table_marks(conn), marks)
        self.assertEqual(self.snapshot(conn), snapshot)
        self.assertEqual(count(conn, "SELECT count(*) FROM job_run"), jobs + 5)  # only job rows are new
        self.assertEqual(count(conn, "SELECT count(*) FROM job_run WHERE status != 'ok'"), 0)

    # --- the third Done-when line

    def test_shared_keys_never_merge(self):
        self.run_sequence()
        conn = self.conn()
        names = self.names(conn)
        peo = {"Clearpath Inc", "Greenfield AI Inc", "Sundial Health Inc"}
        veltrix = {"Veltrix Diagnostics Inc", "Veltrix Pharma Inc", "Veltrix Bio"}  # Bio is the HN post on the domain
        live = {n for i, n in names.items()
                if conn.execute("SELECT merged_into FROM company WHERE company_id = ?", (i,)).fetchone()[0] is None}
        self.assertTrue((peo | veltrix) <= live)
        self.assertEqual(len(peo | veltrix), 6)  # six distinct live companies
        self.assertEqual(count(conn, "SELECT count(*) FROM company WHERE merged_into IS NOT NULL"), 0)
        self.assertTrue(is_shared_key(conn, "fein", "333333333"))
        self.assertTrue(is_shared_key(conn, "domain", "veltrix.com"))
        self.assertEqual(count(conn, "SELECT count(*) FROM company_alias WHERE source = 'fein' AND "
                                     "alias_key = '333333333'"), 0)
        for name in peo | veltrix:
            self.assertEqual(count(conn, "SELECT count(*) FROM company_alias a JOIN company c USING (company_id) "
                                         "WHERE c.name = ? AND a.source = 'fein'", name), 0)


if __name__ == "__main__":
    unittest.main()
