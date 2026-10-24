"""Where stored decisions come from: rebuild() re-decides every open posting under the current ruleset, and
decide_and_store() decides one posting for the crawler. record_evidence() is the one writer of rule-made evidence.

The mail sender and the API share this SQLite file with a 5 second busy timeout, so no step here holds the write lock
while rules run: each batch reads its postings and decides them with no transaction open, then takes the lock only to
write the rows. A rebuild never deletes a decision before it writes the new one (the old Job Radar
rebuild_decisions.py did, and lost data); it upserts under the new version, switches the current ruleset in one short
transaction, then prunes versions nobody reads. The crawler must not run at the same time: #21's job runs rebuild
first, then crawls.

`combiner` (rebuild and decide_and_store) is the posting reader's (#32): a callable taking (conn, posting_id, the
rules' Decision) and returning an object with `decision`, `provisional` and `reader_version`, which is what gets
compared and written. This module never imports the reader: callers pass the combiner in, so the reader's code is
not part of the ruleset hash (rebuild.py is in NOT_RULES). With no combiner every row is written provisional with
no reader version, so a re-decide never keeps an earlier reader's agreement.

`python -m outreach.rules.rebuild` runs a rebuild on the store at OUTREACH_DB (the live path only in the live
checkout) and prints the flip report.
"""
import argparse
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from outreach.store import append_event, atomic, connect, finish_job, start_job, transaction, utcnow

from .config import Config, load_config
from .decide import Decision, PostingInput, decide, passes
from .facts import EvidenceDraft
from .version import ruleset_version

__all__ = ["RulesetNotCurrent", "Flip", "FlipReport", "BATCH_SIZE", "Combiner", "rebuild", "decide_and_store",
           "record_evidence", "stored_decision", "main"]

BATCH_SIZE = 500

_COLUMNS = ("lane", "level_tier", "level_ok", "level_basis", "years_asked", "years_quote", "us", "sponsorship",
            "sponsorship_quote", "citizenship", "citizenship_quote", "eligibility", "eligibility_reason",
            "eligibility_quote", "start_2027")
_WRITTEN = _COLUMNS + ("provisional", "reader_version")
_UPSERT = (
    "INSERT INTO decision(posting_id, ruleset_version, decided_at, " + ", ".join(_WRITTEN) + ") VALUES (?, ?, ?, "
    + ", ".join("?" for _ in _WRITTEN) + ") ON CONFLICT(posting_id, ruleset_version) DO UPDATE SET decided_at = "
    "excluded.decided_at, " + ", ".join(f"{c} = excluded.{c}" for c in _WRITTEN)
)
# The reader's combiner: (conn, posting_id, the rules' decision) -> an object with decision, provisional and
# reader_version (outreach.reader.combine.Combined). Typed loosely so this module needs no reader import.
Combiner = Callable[[sqlite3.Connection, int, Decision], Any]
# The fields passes() reads: what a shown-to-hidden flip is explained by.
_HIDING_FIELDS = ("lane", "us", "sponsorship", "citizenship", "eligibility")


class RulesetNotCurrent(Exception):
    """The store's current ruleset is not the one this code hashes to."""


@dataclass(frozen=True)
class Flip:
    posting_id: int
    company: str
    title: str
    change: str  # for example "sponsorship silent -> no"


@dataclass(frozen=True)
class FlipReport:
    version: str
    previous: str | None
    decided: int
    to_hidden: list[Flip]
    to_shown: list[Flip]
    level_lost: list[Flip]
    skipped: list[tuple[int, str]] = field(default_factory=list)  # (posting_id, error) the decision table refused

    def lines(self) -> list[str]:
        out = [f"Ruleset {self.version} (previous {self.previous or 'none'}): {self.decided} postings decided."]
        for heading, flips in (("Postings that went from shown to hidden", self.to_hidden),
                               ("Postings that went from hidden to shown", self.to_shown),
                               ("Postings that lost the level check", self.level_lost)):
            out.append(f"{heading} ({len(flips)}):")
            out.extend(f"  #{f.posting_id} {f.company}: {f.title} ({f.change})" for f in flips)
            if not flips:
                out.append("None.")
        if self.skipped:
            out.append(f"Postings skipped, the decision table refused them ({len(self.skipped)}):")
            out.extend(f"  #{posting_id}: {error}" for posting_id, error in self.skipped)
        return out


