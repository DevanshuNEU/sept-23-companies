"""The store's write helpers: company name keys, events, job runs, aliases, shared keys and touches.

Every function takes an open connection and writes with single statements, so each is
atomic on its own. Callers that need several writes to land together wrap them in
`db.transaction`.
"""
import hashlib
import json
import re
import sqlite3

from outreach.store.timefmt import iso_utc, utcnow

__all__ = ["name_key", "append_event", "start_job", "finish_job", "body_sha256", "add_alias", "mark_shared_key",
           "is_shared_key", "log_touch", "record_outcome", "undo_touch"]

_WHITESPACE = re.compile(r"\s+")

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


def body_sha256(text: str) -> str:
    """The one hash of a posting body: sha256 hex of the text with every run of whitespace turned into one space
    and the ends stripped, UTF-8, case kept. `posting_body.body_sha256`, `read_queue` and `posting_reading` all
    hold this value, so a body that only changes its line breaks is not a new body."""
    return hashlib.sha256(_WHITESPACE.sub(" ", text).strip().encode("utf-8")).hexdigest()


# Sources whose alias key is the raw value lower-cased. Every other source is a company name: `name_key`.
_RAW_ALIAS_SOURCES = ("fein", "ats", "linkedin")
_NAME_ALIAS_SOURCES = ("name", "hand", "import", "crawl", "merge", "dba", "legal_name", "formd")


def add_alias(conn: sqlite3.Connection, company_id: int, alias: str, source: str) -> bool:
    """Record another name (or FEIN, board, LinkedIn page) for a company.

    The key is `name_key(alias)` for a name-like source and the raw lower-cased value for `fein`, `ats` and
    `linkedin`. Returns True when it was added, False when the key already belongs to this company. Raises
    `ValueError` when the key belongs to another company (identity never merges two companies on an alias), when
    the source is unknown, or when the key would be empty.
    """
    if source in _RAW_ALIAS_SOURCES:
        key = alias.lower()
    elif source in _NAME_ALIAS_SOURCES:
        key = name_key(alias)
    else:
        raise ValueError(f"unknown alias source {source!r}")
    if not key:
        raise ValueError(f"alias {alias!r} has no key")
    cur = conn.execute(
        "INSERT INTO company_alias(alias_key, company_id, alias, source, created_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(alias_key) DO NOTHING",
        (key, company_id, alias, source, utcnow()),
    )
    if cur.rowcount:
        return True
    owner = conn.execute("SELECT company_id FROM company_alias WHERE alias_key = ?", (key,)).fetchone()[0]
    if owner != company_id:
        raise ValueError(f"alias key {key!r} belongs to another company ({owner})")
    return False


def mark_shared_key(conn: sqlite3.Connection, kind: str, value: str, reason: str) -> None:
    """Record that a FEIN or domain is seen on more than one employer, so identity never merges on it.
    Idempotent: a second call changes nothing, whatever its reason says."""
    conn.execute(
        "INSERT INTO shared_key(kind, value, reason, recorded_at) VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
        (kind, value.lower(), reason, utcnow()),
    )


def is_shared_key(conn: sqlite3.Connection, kind: str, value: str) -> bool:
    return conn.execute("SELECT 1 FROM shared_key WHERE kind = ? AND value = ?",
                        (kind, value.lower())).fetchone() is not None


def log_touch(
    conn: sqlite3.Connection,
    *,
    person_id: int | None,
    company_id: int,
    channel: str,
    sent_at: str,
    context: dict,
    posting_id: int | None = None,
    email_touch_id: int | None = None,
    idempotency_key: str,
) -> int:
    """Record that Devanshu reached out. Returns the touch id: the existing one when `idempotency_key` was used
    before (nothing else is written then). A new touch appends an `outreach_touch` event of kind `sent`.

    `context` is the dict #11's `build_context` makes; the schema refuses any other shape. The touch row and its
    event are two statements: wrap the call in `db.transaction` when it sits beside other writes.
    """
    found = conn.execute("SELECT touch_id FROM outreach_touch WHERE idempotency_key = ?",
                         (idempotency_key,)).fetchone()
    if found:
        return found[0]
    sent = iso_utc(sent_at)
    try:
        cur = conn.execute(
            "INSERT INTO outreach_touch(person_id, company_id, channel, posting_id, email_touch_id, sent_at, "
            "context, idempotency_key, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (person_id, company_id, channel, posting_id, email_touch_id, sent,
             json.dumps(context, sort_keys=True), idempotency_key, utcnow()),
        )
    except sqlite3.IntegrityError:
        # Another connection logged this key between the lookup and the insert: that touch stands.
        found = conn.execute("SELECT touch_id FROM outreach_touch WHERE idempotency_key = ?",
                             (idempotency_key,)).fetchone()
        if found:
            return found[0]
        raise
    touch_id = cur.lastrowid
    append_event(
        conn, entity="outreach_touch", kind="sent", actor="devanshu", entity_id=touch_id, company_id=company_id,
        at=sent, detail={"channel": channel, "personId": person_id, "postingId": posting_id},
    )
    return touch_id


