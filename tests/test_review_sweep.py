import contextlib
import io
import os
import plistlib
import re
import sqlite3
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from outreach.review.__main__ import main
from outreach.review.config import ReviewConfig
from outreach.review.play import ny_date
from outreach.review.triggers import SweepReport, clear_gone, clear_stale, sweep_needs_you
from outreach.rules import rebuild
from outreach.store import connect
from outreach.store.timefmt import utcnow
from tests.review_seed import seed_email_person, seed_item, seed_plan_row, seed_queued_message, seed_status
from tests.storetest import fresh_store, seed_board, seed_company, seed_posting

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "launchd" / "com.devanshu.outreach-needs-you.plist.template"
NOW = "2026-11-10T15:00:00Z"
TODAY = "2026-11-10"
CFG = ReviewConfig(sponsorship_top_n=150, planned_days=4, stale_days=14)
UNCLEAR = "We may be able to support visa sponsorship for exceptional candidates."


class SweepBase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)

    def company(self, name=None, status=None):
        n = self.conn.execute("SELECT COUNT(*) FROM company").fetchone()[0]
        cid = seed_company(self.conn, name or f"Co{n}")
        if status:
            seed_status(self.conn, cid, status)
        return cid

    def state(self, review_id):
        r = self.conn.execute("SELECT state, resolution, resolution_note FROM review_item WHERE review_id = ?",
                              (review_id,)).fetchone()
        return tuple(r)

    def marks(self, table):
        return tuple(self.conn.execute(f"SELECT COUNT(*), COALESCE(MAX(rowid), 0) FROM {table}").fetchone())

    def heartbeats(self):
        return self.conn.execute("SELECT * FROM event WHERE entity = 'review_item' AND kind = 'sweep_run' "
                                 "ORDER BY event_id").fetchall()


