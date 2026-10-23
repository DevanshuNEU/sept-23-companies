"""Outcome writes, the prompt, corrections, touch history and the quiet sweeper (#11 Task 12)."""
import dataclasses
import json
import sqlite3
import time
import uuid
from unittest import mock

from fastapi.testclient import TestClient

from outreach.api import sweeper
from outreach.api.config import load_config
from outreach.api.errors import ApiProblem
from outreach.api.queries import outcomes as outcome_queries
from outreach.outcomes.wins import is_log_win
from outreach.sending import queue
from outreach.store import connect
from tests import apiseed as s
from tests.apitest import NOW, ApiCase

CFG = load_config()
DAY_8 = "2026-11-02T15:00:00Z"  # 8 New York days before NOW (2026-11-10)
DAY_7 = "2026-11-03T15:00:00Z"
DAY_6 = "2026-11-04T15:00:00Z"
DAY_20 = "2026-10-21T15:00:00Z"
DAY_21 = "2026-10-20T15:00:00Z"
DAY_25 = "2026-10-16T15:00:00Z"


def context(source: str = "plan", lane: str = "level_role", role_named: bool = True) -> str:
    return json.dumps({"source": source, "lane": lane, "roleKind": "engineer", "roleAgeDays": 3,
                       "rankerVersion": "r1", "signals": {}, "roleNamed": role_named})


def touch(conn, company_id: int, person_id: int | None, *, channel: str = "linkedin_request", sent_at: str = DAY_8,
          ctx: str | None = None, **cols) -> int:
    return s._insert(conn, "outreach_touch", {
        "person_id": person_id, "company_id": company_id, "channel": channel, "sent_at": sent_at,
        "context": ctx or context(), "created_at": sent_at, **cols,
    })


def planned_followup(conn, email_touch_id: int, seq: int = 1, due_on: str = "2026-11-14") -> int:
    return s._insert(conn, "message", {
        "touch_id": email_touch_id, "seq": seq, "state": "planned", "due_on": due_on, "saved_by": "sender",
        "created_at": s.T0, "updated_at": s.T0,
    })


def move(conn, message_id: int, state: str) -> None:
    """Walk a drafted message to `state` through legal transitions (approved, or failed after an unclear send)."""
    conn.execute("UPDATE message SET state = 'approved', approved_at = ? WHERE message_id = ?", (s.T0, message_id))
    if state == "failed":
        conn.execute("UPDATE message SET state = 'sending', pending_rfc_id = ?, claimed_at = ? WHERE message_id = ?",
                     (f"<pending{message_id}@x>", s.T0, message_id))
        conn.execute("UPDATE message SET state = 'send_unknown' WHERE message_id = ?", (message_id,))
        conn.execute("UPDATE message SET state = 'failed', pending_rfc_id = NULL, claimed_at = NULL "
                     "WHERE message_id = ?", (message_id,))


def score(conn, company_id: int, lanes=("level_role",), *, version: str = "r1",
          scored_at: str = "2026-11-09T06:00:00Z", values: dict | None = None) -> None:
    signals = {"values": {k: {"value": v, "weight": 1.0, "points": v} for k, v in (values or {}).items()},
               "reasons": [], "hide": None, "flags": [], "best_posting_id": None}
    conn.execute(
        "INSERT INTO company_score(company_id, ranker_version, score, signals, lanes, scored_at) VALUES (?,?,?,?,?,?)",
        (company_id, version, 1.0, json.dumps(signals), json.dumps(list(lanes)), scored_at),
    )


