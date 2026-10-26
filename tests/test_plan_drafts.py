import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from outreach.drafts.checks import load_banned
from outreach.plan.config import default_plan_config
from outreach.plan.drafts import DraftLocked, LinkedinDraft, confirm_reason, get_draft, save_linkedin_draft
from outreach.plan.notecheck import Fact, NoteCheck, is_ready, load_unconfirmed
from tests.plan_seed import seed_person, seed_scored_company
from tests.test_plan_notecheck import KABIR_BODY, KABIR_FACTS, KABIR_REASON
from tests.storetest import fresh_store

FIX = Path(__file__).resolve().parent / "fixtures" / "plan"
CFG = default_plan_config()
BANNED, _OK = load_banned(FIX / "banned_phrases.txt", scopes=("both", "dm"))
UNCONFIRMED = load_unconfirmed(FIX / "me.md")
REASON = "I read your post on the queue rewrite"
BODY = f"Hi Dana, {REASON} and built a similar job runner. I would like to hear how your platform team works."
FACTS = [Fact("queue rewrite", "Dana's post", "https://acme.example/blog/queue", True)]


class DraftsCase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.company = seed_scored_company(self.conn, "Acme", 5.0, ["level_role"])
        self.person = seed_person(self.conn, self.company, "Dana Doe", "engineer")

    def save(self, *, saved_by="claude", body=BODY, reason=REASON, facts=FACTS, person=None, **kw) -> LinkedinDraft:
        kw.setdefault("banned", BANNED)
        kw.setdefault("unconfirmed", UNCONFIRMED)
        return save_linkedin_draft(self.conn, person_id=person or self.person, body=body, reason_line=reason,
                                   facts=facts, saved_by=saved_by, cfg=CFG, **kw)

    def row(self, person=None):
        return self.conn.execute("SELECT * FROM linkedin_draft WHERE person_id = ?", (person or self.person,)).fetchone()

    def events(self, kind):
        return [json.loads(r[0]) for r in self.conn.execute(
            "SELECT detail FROM event WHERE entity = 'linkedin' AND kind = ?", (kind,))]


