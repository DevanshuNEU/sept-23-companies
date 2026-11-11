"""The job list (#156): open engineering postings at his level, newest posted first, with the facts he checks.

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
- List time (#193, design 4.4): `posting.list_at`, written once by the crawler (`outreach.crawl.row_clock.list_at`).
  NULL means the date is not known: such a row is in the Date unknown group, after every dated row in every window
  and never filtered out by one. The row also carries the board's own time (`posted_at`), the first sighting, the
  seen gap, `first_read` (no seen gap: the board's first read, or a row from before the gap was kept) and, for a
  repost, the first sighting of the posting it repeats (`reposted_from`).
- Reached (#195): `contacted_on` is the newest day he reached the company, a person contacted there (every channel)
  or his own Done mark (`outreach.pipeline.reached`); the not contacted filter hides every company that has one.
- Windows: a dated row is in the 7, 30, 60 or 90 day window when its list time is at or after 00:00 New York that
  many days before today.
- Order: list time newest first, then the smaller company (known small sizes by size, then unknown, then guessed big,
  then known big by size), then posting id; the Date unknown group last, newest first sighting first, then the same.
  `after` is a keyset cursor of `(group, time, size_rank, posting_id)`, the group `dated` or `unknown` (the time is
  the list time, or the first sighting in the unknown group), so a write between two pages never repeats a row.

Reads only; the clock comes in as `now`.
"""
from __future__ import annotations

import base64
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from outreach.pipeline import reached
from outreach.plan.outbound import load_outbound
from outreach.ranking.common import canonical_ids
from outreach.store.timefmt import ISO_FMT, iso_utc

__all__ = ["JobFilters", "JobRow", "JobPage", "query", "passed_posting_ids", "explain", "WINDOWS", "BIG_OVER",
           "BIG_BY_POSTINGS", "ENTERPRISE_ATS", "STATE_SQL", "LISTED_SQL", "listed_params"]

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
# Listed at all (the first rule above), over `p` (the posting) and `d` (its current decision). `:nogo` and `:passed`
# are JSON lists of ids (`listed_params`). The list's one predicate: Check now's new count reads it too (#194).
LISTED_SQL = ("p.closed_at IS NULL AND d.lane = 'engineering' AND d.us <> 'no' AND d.citizenship = 0 "
              "AND d.eligibility = 'ok' AND d.level_tier <> 'senior' "
              "AND p.company_id NOT IN (SELECT value FROM json_each(:nogo)) "
              "AND p.posting_id NOT IN (SELECT value FROM json_each(:passed))")


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
    list_at: str | None  # the time the list sorts by (canonical UTC); None: Date unknown
    posted_at: str | None  # the board's own posting time, when it gives one Knock trusts
    first_seen_at: str  # Knock's first sighting
    seen_gap_s: int | None  # seconds since the board's last good read when Knock first saw it
    first_read: bool  # no seen gap: found on the board's first read
    reposted_from: str | None  # a repost: the first sighting of the posting it repeats
    company_id: int  # the survivor of any merge
    company_name: str
    size: int | None
    big: bool  # known size over 300, or guessed big; size stays null then
    says_no_sponsorship: bool
    everify: bool
    yc: bool
    contacted_on: str | None  # the newest day he reached it: a person contacted there, or his own Done mark


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


