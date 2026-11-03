"""The job list query (#156, Task 1): open engineering postings at his level, newest first, with the facts he checks.

A fresh store and rows written by hand: nothing here reaches the network or the live store.
"""
import itertools
import unittest
from datetime import date, timedelta

from outreach.pipeline import joblist
from outreach.pipeline.joblist import JobFilters, JobRow, passed_posting_ids, query
from outreach.store import append_event
from outreach.store.timefmt import utcnow
from tests.plan_seed import seed_outbound, seed_person
from tests.ranking_store import decision, evidence, merge, no_go, posting
from tests.storetest import fresh_store, seed_company

NOW = "2026-11-03T15:00:00Z"  # 10:00 on 2026-11-03 in New York
_names = itertools.count(1)


class JobListCase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)

    def company(self, name=None, *, size=None, size_low=None, everify=False, yc=False):
        cid = seed_company(self.conn, name or f"Company {next(_names)}")
        if size is not None:
            evidence(self.conn, cid, "size", num_low=size_low if size_low is not None else size, num_high=size)
        elif size_low is not None:
            evidence(self.conn, cid, "size", num_low=size_low)
        if everify:
            self.conn.execute(
                "INSERT INTO evidence(company_id, fact, value, confidence, source_kind, observed_on, recorded_at) "
                "VALUES (?, 'everify', 'enrolled', 'confirmed', 'everify', '2026-10-01', ?)", (cid, utcnow()))
        if yc:
            evidence(self.conn, cid, "stage", value="yc")
        return cid

    def job(self, company, title="Software Engineer", *, posted="2026-11-02", seen="2026-11-01T12:00:00Z",
            tier="new_grad", ok=1, **dec):
        pid = posting(self.conn, company, title, posted_on=posted, first_seen_at=seen, last_seen_at=seen)
        decision(self.conn, pid, level_tier=tier, level_ok=ok, **dec)
        return pid

    def run_query(self, *, after=None, limit=50, now=NOW, **filters):
        return query(self.conn, now, JobFilters(**filters), after, limit)

    def ids(self, **kw):
        return [r.posting_id for r in self.run_query(**kw).rows]


class ListedTests(JobListCase):
    def test_a_plain_open_engineering_posting_is_listed(self):
        pid = self.job(self.company())
        self.assertEqual(self.ids(), [pid])

    def test_a_closed_posting_is_not_listed(self):
        co = self.company()
        closed = posting(self.conn, co, posted_on="2026-11-02", closed=True)
        decision(self.conn, closed)
        self.assertEqual(self.ids(), [])

    def test_a_posting_with_no_decision_is_not_listed(self):
        posting(self.conn, self.company(), posted_on="2026-11-02")
        self.assertEqual(self.ids(), [])

    def test_a_decision_under_an_old_ruleset_is_not_read(self):
        co = self.company()
        pid = posting(self.conn, co, posted_on="2026-11-02")
        decision(self.conn, pid)
        self.conn.execute("INSERT INTO ruleset(ruleset_version, config_toml, created_at, is_current) "
                          "VALUES ('r-0000000000b2', '', ?, 0)", (utcnow(),))
        self.conn.execute("UPDATE ruleset SET is_current = 0")
        self.conn.execute("UPDATE ruleset SET is_current = 1 WHERE ruleset_version = 'r-0000000000b2'")
        self.assertEqual(self.ids(), [])

    def test_senior_non_engineering_and_non_us_are_gone(self):
        co = self.company()
        self.job(co, tier="senior", ok=0)
        self.job(co, lane="not_engineering")
        self.job(co, lane="intern")
        self.job(co, lane="off_lane")
        self.job(co, us="no")
        self.assertEqual(self.ids(), [])
        self.assertEqual(self.ids(view="higher"), [])

    def test_unknown_us_is_listed(self):
        pid = self.job(self.company(), us="unknown")
        self.assertEqual(self.ids(), [pid])

    def test_citizenship_and_excluded_eligibility_are_gone(self):
        co = self.company()
        self.job(co, citizenship=1)
        self.job(co, eligibility="excluded")
        self.assertEqual(self.ids(), [])

    def test_no_go_company_is_gone_and_a_lifted_one_is_back(self):
        gone, back = self.company("Gone"), self.company("Back")
        self.job(gone)
        kept = self.job(back)
        no_go(self.conn, gone)
        no_go(self.conn, back, lifted=True)
        self.assertEqual(self.ids(), [kept])

    def test_a_merged_away_company_follows_its_survivor(self):
        survivor, dupe = self.company("Survivor", size=40, yc=True), self.company("Survivor Inc")
        pid = self.job(dupe)
        merge(self.conn, dupe, survivor)
        row = self.run_query().rows[0]
        self.assertEqual((row.posting_id, row.company_id, row.company_name, row.size, row.yc),
                         (pid, survivor, "Survivor", 40, True))

    def test_a_merged_away_company_is_gone_when_its_survivor_is_no_go(self):
        survivor, dupe = self.company("Survivor"), self.company("Survivor Inc")
        self.job(dupe)
        merge(self.conn, dupe, survivor)
        no_go(self.conn, survivor)
        self.assertEqual(self.ids(), [])

    def test_a_passed_posting_is_gone_and_its_company_stays(self):
        co = self.company()
        gone, kept = self.job(co, posted="2026-11-02"), self.job(co, posted="2026-11-01")
        append_event(self.conn, entity="posting", entity_id=gone, kind="posting_passed", actor="devanshu",
                     detail={"posting_id": gone, "company_id": co})
        self.assertEqual(self.ids(), [kept])

    def test_a_role_pass_names_its_posting_but_other_reasons_do_not(self):
        co = self.company()
        gone, kept = self.job(co, posted="2026-11-02"), self.job(co, posted="2026-11-01")
        append_event(self.conn, entity="company", entity_id=co, company_id=co, kind="pick_passed", actor="devanshu",
                     detail={"reason": "role", "posting_id": gone})
        append_event(self.conn, entity="company", entity_id=co, company_id=co, kind="pick_passed", actor="devanshu",
                     detail={"reason": "visa", "posting_id": kept})
        self.assertEqual(self.ids(), [kept])
        self.assertEqual(passed_posting_ids(self.conn), frozenset({gone}))

    def test_passed_posting_ids_reads_both_event_kinds(self):
        co = self.company()
        a, b = self.job(co), self.job(co)
        append_event(self.conn, entity="posting", entity_id=a, kind="posting_passed", actor="devanshu",
                     detail={"posting_id": a, "company_id": co})
        append_event(self.conn, entity="company", entity_id=co, company_id=co, kind="pick_passed", actor="devanshu",
                     detail={"reason": "role", "posting_id": b})
        append_event(self.conn, entity="company", entity_id=co, company_id=co, kind="pick_passed", actor="devanshu",
                     detail={"reason": "role"})
        self.assertEqual(passed_posting_ids(self.conn), frozenset({a, b}))


