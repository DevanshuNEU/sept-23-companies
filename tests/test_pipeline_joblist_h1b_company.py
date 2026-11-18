"""The job list's H-1B chips and One per company (#309): two filters in the one query, no query per row.

A fresh store, a temp H-1B folder and rows written by hand: nothing here reaches the network or a live path.
"""
import os
import tempfile
from pathlib import Path
from unittest import mock

from outreach.h1b import lookup
from outreach.pipeline.joblist import JobFilters
from tests.test_pipeline_joblist import JobListCase

H1B_FILE = """# H-1B approvals by company

| Company | FY2022 | FY2023 | FY2024 | FY2025 | FY2026 | Trend | USCIS name |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Acme | 0 | 1 | 2 | 4 | 7 | rising | ACME INC |
| Dormant | 3 | 0 | 0 | 0 | 0 | stopped | DORMANT INC |
| Zero Co | 0 | 0 | 0 | 0 | 0 | none | ZERO CO |
"""


class H1bCase(JobListCase):
    def setUp(self):
        super().setUp()
        self.dir = Path(tempfile.mkdtemp(prefix="outreach-h1b-joblist-"))
        patch = mock.patch.dict(os.environ, {"OUTREACH_H1B_DIR": str(self.dir)})
        patch.start()
        self.addCleanup(patch.stop)
        lookup.forget()
        self.addCleanup(lookup.forget)

    def build(self, text=H1B_FILE):
        (self.dir / "companies.md").write_text(text, encoding="utf-8")
        lookup.forget()


class H1bFilterTests(H1bCase):
    def fixture(self):
        self.build()
        self.acme = self.job(self.company("Acme", size=40), "Acme job")
        self.dormant = self.job(self.company("Dormant", size=40), "Dormant job")
        self.nobody = self.job(self.company("Nobody", size=40), "Nobody job")
        self.zero = self.job(self.company("Zero Co", size=40), "Zero job")

    def test_any_is_the_default_and_keeps_every_company(self):
        self.fixture()
        self.assertEqual(JobFilters().h1b, "any")
        self.assertEqual(sorted(self.ids()), sorted([self.acme, self.dormant, self.nobody, self.zero]))

    def test_has_keeps_every_company_with_approvals_whatever_the_trend(self):
        self.fixture()
        self.assertEqual(sorted(self.ids(h1b="has")), sorted([self.acme, self.dormant]))

    def test_none_keeps_the_companies_the_list_does_not_hold(self):
        self.fixture()
        self.assertEqual(sorted(self.ids(h1b="none")), sorted([self.nobody, self.zero]))

    def test_has_and_none_split_the_list_with_nothing_lost_or_doubled(self):
        self.fixture()
        both = self.ids(h1b="has") + self.ids(h1b="none")
        self.assertEqual(sorted(both), sorted(self.ids()))

    def test_the_chip_agrees_with_the_tag_on_a_name_the_key_folds(self):
        self.build()
        pid = self.job(self.company("Acme, Inc."), "Folded name")
        self.assertEqual(self.ids(h1b="has"), [pid])

    def test_the_tab_counts_follow_the_chip(self):
        self.fixture()
        higher = self.job(self.company("Nobody Else", size=40), "Higher", tier="swe2", ok=0, level_state="reject")
        page = self.run_query(h1b="has")
        self.assertEqual(page.counts, {"your_level": 2, "higher": 0, "other": 0})
        self.assertEqual(self.run_query(h1b="none").counts, {"your_level": 2, "higher": 1, "other": 0})
        self.assertEqual(self.ids(h1b="none", view="higher"), [higher])

    def test_it_works_on_the_other_tabs_too(self):
        self.build()
        has = self.job(self.company("Acme", size=40), "Higher at Acme", tier="swe2", ok=0, level_state="reject")
        self.job(self.company("Nobody", size=40), "Higher at Nobody", tier="swe2", ok=0, level_state="reject")
        self.assertEqual(self.ids(view="higher", h1b="has"), [has])

    def test_with_no_list_built_has_and_none_are_refused_not_empty(self):
        self.job(self.company("Acme", size=40))
        for choice in ("has", "none"):
            with self.assertRaises(ValueError) as ctx:
                self.ids(h1b=choice)
            self.assertIn("H-1B list", str(ctx.exception))
        self.assertEqual(len(self.ids()), 1)  # any still lists

    def test_an_unknown_choice_is_refused(self):
        with self.assertRaises(ValueError):
            self.ids(h1b="maybe")


