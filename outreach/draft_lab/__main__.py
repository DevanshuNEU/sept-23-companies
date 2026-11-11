"""The draft lab on the command line (#211).

    python -m outreach.draft_lab judge --a A.json --b B.json [--mode body|inbox] [--seed N] [--out FILE] [--dry-run]
    python -m outreach.draft_lab export --sample N [--state approved|drafted|sent] [--seed N] --out FILE

`judge` sends two sets of drafts to GPT (Codex CLI, no web search) and scores the blind reply. `export` samples stored
first emails into a set file; it only reads the store. Both send names, titles and drafts to OpenAI or read the live
store, so a real run only starts in the live checkout. `judge --dry-run` prints the prompt and calls nothing.

Exit 0 on a scored run; 1 when the judge failed or its reply could not be read.
"""
import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import live_checkout
from outreach.draft_lab import judge
from outreach.research.gpt import CodexRunner
from outreach.store.db import connect

__all__ = ["main"]

DEFAULT_SEED = 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m outreach.draft_lab",
                                     description="Blind GPT judge for two sets of cold-email drafts.")
    sub = parser.add_subparsers(dest="command", required=True)
    j = sub.add_parser("judge", help="judge set b against set a (live checkout only, unless --dry-run)")
    j.add_argument("--a", required=True, help="baseline set file (JSON)")
    j.add_argument("--b", required=True, help="candidate set file (JSON)")
    j.add_argument("--mode", choices=("body", "inbox"), default="body")
    j.add_argument("--seed", type=int, default=DEFAULT_SEED, help="shuffle seed; the report prints it")
    j.add_argument("--out", help="report path (default .context/draft-lab/<UTC timestamp>.md)")
    j.add_argument("--dry-run", action="store_true", help="print the prompt; call nothing")
    e = sub.add_parser("export", help="sample stored first emails into a set file (live checkout only)")
    e.add_argument("--sample", required=True, type=int, help="how many emails")
    e.add_argument("--state", choices=("approved", "drafted", "sent"), default="approved")
    e.add_argument("--seed", type=int, default=DEFAULT_SEED)
    e.add_argument("--out", required=True, help="set file to write (JSON)")
    return parser


def _require_live(what: str) -> None:
    if not live_checkout.is_live():
        raise SystemExit(f"{what} only runs from the live checkout ({live_checkout.LIVE_ROOT}); "
                         f"this is {live_checkout.REPO_ROOT}.")


def _summary(result, mode: str) -> str:
    if mode == "body":
        return (f"b wins {result.wins_b} of {result.compared}, "
                f"human {result.avg_b['human']:.1f} vs {result.avg_a['human']:.1f}, "
                f"reply {result.avg_b['reply']:.1f} vs {result.avg_a['reply']:.1f}: {judge.verdict(result)}")
    r = result
    return (f"first picks a {r.first_a}, b {r.first_b}, decoy {r.first_decoy}; "
            f"opened a {r.opened_a}, b {r.opened_b}, decoy {r.opened_decoy} ({r.items} items)")


def _default_report_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(live_checkout.REPO_ROOT) / ".context" / "draft-lab" / f"{stamp}.md"


def _judge(args, stdout, runner) -> int:
    if not args.dry_run:
        _require_live("the draft judge")
    pairs, only = judge.pair_sets(judge.load_set(args.a), judge.load_set(args.b))
    if only:
        print(f"ids in only one set, skipped: {', '.join(only)}", file=stdout)
    if not pairs:
        print("judge failed: the two sets share no ids", file=stdout)
        return 1
    if args.mode == "body":
        prompt, key = judge.body_prompt(pairs, seed=args.seed)
    else:
        prompt, key = judge.inbox_prompt(pairs, seed=args.seed, decoys=judge.load_decoys())
    if args.dry_run:
        print(prompt, file=stdout)
        return 0
    try:
        reply = judge.parse_reply((runner or CodexRunner(search=False)).run(prompt))
    except Exception as err:  # a failed run or an unreadable reply scores nothing
        print(f"judge failed: {err}", file=stdout)
        return 1
    result = judge.score_body(reply, key, pairs) if args.mode == "body" else judge.score_inbox(reply, key)
    out = Path(args.out) if args.out else _default_report_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(judge.report(result, seed=args.seed, mode=args.mode), encoding="utf-8")
    print(_summary(result, args.mode), file=stdout)
    print(f"report: {out}", file=sys.stderr)
    return 0


def _export(args, stdout) -> int:
    _require_live("the draft export")
    conn = connect()
    rows = conn.execute(
        "SELECT m.message_id, m.subject, m.body, p.title, c.name FROM message m "
        "JOIN touch t ON t.touch_id = m.touch_id JOIN person p ON p.person_id = t.person_id "
        "JOIN company c ON c.company_id = p.company_id WHERE m.seq = 0 AND m.state = ? ORDER BY m.message_id",
        (args.state,)).fetchall()
    picked = random.Random(args.seed).sample(rows, min(args.sample, len(rows)))
    out = [{"id": str(r["message_id"]), "recipient": f"{r['title'] or 'Engineer'} at {r['name']}",
            "subject": r["subject"], "body": r["body"]} for r in picked]
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(out)} of {len(rows)} {args.state} first emails to {path}", file=stdout)
    return 0


def main(argv: list[str] | None = None, *, stdout=None, runner=None) -> int:
    args = _parser().parse_args(argv)
    stdout = stdout if stdout is not None else sys.stdout
    return _judge(args, stdout, runner) if args.command == "judge" else _export(args, stdout)


if __name__ == "__main__":
    raise SystemExit(main())
