"""Per-request connections, idempotent writes and revision bumps (Review Focus 1 and 4).

A scratch write route bumps a company's revision, appends one event and returns a small model, so each test can count
exactly what a write left behind.
"""
import dataclasses
import json
import sqlite3
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from outreach.api.errors import ApiProblem
from outreach.api.idempotency import fingerprint, prune_requests
from outreach.api.revisions import ALLOWED, bump
from outreach.store import connect
from tests.apitest import ApiCase
from tests.storetest import seed_company, seed_touch

SCRATCH = '''
import time

from fastapi import APIRouter, Request

from outreach.api.db import read_conn
from outreach.api.errors import ApiProblem
from outreach.api.idempotency import run_write
from outreach.api.models.base import Wire
from outreach.api.revisions import bump
from outreach.store import append_event

router = APIRouter()
HOOKS = {"pause": 0.0, "boom": False}
RUNS = []


class NoteIn(Wire):
    company_id: int
    revision: int
    text: str = ""


class Saved(Wire):
    event_id: int
    new_revision: int


@router.post("/scratch/notes", status_code=201)
def save(body: NoteIn, request: Request):
    def op(ctx):
        RUNS.append(ctx.request_id)
        if HOOKS["pause"]:
            time.sleep(HOOKS["pause"])
        revision = bump(ctx.conn, "company", "company_id", body.company_id, body.revision,
                        {"status_note": body.text}, at=ctx.now)
        event_id = append_event(ctx.conn, entity="company", entity_id=body.company_id, company_id=body.company_id,
                                kind="scratch_note", actor="devanshu", at=ctx.now, idempotency_key=ctx.event_key(1))
        if HOOKS["boom"]:
            raise RuntimeError("after the writes")
        return Saved(event_id=event_id, new_revision=revision)

    return run_write(request, op, status=201)


@router.post("/scratch/plain")
def plain(body: dict, request: Request):
    return run_write(request, lambda ctx: {"seen": body, "at": ctx.now})


@router.post("/scratch/empty")
def empty(request: Request):
    return run_write(request, lambda ctx: None)


@router.get("/scratch/counts")
def counts(request: Request):
    with read_conn(request) as conn:
        events = conn.execute("SELECT COUNT(*) FROM event WHERE kind = 'scratch_note'").fetchone()[0]
        requests = conn.execute("SELECT COUNT(*) FROM api_request").fetchone()[0]
        return {"events": events, "requests": requests}
'''


class WriteCase(ApiCase):
    def setUp(self):
        super().setUp()
        self.use_routers(zz_write_scratch=SCRATCH)
        self.company = seed_company(self.conn, "Acme")

    @property
    def scratch(self):
        return sys.modules["outreach.api.routers.zz_write_scratch"]

    def note(self, key=None, revision=1, text="hello"):
        return self.post("/api/scratch/notes", {"companyId": self.company, "revision": revision, "text": text}, key=key)

    def count(self, sql, *args):
        return self.conn.execute(sql, args).fetchone()[0]

    def events(self):
        return self.count("SELECT COUNT(*) FROM event WHERE kind = 'scratch_note'")

    def requests(self):
        return self.count("SELECT COUNT(*) FROM api_request")

    def revision(self):
        return self.count("SELECT revision FROM company WHERE company_id = ?", self.company)


