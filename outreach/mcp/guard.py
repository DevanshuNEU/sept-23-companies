"""The write guard: every connection an MCP tool gets is opened here, and only here (#13, plan Task 4).

`open_guarded(grants)` opens the store with the foundation's `connect()` (so a missing, old or newer store and the
live store outside the live checkout are refused exactly as everywhere else), then adds two layers:

1. Temp triggers (`mcp_*`, on this connection only). They refuse the values Claude may never write, whatever the
   grants: approving, skipping, unapproving or reviving a message; a message born in any state but a drafted first
   email by claude; editing a message he saved or one already approved; any touch but a fresh open one; any
   evidence that is locked, by hand, confirmed, or a size, headcount, domain or hq that is not a guess; a LinkedIn
   note not saved by claude as proposed; any LinkedIn log row or outreach touch but a new request he says he sent
   (a `request_sent` row and its `linkedin_request` touch under a `bridge:` key, no outcome, no undo), and any
   change to either table; an event or review item by another actor; a reading or a queue row of any
   reader version but a side session's (`side-`, #32's `side_version`, which can never be promoted, so a reading
   from Claude never decides a posting), and on the night's queue rows any change but their order; any write at all
   to the tables in `NEVER_TABLES` (the sender, companies, no-go rows, decisions and the rest); and every DELETE.
2. An authorizer. It allows reads, transactions and savepoints, and a write only where the tool's grants name it:
   an INSERT on a table whose grant has `insert`, an UPDATE of a granted column. It refuses every DELETE, ATTACH,
   DETACH, PRAGMA, CREATE, DROP (the guard's own triggers included), ALTER, REINDEX, ANALYZE and any action it does
   not know.

A grant may never name a `NEVER_TABLES` table or a `NEVER_COLUMNS` column (`GrantError`), so no tool can even ask.
Each layer refuses an approve, a no-go write and a delete on its own; the tests run each one alone.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field

from outreach.reader.version import is_side_sql
from outreach.store import connect, db_path
from outreach.store.db import normalise

__all__ = ["Grant", "GrantError", "NEVER_TABLES", "NEVER_COLUMNS", "BRIDGE_TABLES", "TRIGGER_PREFIX", "open_guarded",
           "install_triggers", "install_authorizer", "check_grants", "check_store"]

TRIGGER_PREFIX = "mcp_"

# Tables no tool may write, ever. Claude's no-go is a Needs-you item he confirms (#9), never a no_go row.
# `linkedin_log` and `outreach_touch` are not here: since the company-first ruling (#9, 2026-10-26) the bridge's
# `log_sent` may insert a LinkedIn request Devanshu says he sent, and nothing else (the `_BRIDGE` triggers below; no
# grant may update either table, `NEVER_COLUMNS`).
NEVER_TABLES = frozenset({
    "sender", "send_day", "api_request", "application", "inbound_message", "inbound_seen",
    "daily_list", "daily_list_entry", "company", "company_alias", "company_name_key", "no_go", "board", "board_read",
    "watched_page", "job_run", "ruleset", "decision", "schema_migration", "import_record", "mx_check", "win_seen",
    "watch_dismissal", "source", "company_source", "shared_key", "work_tier", "company_post", "company_score",
    "posting", "posting_body",
})

# The two tables only `log_sent` holds a grant on (insert only; tests/test_mcp_write_grants pins it). Every other
# tool leaves them exactly as they were.
BRIDGE_TABLES = frozenset({"linkedin_log", "outreach_touch"})

# Columns no grant may name. `touch` maps to None: no column of a touch is ever updated through MCP.
_MESSAGE_SEND_COLUMNS = frozenset({"touch_id", "seq", "due_on", "approved_at", "pending_rfc_id", "claimed_at",
                                   "rfc_message_id", "gmail_message_id", "sent_at", "reconciled_at", "error"})
NEVER_COLUMNS: dict[str, frozenset[str] | None] = {
    "message": _MESSAGE_SEND_COLUMNS,
    "touch": None,
    "person": frozenset({"person_id", "company_id", "added_by", "created_at"}),
    "linkedin_draft": frozenset({"draft_id", "person_id", "reason_state", "saved_by", "created_at"}),
    "read_queue": frozenset({"posting_id", "body_sha256", "reader_version", "enqueued_at"}),
    "posting_reading": None,
    "linkedin_log": None,
    "outreach_touch": None,
}


class GrantError(ValueError):
    """A tool asked for a write no tool may have."""


@dataclass(frozen=True)
class Grant:
    """What a tool may write to one table: new rows, and updates of these columns only."""
    insert: bool = False
    update: frozenset[str] = field(default_factory=frozenset)


def check_grants(grants: Mapping[str, Grant]) -> None:
    """Raises GrantError when a grant names a never table or a never column."""
    for table, grant in grants.items():
        if table in NEVER_TABLES:
            raise GrantError(f"no tool may write {table}")
        never = NEVER_COLUMNS.get(table, frozenset())
        if never is None and grant.update:
            raise GrantError(f"no tool may update a {table} row")
        bad = sorted(grant.update & never) if never else []
        if bad:
            raise GrantError(f"no tool may update {table}.{', '.join(bad)}")


# ---------------------------------------------------------------------------------------------- layer 1: triggers

def _refuse(why: str) -> str:
    text = why.replace("'", "''")  # a message is a SQL string literal
    return f"SELECT RAISE(ABORT, 'mcp guard: {text}');"


_SEND_COLUMNS_SET = ("NEW.approved_at IS NOT NULL OR NEW.pending_rfc_id IS NOT NULL OR NEW.claimed_at IS NOT NULL "
                     "OR NEW.rfc_message_id IS NOT NULL OR NEW.gmail_message_id IS NOT NULL OR NEW.sent_at IS NOT NULL "
                     "OR NEW.reconciled_at IS NOT NULL OR NEW.error IS NOT ''")
_SEND_COLUMNS_CHANGED = " OR ".join(f"NEW.{c} IS NOT OLD.{c}" for c in sorted(_MESSAGE_SEND_COLUMNS))

# A reader version that is not a side session's (#32 `side_version`: `side-` and the files' version).
_NOT_SIDE = f"NOT ({is_side_sql('{0}.reader_version')})"

_VALUE_TRIGGERS = (
    ("message_insert", "BEFORE INSERT ON main.message",
     f"NEW.state IS NOT 'drafted' OR NEW.seq IS NOT 0 OR NEW.saved_by IS NOT 'claude' OR {_SEND_COLUMNS_SET}",
     "a message from Claude is born a drafted first email saved by claude, never approved or sent"),
    ("message_state", "BEFORE UPDATE OF state ON main.message",
     "NEW.state IS NOT OLD.state AND NOT (OLD.state = 'planned' AND NEW.state = 'drafted')",
     "Claude never approves, skips, unapproves or revives a message; only planned to drafted"),
    ("message_update", "BEFORE UPDATE ON main.message",
     f"NEW.saved_by IS NOT 'claude' OR OLD.saved_by NOT IN ('claude', 'sender') "
     f"OR OLD.state NOT IN ('planned', 'drafted') OR {_SEND_COLUMNS_CHANGED}",
     "Claude edits only its own drafts and planned follow-ups, never one Devanshu saved or approved"),
    ("touch_insert", "BEFORE INSERT ON main.touch",
     "NEW.state IS NOT 'open' OR NEW.origin IS NOT 'sender' OR NEW.close_reason IS NOT NULL "
     "OR NEW.gmail_thread_id IS NOT NULL OR NEW.first_rfc_id IS NOT NULL OR NEW.first_sent_at IS NOT NULL "
     "OR NEW.replied_at IS NOT NULL OR NEW.reply_kind IS NOT NULL OR NEW.bounced_at IS NOT NULL "
     "OR NEW.auto_reply_at IS NOT NULL OR NEW.closed_at IS NOT NULL",
     "a touch from Claude starts open with nothing sent"),
    ("touch_update", "BEFORE UPDATE ON main.touch", None, "Claude never changes a touch"),
    ("person_insert", "BEFORE INSERT ON main.person", "NEW.added_by IS NOT 'claude'",
     "a person Claude adds is added_by claude"),
    ("person_update", "BEFORE UPDATE ON main.person",
     "NEW.added_by IS NOT OLD.added_by OR NEW.company_id IS NOT OLD.company_id",
     "Claude never changes who added a person or their company"),
    ("linkedin_draft_insert", "BEFORE INSERT ON main.linkedin_draft",
     "NEW.saved_by IS NOT 'claude' OR NEW.reason_state IS NOT 'proposed'",
     "a LinkedIn note from Claude is saved by claude with its reason line proposed"),
    ("linkedin_draft_update", "BEFORE UPDATE ON main.linkedin_draft",
     "OLD.saved_by IS NOT 'claude' OR NEW.saved_by IS NOT 'claude' OR NEW.reason_state IS NOT 'proposed'",
     "Claude never confirms a reason line or edits a note Devanshu saved"),
    ("read_queue_insert", "BEFORE INSERT ON main.read_queue",
     f"{_NOT_SIDE.format('NEW')} OR NEW.state IS NOT 'queued' OR NEW.attempts IS NOT 0 OR NEW.lease_until IS NOT NULL",
     "Claude queues only a side session's readings, and a new row is queued with no lease"),
    ("read_queue_update", "BEFORE UPDATE ON main.read_queue",
     "NEW.posting_id IS NOT OLD.posting_id OR NEW.body_sha256 IS NOT OLD.body_sha256 "
     "OR NEW.reader_version IS NOT OLD.reader_version OR NEW.enqueued_at IS NOT OLD.enqueued_at "
     f"OR ({_NOT_SIDE.format('OLD')} AND (NEW.state IS NOT OLD.state OR NEW.lease_until IS NOT OLD.lease_until "
     "OR NEW.attempts IS NOT OLD.attempts OR NEW.last_error IS NOT OLD.last_error))",
     "Claude leases and files only a side session's queue rows; of the night's rows only the order may change"),
    ("posting_reading_insert", "BEFORE INSERT ON main.posting_reading", _NOT_SIDE.format("NEW"),
     "a reading from Claude is filed under a side version, so it never decides a posting"),
    ("posting_reading_update", "BEFORE UPDATE ON main.posting_reading", None, "Claude never changes a stored reading"),
    ("evidence_insert", "BEFORE INSERT ON main.evidence",
     "NEW.locked IS NOT 0 OR NEW.source_kind IS 'hand' OR NEW.confidence IS 'confirmed' "
     "OR (NEW.fact IN ('size', 'headcount', 'domain', 'hq') AND NEW.confidence IS NOT 'guess')",
     "evidence from Claude is never locked, by hand or confirmed; a size, headcount, domain or hq is a guess"),
    ("review_item_insert", "BEFORE INSERT ON main.review_item",
     "NEW.raised_by IS NOT 'claude'",  # the reader tools raise none: they never combine a reading (reader.py)
     "a review item from Claude is raised by claude"),
    ("review_item_update", "BEFORE UPDATE ON main.review_item", "NEW.resolution IS NOT 'cleared'",
     "Claude only clears a review item; Devanshu resolves them"),
    ("event_insert", "BEFORE INSERT ON main.event", "NEW.actor IS NOT 'claude'",
     "an event from Claude has actor claude"),
    # The bridge (#9, company-first ruling): a request he says he sent is one request_sent log row, at most 4 days
    # before it is recorded and never after, with a note LinkedIn would take (300 characters), then its touch: a
    # linkedin_request under a bridge key with no outcome and no undo, sent at the time of a request_sent row of
    # the same person. Never an accept, reply, referral, interview, offer, message, email touch, undo or change.
    ("linkedin_log_insert", "BEFORE INSERT ON main.linkedin_log",
     "NEW.kind IS NOT 'request_sent' OR NEW.reply_kind IS NOT NULL OR NEW.undoes IS NOT NULL "
     "OR length(NEW.note) > 300 OR NEW.at > NEW.recorded_at OR julianday(NEW.recorded_at) - julianday(NEW.at) > 4",
     "Claude records only a LinkedIn request Devanshu says he sent, never an accept, reply, message or undo"),
    ("linkedin_log_update", "BEFORE UPDATE ON main.linkedin_log", None, "Claude never changes the LinkedIn log"),
    ("outreach_touch_insert", "BEFORE INSERT ON main.outreach_touch",
     "NEW.channel IS NOT 'linkedin_request' OR NEW.person_id IS NULL OR NEW.email_touch_id IS NOT NULL "
     "OR NEW.accepted_at IS NOT NULL OR NEW.replied_at IS NOT NULL OR NEW.reply_kind IS NOT NULL "
     "OR NEW.referred_at IS NOT NULL OR NEW.interview_at IS NOT NULL OR NEW.offer_at IS NOT NULL "
     "OR NEW.quiet_at IS NOT NULL OR NEW.undone_at IS NOT NULL OR NEW.revision IS NOT 1 "
     "OR substr(NEW.idempotency_key, 1, 7) IS NOT 'bridge:' "
     "OR NOT EXISTS (SELECT 1 FROM main.linkedin_log l WHERE l.person_id = NEW.person_id "
     "AND l.kind = 'request_sent' AND l.at = NEW.sent_at)",
     "a touch from Claude is a LinkedIn request he sent, logged by log_sent, with no outcome"),
    ("outreach_touch_update", "BEFORE UPDATE ON main.outreach_touch", None,
     "Claude never changes a touch: no reply, interview, referral or undo"),
)


def _trigger_sql(conn: sqlite3.Connection) -> list[str]:
    statements = []
    for name, when_on, condition, why in _VALUE_TRIGGERS:
        when = f" WHEN {condition}" if condition else ""
        statements.append(f"CREATE TEMP TRIGGER {TRIGGER_PREFIX}{name} {when_on} FOR EACH ROW{when} "
                          f"BEGIN {_refuse(why)} END")
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM main.sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    for table in tables:
        statements.append(f"CREATE TEMP TRIGGER {TRIGGER_PREFIX}no_delete_{table} BEFORE DELETE ON main.{table} "
                          f"FOR EACH ROW BEGIN {_refuse('Claude never deletes a row')} END")
        if table in NEVER_TABLES:
            for verb in ("INSERT", "UPDATE"):
                statements.append(
                    f"CREATE TEMP TRIGGER {TRIGGER_PREFIX}never_{verb.lower()}_{table} BEFORE {verb} "
                    f"ON main.{table} FOR EACH ROW BEGIN {_refuse(f'Claude never writes {table}')} END")
    return statements


def install_triggers(conn: sqlite3.Connection) -> None:
    """Adds the guard's temp triggers to `conn` (layer 1). They live only as long as the connection."""
    for sql in _trigger_sql(conn):
        conn.execute(sql)


