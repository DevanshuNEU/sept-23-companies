import unittest
from datetime import date, timedelta

from outreach.plan.config import default_plan_config
from outreach.plan.contact import contact_states, load_contact_states
from outreach.plan.outbound import Outbound
from outreach.ranking.common import canonical_ids
from outreach.store.writes import log_touch
from tests.plan_seed import TODAY, seed_outbound, seed_person, seed_scored_company
from tests.storetest import fresh_store

CFG = default_plan_config()


def ago(days: int) -> date:
    return TODAY - timedelta(days=days)


def row(sent_ago: int, *, person=1, company=1, channel="linkedin_request", accepted_ago=None, replied=False,
        progressed=False) -> Outbound:
    sent = ago(sent_ago)
    accepted = ago(accepted_ago) if accepted_ago is not None else None
    return Outbound(person_id=person, company_id=company, channel=channel, sent_on=sent,
                    anchor_on=max(sent, accepted or sent), accepted=accepted is not None, replied=replied,
                    progressed=progressed, lane=None, context_source="plan", source="touch")


def state(rows, company=1):
    return contact_states(rows, [company], TODAY, CFG)[company]


class ContactStateTest(unittest.TestCase):
    def test_boundary_is_day_fourteen(self):
        self.assertEqual(state([row(13)]).status, "waiting")
        self.assertEqual(state([row(14)]).status, "stale")

    def test_accepted_request_waits_from_the_accept_date(self):
        self.assertEqual(state([row(30, accepted_ago=5)]).status, "waiting")
        self.assertEqual(state([row(30, accepted_ago=20)]).status, "stale")

    def test_any_reply_or_progress_is_engaged_and_never_stale(self):
        self.assertEqual(state([row(90, replied=True)]).status, "engaged")
        self.assertEqual(state([row(90, progressed=True)]).status, "engaged")
        self.assertEqual(state([row(90, person=1), row(60, person=2, replied=True)]).status, "engaged")

    def test_email_and_linkedin_both_count(self):
        s = state([row(40, person=1, channel="email"), row(3, person=2, channel="linkedin_request")])
        self.assertEqual(s.status, "waiting")
        s = state([row(3, person=1, channel="email"), row(40, person=2, channel="linkedin_request")])
        self.assertEqual(s.status, "waiting")

    def test_application_only_company_is_free(self):
        conn = fresh_store()
        company = seed_scored_company(conn, "Acme", 5.0, ["level_role"])
        log_touch(conn, person_id=None, company_id=company, channel="application", sent_at="2026-11-09T15:00:00Z",
                  context={"source": "own", "lane": None, "roleKind": None, "roleAgeDays": None,
                           "rankerVersion": None, "signals": {}, "roleNamed": False}, idempotency_key="app-1")
        self.assertEqual(load_contact_states(conn, [company], canonical_ids(conn), TODAY, CFG)[company].status,
                         "free")
        s = state([])
        self.assertEqual((s.status, s.people_contacted, s.email_people, s.last_person_id, s.last_anchor),
                         ("free", 0, 0, None, None))

    def test_counts_and_last_person(self):
        s = state([row(30, person=1, channel="email"), row(20, person=2), row(5, person=3)])
        self.assertEqual((s.people_contacted, s.email_people, s.last_person_id, s.last_anchor),
                         (3, 1, 3, ago(5)))
        self.assertEqual(s.status, "waiting")

    def test_one_person_twice_counts_once(self):
        s = state([row(30, person=1), row(20, person=1, channel="linkedin_message")])
        self.assertEqual(s.people_contacted, 1)

    def test_reason_lines(self):
        self.assertEqual(state([row(5)]).reason, "Request sent 5 days ago, no answer yet")
        self.assertEqual(state([row(1, channel="email")]).reason, "Email sent 1 day ago, no answer yet")
        self.assertEqual(state([row(30, accepted_ago=2)]).reason, "Request accepted 2 days ago, no answer yet")
        self.assertEqual(state([row(20)]).reason, "No answer for 20 days")
        self.assertEqual(state([row(9, replied=True)]).reason, "Someone replied")
        for status_row in (row(0), row(20), row(9, replied=True), row(9, progressed=True)):
            reason = state([status_row]).reason
            self.assertFalse(any(ord(ch) in (0x2013, 0x2014) for ch in reason))

    def test_other_companies_are_ignored_and_each_id_gets_a_state(self):
        got = contact_states([row(3, company=1), row(20, company=2)], [1, 2, 3], TODAY, CFG)
        self.assertEqual({c: s.status for c, s in got.items()}, {1: "waiting", 2: "stale", 3: "free"})
        self.assertEqual(contact_states([row(3, company=1)], [], TODAY, CFG), {})

    def test_store_read_follows_a_merge(self):
        conn = fresh_store()
        survivor = seed_scored_company(conn, "Acme", 5.0, ["level_role"])
        dup = seed_scored_company(conn, "Acme Inc", 1.0, [])
        free = seed_scored_company(conn, "Beta", 2.0, ["level_role"])
        person = seed_person(conn, dup, "Dee")
        seed_outbound(conn, person, dup, "linkedin_request", ago(4))
        conn.execute("UPDATE company SET status = 'removed', remove_reason = 'duplicate', merged_into = ? "
                     "WHERE company_id = ?", (survivor, dup))
        got = load_contact_states(conn, [survivor, free], canonical_ids(conn), TODAY, CFG)
        self.assertEqual((got[survivor].status, got[survivor].last_person_id, got[survivor].people_contacted),
                         ("waiting", person, 1))
        self.assertEqual(got[free].status, "free")


if __name__ == "__main__":
    unittest.main()