class IdempotencyTest(WriteCase):
    def test_a_write_runs_and_records_its_request(self):
        key = str(uuid.uuid4())
        reply = self.note(key)
        self.assertEqual(reply.status_code, 201, reply.text)
        body = reply.json()
        self.assertEqual(set(body), {"eventId", "newRevision"})
        self.assertEqual(body["newRevision"], 2)
        self.assertNotIn("idempotent-replay", reply.headers)
        row = self.conn.execute("SELECT * FROM api_request").fetchone()
        self.assertEqual((row["request_id"], row["method"], row["path"], row["status"]),
                         (key, "POST", "/api/scratch/notes", 201))
        self.assertEqual(json.loads(row["response"]), body)  # stored by alias, exactly what was sent
        self.assertEqual(row["created_at"], self.now)
        detail = self.conn.execute("SELECT idempotency_key, at FROM event WHERE kind = 'scratch_note'").fetchone()
        self.assertEqual(tuple(detail), (f"{key}:1", self.now))

    def test_same_key_same_body_replays_and_writes_once(self):
        key = str(uuid.uuid4())
        first = self.note(key)
        second = self.note(key)
        self.assertEqual(second.status_code, first.status_code)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(second.content, first.content)
        self.assertEqual(second.headers["idempotent-replay"], "true")
        self.assertEqual(second.headers["content-type"], "application/json")
        self.assertEqual(self.events(), 1)
        self.assertEqual(self.requests(), 1)
        self.assertEqual(self.revision(), 2)
        self.assertEqual(len(self.scratch.RUNS), 1, "a replay must not run the write again")

    def test_a_replay_survives_a_rebuilt_app(self):
        key = str(uuid.uuid4())
        first = self.note(key)
        self.build()  # a restarted server: the replay comes from the store, not from memory
        self.assertEqual(self.note(key).content, first.content)
        self.assertEqual(self.events(), 1)

    def test_same_key_with_reordered_keys_is_the_same_body(self):
        key = str(uuid.uuid4())
        self.post("/api/scratch/plain", {"a": 1, "b": [1, 2]}, key=key)
        reply = self.client.post("/api/scratch/plain", content='{ "b": [1,2], "a": 1 }',
                                 headers={"Content-Type": "application/json", "Idempotency-Key": key})
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertEqual(reply.headers["idempotent-replay"], "true")

    def test_same_key_different_body_is_refused(self):
        key = str(uuid.uuid4())
        self.note(key, text="first")
        reply = self.note(key, text="second")
        self.assertEqual(reply.status_code, 422, reply.text)
        self.assertEqual(reply.json()["code"], "idempotency_mismatch")
        self.assertEqual(self.events(), 1)
        self.assertEqual(self.revision(), 2)
        self.assertEqual(self.count("SELECT status_note FROM company"), "first")

    def test_same_key_on_another_path_is_refused(self):
        key = str(uuid.uuid4())
        self.post("/api/scratch/plain", {}, key=key)
        reply = self.post("/api/scratch/empty", {}, key=key)
        self.assertEqual(reply.json()["code"], "idempotency_mismatch")

    def test_refused_write_stores_nothing_so_the_retry_works(self):
        key = str(uuid.uuid4())
        stale = self.note(key, revision=7)
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["code"], "conflict")
        self.assertEqual(self.requests(), 0)
        self.assertEqual(self.events(), 0)
        retry = self.note(key, revision=1)  # after a refresh: same key, the current revision
        self.assertEqual(retry.status_code, 201, retry.text)
        self.assertNotIn("idempotent-replay", retry.headers)
        self.assertEqual(self.events(), 1)

    def test_a_server_error_stores_nothing_and_rolls_back(self):
        self.scratch.HOOKS["boom"] = True
        with self.assertLogs("outreach.api", level="ERROR"):
            reply = self.note()
        self.assertEqual(reply.status_code, 500)
        self.assertEqual((self.requests(), self.events(), self.revision()), (0, 0, 1))

    def test_two_threads_with_one_key_write_once(self):
        self.settings = dataclasses.replace(self.settings, busy_timeout_ms=5000)
        self.build()
        self.scratch.HOOKS["pause"] = 0.2  # the first holds the write lock while the second arrives
        key = str(uuid.uuid4())
        start = threading.Barrier(2)

        def press():
            start.wait()
            return self.note(key)

        with ThreadPoolExecutor(2) as pool:
            replies = list(pool.map(lambda _: press(), range(2)))
        self.assertEqual([r.status_code for r in replies], [201, 201], [r.text for r in replies])
        self.assertEqual(replies[0].content, replies[1].content)
        self.assertEqual(sorted(r.headers.get("idempotent-replay", "") for r in replies), ["", "true"])
        self.assertEqual((self.events(), self.requests(), self.revision()), (1, 1, 2))
        self.assertEqual(len(self.scratch.RUNS), 1)

    def test_missing_or_short_key_is_bad_request(self):
        body = json.dumps({"companyId": self.company, "revision": 1})
        for key in (None, "", "short", "x" * 81, "has spaces in it", "tab\tkey12345"):
            with self.subTest(key=key):
                headers = {"Content-Type": "application/json"}
                if key is not None:
                    headers["Idempotency-Key"] = key
                reply = self.client.post("/api/scratch/notes", content=body, headers=headers)
                self.assertEqual(reply.status_code, 400, reply.text)
                self.assertEqual(reply.json()["code"], "bad_request")
                self.assertIn("Idempotency-Key", reply.json()["message"])
        self.assertEqual((self.events(), self.requests(), self.revision()), (0, 0, 1))
        self.assertEqual(self.scratch.RUNS, [])

    def test_a_write_with_no_result_stores_null(self):
        key = str(uuid.uuid4())
        reply = self.post("/api/scratch/empty", {}, key=key)
        self.assertEqual((reply.status_code, reply.json()), (200, None))
        self.assertEqual(self.post("/api/scratch/empty", {}, key=key).headers["idempotent-replay"], "true")

    def test_only_a_2xx_status_can_be_stored(self):
        from outreach.api.idempotency import run_write

        for status in (199, 300, 400, 500):
            with self.subTest(status=status), self.assertRaises(ValueError):
                run_write(None, lambda ctx: None, status=status)

    def test_fingerprint_is_method_path_and_canonical_body(self):
        same = fingerprint("POST", "/api/x", b'{"a":1,"b":2}')
        self.assertEqual(same, fingerprint("POST", "/api/x", b'{ "b": 2, "a": 1 }'))
        self.assertNotEqual(same, fingerprint("PUT", "/api/x", b'{"a":1,"b":2}'))
        self.assertNotEqual(same, fingerprint("POST", "/api/y", b'{"a":1,"b":2}'))
        self.assertNotEqual(same, fingerprint("POST", "/api/x", b'{"a":1,"b":3}'))
        self.assertEqual(len(same), 64)


