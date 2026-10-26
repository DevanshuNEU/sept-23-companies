"""`python -m outreach.ranking <command>`: the ranker by hand (#6, Tasks 11 and 12).

- `rank [--dry-run] [--today YYYY-MM-DD] [--diff-with VERSION]`: rank every listed company and print the version,
  the counts and the top 10 with their reasons. `--dry-run` writes nothing, not even a job run. `--today` defaults
  to the UTC date. `--diff-with VERSION` is always a dry run: it ranks with the current code and config, writes
  nothing and prints what moved against that stored version, so a weight change is judged before it is committed.
- `diff [--old VERSION] [--new VERSION]`: what moved between two stored versions; by default the two newest.
- `--min-move` and `--top-n` (on both) default to `diff_min_rank_move` and `diff_top_n` in the ranker config.

The store is opened with `connect()`, which refuses the live store outside the live checkout. Exit code: 0 when the
run finished, 1 when it failed (the message on stderr).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from datetime import date

from outreach.ranking.config import default_ranker_config
from outreach.ranking.diff import MovedReport, scored_to_stored, what_moved
from outreach.ranking.job import RankRun, run_rank
from outreach.ranking.score import list_order
from outreach.ranking.scores import latest_version, load_scores, previous_version
from outreach.rules import RulesetNotCurrent
from outreach.store import StoreError, connect, describe_error

__all__ = ["main"]

_TOP = 10
_EXPECTED = (ValueError, OSError, StoreError, sqlite3.Error, RulesetNotCurrent)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m outreach.ranking", description="The company ranker (#6).")
    sub = p.add_subparsers(dest="command", required=True)
    rank = sub.add_parser("rank", help="score every listed company")
    rank.add_argument("--dry-run", action="store_true", help="compute and print, write nothing")
    rank.add_argument("--today", type=date.fromisoformat, default=None, metavar="YYYY-MM-DD")
    rank.add_argument("--diff-with", default=None, metavar="VERSION",
                      help="dry-run the current code and config and print what moved against this stored version")
    diff = sub.add_parser("diff", help="what moved between two stored ranker versions")
    diff.add_argument("--old", default=None, metavar="VERSION", help="default: the version before --new")
    diff.add_argument("--new", default=None, metavar="VERSION", help="default: the newest version")
    for command in (rank, diff):
        command.add_argument("--min-move", type=int, default=None, metavar="N",
                             help="smallest rank change to list (default: diff_min_rank_move)")
        command.add_argument("--top-n", type=int, default=None, metavar="N",
                             help="the top list that entering and leaving is judged on (default: diff_top_n)")
    return p


def _report(run: RankRun, dry_run: bool) -> str:
    hidden = ", ".join(f"{kind.replace('_', ' ')} {n}" for kind, n in run.hidden.items())
    lines = [
        f"Ranker {run.version}{' (dry run, nothing written)' if dry_run else ''}",
        f"Scored {len(run.scored)} companies; hidden: {hidden}",
        f"No-go rows written {run.no_go_written}; size items {run.size_items}; conflict items {run.conflict_items}",
        f"Postings: {run.undecided_postings} undecided, {run.ignored_postings} ignored (board match doubtful), "
        f"{run.stale_postings} stale",
        f"Interview companies: {run.interview_companies} (tilt weight "
        f"{default_ranker_config().weights['interview_like']:g}, {run.interview_confidence} confidence)",
        f"Top {_TOP}:",
    ]
    for place, s in enumerate(list_order(run.scored)[:_TOP], 1):
        lanes = ", ".join(s.lanes) if s.lanes else "no lane"
        lines.append(f"{place:>2}. {s.name} ({s.score:g}, {lanes}): {s.rank_reason.line}")
    return "\n".join(lines)


def _stored_versions(conn: sqlite3.Connection) -> list[str]:
    """Every stored version, newest first."""
    versions = []
    version = latest_version(conn)
    while version is not None:
        versions.append(version)
        version = previous_version(conn, version)
    return versions


def _need(conn: sqlite3.Connection, version: str) -> None:
    stored = _stored_versions(conn)
    if version not in stored:
        raise ValueError(f"ranker version {version} is not stored; stored versions: {', '.join(stored) or 'none'}")


def _names(conn: sqlite3.Connection, ids: set[int]) -> dict[int, str]:
    return {r["company_id"]: r["name"] for r in conn.execute(
        "SELECT company_id, name FROM company WHERE company_id IN (SELECT value FROM json_each(?))",
        (json.dumps(sorted(ids)),))}


def _numbers(args: argparse.Namespace) -> tuple[int, int]:
    cfg = default_ranker_config()
    min_move = cfg.ranker["diff_min_rank_move"] if args.min_move is None else args.min_move
    top_n = cfg.ranker["diff_top_n"] if args.top_n is None else args.top_n
    if min_move < 1 or top_n < 1:
        raise ValueError("--min-move and --top-n must each be at least 1")
    return min_move, top_n


def _moved(conn: sqlite3.Connection, old_version: str, new_version: str, new, args: argparse.Namespace,
           extra_names: dict[int, str] | None = None) -> MovedReport:
    """What moved from the stored `old_version` to `new` (a mapping of stored-shaped rows, labelled `new_version`)."""
    min_move, top_n = _numbers(args)
    old = load_scores(conn, old_version)
    names = _names(conn, set(old) | set(new))
    names.update(extra_names or {})
    return what_moved(old, new, names, min_move=min_move, top_n=top_n, old_version=old_version,
                      new_version=new_version)


def _diff(conn: sqlite3.Connection, args: argparse.Namespace) -> str:
    new_version = args.new if args.new is not None else latest_version(conn)
    if new_version is not None:
        _need(conn, new_version)
    old_version = args.old if args.old is not None else (
        previous_version(conn, new_version) if new_version is not None else None)
    if new_version is None or old_version is None:
        raise ValueError("diff needs two stored ranker versions; run the ranker again after a change to compare")
    _need(conn, old_version)
    return "\n".join(_moved(conn, old_version, new_version, load_scores(conn, new_version), args).lines())


def _rank(conn: sqlite3.Connection, args: argparse.Namespace) -> str:
    if args.diff_with is not None:
        _numbers(args)  # refuse bad numbers before the (slow) ranking
        _need(conn, args.diff_with)
        run = run_rank(conn, today=args.today, dry_run=True)
        report = _report(run, True)
        names = {s.company_id: s.name for s in run.scored}
        moved = _moved(conn, args.diff_with, f"{run.version} (dry run)", scored_to_stored(run.scored), args, names)
        return report + "\n\n" + "\n".join(moved.lines())
    run = run_rank(conn, today=args.today, dry_run=args.dry_run)
    return _report(run, args.dry_run)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        conn = connect()
        try:
            text = _diff(conn, args) if args.command == "diff" else _rank(conn, args)
        finally:
            conn.close()
    except _EXPECTED as err:
        print(describe_error(err), file=sys.stderr)
        return 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
