"""Submit: check a reading against the posting text and store it only when every fact is quoted (#32).

A reading is a model's answer about one posting body. Nothing in it is taken on trust: the shape goes through the
foundation's `PostingReading.from_json`, and every field that states a fact must quote words that are really in the
posting text (`quote_in_body` on the title, the structured location or the body, one part at a time, so a quote
cannot be stitched across two of them). A free-text value (location, pay, start date) must itself be in the text, or
be the quote. A field that fails is refused. While the posting has leases left, a refused reading is not stored at all and the posting goes back to
the queue for another read; at the limit the reading is stored as partial, each refused field blanked to "unknown",
so a quote that does not check out never becomes a fact. Submit writes no decision: combining the reading with the
rules is `combine.py`'s job.

The queue has no owner column, so a submit is keyed on the reading's (posting_id, body_sha256, reader_version) and
the row's state, not on who holds the lease. A row that is leased (live or run out) or queued takes the reading; a
row that is done or refused answers `duplicate` and changes nothing. Two readers of the same key read the same
text with the same version, so the first good reading stored is the fact and a late reader costs a second model
call, never a second fact. Submit never changes `attempts`: an attempt is counted when `next_batch` hands a lease
out, and a refusal only puts the row back to `queued`. A caller that knows which attempt it leased (the night job)
passes it as `lease_attempt`; when the row has moved on to a later attempt, the lease was handed to someone else, so
a bad reading from it changes nothing (no requeue, no partial) and answers `stale`. A good reading is still taken.

Each reading is its own transaction, so a bad one never undoes its neighbours, and an error inside one (even one
that is not a `ValueError`) is rolled back and answered, never raised.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from outreach.reader.config import ReaderConfig, load_reader_config
from outreach.reader.queue import enqueue
from outreach.reader.types import (FIELDS, INT_FIELDS, TEXT_FIELDS, FieldReading, PostingReading, _collapse,
                                   quote_in_body)
from outreach.reader.version import reader_version
from outreach.store import append_event, atomic, body_sha256, describe_error, iso_utc, utcnow

__all__ = ["SubmitResult", "submit", "blank_field", "min_words"]

Status = Literal["stored", "partial", "refused", "requeued", "stale", "duplicate", "unknown"]

# Values that state no fact, so they need no quote.
_NO_FACT = ("unknown", "silent", None, "")
# Fields whose natural quote is short and exact ("Austin, TX", "Remote", a pay range) take a one-word quote; the
# fields that hide or rank keep the three-word floor (ruling 10).
_SHORT_QUOTE_FIELDS = ("location", "pay", "start_date", "remote")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_MAX_ID = 2 ** 63 - 1


@dataclass(frozen=True)
class SubmitResult:
    posting_id: int | None
    body_sha256: str
    status: Status
    refused_fields: tuple[str, ...] = ()
    error: str = ""


def blank_field(name: str) -> FieldReading:
    """What a refused field is stored as: `unknown`, or None for a number of years and '' for free text."""
    if name in INT_FIELDS:
        return FieldReading(None, "")
    if name in TEXT_FIELDS:
        return FieldReading("", "")
    if name not in FIELDS:
        raise ValueError(f"unknown field {name!r}")
    return FieldReading("unknown", "")


def min_words(name: str) -> int:
    """The quote floor for field `name`."""
    return 1 if name in _SHORT_QUOTE_FIELDS else 3


def submit(conn: sqlite3.Connection, readings: list[dict] | dict, *, cfg: ReaderConfig | None = None,
           version: str | None = None, now: str | None = None,
           lease_attempt: int | Mapping[int, int] | None = None) -> list[SubmitResult]:
    """Check and store readings; one result per reading, in order. Never raises on a bad reading.

    `version` is the reader version a reading without one is filed under (the model does not copy it back; the
    queue key does); it defaults to the version computed from the files. `now` is when the reading arrived.
    `lease_attempt` is the attempt the caller leased: one number for every reading, or a map from posting id to
    attempt (rows in one batch can be on different attempts). A posting it does not name is not checked.
    """
    cfg = cfg if cfg is not None else load_reader_config()
    version = version if version is not None else reader_version(cfg)
    now = iso_utc(now) if now else utcnow()
    if isinstance(readings, dict) or not isinstance(readings, (list, tuple)):
        readings = [readings]
    return [_submit_one(conn, obj, cfg, version, now, lease_attempt) for obj in readings]


class _Undo(Exception):
    """Roll back one reading's writes and answer `result` instead."""

    def __init__(self, result: SubmitResult):
        super().__init__(result.status)
        self.result = result


def _safe(text: str) -> str:
    """Error text that binds to SQLite and prints anywhere: non-ASCII, lone surrogates included, as escapes."""
    return text.encode("ascii", "backslashreplace").decode("ascii")


