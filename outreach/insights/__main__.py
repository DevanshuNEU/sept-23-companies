"""`python -m outreach.insights <command>`: the interview companies, what they share, and the export (#81).

- `summary`: the counts line, the interview companies one per line, what they share with its compare lines, and the
  possible duplicates. Company names only: no person, address or profile URL. Read only.
- `export --format csv|json [--out PATH] [--with-people] [--force]`: the outcome dataset as a file, company level
  unless `--with-people` (names only). With no `--out` it goes to `export_dir` in `config.toml`, named for the New
  York date. Never inside the repo; an existing file needs `--force`. Read only on the store.
- `duplicates [--raise]`: the possible duplicate pairs; `--raise` turns each into one `merge_candidate` item he
  settles in Needs you, and prints how many were new. The only command that writes.

Every command opens the store with `connect()`, which refuses the live store outside the live checkout; with a
scratch `OUTREACH_DB` it runs anywhere. Exit 0 on success, 1 with the message on stderr.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from contextlib import contextmanager

from outreach.insights.bands import board_words
from outreach.insights.config import default_insights_config
from outreach.insights.dataset import OutcomeRow, interview_rows, load_outcome_rows
from outreach.insights.duplicates import DuplicatePair, possible_duplicates, raise_duplicate_items
from outreach.insights.export import (COMPANY_FIELDS, PEOPLE_FIELDS, REPO_ROOT, resolve_out, to_records, write_csv,
                                      write_json)
from outreach.insights.findings import Shared, shared_findings
from outreach.outcomes.windows import ny_day
from outreach.ranking.scores import latest_version, ranked_list
from outreach.store import StoreError, connect, utcnow

__all__ = ["main", "REPO_ROOT"]

_EXPECTED = (ValueError, StoreError, sqlite3.Error, OSError)  # ExportError and MergeLoop are ValueErrors
_WAY_IN = {"linkedin_request": "LinkedIn request", "linkedin_message": "LinkedIn message", "email": "email",
           "application": "application", "hn_reply": "HN reply"}
NOT_KNOWN = "not known"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m outreach.insights",
                                description="Where your interviews come from (#81).")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("summary", help="the interview companies, what they share and possible duplicates")
    export = sub.add_parser("export", help="write the outcome dataset as CSV or JSON, outside the repo")
    export.add_argument("--format", choices=("csv", "json"), required=True)
    export.add_argument("--out", default=None, metavar="PATH", help="a file or a folder (default: export_dir)")
    export.add_argument("--with-people", action="store_true",
                        help="add the name of the person behind the best outcome (never an address or a URL)")
    export.add_argument("--force", action="store_true", help="replace an existing file")
    dup = sub.add_parser("duplicates", help="companies that may be one; --raise makes Needs-you merge items")
    dup.add_argument("--raise", dest="raise_items", action="store_true",
                     help="raise one merge item per pair for Needs you")
    return p


@contextmanager
def _snapshot(conn: sqlite3.Connection):
    """One read transaction, so every query sees the same store; rolled back, so nothing can be written in it."""
    conn.execute("BEGIN")
    try:
        yield conn
    finally:
        if conn.in_transaction:
            conn.execute("ROLLBACK")


def _plural(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def _way_in(channel: str | None) -> str:
    return _WAY_IN.get(channel, "no recorded way in") if channel else "no recorded way in"


def _company_line(r: OutcomeRow) -> str:
    on = r.interview_on or r.offer_on
    way = _way_in(r.best_channel)
    level = r.features.level_band
    parts = [f"{r.best_outcome}" + (f" on {on}" if on else ""), way if way.startswith("no ") else f"way in {way}",
             f"size {r.features.size_band or NOT_KNOWN}", f"stage {r.features.stage_band or NOT_KNOWN}",
             level if level and level.startswith("level ") else f"level {level or NOT_KNOWN}",
             f"board {board_words(r.board_system) if r.board_system else NOT_KNOWN}"]
    return f"  {r.name}: " + ", ".join(parts)


def _pair_line(p: DuplicatePair) -> str:
    return f"  {p.a_name} (#{p.a_id}) and {p.b_name} (#{p.b_id}): {p.detail} ({p.basis}, {p.confidence})"


def _summary(rows: list[OutcomeRow], shared: list[Shared], pairs: list[DuplicatePair], ranked: int) -> str:
    mine = sorted(interview_rows(rows), key=lambda r: (r.interview_on or r.offer_on or "", r.name), reverse=True)
    lines = []
    if mine:
        head = (f"{_plural(len(mine), 'company', 'companies')} interviewed you, out of {len(rows):,} you reached.")
    else:
        head = (f"No interviews logged yet. When you log one on Today, or a reply turns into an interview, the "
                f"company shows up here. You reached {_plural(len(rows), 'company', 'companies')}.")
    if ranked:
        head += f" {_plural(ranked, 'company is', 'companies are')} ranked."
    lines.append(head)
    offers = sum(1 for r in mine if r.best_outcome == "offer")
    if offers:
        lines.append(f"{_plural(offers, 'offer', 'offers')} among them.")
    if mine:
        lines.append("")
        lines.append("Interview companies (newest first):")
        lines.extend(_company_line(r) for r in mine)
    lines.append("")
    lines.append("What they share:")
    for s in shared:
        lines.append(f"  {s.text}")
        if s.compare_text:
            lines.append(f"    {s.compare_text}")
    lines.append("")
    lines.append("Possible duplicates (merge them in Needs you so they count once):" if pairs
                 else "Possible duplicates: none found.")
    lines.extend(_pair_line(p) for p in pairs)
    return "\n".join(lines)


def _run(args: argparse.Namespace, conn: sqlite3.Connection) -> None:
    cfg = default_insights_config()
    if args.command == "summary":
        with _snapshot(conn):
            rows = load_outcome_rows(conn)
            pairs = possible_duplicates(conn, rows)
            ranked = len(ranked_list(conn))
        print(_summary(rows, shared_findings(rows, cfg), pairs, ranked))
    elif args.command == "export":
        today = ny_day(utcnow())
        path = resolve_out(args.out, args.format, today, cfg, force=args.force)
        with _snapshot(conn):
            rows = load_outcome_rows(conn, with_people=args.with_people)
            version = latest_version(conn)
        records = to_records(rows, with_people=args.with_people)
        fields = COMPANY_FIELDS + (PEOPLE_FIELDS if args.with_people else ())
        if args.format == "csv":
            write_csv(records, fields, path)
        else:
            write_json(records, fields, path, {"generated_at": utcnow(), "ranker_version": version})
        print(f"Wrote {_plural(len(records), 'company', 'companies')} to {path}")
    else:
        with _snapshot(conn):
            pairs = possible_duplicates(conn, load_outcome_rows(conn, scores=False))
        print(f"{_plural(len(pairs), 'possible duplicate pair', 'possible duplicate pairs')}:" if pairs
              else "No possible duplicates.")
        for p in pairs:
            print(_pair_line(p))
        if args.raise_items:
            new = raise_duplicate_items(conn, pairs)
            print(f"Raised {new} new merge {'item' if new == 1 else 'items'} for Needs you.")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    conn = None
    try:
        conn = connect()
        _run(args, conn)
    except _EXPECTED as err:
        print(f"insights {args.command}: {err}", file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
