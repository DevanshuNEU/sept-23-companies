"""Top picks: the fresh lane, the visa gate, Not now and applied (the owner's spec, 2026-10-26). Pure parts, no store."""
import dataclasses
import itertools
import unittest
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from outreach.plan.config import default_plan_config
from outreach.plan.contact import ContactState
from outreach.plan.fresh import (Opening, age_words, early_career_fit, fresh_tag, hours_old, is_fresh, level_words,
                                 opened, plan_moment, visa_clear)
from outreach.plan.select import (Caps, Prospect, bench, choose_rows, eligibility, fresh_pick, held_back)

CFG = default_plan_config()
TZ = "America/New_York"
NOW = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)  # 10:00 in New York, a Tuesday
TODAY = date(2026, 11, 10)
_ids = itertools.count(1)


def opening(company: int, *, hours=6.0, tier="new_grad", years=None, stance="silent", lane="level_role",
            posted_on=None, seen_hours=None) -> Opening:
    seen = NOW - timedelta(hours=seen_hours if seen_hours is not None else hours)
    at = NOW - timedelta(hours=hours)
    return Opening(posting_id=next(_ids) + 5000, company_id=company, title="Software Engineer, New Grad",
                   url="https://jobs.example.com/1", location="New York", lane=lane, level_tier=tier,
                   years_asked=years, stance=stance, posted_on=posted_on, first_seen_at=seen,
                   first_seen_on=seen.astimezone(ZoneInfo(TZ)).date(),
                   opened_at=at, timed=posted_on is None)


def prospect(company: int, *, score=50.0, openings=(), everify=None, sponsorship=None, status="free",
             passed_days=None, passed_at=None, applied_on=None, lanes=("level_role",)) -> Prospect:
    state = (ContactState(company, "free", 0, 0, None, None, "No outreach yet", 0, 4, None) if status == "free"
             else ContactState(company, status, 2, 0, 7, TODAY - timedelta(days=3), "Request sent 3 days ago", 2, 2,
                               TODAY + timedelta(days=11)))
    if passed_days is not None and passed_at is None:
        passed_at = NOW - timedelta(days=passed_days)
    return Prospect(company_id=company, name=f"Company {company}", score=score, position=company, lanes=tuple(lanes),
                    contact=state, candidates=(), small=False, carried=None, openings=tuple(openings),
                    everify=everify, company_sponsorship=sponsorship, passed_at=passed_at, passed_days=passed_days,
                    applied_on=applied_on)


def ids(rows):
    return [r.company_id for r in rows]


class FreshWindowTest(unittest.TestCase):
    def test_71_hours_is_fresh_and_73_is_not(self):
        self.assertTrue(is_fresh(NOW - timedelta(hours=71), NOW, CFG))
        self.assertTrue(is_fresh(NOW - timedelta(hours=72), NOW, CFG))
        self.assertFalse(is_fresh(NOW - timedelta(hours=73), NOW, CFG))
        self.assertFalse(is_fresh(NOW - timedelta(hours=72, seconds=1), NOW, CFG))

    def test_a_sighting_ahead_of_now_is_zero_hours_old(self):
        self.assertEqual(hours_old(NOW + timedelta(minutes=5), NOW), 0.0)
        self.assertTrue(is_fresh(NOW + timedelta(minutes=5), NOW, CFG))

    def test_opened_is_the_sighting_unless_it_was_posted_on_an_earlier_day(self):
        seen = datetime(2026, 11, 10, 14, 0, tzinfo=timezone.utc)  # 09:00 New York
        self.assertEqual(opened(None, seen, TZ), (seen, True))
        self.assertEqual(opened(date(2026, 11, 10), seen, TZ), (seen, True))
        self.assertEqual(opened(date(2026, 11, 12), seen, TZ), (seen, True))
        # Posted two days before the crawler saw it: it opened at the start of that New York day.
        self.assertEqual(opened(date(2026, 11, 8), seen, TZ),
                         (datetime(2026, 11, 8, 5, 0, tzinfo=timezone.utc), False))

    def test_an_old_posting_found_today_is_not_fresh(self):
        seen = NOW - timedelta(hours=1)
        at, _ = opened(date(2026, 10, 1), seen, TZ)
        self.assertFalse(is_fresh(at, NOW, CFG))

    def test_plan_moment(self):
        self.assertEqual(plan_moment(TODAY, TZ, NOW), NOW)
        self.assertEqual(plan_moment(date(2026, 11, 11), TZ, NOW), datetime(2026, 11, 11, 13, 0, tzinfo=timezone.utc))


