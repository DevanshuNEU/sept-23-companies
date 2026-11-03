"""Pipeline alarms (#153): four ways the board reading or the scorecard can go quietly wrong.

`check` reads the store and returns the alarms that hold now. It writes nothing and calls no model or network, so the
crawl tick and the shell summary both use it.

- `reader_failing`: an ATS with at least 5 boards read in the last 48 hours, half or more of them now `error`;
- `number_dropped`: the newest scorecard's coverage or level recall is at least 25% below the card 6 to 8 days older
  (skipped when either card lacks the number);
- `source_failing`: `discover`, `resolve` or `find_boards` failed on its last two runs, or the SimplifyJobs feed
  failed its last two polls or has had no good poll for 24 hours (a store that never polled it and holds no feed
  rows is quiet; so is a feed switched off in config);
- `boards_overdue`: 50 or more boards the crawler should read whose `next_read_at` passed more than one interval ago
  (`change_interval_s`, or `floor_s` when unknown).
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from outreach.crawl.ats import reading_ats
from outreach.crawl.config import CrawlConfig, load_crawl_config
from outreach.pipeline.config import load_pipeline_config
from outreach.pipeline.scorecard import EVENT_KIND
from outreach.pipeline.simplify_feed import FAIL_STREAK_KEY, LAST_OK_KEY, SOURCE
from outreach.store import iso_utc
from outreach.store.timefmt import ISO_FMT

__all__ = ["Alarm", "check", "newest_card", "scorecard_line"]

READ_WINDOW_HOURS = 48
READER_MIN_BOARDS = 5
DROP = 0.25
OLDER_DAYS = (6, 8)
SOURCE_JOBS = ("discover", "resolve", "find_boards")
FEED_FAILS = 2
FEED_SILENT_HOURS = 24
OVERDUE_BOARDS = 50
_METRICS = (("simplify_coverage", "Coverage"), ("level_recall", "Level recall"))


@dataclass(frozen=True)
class Alarm:
    kind: str
    text: str


def _moment(stamp: str) -> datetime:
    return datetime.strptime(iso_utc(stamp), ISO_FMT).replace(tzinfo=timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.strftime(ISO_FMT)


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def _reader_failing(conn: sqlite3.Connection, now: str) -> list[Alarm]:
    since = _stamp(_moment(now) - timedelta(hours=READ_WINDOW_HOURS))
    rows = conn.execute(
        "SELECT ats, count(*) AS boards, sum(last_read = 'error') AS failed FROM board WHERE last_read_at >= ? "
        "GROUP BY ats HAVING count(*) >= ? AND 2 * sum(last_read = 'error') >= count(*) ORDER BY ats",
        (since, READER_MIN_BOARDS))
    return [Alarm("reader_failing", f"{ats}: {failed} of {boards} boards read in the last {READ_WINDOW_HOURS} hours "
                                    f"failed their last read") for ats, boards, failed in rows]


def newest_card(conn: sqlite3.Connection) -> tuple[str, dict] | None:
    """The newest stored `pipeline_scorecard` event as (its time, the card's numbers), or None."""
    row = conn.execute("SELECT at, detail FROM event WHERE entity = 'score' AND kind = ? "
                       "ORDER BY at DESC, event_id DESC LIMIT 1", (EVENT_KIND,)).fetchone()
    return (row[0], json.loads(row[1])) if row else None


def scorecard_line(conn: sqlite3.Connection) -> str:
    """The sidebar's words for the newest scorecard."""
    newest = newest_card(conn)
    if newest is None:
        return "Scorecard not run yet"
    card = newest[1]
    return f"Coverage {_percent(card.get('simplify_coverage'))}, level {_percent(card.get('level_recall'))}"


def _number_dropped(conn: sqlite3.Connection) -> list[Alarm]:
    newest = newest_card(conn)
    if newest is None:
        return []
    at, card = newest
    moment = _moment(at)
    low, high = (_stamp(moment - timedelta(days=d)) for d in reversed(OLDER_DAYS))
    row = conn.execute("SELECT detail FROM event WHERE entity = 'score' AND kind = ? AND at >= ? AND at <= ? "
                       "ORDER BY at DESC, event_id DESC LIMIT 1", (EVENT_KIND, low, high)).fetchone()
    if row is None:
        return []
    older = json.loads(row[0])
    found = []
    for key, label in _METRICS:
        before, after = older.get(key), card.get(key)
        if before is None or after is None or before <= 0:
            continue
        if after <= before * (1 - DROP) + 1e-9:
            found.append(Alarm("number_dropped", f"{label} fell from {_percent(before)} to {_percent(after)} "
                                                 "in a week"))
    return found


def _meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM pipeline_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _feed_failing(conn: sqlite3.Connection, now: str) -> Alarm | None:
    if not load_pipeline_config().simplify.enabled:
        return None
    streak = _meta(conn, FAIL_STREAK_KEY)
    if streak is not None and streak.isdigit() and int(streak) >= FEED_FAILS:
        return Alarm("source_failing", f"The SimplifyJobs feed failed its last {streak} polls")
    last_ok = _meta(conn, LAST_OK_KEY)
    if last_ok is None:
        if conn.execute("SELECT 1 FROM feed_listing WHERE source = ? LIMIT 1", (SOURCE,)).fetchone() is None:
            return None  # a store that never polled the feed has nothing to be late
    elif _moment(last_ok) > _moment(now) - timedelta(hours=FEED_SILENT_HOURS):
        return None
    return Alarm("source_failing", f"The SimplifyJobs feed has had no good poll in {FEED_SILENT_HOURS} hours")


def _source_failing(conn: sqlite3.Connection, now: str) -> list[Alarm]:
    found = []
    for job in SOURCE_JOBS:
        last = [r[0] for r in conn.execute(
            "SELECT status FROM job_run WHERE job = ? AND status != 'running' ORDER BY started_at DESC, run_id DESC "
            "LIMIT 2", (job,))]
        if last == ["failed", "failed"]:
            found.append(Alarm("source_failing", f"{job} failed on its last two runs"))
    feed = _feed_failing(conn, now)
    return found + [feed] if feed else found


def _boards_overdue(conn: sqlite3.Connection, now: str, cfg: CrawlConfig) -> list[Alarm]:
    count = conn.execute(
        "SELECT count(*) FROM board b JOIN company c ON c.company_id = b.company_id "
        "WHERE b.active = 1 AND c.status NOT IN ('closed', 'removed') AND c.merged_into IS NULL "
        "AND b.next_read_at IS NOT NULL AND b.ats IN (SELECT value FROM json_each(?)) "
        "AND julianday(b.next_read_at) < julianday(?) - coalesce(b.change_interval_s, ?) / 86400.0",
        (json.dumps(sorted(reading_ats(cfg))), iso_utc(now), cfg.floor_s)).fetchone()[0]
    if count < OVERDUE_BOARDS:
        return []
    return [Alarm("boards_overdue", f"{count} boards are more than one interval overdue to be read")]


def check(conn: sqlite3.Connection, now: str, *, cfg: CrawlConfig | None = None) -> list[Alarm]:
    """The alarms that hold at `now`, in a fixed order. Reads only."""
    cfg = cfg or load_crawl_config()
    return (_reader_failing(conn, now) + _number_dropped(conn) + _source_failing(conn, now)
            + _boards_overdue(conn, now, cfg))
