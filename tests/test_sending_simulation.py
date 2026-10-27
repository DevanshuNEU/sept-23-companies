"""Task 15: ten simulated business days (issue #14's "Done when") and one short scenario per stop rule.

Everything runs the real `run_tick` on a temporary store with FakeGmail and the fake clock; nothing sleeps. The two
weeks are simulated once for the whole module (about 20 seconds) and every test of them reads that one result.
"""
import json
import unittest
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from outreach.sending.config import load_config
from outreach.sending.simulate import TWO_WEEKS, Event, Scenario, format_table, ny, simulate

CFG = load_config()
NY = ZoneInfo("America/New_York")
MON = date(2026, 11, 2)
D = {n: date(2026, 11, 2) + timedelta(days=d) for n, d in
     zip(range(1, 11), (0, 1, 2, 3, 4, 7, 8, 9, 10, 11))}  # business day n of the two weeks
RESULT = None


def setUpModule():
    global RESULT
    RESULT = simulate(TWO_WEEKS)


def tearDownModule():
    RESULT.close()


def ny_date(at: datetime) -> date:
    return at.astimezone(NY).date()


def events(result, kind: str, entity: str | None = None) -> list:
    sql, args = "SELECT * FROM event WHERE kind = ?", [kind]
    if entity:
        sql, args = sql + " AND entity = ?", args + [entity]
    return result.conn.execute(sql + " ORDER BY event_id", args).fetchall()


