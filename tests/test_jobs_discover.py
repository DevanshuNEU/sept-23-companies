"""The discover job (#31 Task 8): sources, candidates, work tiers, posts, job status and the LCA fixtures end to end."""
import json
import sqlite3
import unittest
from dataclasses import asdict
from unittest import mock

from outreach import identity
from outreach.rules.rebuild import EvidenceDraft
from outreach.sources.base import Candidate
from outreach.sources.capabilities import PostRecord, SharedKey
from outreach.sources.jobs import SourceReport, run_discover
from outreach.sources.lca import LcaSource
from outreach.store import mark_shared_key, name_key
from tests.contracts import check_source
from tests.storetest import fresh_store, table_marks
from tests.test_lca_source import FILES


def cand(name, ref, **kw):
    return Candidate(name=name, domain=kw.pop("domain", None), keys=kw.pop("keys", {}), source_ref=ref, **kw)


class FakeSource:
    """Yields its items in order; an Exception item is raised at that point. Records what the job asked, and when."""
    def __init__(self, name, items, *, version="fake-1", **extra):
        self.name, self.version, self.items = name, version, items
        self.log = []
        self.__dict__.update(extra)

    def discover(self, since=None):
        self.log.append(("discover", since))
        for item in self.items:
            if isinstance(item, Exception):
                raise item
            self.log.append(("yield", item.source_ref))
            yield item


class SharedFake(FakeSource):
    def shared_keys(self):
        self.log.append(("shared_keys",))
        return [SharedKey("fein", "999999999", "2 employers: A, B")]


class PostFake(FakeSource):
    def __init__(self, name, items, posts, **kw):
        super().__init__(name, items, **kw)
        self._posts = posts

    def posts(self):
        return list(self._posts)


