"""The pipeline scorecard: how Knock measures up against SimplifyJobs' new grad list (#153).

`compute` reads the store and returns the numbers. It writes nothing. `record` stores one card a New York day.

The window is the feed listings that are `active` and carry a `posted_hint` in the last 30 days. A listing is covered
when Knock holds a posting with the listing's `posting_key`, or, for a Greenhouse link on a company's own site (a
`gh_jid` and no board token), a Greenhouse posting whose key ends in `:<native_id>`. The feed's hint is read here and
nowhere else: it never dates a posting.

How the postings are found (the store holds about 360,000, the window about 1,800 listings):
- A key query by `source_key IN (...)`, 500 keys at a time, which searches the unique index. The crawler's keys are
  not all lower case (Workday paths and Workable shortcodes keep their case), and `lower(source_key)` cannot use the
  index, so each listing offers two candidates: its key as built from the link, and that key lower-cased
  (`urls.posting_key`). A Workday listing offers a third, built with the stored board's token (a board keeps its
  site segment's case, a link may not). A returned key is matched to its listing by lower case.
- A bare `gh_jid` listing has no token to build a key from. One range query over the `greenhouse:` keys (index only,
  about 40,000 of them) finds the keys ending in the native id. `LIKE` would not use the index, and one query per
  listing is what this avoids.
- Then one query per 500 posting ids reads each posting's first sight and its current decision. The decision is joined
  by its key under the current ruleset, as the crawler's `_undecided` does, not through the `current_decision` view.
"""
from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from outreach.crawl.postings import source_key
from outreach.outcomes.windows import ny_day
from outreach.pipeline.urls import BoardKey, posting_key
from outreach.store import append_event, iso_utc, name_key
from outreach.store.timefmt import ISO_FMT
from outreach.store.writes import NAME_ALIAS_SOURCES

__all__ = ["GapCompany", "Scorecard", "compute", "record"]

SOURCE = "simplify"
WINDOW_DAYS = 30
DETECTION_DAYS = 7
BOARD_AGE_DAYS = 2  # a board must be older than this, before the posting's date, for its lag to count
DETECTION_ATS = ("greenhouse", "ashby", "lever")  # the ATSs whose `posted_on` is a real posting date
GAP_TOP = 15
CHUNK = 500
EVENT_KIND = "pipeline_scorecard"

# Rows of `posting` and the current decision, found by key or by id. `{marks}` is a run of `?`.
_FROM = ("FROM posting p LEFT JOIN decision d ON d.posting_id = p.posting_id "
         "AND d.ruleset_version = (SELECT ruleset_version FROM ruleset WHERE is_current = 1) ")
_COLUMNS = "SELECT p.posting_id, p.source_key, p.first_seen_at, d.level_ok, d.level_tier "
POSTING_BY_KEY_SQL = _COLUMNS + _FROM + "WHERE p.source_key IN ({marks})"
POSTING_BY_ID_SQL = _COLUMNS + _FROM + "WHERE p.posting_id IN ({marks})"
_GREENHOUSE_KEYS_SQL = "SELECT posting_id, source_key FROM posting WHERE source_key >= 'greenhouse:' AND source_key < 'greenhouse;'"


@dataclass(frozen=True)
class GapCompany:
    """Uncovered listings of one company on one ATS. `ats` is None for links that name no board Knock reads."""
    company_name: str
    ats: str | None
    listings: int
    knock_knows_company: bool
    knock_has_board: bool


@dataclass(frozen=True)
class Scorecard:
    """`simplify_coverage`, `level_recall` and `lead_over_simplify` are shares from 0 to 1, None when there is nothing
    to divide. `detection_p95_hours` is hours from a posting's date to Knock's first sight. `date_truth` and
    `small_company_coverage` are not measured yet."""
    simplify_listings: int
    simplify_coverage: float | None
    level_recall: float | None
    lead_over_simplify: float | None
    detection_p95_hours: float | None
    date_truth: None = None
    small_company_coverage: None = None
    gap_top: list[GapCompany] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class _Listing:
    ats: str | None
    token: str | None
    native_id: str | None
    company_name: str
    first_seen_at: str
    backfill: int


@dataclass(frozen=True)
class _Posting:
    first_seen_at: str
    level_ok: int | None
    level_tier: str | None


