"""`python -m outreach.crawl churn` (#152, Task 3): postings closed and new per board over a window.

Postings are seeded straight into a scratch store. Nothing here touches the network or the live store.
"""
import contextlib
import io
import itertools
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from outreach.crawl import __main__ as cli
from outreach.crawl.churn import BoardChurn, churn
from tests.ranking_store import board, decision
from tests.storetest import fresh_store, seed_company

NOW = "2026-11-02T12:00:00Z"
IN_WINDOW = "2026-11-02T06:00:00Z"
BEFORE_WINDOW = "2026-10-30T06:00:00Z"
_keys = itertools.count(1)


class ChurnCase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.company = seed_company(self.conn)

    def board(self, ats="workday"):
        return board(self.conn, self.company, ats=ats)

    def post(self, board_id, title="Engineer", location="Remote", first="2026-10-01T00:00:00Z", closed=None):
        return self.conn.execute(
            "INSERT INTO posting(company_id, board_id, source_key, title, location, first_seen_at, last_seen_at, "
            "closed_at, close_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.company, board_id, f"b:{next(_keys)}", title, location, first, closed or first, closed,
             "gone_from_board" if closed else None)).lastrowid

    def one(self, board_id, days=1, limit=20):
        rows = [r for r in churn(self.conn, NOW, days, limit) if r.board_id == board_id]
        self.assertEqual(len(rows), 1)
        return rows[0]


class ChurnTests(ChurnCase):
    def test_churn_counts_closed_and_new(self):
        b = self.board()
        self.post(b, "A", first="2026-10-01T00:00:00Z", closed=IN_WINDOW)        # closed in the window
        self.post(b, "B", first="2026-10-01T00:00:00Z", closed=BEFORE_WINDOW)    # closed earlier
        self.post(b, "C", first=IN_WINDOW)                                       # new and open
        self.post(b, "D", first="2026-10-01T00:00:00Z")                          # old and open
        self.post(b, "E", first=IN_WINDOW, closed="2026-11-02T08:00:00Z")        # new and closed again
        row = self.one(b)
        self.assertEqual(row, BoardChurn(board_id=b, ats="workday", token=row.token, open=2, closed=2, new=2,
                                         relisted=0, us_engineering_open=0))

    def test_window_follows_days(self):
        b = self.board()
        self.post(b, first="2026-10-01T00:00:00Z", closed=BEFORE_WINDOW)
        self.assertEqual(churn(self.conn, NOW, 1, 20), [])
        self.assertEqual(self.one(b, days=5).closed, 1)

    def test_a_board_with_no_change_is_not_listed(self):
        b = self.board()
        self.post(b)
        self.assertEqual(churn(self.conn, NOW, 1, 20), [])

    def test_relisted_needs_same_title_and_location(self):
        b = self.board()
        self.post(b, "Engineer", "Remote", closed="2026-11-02T03:00:00Z")
        same = self.post(b, "Engineer", "Remote", first="2026-11-02T04:00:00Z")
        self.post(b, "Engineer", "Austin", first="2026-11-02T04:00:00Z")          # other location
        self.post(b, "Designer", "Remote", first="2026-11-02T04:00:00Z")          # other title
        self.assertIsNotNone(same)
        self.assertEqual(self.one(b).relisted, 1)

    def test_relisted_counts_each_new_posting_once(self):
        b = self.board()
        self.post(b, "Engineer", "Remote", closed="2026-11-02T01:00:00Z")
        self.post(b, "Engineer", "Remote", closed="2026-11-02T02:00:00Z")
        self.post(b, "Engineer", "Remote", first="2026-11-02T04:00:00Z")
        self.assertEqual(self.one(b).relisted, 1)

    def test_relisted_looks_back_seven_days_only_and_not_forward(self):
        b = self.board()
        # closed 8 days before the new one: too old
        self.post(b, "Old", "Remote", first="2026-10-01T00:00:00Z", closed="2026-10-25T00:00:00Z")
        self.post(b, "Old", "Remote", first="2026-11-02T04:00:00Z")
        # closed 6 days before: counts
        self.post(b, "Recent", "Remote", first="2026-10-01T00:00:00Z", closed="2026-10-27T05:00:00Z")
        self.post(b, "Recent", "Remote", first="2026-11-02T04:00:00Z")
        # closed after the new one was first seen: not a relist
        self.post(b, "Later", "Remote", first="2026-10-01T00:00:00Z", closed="2026-11-02T09:00:00Z")
        self.post(b, "Later", "Remote", first="2026-11-02T04:00:00Z")
        self.assertEqual(self.one(b).relisted, 1)

    def test_relisted_stays_inside_the_board(self):
        a, c = self.board(), self.board("oracle_orc")
        self.post(a, "Engineer", "Remote", closed="2026-11-02T03:00:00Z")
        self.post(c, "Engineer", "Remote", first="2026-11-02T04:00:00Z")
        self.assertEqual(self.one(c).relisted, 0)

    def test_a_posting_is_not_its_own_relist(self):
        b = self.board()
        self.post(b, "Engineer", "Remote", first="2026-11-02T01:00:00Z", closed="2026-11-02T03:00:00Z")
        self.assertEqual(self.one(b).relisted, 0)

    def test_us_engineering_open_needs_lane_us_and_open(self):
        b = self.board()
        self.post(b, closed=IN_WINDOW)
        keep = self.post(b, "Keep")
        no_us = self.post(b, "Abroad")
        unknown = self.post(b, "Unknown")
        other_lane = self.post(b, "Intern")
        closed_eng = self.post(b, "Gone", closed=IN_WINDOW)
        decision(self.conn, keep)
        decision(self.conn, no_us, us="no")
        decision(self.conn, unknown, us="unknown")
        decision(self.conn, other_lane, lane="intern")
        decision(self.conn, closed_eng)
        self.assertEqual(self.one(b).us_engineering_open, 2)

    def test_a_decision_under_an_old_ruleset_does_not_count(self):
        b = self.board()
        self.post(b, closed=IN_WINDOW)
        p = self.post(b)
        decision(self.conn, p)                                                    # makes the ruleset current
        self.conn.execute("INSERT INTO ruleset(ruleset_version, config_toml, created_at) VALUES "
                          "('r-0000000000b2', '', ?)", (NOW,))
        self.conn.execute("UPDATE ruleset SET is_current = 0")
        self.conn.execute("UPDATE ruleset SET is_current = 1 WHERE ruleset_version = 'r-0000000000b2'")
        self.assertEqual(self.one(b).us_engineering_open, 0)

    def test_churn_orders_by_closed(self):
        small, big, mid = self.board("greenhouse"), self.board("workday"), self.board("oracle_orc")
        for _ in range(1):
            self.post(small, closed=IN_WINDOW)
        for _ in range(3):
            self.post(big, closed=IN_WINDOW)
        for _ in range(2):
            self.post(mid, closed=IN_WINDOW)
        self.assertEqual([r.board_id for r in churn(self.conn, NOW, 1, 20)], [big, mid, small])

    def test_ties_go_to_the_board_with_more_new_postings(self):
        a, c = self.board(), self.board("lever")
        self.post(a, closed=IN_WINDOW)
        self.post(c, closed=IN_WINDOW)
        self.post(c, first=IN_WINDOW)
        self.assertEqual([r.board_id for r in churn(self.conn, NOW, 1, 20)], [c, a])

    def test_limit_keeps_the_top_boards(self):
        ids = [self.board(f) for f in ("greenhouse", "workday", "oracle_orc")]
        for n, b in enumerate(ids, 1):
            for _ in range(n):
                self.post(b, closed=IN_WINDOW)
        self.assertEqual([r.board_id for r in churn(self.conn, NOW, 1, 2)], [ids[2], ids[1]])

    def test_postings_without_a_board_are_ignored(self):
        self.conn.execute(
            "INSERT INTO posting(company_id, board_id, source_key, title, first_seen_at, last_seen_at, closed_at, "
            "close_reason) VALUES (?, NULL, 'url:x', 't', ?, ?, ?, 'page_closed')",
            (self.company, IN_WINDOW, IN_WINDOW, IN_WINDOW))
        self.assertEqual(churn(self.conn, NOW, 1, 20), [])