class ClearGoneTest(SweepBase):
    def test_clear_gone_clears_items_of_merged_removed_closed_and_no_go_companies(self):
        keep = self.company("Keep")
        merged_into = self.company("Survivor")
        gone = {"merged": self.company("Dupe", "removed"), "removed": self.company("Gone", "removed"),
                "closed": self.company("Shut", "closed"), "no_go": self.company("Nope")}
        self.conn.execute("UPDATE company SET merged_into = ? WHERE company_id = ?", (merged_into, gone["merged"]))
        self.conn.execute("INSERT INTO no_go(company_id, reason, source, created_at) "
                          "VALUES (?, 'other', 'devanshu', '2026-11-01T00:00:00Z')", (gone["no_go"],))
        lifted = self.company("Lifted")
        self.conn.execute("INSERT INTO no_go(company_id, reason, source, created_at, lifted_at, lift_reason) "
                          "VALUES (?, 'other', 'devanshu', '2026-11-01T00:00:00Z', '2026-11-02T00:00:00Z', 'back')",
                          (lifted,))
        ids = {name: seed_item(self.conn, "flagged", cid) for name, cid in gone.items()}
        ids["size_check"] = seed_item(self.conn, "size_check", gone["no_go"])
        kept = seed_item(self.conn, "size_unknown", keep)
        kept_lifted = seed_item(self.conn, "size_unknown", lifted)
        self.assertEqual(clear_gone(self.conn, now=NOW), 5)
        for name, review_id in ids.items():
            state, resolution, note = self.state(review_id)
            self.assertEqual((state, resolution), ("resolved", "cleared"), name)
            self.assertTrue(note and chr(0x2014) not in note, name)
        self.assertEqual(self.state(kept)[0], "open")
        self.assertEqual(self.state(kept_lifted)[0], "open")
        self.assertEqual(clear_gone(self.conn, now=NOW), 0)

    def test_clear_gone_clears_board_items_of_an_inactive_board_and_keeps_a_quiet_board_item(self):
        cid = self.company()
        dead_board, quiet_board = seed_board(self.conn, cid, token="dead"), seed_board(self.conn, cid, token="quiet")
        self.conn.execute("UPDATE board SET active = 0 WHERE board_id = ?", (dead_board,))
        dead = seed_item(self.conn, "board_empty", cid, board_id=dead_board)
        dead_flag = seed_item(self.conn, "flagged", cid, board_id=dead_board, raised_by="crawler")
        quiet = seed_item(self.conn, "board_unreadable", cid, board_id=quiet_board)
        self.assertEqual(clear_gone(self.conn, now=NOW), 1)
        self.assertEqual(self.state(dead)[:2], ("resolved", "cleared"))
        self.assertEqual(self.state(dead_flag)[0], "open")  # only the board kinds follow the board
        self.assertEqual(self.state(quiet)[0], "open")

    def test_clear_gone_clears_reader_items_of_a_closed_posting(self):
        cid = self.company()
        closed, open_ = seed_posting(self.conn, cid, "A"), seed_posting(self.conn, cid, "B")
        self.conn.execute("UPDATE posting SET closed_at = ?, close_reason = 'hand' WHERE posting_id = ?",
                          (NOW, closed))
        a = seed_item(self.conn, "reader_disagrees", cid, posting_id=closed)
        b = seed_item(self.conn, "reader_disagrees", cid, posting_id=open_)
        self.assertEqual(clear_gone(self.conn, now=NOW), 1)
        self.assertEqual((self.state(a)[0], self.state(b)[0]), ("resolved", "open"))

    def test_clear_gone_clears_a_merge_item_when_a_side_was_merged_elsewhere(self):
        a, b, c = self.company("A"), self.company("B"), self.company("C")
        elsewhere, removed = self.company("Elsewhere", "removed"), self.company("Removed", "removed")
        self.conn.execute("UPDATE company SET merged_into = ? WHERE company_id = ?", (c, elsewhere))
        live = seed_item(self.conn, "merge_candidate", a, evidence_key=str(b))
        merged = seed_item(self.conn, "merge_candidate", a, evidence_key=str(elsewhere))
        gone = seed_item(self.conn, "merge_candidate", a, evidence_key=str(removed))
        self.assertEqual(clear_gone(self.conn, now=NOW), 2)
        self.assertEqual((self.state(live)[0], self.state(merged)[0], self.state(gone)[0]),
                         ("open", "resolved", "resolved"))
        # The merged side's question still asks a real pair, so it moved to the company it was merged into.
        moved = self.conn.execute("SELECT company_id, evidence_key FROM review_item WHERE kind = 'merge_candidate' "
                                  "AND state = 'open' AND review_id NOT IN (?, ?, ?)", (live, merged, gone)).fetchall()
        self.assertEqual([tuple(r) for r in moved], [(a, str(c))])