# The facts are read once per company (not per posting): `company_fact` is the one view that picks a company's value.
_SQL = """
WITH canon(src, dst) AS (SELECT key, value FROM json_each(:canon)),
cand AS MATERIALIZED (
  SELECT p.posting_id, p.title, p.url, p.location, COALESCE(cn.dst, p.company_id) AS cid,
         d.level_tier, {state} AS state, d.sponsorship, d.us,
         p.list_at, p.posted_at, p.first_seen_at, p.seen_gap_s, rp.first_seen_at AS reposted_from,
         CASE WHEN p.list_at IS NULL THEN 1 ELSE 0 END AS unk, COALESCE(p.list_at, p.first_seen_at) AS at
  FROM posting p
  JOIN decision d ON d.posting_id = p.posting_id
   AND d.ruleset_version = (SELECT ruleset_version FROM ruleset WHERE is_current = 1)
  LEFT JOIN canon cn ON cn.src = p.company_id
  LEFT JOIN posting rp ON rp.posting_id = p.repost_of
  WHERE {listed}
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
  SELECT c.posting_id, c.title, c.url, c.location, c.cid, co.name, co.size, co.everify, co.yc,
         c.list_at, c.posted_at, c.first_seen_at, c.seen_gap_s, c.reposted_from, c.unk, c.at,
         c.level_tier, c.state, c.sponsorship,
         co.big,
         CASE WHEN (c.state = 'fit' OR (c.state = 'uncertain' AND NOT co.big))
                   AND (c.us = 'yes' OR NOT co.big)
              THEN 1 ELSE 0 END AS yl,
         CASE WHEN co.size IS NULL THEN (CASE WHEN co.big_guess = 1 THEN :big_rank ELSE :unknown_rank END)
              WHEN co.size <= :big THEN co.size ELSE :big_rank + co.size END AS rank
  FROM cand c JOIN co ON co.cid = c.cid
  WHERE (c.unk = 1 OR c.list_at >= :cutoff) AND (:q = '' OR lower(co.name) LIKE :like ESCAPE '\\')
    AND CASE :size WHEN 'small' THEN co.size <= :big WHEN 'big' THEN co.big
                   WHEN 'unknown' THEN (co.size IS NULL AND co.big_guess = 0) ELSE 1 END
    AND (:not_contacted = 0 OR c.cid NOT IN (SELECT value FROM json_each(:contacted)))
)
""".replace("{state}", STATE_SQL).replace("{listed}", LISTED_SQL)
_COUNT = _SQL + "SELECT COALESCE(SUM(yl), 0) AS yl, COUNT(*) AS n FROM rows"
# The page and both counts in one pass: the window functions run over every row of the filters, before the view
# and the cursor narrow it to the page.
_PAGE = _SQL + """
SELECT * FROM (SELECT rows.*, SUM(yl) OVER () AS yl_total, COUNT(*) OVER () AS n_total FROM rows)
WHERE yl = :yl AND (:has_after = 0 OR unk > :a_unk OR (unk = :a_unk AND (at < :a_at OR (at = :a_at
      AND (rank > :a_rank OR (rank = :a_rank AND posting_id > :a_id))))))
ORDER BY unk, at DESC, rank, posting_id LIMIT :limit
"""
_GROUPS = ("dated", "unknown")  # the cursor's group segment, by `unk`


def _encode(unk: int, at: str, rank: int, posting_id: int) -> str:
    return base64.urlsafe_b64encode(json.dumps([_GROUPS[unk], at, rank, posting_id]).encode()).decode().rstrip("=")


def _decode(cursor: str) -> tuple[int, str, int, int]:
    try:
        group, at, rank, posting_id = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not (group in _GROUPS and isinstance(at, str) and type(rank) is int and type(posting_id) is int):
            raise ValueError
        datetime.strptime(at, ISO_FMT)
        return _GROUPS.index(group), at, rank, posting_id
    except (ValueError, TypeError, json.JSONDecodeError):  # binascii.Error and UnicodeDecodeError are ValueErrors
        raise ValueError(f"{cursor!r} is not a job list cursor") from None


def _ny_midnight(day: date) -> str:
    """00:00 New York on `day`, as canonical UTC: the first moment of a window."""
    return datetime.combine(day, time(0, 0), tzinfo=_NY).astimezone(timezone.utc).strftime(ISO_FMT)


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


def listed_params(conn: sqlite3.Connection, canon: dict[int, int] | None = None) -> dict:
    """The `:nogo` and `:passed` parameters of `LISTED_SQL`. `canon` defaults to `canonical_ids(conn)`."""
    canon = canonical_ids(conn) if canon is None else canon
    held = {r[0] for r in conn.execute(
        "SELECT company_id FROM no_go WHERE company_id IS NOT NULL AND lifted_at IS NULL")}
    # A company merged into a no-go company is a no-go company too, and so is a merged-away one with its own row.
    nogo = sorted(held | {src for src, dst in canon.items() if dst in held})
    return {"nogo": json.dumps(nogo), "passed": json.dumps(sorted(passed_posting_ids(conn)))}


