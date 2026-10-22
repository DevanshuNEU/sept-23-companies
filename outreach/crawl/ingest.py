"""Read one board: the phases that turn one `ReadResult` into store writes (#21, Task 11).

Every phase is its own short write unit and none is open across a request, so a crash anywhere leaves a state the
next read repairs. The order is what keeps the read contract:

1. list the board (an adapter exception other than `HostTripped` or `RulesetNotCurrent` is an `error` read);
2. for an `ok` read, diff the listing with the stored postings and find the list bodies that changed;
2b. the first-read check of a board matched by name or by a guessed slug (ruling 8), before any posting of the
    read is written;
3. write the new and listed postings; then, in units of `decide_batch`, each changed posting's new fields and new
   body together with its decision, so a crash can never leave a title, location or body newer than the decision on
   it (`save_body` resets a replaced body's decision itself);
4. decide every open posting with no current decision (a new posting a crash left undecided);
5. close: `gone_from_board` after an `ok` read only, `board_empty_twice` on the second empty read in a row;
6. fetch missing and due bodies one request at a time (`ok` and `unchanged` reads), storing and deciding each as it
   arrives; a tripped host ends the fetches, not the read;
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

__all__ = ["ReadOutcome", "read_board", "list_board", "DOUBTFUL_JOBS"]

# Ruling 8: every LCA company files 25 or fewer, so a board listing more jobs than this is likely another company's.
DOUBTFUL_JOBS = 300
# Boards found by a name match or a slug guess: either can belong to another company with the same name.
GUESSED_BOARDS = ("stapply_name", "slug_probe")
# Alias sources that hold a raw key, not a name key (identity.py's _RAW_SOURCES).
_RAW_ALIAS_SOURCES = ("fein", "ats", "linkedin")


@dataclass(frozen=True)
class ReadOutcome:
    """What one read did. `interval_s` is the new read interval, None after an `error` read (it keeps the old one).
    `tripped` is the host bucket whose breaker opened during the body fetches (the read itself still counts), so the
    tick defers that bucket's other boards. The plan's `queued` field is gone: the crawler writes no queue row
    (ruling 2)."""
    board_id: int
    result: str
    new: int
    changed: int
    reopened: int
    closed: int
    decided: int
    bodies: int
    interval_s: int | None
    tripped: str | None = None


def read_board(conn: sqlite3.Connection, board: BoardRow, adapter: Adapter, *, run_id: int, now: str,
               cfg: CrawlConfig, rules_cfg=None, combiner=None, bucket: str | None = None) -> ReadOutcome:
    """Read `board` once and write what the read says, in the phase order of this module's docstring.

    `board` is the row from `load_board`; the closing step and the board state both work from it, never from a reload.
    `combiner` is the reader's (ruling 10) and goes to every decision. `HostTripped` from the list request and
    `RulesetNotCurrent` propagate with the board state unwritten, so the caller can defer the board with no streak
    change; `HostTripped` during the body fetches ends them and is reported in `ReadOutcome.tripped`. `bucket` is the
    host bucket the list request went to (the tick knows it); it goes to the board state for the host ruling.
    """
    if conn.in_transaction:
        raise ValueError("read_board sends requests, so it must start with no transaction open")
    result = list_board(adapter, board)
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
    if result.result in ("ok", "empty") and board.found_by in GUESSED_BOARDS and _before_first_good_read(conn, board):
        _check_doubtful(conn, board, result, now)

    if plan is not None:
        # Phase 3: new and listed postings; then each changed field or body with its decision, as one unit.
        ids = write_postings(conn, board, DiffPlan(plan.new, (), plan.present, plan.reopen, ()), now,
                             batch_rows=cfg.batch_rows)
        listed_with_body = {ids[j.native_id] for j in result.jobs if j.body and j.native_id in ids}
        texts = {ids[nid]: text for nid, text in body_updates if nid in ids}
        decided += _apply_changes(conn, board, plan.changed, texts, now, cfg, rules_cfg, combiner)
        new, changed, reopened, bodies = len(plan.new), len(plan.changed), len(plan.reopen), len(texts)
        changed_ids = [pid for pid, _ in plan.changed]
        any_change = bool(plan.new or plan.changed or plan.reopen or plan.gone or texts)

        # Phase 4: the new postings with no list body, and any a crash left undecided.
        decided += _decide(conn, _undecided(conn, board), cfg, rules_cfg, combiner)

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
    tripped = None
    if result.result in ("ok", "unchanged"):
        fill = _backfill(conn, adapter, board, cfg, now, changed_ids, listed_with_body, rules_cfg, combiner)
        bodies += fill.stored
        decided += fill.decided
        any_change = any_change or fill.replaced
        tripped = fill.tripped

    # Phase 7: the board state, last.
    if result.result == "error":
        interval_s = None
        next_read_at = add_seconds(now, error_backoff_s(streaks.error_streak, cfg))
    else:
        interval_s = next_interval_s(last_new_age_s(conn, board, now), board.change_interval_s, any_change, cfg)
        next_read_at = add_seconds(now, interval_s)
    open_now = len(_open_ids(conn, board)) if result.result == "unchanged" else 0  # used for unchanged only
    write_board_state(conn, board, result, run_id=run_id, now=now, open_postings=open_now,
                      next_read_at=next_read_at, interval_s=interval_s, bucket=bucket)
    return ReadOutcome(board_id=board.board_id, result=result.result, new=new, changed=changed, reopened=reopened,
                       closed=closed, decided=decided, bodies=bodies, interval_s=interval_s, tripped=tripped)


def list_board(adapter: Adapter, board: BoardRow) -> ReadResult:
    """The adapter's answer; any failure of the adapter itself is an `error` read (one board's bug never stops a
    tick), except a tripped host and a ruleset change, which say nothing about the board. The tick calls this itself
    first, so it can hold an error read back until it knows whether the host was down (Task 12)."""
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
    error carries no name and no jobs, so the ruling 8 check waits for the first read that does.

    Signs of a good read: an empty run the errors sit in (`empty_streak`), a kept `board_read` row, or a `read_state`
    event to a good state (events are never pruned, `board_read` keeps 10 rows). Postings are no sign: an import
    may have put them on a board that was never read."""
    if board.last_read is None:
        return True
    if board.last_read != "error":
        return False
    if board.empty_streak > 0:
        return False
    seen = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM board_read WHERE board_id = ? AND result IN ('ok', 'empty', 'unchanged')) "
        "OR EXISTS (SELECT 1 FROM event WHERE entity = 'board' AND entity_id = ? AND kind = 'read_state' "
        "AND json_extract(detail, '$.to') IN ('ok', 'empty', 'unchanged'))",
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
    jobs = len({j.native_id for j in result.jobs})
    if jobs > DOUBTFUL_JOBS and conn.execute(
            "SELECT 1 FROM evidence WHERE company_id = ? AND fact = 'lca' LIMIT 1", (board.company_id,)).fetchone():
        why.append(f"it lists {jobs} jobs, more than {DOUBTFUL_JOBS} for a company that files 25 or "
                   "fewer LCAs")
    if not why:
        return
    reason = (f"board match doubtful: the {board.ats} board {board.token} was matched to \"{company}\" "
              f"{'by a guessed slug' if board.found_by == 'slug_probe' else 'by name only'}, and {' and '.join(why)}.")
    with atomic(conn, "board_doubtful"):
        conn.execute(
            "INSERT INTO review_item(kind, company_id, board_id, reason, evidence_key, raised_by, created_at) "
            "VALUES ('flagged', ?, ?, ?, ?, 'crawler', ?) ON CONFLICT DO NOTHING",
            (board.company_id, board.board_id, reason, f"board-doubtful:{board.board_id}", now))