class ClearStaleTest(SweepBase):
    def old(self, review_id, days=20):
        self.conn.execute("UPDATE review_item SET created_at = strftime('%Y-%m-%dT%H:%M:%SZ', ?, ?) "
                          "WHERE review_id = ?", (NOW, f"-{days} days", review_id))

    def plan(self, cid, state="picked"):
        seed_plan_row(self.conn, cid, TODAY, state)

    def test_clear_stale_only_after_the_age_and_only_out_of_play(self):
        in_play, out, young = self.company("InPlay"), self.company("Out"), self.company("Young")
        self.plan(in_play)
        a = seed_item(self.conn, "size_unknown", in_play)
        b = seed_item(self.conn, "size_unknown", out)
        c = seed_item(self.conn, "size_unknown", young)
        d = seed_item(self.conn, "domain_missing", out)
        e = seed_item(self.conn, "sponsorship_unclear", out)
        crawler = seed_item(self.conn, "domain_missing", out, raised_by="crawler")
        other = seed_item(self.conn, "flagged", out)
        for review_id in (a, b, d, e, crawler, other):
            self.old(review_id)
        self.old(c, days=3)
        self.assertEqual(clear_stale(self.conn, now=NOW, cfg=CFG), 3)
        self.assertEqual([self.state(i)[0] for i in (a, c, crawler, other)], ["open"] * 4)
        for review_id in (b, d, e):
            self.assertEqual(self.state(review_id), ("resolved", "cleared", "No longer in your plan."))

    def test_the_boundary_is_stale_days(self):
        out = self.company()
        self.plan(self.company())
        a = seed_item(self.conn, "size_unknown", out)
        self.old(a, days=13)
        self.assertEqual(clear_stale(self.conn, now=NOW, cfg=CFG), 0)
        self.old(a, days=14)
        self.assertEqual(clear_stale(self.conn, now=NOW, cfg=CFG), 1)

    def test_a_sponsorship_item_in_the_top_n_stays_and_a_domain_item_of_an_emailing_company_stays(self):
        top, emailing = self.company("Top"), self.company("Emailing")
        self.plan(self.company())
        a = seed_item(self.conn, "sponsorship_unclear", top)
        b = seed_item(self.conn, "domain_missing", emailing)
        seed_queued_message(self.conn, seed_email_person(self.conn, emailing), "drafted")
        for review_id in (a, b):
            self.old(review_id)
        with mock.patch("outreach.ranking.scores.ranked_list", return_value=[SimpleNamespace(company_id=top)]):
            self.assertEqual(clear_stale(self.conn, now=NOW, cfg=CFG), 0)
        self.assertEqual((self.state(a)[0], self.state(b)[0]), ("open", "open"))

    def test_clear_stale_is_skipped_when_no_recent_list_exists(self):
        out = self.company()
        a = seed_item(self.conn, "size_unknown", out)
        self.old(a)
        self.assertEqual(clear_stale(self.conn, now=NOW, cfg=CFG), 0)
        report = sweep_needs_you(self.conn, now=NOW, cfg=CFG)
        self.assertTrue(any("no recent daily list" in s for s in report.skipped), report.skipped)
        self.assertEqual(self.state(a)[0], "open")
        seed_plan_row(self.conn, self.company(), "2026-11-01", "picked")  # a list, but older than planned_days
        self.assertEqual(clear_stale(self.conn, now=NOW, cfg=CFG), 0)
        self.assertEqual(self.state(a)[0], "open")


