"""Task 8: the pure selection of the day's rows (#15). Hand-built prospects, no store."""
import itertools
import unittest
from datetime import date

from outreach.plan.config import default_plan_config
from outreach.plan.contact import ContactState
from outreach.plan.people import Candidate
from outreach.plan.select import (Caps, CarriedRow, Prospect, choose_rows, eligibility, group_of, options)

CFG = default_plan_config()
DAY = date(2026, 11, 10)
_ids = itertools.count(1)


def free(company: int) -> ContactState:
    return ContactState(company, "free", 0, 0, None, None, "No outreach yet")


def contact(company: int, status: str, *, people=1, email_people=0, last=7) -> ContactState:
    return ContactState(company, status, people, email_people, last, DAY, "x")


def cand(person_id: int | None = None, *, company=1, role="engineer", fit="other", email=None, basis=None,
         post=False, name=None) -> Candidate:
    pid = person_id if person_id is not None else next(_ids) + 1000
    return Candidate(person_id=pid, company_id=company, name=name or f"Person {pid}", title="", role_kind=role,
                     linkedin_url=None, email=email, email_basis=basis, fit=fit, employer_check="ok", sources=(),
                     found_on=None, post_contact=post, draft_id=None)


def prospect(company: int | None = None, *, score=50.0, lanes=("level_role",), status="free", position=None,
             candidates=(), people=None, email_people=0, last=7, carried=None, small=False) -> Prospect:
    cid = company if company is not None else next(_ids) + 100
    state = free(cid) if status == "free" else contact(
        cid, status, people=people if people is not None else 1, email_people=email_people, last=last)
    return Prospect(company_id=cid, name=f"Company {cid}", score=score, position=position or cid, lanes=tuple(lanes),
                    contact=state, candidates=tuple(candidates), small=small, carried=carried)


def many(n: int, lanes, *, start_score=90.0, base=1000):
    return [prospect(base + i, score=start_score - i, lanes=lanes) for i in range(n)]


def chosen_ids(rows):
    return [r.company_id for r in rows]


class CapacityTest(unittest.TestCase):
    def test_capacity_is_never_exceeded(self):
        email_cand = lambda c: [cand(company=c, role="founder", fit="other", email=f"f{c}@x.com", basis="published")]
        ps = [prospect(c, score=100 - c, candidates=email_cand(c) if c % 2 else ()) for c in range(1, 31)]
        rows = choose_rows(ps, Caps(7, 3, 6, 1), CFG)
        self.assertLessEqual(sum(r.channel == "linkedin_request" for r in rows), 7)
        self.assertLessEqual(sum(r.channel == "email" for r in rows), 3)
        self.assertEqual(sum(r.channel == "email" for r in rows), 3)
        self.assertEqual(len(chosen_ids(rows)), len(set(chosen_ids(rows))))
        self.assertEqual(len(rows), 10)

    def test_zero_capacity_returns_no_rows(self):
        self.assertEqual(choose_rows(many(5, ["level_role"]), Caps(0, 0, 0, 0), CFG), [])
        self.assertEqual(choose_rows([], Caps(7, 3, 6, 1), CFG), [])
        self.assertEqual(choose_rows(many(5, ["level_role"]), Caps(-2, -1, 0, 0), CFG), [])


