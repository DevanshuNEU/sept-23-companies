"""The ATS seed (#192): every Greenhouse and Ashby company the stapply lists name that has an open US engineering role.

`gate` reads one list answer and decides from it alone: no store, no clock, no network. A board is promoted when at
least one open job is US engineering, by the rules' own checks on what a list answer carries: the title's lane
(`classify_title`, which reads a title with no body as engineering unless it is off lane) and the US reading of the
location and country (`us_status`, the same call `decide` makes). A job read as outside the US (`no`) does not count;
`yes` and `unknown` do, as on the job list (`us <> 'no'`), so a remote or unplaced role is not missed. A list answer
has no body, so the level, years and sponsorship checks are not run: the gate asks only whether a US engineering role
is open.

`run_seed` takes the rows of the stapply Greenhouse and Ashby lists (never `wellfound` or `ycombinator`), keeps one
`board_candidate` row per board, and gates each one with one list request. The requests go through the crawler's
`PoliteClient` (the crawl config's spacing and breaker) and `fanout.list_boards`; every write happens on the calling
thread, under the crawler's `crawl.lock`, so the seed never sends alongside a tick. A promoted board joins the one
stored company whose name has the row's `board_match_key` when that key is on at most `MAX_HITS` rows (as find-boards
guards a stapply hit), else identity makes or finds its company (see `_promote`); its board row has no
`next_read_at`, so the next tick or Check now reads it in full. A rejected,
dead or failed gate writes only its candidate row. The first full seed is Devanshu's call, by hand (`seed-ats`); after
one has finished, run-due tops it up once a week, after find-boards (`top_up_due`), with the limit from
`ats_seed.toml`.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
import time
import tomllib
import urllib.parse
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from outreach.crawl import fanout
from outreach.crawl.ats import adapter_for
from outreach.crawl.base import ReadResult
from outreach.crawl.config import load_crawl_config
from outreach.crawl.polite import PoliteClient
from outreach.crawl.tick import _store_lock  # crawl.lock beside the store file; none for a store with no file
from outreach.identity import resolve_candidate
from outreach.net import HttpClient
from outreach.rules.config import Config, default_config
from outreach.rules.facts import EvidenceDraft
from outreach.rules.location import us_status
from outreach.rules.text import clean_text
from outreach.review.play import ny_date
from outreach.rules.title import classify_title
from outreach.sources.base import Candidate
from outreach.sources.boards import BoardRef, add_board
from outreach.sources.jobs import _company_names  # the names find-boards matches a stapply row on
from outreach.sources.keys import classify_domain, registrable_domain
from outreach.sources.stapply import MAX_HITS, MIN_KEY, board_match_key, list_rows
from outreach.store import append_event, transaction

__all__ = ["SEED_ATS", "CONFIG_PATH", "DEAD_STATUSES", "TOP_UP_DAYS", "Gate", "SeedConfig",
           "SeedReport", "gate", "load_seed_config", "run_seed", "top_up_due"]

SEED_ATS = ("greenhouse", "ashby")
CONFIG_PATH = Path(__file__).with_name("ats_seed.toml")
TOP_UP_DAYS = 7  # run-due tops the seed up when the last seed run is this many New York days old
EVENT_KIND = "ats_seed_run"
_TIME = "%Y-%m-%dT%H:%M:%SZ"
_SOURCE = "stapply"
_SOURCE_VERSION = "stapply-seed-1"  # only when the seed is the first to name the stapply source; find-boards sets it
# An error read with one of these statuses is about the board (gone, closed), as the tick's `_BOARD_STATUSES` reads
# it: the board is dead, never a failed read to try again on the next run.
DEAD_STATUSES = frozenset({404, 410})


@dataclass(frozen=True)
class SeedConfig:
    enabled: bool
    weekly_limit: int
    retry_days: int

    def __post_init__(self):
        if not isinstance(self.enabled, bool):
            raise ValueError(f"enabled must be true or false, got {self.enabled!r}")
        for key in ("weekly_limit", "retry_days"):
            value = getattr(self, key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{key} must be a whole number of at least 1, got {value!r}")


def load_seed_config(path: Path = CONFIG_PATH) -> SeedConfig:
    data = tomllib.loads(Path(path).read_bytes().decode("utf-8"))
    for key in ("enabled", "weekly_limit", "retry_days"):
        if key not in data:
            raise KeyError(f"{key} is missing from {Path(path).name}")
    return SeedConfig(enabled=data["enabled"], weekly_limit=data["weekly_limit"], retry_days=data["retry_days"])


@dataclass(frozen=True)
class Gate:
    result: Literal["promoted", "no_us_engineering", "dead", "error"]
    open_us_engineering: int
    board_name: str


def gate(result: ReadResult, rules_cfg: Config) -> Gate:
    """The verdict on one list answer. `empty` is `dead`, and so is an `error` whose HTTP status is 404 or 410
    (`DEAD_STATUSES`: the board is gone, so it waits `retry_days` like any other verdict). Any other `error` (and
    `unchanged`, which a first read never gets) stays `error`: it is tried again on the next run and never counts as a
    rejection."""
    if result.result == "empty" or (result.result == "error" and result.http_status in DEAD_STATUSES):
        return Gate("dead", 0, "")
    if result.result != "ok":
        return Gate("error", 0, "")
    count = sum(1 for job in result.jobs if _is_us_engineering(job.title, job.location, job.country, rules_cfg))
    return Gate("promoted" if count else "no_us_engineering", count, result.board_name)


def _is_us_engineering(title: str, location: str, country: str | None, cfg: Config) -> bool:
    title, location = clean_text(title), clean_text(location)
    country = None if country is None else clean_text(country)
    return classify_title(title, cfg).lane == "engineering" and us_status(location, country, cfg, title) != "no"


# --- the seed run ------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SeedReport:
    """One run. `read`: list rows with a valid board (each board once). `promoted`, `rejected` (no US engineering
    role), `dead` (no jobs, or a 404 or 410) and `errors` (any other failed read; tried again next run): the rows
    gated this run. `known`: rows whose board the store already has (no request). `left`: rows due but not gated this
    run (past the limit, behind a tripped bucket, or every due row in a dry run). The rest of `read` were gated in the
    last `retry_days`.
    `tripped` names the buckets whose breaker opened: their ATS's gating ended there."""
    read: int
    promoted: int
    rejected: int
    dead: int
    errors: int
    known: int
    left: int
    tripped: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Row:
    """A list row on its way through `fanout.list_boards`. It is not a board yet, so it is no `BoardRow`: the list
    call reads only `ats` and `token` (a full read sends no validators, so `etag` and `ids_fingerprint` are never
    read)."""
    candidate_id: int | None  # None in a dry run, which writes no candidate row
    ats: str
    token: str
    name: str
    url: str
    domain: str | None
    gated_at: str | None
    gate_result: str | None