def _encodes(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _key(obj: Any, version: str) -> tuple[int | None, str, str, str]:
    """(posting_id, body_sha256, reader_version, error) as far as they can be read; error is '' when all three
    are usable to look up a queue row."""
    if not isinstance(obj, dict):
        return None, "", version, "a reading must be an object"
    pid = obj.get("posting_id")
    if not isinstance(pid, int) or isinstance(pid, bool) or not 0 < pid <= _MAX_ID:
        return None, "", version, "posting_id must be a positive whole number"
    sha = obj.get("body_sha256")
    if not isinstance(sha, str) or not _SHA.match(sha):
        return pid, "", version, "body_sha256 must be 64 lower-case hex characters"
    rv = obj.get("reader_version", version)
    if not isinstance(rv, str) or not rv or not _encodes(rv):
        return pid, sha, version, "reader_version must be a non-empty UTF-8 string"
    return pid, sha, rv, ""


def _submit_one(conn: sqlite3.Connection, obj: Any, cfg: ReaderConfig, version: str, now: str,
                lease_attempt: int | Mapping[int, int] | None) -> SubmitResult:
    pid, sha, rv, error = _key(obj, version)
    if error:
        return SubmitResult(pid, sha, "unknown", error=error)
    attempt = lease_attempt.get(pid) if isinstance(lease_attempt, Mapping) else lease_attempt
    try:
        with atomic(conn, "reader_submit"):
            return _checked(conn, obj, pid, sha, rv, cfg, now, attempt)
    except _Undo as undo:
        return undo.result
    except Exception as err:  # noqa: BLE001 - one reading's failure is its answer, never the batch's end
        return SubmitResult(pid, sha, "refused", error=_safe(describe_error(err)))


def _checked(conn: sqlite3.Connection, obj: dict, pid: int, sha: str, rv: str, cfg: ReaderConfig,
             now: str, attempt: int | None) -> SubmitResult:
    key = (pid, sha, rv)
    row = conn.execute("SELECT state, attempts FROM read_queue WHERE posting_id = ? AND body_sha256 = ? "
                       "AND reader_version = ?", key).fetchone()
    if row is None:
        return SubmitResult(pid, sha, "unknown", error="no queue row for this posting, body and reader version")
    if row["state"] in ("done", "refused"):
        return SubmitResult(pid, sha, "duplicate")
    at_limit = row["attempts"] >= cfg.max_attempts
    # The caller's lease was handed on: its bad reading must not requeue or end someone else's lease.
    handed_on = attempt is not None and attempt != row["attempts"]

    post = conn.execute("SELECT p.title, p.location, p.company_id, b.body FROM posting p "
                        "LEFT JOIN posting_body b ON b.posting_id = p.posting_id WHERE p.posting_id = ?",
                        (pid,)).fetchone()
    if post is None or post["body"] is None:
        _end(conn, key, "refused", "no stored body")
        return SubmitResult(pid, sha, "stale", error="no stored body")
    if body_sha256(post["body"]) != sha:
        _end(conn, key, "done", "body changed")
        enqueue(conn, pid, cfg=cfg, version=rv)
        return SubmitResult(pid, sha, "stale", error="body changed")

    try:
        reading = PostingReading.from_json({**obj, "reader_version": rv})
    except ValueError as err:
        message = _safe(f"malformed: {err}")
        if handed_on:
            return SubmitResult(pid, sha, "stale", error=_handed_on(attempt, row["attempts"], message))
        if at_limit:
            _end(conn, key, "refused", message)
            return SubmitResult(pid, sha, "refused", error=message)
        _requeue(conn, key, message)
        return SubmitResult(pid, sha, "requeued", error=message)

    parts = (post["title"], post["location"], post["body"])
    fields: dict[str, FieldReading] = {}
    refused: list[str] = []
    for name in FIELDS:
        field = reading.fields[name]
        found = _found(field.quote, parts, min_words(name))
        if found and name in TEXT_FIELDS and field.value:
            found = _found(field.value, parts, 1) or _collapse(field.value) == _collapse(field.quote)
        if field.value in _NO_FACT:
            # The value needs no quote; a quote is kept only when it is really there, so that every stored quote
            # of a verified reading was found.
            fields[name] = field if found else FieldReading(field.value, "")
        elif found:
            fields[name] = field
        else:
            refused.append(name)
            fields[name] = blank_field(name)

    if refused and handed_on:
        message = _handed_on(attempt, row["attempts"], "refused: " + ", ".join(refused))
        return SubmitResult(pid, sha, "stale", tuple(refused), message)
    if refused and not at_limit:
        message = "refused: " + ", ".join(refused)
        _requeue(conn, key, message)
        return SubmitResult(pid, sha, "requeued", tuple(refused), message)

    kept = PostingReading(pid, sha, rv, fields)
    added = conn.execute(
        "INSERT INTO posting_reading(posting_id, body_sha256, reader_version, read_at, fields, verified, "
        "refused_fields) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
        (pid, sha, rv, now, json.dumps(kept.to_json()), 0 if refused else 1, json.dumps(refused))).rowcount
    if not added:
        raise _Undo(SubmitResult(pid, sha, "duplicate"))
    kind = "partial" if refused else "stored"
    append_event(conn, entity="reading", kind=kind, actor="claude", entity_id=pid, company_id=post["company_id"],
                 detail={"posting_id": pid, "body_sha256": sha, "reader_version": rv, "refused_fields": refused},
                 at=now, idempotency_key=f"reading:{pid}:{sha}:{rv}")
    if refused:
        message = "refused: " + ", ".join(refused)
        _end(conn, key, "refused", message)
        return SubmitResult(pid, sha, "partial", tuple(refused), message)
    _end(conn, key, "done", "")
    return SubmitResult(pid, sha, "stored")


def _found(text: str, parts: tuple[str, ...], floor: int) -> bool:
    """`text` is word for word inside one part of the posting: the title, the location or the body."""
    return any(quote_in_body(text, part, min_words=floor) for part in parts)


def _handed_on(attempt: int, current: int, message: str) -> str:
    return f"lease handed on (leased attempt {attempt}, the row is at {current}); nothing changed: {message}"


def _end(conn: sqlite3.Connection, key: tuple[int, str, str], state: str, error: str) -> None:
    conn.execute("UPDATE read_queue SET state = ?, lease_until = NULL, last_error = ? WHERE posting_id = ? "
                 "AND body_sha256 = ? AND reader_version = ? AND state IN ('queued', 'leased')", (state, error, *key))


def _requeue(conn: sqlite3.Connection, key: tuple[int, str, str], error: str) -> None:
    """Back to the queue for another read. The attempt was counted when the lease was handed out."""
    _end(conn, key, "queued", error)
