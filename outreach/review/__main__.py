"""`python -m outreach.review sweep [--company ID] [--dry-run]`: the Needs-you sweep by hand, and the launchd job (#16).

It raises and clears the items that have a sweep behind them (unknown size, missing email domain, unclear
sponsorship wording, and what went away or went stale) and prints one line per kind raised or cleared and one per
skipped step. `--company ID` limits it to one company and writes no heartbeat. `--dry-run` runs inside a transaction
it rolls back, so nothing is saved.

The store is opened with `connect()`, which refuses the live store outside the live checkout. Exit code: 0 when the
sweep finished, 1 when it failed (the message on stderr).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence

from outreach.review.triggers import SweepReport, sweep_needs_you
from outreach.store import StoreError, connect, describe_error

__all__ = ["main"]

_EXPECTED = (ValueError, OSError, StoreError, sqlite3.Error)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m outreach.review", description="The Needs-you sweep (#16).")
    sub = p.add_subparsers(dest="command", required=True)
    sweep = sub.add_parser("sweep", help="raise and clear the Needs-you items that have a sweep behind them")
    sweep.add_argument("--company", type=int, default=None, metavar="ID", help="limit the sweep to one company")
    sweep.add_argument("--dry-run", action="store_true", help="run, print, and save nothing")
    return p


def _lines(report: SweepReport) -> list[str]:
    lines = [f"Raised {n} {kind}." for kind, n in sorted(report.raised.items()) if n]
    lines += [f"Cleared {n} {kind}." for kind, n in sorted(report.cleared.items()) if n]
    lines += [f"Skipped: {why}." for why in report.skipped]
    if not report.changed:
        lines.insert(0, "Nothing to raise or clear.")
    return lines


def _sweep(conn: sqlite3.Connection, args: argparse.Namespace) -> list[str]:
    companies = None if args.company is None else [args.company]
    if not args.dry_run:
        return _lines(sweep_needs_you(conn, company_ids=companies))
    conn.execute("BEGIN IMMEDIATE")
    try:
        lines = _lines(sweep_needs_you(conn, company_ids=companies))
    finally:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
    return lines + ["dry run, nothing saved"]


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        conn = connect()
        try:
            lines = _sweep(conn, args)
        finally:
            conn.close()
    except _EXPECTED as err:
        print(describe_error(err), file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