class OutcomeCase(ApiCase):
    def setUp(self):
        super().setUp()
        self.acme = s.company(self.conn, "Acme", "working")
        self.lena = s.person(self.conn, self.acme, "Lena Ortiz", channel="linkedin",
                             linkedin_url="https://www.linkedin.com/in/lena")

    def row(self, touch_id: int) -> sqlite3.Row:
        return self.conn.execute("SELECT * FROM outreach_touch WHERE touch_id = ?", (touch_id,)).fetchone()

    def answer(self, touch_id: int, kind: str, revision: int = 1, reply_kind: str | None = None,
               key: str | None = None):
        body = {"kind": kind, "revision": revision}
        if reply_kind is not None:
            body["replyKind"] = reply_kind
        return self.post(f"/api/touches/{touch_id}/outcomes", body, key=key)

    def correct(self, touch_id: int, kind: str, at, revision: int, reply_kind: str | None = None):
        body = {"at": at, "revision": revision}
        if reply_kind is not None:
            body["replyKind"] = reply_kind
        return self.client.put(f"/api/touches/{touch_id}/outcomes/{kind}", content=json.dumps(body),
                               headers={"Content-Type": "application/json", "Idempotency-Key": str(uuid.uuid4())})

    def prompt(self) -> list[dict]:
        res = self.get("/api/outcomes/prompt")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["asOf"], self.now)
        return res.json()["touches"]

    def prompt_ids(self) -> list[int]:
        return [t["touchId"] for t in self.prompt()]

    def events(self, kind: str | None = None) -> list[sqlite3.Row]:
        sql, args = "SELECT * FROM event", ()
        if kind:
            sql, args = sql + " WHERE kind = ?", (kind,)
        return self.conn.execute(sql + " ORDER BY event_id", args).fetchall()

    def state_of(self, message_id: int) -> str | None:
        row = self.conn.execute("SELECT state FROM message WHERE message_id = ?", (message_id,)).fetchone()
        return row[0] if row else None

    def sendable(self, message_id: int) -> bool:
        """Whether the sender's own queue would still consider this message (approved, and no stop reason)."""
        rows, companies = queue._rows(self.conn)
        ids = {r["message_id"] for r in rows}
        return message_id in ids and message_id not in queue._stop_reasons(rows, companies, self.conn)

    def emailed(self, name: str = "Pat Lee") -> tuple[int, int]:
        """A person with an email touch whose first email was sent: (person_id, email touch_id)."""
        pid = s.person(self.conn, self.acme, name)
        email = s.touch(self.conn, pid)
        s.send_first(self.conn, email)
        return pid, email

    def log_rows(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM linkedin_log ORDER BY entry_id").fetchall()


class Prompt(OutcomeCase):
    def test_empty_store_prompt_is_empty(self):
        self.assertEqual(self.get("/api/outcomes/prompt").json(), {"asOf": NOW, "touches": []})

    def test_prompt_lists_touches_from_day_7_oldest_first(self):
        day7 = touch(self.conn, self.acme, self.lena, sent_at=DAY_7)
        day8 = touch(self.conn, self.acme, self.lena, sent_at=DAY_8, ctx=context("own", "reason_no_role", False))
        touch(self.conn, self.acme, self.lena, sent_at=DAY_6)
        touch(self.conn, self.acme, self.lena, sent_at=DAY_8, accepted_at=NOW)
        touch(self.conn, self.acme, self.lena, sent_at=DAY_8, undone_at=DAY_7)
        items = self.prompt()
        self.assertEqual([t["touchId"] for t in items], [day8, day7])
        first = items[0]
        self.assertEqual(first["ageDays"], 8)
        self.assertEqual(items[1]["ageDays"], 7)
        self.assertEqual(first["channel"], "linkedin_request")
        self.assertEqual(first["sentAt"], DAY_8)
        self.assertEqual(first["revision"], 1)
        self.assertEqual(first["person"]["name"], "Lena Ortiz")
        self.assertEqual(first["company"], {"id": self.acme, "name": "Acme", "status": "working", "pausedAt": None,
                                            "heldByTouchId": None})
        self.assertIsInstance(first["context"], str)
        self.assertTrue(first["context"])
        self.assertNotIn("_", first["context"])
        self.assertIn("LinkedIn request", first["context"])

    def test_an_application_touch_with_no_person_is_asked_about_too(self):
        app = touch(self.conn, self.acme, None, channel="application", sent_at=DAY_8)
        items = self.prompt()
        self.assertEqual([t["touchId"] for t in items], [app])
        self.assertIsNone(items[0]["person"])


class Answers(OutcomeCase):
    def test_accepted_adds_the_outcome_a_linkedin_log_row_and_clears_the_prompt(self):
        t = touch(self.conn, self.acme, self.lena)
        self.assertEqual(self.prompt_ids(), [t])
        res = self.answer(t, "accepted")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), {"revision": 2})
        self.assertEqual(self.row(t)["accepted_at"], NOW)
        logs = self.log_rows()
        self.assertEqual([(r["person_id"], r["kind"], r["at"], r["reply_kind"]) for r in logs],
                         [(self.lena, "accepted", NOW, None)])
        self.assertEqual(self.prompt_ids(), [])

    def test_replied_without_a_kind_is_bad_request(self):
        t = touch(self.conn, self.acme, self.lena)
        res = self.answer(t, "replied")
        self.assertEqual(res.status_code, 400, res.text)
        self.assertEqual(res.json()["code"], "bad_request")
        self.assertEqual(self.row(t)["revision"], 1)
        self.assertEqual(self.events(), [])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM api_request").fetchone()[0], 0)

    def test_a_reply_kind_with_another_outcome_is_bad_request(self):
        t = touch(self.conn, self.acme, self.lena)
        res = self.answer(t, "accepted", reply_kind="interested")
        self.assertEqual(res.status_code, 400, res.text)
        self.assertIsNone(self.row(t)["accepted_at"])

    def test_said_no_adds_a_person_no_go_and_cancels_planned_followups(self):
        email = s.touch(self.conn, self.lena)
        s.send_first(self.conn, email)
        planned = planned_followup(self.conn, email)
        other = s.person(self.conn, self.acme, "Sam Park")
        other_touch = s.touch(self.conn, other)
        s.send_first(self.conn, other_touch)
        kept = planned_followup(self.conn, other_touch)
        t = touch(self.conn, self.acme, self.lena)
        res = self.answer(t, "replied", reply_kind="said_no")
        self.assertEqual(res.status_code, 200, res.text)
        row = self.row(t)
        self.assertEqual((row["replied_at"], row["reply_kind"]), (NOW, "said_no"))
        no_go = self.conn.execute("SELECT * FROM no_go WHERE person_id = ?", (self.lena,)).fetchall()
        self.assertEqual([(g["reason"], g["source"], g["lifted_at"], g["created_at"]) for g in no_go],
                         [("said_no", "reply", None, NOW)])
        messages = {r[0] for r in self.conn.execute("SELECT message_id FROM message WHERE state = 'planned'")}
        self.assertNotIn(planned, messages)
        self.assertIn(kept, messages)
        stopped = self.events("skipped_by_sender")
        self.assertEqual(len(stopped), 1)
        self.assertEqual((stopped[0]["entity"], stopped[0]["entity_id"], stopped[0]["actor"]),
                         ("touch", email, "devanshu"))
        self.assertEqual([(r["kind"], r["reply_kind"]) for r in self.log_rows()], [("replied", "said_no")])

    def test_asked_to_stop_is_a_stop_reply_no_go(self):
        t = touch(self.conn, self.acme, self.lena, channel="linkedin_message")
        self.assertEqual(self.answer(t, "replied", reply_kind="asked_to_stop").status_code, 200)
        reasons = [r[0] for r in self.conn.execute("SELECT reason FROM no_go WHERE person_id = ?", (self.lena,))]
        self.assertEqual(reasons, ["stop_reply"])

    def test_said_no_on_an_email_touch_writes_the_no_go_and_cancels_followups(self):
        pat, email = self.emailed()
        planned = planned_followup(self.conn, email)
        t = touch(self.conn, self.acme, pat, channel="email", email_touch_id=email)
        self.assertEqual(self.answer(t, "replied", reply_kind="said_no").status_code, 200)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM message WHERE message_id = ?", (planned,)).fetchone())
        no_go = self.conn.execute("SELECT person_id, reason, source FROM no_go").fetchall()
        self.assertEqual([tuple(r) for r in no_go], [(pat, "said_no", "reply")])
        self.assertEqual(self.log_rows(), [])

    def test_a_stop_reply_writes_the_no_go_on_every_channel(self):
        for channel in ("linkedin_request", "linkedin_message", "email", "hn_reply", "other"):
            with self.subTest(channel=channel):
                pid = s.person(self.conn, self.acme, f"Person {channel}")
                t = touch(self.conn, self.acme, pid, channel=channel)
                self.assertEqual(self.answer(t, "replied", reply_kind="asked_to_stop").status_code, 200)
                rows = self.conn.execute("SELECT reason, source FROM no_go WHERE person_id = ? AND lifted_at IS NULL",
                                         (pid,)).fetchall()
                self.assertEqual([tuple(r) for r in rows], [("stop_reply", "reply")])

    def test_an_approved_followup_is_not_sendable_after_a_stop_reply(self):
        pat, email = self.emailed()
        approved = s.message(self.conn, email, 1, "approved")
        self.assertTrue(self.sendable(approved))
        t = touch(self.conn, self.acme, pat, channel="email", email_touch_id=email)
        self.assertEqual(self.answer(t, "replied", reply_kind="asked_to_stop").status_code, 200)
        self.assertFalse(self.sendable(approved))
        self.assertEqual(self.state_of(approved), "skipped")

    def test_a_stop_reply_skips_drafted_approved_and_failed_rows_and_leaves_sent_ones(self):
        pat, email = self.emailed()
        first = self.conn.execute("SELECT message_id FROM message WHERE touch_id = ? AND seq = 0",
                                  (email,)).fetchone()[0]
        approved = s.message(self.conn, email, 1, "approved")
        drafted = s.message(self.conn, email, 2, "drafted")
        failed = s.message(self.conn, email, 3, "drafted")
        move(self.conn, failed, "failed")
        other, other_email = self.emailed("Sam Park")
        theirs = s.message(self.conn, other_email, 1, "approved")
        t = touch(self.conn, self.acme, pat)
        self.assertEqual(self.answer(t, "replied", reply_kind="said_no").status_code, 200)
        self.assertEqual([self.state_of(m) for m in (first, approved, drafted, failed)],
                         ["sent", "skipped", "skipped", "skipped"])
        self.assertEqual(self.state_of(theirs), "approved")
        skipped = self.events("skipped_by_sender")
        self.assertEqual(sorted((e["entity"], e["entity_id"]) for e in skipped),
                         sorted(("message", m) for m in (approved, drafted, failed)))
        revisions = [r[0] for r in self.conn.execute(
            "SELECT revision FROM message WHERE message_id IN (?, ?, ?)", (approved, drafted, failed))]
        self.assertEqual(revisions, [2, 2, 2])  # each skip moves the revision on, so a stale approve conflicts

    def test_the_stop_event_matches_the_senders_stop_planned(self):
        pat, email = self.emailed()
        planned_followup(self.conn, email, seq=1, due_on="2026-11-14")
        drafted = s.message(self.conn, email, 2, "drafted")
        t = touch(self.conn, self.acme, pat)
        self.assertEqual(self.answer(t, "replied", reply_kind="said_no").status_code, 200)
        events = {e["entity"]: e for e in self.events("skipped_by_sender")}
        planned_event = events["touch"]
        self.assertEqual((planned_event["entity_id"], planned_event["idempotency_key"]),
                         (email, f"followup-stopped:{email}:1:2026-11-14"))
        self.assertEqual(json.loads(planned_event["detail"]), {"seq": 1, "due_on": "2026-11-14", "reason": "said_no"})
        message_event = events["message"]
        self.assertEqual(message_event["entity_id"], drafted)
        self.assertEqual(json.loads(message_event["detail"]), {"seq": 2, "due_on": "2026-11-12", "reason": "said_no"})

    def test_a_second_said_no_keeps_one_active_no_go(self):
        first = touch(self.conn, self.acme, self.lena)
        second = touch(self.conn, self.acme, self.lena, channel="linkedin_message")
        self.assertEqual(self.answer(first, "replied", reply_kind="said_no").status_code, 200)
        self.assertEqual(self.answer(second, "replied", reply_kind="asked_to_stop").status_code, 200)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM no_go").fetchone()[0], 1)

    def test_interested_reply_clears_the_prompt_and_is_a_log_win(self):
        t = touch(self.conn, self.acme, self.lena)
        self.assertEqual(self.answer(t, "replied", reply_kind="interested").status_code, 200)
        row = self.row(t)
        self.assertEqual((row["replied_at"], row["reply_kind"]), (NOW, "interested"))
        self.assertTrue(is_log_win("replied", row["reply_kind"]))
        self.assertEqual(self.prompt_ids(), [])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM no_go").fetchone()[0], 0)

    def test_referred_interview_and_offer_record_their_dates(self):
        t = touch(self.conn, self.acme, self.lena)
        revision = 1
        for kind in ("referred", "interview", "offer"):
            res = self.answer(t, kind, revision)
            self.assertEqual(res.status_code, 200, res.text)
            revision = res.json()["revision"]
        row = self.row(t)
        self.assertEqual((row["referred_at"], row["interview_at"], row["offer_at"]), (NOW, NOW, NOW))
        self.assertEqual(self.log_rows(), [])

    def test_stale_revision_is_a_conflict(self):
        t = touch(self.conn, self.acme, self.lena)
        self.conn.execute("UPDATE outreach_touch SET revision = 2 WHERE touch_id = ?", (t,))
        res = self.answer(t, "accepted", 1)
        self.assertEqual(res.status_code, 409, res.text)
        self.assertEqual(res.json()["code"], "conflict")
        self.assertIsNone(self.row(t)["accepted_at"])
        self.assertEqual(self.log_rows(), [])

    def test_double_click_accept_records_once(self):
        t = touch(self.conn, self.acme, self.lena)
        first = self.answer(t, "accepted", key="click-one-123")
        self.assertEqual(first.status_code, 200, first.text)
        again = self.answer(t, "accepted", key="click-one-123")
        self.assertEqual((again.status_code, again.json()), (200, {"revision": 2}))
        self.assertEqual(again.headers.get("Idempotent-Replay"), "true")
        second = self.answer(t, "accepted", key="click-two-456")
        self.assertEqual(second.status_code, 409, second.text)
        self.assertEqual(second.json()["code"], "not_allowed")
        self.assertIn("Already recorded", second.json()["message"])
        self.assertEqual(len(self.events("accepted")), 1)
        self.assertEqual(len(self.log_rows()), 1)
        self.assertEqual(self.row(t)["revision"], 2)

    def test_unknown_touch_is_404(self):
        self.assertEqual(self.get("/api/touches/999/history").status_code, 404)
        res = self.answer(999, "accepted")
        self.assertEqual((res.status_code, res.json()["code"]), (404, "not_found"))
        self.assertEqual(self.correct(999, "accepted", NOW, 1).status_code, 404)

    def test_an_undone_touch_takes_no_outcome(self):
        t = touch(self.conn, self.acme, self.lena, undone_at=DAY_7)
        res = self.answer(t, "accepted")
        self.assertEqual(res.status_code, 409, res.text)
        self.assertEqual(res.json()["code"], "not_allowed")


