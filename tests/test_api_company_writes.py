"""Task 8: the company writes (pause, resume, close, reopen) and the role kind of a person.

Every write goes through `run_write`, checks the revision the page loaded, and appends one event by Devanshu keyed
`<request id>:0`. A pause is read back through the sender's own queue verdicts, so "paused" means the sender holds
the mail. The people add waits for #15 (`add_candidate`), and the people edit is not in this batch.
"""
import json
import unittest
from datetime import datetime, timezone

from outreach.sending.config import load_config
from outreach.sending.status import queue_schedule
from outreach.store.writes import append_event
from tests import apiseed as seed
from tests.apitest import NOW, ApiCase

CFG = load_config()


def moment(iso: str) -> datetime:
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


class CompanyWritesBase(ApiCase):
    def setUp(self):
        super().setUp()
        self.cid = seed.company(self.conn, "Acme", status="working")

    def company(self, cid=None):
        return self.conn.execute("SELECT * FROM company WHERE company_id = ?", (cid or self.cid,)).fetchone()

    def events(self, kind=None):
        sql, args = "SELECT * FROM event", ()
        if kind:
            sql, args = sql + " WHERE kind = ?", (kind,)
        return self.conn.execute(sql + " ORDER BY event_id", args).fetchall()

    def approved_mail(self, cid=None) -> int:
        """An approved first email to a new person at the company. Returns the message id."""
        pid = seed.person(self.conn, cid or self.cid)
        tid = seed.touch(self.conn, pid)
        return seed.message(self.conn, tid, 0, "approved")

    def prove(self):
        """The sender waits for every message until Gmail is shown to keep the Message-ID it sets."""
        append_event(self.conn, entity="message", entity_id=1, kind="import_verified", actor="sender",
                     at="2026-11-01T12:00:00Z", idempotency_key="proof")

    def verdict(self, message_id: int) -> str:
        return queue_schedule(self.conn, moment(self.now), CFG)[message_id].reason

    def sent_with_planned_followup(self, cid=None) -> tuple[int, int]:
        """A person whose first email went out and whose follow-up is planned. Returns (touch id, planned id)."""
        pid = seed.person(self.conn, cid or self.cid)
        tid = seed.touch(self.conn, pid)
        seed.send_first(self.conn, tid)
        planned = self.conn.execute(
            "INSERT INTO message(touch_id, seq, state, due_on, saved_by, created_at, updated_at) "
            "VALUES (?, 1, 'planned', '2026-11-16', 'sender', ?, ?)", (tid, seed.T0, seed.T0)).lastrowid
        return tid, planned


