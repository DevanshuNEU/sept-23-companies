"""The job list (#156): open engineering postings at his level, newest first, with the facts he checks.

One read, no score: he reads every posting himself. The current decision is joined by its key with the current
ruleset (never through the `current_decision` view, which SQLite materializes and scans per row), company facts come
from the `company_fact` view for the company the posting belongs to after any merge, and every filter is in SQL, so a
page costs one pass over the open postings and one count, never a query per row.

The list reads the decision's columns:

- Listed at all: open, lane `engineering`, `us <> 'no'`, no citizenship rule, eligibility `ok`, tier not `senior`, the
  company not an active no-go, and the posting not passed (`passed_posting_ids`).
- Big (#172): a company is big when its size is over 300, or its size is unknown and either it has more than 50 open
  postings read as US (counted over the company and every company merged into it) or it has an active Workday or
  Oracle board (`ENTERPRISE_ATS`). The guess is made once per company, in `co`; the row's `size` stays null for it.
- Your level (#154, #172): `level_state = 'fit'`, or `uncertain` at a company that is not big; and at a big company
  (known or guessed) the posting's US read must also be `yes`. Higher levels: every other listed posting (`uncertain`
  at a big company, `fit` at a big company with no US read of yes, `reject`). A row from before migration 0007
  that the rebuild has not reached has no state yet: it falls back to the v1 rule (`fit` when `level_ok = 1`, else
  `uncertain` for tier `not_stated` or `open`, else `reject`), so such a row is listed as it was before #154 and
  changes only when the rebuild gives it a real state.
- The level tag: `fit` and `reject` show the tier (new grad, early career, SDE 1, SDE 2); `uncertain` shows "level not
  stated", whatever its tier; a `fit` posting whose tier is `not_stated` or `open` shows "entry level".
- Date: `posted_on` when there is one (`posted`), else the New York date of the first sighting (`seen`).
- Order: date newest first, then the smaller company (known small sizes by size, then unknown, then guessed big, then
  known big by size), then posting id.
  `after` is a keyset cursor of `(date, size_rank, posting_id)`, so a write between two pages never repeats a row.

Reads only; the clock comes in as `now`.
"""
from __future__ import annotations

import base64
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from outreach.plan.outbound import load_outbound
from outreach.ranking.common import canonical_ids
from outreach.store.timefmt import ISO_FMT, iso_utc

__all__ = ["JobFilters", "JobRow", "JobPage", "query", "passed_posting_ids", "explain", "WINDOWS", "BIG_OVER",
           "BIG_BY_POSTINGS", "ENTERPRISE_ATS", "STATE_SQL"]

WINDOWS = (7, 30, 60, 90, 0)  # days back from today, 0 for any
BIG_OVER = 300  # a company is big above this many people
BIG_BY_POSTINGS = 50  # a company of unknown size is big above this many open postings read as US
ENTERPRISE_ATS = ("workday", "oracle_orc")  # an active board on one of these makes a company of unknown size big
_VIEWS = ("your_level", "higher")
_SIZES = ("any", "small", "big", "unknown")
_TZ = "America/New_York"
_NY = ZoneInfo(_TZ)
# Size ranks: a small company by its size, then unknown, then guessed big, then known big by its size.
_UNKNOWN_RANK = 10_000_000
_BIG_RANK = 20_000_000
_NOT_STATED = "level not stated"
_LEVEL_TAGS = {"new_grad": "new grad", "early_career": "early career", "swe1": "SDE 1", "swe2": "SDE 2",
               "not_stated": _NOT_STATED, "open": _NOT_STATED}
_ENTRY_TAG = "entry level"  # a `fit` posting whose tier names no level (fit by years or entry wording)
_PASS_REASON = "role"
# A decision's level state, with the v1 job list rule for a row the 0007 rebuild has not reached (alerts use it too).
STATE_SQL = ("COALESCE(d.level_state, CASE WHEN d.level_ok = 1 THEN 'fit' "
             "WHEN d.level_tier IN ('not_stated', 'open') THEN 'uncertain' ELSE 'reject' END)")


@dataclass(frozen=True)
class JobFilters:
    view: Literal["your_level", "higher"] = "your_level"
    window_days: int = 30
    size: Literal["any", "small", "big", "unknown"] = "any"
    not_contacted: bool = False
    q: str = ""