class Quiet(OutcomeCase):
    def test_quiet_before_day_21_is_a_snooze_and_leaves_the_outcome_open(self):
        t = touch(self.conn, self.acme, self.lena)
        res = self.answer(t, "quiet")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), {"revision": 1})
        row = self.row(t)
        self.assertIsNone(row["quiet_at"])
        self.assertEqual(row["revision"], 1)
        snoozes = self.events("prompt_snoozed")
        self.assertEqual([(e["entity"], e["entity_id"], e["at"]) for e in snoozes], [("outreach_touch", t, NOW)])
        self.assertEqual(self.prompt_ids(), [])

    def test_snooze_asks_again_after_7_days_then_never(self):
        t = touch(self.conn, self.acme, self.lena, sent_at=DAY_7)
        self.assertEqual(self.answer(t, "quiet").status_code, 200)
        self.set_now("2026-11-16T15:00:00Z")  # 6 days after the snooze
        self.assertEqual(self.prompt_ids(), [])
        self.set_now("2026-11-17T15:00:00Z")  # 7 days after: asked once more
        self.assertEqual(self.prompt_ids(), [t])
        self.assertEqual(self.answer(t, "quiet").status_code, 200)
        self.set_now("2026-11-23T15:00:00Z")  # day 20, after a second snooze: never again
        self.assertEqual(self.prompt_ids(), [])
        self.assertIsNone(self.row(t)["quiet_at"])

    def test_two_snoozes_on_the_same_day_count_once(self):
        t = touch(self.conn, self.acme, self.lena, sent_at=DAY_7)
        self.assertEqual(self.answer(t, "quiet", key="tab-one-123").status_code, 200)
        self.set_now("2026-11-10T22:00:00Z")  # later the same New York day, from another tab
        self.assertEqual(self.answer(t, "quiet", key="tab-two-456").status_code, 200)
        self.assertEqual(len(self.events("prompt_snoozed")), 1)
        self.set_now("2026-11-17T15:00:00Z")  # one snooze counted: asked once more
        self.assertEqual(self.prompt_ids(), [t])

    def test_quiet_from_day_21_sets_quiet_at(self):
        t = touch(self.conn, self.acme, self.lena, sent_at=DAY_21)
        res = self.answer(t, "quiet")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), {"revision": 2})
        self.assertEqual(self.row(t)["quiet_at"], NOW)
        self.assertEqual(len(self.events("quiet")), 1)
        self.assertEqual(self.events("prompt_snoozed"), [])

    def test_quiet_on_a_touch_with_an_outcome_is_not_allowed(self):
        t = touch(self.conn, self.acme, self.lena, replied_at=DAY_7, reply_kind="interested")
        res = self.answer(t, "quiet")
        self.assertEqual(res.status_code, 409, res.text)
        self.assertEqual(res.json()["code"], "not_allowed")
        self.assertEqual(self.events(), [])

    def test_late_reply_after_quiet_is_accepted_and_clears_quiet(self):
        t = touch(self.conn, self.acme, self.lena, sent_at=DAY_25, quiet_at=DAY_7)
        res = self.answer(t, "replied", reply_kind="interested")
        self.assertEqual(res.status_code, 200, res.text)
        row = self.row(t)
        self.assertEqual((row["replied_at"], row["quiet_at"], row["reply_kind"]), (NOW, None, "interested"))