def _chunks(items: list, size: int = CHUNK):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _stamp(now: str, days: int) -> str:
    moment = datetime.strptime(iso_utc(now), ISO_FMT).replace(tzinfo=timezone.utc)
    return (moment - timedelta(days=days)).strftime(ISO_FMT)


def _ratio(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def _window(conn: sqlite3.Connection, now: str) -> list[_Listing]:
    rows = conn.execute(
        "SELECT ats, token, native_id, company_name, first_seen_at, backfill FROM feed_listing "
        "WHERE source = ? AND active = 1 AND posted_hint >= ? ORDER BY feed_id", (SOURCE, _stamp(now, WINDOW_DAYS)))
    return [_Listing(*row) for row in rows]


def _workday_tokens(conn: sqlite3.Connection) -> dict[str, str]:
    """Stored Workday board tokens by lower-cased token. A board keeps its site segment's case (`acme.wd5/External`)
    and a link may spell it another way."""
    return {token.lower(): token for (token,) in conn.execute("SELECT token FROM board WHERE ats = 'workday'")}


def _key_candidates(listing: _Listing, workday: dict[str, str]) -> tuple[str, ...]:
    """The `source_key` a listing's link names, in the link's case and lower-cased, and for Workday also with the
    stored board's token; () when the link names no job. The path's case is the link's own: a link whose path case
    differs from the stored one is not found."""
    if not listing.ats or not listing.token or not listing.native_id:
        return ()
    exact = source_key(listing.ats, listing.token, listing.native_id)
    found = [exact, exact.lower()]
    stored = workday.get(listing.token.lower()) if listing.ats == "workday" else None
    if stored:
        found.append(source_key("workday", stored, listing.native_id))
    return tuple(dict.fromkeys(found))


def _by_keys(conn: sqlite3.Connection, keys: list[str]) -> dict[str, _Posting]:
    """Postings by lower-cased `source_key`, for the keys asked (in either case)."""
    found: dict[str, _Posting] = {}
    for chunk in _chunks(keys):
        sql = POSTING_BY_KEY_SQL.format(marks=", ".join("?" for _ in chunk))
        for row in conn.execute(sql, chunk):
            found.setdefault(row["source_key"].lower(), _Posting(row["first_seen_at"], row["level_ok"], row["level_tier"]))
    return found


def _greenhouse_by_native(conn: sqlite3.Connection, native_ids: set[str]) -> dict[str, _Posting]:
    """For listings with a `gh_jid` and no board token: the Greenhouse posting ending in `:<native_id>`, by native id.
    The earliest first sight wins when two boards hold the same number."""
    by_id: dict[int, str] = {}
    for posting_id, key in conn.execute(_GREENHOUSE_KEYS_SQL):
        native = key.rsplit(":", 1)[1]
        if native in native_ids:
            by_id[posting_id] = native
    found: dict[str, _Posting] = {}
    for chunk in _chunks(list(by_id)):
        sql = POSTING_BY_ID_SQL.format(marks=", ".join("?" for _ in chunk))
        for row in conn.execute(sql, chunk):
            native, posting = by_id[row["posting_id"]], _Posting(row["first_seen_at"], row["level_ok"], row["level_tier"])
            if native not in found or posting.first_seen_at < found[native].first_seen_at:
                found[native] = posting
    return found


def _match(conn: sqlite3.Connection, listings: list[_Listing]) -> list[_Posting | None]:
    """The posting each listing is covered by, or None, in order."""
    workday = _workday_tokens(conn) if any(x.ats == "workday" for x in listings) else {}
    wanted = list(dict.fromkeys(k for listing in listings for k in _key_candidates(listing, workday)))
    by_key = _by_keys(conn, wanted)
    bare = {x.native_id for x in listings if x.ats == "greenhouse" and not x.token and x.native_id}
    by_native = _greenhouse_by_native(conn, bare) if bare else {}
    out: list[_Posting | None] = []
    for listing in listings:
        if listing.ats == "greenhouse" and not listing.token and listing.native_id:
            out.append(by_native.get(listing.native_id))
            continue
        key = posting_key(BoardKey(listing.ats, listing.token, listing.native_id)) if listing.ats else None
        out.append(by_key.get(key) if key else None)
    return out


def _level_ok(posting: _Posting) -> bool:
    return posting.level_ok == 1 or posting.level_tier in ("not_stated", "open")


def _detection_p95(conn: sqlite3.Connection, now: str) -> float | None:
    """The 95th percentile (nearest rank) of hours from a posting's date, taken as midnight UTC, to Knock's first
    sight of it. Postings on Greenhouse, Ashby and Lever with a `posted_on` in the last 7 days, on boards created more
    than 2 days before that date: a board added after the posting went up cannot have seen it any sooner."""
    marks = ", ".join("?" for _ in DETECTION_ATS)
    since = _stamp(now, DETECTION_DAYS)[:10]
    rows = conn.execute(
        "SELECT (julianday(p.first_seen_at) - julianday(p.posted_on)) * 24 FROM posting p "
        f"JOIN board b ON b.board_id = p.board_id WHERE b.ats IN ({marks}) AND p.posted_on >= ? AND p.posted_on <= ? "
        "AND julianday(b.created_at) < julianday(p.posted_on) - ?",
        (*DETECTION_ATS, since, iso_utc(now)[:10], BOARD_AGE_DAYS))
    hours = sorted(r[0] for r in rows)
    if not hours:
        return None
    return round(hours[math.ceil(0.95 * len(hours)) - 1], 1)


def _gap_top(conn: sqlite3.Connection, uncovered: list[_Listing]) -> list[GapCompany]:
    groups: dict[tuple[str, str | None], list[_Listing]] = defaultdict(list)
    for listing in uncovered:
        groups[(name_key(listing.company_name), listing.ats)].append(listing)
    # Most listings first; ties by name so the list does not move between runs.
    ranked = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[1][0].company_name.lower(), kv[0][1] or ""))
    top = ranked[:GAP_TOP]
    known = _known_keys(conn, {key for (key, _), _ in top})
    gap = []
    for (key, ats), rows in top:
        tokens = {r.token.lower() for r in rows if r.token}
        has_board = bool(ats) and any(  # tokens compare in lower case: a Workday board keeps its site's case
            conn.execute("SELECT 1 FROM board WHERE ats = ? AND lower(token) = ?", (ats, t)).fetchone() for t in tokens)
        gap.append(GapCompany(rows[0].company_name, ats, len(rows), key in known, has_board))
    return gap


