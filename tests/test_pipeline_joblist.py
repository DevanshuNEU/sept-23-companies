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
from tests.plan_seed import seed_email_touch, seed_message, seed_outbound, seed_person
from tests.ranking_store import BACKFILL, board, decision, evidence, merge, no_go, posting
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

    def job(self, company, title="Software Engineer", *, posted="2026-11-02", seen=None, tier="new_grad", ok=1,
            board_id=None, posted_at=None, seen_gap_s=None, repost_of=None, list_at=BACKFILL, **dec):
        """A listed posting. First seen at noon UTC on its posted day unless `seen` says otherwise; its list time is
        what the 0009 backfill gives those dates unless `list_at` says otherwise (None: Date unknown)."""
        seen = seen or (f"{posted}T12:00:00Z" if posted else "2026-11-01T12:00:00Z")
        pid = posting(self.conn, company, title, posted_on=posted, first_seen_at=seen, last_seen_at=seen,
                      board_id=board_id, posted_at=posted_at, seen_gap_s=seen_gap_s, repost_of=repost_of,
                      list_at=list_at)
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
    def test_your_level_is_the_fit_postings(self):
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
        self.assertEqual((page.total, page.counts), (2, {"your_level": 2, "higher": 1, "other": 0}))
        page = self.run_query(view="higher")
        self.assertEqual((page.total, page.counts), (1, {"your_level": 2, "higher": 1, "other": 0}))
        page = self.run_query(window_days=0)
        self.assertEqual(page.counts, {"your_level": 3, "higher": 1, "other": 0})
        self.assertIn(old, self.ids(window_days=0))

    def test_counts_follow_the_other_filters(self):
        small, big = self.company(size=20), self.company(size=900)
        self.job(small)
        self.job(big)
        self.job(big, tier="swe2", ok=0)
        self.assertEqual(self.run_query(size="small").counts, {"your_level": 1, "higher": 0, "other": 0})
        self.assertEqual(self.run_query(size="big").counts, {"your_level": 1, "higher": 1, "other": 0})


class RoleTests(JobListCase):
    """#250: only software roles are on the two level tabs; every other listed posting is on Other roles."""

    def test_other_roles_are_their_own_group(self):
        co = self.company(size=50)
        sw = self.job(co)
        other = self.job(co, "Data Scientist I", role="other")
        higher = self.job(co, tier="swe2", ok=0, level_state="reject", role="software")
        unset = self.job(co, "Software Engineer II")  # role NULL: stored before 0014, not rebuilt yet
        self.assertEqual(sorted(self.ids()), sorted([sw, unset]))
        self.assertEqual(self.ids(view="higher"), [higher])
        self.assertEqual(self.ids(view="other"), [other])
        for view in ("your_level", "higher", "other"):
            with self.subTest(view=view):
                self.assertEqual(self.run_query(view=view).counts, {"your_level": 2, "higher": 1, "other": 1})

    def test_an_other_role_at_any_level_is_other(self):
        co = self.company(size=900)
        senior_ish = self.job(co, "Data Engineer II", tier="swe2", ok=0, level_state="reject", role="other")
        self.assertEqual((self.ids(), self.ids(view="higher"), self.ids(view="other")), ([], [], [senior_ish]))

    def test_an_empty_page_still_counts_other(self):
        self.job(self.company())
        page = self.run_query(view="other")
        self.assertEqual((page.rows, page.total, page.counts), ([], 0, {"your_level": 1, "higher": 0, "other": 0}))

    def test_a_cursor_pages_within_its_own_group(self):
        co = self.company()
        sw = [self.job(co, posted=f"2026-11-0{d}") for d in (1, 2, 3)]
        other = [self.job(co, "Data Scientist I", posted=f"2026-11-0{d}", role="other") for d in (1, 2, 3)]
        first = self.run_query(view="other", limit=2)
        rest = self.run_query(view="other", limit=2, after=first.next_after)
        self.assertEqual(sorted(r.posting_id for r in first.rows + rest.rows), sorted(other))
        cross = self.run_query(view="other", limit=50, after=self.run_query(limit=1).next_after)
        self.assertFalse({r.posting_id for r in cross.rows} & set(sw))


