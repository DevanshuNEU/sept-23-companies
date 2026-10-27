"""Idempotent writes: one key, one write, one stored response, however many times the page sends it.

Every write runs through `run_write`. It needs the `Idempotency-Key` header (the page's `requestId`), takes the write
lock with `BEGIN IMMEDIATE`, and in that one transaction either replays the stored answer for a key it has seen, or
runs the write and records the key with its answer. So a double click, a retry after a network error and the same
key from two tabs give one row, one event and one replayed response. Only a 2xx answer is stored: a refusal
(conflict, blocked), a server error or a busy store rolls everything back and stores nothing, so the retry after a
refresh runs for real.
"""
import hashlib
import json
import logging
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from anyio import from_thread
from fastapi import Request, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from outreach.api.clock import get_clock
from outreach.api.db import busy_problem, connect_for, is_busy, open_conn
from outreach.api.errors import ApiProblem
from outreach.api.settings import Settings
from outreach.store import transaction
from outreach.store.timefmt import ISO_FMT, utcnow

log = logging.getLogger("outreach.api")

KEY_HEADER = "Idempotency-Key"
REPLAY_HEADER = "Idempotent-Replay"
_KEY = re.compile(r"[A-Za-z0-9._-]{8,80}\Z")  # a UUID fits; ':' is kept for the event keys built from it
KEEP_DAYS = 7


@dataclass(frozen=True)
class WriteCtx:
    """What a write gets: its request id, the one `now` for every row it writes, and the open transaction."""

    request_id: str
    now: str
    conn: sqlite3.Connection

    def event_key(self, n: int) -> str:
        """`event.idempotency_key` for the write's n-th event: a second guard against a repeated write."""
        return f"{self.request_id}:{n}"


def canonical_body(body: bytes) -> str:
    """The body as JSON with sorted keys and no spaces, so key order and spacing never change the fingerprint."""
    if not body.strip():
        return ""
    try:
        value = json.loads(body)
    except ValueError:
        raise ApiProblem("bad_request", "The body must be JSON.") from None
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(method: str, path: str, body: bytes) -> str:
    """sha256 of method, path and canonical body: what a key was first used for."""
    text = f"{method.upper()} {path}\n{canonical_body(body)}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _dump(result: BaseModel | dict | None) -> str:
    if isinstance(result, BaseModel):
        payload = result.model_dump(by_alias=True, mode="json")
    else:
        payload = jsonable_encoder(result, by_alias=True)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _respond(text: str, status: int, *, replay: bool) -> Response:
    headers = {REPLAY_HEADER: "true"} if replay else None
    return Response(content=text, status_code=status, media_type="application/json", headers=headers)


def _key(request: Request) -> str:
    key = request.headers.get(KEY_HEADER)
    if key is None or not _KEY.match(key):
        raise ApiProblem(
            "bad_request",
            f"A write needs an {KEY_HEADER} header of 8 to 80 letters, digits, dots, dashes or underscores.",
        )
    return key


def run_write(
    request: Request,
    op: Callable[[WriteCtx], BaseModel | dict | None],
    *,
    status: int = 200,
) -> Response:
    """Run `op` once for this request's key, inside one `BEGIN IMMEDIATE` transaction, and answer with its result.

    Call it from a sync endpoint (it reads the raw body through the event loop). `op` gets a `WriteCtx` and returns
    the response model, a dict or None. The same key with the same method, path and body replays the stored answer
    with `Idempotent-Replay: true`; with anything else it is `idempotency_mismatch`. An `ApiProblem` or any other
    exception from `op` rolls back and stores nothing. A locked store is `busy` with `Retry-After: 1`.
    """
    if not 200 <= status <= 299:
        raise ValueError(f"run_write stores only a 2xx answer, got {status}")
    key = _key(request)
    body = from_thread.run(request.body)
    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    digest = fingerprint(request.method, target, body)
    now = get_clock(request)()
    conn = None
    try:
        conn = open_conn(request)
        with transaction(conn):
            row = conn.execute(
                "SELECT fingerprint, status, response FROM api_request WHERE request_id = ?", (key,)
            ).fetchone()
            if row is not None:
                if row["fingerprint"] != digest:
                    raise ApiProblem(
                        "idempotency_mismatch",
                        f"This {KEY_HEADER} was already used for a different request. Nothing was saved.",
                    )
                return _respond(row["response"], row["status"], replay=True)
            text = _dump(op(WriteCtx(request_id=key, now=now, conn=conn)))
            conn.execute(
                "INSERT INTO api_request(request_id, method, path, fingerprint, status, response, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (key, request.method.upper(), request.url.path, digest, status, text, now),
            )
        return _respond(text, status, replay=False)
    except sqlite3.OperationalError as err:
        if is_busy(err):
            raise busy_problem() from err
        raise
    finally:
        if conn is not None:
            conn.close()  # closing also rolls back anything a failed COMMIT left open
        request.app.state.view_cache.version.bump()  # the heavy views compute afresh on the next read


def prune_requests(conn: sqlite3.Connection, days: int = KEEP_DAYS, now: str | None = None) -> int:
    """Delete stored requests older than `days` (a key is retried within minutes, not weeks). Returns how many."""
    moment = datetime.strptime(now or utcnow(), ISO_FMT).replace(tzinfo=timezone.utc)
    cutoff = (moment - timedelta(days=days)).strftime(ISO_FMT)
    return conn.execute("DELETE FROM api_request WHERE created_at < ?", (cutoff,)).rowcount


def prune_at_startup(settings: Settings, now: str) -> None:
    """Prune once when the server starts. A busy store skips it (the next start prunes) and never stops the API."""
    try:
        conn = connect_for(settings)
        try:
            dropped = prune_requests(conn, now=now)
        finally:
            conn.close()
    except sqlite3.OperationalError as err:
        if not is_busy(err):
            raise
        log.warning("The store was busy at startup, so old idempotency keys were not pruned this time.")
        return
    if dropped:
        log.info("Pruned %d idempotency keys older than %d days.", dropped, KEEP_DAYS)