def _domain(url: str) -> str | None:
    """The row URL's registrable domain when it is a company's own (a careers page), never an ATS host."""
    root = registrable_domain((urllib.parse.urlsplit(url).hostname or "").lower())
    return root if root and classify_domain(root) == "company" else None


def _age_days(now: str, then: str) -> float:
    return (datetime.strptime(now, _TIME) - datetime.strptime(then, _TIME)) / timedelta(days=1)


def _rows(conn: sqlite3.Connection, folder: Path, ats: Sequence[str], now: str, dry_run: bool) -> list[_Row]:
    """Every list row as a candidate. Outside a dry run the rows not yet in `board_candidate` are added first (one
    transaction), so each row has its id."""
    listed = [(name, row) for name in ats for row in list_rows(folder, name)]
    if not dry_run:
        with transaction(conn):
            conn.executemany(
                "INSERT INTO board_candidate(ats, token, name, url, domain, source, source_ref, found_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(ats, token) DO NOTHING",
                [(r.ref.ats, r.ref.token, r.name, r.url, _domain(r.url), _SOURCE, f"{name}.csv", now)
                 for name, r in listed])
    stored = {(r["ats"], r["token"]): r for r in conn.execute(
        "SELECT candidate_id, ats, token, gated_at, gate_result FROM board_candidate WHERE ats IN "
        f"({', '.join('?' for _ in ats)})", tuple(ats))}
    out = []
    for _, r in listed:
        row = stored.get((r.ref.ats, r.ref.token))
        out.append(_Row(row["candidate_id"] if row else None, r.ref.ats, r.ref.token, r.name, r.url, _domain(r.url),
                        row["gated_at"] if row else None, row["gate_result"] if row else None))
    return out