class LevelTests(JobListCase):
    def test_your_level_is_level_ok(self):
        co = self.company(size=50)
        ng, ec, s1, s2 = (self.job(co, tier=t) for t in ("new_grad", "early_career", "swe1", "swe2"))
        too_senior = self.job(co, tier="swe2", ok=0, years_asked=5, years_quote="5 years")
        tags = {r.posting_id: r.level_tag for r in self.run_query().rows}
        self.assertEqual(tags, {ng: "new grad", ec: "early career", s1: "SDE 1", s2: "SDE 2"})
        self.assertEqual(self.ids(view="higher"), [too_senior])

    def test_level_not_stated_counts_at_a_small_company_and_is_tagged(self):
        co = self.company(size=300)
        pid = self.job(co, tier="not_stated", ok=0)
        opened = self.job(co, tier="open", ok=0)
        rows = {r.posting_id: r for r in self.run_query().rows}
        self.assertEqual({k: v.level_tag for k, v in rows.items()}, {pid: "level not stated", opened: "level not stated"})
        self.assertEqual(self.ids(view="higher"), [])

    def test_level_not_stated_counts_at_a_company_of_unknown_size(self):
        pid = self.job(self.company(), tier="not_stated", ok=0)
        self.assertEqual(self.ids(), [pid])

    def test_level_not_stated_at_a_big_company_is_higher(self):
        pid = self.job(self.company(size=301), tier="not_stated", ok=0)
        self.assertEqual(self.ids(), [])
        self.assertEqual(self.ids(view="higher"), [pid])

    def test_a_big_company_keeps_its_level_ok_postings_in_your_level(self):
        pid = self.job(self.company(size=5000))
        self.assertEqual(self.ids(), [pid])
        self.assertEqual(self.ids(view="higher"), [])

    def test_counts_show_both_views_for_the_window(self):
        co = self.company(size=50)
        self.job(co)
        self.job(co)
        self.job(co, tier="swe2", ok=0)
        old = self.job(co, posted="2026-08-01")
        page = self.run_query()
        self.assertEqual((page.total, page.counts), (2, {"your_level": 2, "higher": 1}))
        page = self.run_query(view="higher")
        self.assertEqual((page.total, page.counts), (1, {"your_level": 2, "higher": 1}))
        page = self.run_query(window_days=0)
        self.assertEqual(page.counts, {"your_level": 3, "higher": 1})
        self.assertIn(old, self.ids(window_days=0))

    def test_counts_follow_the_other_filters(self):
        small, big = self.company(size=20), self.company(size=900)
        self.job(small)
        self.job(big)
        self.job(big, tier="swe2", ok=0)
        self.assertEqual(self.run_query(size="small").counts, {"your_level": 1, "higher": 0})
        self.assertEqual(self.run_query(size="big").counts, {"your_level": 1, "higher": 1})


