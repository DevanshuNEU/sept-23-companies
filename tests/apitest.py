"""Base class for API tests.

Every test gets its own migrated scratch store and a `TestClient` that talks to the app in process: nothing here
binds a port, opens the live store or calls the mail API. The client looks like Devanshu's own page (loopback host,
matching Origin), so a test that wants a foreign page overrides those headers on the call.
"""
import json
import shutil
import sys
import tempfile
import textwrap
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

import outreach.api.routers as routers_package
from outreach.api.app import create_app
from outreach.api.settings import Settings
from tests.storetest import fresh_store

BASE_URL = "http://127.0.0.1:3100"
ORIGIN = "http://127.0.0.1:3100"
NOW = "2026-11-10T15:14:00Z"


class ApiCase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        folder = Path(tempfile.mkdtemp(prefix="outreach-api-test-"))
        self.addCleanup(shutil.rmtree, folder, True)
        banned = folder / "banned_phrases.txt"
        banned.write_text("synergy\n", encoding="utf-8")
        self.now = NOW
        self.old_sender = False  # whether the old mailer's launchd job reads as loaded (a test sets it)
        self.mx_resolvers = []  # no network: a mail-server lookup asks nobody (unknown) unless a test adds one
        self.settings = Settings(
            db_path=self.conn.execute("PRAGMA database_list").fetchone()["file"],
            host="127.0.0.1",
            port=3100,
            dev_origins=frozenset(),
            web_dist=folder / "dist",
            banned_phrases=banned,
            busy_timeout_ms=200,
            sweeper=False,
        )
        self.build()

    def build(self):
        """(Re)build the app and the client for this test's store."""
        self.app = create_app(self.settings, clock=lambda: self.now, mx_resolvers=self.mx_resolvers,
                              old_sender_loaded=lambda: self.old_sender)
        # raise_server_exceptions=False: a bug is the clean 500 envelope a browser would see, and the test asserts it.
        self.client = TestClient(
            self.app, base_url=BASE_URL, headers={"Origin": ORIGIN}, raise_server_exceptions=False
        )
        self.addCleanup(self.client.close)

    def use_routers(self, **sources: str):
        """Add throwaway router modules (name -> source) to outreach.api.routers for this test, then rebuild.

        Each source defines `router`. They are removed again when the test ends, so no test leaves a route behind.
        """
        folder = tempfile.mkdtemp(prefix="outreach-api-routers-")
        self.addCleanup(shutil.rmtree, folder, True)
        for name, source in sources.items():
            Path(folder, f"{name}.py").write_text(textwrap.dedent(source), encoding="utf-8")
        routers_package.__path__.append(folder)

        def remove():
            routers_package.__path__.remove(folder)
            for name in sources:
                sys.modules.pop(f"outreach.api.routers.{name}", None)

        self.addCleanup(remove)
        self.build()

    def set_now(self, iso: str) -> None:
        self.now = iso

    def get(self, path: str, **kwargs):
        return self.client.get(path, **kwargs)

    def post(self, path: str, body=None, key: str | None = None, headers: dict | None = None):
        sent = {"Content-Type": "application/json", "Idempotency-Key": key or str(uuid.uuid4())}
        sent.update(headers or {})
        return self.client.post(path, content=json.dumps({} if body is None else body), headers=sent)