class Sweeper(OutcomeCase):
    def test_sweeper_marks_quiet_after_21_days_and_never_touches_a_touch_with_an_outcome(self):
        due = touch(self.conn, self.acme, self.lena, sent_at=DAY_21)
        young = touch(self.conn, self.acme, self.lena, sent_at=DAY_20)
        accepted = touch(self.conn, self.acme, self.lena, sent_at=DAY_25, accepted_at=DAY_20)
        replied = touch(self.conn, self.acme, self.lena, sent_at=DAY_25, replied_at=DAY_20, reply_kind="not_now")
        undone = touch(self.conn, self.acme, self.lena, sent_at=DAY_25, undone_at=DAY_21)
        app = touch(self.conn, self.acme, None, channel="application", sent_at=DAY_25)
        self.assertEqual(sweeper.sweep_quiet(self.conn, NOW, CFG), 2)
        self.assertEqual(self.row(due)["quiet_at"], NOW)
        self.assertEqual(self.row(app)["quiet_at"], NOW)
        for t in (young, accepted, replied, undone):
            self.assertIsNone(self.row(t)["quiet_at"], t)
            self.assertEqual(self.row(t)["revision"], 1, t)
        self.assertEqual({e["actor"] for e in self.events("quiet")}, {"system"})
        self.assertEqual(sweeper.sweep_quiet(self.conn, NOW, CFG), 0)
        self.assertFalse(self.conn.in_transaction)

    def test_a_sweep_on_a_busy_store_skips_the_pass_and_logs(self):
        touch(self.conn, self.acme, self.lena, sent_at=DAY_21)
        settings = dataclasses.replace(self.settings, busy_timeout_ms=50)
        holder = connect(settings.db_path)
        self.addCleanup(holder.close)
        holder.execute("BEGIN IMMEDIATE")
        try:
            with self.assertLogs("outreach.api", level="WARNING") as logs:
                self.assertIsNone(sweeper.sweep_pass(settings, NOW, CFG))
        finally:
            holder.execute("ROLLBACK")
        self.assertIn("busy", " ".join(logs.output))
        self.assertEqual(sweeper.sweep_pass(settings, NOW, CFG), 1)

    def test_the_app_runs_the_sweeper_at_startup_only_when_it_is_on(self):
        t = touch(self.conn, self.acme, self.lena, sent_at=DAY_21)
        with TestClient(self.app, base_url="http://127.0.0.1:3100"):
            pass
        self.assertIsNone(self.row(t)["quiet_at"])  # sweeper=False in tests
        self.settings = dataclasses.replace(self.settings, sweeper=True)
        self.build()
        with TestClient(self.app, base_url="http://127.0.0.1:3100"):
            deadline = time.monotonic() + 5
            while self.row(t)["quiet_at"] is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(self.row(t)["quiet_at"], NOW)
            self.assertTrue(any(not task.done() for task in self.app.state.sweeper_tasks))
        self.assertTrue(all(task.done() for task in self.app.state.sweeper_tasks))  # cancelled on shutdown