class StaffingTests(JobListCase):
    """#257: a known staffing firm is on no tab and in no count; an uncertain one carries a tag."""

    def all_ids(self):
        return {pid for view in ("your_level", "higher", "other") for pid in self.ids(view=view)}

    def zero_counts(self):
        return {"your_level": 0, "higher": 0, "other": 0}

    def test_a_known_staffing_firm_is_on_no_tab(self):
        firm = self.company("Raydar")
        self.job(firm)
        self.job(firm, "Data Scientist I", role="other")
        self.assertEqual((self.all_ids(), self.run_query().counts), (set(), self.zero_counts()))

    def test_a_staffing_word_in_the_name_hides_it(self):
        self.job(self.company("Acme Staffing Group"))
        self.job(self.company("Northwind Recruiting"))
        self.assertEqual(self.all_ids(), set())

    def test_mostly_client_postings_hide_the_company(self):
        firm = self.company("Pinnacle Partners")
        for _ in range(3):
            self.job(firm, for_client=1)
        self.job(firm)
        self.assertEqual(self.all_ids(), set())

    def test_two_client_postings_stay_listed_and_say_so(self):
        co = self.company("Small Agency")
        ids = {self.job(co, for_client=1), self.job(co, for_client=1)}
        rows = {r.posting_id: r for r in self.run_query().rows}
        self.assertEqual(set(rows), ids)
        self.assertTrue(all(r.for_client for r in rows.values()))

    def test_a_bank_saying_our_clients_is_untouched(self):
        bank = self.company("Big Bank")
        pid = self.job(bank)  # for_client 0: "our clients" never counts (rules)
        row, = self.run_query().rows
        self.assertEqual((row.posting_id, row.for_client, row.maybe_staffing), (pid, False, False))

    def test_a_company_merged_into_a_staffing_firm_is_hidden(self):
        firm, alias = self.company("True Talent Solutions Partners"), self.company("TTSP")
        merge(self.conn, alias, firm)
        self.job(alias)
        self.assertEqual(self.all_ids(), set())

    def test_tier_3_is_maybe_staffing_unless_promoted(self):
        tier3, promoted = self.company(), self.company()
        for cid, flag in ((tier3, 0), (promoted, 1)):
            self.conn.execute("INSERT INTO work_tier(company_id, tier, promoted, reason, set_at) "
                              "VALUES (?, 3, ?, 'LCA staffing', '2026-11-01T00:00:00Z')", (cid, flag))
        a, b = self.job(tier3), self.job(promoted)
        rows = {r.posting_id: r.maybe_staffing for r in self.run_query().rows}
        self.assertEqual(rows, {a: True, b: False})

    def test_check_now_does_not_count_a_hidden_firm(self):
        from outreach.crawl import check_all
        firm = posting(self.conn, self.company("Raydar"), seen_gap_s=60, posted_on="2026-11-02")
        decision(self.conn, firm)
        ok = posting(self.conn, self.company("Acme"), seen_gap_s=60, posted_on="2026-11-02")
        decision(self.conn, ok)
        self.assertEqual(check_all._listed_new(self.conn, 0, joblist.listed_params(self.conn)), 1)