@dataclass(frozen=True)
class JobRow:
    posting_id: int
    title: str
    url: str
    location: str
    level_tag: str
    date: str  # YYYY-MM-DD
    date_kind: Literal["posted", "seen"]
    age_days: int  # whole New York days from `date` to today, never below 0
    company_id: int  # the survivor of any merge
    company_name: str
    size: int | None
    big: bool
    says_no_sponsorship: bool
    everify: bool
    yc: bool
    contacted_on: str | None  # the newest day he reached a person there


@dataclass(frozen=True)
class JobPage:
    rows: list[JobRow]
    total: int  # postings in the view, under every filter
    next_after: str | None
    counts: dict[str, int]  # `your_level` and `higher` under every filter but the view


def passed_posting_ids(conn: sqlite3.Connection) -> frozenset[int]:
    """The postings he turned down: a `posting_passed` event (Pass on the job list), or a `pick_passed` event whose
    reason is `role` and that names its posting (Not now, role not for me)."""
    out = set()
    for entity_id, detail in conn.execute(
            "SELECT entity_id, json_extract(detail, '$.posting_id') FROM event "
            "WHERE entity = 'posting' AND kind = 'posting_passed'"):
        pid = detail if detail is not None else entity_id
        if pid is not None:
            out.add(int(pid))
    for (pid,) in conn.execute(
            "SELECT json_extract(detail, '$.posting_id') FROM event WHERE kind = 'pick_passed' "
            "AND entity = 'company' AND json_extract(detail, '$.reason') = ? "
            "AND json_extract(detail, '$.posting_id') IS NOT NULL", (_PASS_REASON,)):
        out.add(int(pid))
    return frozenset(out)


def _ny_day(stamp: str) -> str:
    """The New York date of a stored UTC moment (registered as an SQL function: the first sighting's day)."""
    return datetime.strptime(stamp, ISO_FMT).replace(tzinfo=timezone.utc).astimezone(_NY).date().isoformat()


# The facts are read once per company (not per posting): `company_fact` is the one view that picks a company's value.
_SQL = """
WITH canon(src, dst) AS (SELECT key, value FROM json_each(:canon)),
cand AS MATERIALIZED (
  SELECT p.posting_id, p.title, p.url, p.location, COALESCE(cn.dst, p.company_id) AS cid,
         d.level_tier, {state} AS state, d.sponsorship, d.us,
         COALESCE(p.posted_on, jl_ny_day(p.first_seen_at)) AS day,
         CASE WHEN p.posted_on IS NULL THEN 'seen' ELSE 'posted' END AS kind
  FROM posting p
  JOIN decision d ON d.posting_id = p.posting_id
   AND d.ruleset_version = (SELECT ruleset_version FROM ruleset WHERE is_current = 1)
  LEFT JOIN canon cn ON cn.src = p.company_id
  WHERE p.closed_at IS NULL AND d.lane = 'engineering' AND d.us <> 'no' AND d.citizenship = 0
    AND d.eligibility = 'ok' AND d.level_tier <> 'senior'
    AND p.company_id NOT IN (SELECT value FROM json_each(:nogo))
    AND p.posting_id NOT IN (SELECT value FROM json_each(:passed))
),
-- Open postings read as US per company (merged companies counted into their survivor), once, not per posting.
usn AS MATERIALIZED (
  SELECT COALESCE(cn.dst, p.company_id) AS cid, COUNT(*) AS n
  FROM decision d
  JOIN posting p ON p.posting_id = d.posting_id
  LEFT JOIN canon cn ON cn.src = p.company_id
  WHERE d.ruleset_version = (SELECT ruleset_version FROM ruleset WHERE is_current = 1)
    AND d.us = 'yes' AND p.closed_at IS NULL
  GROUP BY 1
),
-- Companies with an active enterprise board (Workday, Oracle), merged companies counted into their survivor.
ent AS MATERIALIZED (
  SELECT DISTINCT COALESCE(cn.dst, b.company_id) AS cid
  FROM board b LEFT JOIN canon cn ON cn.src = b.company_id
  WHERE b.active = 1 AND b.ats IN (SELECT value FROM json_each(:enterprise))
),
co0 AS MATERIALIZED (
  SELECT x.cid, c.name,
         (SELECT COALESCE(f.num_high, f.num_low) FROM company_fact f
           WHERE f.company_id = x.cid AND f.fact = 'size') AS size,
         EXISTS (SELECT 1 FROM company_fact f WHERE f.company_id = x.cid AND f.fact = 'everify'
                  AND f.value = 'enrolled') AS everify,
         EXISTS (SELECT 1 FROM company_fact f WHERE f.company_id = x.cid AND f.fact = 'stage'
                  AND f.value = 'yc') AS yc
  FROM (SELECT DISTINCT cid FROM cand) x JOIN company c ON c.company_id = x.cid
  WHERE x.cid NOT IN (SELECT value FROM json_each(:nogo))
),
-- big_guess: no size on file and either many open US postings or an enterprise board (once per company).
co1 AS MATERIALIZED (
  SELECT co0.*,
         CASE WHEN co0.size IS NULL AND (COALESCE((SELECT n FROM usn WHERE usn.cid = co0.cid), 0) > :big_postings
                                         OR co0.cid IN (SELECT cid FROM ent)) THEN 1 ELSE 0 END AS big_guess
  FROM co0
),
-- big: over 300 people, or guessed big.
co AS (
  SELECT co1.*, CASE WHEN co1.size > :big OR co1.big_guess = 1 THEN 1 ELSE 0 END AS big FROM co1
),
rows AS (
  SELECT c.posting_id, c.title, c.url, c.location, c.cid, co.name, co.size, co.everify, co.yc, c.day, c.kind,
         c.level_tier, c.state, c.sponsorship,
         co.big,
         CASE WHEN (c.state = 'fit' OR (c.state = 'uncertain' AND NOT co.big))
                   AND (c.us = 'yes' OR NOT co.big)
              THEN 1 ELSE 0 END AS yl,
         CASE WHEN co.size IS NULL THEN (CASE WHEN co.big_guess = 1 THEN :big_rank ELSE :unknown_rank END)
              WHEN co.size <= :big THEN co.size ELSE :big_rank + co.size END AS rank
  FROM cand c JOIN co ON co.cid = c.cid
  WHERE c.day >= :cutoff AND (:q = '' OR lower(co.name) LIKE :like ESCAPE '\\')
    AND CASE :size WHEN 'small' THEN co.size <= :big WHEN 'big' THEN co.big
                   WHEN 'unknown' THEN (co.size IS NULL AND co.big_guess = 0) ELSE 1 END
    AND (:not_contacted = 0 OR c.cid NOT IN (SELECT value FROM json_each(:contacted)))
)
""".replace("{state}", STATE_SQL)
_COUNT = _SQL + "SELECT COALESCE(SUM(yl), 0) AS yl, COUNT(*) AS n FROM rows"
# The page and both counts in one pass: the window functions run over every row of the filters, before the view
# and the cursor narrow it to the page.
_PAGE = _SQL + """
SELECT * FROM (SELECT rows.*, SUM(yl) OVER () AS yl_total, COUNT(*) OVER () AS n_total FROM rows)
WHERE yl = :yl AND (:has_after = 0 OR day < :a_day OR (day = :a_day AND (rank > :a_rank
      OR (rank = :a_rank AND posting_id > :a_id))))
ORDER BY day DESC, rank, posting_id LIMIT :limit
"""


