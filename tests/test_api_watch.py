"""The watch list: changed careers pages, new companies with a reason and no role, and possible duplicates."""
import json

from outreach.api.queries import watch as watch_queries
from tests import apiseed as s
from tests.apitest import NOW, ApiCase

CAREERS, NEW, MERGE = 0, 1, 2
RECENT = "2026-11-05T09:00:00Z"  # inside the 14 days before NOW
OLD = "2026-10-20T09:00:00Z"  # more than 14 days before NOW
CONTEXT = json.dumps({"source": "plan", "lane": "reason_no_role", "roleKind": "engineer", "roleAgeDays": None,
                      "rankerVersion": "r1", "signals": {}, "roleNamed": False})


def score(conn, company_id: int, value: float = 1.0, lanes=("reason_no_role",), *, version: str = "r1",
          scored_at: str = "2026-11-09T06:00:00Z", hide=None) -> None:
    """A company_score row in the stored shape: `signals` carries the hide, `lanes` is a JSON list."""
    signals = {"values": {}, "reasons": [], "hide": hide, "flags": [], "best_posting_id": None}
    conn.execute(
        "INSERT INTO company_score(company_id, ranker_version, score, signals, lanes, scored_at) VALUES (?,?,?,?,?,?)",
        (company_id, version, value, json.dumps(signals), json.dumps(list(lanes)), scored_at),
    )


def watched_page(conn, company_id: int, url: str = "https://acme.example/careers") -> None:
    conn.execute("INSERT INTO watched_page(company_id, url) VALUES (?, ?)", (company_id, url))


def application(conn, company_id: int, undone_at: str | None = None) -> int:
    return s._insert(conn, "outreach_touch", {
        "person_id": None, "company_id": company_id, "channel": "application", "sent_at": s.T0, "context": CONTEXT,
        "created_at": s.T0, "undone_at": undone_at,
    })