def _known_boards(conn: sqlite3.Connection) -> tuple[dict[tuple[str, str], int], set[str]]:
    """The store's boards (`(ats, token)` -> board id) and its `ats` aliases (a board find-boards has yet to make):
    the two ways `store_board_known` knows a board, read once."""
    boards = {(r["ats"], r["token"]): r["board_id"] for r in conn.execute("SELECT board_id, ats, token FROM board")}
    aliases = {k for (k,) in conn.execute("SELECT alias_key FROM company_alias WHERE source = 'ats'")}
    return boards, aliases


def _verdict(conn: sqlite3.Connection, row: _Row, now: str, result: str, count: int | None,
             board_id: int | None) -> None:
    """The gate's verdict in one UPDATE: the table ties `gated_at` and `gate_result` together."""
    conn.execute("UPDATE board_candidate SET gated_at = ?, gate_result = ?, open_us_engineering = ?, board_id = ? "
                 "WHERE candidate_id = ?", (now, result, count, board_id, row.candidate_id))


class _NotAdded(Exception):
    """`add_board` did not add the board (another writer has it since the run read the store): undo the unit."""


def _name_owners(conn: sqlite3.Connection) -> dict[str, dict[int, str]]:
    """`board_match_key` -> {company id: status} for the stored companies (not merged, not removed) with a name,
    legal name or dba of that key: the key find-boards matches a stapply row on. A key under `MIN_KEY` characters
    never matches."""
    owners: dict[str, dict[int, str]] = defaultdict(dict)
    for r in conn.execute("SELECT company_id, name, status FROM company "
                          "WHERE merged_into IS NULL AND status <> 'removed'"):
        for name in _company_names(conn, r["company_id"], r["name"]):
            key = board_match_key(name)
            if len(key) >= MIN_KEY:
                owners[key][r["company_id"]] = r["status"]
    return owners


def _promote(conn: sqlite3.Connection, row: _Row, verdict: Gate, now: str, owners: dict[str, dict[int, str]],
             key_rows: Counter) -> bool:
    """The board, its company and the verdict, as one unit. Returns whether the board was added.

    The row's stapply name key decides the company, as find-boards guards a stapply hit. A key some closed company
    holds makes the row `known`: the seed never revives or duplicates a company he closed. A key on at most `MAX_HITS`
    rows of the lists being seeded, held by exactly one stored company, joins it (`found_by` `stapply_name`).
    Otherwise identity makes or finds the company, named as the board names itself when the gate saw a name, else as
    the list does; a company it makes from this board gets `found_by` `import` (no name guess). The `owners` were read
    before the gating, so a company the seed makes never takes a later row of its name: that row goes through
    identity, which raises its weak-name merge item. When `add_board` adds nothing (the board is someone else's by
    now), the unit is undone and the row is recorded `known`."""
    ref = BoardRef(row.ats, row.token)
    ats_key = f"{row.ats}:{row.token}"
    key = board_match_key(row.name)
    held = owners.get(key, {}) if len(key) >= MIN_KEY else {}
    if "closed" in held.values():
        with transaction(conn):
            _verdict(conn, row, now, "known", None, None)
        return False
    joined = next(iter(held)) if len(held) == 1 and key_rows[key] <= MAX_HITS else None
    try:
        with transaction(conn):
            conn.execute("INSERT INTO source(source_name, version) VALUES (?, ?) "
                         "ON CONFLICT(source_name) DO NOTHING", (_SOURCE, _SOURCE_VERSION))
            if joined is not None:
                company_id, found_by = joined, "stapply_name"
                conn.execute(
                    "INSERT INTO company_source(company_id, source_name, source_ref, first_seen_at, last_seen_at) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING", (company_id, _SOURCE, ats_key, now, now))
            else:
                name = verdict.board_name.strip() or row.name or row.token
                evidence = (EvidenceDraft("domain", row.domain, "estimate", _SOURCE, row.url),) if row.domain else ()
                res = resolve_candidate(conn, Candidate(name=name, domain=row.domain, keys={"ats": ats_key},
                                                        evidence=evidence, source_ref=ats_key), source_name=_SOURCE)
                company_id, found_by = res.company_id, "import" if res.created else "stapply_name"
            if add_board(conn, company_id, ref, via="ats_seed", matched_on="name" if joined is not None else "gate",
                         found_by=found_by, actor="source", now=now) != "added":
                raise _NotAdded
            _verdict(conn, row, now, "promoted", verdict.open_us_engineering, _board_id(conn, ref))
    except _NotAdded:
        with transaction(conn):
            _verdict(conn, row, now, "known", None, _board_id(conn, ref))
        return False
    return True


