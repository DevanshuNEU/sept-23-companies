import json
import unittest

from outreach.review import ReviewError
from outreach.review.hand import normalize_domain, normalize_sponsorship, parse_hand_size, write_hand_fact
from outreach.rules.facts import EvidenceDraft
from outreach.store import mark_shared_key
from tests import apiseed as s
from tests.storetest import fresh_store

NOW = "2026-11-10T03:30:00Z"  # Nov 9 in New York


class HandSizeTest(unittest.TestCase):
    def read(self, text):
        d = parse_hand_size(text)
        return d.value, d.num_low, d.num_high, d.confidence

    def test_size_text_cases(self):
        self.assertEqual(self.read("45"), ("45", 45, 45, "confirmed"))
        self.assertEqual(self.read(" 45 "), ("45", 45, 45, "confirmed"))
        self.assertEqual(self.read("about 45"), ("45", 45, 45, "estimate"))
        self.assertEqual(self.read("11 to 50"), ("11-50", 11, 50, "estimate"))
        self.assertEqual(self.read("50+"), ("50+", 50, None, "estimate"))
        self.assertEqual(self.read("1,000"), ("1000", 1000, 1000, "confirmed"))
        self.assertEqual(self.read("45 guess")[3], "guess")
        self.assertEqual(self.read("45 guess")[:3], ("45", 45, 45))
        draft = parse_hand_size("about 45")
        self.assertEqual((draft.fact, draft.source_kind, draft.quote), ("size", "hand", "about 45"))

    def test_size_refusals(self):
        with self.assertRaises(ReviewError) as caught:
            parse_hand_size("lots")
        self.assertEqual((caught.exception.code, caught.exception.message),
                         ("bad_request", "Could not read a size from 'lots'. Try 45 or 11 to 50. Nothing was saved."))
        for text in ("0", "", "   "):
            with self.subTest(text=text), self.assertRaises(ReviewError) as caught:
                parse_hand_size(text)
            self.assertEqual(caught.exception.code, "bad_request")
            self.assertTrue(caught.exception.message.endswith("Nothing was saved."))


class HandDomainTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)

    def test_domain_cases(self):
        for text in ("https://www.Cerebrium.ai/careers", "ana@cerebrium.ai", "cerebrium.ai.", "WWW.cerebrium.ai",
                     "http://cerebrium.ai:443/jobs?x=1"):
            with self.subTest(text=text):
                self.assertEqual(normalize_domain(self.conn, text), "cerebrium.ai")

    def test_domain_refusals(self):
        mark_shared_key(self.conn, "domain", "bigholding.com", "3 employers: A, B, C")
        messages = []
        for text in ("cerebrium", "gmail.com", "deel.com", "acme.vercel.app", "github.com", "bigholding.com"):
            with self.subTest(text=text), self.assertRaises(ReviewError) as caught:
                normalize_domain(self.conn, text)
            self.assertEqual(caught.exception.code, "bad_request")
            self.assertTrue(caught.exception.message.endswith("Nothing was saved."), caught.exception.message)
            messages.append(caught.exception.message)
        self.assertEqual(len(set(messages)), len(messages), messages)
        self.assertIn("personal mail", messages[1])
        self.assertIn("payroll", messages[2])
        self.assertIn("vercel.app", messages[3])
        self.assertIn("shared by several employers", messages[5])
        # A subdomain of a shared domain is shared too.
        with self.assertRaises(ReviewError):
            normalize_domain(self.conn, "eng.bigholding.com")


class HandSponsorshipTest(unittest.TestCase):
    def test_sponsorship_cases_and_no_is_a_no_go(self):
        for text, want in (("Yes", "yes"), ("yes", "yes"), ("OPT only", "opt_only"), ("opt only", "opt_only"),
                           ("opt_only", "opt_only"), ("Unclear", "unclear")):
            with self.subTest(text=text):
                self.assertEqual(normalize_sponsorship(text), want)
        with self.assertRaises(ReviewError) as caught:
            normalize_sponsorship("No")
        self.assertEqual((caught.exception.code, caught.exception.message),
                         ("not_allowed", "Sponsorship No is a no-go. Use Mark no-go."))
        with self.assertRaises(ReviewError) as caught:
            normalize_sponsorship("maybe")
        self.assertEqual(caught.exception.code, "bad_request")


class HandFactTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.cid = s.company(self.conn, "Acme")

    def fact(self, fact="size"):
        return self.conn.execute("SELECT * FROM company_fact WHERE company_id = ? AND fact = ?",
                                 (self.cid, fact)).fetchone()

    def test_hand_fact_is_locked_hand_evidence_and_wins_company_fact_over_a_later_confirmed_automated_row(self):
        draft = parse_hand_size("about 45")
        draft = EvidenceDraft(draft.fact, draft.value, draft.confidence, "hand", source_ref="Their about page",
                              quote=draft.quote, num_low=draft.num_low, num_high=draft.num_high)
        eid = write_hand_fact(self.conn, self.cid, draft, now=NOW)
        row = self.conn.execute("SELECT * FROM evidence WHERE evidence_id = ?", (eid,)).fetchone()
        self.assertEqual((row["company_id"], row["fact"], row["value"], row["num_low"], row["num_high"],
                          row["confidence"], row["locked"], row["source_kind"], row["source_ref"], row["quote"],
                          row["observed_on"], row["recorded_at"], row["posting_id"]),
                         (self.cid, "size", "45", 45, 45, "estimate", 1, "hand", "Their about page", "about 45",
                          "2026-11-09", NOW, None))
        event = self.conn.execute("SELECT * FROM event WHERE entity = 'evidence' AND kind = 'hand_set'").fetchone()
        self.assertEqual((event["entity_id"], event["company_id"], event["actor"]), (eid, self.cid, "devanshu"))
        self.assertEqual(json.loads(event["detail"])["value"], "45")
        s.evidence(self.conn, self.cid, "size", "200", confidence="confirmed", source_kind="website", low=200,
                   recorded_at="2026-11-11T08:00:00Z")
        self.assertEqual(self.fact()["evidence_id"], eid)

    def test_writing_the_same_hand_value_twice_makes_two_rows(self):
        draft = EvidenceDraft("domain", "acme.io", "confirmed", "hand", source_ref="Their site", quote="acme.io")
        first = write_hand_fact(self.conn, self.cid, draft, now=NOW)
        second = write_hand_fact(self.conn, self.cid, draft, now="2026-11-10T04:00:00Z")
        self.assertNotEqual(first, second)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM evidence WHERE locked = 1").fetchone()[0], 2)
        self.assertEqual(self.fact("domain")["evidence_id"], second)  # the newest hand row wins

    def test_a_hand_fact_needs_a_source_and_hand_kind(self):
        with self.assertRaises(ReviewError) as caught:
            write_hand_fact(self.conn, self.cid, EvidenceDraft("domain", "acme.io", "confirmed", "hand"), now=NOW)
        self.assertEqual(caught.exception.code, "bad_request")
        with self.assertRaises(ValueError):
            write_hand_fact(self.conn, self.cid, EvidenceDraft("domain", "acme.io", "confirmed", "website",
                                                               source_ref="x"), now=NOW)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM evidence").fetchone()[0], 0)

    def test_a_sponsorship_fact_carries_its_posting(self):
        posting = s.posting(self.conn, self.cid)
        eid = write_hand_fact(self.conn, self.cid, EvidenceDraft("sponsorship", "yes", "confirmed", "hand",
                                                                 source_ref="The posting"), posting_id=posting,
                              now=NOW)
        self.assertEqual(self.conn.execute("SELECT posting_id FROM evidence WHERE evidence_id = ?",
                                           (eid,)).fetchone()[0], posting)


if __name__ == "__main__":
    unittest.main()