class DateTests(JobListCase):
    def test_posted_date_wins_and_is_labeled_posted(self):
        self.job(self.company(), posted="2026-10-30", seen="2026-11-02T12:00:00Z")
        row = self.run_query().rows[0]
        self.assertEqual((row.date, row.date_kind, row.age_days), ("2026-10-30", "posted", 4))

    def test_no_posted_date_uses_first_sight_labeled_seen_never_posted(self):
        self.job(self.company(), posted=None, seen="2026-11-03T13:00:00Z")
        row = self.run_query().rows[0]
        self.assertEqual((row.date, row.date_kind, row.age_days), ("2026-11-03", "seen", 0))

    def test_the_seen_date_is_a_new_york_day(self):
        # The clocks went back on Nov 1, so 02:30Z on Nov 3 is 21:30 on Nov 2 in New York; in July it is 22:30.
        self.job(self.company(), posted=None, seen="2026-11-03T02:30:00Z")
        self.assertEqual(self.run_query().rows[0].date, "2026-11-02")
        self.job(self.company(), posted=None, seen="2026-07-15T02:30:00Z")
        self.assertEqual(self.run_query(window_days=0).rows[-1].date, "2026-07-14")

    def test_age_counts_new_york_days_not_hours(self):
        self.job(self.company(), posted="2026-11-02")
        # 04:30Z on Nov 3 is 23:30 on Nov 2 in New York: still yesterday's date there, so age 1 at 10:00 Nov 3
        # becomes age 0 at 23:30 Nov 2.
        self.assertEqual(self.run_query(now="2026-11-03T04:30:00Z").rows[0].age_days, 0)
        self.assertEqual(self.run_query(now="2026-11-03T05:30:00Z").rows[0].age_days, 1)

    def test_a_date_ahead_of_today_has_age_zero(self):
        self.job(self.company(), posted="2026-11-05")
        self.assertEqual(self.run_query().rows[0].age_days, 0)

    def test_window_edges(self):
        co = self.company()
        by_age = {n: self.job(co, posted=(date(2026, 11, 3) - timedelta(days=n)).isoformat())
                  for n in (0, 7, 8, 30, 31, 60, 61, 90, 91)}
        self.assertEqual(self.ids(window_days=7), [by_age[0], by_age[7]])
        self.assertEqual(self.ids(window_days=30), [by_age[n] for n in (0, 7, 8, 30)])
        self.assertEqual(self.ids(window_days=60), [by_age[n] for n in (0, 7, 8, 30, 31, 60)])
        self.assertEqual(self.ids(window_days=90), [by_age[n] for n in (0, 7, 8, 30, 31, 60, 61, 90)])
        self.assertEqual(self.ids(window_days=0), [by_age[n] for n in (0, 7, 8, 30, 31, 60, 61, 90, 91)])

    def test_window_applies_to_the_seen_date_when_there_is_no_posted_date(self):
        co = self.company()
        fresh = self.job(co, posted=None, seen="2026-10-30T12:00:00Z")
        stale = self.job(co, posted=None, seen="2026-08-30T12:00:00Z")
        self.assertEqual(self.ids(window_days=7), [fresh])
        self.assertEqual(self.ids(window_days=0), [fresh, stale])


class OrderTests(JobListCase):
    def test_newest_first_then_smaller_company_then_posting_id(self):
        small, mid, big, unknown = (self.company(size=s) for s in (10, 200, 5000, None))
        older = self.job(small, posted="2026-10-30")
        on_big = self.job(big, posted="2026-11-02")
        on_unknown = self.job(unknown, posted="2026-11-02")
        on_mid = self.job(mid, posted="2026-11-02")
        on_small = self.job(small, posted="2026-11-02")
        same_small = self.job(small, posted="2026-11-02")
        newest = self.job(big, posted="2026-11-03")
        self.assertEqual(self.ids(), [newest, on_small, same_small, on_mid, on_unknown, on_big, older])

    def test_a_seen_only_posting_sorts_by_its_first_sight_among_dated_ones(self):
        co = self.company()
        posted = self.job(co, posted="2026-11-01")
        seen_later = self.job(co, posted=None, seen="2026-11-02T12:00:00Z")
        self.assertEqual(self.ids(), [seen_later, posted])


