"""The resolve job (#31 Task 9): duplicate-domain merge items and shared-domain size checks over stored companies.

Companies are made by hand through identity or `seed_company`; the LCA fixtures run end to end for the Veltrix case.
Resolve never moves a row: every test that matters compares the company table before and after.
"""
import hashlib
import json
import sqlite3
import unittest
from unittest import mock

from outreach import identity
from outreach.identity import resolve_store
from outreach.rules.facts import EvidenceDraft
from outreach.rules.rebuild import record_evidence
from outreach.sources.base import Candidate
from outreach.sources.jobs import run_discover, run_resolve
from outreach.sources.lca import LcaSource
from outreach.store import add_alias, mark_shared_key
from tests.storetest import fresh_store, seed_company, table_marks
from tests.test_lca_source import FILES


def dom(value, confidence="estimate", kind="lca"):
    return EvidenceDraft("domain", value, confidence, kind, "r")


def company_hash(conn):
    text = repr([tuple(r) for r in conn.execute("SELECT * FROM company ORDER BY company_id")])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ResolveBase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)

    def company(self, name, *domains, confidence="estimate"):
        cid = seed_company(self.conn, name)
        if domains:
            record_evidence(self.conn, cid, [dom(d, confidence) for d in domains], actor="source")
        return cid

    def items(self, kind):
        return [dict(r) for r in self.conn.execute(
            "SELECT company_id, reason, evidence_key, raised_by, state FROM review_item WHERE kind = ? "
            "ORDER BY review_id", (kind,))]

    def remove(self, cid, into=None):
        if into is None:
            self.conn.execute("UPDATE company SET status = 'removed', remove_reason = 'junk' WHERE company_id = ?",
                              (cid,))
        else:
            self.conn.execute("UPDATE company SET status = 'removed', remove_reason = 'merged', merged_into = ? "
                              "WHERE company_id = ?", (into, cid))