def _known_keys(conn: sqlite3.Connection, keys: set[str]) -> set[str]:
    """Which of the name keys a company in the store answers to: its name keys, a name alias, or its own name."""
    keys = {k for k in keys if k}
    if not keys:
        return set()
    ask = list(keys)
    marks = ", ".join("?" for _ in ask)
    known = {r[0] for r in conn.execute(f"SELECT name_key FROM company_name_key WHERE name_key IN ({marks})", ask)}
    sources = ", ".join("?" for _ in NAME_ALIAS_SOURCES)
    known |= {r[0] for r in conn.execute(
        f"SELECT alias_key FROM company_alias WHERE alias_key IN ({marks}) AND source IN ({sources})",
        (*ask, *NAME_ALIAS_SOURCES))}
    if known != keys:  # a name under 4 characters has no name key row; compare the company names themselves
        known |= {name_key(r[0]) for r in conn.execute("SELECT name FROM company")} & keys
    return known


def compute(conn: sqlite3.Connection, now: str) -> Scorecard:
    """The scorecard as of `now`. Reads only."""
    listings = _window(conn, now)
    matched = _match(conn, listings)
    covered = [(x, p) for x, p in zip(listings, matched) if p is not None]
    fresh = [(x, p) for x, p in covered if not x.backfill]
    return Scorecard(
        simplify_listings=len(listings),
        simplify_coverage=_ratio(len(covered), len(listings)),
        level_recall=_ratio(sum(_level_ok(p) for _, p in covered), len(covered)),
        lead_over_simplify=_ratio(sum(p.first_seen_at < x.first_seen_at for x, p in fresh), len(fresh)),
        detection_p95_hours=_detection_p95(conn, now),
        gap_top=_gap_top(conn, [x for x, p in zip(listings, matched) if p is None]),
    )


def record(conn: sqlite3.Connection, card: Scorecard, now: str) -> None:
    """Store the card as one `pipeline_scorecard` event, keyed by the New York day: a second run that day writes
    nothing."""
    append_event(conn, entity="score", kind=EVENT_KIND, actor="system", detail=card.to_dict(), at=now,
                 idempotency_key=f"scorecard:{ny_day(now)}")
