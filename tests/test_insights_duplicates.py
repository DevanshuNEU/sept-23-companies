"""Possible duplicates (#81, Task 4): pairs of reached companies that may be one, listed with their basis and raised
as merge items only on request. Listing writes nothing."""
import itertools
import unittest

from outreach.insights.dataset import load_outcome_rows
from outreach.insights.duplicates import DuplicatePair, possible_duplicates, raise_duplicate_items
from tests import apiseed as S
from tests.insights_seed import merge, outcome_touch
from tests.storetest import fresh_store, table_marks

SENT = "2026-10-01T15:00:00Z"
_n = itertools.count(1)


class DuplicatesTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()

    def reached(self, name: str, email_domain: str | None = None) -> int:
        """A company with one touch; its person has an address at `email_domain`, or none."""
        cid = S.company(self.conn, name)
        if email_domain:
            pid = S.person(self.conn, cid, f"Person {next(_n)}", email=f"p{next(_n)}@{email_domain}")
        else:
            pid = S.person(self.conn, cid, f"Person {next(_n)}", email=None, channel="linkedin")
        outcome_touch(self.conn, cid, pid, sent_at=SENT)
        return cid

    def pairs(self) -> list[DuplicatePair]:
        return possible_duplicates(self.conn, load_outcome_rows(self.conn, scores=False))

    def test_gm_and_general_motors_are_a_possible_pair(self):
        big = self.reached("General Motors")
        small = self.reached("gm")
        self.reached("Green Mountain Coffee")  # three words, so its initials are gmc, not gm
        self.assertEqual([DuplicatePair(big, "General Motors", small, "gm", "insights_initials", "weak",
                                        "gm is the initials of General Motors")], self.pairs())
        # With people at gm.com in both, the stronger basis wins and the pair is still listed once.
        for cid in (big, small):
            S.person(self.conn, cid, f"Person {next(_n)}", email=f"x{next(_n)}@eng.gm.com")
        (pair,) = self.pairs()
        self.assertEqual(("insights_email_domain", "likely", "both have people at gm.com"),
                         (pair.basis, pair.confidence, pair.detail))
        self.assertLess(pair.a_id, pair.b_id)

    def test_same_domain_fact_is_strong_and_a_guess_never_pairs(self):
        a, b = self.reached("Acme Robotics"), self.reached("Acme Labs")
        S.evidence(self.conn, a, "domain", "acme.com")
        S.evidence(self.conn, b, "domain", "www.acme.com", confidence="estimate")
        c, d = self.reached("Beta One"), self.reached("Beta Two")
        S.evidence(self.conn, c, "domain", "beta.com")
        S.evidence(self.conn, d, "domain", "beta.com", confidence="guess")
        e, f = self.reached("Pages One"), self.reached("Pages Two")
        S.evidence(self.conn, e, "domain", "github.io")
        S.evidence(self.conn, f, "domain", "github.io")
        self.assertEqual([DuplicatePair(a, "Acme Robotics", b, "Acme Labs", "insights_domain", "strong",
                                        "both have the domain acme.com")], self.pairs())

    def test_gmail_addresses_never_pair(self):
        self.reached("Solo One", "gmail.com")
        self.reached("Solo Two", "gmail.com")
        self.reached("Peo One", "trinet.com")
        self.reached("Peo Two", "trinet.com")
        self.assertEqual([], self.pairs())

    def test_same_name_key_is_likely(self):
        a, b = self.reached("Nimbus Robotics"), self.reached("Nimbus Robotics, Inc.")
        for cid in (a, b):
            self.conn.execute("INSERT INTO company_name_key(name_key, company_id, strong) VALUES (?, ?, 1)",
                              ("nimbusrobotics", cid))
        self.assertEqual([("insights_name_key", "likely", "both are known by the name nimbusrobotics")],
                         [(p.basis, p.confidence, p.detail) for p in self.pairs()])

    def test_pairs_are_ordered_by_confidence_then_ids(self):
        g1, g2 = self.reached("General Motors"), self.reached("gm")
        d1, d2 = self.reached("Delta One"), self.reached("Delta Two")
        S.evidence(self.conn, d1, "domain", "delta.com")
        S.evidence(self.conn, d2, "domain", "delta.com")
        self.assertEqual([(d1, d2, "strong"), (g1, g2, "weak")],
                         [(p.a_id, p.b_id, p.confidence) for p in self.pairs()])

    def test_a_merged_pair_is_not_listed(self):
        big, small = self.reached("General Motors", "gm.com"), self.reached("gm", "gm.com")
        merge(self.conn, small, big)
        self.assertEqual([], self.pairs())

    def test_a_merged_away_company_lends_its_keys_to_its_survivor(self):
        keep = self.reached("Keep Co")
        gone = S.company(self.conn, "Gone Co")
        S.evidence(self.conn, gone, "domain", "shared.com")
        merge(self.conn, gone, keep)
        other = self.reached("Other Co")
        S.evidence(self.conn, other, "domain", "shared.com")
        self.assertEqual([(keep, other, "insights_domain")], [(p.a_id, p.b_id, p.basis) for p in self.pairs()])

    def test_raise_makes_one_item_per_pair_and_a_rerun_makes_none(self):
        big, small = self.reached("General Motors"), self.reached("gm")
        pairs = self.pairs()
        self.assertEqual(1, raise_duplicate_items(self.conn, pairs))
        items = self.conn.execute("SELECT company_id, evidence_key, reason, raised_by FROM review_item "
                                  "WHERE kind = 'merge_candidate'").fetchall()
        self.assertEqual(1, len(items))
        self.assertEqual((big, str(small), "source"), (items[0][0], items[0][1], items[0][3]))
        self.assertIn("insights_initials", items[0][2])
        self.assertIn("confidence weak", items[0][2])
        self.assertEqual(0, raise_duplicate_items(self.conn, self.pairs()))
        self.assertEqual(0, raise_duplicate_items(self.conn, []))

    def test_a_pair_he_kept_apart_is_not_listed_again(self):
        self.reached("General Motors")
        self.reached("gm")
        raise_duplicate_items(self.conn, self.pairs())
        self.assertEqual(1, len(self.pairs()))  # an open item is still a possible pair
        self.conn.execute("UPDATE review_item SET state = 'resolved', resolution = 'marked_ok', "
                          "resolved_at = '2026-10-20T12:00:00Z' WHERE kind = 'merge_candidate'")
        self.assertEqual([], self.pairs())

    def test_a_shared_domain_never_pairs_companies_that_use_it_for_different_employers(self):
        a, b = self.reached("Acme Robotics", "acme.com"), self.reached("Acme Labs", "acme.com")
        S.evidence(self.conn, a, "domain", "acme.com")
        S.evidence(self.conn, b, "domain", "acme.com")
        self.assertEqual(1, len(self.pairs()))  # listed once, with its strongest basis, until the domain is shared
        self.conn.execute("INSERT INTO shared_key(kind, value, reason, recorded_at) "
                          "VALUES ('domain', 'acme.com', 'parent company', '2026-10-01T00:00:00Z')")
        self.assertEqual([], self.pairs())

    def test_a_legal_suffix_does_not_make_a_short_name(self):
        self.reached("GE Co")
        self.reached("Great Energy Cloud Operations")  # initials g, e, c, o
        self.reached("GE")
        self.reached("General Electric")
        # "GE Co" is "ge" once the suffix is dropped, so it pairs with General Electric, not with four initials.
        self.assertEqual({("GE Co", "General Electric"), ("GE", "General Electric")},
                         {(p.a_name, p.b_name) for p in self.pairs()})

    def test_one_name_that_starts_with_the_other_is_a_weak_pair(self):
        keys = {"Crusoe": "crusoe", "Crusoe Energy": "crusoeenergy", "Aarden": "aarden", "Aarden AI": "aardenai",
                "Other Co": "otherco", "Otherwise": "otherwise"}  # otherco does not start otherwise
        ids = {}
        for name, key in keys.items():
            ids[name] = self.reached(name)
            self.conn.execute("INSERT INTO company_name_key(name_key, company_id, strong) VALUES (?, ?, 1)",
                              (key, ids[name]))
        self.assertEqual({("Crusoe", "Crusoe Energy"), ("Aarden", "Aarden AI")},
                         {(p.a_name, p.b_name) for p in self.pairs() if p.basis == "insights_name_prefix"})
        (pair,) = [p for p in self.pairs() if p.a_name == "Crusoe"]
        self.assertEqual(("weak", "crusoe is the start of crusoeenergy"), (pair.confidence, pair.detail))

    def test_a_prefix_pair_keeps_a_stronger_basis(self):
        a, b = self.reached("Nimbus"), self.reached("Nimbus Robotics")
        for cid, key in ((a, "nimbus"), (b, "nimbusrobotics"), (a, "nimbusrobotics")):
            self.conn.execute("INSERT INTO company_name_key(name_key, company_id, strong) VALUES (?, ?, 1)",
                              (key, cid))
        (pair,) = self.pairs()
        self.assertEqual("insights_name_key", pair.basis)

    def test_a_pair_the_system_cleared_is_listed_and_raising_puts_it_back_open(self):
        big, small = self.reached("Crusoe"), self.reached("Crusoe Energy")
        for cid, key in ((big, "crusoe"), (small, "crusoeenergy")):
            self.conn.execute("INSERT INTO company_name_key(name_key, company_id, strong) VALUES (?, ?, 1)",
                              (key, cid))
        self.conn.execute(
            "INSERT INTO review_item(kind, company_id, reason, evidence_key, raised_by, created_at, state, "
            "resolution, resolved_at) VALUES ('merge_candidate', ?, 'Maybe one company: old guess', ?, 'source', "
            "'2026-10-20T12:00:00Z', 'resolved', 'cleared', '2026-10-21T12:00:00Z')", (big, str(small)))
        (pair,) = self.pairs()
        self.assertEqual("insights_name_prefix", pair.basis)
        self.assertEqual(1, raise_duplicate_items(self.conn, [pair]))
        state, reason = self.conn.execute(
            "SELECT state, reason FROM review_item WHERE kind = 'merge_candidate'").fetchone()
        self.assertEqual("open", state)
        self.assertIn("insights_name_prefix", reason)
        self.assertEqual(0, raise_duplicate_items(self.conn, [pair]))  # open now: no second one

    def test_listing_writes_nothing(self):
        big, small = self.reached("General Motors", "gm.com"), self.reached("gm", "gm.com")
        S.evidence(self.conn, big, "domain", "gm.com")
        S.evidence(self.conn, small, "domain", "gm.com")
        before, changes = table_marks(self.conn), self.conn.total_changes
        self.assertEqual(1, len(self.pairs()))
        self.assertEqual(before, table_marks(self.conn))
        self.assertEqual(changes, self.conn.total_changes)


if __name__ == "__main__":
    unittest.main()
