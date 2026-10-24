"""Task 21: the No-go list (`GET /api/no-go`) and lifting an entry (`POST /api/no-go/{id}/lift`)."""
import json

from tests import apiseed as s
from tests.apitest import NOW, ApiCase
from tests.test_api_approve import ApproveBase


def no_go(conn, *, company=None, person=None, reason="other", source="devanshu", source_ref="", quote="", note="",
          created_at="2026-11-02T12:00:00Z", lifted_at=None, lift_reason=None) -> int:
    return s._insert(conn, "no_go", {
        "company_id": company, "person_id": person, "reason": reason, "quote": quote, "note": note, "source": source,
        "source_ref": source_ref, "created_at": created_at, "lifted_at": lifted_at, "lift_reason": lift_reason})


class NoGoCase(ApiCase):
    def view(self) -> dict:
        res = self.get("/api/no-go")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def lift(self, no_go_id, reason="They now sponsor", key=None):
        return self.post(f"/api/no-go/{no_go_id}/lift", {"reason": reason}, key=key)


class NoGoView(NoGoCase):
    def test_empty_store_gives_empty_no_go_and_done_views(self):
        self.assertEqual(self.view(), {"asOf": NOW, "companies": [], "people": [], "countsByReason": [],
                                       "hiddenCompanies": []})
        done = self.get("/api/done").json()
        self.assertEqual(done["companies"], [])
        self.assertEqual(done["metrics"]["repliesThisMonth"], 0)

    def test_no_go_lists_companies_people_and_removed_rows_with_counts(self):
        a = s.company(self.conn, "Acme", "candidate")
        b = s.company(self.conn, "Bolt", "candidate")
        c = s.company(self.conn, "Cog", "removed", remove_reason="not_a_fit", status_note="Too big",
                      status_at="2026-11-03T10:00:00Z")
        gone = s.company(self.conn, "Dup", "removed", merged_into=a)  # a merged duplicate is not "removed by you"
        pat = s.person(self.conn, a, "Pat Lee")
        no_go(self.conn, company=a, reason="no_sponsorship", quote="No visas", created_at="2026-11-01T09:00:00Z")
        no_go(self.conn, company=b, reason="no_sponsorship", created_at="2026-11-02T09:00:00Z")
        no_go(self.conn, person=pat, reason="said_no", source="reply", note="Asked us to stop")
        no_go(self.conn, company=a, reason="agency", lifted_at=NOW, lift_reason="Checked", source="rules")
        body = self.view()
        self.assertEqual([r["kind"] for r in body["companies"]], ["no_go", "no_go", "removed"])
        newest = body["companies"][0]
        self.assertEqual((newest["target"]["kind"], newest["target"]["company"]["name"]), ("company", "Bolt"))
        first = body["companies"][1]
        self.assertEqual((first["reason"], first["quote"], first["source"]), ("no_sponsorship", "No visas", "devanshu"))
        removed = body["companies"][2]
        self.assertEqual((removed["company"]["id"], removed["removeReason"], removed["note"], removed["removedAt"]),
                         (c, "not_a_fit", "Too big", "2026-11-03T10:00:00Z"))
        self.assertEqual(removed["company"]["revision"], 1)
        self.assertNotIn(gone, [r["company"]["id"] for r in body["companies"] if r["kind"] == "removed"])
        [person] = body["people"]
        self.assertEqual((person["target"]["kind"], person["target"]["person"]["name"], person["target"]["companyName"]),
                         ("person", "Pat Lee", "Acme"))
        self.assertEqual(person["reason"], "said_no")
        self.assertEqual(body["countsByReason"], [{"reason": "no_sponsorship", "count": 2}, {"reason": "removed", "count": 1}])

    def test_a_no_go_on_a_merged_duplicate_shows_its_survivor(self):
        keep = s.company(self.conn, "Keep", "candidate")
        dup = s.company(self.conn, "Keep Inc", "removed", merged_into=keep)
        no_go(self.conn, company=dup, reason="over_300")
        [row] = self.view()["companies"]
        self.assertEqual((row["target"]["company"]["id"], row["target"]["company"]["name"]), (keep, "Keep"))

    def test_no_go_view_lists_ranking_hides_with_reason_kind_and_provisional(self):
        big = s.company(self.conn, "Bigco", "candidate")
        wait = s.company(self.conn, "Waitco", "candidate")
        shown = s.company(self.conn, "Shownco", "candidate")
        s.score(self.conn, big, total=9.0, hide={"kind": "over_size", "reason": "About 800 people", "provisional": False})
        s.score(self.conn, wait, total=5.0, hide={"kind": "no_sponsorship", "reason": "Posting says no visas",
                                                 "provisional": True})
        s.score(self.conn, shown, total=7.0)
        hidden = self.view()["hiddenCompanies"]
        self.assertEqual([(h["company"]["name"], h["kind"], h["reason"], h["provisional"]) for h in hidden],
                         [("Bigco", "over_size", "About 800 people", False),
                          ("Waitco", "no_sponsorship", "Posting says no visas", True)])

    def test_no_go_source_url_comes_from_the_source_ref(self):
        a = s.company(self.conn, "Acme")
        b = s.company(self.conn, "Bolt")
        c = s.company(self.conn, "Cog")
        no_go(self.conn, company=a, source="claude", source_ref="https://acme.example/jobs/1")
        no_go(self.conn, company=b, source="import", source_ref="import:data_js:42")
        no_go(self.conn, company=c, source="devanshu", source_ref="Closed by you")
        rows = {r["target"]["company"]["name"]: r for r in self.view()["companies"]}
        self.assertEqual((rows["Acme"]["sourceUrl"], rows["Acme"]["importSource"]), ("https://acme.example/jobs/1", None))
        self.assertEqual((rows["Bolt"]["sourceUrl"], rows["Bolt"]["importSource"]), (None, "data_js"))
        self.assertEqual((rows["Cog"]["sourceUrl"], rows["Cog"]["importSource"]), (None, None))
        self.assertEqual(rows["Cog"]["sourceRef"], "Closed by you")