class DuplicateDomainTest(ResolveBase):
    def test_two_companies_with_the_same_unshared_domain_raise_one_item(self):
        # Made by two sources before either knew the domain, then the second learns it.
        for s in ("lca", "hn"):
            self.conn.execute("INSERT INTO source(source_name, version) VALUES (?, 'v1')", (s,))
        a = identity.resolve_candidate(
            self.conn, Candidate(name="Acme Robotics", domain=None, keys={}, source_ref="lca:1",
                                 evidence=(dom("acmerobotics.io"),)), source_name="lca").company_id
        b = identity.resolve_candidate(
            self.conn, Candidate(name="Acme Labs", domain=None, keys={}, source_ref="hn:1"),
            source_name="hn").company_id
        self.assertNotEqual(a, b)
        self.assertEqual(self.items("merge_candidate"), [])
        record_evidence(self.conn, b, [dom("acmerobotics.io", kind="hn")], actor="source")
        report = resolve_store(self.conn)
        self.assertEqual((report.pairs_raised, report.size_checks_raised, report.failed), (1, 0, []))
        (item,) = self.items("merge_candidate")
        self.assertEqual((item["company_id"], item["evidence_key"], item["raised_by"], item["state"]),
                         (a, str(b), "source", "open"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM company WHERE merged_into IS NOT NULL")
                         .fetchone()[0], 0)

    def test_the_reason_states_the_basis_and_the_confidence(self):
        a, b = self.company("Acme Robotics", "acme.io"), self.company("Acme Labs", "acme.io")
        resolve_store(self.conn)
        (item,) = self.items("merge_candidate")
        self.assertIn(f"Acme Robotics (#{a})", item["reason"])
        self.assertIn(f"Acme Labs (#{b})", item["reason"])
        self.assertIn("Basis domain, confidence strong", item["reason"])
        self.assertIn("acme.io", item["reason"])

    def test_a_guess_domain_raises_no_pair(self):
        self.company("Acme Robotics", "acme.io")
        self.company("Acme Labs", "acme.io", confidence="guess")
        report = resolve_store(self.conn)
        self.assertEqual((report.pairs_raised, report.size_checks_raised), (0, 0))

    def test_www_and_case_do_not_hide_a_pair(self):
        self.company("Acme Robotics", "acme.io")
        self.company("Acme Labs", "www.acme.io")
        self.assertEqual(resolve_store(self.conn).pairs_raised, 1)

    def test_a_platform_domain_raises_no_pair(self):
        self.company("Acme Robotics", "acme.vercel.app")
        self.company("Acme Labs", "acme.vercel.app")
        self.assertEqual(resolve_store(self.conn).pairs_raised, 0)

    def test_a_shared_domain_raises_no_pair(self):
        self.company("Veltrix Pharma", "veltrix.com")
        self.company("Veltrix Labs", "veltrix.com")
        mark_shared_key(self.conn, "domain", "veltrix.com", "2 employers: Veltrix Labs, Veltrix Pharma")
        self.assertEqual(resolve_store(self.conn).pairs_raised, 0)
        self.assertEqual(self.items("merge_candidate"), [])

    def test_three_holders_link_through_the_lowest_id(self):
        a = self.company("Acme One", "acme.io")
        b = self.company("Acme Two", "acme.io")
        c = self.company("Acme Three", "acme.io")
        self.assertEqual(resolve_store(self.conn).pairs_raised, 2)
        self.assertEqual({(i["company_id"], i["evidence_key"]) for i in self.items("merge_candidate")},
                         {(a, str(b)), (a, str(c))})

    def test_two_different_fein_aliases_make_the_item_weak(self):
        a, b = self.company("Acme Robotics", "acme.io"), self.company("Acme Labs", "acme.io")
        add_alias(self.conn, a, "111111111", "fein")
        add_alias(self.conn, b, "222222222", "fein")
        resolve_store(self.conn)
        (item,) = self.items("merge_candidate")
        self.assertIn("Basis fein_conflict+domain, confidence weak", item["reason"])

    # The schema CHECK makes `merged_into` imply `status = 'removed'`, so the removed filter alone covers a merged
    # company; resolve keeps both conditions, and these two tests pin each way a company leaves the live set.
    def test_resolve_ignores_a_merged_company(self):
        a = self.company("Acme Robotics", "acme.io")
        b = self.company("Acme Labs", "acme.io")
        self.remove(b, into=a)
        mark_shared_key(self.conn, "domain", "veltrix.com", "2 employers: A, B")
        v = self.company("Veltrix Pharma", "veltrix.com")
        v2 = self.company("Veltrix Merged", "veltrix.com")
        self.remove(v2, into=v)
        report = resolve_store(self.conn)
        self.assertEqual((report.pairs_raised, report.size_checks_raised), (0, 1))
        self.assertEqual([i["company_id"] for i in self.items("size_check")], [v])
        self.assertEqual(self.items("merge_candidate"), [])

    def test_resolve_ignores_a_removed_company(self):
        self.company("Acme Robotics", "acme.io")
        c = self.company("Acme Gone", "acme.io")
        self.remove(c)
        mark_shared_key(self.conn, "domain", "veltrix.com", "2 employers: A, B")
        v = self.company("Veltrix Gone", "veltrix.com")
        self.remove(v)
        report = resolve_store(self.conn)
        self.assertEqual((report.pairs_raised, report.size_checks_raised), (0, 0))
        self.assertEqual(self.items("merge_candidate") + self.items("size_check"), [])

    def test_a_fein_on_one_side_only_keeps_basis_domain(self):
        a, b = self.company("Acme Robotics", "acme.io"), self.company("Acme Labs", "acme.io")
        add_alias(self.conn, a, "111111111", "fein")
        resolve_store(self.conn)
        (item,) = self.items("merge_candidate")
        self.assertIn("Basis domain, confidence strong", item["reason"])
        self.assertNotIn("fein_conflict", item["reason"])

    def test_a_shared_fein_does_not_count_as_a_conflict(self):
        # Two companies cannot hold one FEIN alias (alias_key is unique), so "same FEIN" is not a reachable case; a
        # shared FEIN is ignored like identity ignores it.
        a, b = self.company("Acme Robotics", "acme.io"), self.company("Acme Labs", "acme.io")
        add_alias(self.conn, a, "111111111", "fein")
        add_alias(self.conn, b, "222222222", "fein")
        mark_shared_key(self.conn, "fein", "222222222", "2 employers: A, B")
        resolve_store(self.conn)
        (item,) = self.items("merge_candidate")
        self.assertIn("Basis domain, confidence strong", item["reason"])

    def test_an_unshared_subdomain_is_not_paired_with_its_root(self):
        # Identity matches a candidate's domain exactly, so resolve pairs only the same domain text.
        self.company("Acme Robotics", "acme.io")
        self.company("Acme Labs", "eng.acme.io")
        self.assertEqual(resolve_store(self.conn).pairs_raised, 0)

    def test_a_rerun_after_the_anchor_merges_away_links_the_rest_and_keeps_the_old_items(self):
        a = self.company("Acme One", "acme.io")
        b = self.company("Acme Two", "acme.io")
        c = self.company("Acme Three", "acme.io")
        resolve_store(self.conn)
        self.remove(a, into=b)
        report = resolve_store(self.conn)
        self.assertEqual(report.pairs_raised, 1)
        self.assertEqual({(i["company_id"], i["evidence_key"]) for i in self.items("merge_candidate")},
                         {(a, str(b)), (a, str(c)), (b, str(c))})

    def test_a_resolved_pair_stays_resolved_and_raises_no_second_item(self):
        self.company("Acme Robotics", "acme.io")
        self.company("Acme Labs", "acme.io")
        resolve_store(self.conn)
        self.conn.execute("UPDATE review_item SET state = 'resolved', resolution = 'marked_ok', "
                          "resolved_at = '2026-10-22T00:00:00Z'")
        self.assertEqual(resolve_store(self.conn).pairs_raised, 0)
        (item,) = self.items("merge_candidate")
        self.assertEqual(item["state"], "resolved")


class SizeCheckTest(ResolveBase):
    def test_each_holder_of_a_shared_domain_gets_one_size_check(self):
        a, b = self.company("Veltrix Pharma", "veltrix.com"), self.company("Veltrix Labs", "veltrix.com")
        mark_shared_key(self.conn, "domain", "veltrix.com", "2 employers: Veltrix Labs, Veltrix Pharma")
        report = resolve_store(self.conn)
        self.assertEqual((report.pairs_raised, report.size_checks_raised), (0, 2))
        items = self.items("size_check")
        self.assertEqual([i["company_id"] for i in items], [a, b])
        for i in items:
            self.assertEqual((i["evidence_key"], i["raised_by"], i["state"]), ("shared-domain:veltrix.com",
                                                                               "source", "open"))
            self.assertIn("shares veltrix.com with 1 other employers: it may be a subsidiary; size unknown",
                          i["reason"])
            self.assertIn("Basis shared_domain, confidence weak", i["reason"])

    def test_n_counts_the_employers_the_shared_key_saw_not_only_the_stored_ones(self):
        self.company("Veltrix Pharma", "veltrix.com")
        mark_shared_key(self.conn, "domain", "veltrix.com", "3 employers: Veltrix Corp, Veltrix Labs, Veltrix Pharma")
        resolve_store(self.conn)
        (item,) = self.items("size_check")
        self.assertIn("shares veltrix.com with 2 other employers", item["reason"])

    def test_more_stored_holders_than_the_source_counted_win(self):
        for name in ("One", "Two", "Three"):
            self.company(f"Veltrix {name}", "veltrix.com")
        mark_shared_key(self.conn, "domain", "veltrix.com", "2 employers: Veltrix One, Veltrix Two")
        resolve_store(self.conn)
        for item in self.items("size_check"):
            self.assertIn("shares veltrix.com with 2 other employers", item["reason"])

    def test_an_unreadable_shared_reason_drops_the_number_and_never_prints_zero(self):
        self.company("Solo", "solo.io")
        mark_shared_key(self.conn, "domain", "solo.io", "seen on a few employers")
        resolve_store(self.conn)
        (item,) = self.items("size_check")
        self.assertIn("shares solo.io with other employers: it may be a subsidiary; size unknown", item["reason"])
        self.assertNotRegex(item["reason"], r"with \d+ other")

    def test_a_guess_domain_still_raises_a_size_check(self):
        self.company("Veltrix Pharma", "veltrix.com", confidence="guess")
        mark_shared_key(self.conn, "domain", "veltrix.com", "2 employers: A, B")
        self.assertEqual(resolve_store(self.conn).size_checks_raised, 1)

    def test_a_subdomain_of_a_shared_domain_is_shared_and_keys_on_the_shared_form(self):
        self.company("Veltrix Pharma", "veltrix.com", "eng.veltrix.com")
        mark_shared_key(self.conn, "domain", "veltrix.com", "2 employers: A, B")
        self.assertEqual(resolve_store(self.conn).size_checks_raised, 1)
        (item,) = self.items("size_check")
        self.assertEqual(item["evidence_key"], "shared-domain:veltrix.com")

    def test_an_unshared_domain_raises_no_size_check(self):
        self.company("Acme Robotics", "acme.io")
        self.assertEqual(resolve_store(self.conn).size_checks_raised, 0)

    def test_shared_domain_raises_size_check_not_merge_for_each_holder(self):
        run_discover(self.conn, [LcaSource(FILES)])
        before = self.conn.execute("SELECT COUNT(*) FROM company").fetchone()[0]
        report = resolve_store(self.conn)
        self.assertEqual((report.pairs_raised, report.size_checks_raised, report.failed), (0, 2, []))
        names = sorted(r[0] for r in self.conn.execute(
            "SELECT c.name FROM review_item r JOIN company c USING (company_id) WHERE r.kind = 'size_check'"))
        self.assertEqual(names, ["Veltrix Diagnostics Inc", "Veltrix Pharma Inc"])
        for item in self.items("size_check"):  # Veltrix Corp is a large filer: counted by the source, never stored
            self.assertIn("shares veltrix.com with 2 other employers: it may be a subsidiary; size unknown",
                          item["reason"])
        self.assertEqual(self.items("merge_candidate"), [])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM company").fetchone()[0], before)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM company WHERE merged_into IS NOT NULL")
                         .fetchone()[0], 0)


