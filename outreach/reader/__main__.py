"""The posting reader on the command line (#32).

    uv run python -m outreach.reader next [--limit N]   # queue what is missing, lease a batch, print it as JSON
    uv run python -m outreach.reader submit < readings.json   # check and store readings, print one result each
    uv run python -m outreach.reader apply              # re-combine every stored reading with the rules
    python -m outreach.reader night [--max-batches N] [--max-postings N]   # the night job, live checkout only

`next` and `submit` are what a side session uses: it reads the printed batch with the pinned prompt and pipes its
answer back. `submit` takes one reading object, a list of them, or {"readings": [...]}, prints a JSON list of
results (refusals are results, so the exit is 0), and exits 2 on input that is not JSON. Every stored or partial
reading, and every posting that ended refused, is then combined with the rules (`apply_reading`), so a posting the
reader gave up on is shown, flagged, at once.

`night` reads the queue through `claude -p` (`outreach.reader.night`) and prints one summary line. It refuses to run
outside the live checkout before it reads the config or opens anything, and exits 1 when the night stopped on a
login problem or failed calls.

The store is the one at OUTREACH_DB; the default (live) store opens only from the live checkout, so from a worktree
this runs on a scratch OUTREACH_DB or not at all. Only `night` calls a model. `gold` (Task 7), `eval` and `promote`
(Task 8) are registered and not built yet.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys

import live_checkout
from outreach.reader.combine import apply_all, apply_reading
from outreach.reader.config import load_reader_config
from outreach.reader.night import APPLIED, claude_runner, run_night
from outreach.reader.queue import batch_payload, enqueue_missing, next_batch
from outreach.reader.submit import submit
from outreach.rules import RulesetNotCurrent
from outreach.store import connect

__all__ = ["main"]

_LATER = {"gold": "Task 7", "eval": "Task 8", "promote": "Task 8"}
_NIGHT_OK = ("queue_empty", "cap_reached", "session_limit")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m outreach.reader",
                                     description="The posting reader: hand out postings, take readings back.")
    sub = parser.add_subparsers(dest="command", required=True)
    nxt = sub.add_parser("next", help="lease a batch of postings and print it as JSON")
    nxt.add_argument("--limit", type=int, default=None, help="postings in the batch (default: batch_size)")
    sub.add_parser("submit", help="read readings as JSON on stdin, check and store them")
    sub.add_parser("apply", help="combine every stored reading at the promoted version with the rules")
    night = sub.add_parser("night", help="read the queue through claude -p (live checkout only)")
    night.add_argument("--max-batches", type=_positive, default=None, help="calls tonight (default: config)")
    night.add_argument("--max-postings", type=_positive, default=None, help="postings tonight (default: config)")
    for name, task in _LATER.items():
        sub.add_parser(name, help=f"not built yet ({task})")
    return parser


def _positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {value}")
    return value


def _night(args: argparse.Namespace, stdout) -> int:
    live_checkout.require_live("the reader night job", live_checkout.REPO_ROOT, live_checkout.LIVE_ROOT)
    cfg = load_reader_config()
    if args.max_batches is not None:
        cfg = dataclasses.replace(cfg, max_batches=args.max_batches)
    if args.max_postings is not None:
        cfg = dataclasses.replace(cfg, max_postings_per_night=args.max_postings)
    conn = connect()
    try:
        summary = run_night(conn, claude_runner(cfg), cfg)
    finally:
        conn.close()
    print(summary.line(), file=stdout)
    return 0 if summary.status in _NIGHT_OK else 1


def _readings(text: str):
    """The readings in `text`: a list, one object, or {"readings": [...]}. ValueError when it is not JSON."""
    data = json.loads(text)
    if isinstance(data, dict) and set(data) == {"readings"}:
        return data["readings"]
    return data


def main(argv: list[str] | None = None, *, stdin=None, stdout=None) -> int:
    args = _parser().parse_args(argv)
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    if args.command in _LATER:
        print(f"{args.command} is not built yet ({_LATER[args.command]} of #32)", file=sys.stderr)
        return 2
    if args.command == "night":
        return _night(args, stdout)
    cfg = load_reader_config()
    conn = connect()  # the store guard: the live store opens only from the live checkout
    try:
        if args.command == "next":
            limit = args.limit if args.limit is not None else cfg.batch_size
            enqueue_missing(conn, cfg=cfg)
            print(json.dumps(batch_payload(conn, next_batch(conn, limit, cfg=cfg)), ensure_ascii=False), file=stdout)
            return 0
        if args.command == "submit":
            try:
                readings = _readings(stdin.read())
            except ValueError as err:
                print(f"submit: input is not JSON: {err}", file=sys.stderr)
                return 2
            results = submit(conn, readings, cfg=cfg)
            print(json.dumps([dataclasses.asdict(r) for r in results], ensure_ascii=False), file=stdout)
            try:
                for r in results:
                    if r.status in APPLIED:
                        apply_reading(conn, r.posting_id, cfg=cfg)
            except RulesetNotCurrent as err:
                print(f"submit: the readings are stored but not applied: {err}", file=sys.stderr)
                return 1
            return 0
        print(apply_all(conn, cfg=cfg), file=stdout)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
