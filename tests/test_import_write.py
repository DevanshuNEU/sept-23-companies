"""The writer (#17 Task 11): the plan through the foundation's write helpers and #31's identity, on scratch stores."""
import dataclasses
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from outreach.importer import write
from outreach.importer.manifest import Overrides
from outreach.importer.plan import build_plan, read_all
from outreach.importer.records import SourceRecord, source_digest
from outreach.importer.write import WriteResult, apply_plan
from outreach.sending.config import load_config
from tests.importtest import all_marks, fixture_manifest
from tests.storetest import fresh_store

NOW = "2026-10-20T12:00:00Z"
CFG = load_config()


def overrides(**kw) -> Overrides:
    base = dict(same_company={}, not_same_company=frozenset(), person_name={}, done_add=frozenset(),
                done_remove=frozenset(), touch_for={})
    base.update(kw)
    return Overrides(**base)


def rid(record: SourceRecord) -> str:
    return f"{record.source}:{record.source_key}"


class WriteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.manifest = fixture_manifest(Path(cls._tmp.name))
        cls.records = read_all(cls.manifest)
        cls.plan = build_plan(cls.records, overrides(), now=NOW, send_cfg=CFG)
        cls.conn = fresh_store()
        cls.result = apply_plan(cls.conn, cls.plan, now=NOW)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls._tmp.cleanup()

    # -- helpers --

    def rows(self, sql, *args):
        return self.conn.execute(sql, args).fetchall()

    def one(self, sql, *args):
        return self.conn.execute(sql, args).fetchone()

    def company(self, key):
        return self.result.ids[f"company:{key}"]

    def person(self, key):
        return self.result.ids[f"person:{key}"]

    def changed(self, record_id, change):
        out = []
        for r in self.records:
            if rid(r) == record_id:
                r = dataclasses.replace(r, raw=change(dict(r.raw)))
            out.append(r)
        return out

    # -- the brief's tests --

    def test_apply_writes_every_record_once(self):
        self.assertIsInstance(self.result, WriteResult)
        not_held = [r for r, (_, o) in self.plan.records.items() if o.outcome != "held"]
        self.assertEqual(self.one("SELECT count(*) FROM import_record")[0], len(not_held))
        stored = {(r["source"], r["source_key"]) for r in self.rows("SELECT source, source_key FROM import_record")}
        self.assertEqual(stored, {tuple(k.split(":", 1)) for k in not_held})
        for row in self.rows("SELECT * FROM import_record WHERE outcome IN ('imported', 'merged')"):
            self.assertIsNotNone(row["entity_id"], row["source_key"])
        run = self.one("SELECT * FROM job_run WHERE run_id = ?", self.result.run_id)
        self.assertEqual((run["job"], run["status"]), ("import", "ok"))
        self.assertEqual(self.one("SELECT version, last_run_id FROM source WHERE source_name = 'import'")[:],
                         ("import-1", self.result.run_id))

    def test_second_apply_changes_nothing_but_the_job_run(self):
        conn = fresh_store()
        apply_plan(conn, self.plan, now=NOW)
        before = all_marks(conn)
        again = apply_plan(conn, self.plan, now=NOW)
        after = all_marks(conn)
        self.assertEqual(after["job_run"][0], before["job_run"][0] + 1)
        self.assertEqual({k: v for k, v in after.items() if k != "job_run"},
                         {k: v for k, v in before.items() if k != "job_run"})
        self.assertEqual(sum(again.written.values()), 1)  # the job_run row
        conn.close()

    def test_touches_use_the_write_helpers_and_context_source_own(self):
        touches = self.rows("SELECT * FROM outreach_touch")
        self.assertTrue(touches)
        for t in touches:
            context = json.loads(t["context"])
            self.assertEqual((context["source"], context["lane"]), ("own", "own"))
            self.assertTrue(t["idempotency_key"].startswith("import:"), t["idempotency_key"])
        # each touch has its imported event, with the source the API reads
        for t in touches:
            event = self.one("SELECT * FROM event WHERE entity = 'outreach_touch' AND entity_id = ? "
                             "AND kind = 'imported'", t["touch_id"])
            detail = json.loads(event["detail"])
            self.assertEqual(event["actor"], "import")
            self.assertIn("importSource", detail)
            self.assertEqual(set(detail), {"importSource", "records", "estimatedDates", "roleNamedKnown",
                                           "followupsSent"})

    def test_email_history_has_touch_and_outreach_touch_and_the_sent_message(self):
        cleo = self.person("marlowdynamics/cleo.hartmann@example.com")
        touch = self.one("SELECT * FROM touch WHERE person_id = ?", cleo)
        self.assertEqual((touch["origin"], touch["state"], touch["first_rfc_id"], touch["gmail_thread_id"],
                          touch["first_sent_at"]),
                         ("import", "open", "<c03.fixture@example.com>", "th-c03", "2026-10-07T13:14:00Z"))
        [out] = self.rows("SELECT * FROM outreach_touch WHERE email_touch_id = ?", touch["touch_id"])
        self.assertEqual((out["channel"], out["sent_at"], out["person_id"]), ("email", "2026-10-07T13:14:00Z", cleo))
        messages = self.rows("SELECT * FROM message WHERE touch_id = ? ORDER BY seq", touch["touch_id"])
        self.assertEqual([(m["seq"], m["state"], m["saved_by"]) for m in messages],
                         [(0, "sent", "import"), (1, "sent", "import")])
        self.assertEqual((messages[0]["rfc_message_id"], messages[0]["gmail_message_id"], messages[0]["sent_at"]),
                         ("<c03.fixture@example.com>", "gm-c03", "2026-10-07T13:14:00Z"))
        self.assertEqual(messages[1]["rfc_message_id"], "<import-c03-1@outreach.invalid>")
        self.assertIsNotNone(self.one("SELECT 1 FROM event WHERE entity = 'message' AND entity_id = ? "
                                      "AND kind = 'import_placeholder_rfc'", messages[1]["message_id"]))
        # a historic email (contacted.txt only): a closed touch, its outreach touch, no message rows
        ned = self.one("SELECT * FROM touch WHERE person_id = ?", self.person("marlowdynamics/ned pruitt"))
        self.assertEqual((ned["state"], ned["close_reason"], ned["first_rfc_id"]), ("closed", "no_reply", None))
        self.assertEqual(self.one("SELECT count(*) FROM message WHERE touch_id = ?", ned["touch_id"])[0], 0)
        self.assertEqual(self.one("SELECT count(*) FROM outreach_touch WHERE email_touch_id = ?",
                                  ned["touch_id"])[0], 1)
        # an approved draft: a drafted message, no first send, no outreach touch, an event about the old approval
        gus = self.one("SELECT * FROM touch WHERE person_id = ?", self.person("fathomsystems/gus.albrecht@example.com"))
        self.assertIsNone(gus["first_sent_at"])
        self.assertEqual([m["state"] for m in self.rows("SELECT state FROM message WHERE touch_id = ?",
                                                        gus["touch_id"])], ["drafted"])
        self.assertEqual(self.one("SELECT count(*) FROM outreach_touch WHERE email_touch_id = ?",
                                  gus["touch_id"])[0], 0)
        self.assertIsNotNone(self.one("SELECT 1 FROM event WHERE kind = 'old_approval' AND actor = 'import'"))
        # a follow-up draft keeps its due date; a skipped contact's draft is skipped
        emil = self.one("SELECT touch_id FROM touch WHERE person_id = ?",
                        self.person("cobaltharbor/emil.strand@example.com"))[0]
        self.assertEqual([(m["seq"], m["state"], m["due_on"]) for m in
                          self.rows("SELECT * FROM message WHERE touch_id = ? ORDER BY seq", emil)],
                         [(0, "sent", None), (1, "drafted", "2026-10-20")])
        ivo = self.one("SELECT touch_id FROM touch WHERE person_id = ?",
                       self.person("granitepeaksoftware/ivo.marchetti@example.com"))[0]
        self.assertEqual([m["state"] for m in self.rows("SELECT state FROM message WHERE touch_id = ?", ivo)],
                         ["skipped"])

    def test_linkedin_touch_has_a_matching_linkedin_log_row(self):
        touches = self.rows("SELECT * FROM outreach_touch WHERE channel = 'linkedin_request'")
        self.assertEqual(len(touches), 1)
        logs = self.rows("SELECT * FROM linkedin_log WHERE person_id = ?", touches[0]["person_id"])
        self.assertEqual([(r["kind"], r["at"]) for r in logs], [("request_sent", touches[0]["sent_at"])])

    def test_imported_wins_are_marked_seen(self):
        ada = self.one("SELECT * FROM outreach_touch WHERE person_id = ? AND channel = 'email'",
                       self.person("kestrelrobotics/ada.fenwick@example.com"))
        self.assertEqual((ada["reply_kind"], ada["interview_at"]), ("interested", "2026-10-12T16:00:00Z"))
        seen = {(r["kind"], r["seen_at"]) for r in self.rows("SELECT * FROM win_seen WHERE touch_id = ?",
                                                             ada["touch_id"])}
        self.assertEqual(seen, {("replied", NOW), ("interview", NOW)})
        unseen = self.rows(
            "SELECT t.touch_id FROM outreach_touch t WHERE (t.interview_at IS NOT NULL OR t.offer_at IS NOT NULL "
            "OR t.referred_at IS NOT NULL OR t.accepted_at IS NOT NULL OR t.reply_kind = 'interested') "
            "AND NOT EXISTS (SELECT 1 FROM win_seen w WHERE w.touch_id = t.touch_id)")
        self.assertEqual(unseen, [])

    def test_hand_evidence_is_locked_and_written_once(self):
        lumen = self.company("lumenfreight")
        hand = self.rows("SELECT * FROM evidence WHERE company_id = ? AND source_kind = 'hand'", lumen)
        self.assertEqual([(r["fact"], r["value"], r["locked"], r["source_ref"]) for r in hand],
                         [("sponsorship", "no", 1, "import:data_js:company:lumen")])
        conn = fresh_store()
        apply_plan(conn, self.plan, now=NOW)
        apply_plan(conn, self.plan, now=NOW)
        self.assertEqual(conn.execute("SELECT count(*) FROM evidence WHERE source_kind = 'hand'").fetchone()[0], 1)
        conn.close()

    def test_held_plan_is_refused_before_any_write(self):
        held = build_plan(self.changed("contacts_json:contact:c05", lambda r: {**r, "status": "sending"}),
                          overrides(), now=NOW, send_cfg=CFG)
        conn = fresh_store()
        before = all_marks(conn)
        with self.assertRaises(ValueError) as cm:
            apply_plan(conn, held, now=NOW)
        self.assertIn("contacts_json:contact:c05", str(cm.exception))
        self.assertEqual(all_marks(conn), before)
        conn.close()

    def test_changed_backup_adds_the_new_outcome_and_overwrites_nothing(self):
        def replied(raw):
            return {**raw, "stage": "Replied", "repliedOn": "2026-10-08"}

        def interviewing(raw):
            return {**raw, "stage": "Interviewing", "repliedOn": "2026-10-09"}
        first = build_plan(self.changed("local_storage:company:lumen", replied), overrides(), now=NOW, send_cfg=CFG)
        second = build_plan(self.changed("local_storage:company:lumen", interviewing), overrides(), now=NOW,
                            send_cfg=CFG)
        conn = fresh_store()
        ids = apply_plan(conn, first, now=NOW).ids
        bram = ids["person:lumenfreight/bram.okafor@example.com"]
        sql = "SELECT replied_at, interview_at FROM outreach_touch WHERE person_id = ? AND channel = 'email'"
        self.assertEqual(tuple(conn.execute(sql, (bram,)).fetchone()), ("2026-10-08T16:00:00Z", None))
        apply_plan(conn, second, now=NOW)
        replied_at, interview_at = conn.execute(sql, (bram,)).fetchone()
        self.assertEqual(replied_at, "2026-10-08T16:00:00Z")  # the stored date stays
        self.assertIsNotNone(interview_at)  # the new fact is added
        conflicts = conn.execute("SELECT * FROM review_item WHERE evidence_key LIKE 'import:outcome_conflict:%'"
                                 ).fetchall()
        self.assertEqual(len(conflicts), 1)
        self.assertEqual((conflicts[0]["kind"], conflicts[0]["raised_by"]), ("flagged", "import"))
        self.assertEqual(conn.execute("SELECT count(*) FROM import_record WHERE source = 'local_storage' "
                                      "AND source_key = 'company:lumen'").fetchone()[0], 2)
        conn.close()

    def test_failure_mid_run_keeps_finished_companies_and_ends_partial(self):
        conn = fresh_store()
        real = write._Writer.write_company
        calls = []

        def flaky(self_, key):
            calls.append(key)
            if len(calls) == 2:
                raise RuntimeError("disk on fire")
            return real(self_, key)
        with mock.patch.object(write._Writer, "write_company", flaky):
            result = apply_plan(conn, self.plan, now=NOW)
        run = conn.execute("SELECT * FROM job_run WHERE run_id = ?", (result.run_id,)).fetchone()
        self.assertEqual(run["status"], "partial")
        self.assertIn("disk on fire", run["error"])
        self.assertIn(calls[1], run["error"])
        first = result.ids[f"company:{calls[0]}"]
        self.assertIsNotNone(conn.execute("SELECT 1 FROM company WHERE company_id = ?", (first,)).fetchone())
        self.assertNotIn(f"company:{calls[1]}", result.ids)
        # a rerun finishes the job and writes the first company's rows no second time
        before = conn.execute("SELECT count(*) FROM import_record").fetchone()[0]
        again = apply_plan(conn, self.plan, now=NOW)
        self.assertEqual(conn.execute("SELECT status FROM job_run WHERE run_id = ?", (again.run_id,)).fetchone()[0],
                         "ok")
        total = conn.execute("SELECT count(*) FROM import_record").fetchone()[0]
        self.assertEqual(total, self.one("SELECT count(*) FROM import_record")[0])
        self.assertGreater(total, before)
        conn.close()

    def test_done_set_companies_are_closed_interview(self):
        for key in self.plan.done_set:
            row = self.one("SELECT status, close_reason FROM company WHERE company_id = ?", self.company(key))
            self.assertEqual(tuple(row), ("closed", "interview"))
        self.assertEqual(self.one("SELECT status FROM company WHERE company_id = ?",
                                  self.company("lumenfreight"))[0], "working")
        self.assertEqual(self.one("SELECT status FROM company WHERE company_id = ?",
                                  self.company("saltmarshbio"))[0], "candidate")

    def test_near_name_item_has_weak_confidence_and_basis_import_near_name(self):
        line = SourceRecord("contacted_txt", "line:70", {
            "line": "2026-10-12 - Tidewater - Jun Valdez", "kind": "touch", "date": "2026-10-12",
            "company": "Tidewater", "rest": "Jun Valdez", "people": [{"name": "Jun Valdez", "note": None}]})
        plan = build_plan(self.records + [line], overrides(), now=NOW, send_cfg=CFG)
        self.assertEqual(plan.near_names, (("tidewater", "tidewaterai"),))
        conn = fresh_store()
        result = apply_plan(conn, plan, now=NOW)
        items = conn.execute("SELECT * FROM review_item WHERE kind = 'merge_candidate'").fetchall()
        self.assertEqual(len(items), 1)
        self.assertIn("import_near_name", items[0]["reason"])
        self.assertIn("weak", items[0]["reason"])
        self.assertEqual({items[0]["company_id"], int(items[0]["evidence_key"])},
                         {result.ids["company:tidewater"], result.ids["company:tidewaterai"]})
        conn.close()

    def test_issues_are_raised_through_raise_item_with_actor_import(self):
        items = self.rows("SELECT * FROM review_item WHERE kind = 'flagged'")
        self.assertTrue(items)
        for item in items:
            self.assertEqual(item["raised_by"], "import")
            self.assertTrue(item["evidence_key"].startswith("import:"), item["evidence_key"])
            event = self.one("SELECT actor FROM event WHERE entity = 'review_item' AND entity_id = ? "
                             "AND kind = 'raised'", item["review_id"])
            self.assertEqual(event[0], "import")
        placed = [i for i in self.plan.issues if i.company_key and i.code != "near_name"]
        self.assertEqual(len(items), len(placed))
        conn = fresh_store()
        apply_plan(conn, self.plan, now=NOW)
        count = conn.execute("SELECT count(*) FROM review_item").fetchone()[0]
        apply_plan(conn, self.plan, now=NOW)
        self.assertEqual(conn.execute("SELECT count(*) FROM review_item").fetchone()[0], count)
        conn.close()

    def test_ruled_out_company_stays_visible_with_its_note(self):
        salt = self.company("saltmarshbio")
        self.assertNotEqual(self.one("SELECT status FROM company WHERE company_id = ?", salt)[0], "removed")
        notes = self.rows("SELECT * FROM event WHERE company_id = ? AND kind = 'note'", salt)
        self.assertEqual(len(notes), 1)
        self.assertEqual(json.loads(notes[0]["detail"]),
                         {"text": "Eleven certified LCAs but no new-employment approvals in three years.",
                          "record": "data_js:ruled_out:1"})
        self.assertEqual(notes[0]["actor"], "import")
        items = self.rows("SELECT * FROM review_item WHERE company_id = ? AND state = 'open' AND kind = 'flagged'",
                          salt)
        self.assertEqual([i["evidence_key"] for i in items], ["import:ruled_out:1:saltmarshbio"])

    def test_sources_are_unchanged_after_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = fixture_manifest(Path(tmp))
            before = {s: source_digest(p) for s, p in manifest.paths.items()}
            plan = build_plan(read_all(manifest), overrides(), now=NOW, send_cfg=CFG)
            conn = fresh_store()
            apply_plan(conn, plan, now=NOW)
            conn.close()
            self.assertEqual({s: source_digest(p) for s, p in manifest.paths.items()}, before)

    # -- reruns on a later backup (review fixes) --

    def rerun(self, *changes):
        """A store with the base plan applied, then a plan from the records with `changes` applied on top."""
        records = self.records
        for record_id, change in changes:
            out = []
            for r in records:
                if rid(r) == record_id:
                    r = dataclasses.replace(r, raw=change(dict(r.raw)))
                out.append(r)
            records = out
        conn = fresh_store()
        self.addCleanup(conn.close)
        first = apply_plan(conn, self.plan, now=NOW)
        second = apply_plan(conn, build_plan(records, overrides(), now=NOW, send_cfg=CFG), now=NOW)
        return conn, first, second

    def test_a_draft_sent_between_backups_gets_its_first_send_and_never_stays_sendable(self):
        def sent(raw):
            return {**raw, "status": "sent", "sentAt": "2026-10-21T09:00:00-04:00", "messageId": "gm-c07",
                    "threadId": "th-c07", "rfcMessageId": "<c07.fixture@example.com>",
                    "history": raw["history"] + [{"at": "2026-10-21T09:00:00-04:00", "what": "sent"}]}
        conn, first, second = self.rerun(("contacts_json:contact:c07", sent))
        self.assertEqual(second.status, "ok", second.error)
        gus = first.ids["person:fathomsystems/gus.albrecht@example.com"]
        touch = conn.execute("SELECT * FROM touch WHERE person_id = ?", (gus,)).fetchone()
        self.assertEqual((touch["first_sent_at"], touch["first_rfc_id"], touch["gmail_thread_id"]),
                         ("2026-10-21T13:00:00Z", "<c07.fixture@example.com>", "th-c07"))
        states = [r[0] for r in conn.execute("SELECT state FROM message WHERE touch_id = ? AND seq = 0",
                                             (touch["touch_id"],))]
        self.assertEqual(len(states), 1)
        self.assertNotIn(states[0], ("planned", "drafted", "approved", "failed"))
        self.assertEqual(conn.execute("SELECT count(*) FROM outreach_touch WHERE email_touch_id = ?",
                                      (touch["touch_id"],)).fetchone()[0], 1)
        items = conn.execute("SELECT * FROM review_item WHERE evidence_key LIKE 'import:sent_after_import:%'"
                             ).fetchall()
        self.assertEqual(len(items), 1)

    def test_a_follow_up_sent_between_backups_never_stays_sendable(self):
        def followed_up(raw):
            return {**raw, "status": "followup_sent", "followupSentAt": "2026-10-20T09:30:00-04:00"}
        conn, first, _ = self.rerun(("contacts_json:contact:c05", followed_up))
        emil = first.ids["person:cobaltharbor/emil.strand@example.com"]
        rows = conn.execute("SELECT m.seq, m.state FROM message m JOIN touch t USING(touch_id) WHERE t.person_id = ? "
                            "ORDER BY m.seq", (emil,)).fetchall()
        self.assertEqual(rows[0]["state"], "sent")
        self.assertNotIn(rows[1]["state"], ("planned", "drafted", "approved", "failed"))
        self.assertEqual(conn.execute("SELECT count(*) FROM review_item WHERE evidence_key LIKE "
                                      "'import:sent_after_import:%'").fetchone()[0], 1)

    def test_a_second_no_go_reason_never_blocks_the_run(self):
        orbital = self.plan.companies["orbitalfoods"]
        two = dataclasses.replace(orbital, no_go=orbital.no_go + (("agency", "also an agency"),))
        companies = {**self.plan.companies, "orbitalfoods": two}
        ada_key = "kestrelrobotics/ada.fenwick@example.com"
        ada = self.plan.people[ada_key]
        said_no = dataclasses.replace(ada, no_go=(("said_no", "devvault_notes:file:a.md"),))
        stop = dataclasses.replace(ada, no_go=(("stop_reply", "devvault_notes:file:b.md"),))
        first = dataclasses.replace(self.plan, companies=companies, people={**self.plan.people, ada_key: said_no})
        second = dataclasses.replace(self.plan, companies=companies, people={**self.plan.people, ada_key: stop})
        conn = fresh_store()
        self.addCleanup(conn.close)
        for plan in (first, second, second):
            result = apply_plan(conn, plan, now=NOW)
            self.assertEqual(result.status, "ok", result.error)
        active = conn.execute("SELECT company_id, person_id, reason FROM no_go WHERE lifted_at IS NULL").fetchall()
        self.assertEqual(sorted((r["reason"]) for r in active), ["defence_contractor", "said_no"])

    def test_a_lifted_import_no_go_is_never_added_back(self):
        conn = fresh_store()
        self.addCleanup(conn.close)
        ids = apply_plan(conn, self.plan, now=NOW).ids
        conn.execute("UPDATE no_go SET lifted_at = ?, lift_reason = 'not cleared' WHERE company_id = ?",
                     (NOW, ids["company:orbitalfoods"]))
        self.assertEqual(apply_plan(conn, self.plan, now=NOW).status, "ok")
        self.assertEqual(conn.execute("SELECT count(*) FROM no_go WHERE lifted_at IS NULL").fetchone()[0], 0)

    def test_a_person_whose_key_changes_between_backups_is_one_person(self):
        line = SourceRecord("contacted_txt", "line:50", {
            "line": "2026-10-13 - Harbor Lane - Wren Ashby", "kind": "touch", "date": "2026-10-13",
            "company": "Harbor Lane", "rest": "Wren Ashby", "people": [{"name": "Wren Ashby", "note": None}]})
        note = SourceRecord("devvault_notes", "file:reached-out/harbor-lane.md", {
            "path": "reached-out/harbor-lane.md", "folder": "reached-out", "frontmatter": {
                "company": "Harbor Lane", "people": [{"name": "Wren Ashby", "email": "wren.ashby@example.com",
                                                      "linkedin": "https://www.linkedin.com/in/wren-ashby",
                                                      "sent": "2026-10-13"}]}})
        first = build_plan(self.records + [line], overrides(), now=NOW, send_cfg=CFG)
        second = build_plan(self.records + [line, note], overrides(), now=NOW, send_cfg=CFG)
        self.assertIn("harborlane/wren ashby", first.people)
        self.assertIn("harborlane/wren.ashby@example.com", second.people)
        conn = fresh_store()
        self.addCleanup(conn.close)
        apply_plan(conn, first, now=NOW)
        result = apply_plan(conn, second, now=NOW)
        self.assertEqual(result.status, "ok", result.error)
        rows = conn.execute("SELECT * FROM person WHERE name = 'Wren Ashby'").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["email"], rows[0]["linkedin_url"]),
                         ("wren.ashby@example.com", "https://www.linkedin.com/in/wren-ashby"))
        self.assertEqual(conn.execute("SELECT count(*) FROM touch WHERE person_id = ?",
                                      (rows[0]["person_id"],)).fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT count(*) FROM outreach_touch WHERE person_id = ?",
                                      (rows[0]["person_id"],)).fetchone()[0], 1)
        self.assertEqual(apply_plan(conn, second, now=NOW).written, {"job_run": 1})

    def test_namesakes_the_planner_kept_apart_stay_apart_in_one_run(self):
        lines = [SourceRecord("contacted_txt", f"line:{50 + n}", {
            "line": "2026-10-13 - Harbor Lane - Sam Lee", "kind": "touch", "date": "2026-10-13",
            "company": "Harbor Lane", "rest": "Sam Lee", "people": [{"name": "Sam Lee", "note": None}]})
            for n in range(1)]
        note = SourceRecord("devvault_notes", "file:reached-out/harbor-lane.md", {
            "path": "reached-out/harbor-lane.md", "folder": "reached-out", "frontmatter": {
                "company": "Harbor Lane", "people": [
                    {"name": "Sam Lee", "email": "sam@harbor.example", "sent": "2026-10-14"},
                    {"name": "Sam Lee", "email": "sam.lee@harbor.example", "sent": "2026-10-15"}]}})
        plan = build_plan(self.records + lines + [note], overrides(), now=NOW, send_cfg=CFG)
        sams = [k for k in plan.people if k.startswith("harborlane/")]
        self.assertEqual(len(sams), 3)  # two addresses and the ambiguous name-only mention
        conn = fresh_store()
        self.addCleanup(conn.close)
        apply_plan(conn, plan, now=NOW)
        self.assertEqual(conn.execute("SELECT count(*) FROM person WHERE name = 'Sam Lee'").fetchone()[0], 3)

    def test_a_rerun_keeps_a_status_he_changed(self):
        conn = fresh_store()
        self.addCleanup(conn.close)
        ids = apply_plan(conn, self.plan, now=NOW).ids
        lumen, kestrel = ids["company:lumenfreight"], ids["company:kestrelrobotics"]
        conn.execute("UPDATE company SET status = 'backlog' WHERE company_id = ?", (lumen,))
        conn.execute("UPDATE company SET status = 'working', close_reason = NULL WHERE company_id = ?", (kestrel,))
        self.assertEqual(apply_plan(conn, self.plan, now=NOW).status, "ok")
        status = dict(conn.execute("SELECT company_id, status FROM company").fetchall())
        self.assertEqual((status[lumen], status[kestrel]), ("backlog", "working"))

    def test_a_board_another_company_claims_is_left_alone(self):
        from outreach.importer.model import BoardIn
        from outreach.sources.boards import BoardRef, add_board
        from outreach.store import add_alias
        conn = fresh_store()
        self.addCleanup(conn.close)
        other = conn.execute("INSERT INTO company(name, status, status_at, created_at) "
                             "VALUES ('Other Co', 'candidate', ?, ?)", (NOW, NOW)).lastrowid
        add_alias(conn, other, "ashby:zzz", "ats")  # a claim with no board row
        add_board(conn, other, BoardRef("ashby", "yyy"), via="hand", matched_on="hand", found_by="hand")
        kestrel = self.plan.companies["kestrelrobotics"]
        extra = (("ashby", "zzz"), ("ashby", "yyy"))
        plan = dataclasses.replace(
            self.plan, companies={**self.plan.companies,
                                  "kestrelrobotics": dataclasses.replace(kestrel, boards=kestrel.boards + extra)},
            boards={**self.plan.boards, **{ref: BoardIn(ref[0], ref[1], "kestrelrobotics", False, ("r:1",))
                                           for ref in extra}})
        result = apply_plan(conn, plan, now=NOW)
        self.assertEqual(result.status, "ok", result.error)
        self.assertNotIn("board:ashby:zzz", result.ids)
        self.assertNotIn("board:ashby:yyy", result.ids)
        row = conn.execute("SELECT company_id, active FROM board WHERE ats = 'ashby' AND token = 'yyy'").fetchone()
        self.assertEqual(tuple(row), (other, 1))  # never deactivated by the import
        self.assertIsNone(conn.execute("SELECT 1 FROM board WHERE token = 'zzz'").fetchone())

    def test_a_rerun_after_a_partial_run_rebuilds_the_rules(self):
        conn = fresh_store()
        self.addCleanup(conn.close)
        # every company and posting is written, then the last step fails, so no rebuild ran
        with mock.patch.object(write._Writer, "near_names", side_effect=RuntimeError("stopped")):
            self.assertEqual(apply_plan(conn, self.plan, now=NOW).status, "partial")
        rebuilds = conn.execute("SELECT count(*) FROM job_run WHERE job = 'rebuild'").fetchone()[0]
        self.assertEqual(apply_plan(conn, self.plan, now=NOW).status, "ok")
        self.assertEqual(conn.execute("SELECT count(*) FROM job_run WHERE job = 'rebuild'").fetchone()[0],
                         rebuilds + 1)
        undecided = conn.execute("SELECT count(*) FROM posting p WHERE NOT EXISTS "
                                 "(SELECT 1 FROM decision d WHERE d.posting_id = p.posting_id)").fetchone()[0]
        self.assertEqual(undecided, 0)

    # -- more of what the sender and the store rely on --

    def test_every_name_is_an_alias_of_its_company(self):
        tide = self.company("tidewaterai")
        aliases = {r["alias"] for r in self.rows("SELECT alias FROM company_alias WHERE company_id = ?", tide)}
        self.assertTrue(self.plan.companies["tidewaterai"].names <= aliases | {"Tidewater AI"})

    def test_people_and_their_channels(self):
        ned = self.one("SELECT * FROM person WHERE person_id = ?", self.person("marlowdynamics/ned pruitt"))
        self.assertEqual((ned["channel"], ned["email"], ned["added_by"]), ("email", None, "import"))
        placeholder = [p for p in self.plan.people.values() if p.placeholder][0]
        row = self.one("SELECT * FROM person WHERE person_id = ?", self.person(placeholder.key))
        self.assertEqual(row["name"], "Unnamed other (imported)")
        self.assertIn(placeholder.records[0], row["notes"])

    def test_imported_bodies_and_closed_postings(self):
        posting = self.one("SELECT * FROM posting WHERE source_key = 'greenhouse:kestrel:1001'")
        body = self.one("SELECT * FROM posting_body WHERE posting_id = ?", posting["posting_id"])
        self.assertIsNone(body["fetched_at"])
        self.assertEqual(len(body["body_sha256"]), 64)
        board = self.one("SELECT * FROM board WHERE ats = 'greenhouse' AND token = 'kestrel'")
        self.assertEqual((board["found_by"], board["company_id"]), ("import", self.company("kestrelrobotics")))
        event = self.one("SELECT actor FROM event WHERE entity = 'board' AND entity_id = ?", board["board_id"])
        self.assertEqual(event[0], "import")

    def test_the_import_never_writes_the_sender_row_or_send_days(self):
        self.assertEqual(self.one("SELECT count(*) FROM send_day")[0], 0)
        row = self.one("SELECT * FROM sender")
        self.assertTrue(row is None or row["history_id"] == "")


if __name__ == "__main__":
    unittest.main()