class Corrections(OutcomeCase):
    def test_correct_sets_and_clears_with_revision(self):
        t = touch(self.conn, self.acme, self.lena, accepted_at=DAY_6)
        res = self.correct(t, "accepted", "2026-11-08T10:00:00Z", 1)
        self.assertEqual((res.status_code, res.json()), (200, {"revision": 2}), res.text)
        self.assertEqual(self.row(t)["accepted_at"], "2026-11-08T10:00:00Z")
        stale = self.correct(t, "accepted", None, 1)
        self.assertEqual((stale.status_code, stale.json()["code"]), (409, "conflict"))
        cleared = self.correct(t, "accepted", None, 2)
        self.assertEqual((cleared.status_code, cleared.json()), (200, {"revision": 3}))
        self.assertIsNone(self.row(t)["accepted_at"])
        corrected = self.events("corrected")
        self.assertEqual([e["at"] for e in corrected], [NOW, NOW])
        self.assertEqual(json.loads(corrected[1]["detail"])["new"], None)

    def test_correct_a_reply_kind(self):
        t = touch(self.conn, self.acme, self.lena, replied_at=DAY_6, reply_kind="unsorted")
        res = self.correct(t, "replied", DAY_6, 1, reply_kind="not_now")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.row(t)["reply_kind"], "not_now")

    def test_correct_before_sent_is_refused_with_a_plain_message(self):
        t = touch(self.conn, self.acme, self.lena)
        res = self.correct(t, "accepted", "2026-10-01T10:00:00Z", 1)
        self.assertEqual(res.status_code, 400, res.text)
        self.assertEqual(res.json()["code"], "bad_request")
        self.assertIn("before", res.json()["message"])
        self.assertIn("Nothing was saved", res.json()["message"])
        self.assertIsNone(self.row(t)["accepted_at"])
        self.assertEqual(self.events(), [])

    def test_a_check_violation_from_the_store_is_bad_request_not_a_500(self):
        t = touch(self.conn, self.acme, self.lena)
        refused = sqlite3.IntegrityError("CHECK constraint failed: accepted_at IS NULL OR accepted_at >= sent_at")
        with mock.patch.object(outcome_queries, "correct_outcome", side_effect=refused):
            res = self.correct(t, "accepted", NOW, 1)
        self.assertEqual(res.status_code, 400, res.text)
        self.assertEqual(res.json()["code"], "bad_request")
        self.assertNotIn("CHECK", res.json()["message"])
        self.assertIn("Nothing was saved", res.json()["message"])

    def test_a_correction_to_a_stop_reply_applies_the_same_effects(self):
        pat, email = self.emailed()
        approved = s.message(self.conn, email, 1, "approved")
        drafted = s.message(self.conn, email, 2, "drafted")
        planned = planned_followup(self.conn, email, seq=3, due_on="2026-11-20")
        t = touch(self.conn, self.acme, pat, channel="email", email_touch_id=email, replied_at=DAY_6,
                  reply_kind="interested")
        res = self.correct(t, "replied", DAY_6, 1, reply_kind="asked_to_stop")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.row(t)["reply_kind"], "asked_to_stop")
        self.assertEqual([tuple(r) for r in self.conn.execute("SELECT person_id, reason, source FROM no_go")],
                         [(pat, "stop_reply", "reply")])
        self.assertEqual((self.state_of(approved), self.state_of(drafted), self.state_of(planned)),
                         ("skipped", "skipped", None))
        self.assertFalse(self.sendable(approved))

    def test_a_correction_to_an_interested_reply_stops_nothing(self):
        pat, email = self.emailed()
        approved = s.message(self.conn, email, 1, "approved")
        t = touch(self.conn, self.acme, pat, channel="email", email_touch_id=email, replied_at=DAY_6,
                  reply_kind="unsorted")
        self.assertEqual(self.correct(t, "replied", DAY_6, 1, reply_kind="interested").status_code, 200)
        self.assertEqual(self.state_of(approved), "approved")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM no_go").fetchone()[0], 0)

    def test_an_integrity_error_outside_an_outcome_write_is_a_logged_500(self):
        refused = sqlite3.IntegrityError("UNIQUE constraint failed: outreach_touch.idempotency_key")
        with mock.patch.object(outcome_queries, "log_touch", side_effect=refused):
            with self.assertLogs("outreach.api", level="ERROR"):
                res = self.post("/api/touches", {"channel": "other", "companyId": self.acme, "personId": self.lena,
                                                 "idempotencyKey": "other:boom"})
        self.assertEqual(res.status_code, 500, res.text)
        self.assertEqual(res.json()["code"], "server_error")

    def test_store_refusal_maps_only_constraint_errors(self):
        problem = outcome_queries.store_refusal(sqlite3.IntegrityError("CHECK constraint failed: x"))
        self.assertIsInstance(problem, ApiProblem)
        self.assertEqual(problem.code, "bad_request")
        self.assertEqual(outcome_queries.store_refusal(sqlite3.IntegrityError("FOREIGN KEY constraint failed")).code,
                         "bad_request")

    def test_an_empty_date_is_bad_request(self):
        t = touch(self.conn, self.acme, self.lena, accepted_at=DAY_6)
        res = self.correct(t, "accepted", "", 1)
        self.assertEqual(res.status_code, 400, res.text)
        self.assertEqual(self.row(t)["accepted_at"], DAY_6)


