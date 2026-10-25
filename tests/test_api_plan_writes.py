"""The plan's writes (#11 Task 11): log a request, log applied, skip a row, save and confirm a note.

They log what Devanshu did by hand and keep his notes; nothing here sends, approves or reaches LinkedIn. A reason line
is confirmed only by `POST /api/notes/{id}/confirm` (his key). Scratch store, no network, no model.
"""
import dataclasses
import json
import os
import shutil
import tempfile
import uuid
from datetime import date
from pathlib import Path
from unittest import mock

from outreach.plan.drafts import get_draft_by_id, save_linkedin_draft
from outreach.plan.prepare_store import store_company_result
from outreach.plan.prepare_targets import Target
from outreach.store import undo_touch
from tests.apitest import NOW
from tests.test_api_plan import BANNED, CFG, DAY, FIX, UNCONFIRMED, PlanCase

DASH = chr(0x2014)


class PlanWriteCase(PlanCase):
    def setUp(self):
        super().setUp()
        self.settings = dataclasses.replace(self.settings, banned_phrases=FIX / "banned_phrases.txt")
        self.build()
        home = Path(tempfile.mkdtemp(prefix="outreach-plan-writes-"))
        self.addCleanup(shutil.rmtree, home, True)
        shutil.copy(FIX / "me.md", home / "me.md")
        patcher = mock.patch.dict(os.environ, {"OUTREACH_MAIL_HOME": str(home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    # ------------------------------------------------------------------------------------------- calls

    def log_request(self, company: str, *, person_id=None, revision=1, key=None, request_id=None):
        eid = self.entry_id(company)
        body = {"personId": person_id or self.people[company], "idempotencyKey": key or f"plan:{eid}:request",
                "revision": revision}
        return self.post(f"/api/plan/{eid}/request", body, key=request_id)

    def log_applied(self, company: str, *, posting_id=None, revision=1, key=None, request_id=None):
        eid = self.entry_id(company)
        body = {"postingId": posting_id or self.postings[company], "idempotencyKey": key or f"plan:{eid}:apply",
                "revision": revision}
        return self.post(f"/api/plan/{eid}/apply", body, key=request_id)

    def skip(self, company: str, revision: int = 1):
        return self.post(f"/api/plan/{self.entry_id(company)}/skip", {"revision": revision})

    def save_note(self, note_id: int, text: str, reason_line: str, revision: int):
        return self.client.put(f"/api/notes/{note_id}",
                               content=json.dumps({"text": text, "reasonLine": reason_line, "revision": revision}),
                               headers={"Content-Type": "application/json", "Idempotency-Key": str(uuid.uuid4())})

    def confirm(self, note_id: int, revision: int):
        return self.post(f"/api/notes/{note_id}/confirm", {"revision": revision})

    def refused(self, res, code: str, status: int) -> dict:
        self.assertEqual((res.status_code, res.json()["code"]), (status, code), res.text)
        return res.json()

    # ------------------------------------------------------------------------------------------- reads

    def touches(self, channel: str, **where) -> list:
        sql, args = "SELECT * FROM outreach_touch WHERE channel = ?", [channel]
        for col, value in where.items():
            sql += f" AND {col} IS ?"
            args.append(value)
        return self.conn.execute(sql + " ORDER BY touch_id", args).fetchall()

    def log_rows(self, person_id: int) -> list:
        return self.conn.execute("SELECT * FROM linkedin_log WHERE person_id = ? ORDER BY entry_id",
                                 (person_id,)).fetchall()

    def entry_row(self, company: str):
        return self.conn.execute("SELECT rowid, * FROM daily_list_entry WHERE rowid = ?",
                                 (self.entry_id(company),)).fetchone()


class LogRequestTests(PlanWriteCase):
    def test_log_request_writes_touch_and_linkedin_log_together(self):
        lena = self.people["Pylon"]
        row = self.ok(self.log_request("Pylon"))
        (touch,) = self.touches("linkedin_request", person_id=lena)
        (log,) = self.log_rows(lena)
        self.assertEqual((touch["idempotency_key"], touch["sent_at"], touch["company_id"], touch["posting_id"]),
                         (f"plan:{self.entry_id('Pylon')}:request", NOW, self.ids["Pylon"], self.postings["Pylon"]))
        self.assertEqual((log["kind"], log["at"]), ("request_sent", NOW))
        self.assertEqual(row["steps"][0], {"kind": "request", "touchId": touch["touch_id"], "at": NOW})
        self.assertEqual((row["revision"], self.entry_row("Pylon")["revision"]), (2, 2))

        jonah = self.people["Inngest"]
        bad = {"source": "bogus", "lane": "level_role", "roleKind": None, "roleAgeDays": None, "rankerVersion": None,
               "signals": {}, "roleNamed": False}
        before = self.conn.execute("SELECT count(*) FROM api_request").fetchone()[0]
        with mock.patch("outreach.api.services.plan.build_context", return_value=bad):
            res = self.log_request("Inngest")
        self.assertEqual(res.status_code, 500, res.text)
        self.assertEqual((self.touches("linkedin_request", person_id=jonah), self.log_rows(jonah)), ([], []))
        self.assertEqual(self.entry_row("Inngest")["revision"], 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM api_request").fetchone()[0], before)

    def test_same_idempotency_key_with_a_new_request_id_makes_one_touch(self):
        first = self.ok(self.log_request("Pylon"))
        again = self.ok(self.log_request("Pylon"))  # a new request id: the press after the first commit landed
        self.assertEqual(again["steps"][0]["touchId"], first["steps"][0]["touchId"])
        self.assertEqual(len(self.touches("linkedin_request", person_id=self.people["Pylon"])), 1)
        self.assertEqual(len(self.log_rows(self.people["Pylon"])), 1)
        self.assertEqual(self.entry_row("Pylon")["revision"], 2)  # the second press wrote nothing

    def test_same_request_id_replays(self):
        first = self.log_request("Pylon", request_id="req-same-0001")
        second = self.log_request("Pylon", request_id="req-same-0001")
        self.assertEqual((second.status_code, second.headers.get("Idempotent-Replay")), (200, "true"))
        self.assertEqual(second.json(), first.json())
        self.assertEqual(len(self.touches("linkedin_request", person_id=self.people["Pylon"])), 1)

    def test_a_key_used_for_another_touch_is_refused(self):
        self.ok(self.log_request("Pylon", key="plan:shared:key"))
        self.refused(self.log_request("Inngest", key="plan:shared:key"), "bad_request", 400)
        self.assertEqual(self.touches("linkedin_request", person_id=self.people["Inngest"]), [])
        self.assertEqual(self.log_rows(self.people["Inngest"]), [])

    def test_stale_entry_revision_is_a_conflict(self):
        self.refused(self.log_request("Pylon", revision=5), "conflict", 409)
        self.assertEqual(self.touches("linkedin_request", person_id=self.people["Pylon"]), [])
        self.assertEqual(self.log_rows(self.people["Pylon"]), [])

    def test_request_for_another_persons_id_is_refused(self):
        self.refused(self.log_request("Pylon", person_id=self.people["Inngest"]), "bad_request", 400)
        for company in ("Pylon", "Inngest"):
            self.assertEqual(self.touches("linkedin_request", person_id=self.people[company]), [])
        self.assertEqual(self.log_rows(self.people["Inngest"]), [])

    def test_a_find_someone_row_and_an_email_row_take_no_request(self):
        self.refused(self.post(f"/api/plan/{self.entry_id('Cerebrium')}/request",
                               {"personId": self.people["Pylon"], "idempotencyKey": "plan:x:request",
                                "revision": 1}), "not_allowed", 409)
        self.refused(self.log_request("Mintlify"), "not_allowed", 409)
        self.refused(self.log_applied("Mintlify", posting_id=self.postings["Pylon"]), "not_allowed", 409)

    def test_an_unknown_row_is_not_found(self):
        res = self.post("/api/plan/999999/request", {"personId": 1, "idempotencyKey": "plan:x:request", "revision": 1})
        self.refused(res, "not_found", 404)

    def test_logged_context_has_source_plan_lane_role_age_and_ranker_version(self):
        self.ok(self.log_request("Turso"))
        (touch,) = self.touches("linkedin_request", person_id=self.people["Turso"])
        self.assertEqual(json.loads(touch["context"]), {
            "source": "plan", "lane": "intern_junior", "roleKind": "founder", "roleAgeDays": 9,
            "rankerVersion": "rk-1", "signals": {"role": 1.0, "team": 1.0}, "roleNamed": True})
        self.ok(self.log_applied("Turso", revision=2))
        (applied,) = self.touches("application", posting_id=self.postings["Turso"])
        context = json.loads(applied["context"])
        self.assertEqual((context["source"], context["lane"], context["roleKind"], context["roleAgeDays"],
                          context["roleNamed"]), ("plan", "intern_junior", None, 9, True))

    def test_response_row_matches_the_next_plan_read(self):
        for call in (lambda: self.log_request("Pylon"), lambda: self.log_applied("Pylon", revision=2),
                     lambda: self.skip("Inngest")):
            answer = self.ok(call())
            self.assertEqual(answer, self.row(answer["company"]["name"]))


class KeyAndGuardTests(PlanWriteCase):
    """Fix round 1: the touch found under a key must be this row's and live; paused and no-go refuse both writes."""

    def no_go(self, *, company: str | None = None, person: str | None = None) -> None:
        self.conn.execute("INSERT INTO no_go(company_id, person_id, reason, source, created_at) "
                          "VALUES (?, ?, 'other', 'devanshu', ?)",
                          (self.ids[company] if company else None, self.people[person] if person else None, NOW))

    def nothing_written(self, company: str) -> None:
        self.assertEqual(self.touches("linkedin_request", person_id=self.people[company]), [])
        self.assertEqual(self.log_rows(self.people[company]), [])
        self.assertEqual(self.touches("application", posting_id=self.postings[company]), [])
        self.assertEqual(self.entry_row(company)["revision"], 1)

    def test_a_request_key_replayed_against_another_rows_id_is_refused_and_writes_nothing(self):
        self.ok(self.log_request("Pylon"))
        key = f"plan:{self.entry_id('Pylon')}:request"
        res = self.post(f"/api/plan/{self.entry_id('Inngest')}/request",
                        {"personId": self.people["Pylon"], "idempotencyKey": key, "revision": 1})
        self.refused(res, "bad_request", 400)
        self.nothing_written("Inngest")

    def test_an_apply_key_replayed_against_another_rows_id_is_refused_and_writes_nothing(self):
        self.ok(self.log_applied("Pylon"))
        key = f"plan:{self.entry_id('Pylon')}:apply"
        res = self.post(f"/api/plan/{self.entry_id('Inngest')}/apply",
                        {"postingId": self.postings["Pylon"], "idempotencyKey": key, "revision": 1})
        self.refused(res, "bad_request", 400)
        self.nothing_written("Inngest")

    def test_a_request_key_on_an_apply_is_refused(self):
        self.ok(self.log_request("Pylon"))
        self.refused(self.log_applied("Pylon", key=f"plan:{self.entry_id('Pylon')}:request", revision=2),
                     "bad_request", 400)

    def test_a_request_taken_back_on_the_linkedin_page_can_be_logged_again(self):
        lena = self.people["Pylon"]
        first = self.ok(self.log_request("Pylon"))
        old = first["steps"][0]["touchId"]
        self.ok(self.post(f"/api/linkedin/{lena}/undo", {"step": "request_sent"}))
        self.assertIsNone(self.row("Pylon")["steps"][0]["at"])
        again = self.ok(self.log_request("Pylon", revision=2))
        key = f"plan:{self.entry_id('Pylon')}:request"
        live = [t for t in self.touches("linkedin_request", person_id=lena) if t["undone_at"] is None]
        self.assertEqual(len(live), 1)
        self.assertNotEqual(live[0]["touch_id"], old)
        self.assertEqual(live[0]["idempotency_key"], key)
        moved = self.conn.execute("SELECT idempotency_key, undone_at FROM outreach_touch WHERE touch_id = ?",
                                  (old,)).fetchone()
        self.assertEqual(moved["idempotency_key"], f"{key}#undone-{old}")
        self.assertIsNotNone(moved["undone_at"])
        self.assertEqual(again["steps"][0], {"kind": "request", "touchId": live[0]["touch_id"], "at": NOW})
        self.assertEqual([r["kind"] for r in self.log_rows(lena)], ["request_sent", "undo", "request_sent"])
        (event,) = self.conn.execute("SELECT * FROM event WHERE kind = 'key_released'").fetchall()
        self.assertEqual((event["entity"], event["entity_id"]), ("outreach_touch", old))
        self.assertEqual(self.row("Pylon"), again)

    def test_an_application_taken_back_can_be_logged_again_with_one_application_row(self):
        first = self.ok(self.log_applied("Pylon"))
        old = first["steps"][1]["touchId"]
        undo_touch(self.conn, old, at=NOW, revision=1)
        again = self.ok(self.log_applied("Pylon", revision=2))
        self.assertNotEqual(again["steps"][1]["touchId"], old)
        self.assertEqual(again["steps"][1]["at"], NOW)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM application").fetchone()[0], 1)

    def test_body_keys_are_plain(self):
        self.refused(self.log_request("Pylon", key="plan:1:request#undone-3"), "bad_request", 400)
        self.nothing_written("Pylon")

    def test_a_paused_company_refuses_both_writes(self):
        self.conn.execute("UPDATE company SET status = 'working', paused_at = ? WHERE company_id = ?",
                          (NOW, self.ids["Pylon"]))
        for res in (self.log_request("Pylon"), self.log_applied("Pylon")):
            body = self.refused(res, "not_allowed", 409)
            self.assertIn("Pylon is paused", body["message"])
        self.nothing_written("Pylon")

    def test_a_paused_survivor_refuses_a_row_of_its_merged_duplicate(self):
        survivor = self.s_company("Pylon Labs")
        self.conn.execute("UPDATE company SET merged_into = ?, status = 'removed', remove_reason = 'merged' "
                          "WHERE company_id = ?", (survivor, self.ids["Pylon"]))
        self.conn.execute("UPDATE company SET status = 'working', paused_at = ? WHERE company_id = ?", (NOW, survivor))
        self.refused(self.log_request("Pylon"), "not_allowed", 409)
        self.refused(self.log_applied("Pylon"), "not_allowed", 409)
        self.nothing_written("Pylon")

    def test_a_no_go_company_refuses_both_writes(self):
        self.no_go(company="Pylon")
        for res in (self.log_request("Pylon"), self.log_applied("Pylon")):
            body = self.refused(res, "not_allowed", 409)
            self.assertIn("no-go list", body["message"])
        self.nothing_written("Pylon")

    def test_a_no_go_person_refuses_both_writes(self):
        self.no_go(person="Pylon")
        for res in (self.log_request("Pylon"), self.log_applied("Pylon")):
            body = self.refused(res, "not_allowed", 409)
            self.assertIn("no-go list", body["message"])
        self.nothing_written("Pylon")

    def test_a_lifted_no_go_allows_the_writes_again(self):
        self.no_go(person="Pylon")
        self.conn.execute("UPDATE no_go SET lifted_at = ?, lift_reason = 'slip'", (NOW,))
        self.ok(self.log_request("Pylon"))
        self.ok(self.log_applied("Pylon", revision=2))

    def s_company(self, name: str) -> int:
        from tests import apiseed
        return apiseed.company(self.conn, name)


class LogAppliedTests(PlanWriteCase):
    def test_apply_with_no_role_is_not_allowed_with_the_exact_message(self):
        self.conn.execute("UPDATE daily_list_entry SET posting_id = NULL WHERE company_id = ?", (self.ids["Pylon"],))
        body = self.refused(self.log_applied("Pylon"), "not_allowed", 409)
        self.assertEqual(body["message"], "No role to apply for.")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM application").fetchone()[0], 0)

    def test_apply_on_a_closed_posting_is_not_allowed(self):
        self.conn.execute("UPDATE posting SET closed_at = ?, close_reason = 'hand' WHERE posting_id = ?",
                          ("2026-11-10T14:00:00Z", self.postings["Pylon"]))
        body = self.refused(self.log_applied("Pylon"), "not_allowed", 409)
        self.assertEqual(body["message"], "No role to apply for.")
        self.assertEqual(self.touches("application", posting_id=self.postings["Pylon"]), [])

    def test_apply_for_another_posting_is_refused(self):
        self.refused(self.log_applied("Pylon", posting_id=self.postings["Turso"]), "bad_request", 400)

    def test_apply_writes_application_and_a_touch_with_no_person(self):
        row = self.ok(self.log_applied("Pylon"))
        (app,) = self.conn.execute("SELECT * FROM application").fetchall()
        self.assertEqual((app["company_id"], app["posting_id"], app["applied_on"], app["url"]),
                         (self.ids["Pylon"], self.postings["Pylon"], DAY, "https://jobs.example/pylon"))
        (touch,) = self.touches("application", posting_id=self.postings["Pylon"])
        self.assertEqual((touch["person_id"], touch["company_id"], touch["idempotency_key"]),
                         (None, self.ids["Pylon"], f"plan:{self.entry_id('Pylon')}:apply"))
        self.assertEqual(row["steps"][1], {"kind": "apply", "touchId": touch["touch_id"], "at": NOW})
        self.assertEqual((row["state"], row["revision"]), ("todo", 2))
        again = self.ok(self.log_applied("Pylon"))  # the press after the commit landed: nothing more
        self.assertEqual(again["steps"][1]["touchId"], touch["touch_id"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM application").fetchone()[0], 1)


class SkipTests(PlanWriteCase):
    def test_skip_sets_the_entry_to_backlog_with_decided_at(self):
        row = self.ok(self.skip("Pylon"))
        entry = self.entry_row("Pylon")
        self.assertEqual((entry["state"], entry["decided_at"], entry["revision"]), ("backlog", NOW, 2))
        (event,) = self.conn.execute("SELECT * FROM event WHERE kind = 'row_skipped'").fetchall()
        self.assertEqual((event["entity"], event["entity_id"], event["actor"], event["company_id"]),
                         ("daily_list", entry["rowid"], "devanshu", self.ids["Pylon"]))
        self.assertEqual(json.loads(event["detail"]), {"list_date": DAY, "company_id": self.ids["Pylon"]})
        self.assertEqual((row["state"], row["revision"]), ("skipped", 2))

    def test_skip_marks_the_row_and_refuses_a_done_row(self):
        self.refused(self.skip("Resend"), "not_allowed", 409)
        self.assertEqual(self.entry_row("Resend")["state"], "proposed")
        self.ok(self.skip("Pylon"))
        self.assertEqual(self.row("Pylon")["state"], "skipped")
        self.refused(self.skip("Pylon", revision=1), "conflict", 409)

    def test_skipped_row_can_still_be_logged_later(self):
        self.ok(self.skip("Pylon"))
        self.ok(self.log_request("Pylon", revision=2))
        row = self.ok(self.log_applied("Pylon", revision=3))
        self.assertEqual(row["state"], "done")


class NoteTests(PlanWriteCase):
    def test_save_note_goes_through_save_linkedin_draft_and_its_checks(self):
        note = self.lena_note
        text = ("Hi Lena, I read your post on the queue rewrite " + DASH + " real synergy there. "
                + "I built a similar job runner and would like to hear how the team works. " * 3)
        self.assertGreater(len(text), 200)
        got = self.ok(self.save_note(note.draft_id, text, note.reason_line, note.revision))
        self.assertEqual((got["noteId"], got["text"], got["chars"], got["maxChars"]),
                         (note.draft_id, text, len(text), 200))
        blocks = {c["code"] for c in got["checks"] if c["severity"] == "block"}
        self.assertTrue({"note_too_long", "banned_phrase", "dash"} <= blocks, blocks)
        self.assertEqual((got["savedBy"], got["revision"]), ("devanshu", note.revision + 1))
        stored = get_draft_by_id(self.conn, note.draft_id)
        self.assertEqual((stored.saved_by, stored.body), ("devanshu", text))
        self.assertEqual(got["checks"], [c.to_json() for c in stored.checks])
        self.assertEqual(self.row("Pylon")["note"], got)

    def test_changing_the_reason_line_makes_it_written(self):
        note = self.jonah_note
        same = self.ok(self.save_note(note.draft_id, note.body + " Thanks.", note.reason_line, note.revision))
        self.assertEqual((same["reasonState"], same["needsYourLine"]), ("proposed", True))
        line = "Your talk on step functions"
        got = self.ok(self.save_note(note.draft_id, f"Hi Jonah, {line} was great.", line, same["revision"]))
        self.assertEqual((got["reasonState"], got["needsYourLine"], got["reasonLine"]), ("written", False, line))

    def test_confirm_route_confirms_and_refuses_an_empty_line(self):
        note = self.jonah_note
        got = self.ok(self.confirm(note.draft_id, note.revision))
        self.assertEqual((got["reasonState"], got["needsYourLine"], got["savedBy"], got["revision"]),
                         ("confirmed", False, "devanshu", note.revision + 1))
        self.assertEqual(get_draft_by_id(self.conn, note.draft_id).reason_state, "confirmed")
        empty = save_linkedin_draft(self.conn, person_id=self.people["Turso"], body="Hi Ines, a short note.",
                                    reason_line="", facts=[], saved_by="claude", cfg=CFG, banned=BANNED,
                                    unconfirmed=UNCONFIRMED)
        self.refused(self.confirm(empty.draft_id, empty.revision), "bad_request", 400)
        self.assertEqual(get_draft_by_id(self.conn, empty.draft_id).reason_state, "proposed")

    def test_save_note_stale_revision_conflicts(self):
        note = self.lena_note
        self.refused(self.save_note(note.draft_id, "Hi Lena, new words.", note.reason_line, note.revision + 5),
                     "conflict", 409)
        self.refused(self.confirm(note.draft_id, note.revision + 5), "conflict", 409)
        self.assertEqual(get_draft_by_id(self.conn, note.draft_id).body, note.body)

    def test_an_unknown_note_is_not_found_and_a_blank_note_is_a_bad_request(self):
        self.refused(self.save_note(999999, "Hi.", "", 1), "not_found", 404)
        self.refused(self.confirm(999999, 1), "not_found", 404)
        note = self.lena_note
        self.refused(self.save_note(note.draft_id, "   ", note.reason_line, note.revision), "bad_request", 400)

    def test_a_later_claude_prepare_does_not_overwrite_the_saved_note(self):
        note = self.jonah_note
        mine = self.ok(self.save_note(note.draft_id, "Hi Jonah, my own words about step functions.",
                                      note.reason_line, note.revision))
        before = tuple(self.conn.execute("SELECT * FROM linkedin_draft WHERE draft_id = ?", (note.draft_id,)).fetchone())
        target = Target(company_id=self.ids["Inngest"], name="Inngest", domain="inngest.example", lane="level_role",
                        role=None, reasons=(), post=None, known_people=())
        reply = {"company_id": self.ids["Inngest"], "none_reason": "", "candidates": [{
            "name": "Jonah Park", "title": "Software Engineer", "role_kind": "engineer", "linkedin_url": None,
            "email": None,
            "sources": [{"kind": "team", "url": "https://inngest.example/team", "label": "Team page",
                         "quote": "Jonah Park, Software Engineer at Inngest"}],
            "note": {"body": "Hi Jonah, I saw your talk and would like to hear more.", "reason_line": "I saw your talk",
                     "facts": []}}]}
        got = store_company_result(self.conn, target, reply, run_id=1, version="pv-test", today=date.fromisoformat(DAY),
                                   cfg=CFG, banned=BANNED, unconfirmed=UNCONFIRMED)
        self.assertEqual(got.drafts, 0)
        self.assertTrue(any("DraftLocked" in p for p in got.problems), got.problems)
        after = tuple(self.conn.execute("SELECT * FROM linkedin_draft WHERE draft_id = ?", (note.draft_id,)).fetchone())
        self.assertEqual(after, before)
        self.assertEqual(self.row("Inngest")["note"], mine)
