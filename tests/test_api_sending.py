"""Task 18: the sending status and the global pause.

`GET /api/sending` is the sender's own snapshot (`outreach.sending.status.sending_snapshot`) on the wire, so the
strip says what the sender would do. A pause is his; a plain resume clears only his pause. A hard block (a Gmail
limit, a hard bounce rate, a rewritten Message-ID) needs `acknowledgeBlock`, and the refusal shows its reason.
"""
import json
import unittest
from unittest import mock

from outreach.sending.status import OLD_SENDER_TEXT
from outreach.store.writes import append_event
from tests import apiseed as seed
from tests.apitest import NOW, ApiCase

SATURDAY = "2026-11-14T15:14:00Z"
AFTER_HOURS = "2026-11-10T21:31:00Z"  # Tuesday 16:31 in New York, one minute after the window
TODAY = "2026-11-10"
HARD_REASON = "Gmail said the account hit a sending limit. Sending is paused until you look."


class SendingBase(ApiCase):
    def status(self) -> dict:
        res = self.get("/api/sending")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def sender(self, **cols):
        sets = ", ".join(f"{k} = ?" for k in cols)
        self.conn.execute(f"UPDATE sender SET {sets} WHERE sender_id = 1", tuple(cols.values()))

    def hard_block(self):
        self.sender(paused=1, pause_kind="hard_block", paused_reason=HARD_REASON, paused_at=NOW,
                    revision=3)

    def prove(self):
        append_event(self.conn, entity="message", entity_id=1, kind="import_verified", actor="sender",
                     at="2026-11-01T12:00:00Z", idempotency_key="proof")

    def approved(self, company: int, name: str, approved_at: str = seed.T0) -> tuple[int, int]:
        pid = seed.person(self.conn, company, name)
        tid = seed.touch(self.conn, pid)
        mid = seed.message(self.conn, tid, 0, "approved")
        self.conn.execute("UPDATE message SET approved_at = ? WHERE message_id = ?", (approved_at, mid))
        return tid, mid

    def sent_today(self, company: int, n: int, *, bounced: int = 0):
        for i in range(n):
            tid = seed.touch(self.conn, seed.person(self.conn, company, f"Sent {i}"))
            seed.send_first(self.conn, tid, at=f"2026-11-10T14:{i:02d}:00Z")
            if i < bounced:
                self.conn.execute("UPDATE touch SET state = 'bounced', bounced_at = ? WHERE touch_id = ?",
                                  (f"2026-11-10T15:0{i}:00Z", tid))

    def events(self, kind):
        return self.conn.execute("SELECT * FROM event WHERE entity = 'sender' AND kind = ? ORDER BY event_id",
                                 (kind,)).fetchall()


