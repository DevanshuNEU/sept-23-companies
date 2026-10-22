"""Stale postings, derived from the posted date and the board read state (issue #21, Task 16a)."""
import json
import subprocess
import sys
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest import mock

from outreach.crawl import stale
from outreach.crawl.boardstate import load_board
from outreach.crawl.cadence import add_seconds
from outreach.crawl.config import load_crawl_config
from outreach.crawl.stale import STALE_NOT_ON_BOARD, STALE_OLD, Stale, load_stale, months_before, stale_reason
from outreach.net import FetchError, Response
from outreach.store import start_job
from tests.fakes import FakeClient, status_response, text_response
from tests.storetest import fresh_store, seed_board, seed_company

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures" / "crawl"
TODAY = date(2026, 10, 22)
SEEN = "2026-10-18T08:00:00Z"


def reason(**kw) -> Stale | None:
    args = dict(posted_on=None, last_seen_at=SEEN, closed_at=None, board_empty_streak=None, today=TODAY,
                max_age_months=12)
    args.update(kw)
    return stale_reason(**args)


class PureRuleTests(unittest.TestCase):
    def test_months_before_clamps_to_month_end(self):
        self.assertEqual(months_before(date(2026, 3, 31), 1), date(2026, 2, 28))
        self.assertEqual(months_before(date(2024, 2, 29), 12), date(2023, 2, 28))
        self.assertEqual(months_before(date(2026, 10, 22), 12), date(2025, 10, 22))
        self.assertEqual(months_before(date(2026, 1, 15), 2), date(2025, 11, 15))
        self.assertEqual(months_before(date(2026, 5, 31), 3), date(2026, 2, 28))

    def test_exactly_twelve_months_is_not_stale_and_a_day_more_is(self):
        self.assertIsNone(reason(posted_on="2025-10-22"))
        self.assertEqual(reason(posted_on="2025-10-21"), Stale(("old",), "posted 2025-10-21, over 12 months"))

    def test_a_2016_posting_is_stale_by_age_with_its_date(self):
        r = reason(posted_on="2016-03-02")
        self.assertEqual(r.reason, "posted 2016-03-02, over 12 months")
        self.assertEqual(r.kinds, (STALE_OLD,))

    def test_the_months_come_from_the_argument(self):
        self.assertEqual(reason(posted_on="2026-03-20", max_age_months=6),
                         Stale(("old",), "posted 2026-03-20, over 6 months"))
        self.assertIsNone(reason(posted_on="2026-05-20", max_age_months=6))

    def test_no_posted_on_is_not_stale_by_age(self):
        self.assertIsNone(reason(posted_on=None))
        self.assertIsNone(reason(posted_on=None, board_empty_streak=0))

    def test_first_empty_read_state_is_not_on_the_board_with_the_last_seen_date(self):
        r = reason(board_empty_streak=1)
        self.assertEqual(r, Stale(("not_on_board",), "not on the board since 2026-10-18"))
        self.assertEqual(r.kinds, (STALE_NOT_ON_BOARD,))

    def test_no_board_and_a_never_empty_board_have_no_absence_signal(self):
        self.assertIsNone(reason(board_empty_streak=None))
        self.assertIsNone(reason(board_empty_streak=0))

    def test_closed_posting_is_never_stale(self):
        self.assertIsNone(reason(posted_on="2016-03-02", board_empty_streak=1, closed_at="2026-10-20T08:00:00Z"))

    def test_both_reasons_are_joined_in_order(self):
        r = reason(posted_on="2016-03-02", board_empty_streak=1)
        self.assertEqual(r.kinds, ("old", "not_on_board"))
        self.assertEqual(r.reason, "posted 2016-03-02, over 12 months; not on the board since 2026-10-18")

    def test_reasons_have_no_dashes(self):
        r = reason(posted_on="2016-03-02", board_empty_streak=1)
        self.assertFalse(set(r.reason) & {"–", "—"})
        self.assertNotIn("-", r.reason.replace("2016-03-02", "").replace("2026-10-18", ""))

    def test_importing_stale_loads_no_adapter_or_http_module(self):
        code = ("import sys, outreach.crawl.stale; "
                "print([m for m in sys.modules if m.startswith(('outreach.crawl.ats', 'outreach.crawl.polite', "
                "'outreach.net'))])")
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
        self.assertEqual(out.stdout.strip(), "[]")

    def test_the_module_source_imports_nothing_but_the_standard_library_and_config(self):
        source = (ROOT / "outreach" / "crawl" / "stale.py").read_text(encoding="utf-8")
        imports = [ln for ln in source.splitlines() if ln.startswith(("import ", "from "))]
        for ln in imports:
            self.assertRegex(ln, r"^(from|import) (__future__|calendar|json|sqlite3|collections\.abc|dataclasses|"
                                 r"datetime|outreach\.crawl\.config)\b", ln)


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.company = seed_company(self.conn, "Acme")
        self.board_id = seed_board(self.conn, self.company)
        self.n = 0

    def board(self, *, last_read=None, empty=0, error=0, board_id=None):
        """Put the board in a read state the schema allows."""
        if last_read is None:
            return
        self.conn.execute(
            "UPDATE board SET last_read = ?, last_read_at = ?, streak_since = ?, empty_streak = ?, error_streak = ? "
            "WHERE board_id = ?", (last_read, "2026-10-20T08:00:00Z", "2026-10-20T08:00:00Z", empty, error,
                                   board_id or self.board_id))

    def add(self, *, posted_on=None, seen=SEEN, closed=None, board_id="own", company=None):
        self.n += 1
        bid = self.board_id if board_id == "own" else board_id
        key = f"greenhouse:acme:{self.n}" if bid else f"url:https://x.example/{self.n}"
        return self.conn.execute(
            "INSERT INTO posting(company_id, board_id, source_key, title, posted_on, first_seen_at, last_seen_at, "
            "closed_at, close_reason) VALUES (?, ?, ?, 'Software Engineer', ?, ?, ?, ?, ?)",
            (company or self.company, bid, key, posted_on, seen, seen, closed,
             "gone_from_board" if closed else None)).lastrowid


