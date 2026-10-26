"""route_person and the title readers (connect-integration.md section C, company-first P3): how a person Devanshu
found is reached. Pure: facts in, one verdict with a one-sentence reason and the key behind it out."""
import dataclasses
import unittest
from datetime import date

from outreach.plan.config import default_plan_config
from outreach.plan.route import (Budget, CompanyFacts, PersonFacts, Taken, VERDICTS, classify_role, order_people,
                                 route_all, route_person, seniority)

TODAY = date(2026, 11, 10)
CFG = default_plan_config()
DASHES = (chr(0x2013), chr(0x2014))


def company(**over) -> CompanyFacts:
    base = dict(name="Tessera", status="candidate", close_reason=None, no_go=False, paused=False, contact="free",
                contact_reason="No outreach yet", wave_people=0, free_on=None, email_people=0, on_queue=False)
    base.update(over)
    return CompanyFacts(**base)


def person(name="Priya Rao", title="Software Engineer", **over) -> PersonFacts:
    base = dict(name=name, title=title, role_kind=classify_role(title, CFG), seniority=seniority(title, CFG),
                tenure_months=None, no_go=False, said_no=False, asked_on=None, email_code=None, has_email=False,
                recruiter_blocked=False)
    base.update(over)
    return PersonFacts(**base)


BUDGET = Budget(linkedin_left=50, key="linkedin.hard_total")


def route(p=None, c=None, budget=BUDGET, taken=Taken(), cfg=CFG):
    return route_person(p or person(), c or company(), budget, taken, TODAY, cfg)


class TitleTest(unittest.TestCase):
    def test_role_kind_from_the_title(self):
        for title, kind in (("Software Engineer", "engineer"), ("Founding Engineer", "engineer"),
                            ("Senior Backend Developer", "engineer"), ("Member of Technical Staff", "engineer"),
                            ("Engineering Manager", "manager"), ("Head of Engineering", "manager"),
                            ("CTO", "manager"), ("VP Engineering", "manager"), ("Co-founder & CTO", "founder"),
                            ("Founder", "founder"), ("CEO", "founder"), ("Technical Recruiter", "recruiter"),
                            ("Talent Acquisition Partner", "recruiter"), ("Sales Engineer", "other"),
                            ("Account Executive", "other"), ("Product Designer", "other"), ("Manager", "other"),
                            ("", "other")):
            with self.subTest(title=title):
                self.assertEqual(classify_role(title, CFG), kind)

    def test_seniority_reads_the_first_segment_only(self):
        for title, level in (("Software Engineer", "ic"), ("Senior Software Engineer", "senior"),
                             ("Sr. Engineer", "senior"), ("Staff Engineer", "senior"), ("Tech Lead", "lead"),
                             ("Engineering Manager", "lead"), ("Member of Technical Staff", "ic"),
                             ("Software Engineer at Staff Inc", "ic"), ("Software Engineer, Lead Platform", "ic"),
                             ("SRE", "ic"), ("Product Designer", "unknown")):
            with self.subTest(title=title):
                self.assertEqual(seniority(title, CFG), level)