class TwoWeeksTest(unittest.TestCase):
    r = property(lambda self: RESULT)

    def test_two_weeks_ramp_and_step_downs(self):
        rows = {row.day: row for row in self.r.days}
        self.assertEqual([row.start_limit for row in self.r.days], [20, 30, 40, 50, 40, 50, 60, 70, 60, 70])
        self.assertEqual(len(rows), 10)
        warn = rows[D[4]]  # Thursday 2026-11-05: a policy bounce at 11:00
        self.assertEqual((warn.outcome, warn.effective_limit, warn.sent), ("warning", 40, 40))
        self.assertEqual([e["kind"] for e in events(self.r, "policy_rejection")], ["policy_rejection"])
        block = rows[D[8]]  # Wednesday 2026-11-11: Gmail answers 429 at 13:00
        self.assertEqual(block.outcome, "hard_block")
        self.assertEqual(block.effective_limit, 60)
        hard = events(self.r, "hard_block")
        self.assertEqual(len(hard), 1)
        self.assertIn("sending limit", json.loads(hard[0]["detail"])["reason"])
        self.assertEqual(len(self.r.notices), 1, "exactly one notice, for the hard block")
        self.assertIn("paused", self.r.notices[0][1].lower())
        stopped_from, resumed_at = ny(D[8], "13:00"), ny(D[9], "08:00")
        during = [s for s in self.r.sends if stopped_from <= s.at < resumed_at]
        self.assertEqual(during, [], "nothing goes out between the block and the resume")
        self.assertLess(block.sent, block.start_limit)
        paused = self.r.conn.execute("SELECT paused FROM sender WHERE sender_id = 1").fetchone()[0]
        self.assertEqual(paused, 0, "the scripted resume cleared the pause")
        for n, row in enumerate(self.r.days, start=1):
            if n not in (4, 8):
                self.assertEqual(row.sent, row.start_limit, f"day {n} ({row.day}) is kept full")
                self.assertEqual(row.outcome, "clean", f"day {n}")

    def test_two_weeks_per_company_spacing(self):
        by_day = defaultdict(list)
        for s in self.r.sends:
            by_day[(s.company, ny_date(s.at))].append(s.at)
        self.assertTrue(any(len(v) == 3 for v in by_day.values()), "the cap of 3 is reached somewhere")
        for key, times in by_day.items():
            times.sort()
            self.assertLessEqual(len(times), 3, key)
            for a, b in zip(times, times[1:]):
                self.assertGreaterEqual(b - a, timedelta(minutes=60), key)

    def test_two_weeks_recruiter_timing(self):
        engineer = next(s for s in self.r.sends if s.person == "c0p0")
        recruiter = next(s for s in self.r.sends if s.person == "c0p1")
        self.assertEqual((engineer.role_kind, recruiter.role_kind), ("engineer", "recruiter"))
        approved = {r["m"]: r["approved_at"] for r in self.r.conn.execute(
            "SELECT message_id AS m, approved_at FROM message WHERE seq = 0 AND message_id IN (?, ?)",
            (self.r.people["c0p0"].message_id, self.r.people["c0p1"].message_id))}
        self.assertEqual(len(set(approved.values())), 1, "approved at the same minute")
        self.assertLess(engineer.at, recruiter.at)
        self.assertGreaterEqual(recruiter.at - engineer.at, timedelta(minutes=60))

    def test_two_weeks_window_and_spacing(self):
        for s in self.r.sends:
            local = s.at.astimezone(ZoneInfo(s.zone))
            self.assertLess(local.weekday(), 5, s)
            self.assertTrue(CFG.window_start <= local.time() <= CFG.window_end, f"{s.person} at {local}")
        # The stored gap is cut to whole seconds, so allow a second under the 4 minute floor.
        ordered = sorted(s.at for s in self.r.sends)
        for a, b in zip(ordered, ordered[1:]):
            self.assertGreaterEqual(b - a, timedelta(minutes=4) - timedelta(seconds=1))
        self.assertTrue({"America/Los_Angeles", "Europe/London"} <= {s.zone for s in self.r.sends})

    def test_two_weeks_follow_up_dates(self):
        conn = self.r.conn
        first_day = [s for s in self.r.sends if s.seq == 0 and ny_date(s.at) == D[1]]
        self.assertEqual(len(first_day), 20)
        for s in first_day:
            info = self.r.people[s.person]
            row = conn.execute("SELECT due_on FROM message WHERE touch_id = ? AND seq = 1", (info.touch_id,)).fetchone()
            if row is None:  # stopped by a reply or a bounce: the touch says what it was due
                stopped = conn.execute(
                    "SELECT detail FROM event WHERE entity = 'touch' AND entity_id = ? AND kind = 'skipped_by_sender'",
                    (info.touch_id,)).fetchone()
                self.assertIsNotNone(stopped, s.person)
                due = json.loads(stopped["detail"])["due_on"]
            else:
                due = row["due_on"]
            self.assertEqual(due, "2026-11-11", s.person)
        followups = [s for s in self.r.sends if s.seq == 1]
        self.assertEqual(len(followups), 18, "20 first emails, one replied and one bounced")
        firsts = {s.person: s for s in self.r.sends if s.seq == 0}
        for s in followups:
            self.assertGreaterEqual(ny_date(s.at), D[8], s.person)
            first = firsts[s.person]
            self.assertEqual(s.in_reply_to, first.message_id, s.person)
            self.assertEqual(s.thread_id, first.thread_id, s.person)
        per_day = Counter(ny_date(s.at) for s in self.r.sends)
        for row in self.r.days:
            self.assertEqual(row.sent, per_day[row.day], "a follow-up counts toward its day's sends")
        self.assertTrue(any(ny_date(s.at) == D[8] for s in followups))

    def test_two_weeks_company_hold_on_reply(self):
        conn, info = self.r.conn, self.r.people["c2p0"]
        replied = ny(D[7], "10:00")
        self.assertEqual([s for s in self.r.sends if s.company == 2 and s.at >= replied], [])
        states = {k: conn.execute("SELECT state FROM message WHERE message_id = ?",
                                  (self.r.people[k].message_id,)).fetchone()[0] for k in ("c2p1", "c2p2")}
        self.assertEqual(states, {"c2p1": "approved", "c2p2": "approved"})
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM message WHERE touch_id = ? AND seq = 1",
                                      (info.touch_id,)).fetchone()[0], 0, "the planned follow-up is gone")
        skipped = conn.execute("SELECT detail FROM event WHERE entity = 'touch' AND entity_id = ? "
                               "AND kind = 'skipped_by_sender'", (info.touch_id,)).fetchall()
        self.assertEqual(len(skipped), 1)
        self.assertEqual(json.loads(skipped[0]["detail"])["seq"], 1)
        self.assertEqual(conn.execute("SELECT state FROM touch WHERE touch_id = ?", (info.touch_id,)).fetchone()[0],
                         "replied")
        self.assertEqual(len(events(self.r, "held", "company")), 1)

    def test_two_weeks_no_double_send(self):
        accepted = [m.message_id for m, _, _ in self.r.fake.sent]
        self.assertEqual(len(accepted), len(set(accepted)), "a Message-ID was accepted twice")
        per_message = Counter((m.to, m.in_reply_to is not None) for m, _, _ in self.r.fake.sent)
        self.assertEqual([k for k, n in per_message.items() if n > 1], [])
        hit = events(self.r, "claim_failed", "message")
        self.assertEqual(len(hit), 1, "the one send that hit the 429")
        message_id = hit[0]["entity_id"]
        row = self.r.conn.execute("SELECT state, rfc_message_id FROM message WHERE message_id = ?",
                                  (message_id,)).fetchone()
        self.assertEqual(row["state"], "sent", "it was sent once, after the resume")
        self.assertEqual(sum(1 for _, sent, _ in self.r.fake.sent if sent.rfc_message_id == row["rfc_message_id"]), 1)
        self.assertEqual(self.r.conn.execute("SELECT COUNT(*) FROM message WHERE state = 'sent' AND seq >= 0 AND "
                                             "touch_id IN (SELECT touch_id FROM touch WHERE origin = 'sender')")
                         .fetchone()[0], len(accepted))

    def test_nothing_was_sent_back_to_review_and_nothing_is_stuck(self):
        self.assertEqual(events(self.r, "send_blocked"), [])
        counts = dict(self.r.conn.execute("SELECT state, COUNT(*) FROM message GROUP BY state").fetchall())
        self.assertFalse({"sending", "send_unknown", "failed"} & set(counts), counts)

    def test_the_table_has_ten_day_lines(self):
        lines = [line for line in format_table(self.r) if line.startswith("2026-")]
        self.assertEqual(len(lines), 10)
        self.assertLess(self.r.seconds, 60)