class History(OutcomeCase):
    def test_history_lists_events_newest_first(self):
        logged = self.post("/api/touches", {"channel": "other", "companyId": self.acme, "personId": self.lena,
                                            "idempotencyKey": "other:lena:1"})
        self.assertEqual(logged.status_code, 200, logged.text)
        t = logged.json()["touchId"]
        self.set_now("2026-11-11T15:00:00Z")
        self.assertEqual(self.answer(t, "accepted", 1).status_code, 200)
        self.set_now("2026-11-12T15:00:00Z")
        self.assertEqual(self.correct(t, "accepted", "2026-11-11T09:00:00Z", 2).status_code, 200)
        res = self.get(f"/api/touches/{t}/history")
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["touchId"], t)
        self.assertEqual(body["channel"], "other")
        self.assertEqual(body["person"]["id"], self.lena)
        self.assertEqual(body["company"]["id"], self.acme)
        self.assertEqual(body["sentAt"], NOW)
        self.assertEqual(body["accepted"], "2026-11-11T09:00:00Z")
        for name in ("replied", "replyKind", "referred", "interview", "offer", "quiet"):
            self.assertIsNone(body[name], name)
        self.assertEqual(body["revision"], 3)
        self.assertEqual([e["kind"] for e in body["events"]], ["corrected", "accepted", "sent"])
        self.assertEqual([e["at"] for e in body["events"]],
                         ["2026-11-12T15:00:00Z", "2026-11-11T15:00:00Z", NOW])
        self.assertTrue(all(e["text"] for e in body["events"]))


