"""The `rank` job: score every listed company and write the result in one transaction (#6, Task 11).

`compute_rank` is the read-only half. It reads the store in bulk (a few queries over whole tables, never one per
company) inside one read transaction, so every loader sees the same snapshot and no write lock is held. `run_rank`
then writes, in one `BEGIN IMMEDIATE` transaction: the rules `no_go` rows for final sponsorship refusals, the
version's full set of `company_score` rows (rows of the version for companies not in the run are deleted), the
ranker's review items, the `weights_changed` event of a version change (ruling 8), the prune of old versions and
the job's `ok`. Any exception rolls all of it back, closes the run `failed` and re-raises, so the previous rows and
the no-go table are untouched. A dry run writes nothing, not even a `job_run`.

Staleness is the crawler's rule: `outreach.crawl.stale.load_stale` is called once per run and its mapping is passed
to `load_roles` and `load_hide_inputs`. Because stale depends on `today`, a run on a later day may change rows; the
same day over the same store never does.
"""
from __future__ import annotations

import dataclasses
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from outreach.crawl import stale as crawl_stale
from outreach.ranking.common import canonical_ids, doubtful_board_ids
from outreach.ranking.config import RankerConfig, default_ranker_config
from outreach.ranking.facts import load_facts
from outreach.ranking.hide import Hide, HideInputs, decide_hide, load_hide_inputs, write_no_go
from outreach.ranking.pay import load_wages
from outreach.ranking.review import sync_conflict_items, sync_size_items
from outreach.ranking.roles import load_roles
from outreach.ranking.score import Scored, score_company
from outreach.ranking.scores import latest_version, prune_versions, write_scores
from outreach.ranking.signals import build_signals
from outreach.ranking.version import ranker_version
from outreach.rules import RulesetNotCurrent
from outreach.rules.version import ruleset_version
from outreach.store import append_event, describe_error, finish_job, start_job, transaction, utcnow
from outreach.store.timefmt import ISO_FMT

__all__ = ["CONFLICT_FLAG", "HIDDEN_KINDS", "RankRun", "compute_rank", "run_rank", "weights_text"]

CONFLICT_FLAG = "postings disagree on sponsorship"
# The summary's hidden counts: the three hide kinds, and a provisional hide (pending reader) as its own count.
HIDDEN_KINDS = ("no_go", "over_size", "no_sponsorship", "provisional")
_LABELS = {"everify": "E-Verify"}
_TEXT_MAX_CHANGES = 3


