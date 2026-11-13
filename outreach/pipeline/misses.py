"""The miss log: why a job link Devanshu pasted was not on his list (#153).

`diagnose` gives the first cause it can find for a link, in this order:

1. The link names no board Knock reads (`urls.board_key` is None): `unknown_site`.
2. Knock holds a posting for it (open or closed, matched as the scorecard matches: `match.py`). The posting's decision
   under the current ruleset says which: `read_not_tech` when the lane is not engineering; `hidden_visa` when it asks
   for citizenship or a clearance (a "no sponsorship" posting is shown with a flag, #154); `read_wrong_level` when
   his level fails (a tier other than `not_stated` or `open`) or the posting excludes him by eligibility (grad
   window, current student); `not_us` when only the US rule hides it; else `found`. `found` means the decision passes (`rules.decide.passes`) and Knock shows the job; a posting
   that fails it for several reasons gets the first cause above. A posting with no decision under the current
   ruleset is `not_read_yet`.
3. Knock has the board (same `(ats, token)`, Workday compared in lower case) but no posting for the job:
   `not_read_yet`. A link to a board with no job lands here too.
4. A company is known by a name or domain the link gives, but has no board: `no_board`. The name is read from the
   board token (the Greenhouse, Ashby or Lever slug; a Workday tenant; an iCIMS host's first label) and compared as a
   name key against company names, their keys and their name aliases. The domain is the link's registrable domain
   against a company's non-guess `domain` evidence. A merged company answers as its survivor. This is a guess at
   the company from a link, not a proof: a slug that differs from the company's name finds nothing.
5. Otherwise `no_company`.

`wrong_date` is set later by a person, never by `diagnose`. `log_miss` stores the diagnosis once per link: a second
paste of the same link returns the first row and writes nothing. It opens no transaction: the caller (`run_write` or
the MCP tool) holds the write.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from outreach.drafts.followups import canonical_company
from outreach.pipeline.match import GREENHOUSE_KEYS_SQL, key_candidates, workday_tokens
from outreach.pipeline.urls import BoardKey, board_key, posting_key
from outreach.rules.decide import passes
from outreach.rules.rebuild import stored_decision
from outreach.sources.keys import registrable_domain
from outreach.store import name_key
from outreach.store.writes import NAME_ALIAS_SOURCES

__all__ = ["CAUSES", "CAUSE_TEXT", "Diagnosis", "Miss", "diagnose", "log_miss"]

CAUSES = ("no_company", "no_board", "not_read_yet", "read_wrong_level", "read_not_tech", "hidden_visa", "not_us",
          "wrong_date", "found", "unknown_site")

CAUSE_TEXT = {
    "no_company": "Knock does not know this company yet.",
    "no_board": "Knock knows the company but has no job board for it yet.",
    "not_read_yet": "Knock has the company's job board but has not read this job yet.",
    "read_wrong_level": "Knock read the job and set it aside as the wrong level for you.",
    "read_not_tech": "Knock read the job and set it aside because it is not an engineering role.",
    "hidden_visa": "Knock read the job and hid it because it asks for citizenship or a clearance.",
    "not_us": "Knock has this job but hides it because the posting is outside the US.",
    "wrong_date": "Knock had the job but showed a wrong date for it.",
    "found": "Knock already has this job and shows it.",
    "unknown_site": "This link is not a job board Knock reads.",
}

_NOT_A_BOARD = "The link names no job board Knock reads."
_TIERS_NOT_HIS = ("not_stated", "open")  # a level Knock shows: it is not stated, or open to any level


@dataclass(frozen=True)
class Diagnosis:
    cause: str
    posting_id: int | None = None
    company_id: int | None = None
    board_id: int | None = None
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Miss:
    miss_id: int
    url: str
    note: str
    pasted_at: str
    cause: str
    posting_id: int | None
    company_id: int | None
    board_id: int | None
    detail: dict

    @property
    def cause_text(self) -> str:
        return CAUSE_TEXT[self.cause]


# ----------------------------------------------------------------------------------------------------- the posting

_POSTING_SQL = ("SELECT p.posting_id, p.company_id, p.board_id, p.source_key, p.first_seen_at, p.closed_at, d.* "
                "FROM posting p LEFT JOIN decision d ON d.posting_id = p.posting_id AND d.ruleset_version = "
                "(SELECT ruleset_version FROM ruleset WHERE is_current = 1) WHERE ")


def _posting_row(conn: sqlite3.Connection, key: BoardKey) -> sqlite3.Row | None:
    """The stored posting a link names, with its current decision's columns (NULL when it has none), or None. The
    earliest first sight wins when two match."""
    if not key.native_id:
        return None
    if key.token is None:  # a bare gh_jid: the Greenhouse key ending in :<native_id>
        ids = [pid for pid, stored in conn.execute(GREENHOUSE_KEYS_SQL) if stored.rsplit(":", 1)[1] == key.native_id]
        if not ids:
            return None
        rows = conn.execute(_POSTING_SQL + f"p.posting_id IN ({','.join('?' * len(ids))})", ids).fetchall()
    else:
        wanted = key_candidates(key.ats, key.token, key.native_id, workday_tokens(conn) if key.ats == "workday" else {})
        lower = posting_key(key)
        rows = [r for r in conn.execute(_POSTING_SQL + f"p.source_key IN ({','.join('?' * len(wanted))})", wanted)
                if r["source_key"].lower() == lower]
    return min(rows, key=lambda r: (r["first_seen_at"], r["posting_id"])) if rows else None


def _judge(row: sqlite3.Row) -> tuple[str, dict]:
    """The cause a stored posting gives, with the decision facts behind it."""
    if row["lane"] is None:
        return "not_read_yet", {"undecided": True}
    d = stored_decision(row)
    facts = {"lane": d.lane, "level_tier": d.level_tier, "level_ok": d.level_ok, "citizenship": d.citizenship,
             "sponsorship": d.sponsorship, "us": d.us, "eligibility": d.eligibility, "passes": passes(d)}
    if d.lane != "engineering":
        return "read_not_tech", facts
    if d.citizenship:
        return "hidden_visa", facts
    if (not d.level_ok and d.level_tier not in _TIERS_NOT_HIS) or d.eligibility != "ok":
        return "read_wrong_level", facts
    if d.us == "no":
        return "not_us", facts
    return "found", facts


# ------------------------------------------------------------------------------------------------ board and company

def _board(conn: sqlite3.Connection, key: BoardKey) -> sqlite3.Row | None:
    if not key.token:
        return None
    # Only Workday tokens keep a case that a link may spell another way; elsewhere the stored token is the link's.
    return conn.execute("SELECT board_id, company_id FROM board WHERE ats = ? AND lower(token) = ? "
                        "ORDER BY board_id LIMIT 1", (key.ats, key.token.lower())).fetchone()


def _candidate_name(key: BoardKey) -> str | None:
    """A company name read from the board token: a Workday tenant (`acme.wd5/External`), an iCIMS host's first label
    (`careers-acme.icims.com`, `i3-corps.jibeapply.com`), a Jibe site's domain (`careers.garmin.com` is `garmin`,
    #220), else the token itself (the Greenhouse, Ashby or Lever slug)."""
    if not key.token:
        return None
    if key.ats == "icims" and not key.token.endswith((".icims.com", ".jibeapply.com")):
        return (registrable_domain(key.token) or key.token).split(".")[0]
    if key.ats in ("workday", "icims"):
        return key.token.split("/")[0].split(".")[0]
    return key.token