class AgeWordsTest(unittest.TestCase):
    def test_hours_come_from_the_first_sighting(self):
        self.assertEqual(age_words(opening(1, hours=6), NOW, TZ), "first seen 6 hours ago")
        self.assertEqual(age_words(opening(1, hours=1.5), NOW, TZ), "first seen 1 hour ago")
        self.assertEqual(age_words(opening(1, hours=0.2), NOW, TZ), "first seen under an hour ago")
        self.assertEqual(age_words(opening(1, hours=30), NOW, TZ), "first seen yesterday")
        self.assertEqual(age_words(opening(1, hours=60), NOW, TZ), "first seen 3 days ago")
        self.assertEqual(fresh_tag(opening(1, hours=6), NOW, TZ), "New opening, 6 hours ago")

    def test_a_posted_date_is_said_in_days(self):
        o = opening(1, hours=34, posted_on=date(2026, 11, 9), seen_hours=2)
        self.assertEqual(age_words(o, NOW, TZ), "posted yesterday")
        self.assertEqual(fresh_tag(o, NOW, TZ), "New opening, yesterday")
        self.assertEqual(age_words(dataclasses.replace(o, posted_on=date(2026, 11, 7)), NOW, TZ), "posted 3 days ago")
        self.assertEqual(age_words(dataclasses.replace(o, posted_on=TODAY), NOW, TZ), "posted today")


class LevelTest(unittest.TestCase):
    def test_early_career_fit_for_each_tier(self):
        for tier, years, fit in (("new_grad", None, True), ("early_career", None, True), ("swe1", None, True),
                                 ("swe1", 5, True), ("open", 0, True), ("open", 2, True), ("open", 3, False),
                                 ("open", None, False), ("swe2", None, False), ("swe2", 1, True),
                                 ("senior", None, False), ("not_stated", None, False), (None, None, False)):
            with self.subTest(tier=tier, years=years):
                self.assertIs(early_career_fit(tier, years), fit)

    def test_an_intern_opening_is_not_an_early_career_fit(self):
        self.assertFalse(opening(1, tier="new_grad", lane="intern_junior").early_fit)
        self.assertTrue(opening(1, tier="new_grad").early_fit)

    def test_level_words(self):
        for tier, years, words in (("new_grad", None, "New grad role"), ("early_career", None, "Early career role"),
                                   ("swe1", None, "SWE I role"), ("swe2", None, "SWE II role"),
                                   ("senior", None, "Senior role"), ("open", 2, "Open level, asks 2 years"),
                                   ("open", 1, "Open level, asks 1 year"), ("open", None, "Open level role"),
                                   ("not_stated", None, None)):
            with self.subTest(tier=tier):
                self.assertEqual(level_words(tier, years), words)
        self.assertEqual(level_words("new_grad", None, intern=True), "Intern role")


class VisaGateTest(unittest.TestCase):
    def test_the_gate(self):
        for stance, everify, fact, ok in (
                ("silent", None, None, True),  # no E-Verify fact and a silent posting: clears (the spec's or)
                ("silent", "not_enrolled", None, True),
                ("yes", None, None, True), ("opt_only", "not_enrolled", None, True),
                ("unclear", None, None, False), ("unclear", "not_enrolled", None, False),
                ("unclear", "enrolled", None, True),
                ("no", "enrolled", None, False),
                ("silent", "enrolled", "no", False), ("yes", "enrolled", "no", False),
                ("silent", None, "yes", True)):
            with self.subTest(stance=stance, everify=everify, fact=fact):
                self.assertIs(visa_clear(stance, everify, fact), ok)