def short(days: int, evts: list[Event], *, companies: int = 30, people: int = 2, zones=("America/New_York",)):
    return Scenario(start=MON, days=days, companies=companies, people_per_company=people, zones=zones,
                    events=tuple(evts))


def morning(n: int, count: int) -> Event:
    return Event(ny(D[n], "07:00"), "approve", {"count": count})


class StopRulesTest(unittest.TestCase):
    def run_scenario(self, scenario: Scenario):
        result = simulate(scenario)
        self.addCleanup(result.close)
        return result

    def kinds(self, result) -> list[str]:
        return [e["kind"] for e in result.conn.execute(
            "SELECT kind FROM event WHERE entity = 'sender' ORDER BY event_id")]

    def paused(self, result):
        return result.conn.execute("SELECT paused, pause_kind FROM sender WHERE sender_id = 1").fetchone()

    def after(self, result, at: datetime) -> list:
        return [s for s in result.sends if s.at >= at]

    def test_stop_bounce_rate_warning(self):
        r = self.run_scenario(short(2, [morning(1, 40), morning(2, 40), Event(ny(D[2], "11:00"), "bounce", {"nth": 0})]))
        self.assertIn("bounce_warning", self.kinds(r))
        self.assertNotIn("hard_bounce_rate", self.kinds(r))
        self.assertEqual(self.paused(r)["paused"], 0)
        day = r.days[1]
        self.assertEqual((day.outcome, day.effective_limit), ("warning", 20))
        self.assertEqual(self.after(r, ny(D[2], "11:04")), [], "the day is already past its lowered limit")

    def test_stop_hard_bounce_rate_pauses(self):
        r = self.run_scenario(short(1, [morning(1, 40), Event(ny(D[1], "10:00"), "bounce", {"nth": 0})]))
        self.assertIn("hard_bounce_rate", self.kinds(r))
        self.assertEqual(tuple(self.paused(r)), (1, "hard_block"))
        self.assertEqual(r.days[0].outcome, "hard_block")
        self.assertEqual(len(r.notices), 1)
        self.assertEqual(self.after(r, ny(D[1], "10:04")), [])

    def test_stop_policy_rejection_warning(self):
        r = self.run_scenario(short(3, [morning(1, 40), morning(2, 50), morning(3, 60),
                                        Event(ny(D[3], "11:00"), "bounce", {"nth": 0, "dsn": "policy"})]))
        kinds = self.kinds(r)
        self.assertIn("policy_rejection", kinds)
        self.assertNotIn("bounce_warning", kinds)
        self.assertNotIn("hard_bounce_rate", kinds)
        self.assertEqual(self.paused(r)["paused"], 0)
        self.assertEqual((r.days[2].outcome, r.days[2].start_limit, r.days[2].effective_limit),
                         ("warning", 40, 30))

    def test_stop_gmail_429_pauses(self):
        r = self.run_scenario(short(1, [morning(1, 40), Event(ny(D[1], "09:00"), "send_error",
                                                              {"kind": "rate_limit", "status": 429})]))
        self.assertIn("gmail_limit", self.kinds(r))
        self.assertEqual(tuple(self.paused(r)), (1, "hard_block"))
        self.assertEqual(r.days[0].outcome, "hard_block")
        self.assertEqual(len(r.notices), 1)
        self.assertEqual(self.after(r, ny(D[1], "09:00")), [])

    def test_stop_account_blocked_pauses(self):
        r = self.run_scenario(short(1, [morning(1, 40), Event(ny(D[1], "09:00"), "send_error",
                                                              {"kind": "account_blocked", "status": 403})]))
        self.assertIn("gmail_policy", self.kinds(r))
        self.assertEqual(tuple(self.paused(r)), (1, "hard_block"))
        self.assertEqual(self.after(r, ny(D[1], "09:00")), [])

    def test_stop_one_stop_reply_warns_and_two_pause(self):
        one = [morning(1, 40), Event(ny(D[1], "10:00"), "reply", {"nth": 0}),
               Event(ny(D[1], "10:10"), "tag", {"nth": 0})]
        r = self.run_scenario(short(1, one))
        self.assertIn("stop_reply", self.kinds(r))
        self.assertNotIn("stop_replies_hard", self.kinds(r))
        self.assertEqual(self.paused(r)["paused"], 0)
        self.assertEqual(r.days[0].outcome, "warning")
        two = one + [Event(ny(D[1], "11:00"), "reply", {"nth": 1}), Event(ny(D[1], "11:10"), "tag", {"nth": 1})]
        r = self.run_scenario(short(1, two))
        self.assertIn("stop_replies_hard", self.kinds(r))
        self.assertEqual(tuple(self.paused(r)), (1, "hard_block"))
        self.assertEqual(self.after(r, ny(D[1], "11:14")), [])

    def test_stop_said_no_list_never_sends(self):
        r = self.run_scenario(short(1, [Event(ny(D[1], "07:00"), "no_go", {"person": "c0p0"}),
                                        Event(ny(D[1], "07:00"), "approve", {"people": ["c0p0", "c1p0"]})]))
        self.assertEqual([s.person for s in r.sends], ["c1p0"])
        state = r.conn.execute("SELECT state FROM message WHERE message_id = ?",
                               (r.people["c0p0"].message_id,)).fetchone()[0]
        self.assertEqual(state, "skipped")

    def test_stop_signed_out_sends_nothing_and_is_not_a_hard_block(self):
        r = self.run_scenario(short(2, [Event(ny(D[1], "06:00"), "sign_out"), morning(1, 40),
                                        Event(ny(D[2], "07:00"), "sign_in"), morning(2, 40)]))
        self.assertEqual(r.days[0].sent, 0)
        self.assertGreater(r.days[1].sent, 0, "signing in again resumes sending")
        self.assertNotIn("hard_block", self.kinds(r))
        self.assertEqual(self.paused(r)["paused"], 0)
        self.assertEqual(len(events(r, "signed_out", "sender")), 1)
        self.assertEqual(len(r.notices), 1)
        self.assertEqual(r.days[0].outcome, None)

    def test_simulation_is_deterministic(self):
        scenario = short(2, [morning(1, 30), morning(2, 30), Event(ny(D[2], "10:00"), "reply", {"nth": 2})])
        a, b = self.run_scenario(scenario), self.run_scenario(scenario)

        def shape(result):
            return [(s.at, s.person, s.seq) for s in result.sends]

        self.assertGreater(len(a.sends), 20)
        self.assertEqual(shape(a), shape(b))
        self.assertEqual(a.days, b.days)

    def test_the_simulation_never_touches_the_default_store_or_the_real_gmail_folder(self):
        import os
        from outreach.store import db_path
        before = os.path.exists(db_path())
        r = self.run_scenario(short(1, [morning(1, 5)]))
        self.assertNotEqual(str(r.folder), os.path.dirname(db_path()))
        self.assertEqual(os.path.exists(db_path()), before)


if __name__ == "__main__":
    unittest.main()