class ChurnCliTests(ChurnCase):
    def setUp(self):
        super().setUp()
        path = self.conn.execute("PRAGMA database_list").fetchone()["file"]
        env = mock.patch.dict(os.environ, {"OUTREACH_DB": path})
        env.start()
        self.addCleanup(env.stop)
        self.live = tempfile.mkdtemp(prefix="outreach-crawl-churn-")
        self.addCleanup(shutil.rmtree, self.live, True)
        # the CLI reads the clock, so seed relative to now
        from outreach.store.timefmt import utcnow
        self.stamp = utcnow()

    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv, root=self.live, live_root=self.live)
        return code, out.getvalue(), err.getvalue()

    def test_churn_cli_json(self):
        b = self.board()
        self.post(b, closed=self.stamp)
        self.post(b, first=self.stamp)
        code, out, _ = self.run_cli(["churn", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(len(data), 1)
        self.assertEqual((data[0]["board_id"], data[0]["closed"], data[0]["new"], data[0]["open"]), (b, 1, 1, 1))

    def test_churn_cli_table(self):
        b = self.board()
        self.post(b, closed=self.stamp)
        code, out, _ = self.run_cli(["churn", "--days", "2", "--limit", "5"])
        self.assertEqual(code, 0)
        self.assertIn("workday", out)
        self.assertIn("closed", out)

    def test_churn_cli_with_nothing_to_report(self):
        code, out, _ = self.run_cli(["churn"])
        self.assertEqual(code, 0)
        self.assertIn("no postings opened or closed", out)

    def test_churn_cli_rejects_a_zero_window(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(["churn", "--days", "0"], root=self.live, live_root=self.live)

    def test_churn_cli_refuses_outside_the_live_checkout(self):
        elsewhere = tempfile.mkdtemp(prefix="outreach-crawl-churn-")
        self.addCleanup(shutil.rmtree, elsewhere, True)
        with mock.patch("sqlite3.connect") as opened:
            with self.assertRaises(SystemExit):
                cli.main(["churn"], root=self.live, live_root=elsewhere)
        opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
