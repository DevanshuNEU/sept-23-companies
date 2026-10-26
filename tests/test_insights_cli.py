"""`python -m outreach.insights` (#81, Task 6): summary, export and duplicates on a scratch store. The store opens
only through `connect()`; summary and export write nothing to it."""
import contextlib
import dataclasses
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from outreach.insights import __main__ as cli
from outreach.insights.config import default_insights_config
from tests import apiseed as S
from tests.insights_seed import application, outcome_touch
from tests.storetest import fresh_store

SENT, LATER = "2026-10-01T15:00:00Z", "2026-10-05T15:00:00Z"


def store_path(conn) -> str:
    return conn.execute("PRAGMA database_list").fetchone()[2]


def all_counts(conn) -> dict[str, int]:
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                         "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}


def seed(conn) -> dict[str, int]:
    ids = {}
    ids["retell"] = S.company(conn, "Retell AI")
    pat = S.person(conn, ids["retell"], "Pat Lee", email="pat@retell.example",
                   linkedin_url="https://www.linkedin.com/in/patlee")
    outcome_touch(conn, ids["retell"], pat, sent_at=SENT, interview_at=LATER)
    S.evidence(conn, ids["retell"], "size", "80", low=80)
    S.board(conn, ids["retell"], "ashby")
    ids["gm_big"] = S.company(conn, "General Motors")
    application(conn, ids["gm_big"], outcome="interview", outcome_at=LATER)
    ids["gm"] = S.company(conn, "gm")
    sam = S.person(conn, ids["gm"], "Sam Roe", email="sam@roe.example")
    outcome_touch(conn, ids["gm"], sam, sent_at=SENT, replied_at=LATER)
    return ids


class CliTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.ids = seed(self.conn)
        env = mock.patch.dict(os.environ, {"OUTREACH_DB": store_path(self.conn)})
        env.start()
        self.addCleanup(env.stop)
        self.folder = Path(tempfile.mkdtemp(prefix="insights-cli-test-"))
        cfg = mock.patch.object(cli, "default_insights_config",
                                lambda: dataclasses.replace(default_insights_config(), export_dir=self.folder))
        cfg.start()
        self.addCleanup(cfg.stop)

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_summary_prints_the_interview_companies_and_too_few_to_tell(self):
        code, out, err = self.run_cli("summary")
        self.assertEqual((0, ""), (code, err))
        self.assertIn("2 companies interviewed you, out of 3 you reached.", out)
        self.assertIn("Retell AI", out)
        self.assertIn("General Motors", out)
        self.assertIn("too few to tell", out.lower())
        self.assertIn("gm is the initials of General Motors", out)
        self.assertIn("  Retell AI: interview on 2026-10-05, way in LinkedIn request, size 51 to 300, stage not "
                      "known, level not known, board Ashby", out)
        self.assertNotIn("way in no recorded", out)
        for private in ("@", "linkedin.com", "Pat Lee", "Sam Roe"):
            self.assertNotIn(private, out)
        self.assertFalse(any(c in out for c in chr(0x2013) + chr(0x2014)))

    def test_an_offer_is_dated_and_sorted_by_the_offer_not_the_old_interview(self):
        conn = fresh_store()
        self.addCleanup(conn.close)
        early = S.company(conn, "Early Interview")
        outcome_touch(conn, early, S.person(conn, early, "A One"), sent_at="2026-05-01T15:00:00Z",
                      interview_at="2026-06-05T15:00:00Z")
        won = S.company(conn, "Won Late")
        outcome_touch(conn, won, S.person(conn, won, "B Two"), sent_at="2026-01-01T15:00:00Z",
                      interview_at="2026-01-05T15:00:00Z",
                      offer_at="2026-10-20T15:00:00Z")
        with mock.patch.dict(os.environ, {"OUTREACH_DB": store_path(conn)}):
            code, out, _err = self.run_cli("summary")
        self.assertEqual(0, code)
        self.assertIn("Won Late: offer on 2026-10-20", out)
        self.assertLess(out.index("Won Late"), out.index("Early Interview"))

    def test_summary_on_an_empty_store(self):
        empty = fresh_store()
        self.addCleanup(empty.close)
        with mock.patch.dict(os.environ, {"OUTREACH_DB": store_path(empty)}):
            code, out, _err = self.run_cli("summary")
        self.assertEqual(0, code)
        self.assertIn("No interviews logged yet.", out)

    def test_export_csv_default_path_uses_the_new_york_date(self):
        with mock.patch.object(cli, "utcnow", lambda: "2026-10-26T02:00:00Z"):
            code, out, err = self.run_cli("export", "--format", "csv")
        self.assertEqual((0, ""), (code, err))
        path = (self.folder / "outcomes-2026-10-25.csv").resolve()
        self.assertTrue(path.is_file())
        self.assertEqual(f"Wrote 3 companies to {path}\n", out)
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("Pat Lee", text)
        self.assertNotIn("@", text)
        # A second run on the same day needs --force.
        with mock.patch.object(cli, "utcnow", lambda: "2026-10-26T02:00:00Z"):
            code, _out, err = self.run_cli("export", "--format", "csv")
        self.assertEqual(1, code)
        self.assertIn("--force", err)

    def test_export_json_with_people(self):
        out_path = self.folder / "mine.json"
        code, out, err = self.run_cli("export", "--format", "json", "--out", str(out_path), "--with-people")
        self.assertEqual((0, ""), (code, err))
        text = out_path.read_text(encoding="utf-8")
        self.assertIn("Pat Lee", text)
        self.assertNotIn("@", text)
        self.assertNotIn("linkedin.com", text)

    def test_export_into_the_repo_exits_1(self):
        code, out, err = self.run_cli("export", "--format", "csv", "--out", str(cli.REPO_ROOT / "x.csv"))
        self.assertEqual((1, ""), (code, out))
        self.assertIn("inside the repo", err)
        self.assertFalse((cli.REPO_ROOT / "x.csv").exists())

    def test_export_writes_nothing_to_the_store(self):
        before, changes = all_counts(self.conn), self.conn.total_changes
        for argv in (("summary",), ("export", "--format", "csv"), ("export", "--format", "json", "--with-people"),
                     ("duplicates",)):
            code, _out, err = self.run_cli(*argv)
            self.assertEqual((0, ""), (code, err), argv)
        self.assertEqual(before, all_counts(self.conn))
        self.assertEqual(changes, self.conn.total_changes)

    def test_duplicates_lists_and_raise_writes_items(self):
        code, out, _err = self.run_cli("duplicates")
        self.assertEqual(0, code)
        self.assertIn("General Motors", out)
        self.assertIn("gm is the initials of General Motors", out)
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM review_item").fetchone()[0])
        code, out, _err = self.run_cli("duplicates", "--raise")
        self.assertEqual(0, code)
        self.assertIn("1 new", out)
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM review_item WHERE kind = 'merge_candidate'")
                         .fetchone()[0])
        code, out, _err = self.run_cli("duplicates", "--raise")
        self.assertIn("0 new", out)

    def test_a_store_error_exits_1_with_the_message(self):
        missing = Path(tempfile.mkdtemp(prefix="insights-cli-missing-")) / "none.db"
        with mock.patch.dict(os.environ, {"OUTREACH_DB": str(missing)}):
            code, out, err = self.run_cli("summary")
        self.assertEqual((1, ""), (code, out))
        self.assertIn("no store at", err)
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
