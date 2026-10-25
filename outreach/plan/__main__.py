"""`python -m outreach.plan <command>`: today's people plan by hand (#15, Task 11).

- `build [--date YYYY-MM-DD] [--no-read-boards] [--dry-run] [--force-weekend]`: build the day's plan (once per date; a
  second run changes nothing) and print the status, the capacity and one line per row. The default date is today in
  New York. Unless `--no-read-boards` is given it refuses to run outside the live checkout before it opens anything,
  because it then asks the crawler to read boards and the ranker to re-rank. `--dry-run` computes and prints and writes
  nothing, not even a job run. A weekend builds nothing unless `--force-weekend`.
- `show [--date YYYY-MM-DD]`: print the stored plan, read only: `<rank> <company> <person or find someone> <channel>
  <lane>`, and the people search link under each find-someone row.

The store is the one at OUTREACH_DB; the default (live) store opens only from the live checkout, so from a worktree
this runs on a scratch OUTREACH_DB or not at all. Exit code: 0 when the command finished, 1 when it failed (the
message on stderr). Nothing here sends, approves or logs a LinkedIn action.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Sequence
from datetime import date

import live_checkout
from outreach.plan.boards import BoardsReadError, crawler_boards_reader, rank_runner
from outreach.plan.build import PlanResult, build_plan
from outreach.plan.config import default_plan_config
from outreach.plan.dates import today_ny
from outreach.store import StoreError, connect, describe_error
from outreach.store.timefmt import utcnow

__all__ = ["main"]

_EXPECTED = (ValueError, OSError, StoreError, sqlite3.Error, BoardsReadError, KeyError, LookupError)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m outreach.plan", description="Today's people plan (#15).")
    sub = p.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="build the day's plan (once per date)")
    build.add_argument("--date", type=date.fromisoformat, default=None, metavar="YYYY-MM-DD")
    build.add_argument("--no-read-boards", action="store_true", help="skip the crawler and the ranker")
    build.add_argument("--dry-run", action="store_true", help="compute and print, write nothing")
    build.add_argument("--force-weekend", action="store_true", help="build on a Saturday or Sunday too")
    show = sub.add_parser("show", help="print a stored plan")
    show.add_argument("--date", type=date.fromisoformat, default=None, metavar="YYYY-MM-DD")
    return p


def _today() -> date:
    return today_ny(utcnow(), default_plan_config().get("plan", "timezone"))


def _row_line(rank: int, company: str, person: str | None, channel: str, lane: str) -> str:
    return f"{rank:>2} {company} | {person or 'find someone'} | {channel} | {lane}"


def _names(conn: sqlite3.Connection, table: str, key: str, ids: Sequence[int]) -> dict[int, str]:
    wanted = sorted({i for i in ids if i is not None})
    marks = ", ".join("?" for _ in wanted)
    if not wanted:
        return {}
    return {r[0]: r[1] for r in conn.execute(f"SELECT {key}, name FROM {table} WHERE {key} IN ({marks})", wanted)}


def _report(conn: sqlite3.Connection, result: PlanResult, dry_run: bool) -> list[str]:
    head = f"Plan {result.list_date.isoformat()}: {result.status}{' (dry run, nothing written)' if dry_run else ''}"
    if result.linkedin is None:
        return [head + f", capacity {result.capacity}"] if result.status == "already_built" else [head]
    lines = [f"{head}, capacity {result.capacity} (LinkedIn {result.linkedin.today}, email {result.email.slots}), "
             f"boards {result.boards}, {len(result.chosen)} rows"]
    companies = _names(conn, "company", "company_id", [c.company_id for c in result.chosen])
    people = _names(conn, "person", "person_id", [c.person_id for c in result.chosen])
    for rank, row in enumerate(result.chosen, 1):
        lines.append(_row_line(rank, companies.get(row.company_id, str(row.company_id)),
                               people.get(row.person_id) if row.person_id is not None else None, row.channel,
                               row.list_group))
    return lines


def _start_caffeinate() -> None:
    """Keep the Mac awake until this process ends (caffeinate exits with it). Never a reason not to build."""
    try:
        subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())])
    except OSError as err:
        print(f"build: caffeinate did not start ({err}); building anyway", file=sys.stderr)


def _build(args: argparse.Namespace, root: str | None, live_root: str | None, stdout) -> int:
    reading = not args.no_read_boards
    if reading:
        live_checkout.require_live("the daily plan build", root or live_checkout.REPO_ROOT,
                                   live_root or live_checkout.LIVE_ROOT)
        _start_caffeinate()
    boards_reader = crawler_boards_reader() if reading else None
    ranker = rank_runner() if reading else None
    conn = connect()
    try:
        result = build_plan(conn, today=args.date or _today(), boards_reader=boards_reader, ranker=ranker,
                            read_boards=reading, force_weekend=args.force_weekend, dry_run=args.dry_run)
        print("\n".join(_report(conn, result, args.dry_run)), file=stdout)
    finally:
        conn.close()
    return 0


def _show(args: argparse.Namespace, stdout) -> int:
    day = args.date or _today()
    conn = connect()
    try:
        plan = conn.execute("SELECT capacity FROM daily_list WHERE list_date = ?", (day.isoformat(),)).fetchone()
        if plan is None:
            print(f"Plan {day.isoformat()}: none built", file=stdout)
            return 0
        rows = conn.execute(
            "SELECT e.rank, e.list_group, e.channel, e.state, e.why, c.name AS company, p.name AS person "
            "FROM daily_list_entry e JOIN company c ON c.company_id = e.company_id "
            "LEFT JOIN person p ON p.person_id = e.person_id WHERE e.list_date = ? ORDER BY e.rank",
            (day.isoformat(),)).fetchall()
        print(f"Plan {day.isoformat()}: capacity {plan[0]}, {len(rows)} rows", file=stdout)
        for r in rows:
            print(_row_line(r["rank"], r["company"], r["person"], r["channel"], r["list_group"]), file=stdout)
            try:
                find = json.loads(r["why"]).get("find_someone")
            except (ValueError, AttributeError):
                find = None
            if r["person"] is None and isinstance(find, dict) and find.get("search_url"):
                print(f"   search: {find['search_url']}", file=stdout)
    finally:
        conn.close()
    return 0


def main(argv: Sequence[str] | None = None, *, root: str | None = None, live_root: str | None = None,
         stdout=None) -> int:
    """Run one command and return the exit code. `root` and `live_root` default to this checkout and the live one."""
    args = _parser().parse_args(argv)
    stdout = stdout if stdout is not None else sys.stdout
    try:
        if args.command == "build":
            return _build(args, root, live_root, stdout)
        return _show(args, stdout)
    except _EXPECTED as err:
        print(f"{args.command}: {describe_error(err)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