class LoadStaleTests(StoreCase):
    def test_a_2016_posting_still_listed_is_stale_by_age(self):
        p = self.add(posted_on="2016-03-02")
        self.assertEqual(load_stale(self.conn, TODAY),
                         {p: Stale(("old",), "posted 2016-03-02, over 12 months")})

    def test_load_stale_uses_the_shipped_months_and_the_config(self):
        year = self.add(posted_on="2025-06-01")  # 16 months old
        old = self.add(posted_on="2025-08-22")   # 14 months old
        shipped = load_crawl_config()
        self.assertEqual(shipped.stale_after_months, 12)
        self.assertEqual(set(load_stale(self.conn, TODAY)), {year, old})
        self.assertEqual(set(load_stale(self.conn, TODAY, max_age_months=15)), {year})
        with mock.patch.object(stale, "load_crawl_config", lambda: replace(shipped, stale_after_months=18)):
            self.assertEqual(load_stale(self.conn, TODAY), {})
            self.assertEqual(set(load_stale(self.conn, TODAY, max_age_months=6)), {year, old})

    def test_error_streak_alone_never_makes_a_posting_stale(self):
        self.add(seen="2026-04-20T08:00:00Z")
        self.add(seen="2026-04-20T08:00:00Z", posted_on="2026-04-01")
        self.board(last_read="error", error=3)
        self.assertEqual(load_stale(self.conn, TODAY), {})

    def test_an_error_after_an_empty_keeps_the_postings_stale(self):
        p = self.add()
        self.board(last_read="error", empty=1, error=1)
        self.assertEqual(load_stale(self.conn, TODAY), {p: Stale(("not_on_board",), "not on the board since 2026-10-18")})
        self.assertIsNone(self.conn.execute("SELECT closed_at FROM posting WHERE posting_id = ?", (p,)).fetchone()[0])

    def test_unchanged_reads_do_not_make_postings_stale(self):
        self.add(seen="2026-09-12T08:00:00Z")
        self.board(last_read="unchanged")
        self.assertEqual(load_stale(self.conn, TODAY), {})

    def test_an_ok_read_does_not_make_postings_stale(self):
        self.add()
        self.board(last_read="ok")
        self.assertEqual(load_stale(self.conn, TODAY), {})

    def test_posting_on_a_never_read_board_or_with_no_board_is_not_stale_by_absence(self):
        never = self.add(seen="2026-01-01T08:00:00Z")
        url = self.add(board_id=None, seen="2026-01-01T08:00:00Z")
        self.assertEqual(load_stale(self.conn, TODAY), {})
        old_never = self.add(posted_on="2016-03-02")
        old_url = self.add(board_id=None, posted_on="2016-03-02")
        got = load_stale(self.conn, TODAY)
        self.assertEqual(set(got), {old_never, old_url})
        self.assertTrue(all(s.kinds == ("old",) for s in got.values()))
        self.assertNotIn(never, got)
        self.assertNotIn(url, got)

    def test_closed_posting_is_not_stale(self):
        for reason_ in ("gone_from_board", "board_empty_twice", "page_closed", "hand", "imported_closed"):
            self.n += 1
            self.conn.execute(
                "INSERT INTO posting(company_id, board_id, source_key, title, posted_on, first_seen_at, last_seen_at, "
                "closed_at, close_reason) VALUES (?, ?, ?, 'SWE', '2016-03-02', ?, ?, ?, ?)",
                (self.company, self.board_id, f"greenhouse:acme:c{self.n}", SEEN, SEEN, "2026-10-20T08:00:00Z", reason_))
        self.board(last_read="empty", empty=1)
        self.assertEqual(load_stale(self.conn, TODAY), {})

    def test_both_reasons_are_joined_in_order_from_the_store(self):
        p = self.add(posted_on="2016-03-02")
        self.board(last_read="empty", empty=1)
        self.assertEqual(load_stale(self.conn, TODAY)[p],
                         Stale(("old", "not_on_board"),
                               "posted 2016-03-02, over 12 months; not on the board since 2026-10-18"))

    def test_load_stale_filters_by_posting_and_company_and_returns_only_stale(self):
        other = seed_company(self.conn, "Beta")
        other_board = seed_board(self.conn, other, token="beta")
        a = self.add(posted_on="2016-03-02")
        b = self.add(posted_on="2015-01-01")
        live = self.add(posted_on="2026-09-01")
        c = self.n + 1
        self.conn.execute(
            "INSERT INTO posting(company_id, board_id, source_key, title, posted_on, first_seen_at, last_seen_at) "
            "VALUES (?, ?, 'greenhouse:beta:1', 'SWE', '2014-01-01', ?, ?)", (other, other_board, SEEN, SEEN))
        c = self.conn.execute("SELECT posting_id FROM posting WHERE source_key = 'greenhouse:beta:1'").fetchone()[0]
        self.assertEqual(set(load_stale(self.conn, TODAY)), {a, b, c})
        self.assertNotIn(live, load_stale(self.conn, TODAY))
        self.assertEqual(set(load_stale(self.conn, TODAY, posting_ids=[a, live, c])), {a, c})
        self.assertEqual(set(load_stale(self.conn, TODAY, company_ids=[other])), {c})
        self.assertEqual(set(load_stale(self.conn, TODAY, company_ids=(self.company,), posting_ids={b, c})), {b})
        self.assertEqual(load_stale(self.conn, TODAY, posting_ids=[]), {})
        self.assertEqual(load_stale(self.conn, TODAY, company_ids=set()), {})

    def test_load_stale_is_one_read_only_query(self):
        self.add(posted_on="2016-03-02")
        self.board(last_read="empty", empty=1)
        before = {t: self.conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                  for t in ("posting", "board", "review_item", "event", "job_run")}
        changes = self.conn.total_changes
        statements: list[str] = []
        self.conn.set_trace_callback(statements.append)
        try:
            load_stale(self.conn, TODAY, posting_ids=[1, 2, 3], company_ids=[self.company])
        finally:
            self.conn.set_trace_callback(None)
        self.assertEqual(len(statements), 1, statements)
        self.assertTrue(statements[0].lstrip().upper().startswith("SELECT"), statements)
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(self.conn.total_changes, changes)
        after = {t: self.conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in before}
        self.assertEqual(before, after)

    def test_a_large_posting_id_list_is_still_one_query(self):
        p = self.add(posted_on="2016-03-02")
        self.assertEqual(set(load_stale(self.conn, TODAY, posting_ids=range(1, 100_000))), {p})


