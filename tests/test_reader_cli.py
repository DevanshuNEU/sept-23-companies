"""The reader's command line: next, submit and apply (#32, Task 5). Every test runs on a scratch store."""
import dataclasses
import importlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from outreach.reader import __main__ as cli
from outreach.reader.config import load_reader_config
from outreach.reader.types import FIELDS
from outreach.reader.version import reader_version
from outreach.rules import rebuild
from outreach.store import db as store_db
from tests.storetest import fresh_store, seed_company, seed_posting

rebuild_module = importlib.import_module("outreach.rules.rebuild")

BODY = ("Kestrel Robotics builds control software for warehouse robots. You will write C++ and Python with a small "
        "team in Boston and ship to real customers in your first month. ") * 5


def blank_fields() -> dict:
    out = {}
    for name in FIELDS:
        if name in ("years_low", "years_high"):
            out[name] = {"value": None, "quote": ""}
        elif name in ("start_date", "location", "pay"):
            out[name] = {"value": "", "quote": ""}
        else:
            out[name] = {"value": "unknown", "quote": ""}
    return out


class CliCase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.company = seed_company(self.conn, "Kestrel")
        path = self.conn.execute("PRAGMA database_list").fetchone()["file"]
        env = mock.patch.dict(os.environ, {"OUTREACH_DB": path})
        env.start()
        self.addCleanup(env.stop)
        self.cfg = load_reader_config()

    def postings(self, n=3):
        ids = [seed_posting(self.conn, self.company, "Software Engineer, New Grad", BODY + str(i), "Boston, MA")
               for i in range(n)]
        rebuild(self.conn)
        return ids

    def run_cli(self, argv, stdin_text="", cfg=None):
        out = io.StringIO()
        with mock.patch.object(cli, "load_reader_config", return_value=cfg or self.cfg):
            code = cli.main(argv, stdin=io.StringIO(stdin_text), stdout=out)
        return code, out.getvalue()

    def next_batch(self, limit=2, cfg=None):
        code, out = self.run_cli(["next", "--limit", str(limit)], cfg=cfg)
        self.assertEqual(code, 0)
        return json.loads(out)

    def readings_for(self, batch):
        return [{"posting_id": p["posting_id"], "body_sha256": p["body_sha256"], "fields": blank_fields()}
                for p in batch["postings"]]


class NextTests(CliCase):
    def test_next_prints_json_batch_and_leases(self):
        self.postings(3)
        batch = self.next_batch(limit=2)
        self.assertEqual(len(batch["postings"]), 2)
        for entry in batch["postings"]:
            self.assertEqual(set(entry), {"posting_id", "body_sha256", "reader_version", "title", "location", "body"})
            self.assertEqual(entry["reader_version"], reader_version(self.cfg))
        states = {r["posting_id"]: r["state"] for r in self.conn.execute("SELECT posting_id, state FROM read_queue")}
        self.assertEqual(len(states), 3)  # enqueue_missing ran first
        for entry in batch["postings"]:
            self.assertEqual(states[entry["posting_id"]], "leased")

    def test_next_on_empty_queue_prints_empty_list(self):
        batch = self.next_batch(limit=5)
        self.assertEqual(batch["postings"], [])

    def test_next_defaults_to_the_batch_size(self):
        self.postings(3)
        batch = self.next_batch(limit=1, cfg=dataclasses.replace(self.cfg, batch_size=2))
        self.assertEqual(len(batch["postings"]), 1)
        code, out = self.run_cli(["next"], cfg=dataclasses.replace(self.cfg, batch_size=1))
        self.assertEqual(len(json.loads(out)["postings"]), 1)


