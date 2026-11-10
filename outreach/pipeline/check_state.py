"""Check now (#194, design 4.3): what a check is doing, read from the store.

The API records a request as an `event` (entity `source`, kind `check_now_requested`, actor `devanshu`). The crawl
job, which runs the check, keeps its progress in one `pipeline_meta` row (`PROGRESS_KEY`) as JSON
`{run_id, checked, total, new, started_at, finished_at, paused_hosts}`: written when the check starts, updated as it
goes, given a `finished_at` when it ends. Reads only. Times are canonical UTC text, so they compare as text.
"""
import json
import sqlite3
from dataclasses import dataclass, field

PROGRESS_KEY = "check_progress"
REQUEST_KIND = "check_now_requested"


@dataclass(frozen=True)
class CheckStatus:
    state: str  # idle, queued, running or done
    checked: int = 0
    total: int = 0
    new: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    paused_hosts: list[str] = field(default_factory=list)


def progress(conn: sqlite3.Connection) -> dict | None:
    """The crawler's progress row, or None when no check ever started (or the row is not readable JSON)."""
    row = conn.execute("SELECT value FROM pipeline_meta WHERE key = ?", (PROGRESS_KEY,)).fetchone()
    if row is None:
        return None
    try:
        value = json.loads(row[0])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def pending_request(conn: sqlite3.Connection) -> str | None:
    """The newest request time that is newer than the last finished check, else None."""
    last = progress(conn)
    finished = last.get("finished_at") if last else None
    row = conn.execute("SELECT max(at) FROM event WHERE entity = 'source' AND kind = ? AND (? IS NULL OR at > ?)",
                       (REQUEST_KIND, finished, finished)).fetchone()
    return row[0]


def status(conn: sqlite3.Connection) -> CheckStatus:
    """Running while the progress row has no `finished_at`; else queued when a request waits; else done when a
    check finished; else idle."""
    last = progress(conn)
    fields = {}
    if last is not None:
        fields = dict(checked=int(last.get("checked") or 0), total=int(last.get("total") or 0),
                      new=int(last.get("new") or 0), started_at=last.get("started_at"),
                      finished_at=last.get("finished_at"), paused_hosts=[str(h) for h in last.get("paused_hosts") or []])
        if last.get("finished_at") is None:
            return CheckStatus(state="running", **fields)
    if pending_request(conn) is not None:
        return CheckStatus(state="queued")  # the numbers of an older check are not this check's
    return CheckStatus(state="done", **fields) if last is not None else CheckStatus(state="idle")
