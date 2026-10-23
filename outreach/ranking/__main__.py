"""`python -m outreach.ranking <command>`: the ranker by hand (#6, Task 11; `diff` comes with Task 12).

- `rank [--dry-run] [--today YYYY-MM-DD]`: rank every listed company and print the version, the counts and the top
  10 with their reasons. `--dry-run` writes nothing, not even a job run. `--today` defaults to the UTC date.

The store is opened with `connect()`, which refuses the live store outside the live checkout. Exit code: 0 when the
run finished, 1 when it failed (the message on stderr).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from datetime import date

from outreach.ranking.job import RankRun, run_rank
from outreach.ranking.score import list_order
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
    return p


def _report(run: RankRun, dry_run: bool) -> str:
    hidden = ", ".join(f"{kind.replace('_', ' ')} {n}" for kind, n in run.hidden.items())
    lines = [
        f"Ranker {run.version}{' (dry run, nothing written)' if dry_run else ''}",
        f"Scored {len(run.scored)} companies; hidden: {hidden}",
        f"No-go rows written {run.no_go_written}; size items {run.size_items}; conflict items {run.conflict_items}",
        f"Postings: {run.undecided_postings} undecided, {run.ignored_postings} ignored (board match doubtful), "
        f"{run.stale_postings} stale",
        f"Top {_TOP}:",
    ]
    for place, s in enumerate(list_order(run.scored)[:_TOP], 1):
        lanes = ", ".join(s.lanes) if s.lanes else "no lane"
        lines.append(f"{place:>2}. {s.name} ({s.score:g}, {lanes}): {s.rank_reason.line}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        conn = connect()
        try:
            run = run_rank(conn, today=args.today, dry_run=args.dry_run)
        finally:
            conn.close()
    except _EXPECTED as err:
        print(describe_error(err), file=sys.stderr)
        return 1
    print(_report(run, args.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