@dataclass(frozen=True)
class RankRun:
    version: str
    scored: tuple[Scored, ...]
    hidden: dict[str, int]      # by hide kind; a provisional hide counts as `provisional`
    no_go_written: int
    size_items: int             # size_guess and size_check items raised or reopened by this run
    conflict_items: int         # sponsorship-conflict items raised or reopened by this run
    undecided_postings: int     # trusted open postings with no current decision
    ignored_postings: int       # open postings on doubtful boards
    stale_postings: int         # open postings that would have been visible but are stale


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _check_ruleset(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT ruleset_version FROM ruleset WHERE is_current = 1").fetchone()
    if row is None or row[0] != ruleset_version():
        raise RulesetNotCurrent("ruleset changed; run: uv run python -m outreach.rules.rebuild")


def compute_rank(conn: sqlite3.Connection, *, today: date, cfg: RankerConfig | None = None, wages=None,
                 stale_after_months: int | None = None) -> tuple[list[Scored], dict]:
    """Scores every listed company; returns the rows in `company_id` order and what the write half needs:
    `facts` (by company), `inputs` (hide inputs of the scored companies), `sizes` and the posting counts.

    Read only. With no transaction open it reads inside one deferred read transaction (WAL: a snapshot, no write
    lock); inside the caller's transaction it reads there. Raises RulesetNotCurrent when the store's current ruleset
    is not this code's.
    """
    cfg = cfg if cfg is not None else default_ranker_config()
    wages = wages if wages is not None else load_wages()
    own = not conn.in_transaction
    if own:
        conn.execute("BEGIN DEFERRED")
    try:
        _check_ruleset(conn)
        canon = canonical_ids(conn)
        doubtful = doubtful_board_ids(conn)
        stale = crawl_stale.load_stale(conn, today, max_age_months=stale_after_months)
        roles = load_roles(conn, canon, doubtful, today, stale)
        facts = load_facts(conn, canon, doubtful, roles, today, cfg, wages)
        all_inputs = load_hide_inputs(conn, canon, doubtful, stale)
    finally:
        if own and conn.in_transaction:
            conn.execute("COMMIT")  # a read transaction: nothing to keep or undo

    signals = build_signals(cfg)
    scored: list[Scored] = []
    inputs: dict[int, HideInputs] = {}
    undecided = 0
    for f in facts:
        hi = all_inputs.get(f.company_id) or HideInputs(f.company_id, None, "", (), (), None)
        inputs[f.company_id] = hi
        scored.append(score_company(f, decide_hide(hi, cfg), signals, cfg,
                                    extra_flags=(CONFLICT_FLAG,) if hi.conflict else ()))
        rl = roles.get(f.company_id)
        undecided += rl.undecided if rl else 0
    data = {
        "facts": {f.company_id: f for f in facts},
        "inputs": inputs,
        "sizes": {cid: hi.size for cid, hi in inputs.items() if hi.size is not None},
        "undecided_postings": undecided,
        "ignored_postings": sum(f.ignored_postings for f in facts),
        "stale_postings": sum(len(f.stale_postings) for f in facts),
    }
    return scored, data


def _hidden_counts(scored: list[Scored]) -> dict[str, int]:
    counts = dict.fromkeys(HIDDEN_KINDS, 0)
    for s in scored:
        if s.hide is not None:
            counts["provisional" if s.hide.provisional else s.hide.kind] += 1
    return counts


def _number(x: float | None) -> str:
    return "none" if x is None else f"{x:g}"


def weights_text(changed: dict[str, list]) -> str:
    """One plain line for the weights_changed event: `<label> weight <old> to <new>` joined by `; `, at most three
    changes (by signal name), then `and N more`."""
    if not changed:
        return "Ranker updated, no weight changed"
    parts = []
    for name in sorted(changed)[:_TEXT_MAX_CHANGES]:
        old, new = changed[name]
        label = _LABELS.get(name, name.replace("_", " ").capitalize())
        parts.append(f"{label} weight {_number(old)} to {_number(new)}")
    if len(changed) > _TEXT_MAX_CHANGES:
        parts.append(f"and {len(changed) - _TEXT_MAX_CHANGES} more")
    return "; ".join(parts)


def _old_weights(conn: sqlite3.Connection, version: str) -> dict[str, float]:
    row = conn.execute("SELECT signals FROM company_score WHERE ranker_version = ? ORDER BY company_id LIMIT 1",
                       (version,)).fetchone()
    if row is None:
        return {}
    return {name: float(v["weight"]) for name, v in json.loads(row[0])["values"].items()}


def _weights_changed(conn: sqlite3.Connection, old: str, new: str, cfg: RankerConfig) -> None:
    before = _old_weights(conn, old)
    after = {name: float(w) for name, w in cfg.weights.items()}
    changed = {name: [before.get(name), after.get(name)] for name in sorted(set(before) | set(after))
               if before.get(name) != after.get(name)}
    append_event(conn, entity="score", kind="weights_changed", actor="system",
                 detail={"old_version": old, "new_version": new, "changed": changed, "text": weights_text(changed)},
                 idempotency_key=f"weights-changed:{old}:{new}")


def _scored_at(conn: sqlite3.Connection, version: str) -> str:
    """Now, or one second after the newest row of any other version: the run's version must be the newest by
    `scored_at` (what the readers take as the latest), even when two runs share a second or the clock stepped back."""
    now = utcnow()
    newest = conn.execute("SELECT MAX(scored_at) FROM company_score WHERE ranker_version <> ?", (version,)).fetchone()[0]
    if newest is not None and newest >= now:
        now = (datetime.strptime(newest, ISO_FMT) + timedelta(seconds=1)).strftime(ISO_FMT)
    return now


def _as_no_go(cfg: RankerConfig) -> Hide:
    """The hide a written no_go gives on every later run, so this run's row already matches them."""
    return decide_hide(HideInputs(0, "active", "no_sponsorship", (), (), None), cfg)


def run_rank(conn: sqlite3.Connection, *, today: date | None = None, cfg: RankerConfig | None = None,
             dry_run: bool = False) -> RankRun:
    """Ranks every listed company under this code's ranker version; see the module text for what is written."""
    today = today if today is not None else _utc_today()
    cfg = cfg if cfg is not None else default_ranker_config()
    version = ranker_version()
    if dry_run:
        scored, data = compute_rank(conn, today=today, cfg=cfg)
        return _result(version, scored, data, 0, 0, 0)

    code_ruleset = ruleset_version()
    known = conn.execute("SELECT 1 FROM ruleset WHERE ruleset_version = ?", (code_ruleset,)).fetchone()
    run_id = start_job(conn, "rank", code_ruleset if known else None)  # the column is a key into ruleset
    try:
        scored, data = compute_rank(conn, today=today, cfg=cfg)
        with transaction(conn):
            before = latest_version(conn)
            written = 0
            for i, s in enumerate(scored):
                if s.hide is not None and s.hide.kind == "no_sponsorship" and not s.hide.provisional:
                    if write_no_go(conn, s.company_id, s.hide):
                        written += 1
                        scored[i] = dataclasses.replace(s, hide=_as_no_go(cfg))
            now = _scored_at(conn, version)
            write_scores(conn, scored, version, now)
            conn.execute("DELETE FROM company_score WHERE ranker_version = ? "
                         "AND company_id NOT IN (SELECT value FROM json_each(?))",
                         (version, json.dumps([s.company_id for s in scored])))
            size_items, _cleared = sync_size_items(conn, scored, data["facts"], data["sizes"], cfg, now=now)
            conflict_items, _cleared = sync_conflict_items(conn, data["inputs"], scored, cfg, now=now)
            if before is not None and before != version:
                _weights_changed(conn, before, version, cfg)
            prune_versions(conn, cfg.ranker["keep_versions"])
            run = _result(version, scored, data, written, size_items, conflict_items)
            finish_job(conn, run_id, "ok", _summary(run))
    except BaseException as err:
        try:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            finish_job(conn, run_id, "failed", error=describe_error(err))
        except Exception as finish_err:  # keep the original error as the one that propagates
            err.add_note(f"could not record the failed run: {describe_error(finish_err)}")
        raise
    return run


def _result(version: str, scored: list[Scored], data: dict, written: int, size_items: int,
            conflict_items: int) -> RankRun:
    return RankRun(version=version, scored=tuple(scored), hidden=_hidden_counts(scored), no_go_written=written,
                   size_items=size_items, conflict_items=conflict_items,
                   undecided_postings=data["undecided_postings"], ignored_postings=data["ignored_postings"],
                   stale_postings=data["stale_postings"])


def _summary(run: RankRun) -> dict:
    return {"version": run.version, "scored": len(run.scored), "hidden": run.hidden,
            "no_go_written": run.no_go_written, "size_items": run.size_items, "conflict_items": run.conflict_items,
            "undecided_postings": run.undecided_postings, "ignored_postings": run.ignored_postings,
            "stale_postings": run.stale_postings}
