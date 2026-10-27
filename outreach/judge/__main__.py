"""The profile judge on the command line (Part of #6 and #15).

    python -m outreach.judge run [--limit N] [--dry-run] [--companies NAME,...]   # the nightly job
    uv run python -m outreach.judge show [--top N]                               # the top of the ranking, judged
    python -m outreach.judge eval [--file PATH]                                  # agreement with the golden set

`run` does nothing while `[judge] enabled` is false (as shipped): it prints "judge is off" and exits 0 before it opens
anything. On, a real run only starts in the live checkout (it reads web pages and calls a model through his Claude
Code seat), and it keeps the Mac awake with `caffeinate -i -w <its pid>` until it ends. `--dry-run` lists who is due
and what would be read, fetches, calls and writes nothing, and runs anywhere on a scratch OUTREACH_DB. Exit 0 when
the run ended done, with nothing due, or on a session limit; 1 on a login problem or failed calls.

`eval` compares the judge with the golden set in `golden.toml`. In the live checkout it first judges the golden
companies that have no judgment under the current version (at most one call each, even with the job off); anywhere
else it scores only what is stored. `show` and `eval` open the store through `connect()`, so the live store opens only
from the live checkout.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

import live_checkout
from outreach.crawl.config import load_crawl_config
from outreach.judge.config import load_judge_config
from outreach.judge.fetch import build_reader
from outreach.judge.prompt import read_me
from outreach.judge.run import OFF_LINE, judge_runner, run_judge
from outreach.ranking.common import canonical_ids
from outreach.ranking.judged import judge_line, load_judgments
from outreach.ranking.scores import ranked_list
from outreach.store import connect

__all__ = ["main"]

_OK = ("done", "no_targets", "dry_run", "session_limit", "off")


def _positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {value}")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m outreach.judge", description="The profile judge.")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="judge the due top companies (live checkout only, unless --dry-run)")
    run.add_argument("--limit", type=_positive, default=None, help="companies this run, at most per_run")
    run.add_argument("--dry-run", action="store_true", help="list who is due and what would be read; touch nothing")
    run.add_argument("--companies", default=None, help="only these companies, by name, comma separated")
    show = sub.add_parser("show", help="the top of the ranking with each judge line")
    show.add_argument("--top", type=_positive, default=20)
    ev = sub.add_parser("eval", help="how far the judge agrees with the golden set")
    ev.add_argument("--file", default=None, help="the golden set (default: outreach/judge/golden.toml)")
    return parser


def _start_caffeinate() -> None:
    try:  # awake while this process lives; never a reason not to run
        subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())])
    except OSError as err:
        print(f"judge: caffeinate did not start ({err}); running anyway", file=sys.stderr)


def _require_live(what: str) -> None:
    if not live_checkout.is_live():
        raise SystemExit(f"{what} only runs from the live checkout ({live_checkout.LIVE_ROOT}); "
                         f"this is {live_checkout.REPO_ROOT}.")


def _run(args, stdout, runner, reader, me_text) -> int:
    cfg = load_judge_config()
    if not args.dry_run and cfg.enabled is not True:
        print(OFF_LINE, file=stdout)
        return 0
    if not args.dry_run:
        _require_live("the judge's run")
        _start_caffeinate()
    names = [n.strip() for n in args.companies.split(",") if n.strip()] if args.companies else None
    if not args.dry_run:
        runner = runner or judge_runner(cfg)
        reader = reader or build_reader(cfg, load_crawl_config())
    conn = connect()
    try:
        summary = run_judge(conn, runner, reader, cfg=cfg, me_text=me_text if me_text is not None else read_me(),
                            limit=args.limit, names=names, dry_run=args.dry_run)
    finally:
        conn.close()
    for line in summary.lines:
        print(line, file=stdout)
    print(summary.line(), file=stdout)
    return 0 if summary.status in _OK else 1


def _show(args, stdout) -> int:
    conn = connect()
    try:
        canon = canonical_ids(conn)
        names = {r[0]: r[1] for r in conn.execute("SELECT company_id, name FROM company")}
        judged = load_judgments(conn, canon)
        for i, s in enumerate(ranked_list(conn, limit=args.top), start=1):
            cid = canon.get(s.company_id, s.company_id)
            j = judged.get(cid)
            when = f" (judged {j.at[:10]}{', thin evidence' if j.thin else ''})" if j is not None else ""
            print(f"{i}. {names.get(cid, '?')} (company {cid}, score {s.score:g}): {judge_line(j)}{when}", file=stdout)
    finally:
        conn.close()
    return 0


def main(argv: list[str] | None = None, *, stdout=None, runner=None, reader=None, me_text: str | None = None) -> int:
    args = _parser().parse_args(argv)
    stdout = stdout if stdout is not None else sys.stdout
    if args.command == "run":
        return _run(args, stdout, runner, reader, me_text)
    if args.command == "show":
        return _show(args, stdout)
    from outreach.judge.evaluate import eval_command
    return eval_command(args, stdout, runner=runner, reader=reader, me_text=me_text)


if __name__ == "__main__":
    raise SystemExit(main())
