"""Email fit and the email lane (2026-10-27): which companies are worth a cold email, in plain words, and up to
`email.email_companies_per_day` more companies a day beyond today's picks. Pure rules first, then the store read on a
scratch store. Nothing here names a person, drafts or sends."""
import dataclasses
import unittest
from datetime import date

from outreach.plan.config import default_plan_config
from outreach.plan.contact import ContactState
from outreach.plan.email_fit import (EmailFacts, EmailFit, email_fit, email_lane, lane_text, load_email_facts,
                                     plain_domain, size_words)
from outreach.plan.select import Prospect
from outreach.ranking.common import canonical_ids
from tests import apiseed as s
from tests.plan_seed import seed_outbound, seed_person
from tests.storetest import fresh_store

CFG = default_plan_config()
DAY = date(2026, 11, 10)
MOST = CFG.get("email", "email_max_headcount")
CAP = CFG.get("email", "people_per_company")


def free(cid: int = 1) -> ContactState:
    return ContactState(cid, "free", 0, 0, None, None, "No outreach yet", 0, 4, None)


def facts(**kw) -> EmailFacts:
    base = dict(company_id=1, status="candidate", no_go=False, paused=False, held=False, contact=free(),
                domain="acme.com", domain_confidence="estimate", mail_server=None, size_low=11, size_high=50)
    base.update(kw)
    return EmailFacts(**base)


def fit(**kw) -> EmailFit:
    return email_fit(facts(**kw), CFG)


class ConfigTest(unittest.TestCase):
    def test_the_shipped_numbers(self):
        self.assertEqual(MOST, 200)
        self.assertEqual(CFG.get("email", "email_companies_per_day"), 6)
        self.assertEqual(CAP, 2)


