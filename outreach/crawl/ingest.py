"""Read one board: the phases that turn one `ReadResult` into store writes (#21, Task 11).

Every phase is its own short write unit and none is open across a request, so a crash anywhere leaves a state the
next read repairs. The order is what keeps the read contract:

1. list the board (an adapter exception other than `HostTripped` or `RulesetNotCurrent` is an `error` read);
2. for an `ok` read, diff the listing with the stored postings and find the list bodies that changed;
2b. the first-read check of a name-only board (ruling 8), before any posting of the read is written;
3. write the postings, then their bodies (`save_body` resets a replaced body's decision itself);
4. decide the new, changed and body-updated postings, and every open one with no current decision or with a body
   newer than its decision (what a crash between phases leaves);
5. close: `gone_from_board` after an `ok` read only, `board_empty_twice` on the second empty read in a row;
6. fetch missing and due bodies one request at a time (`ok` and `unchanged` reads), storing each as it arrives;
7. write the board state last: streaks, validators, `board_read`, review items and the next read time.

Because the board state is the last write, a crash before it keeps the old validators and `last_read`: the next read
lists the board again and every phase reruns without writing anything twice. The crawler writes no `read_queue` row
(ruling 2): #32 owns the queue.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from outreach.crawl.base import Adapter, JobRef, ReadResult, Validators
from outreach.crawl.bodies import BODY_LANES, fetch_bodies, missing_body_ids, refetch_body_ids, save_body, stored_sha
from outreach.crawl.boardstate import BoardRow, write_board_state
from outreach.crawl.cadence import add_seconds, error_backoff_s, last_new_age_s, next_interval_s
from outreach.crawl.config import CrawlConfig
from outreach.crawl.decisions import decide_postings
from outreach.crawl.polite import HostTripped
from outreach.crawl.postings import (
    REOPENABLE, DiffPlan, Existing, close_postings, load_existing, plan_diff, source_key, write_postings)
from outreach.crawl.streaks import closes_postings, next_streaks
from outreach.rules import RulesetNotCurrent
from outreach.rules.config import default_config
from outreach.rules.title import classify_title
from outreach.store import atomic, body_sha256, describe_error, name_key

__all__ = ["ReadOutcome", "read_board", "DOUBTFUL_JOBS"]

# Ruling 8: every LCA company files 25 or fewer, so a board listing more jobs than this is likely another company's.
DOUBTFUL_JOBS = 300
# Alias sources that hold a raw key, not a name key (identity.py's _RAW_SOURCES).
_RAW_ALIAS_SOURCES = ("fein", "ats", "linkedin")


@dataclass(frozen=True)
class ReadOutcome:
    """What one read did. `interval_s` is the new read interval, None after an `error` read (it keeps the old one).
    The plan's `queued` field is gone: the crawler writes no queue row (ruling 2)."""
    board_id: int
    result: str
    new: int
    changed: int
    reopened: int
    closed: int
    decided: int
    bodies: int
    interval_s: int | None