class LaneShareTest(unittest.TestCase):
    def test_lane_share_80_20(self):
        role = many(20, ["level_role"], base=1000)
        reason = many(20, ["reason_no_role"], base=2000)
        rows = choose_rows(role + reason, Caps(7, 0, 6, 1), CFG)
        self.assertEqual(sorted(r.company_id for r in rows if r.group == "role"), [1000 + i for i in range(6)])
        self.assertEqual([r.company_id for r in rows if r.group == "reason"], [2000])

    def test_intern_junior_counts_in_the_role_pool(self):
        rows = choose_rows(many(3, ["intern_junior"]), Caps(3, 0, 2, 1), CFG)
        self.assertEqual({r.group for r in rows}, {"role"})
        self.assertEqual({r.list_group for r in rows}, {"intern_junior"})

    def test_short_pool_hands_slots_on(self):
        rows = choose_rows(many(3, ["level_role"], base=1000) + many(10, ["reason_no_role"], base=2000),
                           Caps(7, 0, 6, 1), CFG)
        self.assertEqual(sum(r.group == "role" for r in rows), 3)
        self.assertEqual(sum(r.group == "reason" for r in rows), 4)
        rows = choose_rows(many(3, ["level_role"], base=1000) + many(2, ["any_us_role"], base=3000),
                           Caps(7, 0, 6, 1), CFG)
        self.assertEqual([r.group for r in rows].count("role"), 3)
        self.assertEqual([r.group for r in rows].count("fill"), 2)
        self.assertEqual(len(rows), 5)

    def test_any_us_role_only_fills(self):
        ps = [prospect(1, score=99, lanes=["any_us_role"]), prospect(2, score=10, lanes=["level_role"])]
        rows = choose_rows(ps, Caps(1, 0, 1, 0), CFG)
        self.assertEqual(chosen_ids(rows), [2])
        rows = choose_rows(ps, Caps(2, 0, 1, 0), CFG)
        self.assertEqual(sorted(chosen_ids(rows)), [1, 2])

    def test_group_is_the_strongest_lane_only(self):
        self.assertEqual(group_of(["reason_no_role", "level_role"], CFG), "reason")
        self.assertEqual(group_of(["level_role", "reason_no_role"], CFG), "role")
        self.assertIsNone(group_of([], CFG))
        row = choose_rows([prospect(1, lanes=["reason_no_role", "level_role"])], Caps(1, 0, 0, 1), CFG)[0]
        self.assertEqual((row.group, row.list_group), ("reason", "reason_no_role"))


class EligibilityTest(unittest.TestCase):
    def test_waiting_engaged_and_lane_less_are_ineligible(self):
        self.assertEqual(eligibility(prospect(status="waiting"), CFG), (False, "waiting"))
        self.assertEqual(eligibility(prospect(status="engaged"), CFG), (False, "engaged"))
        self.assertFalse(eligibility(prospect(lanes=()), CFG)[0])
        self.assertTrue(eligibility(prospect(), CFG)[0])
        rows = choose_rows([prospect(1, status="waiting"), prospect(2, status="engaged"), prospect(3, lanes=())],
                           Caps(7, 3, 6, 1), CFG)
        self.assertEqual(rows, [])

    def test_stale_rotates_only_near_the_top(self):
        self.assertTrue(eligibility(prospect(status="stale", position=150, people=1), CFG)[0])
        self.assertFalse(eligibility(prospect(status="stale", position=151, people=1), CFG)[0])
        self.assertTrue(eligibility(prospect(status="stale", position=10, people=2), CFG)[0])
        self.assertFalse(eligibility(prospect(status="stale", position=10, people=3), CFG)[0])

    def test_rotation_picks_a_different_person_and_records_who_it_replaced(self):
        new = cand(60, company=5, fit="team")
        p = prospect(5, status="stale", last=7, candidates=[new])
        row = choose_rows([p], Caps(1, 0, 1, 0), CFG)[0]
        self.assertEqual((row.person_id, row.rotation_from), (60, 7))
        free_row = choose_rows([prospect(6, candidates=[cand(61, company=6)])], Caps(1, 0, 1, 0), CFG)[0]
        self.assertIsNone(free_row.rotation_from)