class PauseResumeTest(CompanyWritesBase):
    def test_pause_then_resume_round_trip_and_revisions(self):
        self.prove()
        mid = self.approved_mail()
        self.assertNotEqual(self.verdict(mid), "company_paused")

        res = self.post(f"/api/companies/{self.cid}/pause", {"revision": 1}, key="pause-key-1")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), {"revision": 2})
        row = self.company()
        self.assertEqual((row["paused_at"], row["revision"]), (NOW, 2))
        self.assertEqual(self.verdict(mid), "company_paused")  # the sender's own queue holds the mail
        [event] = self.events("paused")
        self.assertEqual((event["entity"], event["entity_id"], event["company_id"], event["actor"]),
                         ("company", self.cid, self.cid, "devanshu"))
        self.assertEqual(event["idempotency_key"], "pause-key-1:0")

        res = self.post(f"/api/companies/{self.cid}/resume", {"revision": 2})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), {"released": 1, "revision": 3})
        row = self.company()
        self.assertEqual((row["paused_at"], row["revision"]), (None, 3))
        self.assertNotEqual(self.verdict(mid), "company_paused")
        self.assertEqual(len(self.events("resumed")), 1)

    def test_pause_a_company_that_is_not_working_is_not_allowed(self):
        for status in ("candidate", "backlog", "closed", "removed"):
            with self.subTest(status=status):
                cid = seed.company(self.conn, f"Other {status}", status=status)
                res = self.post(f"/api/companies/{cid}/pause", {"revision": 1})
                self.assertEqual(res.status_code, 409, res.text)
                self.assertEqual(res.json()["code"], "not_allowed")
                self.assertIsNone(self.company(cid)["paused_at"])
        self.conn.execute("UPDATE company SET paused_at = ? WHERE company_id = ?", (NOW, self.cid))
        res = self.post(f"/api/companies/{self.cid}/pause", {"revision": 1})
        self.assertEqual(res.json()["code"], "not_allowed")  # already paused
        self.assertEqual(self.events(), [])

    def test_resume_a_company_that_is_not_paused_is_not_allowed(self):
        res = self.post(f"/api/companies/{self.cid}/resume", {"revision": 1})
        self.assertEqual(res.status_code, 409, res.text)
        self.assertEqual(res.json()["code"], "not_allowed")
        self.assertEqual(self.company()["revision"], 1)

    def test_stale_revision_changes_nothing(self):
        self.conn.execute("UPDATE company SET revision = 4 WHERE company_id = ?", (self.cid,))
        before = len(self.events())
        for path, body in (("pause", {"revision": 3}), ("close", {"reason": "other", "note": "", "revision": 3})):
            with self.subTest(path=path):
                res = self.post(f"/api/companies/{self.cid}/{path}", body)
                self.assertEqual(res.status_code, 409, res.text)
                self.assertEqual(res.json()["code"], "conflict")
                row = self.company()
                self.assertEqual((row["status"], row["paused_at"], row["revision"]), ("working", None, 4))
        self.assertEqual(len(self.events()), before)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM api_request").fetchone()[0], 0)

    def test_double_click_pause_writes_once(self):
        first = self.post(f"/api/companies/{self.cid}/pause", {"revision": 1}, key="double-click-1")
        again = self.post(f"/api/companies/{self.cid}/pause", {"revision": 1}, key="double-click-1")
        self.assertEqual((first.status_code, again.status_code), (200, 200))
        self.assertEqual(again.json(), first.json())
        self.assertEqual(again.headers.get("Idempotent-Replay"), "true")
        self.assertEqual(len(self.events("paused")), 1)
        self.assertEqual(self.company()["revision"], 2)

    def test_a_write_on_a_merged_duplicate_lands_on_its_survivor(self):
        loser = seed.company(self.conn, "Acme Inc", status="removed", merged_into=self.cid)
        res = self.post(f"/api/companies/{loser}/pause", {"revision": 1})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.company()["paused_at"], NOW)
        self.assertIsNone(self.company(loser)["paused_at"])


