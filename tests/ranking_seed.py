"""Seeds the ranking fixture into a store (#6, Task 13): the employers of `tests/fixtures/ranking/employers.json` and
their postings in `postings.json`, then a real `rebuild` so every decision comes from the rules, then the `provisional`
flags the fixture sets.

`seed_ranking_fixture(conn, employers_path, postings_path)` returns {employer key: company id}. Every board is read
`ok` on 2026-10-21 and every posting last seen then, unless the employer or posting row says otherwise. The two
file shapes:

Employer row keys: key, name, status, hq, lca (list of fiscal-year rows), facts (list of evidence rows: fact, value,
confidence, observed_on, num_low, num_high, locked, source_kind), boards (list: read, read_at, found_by, ats,
doubtful), people (list: name, role_kind, email), no_go ({reason, lifted}), merged_into (an employer key).
Posting row keys: employer, board (index into the employer's boards, or null), title, location, country, url,
posted_on, pay, body, first_seen_at, last_seen_at, provisional (0 makes the current decision final).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from outreach.rules import rebuild

__all__ = ["FIXTURE_DIR", "EMPLOYERS", "POSTINGS", "READ_AT", "seed_ranking_fixture", "fixture_employers",
           "fixture_ids"]

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "ranking"
EMPLOYERS = FIXTURE_DIR / "employers.json"
POSTINGS = FIXTURE_DIR / "postings.json"

READ_AT = "2026-10-21T08:00:00Z"
CREATED_AT = "2026-09-01T08:00:00Z"
RECORDED_AT = "2026-10-01T08:00:00Z"
NEVER_DUE = "2027-01-01T08:00:00Z"  # a fixture board is never due, so a crawl tick in a flow never reads it
FIRST_SEEN = "2026-10-15T08:00:00Z"
_SOURCE_KINDS = {"lca": "lca", "hq": "lca", "staffing_risk": "lca", "everify": "everify", "funding": "formd"}


def fixture_employers(path: Path = EMPLOYERS) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def fixture_ids(conn: sqlite3.Connection, employers_path: Path = EMPLOYERS) -> dict[str, int]:
    """{key: company id} of the fixture employers already in the store; a key with no company is left out."""
    ids = {}
    for e in fixture_employers(employers_path):
        row = conn.execute("SELECT company_id FROM company WHERE name = ?", (e["name"],)).fetchone()
        if row is not None:
            ids[e["key"]] = row[0]
    return ids


def _board(conn: sqlite3.Connection, company_id: int, key: str, n: int, b: dict) -> int:
    read = b.get("read", "ok")
    read_at = b.get("read_at", READ_AT) if read is not None else None
    empty = b.get("empty_streak", 1 if read == "empty" else 0)
    error = b.get("error_streak", 1 if read == "error" else 0)
    return conn.execute(
        "INSERT INTO board(company_id, ats, token, active, last_read, last_read_at, empty_streak, error_streak, "
        "streak_since, found_by, next_read_at, created_at) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)",
        (company_id, b.get("ats", "greenhouse"), f"fixture-{key.lower()}-{n}", read, read_at, empty, error, read_at,
         b.get("found_by", "import"), NEVER_DUE, CREATED_AT)).lastrowid


def _evidence(conn: sqlite3.Connection, company_id: int, fact: str, value, *, confidence: str = "confirmed",
              observed_on: str | None = None, num_low: int | None = None, num_high: int | None = None,
              locked: int = 0, source_kind: str | None = None) -> None:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    conn.execute(
        "INSERT INTO evidence(company_id, fact, value, num_low, num_high, confidence, locked, source_kind, "
        "source_ref, observed_on, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'fixture', ?, ?)",
        (company_id, fact, text, num_low, num_high, confidence, locked,
         source_kind or ("hand" if locked else _SOURCE_KINDS.get(fact, "other")), observed_on, RECORDED_AT))


def seed_ranking_fixture(conn: sqlite3.Connection, employers_path: Path = EMPLOYERS,
                         postings_path: Path = POSTINGS) -> dict[str, int]:
    """Inserts the fixture, decides every posting with the real rules and applies the fixture's `provisional` flags.
    Returns {employer key: company id}."""
    employers = fixture_employers(employers_path)
    postings = json.loads(Path(postings_path).read_text(encoding="utf-8"))
    ids: dict[str, int] = {}
    boards: dict[str, list[int]] = {}
    for e in employers:
        cid = conn.execute("INSERT INTO company(name, status, status_at, created_at) VALUES (?, ?, ?, ?)",
                           (e["name"], e.get("status", "candidate"), CREATED_AT, CREATED_AT)).lastrowid
        ids[e["key"]] = cid
        boards[e["key"]] = [_board(conn, cid, e["key"], n, b) for n, b in enumerate(e.get("boards", []))]
        for row in e.get("lca", []):
            _evidence(conn, cid, "lca", row, observed_on="2026-06-30")
        if e.get("hq"):
            _evidence(conn, cid, "hq", e["hq"], confidence="estimate")
        for f in e.get("facts", []):
            _evidence(conn, cid, f["fact"], f["value"], confidence=f.get("confidence", "confirmed"),
                      observed_on=f.get("observed_on"), num_low=f.get("num_low"), num_high=f.get("num_high"),
                      locked=f.get("locked", 0), source_kind=f.get("source_kind"))
        for p in e.get("people", []):
            conn.execute(
                "INSERT INTO person(company_id, name, role_kind, email, channel, added_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'email', 'import', ?, ?)",
                (cid, p["name"], p["role_kind"], p["email"], CREATED_AT, CREATED_AT))
        for n, b in enumerate(e.get("boards", [])):
            if b.get("doubtful"):
                conn.execute(
                    "INSERT INTO review_item(kind, company_id, board_id, reason, evidence_key, raised_by, created_at) "
                    "VALUES ('flagged', ?, ?, ?, 'fixture', 'crawler', ?)",
                    (cid, boards[e["key"]][n], f"board match doubtful: {b['doubtful']}", READ_AT))
        nogo = e.get("no_go")
        if nogo:
            lifted = bool(nogo.get("lifted"))
            conn.execute(
                "INSERT INTO no_go(company_id, reason, source, created_at, lifted_at, lift_reason) "
                "VALUES (?, ?, 'devanshu', ?, ?, ?)",
                (cid, nogo.get("reason", "no_sponsorship"), CREATED_AT, "2026-10-01T08:00:00Z" if lifted else None,
                 "he said yes" if lifted else None))
    for e in employers:
        if e.get("merged_into"):
            conn.execute("UPDATE company SET status = 'removed', remove_reason = 'duplicate', merged_into = ? "
                         "WHERE company_id = ?", (ids[e["merged_into"]], ids[e["key"]]))

    posting_ids: list[tuple[int, dict]] = []
    for n, p in enumerate(postings):
        cid = ids[p["employer"]]
        board_no = p.get("board")
        board_id = boards[p["employer"]][board_no] if board_no is not None else None
        last_seen = p.get("last_seen_at", READ_AT)
        first_seen = min(p.get("first_seen_at", FIRST_SEEN), last_seen)
        key = f"fixture:{p['employer']}:{n}" if board_id is not None else f"url:fixture-{p['employer']}-{n}"
        pid = conn.execute(
            "INSERT INTO posting(company_id, board_id, source_key, title, location, country, url, posted_on, pay, "
            "first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, board_id, key, p["title"], p.get("location", ""), p.get("country"), p.get("url", ""),
             p.get("posted_on"), p.get("pay", ""), first_seen, last_seen)).lastrowid
        if p.get("body"):
            conn.execute("INSERT INTO posting_body(posting_id, body) VALUES (?, ?)", (pid, p["body"]))
        posting_ids.append((pid, p))

    rebuild(conn)  # every decision comes from the real rules; each starts provisional
    version = conn.execute("SELECT ruleset_version FROM ruleset WHERE is_current = 1").fetchone()[0]
    for pid, p in posting_ids:
        if p.get("provisional") == 0:
            conn.execute("UPDATE decision SET provisional = 0 WHERE posting_id = ? AND ruleset_version = ?",
                         (pid, version))
    return ids
