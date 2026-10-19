"""Ordered, forward-only migrations, one transaction each.

Ported from Job Radar `store.py` (split_sql, migrate). Files are `NNNN_name.sql` in
`MIGRATIONS_DIR`, numbered from 0001 with no gaps. Statements run one at a time,
never through `executescript`, which commits implicitly and would end the
transaction opened here.
"""
import re
import sqlite3
from pathlib import Path

from outreach.store.timefmt import utcnow

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

_FILE_NAME = re.compile(r"^(\d{4})_([A-Za-z0-9_]+)\.sql$")


def split_sql(sql: str) -> list[str]:
    """Split a SQL script into statements using SQLite's own parser.

    Splitting on ";" breaks on a semicolon inside a comment, which cuts a table
    definition in half. `sqlite3.complete_statement` is comment-aware and
    quote-aware, so a buffer that ends mid-definition after such a comment is
    correctly seen as incomplete. A trailing fragment is kept so a malformed
    script fails when executed instead of being dropped.
    """
    stmts, buf = [], ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                stmts.append(buf.strip())
            buf = ""
    if buf.strip():
        stmts.append(buf.strip())
    return stmts


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[tuple[int, str, Path]]:
    """The migration files as (version, name, path), sorted by version.

    Names starting with "." (such as `.gitkeep`) are ignored. Any other file that
    is not `NNNN_name.sql`, a duplicate number, or a gap (the numbers must run
    1, 2, 3 and so on) raises ValueError.
    """
    found: list[tuple[int, str, Path]] = []
    for path in sorted(Path(directory).iterdir()):
        if path.name.startswith("."):
            continue
        m = _FILE_NAME.match(path.name)
        if not m:
            raise ValueError(f"migration file name must be NNNN_name.sql: {path.name}")
        found.append((int(m.group(1)), m.group(2), path))
    found.sort(key=lambda f: f[0])
    for expected, (version, _name, path) in enumerate(found, start=1):
        if version != expected:
            kind = "duplicate number" if version < expected else "gap"
            raise ValueError(
                f"migration numbers must run 1, 2, 3 with none missing or repeated: "
                f"{kind} at {path.name} (expected {expected:04d})"
            )
    return found


def latest_version(directory: Path = MIGRATIONS_DIR) -> int:
    files = migration_files(directory)
    return files[-1][0] if files else 0


def migrate(conn: sqlite3.Connection, directory: Path = MIGRATIONS_DIR) -> int:
    """Apply pending migrations and return the new version.

    Forward only. Each file runs in its own BEGIN IMMEDIATE, so a failure rolls
    back the whole step and leaves `user_version` untouched. Refuses a database
    newer than the files: older code must not serve a newer store. `conn` must
    have `isolation_level=None` so this code alone controls transactions.
    """
    if conn.isolation_level is not None:
        raise ValueError("migrate needs a connection with isolation_level=None")
    files = migration_files(directory)
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    latest = files[-1][0] if files else 0
    if current > latest:
        raise RuntimeError(
            f"database is at version {current}, newer than the {latest} migrations "
            f"this code knows. Refusing to run: older code must not serve a newer store."
        )
    for version, name, path in files:
        if version <= current:
            continue
        statements = split_sql(path.read_text(encoding="utf-8"))
        conn.execute("BEGIN IMMEDIATE")
        try:
            for stmt in statements:
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO schema_migration(version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, utcnow()),
            )
            conn.execute(f"PRAGMA user_version = {version}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return conn.execute("PRAGMA user_version").fetchone()[0]
