"""`python -m outreach.people clear-reach [--apply]`: clear the LinkedIn list for the new system (#15).

The default is a dry run: it prints who would be removed, who stays and why, and the row counts, and writes nothing.
It removes LinkedIn people with no history and nothing of his; everyone with history stays in the store. See
`outreach.people.clear` for the rules.

`--apply` first writes a full backup in `backups/` next to the store and prints its path; if the backup fails nothing
is deleted (exit 1). Then one transaction decides again and deletes, and the counts before and after are printed. It
runs only from the live checkout, or with OUTREACH_DB set to a scratch store; the check comes before the store is
opened. Exit 0 when the command finished.
"""
from __future__ import annotations

import argparse
import os
import sys

import live_checkout
from outreach.people.clear import (BackupFailed, ClearPlan, Removal, apply_clear, plan_clear, table_counts,
                                   write_backup)
from outreach.store import connect, db_path, is_live_path, utcnow

__all__ = ["main"]

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m outreach.people", description="People clean-up.")
    sub = parser.add_subparsers(dest="command", required=True)
    clear = sub.add_parser("clear-reach", help="remove the never-contacted people from the LinkedIn list")
    clear.add_argument("--apply", action="store_true", help="back up the store, then delete (default: dry run)")
    return parser


def _apply_allowed() -> bool:
    """The live checkout, or a scratch OUTREACH_DB anywhere else."""
    if live_checkout.is_live():
        return True
    return bool(os.environ.get("OUTREACH_DB")) and not is_live_path(db_path())


def _removal_line(r: Removal) -> str:
    also = []
    if r.drafts:
        also.append("prepare draft " + ", ".join(str(d) for d, _ in r.drafts))
    if r.sources:
        also.append(f"{len(r.sources)} source rows")
    if r.plan_rows:
        also.append("clears the name on plan rows " + ", ".join(x.list_date for x in r.plan_rows))
    tail = f"; also {'; '.join(also)}" if also else ""
    return f"  - {r.name} (person {r.person_id}) at {r.company} (company {r.company_id}), added by {r.added_by}{tail}"


def _counts(title: str, counts: dict[str, int], stdout) -> None:
    print(title + " " + ", ".join(f"{k} {v}" for k, v in counts.items()), file=stdout)


def _print_plan(plan: ClearPlan, stdout, *, verb: str) -> None:
    print(f"{verb} {len(plan.remove)} people:", file=stdout)
    for r in plan.remove:
        print(_removal_line(r), file=stdout)
    print(f"Keeps {len(plan.kept)} people:", file=stdout)
    for k in plan.kept:
        print(f"  - {k.name} (person {k.person_id}) at {k.company}: {'; '.join(k.reasons)}", file=stdout)


def clear_reach(args, stdout) -> int:
    if args.apply and not _apply_allowed():
        raise SystemExit("clear-reach --apply only runs from the live checkout, or with OUTREACH_DB set to a scratch "
                         f"store; this is {live_checkout.REPO_ROOT}.")
    now = utcnow()
    conn = connect()
    try:
        plan = plan_clear(conn, now=now)
        if not args.apply:
            print(f"Clear the LinkedIn list (today {plan.today}). DRY RUN: nothing is written.", file=stdout)
            _print_plan(plan, stdout, verb="Would remove")
            _counts("Rows now:", table_counts(conn), stdout)
            if plan.remove:
                print("Run again with --apply to remove them (it writes a backup first).", file=stdout)
            return 0
        print(f"Clear the LinkedIn list (today {plan.today}). APPLY.", file=stdout)
        if not plan.remove:
            _print_plan(plan, stdout, verb="Would remove")
            print("Nothing to remove: no backup written, nothing changed.", file=stdout)
            return 0
        before = table_counts(conn)
        try:
            backup = write_backup(conn, db_path(), now)
        except BackupFailed as err:
            print(f"Backup failed, nothing deleted: {err}", file=stdout)
            return 1
        print(f"Backup written: {backup}", file=stdout)
        result = apply_clear(conn, plan, backup=backup, now=now)
        _print_plan(ClearPlan(plan.today, result.removed, plan.kept), stdout, verb="Removed")
        for r in result.changed:
            print(f"  - kept, it changed while this ran: {r.name} (person {r.person_id}) at {r.company}", file=stdout)
        _counts("Rows before:", before, stdout)
        _counts("Rows after:", table_counts(conn), stdout)
        print(f"To undo, copy the rows back from the backup: {backup}", file=stdout)
        return 0
    finally:
        conn.close()


def main(argv: list[str] | None = None, *, stdout=None) -> int:
    args = _parser().parse_args(argv)
    stdout = stdout if stdout is not None else sys.stdout
    return clear_reach(args, stdout)


if __name__ == "__main__":
    raise SystemExit(main())