# ---------------------------------------------------------------------------------------------- layer 2: authorizer

_ALWAYS = frozenset({sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_TRANSACTION,
                     sqlite3.SQLITE_SAVEPOINT, sqlite3.SQLITE_RECURSIVE})
_DENIED_FUNCTIONS = frozenset({"load_extension"})


def install_authorizer(conn: sqlite3.Connection, grants: Mapping[str, Grant]) -> None:
    """Sets the authorizer on `conn` (layer 2): reads anywhere, writes only where `grants` say, nothing else."""
    check_grants(grants)
    inserts = frozenset(t for t, g in grants.items() if g.insert)
    updates = {t: frozenset(g.update) for t, g in grants.items() if g.update}

    def authorize(action, arg1, arg2, database, _source):
        if action in _ALWAYS:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_DENY if (arg2 or "").lower() in _DENIED_FUNCTIONS else sqlite3.SQLITE_OK
        if database != "main":
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_INSERT and arg1 in inserts:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_UPDATE and arg2 in updates.get(arg1, ()):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    conn.set_authorizer(authorize)


# ---------------------------------------------------------------------------------------------- opening

def open_guarded(grants: Mapping[str, Grant] | None = None, *, path: str | None = None) -> sqlite3.Connection:
    """A connection to the store with both layers on. The grants are checked before the store is opened."""
    grants = dict(grants or {})
    check_grants(grants)
    conn = connect(path)
    try:
        install_triggers(conn)
        install_authorizer(conn, grants)
    except BaseException:
        conn.close()
        raise
    return conn


def check_store(path: str | None = None) -> str:
    """Opens and closes the store through `connect()` (every refusal comes from there); returns its path."""
    connect(path).close()
    return normalise(path) if path is not None else db_path()
