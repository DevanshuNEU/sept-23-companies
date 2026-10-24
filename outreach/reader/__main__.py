"""The posting reader on the command line (#32).

    uv run python -m outreach.reader next [--limit N]   # queue what is missing, lease a batch, print it as JSON
    uv run python -m outreach.reader submit < readings.json   # check and store readings, print one result each
    uv run python -m outreach.reader apply              # re-combine every stored reading with the rules

`next` and `submit` are what a side session uses: it reads the printed batch with the pinned prompt and pipes its
answer back. `submit` takes one reading object, a list of them, or {"readings": [...]}, prints a JSON list of
results (refusals are results, so the exit is 0), and exits 2 on input that is not JSON. Every stored or partial
reading is then combined with the rules (`apply_reading`).

The store is the one at OUTREACH_DB; the default (live) store opens only from the live checkout, so from a worktree
this runs on a scratch OUTREACH_DB or not at all. Nothing here calls a model: `night` (Task 6), `gold` (Task 7),
`eval` and `promote` (Task 8) are registered and not built yet.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys

from outreach.reader.combine import apply_all, apply_reading
from outreach.reader.config import load_reader_config
from outreach.reader.queue import batch_payload, enqueue_missing, next_batch
from outreach.reader.submit import submit
from outreach.rules import RulesetNotCurrent
from outreach.store import connect

__all__ = ["main"]

_LATER = {"night": "Task 6", "gold": "Task 7", "eval": "Task 8", "promote": "Task 8"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m outreach.reader",
                                     description="The posting reader: hand out postings, take readings back.")
    sub = parser.add_subparsers(dest="command", required=True)
    nxt = sub.add_parser("next", help="lease a batch of postings and print it as JSON")
    nxt.add_argument("--limit", type=int, default=None, help="postings in the batch (default: batch_size)")
    sub.add_parser("submit", help="read readings as JSON on stdin, check and store them")
    sub.add_parser("apply", help="combine every stored reading at the promoted version with the rules")
    for name, task in _LATER.items():
        sub.add_parser(name, help=f"not built yet ({task})")
    return parser


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
                    if r.status in ("stored", "partial"):
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