def _board_id(conn: sqlite3.Connection, ref: BoardRef) -> int | None:
    found = conn.execute("SELECT board_id FROM board WHERE ats = ? AND token = ?", (ref.ats, ref.token)).fetchone()
    return found["board_id"] if found else None


def _plan(conn: sqlite3.Connection, folder: Path, ats: tuple[str, ...], now: str, retry_days: int,
          dry_run: bool) -> tuple[list[_Row], list[tuple[_Row, int | None]], list[_Row]]:
    """(every row, the known rows with their board id, the due rows in gating order)."""
    rows = _rows(conn, folder, ats, now, dry_run)
    boards, aliases = _known_boards(conn)
    known: list[tuple[_Row, int | None]] = []
    due: list[_Row] = []
    for row in rows:
        if (row.ats, row.token) in boards or f"{row.ats}:{row.token}" in aliases:
            known.append((row, boards.get((row.ats, row.token))))
        elif row.gated_at is None or row.gate_result == "error" or _age_days(now, row.gated_at) >= retry_days:
            due.append(row)
    due.sort(key=lambda r: (r.gated_at is not None, r.gated_at or ""))  # stable: list order within each
    return rows, known, due


def run_seed(conn: sqlite3.Connection, client: HttpClient, *, folder: Path, now: str,
             ats: Sequence[str] = SEED_ATS, limit: int | None = None, dry_run: bool = False,
             workers: Mapping[str, int] | None = None, config: SeedConfig | None = None,
             say: Callable[[str], None] | None = None) -> SeedReport:
    """Gate the boards of the stapply lists in `folder` for each of `ats` (a subset of `SEED_ATS`).

    A row whose board the store already has is `known` and sends nothing (its candidate row says `known` unless it
    was promoted). A row gated in the last `retry_days` (`config`, default `ats_seed.toml`) with a result other than
    `error` is skipped. The rest are due: rows never gated first, then the oldest gate; at most `limit` are gated. A
    tripped bucket ends its ATS's gating for the run; its rows stay as they were and count in `left`. `dry_run` sends
    nothing and writes nothing. Otherwise the run holds the crawler's `crawl.lock` from its first write to its last,
    so it never sends alongside a tick or a Check now: `AlreadyRunning`, before any write, when one holds it.
    `workers` as in `fanout.list_boards` (None lists one by one). `say` gets one line before the gating (and in a
    dry run): the due rows per list, the known rows and the limit. A non-dry run ends with one `source` /
    `ats_seed_run` event holding the report, the limit and the lists read. `ValueError` for an ATS outside
    `SEED_ATS`, a limit under 1, a missing list or an open transaction (the run sends requests)."""
    ats = tuple(ats)
    unknown = sorted(set(ats) - set(SEED_ATS))
    if unknown or not ats:
        raise ValueError(f"the seed reads only the {' and '.join(SEED_ATS)} lists, not {unknown or 'none'}")
    if limit is not None and limit < 1:
        raise ValueError(f"limit must be at least 1, got {limit}")
    if conn.in_transaction:
        raise ValueError("the seed sends requests, so it must start with no transaction open")
    retry_days = (config or load_seed_config()).retry_days
    if dry_run:
        rows, known, due = _plan(conn, Path(folder), ats, now, retry_days, dry_run=True)
        if say is not None:
            say(_start_line(ats, rows, known, due, limit, dry_run=True))
        return SeedReport(read=len(rows), promoted=0, rejected=0, dead=0, errors=0, known=len(known), left=len(due))
    with _store_lock(conn):
        rows, known, due = _plan(conn, Path(folder), ats, now, retry_days, dry_run=False)
        if say is not None:
            say(_start_line(ats, rows, known, due, limit, dry_run=False))
        return _gate_rows(conn, client, rows, known, due, now=now, ats=ats, limit=limit, workers=workers)


