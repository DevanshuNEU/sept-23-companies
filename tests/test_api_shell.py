"""Task 22: the shell summary (`GET /api/shell`): the sidebar's counts, the sender line, #33's plan and winsUnseen."""
import json
import os
import time
import unittest

from tests import apiseed as s
from tests.apitest import NOW, ApiCase

TODAY = "2026-11-10"
CONTEXT = json.dumps({"source": "own", "lane": "own", "roleKind": "engineer", "roleAgeDays": 0, "rankerVersion": "r1",
                      "signals": {}, "roleNamed": False})
FIELDS = {"asOf", "pickedToday", "capacityToday", "unhandledReplies", "draftsToReview", "linkedinToReach",
          "openReviewItems", "noGoCount", "doneCount", "sending", "repliesThisMonth", "jobs", "plan", "winsUnseen"}


def outreach_touch(conn, company, person, channel="linkedin_request", sent_at="2026-11-10T13:00:00Z", **cols) -> int:
    return s._insert(conn, "outreach_touch", {
        "person_id": person, "company_id": company, "channel": channel, "sent_at": sent_at, "context": CONTEXT,
        "created_at": sent_at, "idempotency_key": f"shell-{next(s._n)}", **cols})


class Shell(ApiCase):
    def shell(self) -> dict:
        res = self.get("/api/shell")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def seed(self) -> dict:
        """One store with something of every kind. Returns the ids the tests refer to."""
        conn = self.conn
        ids = {}
        # Today: a list of three rows, two picked. The first picked row has had a request since this morning.
        conn.execute("INSERT INTO daily_list(list_date, built_at, capacity) VALUES (?, ?, 9)", (TODAY, "2026-11-10T13:00:00Z"))
        rows = []
        for rank, (name, state) in enumerate([("Picked1", "picked"), ("Picked2", "picked"), ("Proposed", "proposed")], 1):
            cid = s.company(conn, name, "working" if state == "picked" else "candidate")
            rows.append(cid)
            s._insert(conn, "daily_list_entry", {
                "list_date": TODAY, "company_id": cid, "list_group": "level_role", "rank": rank, "state": state,
                "proposed_at": "2026-11-10T13:00:00Z", "decided_at": None if state == "proposed" else "2026-11-10T13:30:00Z"})
        outreach_touch(conn, rows[0], s.person(conn, rows[0], "Lena", channel="linkedin"), "linkedin_request")
        ids["picked"] = rows
        # Drafts to review: two open threads with a drafted email; one at a paused company and one approved do not count.
        work = s.company(conn, "Work", "working")
        for name in ("D1", "D2"):
            s.message(conn, s.touch(conn, s.person(conn, work, name)), 0, "drafted")
        paused = s.company(conn, "Paused", "working", paused_at="2026-11-09T10:00:00Z")
        s.message(conn, s.touch(conn, s.person(conn, paused, "Pa")), 0, "drafted")
        s.message(conn, s.touch(conn, s.person(conn, work, "Ap")), 0, "approved")
        # Replies: two unsorted, one sorted, all this month.
        for name, kind in (("R1", "unsorted"), ("R2", "unsorted"), ("R3", "interested")):
            s.send_first(conn, s.touch(conn, s.person(conn, work, name), "replied", reply_kind=kind), "2026-11-05T15:00:00Z")
        # LinkedIn: three to reach; one waiting (a request), one with a no-go, one at a removed company.
        li = s.company(conn, "Li", "candidate")
        for name in ("L1", "L2", "L3"):
            s.person(conn, li, name, channel="linkedin")
        outreach_touch(conn, li, s.person(conn, li, "Waiting", channel="linkedin"))
        nogo = s.person(conn, li, "Blocked", channel="linkedin")
        conn.execute("INSERT INTO no_go(person_id, reason, source, created_at) VALUES (?, 'other', 'devanshu', ?)", (nogo, s.T0))
        gone = s.company(conn, "Gone", "removed", remove_reason="not_a_fit")
        s.person(conn, gone, "G1", channel="linkedin")
        # Needs you: two open items and a resolved one.
        s.review(conn, li, "size_unknown", evidence_key="a")
        s.review(conn, work, "domain_missing", evidence_key="b")
        s.review(conn, work, "size_guess", evidence_key="c", state="resolved")
        # No-go: a company, a person (above), a removed company (above), a lifted row that does not count.
        bad = s.company(conn, "Bad", "candidate")
        conn.execute("INSERT INTO no_go(company_id, reason, source, created_at) VALUES (?, 'agency', 'rules', ?)", (bad, s.T0))
        conn.execute("INSERT INTO no_go(company_id, reason, source, created_at, lifted_at, lift_reason) "
                     "VALUES (?, 'other', 'rules', ?, ?, 'ok')", (li, s.T0, NOW))
        # Done: two.
        s.company(conn, "Int", "closed", close_reason="interview")
        s.company(conn, "Ref", "closed", close_reason="referral")
        # Wins: two touches with an accept; he has seen one.
        a1, a2 = (outreach_touch(conn, li, s.person(conn, li, f"W{n}", channel="linkedin"),
                                 accepted_at="2026-11-10T14:00:00Z") for n in (1, 2))
        conn.execute("INSERT INTO job_run(job, started_at, finished_at, status) VALUES ('rank', ?, ?, 'ok')",
                     ("2026-11-10T08:00:00Z", "2026-11-10T08:05:00Z"))
        ids["wins"] = (a1, a2)
        return ids

    def test_empty_store_shell_is_all_zero_and_valid(self):
        body = self.shell()
        self.assertEqual(set(body), FIELDS)
        self.assertEqual(body["asOf"], NOW)
        for key in ("pickedToday", "unhandledReplies", "draftsToReview", "linkedinToReach",
                    "openReviewItems", "noGoCount", "doneCount", "repliesThisMonth", "winsUnseen"):
            self.assertEqual(body[key], 0, key)
        self.assertEqual([j["job"] for j in body["jobs"]], ["rebuild"])  # the store's own install run
        self.assertEqual(body["plan"], {"done": 0, "total": 0})
        self.assertEqual(body["capacityToday"], 7)  # no list yet: the one capacity rule, LinkedIn alone while the sender waits
        self.assertEqual(body["sending"]["sentToday"], 0)
        health = self.get("/api/health").json()["sender"]
        self.assertEqual(body["sending"], health)

    def test_each_count_against_a_seeded_store(self):
        ids = self.seed()
        body = self.shell()
        self.assertEqual(body["pickedToday"], 2)
        self.assertEqual(body["capacityToday"], 9)
        self.assertEqual(body["unhandledReplies"], 2)
        self.assertEqual(body["draftsToReview"], 2)
        self.assertEqual(body["linkedinToReach"], 3)  # L1 to L3; the rest have a request, a no-go or a removed company
        self.assertEqual(body["openReviewItems"], 2)
        self.assertEqual(body["noGoCount"], 3)  # company, person and the removed company
        self.assertEqual(body["doneCount"], 2)
        self.assertEqual(body["repliesThisMonth"], 3)
        self.assertEqual({j["job"] for j in body["jobs"]}, {"rank", "rebuild"})
        self.assertEqual(body["plan"], {"done": 1, "total": 2})
        self.assertEqual(body["winsUnseen"], 2)
        self.conn.execute("INSERT INTO win_seen(touch_id, kind, seen_at) VALUES (?, 'accepted', ?)", (ids["wins"][0], NOW))
        self.assertEqual(self.shell()["winsUnseen"], 1)

    def test_shell_agrees_with_the_views_it_summarizes(self):
        self.seed()
        body = self.shell()
        outbox = self.get("/api/outbox").json()
        self.assertEqual(body["draftsToReview"], outbox["counts"]["review"])
        self.assertEqual(body["unhandledReplies"], outbox["counts"]["replied"] - 1)  # one reply is already sorted
        self.assertEqual(body["sending"], {"state": outbox["sending"]["state"], "sentToday": outbox["sending"]["sentToday"],
                                           "limitToday": outbox["sending"]["limitToday"]})
        self.assertEqual(body["linkedinToReach"], self.get("/api/linkedin").json()["counts"]["toReach"])
        self.assertEqual(body["openReviewItems"], len(self.get("/api/needs-you").json()["open"]))
        no_go = self.get("/api/no-go").json()
        self.assertEqual(body["noGoCount"], len(no_go["companies"]) + len(no_go["people"]))
        done = self.get("/api/done").json()
        self.assertEqual((body["doneCount"], body["repliesThisMonth"]), (len(done["companies"]), done["metrics"]["repliesThisMonth"]))
        progress = self.get("/api/progress").json()["today"]
        self.assertEqual(body["plan"], {"done": progress["done"], "total": progress["planned"]})
        self.assertEqual(body["winsUnseen"], self.get("/api/wins").json()["unseen"])
        today = self.get("/api/today").json()
        self.assertEqual((body["pickedToday"], body["capacityToday"]), (today["picked"], today["capacity"]))
        self.assertEqual(body["jobs"], self.get("/api/health").json()["jobs"])

    def test_counts_agree_with_the_views_on_the_edge_cases(self):
        conn = self.conn
        keep = s.company(conn, "Keep", "candidate")
        dup = s.company(conn, "Keep Inc", "removed", merged_into=keep)
        s.person(conn, dup, "OnDuplicate", channel="linkedin")  # listed once, under the survivor
        blocked = s.company(conn, "Blocked", "candidate")
        s.person(conn, blocked, "AtNoGoCompany", channel="linkedin")
        conn.execute("INSERT INTO no_go(company_id, reason, source, created_at) VALUES (?, 'other', 'devanshu', ?)",
                     (blocked, s.T0))
        closed = s.company(conn, "Closed", "closed", close_reason="not_now")
        s.person(conn, closed, "AtClosed", channel="linkedin")
        s.person(conn, closed, "Started", channel="linkedin")
        outreach_touch(conn, closed, conn.execute("SELECT person_id FROM person WHERE name = 'Started'").fetchone()[0])
        other = s.company(conn, "Other", "candidate")
        s.review(conn, keep, "merge_candidate", evidence_key=str(dup))  # one company now: nothing to decide
        s.review(conn, keep, "merge_candidate", evidence_key=str(other))
        s.review(conn, keep, "merge_candidate", evidence_key="not-a-number")
        for name in ("D1", "D2"):  # a draft at a duplicate is judged by the survivor
            s.message(conn, s.touch(conn, s.person(conn, dup, name)), 0, "drafted")
        s.message(conn, s.touch(conn, s.person(conn, closed, "D3")), 0, "drafted")
        body = self.shell()
        self.assertEqual(body["linkedinToReach"], self.get("/api/linkedin").json()["counts"]["toReach"])
        self.assertEqual(body["linkedinToReach"], 1)
        self.assertEqual(body["openReviewItems"], len(self.get("/api/needs-you").json()["open"]))
        self.assertEqual(body["draftsToReview"], self.get("/api/outbox").json()["counts"]["review"])
        self.assertEqual(body["draftsToReview"], 2)

    @unittest.skipIf(os.environ.get("OUTREACH_FAST_TESTS") == "1", "a timing guard: skipped in the fast run")
    def test_shell_stays_fast_on_a_large_store(self):
        self.seed()
        conn = self.conn
        conn.executemany(
            "INSERT INTO company(name, status, status_at, created_at) VALUES (?, 'candidate', ?, ?)",
            [(f"Bulk {n}", s.T0, s.T0) for n in range(20000)])
        first = conn.execute("SELECT min(company_id) FROM company WHERE name = 'Bulk 0'").fetchone()[0]
        conn.executemany(
            "INSERT INTO posting(company_id, source_key, title, location, url, first_seen_at, last_seen_at) "
            "VALUES (?, ?, 'Software Engineer', 'Remote', ?, ?, ?)",
            [(first + n % 20000, f"url:bulk-{n}", f"https://jobs.example/{n}", s.T0, s.T0) for n in range(60000)])
        self.shell()  # warm: the first call imports and compiles
        started = time.perf_counter()
        self.shell()
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 0.2, f"the shell took {elapsed * 1000:.0f} ms on 20,000 companies and 60,000 postings")
