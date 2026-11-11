"""The draft lab commands (#211). Fake runner, scratch store, made-up people."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import live_checkout
from outreach.draft_lab import __main__ as cli
from outreach.draft_lab import judge
from tests.mcptest import fingerprint
from tests.storetest import fresh_store, seed_company, seed_touch


class FakeRunner:
    def __init__(self, text="", error=None):
        self.text, self.error, self.calls = text, error, []

    def run(self, prompt):
        self.calls.append(prompt)
        if self.error:
            raise self.error
        return self.text


def write_sets(root, n=3):
    rows_a = [{"id": str(i), "recipient": "Sam Test at Acme", "subject": "A subject", "body": "Hi Sam,\n\nold note\n\nDev"}
              for i in range(n)]
    rows_b = [dict(r, subject="B subject", body="Hi Sam,\n\nnew note\n\nDev") for r in rows_a]
    a, b = Path(root) / "a.json", Path(root) / "b.json"
    a.write_text(json.dumps(rows_a))
    b.write_text(json.dumps(rows_b))
    return a, b


def winning_reply(pairs, key):
    items = []
    for x, _ in pairs:
        slot = next(s for s, src in key[x.id].items() if src == "b")
        other = "A" if slot == "B" else "B"
        sc = {m: {slot: 8, other: 5} for m in judge.METRICS}
        items.append({"id": x.id, "best": slot, "why": "b is plainer", **sc})
    return json.dumps({"items": items, "repeats": []})


class JudgeCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.a, self.b = write_sets(self.tmp.name)

    def test_judge_dry_run_prints_the_prompt_and_calls_nothing(self):
        runner, out = FakeRunner("{}"), io.StringIO()
        with mock.patch("live_checkout.is_live", return_value=False):
            code = cli.main(["judge", "--a", str(self.a), "--b", str(self.b), "--seed", "1", "--dry-run"],
                            stdout=out, runner=runner)
        self.assertEqual(code, 0)
        self.assertEqual(runner.calls, [])
        self.assertIn("old note", out.getvalue())
        self.assertIn("new note", out.getvalue())
        self.assertEqual(list(Path(self.tmp.name).glob("*.md")), [])

    def test_judge_outside_the_live_checkout_is_refused(self):
        runner = FakeRunner("{}")
        with mock.patch("live_checkout.is_live", return_value=False), self.assertRaises(SystemExit):
            cli.main(["judge", "--a", str(self.a), "--b", str(self.b)], stdout=io.StringIO(), runner=runner)
        self.assertEqual(runner.calls, [])

    def test_judge_writes_the_report_and_summary_line(self):
        pairs, _ = judge.pair_sets(judge.load_set(self.a), judge.load_set(self.b))
        _, key = judge.body_prompt(pairs, seed=4)
        runner, out = FakeRunner(winning_reply(pairs, key)), io.StringIO()
        report = Path(self.tmp.name) / "r.md"
        with mock.patch("live_checkout.is_live", return_value=True):
            code = cli.main(["judge", "--a", str(self.a), "--b", str(self.b), "--seed", "4", "--out", str(report)],
                            stdout=out, runner=runner)
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue().strip(), "b wins 3 of 3, human 8.0 vs 5.0, reply 8.0 vs 5.0: PASS")
        self.assertIn("Seed: 4", report.read_text())
        self.assertEqual(len(runner.calls), 1)

    def test_judge_inbox_mode_summary(self):
        pairs, _ = judge.pair_sets(judge.load_set(self.a), judge.load_set(self.b))
        _, key = judge.inbox_prompt(pairs, seed=2, decoys=judge.load_decoys())
        items = [{"id": i, "open": [next(r for r, s in rows.items() if s == "b")], "never": [], "why": "x"}
                 for i, rows in key.items()]
        out = io.StringIO()
        with mock.patch("live_checkout.is_live", return_value=True):
            code = cli.main(["judge", "--a", str(self.a), "--b", str(self.b), "--mode", "inbox", "--seed", "2",
                             "--out", str(Path(self.tmp.name) / "r.md")], stdout=out,
                            runner=FakeRunner(json.dumps({"items": items})))
        self.assertEqual(code, 0)
        self.assertIn("first picks a 0, b 3", out.getvalue())

    def test_incomplete_reply_exits_2_with_counts_and_writes_the_report(self):
        pairs, _ = judge.pair_sets(judge.load_set(self.a), judge.load_set(self.b))
        _, key = judge.body_prompt(pairs, seed=4)
        full = json.loads(winning_reply(pairs, key))
        full["items"].pop()
        out, report = io.StringIO(), Path(self.tmp.name) / "r.md"
        with mock.patch("live_checkout.is_live", return_value=True):
            code = cli.main(["judge", "--a", str(self.a), "--b", str(self.b), "--seed", "4", "--out", str(report)],
                            stdout=out, runner=FakeRunner(json.dumps(full)))
        self.assertEqual(code, 2)
        self.assertIn("INCOMPLETE", out.getvalue())
        self.assertIn("1 missing, 0 malformed", out.getvalue())
        self.assertTrue(report.exists())

    def test_inbox_summary_shows_missing_items(self):
        out = io.StringIO()
        with mock.patch("live_checkout.is_live", return_value=True):
            code = cli.main(["judge", "--a", str(self.a), "--b", str(self.b), "--mode", "inbox", "--seed", "2",
                             "--out", str(Path(self.tmp.name) / "r.md")], stdout=out,
                            runner=FakeRunner(json.dumps({"items": []})))
        self.assertEqual(code, 0)
        self.assertIn("3 missing, 0 malformed", out.getvalue())

    def test_out_inside_the_repo_but_outside_context_is_refused(self):
        runner, out = FakeRunner("{}"), io.StringIO()
        inside = Path(live_checkout.REPO_ROOT) / "report.md"
        with mock.patch("live_checkout.is_live", return_value=True):
            code = cli.main(["judge", "--a", str(self.a), "--b", str(self.b), "--out", str(inside)],
                            stdout=out, runner=runner)
        self.assertEqual((code, runner.calls), (1, []))
        self.assertIn("inside the repo", out.getvalue())
        self.assertFalse(inside.exists())

    def test_out_under_context_or_outside_the_repo_is_allowed(self):
        self.assertIsNone(cli._out_refused(str(Path(live_checkout.REPO_ROOT) / ".context" / "draft-lab" / "r.md")))
        self.assertIsNone(cli._out_refused(str(Path(self.tmp.name) / "r.md")))
        self.assertIsNone(cli._out_refused(None))

    def test_unparseable_reply_is_a_plain_failure(self):
        out = io.StringIO()
        report = Path(self.tmp.name) / "r.md"
        with mock.patch("live_checkout.is_live", return_value=True):
            code = cli.main(["judge", "--a", str(self.a), "--b", str(self.b), "--out", str(report)], stdout=out,
                            runner=FakeRunner("sorry, no json"))
        self.assertEqual(code, 1)
        self.assertIn("judge failed", out.getvalue())
        self.assertFalse(report.exists())


class ExportCliTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        path = self.conn.execute("PRAGMA database_list").fetchone()["file"]
        patch = mock.patch.dict(os.environ, {"OUTREACH_DB": path})
        patch.start()
        self.addCleanup(patch.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cid = seed_company(self.conn, "Acme")
        for n in range(4):
            touch = seed_touch(self.conn, cid, f"sam{n}@acme.example")
            self.conn.execute("UPDATE person SET title = 'Staff Engineer' WHERE person_id = "
                              "(SELECT person_id FROM touch WHERE touch_id = ?)", (touch,))
            self.insert(touch, 0, ("approved", "approved", "approved", "drafted")[n], f"Body {n}.0")
            if n == 1:  # a follow-up is approved only after the first send; the lab must never export it
                self.conn.execute("UPDATE touch SET first_sent_at = '2026-11-02T00:00:00Z', first_rfc_id = 'rfc-1', gmail_thread_id = 'th-1' WHERE touch_id = ?", (touch,))
                self.insert(touch, 1, "approved", "Body 1.1")
        self.conn.commit()

    def insert(self, touch, seq, state, body):
        self.conn.execute(
            "INSERT INTO message(touch_id, seq, state, subject, body, due_on, created_at, updated_at) "
            "VALUES (?, ?, 'planned', 'Subject', ?, ?, '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z')",
            (touch, seq, body, None if seq == 0 else "2026-11-20"))
        for step in ("drafted", "approved")[:{"drafted": 1, "approved": 2}[state]]:
            self.conn.execute("UPDATE message SET state = ?, approved_at = ? WHERE touch_id = ? AND seq = ?",
                              (step, "2026-11-02T00:00:00Z" if step == "approved" else None, touch, seq))

    def test_export_reads_only_and_writes_a_set_file(self):
        out_file = Path(self.tmp.name) / "set.json"
        before, out = fingerprint(self.conn), io.StringIO()
        with mock.patch("live_checkout.is_live", return_value=True):
            code = cli.main(["export", "--sample", "2", "--seed", "3", "--out", str(out_file)], stdout=out)
        self.assertEqual(code, 0)
        self.assertEqual(fingerprint(self.conn), before)
        rows = json.loads(out_file.read_text())
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertEqual(set(r), {"id", "recipient", "subject", "body"})
            self.assertEqual(r["recipient"], "Staff Engineer at Acme")
            self.assertIn(r["body"], ("Body 0.0", "Body 1.0", "Body 2.0"))  # approved first emails only
        self.assertEqual(len(judge.load_set(out_file)), 2)

    def test_export_connection_is_query_only_and_closed(self):
        seen = []

        class Spy:
            def __init__(self, conn):
                self.conn = conn

            def execute(self, sql, *a):
                seen.append(sql)
                return self.conn.execute(sql, *a)

            def close(self):
                seen.append("close")

        out_file = Path(self.tmp.name) / "set.json"
        with mock.patch("live_checkout.is_live", return_value=True), \
                mock.patch.object(cli, "connect", lambda: Spy(self.conn)):
            cli.main(["export", "--sample", "1", "--out", str(out_file)], stdout=io.StringIO())
        self.assertIn("PRAGMA query_only = ON", seen[0])
        self.assertEqual(seen[-1], "close")
        self.conn.execute("PRAGMA query_only = ON")
        with self.assertRaises(Exception):
            self.conn.execute("DELETE FROM message")
        self.conn.execute("PRAGMA query_only = OFF")

    def test_export_out_inside_the_repo_is_refused_and_sample_must_be_positive(self):
        inside = Path(live_checkout.REPO_ROOT) / "set.json"
        out = io.StringIO()
        with mock.patch("live_checkout.is_live", return_value=True):
            code = cli.main(["export", "--sample", "2", "--out", str(inside)], stdout=out)
            self.assertEqual(code, 1)
            self.assertFalse(inside.exists())
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                cli.main(["export", "--sample", "0", "--out", str(Path(self.tmp.name) / "x.json")], stdout=out)

    def test_export_state_and_seed(self):
        a, b = (Path(self.tmp.name) / n for n in ("a.json", "b.json"))
        with mock.patch("live_checkout.is_live", return_value=True):
            cli.main(["export", "--sample", "9", "--state", "drafted", "--out", str(a)], stdout=io.StringIO())
            for f in (b,):
                cli.main(["export", "--sample", "9", "--seed", "1", "--out", str(f)], stdout=io.StringIO())
        self.assertEqual([r["body"] for r in json.loads(a.read_text())], ["Body 3.0"])
        self.assertEqual(len(json.loads(b.read_text())), 3)

    def test_export_outside_the_live_checkout_is_refused(self):
        out_file = Path(self.tmp.name) / "set.json"
        with mock.patch("live_checkout.is_live", return_value=False), self.assertRaises(SystemExit):
            cli.main(["export", "--sample", "2", "--out", str(out_file)], stdout=io.StringIO())
        self.assertFalse(out_file.exists())


if __name__ == "__main__":
    unittest.main()