class SaveTest(DraftsCase):
    def test_claude_save_stores_proposed_and_runs_checks(self):
        d = self.save(prepared_by_version="prep-1")
        self.assertEqual((d.reason_state, d.saved_by, d.revision, d.prepared_by_version),
                         ("proposed", "claude", 1, "prep-1"))
        self.assertEqual([c.code for c in d.checks], ["reason_unconfirmed"])
        self.assertFalse(is_ready(d.checks, d.reason_state))
        bad = self.save(person=seed_person(self.conn, self.company, "Eli Eng"), body=BODY + " Synergy.")
        self.assertIn("banned_phrase", [c.code for c in bad.checks])
        self.assertEqual(bad.reason_state, "proposed")
        self.assertEqual(self.events("draft_saved")[0]["personId"], self.person)
        self.assertEqual(self.conn.execute("SELECT actor FROM event WHERE kind = 'draft_saved'").fetchone()[0], "claude")

    def test_claude_cannot_overwrite_a_confirmed_or_written_line(self):
        first = self.save()
        confirm_reason(self.conn, first.draft_id, first.revision, cfg=CFG, banned=BANNED, unconfirmed=UNCONFIRMED)
        with self.assertRaises(DraftLocked):
            self.save(body=BODY + " Edited.")
        self.assertEqual(self.row()["body"], BODY)
        other = seed_person(self.conn, self.company, "Eli Eng")
        written = self.save(saved_by="devanshu", person=other)  # a line he wrote on a new draft
        self.assertEqual(written.reason_state, "written")
        with self.assertRaises(DraftLocked):
            self.save(person=other)
        self.assertEqual(self.row(other)["saved_by"], "devanshu")

    def test_claude_cannot_overwrite_a_draft_he_saved(self):
        self.save()
        his = self.save(saved_by="devanshu", body=BODY.replace("platform", "infra"), revision=1)
        self.assertEqual((his.saved_by, his.reason_state), ("devanshu", "proposed"))
        with self.assertRaises(DraftLocked):
            self.save(body="Hi Dana, " + REASON + " and a different note.")
        row = self.row()
        self.assertEqual((row["body"], row["saved_by"], row["reason_state"]), (his.body, "devanshu", "proposed"))

    def test_the_table_check_refuses_a_claude_row_that_is_not_proposed(self):
        d = self.save()
        with self.assertRaises(Exception) as caught:
            self.conn.execute("UPDATE linkedin_draft SET reason_state = 'confirmed' WHERE draft_id = ?", (d.draft_id,))
        self.assertIn("CHECK", str(caught.exception))

    def test_claude_updates_its_own_proposal_in_place(self):
        a = self.save()
        b = self.save(body=BODY.replace("job runner", "queue"), reason=REASON, revision=a.revision)
        self.assertEqual((b.draft_id, b.revision), (a.draft_id, 2))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM linkedin_draft").fetchone()[0], 1)
        self.assertIn("queue", self.row()["body"])
        self.assertEqual(len(self.events("draft_saved")), 2)

    def test_a_claude_update_never_names_reason_state_or_saved_by(self):
        """The MCP guard (#13) grants Claude's connection no update of those two columns, so Claude's save of its own
        proposal must not name them; his save still writes them."""
        import sqlite3
        a = self.save()
        named = []

        def watch(action, _table, column, _db, _src):
            if action == sqlite3.SQLITE_UPDATE and _table == "linkedin_draft":
                named.append(column)
            return sqlite3.SQLITE_OK

        self.conn.set_authorizer(watch)
        self.addCleanup(self.conn.set_authorizer, None)
        b = self.save(body=BODY.replace("job runner", "queue"), revision=a.revision)
        self.assertEqual((b.reason_state, b.saved_by, b.revision), ("proposed", "claude", 2))
        self.assertTrue(named)
        self.assertFalse({"reason_state", "saved_by"} & set(named), named)
        named.clear()
        his = self.save(saved_by="devanshu", reason="My own line in the note", revision=2,
                        body="Hi Dana, My own line in the note.")
        self.assertEqual((his.reason_state, his.saved_by), ("written", "devanshu"))
        self.assertLessEqual({"reason_state", "saved_by"}, set(named))

    def test_his_edit_of_the_line_is_written_and_his_edit_elsewhere_keeps_state(self):
        self.save()
        body_only = self.save(saved_by="devanshu", body=BODY.replace("platform", "infra"))
        self.assertEqual((body_only.reason_state, body_only.saved_by), ("proposed", "devanshu"))
        new_line = "I read your queue rewrite post"
        line = self.save(saved_by="devanshu", body=f"Hi Dana, {new_line} and built a similar runner.", reason=new_line)
        self.assertEqual(line.reason_state, "written")
        again = self.save(saved_by="devanshu", body=f"Hi Dana, {new_line} and built a similar runner today.",
                          reason=new_line)
        self.assertEqual(again.reason_state, "written")
        self.assertTrue(is_ready(again.checks, again.reason_state))

    def test_a_new_draft_by_him_with_a_line_is_written(self):
        d = self.save(saved_by="devanshu")
        self.assertEqual(d.reason_state, "written")
        other = seed_person(self.conn, self.company, "Eli Eng")
        empty = self.save(saved_by="devanshu", person=other, reason="")
        self.assertEqual(empty.reason_state, "proposed")
        self.assertNotIn("reason_missing", [c.code for c in empty.checks])  # his note needs no reason line (#9)
        self.assertTrue(is_ready(empty.checks, empty.reason_state, author="user"))

    def test_stale_revision_raises_lookup_error(self):
        d = self.save()
        self.save(revision=d.revision)
        with self.assertRaises(LookupError):
            self.save(revision=d.revision)
        with self.assertRaises(LookupError):
            self.save(saved_by="devanshu", revision=d.revision)
        with self.assertRaises(LookupError):
            self.save(person=seed_person(self.conn, self.company, "Fay Fo"), revision=3)

    def test_one_draft_per_person_updates_in_place(self):
        self.save()
        self.save(saved_by="devanshu")
        self.save(saved_by="devanshu", body=BODY + " Thanks.")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM linkedin_draft").fetchone()[0], 1)
        self.assertEqual(self.row()["revision"], 3)

    def test_save_records_checks_json_shape(self):
        self.save(body=BODY + " Synergy.", facts=[Fact("queue rewrite", "post", "http://a.example/x", True)])
        row = self.row()
        checks = json.loads(row["checks"])
        self.assertEqual({c["code"] for c in checks}, {"banned_phrase", "fact_no_link", "reason_unconfirmed"})
        self.assertEqual(set(checks[0]), {"code", "severity", "title", "detail", "fix", "spans"})
        self.assertEqual(json.loads(row["facts"]),
                         [{"text": "queue rewrite", "label": "post", "url": "http://a.example/x", "in_note": True}])
        got = get_draft(self.conn, self.person)
        self.assertEqual(got.facts, (Fact("queue rewrite", "post", "http://a.example/x", True),))
        self.assertTrue(all(isinstance(c, NoteCheck) for c in got.checks))

    def test_facts_may_be_given_as_dicts(self):
        d = self.save(facts=[{"text": "queue rewrite", "label": "post", "url": "https://a.example/x", "in_note": True}])
        self.assertEqual(d.facts, (Fact("queue rewrite", "post", "https://a.example/x", True),))

    def test_an_unreadable_banned_list_blocks_instead_of_passing(self):
        d = self.save(banned=[])
        self.assertIn("banned_list_unreadable", [c.code for c in d.checks])

    def test_the_default_banned_list_is_read_from_config_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = replace(CFG, note={**CFG.note, "banned_path": str(Path(folder) / "none.txt")})
            d = save_linkedin_draft(self.conn, person_id=self.person, body=BODY, reason_line=REASON, facts=FACTS,
                                    saved_by="claude", cfg=missing, unconfirmed=UNCONFIRMED)
            self.assertIn("banned_list_unreadable", [c.code for c in d.checks])
            listed = replace(CFG, note={**CFG.note, "banned_path": str(FIX / "banned_phrases.txt")})
            d = save_linkedin_draft(self.conn, person_id=self.person, body=BODY + " Synergy and the dm scope: "
                                    "passionate about it.", reason_line=REASON, facts=FACTS, saved_by="claude",
                                    cfg=listed, unconfirmed=UNCONFIRMED)
            codes = [c.code for c in d.checks]
            self.assertNotIn("banned_list_unreadable", codes)
            self.assertEqual(codes.count("banned_phrase"), 2)

    def test_the_default_me_list_fails_closed_when_unreadable(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {"OUTREACH_MAIL_HOME": folder}):
            d = save_linkedin_draft(self.conn, person_id=self.person, body=BODY, reason_line=REASON, facts=FACTS,
                                    saved_by="claude", cfg=CFG, banned=BANNED)
        self.assertIn("unconfirmed_list_unreadable", [c.code for c in d.checks])

    def test_a_blank_body_or_unknown_person_is_refused(self):
        with self.assertRaises(ValueError):
            self.save(body="   ")
        with self.assertRaises(LookupError):
            self.save(person=999_999)
        with self.assertRaises(ValueError):
            self.save(saved_by="sender")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM linkedin_draft").fetchone()[0], 0)

    def test_posting_id_is_kept_when_a_later_save_gives_none(self):
        pid = self.conn.execute("SELECT posting_id FROM posting LIMIT 1").fetchone()[0]
        self.save(posting_id=pid)
        self.save(saved_by="devanshu")
        self.assertEqual(self.row()["posting_id"], pid)