class StatusTest(SendingBase):
    def test_empty_store_is_a_running_status(self):
        s = self.status()
        self.assertEqual(s["state"], "running")
        self.assertEqual((s["sentToday"], s["limitToday"], s["rampLevel"], s["nextLimitIfClean"]), (0, 20, 1, 30))
        self.assertEqual(s["rampLevels"], [20, 30, 40, 50, 60, 70, 80, 90, 100])
        self.assertEqual(s["window"], {"start": "08:30", "end": "16:30"})
        self.assertEqual(s["signals"], {"bounceRate3d": 0.0, "gmailErrorsToday": 0, "stopRepliesToday": 0})
        self.assertEqual((s["nextSend"], s["noneReason"]), (None, "Nothing is approved and waiting to send."))
        self.assertEqual((s["pausedReason"], s["pausedAt"], s["gmailMessage"], s["lastStepDown"]), ("", None, None, None))
        self.assertEqual(s["revision"], 1)

    def test_sending_state_per_clock_and_pause_kind(self):
        self.set_now(SATURDAY)
        self.assertEqual(self.status()["state"], "weekend")
        self.set_now(AFTER_HOURS)
        self.assertEqual(self.status()["state"], "outside_window")
        self.set_now(NOW)
        self.sender(paused=1, pause_kind="by_you", paused_reason="Paused by you", paused_at=NOW)
        s = self.status()
        self.assertEqual((s["state"], s["pausedReason"], s["pausedAt"]), ("paused_by_you", "Paused by you", NOW))
        self.hard_block()
        s = self.status()
        self.assertEqual((s["state"], s["pausedReason"]), ("paused_hard_block", HARD_REASON))

    def test_the_old_sender_still_loaded_reads_as_paused_with_its_reason(self):
        self.old_sender = True
        s = self.status()
        self.assertEqual((s["state"], s["pausedReason"]), ("paused_by_you", OLD_SENDER_TEXT))

    def test_ramp_level_and_next_limit_if_clean(self):
        for limit, level, nxt in ((40, 3, 50), (100, 9, 100)):
            with self.subTest(limit=limit):
                self.conn.execute("INSERT INTO send_day(day, send_limit, created_at) VALUES (?, ?, ?) "
                                  "ON CONFLICT(day) DO UPDATE SET send_limit = excluded.send_limit",
                                  (TODAY, limit, NOW))
                s = self.status()
                self.assertEqual((s["rampLevel"], s["nextLimitIfClean"], s["limitToday"]), (level, nxt, limit))

    def test_last_step_down_is_the_newest_warning_day_with_its_reason(self):
        self.conn.execute("INSERT INTO send_day(day, send_limit, outcome, created_at) VALUES "
                          "('2026-11-06', 30, 'warning', '2026-11-06T13:00:00Z')")
        append_event(self.conn, entity="sender", entity_id=1, kind="policy_rejection", actor="sender",
                     at="2026-11-06T16:20:00Z", detail={"day": "2026-11-06"}, idempotency_key="w1")
        self.assertEqual(self.status()["lastStepDown"], {"at": "2026-11-06T16:20:00Z", "reason": "policy_rejection"})

    def test_bounce_rate_over_three_days_and_zero_when_nothing_sent(self):
        self.assertEqual(self.status()["signals"]["bounceRate3d"], 0.0)
        cid = seed.company(self.conn, "Acme", status="working")
        self.sent_today(cid, 4, bounced=1)
        self.assertEqual(self.status()["signals"]["bounceRate3d"], 0.25)
        self.assertEqual(self.status()["sentToday"], 4)

    def test_gmail_error_and_stop_reply_counts_come_from_sender_events(self):
        for n, kind in enumerate(("gmail_limit", "gmail_policy")):
            append_event(self.conn, entity="sender", entity_id=1, kind=kind, actor="sender", at=NOW,
                         detail={"day": TODAY, "error_kind": "rate_limit", "status": 429}, idempotency_key=f"g{n}")
        append_event(self.conn, entity="sender", entity_id=1, kind="gmail_limit", actor="sender",
                     at="2026-11-09T15:00:00Z", detail={"day": "2026-11-09"}, idempotency_key="yesterday")
        cid = seed.company(self.conn, "Acme", status="working")
        tid = seed.touch(self.conn, seed.person(self.conn, cid), "replied", reply_kind="asked_to_stop",
                         replied_at="2026-11-10T14:00:00Z")
        seed.send_first(self.conn, tid)
        s = self.status()
        self.assertEqual((s["signals"]["gmailErrorsToday"], s["signals"]["stopRepliesToday"]), (2, 1))
        self.assertIn("Gmail said this account may not send", s["gmailMessage"])

    def test_next_send_skips_paused_and_held_companies(self):
        self.prove()
        paused = seed.company(self.conn, "Paused Co", status="working", paused_at=NOW)
        held = seed.company(self.conn, "Held Co", status="working")
        free = seed.company(self.conn, "Free Co", status="working")
        self.approved(paused, "Pia Paused", "2026-11-01T10:00:00Z")
        holder_tid = seed.touch(self.conn, seed.person(self.conn, held, "Hal Holder"), "replied")
        self.conn.execute("UPDATE company SET held_by_touch = ?, held_at = ? WHERE company_id = ?",
                          (holder_tid, NOW, held))
        self.approved(held, "Hil Held", "2026-11-01T10:30:00Z")
        tid, _ = self.approved(free, "Fay Free", "2026-11-01T11:00:00Z")
        s = self.status()
        self.assertEqual(s["nextSend"], {"at": NOW, "tz": "America/New_York", "touchId": tid,
                                         "personName": "Fay Free", "companyName": "Free Co"})
        self.conn.execute("UPDATE company SET paused_at = ? WHERE company_id = ?", (NOW, free))
        s = self.status()
        self.assertIsNone(s["nextSend"])
        self.assertTrue(s["noneReason"])

    def test_a_broken_sending_config_reads_as_stopped_in_health_not_a_500(self):
        with mock.patch("outreach.api.queries.sending.load_config", side_effect=ValueError("ramp_step is wrong")):
            res = self.get("/api/health")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["sender"], {"state": "paused_hard_block", "sentToday": 0, "limitToday": 0})

    def test_health_reads_the_same_snapshot(self):
        self.hard_block()
        health = self.get("/api/health").json()["sender"]
        s = self.status()
        self.assertEqual(health, {"state": s["state"], "sentToday": s["sentToday"], "limitToday": s["limitToday"]})


