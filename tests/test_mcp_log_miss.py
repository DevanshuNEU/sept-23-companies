"""log_miss (#153, Task 4): a job link Devanshu pasted because Knock did not show it, stored with the first cause
found. Called through the official SDK's client on a scratch store."""
import json

from outreach.mcp import server
from outreach.pipeline.misses import CAUSE_TEXT
from tests import apiseed as seed
from tests.mcptest import NOW, McpCase, fingerprint
from tests.storetest import seed_board
from tests.test_pipeline_scorecard import store_posting


class LogMissCase(McpCase):
    def setUp(self):
        super().setUp()
        self.acme = seed.company(self.db, "Acme", "working")
        self.board = seed_board(self.db, self.acme, "ashby", "acme")
        self.posting = store_posting(self.db, "ashby:acme:ok-1", company_id=self.acme, board_id=self.board,
                                     level=dict(level_tier="new_grad", level_ok=1))


class LogMissTests(LogMissCase):
    def test_the_tool_is_registered_with_a_miss_insert_grant_only(self):
        spec = next(s for s in server.tool_specs() if s.name == "log_miss")
        self.assertEqual(set(spec.grants), {"miss"})
        self.assertTrue(spec.grants["miss"].insert)
        self.assertFalse(spec.grants["miss"].update)

    def test_a_known_job_is_found_and_stored(self):
        out = self.ok("log_miss", {"url": "https://jobs.ashbyhq.com/acme/ok-1", "note": "saw it on a friend's post"})
        self.assertEqual(set(out), {"missId", "cause", "causeText", "postingId", "companyId", "boardId"})
        self.assertEqual((out["cause"], out["causeText"]), ("found", CAUSE_TEXT["found"]))
        self.assertEqual((out["postingId"], out["companyId"], out["boardId"]), (self.posting, self.acme, self.board))
        row = self.db.execute("SELECT * FROM miss").fetchone()
        self.assertEqual((row["miss_id"], row["note"], row["pasted_at"], row["cause"]),
                         (out["missId"], "saw it on a friend's post", NOW, "found"))

    def test_an_unknown_site_is_stored_with_null_ids(self):
        out = self.ok("log_miss", {"url": "https://example.com/jobs/9"})
        self.assertEqual((out["cause"], out["postingId"], out["companyId"], out["boardId"]),
                         ("unknown_site", None, None, None))
        self.assertEqual(out["causeText"], CAUSE_TEXT["unknown_site"])
        self.assertEqual(self.db.execute("SELECT note FROM miss").fetchone()[0], "")

    def test_the_same_link_twice_is_one_row(self):
        first = self.ok("log_miss", {"url": "https://example.com/jobs/9"})
        second = self.ok("log_miss", {"url": "https://example.com/jobs/9", "note": "again"})
        self.assertEqual(second["missId"], first["missId"])
        self.assertEqual(self.db.execute("SELECT count(*) FROM miss").fetchone()[0], 1)

    def test_a_cause_is_given_for_a_company_without_a_board(self):
        seed.company(self.db, "Zed Labs", "candidate")
        out = self.ok("log_miss", {"url": "https://jobs.lever.co/zedlabs/abc"})
        self.assertEqual(out["cause"], "no_board")

    def test_input_checks(self):
        before = fingerprint(self.db)
        bad = [{}, {"url": ""}, {"url": "   "}, {"url": "x" * 2001}, {"url": "https://a.example/j", "note": "n" * 501},
               {"url": 5}, {"url": "https://a.example/j", "note": 5}, {"url": "https://a.example/j", "cause": "found"},
               {"url": "https://a.example/j", "miss_id": 1}]
        for arguments in bad:
            with self.subTest(arguments=json.dumps(arguments)[:60]):
                self.assertEqual(self.error("log_miss", arguments)["code"], "bad_input")
        self.assertEqual(fingerprint(self.db), before)

    def test_the_limits_are_accepted_at_the_edge(self):
        out = self.ok("log_miss", {"url": "https://example.com/" + "a" * (2000 - 20), "note": "n" * 500})
        self.assertEqual(out["cause"], "unknown_site")

    def test_only_the_miss_table_changes(self):
        before = fingerprint(self.db)
        self.ok("log_miss", {"url": "https://jobs.ashbyhq.com/acme/ok-1"})
        self.assertNotEqual(fingerprint(self.db), before)
        self.assertEqual(self.db.execute("SELECT count(*) FROM event").fetchone()[0], 0)