def read_board(conn: sqlite3.Connection, board: BoardRow, adapter: Adapter, *, run_id: int, now: str,
               cfg: CrawlConfig, rules_cfg=None, combiner=None) -> ReadOutcome:
    """Read `board` once and write what the read says, in the phase order of this module's docstring.

    `board` is the row from `load_board`; the closing step and the board state both work from it, never from a reload.
    `combiner` is the reader's (ruling 10) and goes to every decision. `HostTripped` and `RulesetNotCurrent`
    propagate with the board state unwritten, so the caller can defer the board with no streak change.
    """
    if conn.in_transaction:
        raise ValueError("read_board sends requests, so it must start with no transaction open")
    result = _list(adapter, board)
    title_cfg = rules_cfg if rules_cfg is not None else default_config()
    new = changed = reopened = closed = decided = bodies = 0
    any_change = False
    listed_with_body: set[int] = set()
    changed_ids: list[int] = []

    # Phase 2: the diff and the list bodies, read only.
    plan: DiffPlan | None = None
    body_updates: list[tuple[str, str]] = []
    if result.result == "ok":
        existing = load_existing(conn, board.board_id)
        plan = plan_diff(existing, result.jobs, board.ats, board.token)
        body_updates = _body_updates(conn, board, existing, result.jobs, title_cfg)

    # Phase 2b: the first-read check, before any posting of this read is written.
    if result.result in ("ok", "empty") and board.found_by == "stapply_name" and _before_first_good_read(conn, board):
        _check_doubtful(conn, board, result, now)

    if plan is not None:
        # Phase 3: postings, then the bodies that changed.
        ids = write_postings(conn, board, plan, now, batch_rows=cfg.batch_rows)
        listed_with_body = {ids[j.native_id] for j in result.jobs if j.body and j.native_id in ids}
        body_ids = []
        for native_id, text in body_updates:
            posting_id = ids.get(native_id)
            if posting_id is not None:
                save_body(conn, posting_id, text)
                body_ids.append(posting_id)
        new, changed, reopened, bodies = len(plan.new), len(plan.changed), len(plan.reopen), len(body_ids)
        changed_ids = [pid for pid, _ in plan.changed]
        any_change = bool(plan.new or plan.changed or plan.reopen or plan.gone or body_ids)

        # Phase 4: decisions for what changed, and for what a crash left undecided.
        new_ids = [ids[j.native_id] for j in plan.new if j.native_id in ids]
        decided += _decide(conn, [*new_ids, *changed_ids, *body_ids], board, cfg, rules_cfg, combiner)

        # Phase 5a: a posting the ok read no longer lists is gone.
        closed = close_postings(conn, plan.gone, "gone_from_board", now, batch_rows=cfg.batch_rows)

    # Phase 5b: the second empty read in a row closes every open posting. The streaks come from the same BoardRow
    # write_board_state uses below, so the two can never disagree.
    streaks = next_streaks(board.last_read, board.empty_streak, board.error_streak, board.streak_since,
                           result.result, now)
    if closes_postings(result.result, streaks.empty_streak):
        closed = close_postings(conn, _open_ids(conn, board), "board_empty_twice", now, batch_rows=cfg.batch_rows)
        any_change = any_change or closed > 0

    # Phase 6: bodies the list did not carry, one request at a time, each stored as it arrives. An empty board lists
    # nothing to fetch, and an error read says nothing about its postings.
    if result.result in ("ok", "unchanged"):
        fetched, replaced = _backfill(conn, adapter, board, cfg, now, changed_ids, listed_with_body)
        bodies += len(fetched)
        any_change = any_change or replaced
        decided += _decide(conn, fetched, board, cfg, rules_cfg, combiner)

    # Phase 7: the board state, last.
    if result.result == "error":
        interval_s = None
        next_read_at = add_seconds(now, error_backoff_s(streaks.error_streak, cfg))
    else:
        interval_s = next_interval_s(last_new_age_s(conn, board, now), board.change_interval_s, any_change, cfg)
        next_read_at = add_seconds(now, interval_s)
    write_board_state(conn, board, result, run_id=run_id, now=now, open_postings=len(_open_ids(conn, board)),
                      next_read_at=next_read_at, interval_s=interval_s)
    return ReadOutcome(board_id=board.board_id, result=result.result, new=new, changed=changed, reopened=reopened,
                       closed=closed, decided=decided, bodies=bodies, interval_s=interval_s)


def _list(adapter: Adapter, board: BoardRow) -> ReadResult:
    """The adapter's answer; any failure of the adapter itself is an `error` read (one board's bug never stops a
    tick), except a tripped host and a ruleset change, which say nothing about the board."""
    try:
        return adapter.list(board.token, Validators(board.etag, board.ids_fingerprint))
    except (HostTripped, RulesetNotCurrent):
        raise
    except Exception as e:
        return ReadResult("error", (), Validators(), error=f"the {board.ats} adapter failed: {describe_error(e)}")