class SubmitTests(CliCase):
    def test_submit_round_trip(self):
        self.postings(2)
        cfg = dataclasses.replace(self.cfg, promoted_version=reader_version(self.cfg))
        batch = self.next_batch(limit=2, cfg=cfg)
        code, out = self.run_cli(["submit"], json.dumps(self.readings_for(batch)), cfg=cfg)
        self.assertEqual(code, 0)
        results = json.loads(out)
        self.assertEqual([r["status"] for r in results], ["stored", "stored"])
        self.assertEqual([r["posting_id"] for r in results], [p["posting_id"] for p in batch["postings"]])
        for entry in batch["postings"]:
            row = self.conn.execute("SELECT provisional, reader_version FROM current_decision WHERE posting_id = ?",
                                    (entry["posting_id"],)).fetchone()
            self.assertEqual(tuple(row), (0, reader_version(self.cfg)))

    def test_submit_before_promotion_stores_but_decides_nothing(self):
        self.postings(1)
        batch = self.next_batch(limit=1)
        code, out = self.run_cli(["submit"], json.dumps(self.readings_for(batch)))
        self.assertEqual([r["status"] for r in json.loads(out)], ["stored"])
        row = self.conn.execute("SELECT provisional FROM current_decision").fetchone()
        self.assertEqual(row[0], 1)

    def test_submit_accepts_a_single_object_and_a_wrapper(self):
        self.postings(2)
        one, two = self.readings_for(self.next_batch(limit=2))
        code, out = self.run_cli(["submit"], json.dumps(one))
        self.assertEqual((code, [r["status"] for r in json.loads(out)]), (0, ["stored"]))
        code, out = self.run_cli(["submit"], json.dumps({"readings": [two]}))
        self.assertEqual((code, [r["status"] for r in json.loads(out)]), (0, ["stored"]))

    def test_refused_readings_still_exit_0(self):
        self.postings(1)
        reading = self.readings_for(self.next_batch(limit=1))[0]
        reading["fields"]["sponsorship"] = {"value": "wont_sponsor", "quote": "words the posting never had"}
        code, out = self.run_cli(["submit"], json.dumps([reading]))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)[0]["status"], "requeued")
        self.assertEqual(json.loads(out)[0]["refused_fields"], ["sponsorship"])

    def test_a_reading_refused_at_the_limit_fails_open_at_once(self):
        # A posting that used its last lease and still gave no usable reading ends refused. The posting is shown,
        # flagged, by this submit; it does not wait for the next `apply` or night.
        pid = seed_posting(self.conn, self.company, "Software Engineer, New Grad",
                           "We do not sponsor visas for this role. " + BODY, "Boston, MA")
        rebuild(self.conn)
        before = self.conn.execute("SELECT sponsorship FROM current_decision WHERE posting_id = ?", (pid,)).fetchone()
        self.assertEqual(before[0], "no")  # the rules hide it, provisionally
        cfg = dataclasses.replace(self.cfg, promoted_version=reader_version(self.cfg), max_attempts=1)
        reading = self.readings_for(self.next_batch(limit=1, cfg=cfg))[0]
        del reading["fields"]["pay"]  # malformed: nothing can be stored
        code, out = self.run_cli(["submit"], json.dumps([reading]), cfg=cfg)
        self.assertEqual((code, json.loads(out)[0]["status"]), (0, "refused"))
        row = self.conn.execute("SELECT sponsorship, provisional FROM current_decision WHERE posting_id = ?",
                                (pid,)).fetchone()
        self.assertEqual(tuple(row), ("unclear", 1))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM review_item WHERE kind = 'reader_disagrees' "
                                           "AND posting_id = ?", (pid,)).fetchone()[0], 1)

    def test_submit_rejects_non_json_with_exit_2(self):
        for text in ("not json", "", "[1, 2"):
            code, out = self.run_cli(["submit"], text)
            self.assertEqual(code, 2, text)
            self.assertEqual(out, "")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM posting_reading").fetchone()[0], 0)


class ApplyTests(CliCase):
    def test_apply_prints_the_count(self):
        self.postings(1)
        cfg = dataclasses.replace(self.cfg, promoted_version=reader_version(self.cfg))
        batch = self.next_batch(limit=1, cfg=cfg)
        self.run_cli(["submit"], json.dumps(self.readings_for(batch)), cfg=cfg)
        rebuild(self.conn)  # rows go provisional again
        code, out = self.run_cli(["apply"], cfg=cfg)
        self.assertEqual((code, out.strip()), (0, "1"))
        self.assertEqual(self.conn.execute("SELECT provisional FROM current_decision").fetchone()[0], 0)


class GuardTests(unittest.TestCase):
    def test_cli_never_touches_the_live_store(self):
        # The default store path, here a stand-in inside a scratch folder, outside the live checkout: the store
        # guard stops the command before any database is opened.
        folder = Path(tempfile.mkdtemp(prefix="outreach-cli-live-"))
        env = {k: v for k, v in os.environ.items() if k != "OUTREACH_DB"}
        with mock.patch.object(store_db, "DEFAULT_PATH", str(folder / "outreach.db")), \
                mock.patch.dict(os.environ, env, clear=True), \
                mock.patch("sqlite3.connect", side_effect=AssertionError("opened a database")):
            for argv in (["next"], ["submit"], ["apply"]):
                with self.assertRaises(SystemExit) as caught:
                    cli.main(argv, stdin=io.StringIO("[]"), stdout=io.StringIO())
                self.assertIn("live checkout", str(caught.exception.code))
        self.assertFalse((folder / "outreach.db").exists())

    def test_later_commands_are_registered_but_not_built(self):
        for name in ("eval", "promote"):
            err = io.StringIO()
            with mock.patch("sys.stderr", err):
                self.assertEqual(cli.main([name], stdin=io.StringIO(""), stdout=io.StringIO()), 2, name)
            self.assertIn("not built yet", err.getvalue())


class RebuildWiringTests(CliCase):
    def test_rebuild_main_passes_the_reader_combiner_and_applies(self):
        self.postings(1)
        calls = []
        real = rebuild_module.rebuild

        def spy(conn, **kw):
            calls.append(kw.get("combiner"))
            return real(conn, **kw)
        with mock.patch.object(rebuild_module, "rebuild", spy), \
                mock.patch("outreach.reader.combine.apply_all", side_effect=lambda conn: calls.append("apply_all")), \
                mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(rebuild_module.main([]), 0)
        self.assertTrue(callable(calls[0]))
        self.assertEqual(calls[1], "apply_all")

    def test_rebuild_main_runs_alone_without_the_reader(self):
        self.postings(1)
        calls = []
        real = rebuild_module.rebuild

        def spy(conn, **kw):
            calls.append(kw)
            return real(conn, **kw)
        with mock.patch.dict("sys.modules", {"outreach.reader.combine": None}), \
                mock.patch.object(rebuild_module, "rebuild", spy), mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(rebuild_module.main([]), 0)
        self.assertEqual(calls, [{}])


if __name__ == "__main__":
    unittest.main()