# --- Step 5: through read_board, with the real Greenhouse adapter ------------------------------------------------

from outreach.crawl.ats.greenhouse import GreenhouseAdapter  # noqa: E402
from outreach.crawl.ingest import read_board  # noqa: E402

URL = "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"
JOBS = (FIXTURES / "greenhouse_jobs.json").read_bytes()
OLD2016 = (FIXTURES / "greenhouse_2016.json").read_bytes()
EMPTY = (FIXTURES / "greenhouse_empty.json").read_bytes()
T0 = "2026-10-18T08:00:00Z"


def at(days: float) -> str:
    return add_seconds(T0, int(days * 86400))


def ok(body=JOBS, etag='"A"'):
    return Response(200, "", body, {"etag": etag} if etag else {})


class ThroughReadBoardTests(StoreCase):
    def setUp(self):
        super().setUp()
        self.cfg = load_crawl_config()
        self.clock = [T0]
        for target in ("outreach.crawl.bodies.utcnow", "outreach.rules.rebuild.utcnow"):
            patch = mock.patch(target, lambda: self.clock[0])
            patch.start()
            self.addCleanup(patch.stop)

    def read(self, answer, now=T0):
        client = FakeClient({URL: answer})
        self.clock[0] = now
        board = load_board(self.conn, self.board_id)
        out = read_board(self.conn, board, GreenhouseAdapter(client), run_id=start_job(self.conn, "read_boards"),
                         now=now, cfg=self.cfg)
        self.assertEqual(client.unrouted, [])
        return out

    def today(self, now):
        return date.fromisoformat(now[:10])

    def open_count(self):
        return self.conn.execute("SELECT count(*) FROM posting WHERE closed_at IS NULL").fetchone()[0]

    def rows(self):
        return self.conn.execute("SELECT * FROM posting ORDER BY posting_id").fetchall()

    def test_a_2016_posting_still_listed_is_stale_by_age_and_stays_open(self):
        out = self.read(ok(OLD2016))
        self.assertEqual(out.result, "ok")
        p = self.rows()[0]
        self.assertEqual((p["posted_on"], p["closed_at"]), ("2016-03-02", None))
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM current_decision WHERE posting_id = ?",
                                               (p["posting_id"],)).fetchone())
        self.assertEqual(load_stale(self.conn, self.today(T0)),
                         {p["posting_id"]: Stale(("old",), "posted 2016-03-02, over 12 months")})
        self.assertEqual(self.open_count(), 1)

    def test_first_empty_read_makes_open_postings_stale_and_the_second_closes_them(self):
        self.read(ok(JOBS))
        self.read(ok(EMPTY, None), at(2))
        stale_now = load_stale(self.conn, self.today(at(2)))
        self.assertEqual(self.open_count(), 3)
        self.assertEqual(len(stale_now), 3)
        self.assertEqual({s.reason for s in stale_now.values()}, {"not on the board since 2026-10-18"})
        self.read(ok(EMPTY, None), at(3))
        self.assertEqual(self.open_count(), 0)
        self.assertEqual({r["close_reason"] for r in self.rows()}, {"board_empty_twice"})
        self.assertEqual(load_stale(self.conn, self.today(at(3))), {})

    def test_a_failed_read_never_makes_a_posting_stale(self):
        self.read(ok(JOBS))
        html = text_response("<html><body>Just a moment...</body></html>", headers={"content-type": "text/html"})
        answers = (status_response(500), status_response(403), status_response(429), html,
                   FetchError("network", "boom"), status_response(500), html)
        for i, answer in enumerate(answers, start=1):
            with self.subTest(read=i):
                self.assertEqual(self.read(answer, at(i)).result, "error")
                self.assertEqual(load_stale(self.conn, self.today(at(i))), {})
                self.assertEqual(self.open_count(), 3)
        self.assertGreaterEqual(self.conn.execute("SELECT count(*) FROM review_item WHERE kind = 'board_unreadable'")
                                .fetchone()[0], 1)

    def test_a_listed_posting_after_an_empty_read_is_live_again(self):
        self.read(ok(JOBS))
        self.read(ok(EMPTY, None), at(1))
        self.assertEqual(len(load_stale(self.conn, self.today(at(1)))), 3)
        self.read(ok(JOBS), at(2))
        self.assertEqual(load_stale(self.conn, self.today(at(2))), {})
        self.assertEqual({r["last_seen_at"] for r in self.rows()} - {at(2)}, set())

    def test_stale_never_changes_what_the_crawler_writes(self):
        modern = json.loads(OLD2016)
        modern["jobs"][0]["first_published"] = "2026-03-02T09:00:00-05:00"
        modern["jobs"][0]["updated_at"] = "2026-03-02T09:00:00-05:00"
        snapshots = []
        for body in (OLD2016, json.dumps(modern).encode("utf-8")):
            self.setUp()
            self.read(ok(body))
            snap = {
                "posting": [tuple(r)[:2] + tuple(r)[3:8] + tuple(r)[9:] for r in
                            self.conn.execute("SELECT posting_id, company_id, source_key, title, location, country, "
                                              "url, pay, first_seen_at, last_seen_at, closed_at, close_reason FROM posting")],
                "decision": [tuple(r) for r in self.conn.execute(
                    "SELECT posting_id, lane, level_tier, eligibility, provisional FROM current_decision")],
                "body": [tuple(r) for r in self.conn.execute("SELECT posting_id, body, body_sha256 FROM posting_body")],
                "board_read": [tuple(r) for r in self.conn.execute("SELECT result, postings FROM board_read")],
                "items": self.conn.execute("SELECT count(*) FROM review_item").fetchone()[0],
            }
            snapshots.append(snap)
        self.assertEqual(snapshots[0], snapshots[1])


if __name__ == "__main__":
    unittest.main()
