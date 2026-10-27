"""The LinkedIn list and logging a step by hand (#11 Task 16).

Nothing here talks to LinkedIn: the API only records what Devanshu did there. Every write is one transaction that
writes the `linkedin_log` row and the `outreach_touch` together.
"""
import json
import sqlite3

from outreach.outcomes.context import build_context
from outreach.store import log_touch
from tests import apiseed as s
from tests.apitest import NOW, ApiCase

T1 = "2026-11-10T15:20:00Z"
T2 = "2026-11-10T15:30:00Z"
T3 = "2026-11-10T15:40:00Z"


class LinkedInCase(ApiCase):
    def setUp(self):
        super().setUp()
        self.acme = s.company(self.conn, "Acme", "working")
        self.lena = self.person("Lena Ortiz")

    def person(self, name: str, company: int | None = None, **cols) -> int:
        return s.person(self.conn, company or self.acme, name, channel="linkedin",
                        linkedin_url=f"https://www.linkedin.com/in/{name.lower().replace(' ', '-')}", **cols)

    def log(self, person_id: int, step: str, reply_kind: str | None = None, key: str | None = None):
        body = {"step": step}
        if reply_kind is not None:
            body["replyKind"] = reply_kind
        return self.post(f"/api/linkedin/{person_id}/log", body, key=key)

    def undo(self, person_id: int, step: str):
        return self.post(f"/api/linkedin/{person_id}/undo", {"step": step})

    def ok(self, res):
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def refused(self, res, code: str = "not_allowed", status: int = 409) -> dict:
        self.assertEqual((res.status_code, res.json()["code"]), (status, code), res.text)
        return res.json()

    def view(self) -> dict:
        return self.ok(self.get("/api/linkedin"))

    def people(self) -> dict[str, dict]:
        return {p["person"]["name"]: p for g in self.view()["groups"] for p in g["people"]}

    def touches(self, person_id: int | None = None) -> list[sqlite3.Row]:
        sql, args = "SELECT * FROM outreach_touch", ()
        if person_id is not None:
            sql, args = sql + " WHERE person_id = ?", (person_id,)
        return self.conn.execute(sql + " ORDER BY touch_id", args).fetchall()

    def log_rows(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM linkedin_log ORDER BY entry_id").fetchall()

    def counts(self) -> tuple[int, int, int]:
        return (len(self.touches()), len(self.log_rows()),
                self.conn.execute("SELECT count(*) FROM api_request").fetchone()[0])

    def plan_touch(self, person_id: int, company_id: int | None = None, with_log: bool = True) -> int:
        """A request the way the plan logs it (Task 11): source 'plan', one touch and one log row."""
        context = build_context(source="plan", lane="level_role", role_kind="engineer", role_age_days=3,
                                ranker_version="r1", signals={}, role_named=True)
        touch_id = log_touch(self.conn, person_id=person_id, company_id=company_id or self.acme,
                             channel="linkedin_request", sent_at=NOW, context=context,
                             idempotency_key=f"plan:{person_id}:request")
        if with_log:
            s.linkedin(self.conn, person_id, "request_sent", NOW)
        return touch_id

    @staticmethod
    def at(n: int) -> str:
        """When `walk` logs its n-th step."""
        return f"2026-11-10T15:{20 + 5 * n}:00Z"

    def message_state(self, message_id: int) -> str:
        return self.conn.execute("SELECT state FROM message WHERE message_id = ?", (message_id,)).fetchone()[0]

    def walk(self, person_id: int, *steps: str) -> None:
        """Log each step in turn, a few minutes apart (a reply is `interested`)."""
        for n, step in enumerate(steps):
            self.set_now(self.at(n))
            self.ok(self.log(person_id, step, "interested" if step == "replied" else None))


class LogRequest(LinkedInCase):
    def test_log_request_writes_one_log_row_and_one_touch(self):
        body = self.ok(self.log(self.lena, "request_sent"))
        self.assertEqual(body["tab"], "waiting")
        self.assertEqual(body["steps"], [{"step": "request_sent", "at": NOW}])
        self.assertIsNone(body["replyKind"])
        self.assertEqual(body["person"]["name"], "Lena Ortiz")
        logs = self.log_rows()
        self.assertEqual([(r["person_id"], r["kind"], r["at"], r["reply_kind"], r["undoes"]) for r in logs],
                         [(self.lena, "request_sent", NOW, None, None)])
        touches = self.touches()
        self.assertEqual(len(touches), 1)
        t = touches[0]
        self.assertEqual((t["person_id"], t["company_id"], t["channel"], t["sent_at"], t["undone_at"]),
                         (self.lena, self.acme, "linkedin_request", NOW, None))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM event WHERE kind = 'sent'").fetchone()[0], 1)

    def test_the_touch_is_logged_from_the_list_so_its_source_is_own(self):
        self.ok(self.log(self.lena, "request_sent"))
        context = json.loads(self.touches()[0]["context"])
        self.assertEqual(set(context), {"source", "lane", "roleKind", "roleAgeDays", "rankerVersion", "signals",
                                        "roleNamed"})
        self.assertEqual((context["source"], context["roleKind"], context["lane"]), ("own", "engineer", "own"))
        self.assertFalse(context["roleNamed"])

    def test_the_touch_takes_the_lane_and_signals_of_the_newest_score(self):
        signals = {"values": {"small": {"value": 2.0, "weight": 1.0, "points": 2.0}}, "reasons": [], "hide": None,
                   "flags": [], "best_posting_id": None}
        self.conn.execute(
            "INSERT INTO company_score(company_id, ranker_version, score, signals, lanes, scored_at) "
            "VALUES (?, 'r7', 1.0, ?, '[\"intern_junior\"]', '2026-11-09T06:00:00Z')", (self.acme, json.dumps(signals)))
        self.ok(self.log(self.lena, "request_sent"))
        context = json.loads(self.touches()[0]["context"])
        self.assertEqual((context["source"], context["lane"], context["rankerVersion"], context["signals"]),
                         ("own", "intern_junior", "r7", {"small": 2.0}))

    def test_the_touch_names_the_role_the_list_shows(self):
        posting = s.posting(self.conn, self.acme, "Software Engineer, New Grad", first_seen_at="2026-11-05T12:00:00Z")
        s.decision(self.conn, posting)
        group = self.view_after_log_group()
        self.assertEqual(group["role"]["postingId"], posting)
        t = self.touches()[0]
        self.assertEqual(t["posting_id"], posting)
        context = json.loads(t["context"])
        self.assertTrue(context["roleNamed"])
        self.assertEqual(context["roleAgeDays"], 5)

    def view_after_log_group(self) -> dict:
        self.ok(self.log(self.lena, "request_sent"))
        return self.view()["groups"][0]

    def test_a_person_on_a_merged_company_is_logged_against_the_survivor(self):
        dup = s.company(self.conn, "Acme Inc", "removed", merged_into=self.acme)
        mia = self.person("Mia Fox", dup)
        self.ok(self.log(mia, "request_sent"))
        self.assertEqual(self.touches()[0]["company_id"], self.acme)

    def test_same_request_id_replays_and_writes_once(self):
        first = self.log(self.lena, "request_sent", key="press-one-1234")
        again = self.log(self.lena, "request_sent", key="press-one-1234")
        self.assertEqual((first.status_code, again.status_code), (200, 200))
        self.assertEqual(first.json(), again.json())
        self.assertEqual(again.headers["Idempotent-Replay"], "true")
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_a_press_with_a_new_request_id_after_the_first_landed_is_already_logged(self):
        self.ok(self.log(self.lena, "request_sent"))
        error = self.refused(self.log(self.lena, "request_sent"))
        self.assertIn("Already logged", error["message"])
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_unknown_person_is_not_found_and_stores_nothing(self):
        self.refused(self.log(9999, "request_sent"), "not_found", 404)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_a_person_id_too_big_for_the_store_is_a_bad_request_not_a_500(self):
        self.refused(self.log(2**70, "request_sent"), "bad_request", 400)
        self.refused(self.undo(0, "request_sent"), "bad_request", 400)

    def test_unknown_step_and_extra_fields_are_bad_requests(self):
        self.refused(self.log(self.lena, "poked"), "bad_request", 400)
        res = self.post(f"/api/linkedin/{self.lena}/log", {"step": "request_sent", "revision": 1})
        self.refused(res, "bad_request", 400)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_progress_counts_the_request_once_whether_logged_from_the_list_or_the_plan(self):
        plan_person = self.person("Plan Pat")
        self.plan_touch(plan_person)
        error = self.refused(self.log(plan_person, "request_sent"))
        self.assertIn("Already logged", error["message"])
        self.assertEqual(self.counts()[:2], (1, 1))
        self.ok(self.log(self.lena, "request_sent"))
        progress = self.ok(self.get("/api/progress"))
        self.assertEqual(progress["today"]["requests"], 2)
        requests = next(st for st in progress["week"]["stages"] if st["stage"] == "requests")
        self.assertEqual(requests["count"], 2)
        self.assertEqual(self.view()["requestsThisWeek"], 2)

    def test_a_live_request_touch_with_no_log_row_counts_as_the_request_step(self):
        plan_person = self.person("Plan Pat")
        self.plan_touch(plan_person, with_log=False)
        shown = self.people()["Plan Pat"]
        self.assertEqual((shown["tab"], shown["steps"]), ("waiting", [{"step": "request_sent", "at": NOW}]))
        self.assertEqual(self.view()["counts"]["toReach"], 1)  # only Lena
        self.set_now(T1)
        body = self.ok(self.log(plan_person, "accepted"))  # the next step is open to them
        self.assertEqual([x["step"] for x in body["steps"]], ["request_sent", "accepted"])
        self.assertEqual(self.touches(plan_person)[0]["accepted_at"], T1)

    def test_taking_back_a_request_that_has_only_a_touch_undoes_the_touch(self):
        plan_person = self.person("Plan Pat")
        self.plan_touch(plan_person, with_log=False)
        self.set_now(T1)
        body = self.ok(self.undo(plan_person, "request_sent"))
        self.assertEqual((body["steps"], body["tab"]), ([], "to_reach"))
        self.assertEqual(self.touches(plan_person)[0]["undone_at"], T1)
        self.assertEqual(self.log_rows(), [])

    def test_a_plan_touch_with_no_log_row_is_already_logged_too(self):
        plan_person = self.person("Plan Pat")
        self.plan_touch(plan_person, with_log=False)
        self.refused(self.log(plan_person, "request_sent"))
        self.assertEqual(self.counts()[:2], (1, 0))


class Order(LinkedInCase):
    def test_steps_must_follow_order(self):
        for step in ("accepted", "message_sent", "replied"):
            self.refused(self.log(self.lena, step, "interested" if step == "replied" else None))
        self.assertEqual(self.counts(), (0, 0, 0))
        self.ok(self.log(self.lena, "request_sent"))
        # A message needs the accept first.
        self.refused(self.log(self.lena, "message_sent"))
        self.assertEqual(self.counts(), (1, 1, 1))
        self.set_now(T1)
        self.ok(self.log(self.lena, "accepted"))
        self.refused(self.log(self.lena, "accepted"))
        self.set_now(T2)
        self.ok(self.log(self.lena, "message_sent"))
        self.set_now(T3)
        self.ok(self.log(self.lena, "replied", "interested"))
        for step in ("request_sent", "accepted", "message_sent", "replied"):
            error = self.refused(self.log(self.lena, step, "interested" if step == "replied" else None))
            self.assertIn("Already logged", error["message"])
        self.assertEqual([r["kind"] for r in self.log_rows()], ["request_sent", "accepted", "message_sent", "replied"])
        self.assertEqual([t["channel"] for t in self.touches()], ["linkedin_request", "linkedin_message"])

    def test_a_reply_after_only_a_request_logs_the_accept_with_it(self):
        # On LinkedIn a reply to a request means they accepted it. "Mark someone as replied" logs both in one write,
        # the accept first, so the steps stay in order and the newest one (the reply) is what an undo takes back.
        self.walk(self.lena, "request_sent")
        self.set_now(T2)
        body = self.ok(self.log(self.lena, "replied", "interested"))
        self.assertEqual([x["step"] for x in body["steps"]], ["request_sent", "accepted", "replied"])
        self.assertEqual((body["tab"], body["replyKind"]), ("replied", "interested"))
        request, = self.touches()
        self.assertEqual((request["accepted_at"], request["replied_at"], request["reply_kind"]), (T2, T2, "interested"))
        self.assertEqual([r["kind"] for r in self.log_rows()], ["request_sent", "accepted", "replied"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM api_request").fetchone()[0], 2)
        self.ok(self.undo(self.lena, "replied"))
        self.assertEqual([x["step"] for x in self.people()["Lena Ortiz"]["steps"]], ["request_sent", "accepted"])

    def test_a_reply_with_no_request_is_still_refused(self):
        error = self.refused(self.log(self.lena, "replied", "interested"))
        self.assertIn("request first", error["message"])
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_a_reply_after_only_an_accept_is_allowed(self):
        self.walk(self.lena, "request_sent", "accepted", "replied")
        request = self.touches()[0]
        self.assertEqual((request["accepted_at"], request["replied_at"], request["reply_kind"]),
                         (self.at(1), self.at(2), "interested"))
        self.assertEqual(len(self.touches()), 1)

    def test_accepted_sets_the_date_on_the_request_touch_and_a_message_makes_its_own_touch(self):
        self.walk(self.lena, "request_sent", "accepted", "message_sent")
        request, message = self.touches()
        self.assertEqual((request["channel"], request["accepted_at"]), ("linkedin_request", self.at(1)))
        self.assertEqual((message["channel"], message["sent_at"], message["accepted_at"]),
                         ("linkedin_message", self.at(2), None))
        self.assertEqual(json.loads(message["context"])["source"], "own")

    def test_a_reply_lands_on_the_message_touch_and_closes_it_for_the_prompt(self):
        self.walk(self.lena, "request_sent", "accepted", "message_sent", "replied")
        request, message = self.touches()
        self.assertIsNone(request["replied_at"])
        self.assertEqual((message["replied_at"], message["reply_kind"]), (self.at(3), "interested"))

    def test_replied_needs_a_kind_and_only_replied_takes_one(self):
        self.walk(self.lena, "request_sent", "accepted")
        before = self.counts()
        self.refused(self.log(self.lena, "replied"), "bad_request", 400)
        self.refused(self.log(self.lena, "message_sent", "interested"), "bad_request", 400)
        self.assertEqual(self.counts(), before)

    def test_auto_reply_and_unsorted_are_refused(self):
        self.walk(self.lena, "request_sent", "accepted")
        before = self.counts()
        for kind in ("auto_reply", "unsorted"):
            self.refused(self.log(self.lena, "replied", kind), "bad_request", 400)
        self.assertEqual(self.counts(), before)

    def test_history_without_a_touch_is_refused_in_plain_words(self):
        s.linkedin(self.conn, self.lena, "request_sent", "2026-10-01T12:00:00Z")  # a log row nothing else backs
        error = self.refused(self.log(self.lena, "accepted"))
        self.assertIn("no request touch", error["message"].lower())
        self.assertEqual((len(self.touches()), len(self.log_rows())), (0, 1))


class Replies(LinkedInCase):
    def test_replied_said_no_adds_person_no_go_and_hides_the_person_from_to_reach(self):
        other = self.person("Owen Clarke")
        self.assertEqual(set(self.people()), {"Lena Ortiz", "Owen Clarke"})
        self.assertEqual(self.view()["counts"]["toReach"], 2)
        email = s.touch(self.conn, self.lena)
        s.send_first(self.conn, email)
        planned = s.message(self.conn, email, 1, "drafted")
        self.walk(self.lena, "request_sent", "accepted")
        self.set_now(T3)
        body = self.ok(self.log(self.lena, "replied", "said_no"))
        self.assertEqual((body["tab"], body["replyKind"]), ("replied", "said_no"))
        no_go = self.conn.execute("SELECT person_id, reason, source, lifted_at FROM no_go").fetchall()
        self.assertEqual([tuple(r) for r in no_go], [(self.lena, "said_no", "reply", None)])
        self.assertEqual(self.conn.execute("SELECT state FROM message WHERE message_id = ?", (planned,)).fetchone()[0],
                         "skipped")
        view = self.view()
        self.assertEqual(set(self.people()), {"Owen Clarke"})
        self.assertEqual(view["counts"], {"toReach": 1, "waiting": 0, "replied": 0, "all": 1})
        self.assertEqual(self.conn.execute("SELECT replied_at, reply_kind FROM outreach_touch WHERE touch_id = 1")
                         .fetchone()[:], (T3, "said_no"))
        self.assertEqual(other, view["groups"][0]["people"][0]["person"]["id"])

    def test_asked_to_stop_is_a_stop_reply_no_go(self):
        self.walk(self.lena, "request_sent", "accepted")
        self.ok(self.log(self.lena, "replied", "asked_to_stop"))
        reasons = [r[0] for r in self.conn.execute("SELECT reason FROM no_go WHERE person_id = ?", (self.lena,))]
        self.assertEqual(reasons, ["stop_reply"])

    def test_other_reply_kinds_add_no_no_go(self):
        for kind in ("interested", "not_now"):
            person = self.person(f"Person {kind}")
            self.walk(person, "request_sent", "accepted")
            self.set_now(T3)
            self.ok(self.log(person, "replied", kind))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM no_go").fetchone()[0], 0)
        self.assertEqual({n: p["tab"] for n, p in self.people().items() if n != "Lena Ortiz"},
                         {"Person interested": "replied", "Person not_now": "replied"})


class Undo(LinkedInCase):
    def test_undo_hides_the_step_and_clears_the_outcome_date(self):
        self.walk(self.lena, "request_sent", "accepted")
        self.set_now(T3)
        body = self.ok(self.undo(self.lena, "accepted"))
        self.assertEqual(body["steps"], [{"step": "request_sent", "at": self.at(0)}])
        self.assertEqual(body["tab"], "waiting")
        touch = self.touches()[0]
        self.assertIsNone(touch["accepted_at"])
        self.assertIsNone(touch["undone_at"])
        rows = self.log_rows()
        self.assertEqual([r["kind"] for r in rows], ["request_sent", "accepted", "undo"])
        self.assertEqual((rows[2]["undoes"], rows[2]["at"]), (rows[1]["entry_id"], T3))
        self.assertEqual(self.people()["Lena Ortiz"]["steps"], [{"step": "request_sent", "at": self.at(0)}])
        # The step can be logged again, and it counts once.
        self.set_now("2026-11-10T16:00:00Z")
        self.ok(self.log(self.lena, "accepted"))
        self.assertEqual(self.touches()[0]["accepted_at"], "2026-11-10T16:00:00Z")

    def test_undone_request_is_not_counted_in_progress_or_capacity_and_context_source_is_own(self):
        self.ok(self.log(self.lena, "request_sent"))
        self.assertEqual(self.view()["requestsThisWeek"], 1)
        self.set_now(T1)
        body = self.ok(self.undo(self.lena, "request_sent"))
        self.assertEqual((body["steps"], body["tab"]), ([], "to_reach"))
        touches = self.touches()
        self.assertEqual(len(touches), 1)  # kept, never deleted
        self.assertEqual(touches[0]["undone_at"], T1)
        self.assertEqual(json.loads(touches[0]["context"])["source"], "own")
        self.assertEqual(self.view()["requestsThisWeek"], 0)
        progress = self.ok(self.get("/api/progress"))
        self.assertEqual(progress["today"]["requests"], 0)
        week = next(st for st in progress["week"]["stages"] if st["stage"] == "requests")
        self.assertEqual(week["count"], 0)
        self.assertEqual(progress["base"]["requests"], 0)
        self.assertEqual(self.ok(self.get("/api/outcomes/prompt"))["touches"], [])
        # Quota readers count source 'plan' and undone_at IS NULL: neither matches this touch.
        quota = self.conn.execute("SELECT count(*) FROM outreach_touch WHERE undone_at IS NULL "
                                  "AND json_extract(context, '$.source') = 'plan'").fetchone()[0]
        self.assertEqual(quota, 0)
        # A new request after the undo is a new touch.
        self.set_now(T2)
        self.ok(self.log(self.lena, "request_sent"))
        self.assertEqual(len(self.touches()), 2)
        self.assertEqual(self.view()["requestsThisWeek"], 1)

    def test_undo_request_clears_a_quiet_mark_the_sweeper_made_and_undoes_the_touch(self):
        self.ok(self.log(self.lena, "request_sent"))
        self.conn.execute("UPDATE outreach_touch SET quiet_at = ? WHERE touch_id = 1", (T1,))
        self.set_now(T2)
        self.ok(self.undo(self.lena, "request_sent"))
        touch = self.touches()[0]
        self.assertEqual((touch["undone_at"], touch["quiet_at"]), (T2, None))

    def test_only_the_newest_step_can_be_taken_back(self):
        self.walk(self.lena, "request_sent", "accepted")
        error = self.refused(self.undo(self.lena, "request_sent"))
        self.assertIn("accepted", error["message"])
        before = self.counts()
        self.refused(self.undo(self.lena, "message_sent"))
        self.assertEqual(self.counts(), before)
        self.assertIsNone(self.touches()[0]["undone_at"])

    def test_nothing_to_undo_is_refused(self):
        self.refused(self.undo(self.lena, "request_sent"))
        self.refused(self.undo(9999, "request_sent"), "not_found", 404)

    def test_undo_message_sent_undoes_the_message_touch_only(self):
        self.walk(self.lena, "request_sent", "accepted", "message_sent")
        self.set_now(T3)
        body = self.ok(self.undo(self.lena, "message_sent"))
        self.assertEqual([x["step"] for x in body["steps"]], ["request_sent", "accepted"])
        request, message = self.touches()
        self.assertIsNone(request["undone_at"])
        self.assertEqual(message["undone_at"], T3)
        self.assertEqual(self.view()["requestsThisWeek"], 1)

    def test_undo_replied_clears_the_reply_and_its_kind(self):
        self.walk(self.lena, "request_sent", "accepted", "message_sent", "replied")
        self.set_now("2026-11-10T16:00:00Z")
        body = self.ok(self.undo(self.lena, "replied"))
        self.assertEqual((body["tab"], body["replyKind"]), ("waiting", None))
        message = self.touches()[1]
        self.assertEqual((message["replied_at"], message["reply_kind"]), (None, None))

    def test_an_undone_stop_reply_leaves_the_person_on_the_no_go_list_and_the_followups_skipped(self):
        email = s.touch(self.conn, self.lena)
        s.send_first(self.conn, email)
        drafted = s.message(self.conn, email, 1, "drafted")
        self.walk(self.lena, "request_sent", "accepted")
        self.ok(self.log(self.lena, "replied", "said_no"))
        self.assertEqual(self.message_state(drafted), "skipped")
        self.ok(self.undo(self.lena, "replied"))
        active = self.conn.execute("SELECT count(*) FROM no_go WHERE person_id = ? AND lifted_at IS NULL",
                                   (self.lena,)).fetchone()[0]
        self.assertEqual(active, 1)  # the safe direction: lift it from the No-go page if the click was a mistake
        self.assertEqual(self.message_state(drafted), "skipped")  # nothing un-stops a follow-up
        self.assertNotIn("Lena Ortiz", self.people())

    def test_taking_back_a_step_that_has_no_touch_only_hides_it(self):
        s.linkedin(self.conn, self.lena, "request_sent", "2026-10-01T12:00:00Z")  # a log row nothing else backs
        self.assertEqual(self.ok(self.undo(self.lena, "request_sent"))["steps"], [])
        self.assertEqual([r["kind"] for r in self.log_rows()], ["request_sent", "undo"])

    def test_a_request_with_a_real_outcome_cannot_be_taken_back(self):
        self.ok(self.log(self.lena, "request_sent"))
        self.conn.execute("UPDATE outreach_touch SET referred_at = ? WHERE touch_id = 1", (T1,))
        before = self.counts()
        error = self.refused(self.undo(self.lena, "request_sent"))
        self.assertIn("referral", error["message"])
        self.assertEqual(self.counts(), before)
        self.assertIsNone(self.touches()[0]["undone_at"])

    def test_a_request_cannot_be_taken_back_while_their_message_has_an_outcome(self):
        self.ok(self.log(self.lena, "request_sent"))
        s._insert(self.conn, "outreach_touch", {
            "person_id": self.lena, "company_id": self.acme, "channel": "linkedin_message", "sent_at": T1,
            "replied_at": T2, "reply_kind": "interested", "created_at": T1,
            "context": json.dumps(build_context(source="own", lane="own", role_kind=None, role_age_days=None,
                                                ranker_version=None, signals={}, role_named=False))})
        before = self.counts()
        error = self.refused(self.undo(self.lena, "request_sent"))
        self.assertIn("reply", error["message"])
        self.assertEqual(self.counts(), before)
        self.assertIsNone(self.touches()[0]["undone_at"])

    def test_the_same_undo_key_replays(self):
        self.ok(self.log(self.lena, "request_sent"))
        first = self.post(f"/api/linkedin/{self.lena}/undo", {"step": "request_sent"}, key="undo-one-1234")
        again = self.post(f"/api/linkedin/{self.lena}/undo", {"step": "request_sent"}, key="undo-one-1234")
        self.assertEqual((first.status_code, again.status_code), (200, 200))
        self.assertEqual([r["kind"] for r in self.log_rows()], ["request_sent", "undo"])


class NoGo(LinkedInCase):
    def add_no_go(self, **cols) -> int:
        row = {"reason": "other", "source": "devanshu", "created_at": s.T0, **cols}
        return s._insert(self.conn, "no_go", row)

    def test_a_person_on_the_no_go_list_is_never_offered(self):
        pat, sam = self.person("Pat Lee"), self.person("Sam Park")
        other = s.company(self.conn, "Other", "working")
        zed = self.person("Zed Ito", other)
        for who, source, reason in ((pat, "devanshu", "not_a_fit"), (sam, "reply", "said_no")):
            self.add_no_go(person_id=who, source=source, reason=reason)
        self.assertEqual(set(self.people()), {"Lena Ortiz", "Zed Ito"})
        self.add_no_go(company_id=other, reason="agency")
        self.assertEqual(set(self.people()), {"Lena Ortiz"})
        self.assertNotIn(zed, [p["person"]["id"] for p in self.people().values()])
        self.assertEqual(self.view()["counts"]["all"], 1)

    def test_a_reply_no_go_written_on_another_channel_hides_the_linkedin_person(self):
        touch = s.touch(self.conn, self.lena)
        s.send_first(self.conn, touch)
        t = s._insert(self.conn, "outreach_touch", {
            "person_id": self.lena, "company_id": self.acme, "channel": "email", "email_touch_id": touch,
            "sent_at": "2026-11-05T15:00:00Z", "created_at": s.T0,
            "context": json.dumps(build_context(source="own", lane="own", role_kind=None, role_age_days=None,
                                                ranker_version=None, signals={}, role_named=False))})
        self.ok(self.post(f"/api/touches/{t}/outcomes", {"kind": "replied", "replyKind": "said_no", "revision": 1}))
        self.assertEqual(self.view()["groups"], [])

    def test_a_no_go_on_a_merged_duplicate_stops_everyone_at_the_survivor(self):
        dup = s.company(self.conn, "Acme Inc", "removed", merged_into=self.acme)
        self.add_no_go(company_id=dup, reason="agency")
        self.assertEqual(self.view()["groups"], [])
        error = self.refused(self.log(self.lena, "request_sent"))
        self.assertIn("no-go", error["message"].lower())
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_a_request_he_already_sent_to_someone_at_a_removed_or_closed_company_is_history_and_logs(self):
        for name, status, cols in (("Gone", "removed", {}), ("Done", "closed", {"close_reason": "other"})):
            company = s.company(self.conn, name, status, **cols)
            who = self.person(f"Person at {name}", company)
            body = self.ok(self.log(who, "request_sent"))
            self.assertEqual(body["tab"], "waiting")
        self.assertEqual(len(self.touches()), 2)

    def test_a_lifted_no_go_brings_the_person_back(self):
        no_go = self.add_no_go(person_id=self.lena)
        self.assertEqual(self.people(), {})
        self.conn.execute("UPDATE no_go SET lifted_at = ?, lift_reason = 'mistake' WHERE no_go_id = ?", (s.T0, no_go))
        self.assertEqual(set(self.people()), {"Lena Ortiz"})

    def test_a_removed_company_is_not_offered_and_a_closed_one_offers_nobody_new(self):
        gone = s.company(self.conn, "Gone", "removed")
        self.person("Rae Kim", gone)
        done = s.company(self.conn, "Done", "closed", close_reason="no_sponsorship")
        fresh = self.person("Fay Lund", done)
        waiting = self.person("Wes Ode", done)
        s.linkedin(self.conn, waiting, "request_sent", s.T0)
        shown = self.people()
        self.assertEqual(set(shown), {"Lena Ortiz", "Wes Ode"})
        self.assertNotIn(fresh, [p["person"]["id"] for p in shown.values()])

    def test_logging_a_new_request_or_message_to_a_no_go_person_is_refused(self):
        self.add_no_go(person_id=self.lena)
        error = self.refused(self.log(self.lena, "request_sent"))
        self.assertIn("no-go", error["message"].lower())
        self.assertEqual(self.counts(), (0, 0, 0))
        company_blocked = s.company(self.conn, "Blocked", "working")
        mia = self.person("Mia Fox", company_blocked)
        self.add_no_go(company_id=company_blocked)
        self.refused(self.log(mia, "request_sent"))


class Reads(LinkedInCase):
    def test_empty_store_is_an_empty_view(self):
        self.conn.execute("DELETE FROM person")
        self.assertEqual(self.view(), {"asOf": NOW, "groups": [],
                                       "counts": {"toReach": 0, "waiting": 0, "replied": 0, "all": 0},
                                       "activity": {"messagedWeek": 0, "repliedWeek": 0, "messagedTotal": 0,
                                                    "repliedTotal": 0},
                                       "requestsThisWeek": 0})

    def test_a_store_with_only_email_people_is_an_empty_view(self):
        self.conn.execute("DELETE FROM person")
        s.person(self.conn, self.acme, "Pat Lee")
        self.assertEqual(self.view()["groups"], [])

    def test_tabs_are_derived_not_computed_by_the_client(self):
        fresh, wait, accepted, replied = (self.person(n) for n in ("Fay A", "Wes B", "Ann C", "Rey D"))
        self.walk(wait, "request_sent")
        self.walk(accepted, "request_sent", "accepted")
        self.walk(replied, "request_sent", "accepted", "message_sent", "replied")
        view = self.view()
        by_name = self.people()
        self.assertEqual({n: p["tab"] for n, p in by_name.items() if n != "Lena Ortiz"},
                         {"Fay A": "to_reach", "Wes B": "waiting", "Ann C": "waiting", "Rey D": "replied"})
        self.assertEqual(view["counts"], {"toReach": 2, "waiting": 2, "replied": 1, "all": 5})
        self.assertEqual(by_name["Rey D"]["replyKind"], "interested")
        self.assertEqual([x["step"] for x in by_name["Rey D"]["steps"]],
                         ["request_sent", "accepted", "message_sent", "replied"])
        self.assertEqual(by_name["Fay A"]["reason"], "no_email")
        self.assertEqual(by_name["Fay A"]["steps"], [])
        self.assertEqual(view["requestsThisWeek"], 3)
        self.assertEqual(fresh, by_name["Fay A"]["person"]["id"])

    def test_steps_come_from_the_log_with_taken_back_steps_hidden(self):
        a = s.linkedin(self.conn, self.lena, "request_sent", "2026-11-03T12:00:00Z")
        b = s.linkedin(self.conn, self.lena, "accepted", "2026-11-04T12:00:00Z")
        s.linkedin(self.conn, self.lena, "undo", "2026-11-05T12:00:00Z", undoes=b)
        self.assertEqual(self.people()["Lena Ortiz"]["steps"], [{"step": "request_sent", "at": "2026-11-03T12:00:00Z"}])
        self.assertEqual(self.people()["Lena Ortiz"]["tab"], "waiting")
        self.assertTrue(a)

    def test_held_company_shows_who_holds_it(self):
        pat = s.person(self.conn, self.acme, "Priya Natarajan")
        held = s.touch(self.conn, pat)
        s.send_first(self.conn, held)
        quiet = s.person(self.conn, self.acme, "Quinn Rowe")
        s.send_first(self.conn, s.touch(self.conn, quiet))
        s.person(self.conn, self.acme, "Not Emailed Yet")  # an email person with no touch
        s.touch(self.conn, s.person(self.conn, self.acme, "Draft Only"))  # a touch whose first email has not gone out
        self.conn.execute("UPDATE company SET held_by_touch = ?, held_at = ? WHERE company_id = ?",
                          (held, s.T0, self.acme))
        free = s.company(self.conn, "Free", "working")
        self.person("Freya Moe", free)
        groups = {g["company"]["name"]: g for g in self.view()["groups"]}
        self.assertEqual(groups["Acme"]["heldBy"], {"touchId": held, "personName": "Priya Natarajan"})
        self.assertEqual((groups["Acme"]["emailed"], groups["Acme"]["onLinkedIn"]), (2, 1))
        self.assertEqual(groups["Acme"]["company"]["heldByTouchId"], held)
        self.assertIsNone(groups["Free"]["heldBy"])
        self.assertEqual((groups["Free"]["emailed"], groups["Free"]["onLinkedIn"]), (0, 1))

    def test_a_hold_on_a_merged_duplicate_shows_on_the_survivors_group(self):
        dup = s.company(self.conn, "Acme Inc", "removed", merged_into=self.acme)
        held = s.touch(self.conn, s.person(self.conn, dup, "Priya Natarajan"))
        s.send_first(self.conn, held)
        self.conn.execute("UPDATE company SET held_by_touch = ?, held_at = ? WHERE company_id = ?", (held, s.T0, dup))
        groups = {g["company"]["name"]: g for g in self.view()["groups"]}
        self.assertEqual(groups["Acme"]["heldBy"], {"touchId": held, "personName": "Priya Natarajan"})
        self.assertEqual(groups["Acme"]["company"]["heldByTouchId"], held)

    def test_a_merged_company_groups_under_its_survivor_and_follows_its_hold(self):
        dup = s.company(self.conn, "Acme Inc", "removed", merged_into=self.acme)
        self.person("Mia Fox", dup)
        groups = self.view()["groups"]
        self.assertEqual([(g["company"]["name"], g["onLinkedIn"]) for g in groups], [("Acme", 2)])

    def test_the_group_shows_the_role_and_none_when_there_is_no_open_role(self):
        quiet = s.company(self.conn, "Quiet", "working")
        self.person("Quin Ash", quiet)
        s.posting(self.conn, quiet, "Old Role", closed_at="2026-11-02T10:00:00Z")
        posting = s.posting(self.conn, self.acme, "Software Engineer, New Grad")
        s.decision(self.conn, posting, years_asked=1)
        groups = {g["company"]["name"]: g for g in self.view()["groups"]}
        self.assertIsNone(groups["Quiet"]["role"])
        role = groups["Acme"]["role"]
        self.assertEqual((role["title"], role["levelTier"], role["yearsAsked"]), ("Software Engineer, New Grad", "new_grad", 1))

    def test_requests_this_week_is_the_new_york_week_and_ignores_undone_and_other_channels(self):
        context = json.dumps(build_context(source="plan", lane="level_role", role_kind="engineer", role_age_days=1,
                                           ranker_version="r1", signals={}, role_named=True))

        def touch(sent_at: str, channel: str = "linkedin_request", **cols):
            s._insert(self.conn, "outreach_touch", {"person_id": self.lena, "company_id": self.acme,
                                                    "channel": channel, "sent_at": sent_at, "context": context,
                                                    "created_at": sent_at, **cols})
        touch("2026-11-09T05:30:00Z")   # Monday 00:30 New York: in
        touch("2026-11-09T04:59:00Z")   # Sunday 23:59 New York: last week
        touch("2026-11-16T04:59:00Z")   # Sunday 23:59 New York: in
        touch("2026-11-16T05:00:00Z")   # next Monday: out
        touch("2026-11-10T12:00:00Z", undone_at="2026-11-10T13:00:00Z")
        touch("2026-11-10T12:00:00Z", channel="linkedin_message")
        touch("2026-11-10T12:00:00Z", channel="application")
        self.assertEqual(self.view()["requestsThisWeek"], 2)
        progress = self.ok(self.get("/api/progress"))
        week = next(st for st in progress["week"]["stages"] if st["stage"] == "requests")
        self.assertEqual(week["count"], 2)


class RepliedCountUsesTheSendersNoGoSets(ApiCase):
    def test_replied_count_reads_the_no_go_sets_the_sender_reads(self):
        from unittest import mock
        from outreach.api.queries import linkedin as queries
        company = s.company(self.conn, "Acme", "candidate")
        pat = s.person(self.conn, company, "Pat", channel="linkedin")
        s.person(self.conn, company, "Quiet", channel="linkedin")
        s.linkedin(self.conn, pat, "request_sent", "2026-11-03T12:00:00Z")
        s.linkedin(self.conn, pat, "replied", "2026-11-04T12:00:00Z", reply_kind="interested")
        self.assertEqual(queries.replied_count(self.conn), 1)
        with mock.patch.object(queries, "no_go_sets", return_value=(set(), {company})):
            self.assertEqual(queries.replied_count(self.conn), 0)
        with mock.patch.object(queries, "no_go_sets", return_value=({pat}, set())):
            self.assertEqual(queries.replied_count(self.conn), 0)


class Activity(LinkedInCase):
    """The counts line: people messaged (a live LinkedIn request) and people who replied, the last 7 days and in all."""

    def request(self, person_id: int, sent_at: str, **cols) -> None:
        context = json.dumps(build_context(source="own", lane="own", role_kind="engineer", role_age_days=0,
                                           ranker_version="r1", signals={}, role_named=False))
        s._insert(self.conn, "outreach_touch", {"person_id": person_id, "company_id": self.acme,
                                                "channel": "linkedin_request", "sent_at": sent_at,
                                                "context": context, "created_at": sent_at, **cols})

    def test_messaged_counts_people_with_a_live_request_and_replied_counts_a_live_reply_step(self):
        old, recent, twice, undone, emailer = (self.person(n) for n in ("Old A", "New B", "Two C", "Undo D", "Mail E"))
        self.request(old, "2026-10-01T12:00:00Z")
        self.request(recent, "2026-11-04T05:00:00Z")    # Nov 4 00:00 New York: the first of the 7 days
        self.request(twice, "2026-11-03T12:00:00Z")     # Nov 3: before the 7 days
        self.request(twice, "2026-11-10T12:00:00Z")     # and again today: one person, in both counts once
        self.request(undone, "2026-11-09T12:00:00Z", undone_at="2026-11-09T13:00:00Z")
        context = json.dumps(build_context(source="own", lane="own", role_kind="engineer", role_age_days=0,
                                           ranker_version="r1", signals={}, role_named=False))
        s._insert(self.conn, "outreach_touch", {"person_id": emailer, "company_id": self.acme, "channel": "email",
                                                "sent_at": NOW, "context": context, "created_at": NOW})
        s.linkedin(self.conn, old, "replied", "2026-10-02T12:00:00Z", reply_kind="not_now")
        s.linkedin(self.conn, recent, "replied", "2026-11-05T12:00:00Z", reply_kind="interested")
        taken = s.linkedin(self.conn, twice, "replied", "2026-11-10T12:30:00Z", reply_kind="interested")
        s.linkedin(self.conn, twice, "undo", "2026-11-10T12:40:00Z", undoes=taken)
        self.assertEqual(self.view()["activity"], {"messagedWeek": 2, "repliedWeek": 1, "messagedTotal": 3,
                                                   "repliedTotal": 2})

    def test_a_reply_logged_through_the_list_counts(self):
        self.walk(self.lena, "request_sent", "replied")
        self.assertEqual(self.view()["activity"], {"messagedWeek": 1, "repliedWeek": 1, "messagedTotal": 1,
                                                   "repliedTotal": 1})
