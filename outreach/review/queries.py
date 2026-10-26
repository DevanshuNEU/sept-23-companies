"""The facts the Needs-you view shows that a stored row cannot hold on its own (#16): a board item's reason from the
board as it is now, plain sentences for the reasons stored as technical text, what changed when an item came back,
and how many items he settled today."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from outreach.review import words
from outreach.review.items import get_item
from outreach.review.play import ny_date
from outreach.store.timefmt import ISO_FMT

__all__ = ["display_reason", "reopened_note", "settled_today"]

_NEW_YORK = ZoneInfo("America/New_York")
_BOARD_KINDS = ("board_unreadable", "board_empty")
_HIS = ("marked_ok", "marked_no_go", "field_edited")


def _row(conn: sqlite3.Connection, row: sqlite3.Row | int) -> sqlite3.Row:
    """The item row, given the row or its id."""
    return get_item(conn, row) if isinstance(row, int) else row


# The stored reasons that are not plain words, as their producers write them. The store keeps that text (other code
# and the importer key on it); only the display changes. Anything that does not match shows the stored text.
_MERGE = re.compile(r"Maybe one company: .+? \(#\d+\) and .+? \(#\d+\)\. Basis (?P<basis>\S+), confidence \w+: "
                    r"(?P<detail>.*)\Z", re.S)  # identity.raise_merge_candidate
_SHARED = re.compile(r"(?P<name>.+) \(#\d+\)\. Basis shared_domain, confidence \w+: shares (?P<domain>\S+) with "
                     r"(?:(?P<n>\d+) )?other employers: it may be a subsidiary; size unknown\Z", re.S)  # resolve_store
_READER_LEAD = "The reader and the rules disagree on "  # reader.combine._reason
_JSON = r'"(?:[^"\\]|\\.)*"|true|false|null|-?\d+(?:\.\d+)?'
_READER_PART = re.compile(rf'(?P<field>[a-z_]+): rules say (?P<rules>{_JSON}), reader says '
                          rf'(?P<reader>unknown or refused|{_JSON})(?: \("(?P<quote>.*?)"\))?'
                          r"(?=; [a-z_]+: rules say |\Z)", re.S)


def _merge_text(conn: sqlite3.Connection, row: sqlite3.Row, reason: str) -> str | None:
    m = _MERGE.match(reason)
    return words.merge_reason(m["basis"], m["detail"]) if m else None


def _size_text(conn: sqlite3.Connection, row: sqlite3.Row, reason: str) -> str | None:
    m = _SHARED.match(reason)
    if m is None:
        return None  # the ranker's size checks are plain words already
    now = conn.execute("SELECT name FROM company WHERE company_id = ?", (row["company_id"],)).fetchone()
    return words.shared_domain_size_reason(now[0] if now else m["name"], m["domain"],
                                           int(m["n"]) if m["n"] else None)


def _reader_parts(reason: str) -> list[tuple[str, object, object, str]] | None:
    """(field, rules value, reader value or None for "unknown or refused", quote) per part, or None."""
    if not reason.startswith(_READER_LEAD):
        return None
    pos, parts = len(_READER_LEAD), []
    while True:
        m = _READER_PART.match(reason, pos)
        if m is None:
            return None
        try:
            rules = json.loads(m["rules"])
            reader = None if m["reader"] == "unknown or refused" else json.loads(m["reader"])
        except ValueError:
            return None
        parts.append((m["field"], rules, reader, m["quote"] or ""))
        pos = m.end()
        if pos == len(reason):
            return parts
        if not reason.startswith("; ", pos):
            return None
        pos += 2


def _reader_text(conn: sqlite3.Connection, row: sqlite3.Row, reason: str) -> str | None:
    parts = _reader_parts(reason)
    return words.reader_reason(parts) if parts else None


_PLAIN = {"merge_candidate": _merge_text, "size_check": _size_text, "reader_disagrees": _reader_text}


def display_reason(conn: sqlite3.Connection, row: sqlite3.Row | int) -> str:
    """The reason to show. A board item's is built from the board now (`words.board_reason`): before #9's two-read
    rule #21 raised `board_empty` on the first empty read, so such a stored sentence would still say "once" after
    the second empty read closed the postings. Closed postings are those closed `board_empty_twice` since the streak began. A merge
    question, a reader disagreement and a shared-domain size check show plain sentences built from their stored text
    (`words.merge_reason`, `reader_reason`, `shared_domain_size_reason`); a stored text these cannot read shows as it
    is. Every other kind, and a board never read, shows the stored reason. A missing reason shows as empty text."""
    row = _row(conn, row)
    reason = row["reason"] or ""
    if row["kind"] in _PLAIN:
        return _PLAIN[row["kind"]](conn, row, reason) or reason
    if row["kind"] not in _BOARD_KINDS or row["board_id"] is None:
        return reason
    board = conn.execute("SELECT * FROM board WHERE board_id = ?", (row["board_id"],)).fetchone()
    if board is None or board["last_read"] is None:
        return reason
    since = board["streak_since"] or row["created_at"]
    last_ok = conn.execute("SELECT max(at) FROM board_read WHERE board_id = ? AND result IN ('ok', 'unchanged')",
                           (row["board_id"],)).fetchone()[0]
    open_postings = conn.execute("SELECT count(*) FROM posting WHERE board_id = ? AND closed_at IS NULL",
                                 (row["board_id"],)).fetchone()[0]
    closed = conn.execute("SELECT count(*) FROM posting WHERE board_id = ? AND close_reason = 'board_empty_twice' "
                          "AND closed_at >= ?", (row["board_id"], since)).fetchone()[0]
    return words.board_reason(row["kind"], ats=board["ats"], error_streak=board["error_streak"],
                              empty_streak=board["empty_streak"], last_error=board["last_error"], since=since,
                              last_ok_at=last_ok, open_postings=open_postings, closed_postings=closed)


def reopened_note(conn: sqlite3.Connection, row: sqlite3.Row | int) -> dict[str, str] | None:
    """What an item that came back should say, through `words.reopened`: the newest settled sibling (same kind,
    company, board and person, and the same posting except for trigger 3, which is one item per company) with
    another evidence key, raised no later than this one. None for a first item. Takes the row or its id."""
    row = _row(conn, row)
    sql = ("SELECT * FROM review_item WHERE kind = ? AND company_id = ? AND ifnull(board_id, 0) = ? "
           "AND ifnull(person_id, 0) = ? AND state = 'resolved' AND evidence_key <> ? AND review_id <> ? "
           "AND created_at <= ?")
    args: list = [row["kind"], row["company_id"], row["board_id"] or 0, row["person_id"] or 0, row["evidence_key"],
                  row["review_id"], row["created_at"]]
    if row["kind"] != "sponsorship_unclear":
        sql += " AND ifnull(posting_id, 0) = ?"
        args.append(row["posting_id"] or 0)
    previous = conn.execute(sql + " ORDER BY resolved_at DESC, review_id DESC LIMIT 1", args).fetchone()
    if previous is None:
        return None
    return words.reopened(row["kind"], dict(previous), row["evidence_key"])


def _day_bounds(day: str) -> tuple[str, str]:
    """[start, end) of a New York date, in canonical UTC."""
    start = datetime.combine(date.fromisoformat(day), time(), _NEW_YORK)
    end = datetime.combine(date.fromisoformat(day) + timedelta(days=1), time(), _NEW_YORK)
    return tuple(t.astimezone(timezone.utc).strftime(ISO_FMT) for t in (start, end))


def settled_today(conn: sqlite3.Connection, now: str) -> int:
    """How many items he settled (ok, no-go or a field he set) on today's New York date. Items that cleared by
    themselves do not count."""
    start, end = _day_bounds(ny_date(now))
    return conn.execute(
        "SELECT count(*) FROM review_item WHERE state = 'resolved' AND resolution IN (?, ?, ?) "
        "AND resolved_at >= ? AND resolved_at < ?", (*_HIS, start, end)).fetchone()[0]
