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
"""
from __future__ import annotations

from outreach.crawl.cadence import add_seconds
from outreach.crawl.config import load_crawl_config
from outreach.crawl.tick import TickReport, run_tick
from outreach.net import Response
from tests.fakes import FakeClient
from tests.storetest import seed_board, seed_company

__all__ = ["register", "CrawlStage", "TOKEN", "COMPANY", "FIRST_TICK", "DAY_S", "list_url", "fixture_jobs"]

TOKEN = "kestrelrobotics"
COMPANY = "Kestrel Robotics"
ETAG = '"flow-1"'
FIRST_TICK = "2026-10-22T08:00:00Z"
DAY_S = 86400
EMPTY_BODY = b'{"jobs": [], "meta": {"total": 0}}'


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

    def board_id(self) -> int:
        """The `kestrelrobotics` board, which `find_boards` made; seeded here only when it is not there yet."""
        row = self.conn.execute("SELECT board_id FROM board WHERE ats = 'greenhouse' AND token = ?",
                                (TOKEN,)).fetchone()
        if row is not None:
            return row[0]
        company = self.conn.execute("SELECT company_id FROM company WHERE name = ?", (COMPANY,)).fetchone()
        company_id = company[0] if company is not None else seed_company(self.conn, COMPANY)
        return seed_board(self.conn, company_id, "greenhouse", TOKEN)

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
        now = add_seconds(FIRST_TICK, DAY_S * self.ticks)
        self.ticks += 1
        clock = _Clock()
        self.client = FakeClient({list_url(): self.serve if answer is None else answer})
        self.report = run_tick(self.conn, self.client, cfg=load_crawl_config(), now_fn=lambda: now, clock=clock,
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