class HisNoteTest(DraftsCase):
    """#9's authorship ruling (2026-10-26): his save is checked as his text, Claude's as Claude's."""

    def night_draft(self, body=f"Hi Kabir, {KABIR_REASON} I build agent infra.", **kw):
        return self.save(body=body, reason=KABIR_REASON, facts=KABIR_FACTS, **kw)

    def test_the_kabir_save_is_not_blocked_by_the_night_drafts_line_and_facts(self):
        night = self.night_draft()
        self.assertEqual([c.code for c in night.checks], ["reason_unconfirmed"])
        # the app passes the stored line and facts back with his text, as `save_note` does
        mine = self.save(saved_by="devanshu", body=KABIR_BODY, reason=night.reason_line, facts=night.facts,
                         revision=night.revision)
        self.assertEqual(len(mine.body), 297)
        self.assertEqual([c.code for c in mine.checks], [])
        self.assertEqual((mine.saved_by, mine.reason_state), ("devanshu", "proposed"))
        self.assertTrue(is_ready(mine.checks, mine.reason_state, author="user"))
        # the stored fact list now says what is in his text: the night fact is not
        self.assertEqual([f.in_note for f in mine.facts], [False])
        self.assertEqual(json.loads(self.row()["facts"])[0]["in_note"], False)
        self.assertEqual(self.events("draft_saved")[-1]["blocks"], 0)

    def test_the_same_text_saved_by_claude_keeps_the_strict_checks(self):
        d = self.save(body=KABIR_BODY, reason=KABIR_REASON, facts=KABIR_FACTS)
        self.assertEqual([c.code for c in d.checks], ["reason_not_in_note", "fact_not_in_note", "reason_unconfirmed"])
        self.assertFalse(is_ready(d.checks, d.reason_state))
        self.assertEqual([f.in_note for f in d.facts], [True])  # Claude's own claim is kept as Claude made it

    def test_his_save_recomputes_which_facts_are_in_his_text(self):
        facts = [Fact("queue rewrite", "post", "https://acme.example/blog/queue", False),
                 Fact("Series B", "press", "https://acme.example/press", True),
                 Fact("  ", "blank", "https://acme.example/x", True)]
        self.save(facts=facts)
        mine = self.save(saved_by="devanshu", body="Hi Dana, I read about the QUEUE REWRITE and liked it.",
                         facts=facts)
        self.assertEqual([f.in_note for f in mine.facts], [True, False, False])
        self.assertEqual([(f.text, f.label, f.url) for f in mine.facts], [(f.text, f.label, f.url) for f in facts])

    def test_his_note_still_blocks_on_honesty_and_the_limit(self):
        night = self.night_draft()
        revision = night.revision
        for body, code in ((KABIR_BODY + "abcd", "note_too_long"), (KABIR_BODY[:200] + " Real synergy.", "banned_phrase"),
                           (KABIR_BODY[:200] + " Agents \u2014 infra.", "dash"),
                           (KABIR_BODY[:200] + " We have a mutual connection.", "banned_phrase"),
                           (KABIR_BODY[:200] + " {{name}}", "placeholder"),
                           (KABIR_BODY[:200] + " I need H-1B support.", "unconfirmed_fact")):
            mine = self.save(saved_by="devanshu", body=body, reason=night.reason_line, facts=night.facts,
                             revision=revision)
            revision = mine.revision
            self.assertIn(code, [c.code for c in mine.checks if c.severity == "block"], body)
            self.assertFalse(is_ready(mine.checks, mine.reason_state, author="user"), body)

    def test_confirming_claudes_text_keeps_claudes_checks(self):
        night = self.night_draft(body="Hi Kabir, I build agent infra.")  # the line is not in Claude's text
        self.assertIn("reason_not_in_note", [c.code for c in night.checks])
        once = confirm_reason(self.conn, night.draft_id, night.revision, cfg=CFG, banned=BANNED,
                              unconfirmed=UNCONFIRMED)
        self.assertEqual([c.code for c in once.checks], ["reason_not_in_note", "fact_not_in_note"])
        twice = confirm_reason(self.conn, once.draft_id, once.revision, cfg=CFG, banned=BANNED,
                               unconfirmed=UNCONFIRMED)
        self.assertEqual([c.code for c in twice.checks], ["reason_not_in_note", "fact_not_in_note"])

    def test_confirming_after_his_save_keeps_his_checks(self):
        night = self.night_draft()
        mine = self.save(saved_by="devanshu", body=KABIR_BODY, reason=night.reason_line, facts=night.facts,
                         revision=night.revision)
        confirmed = confirm_reason(self.conn, mine.draft_id, mine.revision, cfg=CFG, banned=BANNED,
                                   unconfirmed=UNCONFIRMED)
        self.assertEqual((confirmed.reason_state, [c.code for c in confirmed.checks]), ("confirmed", []))

    def test_his_save_keeps_the_lock_and_revisions(self):
        night = self.night_draft()
        mine = self.save(saved_by="devanshu", body=KABIR_BODY, reason=night.reason_line, facts=night.facts,
                         revision=night.revision)
        self.assertEqual(mine.revision, night.revision + 1)
        with self.assertRaises(DraftLocked):
            self.night_draft()
        with self.assertRaises(LookupError):
            self.save(saved_by="devanshu", body=KABIR_BODY + ".", revision=night.revision)
        self.assertEqual(self.row()["body"], KABIR_BODY)