class PersonTest(unittest.TestCase):
    def test_preference_order_decides_the_person(self):
        recruiter = cand(10, role="recruiter", fit="recruiter")
        alum = cand(11, role="alum", fit="context")
        team = cand(12, role="engineer", fit="team")
        self.assertEqual(options(prospect(1, candidates=[recruiter, alum, team]), CFG)[0][1], 12)
        row = choose_rows([prospect(1, candidates=[recruiter, alum, team])], Caps(1, 0, 1, 0), CFG)[0]
        self.assertEqual((row.person_id, row.channel, row.channel_reason), (12, "linkedin_request", "linkedin"))
        self.assertEqual([o[1] for o in options(prospect(1, candidates=[team, alum]), CFG)], [12, 11, None])

    def test_hn_post_contact_wins_and_goes_by_email(self):
        hn = cand(20, role="engineer", fit="other", email="pat@acme.com", basis="published", post=True)
        team = cand(21, role="engineer", fit="team")
        p = prospect(1, candidates=[team, hn])
        self.assertEqual(options(p, CFG)[0], ("email", 20, "hn_post_asks_email"))
        row = choose_rows([p], Caps(1, 1, 1, 0), CFG)[0]
        self.assertEqual((row.person_id, row.channel), (20, "email"))

    def test_email_cap_pushes_the_hn_company_to_linkedin_when_another_candidate_exists(self):
        hn = cand(20, role="engineer", fit="other", email="pat@acme.com", basis="published", post=True)
        team = cand(21, role="engineer", fit="team")
        row = choose_rows([prospect(1, candidates=[hn, team])], Caps(1, 0, 1, 0), CFG)[0]
        self.assertEqual((row.person_id, row.channel), (21, "linkedin_request"))
        alone = choose_rows([prospect(2, candidates=[hn])], Caps(1, 0, 1, 0), CFG)[0]
        self.assertEqual((alone.person_id, alone.channel, alone.channel_reason),
                         (None, "linkedin_request", "find_someone"))

    def test_email_people_cap_per_company(self):
        hn = cand(20, role="founder", fit="other", email="pat@acme.com", basis="published")
        self.assertEqual(options(prospect(1, candidates=[hn]), CFG)[0][0], "email")
        capped = prospect(1, status="stale", email_people=2, candidates=[hn])
        self.assertEqual([o[0] for o in options(capped, CFG)], ["linkedin_request"])
        self.assertEqual(options(capped, CFG)[0], ("linkedin_request", None, "find_someone"))
        one = prospect(1, status="stale", email_people=1, candidates=[hn])
        self.assertEqual(options(one, CFG)[0][0], "email")

    def test_no_candidate_is_find_someone_and_sorts_after_people_rows(self):
        ps = [prospect(1, score=90), prospect(2, score=10, candidates=[cand(30, company=2, fit="team")])]
        rows = choose_rows(ps, Caps(2, 0, 2, 0), CFG)
        self.assertEqual([(r.company_id, r.person_id) for r in rows], [(2, 30), (1, None)])
        self.assertEqual(rows[1].channel, "linkedin_request")
        self.assertEqual(rows[1].channel_reason, "find_someone")

    def test_display_order_is_score_then_company_id(self):
        ps = [prospect(3, score=50), prospect(1, score=50), prospect(2, score=70)]
        self.assertEqual(chosen_ids(choose_rows(ps, Caps(3, 0, 3, 0), CFG)), [2, 1, 3])