class PauseResumeTest(SendingBase):
    def test_pause_then_resume_and_stale_sender_revision_conflicts(self):
        res = self.post("/api/sending/pause", {"revision": 1}, key="pause-sending-1")
        self.assertEqual(res.status_code, 200, res.text)
        s = res.json()
        self.assertEqual((s["state"], s["pausedReason"], s["pausedAt"], s["revision"]),
                         ("paused_by_you", "Paused by you", NOW, 2))
        row = self.conn.execute("SELECT * FROM sender").fetchone()
        self.assertEqual((row["paused"], row["pause_kind"]), (1, "by_you"))
        [event] = self.events("paused")
        self.assertEqual((event["actor"], event["idempotency_key"]), ("devanshu", "pause-sending-1:0"))

        stale = self.post("/api/sending/resume", {"revision": 1})
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["code"], "conflict")
        self.assertEqual(self.conn.execute("SELECT paused FROM sender").fetchone()[0], 1)

        res = self.post("/api/sending/resume", {"revision": 2})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual((res.json()["state"], res.json()["revision"]), ("running", 3))
        self.assertEqual(len(self.events("resumed")), 1)

        stale = self.post("/api/sending/pause", {"revision": 2})
        self.assertEqual(stale.json()["code"], "conflict")
        self.assertEqual(self.conn.execute("SELECT paused FROM sender").fetchone()[0], 0)

    def test_resume_after_a_hard_block_needs_the_acknowledgement(self):
        self.hard_block()
        res = self.post("/api/sending/resume", {"revision": 3})
        self.assertEqual(res.status_code, 409, res.text)
        self.assertEqual(res.json()["code"], "not_allowed")
        self.assertIn(HARD_REASON, res.json()["message"])  # the reason is shown before he confirms
        row = self.conn.execute("SELECT * FROM sender").fetchone()
        self.assertEqual((row["paused"], row["pause_kind"], row["revision"]), (1, "hard_block", 3))
        self.assertEqual(self.events("resumed"), [])

        res = self.post("/api/sending/resume", {"revision": 3, "acknowledgeBlock": True}, key="ack-resume-1")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual((res.json()["state"], res.json()["pausedReason"]), ("running", ""))
        [event] = self.events("resumed")
        detail = json.loads(event["detail"])
        self.assertEqual((detail["cleared"], detail["acknowledged"], detail["reason"]), ("hard_block", True, HARD_REASON))

    def test_a_pause_never_overwrites_a_hard_block(self):
        self.hard_block()
        res = self.post("/api/sending/pause", {"revision": 3})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["state"], "paused_hard_block")
        self.assertEqual(self.conn.execute("SELECT pause_kind, revision FROM sender").fetchone()[:], ("hard_block", 3))
        self.assertEqual(self.post("/api/sending/resume", {"revision": 3}).json()["code"], "not_allowed")

    def test_pause_when_already_paused_writes_nothing(self):
        self.sender(paused=1, pause_kind="by_you", paused_reason="Paused by you", paused_at=NOW, revision=2)
        res = self.post("/api/sending/pause", {"revision": 1})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual((res.json()["state"], res.json()["revision"]), ("paused_by_you", 2))
        self.assertEqual(self.events("paused"), [])

    def test_resume_when_running_writes_nothing(self):
        res = self.post("/api/sending/resume", {"revision": 1})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual((res.json()["state"], res.json()["revision"]), ("running", 1))
        self.assertEqual(self.events("resumed"), [])
        self.assertEqual(self.post("/api/sending/resume", {"revision": 4}).json()["code"], "conflict")

    def test_double_click_pause_writes_once(self):
        first = self.post("/api/sending/pause", {"revision": 1}, key="double-pause-1")
        again = self.post("/api/sending/pause", {"revision": 1}, key="double-pause-1")
        self.assertEqual(again.json(), first.json())
        self.assertEqual(again.headers.get("Idempotent-Replay"), "true")
        self.assertEqual(len(self.events("paused")), 1)
        self.assertEqual(self.conn.execute("SELECT revision FROM sender").fetchone()[0], 2)

    def test_a_write_without_a_revision_is_bad_request(self):
        self.assertEqual(self.post("/api/sending/pause", {}).status_code, 400)
        self.assertEqual(self.post("/api/sending/resume", {"acknowledgeBlock": True}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
