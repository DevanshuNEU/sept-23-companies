"""Progress and findings (#11 Task 13): the funnel by event date in New York time, base rates, quiet weeks and the
monthly findings with their counts and cautions."""
import json
import math

from tests import apiseed as s
from tests.apitest import NOW, ApiCase

MON = "2026-11-09T15:00:00Z"   # Monday of the week that holds NOW (a Tuesday), New York 10:00
LAST_WEEK = "2026-11-03T15:00:00Z"
LAST_MONTH = "2026-10-12T15:00:00Z"


def context(lane: str = "level_role", role_kind: str = "engineer", role_named: bool = True, signals=None) -> str:
    return json.dumps({"source": "plan", "lane": lane, "roleKind": role_kind, "roleAgeDays": 3, "rankerVersion": "r1",
                       "signals": signals or {}, "roleNamed": role_named})


def touch(conn, company_id: int, person_id: int | None, *, channel: str = "linkedin_request", sent_at: str = MON,
          ctx: str | None = None, **cols) -> int:
    return s._insert(conn, "outreach_touch", {
        "person_id": person_id, "company_id": company_id, "channel": channel, "sent_at": sent_at,
        "context": ctx or context(), "created_at": sent_at, **cols,
    })


def no_nan(text: str):
    """Parse JSON refusing NaN and Infinity (Python's json accepts them unless told not to)."""
    def refuse(token):
        raise AssertionError(f"{token} in the JSON")
    return json.loads(text, parse_constant=refuse)


class ProgressCase(ApiCase):
    def setUp(self):
        super().setUp()
        self.acme = s.company(self.conn, "Acme", "working")
        self.lena = s.person(self.conn, self.acme, "Lena Ortiz", channel="linkedin")

    def requests(self, n: int, sent_at: str = MON, **cols) -> list[int]:
        return [touch(self.conn, self.acme, self.lena, sent_at=sent_at, **cols) for _ in range(n)]

    def progress(self) -> dict:
        res = self.get("/api/progress")
        self.assertEqual(res.status_code, 200, res.text)
        return no_nan(res.text)

    def stages(self, period: dict) -> dict:
        return {x["stage"]: x for x in period["stages"]}