class SweepTest(SweepBase):
    def build(self):
        """A picked company with no size and an unclear posting, and a company with a drafted email and no domain."""
        picked = self.company("Picked")
        seed_plan_row(self.conn, picked, TODAY, "picked")
        seed_posting(self.conn, picked, "Software Engineer, New Grad", UNCLEAR, "Boston, MA")
        rebuild(self.conn)
        mailer = self.company("Mailer")
        seed_queued_message(self.conn, seed_email_person(self.conn, mailer), "drafted")
        return picked, mailer

    def test_sweep_raises_all_three_kinds_and_a_second_sweep_changes_nothing(self):
        self.build()
        first = sweep_needs_you(self.conn, now=NOW, cfg=CFG)
        self.assertEqual(first.raised, {"size_unknown": 1, "domain_missing": 1, "sponsorship_unclear": 1})
        self.assertEqual(first.cleared, {})
        self.assertTrue(first.changed)
        items, events = self.marks("review_item"), self.marks("event")
        second = sweep_needs_you(self.conn, now=NOW, cfg=CFG)
        self.assertFalse(second.changed)
        self.assertEqual(self.marks("review_item"), items)
        self.assertLessEqual(self.marks("event")[0] - events[0], 1)
        later = sweep_needs_you(self.conn, now="2026-11-10T15:40:00Z", cfg=CFG)
        self.assertFalse(later.changed)
        self.assertEqual(self.marks("review_item"), items)

    def test_sweep_clears_what_went_away_and_reports_it_by_kind(self):
        picked, _ = self.build()
        sweep_needs_you(self.conn, now=NOW, cfg=CFG)
        seed_status(self.conn, picked, "removed")
        report = sweep_needs_you(self.conn, now="2026-11-10T16:00:00Z", cfg=CFG)
        self.assertEqual(report.cleared, {"size_unknown": 1, "sponsorship_unclear": 1})
        self.assertEqual(report.raised, {})

    def test_sweep_reports_a_skipped_sponsorship_sync(self):
        self.build()
        self.conn.execute("UPDATE ruleset SET is_current = 0")
        report = sweep_needs_you(self.conn, now=NOW, cfg=CFG)
        self.assertTrue(any("ruleset not current" in s for s in report.skipped), report.skipped)
        self.assertEqual(report.raised.get("sponsorship_unclear", 0), 0)

    def test_every_full_sweep_writes_one_heartbeat_per_half_hour(self):
        sweep_needs_you(self.conn, now="2026-11-10T14:05:00Z", cfg=CFG)
        sweep_needs_you(self.conn, now="2026-11-10T14:20:00Z", cfg=CFG)
        (beat,) = self.heartbeats()
        self.assertEqual((beat["idempotency_key"], beat["actor"], beat["entity_id"], beat["company_id"]),
                         ("sweep:2026-11-10T14:00", "system", None, None))
        self.assertEqual(beat["at"], "2026-11-10T14:05:00Z")
        sweep_needs_you(self.conn, now="2026-11-10T14:35:00Z", cfg=CFG)
        self.assertEqual([b["idempotency_key"] for b in self.heartbeats()],
                         ["sweep:2026-11-10T14:00", "sweep:2026-11-10T14:30"])

    def test_a_company_ids_sweep_writes_no_heartbeat(self):
        sweep_needs_you(self.conn, now=NOW, company_ids=[self.company()], cfg=CFG)
        self.assertEqual(self.heartbeats(), [])

    def test_the_heartbeat_holds_the_counts_and_skips(self):
        self.build()
        sweep_needs_you(self.conn, now=NOW, cfg=CFG)
        import json
        detail = json.loads(self.heartbeats()[0]["detail"])
        self.assertEqual(detail["raised"], {"size_unknown": 1, "domain_missing": 1, "sponsorship_unclear": 1})
        self.assertIn("skipped", detail)

    def test_a_failing_sync_writes_no_heartbeat(self):
        with mock.patch("outreach.review.triggers.sync_domain_missing", side_effect=sqlite3.OperationalError("boom")):
            with self.assertRaises(sqlite3.OperationalError):
                sweep_needs_you(self.conn, now=NOW, cfg=CFG)
        self.assertEqual(self.heartbeats(), [])

    def test_company_ids_limits_the_whole_sweep(self):
        a, b = self.company("A"), self.company("B")
        for cid in (a, b):
            seed_plan_row(self.conn, cid, TODAY, "picked")
        gone_a, gone_b = self.company("GoneA", "removed"), self.company("GoneB", "removed")
        item_a, item_b = seed_item(self.conn, "flagged", gone_a), seed_item(self.conn, "flagged", gone_b)
        report = sweep_needs_you(self.conn, now=NOW, company_ids=[a, gone_a], cfg=CFG)
        self.assertEqual(report.raised, {"size_unknown": 1})
        self.assertEqual(report.cleared, {"flagged": 1})
        sizes = self.conn.execute("SELECT company_id FROM review_item WHERE kind = 'size_unknown'").fetchall()
        self.assertEqual([r[0] for r in sizes], [a])
        self.assertEqual((self.state(item_a)[0], self.state(item_b)[0]), ("resolved", "open"))

    def test_the_report_type(self):
        report = SweepReport(raised={}, cleared={}, skipped=())
        self.assertFalse(report.changed)
        self.assertTrue(SweepReport(raised={}, cleared={"flagged": 1}, skipped=()).changed)
        self.assertFalse(SweepReport(raised={}, cleared={}, skipped=("x",)).changed)