class RacingConnection:
    """Wraps a connection and runs `intervene` once, right after the first read of `linkedin_draft`: the moment
    between a writer's check and its write, when another writer gets in."""

    def __init__(self, conn, intervene):
        self._conn = conn
        self._intervene = intervene

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def execute(self, sql, *args):
        cursor = self._conn.execute(sql, *args)
        if self._intervene and sql.lstrip().upper().startswith("SELECT * FROM LINKEDIN_DRAFT"):
            rows = cursor.fetchall()
            intervene, self._intervene = self._intervene, None
            intervene(self._conn)
            return _Rows(rows)
        return cursor


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class RaceTest(DraftsCase):
    """Review fix I: two writers cannot both pass the revision check."""

    def racing_save(self, intervene, **kw):
        kw.setdefault("banned", BANNED)
        kw.setdefault("unconfirmed", UNCONFIRMED)
        return save_linkedin_draft(RacingConnection(self.conn, intervene), person_id=self.person, body=BODY,
                                   reason_line=REASON, facts=FACTS, cfg=CFG, **kw)

    def bump(self, conn):
        conn.execute("UPDATE linkedin_draft SET body = 'someone else', revision = revision + 1")

    def test_a_save_that_loses_the_race_for_an_update_raises_lookup_error_and_writes_nothing(self):
        d = self.save()
        with self.assertRaises(LookupError):
            self.racing_save(self.bump, saved_by="devanshu", revision=d.revision)
        # the simulated writer shared this connection's transaction, so the refusal undid it too: what matters is
        # that this save wrote no event and no second revision
        self.assertEqual(len(self.events("draft_saved")), 1)
        self.assertEqual(self.row()["revision"], 1)

    def test_a_claude_save_without_a_revision_cannot_overwrite_a_write_that_got_in_first(self):
        self.save()
        with self.assertRaises(LookupError):
            self.racing_save(self.bump, saved_by="claude")
        self.assertEqual(self.row()["revision"], 1)

    def test_a_first_insert_that_loses_the_race_is_a_lookup_error_not_an_integrity_error(self):
        def other_writer(conn):
            conn.execute("INSERT INTO linkedin_draft(person_id, body, saved_by, created_at, updated_at) "
                         "VALUES (?, 'theirs', 'devanshu', '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z')",
                         (self.person,))
        with self.assertRaises(LookupError):
            self.racing_save(other_writer, saved_by="claude")
        self.assertEqual(len(self.events("draft_saved")), 0)

    def test_confirm_that_loses_the_race_raises_lookup_error(self):
        d = self.save()
        racing = RacingConnection(self.conn, self.bump)
        with self.assertRaises(LookupError):
            confirm_reason(racing, d.draft_id, d.revision, cfg=CFG, banned=BANNED, unconfirmed=UNCONFIRMED)
        self.assertEqual(self.row()["reason_state"], "proposed")


