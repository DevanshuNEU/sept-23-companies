"""The store's write helpers: company name keys, events, job runs.

Every function takes an open connection and writes with single statements, so each is
atomic on its own. Callers that need several writes to land together wrap them in
`db.transaction`.
"""
import json
import re
import sqlite3

from outreach.store.timefmt import iso_utc, utcnow

__all__ = ["name_key", "append_event", "start_job", "finish_job"]

# A trailing legal suffix, as a whole word. The separator before it is required, so
# "Corpay" and "Coinbase" are untouched.
_LEGAL_SUFFIX = re.compile(r"[\s,.]+(?:inc|llc|ltd|corp|co|gmbh)[\s.]*$")


def name_key(name: str | None) -> str:
    """The comparison key for a company name: lower case, legal suffix dropped, `[a-z0-9]` only.

    "Retell AI, Inc." and "Retell AI" share a key. Based on Job Radar `normalise_company`,
    plus the suffix strip. A name that is only a suffix keeps it.
    """
    key = (name or "").lower()
    while True:
        shorter = _LEGAL_SUFFIX.sub("", key)
        if shorter == key or not re.search(r"[a-z0-9]", shorter):
            break
        key = shorter
    return re.sub(r"[^a-z0-9]", "", key)


def append_event(
    conn: sqlite3.Connection,
    *,
    entity: str,
    kind: str,
    actor: str,
    entity_id: int | None = None,
    company_id: int | None = None,
    detail: dict | None = None,
    at: str | None = None,
    idempotency_key: str | None = None,
) -> int | None:
    """Append one event. Returns its id, or None if `idempotency_key` was already used.

    `at` is when it happened (any ISO-8601 form, stored as canonical UTC; default now).
    `recorded_at` is always now. A repeated key writes nothing: events are append-only,
    so the first row stands, whatever the second call says.
    """
    now = utcnow()
    cur = conn.execute(
        "INSERT INTO event(at, recorded_at, entity, entity_id, company_id, kind, actor, detail, idempotency_key) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(idempotency_key) DO NOTHING",
        (
            iso_utc(at) if at else now, now, entity, entity_id, company_id, kind, actor,
            json.dumps(detail if detail is not None else {}, sort_keys=True), idempotency_key,
        ),
    )
    return cur.lastrowid if cur.rowcount else None


def start_job(conn: sqlite3.Connection, job: str, ruleset_version: str | None = None) -> int:
    """Record that a job started. Returns the run id."""
    cur = conn.execute(
        "INSERT INTO job_run(job, started_at, ruleset_version) VALUES (?, ?, ?)",
        (job, utcnow(), ruleset_version),
    )
    return cur.lastrowid


def finish_job(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    summary: dict | None = None,
    error: str = "",
) -> None:
    """Close a run as ok, partial or failed. Raises LookupError if there is no such run."""
    cur = conn.execute(
        "UPDATE job_run SET status = ?, finished_at = ?, summary = ?, error = ? WHERE run_id = ?",
        (status, utcnow(), json.dumps(summary if summary is not None else {}, sort_keys=True), error, run_id),
    )
    if cur.rowcount == 0:
        raise LookupError(f"no job run {run_id}")