class LevelStateTests(JobListCase):
    """Your level reads `level_state` (#154): fit, or uncertain at a small or unknown-size company."""

    def test_fit_is_your_level_at_any_size(self):
        small, big = self.company(size=50), self.company(size=5000)
        a, b = self.job(small, level_state="fit"), self.job(big, level_state="fit")
        self.assertEqual(set(self.ids()), {a, b})
        self.assertEqual(self.ids(view="higher"), [])

    def test_uncertain_is_your_level_at_a_small_or_unknown_size_company_and_says_so(self):
        edge, unknown = self.company(size=300), self.company()
        a = self.job(edge, tier="swe2", ok=0, level_state="uncertain")
        b = self.job(unknown, tier="open", ok=0, level_state="uncertain")
        rows = {r.posting_id: r.level_tag for r in self.run_query().rows}
        self.assertEqual(rows, {a: "level not stated", b: "level not stated"})
        self.assertEqual(self.ids(view="higher"), [])

    def test_uncertain_at_a_big_company_is_higher_and_says_so(self):
        pid = self.job(self.company(size=301), tier="not_stated", ok=0, level_state="uncertain")
        self.assertEqual(self.ids(), [])
        row, = self.run_query(view="higher").rows
        self.assertEqual((row.posting_id, row.level_tag), (pid, "level not stated"))

    def test_reject_is_higher_at_every_size_and_keeps_its_tier_tag(self):
        small, unknown, big = self.company(size=20), self.company(), self.company(size=900)
        ids = [self.job(c, tier="swe2", ok=0, level_state="reject") for c in (small, unknown, big)]
        self.assertEqual(self.ids(), [])
        page = self.run_query(view="higher")
        self.assertEqual({r.posting_id: r.level_tag for r in page.rows}, {i: "SDE 2" for i in ids})
        self.assertEqual(page.counts, {"your_level": 0, "higher": 3, "other": 0})

    def test_a_fit_posting_keeps_its_tier_tag(self):
        co = self.company(size=50)
        tags = {t: self.job(co, tier=t, level_state="fit") for t in ("new_grad", "early_career", "swe1", "swe2")}
        got = {r.posting_id: r.level_tag for r in self.run_query().rows}
        self.assertEqual(got, {tags["new_grad"]: "new grad", tags["early_career"]: "early career",
                               tags["swe1"]: "SDE 1", tags["swe2"]: "SDE 2"})

    def test_a_senior_tier_is_never_listed_whatever_its_state(self):
        co = self.company(size=50)
        for state in ("fit", "uncertain", "reject"):
            self.job(co, tier="senior", ok=0, level_state=state)
        self.assertEqual((self.ids(), self.ids(view="higher")), ([], []))

    def test_a_non_tech_lane_is_never_listed(self):
        co = self.company(size=50)
        self.job(co, lane="off_lane", ok=0, level_state="reject")
        self.assertEqual((self.ids(), self.ids(view="higher")), ([], []))

    def test_state_wins_over_level_ok_and_tier(self):
        co = self.company(size=50)
        rejected = self.job(co, tier="new_grad", ok=1, level_state="reject")
        fit = self.job(co, tier="not_stated", ok=0, level_state="fit")
        self.assertEqual(self.ids(), [fit])
        self.assertEqual(self.ids(view="higher"), [rejected])

    def test_a_row_with_no_state_yet_falls_back_to_the_v1_rule(self):
        # Rows decided before 0007 have a NULL state until the rebuild: fit when level_ok, else uncertain for a tier
        # that names no level.
        small, big = self.company(size=50), self.company(size=5000)
        fit_big = self.job(big)
        unsure_small = self.job(small, tier="not_stated", ok=0)
        unsure_big = self.job(big, tier="swe2", ok=0)
        self.assertEqual(set(self.ids()), {fit_big, unsure_small})
        self.assertEqual(self.ids(view="higher"), [unsure_big])
        self.assertEqual(self.run_query().counts, {"your_level": 2, "higher": 1, "other": 0})

    def test_a_row_with_no_state_follows_the_v1_rule_a_swe2_without_level_ok_is_higher(self):
        # The v1 list put a tier it could name without level_ok in higher at every size: the fallback keeps that.
        small, unknown = self.company(size=50), self.company()
        ids = [self.job(small, tier="swe2", ok=0), self.job(unknown, tier="swe2", ok=0)]
        self.assertEqual(self.ids(), [])
        self.assertEqual(set(self.ids(view="higher")), set(ids))

    def test_a_fit_posting_with_no_tier_is_tagged_entry_level(self):
        co = self.company(size=50)
        a = self.job(co, tier="not_stated", ok=0, level_state="fit")
        b = self.job(co, tier="open", ok=0, level_state="fit")
        self.assertEqual({r.posting_id: r.level_tag for r in self.run_query().rows},
                         {a: "entry level", b: "entry level"})

    def test_counts_and_pages_use_the_state_in_one_pass(self):
        small = self.company(size=20)
        self.job(small, level_state="fit")
        self.job(small, tier="swe2", ok=0, level_state="uncertain")
        self.job(small, tier="swe2", ok=0, level_state="reject")
        page = self.run_query(limit=1)
        self.assertEqual((page.total, page.counts), (2, {"your_level": 2, "higher": 1, "other": 0}))


class DateTests(JobListCase):
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