class FreshLaneTest(unittest.TestCase):
    def test_fresh_companies_come_first_newest_first_then_the_ranking(self):
        ps = [prospect(c, score=100 - c) for c in range(1, 11)]
        ps[7] = prospect(8, score=92, openings=[opening(8, hours=30)])
        ps[8] = prospect(9, score=91, openings=[opening(9, hours=5)])
        rows = choose_rows(ps, Caps(8, 0, 7, 1), CFG)
        self.assertEqual(ids(rows), [9, 8, 1, 2, 3, 4, 5, 6])
        self.assertEqual([r.opening is not None for r in rows], [True, True] + [False] * 6)
        self.assertEqual(rows[0].opening.posting_id, ps[8].openings[0].posting_id)
        self.assertEqual(rows[0].list_group, "level_role")

    def test_at_most_fresh_slots_then_the_rest_by_rank(self):
        ps = [prospect(c, score=100 - c, openings=[opening(c, hours=c)]) for c in range(1, 6)]
        ps += [prospect(c, score=100 - c) for c in range(6, 12)]
        rows = choose_rows(ps, Caps(8, 0, 7, 1), CFG)
        self.assertEqual(ids(rows)[:3], [1, 2, 3])
        self.assertEqual(sum(r.opening is not None for r in rows), 3)
        self.assertEqual(ids(rows)[3:], [4, 5, 6, 7, 8])

    def test_fewer_fresh_than_slots_and_none_at_all(self):
        ps = [prospect(c, score=100 - c) for c in range(1, 11)]
        rows = choose_rows(ps, Caps(8, 0, 7, 1), CFG)
        self.assertEqual(ids(rows), list(range(1, 9)))
        self.assertTrue(all(r.opening is None for r in rows))
        ps[9] = prospect(10, score=1, openings=[opening(10, hours=50)])
        rows = choose_rows(ps, Caps(8, 0, 7, 1), CFG)
        self.assertEqual(ids(rows), [10, 1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(sum(r.opening is not None for r in rows), 1)

    def test_fresh_slots_zero_turns_the_lane_off(self):
        cfg = dataclasses.replace(CFG, queue={**CFG.queue, "fresh_slots": 0})
        ps = [prospect(c, score=100 - c) for c in range(1, 10)] + [prospect(10, score=1, openings=[opening(10)])]
        self.assertNotIn(10, ids(choose_rows(ps, Caps(8, 0, 7, 1), cfg)))

    def test_the_visa_gate_keeps_an_opening_out_of_the_lane_not_off_the_list(self):
        unclear = prospect(2, score=10, openings=[opening(2, stance="unclear")])
        silent = prospect(3, score=5, openings=[opening(3, stance="silent")])
        self.assertIsNone(fresh_pick(unclear, CFG))
        self.assertIsNotNone(fresh_pick(dataclasses.replace(unclear, everify="enrolled"), CFG))
        self.assertIsNotNone(fresh_pick(silent, CFG))  # no E-Verify fact and silent sponsorship
        self.assertIsNone(fresh_pick(dataclasses.replace(silent, company_sponsorship="no"), CFG))
        rows = choose_rows([prospect(1, score=90), unclear, silent], Caps(8, 0, 7, 1), CFG)
        self.assertEqual(ids(rows), [3, 1, 2])  # the unclear one is still picked, by rank, not as fresh
        self.assertIsNone(rows[2].opening)

    def test_only_early_career_fits_take_the_lane(self):
        for tier, years, fresh in (("new_grad", None, True), ("early_career", None, True), ("swe1", None, True),
                                   ("open", 2, True), ("open", None, False), ("swe2", None, False),
                                   ("senior", None, False)):
            with self.subTest(tier=tier, years=years):
                p = prospect(5, score=1, openings=[opening(5, tier=tier, years=years)])
                rows = choose_rows([prospect(1, score=90), p], Caps(8, 0, 7, 1), CFG)
                self.assertEqual(ids(rows)[0] == 5, fresh)
                self.assertEqual(rows[ids(rows).index(5)].opening is not None, fresh)

    def test_a_company_in_flight_is_not_fresh(self):
        p = prospect(5, openings=[opening(5)], status="waiting")
        self.assertEqual(eligibility(p, CFG), (False, "waiting"))
        self.assertNotIn(5, ids(choose_rows([p, prospect(1)], Caps(8, 0, 7, 1), CFG)))


class QualityFloorTest(unittest.TestCase):
    def test_rank_300_takes_the_lane_and_301_does_not(self):
        at300 = dataclasses.replace(prospect(5, score=13, openings=[opening(5)]), position=300)
        at301 = dataclasses.replace(at300, position=301)
        self.assertIsNotNone(fresh_pick(at300, CFG))
        self.assertIsNone(fresh_pick(at301, CFG))
        strong = [prospect(c, score=160 - c) for c in range(101, 109)]
        self.assertEqual(ids(choose_rows([*strong, at300], Caps(8, 0, 7, 1), CFG))[0], 5)
        rows = choose_rows([*strong, at301], Caps(8, 0, 7, 1), CFG)
        self.assertNotIn(5, ids(rows))  # a 13 point company below the floor never pushes out a 60 point one
        self.assertTrue(all(r.opening is None for r in rows))

    def test_below_the_floor_the_bench_does_not_flag_it_new(self):
        low = dataclasses.replace(prospect(5, score=13, openings=[opening(5)]), position=301)
        b = bench([prospect(1, score=60), low], taken=set(), cfg=CFG)
        self.assertEqual([(x.prospect.company_id, x.opening) for x in b], [(1, None), (5, None)])

    def test_below_the_floor_a_new_opening_does_not_end_the_cooldown(self):
        p = dataclasses.replace(prospect(5, passed_days=5, openings=[opening(5)]), position=301)
        self.assertEqual(eligibility(p, CFG), (False, "passed"))


class NotNowTest(unittest.TestCase):
    def test_passed_13_days_ago_is_held_and_15_is_back(self):
        p13 = prospect(5, passed_days=13)
        p14 = prospect(5, passed_days=14)
        p15 = prospect(5, passed_days=15)
        self.assertEqual(eligibility(p13, CFG), (False, "passed"))
        self.assertEqual(held_back(p13, CFG), "passed")
        self.assertEqual(eligibility(p14, CFG), (True, "free"))
        self.assertEqual(eligibility(p15, CFG), (True, "free"))
        self.assertNotIn(5, ids(choose_rows([p13, prospect(1)], Caps(8, 0, 7, 1), CFG)))
        self.assertIn(5, ids(choose_rows([p15, prospect(1)], Caps(8, 0, 7, 1), CFG)))

    def test_a_new_opening_after_the_pass_brings_it_back_as_fresh(self):
        passed = NOW - timedelta(days=5)
        new = opening(5, hours=6)  # first seen 6 hours ago, five days after the pass
        p = prospect(5, passed_days=5, passed_at=passed, openings=[new])
        self.assertTrue(eligibility(p, CFG)[0])
        self.assertEqual(fresh_pick(p, CFG), new)
        rows = choose_rows([prospect(1, score=90), p], Caps(8, 0, 7, 1), CFG)
        self.assertEqual(ids(rows), [5, 1])
        self.assertEqual(rows[0].opening, new)

    def test_only_openings_from_before_the_pass_keep_it_cooling(self):
        passed = NOW - timedelta(hours=10)
        old = opening(5, hours=30)  # first seen before the pass
        p = prospect(5, passed_days=0, passed_at=passed, openings=[old])
        self.assertEqual(eligibility(p, CFG), (False, "passed"))
        self.assertIsNone(fresh_pick(p, CFG))
        p = prospect(5, passed_days=5, passed_at=NOW - timedelta(days=5))  # no opening at all
        self.assertEqual(eligibility(p, CFG), (False, "passed"))

    def test_a_new_swe2_opening_does_not_bring_it_back(self):
        p = prospect(5, passed_days=5, openings=[opening(5, tier="swe2")])
        self.assertEqual(eligibility(p, CFG), (False, "passed"))

    def test_a_new_opening_that_fails_the_gate_does_not_bring_it_back(self):
        p = prospect(5, passed_days=5, openings=[opening(5, stance="unclear")])
        self.assertEqual(eligibility(p, CFG), (False, "passed"))


class AppliedTest(unittest.TestCase):
    def test_a_company_he_applied_to_is_held_back(self):
        p = prospect(5, applied_on=date(2026, 10, 1))
        self.assertEqual(eligibility(p, CFG), (False, "applied"))
        self.assertNotIn(5, ids(choose_rows([p, prospect(1)], Caps(8, 0, 7, 1), CFG)))

    def test_a_new_role_after_the_application_brings_it_back_as_fresh(self):
        new = opening(5, hours=6)
        p = prospect(5, applied_on=date(2026, 10, 1), openings=[new])
        self.assertEqual(eligibility(p, CFG), (True, "free"))
        self.assertEqual(ids(choose_rows([prospect(1, score=90), p], Caps(8, 0, 7, 1), CFG))[0], 5)

    def test_an_opening_first_seen_on_the_application_day_is_not_new(self):
        new = opening(5, hours=6)
        p = prospect(5, applied_on=new.first_seen_on, openings=[new])
        self.assertEqual(eligibility(p, CFG), (False, "applied"))

    def test_applied_and_passed_needs_an_opening_after_both(self):
        new = opening(5, hours=6)
        p = prospect(5, applied_on=date(2026, 10, 1), passed_days=0, passed_at=NOW - timedelta(hours=2),
                     openings=[new])
        self.assertEqual(eligibility(p, CFG), (False, "passed"))
        p = dataclasses.replace(p, passed_at=NOW - timedelta(hours=8))
        self.assertTrue(eligibility(p, CFG)[0])


class BenchTest(unittest.TestCase):
    def test_fresh_fits_first_then_fresh_others_then_rank(self):
        ps = [prospect(c, score=100 - c) for c in range(1, 8)]
        ps.append(prospect(20, score=10, openings=[opening(20, tier="swe2", hours=2)]))
        ps.append(prospect(21, score=5, openings=[opening(21, tier="swe1", hours=40)]))
        ps.append(prospect(22, score=4, openings=[opening(22, tier="new_grad", hours=3)]))
        ps.append(prospect(23, score=99, passed_days=2))  # cooling: not on the bench
        ps.append(prospect(24, score=98, status="waiting"))  # in flight: not on the bench
        picks = bench(ps, taken={1, 2}, cfg=CFG)
        self.assertEqual([b.prospect.company_id for b in picks], [22, 21, 20, 3, 4, 5, 6, 7])
        self.assertEqual([b.early_fit for b in picks[:4]], [True, True, False, False])
        self.assertIsNotNone(picks[2].opening)
        self.assertIsNone(picks[3].opening)

    def test_bench_size(self):
        ps = [prospect(c, score=100 - c) for c in range(1, 40)]
        self.assertEqual(len(bench(ps, taken=set(), cfg=CFG)), 12)
        self.assertEqual(len(bench(ps, taken=set(), cfg=CFG, n=3)), 3)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------------------------- the reads

from outreach.plan.fresh import PASSED, load_applied, load_openings, load_passes  # noqa: E402
from outreach.plan.outbound import load_outbound  # noqa: E402
from outreach.plan.prospects import load_prospects  # noqa: E402
from outreach.ranking.common import canonical_ids  # noqa: E402
from outreach.ranking.scores import ranked_list  # noqa: E402
from outreach.store import append_event  # noqa: E402
from outreach.store.timefmt import ISO_FMT  # noqa: E402
from tests.plan_seed import seed_scored_company  # noqa: E402
from tests.ranking_store import board, decision, no_go, posting  # noqa: E402
from tests.storetest import fresh_store  # noqa: E402


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(ISO_FMT)


def _opening(conn, company_id: int, *, hours: float, **fields) -> int:
    pid = posting(conn, company_id, first_seen_at=_ts(NOW - timedelta(hours=hours)),
                  last_seen_at=_ts(NOW), posted_on=fields.pop("posted_on", None))
    decision(conn, pid, **fields)
    return pid


class LoadOpeningsTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.c = seed_scored_company(self.conn, "Tessera Labs", 60, ["level_role"], with_posting=False)

    def openings(self):
        return load_openings(self.conn, canonical_ids(self.conn), NOW, CFG).get(self.c, ())

    def test_71_hours_is_read_and_73_is_not(self):
        p71 = _opening(self.conn, self.c, hours=71)
        _opening(self.conn, self.c, hours=73)
        self.assertEqual([o.posting_id for o in self.openings()], [p71])

    def test_only_visible_postings_at_his_level_on_a_trusted_board(self):
        good = _opening(self.conn, self.c, hours=5)
        _opening(self.conn, self.c, hours=4, level_tier="senior")  # not at his level: any_us_role at best
        _opening(self.conn, self.c, hours=3, us="no")  # outside the US: not visible
        _opening(self.conn, self.c, hours=2, lane="not_engineering")
        shaky = board(self.conn, self.c, active=0)
        pid = posting(self.conn, self.c, board_id=shaky, first_seen_at=_ts(NOW - timedelta(hours=1)),
                      last_seen_at=_ts(NOW))
        decision(self.conn, pid)
        self.assertEqual([o.posting_id for o in self.openings()], [good])

    def test_an_old_posting_first_seen_today_is_not_new(self):
        _opening(self.conn, self.c, hours=1, posted_on="2026-10-20")
        self.assertEqual(self.openings(), ())

    def test_early_career_fits_sort_first(self):
        swe2 = _opening(self.conn, self.c, hours=1, level_tier="swe2")
        ng = _opening(self.conn, self.c, hours=20, level_tier="new_grad")
        got = self.openings()
        self.assertEqual([o.posting_id for o in got], [ng, swe2])
        self.assertEqual([o.early_fit for o in got], [True, False])
        self.assertEqual(got[1].level, "SWE II role")


class LoadProspectsFreshTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()

    def prospects(self):
        canon = canonical_ids(self.conn)
        return {p.company_id: p for p in load_prospects(
            self.conn, ranked_list(self.conn, laned_only=True), canon, TODAY, CFG,
            load_outbound(self.conn, canon, tz=TZ), now=NOW)}

    def test_openings_and_visa_facts_reach_the_prospect(self):
        c = seed_scored_company(self.conn, "Tessera Labs", 60, ["level_role"], with_posting=False)
        pid = _opening(self.conn, c, hours=6)
        self.conn.execute("INSERT INTO evidence(company_id, fact, value, confidence, source_kind, source_ref, observed_on, "
                          "recorded_at) VALUES (?, 'everify', 'enrolled', 'confirmed', 'everify', 'https://e-verify.gov', "
                          "'2026-10-01', '2026-10-01T08:00:00Z')", (c,))
        p = self.prospects()[c]
        self.assertEqual([o.posting_id for o in p.openings], [pid])
        self.assertEqual(p.everify, "enrolled")
        self.assertEqual(fresh_pick(p, CFG).posting_id, pid)

    def test_a_no_go_company_is_never_a_prospect_even_with_a_new_opening(self):
        c = seed_scored_company(self.conn, "Nope Inc", 90, ["level_role"], with_posting=False)
        _opening(self.conn, c, hours=2)
        no_go(self.conn, c)
        self.assertNotIn(c, self.prospects())

    def test_a_fresh_company_below_the_scan_limit_is_scanned(self):
        cfg_limit = CFG.get("plan", "scan_limit")
        # Fill the scan with companies that score higher, then one with a new opening below them.
        for i in range(3):
            seed_scored_company(self.conn, f"Top {i}", 99 - i, ["level_role"], with_posting=False)
        low = seed_scored_company(self.conn, "Low Fresh", 1, ["level_role"], with_posting=False)
        _opening(self.conn, low, hours=3)
        import dataclasses as dc
        cfg = dc.replace(CFG, plan={**CFG.plan, "scan_limit": 2}, linkedin={**CFG.linkedin, "rotate_top_n": 2})
        self.assertLess(2, cfg_limit)
        canon = canonical_ids(self.conn)
        got = {p.company_id for p in load_prospects(self.conn, ranked_list(self.conn, laned_only=True), canon, TODAY,
                                                    cfg, load_outbound(self.conn, canon, tz=TZ), now=NOW)}
        self.assertIn(low, got)
        self.assertEqual(len(got), 3)
        # Past the scan limit and below the quality floor: not scanned.
        cfg = dc.replace(cfg, queue={**cfg.queue, "fresh_pool_top_n": 3})
        got = {p.company_id for p in load_prospects(self.conn, ranked_list(self.conn, laned_only=True), canon, TODAY,
                                                    cfg, load_outbound(self.conn, canon, tz=TZ), now=NOW)}
        self.assertNotIn(low, got)

    def test_a_pass_and_an_application_reach_the_prospect(self):
        c = seed_scored_company(self.conn, "Passed Co", 60, ["level_role"], with_posting=False)
        a = seed_scored_company(self.conn, "Applied Co", 50, ["level_role"], with_posting=False)
        append_event(self.conn, entity="company", entity_id=c, company_id=c, kind=PASSED, actor="devanshu",
                     at=_ts(NOW - timedelta(days=13)), detail={"reason": "too_big"})
        self.conn.execute("INSERT INTO application(company_id, applied_on) VALUES (?, '2026-10-20')", (a,))
        got = self.prospects()
        self.assertEqual(got[c].passed_days, 13)
        self.assertEqual(eligibility(got[c], CFG), (False, "passed"))
        self.assertEqual(got[a].applied_on, date(2026, 10, 20))
        self.assertEqual(eligibility(got[a], CFG), (False, "applied"))

    def test_a_pass_older_than_the_cooldown_is_not_read(self):
        c = seed_scored_company(self.conn, "Passed Co", 60, ["level_role"], with_posting=False)
        append_event(self.conn, entity="company", entity_id=c, company_id=c, kind=PASSED, actor="devanshu",
                     at=_ts(NOW - timedelta(days=15)), detail={})
        self.assertEqual(load_passes(self.conn, canonical_ids(self.conn), TODAY, CFG), {})
        self.assertTrue(eligibility(self.prospects()[c], CFG)[0])

    def test_an_undone_application_touch_does_not_count(self):
        c = seed_scored_company(self.conn, "Applied Co", 50, ["level_role"], with_posting=False)
        from outreach.outcomes.requests import own_context
        from outreach.store.writes import log_touch
        log_touch(self.conn, person_id=None, company_id=c, channel="application", sent_at="2026-10-21T15:00:00Z",
                  context=own_context(self.conn, c, None, None, "2026-10-21T15:00:00Z"), idempotency_key="t-app-1")
        self.assertEqual(load_applied(self.conn, canonical_ids(self.conn), TZ), {c: date(2026, 10, 21)})
        self.conn.execute("UPDATE outreach_touch SET undone_at = '2026-10-21T16:00:00Z'")
        self.assertEqual(load_applied(self.conn, canonical_ids(self.conn), TZ), {})


class BuildFreshTest(unittest.TestCase):
    def test_the_build_writes_the_fresh_row_first_with_its_new_opening(self):
        from outreach.plan.build import build_plan
        conn = fresh_store()
        top = [seed_scored_company(conn, f"Top {i}", 90 - i, ["level_role"]) for i in range(3)]
        low = seed_scored_company(conn, "Low Fresh", 10, ["level_role"], with_posting=False)
        build_at = datetime(2026, 11, 10, 13, 0, tzinfo=timezone.utc)  # 08:00 New York on TODAY
        pid = posting(conn, low, first_seen_at=_ts(build_at - timedelta(hours=20)), last_seen_at=_ts(build_at))
        decision(conn, pid, level_tier="swe1")
        build_plan(conn, today=TODAY, read_boards=False)
        rows = conn.execute("SELECT company_id, posting_id, rank, why FROM daily_list_entry WHERE list_date = ? "
                            "ORDER BY rank", (TODAY.isoformat(),)).fetchall()
        self.assertEqual([r["company_id"] for r in rows], [low, *top])
        self.assertEqual(rows[0]["posting_id"], pid)
        import json as _json
        self.assertEqual(_json.loads(rows[0]["why"])["role"]["posting_id"], pid)