class Watch(ApiCase):
    def items(self) -> list[dict]:
        res = self.get("/api/watch")
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["asOf"], NOW)
        return body["items"]

    def new_company(self, name: str = "Newco", *, created_at: str = RECENT, points: float = 1.0, **cols) -> int:
        cid = s.company(self.conn, name, "candidate", created_at=created_at, **cols)
        score(self.conn, cid, points)
        return cid

    def dismiss(self, item_id: int, key: str | None = None):
        return self.post(f"/api/watch/{item_id}/dismiss", {}, key=key)

    def open_reviews(self) -> int:
        return self.conn.execute("SELECT count(*) FROM review_item WHERE state = 'open'").fetchone()[0]

    # ------------------------------------------------------------------------------------------ reads

    def test_empty_store_is_an_empty_list(self):
        self.assertEqual(self.get("/api/watch").json(), {"asOf": NOW, "items": []})

    def test_each_kind_appears_with_its_reason(self):
        a = s.company(self.conn, "Acme", "working")
        watched_page(self.conn, a, "https://acme.example/jobs")
        careers = s.review(self.conn, a, "careers_changed", reason="The careers page changed since Nov 3.",
                           created_at="2026-11-08T12:00:00Z")
        b = s.company(self.conn, "Bolt")
        c = s.company(self.conn, "Bolt Labs")
        merge = s.review(self.conn, b, "merge_candidate", evidence_key=str(c), created_at="2026-11-07T12:00:00Z")
        fresh = self.new_company("Newco")
        items = {i["kind"]: i for i in self.items()}
        self.assertEqual(set(items), {"careers_changed", "new_reason_no_role", "merge_candidate"})
        self.assertEqual(items["careers_changed"], {
            "id": careers * 4 + CAREERS, "kind": "careers_changed",
            "company": {"id": a, "name": "Acme", "status": "working", "pausedAt": None, "heldByTouchId": None},
            "text": "The careers page changed since Nov 3.", "at": "2026-11-08T12:00:00Z",
            "link": {"label": "Careers page", "url": "https://acme.example/jobs"}, "reviewId": careers,
        })
        self.assertEqual(items["merge_candidate"]["id"], merge * 4 + MERGE)
        self.assertEqual(items["merge_candidate"]["reviewId"], merge)
        self.assertIsNone(items["merge_candidate"]["link"])
        new = items["new_reason_no_role"]
        self.assertEqual((new["id"], new["company"]["name"], new["at"], new["link"], new["reviewId"]),
                         (fresh * 4 + NEW, "Newco", RECENT, None, None))
        self.assertIn("reason", new["text"].lower())

    def test_items_are_newest_first(self):
        a = s.company(self.conn, "Acme")
        watched_page(self.conn, a)
        s.review(self.conn, a, "careers_changed", created_at="2026-11-08T12:00:00Z")
        b = s.company(self.conn, "Bolt")
        c = s.company(self.conn, "Bolt Labs")
        s.review(self.conn, b, "merge_candidate", evidence_key=str(c), created_at="2026-11-09T12:00:00Z")
        self.new_company("Newco", created_at="2026-11-06T00:00:00Z")
        self.assertEqual([i["kind"] for i in self.items()], ["merge_candidate", "careers_changed", "new_reason_no_role"])

    def test_a_careers_item_without_a_stored_page_has_no_link(self):
        a = s.company(self.conn, "Acme")
        s.review(self.conn, a, "careers_changed")
        self.assertIsNone(self.items()[0]["link"])

    def test_resolved_review_items_are_not_listed(self):
        a = s.company(self.conn, "Acme")
        s.review(self.conn, a, "careers_changed", state="resolved")
        b = s.company(self.conn, "Bolt")
        c = s.company(self.conn, "Bolt Labs")
        s.review(self.conn, b, "merge_candidate", evidence_key=str(c), state="resolved")
        self.assertEqual(self.items(), [])

    def test_other_review_kinds_are_not_listed(self):
        a = s.company(self.conn, "Acme")
        s.review(self.conn, a, "size_unknown")
        self.assertEqual(self.items(), [])

    def test_merge_item_names_both_records(self):
        b = s.company(self.conn, "Bolt")
        c = s.company(self.conn, "Bolt Labs, Inc.")
        s.review(self.conn, b, "merge_candidate", evidence_key=str(c))
        item = self.items()[0]
        self.assertIn("Bolt", item["text"])
        self.assertIn("Bolt Labs, Inc.", item["text"])
        self.assertEqual(item["company"]["id"], b)

    def test_a_merge_item_whose_records_already_merged_is_not_listed(self):
        b = s.company(self.conn, "Bolt")
        c = s.company(self.conn, "Bolt Labs", "removed", remove_reason="merged", merged_into=b)
        s.review(self.conn, b, "merge_candidate", evidence_key=str(c))
        self.assertEqual(self.items(), [])

    def test_a_merge_item_with_an_unreadable_key_is_skipped_not_a_500(self):
        b = s.company(self.conn, "Bolt")
        s.review(self.conn, b, "merge_candidate", evidence_key="not-a-number", created_at="2026-11-08T12:00:00Z")
        s.review(self.conn, b, "merge_candidate", evidence_key="99999", created_at="2026-11-08T12:00:01Z")
        self.assertEqual(self.items(), [])

    def test_a_merge_item_follows_a_merged_record_to_its_survivor(self):
        a = s.company(self.conn, "Acme")
        a2 = s.company(self.conn, "Acme Labs", "removed", remove_reason="merged", merged_into=a)
        z = s.company(self.conn, "Zed")
        s.review(self.conn, a2, "merge_candidate", evidence_key=str(z))
        item = self.items()[0]
        self.assertEqual(item["company"]["id"], a)
        self.assertIn("Acme", item["text"])
        self.assertIn("Zed", item["text"])

    def test_a_careers_item_on_a_merged_company_answers_as_the_survivor(self):
        a = s.company(self.conn, "Acme")
        a2 = s.company(self.conn, "Acme Labs", "removed", remove_reason="merged", merged_into=a)
        watched_page(self.conn, a2)
        s.review(self.conn, a2, "careers_changed")
        item = self.items()[0]
        self.assertEqual((item["company"]["id"], item["company"]["name"]), (a, "Acme"))
        self.assertEqual(item["link"]["url"], "https://acme.example/careers")

    def test_company_with_a_person_is_not_new(self):
        cid = self.new_company("Newco")
        s.person(self.conn, cid)
        self.assertEqual(self.items(), [])

    def test_company_with_a_touch_is_not_new_but_an_undone_one_does_not_count(self):
        touched = self.new_company("Touched")
        application(self.conn, touched)
        undone = self.new_company("Undone")
        application(self.conn, undone, undone_at="2026-11-02T00:00:00Z")
        self.assertEqual([i["company"]["name"] for i in self.items()], ["Undone"])

    def test_a_person_on_a_merged_duplicate_makes_the_survivor_not_new(self):
        a = self.new_company("Acme")
        dup = s.company(self.conn, "Acme Labs", "removed", remove_reason="merged", merged_into=a)
        s.person(self.conn, dup)
        self.assertEqual(self.items(), [])

    def test_old_company_is_not_new(self):
        self.new_company("Oldco", created_at=OLD)
        self.new_company("Edge", created_at="2026-10-27T15:14:00Z")  # exactly 14 days: still new
        self.assertEqual([i["company"]["name"] for i in self.items()], ["Edge"])

    def test_only_the_newest_score_decides_the_lane(self):
        lost = s.company(self.conn, "Lost", created_at=RECENT)
        score(self.conn, lost, 5, ("reason_no_role",), version="r1", scored_at="2026-11-01T00:00:00Z")
        score(self.conn, lost, 5, ("level_role",), version="r2", scored_at="2026-11-09T00:00:00Z")
        gained = s.company(self.conn, "Gained", created_at=RECENT)
        score(self.conn, gained, 1, ("level_role",), version="r1", scored_at="2026-11-01T00:00:00Z")
        score(self.conn, gained, 1, ("reason_no_role", "any_us_role"), version="r2", scored_at="2026-11-09T00:00:00Z")
        self.assertEqual([i["company"]["name"] for i in self.items()], ["Gained"])

    def test_a_company_without_a_score_or_with_another_lane_is_not_listed(self):
        s.company(self.conn, "Unscored", created_at=RECENT)
        other = s.company(self.conn, "Elsewhere", created_at=RECENT)
        score(self.conn, other, 9, ("any_us_role",))
        none = s.company(self.conn, "Nothing", created_at=RECENT)
        score(self.conn, none, 9, ())
        self.assertEqual(self.items(), [])

    def test_a_hidden_company_is_not_listed(self):
        cid = s.company(self.conn, "Hidden", created_at=RECENT)
        score(self.conn, cid, 9, hide={"kind": "no_sponsorship", "reason": "Says no.", "provisional": False})
        self.assertEqual(self.items(), [])

    def test_closed_removed_and_no_go_companies_are_not_listed(self):
        closed = s.company(self.conn, "Closed", "closed", created_at=RECENT)
        score(self.conn, closed)
        removed = s.company(self.conn, "Removed", "removed", created_at=RECENT)
        score(self.conn, removed)
        blocked = self.new_company("Blocked")
        self.conn.execute(
            "INSERT INTO no_go(company_id, reason, source, created_at) VALUES (?, 'not_a_fit', 'devanshu', ?)",
            (blocked, s.T0))
        self.assertEqual(self.items(), [])

    def test_a_merged_away_duplicate_is_never_listed_itself(self):
        a = s.company(self.conn, "Acme")
        dup = s.company(self.conn, "Acme Labs", "removed", remove_reason="merged", merged_into=a, created_at=RECENT)
        score(self.conn, dup)
        self.assertEqual(self.items(), [])

    def test_new_companies_are_the_top_twenty_by_score(self):
        ids = [self.new_company(f"Co{n:02d}", points=float(n)) for n in range(25)]
        names = [i["company"]["name"] for i in self.items()]
        self.assertEqual(len(names), 20)
        self.assertEqual(set(names), {f"Co{n:02d}" for n in range(5, 25)})
        self.assertNotIn(ids[0], [i["company"]["id"] for i in self.items()])

    def test_the_score_read_is_keyed_by_company(self):
        plan = " ".join(r[3] for r in self.conn.execute(
            "EXPLAIN QUERY PLAN " + watch_queries.NEW_COMPANIES_SQL, ("2026-10-27T15:14:00Z",)))
        self.assertIn("SEARCH s USING", plan)
        self.assertNotIn("SCAN s", plan)
        self.assertNotIn("SCAN company_score", plan)

    # ------------------------------------------------------------------------------------------ dismiss

    def test_dismissed_new_company_stays_gone(self):
        cid = self.new_company("Newco")
        res = self.dismiss(cid * 4 + NEW)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), {"id": cid * 4 + NEW})
        row = self.conn.execute("SELECT kind, ref_id, dismissed_at FROM watch_dismissal").fetchall()
        self.assertEqual([tuple(r) for r in row], [("new_reason_no_role", cid, NOW)])
        self.assertEqual(self.items(), [])
        # A later score run does not bring it back.
        score(self.conn, cid, 99, version="r2", scored_at="2026-11-10T06:00:00Z")
        self.assertEqual(self.items(), [])

    def test_dismissing_a_survivors_new_company_hides_it_even_when_a_duplicate_was_merged_in_later(self):
        a = self.new_company("Acme")
        self.dismiss(a * 4 + NEW)
        s.company(self.conn, "Acme Labs", "removed", remove_reason="merged", merged_into=a)
        self.assertEqual(self.items(), [])

    def test_dismissing_a_new_company_twice_keeps_one_row_and_one_event(self):
        cid = self.new_company("Newco")
        first = self.dismiss(cid * 4 + NEW)
        second = self.dismiss(cid * 4 + NEW)  # another click, another key
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM watch_dismissal").fetchone()[0], 1)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM event WHERE kind = 'watch_dismissed'").fetchone()[0], 1)

    def test_a_repeated_key_replays_and_writes_once(self):
        cid = self.new_company("Newco")
        first = self.dismiss(cid * 4 + NEW, key="same-key-1234")
        again = self.dismiss(cid * 4 + NEW, key="same-key-1234")
        self.assertEqual(first.json(), again.json())
        self.assertEqual(again.headers.get("Idempotent-Replay"), "true")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM event WHERE kind = 'watch_dismissed'").fetchone()[0], 1)

    def test_dismissing_a_careers_item_resolves_the_review_item_and_it_leaves_needs_you_too(self):
        a = s.company(self.conn, "Acme")
        watched_page(self.conn, a)
        rid = s.review(self.conn, a, "careers_changed")
        self.assertEqual(self.open_reviews(), 1)
        res = self.dismiss(rid * 4 + CAREERS)
        self.assertEqual(res.status_code, 200, res.text)
        row = self.conn.execute(
            "SELECT state, resolution, resolved_at, revision FROM review_item WHERE review_id = ?", (rid,)).fetchone()
        self.assertEqual(tuple(row), ("resolved", "marked_ok", NOW, 2))
        self.assertEqual(self.open_reviews(), 0)  # the queue Needs you reads
        self.assertEqual(self.items(), [])
        event = self.conn.execute(
            "SELECT entity, entity_id, company_id, actor FROM event WHERE kind = 'review_resolved'").fetchall()
        self.assertEqual([tuple(r) for r in event], [("review_item", rid, a, "devanshu")])

    def test_dismissing_a_careers_item_that_is_already_resolved_changes_nothing(self):
        a = s.company(self.conn, "Acme")
        rid = s.review(self.conn, a, "careers_changed", state="resolved")
        res = self.dismiss(rid * 4 + CAREERS)
        self.assertEqual(res.status_code, 200, res.text)
        row = self.conn.execute("SELECT revision FROM review_item WHERE review_id = ?", (rid,)).fetchone()
        self.assertEqual(row[0], 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM event WHERE kind = 'review_resolved'").fetchone()[0], 0)

    def test_merge_item_cannot_be_dismissed_here(self):
        b = s.company(self.conn, "Bolt")
        c = s.company(self.conn, "Bolt Labs")
        rid = s.review(self.conn, b, "merge_candidate", evidence_key=str(c))
        res = self.dismiss(rid * 4 + MERGE)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["code"], "not_allowed")
        self.assertEqual(res.json()["message"], "Decide it in Needs you.")
        self.assertEqual(self.open_reviews(), 1)
        self.assertEqual(self.items()[0]["kind"], "merge_candidate")

    def test_a_refused_dismiss_stores_nothing_so_a_retry_runs_for_real(self):
        res = self.dismiss(9999 * 4 + NEW, key="retry-key-1234")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM api_request").fetchone()[0], 0)

    def test_unknown_id_is_not_found(self):
        a = s.company(self.conn, "Acme")
        other = s.review(self.conn, a, "size_unknown")
        for item_id in (0, -4, 3, 4 * 9999 + CAREERS, 4 * 9999 + NEW, 4 * 9999 + MERGE, 4 * other + CAREERS,
                        4 * other + MERGE, 4 * 9999 + 3):
            with self.subTest(item_id=item_id):
                res = self.dismiss(item_id)
                self.assertEqual(res.status_code, 404, res.text)
                self.assertEqual(res.json()["code"], "not_found")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM watch_dismissal").fetchone()[0], 0)
        self.assertEqual(self.open_reviews(), 1)

    def test_a_dismiss_needs_an_idempotency_key(self):
        cid = self.new_company("Newco")
        res = self.client.post(f"/api/watch/{cid * 4 + NEW}/dismiss", content="{}",
                               headers={"Content-Type": "application/json"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.items()[0]["company"]["id"], cid)
