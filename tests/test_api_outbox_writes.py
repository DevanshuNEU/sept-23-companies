"""Task 19: the Outbox writes other than approve (save, unapprove, skip, the reply kind) and the release of a hold.

A said-no or asked-to-stop reply kind reuses the outcome writes' own reply effects (`apply_reply_effects`). An
auto-reply puts the thread back to open and its hold drops, because an auto-reply never counts as a reply. A release
appends `hold_released` on every company of the merged family and clears `held_by_touch` there: exactly what the
sender's `held_survivors` reads.
"""
import dataclasses
import json
import unittest
from datetime import datetime, timezone

from outreach.drafts.followups import company_map, held_survivors, replied_people
from outreach.sending.config import load_config
from outreach.sending.status import queue_schedule
from outreach.store.writes import append_event, record_outcome
from tests import apiseed as seed
from tests.apitest import NOW, ApiCase
from tests.test_api_approve import BODY, CONTEXT, FIXTURE_BANNED, SUBJECT

CFG = load_config()


def verdicts(conn) -> dict[int, str]:
    when = datetime.strptime(NOW, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return {mid: v.reason for mid, v in queue_schedule(conn, when, CFG).items()}


class WritesBase(ApiCase):
    def setUp(self):
        super().setUp()
        self.settings = dataclasses.replace(self.settings, banned_phrases=FIXTURE_BANNED)
        self.build()
        self.acme = seed.company(self.conn, "Acme", status="working")
        self.pat = seed.person(self.conn, self.acme, "Pat Lee", email="pat@acme.example")
        self.touch = seed.touch(self.conn, self.pat)
        append_event(self.conn, entity="message", entity_id=1, kind="marker_verified", actor="sender",
                     at="2026-11-01T12:00:00Z", idempotency_key="proof")

    def message(self, mid):
        return self.conn.execute("SELECT * FROM message WHERE message_id = ?", (mid,)).fetchone()

    def touch_row(self, tid=None):
        return self.conn.execute("SELECT * FROM touch WHERE touch_id = ?", (tid or self.touch,)).fetchone()

    def approved_colleague(self, company=None, name="Lena Szabo") -> tuple[int, int]:
        pid = seed.person(self.conn, company or self.acme, name)
        tid = seed.touch(self.conn, pid)
        return tid, seed.message(self.conn, tid, 0, "approved")

    def replied_by_email(self, kind="unsorted") -> None:
        """Pat replied by email, as the sender records it: the touch, its outcome row, and the company hold."""
        seed.send_first(self.conn, self.touch, at="2026-11-05T15:00:00Z")
        self.conn.execute("UPDATE touch SET state = 'replied', replied_at = ?, reply_kind = ? WHERE touch_id = ?",
                          ("2026-11-09T10:00:00Z", kind, self.touch))
        append_event(self.conn, entity="touch", entity_id=self.touch, kind="replied", actor="sender",
                     at="2026-11-09T10:00:00Z", idempotency_key="replied-1")
        otid = self.conn.execute(
            "INSERT INTO outreach_touch(person_id, company_id, channel, email_touch_id, sent_at, context, "
            "idempotency_key, created_at) VALUES (?, ?, 'email', ?, '2026-11-05T15:00:00Z', ?, 'email-1', ?)",
            (self.pat, self.acme, self.touch, json.dumps(CONTEXT), seed.T0)).lastrowid
        record_outcome(self.conn, otid, kind="replied", at="2026-11-09T10:00:00Z", revision=1, actor="sender")
        self.conn.execute("UPDATE company SET held_by_touch = ?, held_at = ? WHERE company_id = ?",
                          (self.touch, NOW, self.acme))

    def held(self) -> set[int]:
        return held_survivors(company_map(self.conn))

    def revision(self, cid=None):
        return self.conn.execute("SELECT revision FROM company WHERE company_id = ?", (cid or self.acme,)).fetchone()[0]


class SaveTest(WritesBase):
    def test_save_rechecks_and_stores_checks(self):
        mid = seed.message(self.conn, self.touch, 0, subject=SUBJECT, body=BODY)
        res = self.post(f"/api/messages/{mid}/save",
                        {"subject": SUBJECT, "body": BODY.replace("Thanks,", "TODO Thanks,"), "revision": 1},
                        key="save-key-1")
        self.assertEqual(res.status_code, 200, res.text)
        detail = res.json()
        self.assertEqual((detail["item"]["revision"], detail["savedBy"]), (2, "devanshu"))
        self.assertIn("TODO", detail["body"])
        row = self.message(mid)
        self.assertEqual((row["state"], row["saved_by"], row["revision"]), ("drafted", "devanshu", 2))
        self.assertIn("placeholder", [c["code"] for c in json.loads(row["checks"]) if c["severity"] == "block"])
        self.assertEqual(detail["item"]["worst"], "block")
        [event] = self.conn.execute("SELECT * FROM event WHERE kind = 'saved'").fetchall()
        self.assertEqual((event["actor"], event["idempotency_key"]), ("devanshu", "save-key-1:0"))

    def test_save_in_approved_state_is_not_allowed(self):
        mid = seed.message(self.conn, self.touch, 0, "approved")
        res = self.post(f"/api/messages/{mid}/save", {"subject": "New", "body": "New body", "revision": 1})
        self.assertEqual(res.status_code, 409, res.text)
        self.assertEqual(res.json()["code"], "not_allowed")
        self.assertEqual(self.message(mid)["body"], "Hi there")

    def test_save_stale_revision_conflicts(self):
        mid = seed.message(self.conn, self.touch, 0)
        res = self.post(f"/api/messages/{mid}/save", {"subject": "New", "body": "New body", "revision": 4})
        self.assertEqual(res.json()["code"], "conflict")

    def test_save_an_empty_body_is_bad_request(self):
        mid = seed.message(self.conn, self.touch, 0)
        self.assertEqual(self.post(f"/api/messages/{mid}/save", {"subject": "S", "body": "", "revision": 1})
                         .status_code, 400)


class UnapproveSkipTest(WritesBase):
    def test_unapprove_moves_approved_back_to_drafted(self):
        mid = seed.message(self.conn, self.touch, 0, "approved")
        res = self.post(f"/api/messages/{mid}/unapprove", {"revision": 1})
        self.assertEqual(res.status_code, 200, res.text)
        row = self.message(mid)
        self.assertEqual((row["state"], row["approved_at"], row["revision"]), ("drafted", None, 2))
        self.assertEqual(res.json()["item"]["group"], "review")

    def test_unapprove_after_claim_is_not_allowed(self):
        mid = seed.message(self.conn, self.touch, 0, "approved")
        self.conn.execute("UPDATE message SET state = 'sending', pending_rfc_id = '<p@x>', claimed_at = ? "
                          "WHERE message_id = ?", (NOW, mid))
        for state in ("sending", "send_unknown"):
            with self.subTest(state=state):
                self.conn.execute("UPDATE message SET state = ? WHERE message_id = ?", (state, mid))
                res = self.post(f"/api/messages/{mid}/unapprove", {"revision": 1})
                self.assertEqual(res.status_code, 409, res.text)
                self.assertEqual(res.json()["code"], "not_allowed")
                self.assertEqual(res.json()["message"], "It is already being sent or was sent.")
        sent = seed.send_first(self.conn, seed.touch(self.conn, seed.person(self.conn, self.acme, "Sue")))
        self.assertEqual(self.post(f"/api/messages/{sent}/unapprove", {"revision": 1}).json()["message"],
                         "It is already being sent or was sent.")

    def test_skip_a_draft_closes_a_thread_that_never_went_out(self):
        mid = seed.message(self.conn, self.touch, 0)
        res = self.post(f"/api/messages/{mid}/skip", {"revision": 1})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.message(mid)["state"], "skipped")
        touch = self.touch_row()
        self.assertEqual((touch["state"], touch["close_reason"]), ("closed", "skipped"))
        self.assertEqual(res.json()["item"]["group"], "closed")

    def test_skip_a_planned_follow_up_goes_through_the_senders_stop(self):
        seed.send_first(self.conn, self.touch)
        planned = self.conn.execute(
            "INSERT INTO message(touch_id, seq, state, due_on, saved_by, created_at, updated_at) "
            "VALUES (?, 1, 'planned', '2026-11-16', 'sender', ?, ?)", (self.touch, seed.T0, seed.T0)).lastrowid
        res = self.post(f"/api/messages/{planned}/skip", {"revision": 1})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertIsNone(self.message(planned))
        [event] = self.conn.execute("SELECT * FROM event WHERE kind = 'skipped_by_sender'").fetchall()
        self.assertEqual((event["entity"], event["actor"]), ("touch", "devanshu"))
        self.assertEqual(self.touch_row()["state"], "open")  # the first email went out; the thread stays open

    def test_skip_in_sending_and_send_unknown_is_not_allowed(self):
        mid = seed.message(self.conn, self.touch, 0, "approved")
        self.conn.execute("UPDATE message SET state = 'sending', pending_rfc_id = '<p@x>', claimed_at = ? "
                          "WHERE message_id = ?", (NOW, mid))
        for state in ("sending", "send_unknown"):
            with self.subTest(state=state):
                self.conn.execute("UPDATE message SET state = ? WHERE message_id = ?", (state, mid))
                res = self.post(f"/api/messages/{mid}/skip", {"revision": 1})
                self.assertEqual(res.json()["code"], "not_allowed")
                self.assertEqual(self.message(mid)["state"], state)

    def test_unknown_message_is_404(self):
        for verb in ("save", "unapprove", "skip"):
            body = {"subject": "S", "body": "B", "revision": 1} if verb == "save" else {"revision": 1}
            self.assertEqual(self.post(f"/api/messages/999/{verb}", body).status_code, 404)