class FitTest(unittest.TestCase):
    def test_a_small_company_with_a_website_can_be_emailed(self):
        f = fit()
        self.assertEqual((f.verdict, f.label, f.domain), ("email", "Email", "acme.com"))
        self.assertEqual(f.reasons, ("Website known (acme.com)", "11 to 50 people: a founder or engineer reads this"))

    def test_a_tiny_company_says_a_founder_reads_it(self):
        self.assertEqual(fit(size_low=12, size_high=12).reasons[1], "About 12 people: a founder reads this")
        self.assertEqual(fit(size_low=1, size_high=25).reasons[1], "1 to 25 people: a founder reads this")
        self.assertEqual(fit(size_low=1, size_high=26).reasons[1], "1 to 26 people: a founder or engineer reads this")

    def test_no_website_is_linkedin_only(self):
        f = fit(domain=None, domain_confidence=None)
        self.assertEqual((f.verdict, f.label), ("linkedin_only", "LinkedIn only"))
        self.assertEqual(f.reasons, ("No website on file, so no address pattern",))

    def test_a_guessed_website_counts_and_says_so(self):
        f = fit(domain_confidence="guess")
        self.assertEqual(f.verdict, "email")
        self.assertEqual(f.reasons[0], "Website known (acme.com, a guess)")

    def test_a_domain_with_no_mail_server_is_linkedin_only(self):
        self.assertEqual(fit(mail_server="none").reasons, ("acme.com takes no mail (no mail server)",))
        self.assertEqual(fit(mail_server="ok").verdict, "email")

    def test_size_at_the_limit_is_fine_and_one_over_is_not(self):
        self.assertEqual(fit(size_low=MOST, size_high=MOST).verdict, "email")
        over = fit(size_low=MOST + 1, size_high=500)
        self.assertEqual(over.verdict, "linkedin_only")
        self.assertEqual(over.reasons, (f"{MOST + 1} to 500 people: over {MOST}, a cold email gets lost there",))

    def test_size_is_read_from_the_low_end_as_the_size_gate_does(self):
        self.assertEqual(fit(size_low=51, size_high=MOST + 300).verdict, "email")

    def test_unknown_size_counts_as_ok(self):
        f = fit(size_low=None, size_high=None)
        self.assertEqual(f.verdict, "email")
        self.assertEqual(f.reasons[1], "Size not on file, no sign it is large")

    def test_no_go_removed_closed_and_paused_block(self):
        self.assertEqual(fit(no_go=True).reasons, ("On the no-go list",))
        self.assertEqual(fit(status="removed").reasons, ("Removed from Knock",))
        self.assertEqual(fit(status="closed").reasons, ("Closed in Knock",))
        self.assertEqual(fit(paused=True).reasons, ("You paused it",))

    def test_a_reply_holds_it(self):
        self.assertEqual(fit(held=True).reasons, ("Someone there replied: work that thread first",))
        engaged = ContactState(1, "engaged", 1, 0, 7, DAY, "Someone replied", 1, 0, None)
        self.assertEqual(fit(contact=engaged).verdict, "linkedin_only")

    def test_already_emailed_up_to_the_cap(self):
        st = ContactState(1, "stale", CAP, CAP, 7, date(2026, 10, 1), "No answer for 40 days", 0, 4, None)
        self.assertEqual(fit(contact=st).reasons, (f"{CAP} people there already emailed (up to {CAP})",))
        one = ContactState(1, "stale", 1, CAP - 1, 7, date(2026, 10, 1), "No answer for 40 days", 0, 4, None)
        f = fit(contact=one)
        self.assertEqual(f.verdict, "email")
        self.assertIn(f"{CAP - 1} of {CAP} emailed there so far", f.reasons)

    def test_a_full_wave_in_flight_waits(self):
        st = ContactState(1, "waiting", 4, 0, 7, DAY, "Request sent today, no answer yet", 4, 0, date(2026, 11, 24))
        self.assertEqual(fit(contact=st).reasons, ("Wave in flight: 4 asked, free again Nov 24",))

    def test_a_wave_with_room_can_still_be_emailed(self):
        st = ContactState(1, "waiting", 2, 0, 7, DAY, "Request sent today, no answer yet", 2, 2, date(2026, 11, 24))
        f = fit(contact=st)
        self.assertEqual(f.verdict, "email")
        self.assertEqual(f.reasons[-1], "2 asked in this wave, room for 2 more")

    def test_every_blocker_is_listed(self):
        f = fit(domain=None, size_low=900, size_high=1000, paused=True)
        self.assertEqual(f.reasons, ("You paused it", "No website on file, so no address pattern",
                                     f"900 to 1000 people: over {MOST}, a cold email gets lost there"))

    def test_no_dashes_in_any_reason(self):
        many = [fit(), fit(domain=None), fit(no_go=True), fit(size_low=900, size_high=None), fit(held=True)]
        for f in many:
            for r in f.reasons:
                self.assertNotIn(chr(0x2014), r)
                self.assertNotIn(chr(0x2013), r)

    def test_size_words(self):
        self.assertEqual(size_words(50, 50), "About 50 people")
        self.assertEqual(size_words(1, 1), "About 1 person")
        self.assertEqual(size_words(300, None), "300 or more people")


class DomainTest(unittest.TestCase):
    def test_plain_domain(self):
        self.assertEqual(plain_domain("www.Acme.com"), "acme.com")
        self.assertIsNone(plain_domain("gmail.com"))  # free mail
        self.assertIsNone(plain_domain("github.com"))  # a platform host
        self.assertIsNone(plain_domain("acme"))
        self.assertIsNone(plain_domain(None))


def prospect(cid: int, position: int, *, status="free") -> Prospect:
    st = free(cid) if status == "free" else ContactState(cid, status, 1, 0, 7, DAY, "x", 1, 3, date(2026, 11, 20))
    return Prospect(company_id=cid, name=f"Company {cid}", score=100.0 - position, position=position,
                    lanes=("level_role",), contact=st, candidates=(), small=False, carried=None)


EMAIL = EmailFit("email", ("Website known (x.com)",), "x.com")
LINKEDIN = EmailFit("linkedin_only", ("No website on file, so no address pattern",), None)