class ListTimeTests(JobListCase):
    """The list sorts by each row's list time (#193, design 4.4); a row with none is in the Date unknown group."""

    def test_sorts_by_list_at_to_the_minute(self):
        small, big = self.company(size=10), self.company(size=5000)
        gh, ashby = board(self.conn, small, ats="greenhouse"), board(self.conn, big, ats="ashby")
        # 10:38 and 10:42 New York on Nov 3, both posted the same day: the later one heads the list even though the
        # earlier one is at the smaller company.
        at_1038 = self.job(small, posted=None, seen="2026-11-03T15:40:00Z", board_id=gh, seen_gap_s=300,
                           posted_at="2026-11-03T15:38:00Z", list_at="2026-11-03T15:38:00Z")
        at_1042 = self.job(big, posted=None, seen="2026-11-03T15:44:00Z", board_id=ashby, seen_gap_s=300,
                           posted_at="2026-11-03T15:42:00Z", list_at="2026-11-03T15:42:00Z")
        older = self.job(small, posted="2026-11-02")
        self.assertEqual(self.ids(), [at_1042, at_1038, older])

    def test_unknown_group_after_dated_rows(self):
        co = self.company(size=10)
        old_dated = self.job(co, posted="2026-10-10")
        unknown_new = self.job(co, posted=None, seen="2026-11-03T14:00:00Z", list_at=None)
        unknown_old = self.job(co, posted=None, seen="2026-11-02T14:00:00Z", list_at=None)
        dated = self.job(co, posted="2026-11-01")
        self.assertEqual(self.ids(), [dated, old_dated, unknown_new, unknown_old])
        self.assertEqual(self.ids(view="higher"), [])

    def test_unknown_group_shows_in_7_day_window(self):
        co = self.company(size=10)
        months_old = self.job(co, posted=None, seen="2026-08-01T14:00:00Z", list_at=None)
        fresh = self.job(co, posted="2026-11-02")
        for window in (7, 30, 60, 90, 0):
            with self.subTest(window=window):
                self.assertEqual(self.ids(window_days=window), [fresh, months_old])
        self.assertEqual(self.run_query(window_days=7).counts, {"your_level": 2, "higher": 0, "other": 0})

    def test_window_filters_on_list_at(self):
        co = self.company(size=10)
        # Seen yesterday, but the board says it went up 40 days ago: outside 30 days.
        old_post = self.job(co, posted=None, seen="2026-11-02T12:00:00Z", seen_gap_s=600,
                            posted_at="2026-09-24T12:00:00Z", list_at="2026-09-24T12:00:00Z")
        # The 7 day window starts at 00:00 New York on Oct 27 (04:00 UTC, still daylight time then).
        edge_in = self.job(co, posted=None, seen="2026-11-02T12:00:00Z", list_at="2026-10-27T04:00:00Z")
        edge_out = self.job(co, posted=None, seen="2026-11-02T12:00:00Z", list_at="2026-10-27T03:59:00Z")
        self.assertEqual(self.ids(window_days=7), [edge_in])
        self.assertEqual(self.ids(window_days=30), [edge_in, edge_out])
        self.assertEqual(self.ids(window_days=60), [edge_in, edge_out, old_post])

    def test_repost_row_carries_old_first_seen(self):
        co = self.company(size=10)
        gone = posting(self.conn, co, "Software Engineer", first_seen_at="2026-08-01T12:00:00Z",
                       last_seen_at="2026-10-20T12:00:00Z", closed=True)
        again = self.job(co, posted=None, seen="2026-11-03T14:00:00Z", seen_gap_s=1800, repost_of=gone,
                         posted_at="2026-11-03T13:30:00Z", list_at="2026-11-03T13:30:00Z")
        first_read = self.job(co, posted=None, seen="2026-11-02T14:00:00Z")
        rows = {r.posting_id: r for r in self.run_query().rows}
        self.assertEqual(set(rows), {again, first_read})
        self.assertEqual((rows[again].list_at, rows[again].posted_at, rows[again].first_seen_at,
                          rows[again].seen_gap_s, rows[again].first_read, rows[again].reposted_from),
                         ("2026-11-03T13:30:00Z", "2026-11-03T13:30:00Z", "2026-11-03T14:00:00Z", 1800, False,
                          "2026-08-01T12:00:00Z"))
        self.assertEqual((rows[first_read].list_at, rows[first_read].posted_at, rows[first_read].seen_gap_s,
                          rows[first_read].first_read, rows[first_read].reposted_from),
                         ("2026-11-02T14:00:00Z", None, None, True, None))

    def test_cursor_pages_across_unknown_group(self):
        small, big = self.company(size=10), self.company(size=5000)
        for co in (big, small):
            for day in ("2026-11-01", "2026-11-02"):
                self.job(co, posted=day)
            for seen in ("2026-11-02T12:00:00Z", "2026-11-03T12:00:00Z"):
                self.job(co, posted=None, seen=seen, list_at=None)
        full = self.ids(limit=100)
        self.assertEqual(len(full), 8)
        unknown = {r[0] for r in self.conn.execute("SELECT posting_id FROM posting WHERE list_at IS NULL")}
        self.assertEqual(set(full[4:]), unknown)  # every dated row first
        for limit in (1, 2, 3, 5):
            seen, after = [], None
            while True:
                page = self.run_query(after=after, limit=limit)
                seen += [r.posting_id for r in page.rows]
                if page.next_after is None:
                    break
                after = page.next_after
            self.assertEqual(seen, full, msg=f"limit {limit}")


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

    def mark(self, company, at, kind="company_reached"):
        append_event(self.conn, entity="company", entity_id=company, company_id=company, kind=kind,
                     actor="devanshu", at=at, detail={"company_id": company})

    def test_reached_mark_sets_contacted_on(self):
        marked = self.company("Marked")
        self.job(marked)
        self.mark(marked, "2026-10-28T15:00:00Z")
        self.assertEqual(self.run_query().rows[0].contacted_on, "2026-10-28")

    def test_the_newer_of_a_mark_and_a_touch_is_contacted_on(self):
        both, later = self.company("Both"), self.company("Later")
        self.job(both, posted="2026-11-02")
        self.job(later, posted="2026-11-01")
        seed_outbound(self.conn, seed_person(self.conn, both, "Ada"), both, "linkedin_request", date(2026, 10, 25))
        self.mark(both, "2026-10-20T15:00:00Z")  # an older mark never hides a newer touch
        seed_outbound(self.conn, seed_person(self.conn, later, "Bo"), later, "linkedin_request", date(2026, 10, 20))
        self.mark(later, "2026-10-30T15:00:00Z")
        self.assertEqual([r.contacted_on for r in self.run_query().rows], ["2026-10-25", "2026-10-30"])

    def test_not_contacted_filter_hides_marked(self):
        marked, cleared, fresh = self.company("Marked"), self.company("Cleared"), self.company("Fresh")
        a, b, c = self.job(marked), self.job(cleared), self.job(fresh)
        self.mark(marked, "2026-10-28T15:00:00Z")
        self.mark(cleared, "2026-10-28T15:00:00Z")
        self.mark(cleared, "2026-10-28T15:01:00Z", kind="company_reached_cleared")
        self.assertEqual(sorted(self.ids()), sorted([a, b, c]))
        self.assertEqual(sorted(self.ids(not_contacted=True)), sorted([b, c]))
        self.assertIsNone(next(r for r in self.run_query().rows if r.posting_id == b).contacted_on)

    def emailed(self, company, name, sent_on, *, followup="planned"):
        """A first email sent on `sent_on` (the sender's outreach_touch with it) and a follow-up in `followup`."""
        person = seed_person(self.conn, company, name, email=f"{name.lower()}@example.com")
        touch = seed_email_touch(self.conn, person, sent_on, company_id=company, outreach=True)
        seed_message(self.conn, touch, 0, "sent", sent_on=sent_on)
        if followup is not None:
            seed_message(self.conn, touch, 1, followup)
        return person

    def test_a_sent_email_with_a_follow_up_queued_is_reached(self):
        # Final review, critical 1: the sender writes the planned follow-ups at the first send, so a sent email is
        # reached for its whole follow-up cycle (#9 first to see, design 4.5).
        mailed, fresh = self.company("Mailed"), self.company("Fresh")
        a, b = self.job(mailed), self.job(fresh)
        self.emailed(mailed, "Ada", date(2026, 10, 28))
        self.assertEqual(next(r for r in self.run_query().rows if r.posting_id == a).contacted_on, "2026-10-28")
        self.assertEqual(self.ids(not_contacted=True), [b])

    def test_a_first_email_still_in_the_outbox_is_not_reached(self):
        waiting = self.company("Waiting")
        a = self.job(waiting)
        person = seed_person(self.conn, waiting, "Bo", email="bo@example.com")
        seed_message(self.conn, seed_email_touch(self.conn, person, None), 0, "approved")
        self.assertIsNone(self.run_query().rows[0].contacted_on)
        self.assertEqual(self.ids(not_contacted=True), [a])

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
        pid = posting(self.conn, co, "Backend Engineer", location="Austin, TX", posted_on="2026-11-01",
                      first_seen_at="2026-11-01T12:00:00Z", last_seen_at="2026-11-02T12:00:00Z")
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