def _link_domain(url: str) -> str | None:
    text = url.strip()
    try:
        host = urlsplit(text if "//" in text else "https://" + text).hostname
    except ValueError:
        return None
    return registrable_domain(host)


def _company_by_name(conn: sqlite3.Connection, key: str) -> int | None:
    if not key:
        return None
    row = conn.execute("SELECT company_id FROM company_name_key WHERE name_key = ? ORDER BY strong DESC, company_id "
                       "LIMIT 1", (key,)).fetchone()
    if row is None:
        marks = ", ".join("?" for _ in NAME_ALIAS_SOURCES)
        row = conn.execute(f"SELECT company_id FROM company_alias WHERE alias_key = ? AND source IN ({marks})",
                           (key, *NAME_ALIAS_SOURCES)).fetchone()
    if row is not None:
        return row[0]
    for company_id, name in conn.execute("SELECT company_id, name FROM company ORDER BY company_id"):
        if name_key(name) == key:  # a name under 4 characters has no name key row
            return company_id
    return None


def _company_by_domain(conn: sqlite3.Connection, domain: str | None) -> int | None:
    if not domain:
        return None
    row = conn.execute("SELECT company_id FROM evidence WHERE fact = 'domain' AND value = ? AND confidence <> 'guess' "
                       "ORDER BY company_id LIMIT 1", (domain,)).fetchone()
    return row[0] if row else None


def _company(conn: sqlite3.Connection, key: BoardKey, url: str) -> int | None:
    found = _company_by_name(conn, name_key(_candidate_name(key) or "")) or _company_by_domain(conn, _link_domain(url))
    return canonical_company(conn, found) if found else None


# --------------------------------------------------------------------------------------------------------- diagnose

def diagnose(conn: sqlite3.Connection, url: str) -> Diagnosis:
    """The first cause found for a pasted link, in the order the module text gives. Reads only."""
    key = board_key(url)
    if key is None:
        return Diagnosis("unknown_site", detail={"reason": _NOT_A_BOARD})
    detail = {"ats": key.ats, "token": key.token, "native_id": key.native_id}
    posting = _posting_row(conn, key)
    if posting is not None:
        cause, facts = _judge(posting)
        return Diagnosis(cause, posting["posting_id"], posting["company_id"], posting["board_id"],
                         {**detail, **facts, "closed_at": posting["closed_at"]})
    board = _board(conn, key)
    if board is not None:
        return Diagnosis("not_read_yet", None, board["company_id"], board["board_id"], detail)
    company_id = _company(conn, key, url)
    if company_id is not None:
        return Diagnosis("no_board", company_id=company_id, detail=detail)
    return Diagnosis("no_company", detail=detail)


# ---------------------------------------------------------------------------------------------------------- log_miss

def _miss(row: sqlite3.Row) -> Miss:
    return Miss(row["miss_id"], row["url"], row["note"], row["pasted_at"], row["cause"], row["posting_id"],
                row["company_id"], row["board_id"], json.loads(row["detail"]))


def log_miss(conn: sqlite3.Connection, url: str, note: str, now: str) -> Miss:
    """Store the diagnosis of a link, once: the same link again returns the first row and writes nothing. The caller
    holds the write transaction."""
    link = url.strip()
    row = conn.execute("SELECT * FROM miss WHERE url = ? ORDER BY miss_id LIMIT 1", (link,)).fetchone()
    if row is None:
        got = diagnose(conn, link)
        miss_id = conn.execute(
            "INSERT INTO miss(url, note, pasted_at, cause, detail, posting_id, company_id, board_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (link, note.strip(), now, got.cause, json.dumps(got.detail, sort_keys=True), got.posting_id,
             got.company_id, got.board_id)).lastrowid
        row = conn.execute("SELECT * FROM miss WHERE miss_id = ?", (miss_id,)).fetchone()
    return _miss(row)
