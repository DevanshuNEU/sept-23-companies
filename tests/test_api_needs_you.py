"""Needs you: the open review items with their evidence, the one-click fixes, the merge and the unmerge."""
import json

from outreach.drafts.followups import company_map, held_survivors, no_go_sets
from outreach.sending import queue, ramp
from outreach.sending.config import load_config
from tests import apiseed as s
from tests.apitest import NOW, ApiCase
from tests.sendtest import MON, seed_email_person, seed_message

TODAY = "2026-11-10"  # NOW in New York
KINDS = ("board_unreadable", "board_empty", "sponsorship_unclear", "size_unknown", "size_guess", "domain_missing",
         "flagged", "merge_candidate", "reader_disagrees", "careers_changed", "size_check")


def run(conn, job: str, at: str, status: str = "ok") -> int:
    return conn.execute("INSERT INTO job_run(job, started_at, finished_at, status) VALUES (?, ?, ?, ?)",
                        (job, at, at, status)).lastrowid


def board_read(conn, board_id: int, at: str, result: str, postings: int = 0, error: str = "") -> None:
    rid = run(conn, "read_boards", at)
    conn.execute("INSERT INTO board_read(board_id, run_id, at, result, postings, error) VALUES (?, ?, ?, ?, ?, ?)",
                 (board_id, rid, at, result, postings, error))


def plan_entry(conn, company_id: int, state: str = "picked", *, list_date: str = TODAY, rank: int | None = None,
               person_id: int | None = None) -> None:
    conn.execute("INSERT INTO daily_list(list_date, built_at, capacity) VALUES (?, ?, 10) ON CONFLICT DO NOTHING",
                 (list_date, f"{list_date}T12:00:00Z"))
    rank = rank or conn.execute("SELECT count(*) + 1 FROM daily_list_entry WHERE list_date = ?", (list_date,)).fetchone()[0]
    conn.execute(
        "INSERT INTO daily_list_entry(list_date, company_id, list_group, rank, state, proposed_at, decided_at, "
        "person_id, channel) VALUES (?, ?, 'level_role', ?, ?, ?, ?, ?, ?)",
        (list_date, company_id, rank, state, f"{list_date}T12:00:00Z",
         None if state == "proposed" else f"{list_date}T13:00:00Z", person_id, "email" if person_id else None))


def no_go(conn, company_id: int, *, lifted: bool = False) -> int:
    return s._insert(conn, "no_go", {
        "company_id": company_id, "reason": "not_a_fit", "source": "devanshu", "created_at": s.T0,
        "lifted_at": "2026-11-02T12:00:00Z" if lifted else None, "lift_reason": "Changed my mind" if lifted else None,
    })


def hold(conn, company_id: int) -> int:
    """Hold the company on a reply from one of its people (the sender reads `held_by_touch`)."""
    t = s.touch(conn, s.person(conn, company_id, "Holly Held"), "replied")
    conn.execute("UPDATE company SET held_by_touch = ?, held_at = ? WHERE company_id = ?", (t, s.T0, company_id))
    return t