def _encode(day: str, rank: int, posting_id: int) -> str:
    return base64.urlsafe_b64encode(json.dumps([day, rank, posting_id]).encode()).decode().rstrip("=")


def _decode(cursor: str) -> tuple[str, int, int]:
    try:
        day, rank, posting_id = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        date.fromisoformat(day)
        if not (isinstance(day, str) and type(rank) is int and type(posting_id) is int):
            raise ValueError
        return day, rank, posting_id
    except (ValueError, TypeError, json.JSONDecodeError):  # binascii.Error and UnicodeDecodeError are ValueErrors
        raise ValueError(f"{cursor!r} is not a job list cursor") from None


def _today(now: str) -> date:
    stamp = iso_utc(now)
    if stamp is None:
        raise ValueError("the job list needs a time")
    return datetime.strptime(stamp, ISO_FMT).replace(tzinfo=timezone.utc).astimezone(_NY).date()


def _like(q: str) -> str:
    return "%" + re.sub(r"([\\%_])", r"\\\1", q.strip().lower()) + "%"


def _check(f: JobFilters, limit: int) -> None:
    if f.view not in _VIEWS:
        raise ValueError(f"view must be one of {_VIEWS}, got {f.view!r}")
    if f.window_days not in WINDOWS:
        raise ValueError(f"window_days must be one of {WINDOWS}, got {f.window_days!r}")
    if f.size not in _SIZES:
        raise ValueError(f"size must be one of {_SIZES}, got {f.size!r}")
    if limit < 1:
        raise ValueError("limit must be at least 1")


