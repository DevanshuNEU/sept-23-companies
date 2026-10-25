import unittest
from datetime import date

from outreach.plan.capacity import (current_send_limit, email_capacity, lane_quotas, linkedin_capacity,
                                    plan_capacity)
from outreach.plan.config import default_plan_config
from outreach.ranking.common import canonical_ids
from outreach.store.timefmt import utcnow
from outreach.store.writes import log_touch
from tests.plan_seed import (TODAY, seed_log_only, seed_outbound, seed_person, seed_scored_company,
                             seed_send_day)
from tests.storetest import fresh_store

D = date
CFG = default_plan_config()


class LinkedinCapacityTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.company = seed_scored_company(self.conn, "Acme", 5.0, ["level_role"])
        self.person = seed_person(self.conn, self.company, "Ada")

    def requests(self, n, on, *, source="plan", lane=None):
        for _ in range(n):
            seed_outbound(self.conn, self.person, self.company, "linkedin_request", on, context_source=source,
                          lane=lane)

    def cap(self, today):
        return linkedin_capacity(self.conn, canonical_ids(self.conn), today, CFG)

    def test_fresh_month_gives_about_seven(self):
        self.assertEqual(self.cap(D(2026, 11, 2)).today, 7)
        self.requests(40, D(2026, 11, 3))
        c = self.cap(D(2026, 11, 10))
        self.assertEqual((c.today, c.remaining, c.days_left, c.used, c.hard_used), (7, 110, 15, 40, 40))

    def test_last_weekday_is_capped_at_daily_max(self):
        self.requests(140, D(2026, 10, 5))
        c = self.cap(D(2026, 10, 30))
        self.assertEqual((c.remaining, c.days_left, c.today), (10, 1, 10))

    def test_budget_spent_or_overspent_is_zero_not_negative(self):
        self.requests(150, D(2026, 11, 3))
        c = self.cap(TODAY)
        self.assertEqual((c.today, c.remaining), (0, 0))
        self.requests(60, D(2026, 11, 4), source="own")  # 150 plan plus 60 own is over the hard total too
        c = self.cap(TODAY)
        self.assertEqual((c.today, c.remaining), (0, 0))

    def test_plan_and_own_overspend_is_zero(self):
        self.requests(100, D(2026, 11, 3))
        self.requests(110, D(2026, 11, 4), source="own")
        c = self.cap(TODAY)
        self.assertEqual((c.today, c.remaining, c.used, c.hard_used), (0, 0, 100, 210))

    def test_his_own_requests_count_only_against_the_hard_total(self):
        self.requests(60, D(2026, 11, 3), source="own")
        c = self.cap(TODAY)
        self.assertEqual((c.remaining, c.used, c.hard_used), (140, 0, 60))
        self.requests(130, D(2026, 11, 4), source="own")
        self.assertEqual(self.cap(TODAY).remaining, 10)

    def test_the_quota_binds_when_it_is_lower(self):
        self.requests(150, D(2026, 11, 3))
        self.assertEqual(self.cap(TODAY).remaining, 0)

    def test_reason_used_counts_plan_reason_lane_only(self):
        self.requests(3, D(2026, 11, 3), lane="reason_no_role")
        self.requests(2, D(2026, 11, 3), lane="level_role")
        self.requests(4, D(2026, 11, 3), lane="reason_no_role", source="own")
        self.assertEqual(self.cap(TODAY).reason_used, 3)

    def test_month_boundary_is_new_york(self):
        log_touch(self.conn, person_id=self.person, company_id=self.company, channel="linkedin_request",
                  sent_at="2026-11-01T03:30:00Z",
                  context={"source": "plan", "lane": None, "roleKind": None, "roleAgeDays": None,
                           "rankerVersion": None, "signals": {}, "roleNamed": False},
                  idempotency_key="boundary")
        self.assertEqual(self.cap(D(2026, 10, 15)).hard_used, 1)
        self.assertEqual(self.cap(D(2026, 11, 2)).hard_used, 0)

    def test_each_touch_counts_once(self):
        seed_outbound(self.conn, self.person, self.company, "linkedin_request", D(2026, 11, 3))
        seed_log_only(self.conn, self.person, "request_sent", D(2026, 11, 3))
        self.assertEqual(self.cap(TODAY).hard_used, 1)
        other = seed_person(self.conn, self.company, "Bo")
        seed_log_only(self.conn, other, "request_sent", D(2026, 11, 4))
        c = self.cap(TODAY)
        self.assertEqual((c.hard_used, c.used), (2, 1))  # a log-only import is his own, not the plan's

    def test_messages_are_not_requests(self):
        seed_outbound(self.conn, self.person, self.company, "linkedin_message", D(2026, 11, 3))
        seed_outbound(self.conn, self.person, self.company, "email", D(2026, 11, 3))
        self.assertEqual(self.cap(TODAY).hard_used, 0)

    def test_sunday_force_build_has_a_positive_days_left(self):
        c = self.cap(D(2026, 11, 8))
        self.assertEqual(c.days_left, 16)
        self.assertGreaterEqual(c.today, 1)
        # Review fix H: Saturday 31 October has no weekday left in the month, so a build then gives no row (Friday
        # took the month's last full day). It is not rounded up to one.
        sat = self.cap(D(2026, 10, 31))
        self.assertEqual((sat.days_left, sat.today, sat.remaining), (0, 0, 150))

    def test_one_request_left_with_many_weekdays_left_rounds_down_to_none(self):
        self.requests(149, D(2026, 11, 3))
        c = self.cap(D(2026, 11, 10))
        self.assertEqual((c.remaining, c.days_left, c.today), (1, 15, 0))
        # it still finishes: half way through the remaining days the half rounds up, and the last day takes the rest
        self.assertEqual(self.cap(D(2026, 11, 27)).today, 1)  # the last Friday: 2 weekdays left (Fri, Mon 30)
        self.assertEqual(self.cap(D(2026, 11, 30)).today, 1)

    def test_a_weekend_inside_a_month_with_weekdays_left_still_has_capacity(self):
        sunday = self.cap(D(2026, 11, 22))
        self.assertEqual((sunday.days_left, sunday.today), (6, 10))  # 150 over 6 weekdays, capped at daily_max


class EmailCapacityTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.company = seed_scored_company(self.conn, "Acme", 5.0, ["level_role"])
        self.n = 0

    def message(self, state, *, seq=0, due_on=None, sent_at=None):
        """A message in `state`. The store makes a message born planned, drafted or sent and moves it by UPDATE, and
        a follow-up needs a touch whose first email went out, so this does the same."""
        self.n += 1
        person = seed_person(self.conn, self.company, f"P{self.n}")
        now = utcnow()
        first_sent = "2026-11-02T15:00:00Z" if seq >= 1 else None
        touch = self.conn.execute("INSERT INTO touch(person_id, origin, first_sent_at, created_at, updated_at) "
                                  "VALUES (?, 'import', ?, ?, ?)", (person, first_sent, now, now)).lastrowid
        born = "sent" if state == "sent" else ("planned" if seq >= 1 else "drafted")
        self.conn.execute(
            "INSERT INTO message(touch_id, seq, state, subject, body, due_on, sent_at, rfc_message_id, created_at, "
            "updated_at) VALUES (?, ?, ?, 's', 'b', ?, ?, ?, ?, ?)",
            (touch, seq, born, due_on, sent_at, f"<m{self.n}@x>" if sent_at else None, now, now))
        steps = {"approved": ("approved",), "sending": ("approved", "sending"),
                 "send_unknown": ("approved", "sending", "send_unknown"),
                 "failed": ("approved", "sending", "send_unknown", "failed")}.get(state, ())
        for step in steps:
            held = step in ("sending", "send_unknown")
            self.conn.execute(
                "UPDATE message SET state = ?, approved_at = ?, pending_rfc_id = ?, claimed_at = ? "
                "WHERE touch_id = ?",
                (step, now, f"<p{self.n}@x>" if held else None, now if held else None, touch))

    def test_email_slots_from_the_ramp_row(self):
        seed_send_day(self.conn, TODAY, 30)
        for _ in range(4):
            self.message("approved")
        for _ in range(3):
            self.message("drafted", seq=1, due_on=TODAY.isoformat())
        for _ in range(2):
            self.message("sent", sent_at="2026-11-10T15:00:00Z")
        e = email_capacity(self.conn, TODAY, CFG)
        self.assertEqual((e.send_limit, e.committed, e.paused, e.slots), (30, 9, False, 15))
        self.conn.execute("UPDATE send_day SET send_limit = 20")
        self.assertEqual(email_capacity(self.conn, TODAY, CFG).slots, 11)

    def test_what_is_not_committed(self):
        self.message("drafted", seq=1, due_on="2026-11-11")  # due tomorrow
        self.message("planned", seq=2, due_on="2026-11-09")  # due and planned: counts
        self.message("drafted", seq=0)  # a first email not yet approved
        self.message("sent", sent_at="2026-11-10T04:59:00Z")  # 23:59 yesterday in New York
        self.message("sent", sent_at="2026-11-11T04:59:00Z")  # 23:59 today
        self.message("send_unknown")
        self.message("failed")  # a failed message holds no slot
        e = email_capacity(self.conn, TODAY, CFG)
        self.assertEqual(e.committed, 3)

    def test_email_limit_falls_back_to_latest_row_then_start_limit(self):
        self.assertEqual(current_send_limit(self.conn, TODAY, CFG), 20)
        seed_send_day(self.conn, D(2026, 11, 6), 25)
        seed_send_day(self.conn, D(2026, 11, 11), 40)  # tomorrow: not today's
        self.assertEqual(current_send_limit(self.conn, TODAY, CFG), 25)
        seed_send_day(self.conn, TODAY, 12)
        self.assertEqual(current_send_limit(self.conn, TODAY, CFG), 12)
        self.assertEqual(email_capacity(self.conn, TODAY, CFG, limit_fn=lambda c, d, g: 99).send_limit, 99)

    def test_paused_sender_gives_zero_email_slots(self):
        now = utcnow()
        self.conn.execute("UPDATE sender SET paused = 1, pause_kind = 'by_you', paused_at = ?", (now,))
        e = email_capacity(self.conn, TODAY, CFG)
        self.assertEqual((e.paused, e.slots), (True, 0))

    def test_over_committed_is_zero_not_negative(self):
        seed_send_day(self.conn, TODAY, 2)
        for _ in range(5):
            self.message("approved")
        self.assertEqual(email_capacity(self.conn, TODAY, CFG).slots, 0)

    def test_plan_capacity_is_linkedin_today_plus_email_slots(self):
        conn = self.conn
        canon = canonical_ids(conn)
        seed_send_day(conn, D(2026, 11, 2), 10)
        self.assertEqual(plan_capacity(conn, canon, D(2026, 11, 2), CFG), 7 + 10)
        for day in (D(2026, 11, 10), D(2026, 11, 11)):
            self.assertEqual(plan_capacity(conn, canon, day, CFG),
                             linkedin_capacity(conn, canon, day, CFG).today + email_capacity(conn, day, CFG).slots)
        self.assertEqual(plan_capacity(conn, canon, D(2026, 11, 11), CFG), 10 + 10)


class LaneQuotasTest(unittest.TestCase):
    def test_lane_quotas_table(self):
        for args, want in (((7, 0, 0), (6, 1)), ((7, 7, 1), (5, 2)), ((7, 70, 14), (6, 1)), ((7, 70, 20), (7, 0)),
                           ((2, 0, 0), (2, 0)), ((3, 0, 0), (2, 1)), ((0, 5, 1), (0, 0))):
            self.assertEqual(lane_quotas(*args, CFG), want, args)


if __name__ == "__main__":
    unittest.main()