def job(conn, run_id=None):
    """The latest discover job row (the install job's row is also in job_run)."""
    return conn.execute("SELECT * FROM job_run WHERE job = 'discover' ORDER BY run_id DESC").fetchone()


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class DiscoverTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()

    def test_discover_registers_source_version_and_job_run(self):
        run_id, reports = run_discover(self.conn, [FakeSource("fake", [cand("Acme Robotics", "f:1")])])
        src = self.conn.execute("SELECT version, last_run_id FROM source WHERE source_name = 'fake'").fetchone()
        self.assertEqual(tuple(src), ("fake-1", run_id))
        row = self.conn.execute("SELECT job, status, finished_at, error FROM job_run WHERE run_id = ?",
                                (run_id,)).fetchone()
        self.assertEqual((row["job"], row["status"], row["error"]), ("discover", "ok", ""))
        self.assertIsNotNone(row["finished_at"])
        report = reports["fake"]
        self.assertEqual((report.candidates, report.created, report.attached, report.error),
                         (1, 1, 0, ""))
        summary = json.loads(job(self.conn)["summary"])
        self.assertEqual(summary, {"fake": asdict(report)})
        self.assertEqual(summary["fake"]["version"], "fake-1")
        self.assertEqual(report.version, "fake-1")

    def test_since_reaches_the_source(self):
        source = FakeSource("fake", [])
        run_discover(self.conn, [source], since="2026-01-01")
        self.assertIn(("discover", "2026-01-01"), source.log)

    def test_shared_keys_are_marked_before_the_first_candidate(self):
        source = SharedFake("fake", [cand("Acme Robotics", "f:1"), cand("Birch Labs", "f:2")])
        marked_before = []
        real = identity.resolve_candidate

        def spy(conn, c, **kw):
            marked_before.append(count(conn, "shared_key"))
            return real(conn, c, **kw)

        with mock.patch.object(identity, "resolve_candidate", spy):
            run_discover(self.conn, [source])
        self.assertEqual(marked_before, [1, 1])
        self.assertEqual(source.log[0], ("shared_keys",))
        self.assertEqual(self.conn.execute("SELECT kind, value, reason FROM shared_key").fetchone()[:2],
                         ("fein", "999999999"))

    def test_one_failing_source_is_partial_and_the_others_land(self):
        first = FakeSource("first", [cand("Acme Robotics", "a:1"), cand("Birch Labs", "a:2"),
                                     RuntimeError("feed went away")])
        second = FakeSource("second", [cand("Cedar Works", "b:1")])
        run_id, reports = run_discover(self.conn, [first, second])
        self.assertEqual(count(self.conn, "company"), 3)
        self.assertEqual(reports["first"].candidates, 2)
        self.assertIn("feed went away", reports["first"].error)
        self.assertEqual(reports["second"].error, "")
        row = self.conn.execute("SELECT status, summary, error FROM job_run WHERE run_id = ?", (run_id,)).fetchone()
        self.assertEqual(row["status"], "partial")
        self.assertIn("feed went away", json.loads(row["summary"])["first"]["error"])
        self.assertIn("feed went away", row["error"])

    def test_a_source_that_raises_before_yielding_is_an_error_not_zero_candidates(self):
        run_id, reports = run_discover(self.conn, [FakeSource("fake", [ValueError("no employers")])])
        self.assertIn("no employers", reports["fake"].error)
        self.assertEqual(reports["fake"].candidates, 0)
        self.assertEqual(job(self.conn)["status"], "failed")

    def test_every_source_failing_is_failed(self):
        _, reports = run_discover(self.conn, [FakeSource("a", [OSError("one")]), FakeSource("b", [OSError("two")])])
        self.assertTrue(all(r.error for r in reports.values()))
        self.assertEqual(job(self.conn)["status"], "failed")

    def test_shared_keys_raising_is_a_source_error_and_no_candidate_is_resolved(self):
        class Broken(FakeSource):
            def shared_keys(self):
                raise RuntimeError("cannot read keys")

        _, reports = run_discover(self.conn, [Broken("fake", [cand("Acme Robotics", "f:1")])])
        self.assertIn("cannot read keys", reports["fake"].error)
        self.assertEqual(count(self.conn, "company"), 0)

    def test_candidate_failure_is_counted_not_swallowed(self):
        real = identity.resolve_candidate

        def flaky(conn, c, **kw):
            if c.source_ref == "f:2":
                raise sqlite3.IntegrityError("CHECK constraint failed: bad row")
            return real(conn, c, **kw)

        source = FakeSource("fake", [cand("Acme Robotics", "f:1"), cand("Birch Labs", "f:2"),
                                     cand("Cedar Works", "f:3")])
        with mock.patch.object(identity, "resolve_candidate", flaky):
            run_id, reports = run_discover(self.conn, [source])
        report = reports["fake"]
        self.assertEqual(report.candidates, 3)
        self.assertEqual(len(report.failed), 1)
        self.assertIn("f:2", report.failed[0])
        self.assertIn("CHECK constraint failed: bad row", report.failed[0])
        self.assertEqual(report.error, "")
        self.assertEqual(count(self.conn, "company"), 2)
        self.assertEqual(job(self.conn)["status"], "partial")
        self.assertEqual(job(self.conn)["error"], "1 candidates failed in fake")

    def test_a_value_error_from_a_candidate_is_listed_and_the_run_goes_on(self):
        source = FakeSource("fake", [cand("Acme Robotics", ""), cand("Birch Labs", "f:2")])
        _, reports = run_discover(self.conn, [source])
        self.assertEqual(len(reports["fake"].failed), 1)
        self.assertTrue(reports["fake"].failed[0].startswith("Acme Robotics: "))  # no source_ref: listed by name
        self.assertEqual(count(self.conn, "company"), 1)

    def test_a_failed_tier_write_rolls_the_whole_candidate_back(self):
        class BadTier(FakeSource):
            def work_tier(self, ref):
                return (9, "not a tier") if ref == "f:1" else (1, "fine")

        _, reports = run_discover(self.conn, [BadTier("fake", [cand("Acme Robotics", "f:1"),
                                                               cand("Birch Labs", "f:2")])])
        self.assertEqual(len(reports["fake"].failed), 1)
        self.assertEqual([r[0] for r in self.conn.execute("SELECT name FROM company")], ["Birch Labs"])
        self.assertEqual(count(self.conn, "company_source"), 1)

    def test_creates_false_skips_unmatched_and_attaches_matched(self):
        run_discover(self.conn, [FakeSource("one", [cand("Acme Robotics", "o:1", domain="acme.io", evidence=(EvidenceDraft("domain", "acme.io", "estimate", "lca", "r"),))])])
        source = FakeSource("two", [cand("Acme Robotics Inc", "t:1", domain="acme.io"),
                                    cand("Birch Labs", "t:2")], creates_companies=False)
        _, reports = run_discover(self.conn, [source])
        report = reports["two"]
        self.assertEqual((report.attached, report.skipped_unmatched, report.created), (1, 1, 0))
        self.assertEqual(count(self.conn, "company"), 1)

    def test_creates_argument_overrides_the_source(self):
        source = FakeSource("fake", [cand("Birch Labs", "f:1")])
        _, reports = run_discover(self.conn, [source], creates={"fake": False})
        self.assertEqual((reports["fake"].skipped_unmatched, count(self.conn, "company")), (1, 0))

    def test_notes_come_from_the_source_summary(self):
        class Noted(FakeSource):
            def summary(self):
                return {"fy_mismatch": 7}

        _, reports = run_discover(self.conn, [Noted("fake", [cand("Acme Robotics", "f:1")])])
        self.assertEqual(reports["fake"].notes, {"fy_mismatch": 7})
        summary = json.loads(job(self.conn)["summary"])
        self.assertEqual(summary["fake"]["notes"], {"fy_mismatch": 7})

    def test_work_tier_is_set_from_the_source(self):
        class Tiered(FakeSource):
            def work_tier(self, ref):
                return (3, "excluded: body shop") if ref == "f:1" else None

        run_discover(self.conn, [Tiered("fake", [cand("Acme Robotics", "f:1"), cand("Birch Labs", "f:2")])])
        rows = self.conn.execute("SELECT c.name, t.tier, t.reason, t.source_version FROM work_tier t "
                                 "JOIN company c USING (company_id)").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("Acme Robotics", 3, "excluded: body shop", "fake-1")])

    def test_a_summary_failure_after_a_clean_discover_is_a_source_error(self):
        class BadSummary(FakeSource):
            def summary(self):
                raise KeyError("summary lost")

        _, reports = run_discover(self.conn, [BadSummary("fake", [cand("Acme Robotics", "f:1")])])
        self.assertIn("summary lost", reports["fake"].error)
        self.assertEqual(count(self.conn, "company"), 1)  # the candidate landed before the summary was asked for
        self.assertEqual(job(self.conn)["status"], "failed")

    def test_a_summary_failure_hides_nothing_when_discover_already_raised(self):
        class BadSummary(FakeSource):
            def summary(self):
                raise KeyError("summary lost")

        _, reports = run_discover(self.conn, [BadSummary("fake", [RuntimeError("feed went away")])])
        self.assertIn("feed went away", reports["fake"].error)
        self.assertNotIn("summary lost", reports["fake"].error)

    def test_same_name_sources_one_failing_is_partial_not_failed(self):
        bad = FakeSource("dup", [OSError("first broke")])
        good = FakeSource("dup", [cand("Acme Robotics", "d:1")])
        _, reports = run_discover(self.conn, [bad, good])
        self.assertEqual(list(reports), ["dup"])
        self.assertEqual(job(self.conn)["status"], "partial")
        self.assertEqual(count(self.conn, "company"), 1)

    def test_same_name_sources_both_failing_keep_both_errors(self):
        _, reports = run_discover(self.conn, [FakeSource("dup", [OSError("first broke")]),
                                              FakeSource("dup", [OSError("second broke")])])
        self.assertIn("first broke", reports["dup"].error)
        self.assertIn("second broke", reports["dup"].error)
        self.assertEqual(job(self.conn)["status"], "failed")
        self.assertIn("first broke", job(self.conn)["error"])
        self.assertIn("second broke", job(self.conn)["error"])

    def test_a_source_that_errors_midway_keeps_its_companies_and_writes_its_posts_on_a_rerun(self):
        post = PostRecord("hn:acme", "https://example.com/p1", None, "Acme hires", False)
        broken = PostFake("hn", [cand("Acme Robotics", "hn:acme"), RuntimeError("feed went away")], [post])
        _, reports = run_discover(self.conn, [broken])
        self.assertEqual((reports["hn"].candidates, count(self.conn, "company")), (1, 1))
        self.assertEqual(reports["hn"].posts, 0)
        self.assertEqual(count(self.conn, "company_post"), 0)
        self.assertEqual(job(self.conn)["status"], "failed")
        whole = PostFake("hn", [cand("Acme Robotics", "hn:acme")], [post])
        _, again = run_discover(self.conn, [whole])
        self.assertEqual((again["hn"].posts, again["hn"].attached, count(self.conn, "company")), (1, 1, 1))

    def test_a_type_error_from_a_candidate_ends_that_source_with_an_error_and_the_others_run(self):
        real = identity.resolve_candidate

        def broken(conn, c, **kw):
            if c.source_ref == "a:2":
                raise TypeError("bad shape")
            return real(conn, c, **kw)

        first = FakeSource("first", [cand("Acme Robotics", "a:1"), cand("Birch Labs", "a:2"),
                                     cand("Cedar Works", "a:3")])
        second = FakeSource("second", [cand("Delta Systems", "b:1")])
        with mock.patch.object(identity, "resolve_candidate", broken):
            _, reports = run_discover(self.conn, [first, second])
        self.assertIn("bad shape", reports["first"].error)
        self.assertEqual(reports["first"].candidates, 2)
        self.assertEqual(reports["second"].error, "")
        self.assertEqual(sorted(r[0] for r in self.conn.execute("SELECT name FROM company")),
                         ["Acme Robotics", "Delta Systems"])
        self.assertEqual(job(self.conn)["status"], "partial")

    def test_a_type_error_from_the_tier_call_ends_that_source_with_an_error(self):
        class BadTier(FakeSource):
            def work_tier(self, ref):
                raise TypeError("tier lookup broke")

        _, reports = run_discover(self.conn, [BadTier("fake", [cand("Acme Robotics", "f:1")])])
        self.assertIn("tier lookup broke", reports["fake"].error)
        self.assertEqual(count(self.conn, "company"), 0)  # the candidate's transaction rolled back
        self.assertEqual(job(self.conn)["status"], "failed")

    def test_creates_true_overrides_a_source_that_sets_false(self):
        source = FakeSource("fake", [cand("Birch Labs", "f:1")], creates_companies=False)
        _, reports = run_discover(self.conn, [source], creates={"fake": True})
        self.assertEqual((reports["fake"].created, count(self.conn, "company")), (1, 1))


class LcaFixtureTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.source = LcaSource(FILES)

    def company(self, name):
        return self.conn.execute("SELECT company_id FROM company WHERE name = ?", (name,)).fetchone()[0]

    def aliases(self, company_id):
        return {r["alias_key"]: r["source"] for r in
                self.conn.execute("SELECT alias_key, source FROM company_alias WHERE company_id = ?", (company_id,))}

    def test_lca_source_passes_the_source_contract(self):
        check_source(self, self.source)

    def test_lca_fixtures_store_the_expected_companies(self):
        _, reports = run_discover(self.conn, [self.source])
        report = reports["lca"]
        self.assertEqual((report.candidates, report.created, report.error, report.failed), (14, 14, "", []))
        self.assertEqual(count(self.conn, "company"), 14)
        self.assertEqual(job(self.conn)["status"], "ok")
        # fy_mismatch (and the rest of the source's summary) reaches the job summary
        self.assertEqual(reports["lca"].notes, self.source.summary())
        summary = json.loads(job(self.conn)["summary"])
        self.assertEqual(summary["lca"]["notes"]["fy_mismatch"], 0)
        self.assertEqual(summary["lca"]["notes"]["qualifying"], 14)
        for cand_ in self.source.discover():
            company_id = self.conn.execute(
                "SELECT company_id FROM company_source WHERE source_name = 'lca' AND source_ref = ?",
                (cand_.source_ref,)).fetchone()[0]
            self.assertIn(name_key(cand_.name), self.aliases(company_id), cand_.name)
            ev = self.conn.execute("SELECT COUNT(*) FROM evidence WHERE company_id = ? AND fact = 'lca'",
                                   (company_id,)).fetchone()[0]
            self.assertGreaterEqual(ev, 1, cand_.name)
        fein_aliases = {self.company(n): k for n, k in (("Nimbus Robotics, Inc.", "111111111"), ("Orchard", "222222222"),
                                                        ("Riverside University", "444444444"),
                                                        ("Mesh Systems Inc", "555555555"))}
        for company_id, key in fein_aliases.items():
            self.assertEqual(self.aliases(company_id).get(key), "fein")
        every_fein = {k for k, s in self.conn.execute("SELECT alias_key, source FROM company_alias") if s == "fein"}
        self.assertEqual(every_fein, set(fein_aliases.values()))
        for peo_client in ("Clearpath Inc", "Greenfield AI Inc", "Sundial Health Inc"):
            self.assertNotIn("fein", self.aliases(self.company(peo_client)).values(), peo_client)
        tiers = {r["name"]: r["tier"] for r in self.conn.execute(
            "SELECT c.name, t.tier FROM work_tier t JOIN company c USING (company_id)")}
        self.assertEqual(len(tiers), 14)
        self.assertEqual(tiers["Nimbus Robotics, Inc."], 1)
        self.assertEqual(tiers["Quill Data Services Inc"], 3)
        self.assertEqual(tiers["Bridgeway Staffing Solutions LLC"], 3)
        versions = {r[0] for r in self.conn.execute("SELECT source_version FROM work_tier")}
        self.assertEqual(versions, {self.source.version})
        self.assertEqual(self.conn.execute("SELECT version FROM source WHERE source_name = 'lca'").fetchone()[0],
                         self.source.version)

    def test_no_lca_company_has_a_size_or_headcount_row(self):
        run_discover(self.conn, [self.source])
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM evidence WHERE fact IN ('size', 'headcount')").fetchone()[0], 0)

    def test_rerun_writes_nothing_new(self):
        run_discover(self.conn, [self.source])
        before = table_marks(self.conn)
        tiers = [tuple(r) for r in self.conn.execute("SELECT * FROM work_tier ORDER BY company_id")]
        _, reports = run_discover(self.conn, [LcaSource(FILES)])
        self.assertEqual(table_marks(self.conn), before)
        self.assertEqual([tuple(r) for r in self.conn.execute("SELECT * FROM work_tier ORDER BY company_id")], tiers)
        self.assertEqual((reports["lca"].created, reports["lca"].attached, reports["lca"].evidence_added,
                          reports["lca"].items_raised), (0, 14, 0, 0))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM job_run WHERE job = 'discover'").fetchone()[0], 2)

    def test_shared_fein_clients_stay_separate_companies(self):
        run_discover(self.conn, [self.source])
        clients = [self.company(n) for n in ("Clearpath Inc", "Greenfield AI Inc", "Sundial Health Inc")]
        self.assertEqual(len(set(clients)), 3)
        self.assertIsNotNone(self.conn.execute(
            "SELECT 1 FROM shared_key WHERE kind = 'fein' AND value = '333333333'").fetchone())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM company WHERE merged_into IS NOT NULL").fetchone()[0],
                         0)
        # a later source carrying the PEO's FEIN as a key attaches to none of them
        late = FakeSource("late", [cand("Some Staffing Co", "l:1", keys={"fein": "333333333"})])
        _, reports = run_discover(self.conn, [late])
        self.assertEqual(reports["late"].created, 1)
        self.assertEqual(count(self.conn, "company"), 15)


class PostsTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()

    def posts(self):
        return [tuple(r) for r in self.conn.execute(
            "SELECT p.url, c.name, p.asks_email, p.posted_at, p.text, p.source_name FROM company_post p "
            "JOIN company c USING (company_id) ORDER BY p.url")]

    def test_posts_are_written_after_the_candidate_resolves_and_once(self):
        real = identity.resolve_candidate

        def flaky(conn, c, **kw):
            if c.source_ref == "hn:birch":
                raise sqlite3.IntegrityError("boom")
            return real(conn, c, **kw)

        source = PostFake(
            "hn", [cand("Acme Robotics", "hn:acme"), cand("Birch Labs", "hn:birch")],
            [PostRecord("hn:acme", "https://news.ycombinator.com/item?id=1", "2026-10-01T12:00:00Z",
                        "Acme Robotics: email me at jobs@acme.io", True),
             PostRecord("hn:acme", "https://news.ycombinator.com/item?id=2", None, "Acme again", False),
             PostRecord("hn:birch", "https://news.ycombinator.com/item?id=3", None, "Birch hires", False),
             PostRecord("hn:ghost", "https://news.ycombinator.com/item?id=4", None, "no such candidate", False)])
        with mock.patch.object(identity, "resolve_candidate", flaky):
            _, reports = run_discover(self.conn, [source])
        self.assertEqual(reports["hn"].posts, 2)
        # the failed candidate is listed once; its post is skipped. A post of a ref never yielded is listed too.
        self.assertEqual(len(reports["hn"].failed), 2)
        self.assertTrue(any(f.startswith("hn:birch: ") for f in reports["hn"].failed))
        self.assertIn("post https://news.ycombinator.com/item?id=4: unknown source_ref", reports["hn"].failed)
        self.assertEqual(job(self.conn)["status"], "partial")
        self.assertEqual(self.posts(), [
            ("https://news.ycombinator.com/item?id=1", "Acme Robotics", 1, "2026-10-01T12:00:00Z",
             "Acme Robotics: email me at jobs@acme.io", "hn"),
            ("https://news.ycombinator.com/item?id=2", "Acme Robotics", 0, None, "Acme again", "hn")])
        before = table_marks(self.conn)
        with mock.patch.object(identity, "resolve_candidate", flaky):
            _, again = run_discover(self.conn, [source])
        self.assertEqual(again["hn"].posts, 0)
        self.assertEqual(table_marks(self.conn)["company_post"], before["company_post"])
        self.assertEqual(len(self.posts()), 2)

    def test_post_text_is_capped_at_20000_characters(self):
        source = PostFake("hn", [cand("Acme Robotics", "hn:acme")],
                          [PostRecord("hn:acme", "https://example.com/p", None, "x" * 25_000, False)])
        run_discover(self.conn, [source])
        self.assertEqual(len(self.posts()[0][4]), 20_000)

    def test_a_bad_post_is_listed_and_the_others_still_land(self):
        source = PostFake("hn", [cand("Acme Robotics", "hn:acme")],
                          [PostRecord("hn:acme", "", None, "no url", False),
                           PostRecord("hn:acme", "https://example.com/ok", None, "fine", False)])
        run_discover(self.conn, [source])
        self.assertEqual(job(self.conn)["status"], "partial")
        self.assertEqual(len(self.posts()), 1)

    def test_a_post_time_that_cannot_be_read_is_listed_and_the_others_still_land(self):
        source = PostFake("hn", [cand("Acme Robotics", "hn:acme")],
                          [PostRecord("hn:acme", "https://example.com/bad", "not a time", "x", False),
                           PostRecord("hn:acme", "https://example.com/ok", None, "fine", False)])
        _, reports = run_discover(self.conn, [source])
        self.assertEqual(len(reports["hn"].failed), 1)
        self.assertIn("https://example.com/bad", reports["hn"].failed[0])
        self.assertEqual(len(self.posts()), 1)
        self.assertEqual(job(self.conn)["error"], "1 posts failed in hn")

    def test_an_unmatched_candidate_of_a_no_create_source_writes_no_post(self):
        source = PostFake("hn", [cand("Birch Labs", "hn:birch")],
                          [PostRecord("hn:birch", "https://example.com/p", None, "t", False)],
                          creates_companies=False)
        _, reports = run_discover(self.conn, [source])
        self.assertEqual((reports["hn"].posts, len(self.posts())), (0, 0))


if __name__ == "__main__":
    unittest.main()
