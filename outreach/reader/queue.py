"""The reader's work list: who is eligible, in what order they are read, and the leases that hand them out (#32).

`read_queue` holds one row per posting, body hash and reader version. A body that changes has a new hash and so a new
row; the old row stays as history. Only this module writes the table (the submit step moves rows to `done` or
`refused` through the same key).

A lease is a claim for `lease_minutes`. The night job and a side session can both ask for work: `next_batch` takes
the write lock (BEGIN IMMEDIATE) before it reads, so two callers never get the same live lease. A lease that is not
finished by its deadline is handed out again with `attempts` up; a posting that has used `max_attempts` leases ends
`refused` and is shown, flagged, instead of being tried every night.

A stale posting (old, or gone from its board's latest read; `outreach.crawl.stale.load_stale`) is never read: it is
not queued, and a queued row that has gone stale is skipped by `next_batch` and left exactly as it is, so it is
read again if the posting is listed again. `today` is the day staleness is judged on (default: today in UTC).

Order of reading (design section 7.3): provisional hides first, then lane 1 postings at the top-scored companies,
then everything else, newest first. A company whose newest score carries a final hide is held: nothing of it is
shown, so reading it can wait, and it takes none of the top slots.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from outreach.crawl.stale import load_stale

from outreach.ranking.common import canonical_ids
from outreach.ranking.hide import posting_visible
from outreach.ranking.scores import latest_version
from outreach.reader.config import ReaderConfig, load_reader_config
from outreach.reader.types import ReadRequest
from outreach.reader.version import reader_version
from outreach.rules.rebuild import stored_decision
from outreach.store import atomic, body_sha256, iso_utc, transaction, utcnow
from outreach.store.timefmt import ISO_FMT

__all__ = ["enqueue", "enqueue_missing", "refresh_priorities", "next_batch", "release", "requeue", "set_aside",
           "batch_payload", "TIER_ORDINARY", "TIER_LANE_ONE", "TIER_PROVISIONAL_HIDE", "HELD"]

BATCH = 500  # rows per write transaction, as `rebuild` does
TIER_ORDINARY, TIER_LANE_ONE, TIER_PROVISIONAL_HIDE = 1, 2, 3
_TIER = 1_000_000
_MAX_POINTS = 999_999
# Priority of a row that is not handed out: its company has a final hide. It stays queued and comes back by itself
# when the hide is lifted.
HELD = 0

# Open postings whose current decision (the current ruleset's row) puts them in the engineering or intern lane and
# whose stored body is long enough. Same rows as `current_decision`, joined by key so a bulk read is not a scan.
_ELIGIBLE = (
    "FROM posting p JOIN posting_body b ON b.posting_id = p.posting_id "
    "JOIN decision d ON d.posting_id = p.posting_id "
    "AND d.ruleset_version = (SELECT ruleset_version FROM ruleset WHERE is_current = 1) "
    "WHERE p.closed_at IS NULL AND d.lane IN ('engineering', 'intern') AND length(b.body) >= ?")

_WHITESPACE = re.compile(r"\s+")  # the pattern `body_sha256` collapses

_INSERT = ("INSERT INTO read_queue(posting_id, body_sha256, reader_version, priority, state, enqueued_at) "
           "VALUES (?, ?, ?, ?, 'queued', ?) ON CONFLICT DO NOTHING")


def _long_enough(body: str, cfg: ReaderConfig) -> bool:
    """The floor counts the text `body_sha256` hashes (whitespace collapsed, ends stripped), not the raw body."""
    return len(_WHITESPACE.sub(" ", body).strip()) >= cfg.min_body_chars


def _today(today: date | None) -> date:
    return today if today is not None else datetime.now(timezone.utc).date()


def _defaults(cfg: ReaderConfig | None, version: str | None) -> tuple[ReaderConfig, str]:
    cfg = cfg if cfg is not None else load_reader_config()
    return cfg, version if version is not None else reader_version(cfg)


def _insert(conn: sqlite3.Connection, keys: list[tuple[int, str]], version: str) -> int:
    """Rows added. The priority is the neutral one: `next_batch` sets the real order before it leases anything."""
    added = 0
    for start in range(0, len(keys), BATCH):
        with atomic(conn, "reader_enqueue"):
            now = utcnow()
            for posting_id, digest in keys[start:start + BATCH]:
                added += conn.execute(_INSERT, (posting_id, digest, version, TIER_ORDINARY * _TIER, now)).rowcount
    return added


def enqueue(conn: sqlite3.Connection, posting_id: int, *, cfg: ReaderConfig | None = None,
            version: str | None = None, today: date | None = None) -> bool:
    """Queue one posting's current body for `version`. False, and nothing written, when the posting has no body,
    a body shorter than `min_body_chars` once whitespace is collapsed, is closed or stale, has no decision or is not
    in the engineering or intern lane, or when this body is already queued (or done, or refused) under `version`."""
    cfg, version = _defaults(cfg, version)
    row = conn.execute("SELECT b.body " + _ELIGIBLE + " AND p.posting_id = ?",
                       (cfg.min_body_chars, posting_id)).fetchone()
    if row is None or not _long_enough(row["body"], cfg) or load_stale(conn, _today(today), posting_ids=[posting_id]):
        return False
    return _insert(conn, [(posting_id, body_sha256(row["body"]))], version) == 1


def enqueue_missing(conn: sqlite3.Connection, *, cfg: ReaderConfig | None = None, version: str | None = None,
                    limit: int | None = None, today: date | None = None) -> int:
    """Queue every eligible, fresh open posting that has no row for its current body under `version`: the backlog, a
    night that was skipped, a new reader version or a body that changed. Newest postings first when `limit` cuts it
    short. Returns how many rows were added."""
    cfg, version = _defaults(cfg, version)
    stale = load_stale(conn, _today(today))
    have = {(r[0], r[1]) for r in conn.execute(
        "SELECT posting_id, body_sha256 FROM read_queue WHERE reader_version = ?", (version,))}
    missing: list[tuple[int, str]] = []
    for row in conn.execute("SELECT p.posting_id, b.body " + _ELIGIBLE + " ORDER BY p.posting_id DESC",
                            (cfg.min_body_chars,)):
        if limit is not None and len(missing) >= limit:
            break
        if row["posting_id"] in stale or not _long_enough(row["body"], cfg):
            continue
        key = (row["posting_id"], body_sha256(row["body"]))
        if key not in have:
            missing.append(key)
    return _insert(conn, missing, version)


@dataclass(frozen=True)
class _Scores:
    """What the newest company scores say about the queue."""
    scored: bool                  # company_score has rows at all
    held: frozenset[int]          # companies whose newest score has a final hide
    points: dict[int, int]        # the top companies that can be seen, by queue points


def _load_scores(conn: sqlite3.Connection, cfg: ReaderConfig) -> _Scores:
    version = latest_version(conn)
    if version is None:
        return _Scores(False, frozenset(), {})
    held: set[int] = set()
    points: dict[int, int] = {}
    # A final hide is a `hide` object whose `provisional` is false; a provisional one (a refusal that rests on a
    # provisional decision) still shows the company, so it is not held (ranking's open question 6).
    for row in conn.execute(
            "SELECT company_id, score, coalesce(json_extract(signals, '$.hide') IS NOT NULL "
            "AND json_extract(signals, '$.hide.provisional') = 0, 0) AS final_hide FROM company_score "
            "WHERE ranker_version = ? ORDER BY score DESC, company_id", (version,)):
        if row["final_hide"]:
            held.add(row["company_id"])
        elif len(points) < cfg.top_companies:
            points[row["company_id"]] = min(max(int(row["score"] * 1000), 0), _MAX_POINTS)
    return _Scores(True, frozenset(held), points)


def _priority(row: sqlite3.Row, company_id: int, scores: _Scores) -> int:
    if company_id in scores.held:
        return HELD
    if row["lane"] is not None:
        decision = stored_decision(row)
        if row["provisional"] == 1 and not posting_visible(decision):
            return TIER_PROVISIONAL_HIDE * _TIER
        if decision.lane == "engineering" and decision.level_ok:
            if not scores.scored:
                return TIER_LANE_ONE * _TIER
            if company_id in scores.points:
                return TIER_LANE_ONE * _TIER + scores.points[company_id]
    return TIER_ORDINARY * _TIER


def _canon(conn: sqlite3.Connection) -> dict[int, int]:
    try:
        return canonical_ids(conn)
    except ValueError:  # a merge loop is a store error for ranking to report; here it only means "no survivor"
        return {}


def refresh_priorities(conn: sqlite3.Connection, *, cfg: ReaderConfig | None = None) -> int:
    """Recompute `priority` of every queued row from the current decisions and scores; returns how many changed.

    `priority = tier * 1_000_000 + points`: tier 3 is a provisional hide (the rules hid it and no reader has agreed
    yet; an intern posting that shows is not one), tier 2 a lane 1 posting (engineering, level fits) at one of the
    top `top_companies` companies by the newest `company_score` (points are the score times 1000), or any lane 1
    posting while there are no scores, tier 1 the rest. A company with a final hide is held at priority 0. Leased,
    done and refused rows are not touched. Reads first, then writes in short batches, so no write lock is held while
    the rules run.
    """
    cfg = cfg if cfg is not None else load_reader_config()
    scores = _load_scores(conn, cfg)
    canon = _canon(conn)
    changes: list[tuple[int, int]] = []
    for row in conn.execute(
            "SELECT q.rowid AS qrowid, q.priority AS queued_priority, p.company_id, d.* FROM read_queue q "
            "JOIN posting p ON p.posting_id = q.posting_id LEFT JOIN decision d ON d.posting_id = q.posting_id "
            "AND d.ruleset_version = (SELECT ruleset_version FROM ruleset WHERE is_current = 1) "
            "WHERE q.state = 'queued'"):
        company = canon.get(row["company_id"], row["company_id"])
        priority = _priority(row, company, scores)
        if priority != row["queued_priority"]:
            changes.append((priority, row["qrowid"]))
    changed = 0
    for start in range(0, len(changes), BATCH):
        with atomic(conn, "reader_priorities"):
            for priority, rowid in changes[start:start + BATCH]:
                changed += conn.execute("UPDATE read_queue SET priority = ? WHERE rowid = ? AND state = 'queued'",
                                        (priority, rowid)).rowcount
    return changed


def next_batch(conn: sqlite3.Connection, limit: int, *, cfg: ReaderConfig | None = None, version: str | None = None,
               now: str | None = None, today: date | None = None,
               skip: Iterable[int] = ()) -> list[ReadRequest]:
    """Lease up to `limit` postings for `version`, best priority first (newest first within a priority).

    A row is up for lease when it is queued, or leased with a deadline before `now`. Each one handed out gets
    `lease_minutes` from `now` and one more attempt. Rows that are skipped and left exactly as they are, whatever
    their state: a stale posting (`today`, default today in UTC), a company with a final hide (held) and a posting
    id in `skip` (the night job's postings that failed tonight). That covers an expired lease too, which `refresh_priorities` does not touch. A row that has used `max_attempts` leases is
    set refused ("gave up after N leases") and takes no slot. A row whose body is gone or no longer matches its
    hash (the body changed after it was queued: `enqueue` makes a new key for the new body) is refused too, so a
    reading is never made of text the hash does not name.
    """
    cfg, version = _defaults(cfg, version)
    if limit < 1:
        return []
    refresh_priorities(conn, cfg=cfg)
    now = iso_utc(now) if now else utcnow()
    today = _today(today)
    until = (datetime.strptime(now, ISO_FMT) + timedelta(minutes=cfg.lease_minutes)).strftime(ISO_FMT)
    held = _load_scores(conn, cfg).held
    canon = _canon(conn)
    out: list[ReadRequest] = []
    seen: set[int] = set()  # rows looked at and skipped, so the loop never fetches one again
    skipped = json.dumps(sorted({int(pid) for pid in skip}))
    with transaction(conn):  # BEGIN IMMEDIATE: the lock is taken before the rows are read
        while len(out) < limit:
            rows = conn.execute(
                "SELECT q.rowid AS qrowid, q.posting_id, q.body_sha256, q.reader_version, q.attempts, p.title, "
                "p.company_id, b.body FROM read_queue q JOIN posting p ON p.posting_id = q.posting_id "
                "LEFT JOIN posting_body b ON b.posting_id = q.posting_id "
                "WHERE q.priority > ? AND q.reader_version = ? "
                "AND (q.state = 'queued' OR (q.state = 'leased' AND q.lease_until < ?)) "
                "AND q.rowid NOT IN (SELECT value FROM json_each(?)) "
                "AND q.posting_id NOT IN (SELECT value FROM json_each(?)) "
                "ORDER BY q.priority DESC, q.posting_id DESC LIMIT ?",
                (HELD, version, now, json.dumps(sorted(seen)), skipped, limit - len(out))).fetchall()
            if not rows:
                break
            stale = load_stale(conn, today, posting_ids=[r["posting_id"] for r in rows])
            for row in rows:
                seen.add(row["qrowid"])
                if row["posting_id"] in stale or canon.get(row["company_id"], row["company_id"]) in held:
                    continue
                if row["attempts"] >= cfg.max_attempts:
                    why = f"gave up after {row['attempts']} leases"
                elif row["body"] is None:
                    why = "no stored body"
                elif body_sha256(row["body"]) != row["body_sha256"]:
                    why = "body changed"
                else:
                    conn.execute("UPDATE read_queue SET state = 'leased', lease_until = ?, attempts = attempts + 1 "
                                 "WHERE rowid = ?", (until, row["qrowid"]))
                    out.append(ReadRequest(row["posting_id"], row["body_sha256"], row["reader_version"], row["title"],
                                           row["body"]))
                    continue
                conn.execute("UPDATE read_queue SET state = 'refused', lease_until = NULL, last_error = ? "
                             "WHERE rowid = ?", (why, row["qrowid"]))
    return out


def release(conn: sqlite3.Connection, requests: Iterable[ReadRequest]) -> None:
    """Put leased rows back to queued and give back the attempt, for a stop that was not the posting's fault (a
    session limit, an auth error, a crashed call). A row that is no longer leased (it finished, or was refused) is
    left alone, so releasing twice gives back one attempt."""
    keys = [(r.posting_id, r.body_sha256, r.reader_version) for r in requests]
    if not keys:
        return
    with atomic(conn, "reader_release"):
        for key in keys:
            conn.execute(
                "UPDATE read_queue SET state = 'queued', lease_until = NULL, attempts = max(attempts - 1, 0) "
                "WHERE posting_id = ? AND body_sha256 = ? AND reader_version = ? AND state = 'leased'", key)


def requeue(conn: sqlite3.Connection, requests: Iterable[ReadRequest], error: str, *,
            attempts: Mapping[int, int] | None = None) -> int:
    """Put leased rows back to queued and keep the attempt their lease used, for a stop that is the posting's: the
    model's reply had no reading for it, or a call with only this posting in it failed. A posting that keeps failing
    this way runs out of leases and `next_batch` ends it refused, so it is shown, flagged, instead of being tried
    every night. Only a row that is still leased is touched, and, when `attempts` names its posting, only on that
    attempt (a lease handed on to another caller is theirs). Returns how many rows went back."""
    back = 0
    with atomic(conn, "reader_requeue"):
        for r in requests:
            sql = ("UPDATE read_queue SET state = 'queued', lease_until = NULL, last_error = ? WHERE posting_id = ? "
                   "AND body_sha256 = ? AND reader_version = ? AND state = 'leased'")
            args: list = [error, r.posting_id, r.body_sha256, r.reader_version]
            if attempts is not None and r.posting_id in attempts:
                sql += " AND attempts = ?"
                args.append(attempts[r.posting_id])
            back += conn.execute(sql, args).rowcount
    return back


def set_aside(conn: sqlite3.Connection, requests: Iterable[ReadRequest], error: str) -> int:
    """End leased rows `refused` with `error`, keeping the attempt their lease used, for a posting this caller can
    never read (#13: one too long for a side session's result). The row stays, so `enqueue` never queues the same
    body for the same version again and `next_batch` never hands it out again. Only a row that is still leased is
    touched. Returns how many rows ended."""
    ended = 0
    with atomic(conn, "reader_set_aside"):
        for r in requests:
            ended += conn.execute(
                "UPDATE read_queue SET state = 'refused', lease_until = NULL, last_error = ? WHERE posting_id = ? "
                "AND body_sha256 = ? AND reader_version = ? AND state = 'leased'",
                (error, r.posting_id, r.body_sha256, r.reader_version)).rowcount
    return ended


def batch_payload(conn: sqlite3.Connection, requests: list[ReadRequest]) -> dict:
    """What goes to the model for one batch: plain data, one entry per request in the same order. `location` is the
    posting's structured location; the reader may quote it. A batch is one reader version (ValueError otherwise);
    an empty one has version None."""
    versions = {r.reader_version for r in requests}
    if len(versions) > 1:
        raise ValueError(f"a batch is one reader version, got {', '.join(sorted(versions))}")
    marks = ", ".join("?" for _ in requests)
    locations = {r["posting_id"]: r["location"] for r in conn.execute(
        f"SELECT posting_id, location FROM posting WHERE posting_id IN ({marks})", [r.posting_id for r in requests])}
    return {
        "reader_version": next(iter(versions), None),
        "postings": [{"posting_id": r.posting_id, "body_sha256": r.body_sha256, "reader_version": r.reader_version,
                      "title": r.title, "location": locations.get(r.posting_id, ""), "body": r.body}
                     for r in requests],
    }
