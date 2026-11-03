"""POST /api/pipeline/misses (#153, Task 4): the miss log through the API, on the test client only."""
from typing import get_args

from outreach.api.models.pipeline import MissCause
from outreach.pipeline.misses import CAUSES, CAUSE_TEXT
from tests import apiseed as s
from tests.apitest import NOW, ApiCase
from tests.storetest import seed_board
from tests.test_pipeline_scorecard import store_posting


class MissRoute(ApiCase):
    def miss(self, url="https://jobs.ashbyhq.com/acme/ok-1", note="", key=None):
        return self.post("/api/pipeline/misses", {"url": url, "note": note}, key=key)

    def test_a_job_link_gets_its_cause(self):
        acme = s.company(self.conn, "Acme", "working")
        board = seed_board(self.conn, acme, "ashby", "acme")
        store_posting(self.conn, "ashby:acme:ok-1", company_id=acme, board_id=board,
                      level=dict(level_tier="senior"))
        res = self.miss(note="a friend sent it")
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(set(body), {"missId", "cause", "causeText"})
        self.assertEqual((body["cause"], body["causeText"]), ("read_wrong_level", CAUSE_TEXT["read_wrong_level"]))
        row = self.conn.execute("SELECT * FROM miss").fetchone()
        self.assertEqual((row["miss_id"], row["url"], row["note"], row["pasted_at"], row["board_id"]),
                         (body["missId"], "https://jobs.ashbyhq.com/acme/ok-1", "a friend sent it", NOW, board))

    def test_an_unknown_site_is_stored(self):
        body = self.miss(url="https://example.com/careers/1").json()
        self.assertEqual(body["cause"], "unknown_site")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM miss").fetchone()[0], 1)

    def test_the_same_key_replays_and_the_same_link_with_a_new_key_keeps_one_row(self):
        first = self.miss(key="miss-key-0001")
        again = self.miss(key="miss-key-0001")
        self.assertEqual(again.headers.get("Idempotent-Replay"), "true")
        self.assertEqual(again.json(), first.json())
        other = self.miss(key="miss-key-0002")
        self.assertEqual(other.json()["missId"], first.json()["missId"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM miss").fetchone()[0], 1)

    def test_bad_bodies_are_refused_and_write_nothing(self):
        for body in ({}, {"url": ""}, {"url": "   "}, {"url": "x" * 2001, "note": ""},
                     {"url": "https://a.example/j", "note": "n" * 501}, {"url": "https://a.example/j", "cause": "found"},
                     {"url": 7}):
            with self.subTest(body=str(body)[:50]):
                res = self.post("/api/pipeline/misses", body)
                self.assertIn(res.status_code, (400, 422), res.text)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM miss").fetchone()[0], 0)

    def test_a_write_needs_the_idempotency_key(self):
        res = self.client.post("/api/pipeline/misses", json={"url": "https://a.example/j"})
        self.assertEqual(res.status_code, 400, res.text)

    def test_the_wire_causes_are_the_stored_causes(self):
        self.assertEqual(set(get_args(MissCause)), set(CAUSES))
