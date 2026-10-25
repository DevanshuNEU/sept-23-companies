"""Shared helpers for the import tests.

`fixture_manifest(tmp)` copies the synthetic sources under `tests/fixtures/import/` into `tmp`, builds the fixture
radar.db there, and returns a manifest of the copies. Nothing here points at a real source.
"""
import importlib.util
import shutil
import sqlite3
from pathlib import Path

from outreach.importer.manifest import Manifest

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "import"


def _build_radar(path: Path) -> Path:
    spec = importlib.util.spec_from_file_location("import_fixture_build_radar", FIXTURES / "build_radar.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build(path)


def fixture_manifest(tmp: Path) -> Manifest:
    """A fresh copy of the fixture sources under `tmp/sources`, and a manifest naming them."""
    root = Path(tmp) / "sources"
    shutil.copytree(FIXTURES, root, ignore=shutil.ignore_patterns("__pycache__", "build_radar.py"))
    radar = _build_radar(root / "radar.db")
    return Manifest(
        paths={
            "data_js": root / "data.js",
            "local_storage": root / "outreach-backup-2026-10-22.json",
            "contacts_json": root / "mail" / "contacts.json",
            "send_log": root / "mail" / "send.log",
            "radar_db": radar,
            "devboard": root / "job-state.json",
            "contacted_txt": root / "contacted.txt",
            "devvault_notes": root / "companies",
            "interview_prep": root / "interview-prep" / "companies",
            "curated_touches": root / "curated_touches.csv",
        },
        overrides=None,
    )


def make_contacts_empty(manifest: Manifest) -> None:
    """The old mailer that never sent: `contacts.json` parses and holds no contacts, and `send.log` has only lines
    that name no send (a sign-in note and a dry run). Both files are the copies under the manifest."""
    manifest.paths["contacts_json"].write_text('{"contacts": []}\n', encoding="utf-8")
    log = manifest.paths["send_log"]
    keep = [line for line in log.read_text(encoding="utf-8").splitlines()
            if "nothing sent" in line or "(dry run)" in line]
    log.write_text("\n".join(keep) + "\n", encoding="utf-8")


def all_marks(conn: sqlite3.Connection) -> dict[str, tuple[int, int]]:
    """`(row count, highest rowid)` for every table in the store, so a test can show a run wrote nothing.

    A WITHOUT ROWID table has no rowid, so its second number is 0 and only its row count is watched.
    """
    marks = {}
    for name, sql in conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall():
        if "WITHOUT ROWID" in (sql or "").upper():
            marks[name] = (conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0], 0)
        else:
            marks[name] = tuple(conn.execute(f'SELECT count(*), coalesce(max(rowid), 0) FROM "{name}"').fetchone())
    return marks
