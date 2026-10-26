"""`record_request`: the one write behind every LinkedIn request log (the plan row, the LinkedIn page, the MCP bridge)."""
import json
import unittest

from outreach.outcomes.requests import own_context, plan_context, record_request
from tests import apiseed as seed
from tests.storetest import fresh_store

NOW = "2026-11-10T15:14:00Z"


class RecordRequestTest(unittest.TestCase):
    def setUp(self):
        self.db = fresh_store()
        self.addCleanup(self.db.close)
        self.acme = seed.company(self.db, "Acme", "working")
        self.pat = seed.person(self.db, self.acme, "Pat Lee", channel="linkedin")

    def record(self, key="k:1", note=""):
        return record_request(self.db, person_id=self.pat, company_id=self.acme, sent_at=NOW, recorded_at=NOW,
                              context=own_context(self.db, self.acme, "engineer", None, NOW), idempotency_key=key,
                              note=note, actor="claude")

    def test_one_log_row_and_one_touch_with_the_note_and_the_actor(self):
        touch_id, created = self.record(note="Hi Pat, I liked your post.")
        self.assertTrue(created)
        (log,) = self.db.execute("SELECT * FROM linkedin_log").fetchall()
        self.assertEqual((log["person_id"], log["kind"], log["at"], log["note"]),
                         (self.pat, "request_sent", NOW, "Hi Pat, I liked your post."))
        touch = self.db.execute("SELECT * FROM outreach_touch WHERE touch_id = ?", (touch_id,)).fetchone()
        self.assertEqual((touch["channel"], touch["sent_at"], touch["idempotency_key"]), ("linkedin_request", NOW, "k:1"))
        self.assertEqual(json.loads(touch["context"])["source"], "own")
        (event,) = self.db.execute("SELECT * FROM event WHERE entity = 'outreach_touch'").fetchall()
        self.assertEqual((event["kind"], event["actor"]), ("sent", "claude"))

    def test_a_used_key_returns_the_touch_and_writes_nothing(self):
        first, _ = self.record()
        again, created = self.record()
        self.assertEqual((again, created), (first, False))
        self.assertEqual(self.db.execute("SELECT count(*) FROM linkedin_log").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM outreach_touch").fetchone()[0], 1)

    def test_plan_context_takes_the_rows_lane_and_names_the_role(self):
        posting = seed.posting(self.db, self.acme, first_seen_at="2026-11-07T12:00:00Z")
        row = self.db.execute("SELECT * FROM posting WHERE posting_id = ?", (posting,)).fetchone()
        ctx = plan_context(self.db, lane="level_role", survivor=self.acme, role_kind="engineer", posting=row, now=NOW)
        self.assertEqual((ctx["source"], ctx["lane"], ctx["roleNamed"], ctx["roleAgeDays"]),
                         ("plan", "level_role", True, 3))


if __name__ == "__main__":
    unittest.main()