class OnePerCompanyTests(H1bCase):
    def test_off_lists_every_posting_and_says_no_more_roles(self):
        co = self.company(size=40)
        a, b = self.job(co, "A", posted="2026-11-02"), self.job(co, "B", posted="2026-11-01")
        page = self.run_query()
        self.assertEqual([r.posting_id for r in page.rows], [a, b])
        self.assertEqual([r.more_roles for r in page.rows], [0, 0])

    def test_a_company_shows_once_with_its_newest_posting_and_a_count_of_the_rest(self):
        co = self.company(size=40)
        old, new, mid = (self.job(co, "Old", posted="2026-10-20"), self.job(co, "New", posted="2026-11-02"),
                         self.job(co, "Mid", posted="2026-10-28"))
        page = self.run_query(one_per_company=True)
        self.assertEqual([(r.posting_id, r.more_roles) for r in page.rows], [(new, 2)])
        self.assertEqual(page.total, 1)
        self.assertEqual(sorted(self.ids()), sorted([old, new, mid]))

    def test_companies_are_ordered_by_the_posting_shown(self):
        a, b, c = (self.company(size=40) for _ in range(3))
        a_old, a_new = self.job(a, "A old", posted="2026-10-01"), self.job(a, "A new", posted="2026-11-02")
        b_one = self.job(b, "B", posted="2026-11-01")
        c_one = self.job(c, "C", posted="2026-10-15")
        self.assertEqual(self.ids(one_per_company=True), [a_new, b_one, c_one])
        self.assertNotIn(a_old, self.ids(one_per_company=True))

    def test_the_window_decides_which_postings_count(self):
        co = self.company(size=40)
        new = self.job(co, "New", posted="2026-11-02")
        self.job(co, "Outside", posted="2026-08-01")
        page = self.run_query(one_per_company=True)
        self.assertEqual([(r.posting_id, r.more_roles) for r in page.rows], [(new, 0)])
        wide = self.run_query(one_per_company=True, window_days=0)
        self.assertEqual([(r.posting_id, r.more_roles) for r in wide.rows], [(new, 1)])

    def test_a_company_whose_newest_posting_is_outside_the_window_is_not_shown(self):
        co = self.company(size=40)
        self.job(co, "Old", posted="2026-08-01")
        self.assertEqual(self.ids(one_per_company=True), [])

    def test_the_level_chips_decide_the_posting_shown(self):
        co = self.company(size=40)
        ng = self.job(co, "NG", posted="2026-10-30", tier="new_grad")
        self.job(co, "S1", posted="2026-11-02", tier="swe1")
        page = self.run_query(one_per_company=True, levels=frozenset({"new_grad"}))
        self.assertEqual([(r.posting_id, r.more_roles) for r in page.rows], [(ng, 0)])

    def test_each_tab_counts_companies_and_shows_a_company_once_per_tab(self):
        co, other = self.company(size=40), self.company(size=40)
        mine = self.job(co, "Mine", posted="2026-11-02")
        self.job(co, "Mine too", posted="2026-11-01")
        higher = self.job(co, "Higher", posted="2026-11-02", tier="swe2", ok=0, level_state="reject")
        self.job(other, "Other's", posted="2026-11-02")
        page = self.run_query(one_per_company=True)
        self.assertEqual(page.counts, {"your_level": 2, "higher": 1, "other": 0})
        self.assertEqual(self.ids(one_per_company=True, view="higher"), [higher])
        self.assertIn(mine, [r.posting_id for r in page.rows])

    def test_a_dated_posting_is_shown_before_an_undated_one(self):
        co = self.company(size=40)
        self.job(co, "Undated", posted=None, list_at=None)
        dated = self.job(co, "Dated", posted="2026-10-01")
        page = self.run_query(one_per_company=True, window_days=0)
        self.assertEqual([(r.posting_id, r.more_roles) for r in page.rows], [(dated, 1)])

    def test_a_merged_company_counts_as_its_survivor(self):
        from tests.ranking_store import merge
        survivor, dupe = self.company("Firm", size=40), self.company("Firm Inc", size=40)
        merge(self.conn, dupe, survivor)
        a, b = self.job(survivor, "A", posted="2026-11-02"), self.job(dupe, "B", posted="2026-11-01")
        page = self.run_query(one_per_company=True)
        self.assertEqual([(r.posting_id, r.more_roles) for r in page.rows], [(a, 1)])

    def test_it_pages_by_cursor_without_a_repeat_or_a_gap(self):
        cos = [self.company(size=40) for _ in range(5)]
        shown = [self.job(co, "New", posted=f"2026-11-0{i + 1}") for i, co in enumerate(cos)][::-1]
        for co in cos:
            self.job(co, "Older", posted="2026-10-10")
        seen, after = [], None
        for _ in range(10):
            page = self.run_query(one_per_company=True, limit=2, after=after)
            seen += [r.posting_id for r in page.rows]
            after = page.next_after
            if after is None:
                break
        self.assertEqual(seen, shown)

    def test_the_company_filter_lists_every_posting_of_one_company(self):
        co, other = self.company(size=40), self.company(size=40)
        a, b = self.job(co, "A", posted="2026-11-02"), self.job(co, "B", posted="2026-11-01")
        self.job(other, "Other's", posted="2026-11-02")
        self.assertEqual(self.ids(company_id=co), [a, b])
        self.assertEqual(self.run_query(company_id=co).total, 2)

    def test_the_company_filter_follows_a_merge(self):
        from tests.ranking_store import merge
        survivor, dupe = self.company("Firm", size=40), self.company("Firm Inc", size=40)
        merge(self.conn, dupe, survivor)
        a, b = self.job(survivor, "A", posted="2026-11-02"), self.job(dupe, "B", posted="2026-11-01")
        self.assertEqual(self.ids(company_id=survivor), [a, b])

    def test_it_combines_with_the_h1b_chip(self):
        self.build()
        acme = self.company("Acme", size=40)
        nobody = self.company("Nobody", size=40)
        shown = self.job(acme, "Acme new", posted="2026-11-02")
        self.job(acme, "Acme old", posted="2026-11-01")
        self.job(nobody, "Nobody", posted="2026-11-02")
        page = self.run_query(one_per_company=True, h1b="has")
        self.assertEqual([(r.posting_id, r.more_roles) for r in page.rows], [(shown, 1)])
        self.assertEqual(page.counts["your_level"], 1)

    def test_search_and_not_contacted_still_apply(self):
        a, b = self.company("Alpha", size=40), self.company("Beta", size=40)
        self.job(a, "A1")
        self.job(b, "B1")
        self.assertEqual(len(self.ids(one_per_company=True, q="alp")), 1)