def _open_ids(conn: sqlite3.Connection, board: BoardRow) -> list[int]:
    return [r[0] for r in conn.execute(
        "SELECT posting_id FROM posting WHERE board_id = ? AND closed_at IS NULL ORDER BY posting_id",
        (board.board_id,))]


def _undecided(conn: sqlite3.Connection, board: BoardRow) -> list[int]:
    """Open postings of the board with no current decision: the new ones, and any a crash left undecided. A changed
    field or body never needs this sweep, because it is written in one unit with its decision."""
    return [r[0] for r in conn.execute(
        "SELECT p.posting_id FROM posting p LEFT JOIN current_decision d ON d.posting_id = p.posting_id "
        "WHERE p.board_id = ? AND p.closed_at IS NULL AND d.posting_id IS NULL ORDER BY p.posting_id",
        (board.board_id,))]


def _decide(conn: sqlite3.Connection, posting_ids: list[int], cfg: CrawlConfig, rules_cfg, combiner) -> int:
    if not posting_ids:
        return 0
    return len(decide_postings(conn, posting_ids, batch=cfg.decide_batch, rules_cfg=rules_cfg, combiner=combiner))


def _apply_changes(conn: sqlite3.Connection, board: BoardRow, changed: tuple[tuple[int, JobRef], ...],
                   texts: dict[int, str], now: str, cfg: CrawlConfig, rules_cfg, combiner) -> int:
    """Write each changed posting's listed fields and each new body, and decide those postings, `decide_batch`
    postings per unit: the write and its decision commit together or not at all. Returns how many were decided."""
    fields = dict(changed)
    targets = list(dict.fromkeys([*fields, *texts]))
    decided = 0
    for start in range(0, len(targets), cfg.decide_batch):
        batch = targets[start:start + cfg.decide_batch]
        with atomic(conn, "posting_change"):
            moved = tuple((pid, fields[pid]) for pid in batch if pid in fields)
            if moved:
                write_postings(conn, board, DiffPlan((), moved, (), (), ()), now, batch_rows=cfg.batch_rows)
            for pid in batch:
                if pid in texts:
                    save_body(conn, pid, texts[pid])
            decided += _decide(conn, batch, cfg, rules_cfg, combiner)
    return decided