class Progress(ProgressCase):
    def test_a_week_across_the_daylight_saving_change_has_one_week_and_a_25_hour_sunday(self):
        # The clocks went back on Sunday 2026-11-01 at 02:00: that Sunday is 25 hours, in the week of Oct 26.
        self.requests(1, sent_at="2026-11-01T03:59:00Z", accepted_at="2026-11-01T04:30:00Z")   # Oct 31 23:59 EDT
        self.requests(1, sent_at="2026-11-01T04:00:00Z")   # Sunday 00:00 EDT
        self.requests(1, sent_at="2026-11-02T04:59:00Z")   # Sunday 23:59 EST: still the week
        self.requests(1, sent_at="2026-11-02T05:00:00Z")   # Monday 00:00 EST: next week
        self.set_now("2026-11-01T17:00:00Z")
        week = self.progress()["week"]
        self.assertEqual((week["from"], week["to"]), ("2026-10-26", "2026-11-01"))
        self.assertEqual(self.stages(week)["requests"]["count"], 3)
        self.assertEqual(self.stages(week)["accepts"]["count"], 1)
        month = self.progress()["month"]
        self.assertEqual((month["from"], month["to"]), ("2026-11-01", "2026-11-30"))
        self.assertEqual(self.stages(month)["requests"]["count"], 3)  # Nov 1 and Nov 2 requests, not the Oct 31 one
        self.set_now("2026-11-02T10:00:00Z")
        week = self.progress()["week"]
        self.assertEqual((week["from"], week["to"]), ("2026-11-02", "2026-11-08"))
        self.assertEqual(self.stages(week)["requests"]["count"], 1)

    def test_empty_store_progress_has_zero_counts_no_rates_and_not_enough(self):
        view = self.progress()
        self.assertEqual(view["asOf"], NOW)
        self.assertEqual(view["today"], {"planned": 0, "done": 0, "requests": 0, "applications": 0,
                                         "accepted": 0, "replied": 0})
        for name, first, last in (("week", "2026-11-09", "2026-11-15"), ("month", "2026-11-01", "2026-11-30")):
            period = view[name]
            self.assertEqual((period["from"], period["to"]), (first, last))
            self.assertEqual([x["stage"] for x in period["stages"]],
                             ["requests", "accepts", "replies", "referrals", "interviews"])
            self.assertTrue(all(x["count"] == 0 for x in period["stages"]))
            self.assertEqual(self.stages(period)["requests"]["from"], None)
            for stage, of in (("accepts", "requests"), ("replies", "requests"), ("referrals", "replies"),
                              ("interviews", "replies")):
                self.assertEqual(self.stages(period)[stage]["from"], {"stage": of, "count": 0})  # every denominator 0
            self.assertEqual([(e["label"], e["count"]) for e in period["effort"]],
                             [("Requests sent", 0), ("Applications sent", 0), ("Emails sent", 0)])
            self.assertFalse(period["enough"])
            self.assertFalse(period["quiet"])  # nothing was done, so nothing was quiet
        self.assertEqual(view["base"], {"since": "2026-11-10", "requests": 0, "accepts": 0, "replies": 0,
                                        "medianDaysToReply": None, "enough": False, "minRequests": 30})

    def test_week_counts_by_event_date_in_new_york_time(self):
        # Sunday 2026-11-08 23:30 New York is 2026-11-09 04:30Z. The clocks went back on Nov 1, so it is EST.
        sunday_late = "2026-11-09T04:30:00Z"
        self.requests(2, sent_at="2026-11-02T15:00:00Z", accepted_at="2026-11-05T15:00:00Z")
        t = touch(self.conn, self.acme, self.lena, sent_at="2026-11-03T15:00:00Z", replied_at=sunday_late,
                  reply_kind="interested")
        self.set_now("2026-11-09T04:45:00Z")  # Sunday 23:45 New York: still the week of Nov 2
        week = self.progress()["week"]
        self.assertEqual((week["from"], week["to"]), ("2026-11-02", "2026-11-08"))
        self.assertEqual(self.stages(week)["replies"]["count"], 1)
        self.assertEqual(self.stages(week)["accepts"]["count"], 2)
        self.assertEqual(self.stages(week)["requests"]["count"], 3)
        self.set_now("2026-11-09T05:30:00Z")  # Monday 00:30 New York: a new week, and the reply is last week's
        week = self.progress()["week"]
        self.assertEqual((week["from"], week["to"]), ("2026-11-09", "2026-11-15"))
        self.assertEqual(self.stages(week)["replies"]["count"], 0)
        self.assertEqual(self.stages(week)["requests"]["count"], 0)
        self.assertIsNotNone(t)

    def test_funnel_stages_carry_their_denominators_and_month_holds_the_week(self):
        self.requests(4, sent_at=MON, accepted_at="2026-11-10T10:00:00Z")
        self.requests(2, sent_at=LAST_WEEK)
        touch(self.conn, self.acme, self.lena, sent_at=MON, replied_at="2026-11-10T11:00:00Z", reply_kind="interested",
              referred_at="2026-11-10T12:00:00Z")
        touch(self.conn, self.acme, self.lena, sent_at=LAST_MONTH, interview_at="2026-11-10T12:30:00Z")
        view = self.progress()
        week = self.stages(view["week"])
        self.assertEqual([week[k]["count"] for k in ("requests", "accepts", "replies", "referrals", "interviews")],
                         [5, 4, 1, 1, 1])
        self.assertEqual(week["accepts"]["from"], {"stage": "requests", "count": 5})
        self.assertEqual(week["referrals"]["from"], {"stage": "replies", "count": 1})
        month = self.stages(view["month"])
        self.assertEqual(month["requests"]["count"], 7)
        self.assertEqual(view["today"]["accepted"], 4)
        self.assertEqual(view["today"]["replied"], 1)
        self.assertEqual(view["today"]["requests"], 0)  # all sent before today

    def test_enough_flips_at_exactly_30_requests(self):
        self.requests(29)
        view = self.progress()
        self.assertFalse(view["week"]["enough"])
        self.assertFalse(view["month"]["enough"])
        self.assertFalse(view["base"]["enough"])
        self.requests(1)
        view = self.progress()
        self.assertTrue(view["week"]["enough"])
        self.assertTrue(view["month"]["enough"])
        self.assertTrue(view["base"]["enough"])
        self.assertEqual(view["week"]["effort"][0], {"label": "Requests sent", "count": 30})

    def test_enough_counts_requests_only_not_applications_or_emails(self):
        self.requests(20)
        for _ in range(15):
            touch(self.conn, self.acme, None, channel="application")
        self.assertFalse(self.progress()["week"]["enough"])

    def test_first_week_has_counts_and_enough_false(self):
        self.requests(9, accepted_at="2026-11-10T10:00:00Z")
        touch(self.conn, self.acme, self.lena, replied_at="2026-11-10T11:00:00Z", reply_kind="not_now")
        view = self.progress()
        week = self.stages(view["week"])
        self.assertEqual((week["requests"]["count"], week["accepts"]["count"], week["replies"]["count"]), (10, 9, 1))
        self.assertFalse(view["week"]["enough"])
        self.assertFalse(view["month"]["enough"])
        self.assertFalse(view["week"]["quiet"])
        self.assertEqual(view["base"]["requests"], 10)
        self.assertFalse(view["base"]["enough"])

    def test_quiet_week_flags_quiet_and_keeps_effort(self):
        self.requests(31)
        for _ in range(3):
            touch(self.conn, self.acme, None, channel="application")
        touch(self.conn, self.acme, self.lena, channel="email", sent_at=MON)
        view = self.progress()
        week = view["week"]
        self.assertTrue(week["quiet"])
        self.assertTrue(week["enough"])
        self.assertEqual([(e["label"], e["count"]) for e in week["effort"]],
                         [("Requests sent", 31), ("Applications sent", 3), ("Emails sent", 1)])
        self.assertEqual(self.stages(week)["accepts"]["count"], 0)

    def test_a_week_with_an_accept_is_not_quiet(self):
        self.requests(5, accepted_at="2026-11-10T10:00:00Z")
        self.assertFalse(self.progress()["week"]["quiet"])

    def test_said_no_counts_as_a_reply_in_progress_but_is_not_a_win(self):
        touch(self.conn, self.acme, self.lena, replied_at="2026-11-10T11:00:00Z", reply_kind="said_no")
        view = self.progress()
        self.assertEqual(self.stages(view["week"])["replies"]["count"], 1)
        self.assertEqual(view["today"]["replied"], 1)
        wins = self.get("/api/wins").json()
        self.assertEqual(wins["wins"], [])
        self.assertEqual(wins["unseen"], 0)
        self.assertEqual(wins["counts"]["replied"], 0)

    def test_undone_touches_count_nowhere(self):
        self.requests(3, undone_at="2026-11-09T16:00:00Z")
        view = self.progress()
        self.assertEqual(self.stages(view["week"])["requests"]["count"], 0)
        self.assertEqual(view["base"]["requests"], 0)

    def test_base_rates_all_time_and_median_days(self):
        # Requests from months ago still count: the base is all time. Replies after 2, 4 and 9 days: median 4.
        for days_to_reply, sent in ((2, "2026-08-03T15:00:00Z"), (4, "2026-09-07T15:00:00Z"),
                                    (9, "2026-10-05T15:00:00Z")):
            day = int(sent[8:10]) + days_to_reply
            touch(self.conn, self.acme, self.lena, sent_at=sent, accepted_at=sent[:8] + f"{day:02d}T15:00:00Z",
                  replied_at=sent[:8] + f"{day:02d}T15:00:00Z", reply_kind="interested")
        touch(self.conn, self.acme, self.lena, sent_at="2026-08-04T15:00:00Z")
        touch(self.conn, self.acme, None, channel="application", sent_at="2026-08-04T15:00:00Z")  # not a request
        base = self.progress()["base"]
        self.assertEqual(base, {"since": "2026-08-03", "requests": 4, "accepts": 3, "replies": 3,
                                "medianDaysToReply": 4.0, "enough": False, "minRequests": 30})

    def test_median_days_with_an_even_number_of_replies(self):
        for days in (2, 5):
            sent = "2026-10-05T15:00:00Z"
            touch(self.conn, self.acme, self.lena, sent_at=sent, replied_at=f"2026-10-{5 + days:02d}T15:00:00Z",
                  reply_kind="interested")
        self.assertEqual(self.progress()["base"]["medianDaysToReply"], 3.5)

    def test_no_nan_or_infinity_anywhere_and_a_null_ratio_stays_null(self):
        self.requests(5)
        text = self.get("/api/progress").text
        no_nan(text)
        self.assertIsNone(no_nan(text)["base"]["medianDaysToReply"])
        for path in ("/api/findings", "/api/wins"):
            no_nan(self.get(path).text)


