import contextlib
import io
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import live_checkout
from outreach import install
from outreach.store import db
from outreach.store.db import StoreError, connect, db_path, guard, is_live_path, transaction
from outreach.store.migrate import latest_version

ROOT = Path(__file__).resolve().parent.parent


class StoreTestCase(unittest.TestCase):
    def tmp(self) -> Path:
        folder = tempfile.TemporaryDirectory(prefix="outreach-connect-test-")
        self.addCleanup(folder.cleanup)
        return Path(folder.name)

    def fake_live(self, root: Path) -> Path:
        """Patch DEFAULT_PATH to <root>/live/outreach.db (folder made, file not) and return it."""
        live = root / "live"
        live.mkdir()
        target = live / "outreach.db"
        patcher = mock.patch.object(db, "DEFAULT_PATH", str(target))
        patcher.start()
        self.addCleanup(patcher.stop)
        return target

    def not_live_checkout(self):
        patcher = mock.patch.object(live_checkout, "is_live", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def live_checkout(self):
        patcher = mock.patch.object(live_checkout, "is_live", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)


class LivePathGuardTests(StoreTestCase):
    def test_tests_never_use_the_live_path(self):
        self.assertNotEqual(db_path(), os.path.realpath(db.DEFAULT_PATH))
        self.assertFalse(is_live_path(db_path()))

    @unittest.skipIf(
        live_checkout.is_live(),
        "in the live checkout the default path is allowed; the patched-path tests below cover the rule",
    )
    def test_default_path_is_refused_outside_live(self):
        home = self.tmp()
        env = {k: v for k, v in os.environ.items() if k != "OUTREACH_DB"}
        env["HOME"] = str(home)
        run = subprocess.run(
            [sys.executable, "-c", "import outreach.store as s; s.connect()"],
            cwd=ROOT, env=env, capture_output=True, text=True,
        )
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("live checkout", run.stdout + run.stderr)
        self.assertEqual(os.listdir(home), [])

    def test_other_spellings_of_the_live_path_are_refused(self):
        root = self.tmp()
        target = self.fake_live(root)
        self.not_live_checkout()
        (root / "link").symlink_to(root / "live")
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(root)
        spellings = {
            "symlinked folder": str(root / "link" / "outreach.db"),
            "dot-dot": f"{root}/live/../live/outreach.db",
            "relative": os.path.relpath(target, start=root),
            "other letter case": str(root / "LIVE" / "Outreach.db"),
        }
        # No store file exists yet: the letter-case spelling can only be caught by case-folding,
        # not by comparing files.
        self.assertFalse(target.exists())
        for name, spelling in spellings.items():
            with self.subTest(spelling=name, file_present=False):
                with self.assertRaises(SystemExit):
                    connect(spelling)
                with self.assertRaises(SystemExit):
                    install.install(spelling)
        self.assertEqual(os.listdir(root / "live"), [])
        # With the file present, every spelling is still refused.
        with mock.patch.object(live_checkout, "is_live", return_value=True):
            install.install(str(target))
        self.assertTrue(target.exists())
        for name, spelling in spellings.items():
            with self.subTest(spelling=name, file_present=True):
                with self.assertRaises(SystemExit):
                    connect(spelling)

    def test_hard_link_to_the_live_store_is_refused(self):
        root = self.tmp()
        target = self.fake_live(root)
        with mock.patch.object(live_checkout, "is_live", return_value=True):
            install.install(str(target))
        other = root / "other.db"
        os.link(target, other)
        self.not_live_checkout()
        with self.assertRaises(SystemExit):
            connect(str(other))

    def test_tilde_spelling_is_expanded_then_refused(self):
        home = self.tmp()
        live = home / "Library" / "Application Support" / "outreach"
        live.mkdir(parents=True)
        patcher = mock.patch.object(db, "DEFAULT_PATH", str(live / "outreach.db"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.not_live_checkout()
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            with self.assertRaises(SystemExit):
                connect("~/Library/Application Support/outreach/outreach.db")
        self.assertEqual(os.listdir(live), [])

    def test_guard_allows_other_paths(self):
        root = self.tmp()
        self.fake_live(root)
        self.not_live_checkout()
        guard(str(root / "elsewhere.db"))

    def test_live_path_opens_in_the_live_checkout(self):
        root = self.tmp()
        target = self.fake_live(root)
        self.live_checkout()
        install.install(str(target))
        conn = connect(str(target))
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], latest_version())

    def test_install_is_refused_on_the_live_path_outside_live(self):
        root = self.tmp()
        target = self.fake_live(root)
        self.not_live_checkout()
        with self.assertRaises(SystemExit):
            install.install(str(target))
        self.assertFalse(target.exists())

    def test_install_refused_before_creating_the_folder(self):
        root = self.tmp()
        target = root / "never" / "made" / "outreach.db"
        patcher = mock.patch.object(db, "DEFAULT_PATH", str(target))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.not_live_checkout()
        with self.assertRaises(SystemExit):
            install.install(str(target))
        self.assertFalse((root / "never").exists())


class ConnectTests(StoreTestCase):
    def installed(self, name="outreach.db") -> str:
        path = str(self.tmp() / name)
        install.install(path)
        return path

    def test_missing_store_is_not_created_by_connect(self):
        path = self.tmp() / "none.db"
        with self.assertRaises(StoreError) as caught:
            connect(str(path))
        self.assertIn("run: uv run python -m outreach.install", str(caught.exception))
        self.assertFalse(path.exists())

    def test_open_never_migrates(self):
        path = self.tmp() / "old.db"
        raw = sqlite3.connect(path)
        raw.execute("PRAGMA user_version = 0")
        raw.close()
        with self.assertRaises(StoreError) as caught:
            connect(str(path))
        self.assertIn("install", str(caught.exception))
        check = sqlite3.connect(path)
        self.addCleanup(check.close)
        self.assertEqual(check.execute("PRAGMA user_version").fetchone()[0], 0)
        self.assertEqual(check.execute("SELECT count(*) FROM sqlite_master").fetchone()[0], 0)

    def test_newer_store_is_refused(self):
        path = self.installed()
        raw = sqlite3.connect(path)
        raw.execute(f"PRAGMA user_version = {latest_version() + 1}")
        raw.close()
        with self.assertRaises(StoreError) as caught:
            connect(path)
        self.assertIn("older", str(caught.exception))

    def test_install_creates_wal_store_and_is_idempotent(self):
        path = self.tmp() / "outreach.db"
        first = install.install(str(path))
        second = install.install(str(path))
        self.assertEqual(first, second)
        self.assertEqual(first, latest_version())
        conn = connect(str(path))
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(conn.execute("PRAGMA auto_vacuum").fetchone()[0], 2)
        rows = conn.execute("SELECT version FROM schema_migration ORDER BY version").fetchall()
        self.assertEqual([r[0] for r in rows], list(range(1, latest_version() + 1)))

    def test_path_with_a_space_works(self):
        path = self.tmp() / "Application Support" / "outreach.db"
        install.install(str(path))
        conn = connect(str(path))
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], latest_version())

    def test_path_with_uri_characters_works(self):
        path = self.tmp() / "a#b c?d" / "outreach.db"
        install.install(str(path))
        conn = connect(str(path))
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], latest_version())

    def test_connection_pragmas(self):
        conn = connect(self.installed())
        self.addCleanup(conn.close)
        pragma = lambda name: conn.execute(f"PRAGMA {name}").fetchone()[0]
        self.assertEqual(pragma("foreign_keys"), 1)
        self.assertEqual(pragma("synchronous"), 2)
        self.assertEqual(pragma("busy_timeout"), 5000)
        self.assertEqual(pragma("temp_store"), 2)
        self.assertEqual(pragma("journal_size_limit"), 67108864)
        self.assertIsNone(conn.isolation_level)
        self.assertIs(conn.row_factory, sqlite3.Row)

    def test_connect_defaults_to_the_scratch_store(self):
        install.install(db_path())
        conn = connect()
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], latest_version())

    def test_transaction_commits(self):
        conn = connect(self.installed())
        self.addCleanup(conn.close)
        with transaction(conn):
            conn.execute(
                "INSERT INTO company(name, status_at, created_at) VALUES ('Acme', '2026-10-19T12:00:00Z', '2026-10-19T12:00:00Z')"
            )
        self.assertEqual(conn.execute("SELECT count(*) FROM company").fetchone()[0], 1)

    def test_transaction_rolls_back(self):
        conn = connect(self.installed())
        self.addCleanup(conn.close)
        with self.assertRaises(ZeroDivisionError):
            with transaction(conn):
                conn.execute(
                    "INSERT INTO company(name, status_at, created_at) VALUES ('Acme', '2026-10-19T12:00:00Z', '2026-10-19T12:00:00Z')"
                )
                1 / 0
        self.assertEqual(conn.execute("SELECT count(*) FROM company").fetchone()[0], 0)
        self.assertFalse(conn.in_transaction)


class InstallMainTests(StoreTestCase):
    def test_install_main_prints_version(self):
        path = self.tmp() / "outreach.db"
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"OUTREACH_DB": str(path)}), contextlib.redirect_stdout(out):
            self.assertEqual(install.main([]), 0)
        text = out.getvalue()
        self.assertIn(f"store at version {latest_version()}", text)
        self.assertIn(os.path.realpath(path), text)
        self.assertIn("initial", text)


if __name__ == "__main__":
    unittest.main()