class FilterTests(JobListCase):
    def test_size_filter(self):
        small, edge, big, unknown = (self.company(size=s) for s in (50, 300, 301, None))
        a, b, c, d = (self.job(co) for co in (small, edge, big, unknown))
        self.assertEqual(sorted(self.ids(size="small")), sorted([a, b]))
        self.assertEqual(self.ids(size="big"), [c])
        self.assertEqual(self.ids(size="unknown"), [d])
        self.assertEqual(sorted(self.ids(size="any")), sorted([a, b, c, d]))

    def test_size_is_num_high_else_num_low(self):
        high = self.company(size=250, size_low=100)
        low_only = self.company(size_low=400)
        a, b = self.job(high), self.job(low_only)
        rows = {r.posting_id: r for r in self.run_query().rows}
        self.assertEqual((rows[a].size, rows[a].big), (250, False))
        self.assertEqual((rows[b].size, rows[b].big), (400, True))

    def test_unknown_size_is_not_big(self):
        self.job(self.company())
        row = self.run_query().rows[0]
        self.assertEqual((row.size, row.big), (None, False))

    def test_not_contacted_filter_hides_reached_companies_only_when_asked(self):
        reached, fresh = self.company("Reached"), self.company("Fresh")
        a, b = self.job(reached), self.job(fresh)
        seed_outbound(self.conn, seed_person(self.conn, reached, "Ada"), reached, "linkedin_request", date(2026, 10, 20))
        self.assertEqual(sorted(self.ids()), sorted([a, b]))
        self.assertEqual(self.ids(not_contacted=True), [b])

    def test_contacted_on_is_shown_on_the_row(self):
        reached = self.company("Reached")
        self.job(reached)
        person = seed_person(self.conn, reached, "Ada")
        seed_outbound(self.conn, person, reached, "linkedin_request", date(2026, 10, 20))
        seed_outbound(self.conn, person, reached, "linkedin_message", date(2026, 10, 25))
        self.assertEqual(self.run_query().rows[0].contacted_on, "2026-10-25")

    def test_an_application_is_not_contact(self):
        co = self.company()
        self.job(co)
        self.conn.execute(
            "INSERT INTO application(company_id, applied_on) VALUES (?, '2026-10-20')", (co,))
        self.assertIsNone(self.run_query().rows[0].contacted_on)

    def test_name_search(self):
        acme, other = self.company("Acme Robotics, Inc."), self.company("Globex")
        a = self.job(acme)
        self.job(other)
        self.assertEqual(self.ids(q="acme"), [a])
        self.assertEqual(self.ids(q="  ROBOT "), [a])
        self.assertEqual(self.ids(q="nothing"), [])

    def test_name_search_treats_wildcards_as_text(self):
        self.job(self.company("Acme"))
        self.assertEqual(self.ids(q="%"), [])
        self.assertEqual(self.ids(q="_"), [])


class FlagTests(JobListCase):
    def test_sponsorship_no_is_shown_not_hidden(self):
        pid = self.job(self.company(), sponsorship="no")
        other = self.job(self.company(), sponsorship="yes")
        rows = {r.posting_id: r for r in self.run_query().rows}
        self.assertTrue(rows[pid].says_no_sponsorship)
        self.assertFalse(rows[other].says_no_sponsorship)

    def test_everify_and_yc_badges(self):
        plain, badged = self.company(), self.company(everify=True, yc=True)
        a, b = self.job(plain), self.job(badged)
        rows = {r.posting_id: r for r in self.run_query().rows}
        self.assertEqual((rows[a].everify, rows[a].yc), (False, False))
        self.assertEqual((rows[b].everify, rows[b].yc), (True, True))

    def test_a_not_enrolled_company_has_no_badge(self):
        co = seed_company(self.conn, "NotIn")
        self.conn.execute(
            "INSERT INTO evidence(company_id, fact, value, confidence, source_kind, observed_on, recorded_at) "
            "VALUES (?, 'everify', 'not_enrolled', 'confirmed', 'everify', '2026-10-01', ?)", (co, utcnow()))
        self.job(co)
        self.assertFalse(self.run_query().rows[0].everify)

    def test_row_fields(self):
        co = self.company("Acme", size=40)
        pid = posting(self.conn, co, "Backend Engineer", location="Austin, TX", posted_on="2026-11-01")
        self.conn.execute("UPDATE posting SET url = 'https://example.com/j/1' WHERE posting_id = ?", (pid,))
        decision(self.conn, pid, level_tier="swe1")
        row = self.run_query().rows[0]
        self.assertIsInstance(row, JobRow)
        self.assertEqual((row.posting_id, row.title, row.url, row.location, row.level_tag, row.company_id,
                          row.company_name, row.size, row.big, row.contacted_on),
                         (pid, "Backend Engineer", "https://example.com/j/1", "Austin, TX", "SDE 1", co, "Acme", 40,
                          False, None))