class CommandTest(SweepBase):
    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        path = self.conn.execute("PRAGMA database_list").fetchone()["file"]
        with mock.patch.dict(os.environ, {"OUTREACH_DB": path}), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def seed_picked(self, name="Picked"):
        cid = self.company(name)
        seed_plan_row(self.conn, cid, ny_date(utcnow()), "picked")
        return cid

    def test_command_prints_counts_and_dry_run_saves_nothing(self):
        self.seed_picked()
        code, out, _ = self.run_main("sweep", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("Raised 1 size_unknown.", out)
        self.assertIn("dry run, nothing saved", out)
        self.assertEqual(self.marks("review_item")[0], 0)
        self.assertEqual(self.heartbeats(), [])
        code, out, _ = self.run_main("sweep")
        self.assertEqual(code, 0)
        self.assertIn("Raised 1 size_unknown.", out)
        self.assertNotIn("dry run", out)
        self.assertEqual(self.marks("review_item")[0], 1)
        self.assertEqual(len(self.heartbeats()), 1)

    def test_command_says_so_when_there_is_nothing_to_do(self):
        code, out, _ = self.run_main("sweep")
        self.assertEqual(code, 0)
        self.assertIn("Nothing to raise or clear.", out)

    def test_command_limits_to_one_company(self):
        a, b = self.seed_picked("A"), self.seed_picked("B")
        code, out, _ = self.run_main("sweep", "--company", str(a))
        self.assertEqual(code, 0)
        rows = self.conn.execute("SELECT company_id FROM review_item").fetchall()
        self.assertEqual([r[0] for r in rows], [a])
        self.assertEqual(self.heartbeats(), [])

    def test_command_prints_a_skip(self):
        self.conn.execute("UPDATE ruleset SET is_current = 0")
        _, out, _ = self.run_main("sweep")
        self.assertIn("Skipped: ", out)
        self.assertIn("ruleset not current", out)

    def test_command_refuses_the_live_path_outside_the_live_checkout(self):
        with mock.patch.dict(os.environ, {"OUTREACH_DB": ""}), mock.patch("live_checkout.is_live", return_value=False), \
                mock.patch("sqlite3.connect") as opened:
            with self.assertRaises(SystemExit) as caught:
                main(["sweep"])
        self.assertIn("live checkout", str(caught.exception.code))
        opened.assert_not_called()

    def test_command_reports_a_store_error_and_exits_1(self):
        missing = str(Path(os.environ["OUTREACH_DB"]).parent / "nothing-here.db")
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"OUTREACH_DB": missing}), contextlib.redirect_stderr(err):
            self.assertEqual(main(["sweep"]), 1)
        self.assertIn("no store", err.getvalue())


class TemplateTest(unittest.TestCase):
    def text(self):
        return TEMPLATE.read_text(encoding="utf-8")

    def test_launchd_template_has_the_label_interval_and_no_secret(self):
        text = self.text()
        self.assertEqual(set(re.findall(r"\{\{[A-Z_]+\}\}", text)), {"{{LIVE_CHECKOUT}}", "{{LOG_PATH}}"})
        plist = plistlib.loads(text.replace("{{LIVE_CHECKOUT}}", "/live/checkout")
                               .replace("{{LOG_PATH}}", "/logs/needs-you.log").encode("utf-8"))
        self.assertEqual(plist["Label"], "com.devanshu.outreach-needs-you")
        self.assertEqual(plist["StartInterval"], 1800)
        self.assertEqual(plist["WorkingDirectory"], "/live/checkout")
        self.assertEqual(plist["ProgramArguments"],
                         ["/live/checkout/.venv/bin/python", "-m", "outreach.review", "sweep"])
        self.assertEqual((plist["StandardOutPath"], plist["StandardErrorPath"]),
                         ("/logs/needs-you.log", "/logs/needs-you.log"))
        self.assertEqual(sorted(plist), ["Label", "ProgramArguments", "StandardErrorPath", "StandardOutPath",
                                         "StartInterval", "WorkingDirectory"])
        for word in ("OUTREACH_DB", "EnvironmentVariables", "token", "secret", "password"):
            self.assertNotIn(word, text)
        self.assertEqual(sum(1 for c in text if c in (chr(0x2013), chr(0x2014))), 0)

    def test_launchd_template_renders_with_the_deploy_tool(self):
        try:
            from outreach.deploy import jobs
        except ImportError:
            self.skipTest("outreach.deploy.jobs is not on this branch")
        label = "com.devanshu.outreach-needs-you"
        rendered = jobs.render(self.text(), live_root="/tmp/live checkout", log_path="/tmp/logs/needs-you.log",
                               label=label)
        plist = plistlib.loads(rendered.encode("utf-8"))
        self.assertEqual(plist["Label"], label)
        self.assertEqual(plist["ProgramArguments"][0], "/tmp/live checkout/.venv/bin/python")
        job = next(j for j in jobs.NEW_JOBS if j.label == label)
        self.assertEqual(job.template, "launchd/com.devanshu.outreach-needs-you.plist.template")


if __name__ == "__main__":
    unittest.main()
