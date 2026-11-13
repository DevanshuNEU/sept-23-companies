"""`python -m outreach.h1b build`: write companies.md and unsure.md from the downloaded USCIS files (#252).

It reads the store read only and sends no request. Defaults: the files in `<sources>/h1b-uscis`, the output in an
`h1b` folder beside the store.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from outreach.h1b import build
from outreach.sources.paths import sources_dir
from outreach.store.db import db_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m outreach.h1b", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="write companies.md and unsure.md from the USCIS files")
    b.add_argument("--db", help="the store (default: OUTREACH_DB or the live store)")
    b.add_argument("--source", help="the folder of USCIS CSV files (default: <sources>/h1b-uscis)")
    b.add_argument("--out", help="where the markdown goes (default: an h1b folder beside the store)")
    b.add_argument("--years", type=int, nargs="+", help="the fiscal years shown (default: every year in the files)")
    args = parser.parse_args(argv)

    db = Path(args.db or db_path())
    source = Path(args.source) if args.source else sources_dir() / "h1b-uscis"
    out = Path(args.out) if args.out else db.parent / "h1b"
    if not any(source.glob("*.csv")):
        print(f"No USCIS CSV files in {source}. Download them first (see outreach/h1b/__init__.py).", file=sys.stderr)
        return 1
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        report = build(conn, source, out, years=args.years)
    finally:
        conn.close()
    print(f"{report.matched} companies matched, {report.unsure} to check, from {report.employers} USCIS employers "
          f"(fiscal years {report.years[0]} to {report.years[-1]}). Wrote {out / 'companies.md'} and "
          f"{out / 'unsure.md'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
