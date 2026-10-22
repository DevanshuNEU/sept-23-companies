"""Store seed helpers for the ranking tests (#6): boards in a given read state, postings with dates, decision rows
written directly, evidence, no-go rows and review items. Each helper writes the smallest valid row."""
from __future__ import annotations

import itertools
import sqlite3

from outreach.rules import rebuild
from outreach.store.timefmt import utcnow

_keys = itertools.count(1)
TEST_RULESET = "r-0000000000a1"

_DECISION_DEFAULTS = dict(
    lane="engineering", level_tier="new_grad", level_ok=1, level_basis="Title says new grad", years_asked=None,
    years_quote="", us="yes", sponsorship="silent", sponsorship_quote="", citizenship=0, citizenship_quote="",
    eligibility="ok", eligibility_reason=None, eligibility_quote="", start_2027=0, provisional=1, reader_version=None)


def board(conn: sqlite3.Connection, company_id: int, *, found_by: str = "import", last_read: str | None = "ok",
          read_at: str = "2026-10-21T08:00:00Z", empty_streak: int | None = None, error_streak: int | None = None,
          active: int = 1, ats: str = "greenhouse") -> int:
    """A board read `last_read` at `read_at` (None: never read). The streaks default to what the state needs."""
    if empty_streak is None:
        empty_streak = 1 if last_read == "empty" else 0
    if error_streak is None:
        error_streak = 1 if last_read == "error" else 0
    when = None if last_read is None else read_at
    return conn.execute(
        "INSERT INTO board(company_id, ats, token, active, last_read, last_read_at, empty_streak, error_streak, "
        "streak_since, found_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (company_id, ats, f"tok{next(_keys)}", active, last_read, when, empty_streak, error_streak, when, found_by,
         "2026-09-01T08:00:00Z")).lastrowid


def posting(conn: sqlite3.Connection, company_id: int, title: str = "Software Engineer, New Grad", *,
            board_id: int | None = None, body: str = "", location: str = "San Francisco, CA",
            posted_on: str | None = None, first_seen_at: str = "2026-10-01T08:00:00Z",
            last_seen_at: str = "2026-10-21T08:00:00Z", closed: bool = False, pay: str = "") -> int:
    """An open posting (closed by hand when `closed`). No board means a `url:` posting."""
    key = f"{'url' if board_id is None else 'b'}:{next(_keys)}"
    pid = conn.execute(
        "INSERT INTO posting(company_id, board_id, source_key, title, location, posted_on, pay, first_seen_at, "
        "last_seen_at, closed_at, close_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (company_id, board_id, key, title, location, posted_on, pay, first_seen_at, last_seen_at,
         last_seen_at if closed else None, "hand" if closed else None)).lastrowid
    if body:
        conn.execute("INSERT INTO posting_body(posting_id, body) VALUES (?, ?)", (pid, body))
    return pid


def current_ruleset(conn: sqlite3.Connection) -> str:
    """The store's current ruleset, made current as a test version when there is none."""
    row = conn.execute("SELECT ruleset_version FROM ruleset WHERE is_current = 1").fetchone()
    if row is not None:
        return row[0]
    conn.execute("INSERT INTO ruleset(ruleset_version, config_toml, created_at, is_current) VALUES (?, '', ?, 1)",
                 (TEST_RULESET, utcnow()))
    return TEST_RULESET


def decision(conn: sqlite3.Connection, posting_id: int, **fields) -> None:
    """A decision row under the current ruleset: an engineering new-grad US posting that says nothing about
    sponsorship, provisional, unless `fields` say otherwise. Quotes the CHECKs need are filled in."""
    row = {**_DECISION_DEFAULTS, **fields}
    if row["sponsorship"] != "silent" and not row["sponsorship_quote"]:
        row["sponsorship_quote"] = f"sponsorship {row['sponsorship']}"
    if row["citizenship"] and not row["citizenship_quote"]:
        row["citizenship_quote"] = "Must be a US citizen."
    if row["eligibility"] == "excluded":
        row["eligibility_reason"] = row["eligibility_reason"] or "current_student"
        row["eligibility_quote"] = row["eligibility_quote"] or "Must be currently enrolled."
    if row["lane"] != "engineering" or row["level_tier"] in ("not_stated", "senior"):
        row["level_ok"] = 0
    cols = ", ".join(row)
    conn.execute(f"INSERT INTO decision(posting_id, ruleset_version, decided_at, {cols}) VALUES (?, ?, ?, "
                 + ", ".join("?" for _ in row) + ")", (posting_id, current_ruleset(conn), utcnow(), *row.values()))


def decide_all(conn: sqlite3.Connection) -> None:
    """Decide every posting with the real rules (a rebuild under the code's ruleset)."""
    rebuild(conn)


def make_final(conn: sqlite3.Connection, posting_id: int) -> None:
    """A verified reading agreed: the posting's current decision is final."""
    conn.execute("UPDATE decision SET provisional = 0 WHERE posting_id = ? AND ruleset_version = ?",
                 (posting_id, current_ruleset(conn)))


def evidence(conn: sqlite3.Connection, company_id: int, fact: str, *, value: str = "x", num_low: int | None = None,
             num_high: int | None = None, confidence: str = "estimate", source_kind: str = "linkedin",
             source_ref: str = "https://example.com/about") -> int:
    return conn.execute(
        "INSERT INTO evidence(company_id, fact, value, num_low, num_high, confidence, source_kind, source_ref, "
        "recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (company_id, fact, value, num_low, num_high, confidence, source_kind, source_ref, utcnow())).lastrowid


def no_go(conn: sqlite3.Connection, company_id: int, reason: str = "no_sponsorship", *, lifted: bool = False,
          source: str = "devanshu") -> int:
    now = utcnow()
    return conn.execute(
        "INSERT INTO no_go(company_id, reason, source, created_at, lifted_at, lift_reason) VALUES (?, ?, ?, ?, ?, ?)",
        (company_id, reason, source, now, now if lifted else None, "he said yes" if lifted else None)).lastrowid


def flag_board(conn: sqlite3.Connection, company_id: int, board_id: int,
               reason: str = "board match doubtful: org name differs", raised_by: str = "crawler") -> int:
    return conn.execute(
        "INSERT INTO review_item(kind, company_id, board_id, reason, evidence_key, raised_by, created_at) "
        "VALUES ('flagged', ?, ?, ?, ?, ?, ?)",
        (company_id, board_id, reason, f"k{next(_keys)}", raised_by, utcnow())).lastrowid


def resolve(conn: sqlite3.Connection, review_id: int, resolution: str = "marked_ok") -> None:
    conn.execute("UPDATE review_item SET state = 'resolved', resolution = ?, resolved_at = ? WHERE review_id = ?",
                 (resolution, utcnow(), review_id))


def merge(conn: sqlite3.Connection, company_id: int, into: int) -> None:
    conn.execute("UPDATE company SET status = 'removed', remove_reason = 'duplicate', merged_into = ? "
                 "WHERE company_id = ?", (into, company_id))