def _start_line(ats: tuple[str, ...], rows: list[_Row], known: list, due: list[_Row], limit: int | None, *,
                dry_run: bool) -> str:
    per = Counter(r.ats for r in due)
    text = (f"{len(due)} due ({', '.join(f'{a} {per[a]}' for a in ats)}) of {len(rows)} rows read, "
            f"{len(known)} known")
    if dry_run:
        return text + "; dry run: nothing is sent or written"
    return text + (f"; gating at most {limit}" if limit is not None else "; gating them all")


def _gate_rows(conn: sqlite3.Connection, client: HttpClient, rows: list[_Row], known: list[tuple[_Row, int | None]],
               due: list[_Row], *, now: str, ats: tuple[str, ...], limit: int | None,
               workers: Mapping[str, int] | None) -> SeedReport:
    with transaction(conn):
        for row, board_id in known:
            if row.gate_result not in ("promoted", "known"):
                _verdict(conn, row, now, "known", None, board_id)
    gating = due if limit is None else due[:limit]
    counts = {"promoted": 0, "no_us_engineering": 0, "dead": 0, "error": 0, "known": len(known)}
    left = len(due) - len(gating)
    tripped: set[str] = set()
    owners: dict[str, dict[int, str]] | None = None  # read at the first promotion, then never added to
    key_rows = Counter(board_match_key(r.name) for r in rows)  # how many rows of these lists carry each name key
    polite = client if isinstance(client, PoliteClient) else PoliteClient(client, load_crawl_config())
    rules_cfg = default_config()
    listed = fanout.list_boards(gating, lambda row: (adapter_for(row.ats, polite), None), lambda row: True,
                                workers=workers, should_stop=lambda: False, clock=time.monotonic)
    with contextlib.closing(listed):  # a raise stops the workers now
        for item in listed:
            if item.result is None:  # the breaker refused it (or no adapter reads the ATS): not gated this run
                left += 1
                if item.tripped:
                    tripped.add(item.tripped)
                continue
            verdict = gate(item.result, rules_cfg)
            if verdict.result == "promoted":
                if owners is None:
                    owners = _name_owners(conn)
                counts["promoted" if _promote(conn, item.board, verdict, now, owners, key_rows) else "known"] += 1
                continue
            counts[verdict.result] += 1
            count = None if verdict.result == "error" else verdict.open_us_engineering
            with transaction(conn):
                _verdict(conn, item.board, now, verdict.result, count, None)
    report = SeedReport(read=len(rows), promoted=counts["promoted"], rejected=counts["no_us_engineering"],
                        dead=counts["dead"], errors=counts["error"], known=counts["known"], left=left,
                        tripped=tuple(sorted(tripped)))
    with transaction(conn):
        append_event(conn, entity="source", kind=EVENT_KIND, actor="source", at=now,
                     detail={**asdict(report), "limit": limit, "ats": list(ats)})
    return report


def top_up_due(conn: sqlite3.Connection, now: str, config: SeedConfig | None = None) -> str | None:
    """None when run-due should top the seed up, else why not: the top-up is off (`enabled = false`; `config`,
    default `ats_seed.toml`), no full seed (every list, no limit) has finished yet, or the New York date of the last
    seed run is under `TOP_UP_DAYS` days before today's."""
    if not (config or load_seed_config()).enabled:
        return "the weekly seed top-up is off (enabled = false in ats_seed.toml)"
    runs = [(r["at"], json.loads(r["detail"])) for r in conn.execute(
        "SELECT at, detail FROM event WHERE entity = 'source' AND kind = ? ORDER BY at", (EVENT_KIND,))]
    if not any(d.get("limit") is None and set(d.get("ats") or ()) >= set(SEED_ATS) for _, d in runs):
        return "no full seed has run yet; the first is by hand: python -m outreach.sources seed-ats"
    last = runs[-1][0]
    days = (datetime.fromisoformat(ny_date(now)) - datetime.fromisoformat(ny_date(last))).days
    if days < TOP_UP_DAYS:
        return f"the last seed ran {last}"
    return None