class TodayAndPlan(ProgressCase):
    def entry(self, rank: int, person_id: int | None, channel: str | None, *, posting_id: int | None = None,
              state: str = "picked", list_date: str = "2026-11-10") -> int:
        self.conn.execute(
            "INSERT INTO daily_list(list_date, built_at, capacity) VALUES (?, ?, 7) ON CONFLICT DO NOTHING",
            (list_date, "2026-11-10T11:00:00Z"))
        # The plan job's own run for the date, as #15's build writes it: a list with only find-someone rows is still
        # the people plan (a list with no channel anywhere and no plan run is the lean Today's, and counts nothing).
        self.conn.execute(
            "INSERT INTO job_run(job, started_at, finished_at, status, summary) SELECT 'plan', ?, ?, 'ok', "
            "json_object('date', ?) WHERE NOT EXISTS (SELECT 1 FROM job_run WHERE job = 'plan' "
            "AND json_extract(summary, '$.date') = ?)",
            ("2026-11-10T11:00:00Z", "2026-11-10T11:00:05Z", list_date, list_date))
        company = s.company(self.conn, f"Co{rank}", "working")
        s._insert(self.conn, "daily_list_entry", {
            "list_date": list_date, "company_id": company, "list_group": "level_role", "rank": rank, "state": state,
            "posting_id": posting_id, "proposed_at": "2026-11-10T11:00:00Z", "person_id": person_id, "channel": channel,
            "decided_at": None if state == "proposed" else "2026-11-10T11:30:00Z",
        })
        return company

    def test_today_counts_the_plan_and_the_days_touches(self):
        today = "2026-11-10T14:00:00Z"
        done_company = self.entry(1, self.lena, "linkedin_request")
        touch(self.conn, done_company, self.lena, sent_at=today)
        ana_company = self.entry(2, None, None)
        ana = s.person(self.conn, ana_company, "Ana", channel="linkedin")
        job = s.posting(self.conn, ana_company, "Software Engineer")
        self.conn.execute("UPDATE daily_list_entry SET person_id = ?, channel = 'linkedin_request', posting_id = ? "
                          "WHERE company_id = ?", (ana, job, ana_company))
        touch(self.conn, ana_company, ana, sent_at=today, posting_id=None)
        self.entry(3, None, None)  # find someone: nothing done
        self.entry(4, None, None, state="removed")  # removed rows are not planned
        touch(self.conn, ana_company, None, channel="application", sent_at="2026-11-09T14:00:00Z")  # not today
        view = self.progress()["today"]
        # Row 2 has a role, so it needs the application too: only row 1 is done.
        self.assertEqual((view["planned"], view["done"]), (3, 1))
        self.assertEqual((view["requests"], view["applications"]), (2, 0))
        touch(self.conn, ana_company, None, channel="application", sent_at=today, posting_id=job)
        view = self.progress()["today"]
        self.assertEqual((view["planned"], view["done"], view["applications"]), (3, 2, 1))

    def email_row(self, rank: int = 1, state: str = "drafted", **cols) -> tuple[int, int]:
        company = self.entry(rank, None, None)
        pat = s.person(self.conn, company, f"Pat{rank}", channel="email")
        self.conn.execute("UPDATE daily_list_entry SET person_id = ?, channel = 'email' WHERE company_id = ?",
                          (pat, company))
        return pat, s.message(self.conn, s.touch(self.conn, pat), 0, state, **cols)

    def test_an_email_row_is_done_once_its_message_left_drafted(self):
        _, m = self.email_row()
        self.assertEqual(self.progress()["today"]["done"], 0)
        self.conn.execute("UPDATE message SET state = 'approved', approved_at = ? WHERE message_id = ?",
                          ("2026-11-10T13:00:00Z", m))
        self.assertEqual(self.progress()["today"]["done"], 1)

    def test_a_message_approved_before_today_does_not_make_todays_row_done(self):
        _, m = self.email_row()
        # 2026-11-10 05:00Z is New York midnight; 04:59:59Z is still the 9th.
        self.conn.execute("UPDATE message SET state = 'approved', approved_at = ? WHERE message_id = ?",
                          ("2026-11-10T04:59:59Z", m))
        self.assertEqual(self.progress()["today"]["done"], 0)
        self.conn.execute("UPDATE message SET approved_at = ? WHERE message_id = ?", ("2026-11-10T05:00:00Z", m))
        self.assertEqual(self.progress()["today"]["done"], 1)

    def test_a_sent_message_with_no_approval_time_is_judged_by_when_it_was_sent(self):
        _, m = self.email_row(state="sent", sent_at="2026-11-09T14:00:00Z")  # inserted sent: approved_at stays NULL
        self.assertEqual(self.progress()["today"]["done"], 0)
        self.conn.execute("UPDATE message SET sent_at = ? WHERE message_id = ?", ("2026-11-10T14:00:00Z", m))
        self.assertEqual(self.progress()["today"]["done"], 1)

    def test_a_skipped_row_still_counts_in_the_plan_total(self):
        self.entry(1, self.lena, "linkedin_request")
        self.entry(2, None, None, state="backlog")  # a skip sets backlog: still today's row, as PlanView.total counts
        self.entry(3, None, None, state="picked")
        self.assertEqual(self.progress()["today"]["planned"], 3)

    def test_a_list_the_lean_today_stored_is_no_plan(self):
        self.conn.execute("INSERT INTO daily_list(list_date, built_at, capacity) VALUES ('2026-11-10', ?, 7)",
                          ("2026-11-10T11:00:00Z",))
        s._insert(self.conn, "daily_list_entry", {
            "list_date": "2026-11-10", "company_id": s.company(self.conn, "Lean", "candidate"),
            "list_group": "level_role", "rank": 1, "state": "proposed", "proposed_at": "2026-11-10T11:00:00Z"})
        self.assertEqual((self.progress()["today"]["planned"], self.progress()["today"]["done"]), (0, 0))

    def test_a_find_someone_row_is_done_by_a_request_at_a_merged_duplicate_of_its_company(self):
        company = self.entry(1, None, None)
        dup = s.company(self.conn, "Dup", "removed", merged_into=company)
        kim = s.person(self.conn, dup, "Kim", channel="linkedin")
        touch(self.conn, dup, kim, sent_at="2026-11-10T13:00:00Z")
        self.assertEqual(self.progress()["today"]["done"], 1)

    def test_a_find_someone_row_is_done_across_the_family_when_the_entry_sits_on_a_duplicate(self):
        company = self.entry(1, None, None)
        survivor = s.company(self.conn, "Survivor", "working")
        self.conn.execute("UPDATE company SET status = 'removed', remove_reason = 'merged', merged_into = ?, "
                          "paused_at = NULL WHERE company_id = ?", (survivor, company))
        kim = s.person(self.conn, survivor, "Kim", channel="linkedin")
        touch(self.conn, survivor, kim, sent_at="2026-11-10T13:00:00Z")
        view = self.progress()["today"]
        self.assertEqual((view["planned"], view["done"]), (1, 1))

    def test_a_find_someone_row_is_done_when_the_company_has_a_request_since_the_list_date(self):
        company = self.entry(1, None, None)
        self.assertEqual(self.progress()["today"]["done"], 0)
        other = s.person(self.conn, company, "Kim", channel="linkedin")
        touch(self.conn, company, other, sent_at="2026-11-10T13:00:00Z")
        self.assertEqual(self.progress()["today"]["done"], 1)

    def test_yesterdays_list_is_not_todays_plan(self):
        self.entry(1, self.lena, "linkedin_request", list_date="2026-11-09")
        self.assertEqual(self.progress()["today"]["planned"], 0)


