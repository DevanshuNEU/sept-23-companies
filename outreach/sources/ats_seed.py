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
thread. A promoted board gets its company (through identity, so one company per domain) and its board row, which has
no `next_read_at`: the next tick or Check now reads it in full. A rejected, dead or failed gate writes only its
candidate row. The first full seed is Devanshu's call, by hand (`seed-ats`); after one has finished, run-due tops it
up once a week (`top_up_due`), with the limit from `ats_seed.toml`.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
import time
import tomllib
import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from outreach.crawl import fanout
from outreach.crawl.ats import adapter_for
from outreach.crawl.base import ReadResult
from outreach.crawl.config import load_crawl_config
from outreach.crawl.polite import PoliteClient
from outreach.identity import resolve_candidate
from outreach.net import HttpClient
from outreach.rules.config import Config, default_config
from outreach.rules.facts import EvidenceDraft
from outreach.rules.location import us_status
from outreach.rules.text import clean_text
from outreach.rules.title import classify_title
from outreach.sources.base import Candidate
from outreach.sources.boards import BoardRef, add_board
from outreach.sources.keys import classify_domain, registrable_domain
from outreach.sources.stapply import list_rows
from outreach.store import append_event, transaction

__all__ = ["SEED_ATS", "RETRY_DAYS", "CONFIG_PATH", "TOP_UP_DAYS", "Gate", "SeedConfig", "SeedReport", "gate",
           "load_seed_config", "run_seed", "top_up_due"]

SEED_ATS = ("greenhouse", "ashby")
CONFIG_PATH = Path(__file__).with_name("ats_seed.toml")
TOP_UP_DAYS = 7  # run-due tops the seed up when the last seed run is this many days old
EVENT_KIND = "ats_seed_run"
_TIME = "%Y-%m-%dT%H:%M:%SZ"
_SOURCE = "stapply"
_SOURCE_VERSION = "stapply-seed-1"  # only when the seed is the first to name the stapply source; find-boards sets it


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


# A board that failed its gate (no US engineering role, or dead) is read again after this many days.
RETRY_DAYS = load_seed_config().retry_days


@dataclass(frozen=True)
class Gate:
    result: Literal["promoted", "no_us_engineering", "dead", "error"]
    open_us_engineering: int
    board_name: str


def gate(result: ReadResult, rules_cfg: Config) -> Gate:
    """The verdict on one list answer. `error` (and `unchanged`, which a first read never gets) stays `error`: it is
    tried again on the next run and never counts as a rejection. `empty` is `dead`."""
    if result.result == "empty":
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
    role), `dead` (no jobs) and `errors` (the read failed; tried again next run): the rows gated this run. `known`:
    rows whose board the store already has (no request). `left`: rows due but not gated this run (past the limit,
    behind a tripped bucket, or every due row in a dry run). The rest of `read` were gated in the last `RETRY_DAYS`.
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


def _promote(conn: sqlite3.Connection, row: _Row, verdict: Gate, now: str) -> None:
    """The company (identity: the same domain is the same company), the board and the verdict, as one unit."""
    ref = BoardRef(row.ats, row.token)
    key = f"{row.ats}:{row.token}"
    evidence = (EvidenceDraft("domain", row.domain, "estimate", _SOURCE, row.url),) if row.domain else ()
    with transaction(conn):
        conn.execute("INSERT INTO source(source_name, version) VALUES (?, ?) ON CONFLICT(source_name) DO NOTHING",
                     (_SOURCE, _SOURCE_VERSION))
        res = resolve_candidate(conn, Candidate(name=row.name or verdict.board_name or row.token, domain=row.domain,
                                                keys={"ats": key}, evidence=evidence, source_ref=key),
                                source_name=_SOURCE)
        add_board(conn, res.company_id, ref, via="ats_seed", matched_on="gate", found_by="stapply_name",
                  actor="source", now=now)
        found = conn.execute("SELECT board_id FROM board WHERE ats = ? AND token = ?", (ref.ats, ref.token)).fetchone()
        _verdict(conn, row, now, "promoted", verdict.open_us_engineering, found["board_id"] if found else None)


def run_seed(conn: sqlite3.Connection, client: HttpClient, *, folder: Path, now: str,
             ats: Sequence[str] = SEED_ATS, limit: int | None = None, dry_run: bool = False,
             workers: Mapping[str, int] | None = None) -> SeedReport:
    """Gate the boards of the stapply lists in `folder` for each of `ats` (a subset of `SEED_ATS`).

    A row whose board the store already has is `known` and sends nothing (its candidate row says `known` unless it
    was promoted). A row gated in the last `RETRY_DAYS` with a result other than `error` is skipped. The rest are due:
    rows never gated first, then the oldest gate; at most `limit` are gated. A tripped bucket ends its ATS's gating
    for the run; its rows stay as they were and count in `left`. `dry_run` sends nothing and writes nothing.
    `workers` as in `fanout.list_boards` (None lists one by one). A non-dry run ends with one `source` /
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
    rows = _rows(conn, Path(folder), ats, now, dry_run)
    boards, aliases = _known_boards(conn)
    known: list[tuple[_Row, int | None]] = []
    due: list[_Row] = []
    for row in rows:
        if (row.ats, row.token) in boards or f"{row.ats}:{row.token}" in aliases:
            known.append((row, boards.get((row.ats, row.token))))
        elif row.gated_at is None or row.gate_result == "error" or _age_days(now, row.gated_at) >= RETRY_DAYS:
            due.append(row)
    due.sort(key=lambda r: (r.gated_at is not None, r.gated_at or ""))  # stable: list order within each
    if dry_run:
        return SeedReport(read=len(rows), promoted=0, rejected=0, dead=0, errors=0, known=len(known), left=len(due))
    with transaction(conn):
        for row, board_id in known:
            if row.gate_result not in ("promoted", "known"):
                _verdict(conn, row, now, "known", None, board_id)
    gating = due if limit is None else due[:limit]
    counts = {"promoted": 0, "no_us_engineering": 0, "dead": 0, "error": 0}
    left = len(due) - len(gating)
    tripped: set[str] = set()
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
            counts[verdict.result] += 1
            if verdict.result == "promoted":
                _promote(conn, item.board, verdict, now)
            else:
                count = None if verdict.result == "error" else verdict.open_us_engineering
                with transaction(conn):
                    _verdict(conn, item.board, now, verdict.result, count, None)
    report = SeedReport(read=len(rows), promoted=counts["promoted"], rejected=counts["no_us_engineering"],
                        dead=counts["dead"], errors=counts["error"], known=len(known), left=left,
                        tripped=tuple(sorted(tripped)))
    with transaction(conn):
        append_event(conn, entity="source", kind=EVENT_KIND, actor="source", at=now,
                     detail={**asdict(report), "limit": limit, "ats": list(ats)})
    return report


def top_up_due(conn: sqlite3.Connection, now: str) -> str | None:
    """None when run-due should top the seed up, else why not: no full seed (every list, no limit) has finished yet,
    or the last seed run is under `TOP_UP_DAYS` old."""
    runs = [(r["at"], json.loads(r["detail"])) for r in conn.execute(
        "SELECT at, detail FROM event WHERE entity = 'source' AND kind = ? ORDER BY at", (EVENT_KIND,))]
    if not any(d.get("limit") is None and set(d.get("ats") or ()) >= set(SEED_ATS) for _, d in runs):
        return "no full seed has run yet; the first is by hand: python -m outreach.sources seed-ats"
    last = runs[-1][0]
    if _age_days(now, last) < TOP_UP_DAYS:
        return f"the last seed ran {last}"
    return None
