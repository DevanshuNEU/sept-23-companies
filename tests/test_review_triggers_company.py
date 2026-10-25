import unittest

from outreach.review import words
from outreach.review.items import raise_item, resolve_item
from outreach.review.triggers import SyncResult, sync_domain_missing, sync_size_unknown
from outreach.store.timefmt import utcnow
from tests.review_seed import seed_email_person, seed_plan_row, seed_queued_message, seed_status
from tests.storetest import fresh_store, seed_company

NOW = "2026-11-10T15:00:00Z"  # 10:00 in New York, the same day
TODAY = "2026-11-10"


def add_fact(conn, company_id, fact, value, **kw):
    cols = {"num_low": None, "num_high": None, "confidence": "estimate", "source_kind": "linkedin"}
    cols.update(kw)
    conn.execute(
        "INSERT INTO evidence(company_id, fact, value, num_low, num_high, confidence, source_kind, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (company_id, fact, value, cols["num_low"], cols["num_high"], cols["confidence"], cols["source_kind"],
         utcnow()))


class TriggerCompanyTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)

    def company(self, name="Acme"):
        return seed_company(self.conn, name)

    def items(self, kind="size_unknown", where=""):
        return self.conn.execute(f"SELECT * FROM review_item WHERE kind = ? {where} ORDER BY review_id",
                                 (kind,)).fetchall()

    def counts(self):
        return tuple(self.conn.execute(f"SELECT COUNT(*), COALESCE(MAX(rowid), 0) FROM {t}").fetchone()[:]
                     for t in ("review_item", "event"))

    def size(self, **kw):
        return sync_size_unknown(self.conn, now=NOW, **kw)

    def domain(self, **kw):
        return sync_domain_missing(self.conn, now=NOW, **kw)

    def picked(self, name="Acme"):
        cid = self.company(name)
        seed_plan_row(self.conn, cid, TODAY, "picked")
        return cid

    # trigger 4, size unknown

    def test_picked_company_with_no_size_raises_one_size_unknown_with_the_picked_reason(self):
        cid = self.picked("Retell")
        result = self.size()
        self.assertEqual(result, SyncResult(raised=1, cleared=0))
        (item,) = self.items()
        self.assertEqual((item["company_id"], item["raised_by"], item["evidence_key"], item["state"]),
                         (cid, "daily_list", "no-size", "open"))
        self.assertEqual(item["reason"], words.size_reason("Retell", "picked"))

    def test_planned_company_uses_the_planned_reason(self):
        cid = self.company("Plan Co")
        seed_plan_row(self.conn, cid, TODAY, "proposed", person_id=seed_email_person(self.conn, cid))
        self.size()
        (item,) = self.items()
        self.assertEqual(item["reason"], words.size_reason("Plan Co", "planned"))

    def test_a_size_guess_row_means_the_size_is_known_here(self):
        cid = self.picked()
        add_fact(self.conn, cid, "size", "11-50", num_low=11, num_high=50, confidence="guess")
        self.assertEqual(self.size(), SyncResult(0, 0))
        self.assertEqual(self.items(), [])

    def test_headcount_alone_counts_as_a_size(self):
        cid = self.picked()
        add_fact(self.conn, cid, "headcount", "120", num_low=120, num_high=120)
        self.assertEqual(self.size(), SyncResult(0, 0))

    def test_a_size_on_a_merged_duplicate_counts_for_the_company(self):
        cid, dupe = self.picked("Main"), self.company("Main Inc")
        seed_status(self.conn, dupe, "removed")
        self.conn.execute("UPDATE company SET merged_into = ? WHERE company_id = ?", (cid, dupe))
        add_fact(self.conn, dupe, "size", "40", num_low=40, num_high=40)
        self.assertEqual(self.size(), SyncResult(0, 0))

    def test_second_run_writes_nothing(self):
        self.picked()
        self.size()
        before = self.counts()
        self.assertEqual(self.size(), SyncResult(0, 0))
        self.assertEqual(self.counts(), before)

    def test_resolved_size_item_does_not_return_and_a_cleared_one_does(self):
        marked, cleared = self.picked("Marked"), self.picked("Cleared")
        self.size()
        by_company = {r["company_id"]: r for r in self.items()}
        resolve_item(self.conn, by_company[marked]["review_id"], resolution="marked_ok", note="ok", revision=1)
        self.conn.execute("UPDATE review_item SET state = 'resolved', resolution = 'cleared', resolved_at = ?, "
                          "revision = revision + 1 WHERE review_id = ?", (NOW, by_company[cleared]["review_id"]))
        result = self.size()
        self.assertEqual(result, SyncResult(raised=1, cleared=0))
        states = {r["company_id"]: (r["state"], r["resolution"]) for r in self.items()}
        self.assertEqual(states[marked], ("resolved", "marked_ok"))
        self.assertEqual(states[cleared], ("open", None))
        self.assertEqual(len(self.items()), 2)

    def test_a_decision_on_another_row_of_the_same_key_is_not_asked_again(self):
        cid = self.picked()
        self.size()
        item = self.items()[0]
        resolve_item(self.conn, item["review_id"], resolution="marked_no_go", note="no", revision=1)
        self.assertEqual(self.size(), SyncResult(0, 0))
        self.assertEqual(len(self.items()), 1)
        self.assertEqual(self.items()[0]["company_id"], cid)

    def test_size_arriving_clears_the_item_with_its_note(self):
        cid = self.picked()
        self.size()
        add_fact(self.conn, cid, "size", "30", num_low=30, num_high=30)
        self.assertEqual(self.size(), SyncResult(raised=0, cleared=1))
        (item,) = self.items()
        self.assertEqual((item["state"], item["resolution"], item["resolution_note"]),
                         ("resolved", "cleared", "A size is on file now."))
        event = self.conn.execute("SELECT * FROM event WHERE entity = 'review_item' AND kind = 'cleared'").fetchone()
        self.assertIsNotNone(event)

    def test_size_item_of_another_producer_is_not_cleared_here(self):
        cid = self.picked()
        raise_item(self.conn, kind="size_unknown", company_id=cid, reason="Somebody else raised this.",
                   evidence_key="elsewhere", raised_by="ranker")
        add_fact(self.conn, cid, "size", "30", num_low=30, num_high=30)
        self.size()
        (item,) = self.items(where="AND raised_by = 'ranker'")
        self.assertEqual(item["state"], "open")

    # trigger 5, missing domain

    def test_emailing_company_with_no_domain_raises_domain_missing_with_basis_sender(self):
        cid = self.company("Mailer")
        seed_queued_message(self.conn, seed_email_person(self.conn, cid), "drafted")
        self.assertEqual(self.domain(), SyncResult(raised=1, cleared=0))
        (item,) = self.items("domain_missing")
        self.assertEqual((item["company_id"], item["raised_by"], item["evidence_key"]), (cid, "sender", "no-domain"))
        self.assertEqual(item["reason"], words.domain_reason("Mailer"))

    def test_a_company_that_is_not_emailing_gets_no_domain_item(self):
        self.picked()
        cid = self.company("Sent")
        seed_queued_message(self.conn, seed_email_person(self.conn, cid), "sent")
        self.assertEqual(self.domain(), SyncResult(0, 0))
        self.assertEqual(self.items("domain_missing"), [])

    def test_a_domain_arriving_clears_the_sender_item(self):
        cid = self.company()
        seed_queued_message(self.conn, seed_email_person(self.conn, cid), "approved")
        self.domain()
        add_fact(self.conn, cid, "domain", "acme.com")
        self.assertEqual(self.domain(), SyncResult(raised=0, cleared=1))
        (item,) = self.items("domain_missing")
        self.assertEqual((item["resolution"], item["resolution_note"]), ("cleared", "A domain is on file now."))

    def test_the_crawlers_no_usable_domain_item_is_never_cleared_by_this_sweep(self):
        cid = self.company()
        raise_item(self.conn, kind="domain_missing", company_id=cid, reason="No usable domain for this company.",
                   evidence_key="no-usable-domain", raised_by="crawler")
        add_fact(self.conn, cid, "domain", "acme.com")
        self.domain()
        (item,) = self.items("domain_missing")
        self.assertEqual((item["raised_by"], item["state"]), ("crawler", "open"))

    # scope and size of the sweep

    def test_company_ids_limits_the_sweep_to_those_companies(self):
        a, b = self.picked("A"), self.picked("B")
        self.assertEqual(self.size(company_ids=[a]), SyncResult(1, 0))
        self.assertEqual([r["company_id"] for r in self.items()], [a])
        add_fact(self.conn, a, "size", "30", num_low=30, num_high=30)
        add_fact(self.conn, b, "size", "30", num_low=30, num_high=30)
        self.assertEqual(self.size(company_ids=[b]), SyncResult(0, 0))
        self.assertEqual(self.items()[0]["state"], "open")
        self.assertEqual(self.size(company_ids=[a]), SyncResult(0, 1))

    def test_forty_planned_companies_make_forty_items_and_five_hundred_other_companies_make_none(self):
        planned = []
        for n in range(40):
            cid = self.company(f"Planned {n}")
            planned.append(cid)
            seed_plan_row(self.conn, cid, TODAY, "proposed", person_id=seed_email_person(self.conn, cid))
        for n in range(500):
            self.company(f"Other {n}")
        self.assertEqual(self.size(), SyncResult(raised=40, cleared=0))
        self.assertEqual(sorted(r["company_id"] for r in self.items()), sorted(planned))
        self.assertEqual(self.size(), SyncResult(0, 0))
        self.assertEqual(len(self.items()), 40)

    def test_a_size_he_settled_on_a_merged_duplicate_is_not_asked_again_on_the_keeper(self):
        keeper, dupe = self.picked("Keeper"), self.company("Keeper Inc")
        self.size(company_ids=[keeper])
        # He settled the question while it sat on the duplicate; the merge then moved the plan to the keeper.
        self.conn.execute("UPDATE review_item SET company_id = ?", (dupe,))
        resolve_item(self.conn, self.items()[0]["review_id"], resolution="marked_ok", note="ok", revision=1)
        self.conn.execute("UPDATE company SET status = 'removed', remove_reason = 'merged', merged_into = ? "
                          "WHERE company_id = ?", (keeper, dupe))
        self.assertEqual(self.size(), SyncResult(0, 0))
        self.assertEqual(len(self.items()), 1)

    def test_a_merged_picked_and_emailed_company_gets_its_items_on_the_keeper(self):
        loser_a, keeper_a, loser_b, keeper_b = (self.company(n) for n in ("A", "A Inc", "B", "B Inc"))
        seed_plan_row(self.conn, loser_a, TODAY, "picked")
        seed_queued_message(self.conn, seed_email_person(self.conn, loser_b), "drafted")
        for loser, keeper in ((loser_a, keeper_a), (loser_b, keeper_b)):
            seed_status(self.conn, loser, "removed")
            self.conn.execute("UPDATE company SET merged_into = ? WHERE company_id = ?", (keeper, loser))
        self.assertEqual(self.size(), SyncResult(1, 0))
        self.assertEqual(self.domain(), SyncResult(1, 0))
        self.assertEqual([r["company_id"] for r in self.items()], [keeper_a])
        self.assertEqual([r["company_id"] for r in self.items("domain_missing")], [keeper_b])

    def test_no_review_item_text_holds_an_en_or_em_dash(self):
        self.picked("Dash" + chr(0x2014) + "Co")
        self.size()
        for r in self.items():
            self.assertNotIn(chr(0x2014), r["reason"])


if __name__ == "__main__":
    unittest.main()