class LogTouch(OutcomeCase):
    def log(self, channel: str = "hn_reply", key: str = "hn:4711:lena", **extra):
        body = {"channel": channel, "companyId": self.acme, "personId": self.lena, "idempotencyKey": key, **extra}
        return self.post("/api/touches", body)

    def test_log_an_hn_reply_touch_once_per_key_with_context_source_own(self):
        res = self.log()
        self.assertEqual(res.status_code, 200, res.text)
        t = res.json()["touchId"]
        self.assertEqual(res.json(), {"touchId": t, "revision": 1})
        again = self.log()  # a new request id, the same touch key: the touch that has it
        self.assertEqual((again.status_code, again.json()), (200, {"touchId": t, "revision": 1}))
        rows = self.conn.execute("SELECT * FROM outreach_touch").fetchall()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["channel"], row["person_id"], row["company_id"], row["sent_at"]),
                         ("hn_reply", self.lena, self.acme, NOW))
        ctx = json.loads(row["context"])
        self.assertEqual(ctx["source"], "own")
        self.assertEqual(ctx["lane"], "own")  # no score yet
        self.assertEqual(ctx["roleKind"], "engineer")
        self.assertFalse(ctx["roleNamed"])
        self.assertEqual(len(self.events("sent")), 1)

    def test_logged_touch_takes_the_lane_and_signals_of_the_newest_score(self):
        score(self.conn, self.acme, ("intern_junior",), version="r1", scored_at="2026-11-01T06:00:00Z")
        score(self.conn, self.acme, ("level_role", "any_us_role"), version="r2", scored_at="2026-11-09T06:00:00Z",
              values={"everify": 1.0, "recent_funding": 0.5})
        posting = s.posting(self.conn, self.acme, first_seen_at="2026-11-07T12:00:00Z")
        res = self.log(postingId=posting)
        self.assertEqual(res.status_code, 200, res.text)
        ctx = json.loads(self.row(res.json()["touchId"])["context"])
        self.assertEqual((ctx["lane"], ctx["rankerVersion"]), ("level_role", "r2"))
        self.assertEqual(ctx["signals"], {"everify": 1.0, "recent_funding": 0.5})
        self.assertTrue(ctx["roleNamed"])
        self.assertEqual(ctx["roleAgeDays"], 3)

    def test_log_touch_refuses_the_other_channels(self):
        for channel in ("linkedin_request", "linkedin_message", "email", "application"):
            with self.subTest(channel=channel):
                res = self.log(channel, key=f"k:{channel}")
                self.assertEqual(res.status_code, 400, res.text)
                self.assertEqual(res.json()["code"], "bad_request")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM outreach_touch").fetchone()[0], 0)

    def test_a_logged_touch_needs_a_person_at_that_company(self):
        res = self.post("/api/touches", {"channel": "other", "companyId": self.acme, "idempotencyKey": "other:1"})
        self.assertEqual((res.status_code, res.json()["code"]), (400, "bad_request"), res.text)
        elsewhere = s.company(self.conn, "Bolt")
        res = self.post("/api/touches", {"channel": "other", "companyId": elsewhere, "personId": self.lena,
                                         "idempotencyKey": "other:2"})
        self.assertEqual((res.status_code, res.json()["code"]), (400, "bad_request"), res.text)
        self.assertEqual(self.log(key="other:3", companyId=999).status_code, 404)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM outreach_touch").fetchone()[0], 0)


