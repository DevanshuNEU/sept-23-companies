"""save_linkedin_note (#13, plan Task 11), called through the official SDK's client on a scratch store with the
plan's fixture banned list and me.md.

The note goes through #15's one writer, `outreach.plan.drafts.save_linkedin_draft(saved_by='claude')`, so its reason
line is always `proposed`: only Devanshu confirms or writes it. A note he confirmed, wrote or saved himself is never
overwritten (`DraftLocked` comes back as `draft_locked`). The checks are #15's `check_note`; a block still saves."""
import json
import os
import re
import sqlite3
import unittest
from pathlib import Path
from unittest import mock

from outreach.mcp import server
from outreach.mcp.guard import install_triggers, open_guarded
from outreach.plan.drafts import confirm_reason
from outreach.store import connect
from tests import apiseed as seed
from tests.mcptest import McpCase, fingerprint
from tests.test_mcp_reads import no_go
from tests.test_mcp_write_grants import table_hashes

FIX = Path(__file__).resolve().parent / "fixtures" / "plan"
REASON = "I read your post on the queue rewrite"
BODY = f"Hi Lin, {REASON} and built a similar job runner. I would like to hear how your team works."
FACT = {"text": "queue rewrite", "label": "Blog", "url": "https://acme.example/blog/queue", "in_note": True}


def spec(name: str):
    return next(s for s in server.tool_specs() if s.name == name)


