"""The Applied list (#218): `GET /api/applied` and the sidebar's count. Scratch store, no network.

Every live application touch that names a posting, newest first: the role and its job link, the company after any
merge, the day he applied, and whether the posting is still open. An undone touch is left out.
"""
from outreach.outcomes.context import build_context
from outreach.store.writes import log_touch, undo_touch
from tests import apiseed as s
from tests.test_api_jobs_applied import AppliedCase


class AppliedListTest(AppliedCase):
    def rows(self):
        res = self.get("/api/applied")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["rows"]

    def test_newest_first_with_the_job_link_and_company(self):
        older = self.with_url(self.job("Ember", "Backend Engineer"), "https://jobs.example/ember/older")
        newer = self.with_url(self.job("Ember"), "https://jobs.example/ember/newer")
        self.applied(older)
        self.applied(newer)
        rows = [r for r in self.rows() if r["companyId"] == self.ids["Ember"]]
        self.assertEqual([r["postingId"] for r in rows], [newer, older])
        self.assertEqual(rows[0], {"postingId": newer, "companyId": self.ids["Ember"], "companyName": "Ember",
                                   "title": "Software Engineer", "url": "https://jobs.example/ember/newer",
                                   "appliedOn": "2026-11-10", "open": True})

    def test_a_closed_posting_says_so(self):
        a = self.job("Ember")
        self.applied(a)
        self.conn.execute("UPDATE posting SET closed_at = last_seen_at, close_reason = 'hand' WHERE posting_id = ?",
                          (a,))
        row, = [r for r in self.rows() if r["postingId"] == a]
        self.assertFalse(row["open"])

    def test_the_application_url_stands_in_for_an_empty_posting_url(self):
        a = self.job("Ember")
        self.applied(a)
        self.conn.execute("UPDATE application SET url = 'https://apply.example/1' WHERE posting_id = ?", (a,))
        row, = [r for r in self.rows() if r["postingId"] == a]
        self.assertEqual(row["url"], "https://apply.example/1")

    def test_an_undone_touch_is_left_out(self):
        a = self.job("Ember")
        ctx = build_context(source="own", lane="own", role_kind=None, role_age_days=None, ranker_version=None,
                            signals={}, role_named=True)
        tid = log_touch(self.conn, person_id=None, company_id=self.ids["Ember"], channel="application",
                        sent_at="2026-11-10T14:00:00Z", context=ctx, posting_id=a, idempotency_key="undo-me-0002")
        undo_touch(self.conn, tid, at="2026-11-10T14:05:00Z", revision=1)
        self.assertNotIn(a, [r["postingId"] for r in self.rows()])

    def test_a_company_merged_away_later_shows_as_its_survivor(self):
        dupe = s.company(self.conn, "Ember Labs")
        self.ids["Ember Labs"] = dupe
        a = self.job("Ember Labs")
        self.applied(a)
        self.conn.execute("UPDATE company SET status = 'removed', remove_reason = 'duplicate', merged_into = ? "
                          "WHERE company_id = ?", (self.ids["Ember"], dupe))
        row, = [r for r in self.rows() if r["postingId"] == a]
        self.assertEqual((row["companyId"], row["companyName"]), (self.ids["Ember"], "Ember"))

    def test_the_shell_counts_the_rows(self):
        self.applied(self.job("Ember"))
        self.applied(self.job("Ember", "Backend Engineer"))
        shell = self.get("/api/shell").json()
        self.assertEqual(shell["appliedCount"], len(self.rows()))
        self.assertGreaterEqual(shell["appliedCount"], 2)