class Findings(ProgressCase):
    def seed_group(self, n: int, replies: int, *, lane: str = "level_role", signals=None, sent_at: str = MON,
                   channel: str = "linkedin_request", replied_at: str = "2026-11-10T10:00:00Z") -> None:
        for i in range(n):
            cols = {"replied_at": replied_at, "reply_kind": "interested"} if i < replies else {}
            touch(self.conn, self.acme, self.lena, channel=channel, sent_at=sent_at,
                  ctx=context(lane=lane, signals=signals), **cols)

    def findings(self) -> dict:
        res = self.get("/api/findings")
        self.assertEqual(res.status_code, 200, res.text)
        return no_nan(res.text)

    def test_findings_empty_before_enough_data_with_reported_on_null(self):
        self.seed_group(19, 10)
        self.seed_group(19, 1, lane="intern_junior")
        view = self.findings()
        self.assertEqual(view, {"asOf": NOW, "month": "2026-11-01", "reportedOn": None, "findings": [], "changes": []})

    def test_empty_store_findings_view(self):
        self.assertEqual(self.findings()["findings"], [])

    def test_month_is_the_previous_month_in_the_first_week_else_the_current_one(self):
        for now, month in (("2026-11-07T15:00:00Z", "2026-10-01"), ("2026-11-08T15:00:00Z", "2026-11-01"),
                           ("2026-11-01T15:00:00Z", "2026-10-01"), ("2027-01-03T15:00:00Z", "2026-12-01"),
                           ("2026-11-30T15:00:00Z", "2026-11-01")):
            self.set_now(now)
            self.assertEqual(self.findings()["month"], month, now)

    def test_month_uses_the_new_york_day(self):
        self.set_now("2026-11-08T04:30:00Z")  # Saturday Nov 7 23:30 in New York: still the first week
        self.assertEqual(self.findings()["month"], "2026-10-01")
        self.set_now("2026-11-08T05:30:00Z")  # Sunday Nov 8 00:30 in New York
        self.assertEqual(self.findings()["month"], "2026-11-01")

    def test_finding_has_counts_with_denominators_and_a_caution_when_a_group_is_small(self):
        self.seed_group(25, 10, lane="level_role")
        self.seed_group(25, 2, lane="intern_junior")
        view = self.findings()
        self.assertEqual(view["reportedOn"], "2026-11-10")
        self.assertEqual(len(view["findings"]), 1)
        f = view["findings"][0]
        self.assertEqual([(c["count"], c["of"]) for c in f["counts"]], [(10, 25), (2, 25)])
        self.assertTrue(all(isinstance(c["label"], str) and c["label"] for c in f["counts"]))
        self.assertIn("at least twice as often", f["text"])
        self.assertNotIn("_", f["text"])
        self.assertTrue(f["text"][0].isupper())
        self.assertIsNotNone(f["caution"])
        self.assertIn("25", f["caution"])
        self.assertIsNone(f["changed"])
        self.assertIsInstance(f["id"], int)

    def test_no_caution_when_both_groups_are_at_least_twice_the_minimum(self):
        self.seed_group(40, 16, lane="level_role")
        self.seed_group(40, 4, lane="intern_junior")
        f = self.findings()["findings"][0]
        self.assertEqual([(c["count"], c["of"]) for c in f["counts"]], [(16, 40), (4, 40)])
        self.assertIsNone(f["caution"])

    def test_the_caution_names_the_smaller_group(self):
        self.seed_group(60, 30, lane="level_role")
        self.seed_group(22, 2, lane="intern_junior")
        f = self.findings()["findings"][0]
        self.assertIn("22", f["caution"])
        self.assertNotIn("60", f["caution"])

    def test_at_most_two_findings_strongest_first_with_stable_ids(self):
        self.seed_group(30, 15, lane="level_role", signals={"everify": 1})
        self.seed_group(30, 3, lane="intern_junior", signals={"everify": 0})
        first = self.findings()["findings"]
        second = self.findings()["findings"]
        self.assertEqual(first, second)
        self.assertEqual(len(first), 2)  # the lane contrast and the signal contrast; the two-finding cap holds
        self.assertEqual(len({f["id"] for f in first}), 2)

    def test_finding_changed_reads_the_weights_changed_event(self):
        both, neither = {"everify": 1, "size": 1}, {"everify": 0, "size": 0}
        self.seed_group(45, 20, lane="level_role", signals=both)
        self.seed_group(45, 3, lane="level_role", signals=neither)
        s._insert(self.conn, "event", {  # keyed as #6 writes it; the key is never read
            "at": "2026-11-02T06:00:00Z", "recorded_at": "2026-11-02T06:00:00Z", "entity": "score",
            "kind": "weights_changed", "actor": "system", "idempotency_key": "weights-changed:r1:r2:7",
            "detail": json.dumps({"old_version": "r1", "new_version": "r2", "changed": {"everify": [12, 40]},
                                  "text": "E-Verify weight 12 to 40"})})
        s.event(self.conn, None, "weights_changed", "2026-10-02T06:00:00Z", entity="score", actor="system",
                detail={"old_version": "r0", "new_version": "r1", "changed": {"size": [5, 6]},
                        "text": "Size weight 5 to 6"})
        view = self.findings()
        by_signal = {("everify" if "everify" in f["text"] else "size"): f for f in view["findings"]}
        self.assertEqual(set(by_signal), {"everify", "size"})
        self.assertEqual(by_signal["everify"]["changed"], "E-Verify weight 12 to 40")  # its own signal's event
        self.assertEqual(by_signal["size"]["changed"], "Size weight 5 to 6")
        self.assertEqual(view["changes"], [{"at": "2026-11-02T06:00:00Z", "text": "E-Verify weight 12 to 40"},
                                           {"at": "2026-10-02T06:00:00Z", "text": "Size weight 5 to 6"}])

    def test_a_finding_no_weight_change_touched_has_no_changed_line(self):
        self.seed_group(45, 20, lane="level_role", signals={"everify": 1})
        self.seed_group(45, 3, lane="level_role", signals={"everify": 0})
        s.event(self.conn, None, "weights_changed", "2026-11-02T06:00:00Z", entity="score", actor="system",
                detail={"old_version": "r1", "new_version": "r2", "changed": {"size": [5, 6]}, "text": "Size changed"})
        self.assertIsNone(self.findings()["findings"][0]["changed"])

    def test_a_change_with_no_text_is_still_listed_in_words(self):
        s.event(self.conn, None, "weights_changed", "2026-11-02T06:00:00Z", entity="score", actor="system",
                detail={"old_version": "r1", "new_version": "r2"})
        change = self.findings()["changes"][0]
        self.assertEqual(change["at"], "2026-11-02T06:00:00Z")
        self.assertIn("r1", change["text"])
        self.assertIn("r2", change["text"])

    def test_findings_ignore_undone_touches(self):
        self.seed_group(25, 10, lane="level_role", sent_at=MON)
        self.seed_group(25, 2, lane="intern_junior")
        self.conn.execute("UPDATE outreach_touch SET undone_at = ?, replied_at = NULL, reply_kind = NULL "
                          "WHERE context LIKE '%intern_junior%'", ("2026-11-10T12:00:00Z",))
        self.assertEqual(self.findings()["findings"], [])

    def test_reported_on_is_the_newest_input_date_in_new_york_time(self):
        self.seed_group(25, 10, lane="level_role", replied_at="2026-11-10T03:30:00Z")  # Nov 9 22:30 New York
        self.seed_group(25, 2, lane="intern_junior", replied_at="2026-11-09T20:00:00Z")
        self.assertEqual(self.findings()["reportedOn"], "2026-11-09")
