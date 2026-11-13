"""Boards the SimplifyJobs new grad feed names (#239): the feed's listings point at company boards on ATSs the
crawler reads, and Knock had no row for many of them (180 of the 312 listings it missed on 2026-11-12).

`run_feed_seed` runs in the crawl tick, right after the feed poll (#153), under the tick's lock and through its polite
client. It takes the active listings with an ATS and a board token, one row per board, keeps a `board_candidate` row
for each (source `simplify`; a board the stapply lists already named keeps its row and its verdict), and gates at
most `limit` of them with the ATS seed's one-read gate (`ats_seed.gate`, #192): a board with an open US engineering
role joins its company, or a new one, credited to the `simplify` source, and the next tick reads it in full (its old
jobs are never shown as new). A rejected or dead board waits `retry_days`; a failed read is tried on the next tick.

A board the store already has, by token in any letter case (a Workday or Oracle link may spell the site another
way), or by an `ats` alias, sends nothing. Only the ATSs the caller passes are taken (the tick passes the ones it
reads, so a switched-off reader such as SmartRecruiters never gets a request).
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable, Collection
from datetime import datetime, timedelta

from outreach.crawl.base import plain_icims
from outreach.net import HttpClient
from outreach.sources.ats_seed import SeedConfig, SeedReport, _gate_rows, _Row, load_seed_config
from outreach.sources.boards import SUPPORTED_ATS, BoardRef
from outreach.store import transaction

__all__ = ["EVENT_KIND", "SOURCE", "SOURCE_VERSION", "feed_rows", "run_feed_seed"]

SOURCE = "simplify"
SOURCE_VERSION = "simplify-feed-1"
EVENT_KIND = "feed_seed_run"
_TIME = "%Y-%m-%dT%H:%M:%SZ"


def _listed(conn: sqlite3.Connection, ats: Collection[str]) -> list[tuple[BoardRef, str, str]]:
    """(board, company name, link) per board the active listings name, newest first sight first, each board once."""
    wanted = sorted(set(ats) & SUPPORTED_ATS)
    if not wanted:
        return []
    rows = conn.execute(
        "SELECT ats, token, company_name, url FROM feed_listing WHERE source = ? AND active = 1 AND token IS NOT NULL "
        f"AND token <> '' AND ats IN ({', '.join('?' for _ in wanted)}) ORDER BY first_seen_at DESC, feed_id DESC",
        (SOURCE, *wanted))
    seen: set[tuple[str, str]] = set()
    out = []
    for r in rows:
        try:
            ref = BoardRef(r["ats"], r["token"])
        except ValueError:  # a link whose token is not a board token
            continue
        if (ref.ats, ref.token.lower()) in seen or plain_icims(ref.ats, ref.token):  # a plain iCIMS site: no reader
            continue
        seen.add((ref.ats, ref.token.lower()))
        out.append((ref, r["company_name"].strip(), r["url"]))
    return out


def feed_rows(conn: sqlite3.Connection, ats: Collection[str], now: str) -> list[_Row]:
    """Every board the feed names as a candidate row, newest listing first. The rows not yet in `board_candidate`
    are added first (one transaction), so each has its id."""
    listed = _listed(conn, ats)
    with transaction(conn):
        conn.executemany(
            "INSERT INTO board_candidate(ats, token, name, url, source, source_ref, found_at) "
            "VALUES (?, ?, ?, ?, ?, 'feed', ?) ON CONFLICT(ats, token) DO NOTHING",
            [(ref.ats, ref.token, name, url, SOURCE, now) for ref, name, url in listed])
    stored = {(r["ats"], r["token"]): r for r in conn.execute(
        "SELECT candidate_id, ats, token, gated_at, gate_result FROM board_candidate")}
    out = []
    for ref, name, url in listed:
        row = stored.get((ref.ats, ref.token))
        out.append(_Row(row["candidate_id"], ref.ats, ref.token, name, url, None, row["gated_at"], row["gate_result"]))
    return out


def _age_days(now: str, then: str) -> float:
    return (datetime.strptime(now, _TIME) - datetime.strptime(then, _TIME)) / timedelta(days=1)


def _known(conn: sqlite3.Connection) -> tuple[dict[tuple[str, str], int], set[str]]:
    """Stored boards by (ats, lower-cased token) and the `ats` aliases, lower-cased."""
    boards = {(r["ats"], r["token"].lower()): r["board_id"] for r in conn.execute("SELECT board_id, ats, token FROM board")}
    aliases = {k.lower() for (k,) in conn.execute("SELECT alias_key FROM company_alias WHERE source = 'ats'")}
    return boards, aliases


def run_feed_seed(conn: sqlite3.Connection, client: HttpClient, *, now: str, ats: Collection[str], limit: int | None = None,
                  should_stop: Callable[[], bool] = lambda: False, config: SeedConfig | None = None
                  ) -> SeedReport | None:
    """Gate at most `limit` (default `feed_per_tick` in `ats_seed.toml`) of the boards the feed names that the store
    lacks. None when nothing is due (or the limit is 0): then nothing is sent and no run event is written. Otherwise
    one `source` / `feed_seed_run` event holds the report. The caller holds the crawl lock and hands in its polite
    client, used as it is; `should_stop` is asked before each board is read (the tick's budget)."""
    if conn.in_transaction:
        raise ValueError("the feed seed sends requests, so it must start with no transaction open")
    config = config or load_seed_config()
    limit = config.feed_per_tick if limit is None else limit
    if limit < 1:
        return None
    rows = feed_rows(conn, ats, now)
    boards, aliases = _known(conn)
    known: list[tuple[_Row, int | None]] = []
    due: list[_Row] = []
    for row in rows:
        if (row.ats, row.token.lower()) in boards or f"{row.ats}:{row.token}".lower() in aliases:
            known.append((row, boards.get((row.ats, row.token.lower()))))
        elif (row.gated_at is None or row.gate_result in ("error", "no_us_engineering")
              or _age_days(now, row.gated_at) >= config.retry_days):  # a board turned away for no US engineering role
            # is read again for the feed, which adds it whenever it answers (2026-11-13)
            due.append(row)
    if not due:
        return None
    due.sort(key=lambda r: (r.gated_at is not None, r.gated_at or ""))  # stable: newest listing first within each
    return _gate_rows(conn, client, rows, known, due, now=now, ats=tuple(sorted({r.ats for r in due})), limit=limit,
                      workers=None, source=(SOURCE, SOURCE_VERSION), event_kind=EVENT_KIND, should_stop=should_stop,
                      as_is=True, any_answer=True)