class Lift(NoGoCase):
    def setUp(self):
        super().setUp()
        self.acme = s.company(self.conn, "Acme", "candidate")
        self.row = no_go(self.conn, company=self.acme, reason="no_sponsorship")

    def stored(self):
        return self.conn.execute("SELECT * FROM no_go WHERE no_go_id = ?", (self.row,)).fetchone()

    def test_lift_keeps_the_row_and_removes_it_from_the_list(self):
        res = self.lift(self.row, key="lift-key-1")
        self.assertEqual((res.status_code, res.json()), (200, {}))
        row = self.stored()
        self.assertEqual((row["lifted_at"], row["lift_reason"]), (NOW, "They now sponsor"))
        self.assertEqual(self.view()["companies"], [])
        self.assertEqual(self.view()["countsByReason"], [])
        [event] = self.conn.execute("SELECT * FROM event WHERE kind = 'lifted'").fetchall()
        self.assertEqual((event["entity"], event["entity_id"], event["actor"], event["company_id"],
                          event["idempotency_key"]), ("no_go", self.row, "devanshu", self.acme, "lift-key-1:0"))
        self.assertEqual(json.loads(event["detail"])["reason"], "They now sponsor")

    def test_lift_twice_is_not_allowed(self):
        self.assertEqual(self.lift(self.row).status_code, 200)
        res = self.lift(self.row, reason="Again")
        self.assertEqual((res.status_code, res.json()["code"]), (409, "not_allowed"))
        self.assertEqual(self.stored()["lift_reason"], "They now sponsor")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM event WHERE kind = 'lifted'").fetchone()[0], 1)

    def test_the_same_key_replays_and_lifts_once(self):
        first, again = self.lift(self.row, key="lift-key-2"), self.lift(self.row, key="lift-key-2")
        self.assertEqual((first.status_code, again.status_code), (200, 200))
        self.assertEqual(again.headers.get("Idempotent-Replay"), "true")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM event WHERE kind = 'lifted'").fetchone()[0], 1)

    def test_a_reason_is_required_and_an_unknown_id_is_not_found(self):
        for reason in ("", "   "):
            res = self.lift(self.row, reason=reason)
            self.assertEqual((res.status_code, res.json()["code"]), (400, "bad_request"), res.text)
        self.assertIsNone(self.stored()["lifted_at"])
        self.assertEqual(self.lift(9999).status_code, 404)

    def test_lifting_a_person_row(self):
        pat = s.person(self.conn, self.acme, "Pat")
        pid = no_go(self.conn, person=pat, reason="not_a_fit")
        self.assertEqual(self.lift(pid).status_code, 200)
        self.assertEqual(self.view()["people"], [])


