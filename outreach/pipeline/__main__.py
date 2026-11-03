"""`python -m outreach.pipeline <command>`: the pipeline scorecard by hand (#153).

- `scorecard [--json] [--record]`: Knock measured against SimplifyJobs' new grad list: coverage, level recall, lead,
  detection lag and the companies Knock misses most. Prints the card; `--record` also stores it as the day's
  `pipeline_scorecard` event (a second run the same New York day stores nothing).

Reads the store through `outreach.store.connect()`, so the live store opens only from the live checkout. No requests:
the feed is polled by the crawl tick, not here. Only `--record` writes.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence

from outreach.net import FetchError
from outreach.pipeline import scorecard
from outreach.pipeline.scorecard import Scorecard
from outreach.store import StoreError, connect, describe_error, utcnow

__all__ = ["main"]

_EXPECTED = (ValueError, OSError, FetchError, StoreError, sqlite3.Error)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m outreach.pipeline", description="The pipeline scorecard (#153).")
    sub = parser.add_subparsers(dest="command", required=True)
    card = sub.add_parser("scorecard", help="measure Knock against SimplifyJobs' new grad list")
    card.add_argument("--json", action="store_true", help="print the card as JSON")
    card.add_argument("--record", action="store_true", help="also store the card as today's event")
    return parser


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def render(card: Scorecard) -> str:
    """The card as plain text."""
    hours = "n/a" if card.detection_p95_hours is None else f"{card.detection_p95_hours:g} h"
    lines = [
        f"SimplifyJobs listings (last 30 days): {card.simplify_listings}",
        f"Coverage: {_percent(card.simplify_coverage)} of them are postings Knock holds",
        f"Level recall: {_percent(card.level_recall)} of those Knock rates in level",
        f"Lead: {_percent(card.lead_over_simplify)} Knock saw before SimplifyJobs did (backfill left out)",
        f"Detection p95: {hours} from a posting's date to Knock's first sight (Greenhouse, Ashby, Lever)",
    ]
    if card.gap_top:
        lines += ["", "Gap: the companies Knock misses most", f"  {'listings':>8}  {'ats':<12} company"]
        for g in card.gap_top:
            known = "board" if g.knock_has_board else "company, no board" if g.knock_knows_company else "unknown"
            lines.append(f"  {g.listings:>8}  {g.ats or '-':<12} {g.company_name} ({known})")
    return "\n".join(lines)


def _scorecard(as_json: bool, record: bool) -> int:
    conn = connect()
    try:
        now = utcnow()
        card = scorecard.compute(conn, now)
        if record:
            scorecard.record(conn, card, now)
    finally:
        conn.close()
    print(json.dumps(card.to_dict(), indent=2) if as_json else render(card))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _scorecard(args.json, args.record)
    except _EXPECTED as err:
        print(f"error: {describe_error(err)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
