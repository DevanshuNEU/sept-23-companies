"""Shared helpers for the MCP tests: a scratch store, a fixed clock, and calls through the official SDK's in-process
client (`mcp.Client` on the server object, legacy initialize handshake, JSON-RPC over memory streams), so every
call crosses the same protocol layer Claude Code uses."""
import hashlib
import json
import sqlite3
import unittest

import anyio
from mcp import Client

from outreach.mcp.server import build_server
from tests.storetest import fresh_store

NOW = "2026-11-10T15:14:00Z"  # a Tuesday; 10:14 in New York


def store_path(conn: sqlite3.Connection) -> str:
    return next(r["file"] for r in conn.execute("PRAGMA database_list") if r["name"] == "main")


def call(server, name: str, arguments: dict) -> tuple[bool, dict]:
    """(is_error, payload) of one tool call. The result must be one text block of JSON equal to the structured
    content, so a client that reads either sees the same thing."""
    async def go():
        async with Client(server, mode="legacy") as client:
            return await client.call_tool(name, arguments)

    result = anyio.run(go)
    if len(result.content) != 1 or result.content[0].type != "text":
        raise AssertionError(f"expected one text block, got {result.content!r}")
    payload = json.loads(result.content[0].text)
    if payload != result.structured_content:
        raise AssertionError("the text block and the structured content differ")
    return bool(result.is_error), payload


def list_tools(server) -> list:
    async def go():
        async with Client(server, mode="legacy") as client:
            return (await client.list_tools()).tools

    return anyio.run(go)


def fingerprint(conn: sqlite3.Connection) -> str:
    """A hash of every row of every table, for "the store did not change" assertions."""
    digest = hashlib.sha256()
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                                 "ORDER BY name"):
        digest.update(table.encode())
        for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1"):
            digest.update(repr(tuple(row)).encode())
    return digest.hexdigest()


class McpCase(unittest.TestCase):
    """A scratch store (`self.db`, unguarded, for seeding and reading back) and a server on it with a fixed clock."""

    specs = None  # None: the server's real tools

    def setUp(self):
        self.db = fresh_store()
        self.addCleanup(self.db.close)
        self.path = store_path(self.db)
        self.server = build_server(path=self.path, clock=lambda: NOW, specs=self.specs)

    def call(self, name: str, arguments: dict | None = None) -> tuple[bool, dict]:
        return call(self.server, name, arguments or {})

    def ok(self, name: str, arguments: dict | None = None) -> dict:
        is_error, payload = self.call(name, arguments)
        if is_error:
            raise AssertionError(f"{name} failed: {payload}")
        return payload

    def error(self, name: str, arguments: dict | None = None) -> dict:
        is_error, payload = self.call(name, arguments)
        if not is_error:
            raise AssertionError(f"{name} did not fail: {payload}")
        self.assertEqual(set(payload), {"code", "message", "fix"})
        return payload
