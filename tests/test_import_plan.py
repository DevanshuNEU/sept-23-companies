"""The plan builder (#17 Task 10): one plan from every fixture source, and an outcome for every record."""
import dataclasses
import tempfile
import unittest
from pathlib import Path

from outreach.importer.manifest import Overrides
from outreach.importer.model import EmailIn
from outreach.importer.plan import ImportPlan, build_plan, read_all
from outreach.importer.records import SOURCES, SourceError, SourceRecord
from outreach.importer.report import check_report, count_report
from outreach.importer.vocab import noon_ny
from outreach.sending.config import load_config
from tests.importtest import fixture_manifest

NOW = "2026-10-20T12:00:00Z"
CFG = load_config()


def overrides(**kw) -> Overrides:
    base = dict(same_company={}, not_same_company=frozenset(), person_name={}, done_add=frozenset(),
                done_remove=frozenset(), touch_for={})
    base.update(kw)
    return Overrides(**base)


def rid(record: SourceRecord) -> str:
    return f"{record.source}:{record.source_key}"


class PlanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.records = read_all(fixture_manifest(Path(cls._tmp.name)))
        cls.base = build_plan(cls.records, overrides(), now=NOW, send_cfg=CFG)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    # -- helpers --

    def plan(self, records=None, *, now=NOW, ov=None) -> ImportPlan:
        return build_plan(self.records if records is None else records, ov or overrides(), now=now, send_cfg=CFG)

    def changed(self, record_id: str, change) -> list[SourceRecord]:
        """The fixture records with one record's raw replaced by `change(raw copy)`."""
        out = []
        for r in self.records:
            if rid(r) == record_id:
                raw = change(dict(r.raw))
                r = dataclasses.replace(r, raw=raw)
            out.append(r)
        return out

    def outcome(self, plan, record_id):
        return plan.records[record_id][1]

    def issues(self, plan, code, company=None):
        return [i for i in plan.issues if i.code == code and (company is None or i.company_key == company)]

    def touches_of(self, plan, person_key):
        return [t for t in plan.touches.values() if t.person_key == person_key]

    def contact_touch(self, plan, contact_id):
        found = [t for t in plan.touches.values() if t.email and t.email.contact_id == contact_id]
        self.assertEqual(len(found), 1, contact_id)
        return found[0]

    # -- read_all --

    def test_read_all_reads_every_source(self):
        self.assertEqual({r.source for r in self.records}, set(SOURCES))

    def test_read_all_stops_on_a_missing_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = fixture_manifest(Path(tmp))
            manifest.paths["contacted_txt"].unlink()
            with self.assertRaises(SourceError) as cm:
                read_all(manifest)
        self.assertIn("contacted_txt", str(cm.exception))

    # -- the tests the brief names --

    def test_every_record_gets_exactly_one_outcome(self):
        plan = self.base
        self.assertEqual(len(plan.records), len(self.records))
        self.assertEqual(set(plan.records), {rid(r) for r in self.records})
        for record_id, (record, outcome) in plan.records.items():
            self.assertEqual(rid(record), record_id)
            self.assertIn(outcome.outcome, ("imported", "merged", "skipped", "held"))
        self.assertEqual(plan.held(), ())  # the base fixture set holds nothing

    def test_visa_denied_is_hand_evidence_and_an_item_not_a_no_go(self):
        lumen = self.base.companies["lumenfreight"]
        self.assertEqual([(d.fact, d.value, d.source_kind, d.quote, d.source_ref) for d in lumen.hand],
                         [("sponsorship", "no", "hand", "No sponsorship", "import:data_js:company:lumen")])
        self.assertEqual(lumen.no_go, ())
        items = self.issues(self.base, "old_no_sponsorship", "lumenfreight")
        self.assertEqual(len(items), 1)
        self.assertFalse(items[0].holds)

    def test_cleared_and_agency_become_no_go_and_over_500_becomes_size_evidence(self):
        self.assertEqual(self.base.companies["orbitalfoods"].no_go,
                         (("defence_contractor", "Cleared defence contractor, per the old dashboard"),))
        quarry = self.base.companies["quarrylabs"]
        self.assertEqual(quarry.no_go, ())
        sizes = [d for d in quarry.evidence if d.fact == "size" and d.num_low == 501]
        self.assertEqual(len(sizes), 1)
        self.assertEqual((sizes[0].confidence, sizes[0].source_kind), ("estimate", "old_dashboard"))
        agency = self.plan(self.changed("data_js:company:tidewater", lambda r: {**r, "sizeClass": "agency",
                                                                                "sizeWhy": "A staffing agency"}))
        self.assertEqual(agency.companies["tidewaterai"].no_go, (("agency", "A staffing agency"),))

    def test_ruled_out_is_a_note_and_an_item_not_a_removal(self):
        salt = self.base.companies["saltmarshbio"]
        why = "Eleven certified LCAs but no new-employment approvals in three years."
        self.assertEqual(salt.notes, (("data_js:ruled_out:1", why),))
        self.assertIsNone(salt.close)
        self.assertFalse(hasattr(salt, "removed"))
        items = self.issues(self.base, "ruled_out", "saltmarshbio")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].key, "import:ruled_out:1:saltmarshbio")
        self.assertEqual(items[0].detail, "The old dashboard ruled this out: Eleven certified LCAs but no new-employment "
                                          "approvals in three years. Remove it if that still holds.")
        self.assertEqual(self.outcome(self.base, "data_js:ruled_out:1").outcome, "imported")

    def test_ruled_out_slash_names_split(self):
        record = "data_js:ruled_out:2"
        why = "Both are ruled out by one note in the old list."
        ferrous, tide = self.base.companies["ferrousgrid"], self.base.companies["tidewaterai"]
        self.assertEqual(ferrous.name, "Ferrous Grid")
        self.assertIn((record, why), ferrous.notes)
        self.assertIn((record, why), tide.notes)
        self.assertIn("data_js:company:tidewater", tide.records)  # linked to its COMPANIES row
        self.assertIn(record, tide.records)
        self.assertIn(record, ferrous.records)
        self.assertEqual(sorted(i.key for i in self.base.issues if i.code == "ruled_out" and i.record == record),
                         ["import:ruled_out:2:ferrousgrid", "import:ruled_out:2:tidewaterai"])
        out = self.outcome(self.base, record)
        self.assertEqual((out.outcome, out.entity, out.entity_key), ("imported", "company", "ferrousgrid"))

    def test_untouched_tracker_entry_is_an_explained_skip(self):
        tide = self.outcome(self.base, "local_storage:company:tidewater")
        self.assertEqual((tide.outcome, tide.reason), ("skipped", "untouched_default"))
        orbital = self.outcome(self.base, "local_storage:company:orbital")
        self.assertEqual((orbital.outcome, orbital.reason), ("skipped", "old_queue_skip"))
        quarry = self.outcome(self.base, "local_storage:company:quarry")  # a note is not a default entry
        self.assertEqual(quarry.entity, "company")
        self.assertIn(("local_storage:company:quarry", "Size looks too big."), self.base.companies["quarrylabs"].notes)

    def test_unclear_send_is_held_and_blocks_the_run(self):
        for status in ("sending", "send_unknown"):
            plan = self.plan(self.changed("contacts_json:contact:c05", lambda r: {**r, "status": status}))
            out = self.outcome(plan, "contacts_json:contact:c05")
            self.assertEqual((out.outcome, out.reason), ("held", "unclear_send"))
            items = [i for i in self.issues(plan, "unclear_send") if i.record == "contacts_json:contact:c05"]
            self.assertEqual(len(items), 1)
            self.assertTrue(items[0].holds)
            self.assertIn("contacts_json:contact:c05", plan.held())

    def test_sent_log_line_without_contact_is_held(self):
        extra = SourceRecord("send_log", "line:99", {
            "line": "[2026-10-12 09:00] sent to Quinn Rowe <quinn.rowe@example.com> at Nowhere Labs (1/10 today)",
            "at": "2026-10-12 09:00", "kind": "sent", "name": "Quinn Rowe", "email": "quinn.rowe@example.com",
            "company": "Nowhere Labs", "follow_up": False})
        plan = self.plan(self.records + [extra])
        out = self.outcome(plan, "send_log:line:99")
        self.assertEqual((out.outcome, out.reason), ("held", "sent_not_in_contacts"))
        self.assertTrue(all(i.holds for i in self.issues(plan, "sent_not_in_contacts")))
        self.assertEqual(plan.held(), ("send_log:line:99",))
        # a sent line that matches its contact merges into that touch
        ada = self.outcome(self.base, "send_log:line:2")
        self.assertEqual((ada.outcome, ada.reason, ada.entity), ("merged", "same_touch", "outreach_touch"))

    def test_approved_contact_becomes_a_draft_not_an_approval(self):
        gus = self.contact_touch(self.base, "c07").email
        self.assertIsNone(gus.sent0_at)
        self.assertEqual(gus.body0, "Hi Gus, this is a made-up fixture message about Fathom Systems.")
        self.assertEqual(gus.touch_state, "open")
        self.assertTrue(gus.old_approval)  # an event notes it; the message is a draft
        names = {f.name for f in dataclasses.fields(EmailIn)}
        self.assertFalse({"approved_at", "approved", "state0", "state1"} & names)

    def test_sent_contact_gets_one_planned_follow_up_seven_business_days_later(self):
        def friday(raw):
            return {**raw, "sentAt": "2026-10-16T09:14:00-04:00",
                    "history": [{"at": "2026-10-16T09:14:00-04:00", "what": "sent"}]}
        plan = self.plan(self.changed("contacts_json:contact:c02", friday), now="2026-10-20T12:00:00Z")
        bram = self.contact_touch(plan, "c02").email
        self.assertEqual(bram.due1_on, "2026-10-27")
        self.assertIsNone(bram.body1)
        self.assertIsNone(self.contact_touch(plan, "c01").email.due1_on)  # replied: no follow-up
        self.assertEqual(plan.followups_not_planned, 0)

    def test_follow_up_due_before_the_import_is_not_planned_and_counted(self):
        def friday(raw):
            return {**raw, "sentAt": "2026-10-16T09:14:00-04:00"}
        plan = self.plan(self.changed("contacts_json:contact:c02", friday), now="2026-11-01T12:00:00Z")
        self.assertIsNone(self.contact_touch(plan, "c02").email.due1_on)
        self.assertEqual(plan.followups_not_planned, 1)

    def test_followup_ready_draft_gets_its_due_date(self):
        emil = self.contact_touch(self.base, "c05").email
        self.assertEqual(emil.body1, "Following up, a fixture note for Emil Strand.")
        self.assertEqual(emil.due1_on, "2026-10-20")  # sent Friday 2026-10-09, plus 7 business days

    def test_historic_email_touch_is_closed_and_has_no_message(self):
        [ned] = [t for t in self.base.touches.values() if t.person_key == "marlowdynamics/ned pruitt"]
        self.assertEqual(ned.channel, "email")
        self.assertEqual(ned.sent_at, noon_ny("2026-10-07"))
        email = ned.email
        self.assertIsNone(email.contact_id)
        self.assertEqual((email.subject0, email.body0, email.subject1, email.body1), (None, None, None, None))
        self.assertEqual((email.touch_state, email.close_reason), ("closed", "no_reply"))
        self.assertIsNone(email.due1_on)

    def test_free_text_reply_is_unsorted_with_the_text_in_an_item(self):
        extra = SourceRecord("devvault_notes", "file:reached-out/harbor-lane.md", {
            "path": "reached-out/harbor-lane.md", "folder": "reached-out", "frontmatter": {
                "company": "Harbor Lane", "people": [{"name": "Wren Ashby", "role": "engineer",
                                                      "email": "wren.ashby@example.com", "sent": "2026-10-13",
                                                      "reply": "said she would pass it on"}]}})
        plan = self.plan(self.records + [extra])
        [touch] = self.touches_of(plan, "harborlane/wren.ashby@example.com")
        [reply] = [o for o in touch.outcomes if o.kind == "replied"]
        self.assertEqual((reply.reply_kind, reply.estimated), ("unsorted", True))
        self.assertEqual(touch.email.touch_state, "replied")
        [item] = self.issues(plan, "reply_text", "harborlane")
        self.assertIn("said she would pass it on", item.detail)

    def test_reply_none_in_a_company_file_is_no_reply(self):
        cleo = self.contact_touch(self.base, "c03")
        self.assertFalse([o for o in cleo.outcomes if o.kind == "replied"])
        self.assertFalse([i for i in self.issues(self.base, "reply_text") if "marlow" in i.record])

    def test_radar_frozen_board_closes_its_postings_and_keeps_the_application(self):
        self.assertTrue(self.base.boards[("greenhouse", "kestrel")].active)
        self.assertFalse(self.base.postings["greenhouse:kestrel:1001"].closed)
        plan = self.plan(self.changed("radar_db:board:1", lambda r: {**r, "ats": "apple"}))
        self.assertFalse(plan.boards[("apple", "kestrel")].active)
        for native in ("1001", "1002"):
            self.assertTrue(plan.postings[f"apple:kestrel:{native}"].closed)
        apps = [a for a in plan.applications.values() if a.posting_key == "apple:kestrel:1001"]
        self.assertEqual(len(apps), 1)
        self.assertEqual(apps[0].applied_on, "2026-10-19")

    def test_devboard_and_radar_application_on_one_posting_is_one_application(self):
        apps = [a for a in self.base.applications.values() if a.posting_key == "greenhouse:kestrel:1001"]
        self.assertEqual(len(apps), 1)
        self.assertIn("radar_db:app_event:2", apps[0].records)
        self.assertIn("devboard:job:greenhouse:kestrel:1001", apps[0].records)
        out = self.outcome(self.base, "devboard:job:greenhouse:kestrel:1001")
        self.assertEqual((out.outcome, out.reason, out.entity), ("merged", "same_application", "application"))
        self.assertEqual(self.outcome(self.base, "radar_db:app_event:1").reason, "opened_only")
        self.assertEqual(self.outcome(self.base, "radar_db:app_event:3").reason, "triage_ignore")

    def test_two_email_records_for_one_person_are_one_touch(self):
        line = SourceRecord("contacted_txt", "line:50", {
            "line": "2026-10-20 - Harbor Lane - Wren Ashby", "kind": "touch", "date": "2026-10-20",
            "company": "Harbor Lane", "rest": "Wren Ashby", "people": [{"name": "Wren Ashby", "note": None}]})
        note = SourceRecord("devvault_notes", "file:reached-out/harbor-lane.md", {
            "path": "reached-out/harbor-lane.md", "folder": "reached-out", "frontmatter": {
                "company": "Harbor Lane", "people": [{"name": "Wren Ashby", "email": "wren.ashby@example.com",
                                                      "sent": "2026-10-22"}]}})
        plan = self.plan(self.records + [line, note], now="2026-10-24T12:00:00Z")
        touches = self.touches_of(plan, "harborlane/wren.ashby@example.com")
        self.assertEqual(len(touches), 1)
        self.assertEqual(touches[0].sent_at, noon_ny("2026-10-20"))
        self.assertEqual(touches[0].followups_sent, 1)
        self.assertEqual(self.outcome(plan, "contacted_txt:line:50").outcome, "imported")
        self.assertEqual(self.outcome(plan, "devvault_notes:file:reached-out/harbor-lane.md").reason, "same_touch")

    def test_done_set_is_interviews_plus_overrides(self):
        self.assertIn("kestrelrobotics", self.base.done_set)
        self.assertEqual(self.base.companies["kestrelrobotics"].close, ("interview", "Done: interviewed (imported)"))
        plan = self.plan(ov=overrides(done_remove=frozenset({"kestrelrobotics"}), done_add=frozenset({"quarrylabs"})))
        self.assertNotIn("kestrelrobotics", plan.done_set)
        self.assertIsNone(plan.companies["kestrelrobotics"].close)
        self.assertIn("quarrylabs", plan.done_set)
        self.assertEqual(plan.companies["quarrylabs"].close, ("interview", "Done: interviewed (imported)"))

    def test_build_plan_is_deterministic(self):
        self.assertEqual(self.plan(), self.plan())

    # -- placement and people --

    def test_interview_goes_on_the_touch_that_got_the_reply_with_the_session_date(self):
        ada = self.contact_touch(self.base, "c01")
        [interview] = [o for o in ada.outcomes if o.kind == "interview"]
        self.assertEqual((interview.at, interview.estimated), (noon_ny("2026-10-12"), False))
        out = self.outcome(self.base, "interview_prep:folder:kestrel-robotics")
        self.assertEqual((out.outcome, out.entity, out.entity_key), ("imported", "outreach_touch", ada.key))
        self.assertEqual(self.outcome(self.base, "interview_prep:folder:general").reason, "not_a_company")

    def test_one_touch_per_person_keeps_the_contacts_touch(self):
        ada = self.touches_of(self.base, "kestrelrobotics/ada.fenwick@example.com")
        self.assertEqual(len(ada), 1)
        self.assertEqual(ada[0].email.contact_id, "c01")
        self.assertEqual(ada[0].records[0], "contacts_json:contact:c01")
        for merged in ("contacted_txt:line:5", "local_storage:company:kestrel",
                       "devvault_notes:file:reached-out/kestrel-robotics.md"):
            self.assertEqual(self.outcome(self.base, merged).reason, "same_touch", merged)

    def test_email_touch_fields_for_the_sender(self):
        cleo = self.contact_touch(self.base, "c03").email
        self.assertEqual((cleo.rfc0, cleo.thread_id, cleo.gmail_id0), ("<c03.fixture@example.com>", "th-c03",
                                                                       "gm-c03"))
        self.assertEqual(cleo.sent0_at, "2026-10-07T13:14:00Z")
        self.assertEqual(cleo.sent1_at, "2026-10-14T13:30:00Z")
        self.assertIsNone(cleo.rfc1)  # the old mailer never kept a follow-up's Message-ID
        dara = self.contact_touch(self.base, "c04").email
        self.assertEqual(dara.touch_state, "bounced")
        self.assertIsNotNone(dara.bounced_at)

    def test_unparsed_contacted_line_naming_a_known_company_is_a_note(self):
        out = self.outcome(self.base, "contacted_txt:line:10")
        self.assertEqual((out.outcome, out.entity, out.entity_key), ("merged", "company", "fathomsystems"))
        self.assertIn(("contacted_txt:line:10", "Found Fathom Systems on a list and meant to log it later"),
                      self.base.companies["fathomsystems"].notes)

    def test_unparsed_line_with_no_company_is_held_and_skip_record_settles_it(self):
        line = SourceRecord("contacted_txt", "line:60", {"line": "called someone about something",
                                                         "kind": "unparsed"})
        plan = self.plan(self.records + [line])
        self.assertEqual(plan.held(), ("contacted_txt:line:60",))
        self.assertEqual(self.outcome(plan, "contacted_txt:line:60").reason, "unparsed_record")
        settled = self.plan(self.records + [line], ov=overrides(skip_record={"contacted_txt:line:60": "a stray note"}))
        self.assertEqual(settled.held(), ())
        out = self.outcome(settled, "contacted_txt:line:60")
        self.assertEqual((out.outcome, out.reason), ("skipped", "by_override"))

    def unlisted_merge(self):
        """Radar posting 2 says it was merged into posting 999, which radar.db does not list."""
        return self.changed("radar_db:posting:2", lambda r: {**r, "merged_into": 999})

    def test_a_radar_posting_merged_into_an_unlisted_one_is_held(self):
        plan = self.plan(self.unlisted_merge())
        self.assertEqual(plan.held(), ("radar_db:posting:2",))
        self.assertEqual(self.outcome(plan, "radar_db:posting:2").reason, "unparsed_record")
        self.assertEqual(self.issues(plan, "skip_record_unused"), [])

    def test_skip_record_settles_a_hold_raised_late(self):
        # the merged-radar-posting hold is raised after the other holds; it is settleable like they are
        ov = overrides(skip_record={"radar_db:posting:2": "a duplicate of a posting he does not track"})
        plan = self.plan(self.unlisted_merge(), ov=ov)
        self.assertEqual(plan.held(), ())
        out = self.outcome(plan, "radar_db:posting:2")
        self.assertEqual((out.outcome, out.reason), ("skipped", "by_override"))
        self.assertEqual(self.issues(plan, "skip_record_unused"), [])
        [issue] = [i for i in plan.issues if i.record == "radar_db:posting:2"]
        self.assertFalse(issue.holds)
        self.assertIn("Skipped by [skip_record]: a duplicate of a posting he does not track", issue.detail)
        report = count_report(plan, self.unlisted_merge())
        self.assertEqual(check_report(report), [])
        self.assertEqual(report.sources["radar_db"].skipped["by_override"], 1)

    def test_skip_record_for_a_record_that_is_not_held_is_still_reported_unused(self):
        ov = overrides(skip_record={"radar_db:posting:2": "not held here", "radar_db:posting:999": "no such record"})
        plan = self.plan(ov=ov)  # the plain fixture: posting 2 is imported, 999 does not exist
        self.assertEqual({i.record for i in self.issues(plan, "skip_record_unused")},
                         {"radar_db:posting:2", "radar_db:posting:999"})
        self.assertEqual(self.outcome(plan, "radar_db:posting:2").outcome, "imported")

    def test_skip_record_cannot_settle_an_unclear_send(self):
        records = self.changed("contacts_json:contact:c05", lambda r: {**r, "status": "sending"})
        plan = self.plan(records, ov=overrides(skip_record={"contacts_json:contact:c05": "it went out"}))
        self.assertEqual(plan.held(), ("contacts_json:contact:c05",))

    def test_skip_record_cannot_settle_a_sent_contact_with_no_company(self):
        records = self.changed("contacts_json:contact:c02", lambda r: {**r, "company": ""})
        plan = self.plan(records, ov=overrides(skip_record={"contacts_json:contact:c02": "no company"}))
        self.assertIn("contacts_json:contact:c02", plan.held())
        self.assertEqual(self.outcome(plan, "contacts_json:contact:c02").reason, "sent_unplaced")

    def test_a_said_no_reply_stops_the_person(self):
        def no(raw):
            fm = dict(raw["frontmatter"])
            fm["people"] = [{**fm["people"][0], "reply": "polite-no"}] + fm["people"][1:]
            return {**raw, "frontmatter": fm}
        plan = self.plan(self.changed("devvault_notes:file:reached-out/kestrel-robotics.md", no))
        ada = plan.people["kestrelrobotics/ada.fenwick@example.com"]
        self.assertEqual(ada.no_go, (("said_no", "devvault_notes:file:reached-out/kestrel-robotics.md"),))

    def test_every_touch_has_its_person_and_company(self):
        for touch in self.base.touches.values():
            self.assertIn(touch.company_key, self.base.companies)
            self.assertIn(touch.person_key, self.base.people, touch.key)
        for person in self.base.people.values():
            self.assertIn(person.company_key, self.base.companies)


if __name__ == "__main__":
    unittest.main()