class BigGuessTests(JobListCase):
    """A company with no size on file is big when it has more than 50 open US postings or a Workday or Oracle board."""

    def us_postings(self, co, n, *, us="yes"):
        """`n` open postings that are not listed (lane not_engineering) but count as read-US postings."""
        for _ in range(n):
            self.job(co, lane="not_engineering", us=us)

    def test_unknown_size_with_many_us_postings_is_big(self):
        co = self.company()
        self.us_postings(co, 50)
        pid = self.job(co, tier="not_stated", ok=0, level_state="uncertain")  # the 51st open US posting
        self.assertEqual(self.ids(), [])
        row, = self.run_query(view="higher").rows
        self.assertEqual((row.posting_id, row.size, row.big), (pid, None, True))

    def test_fifty_us_postings_is_not_yet_big(self):
        co = self.company()
        self.us_postings(co, 49)
        pid = self.job(co, tier="not_stated", ok=0, level_state="uncertain")  # the 50th
        row, = self.run_query().rows
        self.assertEqual((row.posting_id, row.size, row.big), (pid, None, False))

    def test_only_open_postings_read_as_us_count(self):
        co = self.company()
        self.us_postings(co, 30)
        self.us_postings(co, 30, us="unknown")
        for _ in range(30):
            gone = posting(self.conn, co, posted_on="2026-11-02", closed=True)
            decision(self.conn, gone, lane="not_engineering")
        pid = self.job(co, tier="not_stated", ok=0, level_state="uncertain")
        self.assertEqual(self.ids(), [pid])

    def test_a_known_size_is_never_guessed(self):
        co = self.company(size=40)
        self.us_postings(co, 60)
        board(self.conn, co, ats="workday")
        pid = self.job(co, tier="not_stated", ok=0, level_state="uncertain")
        row, = self.run_query().rows
        self.assertEqual((row.posting_id, row.size, row.big), (pid, 40, False))

    def test_unknown_size_with_workday_board_is_big(self):
        for ats in ("workday", "oracle_orc"):
            co = self.company()
            board(self.conn, co, ats=ats)
            pid = self.job(co, tier="not_stated", ok=0, level_state="uncertain")
            row, = [r for r in self.run_query(view="higher").rows if r.posting_id == pid]
            self.assertEqual((row.size, row.big), (None, True), msg=ats)
            self.assertNotIn(pid, self.ids())

    def test_an_inactive_enterprise_board_does_not_make_it_big(self):
        co = self.company()
        board(self.conn, co, ats="workday", active=0)
        pid = self.job(co, tier="not_stated", ok=0, level_state="uncertain")
        self.assertEqual(self.ids(), [pid])

    def test_unknown_size_small_board_stays_your_level(self):
        co = self.company()
        board(self.conn, co, ats="greenhouse")
        board(self.conn, co, ats="lever")
        pid = self.job(co, tier="not_stated", ok=0, level_state="uncertain")
        row, = self.run_query().rows
        self.assertEqual((row.posting_id, row.size, row.big), (pid, None, False))

    def test_fit_at_big_company_needs_us_yes(self):
        known, guessed = self.company(size=5000), self.company()
        board(self.conn, guessed, ats="workday")
        yes = [self.job(c, level_state="fit", us="yes") for c in (known, guessed)]
        unclear = [self.job(c, level_state="fit", us="unknown") for c in (known, guessed)]
        self.assertEqual(set(self.ids()), set(yes))
        self.assertEqual(set(self.ids(view="higher")), set(unclear))
        self.assertEqual(self.run_query().counts, {"your_level": 2, "higher": 2, "other": 0})

    def test_unknown_us_at_small_company_stays(self):
        small, unknown = self.company(size=50), self.company()
        a = self.job(small, level_state="fit", us="unknown")
        b = self.job(unknown, level_state="fit", us="unknown")
        c = self.job(unknown, tier="not_stated", ok=0, level_state="uncertain", us="unknown")
        self.assertEqual(set(self.ids()), {a, b, c})
        self.assertEqual(self.ids(view="higher"), [])

    def test_an_uncertain_posting_at_a_known_big_company_is_higher_even_with_us_yes(self):
        pid = self.job(self.company(size=900), tier="not_stated", ok=0, level_state="uncertain", us="yes")
        self.assertEqual((self.ids(), self.ids(view="higher")), ([], [pid]))

    def test_the_count_and_the_boards_follow_a_merge(self):
        survivor, dupe, other = self.company("Survivor"), self.company("Survivor Inc"), self.company("Other")
        self.us_postings(survivor, 30)
        self.us_postings(dupe, 25)  # 55 over the pair, neither over 50 alone
        merge(self.conn, dupe, survivor)
        pid = self.job(survivor, tier="not_stated", ok=0, level_state="uncertain")
        self.us_postings(other, 25)
        other_pid = self.job(other, tier="not_stated", ok=0, level_state="uncertain")
        self.assertEqual(self.ids(), [other_pid])
        self.assertEqual(self.ids(view="higher"), [pid])

        into, away = self.company("Into"), self.company("Away")
        board(self.conn, away, ats="workday")
        merge(self.conn, away, into)
        via_board = self.job(into, tier="not_stated", ok=0, level_state="uncertain")
        self.assertIn(via_board, self.ids(view="higher"))

    def test_size_filter_uses_big_guess(self):
        small, known_big, unknown, guessed = self.company(size=50), self.company(size=900), self.company(), self.company()
        board(self.conn, guessed, ats="workday")
        a, b, c, d = (self.job(co) for co in (small, known_big, unknown, guessed))
        self.assertEqual(self.ids(size="small"), [a])
        self.assertEqual(sorted(self.ids(size="big")), sorted([b, d]))
        self.assertEqual(self.ids(size="unknown"), [c])
        self.assertEqual(self.run_query(size="big").counts, {"your_level": 2, "higher": 0, "other": 0})

    def test_order_puts_guessed_big_before_known_big(self):
        small, unknown, guessed, known_big = (self.company(size=s) for s in (10, None, None, 5000))
        board(self.conn, guessed, ats="oracle_orc")
        on_known, on_guessed, on_unknown, on_small = (self.job(c) for c in (known_big, guessed, unknown, small))
        page = self.run_query(limit=2)
        self.assertEqual([r.posting_id for r in page.rows], [on_small, on_unknown])
        rest = self.run_query(limit=2, after=page.next_after)
        self.assertEqual([r.posting_id for r in rest.rows], [on_guessed, on_known])
        self.assertIsNone(rest.next_after)


