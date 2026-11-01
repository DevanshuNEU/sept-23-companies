"""The nightly follow-up drafter on the command line (#142).

    python -m outreach.followup_drafter run [--limit N] [--dry-run]

`run` drafts every follow-up due by the next business day into the Outbox, one `claude -p` call each through his
Claude Code seat, and prints one line per follow-up and one summary line. It never approves or sends. A real run only
starts in the live checkout (it calls a model and writes the live store), and keeps the Mac awake with
`caffeinate -i -w <its pid>` until it ends. `--dry-run` lists what is due and calls and writes nothing; it runs
anywhere on a scratch OUTREACH_DB. With `[drafter] enabled = false` it prints the off line and exits 0.

Exit 0 when the run is done, nothing is due, it is off, a dry run, or a session limit stopped it (the next night picks
up); 1 on a login problem, a broken CLI, unreadable voice files or failed calls in a row.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

import live_checkout
from outreach.followup_drafter.config import load_drafter_config
from outreach.followup_drafter.run import OFF_LINE, drafter_runner, run_drafter
from outreach.store.timefmt import utcnow

__all__ = ["main"]

_OK = ("done", "nothing_due", "dry_run", "session_limit", "off")


def _positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {value}")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m outreach.followup_drafter",
                                     description="Draft the follow-ups due by the next business day.")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="draft what is due (live checkout only, unless --dry-run)")
    run.add_argument("--limit", type=_positive, default=None, help="follow-ups this run, at most max_per_run")
    run.add_argument("--dry-run", action="store_true", help="list what is due; call and write nothing")
    return parser


def _start_caffeinate() -> None:
    try:  # awake while this process lives; never a reason not to run
        subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())])
    except OSError as err:
        print(f"followup drafter: caffeinate did not start ({err}); running anyway", file=sys.stderr)


def main(argv: list[str] | None = None, *, stdout=None, runner=None, now: str | None = None) -> int:
    args = _parser().parse_args(argv)
    stdout = stdout if stdout is not None else sys.stdout
    cfg = load_drafter_config()
    if not args.dry_run and cfg.enabled is not True:
        print(OFF_LINE, file=stdout)
        return 0
    if not args.dry_run:
        if not live_checkout.is_live():
            raise SystemExit(f"the follow-up drafter only runs from the live checkout ({live_checkout.LIVE_ROOT}); "
                             f"this is {live_checkout.REPO_ROOT}.")
        _start_caffeinate()
        runner = runner or drafter_runner(cfg)
    summary = run_drafter(runner, cfg, now=now or utcnow(), limit=args.limit, dry_run=args.dry_run)
    for line in summary.lines:
        print(line, file=stdout)
    print(summary.line(), file=stdout)
    return 0 if summary.status in _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