class PagingTests(JobListCase):
    def test_paging_by_cursor(self):
        small, big, unknown = self.company(size=10), self.company(size=900), self.company()
        made = []
        for day in range(1, 4):
            for co in (big, unknown, small):
                made += [self.job(co, posted=f"2026-11-0{day}"), self.job(co, posted=f"2026-11-0{day}")]
        full = self.ids(limit=100)
        self.assertEqual(len(full), 18)
        seen, after, pages = [], None, 0
        while True:
            page = self.run_query(after=after, limit=4)
            pages += 1
            self.assertEqual(page.total, 18)
            self.assertLessEqual(len(page.rows), 4)
            seen += [r.posting_id for r in page.rows]
            if page.next_after is None:
                break
            self.assertEqual(len(page.rows), 4)
            after = page.next_after
        self.assertEqual(seen, full)  # disjoint pages that add up to the list, in order
        self.assertEqual(len(set(seen)), 18)
        self.assertEqual(pages, 5)

    def test_next_after_is_none_when_the_list_fits_one_page(self):
        self.job(self.company())
        page = self.run_query(limit=1)
        self.assertIsNone(page.next_after)

    def test_a_write_between_pages_never_repeats_or_skips(self):
        co = self.company()
        pids = [self.job(co, posted=f"2026-10-{20 + i}") for i in range(6)]  # oldest first
        first = self.run_query(limit=2)
        newest = self.job(co, posted="2026-11-03")  # a newer posting arrives after page one
        second = self.run_query(after=first.next_after, limit=2)
        self.assertEqual([r.posting_id for r in first.rows], pids[::-1][:2])
        self.assertEqual([r.posting_id for r in second.rows], pids[::-1][2:4])
        self.assertNotIn(newest, [r.posting_id for r in second.rows])

    def test_a_malformed_cursor_raises_value_error(self):
        self.job(self.company())
        for bad in ("", "nope", "2026-11-02|x|1", "!!!!", "2026-11-02|1"):
            with self.assertRaises(ValueError, msg=bad):
                self.run_query(after=bad)

    def test_limit_must_be_positive(self):
        with self.assertRaises(ValueError):
            self.run_query(limit=0)

    def test_bad_filters_raise_value_error(self):
        for bad in (dict(view="all"), dict(window_days=45), dict(size="huge")):
            with self.assertRaises(ValueError, msg=str(bad)):
                self.run_query(**bad)


class QueryPlanTests(JobListCase):
    def test_query_plan_starts_from_open_postings(self):
        co = self.company(size=20)
        for _ in range(30):
            self.job(co)
        plans = joblist.explain(self.conn, NOW, JobFilters(), None, 50)
        text = "\n".join(plans)
        # The postings and decisions are read through an index or primary key, never a table scan; the only scans are
        # of the small JSON lists the query is given and of its own materialized sets (cand, co).
        for line in plans:
            if line.startswith("SCAN"):
                self.assertRegex(line, r"SCAN ((json_each|cn|x|c|cand|co|rows|canon)\b|\(subquery-\d+\))", msg=text)
        self.assertRegex(text, r"SEARCH (d USING INDEX decision_version|p USING INDEX posting_)")
        self.assertIn("SEARCH p USING INTEGER PRIMARY KEY", text)
        self.assertNotIn("current_decision", text)


class ScaleTests(JobListCase):
    def test_thousands_of_postings_in_one_query(self):
        companies = [self.company(size=s) for s in (10, 80, 250, 900, None)]
        for i in range(3000):
            tier, ok = (("new_grad", 1), ("swe2", 0), ("not_stated", 0))[i % 3]
            self.job(companies[i % 5], posted=f"2026-10-{1 + i % 28:02d}", tier=tier, ok=ok)
        page = self.run_query(window_days=0)
        self.assertEqual(len(page.rows), 50)
        # new grad rows are all his level (1000); not stated counts at the four small or unknown companies (800 of
        # 1000); SDE 2 asking too much (1000) and not stated at the big one (200) are higher.
        self.assertEqual(page.counts, {"your_level": 1800, "higher": 1200})
        self.assertEqual(page.total, 1800)


if __name__ == "__main__":
    unittest.main()