class NeedsYou(ApiCase):
    def view(self) -> dict:
        res = self.get("/api/needs-you")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def open_items(self) -> dict[int, dict]:
        return {i["id"]: i for i in self.view()["open"]}

    def resolve(self, review_id: int, fix: dict, revision: int = 1, key: str | None = None):
        return self.post(f"/api/review/{review_id}/resolve", {"fix": fix, "revision": revision}, key=key)

    def unmerge(self, company_id: int, revision: int, key: str | None = None):
        return self.post(f"/api/companies/{company_id}/unmerge", {"revision": revision}, key=key)

    def review_row(self, review_id: int):
        return self.conn.execute("SELECT * FROM review_item WHERE review_id = ?", (review_id,)).fetchone()

    def company_row(self, company_id: int):
        return self.conn.execute("SELECT * FROM company WHERE company_id = ?", (company_id,)).fetchone()

    def count(self, sql: str, *params) -> int:
        return self.conn.execute(sql, params).fetchone()[0]

    def pair(self, a_name: str = "Bolt", b_name: str = "Bolt Labs", **cols):
        a = s.company(self.conn, a_name, "working")
        b = s.company(self.conn, b_name, **cols)
        item = s.review(self.conn, a, "merge_candidate", evidence_key=str(b),
                        reason=f"Maybe one company: {a_name} (#{a}) and {b_name} (#{b}).")
        return a, b, item

    def ok(self, res, status: int = 200) -> dict:
        self.assertEqual(res.status_code, status, res.text)
        return res.json()

    def refused(self, res, code: str) -> dict:
        body = res.json()
        self.assertEqual(body.get("code"), code, res.text)
        return body

    # ------------------------------------------------------------------------------------------ reads

    def test_empty_store_is_an_empty_view(self):
        self.assertEqual(self.view(), {"asOf": NOW, "open": [], "resolvedThisWeek": [], "boardsJob": None})

    def test_an_item_shows_a_hold_left_on_a_merged_duplicate(self):
        cid = s.company(self.conn, "Acme", "working")
        dup = s.company(self.conn, "Acme Labs", "removed", remove_reason="merged", merged_into=cid)
        t = hold(self.conn, dup)
        s.review(self.conn, cid, "size_unknown", reason="Size?", evidence_key="k")
        [item] = self.view()["open"]
        self.assertEqual(item["company"]["heldByTouchId"], t)

    def test_each_kind_lists_with_its_evidence(self):
        cid = s.company(self.conn, "Acme", "working")
        b = s.board(self.conn, cid, "ashby", "acme", last_read="error", error_streak=1)
        board_read(self.conn, b, "2026-11-01T08:00:00Z", "ok", postings=2)
        board_read(self.conn, b, "2026-11-05T08:00:00Z", "error", error="HTTP 503")
        open_p = s.posting(self.conn, cid, "Engineer", board_id=b)
        s.posting(self.conn, cid, "Old role", board_id=b, closed_at="2026-11-02T00:00:00Z")
        s.decision(self.conn, open_p, sponsorship="unclear", sponsorship_quote="We may sponsor for exceptional people.")
        read_p = s.posting(self.conn, cid, "Platform Engineer")
        s.decision(self.conn, read_p, citizenship=1, citizenship_quote="Must hold a clearance.")
        s.evidence(self.conn, cid, "size", "30", confidence="guess", source_kind="linkedin", low=30)
        other = s.company(self.conn, "Acme Labs")
        ids = {}
        for kind in KINDS:
            ids[kind] = s.review(
                self.conn, cid, kind, reason=f"About {kind}.", evidence_key=str(other) if kind == "merge_candidate" else "k",
                board_id=b if kind.startswith("board_") else None,
                posting_id=open_p if kind == "sponsorship_unclear" else read_p if kind == "reader_disagrees" else None,
                created_at="2026-11-05T08:00:00Z")
        items = {i["kind"]: i for i in self.view()["open"]}
        self.assertEqual(set(items), set(KINDS))
        for kind, item in items.items():
            self.assertEqual(item["id"], ids[kind])
            self.assertEqual(item["reason"], f"About {kind}.")
            self.assertEqual((item["ageDays"], item["openedAt"], item["revision"]), (5, "2026-11-05T08:00:00Z", 1))
            self.assertEqual(item["company"], {"id": cid, "name": "Acme", "status": "working", "pausedAt": None,
                                               "heldByTouchId": None, "picked": False})
            self.assertIsNone(item["reopened"])
        u = items["board_unreadable"]
        self.assertEqual([(r["result"], r["detail"]) for r in u["reads"]],
                         [("ok", "Ashby board read, 2 postings"), ("error", "HTTP 503")])
        self.assertEqual((u["lastOkAt"], u["openPostings"]), ("2026-11-01T08:00:00Z", 1))
        self.assertEqual(items["board_empty"]["openPostings"], 1)
        quote = items["sponsorship_unclear"]["quote"]
        self.assertEqual((quote["text"], quote["postingId"], quote["postingTitle"]),
                         ("We may sponsor for exceptional people.", open_p, "Engineer"))
        self.assertEqual(items["reader_disagrees"]["quote"]["text"], "Must hold a clearance.")
        for kind in ("size_unknown", "size_guess", "size_check"):
            self.assertEqual([f["value"] for f in items[kind]["sizeFacts"]], ["30"], kind)
        for kind in ("flagged", "merge_candidate", "careers_changed", "domain_missing"):
            item = items[kind]
            self.assertEqual((item["quote"], item["reads"], item["lastOkAt"], item["openPostings"], item["sizeFacts"]),
                             (None, [], None, None, []), kind)

    def test_reopened_item_names_the_change(self):
        cid = s.company(self.conn, "Acme")
        b = s.board(self.conn, cid, "ashby", "acme", last_read="error", error_streak=3)
        old = s.review(self.conn, cid, "board_unreadable", board_id=b, evidence_key="streak:2026-10-20",
                       created_at="2026-10-20T08:00:00Z")
        self.ok(self.resolve(old, {"kind": "ok"}))
        new = s.review(self.conn, cid, "board_unreadable", board_id=b, evidence_key="streak:2026-11-05",
                       created_at="2026-11-05T08:00:00Z")
        first_size = s.review(self.conn, cid, "size_unknown", evidence_key="no-size")
        item = self.open_items()[new]
        self.assertEqual(item["reopened"], {"changed": "the board failed again",
                                            "previous": "you marked it ok on Nov 10"})
        self.assertIsNone(self.open_items()[first_size]["reopened"])

    def test_in_play_companies_come_first_and_the_rest_are_later(self):
        later = s.company(self.conn, "Later Co")
        working = s.company(self.conn, "Working Co", "working")
        picked = s.company(self.conn, "Picked Co", "backlog")
        planned = s.company(self.conn, "Planned Co")
        stale = s.company(self.conn, "Stale Co")
        plan_entry(self.conn, picked, "picked")
        plan_entry(self.conn, planned, "proposed", person_id=s.person(self.conn, planned))
        plan_entry(self.conn, stale, "picked", list_date="2026-11-01")  # an older list
        blocked = s.company(self.conn, "No-go Co", "working")
        no_go(self.conn, blocked)
        made = {}
        for n, cid in enumerate((later, blocked, working, picked, planned, stale)):
            made[cid] = s.review(self.conn, cid, "domain_missing", created_at=f"2026-11-0{n + 1}T08:00:00Z")
        items = self.view()["open"]
        self.assertEqual([i["company"]["name"] for i in items],
                         ["Working Co", "Picked Co", "Planned Co", "Later Co", "No-go Co", "Stale Co"])
        self.assertEqual([i["inPlay"] for i in items], [True, True, True, False, False, False])
        by_company = {i["company"]["id"]: i for i in items}
        self.assertTrue(by_company[picked]["company"]["picked"])
        self.assertTrue(by_company[picked]["holdsEmail"])  # a picked company's missing domain holds its email
        self.assertFalse(by_company[working]["company"]["picked"])

    def test_resolved_this_week_lists_this_new_york_week(self):
        cid = s.company(self.conn, "Acme")
        this_week = s.review(self.conn, cid, "flagged", evidence_key="a")
        self.ok(self.resolve(this_week, {"kind": "ok"}))
        s._insert(self.conn, "review_item", {
            "kind": "flagged", "company_id": cid, "reason": "Old one.", "evidence_key": "b", "raised_by": "rules",
            "state": "resolved", "resolution": "marked_ok", "created_at": "2026-11-01T08:00:00Z",
            "resolved_at": "2026-11-09T04:00:00Z"})  # Sunday Nov 8 in New York: last week
        s._insert(self.conn, "review_item", {
            "kind": "flagged", "company_id": cid, "reason": "Went away.", "evidence_key": "c", "raised_by": "rules",
            "state": "resolved", "resolution": "cleared", "created_at": "2026-11-01T08:00:00Z",
            "resolved_at": "2026-11-09T16:00:00Z"})  # cleared by itself: not one of his
        self.assertEqual(self.view()["resolvedThisWeek"], [{
            "id": this_week, "kind": "flagged", "companyName": "Acme", "resolution": "marked_ok",
            "note": "Marked ok by you.", "at": NOW, "locked": False}])

    def test_boards_job_is_the_last_read_boards_run_else_the_crawl(self):
        run(self.conn, "crawl", "2026-11-09T08:00:00Z")
        self.assertEqual(self.view()["boardsJob"]["job"], "crawl")
        run(self.conn, "read_boards", "2026-11-08T08:00:00Z")
        run(self.conn, "read_boards", "2026-11-10T08:00:00Z", "partial")
        job = self.view()["boardsJob"]
        self.assertEqual((job["job"], job["startedAt"], job["status"]), ("read_boards", "2026-11-10T08:00:00Z", "partial"))

    def test_a_merged_duplicates_item_answers_as_the_survivor(self):
        keep = s.company(self.conn, "Bolt", "working")
        dup = s.company(self.conn, "Bolt Inc", "removed", remove_reason="merged", merged_into=keep)
        item = s.review(self.conn, dup, "domain_missing")
        same = s.review(self.conn, keep, "merge_candidate", evidence_key=str(dup))  # already one company
        items = self.open_items()
        self.assertEqual(items[item]["company"]["id"], keep)
        self.assertNotIn(same, items)

    # ------------------------------------------------------------------------------------------ ok, edit, no-go

    def test_mark_ok_resolves_and_stays_resolved_until_the_evidence_key_changes(self):
        cid = s.company(self.conn, "Acme")
        item = s.review(self.conn, cid, "size_guess", evidence_key="guess:linkedin")
        self.assertEqual(self.ok(self.resolve(item, {"kind": "ok"})), {"id": item, "revision": 2})
        row = self.review_row(item)
        self.assertEqual((row["state"], row["resolution"], row["resolved_at"], row["revision"]),
                         ("resolved", "marked_ok", NOW, 2))
        self.assertEqual(self.count("SELECT count(*) FROM event WHERE entity = 'review_item' AND entity_id = ? "
                                    "AND kind = 'review_resolved' AND actor = 'devanshu'", item), 1)
        raise_again = ("INSERT INTO review_item(kind, company_id, reason, evidence_key, raised_by, created_at) "
                       "VALUES ('size_guess', ?, 'Again.', ?, 'rules', ?) ON CONFLICT DO NOTHING")
        self.conn.execute(raise_again, (cid, "guess:linkedin", NOW))
        self.assertEqual(self.view()["open"], [])
        self.conn.execute(raise_again, (cid, "guess:builtin", NOW))
        (again,) = self.view()["open"]
        self.assertEqual(again["reopened"]["previous"], "you marked it ok on Nov 10")

    def test_edit_requires_a_source_and_writes_locked_hand_evidence_that_wins_the_fact(self):
        cid = s.company(self.conn, "Acme")
        s.evidence(self.conn, cid, "size", "11-50", confidence="confirmed", source_kind="linkedin", low=11, high=50)
        item = s.review(self.conn, cid, "size_unknown")
        for source in ("", "   "):
            res = self.resolve(item, {"kind": "edit", "field": "size", "value": "45", "source": source})
            self.assertEqual(res.status_code, 400, res.text)
            self.refused(res, "bad_request")
        self.assertEqual(self.count("SELECT count(*) FROM evidence WHERE locked = 1"), 0)
        self.assertEqual(self.review_row(item)["state"], "open")
        self.ok(self.resolve(item, {"kind": "edit", "field": "size", "value": "about 45", "source": "Their about page"}))
        ev = self.conn.execute("SELECT * FROM evidence WHERE locked = 1").fetchone()
        self.assertEqual((ev["company_id"], ev["fact"], ev["value"], ev["num_low"], ev["num_high"], ev["confidence"],
                          ev["source_kind"], ev["source_ref"], ev["recorded_at"]),
                         (cid, "size", "45", 45, 45, "estimate", "hand", "Their about page", NOW))
        # A later confirmed automated row never beats his locked one.
        s.evidence(self.conn, cid, "size", "200", confidence="confirmed", source_kind="website", low=200,
                   recorded_at="2026-11-11T08:00:00Z")
        fact = self.conn.execute("SELECT value FROM company_fact WHERE company_id = ? AND fact = 'size'", (cid,)).fetchone()
        self.assertEqual(fact["value"], "45")
        row = self.review_row(item)
        self.assertEqual((row["state"], row["resolution"]), ("resolved", "field_edited"))
        (done,) = self.view()["resolvedThisWeek"]
        self.assertEqual((done["note"], done["locked"]), ("Size set to 45 by you, from Their about page.", True))

    def test_edit_values_are_read_and_a_bad_one_is_refused(self):
        cid = s.company(self.conn, "Acme")
        cases = [
            ("size", "size_guess", "11 to 50", ("11-50", 11, 50, "estimate")),
            ("size", "size_check", "1,000", ("1000", 1000, 1000, "confirmed")),
            ("size", "size_unknown", "50+", ("50+", 50, None, "estimate")),
            ("domain", "domain_missing", "https://www.Acme.io/careers", ("acme.io", None, None, "confirmed")),
            ("sponsorship", "sponsorship_unclear", "Yes", ("yes", None, None, "confirmed")),
        ]
        for n, (field, kind, value, want) in enumerate(cases):
            with self.subTest(value=value):
                posting = s.posting(self.conn, cid) if kind == "sponsorship_unclear" else None
                item = s.review(self.conn, cid, kind, evidence_key=f"case{n}", posting_id=posting)
                self.ok(self.resolve(item, {"kind": "edit", "field": field, "value": value, "source": "LinkedIn"}))
                ev = self.conn.execute("SELECT value, num_low, num_high, confidence FROM evidence WHERE locked = 1 "
                                       "ORDER BY evidence_id DESC LIMIT 1").fetchone()
                self.assertEqual(tuple(ev), want)
        bad = [("size", "size_unknown", "lots"), ("size", "size_unknown", "0"), ("domain", "domain_missing", "gmail.com"),
               ("domain", "domain_missing", "not a domain"), ("sponsorship", "sponsorship_unclear", "No"),
               ("domain", "size_unknown", "acme.io"), ("size", "flagged", "45")]
        for n, (field, kind, value) in enumerate(bad):
            with self.subTest(bad=value, kind=kind):
                posting = s.posting(self.conn, cid) if kind == "sponsorship_unclear" else None
                item = s.review(self.conn, cid, kind, evidence_key=f"bad{n}", posting_id=posting)
                res = self.resolve(item, {"kind": "edit", "field": field, "value": value, "source": "LinkedIn"})
                self.assertIn(res.status_code, (400, 409), res.text)
                self.assertTrue(res.json()["message"])
                self.assertEqual(self.review_row(item)["state"], "open")
        self.assertEqual(self.count("SELECT count(*) FROM evidence WHERE locked = 1"), len(cases))

    def test_board_edit_replaces_the_board_the_item_is_about(self):
        cid = s.company(self.conn, "Acme")
        old = s.board(self.conn, cid, "ashby", "acmeold", last_read="error", error_streak=3)
        stale = s.posting(self.conn, cid, "Engineer", board_id=old)
        item = s.review(self.conn, cid, "board_unreadable", board_id=old)
        same = self.resolve(item, {"kind": "edit", "field": "board_url", "value": "https://jobs.ashbyhq.com/acmeold",
                                   "source": "Their site"})
        self.refused(same, "bad_request")
        self.refused(self.resolve(item, {"kind": "edit", "field": "board_url", "value": "https://acme.example/jobs",
                                         "source": "Their site"}), "bad_request")
        other = s.company(self.conn, "Other")
        s.board(self.conn, other, "greenhouse", "taken")
        self.refused(self.resolve(item, {"kind": "edit", "field": "board_url",
                                         "value": "https://boards.greenhouse.io/taken", "source": "Their site"}), "conflict")
        self.assertEqual(self.count("SELECT count(*) FROM review_item WHERE kind = 'merge_candidate'"), 0)
        self.ok(self.resolve(item, {"kind": "edit", "field": "board_url", "value": "https://jobs.lever.co/acme",
                                    "source": "Their site"}))
        boards = {(r["ats"], r["token"]): r for r in self.conn.execute("SELECT * FROM board WHERE company_id = ?", (cid,))}
        self.assertEqual((boards[("lever", "acme")]["found_by"], boards[("lever", "acme")]["active"]), ("hand", 1))
        self.assertEqual(boards[("ashby", "acmeold")]["active"], 0)
        p = self.conn.execute("SELECT closed_at, close_reason FROM posting WHERE posting_id = ?", (stale,)).fetchone()
        self.assertEqual(tuple(p), (NOW, "hand"))
        self.assertEqual(self.review_row(item)["resolution"], "field_edited")

    def test_no_go_fix_writes_the_no_go_row(self):
        cid = s.company(self.conn, "Acme", "working")
        item = s.review(self.conn, cid, "sponsorship_unclear", posting_id=s.posting(self.conn, cid))
        self.refused(self.resolve(item, {"kind": "no_go", "reason": "no_sponsorship", "source": " "}), "bad_request")
        self.ok(self.resolve(item, {"kind": "no_go", "reason": "no_sponsorship", "source": "The posting"}))
        row = self.conn.execute("SELECT * FROM no_go").fetchone()
        self.assertEqual((row["company_id"], row["person_id"], row["reason"], row["source"], row["source_ref"],
                          row["created_at"], row["lifted_at"]),
                         (cid, None, "no_sponsorship", "devanshu", "The posting", NOW, None))
        self.assertEqual(self.review_row(item)["resolution"], "marked_no_go")
        self.assertIn(cid, no_go_sets(self.conn, company_map(self.conn))[1])

    def test_no_go_on_a_company_already_on_the_list_keeps_the_one_row(self):
        cid = s.company(self.conn, "Acme")
        no_go(self.conn, cid)
        item = s.review(self.conn, cid, "flagged")
        self.ok(self.resolve(item, {"kind": "no_go", "reason": "agency", "source": "Their site"}))
        self.assertEqual(self.count("SELECT count(*) FROM no_go"), 1)
        self.assertEqual(self.review_row(item)["resolution"], "marked_no_go")

    def test_resolve_stale_revision_conflicts(self):
        cid = s.company(self.conn, "Acme")
        item = s.review(self.conn, cid, "size_unknown")
        self.conn.execute("UPDATE review_item SET revision = 2 WHERE review_id = ?", (item,))
        res = self.resolve(item, {"kind": "edit", "field": "size", "value": "45", "source": "LinkedIn"}, revision=1)
        self.assertEqual(res.status_code, 409)
        self.refused(res, "conflict")
        self.assertEqual(self.count("SELECT count(*) FROM evidence"), 0)
        self.assertEqual(self.review_row(item)["state"], "open")

    def test_resolve_an_already_resolved_item_is_not_allowed(self):
        cid = s.company(self.conn, "Acme")
        item = s.review(self.conn, cid, "flagged")
        self.ok(self.resolve(item, {"kind": "ok"}))
        res = self.resolve(item, {"kind": "no_go", "reason": "agency", "source": "x"}, revision=2)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(self.refused(res, "not_allowed")["message"], "Already resolved.")
        self.assertEqual(self.count("SELECT count(*) FROM no_go"), 0)

    def test_unknown_item_is_not_found_and_a_bad_body_is_refused(self):
        self.refused(self.resolve(99, {"kind": "ok"}), "not_found")
        cid = s.company(self.conn, "Acme")
        item = s.review(self.conn, cid, "flagged")
        self.refused(self.resolve(item, {"kind": "delete"}), "bad_request")
        self.refused(self.post(f"/api/review/{item}/resolve", {"fix": {"kind": "ok"}}), "bad_request")  # no revision

    # ------------------------------------------------------------------------------------------ merge

    def test_merge_sets_merged_into_and_alias_and_company_reads_follow_it(self):
        a, b, item = self.pair()
        pat = s.person(self.conn, b, "Pat Dup")
        self.ok(self.resolve(item, {"kind": "merge", "keep": "a"}))
        lost = self.company_row(b)
        self.assertEqual((lost["merged_into"], lost["status"], lost["remove_reason"], lost["revision"]),
                         (a, "removed", "merged", 2))
        alias = self.conn.execute("SELECT company_id, alias, source FROM company_alias WHERE alias_key = 'boltlabs'").fetchone()
        self.assertEqual(tuple(alias), (a, "Bolt Labs", "merge"))
        row = self.review_row(item)
        self.assertEqual((row["state"], row["resolution"], row["resolution_note"]),
                         ("resolved", "field_edited", f"merged into {a}"))
        (event,) = self.conn.execute("SELECT * FROM event WHERE entity = 'company' AND kind = 'merged'").fetchall()
        detail = json.loads(event["detail"])
        self.assertEqual((event["entity_id"], event["actor"], detail["previous_status"], detail["kept_id"]),
                         (b, "devanshu", "candidate", a))
        page = self.ok(self.get(f"/api/companies/{b}"))
        self.assertEqual(page["company"]["id"], a)
        self.assertIn("Bolt Labs", page["company"]["aliases"])
        self.assertIn("Pat Dup", [p["person"]["name"] for p in page["people"]])
        self.assertEqual(self.count("SELECT company_id FROM person WHERE person_id = ?", pat), b)  # no row moved
        done = self.view()["resolvedThisWeek"][0]
        self.assertEqual((done["resolution"], done["locked"]), ("field_edited", False))

    def test_merge_keep_b_merges_a_into_b(self):
        a, b, item = self.pair()
        self.ok(self.resolve(item, {"kind": "merge", "keep": "b"}))
        self.assertEqual(self.company_row(a)["merged_into"], b)
        detail = json.loads(self.conn.execute("SELECT detail FROM event WHERE kind = 'merged'").fetchone()[0])
        self.assertEqual(detail["previous_status"], "working")

    def test_keep_separate_does_not_merge(self):
        a, b, item = self.pair()
        self.ok(self.resolve(item, {"kind": "ok"}))
        self.assertIsNone(self.company_row(b)["merged_into"])
        self.assertEqual(self.company_row(b)["status"], "candidate")
        self.assertEqual(self.review_row(item)["resolution"], "marked_ok")
        self.assertEqual(self.review_row(item)["resolution_note"], "Kept separate by you.")
        self.assertEqual(self.count("SELECT count(*) FROM company_alias"), 0)

    def test_merge_is_only_for_a_merge_question_and_a_merge_question_has_no_no_go_or_edit(self):
        cid = s.company(self.conn, "Acme")
        flagged = s.review(self.conn, cid, "flagged")
        self.refused(self.resolve(flagged, {"kind": "merge", "keep": "a"}), "not_allowed")
        a, b, item = self.pair()
        self.refused(self.resolve(item, {"kind": "no_go", "reason": "agency", "source": "x"}), "not_allowed")
        self.refused(self.resolve(item, {"kind": "edit", "field": "domain", "value": "bolt.io", "source": "x"}),
                     "not_allowed")
        self.assertEqual(self.review_row(item)["state"], "open")

    def test_merge_into_a_merged_company_conflicts(self):
        a, b, item = self.pair()
        elsewhere = s.company(self.conn, "Elsewhere")
        self.conn.execute("UPDATE company SET status = 'removed', remove_reason = 'merged', merged_into = ? "
                          "WHERE company_id = ?", (elsewhere, a))
        res = self.resolve(item, {"kind": "merge", "keep": "a"})
        self.assertEqual(res.status_code, 409)
        self.refused(res, "conflict")
        self.assertIsNone(self.company_row(b)["merged_into"])
        self.assertEqual(self.review_row(item)["state"], "open")
        self.refused(self.resolve(item, {"kind": "merge", "keep": "b"}), "conflict")  # the loser is merged too

    def test_merge_that_would_loop_conflicts(self):
        a, b, item = self.pair()
        self.conn.execute("UPDATE company SET status = 'removed', remove_reason = 'merged', merged_into = ? "
                          "WHERE company_id = ?", (a, b))
        self.refused(self.resolve(item, {"kind": "merge", "keep": "b"}), "conflict")  # a into b while b is in a
        self.assertIsNone(self.company_row(a)["merged_into"])

    def test_a_double_click_on_merge_merges_once(self):
        a, b, item = self.pair()
        first = self.resolve(item, {"kind": "merge", "keep": "a"}, key="merge-click-1")
        again = self.resolve(item, {"kind": "merge", "keep": "a"}, key="merge-click-1")
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.headers.get("Idempotent-Replay"), "true")
        self.assertEqual(again.json(), first.json())
        self.refused(self.resolve(item, {"kind": "merge", "keep": "a"}, revision=2, key="merge-click-2"), "not_allowed")
        self.assertEqual(self.count("SELECT count(*) FROM event WHERE kind = 'merged'"), 1)
        self.assertEqual(self.company_row(b)["revision"], 2)

    def test_merge_keeps_a_held_duplicates_hold_on_the_survivor(self):
        a, b, item = self.pair()
        touch = hold(self.conn, b)
        self.ok(self.resolve(item, {"kind": "merge", "keep": "a"}))
        self.assertEqual(self.company_row(b)["held_by_touch"], touch)
        self.assertIn(a, held_survivors(company_map(self.conn)))

    def test_merge_keeps_an_active_no_go_on_either_side_and_writes_none(self):
        for keep in ("a", "b"):
            with self.subTest(keep=keep):
                a, b, item = self.pair(f"Keep {keep}", f"Lose {keep}")
                barred = b if keep == "a" else a  # the no-go sits on the company that goes away
                no_go(self.conn, barred)
                before = self.count("SELECT count(*) FROM no_go")
                self.ok(self.resolve(item, {"kind": "merge", "keep": keep}))
                survivor = a if keep == "a" else b
                self.assertIn(survivor, no_go_sets(self.conn, company_map(self.conn))[1])
                self.assertEqual(self.count("SELECT count(*) FROM no_go"), before)

    def test_merge_never_brings_back_a_no_go_he_lifted(self):
        a, b, item = self.pair()
        lifted = no_go(self.conn, b, lifted=True)
        self.ok(self.resolve(item, {"kind": "merge", "keep": "a"}))
        self.assertNotIn(a, no_go_sets(self.conn, company_map(self.conn))[1])
        self.assertEqual(self.count("SELECT count(*) FROM no_go"), 1)
        self.assertIsNotNone(self.conn.execute("SELECT lifted_at FROM no_go WHERE no_go_id = ?", (lifted,)).fetchone()[0])

    def test_merge_moves_no_person_touch_or_message_row(self):
        a, b, item = self.pair()
        t = s.touch(self.conn, s.person(self.conn, b, "Pat Dup"))
        s.message(self.conn, t, 0)
        before = {table: self.conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                  for table in ("person", "touch", "message", "outreach_touch", "no_go")}
        self.ok(self.resolve(item, {"kind": "merge", "keep": "a"}))
        for table, rows in before.items():
            self.assertEqual([tuple(r) for r in self.conn.execute(f"SELECT * FROM {table} ORDER BY 1")],
                             [tuple(r) for r in rows], table)

    # ------------------------------------------------------------------------------------------ unmerge

    def test_unmerge_restores_status_and_keeps_the_alias_with_the_survivor(self):
        a, b, item = self.pair(status="backlog")
        self.ok(self.resolve(item, {"kind": "merge", "keep": "a"}))
        self.assertEqual(self.ok(self.unmerge(b, 2)), {"revision": 3})
        row = self.company_row(b)
        self.assertEqual((row["merged_into"], row["status"], row["remove_reason"], row["revision"]),
                         (None, "backlog", None, 3))
        alias = self.conn.execute("SELECT company_id FROM company_alias WHERE alias_key = 'boltlabs'").fetchone()
        self.assertEqual(alias[0], a)
        self.assertEqual(self.count("SELECT count(*) FROM event WHERE entity = 'company' AND kind = 'unmerged' "
                                    "AND entity_id = ? AND actor = 'devanshu'", b), 1)
        self.assertEqual(self.review_row(item)["state"], "resolved")  # nothing else is moved back
        self.assertEqual(self.ok(self.get(f"/api/companies/{b}"))["company"]["id"], b)

    def test_unmerge_restores_a_paused_or_a_closed_company_exactly(self):
        for cols in ({"status": "working", "paused_at": "2026-11-02T12:00:00Z", "pause_note": "Wait for Q1"},
                     {"status": "closed", "close_reason": "not_now", "status_note": "Later"},
                     {"status": "removed", "remove_reason": "not_a_fit"}):
            with self.subTest(status=cols["status"]):
                a, b, item = self.pair(f"Keep {cols['status']}", f"Lose {cols['status']}", **cols)
                # A stopped company merges only into one that stops mail too.
                self.conn.execute("UPDATE company SET status = 'closed', close_reason = 'other' WHERE company_id = ?",
                                  (a,))
                before = dict(self.company_row(b))
                self.ok(self.resolve(item, {"kind": "merge", "keep": "a"}))
                self.ok(self.unmerge(b, before["revision"] + 1))
                after = dict(self.company_row(b))
                for key in ("revision", "status_at"):
                    before.pop(key), after.pop(key)
                self.assertEqual(after, before)

    def test_unmerge_gives_each_company_back_only_its_own_hold_and_no_go(self):
        a, b, item = self.pair()
        hold(self.conn, b)
        no_go(self.conn, b)
        self.ok(self.resolve(item, {"kind": "merge", "keep": "a"}))
        self.ok(self.unmerge(b, 2))
        companies = company_map(self.conn)
        self.assertEqual(held_survivors(companies), {b})
        self.assertEqual(no_go_sets(self.conn, companies)[1], {b})

    def test_unmerge_of_an_unmerged_company_is_not_allowed(self):
        cid = s.company(self.conn, "Acme")
        res = self.unmerge(cid, 1)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(self.refused(res, "not_allowed")["message"], "Not merged.")
        self.refused(self.unmerge(999, 1), "not_found")

    def test_unmerge_stale_revision_conflicts(self):
        a, b, item = self.pair()
        self.ok(self.resolve(item, {"kind": "merge", "keep": "a"}))
        self.refused(self.unmerge(b, 1), "conflict")
        self.assertEqual(self.company_row(b)["merged_into"], a)

    # ------------------------------------------------------------------------------------------ review fixes

    def verdict_of(self, message_id: int) -> str:
        cfg = load_config()
        day = ramp.open_day(self.conn, MON, cfg)
        return {v.message_id: v.reason for v in queue.verdicts(self.conn, MON, cfg, day=day, gate=None)}[message_id]

    def test_merging_away_a_paused_company_is_refused_so_its_mail_stays_paused(self):
        a, b, item = self.pair(status="working", paused_at="2026-11-02T12:00:00Z")
        message = seed_message(self.conn, seed_email_person(self.conn, b, "Pat Paused", "pat@boltlabs.example"))
        self.assertEqual(self.verdict_of(message), "company_paused")
        res = self.resolve(item, {"kind": "merge", "keep": "a"})
        self.assertEqual(self.refused(res, "not_allowed")["message"],
                         "This one is paused. Keep the paused company, or unpause it first.")
        self.assertIsNone(self.company_row(b)["merged_into"])
        self.assertEqual(self.verdict_of(message), "company_paused")
        self.ok(self.resolve(item, {"kind": "merge", "keep": "b"}))  # keeping the paused one is fine
        self.assertEqual(self.verdict_of(message), "company_paused")

    def test_merging_away_a_closed_or_removed_company_into_a_live_one_is_refused(self):
        for status, cols, words in (
                ("closed", {"close_reason": "not_now"}, "This one is closed. Keep the closed company, or reopen it first."),
                ("removed", {"remove_reason": "not_a_fit"},
                 "This one is removed. Keep the removed company, or restore it first.")):
            with self.subTest(status=status):
                a, b, item = self.pair(f"Keep {status}", f"Lose {status}", status=status, **cols)
                message = seed_message(self.conn, seed_email_person(self.conn, b, "Pat", f"pat@{status}.example"))
                self.assertEqual(self.verdict_of(message), "company_closed")
                self.assertEqual(self.refused(self.resolve(item, {"kind": "merge", "keep": "a"}), "not_allowed")["message"],
                                 words)
                self.assertEqual(self.verdict_of(message), "company_closed")

    def test_a_fix_on_a_merged_away_companys_item_stays_with_that_company_after_an_unmerge(self):
        a, b, item = self.pair()
        lost_item = s.review(self.conn, b, "flagged")
        domain_item = s.review(self.conn, b, "domain_missing")
        self.ok(self.resolve(item, {"kind": "merge", "keep": "a"}))
        self.ok(self.resolve(lost_item, {"kind": "no_go", "reason": "agency", "source": "Their site"}))
        self.ok(self.resolve(domain_item, {"kind": "edit", "field": "domain", "value": "boltlabs.io", "source": "Site"}))
        self.assertIn(a, no_go_sets(self.conn, company_map(self.conn))[1])  # while merged, it stops the survivor
        self.assertEqual(self.ok(self.get(f"/api/companies/{a}"))["company"]["domain"], "boltlabs.io")
        self.ok(self.unmerge(b, 2))
        survivors = no_go_sets(self.conn, company_map(self.conn))[1]
        self.assertIn(b, survivors)
        self.assertNotIn(a, survivors)
        owners = {r[0] for r in self.conn.execute("SELECT company_id FROM evidence WHERE locked = 1")}
        self.assertEqual(owners, {b})

    def test_a_no_go_fix_on_an_item_about_a_person_is_a_person_no_go(self):
        cid = s.company(self.conn, "Acme")
        person = s.person(self.conn, cid, "Pat Lee")
        item = s._insert(self.conn, "review_item", {
            "kind": "flagged", "company_id": cid, "person_id": person, "reason": "Pat may be a recruiter.",
            "evidence_key": "k", "raised_by": "claude", "created_at": "2026-11-08T12:00:00Z"})
        self.ok(self.resolve(item, {"kind": "no_go", "reason": "not_a_fit", "source": "Their profile"}))
        row = self.conn.execute("SELECT company_id, person_id, source, source_ref FROM no_go").fetchone()
        self.assertEqual(tuple(row), (None, person, "devanshu", "Their profile"))

    def test_a_merge_question_with_a_side_merged_into_a_third_company_is_refused_plainly(self):
        a, b, item = self.pair()
        third = s.company(self.conn, "Third")
        self.conn.execute("UPDATE company SET status = 'removed', remove_reason = 'merged', merged_into = ? "
                          "WHERE company_id = ?", (third, b))
        for keep in ("a", "b"):
            self.assertEqual(self.refused(self.resolve(item, {"kind": "merge", "keep": keep}), "conflict")["message"],
                             "One of these was already merged into another company. Keep them separate here, then "
                             "merge from that company's page.")

    def test_a_board_edit_on_a_board_of_another_company_is_a_conflict_and_closes_nothing(self):
        cid = s.company(self.conn, "Acme")
        other = s.company(self.conn, "Other")
        theirs = s.board(self.conn, other, "ashby", "otherco")
        posting = s.posting(self.conn, other, board_id=theirs)
        item = s.review(self.conn, cid, "board_unreadable", board_id=theirs)  # a row that names the wrong board
        self.refused(self.resolve(item, {"kind": "edit", "field": "board_url", "value": "https://jobs.lever.co/acme",
                                         "source": "Their site"}), "conflict")
        self.assertIsNone(self.conn.execute("SELECT closed_at FROM posting WHERE posting_id = ?", (posting,)).fetchone()[0])
        self.assertEqual(self.conn.execute("SELECT active FROM board WHERE board_id = ?", (theirs,)).fetchone()[0], 1)
        self.assertEqual(self.count("SELECT count(*) FROM board WHERE token = 'acme'"), 0)

    def test_a_board_conflict_from_add_board_retires_nothing(self):
        from unittest import mock
        cid = s.company(self.conn, "Acme")
        old = s.board(self.conn, cid, "ashby", "acmeold")
        posting = s.posting(self.conn, cid, board_id=old)
        item = s.review(self.conn, cid, "board_unreadable", board_id=old)
        with mock.patch("outreach.api.queries.needs_you.add_board", return_value="conflict"):
            res = self.resolve(item, {"kind": "edit", "field": "board_url", "value": "https://jobs.lever.co/acme",
                                      "source": "Their site"})
        self.refused(res, "conflict")
        self.assertEqual(self.conn.execute("SELECT active FROM board WHERE board_id = ?", (old,)).fetchone()[0], 1)
        self.assertIsNone(self.conn.execute("SELECT closed_at FROM posting WHERE posting_id = ?", (posting,)).fetchone()[0])
