import unittest
from datetime import date

from outreach.plan.capacity import (companies_today, current_send_limit, email_capacity, lane_quotas,
                                    linkedin_capacity, plan_capacity)
from outreach.plan.config import default_plan_config
from outreach.ranking.common import canonical_ids
from outreach.store.timefmt import utcnow
from outreach.store.writes import log_touch
from tests.plan_seed import (PEOPLE_ON, TODAY, seed_log_only, seed_outbound, seed_person, seed_scored_company,
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

    def test_a_fresh_week_gives_the_queue_of_eight(self):
        self.assertEqual(self.cap(D(2026, 11, 2)).today, 8)
        self.requests(40, D(2026, 11, 9))
        c = self.cap(D(2026, 11, 10))
        self.assertEqual((c.today, c.remaining, c.used, c.hard_used), (8, 110, 40, 40))

    def test_plenty_left_in_the_week_never_raises_the_day_above_the_queue(self):
        # #9 company first: about 8 companies a day is the queue, not what the week could hold (150 / 4 is 37).
        for day in (D(2026, 11, 6), D(2026, 11, 8), D(2026, 11, 30), D(2026, 10, 31)):
            self.assertEqual(self.cap(day).today, CFG.get("queue", "companies_per_day"), day)

    def test_a_heavy_week_cuts_the_queue_to_whole_waves(self):
        # companies_today = min(companies_per_day, floor(remaining / requests_per_company))
        for used, want in ((120, 7), (122, 7), (124, 6), (140, 2), (146, 1), (147, 0), (150, 0)):
            conn = fresh_store()
            company = seed_scored_company(conn, "Acme", 5.0, ["level_role"])
            person = seed_person(conn, company, "Ada")
            for _ in range(used):
                seed_outbound(conn, person, company, "linkedin_request", D(2026, 11, 9))
            with self.subTest(used=used):
                c = linkedin_capacity(conn, canonical_ids(conn), TODAY, CFG)
                self.assertEqual((c.remaining, c.today), (150 - used, want))

    def test_companies_today_formula(self):
        for remaining, want in ((150, 8), (32, 8), (31, 7), (8, 2), (7, 1), (4, 1), (3, 0), (0, 0), (-5, 0)):
            self.assertEqual(companies_today(remaining, CFG), want, remaining)

    def test_the_window_is_the_last_seven_days(self):
        self.requests(5, D(2026, 11, 3))  # Tuesday a week ago: out of the window
        self.requests(4, D(2026, 11, 4))  # six days before today: in it
        self.requests(3, D(2026, 11, 10))  # today
        c = self.cap(TODAY)
        self.assertEqual((c.used, c.hard_used, c.remaining), (7, 7, 143))
        self.assertEqual(self.cap(D(2026, 11, 9)).used, 9)  # Monday's window still holds the Tuesday before

    def test_requests_older_than_the_window_free_the_quota(self):
        self.requests(150, D(2026, 11, 3))
        self.assertEqual((self.cap(D(2026, 11, 9)).today, self.cap(D(2026, 11, 9)).remaining), (0, 0))
        c = self.cap(TODAY)
        self.assertEqual((c.today, c.remaining, c.used), (8, 150, 0))

    def test_budget_spent_or_overspent_is_zero_not_negative(self):
        self.requests(150, D(2026, 11, 9))
        c = self.cap(TODAY)
        self.assertEqual((c.today, c.remaining), (0, 0))
        self.requests(60, D(2026, 11, 8), source="own")  # 150 plan plus 60 own is over the hard total too
        c = self.cap(TODAY)
        self.assertEqual((c.today, c.remaining), (0, 0))

    def test_plan_and_own_overspend_is_zero(self):
        self.requests(100, D(2026, 11, 9))
        self.requests(110, D(2026, 11, 8), source="own")
        c = self.cap(TODAY)
        self.assertEqual((c.today, c.remaining, c.used, c.hard_used), (0, 0, 100, 210))

    def test_his_own_requests_count_only_against_the_hard_total(self):
        self.requests(60, D(2026, 11, 9), source="own")
        c = self.cap(TODAY)
        self.assertEqual((c.remaining, c.used, c.hard_used), (140, 0, 60))
        self.requests(130, D(2026, 11, 8), source="own")
        self.assertEqual(self.cap(TODAY).remaining, 10)

    def test_the_quota_binds_when_it_is_lower(self):
        self.requests(150, D(2026, 11, 9))  # the plan's share is spent while the hard total has 50 left
        self.assertEqual(self.cap(TODAY).remaining, 0)

    def test_the_hard_total_binds_when_it_is_lower(self):
        self.requests(40, D(2026, 11, 9))
        self.requests(156, D(2026, 11, 8), source="own")  # 196 in all: the hard total leaves 4, the share 110
        c = self.cap(TODAY)
        self.assertEqual((c.remaining, c.today, c.budget, c.hard_limit), (4, 1, 150, 200))  # one wave of 4 left
        self.requests(1, D(2026, 11, 8), source="own")  # 3 left: not a whole wave
        self.assertEqual((self.cap(TODAY).remaining, self.cap(TODAY).today), (3, 0))

    def test_reason_used_counts_plan_reason_lane_only(self):
        self.requests(3, D(2026, 11, 9), lane="reason_no_role")
        self.requests(2, D(2026, 11, 9), lane="level_role")
        self.requests(4, D(2026, 11, 9), lane="reason_no_role", source="own")
        self.requests(5, D(2026, 11, 3), lane="reason_no_role")  # out of the window
        self.assertEqual(self.cap(TODAY).reason_used, 3)

    def test_window_boundary_is_new_york(self):
        def at(ts, key):
            log_touch(self.conn, person_id=self.person, company_id=self.company, channel="linkedin_request",
                      sent_at=ts,
                      context={"source": "plan", "lane": None, "roleKind": None, "roleAgeDays": None,
                               "rankerVersion": None, "signals": {}, "roleNamed": False},
                      idempotency_key=key)
        at("2026-11-04T04:30:00Z", "late")  # 23:30 on Tuesday 3 November in New York: out of Tuesday 10's window
        self.assertEqual(self.cap(TODAY).hard_used, 0)
        self.assertEqual(self.cap(D(2026, 11, 9)).hard_used, 1)
        at("2026-11-04T05:30:00Z", "early")  # 00:30 on Wednesday 4 November: in it
        self.assertEqual(self.cap(TODAY).hard_used, 1)

    def test_each_touch_counts_once(self):
        seed_outbound(self.conn, self.person, self.company, "linkedin_request", D(2026, 11, 9))
        seed_log_only(self.conn, self.person, "request_sent", D(2026, 11, 9))
        self.assertEqual(self.cap(TODAY).hard_used, 1)
        other = seed_person(self.conn, self.company, "Bo")
        seed_log_only(self.conn, other, "request_sent", D(2026, 11, 8))
        c = self.cap(TODAY)
        self.assertEqual((c.hard_used, c.used), (2, 1))  # a log-only import is his own, not the plan's

    def test_messages_are_not_requests(self):
        seed_outbound(self.conn, self.person, self.company, "linkedin_message", D(2026, 11, 9))
        seed_outbound(self.conn, self.person, self.company, "email", D(2026, 11, 9))
        self.assertEqual(self.cap(TODAY).hard_used, 0)

    def test_a_weekend_force_build_still_has_capacity(self):
        self.assertEqual(self.cap(D(2026, 11, 8)).today, 8)


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
        on = PEOPLE_ON  # with people on, the plan may write email rows
        self.assertEqual(plan_capacity(conn, canon, D(2026, 11, 2), on), 8 + 10)
        for day in (D(2026, 11, 10), D(2026, 11, 11)):
            self.assertEqual(plan_capacity(conn, canon, day, on),
                             linkedin_capacity(conn, canon, day, on).today + email_capacity(conn, day, on).slots)
        self.assertEqual(plan_capacity(conn, canon, D(2026, 11, 11), on), 8 + 10)
        # shipped (company first): no email rows, so no email slots in the plan's total; the sender's own are kept
        self.assertEqual(plan_capacity(conn, canon, D(2026, 11, 11), CFG), 8)
        self.assertEqual(email_capacity(conn, D(2026, 11, 11), CFG).slots, 10)


class LaneQuotasTest(unittest.TestCase):
    def test_lane_quotas_table(self):
        for args, want in (((7, 0, 0), (6, 1)), ((7, 7, 1), (5, 2)), ((7, 70, 14), (6, 1)), ((7, 70, 20), (7, 0)),
                           ((2, 0, 0), (2, 0)), ((3, 0, 0), (2, 1)), ((0, 5, 1), (0, 0))):
            self.assertEqual(lane_quotas(*args, CFG), want, args)


if __name__ == "__main__":
    unittest.main()