def _body_updates(conn: sqlite3.Connection, board: BoardRow, existing: dict[str, Existing], jobs: tuple[JobRef, ...],
                  title_cfg) -> list[tuple[str, str]]:
    """`(native_id, text)` for each listed body of an engineering or intern title that is new or differs from the
    stored one by more than whitespace. A posting closed for a reason the board cannot undo gets nothing."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for job in jobs:
        if job.native_id in seen or not job.body:
            continue
        seen.add(job.native_id)
        if classify_title(job.title, title_cfg).lane not in BODY_LANES:
            continue
        e = existing.get(source_key(board.ats, board.token, job.native_id))
        if e is None:
            out.append((job.native_id, job.body))
        elif e.closed_at is None or e.close_reason in REOPENABLE:
            if stored_sha(conn, e.posting_id) != body_sha256(job.body):
                out.append((job.native_id, job.body))
    return out


def _before_first_good_read(conn: sqlite3.Connection, board: BoardRow) -> bool:
    """True while the board has had no `ok`, `empty` or `unchanged` read: never read, or only errors so far. An
    error carries no name and no jobs, so the ruling 8 check waits for the first read that does."""
    if board.last_read is None:
        return True
    if board.last_read != "error":
        return False
    seen = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM posting WHERE board_id = ?) "
        "OR EXISTS (SELECT 1 FROM board_read WHERE board_id = ? AND result IN ('ok', 'empty', 'unchanged'))",
        (board.board_id, board.board_id)).fetchone()[0]
    return not seen


def _company_name_keys(conn: sqlite3.Connection, company_id: int) -> set[str]:
    """Every name key the company (and any company merged into it) is known by: its name, its company_name_key rows
    and its name-like aliases."""
    family = "SELECT company_id FROM company WHERE company_id = :c OR merged_into = :c"
    marks = ", ".join(f"'{s}'" for s in _RAW_ALIAS_SOURCES)  # fixed plain words
    keys = {name_key(r[0]) for r in conn.execute(f"SELECT name FROM company WHERE company_id IN ({family})",
                                                 {"c": company_id})}
    keys.update(r[0] for r in conn.execute(
        f"SELECT name_key FROM company_name_key WHERE company_id IN ({family})", {"c": company_id}))
    keys.update(r[0] for r in conn.execute(
        f"SELECT alias_key FROM company_alias WHERE company_id IN ({family}) AND source NOT IN ({marks})",
        {"c": company_id}))
    keys.discard("")
    return keys


def _check_doubtful(conn: sqlite3.Connection, board: BoardRow, result: ReadResult, now: str) -> None:
    """Ruling 8: raise one `flagged` item when the board's org name is none of the company's names, or a company with
    LCA evidence (a filer of 25 or fewer) has a board of over DOUBTFUL_JOBS jobs. Once per board: a repeat is a
    no-op, so a resolved item stays resolved."""
    company = conn.execute("SELECT name FROM company WHERE company_id = ?", (board.company_id,)).fetchone()[0]
    why = []
    if result.board_name and name_key(result.board_name) not in _company_name_keys(conn, board.company_id):
        why.append(f'the board calls itself "{result.board_name}", which is none of the names of "{company}"')
    if len(result.jobs) > DOUBTFUL_JOBS and conn.execute(
            "SELECT 1 FROM evidence WHERE company_id = ? AND fact = 'lca' LIMIT 1", (board.company_id,)).fetchone():
        why.append(f"it lists {len(result.jobs)} jobs, more than {DOUBTFUL_JOBS} for a company that files 25 or "
                   "fewer LCAs")
    if not why:
        return
    reason = (f"board match doubtful: the {board.ats} board {board.token} was matched to \"{company}\" by name "
              f"only, and {' and '.join(why)}.")
    with atomic(conn, "board_doubtful"):
        conn.execute(
            "INSERT INTO review_item(kind, company_id, board_id, reason, evidence_key, raised_by, created_at) "
            "VALUES ('flagged', ?, ?, ?, ?, 'crawler', ?) ON CONFLICT DO NOTHING",
            (board.company_id, board.board_id, reason, f"board-doubtful:{board.board_id}", now))


def _open_ids(conn: sqlite3.Connection, board: BoardRow) -> list[int]:
    return [r[0] for r in conn.execute(
        "SELECT posting_id FROM posting WHERE board_id = ? AND closed_at IS NULL ORDER BY posting_id",
        (board.board_id,))]


def _needs_decision(conn: sqlite3.Connection, board: BoardRow) -> list[int]:
    """Open postings of the board with no current decision, or whose body was stored after their decision was made:
    what a crash between a write and its decision leaves behind."""
    return [r[0] for r in conn.execute(
        "SELECT p.posting_id FROM posting p "
        "LEFT JOIN current_decision d ON d.posting_id = p.posting_id "
        "LEFT JOIN posting_body b ON b.posting_id = p.posting_id "
        "WHERE p.board_id = ? AND p.closed_at IS NULL "
        "AND (d.posting_id IS NULL OR (b.fetched_at IS NOT NULL AND d.decided_at < b.fetched_at)) "
        "ORDER BY p.posting_id", (board.board_id,))]


def _decide(conn: sqlite3.Connection, posting_ids: list[int], board: BoardRow, cfg: CrawlConfig, rules_cfg,
            combiner) -> int:
    ids = list(dict.fromkeys([*posting_ids, *_needs_decision(conn, board)]))
    if not ids:
        return 0
    return len(decide_postings(conn, ids, batch=cfg.decide_batch, rules_cfg=rules_cfg, combiner=combiner))


def _backfill(conn: sqlite3.Connection, adapter: Adapter, board: BoardRow, cfg: CrawlConfig, now: str,
              changed_ids: list[int], listed_with_body: set[int]) -> tuple[list[int], bool]:
    """Fetch up to `cfg.detail_per_read` bodies: missing ones first, then the re-fetches of ruling 6 (a changed
    listing, or a level-fit engineering body `body_refetch_days` old). A body the list carried this read is not
    fetched again. Returns the postings whose stored body changed, and whether any of them replaced a body."""
    wanted = missing_body_ids(conn, board, cfg.detail_per_read)
    room = cfg.detail_per_read - len(wanted)
    if room > 0:
        skip = listed_with_body | {pid for pid, _ in wanted}
        again = refetch_body_ids(conn, board, [i for i in changed_ids if i not in skip], now, cfg.body_refetch_days,
                                 room + len(skip))
        wanted += [w for w in again if w[0] not in skip][:room]
    stored: list[int] = []
    replaced = False

    def keep(posting_id: int, text: str) -> None:
        nonlocal replaced
        before = stored_sha(conn, posting_id)
        if save_body(conn, posting_id, text) != before:
            stored.append(posting_id)
            replaced = replaced or before is not None

    fetch_bodies(conn, adapter, board, wanted, on_body=keep)
    return stored, replaced