class QueryPlanTests(JobListCase):
    def test_query_plan_starts_from_open_postings(self):
        co = self.company(size=20)
        for _ in range(30):
            self.job(co)
        plans = joblist.explain(self.conn, NOW, JobFilters(), None, 50)
        text = "\n".join(plans)
        # The postings and decisions are read through an index or primary key, never a table scan; the only scans are
        # of the small JSON lists the query is given and of its own materialized sets (cand, co, usn, ent).
        for line in plans:
            if line.startswith("SCAN"):
                self.assertRegex(line, r"SCAN ((json_each|cn|x|c|cand|co|co0|co1|rows|canon|ent|usn)\b|\(subquery-\d+\))", msg=text)
        self.assertRegex(text, r"SEARCH (d USING INDEX decision_version|p USING INDEX posting_)")
        self.assertIn("SEARCH p USING INTEGER PRIMARY KEY", text)
        self.assertNotIn("current_decision", text)


class ScaleTests(JobListCase):
    def test_thousands_of_postings_in_one_query(self):
        companies = [self.company(size=s) for s in (10, 80, 250, 900, None)]
        for i in range(3000):
            tier, ok, state = (("new_grad", 1, "fit"), ("swe2", 0, "reject"), ("not_stated", 0, "uncertain"))[i % 3]
            self.job(companies[i % 5], posted=f"2026-10-{1 + i % 28:02d}", tier=tier, ok=ok, level_state=state)
        page = self.run_query(window_days=0)
        self.assertEqual(len(page.rows), 50)
        # fit rows are all his level (1000); uncertain counts at the three small companies (600 of 1000); the
        # unknown-size one has 600 open US postings, so it is a guessed big company and so, with the 900-person one,
        # its uncertain rows (400) are higher with the rejected SDE 2 (1000).
        self.assertEqual(page.counts, {"your_level": 1600, "higher": 1400, "other": 0})
        self.assertEqual(page.total, 1600)