class NoteCase(McpCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"OUTREACH_BANNED_PHRASES": str(FIX / "banned_phrases.txt"),
                                               "OUTREACH_MAIL_HOME": str(FIX)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.acme = seed.company(self.db, "Acme", "working")
        self.lin = seed.person(self.db, self.acme, "Lin Kedin", channel="linkedin")

    def args(self, **changes) -> dict:
        out = {"person_id": self.lin, "body": BODY, "reason_line": REASON, "facts": [FACT]}
        out.update(changes)
        return {k: v for k, v in out.items() if v is not None}

    def draft(self, person_id=None):
        return self.db.execute("SELECT * FROM linkedin_draft WHERE person_id = ?",
                               (person_id or self.lin,)).fetchone()

    def note_events(self):
        return [(e["kind"], e["actor"]) for e in self.db.execute(
            "SELECT kind, actor FROM event WHERE entity = 'linkedin' ORDER BY event_id")]


class SaveNoteTest(NoteCase):
    def test_claude_note_is_stored_proposed_with_checks_and_needs_his_line(self):
        got = self.ok("save_linkedin_note", self.args())
        row = self.draft()
        self.assertEqual((got["draft_id"], got["revision"], got["created"], got["unchanged"]),
                         (row["draft_id"], 1, True, False))
        self.assertEqual((got["reason_state"], got["needs_his_line"], got["max_chars"]), ("proposed", True, 300))
        self.assertEqual(got["blocks"], [])
        self.assertEqual([w["code"] for w in got["warns"]], ["reason_unconfirmed"])
        self.assertEqual((row["body"], row["reason_line"], row["reason_state"], row["saved_by"],
                          row["prepared_by_version"]), (BODY, REASON, "proposed", "claude", "mcp:1"))
        self.assertEqual(json.loads(row["facts"]), [FACT])
        self.assertEqual([c["code"] for c in json.loads(row["checks"])], ["reason_unconfirmed"])
        self.assertEqual(self.note_events(), [("draft_saved", "claude")])
        self.assertIn("only Devanshu", spec("save_linkedin_note").description)

    def test_reason_state_saved_by_confirm_and_actor_are_bad_input(self):
        before = fingerprint(self.db)
        for extra in ({"reason_state": "confirmed"}, {"saved_by": "devanshu"}, {"confirm": True},
                      {"actor": "devanshu"}, {"prepared_by_version": "x"}, {"draft_id": 1}):
            with self.subTest(extra=extra):
                self.assertEqual(self.error("save_linkedin_note", self.args(**extra))["code"], "bad_input")
        for bad in ({"body": ""}, {"body": "x" * 2001}, {"reason_line": ""}, {"facts": [FACT] * 9},
                    {"facts": [{**FACT, "in_note": "yes"}]}, {"facts": [{**FACT, "extra": 1}]},
                    {"person_id": "1"}, {"revision": 0}):
            with self.subTest(bad=bad):
                self.assertEqual(self.error("save_linkedin_note", self.args(**bad))["code"], "bad_input")
        self.assertEqual(fingerprint(self.db), before)

    def test_a_confirmed_line_cannot_be_written_by_a_hostile_statement_on_the_guard_or_without_it(self):
        self.ok("save_linkedin_note", self.args())
        draft_id = self.draft()["draft_id"]
        grants = spec("save_linkedin_note").grants
        for sql in ("UPDATE linkedin_draft SET reason_state = 'confirmed' WHERE draft_id = ?",
                    "UPDATE linkedin_draft SET reason_state = 'written', saved_by = 'devanshu' WHERE draft_id = ?",
                    "UPDATE linkedin_draft SET saved_by = 'devanshu' WHERE draft_id = ?"):
            with self.subTest(layer="the guard with the tool's grants", sql=sql):
                conn = open_guarded(grants, path=self.path)
                self.addCleanup(conn.close)
                with self.assertRaises(sqlite3.DatabaseError):
                    conn.execute(sql, (draft_id,))
            with self.subTest(layer="the triggers alone", sql=sql):
                conn = connect(self.path)
                self.addCleanup(conn.close)
                install_triggers(conn)
                with self.assertRaises(sqlite3.IntegrityError) as caught:
                    conn.execute(sql, (draft_id,))
                self.assertIn("mcp guard", str(caught.exception))
        with self.assertRaises(sqlite3.IntegrityError):  # no guard at all: the foundation CHECK still refuses
            self.db.execute("UPDATE linkedin_draft SET reason_state = 'confirmed', saved_by = 'claude' "
                            "WHERE draft_id = ?", (draft_id,))
        self.assertEqual((self.draft()["reason_state"], self.draft()["saved_by"]), ("proposed", "claude"))

    def test_a_locked_draft_is_refused_and_stays_byte_identical(self):
        def his(how):
            pid = seed.person(self.db, self.acme, f"Pat {how}", channel="linkedin")
            self.ok("save_linkedin_note", self.args(person_id=pid))
            row = self.draft(pid)
            if how == "confirmed":
                confirm_reason(self.db, row["draft_id"], row["revision"], banned=[("zzz", re.compile("zzz"))],
                               unconfirmed=[])
            elif how == "written":
                self.db.execute("UPDATE linkedin_draft SET reason_line = 'My own line', reason_state = 'written', "
                                "saved_by = 'devanshu' WHERE draft_id = ?", (row["draft_id"],))
            else:  # his body-only save leaves the line proposed
                self.db.execute("UPDATE linkedin_draft SET body = 'His words', saved_by = 'devanshu' "
                                "WHERE draft_id = ?", (row["draft_id"],))
            return pid

        for how, word in (("confirmed", "confirmed"), ("written", "wrote"), ("saved", "edited")):
            with self.subTest(how=how):
                pid = his(how)
                before = fingerprint(self.db)
                err = self.error("save_linkedin_note", self.args(person_id=pid, body=BODY + " Thanks."))
                self.assertEqual(err["code"], "draft_locked")
                self.assertIn(word, err["message"])
                self.assertTrue(err["fix"])
                same = self.error("save_linkedin_note", self.args(person_id=pid))  # even an identical save
                self.assertEqual(same["code"], "draft_locked")
                self.assertEqual(fingerprint(self.db), before)

    def test_stale_revision_conflicts(self):
        self.ok("save_linkedin_note", self.args())
        before = fingerprint(self.db)
        err = self.error("save_linkedin_note", self.args(body=BODY + " Thanks.", revision=7))
        self.assertEqual(err["code"], "stale_revision")
        self.assertTrue(err["fix"])
        self.assertEqual(fingerprint(self.db), before)
        other = seed.person(self.db, self.acme, "Ned New", channel="linkedin")
        self.assertEqual(self.error("save_linkedin_note", self.args(person_id=other, revision=1))["code"],
                         "stale_revision")  # a revision for a note that is not there yet
        got = self.ok("save_linkedin_note", self.args(body=BODY + " Thanks.", revision=1))
        self.assertEqual(got["revision"], 2)

    def test_one_draft_per_person_updates_in_place_and_an_identical_save_changes_nothing(self):
        first = self.ok("save_linkedin_note", self.args())
        second = self.ok("save_linkedin_note", self.args(body=BODY + " Thanks."))
        self.assertEqual((second["draft_id"], second["revision"], second["created"], second["unchanged"]),
                         (first["draft_id"], 2, False, False))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM linkedin_draft").fetchone()[0], 1)
        self.assertEqual(self.draft()["body"], BODY + " Thanks.")
        before = fingerprint(self.db)
        again = self.ok("save_linkedin_note", self.args(body=BODY + " Thanks."))
        self.assertEqual((again["draft_id"], again["revision"], again["created"], again["unchanged"]),
                         (first["draft_id"], 2, False, True))
        self.assertEqual(again["warns"], second["warns"])
        self.assertEqual(fingerprint(self.db), before)
        self.assertEqual(self.note_events(), [("draft_saved", "claude"), ("draft_saved", "claude")])

    def test_blocks_come_back_with_codes_and_fixes(self):
        long_body = BODY + " " + "More words here." * 20  # past the 300 characters a note may have
        got = self.ok("save_linkedin_note", self.args(
            body=long_body.replace("similar", "similar " + chr(0x2014)),
            facts=[FACT, {"text": "talk", "label": "Talk", "url": "https://www.linkedin.com/posts/x", "in_note": False}]))
        codes = [b["code"] for b in got["blocks"]]
        self.assertEqual(codes, ["note_too_long", "dash", "fact_no_link"])
        for b in got["blocks"]:
            self.assertEqual(set(b), {"code", "title", "detail", "fix"})
            self.assertTrue(b["fix"])
        row = self.draft()  # a block still saves, so he and Claude see it
        self.assertEqual(row["reason_state"], "proposed")
        self.assertEqual([c["code"] for c in json.loads(row["checks"]) if c["severity"] == "block"], codes)

    def test_a_removed_company_refuses_the_note(self):
        gone = seed.company(self.db, "Gone Co", "removed")
        at_gone = seed.person(self.db, gone, "Rem Oved", channel="linkedin")
        closed = seed.company(self.db, "Closed Co", "closed")
        at_closed = seed.person(self.db, closed, "Cal Closed", channel="linkedin")
        banned = seed.person(self.db, self.acme, "Nora Nogo", channel="linkedin")
        no_go(self.db, person_id=banned)
        said = seed.person(self.db, self.acme, "Sid No", email="sid@acme.example")
        seed.touch(self.db, said, "replied", reply_kind="said_no")
        nogo_co = seed.company(self.db, "Nogo Co")
        no_go(self.db, company_id=nogo_co)
        at_nogo = seed.person(self.db, nogo_co, "Nat Nogo", channel="linkedin")
        before = fingerprint(self.db)
        for pid, code in ((at_gone, "company_removed"), (at_closed, "company_closed"), (banned, "person_no_go"),
                          (said, "said_no"), (at_nogo, "company_no_go"), (999, "not_found")):
            with self.subTest(code=code):
                err = self.error("save_linkedin_note", self.args(person_id=pid))
                self.assertEqual(err["code"], code)
                self.assertTrue(err["message"] and err["fix"])
        self.assertEqual(fingerprint(self.db), before)

    def test_posting_must_belong_to_the_persons_company(self):
        mine = seed.posting(self.db, self.acme)
        other = seed.posting(self.db, seed.company(self.db, "Bolt"))
        before = fingerprint(self.db)
        for posting_id in (other, 999):
            with self.subTest(posting_id=posting_id):
                self.assertEqual(self.error("save_linkedin_note", self.args(posting_id=posting_id))["code"],
                                 "bad_input")
        self.assertEqual(fingerprint(self.db), before)
        self.ok("save_linkedin_note", self.args(posting_id=mine))
        self.assertEqual(self.draft()["posting_id"], mine)

    def test_the_tool_writes_nothing_outside_its_grants(self):
        tool = spec("save_linkedin_note")
        self.assertEqual(set(tool.grants), {"linkedin_draft", "event"})
        self.assertTrue(tool.grants["linkedin_draft"].insert)
        self.assertNotIn("reason_state", tool.grants["linkedin_draft"].update)
        self.assertNotIn("saved_by", tool.grants["linkedin_draft"].update)
        drafted = seed.message(self.db, seed.touch(self.db, seed.person(self.db, self.acme, "Pat Lee")), 0)
        conn = open_guarded(tool.grants, path=self.path)
        self.addCleanup(conn.close)
        before = table_hashes(self.db)
        for sql, params in (
                ("UPDATE message SET state = 'approved', approved_at = '2026-11-10T15:00:00Z' WHERE message_id = ?",
                 (drafted,)),
                ("INSERT INTO no_go(person_id, reason, source, created_at) VALUES (?, 'said_no', 'claude', "
                 "'2026-11-10T15:00:00Z')", (self.lin,)),
                ("UPDATE person SET notes = 'x' WHERE person_id = ?", (self.lin,)),
                ("INSERT INTO person_source(person_id, kind, url, recorded_at) VALUES (?, 'team', "
                 "'https://acme.example/t', '2026-11-10T15:00:00Z')", (self.lin,)),
                ("DELETE FROM linkedin_draft", ())):
            with self.subTest(sql=sql):
                with self.assertRaises(sqlite3.DatabaseError):
                    conn.execute(sql, params)
        self.assertEqual(table_hashes(self.db), before)
        self.ok("save_linkedin_note", self.args())
        after = table_hashes(self.db)
        self.assertEqual({t for t in after if after[t] != before[t]}, {"linkedin_draft", "event"})


if __name__ == "__main__":
    unittest.main()