class CompanyHold(OutcomeCase):
    """#9: a reply from anyone at the company, on any channel, holds its queued email. The queue and the claim's
    stop re-check read the same hold."""

    def setUp(self):
        super().setUp()
        from outreach.sending.config import load_config as load_send_config
        self.send_cfg = load_send_config()
        self.colleague = s.person(self.conn, self.acme, "Sam Park")
        self.queued = s.message(self.conn, s.touch(self.conn, self.colleague), 0, "approved")

    def verdict(self) -> str | None:
        from datetime import datetime, timezone
        now = datetime.strptime(NOW, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        found = {v.message_id: v.reason for v in queue.verdicts(self.conn, now, self.send_cfg, day=None, gate=None)}
        return found.get(self.queued)

    def claimable(self) -> bool:
        """Whether the claim's own stop re-check would let the queued email go (it never reaches Gmail here)."""
        from outreach.sending import claim
        q = next(q for q in queue.load_queue(self.conn, self.send_cfg) if q.message_id == self.queued)
        taken = claim.claim(self.conn, q, rfc_id="<hold.test@gmail.com>", now=NOW)
        if taken:  # put it back: this test only asks whether the claim would go ahead
            self.conn.execute("UPDATE message SET state = 'send_unknown' WHERE message_id = ?", (self.queued,))
        return taken

    def test_a_linkedin_reply_recorded_through_the_api_holds_the_companys_queued_email(self):
        self.assertNotEqual(self.verdict(), "company_held")
        t = touch(self.conn, self.acme, self.lena, channel="linkedin_message")
        self.assertEqual(self.answer(t, "replied", reply_kind="interested").status_code, 200)
        self.assertEqual(self.verdict(), "company_held")
        self.assertFalse(self.claimable())
        self.assertEqual(self.state_of(self.queued), "approved")

    def test_a_reply_at_a_merged_duplicate_holds_the_survivors_queued_email(self):
        duplicate = s.company(self.conn, "Acme Labs", "removed", remove_reason="merged", merged_into=self.acme)
        pid = s.person(self.conn, duplicate, "Ira Dup", channel="linkedin", linkedin_url="https://www.linkedin.com/in/ira")
        t = touch(self.conn, duplicate, pid)
        self.assertEqual(self.answer(t, "replied", reply_kind="not_now").status_code, 200)
        self.assertEqual(self.verdict(), "company_held")

    def test_an_undone_reply_does_not_hold(self):
        t = touch(self.conn, self.acme, self.lena)
        self.assertEqual(self.answer(t, "replied", reply_kind="interested").status_code, 200)
        self.assertEqual(self.verdict(), "company_held")
        self.assertEqual(self.correct(t, "replied", None, 2).status_code, 200)
        self.assertNotEqual(self.verdict(), "company_held")
        self.assertTrue(self.claimable())

    def test_an_auto_reply_does_not_hold(self):
        t = touch(self.conn, self.acme, self.lena)
        res = self.answer(t, "replied", reply_kind="auto_reply")
        self.assertIn(res.status_code, (400, 422), res.text)
        self.assertIsNone(self.row(t)["replied_at"])
        self.assertNotEqual(self.verdict(), "company_held")

    def test_a_stop_reply_leaves_no_follow_up_sendable_by_the_queues_verdicts(self):
        from outreach.drafts.followups import due_followups
        pat, email = self.emailed()
        approved = s.message(self.conn, email, 1, "approved")
        planned = planned_followup(self.conn, email, seq=2, due_on="2026-11-14")
        t = touch(self.conn, self.acme, pat, channel="email", email_touch_id=email)
        self.assertEqual(self.answer(t, "replied", reply_kind="asked_to_stop").status_code, 200)
        from datetime import datetime, timezone
        now = datetime.strptime(NOW, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        reasons = {v.message_id: v.reason for v in queue.verdicts(self.conn, now, self.send_cfg, day=None, gate=None)}
        self.assertNotIn(approved, reasons)
        self.assertNotEqual(reasons.get(self.queued), "ready")  # the stop reply also holds the company
        self.assertIsNone(self.state_of(planned))
        self.assertEqual(due_followups(self.conn, now.date(), days_ahead=30), [])
