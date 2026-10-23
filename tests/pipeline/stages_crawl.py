"""The `read_boards` stage (#21 owns it): read the board the sources stream found, from a saved response.

`find_boards` (#31) puts one Greenhouse board in the store, `kestrelrobotics`, whose list the fixture
`board_list.json` answers (one new-grad engineering posting and one account executive). This stage runs one real
`run_tick` over that store with a `FakeClient` that serves the fixture like the Greenhouse API does: 200 with an ETag,
and 304 to a request carrying that ETag. It makes no request outside that one route and never opens a socket.

The crawler never creates board rows, so the stage seeds the company and the board only when no such board exists
(a flow that registers this stage without the sources stream). Each call of the stage is one tick, one day after the
last, so a board is due every time (the ceiling is seven days, the floor six hours).

`register` also leaves the stage on the harness as `harness.crawl` (a `CrawlStage`), so a flow test can run another
tick with a different answer (`harness.crawl.run(status_response(500))`), or read its board and the report of the
last tick. The registered callable runs one tick with the stage's default answer.

The ticks run with the shipped config, so SmartRecruiters and Pinpoint are switched off (#46). When the sources stream
made `Clearpath Inc` (a fixture company with no board of its own), the stage gives it, once, one board on every
switched-off ATS: each read before, due now, one in an empty streak with an open posting that has its decision. The
rebuild a tick starts with runs then, so its decisions are in place before the tick. Before each tick the stage
keeps a snapshot of those boards (`off_before`), and the invariant checks that the tick changed none of it.
"""
from __future__ import annotations

from unittest import mock

from outreach.crawl.cadence import add_seconds
from outreach.crawl.config import load_crawl_config
from outreach.crawl.probe import ProbeReport
from outreach.crawl.tick import TickReport, ensure_ruleset_current, resolve_combiner, run_tick
from outreach.net import Response
from tests.fakes import FakeClient
from tests.storetest import seed_board, seed_company

__all__ = ["register", "CrawlStage", "TOKEN", "COMPANY", "FIRST_TICK", "DAY_S", "list_url", "fixture_jobs",
           "OFF_COMPANY", "OFF_TOKEN"]

TOKEN = "kestrelrobotics"
COMPANY = "Kestrel Robotics"
ETAG = '"flow-1"'
FIRST_TICK = "2026-10-22T08:00:00Z"
DAY_S = 86400
EMPTY_BODY = b'{"jobs": [], "meta": {"total": 0}}'
OFF_COMPANY = "Clearpath Inc"  # a sources fixture company with no board: the switched-off boards are its
OFF_TOKEN = "clearpath"
OFF_READ_AT = "2026-10-15T08:00:00Z"  # a week before the first tick: the boards were read while their reader was on


def list_url(token: str = TOKEN) -> str:
    return f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"


def fixture_jobs(harness) -> bytes:
    return harness.fixture("board_list.json").read_bytes()


class _Clock:
    """A monotonic clock that only `sleep` moves, so the polite spacing never really waits."""

    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