def stored_decision(row: sqlite3.Row) -> Decision:
    """A Decision from a decision-table row (or a current_decision or posting_decision row). Ranking and the reader
    read stored rows through this."""
    values = {c: row[c] for c in _COLUMNS}
    for flag in ("level_ok", "citizenship", "start_2027"):
        values[flag] = bool(values[flag])
    return Decision(**values)


_stored = stored_decision  # the old private name, kept so nothing else breaks


def _word(value) -> str:
    return {True: "yes", False: "no", None: "none"}.get(value, str(value))


def _change(old: Decision, new: Decision, fields) -> str:
    return "; ".join(f"{f} {_word(getattr(old, f))} -> {_word(getattr(new, f))}"
                     for f in fields if getattr(old, f) != getattr(new, f))


@dataclass(frozen=True)
class _Write:
    """One decision row to write: the facts, and whether a verified reading made them final."""
    decision: Decision
    provisional: bool = True
    reader_version: str | None = None


def _combined(conn: sqlite3.Connection, posting_id: int, rules: Decision, combiner: Combiner | None) -> _Write:
    if combiner is None:
        return _Write(rules)
    got = combiner(conn, posting_id, rules)
    provisional = bool(got.provisional)
    return _Write(got.decision, provisional, None if provisional else got.reader_version)


def _upsert(conn: sqlite3.Connection, posting_id: int, version: str, w: _Write) -> None:
    row = w.decision.as_row()
    conn.execute(_UPSERT, (posting_id, version, utcnow(), *(row[c] for c in _COLUMNS), int(w.provisional),
                           w.reader_version))


def _posting_input(row: sqlite3.Row) -> PostingInput:
    return PostingInput(row["title"], row["body"], row["location"], row["country"])


def _decide_batch(conn, ids, cfg, report: dict, combiner: Combiner | None = None) -> list[tuple[int, _Write]]:
    """Decide one batch with no write transaction open; add its flips to `report`; return the rows to write.

    Reads run in autocommit, so the CPU-bound decide() calls never hold the write lock. The flip comparison uses the
    current_decision row read here: rebuild and the crawler do not overlap, so nothing moves it under us.
    """
    decided = []
    for posting_id in ids:
        posting = conn.execute(
            "SELECT p.title, coalesce(b.body, '') AS body, p.location, p.country, p.closed_at, c.name "
            "FROM posting p JOIN company c ON c.company_id = p.company_id "
            "LEFT JOIN posting_body b ON b.posting_id = p.posting_id WHERE p.posting_id = ?", (posting_id,)).fetchone()
        if posting is None:
            continue
        old_row = None
        if posting["closed_at"] is None:
            old_row = conn.execute("SELECT * FROM current_decision WHERE posting_id = ?", (posting_id,)).fetchone()
        write = _combined(conn, posting_id, decide(_posting_input(posting), cfg), combiner)
        new = write.decision
        if old_row is not None:
            old = stored_decision(old_row)
            was, now = passes(old), passes(new)
            if was and not now:
                report["to_hidden"].append(
                    Flip(posting_id, posting["name"], posting["title"], _change(old, new, _HIDING_FIELDS)))
            elif now and not was:
                report["to_shown"].append(
                    Flip(posting_id, posting["name"], posting["title"], _change(old, new, _HIDING_FIELDS)))
            if old.level_ok and not new.level_ok:
                report["level_lost"].append(Flip(
                    posting_id, posting["name"], posting["title"],
                    f"level {old.level_tier} -> {new.level_tier}: {new.level_basis}"))
        decided.append((posting_id, write))
    return decided


