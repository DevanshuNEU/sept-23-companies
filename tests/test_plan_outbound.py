import unittest
from datetime import date

from outreach.plan.outbound import load_outbound
from outreach.ranking.common import canonical_ids
from outreach.store.timefmt import utcnow
from outreach.store.writes import log_touch, record_outcome, undo_touch
from tests.plan_seed import (TODAY, seed_log_only, seed_outbound, seed_person, seed_scored_company)
from tests.storetest import fresh_store

TZ = "America/New_York"
D = date


def _context(source="plan", lane=None):
    return {"source": source, "lane": lane, "roleKind": None, "roleAgeDays": None, "rankerVersion": None,
            "signals": {}, "roleNamed": False}


class OutboundTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.company = seed_scored_company(self.conn, "Acme", 5.0, ["level_role"])
        self.person = seed_person(self.conn, self.company, "Ada")

    def load(self, **kw):
        return load_outbound(self.conn, canonical_ids(self.conn), tz=TZ, **kw)

    def test_touch_row_becomes_one_outbound_with_ny_day(self):
        log_touch(self.conn, person_id=self.person, company_id=self.company, channel="linkedin_request",
                  sent_at="2026-11-01T03:30:00Z", context=_context(lane="level_role"), idempotency_key="k1")
        (o,) = self.load()
        self.assertEqual(o.sent_on, D(2026, 10, 31))
        self.assertEqual(o.anchor_on, D(2026, 10, 31))
        self.assertEqual((o.person_id, o.company_id, o.channel), (self.person, self.company, "linkedin_request"))
        self.assertEqual((o.lane, o.context_source, o.source), ("level_role", "plan", "touch"))
        self.assertFalse(o.accepted or o.replied or o.progressed)

    def test_applications_are_not_outbound(self):
        log_touch(self.conn, person_id=None, company_id=self.company, channel="application",
                  sent_at="2026-11-02T15:00:00Z", context=_context("own"), idempotency_key="app")
        self.assertEqual(self.load(), [])

    def test_undone_touch_is_not_outbound(self):
        touch = seed_outbound(self.conn, self.person, self.company, "linkedin_request", D(2026, 11, 2))
        self.assertEqual(len(self.load()), 1)
        undo_touch(self.conn, touch, at="2026-11-03T15:00:00Z", revision=1)
        self.assertEqual(self.load(), [])

    def test_context_source_comes_from_the_touch_context_and_defaults_to_own(self):
        other = seed_person(self.conn, self.company, "Bo")
        third = seed_person(self.conn, self.company, "Cy")
        fourth = seed_person(self.conn, self.company, "Di")
        seed_outbound(self.conn, self.person, self.company, "linkedin_request", D(2026, 11, 2),
                      context_source="plan")
        seed_outbound(self.conn, other, self.company, "linkedin_request", D(2026, 11, 3), context_source="own")
        # A legacy email touch (no outreach_touch) and a log-only request (nothing in the plan touched them).
        self.conn.execute(
            "INSERT INTO touch(person_id, origin, first_sent_at, created_at, updated_at) "
            "VALUES (?, 'import', '2026-11-04T15:00:00Z', ?, ?)", (third, utcnow(), utcnow()))
        seed_log_only(self.conn, fourth, "request_sent", D(2026, 11, 5))
        got = {o.person_id: o for o in self.load()}
        self.assertEqual({p: o.context_source for p, o in got.items()},
                         {self.person: "plan", other: "own", third: "own", fourth: "own"})
        self.assertEqual({p: o.source for p, o in got.items()},
                         {self.person: "touch", other: "touch", third: "email_touch", fourth: "linkedin_log"})

    def test_accepted_moves_the_anchor_and_replied_and_progress_flags_follow(self):
        plain = seed_outbound(self.conn, self.person, self.company, "linkedin_request", D(2026, 11, 2),
                              accepted_on=D(2026, 11, 6))
        other = seed_person(self.conn, self.company, "Bo")
        replied = seed_outbound(self.conn, other, self.company, "linkedin_request", D(2026, 11, 2),
                                accepted_on=D(2026, 11, 3), replied_on=D(2026, 11, 4))
        third = seed_person(self.conn, self.company, "Cy")
        referred = seed_outbound(self.conn, third, self.company, "email", D(2026, 11, 2))
        record_outcome(self.conn, referred, kind="referred", at="2026-11-05T15:00:00Z", revision=1)
        a, b, c = self.load()
        self.assertEqual((a.sent_on, a.anchor_on, a.accepted, a.replied, a.progressed),
                         (D(2026, 11, 2), D(2026, 11, 6), True, False, False))
        self.assertEqual((b.anchor_on, b.accepted, b.replied, b.progressed), (D(2026, 11, 3), True, True, False))
        self.assertEqual((c.anchor_on, c.accepted, c.replied, c.progressed), (D(2026, 11, 2), False, False, True))
        self.assertIsNotNone(plain)
        self.assertIsNotNone(replied)

    def test_legacy_email_touch_counts_once(self):
        email_touch = self.conn.execute(
            "INSERT INTO touch(person_id, origin, first_sent_at, created_at, updated_at) "
            "VALUES (?, 'import', '2026-11-04T15:00:00Z', ?, ?)", (self.person, utcnow(), utcnow())).lastrowid
        (o,) = self.load()
        self.assertEqual((o.source, o.channel, o.sent_on, o.person_id), ("email_touch", "email", D(2026, 11, 4),
                                                                         self.person))
        log_touch(self.conn, person_id=self.person, company_id=self.company, channel="email",
                  sent_at="2026-11-04T15:00:00Z", context=_context("own"), email_touch_id=email_touch,
                  idempotency_key="email-1")
        (o,) = self.load()
        self.assertEqual(o.source, "touch")

    def test_legacy_email_touch_reply(self):
        self.conn.execute(
            "INSERT INTO touch(person_id, origin, state, first_sent_at, replied_at, reply_kind, created_at, "
            "updated_at) VALUES (?, 'import', 'replied', '2026-11-04T15:00:00Z', '2026-11-05T15:00:00Z', "
            "'interested', ?, ?)", (self.person, utcnow(), utcnow()))
        (o,) = self.load()
        self.assertTrue(o.replied)

    def test_log_only_request_counts_unless_a_touch_matches(self):
        seed_log_only(self.conn, self.person, "request_sent", D(2026, 11, 2))
        self.assertEqual([o.source for o in self.load()], ["linkedin_log"])
        # The same person, channel and New York day as a touch: the touch is the record, the log is its twin.
        seed_outbound(self.conn, self.person, self.company, "linkedin_request", D(2026, 11, 2))
        self.assertEqual([o.source for o in self.load()], ["touch"])
        # A request on another day still counts on its own.
        seed_log_only(self.conn, self.person, "request_sent", D(2026, 11, 3))
        self.assertEqual([o.source for o in self.load()], ["touch", "linkedin_log"])

    def test_log_only_message_is_a_linkedin_message(self):
        seed_log_only(self.conn, self.person, "message_sent", D(2026, 11, 2))
        (o,) = self.load()
        self.assertEqual(o.channel, "linkedin_message")

    def test_undone_log_entry_does_not_count(self):
        entry = seed_log_only(self.conn, self.person, "request_sent", D(2026, 11, 2))
        seed_log_only(self.conn, self.person, "undo", D(2026, 11, 3), undoes=entry)
        self.assertEqual(self.load(), [])

    def test_log_only_accept_sets_accepted(self):
        seed_log_only(self.conn, self.person, "request_sent", D(2026, 11, 2))
        seed_log_only(self.conn, self.person, "accepted", D(2026, 11, 6))
        (o,) = self.load()
        self.assertEqual((o.accepted, o.anchor_on, o.replied), (True, D(2026, 11, 6), False))
        seed_log_only(self.conn, self.person, "replied", D(2026, 11, 7))
        (o,) = self.load()
        self.assertTrue(o.replied)

    def test_log_accept_before_the_request_does_not_count(self):
        seed_log_only(self.conn, self.person, "accepted", D(2026, 11, 1))
        seed_log_only(self.conn, self.person, "request_sent", D(2026, 11, 2))
        (o,) = self.load()
        self.assertFalse(o.accepted)

    def test_company_follows_a_merge(self):
        dup = seed_scored_company(self.conn, "Acme Inc", 1.0, [])
        person = seed_person(self.conn, dup, "Dee")
        seed_outbound(self.conn, person, dup, "linkedin_request", D(2026, 11, 2))
        seed_log_only(self.conn, self.person, "request_sent", D(2026, 11, 3))
        self.conn.execute("UPDATE company SET status = 'removed', remove_reason = 'duplicate', merged_into = ? "
                          "WHERE company_id = ?", (self.company, dup))
        self.assertEqual({o.company_id for o in self.load()}, {self.company})

    def test_since_and_company_filters(self):
        other_company = seed_scored_company(self.conn, "Beta", 3.0, ["level_role"])
        other = seed_person(self.conn, other_company, "Bo")
        seed_outbound(self.conn, self.person, self.company, "linkedin_request", D(2026, 11, 2))
        seed_outbound(self.conn, self.person, self.company, "linkedin_request", D(2026, 11, 9))
        seed_outbound(self.conn, other, other_company, "linkedin_request", D(2026, 11, 9))
        self.assertEqual(len(self.load()), 3)
        self.assertEqual(len(self.load(since=D(2026, 11, 9))), 2)
        self.assertEqual({o.company_id for o in self.load(company_ids=[other_company])}, {other_company})
        self.assertEqual(len(self.load(since=D(2026, 11, 9), company_ids={self.company})), 1)
        self.assertEqual(self.load(company_ids=[]), [])
        self.assertEqual(TODAY, D(2026, 11, 10))


if __name__ == "__main__":
    unittest.main()