class CrawlStage:
    def __init__(self, harness):
        self.harness = harness
        self.conn = harness.conn
        self.body = fixture_jobs(harness)
        self.ticks = 0
        self.report: TickReport | None = None
        self.client: FakeClient | None = None
        self.cfg = load_crawl_config()
        self.off_before: dict | None = None

    def board_id(self) -> int:
        """The `kestrelrobotics` board, which `find_boards` made; seeded here only when it is not there yet."""
        row = self.conn.execute("SELECT board_id FROM board WHERE ats = 'greenhouse' AND token = ?",
                                (TOKEN,)).fetchone()
        if row is not None:
            return row[0]
        company = self.conn.execute("SELECT company_id FROM company WHERE name = ?", (COMPANY,)).fetchone()
        company_id = company[0] if company is not None else seed_company(self.conn, COMPANY)
        return seed_board(self.conn, company_id, "greenhouse", TOKEN)

    def off_board_ids(self) -> list[int]:
        """The boards on an ATS the shipped config switches off, by id."""
        marks = ",".join("?" * len(self.cfg.ats_off))
        return [r[0] for r in self.conn.execute(
            f"SELECT board_id FROM board WHERE ats IN ({marks}) ORDER BY board_id", sorted(self.cfg.ats_off))]

    def seed_switched_off(self) -> None:
        """Once, and only when the sources stream made `OFF_COMPANY`: one read, due board per switched-off ATS. The
        first is in an error streak (one more error raises its item) with an open posting, the others in an empty
        streak (a second empty read would raise and close). Then the rebuild the tick would start with, so the posting
        has its decision before the snapshot. The posting is not engineering and its board's last read failed, so it
        is not stale and gives no role credit: the ranking stages see Clearpath as before."""
        company = self.conn.execute("SELECT company_id FROM company WHERE name = ?", (OFF_COMPANY,)).fetchone()
        if company is None or self.off_board_ids():
            return
        for i, ats in enumerate(sorted(self.cfg.ats_off)):
            board_id = seed_board(self.conn, company[0], ats, OFF_TOKEN)
            if i == 0:
                last, empty, error, why = "error", 0, 2, "HTTP 503"
                self.conn.execute(
                    "INSERT INTO posting(company_id, board_id, source_key, title, location, url, first_seen_at, "
                    "last_seen_at) VALUES (?, ?, ?, 'Account Executive', 'Boston, MA', ?, ?, ?)",
                    (company[0], board_id, f"{ats}:{OFF_TOKEN}:1", f"https://example.com/{ats}/1", OFF_READ_AT,
                     OFF_READ_AT))
            else:
                last, empty, error, why = "empty", 1, 0, ""
            self.conn.execute(
                "UPDATE board SET last_read = ?, last_read_at = ?, streak_since = ?, empty_streak = ?, error_streak = ?, "
                "last_error = ?, next_read_at = ? WHERE board_id = ?",
                (last, OFF_READ_AT, OFF_READ_AT, empty, error, why, OFF_READ_AT, board_id))
        ensure_ruleset_current(self.conn, resolve_combiner(self.conn))

    def off_snapshot(self) -> dict:
        """Everything a read could change about the switched-off boards: the board rows, their reads, postings,
        bodies, decisions and review items, and the board and posting events."""
        boards = self.off_board_ids()
        if not boards:
            return {}
        b = ",".join("?" * len(boards))
        postings = [r[0] for r in self.conn.execute(f"SELECT posting_id FROM posting WHERE board_id IN ({b})", boards)]
        p = ",".join("?" * len(postings)) or "NULL"
        queries = {
            "board": (f"SELECT * FROM board WHERE board_id IN ({b}) ORDER BY board_id", boards),
            "board_read": (f"SELECT * FROM board_read WHERE board_id IN ({b}) ORDER BY rowid", boards),
            "posting": (f"SELECT * FROM posting WHERE board_id IN ({b}) ORDER BY posting_id", boards),
            "posting_body": (f"SELECT * FROM posting_body WHERE posting_id IN ({p}) ORDER BY posting_id", postings),
            "decision": (f"SELECT * FROM decision WHERE posting_id IN ({p}) ORDER BY rowid", postings),
            "review_item": (f"SELECT * FROM review_item WHERE board_id IN ({b}) OR company_id IN "
                            f"(SELECT company_id FROM board WHERE board_id IN ({b})) ORDER BY rowid", boards + boards),
            "event": (f"SELECT * FROM event WHERE (entity = 'board' AND entity_id IN ({b})) "
                      f"OR (entity = 'posting' AND entity_id IN ({p})) ORDER BY rowid", boards + postings),
        }
        return {k: [tuple(r) for r in self.conn.execute(sql, args)] for k, (sql, args) in queries.items()}

    def last_tick_at(self) -> str:
        """The time of the tick that ran last (the first tick's time before any ran)."""
        return add_seconds(FIRST_TICK, DAY_S * max(self.ticks - 1, 0))

    def serve(self, method, url, headers, body) -> Response:
        """The fixture as the API serves it: 200 with an ETag, 304 to that ETag."""
        if headers.get("If-None-Match") == ETAG:
            return Response(304, url, b"", {"etag": ETAG})
        return Response(200, url, self.body, {"etag": ETAG, "content-type": "application/json"})

    def run(self, answer=None) -> TickReport:
        """One tick, a day after the last. `answer` is what the board's list URL says this once (a `Response`, bytes,
        an exception or a callable, as `FakeClient` takes them); None serves the fixture."""
        self.board_id()
        self.seed_switched_off()
        self.off_before = self.off_snapshot()
        now = add_seconds(FIRST_TICK, DAY_S * self.ticks)
        self.ticks += 1
        clock = _Clock()
        self.client = FakeClient({list_url(): self.serve if answer is None else answer})
        # The daily slug probe (Task 26a) is not this stage: it would ask the three ATSs about every board-less company.
        with mock.patch("outreach.crawl.tick.probe_slugs", return_value=ProbeReport(0, 0, 0, 0, 0, 0)):
            self.report = run_tick(self.conn, self.client, cfg=self.cfg, now_fn=lambda: now, clock=clock,
                                   sleep=clock.sleep)
        if self.client.unrouted:
            raise AssertionError(f"the read_boards stage asked for a URL it does not serve: {self.client.unrouted}")
        return self.report

    def empty(self) -> Response:
        return Response(200, list_url(), EMPTY_BODY, {"content-type": "application/json"})


def register(harness) -> None:
    stage = CrawlStage(harness)
    harness.crawl = stage
    harness.stages.append(("read_boards", stage.run))
