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
this runs on a scratch OUTREACH_DB or not at all. Only `night` calls a model.

`gold` works the labeled set (`outreach.reader.gold`, where the file format and the labeling rules live):

    uv run python -m outreach.reader gold [--file PATH] sample --per-class N --seed S   # needs the store
    uv run python -m outreach.reader gold [--file PATH] show [--limit N]
    uv run python -m outreach.reader gold [--file PATH] confirm <gold_id> [--set field=value --quote "..."]...
    uv run python -m outreach.reader gold [--file PATH] confirm --all-shown
    uv run python -m outreach.reader gold [--file PATH] coverage

`--file` defaults to ~/Library/Application Support/outreach/gold/items.jsonl (outside the repo, so the live checkout
stays fast-forwardable), and the readings live in `readings/` beside it. `show` remembers what it printed (a `.shown` file next to the items),
and `confirm --all-shown` confirms those items that are still pending and unchanged since. Only `sample` opens the
store.

`eval` and `promote` are the gate (`outreach.reader.evaluate`, where every threshold and its source is stated):

    uv run python -m outreach.reader eval [--version V] [--against OLD] [--run] [--json]
    uv run python -m outreach.reader promote --version V [--against OLD]

`eval` scores version V (default: the one the files make now) from `readings/<V>.jsonl` beside the gold file, prints the table
and the gate, and with `--against` the regressions (both versions scored on the items both read), exiting 1 if there
are any. Only `--run` calls a model: it reads the gold items through `claude -p` first (seat usage, never from a
test), and only for the version the files make now (exit 2 for any other `--version`). `promote` writes the `promoted_version`
line in config.toml only when the gate passes (exit 1 otherwise); `--against` defaults to the promoted version. It
refuses outside the live checkout unless OUTREACH_DB names a scratch store, and never commits. Neither opens the
store; after a promotion run `apply`.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from datetime import date
from pathlib import Path

import live_checkout
from outreach.reader.combine import apply_all, apply_reading
from outreach.reader import evaluate, gold
from outreach.reader.config import CONFIG_PATH, load_reader_config
from outreach.reader.evaluate import run_reader_on_gold
from outreach.reader.night import APPLIED, claude_runner, run_night
from outreach.reader.queue import batch_payload, enqueue_missing, next_batch
from outreach.reader.submit import submit
from outreach.reader.version import reader_version
from outreach.rules import RulesetNotCurrent
from outreach.store import body_sha256, connect
from outreach.store.db import db_path, is_live_path

__all__ = ["main"]

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
    _gold_parser(sub)
    ev = sub.add_parser("eval", help="score a reader version on the gold set, with the gate")
    ev.add_argument("--version", default=None, help="the reader version (default: the one the files make now)")
    ev.add_argument("--against", default=None, help="compare with this version and list its regressions")
    ev.add_argument("--run", action="store_true", help="read the gold set through claude -p first (seat usage)")
    ev.add_argument("--json", action="store_true", help="print JSON instead of the table")
    pr = sub.add_parser("promote", help="make a version decide postings, when the gate passes")
    pr.add_argument("--version", required=True)
    pr.add_argument("--against", default=None, help="the version it must not be worse than (default: promoted)")
    for p in (ev, pr):
        p.add_argument("--file", type=Path, default=None, help="the gold items (default: the real set)")
        p.add_argument("--readings", type=Path, default=None, help="the readings folder (default: beside the file)")
    return parser


def _gold_parser(sub) -> None:
    g = sub.add_parser("gold", help="the labeled set: sample, show, confirm, coverage")
    g.add_argument("--file", type=Path, default=None, help="the items file (default: the real set in Application Support)")
    gsub = g.add_subparsers(dest="gold_command", required=True)
    sample = gsub.add_parser("sample", help="draw postings from the store and append them prelabeled")
    sample.add_argument("--per-class", type=_positive, required=True)
    sample.add_argument("--seed", type=int, required=True)
    show = gsub.add_parser("show", help="print the next pending items for a sitting")
    show.add_argument("--limit", type=_positive, default=10)
    conf = gsub.add_parser("confirm", help="confirm an item, with corrections")
    conf.add_argument("gold_id", nargs="?")
    conf.add_argument("--set", dest="sets", action="append", default=[], metavar="FIELD=VALUE")
    conf.add_argument("--quote", dest="quotes", action="append", default=[])
    conf.add_argument("--all-shown", action="store_true", help="confirm what the last show printed, untouched")
    gsub.add_parser("coverage", help="confirmed against target per stratum")


def _label_value(field: str, text: str):
    """A --set value as the field holds it: a whole number (or null) for years, text otherwise."""
    if field in ("years_low", "years_high"):
        return None if text in ("", "null", "none") else int(text)
    return text


def _shown_path(items: Path) -> Path:
    return items.with_name(items.name + ".shown")