class RouteTest(unittest.TestCase):
    def assert_route(self, got, verdict, key):
        self.assertEqual((got.verdict, got.key), (verdict, key), got.reason)
        self.assertIn(got.verdict, VERDICTS)
        self.assertTrue(got.reason.endswith("."), got.reason)
        self.assertFalse(any(d in got.reason for d in DASHES), got.reason)

    def test_a_plain_engineer_at_a_fresh_company_gets_a_note(self):
        got = route(person(tenure_months=26))
        self.assert_route(got, "linkedin_note", "linkedin.hard_total")
        self.assertIn("2 years", got.reason)

    def test_a_company_on_todays_queue_counts_toward_the_plan_quota(self):
        got = route(c=company(on_queue=True), budget=Budget(linkedin_left=10, key="linkedin.plan_quota"))
        self.assert_route(got, "linkedin_note", "linkedin.plan_quota")

    def test_stops_come_first(self):
        self.assert_route(route(c=company(no_go=True)), "stop", "#9 no-go")
        self.assert_route(route(c=company(status="removed")), "stop", "company.status")
        got = route(c=company(status="closed", close_reason="interview"))
        self.assert_route(got, "stop", "company.status")
        self.assertIn("interview", got.reason)
        self.assert_route(route(person(no_go=True)), "stop", "#9 no-go")
        self.assert_route(route(person(said_no=True)), "stop", "#9 said no")
        self.assert_route(route(person(no_go=True), company(paused=True)), "stop", "#9 no-go")

    def test_paused_and_engaged_companies_wait(self):
        self.assert_route(route(c=company(paused=True)), "wait", "company.paused")
        self.assert_route(route(c=company(contact="engaged", contact_reason="Someone replied")), "wait",
                          "#9 a reply holds the company")

    def test_someone_asked_recently_waits_and_someone_asked_long_ago_is_someone_else(self):
        got = route(person(asked_on=date(2026, 11, 4)))
        self.assert_route(got, "wait", "linkedin.rotate_days")
        self.assertIn("Nov 4", got.reason)
        self.assert_route(route(person(asked_on=date(2026, 10, 1))), "find_someone_else", "linkedin.rotate_days")

    def test_outside_engineering_and_a_recruiter_with_others_known_are_someone_else(self):
        got = route(person(title="Account Executive"))
        self.assert_route(got, "find_someone_else", "route.reach_kinds")
        self.assertIn("your pick", got.reason)
        self.assert_route(route(person(title="Technical Recruiter", recruiter_blocked=True)), "find_someone_else",
                          "#9 recruiters last")
        self.assert_route(route(person(title="Technical Recruiter")), "linkedin_note", "linkedin.hard_total")

    def test_a_full_wave_waits_until_the_company_is_free_again(self):
        got = route(c=company(contact="waiting", wave_people=4, free_on=date(2026, 11, 20)))
        self.assert_route(got, "wait", "route.wave_size")
        self.assertIn("Nov 20", got.reason)
        self.assert_route(route(c=company(contact="waiting", wave_people=2, free_on=date(2026, 11, 20)),
                                taken=Taken(wave=1)), "linkedin_note", "linkedin.hard_total")
        self.assert_route(route(c=company(contact="waiting", wave_people=2, free_on=date(2026, 11, 20)),
                                taken=Taken(wave=2)), "wait", "route.wave_size")

    def test_email_only_where_pick_channel_said_email_and_the_company_has_room(self):
        founder = person("Ana Ruiz", "Founder", email_code="founder_published_email", has_email=True)
        got = route(founder)
        self.assert_route(got, "email", "email.people_per_company")
        self.assertIn("published", got.reason)
        self.assertIn("ramp", got.reason)
        self.assert_route(route(founder, company(email_people=2)), "linkedin_note", "linkedin.hard_total")
        self.assert_route(route(founder, taken=Taken(email=2)), "linkedin_note", "linkedin.hard_total")
        unbacked = route(person("Eli Park", has_email=True))
        self.assert_route(unbacked, "linkedin_note", "linkedin.hard_total")
        self.assertIn("published or verified", unbacked.reason)

    def test_no_requests_left_in_the_week_waits(self):
        self.assert_route(route(budget=Budget(linkedin_left=0, key="linkedin.hard_total")), "wait",
                          "linkedin.hard_total")
        self.assert_route(route(budget=Budget(linkedin_left=2, key="linkedin.plan_quota"), taken=Taken(linkedin=2)),
                          "wait", "linkedin.plan_quota")


class OrderTest(unittest.TestCase):
    PEOPLE = [person("Mo Lead", "Engineering Manager"), person("Sam Senior", "Senior Software Engineer"),
              person("Rae Recruiter", "Technical Recruiter"), person("Ian Plain", "Software Engineer")]

    def test_the_preference_puts_plain_engineers_first_and_recruiters_last(self):
        self.assertEqual(order_people(self.PEOPLE, CFG), [3, 1, 0, 2])

    def test_with_the_preference_off_the_paste_order_stands_but_recruiters_still_go_last(self):
        off = dataclasses.replace(CFG, route={**CFG.route, "prefer_ic_in_role": False})
        self.assertEqual(order_people(self.PEOPLE, off), [0, 1, 3, 2])

    def test_route_all_fills_one_wave_and_hides_nobody(self):
        six = self.PEOPLE + [person("Una Plain", "Software Engineer"), person("Ned Plain", "Software Engineer")]
        out = route_all(six, company(), BUDGET, TODAY, CFG)
        self.assertEqual(sorted(i for i, _r, _p in out), [0, 1, 2, 3, 4, 5])  # everyone comes back
        self.assertEqual([six[i].name for i, _r, _p in out],
                         ["Ian Plain", "Una Plain", "Ned Plain", "Sam Senior", "Mo Lead", "Rae Recruiter"])
        verdicts = {six[i].name: r.verdict for i, r, _p in out}
        self.assertEqual(verdicts, {"Ian Plain": "linkedin_note", "Una Plain": "linkedin_note",
                                    "Ned Plain": "linkedin_note", "Sam Senior": "linkedin_note", "Mo Lead": "wait",
                                    "Rae Recruiter": "find_someone_else"})
        prefs = [p for _i, _r, p in out]
        self.assertTrue(all(p["key"] == "route.prefer_ic_in_role" and p["label"] == "unmeasured" for p in prefs))


if __name__ == "__main__":
    unittest.main()