class ResolveJobTest(ResolveBase):
    def seed_mixed(self):
        self.company("Acme Robotics", "acme.io")
        self.company("Acme Labs", "acme.io")
        self.company("Veltrix Pharma", "veltrix.com")
        self.company("Veltrix Labs", "veltrix.com")
        mark_shared_key(self.conn, "domain", "veltrix.com", "2 employers: Veltrix Labs, Veltrix Pharma")

    def test_resolve_is_idempotent(self):
        self.seed_mixed()
        first = resolve_store(self.conn)
        self.assertEqual((first.pairs_raised, first.size_checks_raised), (1, 2))
        before = table_marks(self.conn)
        second = resolve_store(self.conn)
        self.assertEqual((second.pairs_raised, second.size_checks_raised), (0, 0))
        self.assertEqual(table_marks(self.conn), before)

    def test_resolve_changes_no_company_row(self):
        self.seed_mixed()
        self.company("Acme Gone", "acme.io")
        before = company_hash(self.conn)
        resolve_store(self.conn)
        self.assertEqual(company_hash(self.conn), before)

    def test_no_source_writes_size_evidence(self):
        run_discover(self.conn, [LcaSource(FILES)])
        run_resolve(self.conn)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM evidence WHERE fact IN ('size', 'headcount')").fetchone()[0], 0)
        self.assertEqual({r[0] for r in self.conn.execute("SELECT status FROM company")}, {"candidate"})

    def test_run_resolve_records_one_job_run(self):
        self.seed_mixed()
        run_id, report = run_resolve(self.conn)
        row = self.conn.execute("SELECT job, status, error, finished_at, summary FROM job_run WHERE run_id = ?",
                                (run_id,)).fetchone()
        self.assertEqual((row["job"], row["status"], row["error"]), ("resolve", "ok", ""))
        self.assertIsNotNone(row["finished_at"])
        self.assertEqual(json.loads(row["summary"]), {"pairs_raised": 1, "size_checks_raised": 2, "attempted": 3, "failed": []})
        self.assertEqual((report.pairs_raised, report.size_checks_raised), (1, 2))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM job_run WHERE job = 'resolve'").fetchone()[0], 1)

    def test_a_failure_on_one_pair_is_listed_and_the_pass_goes_on(self):
        a = self.company("Acme Robotics", "acme.io")
        b = self.company("Acme Labs", "acme.io")
        self.company("Beta One", "beta.io")
        self.company("Beta Two", "beta.io")
        real = identity.raise_merge_candidate

        def flaky(conn, x, y, *args, **kw):
            if {x, y} == {a, b}:
                raise sqlite3.IntegrityError("boom")
            return real(conn, x, y, *args, **kw)

        with mock.patch.object(identity, "raise_merge_candidate", flaky):
            run_id, report = run_resolve(self.conn)
        self.assertEqual(report.pairs_raised, 1)
        self.assertEqual(len(report.failed), 1)
        self.assertIn("boom", report.failed[0])
        self.assertIn(f"#{a}", report.failed[0])
        row = self.conn.execute("SELECT status, error FROM job_run WHERE run_id = ?", (run_id,)).fetchone()
        self.assertEqual(row["status"], "partial")
        self.assertIn("boom", row["error"])

    def test_a_failure_on_one_size_check_is_listed_and_the_pass_goes_on(self):
        self.seed_mixed()
        bad = {"n": 0}

        class Wrapper:
            """Fails the first size_check insert only; everything else is the real connection."""
            def __init__(self, conn):
                self._c = conn

            def execute(self, sql, *args):
                if "'size_check'" in sql and sql.lstrip().startswith("INSERT") and bad["n"] == 0:
                    bad["n"] += 1
                    raise sqlite3.IntegrityError("size boom")
                return self._c.execute(sql, *args)

            def __getattr__(self, name):
                return getattr(self._c, name)

        run_id, report = run_resolve(Wrapper(self.conn))
        self.assertEqual((report.pairs_raised, report.size_checks_raised, len(report.failed)), (1, 1, 1))
        self.assertIn("size boom", report.failed[0])
        self.assertEqual(self.conn.execute("SELECT status FROM job_run WHERE run_id = ?", (run_id,))
                         .fetchone()[0], "partial")

    def test_every_item_failing_is_failed_not_partial(self):
        self.seed_mixed()
        with mock.patch.object(identity, "raise_merge_candidate", side_effect=sqlite3.IntegrityError("no")), \
                mock.patch.object(identity, "atomic", side_effect=sqlite3.IntegrityError("no")):
            run_id, report = run_resolve(self.conn)
        self.assertEqual((report.attempted, len(report.failed)), (3, 3))
        self.assertEqual(self.conn.execute("SELECT status FROM job_run WHERE run_id = ?", (run_id,)).fetchone()[0],
                         "failed")

    def test_some_items_already_present_and_the_rest_failing_is_partial_not_failed(self):
        self.seed_mixed()
        resolve_store(self.conn)
        x = self.company("Beta One", "beta.io")
        y = self.company("Beta Two", "beta.io")
        real = identity.raise_merge_candidate

        def flaky(conn, a, b, *args, **kw):
            if {a, b} == {x, y}:
                raise sqlite3.IntegrityError("no")
            return real(conn, a, b, *args, **kw)

        with mock.patch.object(identity, "raise_merge_candidate", flaky):
            run_id, report = run_resolve(self.conn)
        self.assertEqual((report.attempted, len(report.failed)), (4, 1))
        self.assertEqual(self.conn.execute("SELECT status FROM job_run WHERE run_id = ?", (run_id,)).fetchone()[0],
                         "partial")

    def test_a_failing_finish_job_does_not_mask_the_original_error(self):
        with mock.patch("outreach.sources.jobs.resolve_store", side_effect=sqlite3.OperationalError("disk gone")), \
                mock.patch("outreach.sources.jobs.finish_job", side_effect=sqlite3.OperationalError("also gone")):
            with self.assertRaises(sqlite3.OperationalError) as ctx:
                run_resolve(self.conn)
        self.assertIn("disk gone", str(ctx.exception))

    def test_a_pass_that_cannot_run_is_failed_in_job_run_and_raises(self):
        with mock.patch("outreach.sources.jobs.resolve_store", side_effect=sqlite3.OperationalError("disk gone")):
            with self.assertRaises(sqlite3.OperationalError):
                run_resolve(self.conn)
        row = self.conn.execute("SELECT status, error, finished_at FROM job_run WHERE job = 'resolve'").fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertIn("disk gone", row["error"])
        self.assertIsNotNone(row["finished_at"])


if __name__ == "__main__":
    unittest.main()