class BusyStoreTest(WriteCase):
    def test_locked_store_is_a_503_busy_envelope(self):
        self.settings = dataclasses.replace(self.settings, busy_timeout_ms=50)
        self.build()
        holder = connect(self.settings.db_path)
        self.addCleanup(holder.close)
        holder.execute("BEGIN IMMEDIATE")  # the sender, mid-claim
        key = str(uuid.uuid4())
        try:
            reply = self.note(key)
        finally:
            holder.execute("ROLLBACK")
        self.assertEqual(reply.status_code, 503, reply.text)
        self.assertEqual(reply.json()["code"], "busy")
        self.assertEqual(reply.headers["retry-after"], "1")
        self.assertIn("Nothing was saved", reply.json()["message"])
        self.assertEqual((self.requests(), self.events(), self.revision()), (0, 0, 1))
        retry = self.note(key)  # the lock is gone: the same key now writes once
        self.assertEqual(retry.status_code, 201, retry.text)
        self.assertNotIn("idempotent-replay", retry.headers)

    def test_no_transaction_survives_a_request(self):
        self.assertEqual(self.get("/api/scratch/counts").status_code, 200)
        self.assertEqual(self.note().status_code, 201)
        self.assertEqual(self.get("/api/scratch/counts").json(), {"events": 1, "requests": 1})
        fresh = connect(self.settings.db_path)
        self.addCleanup(fresh.close)
        self.assertEqual(fresh.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        fresh.execute("PRAGMA busy_timeout = 0")
        # TRUNCATE needs every reader and writer gone; a leftover snapshot or write lock makes busy 1.
        self.assertEqual(fresh.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0], 0)

    def test_concurrent_reads_use_their_own_threads(self):
        with ThreadPoolExecutor(8) as pool:
            replies = list(pool.map(lambda _: self.get("/api/scratch/counts"), range(8)))
        self.assertEqual([r.status_code for r in replies], [200] * 8, [r.text for r in replies])