class CarriedTest(unittest.TestCase):
    def carry(self, company: int, person: int | None, first: date):
        return CarriedRow(company, person, date(2026, 11, 9), first)

    def test_carried_rows_are_admitted_first_and_count_against_their_pool(self):
        carried = [prospect(100 + i, score=1.0, carried=self.carry(100 + i, None, date(2026, 11, 2) if i == 0
                                                                   else date(2026, 11, 6)))
                   for i in range(7)]
        fresh = many(10, ["level_role"], start_score=99, base=500) + many(5, ["reason_no_role"], base=700)
        rows = choose_rows(carried + fresh, Caps(7, 0, 6, 1), CFG)
        self.assertEqual(sorted(chosen_ids(rows)), sorted(100 + i for i in range(7)))
        # eight carried rows: the eighth does not fit and is dropped
        carried8 = carried + [prospect(108, score=1.0, carried=self.carry(108, None, date(2026, 11, 9)))]
        rows = choose_rows(carried8, Caps(7, 0, 6, 1), CFG)
        self.assertEqual(len(rows), 7)
        self.assertNotIn(108, chosen_ids(rows))

    def test_a_carried_row_keeps_its_dates_and_a_partial_carry_leaves_room_for_new_rows(self):
        carried = [prospect(100 + i, score=1.0, carried=self.carry(100 + i, None, date(2026, 11, 3)))
                   for i in range(4)]
        fresh = many(10, ["level_role"], start_score=99, base=500) + many(5, ["reason_no_role"], base=700)
        rows = choose_rows(carried + fresh, Caps(7, 0, 6, 1), CFG)
        mine = [r for r in rows if r.company_id < 200]
        self.assertEqual(len(mine), 4)
        self.assertTrue(all(r.carried_from == date(2026, 11, 9) and r.first_proposed == date(2026, 11, 3)
                            for r in mine))
        self.assertEqual(sum(1 for r in rows if r.group == "role"), 6)  # 4 carried + 2 new
        self.assertEqual(sum(1 for r in rows if r.group == "reason"), 1)
        self.assertTrue(all(r.carried_from is None for r in rows if r.company_id >= 500))

    def test_a_carried_person_who_is_still_a_candidate_goes_first(self):
        team, other = cand(40, company=9, fit="team"), cand(41, company=9, fit="context")
        p = prospect(9, candidates=[team, other], carried=self.carry(9, 41, date(2026, 11, 6)))
        self.assertEqual(options(p, CFG)[0][1], 41)
        gone = prospect(9, candidates=[team], carried=self.carry(9, 41, date(2026, 11, 6)))
        self.assertEqual(options(gone, CFG)[0][1], 40)

    def test_a_carried_person_without_room_keeps_the_company_waiting_never_someone_else(self):
        ada = cand(50, company=9, role="founder", email="ada@nine.example", basis="published")
        ben = cand(51, company=9, fit="team")
        p = prospect(9, score=99.0, candidates=[ada, ben], carried=self.carry(9, 50, date(2026, 11, 6)))
        others = many(3, ["level_role"], start_score=50, base=500)
        rows = choose_rows([p] + others, Caps(5, 0, 4, 1), CFG)  # Ada is an email row and no email slot is left
        self.assertNotIn(9, chosen_ids(rows))  # not Ben in her place, and no find-someone row either
        self.assertEqual(sorted(chosen_ids(rows)), [500, 501, 502])
        rows = choose_rows([p] + others, Caps(5, 1, 4, 1), CFG)
        mine = [r for r in rows if r.company_id == 9]
        self.assertEqual([(r.person_id, r.channel) for r in mine], [(50, "email")])
        # the other way round: Ben carried on LinkedIn with no LinkedIn room; Ada is not emailed instead
        q = prospect(9, score=99.0, candidates=[ada, ben], carried=self.carry(9, 51, date(2026, 11, 6)))
        self.assertEqual(choose_rows([q], Caps(0, 3, 0, 0), CFG), [])

    def test_oldest_first_proposed_is_admitted_first(self):
        old = prospect(1, score=1.0, carried=self.carry(1, None, date(2026, 11, 2)))
        newer = prospect(2, score=99.0, carried=self.carry(2, None, date(2026, 11, 6)))
        rows = choose_rows([newer, old], Caps(1, 0, 1, 0), CFG)
        self.assertEqual(chosen_ids(rows), [1])

    def test_an_ineligible_carried_company_is_dropped(self):
        p = prospect(1, status="waiting", carried=self.carry(1, None, date(2026, 11, 6)))
        self.assertEqual(choose_rows([p], Caps(7, 3, 6, 1), CFG), [])


if __name__ == "__main__":
    unittest.main()