class LiftThenApprove(ApproveBase):
    def test_lifted_company_can_be_approved_again(self):
        row = no_go(self.conn, company=self.acme, reason="other")
        blocked = self.approve()
        self.assertEqual(blocked.status_code, 422, blocked.text)
        self.assertIn("no_go_person", [c["code"] for c in blocked.json()["checks"] if c["severity"] == "block"])
        self.assertEqual(self.post(f"/api/no-go/{row}/lift", {"reason": "They changed their mind"}).status_code, 200)
        self.assertEqual(self.approve().status_code, 200)


class LiftOnAFamily(NoGoCase):
    """To him a merged company is one company: lifting its no-go lifts every active no-go of the family."""

    def setUp(self):
        super().setUp()
        self.keep = s.company(self.conn, "Keep", "candidate")
        self.dup = s.company(self.conn, "Keep Inc", "removed", merged_into=self.keep)
        self.on_dup = no_go(self.conn, company=self.dup, reason="over_300", created_at="2026-11-01T09:00:00Z")
        self.on_keep = no_go(self.conn, company=self.keep, reason="agency", created_at="2026-11-02T09:00:00Z")
        self.other = no_go(self.conn, company=s.company(self.conn, "Elsewhere"), reason="other")
        self.pat = no_go(self.conn, person=s.person(self.conn, self.keep, "Pat"), reason="not_a_fit")

    def active(self) -> set[int]:
        return {r[0] for r in self.conn.execute("SELECT no_go_id FROM no_go WHERE lifted_at IS NULL")}

    def test_lifting_one_row_lifts_every_active_company_row_of_its_family_in_one_write(self):
        res = self.lift(self.on_dup, key="lift-family-1")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.active(), {self.other, self.pat})  # another company and a person stay
        events = self.conn.execute("SELECT entity_id, company_id, idempotency_key FROM event WHERE kind = 'lifted' "
                                   "ORDER BY event_id").fetchall()
        self.assertEqual([(e["entity_id"], e["company_id"], e["idempotency_key"]) for e in events],
                         [(self.on_dup, self.keep, "lift-family-1:0"), (self.on_keep, self.keep, "lift-family-1:1")])
        for row in self.conn.execute("SELECT * FROM no_go WHERE lifted_at IS NOT NULL"):
            self.assertEqual((row["lifted_at"], row["lift_reason"]), (NOW, "They now sponsor"))

    def test_the_ranking_and_the_sender_agree_after_the_lift(self):
        from outreach.drafts.followups import company_map, no_go_sets
        from outreach.ranking.hide import load_hide_inputs
        self.assertEqual(self.lift(self.on_keep).status_code, 200)
        companies = company_map(self.conn)
        self.assertNotIn(self.keep, no_go_sets(self.conn, companies)[1])
        canon = {self.keep: self.keep, self.dup: self.keep}
        inputs = load_hide_inputs(self.conn, canon, frozenset(), {})
        self.assertNotEqual(inputs[self.keep].no_go, "active")
        self.assertEqual(inputs[self.keep].no_go, "lifted")

    def test_the_no_go_page_shows_nothing_active_for_the_family(self):
        self.assertEqual(self.lift(self.on_dup).status_code, 200)
        ids = [r["target"]["company"]["id"] for r in self.view()["companies"] if r["kind"] == "no_go"]
        self.assertNotIn(self.keep, ids)
        self.assertEqual(len(ids), 1)  # only the other company's row is left

    def test_a_second_lift_from_the_other_member_is_not_allowed(self):
        self.assertEqual(self.lift(self.on_dup).status_code, 200)
        res = self.lift(self.on_keep, reason="Again")
        self.assertEqual((res.status_code, res.json()["code"]), (409, "not_allowed"))

    def test_lifting_a_person_row_touches_only_that_row(self):
        self.assertEqual(self.lift(self.pat).status_code, 200)
        self.assertEqual(self.active(), {self.on_dup, self.on_keep, self.other})