@dataclass(frozen=True)
class _Backfill:
    stored: int  # bodies stored that were new or different
    replaced: bool  # one of them replaced a stored body
    decided: int
    tripped: str | None  # the bucket whose breaker opened, when one did


def _backfill(conn: sqlite3.Connection, adapter: Adapter, board: BoardRow, cfg: CrawlConfig, now: str,
              changed_ids: list[int], listed_with_body: set[int], rules_cfg, combiner) -> _Backfill:
    """Fetch up to `cfg.detail_per_read` bodies: missing ones first, then the re-fetches of ruling 6 (a changed
    listing, or a level-fit engineering body `body_refetch_days` old). A body the list carried this read is not
    fetched again. Each body that is new or different is stored and decided in one unit as it arrives. A tripped host
    ends the fetches and is reported; what was stored stays."""
    wanted = missing_body_ids(conn, board, cfg.detail_per_read)
    room = cfg.detail_per_read - len(wanted)
    if room > 0:
        skip = listed_with_body | {pid for pid, _ in wanted}
        again = refetch_body_ids(conn, board, [i for i in changed_ids if i not in skip], now, cfg.body_refetch_days,
                                 room + len(skip))
        wanted += [w for w in again if w[0] not in skip][:room]
    stored = decided = 0
    replaced = False

    def keep(posting_id: int, text: str) -> None:
        nonlocal stored, decided, replaced
        with atomic(conn, "fetched_body"):
            before = stored_sha(conn, posting_id)
            if save_body(conn, posting_id, text) == before:
                return
            decided += _decide(conn, [posting_id], cfg, rules_cfg, combiner)
        stored += 1
        replaced = replaced or before is not None

    try:
        fetch_bodies(conn, adapter, board, wanted, on_body=keep)
    except HostTripped as e:
        return _Backfill(stored, replaced, decided, e.bucket)
    return _Backfill(stored, replaced, decided, None)