def _params(conn: sqlite3.Connection, today: date, f: JobFilters, canon: dict[int, int],
            contacted: dict[int, date]) -> dict:
    held = {r[0] for r in conn.execute(
        "SELECT company_id FROM no_go WHERE company_id IS NOT NULL AND lifted_at IS NULL")}
    # A company merged into a no-go company is a no-go company too, and so is a merged-away one with its own row.
    nogo = sorted(held | {src for src, dst in canon.items() if dst in held})
    cutoff = (today - timedelta(days=f.window_days)).isoformat() if f.window_days else "0000-00-00"
    return {
        "canon": json.dumps({str(src): dst for src, dst in canon.items() if src != dst}),
        "nogo": json.dumps(nogo), "passed": json.dumps(sorted(passed_posting_ids(conn))),
        "big": BIG_OVER, "big_postings": BIG_BY_POSTINGS, "enterprise": json.dumps(ENTERPRISE_ATS),
        "unknown_rank": _UNKNOWN_RANK, "big_rank": _BIG_RANK, "cutoff": cutoff,
        "q": f.q.strip(), "like": _like(f.q), "size": f.size, "not_contacted": int(f.not_contacted),
        "contacted": json.dumps(sorted(contacted)),
    }


def _contacted(conn: sqlite3.Connection, canon: dict[int, int]) -> dict[int, date]:
    """Each survivor's newest day he reached a person (every channel, once; a first email still in the Outbox is not
    contact yet)."""
    out: dict[int, date] = {}
    for o in load_outbound(conn, canon, tz=_TZ):
        if not o.queued and (o.company_id not in out or o.sent_on > out[o.company_id]):
            out[o.company_id] = o.sent_on
    return out


def _prepare(conn: sqlite3.Connection, now: str, f: JobFilters, after: str | None, limit: int):
    _check(f, limit)
    cursor = _decode(after) if after is not None else None
    today = _today(now)
    canon = canonical_ids(conn)
    contacted = _contacted(conn, canon)
    params = _params(conn, today, f, canon, contacted)
    page = {**params, "yl": int(f.view == "your_level"), "has_after": int(cursor is not None),
            "a_day": cursor[0] if cursor else "", "a_rank": cursor[1] if cursor else 0,
            "a_id": cursor[2] if cursor else 0, "limit": limit + 1}
    conn.create_function("jl_ny_day", 1, _ny_day, deterministic=True)
    return today, contacted, params, page


def query(conn: sqlite3.Connection, now: str, f: JobFilters, after: str | None, limit: int = 50) -> JobPage:
    """One page of the list. `after` is the cursor of the last row seen (None for the first page); `counts` ignore the
    view, so both tabs can show their number. A bad filter or cursor is a ValueError."""
    today, contacted, params, page = _prepare(conn, now, f, after, limit)
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    found = cur.execute(_PAGE, page).fetchall()
    if found:
        counts = {"your_level": found[0]["yl_total"], "higher": found[0]["n_total"] - found[0]["yl_total"]}
    else:  # no row in this view or past the last one: the page carries no count, so ask for it
        counted = cur.execute(_COUNT, params).fetchone()
        counts = {"your_level": counted["yl"], "higher": counted["n"] - counted["yl"]}
    more = len(found) > limit
    rows = [_row(r, today, contacted) for r in found[:limit]]
    last = found[limit - 1] if more else None
    return JobPage(rows, counts[f.view], _encode(last["day"], last["rank"], last["posting_id"]) if last else None,
                   counts)


def _level_tag(state: str, tier: str) -> str:
    if state == "uncertain":
        return _NOT_STATED
    if state == "fit" and tier in ("not_stated", "open"):
        return _ENTRY_TAG
    return _LEVEL_TAGS[tier]


def _row(r: sqlite3.Row, today: date, contacted: dict[int, date]) -> JobRow:
    size = r["size"]
    when = contacted.get(r["cid"])
    return JobRow(
        posting_id=r["posting_id"], title=r["title"], url=r["url"], location=r["location"],
        level_tag=_level_tag(r["state"], r["level_tier"]), date=r["day"], date_kind=r["kind"],
        age_days=max((today - date.fromisoformat(r["day"])).days, 0), company_id=r["cid"], company_name=r["name"],
        size=size, big=bool(r["big"]), says_no_sponsorship=r["sponsorship"] == "no",
        everify=bool(r["everify"]), yc=bool(r["yc"]), contacted_on=when.isoformat() if when else None)


def explain(conn: sqlite3.Connection, now: str, f: JobFilters, after: str | None, limit: int = 50) -> list[str]:
    """SQLite's query plan lines for the page query and the count query (for the plan test and for timing work)."""
    _, _, params, page = _prepare(conn, now, f, after, limit)
    return ([r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + _PAGE, page)]
            + [r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + _COUNT, params)])