class ConfirmTest(DraftsCase):
    def confirm(self, d):
        return confirm_reason(self.conn, d.draft_id, d.revision, cfg=CFG, banned=BANNED, unconfirmed=UNCONFIRMED)

    def test_confirm_reason(self):
        d = self.save()
        c = self.confirm(d)
        self.assertEqual((c.reason_state, c.saved_by, c.revision), ("confirmed", "devanshu", 2))
        self.assertEqual([x.code for x in c.checks], [])
        self.assertTrue(is_ready(c.checks, c.reason_state))
        event = self.conn.execute("SELECT actor FROM event WHERE kind = 'draft_confirmed'").fetchone()
        self.assertEqual(event[0], "devanshu")

    def test_a_blocked_note_stays_not_ready_after_confirming(self):
        d = self.save(body=BODY + " Synergy.")
        c = self.confirm(d)
        self.assertEqual(c.reason_state, "confirmed")
        self.assertFalse(is_ready(c.checks, c.reason_state))

    def test_confirm_refuses_empty_line(self):
        d = self.save(reason="")
        with self.assertRaises(ValueError):
            self.confirm(d)
        self.assertEqual(self.row()["reason_state"], "proposed")

    def test_confirm_with_a_stale_revision_or_missing_draft(self):
        d = self.save()
        with self.assertRaises(LookupError):
            confirm_reason(self.conn, d.draft_id, d.revision + 1, cfg=CFG, banned=BANNED, unconfirmed=UNCONFIRMED)
        with self.assertRaises(LookupError):
            confirm_reason(self.conn, 999_999, 1, cfg=CFG, banned=BANNED, unconfirmed=UNCONFIRMED)

    def test_after_confirming_claude_is_locked_out(self):
        self.confirm(self.save())
        with self.assertRaises(DraftLocked):
            self.save()


class GetDraftTest(DraftsCase):
    def test_get_draft_is_none_without_one(self):
        self.assertIsNone(get_draft(self.conn, self.person))


if __name__ == "__main__":
    unittest.main()
