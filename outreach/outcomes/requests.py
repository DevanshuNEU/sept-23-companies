"""A LinkedIn request Devanshu sent, recorded once: the one write behind every path that logs one.

Three paths log a request: a plan row's request step, the LinkedIn page's `request_sent` step, and the MCP bridge's
`log_sent` (after he says he sent it). Each picks its context and its key, then calls `record_request`, which writes
the two rows a request is: one `linkedin_log` row (`request_sent`, with the note he sent when there is one) and one
`linkedin_request` touch, log row first. So the 7-day windows (`outreach.plan.capacity`), the company's state
(`outreach.plan.contact`) and the outcome prompt all read the same rows whichever path wrote them.

The context helpers are the ones those paths share: `plan_context` for a company on today's plan (source `plan`,
the row's lane), `own_context` for anyone else (source `own`, the newest score's lane). Stdlib only, no clock: the
caller passes `now`.
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Mapping
from datetime import date

from outreach.outcomes.context import build_context
from outreach.outcomes.windows import ny_day, ny_days_between
from outreach.store import log_touch

__all__ = ["newest_score_parts", "own_context", "plan_context", "role_age_days", "record_request"]


def _newest_score(conn: sqlite3.Connection, company_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT ranker_version, signals, lanes FROM company_score WHERE company_id = ? "
        "ORDER BY scored_at DESC, ranker_version DESC LIMIT 1", (company_id,)).fetchone()


def _score_parts(score: sqlite3.Row | None) -> tuple[str, str | None, dict[str, float]]:
    """The lane (the newest score's first lane, else `own`), the ranker version and the signal values."""
    if score is None:
        return "own", None, {}
    try:
        lanes = json.loads(score["lanes"])
        signals = json.loads(score["signals"])
    except ValueError:
        return "own", score["ranker_version"], {}
    lane = lanes[0] if isinstance(lanes, list) and lanes and isinstance(lanes[0], str) and lanes[0] else "own"
    values: dict[str, float] = {}
    raw = signals.get("values") if isinstance(signals, dict) else None
    for name, entry in (raw.items() if isinstance(raw, dict) else ()):
        value = entry.get("value") if isinstance(entry, dict) else None
        if isinstance(name, str) and name and isinstance(value, (int, float)) and not isinstance(value, bool) \
                and math.isfinite(value):
            values[name] = float(value)
    return lane, score["ranker_version"], values


def newest_score_parts(conn: sqlite3.Connection, company_id: int) -> tuple[str, str | None, dict[str, float]]:
    """`(lane, ranker_version, signal values)` of the company's newest score, as `own_context` reads them (lane
    `own` when it has none). The plan's writes use the version and the values; their lane is the row's."""
    return _score_parts(_newest_score(conn, company_id))


def own_context(conn: sqlite3.Connection, company_id: int, role_kind: str | None, role_first_seen_at: str | None,
                now: str) -> dict:
    """The context of a touch he logged himself (`source 'own'`): the lane, ranker version and signals come from the
    company's newest score (lane `own` when it has none), and a role was named when `role_first_seen_at` is given."""
    lane, version, signals = newest_score_parts(conn, company_id)
    return build_context(
        source="own", lane=lane, role_kind=role_kind,
        role_age_days=max(ny_days_between(role_first_seen_at, now), 0) if role_first_seen_at is not None else None,
        ranker_version=version, signals=signals, role_named=role_first_seen_at is not None,
    )


def role_age_days(posting: Mapping | None, now: str) -> int | None:
    """Days since the role was posted (else first seen), New York dates: as #15's `why.role.age_days`."""
    if posting is None:
        return None
    since = date.fromisoformat(posting["posted_on"]) if posting["posted_on"] else \
        date.fromisoformat(ny_day(posting["first_seen_at"]))
    return max(0, (date.fromisoformat(ny_day(now)) - since).days)


def plan_context(conn: sqlite3.Connection, *, lane: str, survivor: int, role_kind: str | None,
                 posting: Mapping | None, now: str) -> dict:
    """`build_context(source='plan')`: the lane is the plan row's, the role kind the person's (None for an
    application), the role age the posting's, the ranker version and signal values the company's newest score."""
    _, version, signals = newest_score_parts(conn, survivor)
    return build_context(source="plan", lane=lane, role_kind=role_kind, role_age_days=role_age_days(posting, now),
                         ranker_version=version, signals=signals, role_named=posting is not None)


def record_request(conn: sqlite3.Connection, *, person_id: int, company_id: int, sent_at: str, recorded_at: str,
                   context: dict, idempotency_key: str, posting_id: int | None = None, note: str = "",
                   actor: str = "devanshu") -> tuple[int, bool]:
    """Record one LinkedIn request: the `linkedin_log` row (`request_sent` at `sent_at`, with `note`), then the
    `linkedin_request` touch under `idempotency_key` (its `sent` event has `actor`). Returns `(touch_id, created)`:
    a key already used returns that touch and writes nothing. Run it inside the caller's transaction, so if either
    row fails neither stays. `company_id` is the surviving company; `context` comes from `plan_context` or
    `own_context`."""
    found = conn.execute("SELECT touch_id FROM outreach_touch WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
    if found is not None:
        return found[0], False
    conn.execute("INSERT INTO linkedin_log(person_id, kind, at, note, recorded_at) VALUES (?, 'request_sent', ?, ?, ?)",
                 (person_id, sent_at, note, recorded_at))
    touch_id = log_touch(conn, person_id=person_id, company_id=company_id, channel="linkedin_request",
                         sent_at=sent_at, context=context, posting_id=posting_id, idempotency_key=idempotency_key,
                         actor=actor)
    return touch_id, True