class BumpTest(WriteCase):
    def bump(self, *args, **kwargs):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            return bump(self.conn, *args, **{"at": "2026-11-10T15:14:00Z", **kwargs})
        finally:
            self.conn.execute("COMMIT")

    def test_bump_needs_the_writes_clock(self):
        # a default of the wall clock would stamp updated_at off the write's own `now`
        with self.assertRaises(TypeError):
            bump(self.conn, "company", "company_id", self.company, 1, {"status_note": "n"})
        self.assertEqual(self.revision(), 1)

    def test_bump_moves_the_revision_and_sets_the_columns(self):
        self.assertEqual(self.bump("company", "company_id", self.company, 1, {"status_note": "n"}), 2)
        row = self.conn.execute("SELECT status_note, revision FROM company").fetchone()
        self.assertEqual(tuple(row), ("n", 2))

    def test_bump_sets_updated_at_where_the_table_has_one(self):
        touch = seed_touch(self.conn, self.company, "ada@acme.example")
        person = self.count("SELECT person_id FROM touch WHERE touch_id = ?", touch)
        self.bump("person", "person_id", person, 1, {"notes": "met at a meetup"}, at="2026-11-10T15:14:00Z")
        row = self.conn.execute("SELECT notes, updated_at, revision FROM person").fetchone()
        self.assertEqual(tuple(row), ("met at a meetup", "2026-11-10T15:14:00Z", 2))

    def test_bump_conflicts_on_a_stale_revision_and_names_the_row(self):
        with self.assertRaises(ApiProblem) as caught:
            self.bump("company", "company_id", self.company, 5, {"status_note": "late"})
        self.assertEqual(caught.exception.code, "conflict")
        self.assertEqual(caught.exception.message, "The company changed since you loaded it.")
        self.assertEqual(tuple(self.conn.execute("SELECT status_note, revision FROM company").fetchone()), ("", 1))

    def test_bump_unknown_id_is_not_found(self):
        with self.assertRaises(ApiProblem) as caught:
            self.bump("company", "company_id", 999, 1, {"status_note": "x"})
        self.assertEqual(caught.exception.code, "not_found")
        self.assertIn("company", caught.exception.message)

    def test_bump_rejects_a_table_outside_the_allow_list(self):
        for table in ("event", "api_request", "company; DROP TABLE company", "sqlite_master"):
            with self.subTest(table=table), self.assertRaises(ValueError):
                self.bump(table, "event_id", 1, 1, {"kind": "x"})

    def test_bump_rejects_a_key_or_column_it_does_not_know(self):
        bad = [
            ("company", "name", self.company, 1, {"status_note": "x"}),  # not the key
            ("company", "company_id", self.company, 1, {"revision": 9}),  # bump owns the revision
            ("company", "company_id", self.company, 1, {"company_id": 2}),
            ("company", "company_id", self.company, 1, {"no_such_column": 1}),
            ("company", "company_id", self.company, 1, {"status_note = 'x', name": 1}),
        ]
        for args in bad:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.bump(*args)
        self.assertEqual(self.revision(), 1)

    def test_every_allowed_table_has_its_key_and_a_revision(self):
        for table, rule in ALLOWED.items():
            with self.subTest(table=table):
                columns = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
                self.assertIn("revision", columns)
                self.assertTrue(rule.key == "rowid" or rule.key in columns)
                self.assertEqual(rule.stamps_updated_at, "updated_at" in columns)

    def test_a_stale_revision_through_the_api_changes_nothing_and_says_so(self):
        self.assertEqual(self.note(revision=1).status_code, 201)
        reply = self.note(revision=1, text="from the other tab")
        self.assertEqual(reply.status_code, 409)
        self.assertEqual(reply.json()["message"], "The company changed since you loaded it.")
        self.assertEqual(self.count("SELECT status_note FROM company"), "hello")

    def test_an_unknown_id_through_the_api_is_404(self):
        reply = self.post("/api/scratch/notes", {"companyId": 999, "revision": 1})
        self.assertEqual((reply.status_code, reply.json()["code"]), (404, "not_found"))
        self.assertEqual(self.requests(), 0)


class PruneTest(WriteCase):
    def add(self, key, at):
        self.conn.execute(
            "INSERT INTO api_request(request_id, method, path, fingerprint, status, response, created_at) "
            "VALUES (?, 'POST', '/api/x', ?, 200, '{}', ?)", (key, "0" * 64, at))

    def test_prune_drops_rows_older_than_seven_days_only(self):
        self.add("eight-days", "2026-11-02T15:13:59Z")
        self.add("seven-days", "2026-11-03T15:14:00Z")
        self.add("six-days-x", "2026-11-04T09:00:00Z")
        self.add("just-now-x", "2026-11-10T15:14:00Z")
        dropped = prune_requests(self.conn, days=7, now="2026-11-10T15:14:00Z")
        self.assertEqual(dropped, 1)
        kept = [r[0] for r in self.conn.execute("SELECT request_id FROM api_request ORDER BY created_at")]
        self.assertEqual(kept, ["seven-days", "six-days-x", "just-now-x"])
        self.assertFalse(self.conn.in_transaction)

    def test_prune_runs_when_the_app_starts(self):
        self.add("ancient-key", "2026-01-01T00:00:00Z")
        self.add("recent-key", "2026-11-09T00:00:00Z")
        from fastapi.testclient import TestClient

        with TestClient(self.app, base_url="http://127.0.0.1:3100"):
            pass
        kept = [r[0] for r in self.conn.execute("SELECT request_id FROM api_request")]
        self.assertEqual(kept, ["recent-key"])

    def test_startup_on_a_busy_store_still_starts(self):
        self.settings = dataclasses.replace(self.settings, busy_timeout_ms=50)
        self.build()
        holder = connect(self.settings.db_path)
        self.addCleanup(holder.close)
        holder.execute("BEGIN IMMEDIATE")
        from fastapi.testclient import TestClient

        try:
            with self.assertLogs("outreach.api", level="WARNING"):
                with TestClient(self.app, base_url="http://127.0.0.1:3100"):
                    pass
        finally:
            holder.execute("ROLLBACK")


class ReadConnTest(WriteCase):
    def test_read_conn_is_a_snapshot_and_closes(self):
        from outreach.api.db import read_conn

        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=self.settings)))
        with read_conn(request) as conn:
            self.assertTrue(conn.in_transaction)
            before = conn.execute("SELECT COUNT(*) FROM company").fetchone()[0]
            seed_company(self.conn, "Later Co")  # another writer commits mid-read
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM company").fetchone()[0], before)
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")  # closed


if __name__ == "__main__":
    import unittest

    unittest.main()