_OUTCOME_COLUMNS = {
    "accepted": "accepted_at", "replied": "replied_at", "referred": "referred_at",
    "interview": "interview_at", "offer": "offer_at", "quiet": "quiet_at",
}
_REPLY_KINDS = ("unsorted", "interested", "not_now", "said_no", "asked_to_stop", "auto_reply")


def _touch_row(conn: sqlite3.Connection, touch_id: int, revision: int) -> sqlite3.Row:
    """The touch, after checking it exists and `revision` is its current one (LookupError otherwise)."""
    row = conn.execute("SELECT * FROM outreach_touch WHERE touch_id = ?", (touch_id,)).fetchone()
    if row is None:
        raise LookupError(f"no touch {touch_id}")
    if row["revision"] != revision:
        raise LookupError(f"touch {touch_id} is at revision {row['revision']}, not {revision}")
    return row


def _bump(conn: sqlite3.Connection, set_sql: str, params: tuple, touch_id: int, revision: int) -> int:
    """One guarded UPDATE: it lands only if the revision is still the one the caller read."""
    cur = conn.execute(
        f"UPDATE outreach_touch SET {set_sql}, revision = revision + 1 WHERE touch_id = ? AND revision = ?",
        (*params, touch_id, revision),
    )
    if cur.rowcount == 0:
        raise LookupError(f"touch {touch_id} changed since revision {revision}")
    return revision + 1


def record_outcome(
    conn: sqlite3.Connection,
    touch_id: int,
    *,
    kind: str,
    at: str,
    reply_kind: str | None = None,
    revision: int,
    actor: str = "devanshu",
) -> int:
    """Record what came of a touch. `kind` is accepted, replied, referred, interview, offer or quiet.

    Sets the matching date (a repeat overwrites it; `correct_outcome` is the way to clear one). `replied` also
    stores `reply_kind` and clears `quiet_at`: a late reply beats quiet. Returns the new revision and appends an
    event of the same kind. `LookupError` when the touch is missing or `revision` is stale; `ValueError` when the
    touch is undone, `at` is before it was sent, `reply_kind` is not a reply kind or is given with another outcome,
    or a touch that replied is marked quiet.
    """
    column = _OUTCOME_COLUMNS.get(kind)
    if column is None:
        raise ValueError(f"unknown outcome {kind!r}")
    if reply_kind is not None and (kind != "replied" or reply_kind not in _REPLY_KINDS):
        raise ValueError(f"reply_kind {reply_kind!r} does not go with outcome {kind!r}")
    row = _touch_row(conn, touch_id, revision)
    when = iso_utc(at)
    if row["undone_at"] is not None:
        raise ValueError(f"touch {touch_id} was undone")
    if when < row["sent_at"]:
        raise ValueError(f"{kind} at {when} is before the touch was sent at {row['sent_at']}")
    if kind == "quiet" and row["replied_at"] is not None:
        raise ValueError(f"touch {touch_id} has a reply, so it is not quiet")
    if kind == "replied":
        new = _bump(conn, "replied_at = ?, reply_kind = ?, quiet_at = NULL", (when, reply_kind), touch_id, revision)
    else:
        new = _bump(conn, f"{column} = ?", (when,), touch_id, revision)
    append_event(
        conn, entity="outreach_touch", kind=kind, actor=actor, entity_id=touch_id, company_id=row["company_id"],
        at=when, detail={"replyKind": reply_kind} if reply_kind else {},
    )
    return new


def undo_touch(conn: sqlite3.Connection, touch_id: int, *, at: str, revision: int, actor: str = "devanshu") -> int:
    """Take back a touch logged by mistake. It is kept (`undone_at` is set), never deleted, and counts nowhere:
    every count of touches filters `undone_at IS NULL`. Returns the new revision and appends an `undone` event.
    `LookupError` when the touch is missing or `revision` is stale; `ValueError` when it is already undone or
    any outcome date is set (an outcome means the touch was real)."""
    row = _touch_row(conn, touch_id, revision)
    if row["undone_at"] is not None:
        raise ValueError(f"touch {touch_id} is already undone")
    if any(row[c] is not None for c in _OUTCOME_COLUMNS.values()):
        raise ValueError(f"touch {touch_id} has an outcome, so it cannot be undone")
    when = iso_utc(at)
    new = _bump(conn, "undone_at = ?", (when,), touch_id, revision)
    append_event(
        conn, entity="outreach_touch", kind="undone", actor=actor, entity_id=touch_id,
        company_id=row["company_id"], at=when,
    )
    return new