def _params(conn: sqlite3.Connection, today: date, f: JobFilters, canon: dict[int, int],
            contacted: dict[int, date]) -> dict:
    cutoff = _ny_midnight(today - timedelta(days=f.window_days)) if f.window_days else ""
    return {
        "canon": json.dumps({str(src): dst for src, dst in canon.items() if src != dst}),
        **listed_params(conn, canon),
        "big": BIG_OVER, "big_postings": BIG_BY_POSTINGS, "enterprise": json.dumps(ENTERPRISE_ATS),
        "unknown_rank": _UNKNOWN_RANK, "big_rank": _BIG_RANK, "cutoff": cutoff,
        "q": f.q.strip(), "like": _like(f.q), "size": f.size, "not_contacted": int(f.not_contacted),
        "contacted": json.dumps(sorted(contacted)),
    }


def _contacted(conn: sqlite3.Connection, canon: dict[int, int]) -> dict[int, date]:
    """Each survivor's newest day he reached it: a person reached (every channel, once; `Outbound.reached`, so a sent
    email counts while its follow-ups wait and a first email still in the Outbox does not) or his own mark
    (`reached.marks`), whichever is later."""
    out: dict[int, date] = {}
    for o in load_outbound(conn, canon, tz=_TZ):
        if o.reached and (o.company_id not in out or o.sent_on > out[o.company_id]):
            out[o.company_id] = o.sent_on
    for company, day in reached.marks(conn, canon).items():
        on = date.fromisoformat(day)
        if company not in out or on > out[company]:
            out[company] = on
    return out


def _prepare(conn: sqlite3.Connection, now: str, f: JobFilters, after: str | None, limit: int):
    _check(f, limit)
    cursor = _decode(after) if after is not None else None
    today = _today(now)
    canon = canonical_ids(conn)
    contacted = _contacted(conn, canon)
    params = _params(conn, today, f, canon, contacted)
    page = {**params, "yl": int(f.view == "your_level"), "has_after": int(cursor is not None),
            "a_unk": cursor[0] if cursor else 0, "a_at": cursor[1] if cursor else "",
            "a_rank": cursor[2] if cursor else 0, "a_id": cursor[3] if cursor else 0, "limit": limit + 1}
    return contacted, params, page


def query(conn: sqlite3.Connection, now: str, f: JobFilters, after: str | None, limit: int = 50) -> JobPage:
    """One page of the list. `after` is the cursor of the last row seen (None for the first page); `counts` ignore the
    view, so both tabs can show their number. A bad filter or cursor is a ValueError."""
    contacted, params, page = _prepare(conn, now, f, after, limit)
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    found = cur.execute(_PAGE, page).fetchall()
    if found:
        counts = {"your_level": found[0]["yl_total"], "higher": found[0]["n_total"] - found[0]["yl_total"]}
    else:  # no row in this view or past the last one: the page carries no count, so ask for it
        counted = cur.execute(_COUNT, params).fetchone()
        counts = {"your_level": counted["yl"], "higher": counted["n"] - counted["yl"]}
    more = len(found) > limit
    rows = [_row(r, contacted) for r in found[:limit]]
    last = found[limit - 1] if more else None
    return JobPage(rows, counts[f.view],
                   _encode(last["unk"], last["at"], last["rank"], last["posting_id"]) if last else None, counts)


def _level_tag(state: str, tier: str) -> str:
    if state == "uncertain":
        return _NOT_STATED
    if state == "fit" and tier in ("not_stated", "open"):
        return _ENTRY_TAG
    return _LEVEL_TAGS[tier]


def _row(r: sqlite3.Row, contacted: dict[int, date]) -> JobRow:
    size = r["size"]
    when = contacted.get(r["cid"])
    return JobRow(
        posting_id=r["posting_id"], title=r["title"], url=r["url"], location=r["location"],
        level_tag=_level_tag(r["state"], r["level_tier"]), list_at=r["list_at"], posted_at=r["posted_at"],
        first_seen_at=r["first_seen_at"], seen_gap_s=r["seen_gap_s"], first_read=r["seen_gap_s"] is None,
        reposted_from=r["reposted_from"], company_id=r["cid"], company_name=r["name"],
        size=size, big=bool(r["big"]), says_no_sponsorship=r["sponsorship"] == "no",
        everify=bool(r["everify"]), yc=bool(r["yc"]), contacted_on=when.isoformat() if when else None)


def explain(conn: sqlite3.Connection, now: str, f: JobFilters, after: str | None, limit: int = 50) -> list[str]:
    """SQLite's query plan lines for the page query and the count query (for the plan test and for timing work)."""
    _, params, page = _prepare(conn, now, f, after, limit)
    return ([r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + _PAGE, page)]
            + [r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + _COUNT, params)])
