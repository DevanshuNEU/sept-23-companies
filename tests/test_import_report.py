"""The count report and its check (#17 Task 12)."""
import dataclasses
import json
import tempfile
import unittest
from pathlib import Path

from outreach.importer.manifest import Overrides
from outreach.importer.model import IssueIn, RecordOutcome
from outreach.importer.plan import build_plan, read_all
from outreach.importer.records import SOURCES, SourceRecord
from outreach.importer.report import (EXPLAINED_SKIPS, MERGE_REASONS, CountReport, check_report, count_report,
                                      render_report, report_from_json, report_to_json)
from outreach.sending.config import load_config
from tests.importtest import fixture_manifest

NOW = "2026-10-20T12:00:00Z"
CFG = load_config()


def overrides() -> Overrides:
    return Overrides({}, frozenset(), {}, frozenset(), frozenset(), {})


class ReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.records = read_all(fixture_manifest(Path(cls._tmp.name)))
        cls.plan = build_plan(cls.records, overrides(), now=NOW, send_cfg=CFG)
        cls.report = count_report(cls.plan, cls.records)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def with_outcome(self, record_id, outcome):
        records = dict(self.plan.records)
        records[record_id] = (records[record_id][0], outcome)
        return dataclasses.replace(self.plan, records=records)

    def test_counts_add_up_for_every_fixture_source(self):
        self.assertEqual(set(self.report.sources), set(SOURCES))
        for source, c in self.report.sources.items():
            with self.subTest(source=source):
                self.assertGreater(c.read, 0)
                self.assertEqual(c.read, c.imported + sum(c.merged.values()) + sum(c.skipped.values())
                                 + sum(c.held.values()))
        self.assertEqual(check_report(self.report), [])
        self.assertEqual(self.report.sources["send_log"].merged, {"same_touch": 7})

    def test_unknown_skip_reason_fails_the_check(self):
        plan = self.with_outcome("data_js:meta", RecordOutcome("skipped", "felt_like_it", None, None))
        failures = check_report(count_report(plan, self.records))
        self.assertEqual(len(failures), 1)
        self.assertIn("felt_like_it", failures[0])
        plan = self.with_outcome("send_log:line:2", RecordOutcome("merged", "close_enough", "company", "x"))
        self.assertTrue(any("close_enough" in f for f in check_report(count_report(plan, self.records))))

    def test_held_records_fail_the_check(self):
        plan = self.with_outcome("contacts_json:contact:c05", RecordOutcome("held", "unclear_send", None, None))
        report = count_report(plan, self.records)
        self.assertEqual(report.sources["contacts_json"].held, {"unclear_send": 1})
        failures = check_report(report)
        self.assertEqual(len(failures), 1)
        self.assertIn("unclear_send", failures[0])

    def test_empty_required_source_fails_the_check(self):
        records = [r for r in self.records if r.source != "curated_touches"]
        plan = dataclasses.replace(self.plan, records={k: v for k, v in self.plan.records.items()
                                                       if not k.startswith("curated_touches:")})
        failures = check_report(count_report(plan, records))
        self.assertEqual(len(failures), 1)
        self.assertIn("curated_touches", failures[0])

    def empty_contacts(self, **kw):
        """The report of the fixture set with no contacts at all: contacts_json read nothing."""
        records = [r for r in self.records if r.source != "contacts_json"]
        plan = dataclasses.replace(self.plan, records={k: v for k, v in self.plan.records.items()
                                                       if not k.startswith("contacts_json:")})
        return count_report(plan, records, **kw)

    def test_a_declared_empty_source_may_have_zero_records(self):
        undeclared = check_report(self.empty_contacts())
        self.assertEqual(len(undeclared), 1)
        self.assertEqual(undeclared[0], "contacts_json: no records at all; a required source never counts as empty")
        declared = frozenset({"contacts_json"})
        self.assertEqual(check_report(self.empty_contacts(allow_empty=declared), allow_empty=declared), [])
        self.assertEqual(check_report(self.empty_contacts(), allow_empty=declared), [])

    def test_declaring_one_source_does_not_excuse_another(self):
        declared = frozenset({"send_log"})
        failures = check_report(self.empty_contacts(allow_empty=declared), allow_empty=declared)
        self.assertEqual(len(failures), 1)
        self.assertIn("contacts_json: no records at all", failures[0])

    def test_a_declared_source_that_has_records_is_unchanged(self):
        declared = frozenset(SOURCES)
        report = count_report(self.plan, self.records, allow_empty=declared)
        self.assertEqual(check_report(report, allow_empty=declared), [])
        self.assertEqual(report.empty_on_purpose, ())
        self.assertEqual(report, self.report)
        self.assertNotIn("empty on purpose", render_report(report))

    def test_a_source_missing_from_the_report_still_fails_whatever_is_declared(self):
        # no entry at all is a source that was never read, not a source read empty
        report = dataclasses.replace(self.report, sources={s: c for s, c in self.report.sources.items()
                                                           if s != "contacts_json"})
        failures = check_report(report, allow_empty=frozenset(SOURCES))
        self.assertEqual(len(failures), 1)
        self.assertIn("contacts_json: no records at all", failures[0])

    def test_the_count_identity_still_applies_to_a_declared_empty_source(self):
        declared = frozenset({"contacts_json"})
        report = self.empty_contacts(allow_empty=declared)
        counts = report.sources["contacts_json"]
        broken = dataclasses.replace(report, sources={**report.sources, "contacts_json": dataclasses.replace(
            counts, imported=2)})
        failures = check_report(broken, allow_empty=declared)
        self.assertEqual(failures, ["contacts_json: read 0 but accounted for 2"])
        held = dataclasses.replace(report, sources={**report.sources, "contacts_json": dataclasses.replace(
            counts, held={"unclear_send": 1})})
        self.assertTrue(any("accounted for 1" in f for f in check_report(held, allow_empty=declared)))

    def test_render_says_a_declared_source_was_empty_on_purpose(self):
        declared = frozenset({"contacts_json"})
        report = self.empty_contacts(allow_empty=declared)
        self.assertEqual(report.empty_on_purpose, ("contacts_json",))
        text = render_report(report)
        self.assertIn("contacts_json: empty on purpose (his ruling)", text)
        self.assertIn("Unexplained losses: 0", text)
        undeclared = render_report(self.empty_contacts())
        self.assertNotIn("empty on purpose", undeclared)
        self.assertIn("no records at all", undeclared)

    def test_the_empty_ruling_survives_the_stored_report(self):
        report = self.empty_contacts(allow_empty=frozenset({"contacts_json"}))
        stored = json.loads(json.dumps(report_to_json(report)))
        again = report_from_json(stored)
        self.assertEqual(again, report)
        self.assertIn("contacts_json: empty on purpose (his ruling)", render_report(again))
        self.assertEqual(check_report(again), [])  # the stored ruling is what the stored report was checked with
        self.assertEqual(report_from_json({k: v for k, v in stored.items() if k != "empty_on_purpose"})
                         .empty_on_purpose, ())

    def test_radar_tables_list_every_table_with_its_rows(self):
        tables = self.report.radar_tables
        self.assertEqual(tables["board"], (3, "rows"))
        self.assertEqual(tables["posting"], (6, "rows"))
        self.assertEqual(tables["app_event"], (4, "rows"))
        self.assertEqual(tables["poll_run"], (3, "run_history"))
        self.assertEqual(tables["posting_version"], (2, "older_version"))
        self.assertEqual(len(tables), 12)
        radar = self.report.sources["radar_db"]
        self.assertEqual(radar.skipped["run_history"], 3 + 9 + 1)  # poll_run, board_fetch, body_attempt rows

    def test_render_says_unexplained_losses_zero_on_a_clean_plan(self):
        text = render_report(self.report)
        self.assertIn("Unexplained losses: 0", text)
        self.assertIn("Follow-ups not planned: due before the switch: 1", text)
        self.assertIn("kestrelrobotics", text)  # the done set
        failing = render_report(count_report(
            self.with_outcome("contacts_json:contact:c05", RecordOutcome("held", "unclear_send", None, None)),
            self.records))
        self.assertNotIn("Unexplained losses: 0", failing)
        self.assertIn("unclear_send", failing)

    def test_report_round_trips_through_json(self):
        stored = json.loads(json.dumps(report_to_json(self.report)))
        self.assertEqual(report_from_json(stored), self.report)
        self.assertIsInstance(report_from_json(stored), CountReport)

    def test_render_has_no_em_or_en_dash(self):
        text = render_report(self.report)
        self.assertFalse(any(c in text for c in (chr(0x2013), chr(0x2014))))

    def test_issues_with_no_company_are_listed_so_he_sees_them(self):
        lost = IssueIn("done_unknown", None, "overrides:done_add", "[done_add] names 'nowhere', which is no company.",
                       False)
        plan = dataclasses.replace(self.plan, issues=self.plan.issues + (lost,))
        report = count_report(plan, self.records)
        self.assertEqual(report.unplaced_issues, ("done_unknown overrides:done_add: [done_add] names 'nowhere', "
                                                  "which is no company.",))
        self.assertEqual(self.report.unplaced_issues, ())
        text = render_report(report)
        self.assertIn("Issues with no company (no review item, read them here): 1", text)
        self.assertIn("names 'nowhere'", text)

    def test_written_counts_replace_the_plan_counts(self):
        report = count_report(self.plan, self.records, written={"company": 14, "person": 13})
        self.assertEqual(report.would_write, {"company": 14, "person": 13})
        self.assertEqual(self.report.would_write["companies"], len(self.plan.companies))

    def test_the_planner_skips_and_merges_only_with_explained_reasons(self):
        self.assertIn("by_override", EXPLAINED_SKIPS)
        for _, outcome in self.plan.records.values():
            if outcome.outcome == "skipped":
                self.assertIn(outcome.reason, EXPLAINED_SKIPS)
            if outcome.outcome == "merged":
                self.assertIn(outcome.reason, MERGE_REASONS)


if __name__ == "__main__":
    unittest.main()