class CloseReopenTest(CompanyWritesBase):
    def test_close_no_sponsorship_adds_no_go_and_skips_planned_followups(self):
        tid, planned = self.sent_with_planned_followup()
        self.conn.execute("UPDATE company SET paused_at = ? WHERE company_id = ?", (NOW, self.cid))
        res = self.post(f"/api/companies/{self.cid}/close",
                        {"reason": "no_sponsorship", "note": "Told me on a call", "revision": 1}, key="close-key-1")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), {"revision": 2})
        row = self.company()
        self.assertEqual((row["status"], row["close_reason"], row["status_note"], row["status_at"], row["paused_at"]),
                         ("closed", "no_sponsorship", "Told me on a call", NOW, None))
        [no_go] = self.conn.execute("SELECT * FROM no_go").fetchall()
        self.assertEqual((no_go["company_id"], no_go["person_id"], no_go["reason"], no_go["source"], no_go["note"]),
                         (self.cid, None, "no_sponsorship", "devanshu", "Told me on a call"))
        # The planned follow-up goes through the sender's own stop: deleted, with its event on the touch.
        self.assertIsNone(self.conn.execute("SELECT 1 FROM message WHERE message_id = ?", (planned,)).fetchone())
        [stop] = self.events("skipped_by_sender")
        self.assertEqual((stop["entity"], stop["entity_id"], stop["actor"]), ("touch", tid, "devanshu"))
        self.assertEqual(json.loads(stop["detail"])["reason"], "company_closed")
        [closed] = self.events("closed")
        self.assertEqual((closed["actor"], closed["idempotency_key"]), ("devanshu", "close-key-1:0"))
        self.assertEqual(len(self.events("added")), 1)  # the no-go's own event

    def test_close_other_reasons_do_not_add_no_go(self):
        for reason in ("referral", "interview", "no_referrals", "already_hired", "not_now", "other"):
            with self.subTest(reason=reason):
                cid = seed.company(self.conn, f"Co {reason}", status="working")
                _, planned = self.sent_with_planned_followup(cid)
                res = self.post(f"/api/companies/{cid}/close", {"reason": reason, "note": "", "revision": 1})
                self.assertEqual(res.status_code, 200, res.text)
                self.assertEqual(self.company(cid)["close_reason"], reason)
                self.assertIsNone(self.conn.execute("SELECT 1 FROM message WHERE message_id = ?",
                                                    (planned,)).fetchone())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM no_go").fetchone()[0], 0)

    def test_close_a_closed_or_removed_company_is_not_allowed(self):
        for status in ("closed", "removed"):
            with self.subTest(status=status):
                cid = seed.company(self.conn, f"Gone {status}", status=status)
                res = self.post(f"/api/companies/{cid}/close", {"reason": "other", "note": "", "revision": 1})
                self.assertEqual(res.json()["code"], "not_allowed")

    def test_close_an_unknown_reason_is_bad_request(self):
        res = self.post(f"/api/companies/{self.cid}/close", {"reason": "bored", "note": "", "revision": 1})
        self.assertEqual(res.status_code, 400, res.text)
        self.assertEqual(self.company()["status"], "working")

    def test_reopen_restores_the_previous_working_state(self):
        backlog = seed.company(self.conn, "Later", status="backlog")
        for cid, before in ((self.cid, "working"), (backlog, "backlog")):
            with self.subTest(before=before):
                self.assertEqual(self.post(f"/api/companies/{cid}/close",
                                           {"reason": "not_now", "note": "", "revision": 1}).status_code, 200)
                res = self.post(f"/api/companies/{cid}/reopen", {"revision": 2})
                self.assertEqual(res.status_code, 200, res.text)
                self.assertEqual(res.json(), {"revision": 3})
                row = self.company(cid)
                self.assertEqual((row["status"], row["close_reason"]), (before, None))
        self.assertEqual(len(self.events("reopened")), 2)

    def test_reopen_without_a_close_event_is_working_only_when_it_was_picked(self):
        picked = seed.company(self.conn, "Picked", status="closed")
        never = seed.company(self.conn, "Never", status="closed")
        self.conn.execute("INSERT INTO daily_list(list_date, built_at, capacity) VALUES ('2026-11-02', ?, 5)", (seed.T0,))
        self.conn.execute(
            "INSERT INTO daily_list_entry(list_date, company_id, list_group, rank, state, proposed_at, decided_at) "
            "VALUES ('2026-11-02', ?, 'level_role', 1, 'picked', ?, ?)", (picked, seed.T0, seed.T0))
        self.post(f"/api/companies/{picked}/reopen", {"revision": 1})
        self.post(f"/api/companies/{never}/reopen", {"revision": 1})
        self.assertEqual(self.company(picked)["status"], "working")
        self.assertEqual(self.company(never)["status"], "backlog")

    def test_reopen_lifts_the_no_go_its_close_wrote_but_not_another(self):
        self.post(f"/api/companies/{self.cid}/close", {"reason": "no_sponsorship", "note": "", "revision": 1})
        res = self.post(f"/api/companies/{self.cid}/reopen", {"revision": 2})
        self.assertEqual(res.status_code, 200, res.text)
        [row] = self.conn.execute("SELECT * FROM no_go").fetchall()
        self.assertIsNotNone(row["lifted_at"])

        ruled = seed.company(self.conn, "Ruled", status="closed", close_reason="no_sponsorship")
        self.conn.execute("INSERT INTO no_go(company_id, reason, source, created_at) "
                          "VALUES (?, 'no_sponsorship', 'rules', ?)", (ruled, seed.T0))
        res = self.post(f"/api/companies/{ruled}/reopen", {"revision": 1})
        self.assertEqual(res.status_code, 409, res.text)
        self.assertEqual(res.json()["code"], "not_allowed")
        self.assertEqual(self.company(ruled)["status"], "closed")

    def test_reopen_a_company_that_is_not_closed_is_not_allowed(self):
        res = self.post(f"/api/companies/{self.cid}/reopen", {"revision": 1})
        self.assertEqual(res.json()["code"], "not_allowed")