def _prune(conn: sqlite3.Connection, keep: list[str], batch_size: int) -> int:
    """Delete decision rows under other versions, for postings that still have a row under a kept one."""
    marks = ", ".join("?" for _ in keep)
    pick = (f"SELECT d.rowid FROM decision d WHERE d.rowid > ? AND d.ruleset_version NOT IN ({marks}) AND EXISTS "
            f"(SELECT 1 FROM decision k WHERE k.posting_id = d.posting_id AND k.ruleset_version IN ({marks})) "
            "ORDER BY d.rowid LIMIT ?")
    pruned, last = 0, 0
    while True:
        with transaction(conn):
            rowids = [r[0] for r in conn.execute(pick, (last, *keep, *keep, batch_size))]
            if rowids:
                conn.execute(f"DELETE FROM decision WHERE rowid IN ({', '.join('?' for _ in rowids)})", rowids)
        if not rowids:
            return pruned
        pruned += len(rowids)
        last = rowids[-1]


def rebuild(conn: sqlite3.Connection, *, cfg: Config | None = None, version: str | None = None,
            batch_size: int = BATCH_SIZE, progress=None, combiner: Combiner | None = None) -> FlipReport:
    """Re-decide every open posting (and any with no decision) under `version`, make it current, prune old rows.

    `cfg` and `version` default to the shipped config and the code's ruleset version; tests override them. Returns
    the flips against the ruleset that was current when the run started; with a `combiner`, what it returns is
    what is compared and written (it runs with no transaction open, like decide()). A failed run leaves that ruleset current;
    rows already written under the new version are overwritten by the next run. A posting whose decision the table
    refuses (sqlite3.IntegrityError) is skipped and listed in the report and the job summary, and the job is closed
    "partial"; the rest of its batch still commits. If only the prune after the switch fails, the new ruleset is
    already current: the job is closed "partial" with the error, and nothing is raised.
    """
    cfg = cfg if cfg is not None else load_config()
    version = version if version is not None else ruleset_version()
    with transaction(conn):
        row = conn.execute("SELECT ruleset_version FROM ruleset WHERE is_current = 1").fetchone()
        previous = row[0] if row else None
        conn.execute(
            "INSERT INTO ruleset(ruleset_version, config_toml, created_at, is_current) VALUES (?, ?, ?, 0) "
            "ON CONFLICT(ruleset_version) DO NOTHING", (version, cfg.raw.decode("utf-8"), utcnow()))
        run_id = start_job(conn, "rebuild", version)
    flips = {"to_hidden": [], "to_shown": [], "level_lost": []}
    skipped: list[tuple[int, str]] = []
    try:
        ids = [r[0] for r in conn.execute(
            "SELECT posting_id FROM posting p WHERE closed_at IS NULL OR NOT EXISTS "
            "(SELECT 1 FROM decision d WHERE d.posting_id = p.posting_id) ORDER BY posting_id")]
        for start in range(0, len(ids), batch_size):
            decided = _decide_batch(conn, ids[start:start + batch_size], cfg, flips, combiner)
            with transaction(conn):
                for posting_id, write in decided:
                    conn.execute("SAVEPOINT rebuild_row")  # one refused row undoes only itself
                    try:
                        _upsert(conn, posting_id, version, write)
                    except sqlite3.IntegrityError as err:
                        conn.execute("ROLLBACK TO rebuild_row")
                        skipped.append((posting_id, str(err)))
                    conn.execute("RELEASE rebuild_row")
            if progress is not None:
                progress(min(start + batch_size, len(ids)))
        with transaction(conn):
            conn.execute("UPDATE ruleset SET is_current = 0 WHERE is_current = 1")
            conn.execute("UPDATE ruleset SET is_current = 1 WHERE ruleset_version = ?", (version,))
        keep = [version] + ([previous] if previous and previous != version else [])
        pruned, prune_error = None, ""
        try:
            pruned = _prune(conn, keep, batch_size)
        except Exception as err:  # the switch is done: old rows are only clutter
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            prune_error = f"prune failed: {type(err).__name__}: {err}"
        refused = {posting_id for posting_id, _ in skipped}
        flips = {name: [f for f in found if f.posting_id not in refused] for name, found in flips.items()}
        decided = len(ids) - len(skipped)
        summary = {"decided": decided, "previous": previous, "pruned": pruned,
                   **{name: [f.posting_id for f in found] for name, found in flips.items()}}
        error = "; ".join(filter(None, [
            f"{len(skipped)} postings skipped, the decision table refused them" if skipped else "", prune_error]))
        if skipped:
            summary["skipped"] = [{"posting_id": posting_id, "error": text} for posting_id, text in skipped]
        finish_job(conn, run_id, "partial" if error else "ok", summary, error=error)
        conn.execute("PRAGMA optimize")  # refresh the planner's statistics after a bulk rewrite of decisions
    except BaseException as err:
        try:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            finish_job(conn, run_id, "failed", error=f"{type(err).__name__}: {err}")
        except Exception as finish_err:  # keep the original error as the one that propagates
            err.add_note(f"could not record the failed run: {type(finish_err).__name__}: {finish_err}")
        raise
    return FlipReport(version, previous, decided, **flips, skipped=skipped)