class ReplyKindTest(WritesBase):
    def test_reply_kind_said_no_adds_person_no_go_and_blocks_a_second_draft_to_them(self):
        self.replied_by_email()
        follow = seed.message(self.conn, self.touch, 1, "drafted", subject="Re: Hello", body="Hi Pat, one more.")
        res = self.post(f"/api/touches/{self.touch}/reply-kind", {"kind": "said_no", "revision": 1}, key="kind-key-1")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["reply"]["kind"], "said_no")
        touch = self.touch_row()
        self.assertEqual((touch["state"], touch["reply_kind"], touch["revision"]), ("replied", "said_no", 2))
        [no_go] = self.conn.execute("SELECT * FROM no_go").fetchall()
        self.assertEqual((no_go["person_id"], no_go["reason"], no_go["source"]), (self.pat, "said_no", "reply"))
        self.assertEqual(self.message(follow)["state"], "skipped")  # the reply effects stop the unsent email
        outcome = self.conn.execute("SELECT reply_kind FROM outreach_touch WHERE email_touch_id = ?",
                                    (self.touch,)).fetchone()
        self.assertEqual(outcome["reply_kind"], "said_no")

        # A new draft to Pat is blocked by the no-go: check it the way approve does.
        res = self.post(f"/api/messages/{follow}/approve", {"revision": self.message(follow)["revision"]})
        self.assertEqual(res.json()["code"], "not_allowed")  # it is skipped now
        mid = seed.message(self.conn, self.touch, 2, "drafted", subject="Re: Hello", body=BODY)
        res = self.post(f"/api/messages/{mid}/approve", {"revision": 1})
        self.assertEqual(res.status_code, 422, res.text)
        blocks = {c["code"] for c in res.json()["checks"] if c["severity"] == "block"}
        self.assertTrue({"no_go_person", "said_no"} <= blocks)

    def test_asked_to_stop_also_writes_the_no_go(self):
        self.replied_by_email()
        res = self.post(f"/api/touches/{self.touch}/reply-kind", {"kind": "asked_to_stop", "revision": 1})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.conn.execute("SELECT reason FROM no_go").fetchone()[0], "stop_reply")

    def test_a_human_kind_keeps_the_hold(self):
        self.replied_by_email()
        self.post(f"/api/touches/{self.touch}/reply-kind", {"kind": "interested", "revision": 1})
        self.assertIn(self.acme, self.held())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM no_go").fetchone()[0], 0)

    def test_auto_reply_reopens_the_touch_and_clears_the_hold(self):
        self.replied_by_email()
        _, lena = self.approved_colleague()
        self.assertIn(self.acme, self.held())
        self.assertEqual(verdicts(self.conn)[lena], "company_held")
        res = self.post(f"/api/touches/{self.touch}/reply-kind", {"kind": "auto_reply", "revision": 1})
        self.assertEqual(res.status_code, 200, res.text)
        touch = self.touch_row()
        self.assertEqual((touch["state"], touch["reply_kind"]), ("open", "auto_reply"))
        self.assertIsNone(self.conn.execute("SELECT held_by_touch FROM company WHERE company_id = ?",
                                            (self.acme,)).fetchone()[0])
        self.assertNotIn(self.acme, self.held())  # an auto-reply never counts as a reply
        self.assertNotIn(self.pat, replied_people(self.conn))
        self.assertNotEqual(verdicts(self.conn)[lena], "company_held")
        self.assertEqual(res.json()["item"]["touchState"], "open")

        # Re-marked as a person's reply: the outcome row has its reply again, and the company is held again.
        res = self.post(f"/api/touches/{self.touch}/reply-kind", {"kind": "interested", "revision": 2})
        self.assertEqual(res.status_code, 200, res.text)
        outcome = self.conn.execute("SELECT replied_at, reply_kind FROM outreach_touch WHERE email_touch_id = ?",
                                    (self.touch,)).fetchone()
        self.assertEqual(tuple(outcome), ("2026-11-09T10:00:00Z", "interested"))
        self.assertIn(self.acme, self.held())
        self.assertEqual(verdicts(self.conn)[lena], "company_held")

    def test_auto_reply_restores_a_bounced_or_closed_thread_and_its_stop(self):
        cases = (
            ("bounced", {"bounced_at": "2026-11-06T10:00:00Z"}, {"was": "bounced"}),  # the sender's own "was"
            ("closed", {"closed_at": "2026-11-07T10:00:00Z", "close_reason": "no_reply"}, None),  # columns only
        )
        for before, cols, detail in cases:
            with self.subTest(before=before):
                pid = seed.person(self.conn, self.acme, f"Was {before}")
                tid = seed.touch(self.conn, pid)
                seed.send_first(self.conn, tid)
                follow = seed.message(self.conn, tid, 1, "approved", subject="Re: Hello", body="Hi, one more.")
                sets = ", ".join(f"{k} = ?" for k in cols)
                self.conn.execute(f"UPDATE touch SET state = 'replied', replied_at = '2026-11-09T10:00:00Z', "
                                  f"reply_kind = 'unsorted', {sets} WHERE touch_id = ?", (*cols.values(), tid))
                if detail is not None:
                    append_event(self.conn, entity="touch", entity_id=tid, kind="replied", actor="sender",
                                 at="2026-11-09T10:00:00Z", detail=detail, idempotency_key=f"replied-{tid}")
                res = self.post(f"/api/touches/{tid}/reply-kind", {"kind": "auto_reply", "revision": 1})
                self.assertEqual(res.status_code, 200, res.text)
                touch = self.touch_row(tid)
                self.assertEqual((touch["state"], touch["reply_kind"]), (before, "auto_reply"))
                self.assertEqual(verdicts(self.conn)[follow], "touch_done")  # the queue's stop still applies

    def test_re_marking_an_auto_reply_as_human_holds_the_company_again(self):
        # An imported thread: no outcome row, no replied event, and a release on record from before.
        append_event(self.conn, entity="company", entity_id=self.acme, company_id=self.acme, kind="hold_released",
                     actor="devanshu", at="2026-11-08T10:00:00Z", idempotency_key="old-release")
        seed.send_first(self.conn, self.touch)
        self.conn.execute("UPDATE touch SET origin = 'import', state = 'open', replied_at = '2026-11-07T10:00:00Z', "
                          "reply_kind = 'auto_reply' WHERE touch_id = ?", (self.touch,))
        _, lena = self.approved_colleague()
        self.assertNotIn(self.acme, self.held())
        res = self.post(f"/api/touches/{self.touch}/reply-kind", {"kind": "interested", "revision": 1})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.touch_row()["state"], "replied")
        self.assertEqual(self.conn.execute("SELECT held_by_touch FROM company WHERE company_id = ?",
                                           (self.acme,)).fetchone()[0], self.touch)
        self.assertIn(self.acme, self.held())
        self.assertEqual(verdicts(self.conn)[lena], "company_held")

    def test_a_human_kind_change_never_re_holds_a_released_company(self):
        self.replied_by_email()
        self.assertEqual(self.post(f"/api/companies/{self.acme}/release-hold",
                                   {"revision": self.revision()}).status_code, 200)
        res = self.post(f"/api/touches/{self.touch}/reply-kind", {"kind": "not_now", "revision": 1})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertNotIn(self.acme, self.held())

    def test_the_same_kind_again_under_a_new_key_is_not_allowed_and_writes_nothing(self):
        self.replied_by_email(kind="interested")
        before = self.conn.execute("SELECT COUNT(*) FROM event").fetchone()[0]
        res = self.post(f"/api/touches/{self.touch}/reply-kind", {"kind": "interested", "revision": 1})
        self.assertEqual(res.status_code, 409, res.text)
        self.assertEqual(res.json()["code"], "not_allowed")
        self.assertEqual(self.touch_row()["revision"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM event").fetchone()[0], before)

    def test_reply_kind_on_a_touch_with_no_reply_is_not_allowed(self):
        seed.send_first(self.conn, self.touch)
        res = self.post(f"/api/touches/{self.touch}/reply-kind", {"kind": "interested", "revision": 1})
        self.assertEqual(res.status_code, 409, res.text)
        self.assertEqual(res.json()["code"], "not_allowed")

    def test_reply_kind_checks_the_touch_revision_and_refuses_unsorted(self):
        self.replied_by_email()
        self.assertEqual(self.post(f"/api/touches/{self.touch}/reply-kind",
                                   {"kind": "interested", "revision": 7}).json()["code"], "conflict")
        self.assertEqual(self.post(f"/api/touches/{self.touch}/reply-kind",
                                   {"kind": "unsorted", "revision": 1}).status_code, 400)
        self.assertEqual(self.post("/api/touches/999/reply-kind", {"kind": "interested", "revision": 1}).status_code,
                         404)


class ReleaseHoldTest(WritesBase):
    def linkedin_reply(self, person: int, at: str, key: str) -> int:
        otid = self.conn.execute(
            "INSERT INTO outreach_touch(person_id, company_id, channel, sent_at, context, idempotency_key, created_at) "
            "VALUES (?, ?, 'linkedin_message', '2026-11-02T15:00:00Z', ?, ?, ?)",
            (person, self.conn.execute("SELECT company_id FROM person WHERE person_id = ?", (person,)).fetchone()[0],
             json.dumps(CONTEXT), key, seed.T0)).lastrowid
        record_outcome(self.conn, otid, kind="replied", at=at, reply_kind="interested", revision=1)
        return otid

    def release(self, cid=None, revision=None):
        cid = cid or self.acme
        return self.post(f"/api/companies/{cid}/release-hold", {"revision": revision or self.revision(cid)})

    def test_a_company_held_only_by_a_linkedin_reply_is_released(self):
        self.linkedin_reply(self.pat, "2026-11-09T10:00:00Z", "li-pat")
        _, lena = self.approved_colleague()
        self.assertIn(self.acme, self.held())
        self.assertEqual(verdicts(self.conn)[lena], "company_held")
        res = self.release(revision=1)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), {"released": 1, "revision": 2})
        self.assertNotIn(self.acme, self.held())
        self.assertNotEqual(verdicts(self.conn)[lena], "company_held")
        [event] = self.conn.execute("SELECT * FROM event WHERE kind = 'hold_released'").fetchall()
        self.assertEqual((event["entity"], event["entity_id"], event["actor"]), ("company", self.acme, "devanshu"))

    def test_a_reply_recorded_after_the_release_holds_again(self):
        self.linkedin_reply(self.pat, "2026-11-09T10:00:00Z", "li-pat")
        self.assertEqual(self.release().status_code, 200)
        self.assertNotIn(self.acme, self.held())
        dana = seed.person(self.conn, self.acme, "Dana Late", channel="linkedin", email=None)
        self.linkedin_reply(dana, "2026-11-08T10:00:00Z", "li-dana")  # dated before the release, recorded after
        self.assertIn(self.acme, self.held())

    def test_the_repliers_own_email_stays_stopped_after_the_release(self):
        self.linkedin_reply(self.pat, "2026-11-09T10:00:00Z", "li-pat")
        own = seed.message(self.conn, self.touch, 0, "approved")
        _, lena = self.approved_colleague()
        self.release()
        found = verdicts(self.conn)
        self.assertEqual(found[own], "reply_stop")  # replied_people: a person who replied gets nothing more
        self.assertEqual(found[lena], "ready")

    def test_release_covers_every_company_of_the_merged_family(self):
        loser = seed.company(self.conn, "Acme Inc", status="removed", merged_into=self.acme)
        old = seed.person(self.conn, loser, "Old Hand")
        old_touch = seed.touch(self.conn, old, "replied")
        seed.send_first(self.conn, old_touch)
        self.conn.execute("UPDATE company SET held_by_touch = ?, held_at = ? WHERE company_id = ?",
                          (old_touch, NOW, loser))
        self.linkedin_reply(self.pat, "2026-11-09T10:00:00Z", "li-pat")
        self.assertIn(self.acme, self.held())
        res = self.post(f"/api/companies/{loser}/release-hold", {"revision": self.revision()})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertNotIn(self.acme, self.held())
        held_by = {r[0]: r[1] for r in self.conn.execute("SELECT company_id, held_by_touch FROM company")}
        self.assertEqual((held_by[self.acme], held_by[loser]), (None, None))
        released = {r[0] for r in self.conn.execute("SELECT entity_id FROM event WHERE kind = 'hold_released'")}
        self.assertEqual(released, {self.acme, loser})

    def test_release_of_a_company_that_is_not_held_is_not_allowed_and_stale_conflicts(self):
        self.assertEqual(self.release().json()["code"], "not_allowed")
        self.linkedin_reply(self.pat, "2026-11-09T10:00:00Z", "li-pat")
        self.assertEqual(self.release(revision=9).json()["code"], "conflict")
        self.assertIn(self.acme, self.held())
        self.assertEqual(self.post("/api/companies/999/release-hold", {"revision": 1}).status_code, 404)


