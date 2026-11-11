"""Applied on the job list (#218): `POST /api/jobs/{postingId}/applied`. Scratch store, no network.

Applied records that he applied online to one posting: an `application` row and an `application` touch (source
`own`), so Progress counts it. The posting leaves the job list for good, in both tabs; its company is untouched and it
is not Mark reached.
"""
import json

from outreach.outcomes.context import build_context
from outreach.pipeline import reached
from outreach.pipeline.joblist import applied_posting_ids
from outreach.store.writes import log_touch, undo_touch
from tests import apiseed as s
from tests.ranking_store import decision, posting
from tests.test_api_jobs_writes import WritesCase
from tests.test_api_plan_picks import DAY


class AppliedCase(WritesCase):
    def applied(self, pid, **kw):
        return self.post(f"/api/jobs/{pid}/applied", {}, **kw)

    def with_url(self, pid, url="https://jobs.example/ember/1"):
        self.conn.execute("UPDATE posting SET url = ? WHERE posting_id = ?", (url, pid))
        return pid


class AppliedWriteTest(AppliedCase):
    def test_applied_records_application_and_touch(self):
        a = self.with_url(self.job("Ember"))
        res = self.applied(a)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), {"postingId": a, "companyId": self.ids["Ember"], "appliedOn": DAY,
                                      "text": "Saved to Applied."})
        app = self.conn.execute("SELECT * FROM application WHERE posting_id = ?", (a,)).fetchall()
        self.assertEqual([(r["company_id"], r["posting_id"], r["applied_on"], r["url"], r["outcome"]) for r in app],
                         [(self.ids["Ember"], a, DAY, "https://jobs.example/ember/1", "open")])
        touch = self.conn.execute("SELECT * FROM outreach_touch WHERE channel = 'application'").fetchall()
        self.assertEqual(len(touch), 1)
        self.assertEqual((touch[0]["company_id"], touch[0]["posting_id"], touch[0]["person_id"]),
                         (self.ids["Ember"], a, None))
        self.assertEqual(json.loads(touch[0]["context"])["source"], "own")
        self.assertIn(a, applied_posting_ids(self.conn))

    def test_applied_twice_writes_once(self):
        a = self.job("Ember")
        self.applied(a)
        again = self.applied(a)
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json()["text"], "Already applied on Nov 10.")
        self.assertEqual(again.json()["appliedOn"], DAY)
        self.assertEqual(self.count("SELECT count(*) FROM application WHERE posting_id = ?", a), 1)
        self.assertEqual(self.count("SELECT count(*) FROM outreach_touch WHERE channel = 'application'"), 1)

    def test_applied_replays_the_same_key(self):
        a = self.job("Ember")
        first = self.applied(a, key="applied-key-0001")
        again = self.applied(a, key="applied-key-0001")
        self.assertEqual(again.headers["Idempotent-Replay"], "true")
        self.assertEqual(first.json(), again.json())
        self.assertEqual(self.count("SELECT count(*) FROM outreach_touch WHERE channel = 'application'"), 1)

    def test_applied_on_a_missing_posting_is_a_404(self):
        res = self.applied(999999)
        self.assertEqual((res.status_code, res.json()["code"]), (404, "not_found"))

    def test_applied_with_a_stray_field_is_a_400(self):
        a = self.job("Ember")
        self.assertEqual(self.post(f"/api/jobs/{a}/applied", {"company": 1}).status_code, 400)

    def test_applied_is_not_reached(self):
        a = self.job("Ember")
        self.applied(a)
        self.assertEqual(reached.marks(self.conn), {})
        self.assertEqual(self.count("SELECT status FROM company WHERE company_id = ?", self.ids["Ember"]), "candidate")
        self.assertEqual(self.count("SELECT count(*) FROM no_go WHERE company_id = ?", self.ids["Ember"]), 0)

    def test_applied_follows_a_merge_to_the_survivor(self):
        dupe = s.company(self.conn, "Ember Inc", "removed", merged_into=self.ids["Ember"])
        self.ids["Ember Inc"] = dupe
        a = self.job("Ember Inc")
        body = self.applied(a).json()
        self.assertEqual(body["companyId"], self.ids["Ember"])
        self.assertEqual(self.count("SELECT company_id FROM application WHERE posting_id = ?", a), self.ids["Ember"])
        self.assertEqual(self.count("SELECT company_id FROM outreach_touch WHERE channel = 'application'"),
                         self.ids["Ember"])

    def test_a_closed_posting_can_still_be_marked_applied(self):
        a = self.job("Ember")
        self.conn.execute("UPDATE posting SET closed_at = last_seen_at, close_reason = 'hand' WHERE posting_id = ?",
                          (a,))
        self.assertEqual(self.applied(a).status_code, 200)
        self.assertEqual(self.count("SELECT count(*) FROM application WHERE posting_id = ?", a), 1)


class AppliedHidesTest(AppliedCase):
    def test_applied_posting_leaves_both_tabs(self):
        a, b = self.job("Ember"), self.job("Ember", "Backend Engineer")
        senior = posting(self.conn, self.ids["Ember"], "Software Engineer II", posted_on="2026-11-09",
                         first_seen_at="2026-11-01T12:00:00Z", last_seen_at="2026-11-10T12:00:00Z")
        decision(self.conn, senior, level_tier="swe2", level_ok=0, level_state="reject")
        self.assertIn(senior, self.jobs(q="Ember", view="higher"))
        self.applied(a)
        self.applied(senior)
        self.assertEqual(self.jobs(q="Ember"), [b])
        self.assertNotIn(senior, self.jobs(q="Ember", view="higher"))

    def test_undone_application_touch_does_not_hide(self):
        a = self.job("Ember")
        ctx = build_context(source="own", lane="own", role_kind=None, role_age_days=None, ranker_version=None,
                            signals={}, role_named=True)
        tid = log_touch(self.conn, person_id=None, company_id=self.ids["Ember"], channel="application",
                        sent_at="2026-11-10T14:00:00Z", context=ctx, posting_id=a, idempotency_key="undo-me-0001")
        undo_touch(self.conn, tid, at="2026-11-10T14:05:00Z", revision=1)
        self.assertNotIn(a, applied_posting_ids(self.conn))
        self.assertEqual(self.jobs(q="Ember"), [a])
        res = self.applied(a)
        self.assertEqual(res.json()["text"], "Saved to Applied.")
        self.assertIn(a, applied_posting_ids(self.conn))