def decide_and_store(conn: sqlite3.Connection, posting_id: int, *, cfg: Config | None = None,
                     combiner: Combiner | None = None) -> Decision:
    """Decide one posting and upsert its row under the store's current ruleset. What the crawler calls per posting.

    With a `combiner` the row and the returned Decision are the combiner's; without one the row is provisional with
    no reader version. Raises RulesetNotCurrent when the store's current version is not this code's. Joins the caller's transaction
    when there is one.
    """
    with atomic(conn, "rules_unit"):
        row = conn.execute("SELECT ruleset_version FROM ruleset WHERE is_current = 1").fetchone()
        if row is None or row[0] != ruleset_version():
            raise RulesetNotCurrent("ruleset changed; run: uv run python -m outreach.rules.rebuild")
        posting = conn.execute(
            "SELECT p.title, coalesce(b.body, '') AS body, p.location, p.country FROM posting p "
            "LEFT JOIN posting_body b ON b.posting_id = p.posting_id WHERE p.posting_id = ?", (posting_id,)).fetchone()
        if posting is None:
            raise LookupError(f"no posting {posting_id}")
        write = _combined(conn, posting_id, decide(_posting_input(posting), cfg), combiner)
        _upsert(conn, posting_id, row[0], write)
    return write.decision


def record_evidence(conn: sqlite3.Connection, company_id: int, drafts: list[EvidenceDraft], *,
                    posting_id: int | None = None, actor: str = "rules", at: str | None = None) -> int:
    """Insert rule-made evidence (never locked) and return how many rows were new.

    `actor` labels the event: 'rules' by default, 'source' for LCA, HN and Form D evidence (an event.actor value).
    `at` (canonical UTC) is when the rows and the event are recorded; default now. The MCP server passes its clock.

    A repeat of an earlier unlocked row adds nothing (the `evidence_once` index). A hand draft is refused: hand values
    come from #16 edits and #17's locked import, which write `locked = 1` themselves. A draft the table rejects
    raises sqlite3.IntegrityError and nothing from this call is kept.
    """
    if any(d.source_kind == "hand" for d in drafts):
        raise ValueError("hand evidence is written locked by edits and the import, not by the rules")
    with atomic(conn, "rules_unit"):
        added = []
        for d in drafts:
            cur = conn.execute(
                "INSERT INTO evidence(company_id, fact, value, num_low, num_high, confidence, locked, source_kind, "
                "source_ref, quote, posting_id, observed_on, recorded_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT DO NOTHING",
                (company_id, d.fact, d.value, d.num_low, d.num_high, d.confidence, d.source_kind, d.source_ref,
                 d.quote, posting_id, d.observed_on, at or utcnow()))
            if cur.rowcount:
                added.append(d.fact)
        if added:
            append_event(conn, entity="evidence", kind="recorded", actor=actor, company_id=company_id,
                         detail={"added": len(added), "facts": added, "posting_id": posting_id}, at=at)
    return len(added)


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(
        prog="python -m outreach.rules.rebuild",
        description="Re-decide every open posting under the current ruleset and print what flipped.",
    ).parse_args(argv)
    conn = connect()
    try:
        print("\n".join(rebuild(conn).lines()))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
