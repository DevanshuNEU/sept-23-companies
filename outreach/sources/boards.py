"""Board references (#31 Task 10): the ATS names a board can have, its token and `add_board`.

A board is `(ats, token)`. The token follows the one grammar of `outreach.crawl.base.valid_token`: the lower-case slug
for most ATS, Workday `<tenant>.wd<N>/<site>`, Oracle `<host>/<siteNumber>` and iCIMS the board host. `BoardRef`
normalizes and validates on construction, so a malformed token never reaches a row.
"""
from __future__ import annotations

import contextlib
import sqlite3
from dataclasses import dataclass
from typing import Literal

from outreach.crawl.base import BOARD_ATS, valid_token
from outreach.identity import follow_merged, raise_merge_candidate
from outreach.store import add_alias, append_event, transaction, utcnow

__all__ = ["SUPPORTED_ATS", "FOUND_BY", "BoardRef", "normalize_token", "add_board"]

# The board.ats CHECK set in 0001_initial.sql (base.BOARD_ATS, which a test keeps in sync with the schema).
SUPPORTED_ATS: frozenset[str] = BOARD_ATS
# The board.found_by CHECK set (plan ruling 6).
FOUND_BY = ("stapply_name", "slug_probe", "careers_page", "import", "hand")


def normalize_token(ats: str, token: str) -> str:
    """The token as a row stores it, or `ValueError`.

    Lower-cases only what the format says: the whole slug, the Workday host and tenant (the site stays as spelled),
    the Oracle host (the site number stays as spelled) and the iCIMS host. Surrounding whitespace is dropped.
    """
    if not isinstance(token, str):
        raise ValueError(f"board token {token!r} is not text")
    text = token.strip()
    if ats in ("workday", "oracle_orc"):
        host, slash, site = text.partition("/")
        text = host.lower() + slash + site
    else:
        text = text.lower()
    if not valid_token(ats, text):
        raise ValueError(f"{text!r} is not a valid {ats!r} board token")
    return text


@dataclass(frozen=True)
class BoardRef:
    """One board: the ATS and the normalized token. A bad ATS name or token raises `ValueError`."""
    ats: str
    token: str

    def __post_init__(self):
        object.__setattr__(self, "token", normalize_token(self.ats, self.token))


@contextlib.contextmanager
def _atomic(conn: sqlite3.Connection):
    """The row, alias and event land together: a transaction of their own, or a savepoint in the caller's."""
    if not conn.in_transaction:
        with transaction(conn):
            yield
        return
    conn.execute("SAVEPOINT add_board")
    try:
        yield
    except BaseException:
        if conn.in_transaction:  # SQLite may have rolled back the whole transaction already
            conn.execute("ROLLBACK TO add_board")
            conn.execute("RELEASE add_board")
        raise
    conn.execute("RELEASE add_board")


def add_board(conn: sqlite3.Connection, company_id: int, ref: BoardRef, *, via: str, matched_on: str,
              found_by: str) -> Literal["added", "exists", "conflict"]:
    """Attach a board to a company.

    `added`: a new `board` row (with `found_by`), the alias `<ats>:<token>` (source `ats`) and a `board` / `found`
    event (actor `crawler`, detail `via` and `matched_on`, key `board-found:<ats>:<token>`), as one unit.
    `exists`: the board already belongs to this company (or to one merged into or from it): nothing is written.
    `conflict`: the board, or its `ats` alias, belongs to another company: the board never moves, and one
    `merge_candidate` item (basis `same_board`) is raised for the pair; a re-run raises nothing new.
    `ValueError` before any write for an ATS outside `SUPPORTED_ATS`, a malformed token or a `found_by` outside
    `FOUND_BY`.
    """
    if found_by not in FOUND_BY:
        raise ValueError(f"found_by {found_by!r} is not one of {FOUND_BY}")
    if ref.ats not in SUPPORTED_ATS:
        raise ValueError(f"ATS {ref.ats!r} is not a supported board ATS")
    token = normalize_token(ref.ats, ref.token)  # a BoardRef changed after construction is still checked
    alias = f"{ref.ats}:{token}"
    with _atomic(conn):
        owner = _owner(conn, ref.ats, token)
        if owner is None:
            cur = conn.execute(
                "INSERT INTO board(company_id, ats, token, found_by, created_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(ats, token) DO NOTHING", (company_id, ref.ats, token, found_by, utcnow()))
            if cur.rowcount:
                add_alias(conn, company_id, alias, "ats")
                append_event(conn, entity="board", kind="found", actor="crawler", entity_id=cur.lastrowid,
                             company_id=company_id, detail={"via": via, "matched_on": matched_on},
                             idempotency_key=f"board-found:{ref.ats}:{token}")
                return "added"
            owner = _owner(conn, ref.ats, token)  # another writer took it between the read and the insert
        if follow_merged(conn, owner) == follow_merged(conn, company_id):
            return "exists"
        raise_merge_candidate(conn, company_id, owner, "same_board", f"both claim the {ref.ats} board {token}")
        return "conflict"


def _owner(conn: sqlite3.Connection, ats: str, token: str) -> int | None:
    """The company that holds the board, or failing that the one whose `ats` alias names it."""
    row = conn.execute("SELECT company_id FROM board WHERE ats = ? AND token = ?", (ats, token)).fetchone()
    if row is None:
        row = conn.execute("SELECT company_id FROM company_alias WHERE alias_key = ?",
                           (f"{ats}:{token}",)).fetchone()
    return row[0] if row else None