class RoleKindTest(CompanyWritesBase):
    def drafted(self, role_kind="engineer") -> tuple[int, int]:
        pid = seed.person(self.conn, self.cid, "Dana Whit", role_kind)
        tid = seed.touch(self.conn, pid)
        return pid, seed.message(self.conn, tid, 0, "drafted")

    def recruiter_check(self, message_id: int) -> str:
        checks = json.loads(self.conn.execute("SELECT checks FROM message WHERE message_id = ?",
                                              (message_id,)).fetchone()[0])
        return next(c["severity"] for c in checks if c["code"] == "recruiter")

    def test_role_kind_change_reruns_open_draft_checks(self):
        pid, mid = self.drafted()
        res = self.post(f"/api/people/{pid}/role-kind", {"kind": "recruiter", "revision": 1}, key="role-key-1")
        self.assertEqual(res.status_code, 200, res.text)
        person = res.json()
        self.assertEqual((person["id"], person["roleKind"], person["revision"]), (pid, "recruiter", 2))
        self.assertEqual(self.recruiter_check(mid), "warn")
        msg = self.conn.execute("SELECT revision, state FROM message WHERE message_id = ?", (mid,)).fetchone()
        self.assertEqual((msg["revision"], msg["state"]), (1, "drafted"))  # checks are stored, nothing else moves
        [event] = self.events("role_kind_changed")
        self.assertEqual((event["entity"], event["entity_id"], event["actor"], event["idempotency_key"]),
                         ("person", pid, "devanshu", "role-key-1:0"))

        res = self.post(f"/api/people/{pid}/role-kind", {"kind": "engineer", "revision": 2})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.recruiter_check(mid), "pass")

    def test_role_kind_leaves_sent_messages_alone(self):
        pid = seed.person(self.conn, self.cid, "Sam Sent")
        tid = seed.touch(self.conn, pid)
        mid = seed.send_first(self.conn, tid)
        self.post(f"/api/people/{pid}/role-kind", {"kind": "recruiter", "revision": 1})
        self.assertEqual(self.conn.execute("SELECT checks FROM message WHERE message_id = ?", (mid,)).fetchone()[0],
                         "[]")

    def test_role_kind_stale_revision_conflicts(self):
        pid, mid = self.drafted()
        res = self.post(f"/api/people/{pid}/role-kind", {"kind": "recruiter", "revision": 7})
        self.assertEqual(res.status_code, 409, res.text)
        self.assertEqual(res.json()["code"], "conflict")
        self.assertEqual(self.conn.execute("SELECT role_kind FROM person WHERE person_id = ?",
                                           (pid,)).fetchone()[0], "engineer")
        self.assertEqual(self.conn.execute("SELECT checks FROM message WHERE message_id = ?", (mid,)).fetchone()[0],
                         "[]")

    def test_an_unknown_role_kind_is_bad_request(self):
        pid, _ = self.drafted()
        res = self.post(f"/api/people/{pid}/role-kind", {"kind": "wizard", "revision": 1})
        self.assertEqual(res.status_code, 400, res.text)


class UnknownIdsTest(CompanyWritesBase):
    def test_unknown_ids_are_404(self):
        for path, body in (
            ("/api/companies/999/pause", {"revision": 1}),
            ("/api/companies/999/resume", {"revision": 1}),
            ("/api/companies/999/close", {"reason": "other", "note": "", "revision": 1}),
            ("/api/companies/999/reopen", {"revision": 1}),
            ("/api/people/999/role-kind", {"kind": "engineer", "revision": 1}),
        ):
            with self.subTest(path=path):
                res = self.post(path, body)
                self.assertEqual(res.status_code, 404, res.text)
                self.assertEqual(res.json()["code"], "not_found")

    def test_a_huge_id_is_bad_request_not_a_500(self):
        res = self.post(f"/api/companies/{2**63}/pause", {"revision": 1})
        self.assertEqual(res.status_code, 400, res.text)


class PeopleAddTest(ApiCase):
    @unittest.skip("waits for #15 PR 1: the people add calls add_candidate, which is not on master yet")
    def test_add_person_goes_through_add_candidate_and_maps_each_refusal_code(self):
        pass

    @unittest.skip("not in API part 2 batch A: the people edit lands with the people add")
    def test_edit_person_checks_revision_and_keeps_unique_indexes(self):
        pass


if __name__ == "__main__":
    unittest.main()
