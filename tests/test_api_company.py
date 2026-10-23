"""The company page: GET /api/companies/{id}, read from a scratch store seeded through SQL."""
import json
import re
import unittest
from types import SimpleNamespace
from unittest import mock

from outreach.api import db
from outreach.api.queries import company as company_queries
from tests import apiseed as s
from tests.apitest import NOW, ApiCase


class CompanyPage(ApiCase):
    def view(self, company_id: int) -> dict:
        res = self.get(f"/api/companies/{company_id}")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def test_company_with_nothing_is_a_valid_view(self):
        cid = s.company(self.conn, "Acme")
        body = self.view(cid)
        self.assertEqual(body["asOf"], NOW)
        self.assertEqual(
            body["company"],
            {"id": cid, "name": "Acme", "status": "candidate", "pausedAt": None, "heldByTouchId": None,
             "domain": None, "aliases": [], "hq": "", "pickedOn": None, "day": None, "closed": None, "revision": 1},
        )
        for key in ("facts", "postings", "people", "touches", "evidence", "events", "reviewItems"):
            self.assertEqual(body[key], [], key)
        self.assertIsNone(body["role"])
        self.assertIsNone(body["board"])
        self.assertEqual(body["sponsorshipShown"], "not_stated")
        self.assertIsNone(body["mailServerOk"])
        self.assertRegex(body["rulesetVersion"], r"^r-[0-9a-f]{12}$")
        self.assertTrue(body["summary"])
        self.assertNotIn("score", body)

    def test_unknown_company_is_404(self):
        res = self.get("/api/companies/999")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["code"], "not_found")

    def test_merged_company_answers_as_its_survivor_with_both_histories(self):
        a = s.company(self.conn, "Acme")
        b = s.company(self.conn, "Acme Labs", "removed", remove_reason="merged", merged_into=a)
        c = s.company(self.conn, "Acme Labs Inc", "removed", remove_reason="merged", merged_into=b)
        for cid, tag in ((a, "survivor"), (b, "middle"), (c, "last")):
            s.event(self.conn, cid, f"seen_{tag}", f"2026-11-0{1 + cid}T10:00:00Z")
            s.posting(self.conn, cid, f"Engineer {tag}")
            s.person(self.conn, cid, f"Person {tag}")
            s.evidence(self.conn, cid, "hq", f"City {tag}")
        for asked in (a, b, c):
            body = self.view(asked)
            self.assertEqual(body["company"]["id"], a, asked)
            self.assertEqual(body["company"]["name"], "Acme")
            self.assertEqual({e["kind"] for e in body["events"]}, {"seen_survivor", "seen_middle", "seen_last"})
            self.assertEqual({p["title"] for p in body["postings"]}, {f"Engineer {t}" for t in ("survivor", "middle", "last")})
            self.assertEqual(len(body["people"]), 3)
            self.assertEqual(len(body["evidence"]), 3)

    def test_facts_pick_the_locked_hand_value_over_a_newer_guess(self):
        cid = s.company(self.conn)
        s.evidence(self.conn, cid, "size", "20", locked=True, recorded_at="2026-10-01T10:00:00Z", low=20, high=20)
        s.evidence(self.conn, cid, "size", "10", confidence="guess", source_kind="linkedin",
                   recorded_at="2026-11-05T10:00:00Z", low=10, high=10)
        s.evidence(self.conn, cid, "stage", "seed", confidence="estimate")
        body = self.view(cid)
        by = {f["fact"]: f for f in body["facts"]}
        self.assertEqual(by["size"]["value"], "20")
        self.assertTrue(by["size"]["locked"])
        self.assertEqual(by["size"]["sourceKind"], "hand")
        self.assertEqual((by["size"]["low"], by["size"]["high"]), (20, 20))
        self.assertEqual(by["stage"]["confidence"], "estimate")
        self.assertEqual(len(body["facts"]), 2)
        self.assertEqual(len(body["evidence"]), 3)  # evidence lists every row, newest first
        self.assertEqual([e["value"] for e in body["evidence"]][0], "10")

    def test_facts_across_a_merged_pair_pick_one_winner_per_kind(self):
        a = s.company(self.conn, "Acme")
        b = s.company(self.conn, "Acme Labs", "removed", remove_reason="merged", merged_into=a)
        s.evidence(self.conn, a, "stage", "seed", confidence="guess")
        s.evidence(self.conn, b, "stage", "series_a", locked=True)
        body = self.view(a)
        self.assertEqual([f["value"] for f in body["facts"] if f["fact"] == "stage"], ["series_a"])

    def test_domain_hq_and_aliases_come_from_the_facts_and_alias_table(self):
        cid = s.company(self.conn, "Acme")
        s.evidence(self.conn, cid, "domain", "acme.example")
        s.evidence(self.conn, cid, "hq", "Austin, TX")
        s.alias(self.conn, cid, "Acme Labs")
        s.alias(self.conn, cid, "acme", source="name")  # the company's own name is not an alias
        s.alias(self.conn, cid, "ashby:acme", source="ats")  # a board key is not a name
        s.alias(self.conn, cid, "Acme Holdings LLC", source="formd")  # a name the SEC filing carries
        body = self.view(cid)["company"]
        self.assertEqual(
            (body["domain"], body["hq"], body["aliases"]), ("acme.example", "Austin, TX", ["Acme Holdings LLC", "Acme Labs"]))

    def test_sponsorship_shown_precedence(self):
        def shown(locked=None, posting=None, lca=None, unlocked=None) -> str:
            cid = s.company(self.conn, f"Co{self.conn.execute('SELECT COUNT(*) FROM company').fetchone()[0]}")
            if locked:
                s.evidence(self.conn, cid, "sponsorship", locked, locked=True)
            if unlocked:
                s.evidence(self.conn, cid, "sponsorship", unlocked, confidence="confirmed")
            if posting:
                s.decision(self.conn, s.posting(self.conn, cid), sponsorship=posting)
            if lca is not None:
                s.evidence(self.conn, cid, "lca", s.lca(software=lca))
            return self.view(cid)["sponsorshipShown"]

        with self.subTest("locked yes"):
            self.assertEqual(shown(locked="yes"), "says_yes")
        with self.subTest("locked no"):
            self.assertEqual(shown(locked="no"), "says_no")
        with self.subTest("locked opt_only"):
            self.assertEqual(shown(locked="opt_only"), "opt_only")
        with self.subTest("locked beats a posting and the filings"):
            self.assertEqual(shown(locked="no", posting="yes", lca=5), "says_no")
        with self.subTest("a locked unclear falls through to the posting"):
            self.assertEqual(shown(locked="unclear", posting="yes"), "says_yes")
        with self.subTest("an unlocked fact is not a decision"):
            self.assertEqual(shown(unlocked="yes"), "not_stated")
        for stored, expected in (("yes", "says_yes"), ("no", "says_no"), ("opt_only", "opt_only"), ("unclear", "unclear")):
            with self.subTest(f"best posting {stored}"):
                self.assertEqual(shown(posting=stored), expected)
        with self.subTest("a silent posting falls through to the filings"):
            self.assertEqual(shown(posting="silent", lca=2), "has_sponsored")
        with self.subTest("filings with no software filing say nothing"):
            self.assertEqual(shown(lca=0), "not_stated")
        with self.subTest("filings alone"):
            self.assertEqual(shown(lca=4), "has_sponsored")
        with self.subTest("nothing at all"):
            self.assertEqual(shown(), "not_stated")

    def test_a_hidden_or_off_lane_yes_is_not_says_yes_but_a_hidden_no_is_says_no(self):
        def shown(**cols) -> str:
            cid = s.company(self.conn, f"Co{self.conn.execute('SELECT COUNT(*) FROM company').fetchone()[0]}")
            s.decision(self.conn, s.posting(self.conn, cid), **cols)
            return self.view(cid)["sponsorshipShown"]

        with self.subTest("a yes on a hidden posting"):
            self.assertEqual(shown(sponsorship="yes", citizenship=1), "not_stated")
        with self.subTest("a yes on an off-lane posting"):
            self.assertEqual(shown(sponsorship="yes", lane="not_engineering", level_tier="not_stated", level_ok=0), "not_stated")
        with self.subTest("opt_only and unclear on a hidden posting"):
            self.assertEqual(shown(sponsorship="opt_only", us="no"), "not_stated")
            self.assertEqual(shown(sponsorship="unclear", eligibility="excluded"), "not_stated")
        with self.subTest("a no on a hidden posting is the evidence for the hide and is shown"):
            self.assertEqual(shown(sponsorship="no"), "says_no")
        with self.subTest("a no on an off-lane posting"):
            self.assertEqual(shown(sponsorship="no", lane="intern", level_tier="not_stated", level_ok=0), "says_no")

    def test_a_no_on_a_closed_posting_is_not_shown(self):
        cid = s.company(self.conn)
        s.decision(self.conn, s.posting(self.conn, cid, closed_at="2026-11-02T10:00:00Z"), sponsorship="no")
        self.assertEqual(self.view(cid)["sponsorshipShown"], "not_stated")

    def test_an_open_yes_beats_a_hidden_no_elsewhere(self):
        cid = s.company(self.conn)
        s.decision(self.conn, s.posting(self.conn, cid, "Open"), sponsorship="yes")
        s.decision(self.conn, s.posting(self.conn, cid, "Hidden"), sponsorship="no")
        self.assertEqual(self.view(cid)["sponsorshipShown"], "says_yes")

    def test_a_person_on_a_merged_duplicate_is_judged_against_the_survivors_pause(self):
        a = s.company(self.conn, "Acme", "working", paused_at="2026-11-09T12:00:00Z")
        b = s.company(self.conn, "Acme Labs", "removed", remove_reason="merged", merged_into=a)
        pid = s.person(self.conn, b, "Ann Approved")
        s.message(self.conn, s.touch(self.conn, pid), 0, "approved")
        lines = {p["person"]["name"]: p["statusLine"] for p in self.view(a)["people"]}
        self.assertEqual(lines["Ann Approved"], "Paused, approved")

    def test_a_merge_cycle_still_answers(self):
        a = s.company(self.conn, "Acme", "removed", remove_reason="merged")
        b = s.company(self.conn, "Acme Labs", "removed", remove_reason="merged", merged_into=a)
        self.conn.execute("UPDATE company SET merged_into = ? WHERE company_id = ?", (b, a))
        s.person(self.conn, a, "Pat Lee")
        for asked in (a, b):
            self.assertEqual(self.get(f"/api/companies/{asked}").status_code, 200)

    def test_mail_server_ok_is_tristate(self):
        cid = s.company(self.conn)
        self.assertIsNone(self.view(cid)["mailServerOk"])  # no domain
        s.evidence(self.conn, cid, "domain", "acme.example")
        self.assertIsNone(self.view(cid)["mailServerOk"])  # a domain nobody checked
        s.mx(self.conn, "acme.example", "ok")
        self.assertIs(self.view(cid)["mailServerOk"], True)
        self.conn.execute("UPDATE mx_check SET status = 'none'")
        self.assertIs(self.view(cid)["mailServerOk"], False)

    def test_hidden_posting_names_its_rule(self):
        cid = s.company(self.conn)
        cases = {
            "sponsorship": (dict(sponsorship="no"), "hidden", "Says it will not sponsor"),
            "citizenship": (dict(citizenship=1), "hidden", "Needs US citizenship or a clearance"),
            "non-US": (dict(us="no"), "hidden", "Not a US role"),
            "student": (dict(eligibility="excluded", eligibility_reason="current_student"), "hidden", "Only for current students"),
            "senior": (dict(level_tier="senior", level_ok=0, level_basis="Title says senior"), "out_of_lane", "Title says senior"),
            "years": (dict(level_tier="not_stated", level_ok=0, level_basis="Asks 5 years", years_asked=5), "out_of_lane", "Asks 5 years"),
            "off lane": (dict(lane="not_engineering", level_tier="not_stated", level_ok=0), "out_of_lane", "Not an engineering role"),
        }
        ids = {}
        for name, (cols, _state, _rule) in cases.items():
            ids[name] = s.posting(self.conn, cid, f"Role {name}")
            s.decision(self.conn, ids[name], **cols)
        open_id = s.posting(self.conn, cid, "Open role")
        s.decision(self.conn, open_id)
        closed_id = s.posting(self.conn, cid, "Closed role", closed_at="2026-11-02T10:00:00Z")
        undecided_id = s.posting(self.conn, cid, "Undecided role")
        by = {p["id"]: p for p in self.view(cid)["postings"]}
        for name, (_cols, state, rule) in cases.items():
            self.assertEqual((by[ids[name]]["state"], by[ids[name]]["hiddenRule"]), (state, rule), name)
        self.assertEqual((by[open_id]["state"], by[open_id]["hiddenRule"]), ("open", None))
        self.assertEqual((by[closed_id]["state"], by[closed_id]["closeReason"]), ("closed", "Closed on the board"))
        self.assertEqual((by[undecided_id]["state"], by[undecided_id]["levelTier"], by[undecided_id]["sponsorship"]), ("open", "not_stated", "silent"))
        self.assertEqual(by[open_id]["flags"], [])

    def test_the_role_to_name_is_the_best_open_posting_that_passes(self):
        cid = s.company(self.conn)
        hidden = s.posting(self.conn, cid, "Hidden role", first_seen_at="2026-11-09T10:00:00Z")
        s.decision(self.conn, hidden, sponsorship="no", level_tier="new_grad")
        closed = s.posting(self.conn, cid, "Closed new grad", closed_at="2026-11-02T10:00:00Z")
        s.decision(self.conn, closed)
        swe1 = s.posting(self.conn, cid, "Software Engineer I", first_seen_at="2026-11-08T10:00:00Z")
        s.decision(self.conn, swe1, level_tier="swe1")
        grad = s.posting(self.conn, cid, "Software Engineer, New Grad", first_seen_at="2026-11-01T10:00:00Z")
        s.decision(self.conn, grad, level_tier="new_grad", start_2027=1)
        role = self.view(cid)["role"]
        self.assertEqual(role["postingId"], grad)
        self.assertEqual(role["title"], "Software Engineer, New Grad")
        self.assertEqual((role["levelTier"], role["start2027"], role["yearsAsked"]), ("new_grad", True, None))
        self.assertEqual(role["firstSeenAt"], "2026-11-01T10:00:00Z")
        self.assertEqual(role["url"], self.conn.execute("SELECT url FROM posting WHERE posting_id = ?", (grad,)).fetchone()[0])
        postings = self.view(cid)["postings"]
        self.assertEqual([p["state"] for p in postings], ["open", "open", "hidden", "closed"])  # open first, newest first

    def test_company_events_are_newest_first_and_carry_the_import_source(self):
        cid = s.company(self.conn)
        s.event(self.conn, cid, "picked", "2026-11-03T10:00:00Z", actor="devanshu", detail={"text": "Picked from Today"})
        s.event(self.conn, cid, "stage", "2026-10-21T12:00:00Z", actor="import",
                detail={"text": "Stage Referral asked", "importSource": "data_js"})
        s.event(self.conn, cid, "board_read", "2026-11-09T08:04:00Z", actor="crawler", detail={})
        s.event(self.conn, None, "elsewhere", "2026-11-09T09:00:00Z")
        s.event(self.conn, cid, "old_key", "2026-10-01T12:00:00Z", actor="import", detail={"import_source": "data_js", "source": "devboard"})
        events = self.view(cid)["events"]
        self.assertEqual([e["kind"] for e in events], ["board_read", "picked", "stage", "old_key"])
        self.assertEqual(events[0]["text"], "Board read")  # no text in the detail: the kind in words
        self.assertEqual((events[1]["text"], events[1]["actor"], events[1]["importSource"]), ("Picked from Today", "devanshu", None))
        self.assertEqual((events[2]["actor"], events[2]["importSource"]), ("import", "data_js"))
        self.assertIsNone(events[3]["importSource"])  # only detail.importSource names the source

    def test_events_from_the_source_and_reader_actors_are_shown(self):
        cid = s.company(self.conn)
        s.event(self.conn, cid, "seen", "2026-11-03T10:00:00Z", actor="source")
        s.event(self.conn, cid, "read", "2026-11-04T10:00:00Z", actor="reader")
        self.assertEqual([e["actor"] for e in self.view(cid)["events"]], ["reader", "source"])

    def test_evidence_kinds_beyond_the_headline_six_are_shown_readably(self):
        cid = s.company(self.conn)
        s.evidence(self.conn, cid, "lca", s.lca(fy=2025, software=3), source_kind="lca")
        s.evidence(self.conn, cid, "funding", {"filed_on": "2026-03-01", "amount_usd": 5000000, "kind": "Form D"}, source_kind="formd")
        s.evidence(self.conn, cid, "staffing_risk", "low", source_kind="stapply")
        by = {e["fact"]: e for e in self.view(cid)["evidence"]}
        self.assertEqual(by["lca"]["value"], "3 software filings in FY2025")
        self.assertEqual(by["funding"]["value"], "Form D filed 2026-03-01, $5,000,000")
        self.assertEqual((by["staffing_risk"]["value"], by["staffing_risk"]["sourceKind"]), ("low", "stapply"))

    def test_board_and_its_last_read(self):
        cid = s.company(self.conn)
        s.board(self.conn, cid, "greenhouse", "old", active=0)
        s.board(self.conn, cid, "ashby", "acme", last_read="error", last_read_at="2026-11-09T08:04:00Z", error_streak=3)
        board = self.view(cid)["board"]
        self.assertEqual(
            board,
            {"ats": "Ashby", "token": "acme", "url": "https://jobs.ashbyhq.com/acme", "lastRead": "error",
             "lastReadAt": "2026-11-09T08:04:00Z", "errorStreak": 3, "emptyStreak": 0},
        )
        self.conn.execute("UPDATE board SET last_read = 'unchanged', last_read_at = ?, error_streak = 0 WHERE token = 'acme'", (NOW,))
        self.assertEqual(self.view(cid)["board"]["lastRead"], "ok")  # unchanged is a read that went fine

    def test_header_closed_pause_hold_and_picked_day(self):
        cid = s.company(self.conn, "Acme", "closed", close_reason="referral", status_note="Got a referral", status_at="2026-11-09T10:00:00Z")
        self.assertEqual(
            self.view(cid)["company"]["closed"], {"reason": "referral", "note": "Got a referral", "at": "2026-11-09T10:00:00Z"}
        )
        w = s.company(self.conn, "Working", "working", paused_at="2026-11-09T12:00:00Z")
        self.conn.execute("INSERT INTO daily_list(list_date, built_at, capacity) VALUES ('2026-11-06', ?, 5)", ("2026-11-06T13:00:00Z",))
        self.conn.execute(
            "INSERT INTO daily_list_entry(list_date, company_id, list_group, rank, state, proposed_at, decided_at) "
            "VALUES ('2026-11-06', ?, 'level_role', 1, 'picked', '2026-11-06T13:00:00Z', '2026-11-06T14:00:00Z')", (w,))
        header = self.view(w)["company"]
        self.assertEqual((header["pickedOn"], header["pausedAt"]), ("2026-11-06", "2026-11-09T12:00:00Z"))
        self.assertEqual(header["day"], 3)  # Fri, Mon, Tue (now is Tue Nov 10), capped at 3

    def test_people_status_lines_and_tone(self):
        cid = s.company(self.conn, "Acme", "working")
        replied = s.person(self.conn, cid, "Rae Replied")
        s.send_first(self.conn, s.touch(self.conn, replied, "replied"))
        bounced = s.person(self.conn, cid, "Bo Bounced")
        s.send_first(self.conn, s.touch(self.conn, bounced, "bounced"))
        sent = s.person(self.conn, cid, "Sam Sent")
        s.send_first(self.conn, s.touch(self.conn, sent))
        approved = s.person(self.conn, cid, "Ann Approved")
        s.message(self.conn, s.touch(self.conn, approved), 0, "approved")
        drafted = s.person(self.conn, cid, "Dee Drafted")
        s.message(self.conn, s.touch(self.conn, drafted), 0, "drafted")
        fresh = s.person(self.conn, cid, "Nia New")
        asked = s.person(self.conn, cid, "Lin Asked", channel="linkedin")
        s.linkedin(self.conn, asked, "request_sent", "2026-11-03T15:00:00Z")
        idle = s.person(self.conn, cid, "Lou Idle", channel="linkedin")
        undone = s.person(self.conn, cid, "Una Undone", channel="linkedin")
        first = s.linkedin(self.conn, undone, "request_sent", "2026-11-03T15:00:00Z")
        s.linkedin(self.conn, undone, "undo", "2026-11-03T16:00:00Z", undoes=first)
        lines = {p["person"]["name"]: (p["channel"], p["statusLine"], p["tone"]) for p in self.view(cid)["people"]}
        self.assertEqual(lines["Rae Replied"], ("email", "Replied", "ok"))
        self.assertEqual(lines["Bo Bounced"], ("email", "Bounced", "bad"))
        self.assertEqual(lines["Sam Sent"], ("email", "Sent", "idle"))
        self.assertEqual(lines["Ann Approved"], ("email", "Approved", "info"))
        self.assertEqual(lines["Dee Drafted"], ("email", "Draft to review", "idle"))
        self.assertEqual(lines["Nia New"], ("email", "Not started", "idle"))
        self.assertEqual(lines["Lin Asked"], ("linkedin", "Requested Nov 3", "info"))
        self.assertEqual(lines["Lou Idle"], ("linkedin", "Not started", "idle"))
        self.assertEqual(lines["Una Undone"], ("linkedin", "Not started", "idle"))
        last = {p["person"]["name"]: p["lastTouchAt"] for p in self.view(cid)["people"]}
        self.assertEqual(last["Sam Sent"], "2026-11-05T15:00:00Z")
        self.assertEqual(last["Lin Asked"], "2026-11-03T15:00:00Z")
        self.assertIsNone(last["Nia New"])
        person = next(p for p in self.view(cid)["people"] if p["person"]["name"] == "Rae Replied")["person"]
        self.assertEqual(set(person), {"id", "name", "title", "roleKind", "email", "linkedinUrl", "revision"})

    def test_a_held_company_marks_queued_people_as_held(self):
        cid = s.company(self.conn, "Acme", "working")
        talker = s.person(self.conn, cid, "Rae Replied")
        t = s.touch(self.conn, talker, "replied")
        s.send_first(self.conn, t)
        waiting = s.person(self.conn, cid, "Ann Approved")
        s.message(self.conn, s.touch(self.conn, waiting), 0, "approved")
        self.conn.execute("UPDATE company SET held_by_touch = ?, held_at = ? WHERE company_id = ?", (t, NOW, cid))
        body = self.view(cid)
        self.assertEqual(body["company"]["heldByTouchId"], t)
        lines = {p["person"]["name"]: p["statusLine"] for p in body["people"]}
        self.assertEqual(lines["Ann Approved"], "Held, approved")
        self.assertEqual(lines["Rae Replied"], "Replied")

    def test_touches_carry_their_threads_in_time_order(self):
        cid = s.company(self.conn, "Acme", "working")
        pid = s.person(self.conn, cid, "Rae Replied")
        tid = s.touch(self.conn, pid, "replied")
        s.send_first(self.conn, tid, "2026-11-05T15:00:00Z")
        s.inbound(self.conn, tid, "2026-11-09T10:00:00Z", from_addr="rae@acme.example", body="Happy to help")
        s.message(self.conn, tid, 1, "drafted")  # a draft is not part of the thread yet
        other = s.touch(self.conn, s.person(self.conn, cid, "Nia New"))
        touches = {t["touchId"]: t for t in self.view(cid)["touches"]}
        thread = touches[tid]["messages"]
        self.assertEqual([(m["direction"], m["seq"], m["at"]) for m in thread],
                         [("out", 0, "2026-11-05T15:00:00Z"), ("in", None, "2026-11-09T10:00:00Z")])
        self.assertEqual(thread[1]["from"], "rae@acme.example")
        self.assertEqual((thread[0]["subject"], thread[0]["body"]), ("Hello", "Hi there"))
        self.assertEqual(touches[tid]["state"], "replied")
        self.assertEqual(touches[tid]["summary"], "First email sent Nov 5. Replied Nov 9")
        self.assertEqual(touches[tid]["person"]["name"], "Rae Replied")
        self.assertEqual(touches[other]["messages"], [])
        self.assertEqual(touches[other]["summary"], "Not started")

    def test_review_items_are_the_open_ones_with_their_evidence(self):
        cid = s.company(self.conn, "Acme", "working")
        b = s.board(self.conn, cid, "ashby", "acme", last_read="error", error_streak=2)
        p = s.posting(self.conn, cid, "Engineer", board_id=b)
        s.decision(self.conn, p, sponsorship="unclear", sponsorship_quote="We may sponsor for exceptional candidates.")
        for run, (at, result) in enumerate([("2026-11-01T08:00:00Z", "ok"), ("2026-11-05T08:00:00Z", "error")], start=1):
            self.conn.execute("INSERT INTO job_run(job, started_at, finished_at, status) VALUES ('read_boards', ?, ?, 'ok')", (at, at))
            self.conn.execute(
                "INSERT INTO board_read(board_id, run_id, at, result, postings, error, http_status) VALUES (?,?,?,?,?,?,?)",
                (b, run, at, result, 1 if result == "ok" else 0, "HTTP 503" if result == "error" else "", None))
        s.evidence(self.conn, cid, "size", "30", confidence="guess", source_kind="linkedin", low=30)
        unreadable = s.review(self.conn, cid, "board_unreadable", board_id=b, created_at="2026-11-05T08:00:00Z")
        unclear = s.review(self.conn, cid, "sponsorship_unclear", posting_id=p, evidence_key="k2")
        size = s.review(self.conn, cid, "size_guess", evidence_key="k3")
        s.review(self.conn, cid, "domain_missing", evidence_key="k4", state="resolved")
        s.review(self.conn, cid, "merge_candidate", evidence_key="k5")  # names no second company: nothing to decide
        items = {i["id"]: i for i in self.view(cid)["reviewItems"]}
        self.assertEqual(set(items), {unreadable, unclear, size})
        u = items[unreadable]
        self.assertEqual((u["kind"], u["ageDays"], u["openPostings"], u["lastOkAt"]), ("board_unreadable", 5, 1, "2026-11-01T08:00:00Z"))
        self.assertEqual([(r["result"], r["detail"]) for r in u["reads"]], [("ok", "Ashby board read, 1 posting"), ("error", "HTTP 503")])
        self.assertEqual(u["company"]["id"], cid)
        self.assertFalse(u["company"]["picked"])
        self.assertEqual(items[unclear]["quote"]["text"], "We may sponsor for exceptional candidates.")
        self.assertEqual(items[unclear]["quote"]["postingId"], p)
        self.assertEqual(items[size]["sizeFacts"][0]["value"], "30")
        self.assertIsNone(items[size]["quote"])

    def test_one_query_per_table_not_one_per_row(self):
        def statements(people: int) -> int:
            cid = s.company(self.conn, f"Co{people}", "working")
            for i in range(people):
                p = s.person(self.conn, cid, f"Person {i}")
                t = s.touch(self.conn, p)
                s.send_first(self.conn, t)
                s.inbound(self.conn, t, "2026-11-09T10:00:00Z")
                s.posting(self.conn, cid, f"Role {i}")
                s.evidence(self.conn, cid, "hq", f"City {i}", recorded_at=f"2026-11-0{1 + i}T10:00:00Z")
                s.event(self.conn, cid, f"k{i}", f"2026-11-0{1 + i}T10:00:00Z")
            seen: list[str] = []
            original = db.connect_for

            def traced(settings):
                conn = original(settings)
                conn.set_trace_callback(lambda sql: seen.append(sql) if sql.lstrip().upper().startswith("SELECT") else None)
                return conn

            with mock.patch.object(db, "connect_for", traced):
                self.view(cid)
            return len(seen)

        self.assertEqual(statements(1), statements(6))

    def test_posting_flags_are_empty_without_the_reader_package(self):
        cid = s.company(self.conn)
        p = s.posting(self.conn, cid)
        s.decision(self.conn, p)
        self._reading(p)
        with mock.patch.object(company_queries, "load_explain", lambda: None):
            self.assertEqual(self.view(cid)["postings"][0]["flags"], [])

    def test_posting_flags_come_from_explain(self):
        cid = s.company(self.conn)
        flagged = s.posting(self.conn, cid, "Has a reading")
        s.decision(self.conn, flagged, sponsorship="yes", sponsorship_quote="We sponsor visas.")
        unread = s.posting(self.conn, cid, "Has none")
        s.decision(self.conn, unread)
        self._reading(flagged)
        verdict = dict(field="sponsorship", state="disagree", reader_value="wont_sponsor", rules_value="yes",
                       reader_quote="We cannot sponsor visas.", would_hide_rules=False, would_hide_reader=True)
        calls = []

        def explain(conn, posting_id):
            calls.append(posting_id)
            return (
                SimpleNamespace(**verdict),
                SimpleNamespace(**{**verdict, "field": "location", "state": "agree"}),  # agreeing fields are not flags
                SimpleNamespace(**{**verdict, "field": "citizenship_or_clearance", "state": "missing", "would_hide_reader": False}),
            )

        with mock.patch.object(company_queries, "load_explain", lambda: explain):
            postings = {p["id"]: p for p in self.view(cid)["postings"]}
        self.assertEqual(calls, [flagged])  # asked only for the posting that has a reading, once
        self.assertEqual(
            postings[flagged]["flags"],
            [{"field": "sponsorship", "readerValue": "wont_sponsor", "rulesValue": "yes",
              "readerQuote": "We cannot sponsor visas.", "rulesQuote": "We sponsor visas."}],
        )
        self.assertEqual(postings[unread]["flags"], [])

    def test_a_missing_reader_is_not_an_error_but_a_failing_explain_is(self):
        cid = s.company(self.conn)
        p = s.posting(self.conn, cid)
        s.decision(self.conn, p)
        self._reading(p)

        def explain(conn, posting_id):
            raise RuntimeError("reader broke")

        with mock.patch.object(company_queries, "load_explain", lambda: explain):
            res = self.get(f"/api/companies/{cid}")
        self.assertEqual(res.status_code, 500)  # never swallowed: a broken reader is not "no flags"

    @unittest.skipUnless(company_queries.load_explain() is not None, "needs the reader package from #32 (outreach.reader.combine.explain)")
    def test_flags_from_the_real_reader(self):
        cid = s.company(self.conn)
        p = s.posting(self.conn, cid, "Engineer")
        s.decision(self.conn, p, sponsorship="yes", sponsorship_quote="We sponsor visas.")
        self.conn.execute(
            "INSERT INTO posting_body(posting_id, body, body_sha256) VALUES (?, 'We cannot sponsor visas.', ?)", (p, "a" * 64))
        self._reading(p, sponsorship="wont_sponsor")
        flags = self.view(cid)["postings"][0]["flags"]
        self.assertEqual([f["field"] for f in flags], ["sponsorship"])
        self.assertEqual((flags[0]["readerValue"], flags[0]["rulesValue"]), ("wont_sponsor", "yes"))

    def _reading(self, posting_id: int, **fields) -> None:
        self.conn.execute(
            "INSERT INTO posting_reading(posting_id, body_sha256, reader_version, read_at, fields, verified) "
            "VALUES (?, ?, 'rd-test', ?, ?, 1)",
            (posting_id, "a" * 64, NOW, json.dumps(fields)),
        )

    def test_summary_is_one_plain_sentence(self):
        cid = s.company(self.conn, "Acme", "working")
        p = s.posting(self.conn, cid, "Software Engineer, New Grad")
        s.decision(self.conn, p)
        s.person(self.conn, cid, "Em Mail")
        s.person(self.conn, cid, "Lin Kedin", channel="linkedin")
        summary = self.view(cid)["summary"]
        self.assertRegex(summary, r"^[A-Z][^\n]*\.$")
        self.assertEqual(summary.count(". "), 0)
        for text in ("Software Engineer, New Grad", "1 by email", "1 on LinkedIn"):
            self.assertIn(text, summary)

    def test_a_large_page_is_gzipped(self):
        cid = s.company(self.conn)
        for i in range(30):
            s.posting(self.conn, cid, f"Software Engineer number {i}")
        res = self.get(f"/api/companies/{cid}", headers={"Accept-Encoding": "gzip"})
        self.assertGreater(len(res.content), 0)
        self.assertEqual(res.headers.get("content-encoding"), "gzip")
        small = self.get(f"/api/companies/{s.company(self.conn, 'Tiny')}", headers={"Accept-Encoding": "gzip"})
        self.assertNotIn("gzip", small.headers.get("content-encoding", ""))


if __name__ == "__main__":
    unittest.main()