class RepeatTest(WritesBase):
    """Review Focus 1 for every Outbox write: one key twice replays, a second key is refused and writes nothing."""

    def writes(self):
        """(name, path, body) for each write, each on a fresh row that can take it."""
        drafted = seed.message(self.conn, seed.touch(self.conn, seed.person(self.conn, self.acme, "Dee")), 0)
        approved = seed.message(self.conn, seed.touch(self.conn, seed.person(self.conn, self.acme, "Abe")), 0,
                                "approved")
        skipped = seed.message(self.conn, seed.touch(self.conn, seed.person(self.conn, self.acme, "Ski")), 0)
        self.replied_by_email()
        return (
            ("save", f"/api/messages/{drafted}/save", {"subject": "New subject", "body": "New body", "revision": 1}),
            ("unapprove", f"/api/messages/{approved}/unapprove", {"revision": 1}),
            ("skip", f"/api/messages/{skipped}/skip", {"revision": 1}),
            ("reply-kind", f"/api/touches/{self.touch}/reply-kind", {"kind": "not_now", "revision": 1}),
            ("release-hold", f"/api/companies/{self.acme}/release-hold", {"revision": self.revision()}),
        )

    def counts(self):
        return (self.conn.execute("SELECT COUNT(*) FROM event").fetchone()[0],
                self.conn.execute("SELECT sum(revision) FROM message").fetchone()[0],
                self.conn.execute("SELECT sum(revision) FROM touch").fetchone()[0],
                self.conn.execute("SELECT sum(revision) FROM company").fetchone()[0])

    def test_one_key_twice_writes_once_and_replays(self):
        for name, path, body in self.writes():
            with self.subTest(write=name):
                first = self.post(path, body, key=f"repeat-{name}")
                self.assertEqual(first.status_code, 200, first.text)
                after = self.counts()
                again = self.post(path, body, key=f"repeat-{name}")
                self.assertEqual((again.status_code, again.headers.get("Idempotent-Replay")), (200, "true"))
                self.assertEqual(again.json(), first.json())
                self.assertEqual(self.counts(), after)

    def test_a_second_key_is_refused_and_writes_nothing(self):
        for name, path, body in self.writes():
            with self.subTest(write=name):
                self.assertEqual(self.post(path, body, key=f"first-{name}").status_code, 200)
                after = self.counts()
                res = self.post(path, body, key=f"second-{name}")
                self.assertIn(res.json()["code"], ("conflict", "not_allowed"), res.text)
                self.assertEqual(self.counts(), after)


class ReleaseScopeTest(ReleaseHoldTest):
    def test_a_release_leaves_an_unrelated_held_company_alone(self):
        other = seed.company(self.conn, "Other Co", status="working")
        self.linkedin_reply(self.pat, "2026-11-09T10:00:00Z", "li-pat")
        self.linkedin_reply(seed.person(self.conn, other, "Oli Other"), "2026-11-09T11:00:00Z", "li-oli")
        self.assertEqual(self.held(), {self.acme, other})
        self.assertEqual(self.release().status_code, 200)
        self.assertEqual(self.held(), {other})
        self.assertEqual({r[0] for r in self.conn.execute("SELECT entity_id FROM event WHERE kind = 'hold_released'")},
                         {self.acme})


if __name__ == "__main__":
    unittest.main()
