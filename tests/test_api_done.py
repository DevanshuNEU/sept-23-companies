"""Task 21: the Done view (`GET /api/done`): companies closed with an interview or a referral, and the reply metrics."""
import json

from tests import apiseed as s
from tests.apitest import NOW, ApiCase

CONTEXT = json.dumps({"source": "own", "lane": "own", "roleKind": "engineer", "roleAgeDays": 0, "rankerVersion": "r1",
                      "signals": {}, "roleNamed": False})


def reply(conn, company, person, replied_at, *, channel="linkedin_request", kind="interested", sent_at="2026-09-01T12:00:00Z",
          undone_at=None, key=None) -> int:
    return s._insert(conn, "outreach_touch", {
        "person_id": person, "company_id": company, "channel": channel, "sent_at": sent_at, "replied_at": replied_at,
        "reply_kind": kind, "context": CONTEXT, "created_at": sent_at, "undone_at": undone_at,
        "idempotency_key": key or f"k{s._n.__next__()}"})


class Done(ApiCase):
    def done(self) -> dict:
        res = self.get("/api/done")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def test_done_lists_only_interview_and_referral_closes(self):
        s.company(self.conn, "Interviewed", "closed", close_reason="interview", status_at="2026-11-02T10:00:00Z",
                  status_note="Phone screen Friday")
        s.company(self.conn, "Referred", "closed", close_reason="referral", status_at="2026-11-05T10:00:00Z")
        s.company(self.conn, "NoVisa", "closed", close_reason="no_sponsorship")
        s.company(self.conn, "NotNow", "closed", close_reason="not_now")
        s.company(self.conn, "Working", "working")
        done = self.done()
        self.assertEqual([(d["company"]["name"], d["outcome"], d["outcomeLabel"]) for d in done["companies"]],
                         [("Referred", "referral", "Referral"), ("Interviewed", "interview", "Interview")])
        self.assertEqual(done["companies"][1]["note"], "Phone screen Friday")
        self.assertEqual(done["companies"][1]["closedAt"], "2026-11-02T10:00:00Z")
        self.assertEqual((done["metrics"]["referrals"], done["metrics"]["interviews"]), (1, 1))

    def test_how_it_started_role_and_imported(self):
        a = s.company(self.conn, "Alpha", "closed", close_reason="referral")
        kai = s.person(self.conn, a, "Kai Andersen", "engineer")
        post = s.posting(self.conn, a, "Software Engineer, Early Career")
        s._insert(self.conn, "outreach_touch", {
            "person_id": kai, "company_id": a, "channel": "linkedin_request", "posting_id": post,
            "sent_at": "2026-10-20T12:00:00Z", "context": CONTEXT, "created_at": s.T0})
        b = s.company(self.conn, "Beta", "closed", close_reason="interview")
        s.event(self.conn, b, "imported", "2026-10-18T12:00:00Z", actor="import", detail={"importSource": "data_js"})
        rows = {d["company"]["name"]: d for d in self.done()["companies"]}
        self.assertEqual((rows["Alpha"]["howStarted"], rows["Alpha"]["role"], rows["Alpha"]["imported"]),
                         ("LinkedIn request to Kai Andersen, engineer", "Software Engineer, Early Career", False))
        self.assertEqual((rows["Beta"]["howStarted"], rows["Beta"]["role"], rows["Beta"]["imported"]),
                         ("Before the switch", None, True))

    def test_done_metrics_by_new_york_week(self):
        co = s.company(self.conn, "Acme", "working")
        pat = s.person(self.conn, co, "Pat")
        # NOW is Tuesday 2026-11-10 in New York; the week starts Monday 2026-11-09 at 05:00Z (EST).
        reply(self.conn, co, pat, "2026-11-09T04:59:59Z")  # Sunday night: the week of Nov 2
        reply(self.conn, co, pat, "2026-11-09T05:00:00Z")  # Monday midnight: the week of Nov 9
        reply(self.conn, co, pat, "2026-11-10T14:00:00Z")
        reply(self.conn, co, pat, "2026-09-22T14:00:00Z")  # the oldest of the eight weeks (week of Sep 21)
        reply(self.conn, co, pat, "2026-09-20T14:00:00Z")  # the week of Sep 14: outside the eight
        email = s.touch(self.conn, s.person(self.conn, co, "Em"), "replied", replied_at="2026-11-04T12:00:00Z",
                        reply_kind="interested")
        s.send_first(self.conn, email, "2026-11-03T12:00:00Z")
        auto = s.touch(self.conn, s.person(self.conn, co, "Auto"), auto_reply_at="2026-11-04T12:00:00Z")
        s.send_first(self.conn, auto, "2026-11-04T09:00:00Z")
        s.send_first(self.conn, s.touch(self.conn, s.person(self.conn, co, "Quiet")), "2026-10-30T12:00:00Z")  # last month
        metrics = self.done()["metrics"]
        self.assertEqual([w["weekOf"] for w in metrics["repliesPerWeek"]],
                         ["2026-09-21", "2026-09-28", "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26",
                          "2026-11-02", "2026-11-09"])
        self.assertEqual([w["count"] for w in metrics["repliesPerWeek"]], [1, 0, 0, 0, 0, 0, 2, 2])
        self.assertEqual(metrics["repliesThisMonth"], 4)  # Nov 4 email reply, Nov 9 05:00Z, Nov 9 and Nov 10 touches
        # Of the two first emails sent this month, one replied (the cohort, not every reply of the month).
        self.assertEqual(metrics["replyRate"], {"replies": 1, "firstEmails": 2})

    def test_an_email_reply_with_a_linked_outreach_touch_counts_once(self):
        co = s.company(self.conn, "Acme", "working")
        pat = s.person(self.conn, co, "Pat")
        email = s.touch(self.conn, pat, "replied", replied_at="2026-11-04T12:00:00Z", reply_kind="interested")
        s.send_first(self.conn, email, "2026-11-03T12:00:00Z")
        row = reply(self.conn, co, pat, "2026-11-04T12:00:00Z", channel="email", sent_at="2026-11-03T12:00:00Z")
        self.conn.execute("UPDATE outreach_touch SET email_touch_id = ? WHERE touch_id = ?", (email, row))
        self.assertEqual(self.done()["metrics"]["repliesThisMonth"], 1)