if __name__ == "__main__":
    unittest.main()


class HourWindowAndLevelTests(JobListCase):
    """#213: "posted in the last hour, 6 hours, 24 hours" windows and level chips (multi-select)."""

    def test_hour_windows_read_the_list_time_and_leave_out_date_unknown_rows(self):
        co = self.company(size=40)
        # NOW is 15:00 UTC on 2026-11-03.
        half_hour = self.job(co, "A", posted="2026-11-03", seen="2026-11-03T14:30:00Z", list_at="2026-11-03T14:30:00Z")
        five_hours = self.job(co, "B", posted="2026-11-03", seen="2026-11-03T10:00:00Z", list_at="2026-11-03T10:00:00Z")
        a_day = self.job(co, "C", posted="2026-11-02", seen="2026-11-02T16:00:00Z", list_at="2026-11-02T16:00:00Z")
        old = self.job(co, "D", posted="2026-11-01", seen="2026-11-01T12:00:00Z", list_at="2026-11-01T12:00:00Z")
        unknown = self.job(co, "E", posted=None, list_at=None)
        self.assertEqual(self.ids(window_days=0, window_hours=1), [half_hour])
        self.assertEqual(self.ids(window_days=0, window_hours=6), [half_hour, five_hours])
        self.assertEqual(self.ids(window_days=0, window_hours=24), [half_hour, five_hours, a_day])
        self.assertEqual(self.ids(window_days=7), [half_hour, five_hours, a_day, old, unknown])

    def test_the_hour_edge_is_inclusive(self):
        co = self.company(size=40)
        edge = self.job(co, posted="2026-11-03", seen="2026-11-03T14:00:00Z", list_at="2026-11-03T14:00:00Z")
        self.assertEqual(self.ids(window_days=0, window_hours=1), [edge])

    def test_a_bad_hour_window_is_refused(self):
        with self.assertRaises(ValueError):
            self.ids(window_days=0, window_hours=2)
        with self.assertRaises(ValueError):
            self.ids(window_days=7, window_hours=1)  # one window at a time

    def levels_fixture(self):
        co = self.company(size=40)
        return {
            "new_grad": self.job(co, "NG", tier="new_grad"),
            "early_career": self.job(co, "EC", tier="early_career"),
            "swe1": self.job(co, "S1", tier="swe1"),
            "swe2": self.job(co, "S2", tier="swe2", ok=1, level_state="fit"),
            "entry": self.job(co, "EN", tier="not_stated", ok=1, level_state="fit"),
            "not_stated": self.job(co, "NS", tier="not_stated", ok=0, level_state="uncertain"),
        }

    def test_each_level_chip_keeps_its_rows(self):
        rows = self.levels_fixture()
        for key, pid in rows.items():
            self.assertEqual(self.ids(levels=frozenset({key})), [pid], key)

    def test_chips_combine_and_the_counts_follow(self):
        rows = self.levels_fixture()
        page = self.run_query(levels=frozenset({"swe1", "swe2"}))
        self.assertEqual(sorted(r.posting_id for r in page.rows), sorted([rows["swe1"], rows["swe2"]]))
        self.assertEqual(page.counts, {"your_level": 2, "higher": 0, "other": 0})
        self.assertEqual(self.run_query().counts["your_level"], 6)

    def test_an_uncertain_row_counts_as_level_not_stated_whatever_its_tier(self):
        co = self.company(size=40)
        pid = self.job(co, tier="swe2", ok=0, level_state="uncertain")
        self.assertEqual(self.ids(levels=frozenset({"not_stated"})), [pid])
        self.assertEqual(self.ids(levels=frozenset({"swe2"})), [])

    def test_the_higher_tab_filters_by_level_too(self):
        co = self.company(size=40)
        sde2 = self.job(co, "Higher S2", tier="swe2", ok=0, level_state="reject")
        plain = self.job(co, "Higher NS", tier="not_stated", ok=0, level_state="reject")
        self.assertEqual(self.ids(view="higher", levels=frozenset({"swe2"})), [sde2])
        self.assertEqual(self.ids(view="higher", levels=frozenset({"not_stated"})), [plain])

    def test_an_unknown_level_is_refused(self):
        with self.assertRaises(ValueError):
            self.ids(levels=frozenset({"senior"}))