class LaneTest(unittest.TestCase):
    def test_by_rank_without_todays_picks_and_capped(self):
        ps = [prospect(cid, pos) for pos, cid in enumerate([30, 10, 20, 40, 50, 60, 70, 80, 90], 1)]
        fits = {p.company_id: EMAIL for p in ps}
        lane = email_lane(ps, {10}, fits, CFG)
        self.assertEqual([b.prospect.company_id for b in lane], [30, 20, 40, 50, 60, 70])

    def test_only_email_fits_and_only_companies_the_plan_could_pick(self):
        ps = [prospect(1, 1), prospect(2, 2), prospect(3, 3, status="waiting"), prospect(4, 4)]
        fits = {1: EMAIL, 2: LINKEDIN, 3: EMAIL}  # 4 has no fit
        self.assertEqual([b.prospect.company_id for b in email_lane(ps, set(), fits, CFG)], [1])

    def test_the_order_is_the_ranking_whatever_the_input_order(self):
        ps = [prospect(5, 3), prospect(6, 1), prospect(7, 2)]
        fits = {p.company_id: EMAIL for p in ps}
        self.assertEqual([b.prospect.company_id for b in email_lane(list(reversed(ps)), set(), fits, CFG)], [6, 7, 5])
        self.assertEqual([b.prospect.company_id for b in email_lane(ps, set(), fits, CFG)], [6, 7, 5])

    def test_zero_turns_the_lane_off(self):
        off = dataclasses.replace(CFG, email={**CFG.email, "email_companies_per_day": 0})
        self.assertEqual(email_lane([prospect(1, 1)], set(), {1: EMAIL}, off), [])

    def test_lane_text(self):
        self.assertEqual(lane_text(6, CFG), "6 more companies you can email today, by rank. Find up to 2 people at "
                                            "each and bring the names to your terminal.")
        self.assertTrue(lane_text(0, CFG).startswith("No other company can be emailed today."))


class LoadTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()

    def load(self, *ids):
        return load_email_facts(self.conn, canonical_ids(self.conn), ids, DAY, CFG)

    def test_the_best_usable_domain_and_size(self):
        c = s.company(self.conn, "Acme")
        s.evidence(self.conn, c, "domain", "www.acme.com", confidence="estimate", source_kind="old_dashboard")
        s.evidence(self.conn, c, "size", "11-50", confidence="estimate", low=11, high=50)
        s.mx(self.conn, "acme.com", "ok")
        f = self.load(c)[c]
        self.assertEqual((f.domain, f.domain_confidence, f.mail_server, f.size_low, f.size_high),
                         ("acme.com", "estimate", "ok", 11, 50))
        self.assertEqual(email_fit(f, CFG).verdict, "email")

    def test_a_shared_or_platform_domain_is_no_website(self):
        c = s.company(self.conn, "Acme")
        s.evidence(self.conn, c, "domain", "parent.com")
        s.evidence(self.conn, c, "domain", "notion.site")
        self.conn.execute("INSERT INTO shared_key(kind, value, reason, recorded_at) VALUES ('domain', 'parent.com', "
                          "'2 employers: A, B', '2026-11-01T00:00:00Z')")
        f = self.load(c)[c]
        self.assertIsNone(f.domain)
        self.assertEqual(email_fit(f, CFG).reasons, ("No website on file, so no address pattern",))

    def test_a_merged_duplicate_brings_its_domain_and_a_locked_size_wins(self):
        keep = s.company(self.conn, "Acme")
        dup = s.company(self.conn, "Acme Inc", "removed", remove_reason="merged", merged_into=keep)
        s.evidence(self.conn, dup, "domain", "acme.io")
        s.evidence(self.conn, keep, "size", "500", confidence="estimate", low=500, high=1000)
        s.evidence(self.conn, keep, "size", "40", locked=True, low=40, high=40)
        f = self.load(keep, dup)
        self.assertEqual(list(f), [keep])
        self.assertEqual((f[keep].domain, f[keep].size_low), ("acme.io", 40))

    def test_no_go_paused_and_emailed_people_are_read(self):
        a = s.company(self.conn, "Acme", status="working", paused_at="2026-11-01T00:00:00Z")
        b = s.company(self.conn, "Bolt")
        self.conn.execute("INSERT INTO no_go(company_id, reason, source, created_at) VALUES (?, 'other', 'devanshu', "
                          "'2026-11-01T12:00:00Z')", (b,))
        c = s.company(self.conn, "Corp")
        for i, name in enumerate(("Ada", "Bea")):
            pid = seed_person(self.conn, c, name, email=f"{name.lower()}@corp.com")
            seed_outbound(self.conn, pid, c, "email", date(2026, 9, 1 + i))
        got = self.load(a, b, c)
        self.assertTrue(got[a].paused)
        self.assertTrue(got[b].no_go)
        self.assertEqual(got[c].contact.email_people, 2)
        self.assertEqual(email_fit(got[c], CFG).reasons[0], "2 people there already emailed (up to 2)")


if __name__ == "__main__":
    unittest.main()
