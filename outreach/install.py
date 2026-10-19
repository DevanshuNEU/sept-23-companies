"""`python -m outreach.install`: create the store if needed and apply pending migrations.

This is the only code that creates a store or migrates one. Task 15 adds the ruleset registration.
"""
import argparse
import os
import sqlite3
from pathlib import Path

from outreach.store.db import connect, db_path, guard, normalise
from outreach.store.migrate import migrate


def install(path: str) -> int:
    """Create (if new) and migrate the store at `path`. Returns its version."""
    target = normalise(path)
    guard(target)  # before any folder or file is made
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    is_new = not os.path.exists(target) or os.path.getsize(target) == 0
    conn = sqlite3.connect(target, isolation_level=None)
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
        if is_new:
            # Both must come before the first table: auto_vacuum cannot change once tables exist,
            # and the journal_mode write is what fixes it into the file header.
            conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
            conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return migrate(conn)
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(
        prog="python -m outreach.install",
        description="Create the store at OUTREACH_DB (default: the live path) and apply pending migrations.",
    ).parse_args(argv)
    path = db_path()
    version = install(path)
    print(f"{path}: store at version {version}")
    conn = connect(path)
    try:
        for row in conn.execute("SELECT version, name, applied_at FROM schema_migration ORDER BY version"):
            print(f"  {row['version']:04d} {row['name']} (applied {row['applied_at']})")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