def _gold(args: argparse.Namespace, stdout) -> int:
    path = args.file if args.file is not None else gold.GOLD_PATH
    command = args.gold_command
    if command == "sample":
        items = gold.load_gold(path) if path.exists() else []
        conn = connect()  # the store guard: the live store opens only from the live checkout
        try:
            new = gold.sample_gold(conn, args.per_class, seed=args.seed,
                                   existing={body_sha256(i.body) for i in items}, first_id=gold.next_id(items))
        finally:
            conn.close()
        gold.append_items(path, new)
        print(f"gold: drew {len(new)} postings into {path}", file=stdout)
        return 0
    if not path.exists():
        print(f"gold: no gold file at {path}; run: uv run python -m outreach.reader gold sample", file=sys.stderr)
        return 2
    items = gold.load_gold(path)
    if command == "show":
        shown = gold.pending(items)[:args.limit]
        print(gold.show_text(shown), file=stdout)
        _shown_path(path).write_text(json.dumps({i.gold_id: gold.labels_digest(i) for i in shown}), encoding="utf-8")
        return 0
    if command == "coverage":
        print(gold.coverage_text(items), file=stdout)
        return 0
    today = date.today().isoformat()
    if args.all_shown:
        if args.gold_id or args.sets:
            print("gold confirm: --all-shown takes no id and no --set", file=sys.stderr)
            return 2
        marks = _shown_path(path)
        seen = json.loads(marks.read_text(encoding="utf-8")) if marks.exists() else {}
        by_id = {i.gold_id: i for i in items}
        done = 0
        for gold_id, digest in seen.items():
            item = by_id.get(gold_id)
            if item is None or item.status != "prelabeled":
                continue
            if gold.labels_digest(item) != digest:
                print(f"gold: {gold_id} changed after show printed it; confirm it by id", file=stdout)
                continue
            if not item.labels:
                print(f"gold: {gold_id} has no labels yet; nothing to confirm", file=stdout)
                continue
            gold.confirm(path, gold_id, today=today)
            done += 1
        print(f"gold: confirmed {done} of {len(seen)} shown", file=stdout)
        return 0
    if not args.gold_id:
        print("gold confirm: give an id or --all-shown", file=sys.stderr)
        return 2
    if len(args.sets) != len(args.quotes):
        print("gold confirm: each --set needs its --quote (use --quote \"\" for unknown)", file=sys.stderr)
        return 2
    try:
        fixes = {}
        for pair, quote in zip(args.sets, args.quotes):
            field, sep, value = pair.partition("=")
            if not sep:
                raise ValueError(f"--set {pair!r} is not field=value")
            fixes[field] = gold.FieldReading(_label_value(field, value), quote)
        item = gold.confirm(path, args.gold_id, set_fields=fixes, today=today)
    except ValueError as err:
        print(f"gold confirm: {err}", file=sys.stderr)
        return 1
    print(f"gold: {item.gold_id} confirmed on {item.confirmed_on}", file=stdout)
    return 0


def _promote_allowed() -> bool:
    """The live checkout, or a scratch OUTREACH_DB anywhere else."""
    if live_checkout.is_live():
        return True
    scratch = os.environ.get("OUTREACH_DB")
    return bool(scratch) and not is_live_path(db_path())


def _evaluate(args: argparse.Namespace, stdout) -> int:
    if args.command == "promote" and not _promote_allowed():
        raise SystemExit("promote only runs from the live checkout, or with OUTREACH_DB set to a scratch store; "
                         f"this is {live_checkout.REPO_ROOT}.")
    cfg = load_reader_config(CONFIG_PATH)
    current = reader_version(cfg)
    version = args.version or current
    path = args.file if args.file is not None else gold.GOLD_PATH
    folder = args.readings if args.readings is not None else path.parent / "readings"
    if not path.exists():
        print(f"{args.command}: no gold file at {path}; run: uv run python -m outreach.reader gold sample",
              file=sys.stderr)
        return 2
    items = gold.load_gold(path)
    against = args.against
    if args.command == "promote" and against is None:
        against = cfg.promoted_version or None
    if args.command == "eval" and args.run and version != current:
        # The prompt and model read with are the current files', so readings filed under another version would lie.
        print(f"eval --run: --version {version} is not the version the reader files make now ({current}); "
              "--run reads only under that one", file=sys.stderr)
        return 2
    try:
        if args.command == "eval" and args.run:
            run_reader_on_gold(items, claude_runner(cfg), cfg, version, folder=folder)
        readings = evaluate.load_readings(version, folder)
        old_readings = evaluate.load_readings(against, folder) if against else None
    except (OSError, RuntimeError, ValueError) as err:
        print(f"{args.command}: {err}", file=sys.stderr)
        return 2
    scores = evaluate.score(items, readings)
    shared = old = None
    if old_readings is not None:  # compared on the items both versions read
        shared, old = evaluate.shared_scores(items, readings, old_readings)
    if args.command == "promote":
        try:
            result = evaluate.promote(version, items, scores, old, CONFIG_PATH, readings=readings,
                                      current_version=current, new_shared=shared)
        except ValueError as err:
            print(str(err), file=stdout)
            return 1
        print(result.text(), file=stdout)
        print(f"\npromoted {version} in {CONFIG_PATH}. Next: uv run python -m outreach.reader apply (re-combines "
              "the store), then commit config.toml.", file=stdout)
        return 0
    result = evaluate.gate(items, scores, old, readings=readings, version=version, current_version=current,
                           new_shared=shared)
    regressions = evaluate.not_worse(shared, old) if old is not None else []
    if args.json:
        print(json.dumps({"version": version, "against": against, "scores": evaluate.scores_json(scores),
                          "regressions": regressions, "gate": {"verdict": result.verdict, "lines": result.lines}},
                         ensure_ascii=False), file=stdout)
    else:
        confirmed = sum(1 for i in items if i.status == "confirmed")
        read = sum(1 for i in items if i.status == "confirmed" and i.gold_id in readings)
        print(f"reader {version}: {confirmed} confirmed items, {read} read", file=stdout)
        print(evaluate.table_text(scores), file=stdout)
        print("", file=stdout)
        print(result.text(), file=stdout)
        if against:
            print(f"\nagainst {against}: " + (f"{len(regressions)} regressions" if regressions else "no regressions"),
                  file=stdout)
            for line in regressions:
                print(f"  {line}", file=stdout)
    return 1 if regressions else 0


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
    if args.command == "night":
        return _night(args, stdout)
    if args.command == "gold":
        return _gold(args, stdout)
    if args.command in ("eval", "promote"):
        return _evaluate(args, stdout)
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
