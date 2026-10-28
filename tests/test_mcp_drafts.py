"""The draft tools (#13, plan Tasks 8 and 9, lean v1): save_draft and save_followup, called through the official
SDK's client on a scratch store with the fixture banned list.

A draft is always saved drafted by claude with the #9 checks of `outreach.drafts.checks.run_checks` stored on it; a
content block still saves and says what to fix; a precondition (no address, no-go, said no, company closed or
removed, already emailed, his edit, approved) refuses and stores nothing. Nothing here approves."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from outreach.drafting import brief as drafting_brief
from outreach.drafting.voice import load_voice_pack
from tests import apiseed as seed
from tests.mcptest import NOW, McpCase, fingerprint
from tests.test_drafts_checks import BODY, FIXTURE_BANNED, PORTFOLIO, PROJECT
from tests.test_mcp_reads import no_go

FIXTURE_MAIL = Path(__file__).parent / "fixtures" / "plan"  # its me.md is the evidence the brief reads
SUBJECT = "Your queue rewrite and the new grad backend role"
HOOK = "His post on the queue rewrite"
HIS_WORDS = "He has edited this one. Leave it."
FOLLOW = "Hi Sam, one more note on the ledger idea from my last email. Happy to send a short demo. Thanks, Devanshu"


class DraftCase(McpCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"OUTREACH_BANNED_PHRASES": str(FIXTURE_BANNED),
                                               "OUTREACH_MAIL_HOME": str(FIXTURE_MAIL)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.acme = seed.company(self.db, "Acme", "working")
        self.pat = seed.person(self.db, self.acme, "Pat Lee", email="pat@acme.example")
        self.mx("acme.example", "ok")

    def mx(self, domain, status, at="2026-11-10T14:00:00Z"):
        self.db.execute("INSERT INTO mx_check(domain, status, checked_at) VALUES (?, ?, ?) ON CONFLICT(domain) "
                        "DO UPDATE SET status = excluded.status, checked_at = excluded.checked_at",
                        (domain, status, at))

    def draft_args(self, *, brief: bool = True, **changes) -> dict:
        args = {"person_id": self.pat, "subject": SUBJECT, "body": BODY, "hook": HOOK, "hook_on": "2026-11-02",
                "project": "runner"}
        args.update(changes)
        if brief and "brief_id" not in args:
            args["brief_id"] = self.brief_id(args["person_id"])
        return {k: v for k, v in args.items() if v is not None}

    def brief_id(self, person_id=None) -> str:
        """What draft_brief gives for this person now, computed here so an unknown person id can be saved too."""
        return drafting_brief.brief_id(load_voice_pack(), drafting_brief.read_evidence(), person_id or self.pat)

    def first(self, person_id=None):
        return self.db.execute(
            "SELECT m.* FROM message m JOIN touch t ON t.touch_id = m.touch_id WHERE t.person_id = ? AND m.seq = 0",
            (person_id or self.pat,)).fetchone()

    def message_events(self):
        return [(e["kind"], e["actor"]) for e in self.db.execute(
            "SELECT kind, actor FROM event WHERE entity = 'message' ORDER BY event_id")]

    def codes(self, checks):
        return [c["code"] for c in checks]


class SaveDraftTest(DraftCase):
    def test_saves_a_drafted_first_email_by_claude_with_its_checks_and_returns_blocks_warns_and_passes(self):
        got = self.ok("save_draft", self.draft_args())
        row = self.first()
        self.assertEqual((got["message_id"], got["state"], got["revision"], got["created"], got["unchanged"]),
                         (row["message_id"], "drafted", 1, True, False))
        self.assertEqual((got["blocks"], got["warns"]), ([], []))
        self.assertGreater(got["passes"], 5)
        self.assertGreater(got["word_count"], 80)
        self.assertEqual((row["seq"], row["state"], row["saved_by"], row["approved_at"], row["due_on"],
                          row["subject"], row["body"], row["project"]),
                         (0, "drafted", "claude", None, None, SUBJECT, BODY, "runner"))
        stored = json.loads(row["checks"])
        self.assertTrue(stored)
        self.assertEqual({c["severity"] for c in stored}, {"pass"})
        touch = self.db.execute("SELECT state, origin, first_sent_at FROM touch WHERE person_id = ?",
                                (self.pat,)).fetchone()
        self.assertEqual(tuple(touch), ("open", "sender", None))
        self.assertEqual(self.message_events(), [("drafted", "claude")])

    def test_result_check_entries_hold_only_code_title_detail_and_fix(self):
        got = self.ok("save_draft", self.draft_args(body=BODY + "\nhttps://example.com/three"))
        self.assertEqual(set(got), {"message_id", "state", "revision", "created", "unchanged", "word_count",
                                    "blocks", "warns", "passes"})
        for entry in got["blocks"] + got["warns"]:
            self.assertEqual(set(entry), {"code", "title", "detail", "fix"})

    def test_a_content_block_still_saves_and_says_what_to_fix(self):
        got = self.ok("save_draft", self.draft_args(body=BODY + "\nhttps://example.com/three"))
        self.assertEqual(self.codes(got["blocks"]), ["too_many_links"])
        self.assertIn("At most 2", got["blocks"][0]["detail"])
        row = self.first()
        self.assertEqual(row["state"], "drafted")
        self.assertIn("too_many_links", [c["code"] for c in json.loads(row["checks"]) if c["severity"] == "block"])

    def test_hook_on_appends_the_date_the_checks_read(self):
        self.ok("save_draft", self.draft_args())
        self.assertEqual(self.first()["hook"], f"{HOOK} (2026-11-02)")
        got = self.ok("save_draft", self.draft_args(hook_on="2026-09-01"))
        self.assertIn("stale_hook", self.codes(got["warns"]))
        self.ok("save_draft", self.draft_args(hook_on=None, hook="Their launch post"))
        self.assertEqual(self.first()["hook"], "Their launch post")

    def test_a_future_or_unreadable_hook_on_and_a_hook_already_dated_are_bad_input(self):
        for changes in ({"hook_on": "2026-11-11"}, {"hook_on": "2026-02-30"}, {"hook_on": "11/02/2026"},
                        {"hook": f"{HOOK} (2026-11-01)"}, {"hook": "   "}, {"body": "   "}):
            with self.subTest(changes=changes):
                self.assertEqual(self.error("save_draft", self.draft_args(**changes))["code"], "bad_input")
        self.assertIsNone(self.first())

    def test_state_saved_by_seq_approved_and_a_text_revision_are_bad_input(self):
        for extra in ({"state": "approved"}, {"saved_by": "devanshu"}, {"seq": 1}, {"approved": True},
                      {"revision": "1"}, {"message_id": 1}):
            with self.subTest(extra=extra):
                self.assertEqual(self.error("save_draft", self.draft_args(**extra))["code"], "bad_input")
        self.assertIsNone(self.first())

    def test_a_resave_replaces_claudes_draft_and_bumps_the_revision_once(self):
        self.ok("save_draft", self.draft_args())
        got = self.ok("save_draft", self.draft_args(subject="The queue rewrite at Acme", revision=1))
        self.assertEqual((got["revision"], got["created"], got["unchanged"]), (2, False, False))
        row = self.first()
        self.assertEqual((row["subject"], row["revision"], row["saved_by"], row["state"]),
                         ("The queue rewrite at Acme", 2, "claude", "drafted"))
        self.assertEqual(self.message_events(), [("drafted", "claude"), ("redrafted", "claude")])

    def test_an_identical_resave_changes_nothing_and_adds_no_event(self):
        self.ok("save_draft", self.draft_args())
        before = fingerprint(self.db)
        got = self.ok("save_draft", self.draft_args())
        self.assertEqual((got["revision"], got["created"], got["unchanged"]), (1, False, True))
        self.assertEqual(fingerprint(self.db), before)

    def test_a_stale_revision_is_refused_and_the_row_is_unchanged(self):
        self.ok("save_draft", self.draft_args())
        before = fingerprint(self.db)
        err = self.error("save_draft", self.draft_args(subject="Another subject", revision=5))
        self.assertEqual(err["code"], "stale_revision")
        self.assertEqual(fingerprint(self.db), before)

    def test_a_draft_he_saved_or_approved_is_refused_in_his_words(self):
        self.ok("save_draft", self.draft_args())
        self.db.execute("UPDATE message SET saved_by = 'devanshu' WHERE message_id = ?", (self.first()["message_id"],))
        before = fingerprint(self.db)
        err = self.error("save_draft", self.draft_args(subject="Mine now"))
        self.assertEqual((err["code"], err["message"]), ("edited_by_devanshu", HIS_WORDS))
        self.assertEqual(fingerprint(self.db), before)
        ann = seed.person(self.db, self.acme, "Ann Approved", email="ann@acme.example")
        seed.message(self.db, seed.touch(self.db, ann), 0, "approved")
        err = self.error("save_draft", self.draft_args(person_id=ann))
        self.assertEqual((err["code"], err["message"]), ("approved_or_later", HIS_WORDS))

    def test_each_precondition_refuses_and_stores_nothing(self):
        def person(name, **kw):
            return seed.person(self.db, self.acme, name, **kw)

        linked = person("Lin Kedin", channel="linkedin")
        banned = person("Nora Nogo", email="nora@acme.example")
        no_go(self.db, person_id=banned)
        said = person("Sid No", email="sid@acme.example")
        seed.touch(self.db, said, "replied", reply_kind="said_no")
        sent = person("Sam Sent", email="sam@acme.example")
        seed.send_first(self.db, seed.touch(self.db, sent))
        skipped = person("Skip Ped", email="skip@acme.example")
        mid = seed.message(self.db, seed.touch(self.db, skipped), 0)
        self.db.execute("UPDATE message SET state = 'skipped' WHERE message_id = ?", (mid,))
        closed_co = seed.company(self.db, "Closed Co", "closed")
        at_closed = seed.person(self.db, closed_co, "Cal Closed", email="cal@closed.example")
        removed_co = seed.company(self.db, "Gone Co", "removed")
        at_removed = seed.person(self.db, removed_co, "Rem Oved", email="rem@gone.example")
        nogo_co = seed.company(self.db, "Nogo Co")
        no_go(self.db, company_id=nogo_co)
        at_nogo = seed.person(self.db, nogo_co, "Nat Nogo", email="nat@nogo.example")
        before = fingerprint(self.db)
        for pid, code in ((linked, "no_email"), (banned, "person_no_go"), (said, "said_no"),
                          (sent, "already_emailed"), (skipped, "skipped"), (at_closed, "company_closed"),
                          (at_removed, "company_removed"), (at_nogo, "company_no_go"), (999, "not_found")):
            with self.subTest(code=code):
                err = self.error("save_draft", self.draft_args(person_id=pid))
                self.assertEqual(err["code"], code)
                self.assertTrue(err["message"] and err["fix"])
        self.assertEqual(fingerprint(self.db), before)

    def test_mark_no_go_alone_does_not_stop_a_draft_until_he_confirms(self):
        self.ok("mark_no_go", {"person_id": self.pat, "reason": "not_a_fit", "note": "Works in sales."})
        self.assertEqual(self.ok("save_draft", self.draft_args())["blocks"], [])

    def test_the_mail_server_comes_from_the_cache_only(self):
        self.db.execute("DELETE FROM mx_check")
        self.assertIn("mail_server_unknown", self.codes(self.ok("save_draft", self.draft_args())["warns"]))
        self.mx("acme.example", "ok", at="2026-11-08T10:00:00Z")  # older than a day
        self.assertIn("mail_server_unknown", self.codes(self.ok("save_draft", self.draft_args(
            subject="Second try"))["warns"]))
        self.mx("acme.example", "none")
        self.assertIn("no_mail_server", self.codes(self.ok("save_draft", self.draft_args(
            subject="Third try"))["blocks"]))

    def test_a_near_identical_colleague_draft_blocks_and_names_the_other_message(self):
        lee = seed.person(self.db, self.acme, "Lee Colleague", email="lee@acme.example")
        other = seed.message(self.db, seed.touch(self.db, lee), 0, subject="Something else", body=BODY)
        got = self.ok("save_draft", self.draft_args())
        block = next(c for c in got["blocks"] if c["code"] == "near_identical")
        self.assertEqual(block["fix"], {"kind": "compare", "otherMessageId": other})

    def test_an_unreadable_banned_list_blocks_and_still_saves(self):
        missing = os.path.join(os.path.dirname(self.path), "no-banned-list.txt")
        with mock.patch.dict(os.environ, {"OUTREACH_BANNED_PHRASES": missing}):
            got = self.ok("save_draft", self.draft_args())
        self.assertIn("banned_list_missing", self.codes(got["blocks"]))
        self.assertEqual(self.first()["state"], "drafted")

    def test_a_banned_phrase_warns(self):
        got = self.ok("save_draft", self.draft_args(body=BODY.replace("I build", "I am passionate about building")))
        self.assertIn("banned_phrase", self.codes(got["warns"]))


class BriefGateTest(DraftCase):
    """save_draft refuses a draft made without a current brief (#135): nothing is stored."""

    def args_with(self, brief_id, **changes):
        return self.draft_args(brief_id=brief_id, **changes)

    def test_save_without_a_brief_id_is_refused_by_the_schema(self):
        err = self.error("save_draft", self.draft_args(brief=False))
        self.assertEqual(err["code"], "bad_input")
        self.assertIsNone(self.first())

    def test_a_wrong_brief_id_is_refused_and_stores_nothing(self):
        before = fingerprint(self.db)
        err = self.error("save_draft", self.args_with("0" * 16))
        self.assertEqual(err["code"], "stale_brief")
        self.assertIn("draft_brief", err["fix"])
        self.assertEqual(fingerprint(self.db), before)

    def test_another_persons_brief_is_refused(self):
        other = seed.person(self.db, self.acme, "Quinn Ray", email="quinn@acme.example")
        err = self.error("save_draft", self.args_with(self.brief_id(other)))
        self.assertEqual(err["code"], "stale_brief")
        self.assertIsNone(self.first())

    def test_a_voice_file_changed_after_the_brief_makes_it_stale(self):
        copy = tempfile.mkdtemp(prefix="voice-copy-")
        self.addCleanup(shutil.rmtree, copy, ignore_errors=True)
        shutil.copytree(os.environ["OUTREACH_VOICE_DIR"], copy, dirs_exist_ok=True)
        with mock.patch.dict(os.environ, {"OUTREACH_VOICE_DIR": copy}):
            brief = self.brief_id()
            with open(Path(copy) / "lens" / "voice.md", "a", encoding="utf-8") as f:
                f.write("\nnew rule")
            err = self.error("save_draft", self.args_with(brief))
        self.assertEqual(err["code"], "stale_brief")
        self.assertIsNone(self.first())

    def test_an_unreadable_voice_folder_refuses_with_voice_unreadable(self):
        brief = self.brief_id()
        empty = tempfile.mkdtemp(prefix="voice-empty-")
        self.addCleanup(shutil.rmtree, empty, ignore_errors=True)
        with mock.patch.dict(os.environ, {"OUTREACH_VOICE_DIR": empty}):
            err = self.error("save_draft", self.args_with(brief))
        self.assertEqual(err["code"], "voice_unreadable")
        self.assertIsNone(self.first())

    def test_the_saved_checks_record_the_brief(self):
        brief = self.brief_id()
        self.ok("save_draft", self.args_with(brief))
        stored = {c["code"]: c for c in json.loads(self.first()["checks"])}
        self.assertEqual(stored["voice_brief"]["severity"], "pass")
        self.assertIn(brief, stored["voice_brief"]["detail"])

    def test_voice_warns_come_back_and_the_draft_still_saves(self):
        brief = self.brief_id()
        body = BODY.replace("I can send anything", "I'd love to send anything")
        got = self.ok("save_draft", self.args_with(brief, body=body))
        self.assertIn("voice_eager", [w["code"] for w in got["warns"]])
        self.assertEqual(self.first()["state"], "drafted")

    def test_a_number_not_in_me_md_or_the_profile_warns_as_ungrounded(self):
        brief = self.brief_id()
        body = BODY.replace("forty thousand", "40,000")
        got = self.ok("save_draft", self.args_with(brief, body=body))
        self.assertIn("voice_ungrounded", [w["code"] for w in got["warns"]])


class GroundCase(DraftCase):
    def test_a_number_only_in_the_models_own_hook_is_still_ungrounded(self):
        brief = self.brief_id()
        body = BODY.replace("forty thousand", "777")
        got = self.ok("save_draft", self.draft_args(brief_id=brief, body=body, hook="His post on 777 queues"))
        self.assertIn("voice_ungrounded", [w["code"] for w in got["warns"]])


class SaveFollowupTest(DraftCase):
    def setUp(self):
        super().setUp()
        self.sam = seed.person(self.db, self.acme, "Sam Sent", email="sam@acme.example")
        self.sam_touch = seed.touch(self.db, self.sam)
        self.db.execute("UPDATE touch SET first_sent_at = ?, first_rfc_id = '<r1@x>', gmail_thread_id = 't1' "
                        "WHERE touch_id = ?", ("2026-11-02T14:00:00Z", self.sam_touch))
        seed.message(self.db, self.sam_touch, 0, "sent", subject="Sam and the ledger", body="First body",
                     sent_at="2026-11-02T14:00:00Z")
        self.planned = self.planned_row(self.sam_touch, 1, "2026-11-10")

    def planned_row(self, touch, seq, due_on):
        return self.db.execute("INSERT INTO message(touch_id, seq, state, due_on, created_at, updated_at) "
                               "VALUES (?, ?, 'planned', ?, ?, ?)", (touch, seq, due_on, NOW, NOW)).lastrowid

    def row(self, message_id=None):
        return self.db.execute("SELECT * FROM message WHERE message_id = ?", (message_id or self.planned,)).fetchone()

    def test_fills_a_due_planned_follow_up_with_the_re_subject_as_drafted_by_claude(self):
        listed = self.ok("followups_due")
        self.assertEqual([i["message_id"] for i in listed["items"]], [self.planned])
        got = self.ok("save_followup", {"person_id": self.sam, "seq": 1, "body": FOLLOW})
        self.assertEqual((got["message_id"], got["state"], got["revision"], got["created"], got["unchanged"]),
                         (self.planned, "drafted", 2, True, False))
        row = self.row()
        self.assertEqual((row["state"], row["subject"], row["body"], row["saved_by"], row["seq"], row["due_on"],
                          row["approved_at"]),
                         ("drafted", "Re: Sam and the ledger", FOLLOW, "claude", 1, "2026-11-10", None))
        self.assertIn("followup_subject", [c["code"] for c in json.loads(row["checks"]) if c["severity"] == "pass"])
        self.assertEqual(self.message_events(), [("drafted", "claude")])

    def test_a_follow_up_over_80_words_warns(self):
        got = self.ok("save_followup", {"person_id": self.sam, "seq": 1, "body": "Hi Sam, " + "word " * 90})
        self.assertIn("length", self.codes(got["warns"]))

    def test_a_resave_replaces_claudes_follow_up_and_an_identical_one_changes_nothing(self):
        self.ok("save_followup", {"person_id": self.sam, "seq": 1, "body": FOLLOW})
        got = self.ok("save_followup", {"person_id": self.sam, "seq": 1, "body": FOLLOW + " P.S.", "revision": 2})
        self.assertEqual((got["revision"], got["created"]), (3, False))
        before = fingerprint(self.db)
        again = self.ok("save_followup", {"person_id": self.sam, "seq": 1, "body": FOLLOW + " P.S."})
        self.assertTrue(again["unchanged"])
        self.assertEqual(fingerprint(self.db), before)
        self.assertEqual(self.error("save_followup", {"person_id": self.sam, "seq": 1, "body": "Other",
                                                      "revision": 1})["code"], "stale_revision")

    def test_a_missing_planned_row_is_no_such_followup(self):
        err = self.error("save_followup", {"person_id": self.sam, "seq": 2, "body": FOLLOW})
        self.assertEqual(err["code"], "no_such_followup")

    def test_before_the_first_email_is_sent_it_is_refused_in_plain_words(self):
        self.ok("save_draft", self.draft_args())
        err = self.error("save_followup", {"person_id": self.pat, "seq": 1, "body": FOLLOW})
        self.assertEqual(err["code"], "first_email_not_sent")
        self.assertNotIn("trigger", err["message"])

    def test_a_reply_a_hold_or_a_no_go_stops_follow_up_drafts(self):
        before = fingerprint(self.db)
        self.db.execute("UPDATE touch SET state = 'replied', replied_at = ?, reply_kind = 'interested' "
                        "WHERE touch_id = ?", (NOW, self.sam_touch))
        self.assertEqual(self.error("save_followup", {"person_id": self.sam, "seq": 1, "body": FOLLOW})["code"],
                         "replied")
        self.db.execute("UPDATE touch SET state = 'open', replied_at = NULL, reply_kind = NULL WHERE touch_id = ?",
                        (self.sam_touch,))
        self.db.execute("UPDATE company SET held_by_touch = ?, held_at = ? WHERE company_id = ?",
                        (self.sam_touch, NOW, self.acme))
        self.assertEqual(self.error("save_followup", {"person_id": self.sam, "seq": 1, "body": FOLLOW})["code"],
                         "company_held")
        self.db.execute("UPDATE company SET held_by_touch = NULL, held_at = NULL WHERE company_id = ?", (self.acme,))
        no_go(self.db, person_id=self.sam)
        self.assertEqual(self.error("save_followup", {"person_id": self.sam, "seq": 1, "body": FOLLOW})["code"],
                         "person_no_go")
        self.assertEqual(self.row()["state"], "planned")

    def test_a_follow_up_due_more_than_a_week_out_is_not_due_yet(self):
        later = self.planned_row(self.sam_touch, 2, "2026-11-30")
        err = self.error("save_followup", {"person_id": self.sam, "seq": 2, "body": FOLLOW})
        self.assertEqual(err["code"], "not_due")
        self.assertIn("2026-11-30", err["message"])
        self.assertEqual(self.row(later)["state"], "planned")

    def test_his_edit_approved_skipped_and_sent_follow_ups_refuse(self):
        self.ok("save_followup", {"person_id": self.sam, "seq": 1, "body": FOLLOW})
        self.db.execute("UPDATE message SET saved_by = 'devanshu' WHERE message_id = ?", (self.planned,))
        err = self.error("save_followup", {"person_id": self.sam, "seq": 1, "body": "Mine"})
        self.assertEqual((err["code"], err["message"]), ("edited_by_devanshu", HIS_WORDS))
        for state, extra in (("approved", ", approved_at = '2026-11-10T15:00:00Z'"), ("skipped", "")):
            with self.subTest(state=state):
                self.db.execute(f"UPDATE message SET state = 'drafted', saved_by = 'claude', approved_at = NULL "
                                f"WHERE message_id = ?", (self.planned,))
                self.db.execute(f"UPDATE message SET state = ?{extra} WHERE message_id = ?", (state, self.planned))
                self.assertEqual(self.error("save_followup", {"person_id": self.sam, "seq": 1, "body": "x"})["code"],
                                 "not_a_draft")

    def test_seq_bounds_and_a_subject_or_due_on_property_are_bad_input(self):
        for args in ({"seq": 0}, {"seq": 4}, {"seq": "1"}, {"subject": "Re: x"}, {"due_on": "2026-11-12"},
                     {"state": "drafted"}, {"body": ""}):
            with self.subTest(args=args):
                self.assertEqual(self.error("save_followup", {"person_id": self.sam, "seq": 1, "body": FOLLOW,
                                                              **args})["code"], "bad_input")
        self.assertEqual(self.row()["state"], "planned")

    def test_unknown_person_is_not_found(self):
        self.assertEqual(self.error("save_followup", {"person_id": 999, "seq": 1, "body": FOLLOW})["code"],
                         "not_found")


if __name__ == "__main__":
    unittest.main()
