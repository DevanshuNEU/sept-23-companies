"""The research tools (#13, plan Task 7, lean v1): record_research, flag_review and mark_no_go, called through the
official SDK's client on a scratch store. Claude's evidence is never locked, never by hand and never confirmed; a
size, headcount, domain or hq it found is a guess. Claude never writes a no_go row: mark_no_go raises a Needs-you
item that Devanshu confirms."""
import unittest

from tests import apiseed as seed
from tests.mcptest import NOW, McpCase, fingerprint
from tests.test_mcp_reads import merge, no_go

SITE = "https://acme.example/about"


def item(fact="size", value="40", **extra) -> dict:
    out = {"fact": fact, "value": value, "source_kind": "website", "source_ref": SITE,
           "quote": "We are a team of 40 people."}
    if fact in ("size", "headcount"):
        out["num_low"] = 40
    out.update(extra)
    return out


class ResearchCase(McpCase):
    def setUp(self):
        super().setUp()
        self.acme = seed.company(self.db, "Acme", "working")
        self.pat = seed.person(self.db, self.acme, "Pat Lee")

    def evidence(self, company_id=None):
        return self.db.execute("SELECT fact, value, num_low, confidence, locked, source_kind, source_ref, quote "
                               "FROM evidence WHERE company_id = ? ORDER BY evidence_id",
                               (company_id or self.acme,)).fetchall()

    def events(self, entity):
        return self.db.execute("SELECT kind, actor, entity_id, company_id FROM event WHERE entity = ? "
                               "ORDER BY event_id", (entity,)).fetchall()

    def reviews(self):
        return self.db.execute("SELECT * FROM review_item ORDER BY review_id").fetchall()


class RecordResearchTest(ResearchCase):
    def test_a_size_is_stored_as_a_guess_with_its_source_quote_and_an_event_by_claude(self):
        got = self.ok("record_research", {"company_id": self.acme, "items": [item()]})
        self.assertEqual((got["added"], got["already_known"]), (1, 0))
        row = self.evidence()[0]
        self.assertEqual(tuple(row), ("size", "40", 40, "guess", 0, "website", SITE, "We are a team of 40 people."))
        fact = self.db.execute("SELECT confidence FROM company_fact WHERE company_id = ? AND fact = 'size'",
                               (self.acme,)).fetchone()
        self.assertEqual(fact["confidence"], "guess")  # ranking hides on confirmed or estimate only
        self.assertEqual([(e["kind"], e["actor"]) for e in self.events("evidence")], [("recorded", "claude")])

    def test_rows_and_their_event_carry_the_calls_clock(self):
        self.ok("record_research", {"company_id": self.acme, "items": [item()]})
        self.assertEqual(self.db.execute("SELECT recorded_at FROM evidence").fetchone()[0], NOW)
        self.assertEqual(self.db.execute("SELECT at FROM event WHERE entity = 'evidence'").fetchone()[0], NOW)

    def test_a_size_headcount_domain_or_hq_above_guess_is_refused_with_the_reason(self):
        for fact, value in (("size", "40"), ("headcount", "40"), ("domain", "acme.example"), ("hq", "Boston, MA")):
            with self.subTest(fact=fact):
                err = self.error("record_research", {"company_id": self.acme,
                                                     "items": [item(fact, value, confidence="estimate")]})
                self.assertEqual(err["code"], "bad_input")
                self.assertIn("recorded as a guess", err["message"])
        self.assertEqual(self.evidence(), [])

    def test_confirmed_is_bad_input_for_every_fact(self):
        for fact, value in (("size", "40"), ("stage", "seed"), ("domain", "acme.example"), ("hq", "Boston, MA")):
            with self.subTest(fact=fact):
                err = self.error("record_research", {"company_id": self.acme,
                                                     "items": [item(fact, value, confidence="confirmed")]})
                self.assertEqual(err["code"], "bad_input")
        self.assertEqual(self.evidence(), [])

    def test_a_stage_may_be_a_guess_or_an_estimate(self):
        self.ok("record_research", {"company_id": self.acme, "items": [
            item("stage", "seed", confidence="estimate"), item("stage", "series_a", quote="Raised a Series A.")]})
        self.assertEqual([(r["value"], r["confidence"]) for r in self.evidence()],
                         [("seed", "estimate"), ("series_a", "guess")])

    def test_only_the_five_facts_are_accepted(self):
        for fact in ("sponsorship", "lca", "funding", "everify", "cap_exempt", "staffing_risk"):
            with self.subTest(fact=fact):
                self.assertEqual(self.error("record_research", {"company_id": self.acme,
                                                                "items": [item(fact, "yes")]})["code"], "bad_input")

    def test_source_must_be_https_not_linkedin_and_the_quote_is_required(self):
        for change in ({"source_ref": "http://acme.example/about"}, {"source_ref": "acme.example"},
                       {"source_ref": "https://www.linkedin.com/company/acme"}, {"source_ref": "https://lnkd.in/x"},
                       {"quote": ""}, {"quote": "ok"}):
            with self.subTest(change=change):
                self.assertEqual(self.error("record_research", {"company_id": self.acme,
                                                                "items": [item(**change)]})["code"], "bad_input")
        bare = item()
        del bare["quote"]
        self.assertEqual(self.error("record_research", {"company_id": self.acme, "items": [bare]})["code"],
                         "bad_input")
        self.assertEqual(self.evidence(), [])

    def test_source_kind_is_limited(self):
        for kind in ("hand", "linkedin", "lca", "formd", "posting"):
            with self.subTest(kind=kind):
                self.assertEqual(self.error("record_research", {"company_id": self.acme,
                                                                "items": [item(source_kind=kind)]})["code"],
                                 "bad_input")
        for kind in ("website", "builtin", "yc", "hn", "other"):
            self.ok("record_research", {"company_id": self.acme, "items": [item(source_kind=kind)]})
        self.assertEqual({r["source_kind"] for r in self.evidence()}, {"website", "builtin", "yc", "hn", "other"})

    def test_rows_are_never_locked_and_never_by_hand(self):
        self.ok("record_research", {"company_id": self.acme, "items": [
            item(), item("domain", "acme.example"), item("hq", "Boston, MA"), item("stage", "seed")]})
        rows = self.evidence()
        self.assertEqual(len(rows), 4)
        self.assertEqual({(r["locked"], r["source_kind"]) for r in rows}, {(0, "website")})

    def test_the_same_finding_twice_is_already_known(self):
        self.ok("record_research", {"company_id": self.acme, "items": [item()]})
        got = self.ok("record_research", {"company_id": self.acme, "items": [item(), item("hq", "Boston, MA")]})
        self.assertEqual((got["added"], got["already_known"]), (1, 1))
        self.assertEqual(len(self.evidence()), 2)

    def test_a_free_mail_payroll_or_platform_domain_is_refused_naming_its_kind(self):
        for domain, kind in (("gmail.com", "free mail"), ("trinet.com", "payroll"), ("acme.vercel.app", "platform"),
                             ("github.com", "platform")):
            with self.subTest(domain=domain):
                err = self.error("record_research", {"company_id": self.acme, "items": [item("domain", domain)]})
                self.assertEqual(err["code"], "bad_input")
                self.assertIn(domain, err["message"])
                self.assertIn(kind, err["message"])
        self.assertEqual(self.evidence(), [])

    def test_a_domain_is_stored_lower_case(self):
        self.ok("record_research", {"company_id": self.acme, "items": [item("domain", "Acme.Example")]})
        self.assertEqual(self.evidence()[0]["value"], "acme.example")

    def test_one_bad_item_stores_nothing(self):
        before = fingerprint(self.db)
        err = self.error("record_research", {"company_id": self.acme, "items": [item(), item("domain", "gmail.com")]})
        self.assertEqual(err["code"], "bad_input")
        self.assertIn("items.1", err["message"])
        self.assertEqual(fingerprint(self.db), before)

    def test_a_size_needs_num_low_and_other_facts_take_no_numbers(self):
        no_low = item()
        del no_low["num_low"]
        self.assertEqual(self.error("record_research", {"company_id": self.acme, "items": [no_low]})["code"],
                         "bad_input")
        self.assertEqual(self.error("record_research", {"company_id": self.acme,
                                                        "items": [item("hq", "Boston, MA", num_low=3)]})["code"],
                         "bad_input")
        self.assertEqual(self.error("record_research", {"company_id": self.acme,
                                                        "items": [item(num_low=50, num_high=10)]})["code"],
                         "bad_input")

    def test_a_merged_company_records_on_the_survivor(self):
        dup = seed.company(self.db, "Acme Dup")
        merge(self.db, dup, self.acme)
        got = self.ok("record_research", {"company_id": dup, "items": [item()]})
        self.assertEqual(got["company_id"], self.acme)
        self.assertIn(str(dup), got["merged_note"])
        self.assertEqual(len(self.evidence(self.acme)), 1)
        self.assertEqual(self.evidence(dup), [])

    def test_unknown_company_is_not_found_and_the_batch_is_bounded(self):
        self.assertEqual(self.error("record_research", {"company_id": 999, "items": [item()]})["code"], "not_found")
        self.assertEqual(self.error("record_research", {"company_id": self.acme, "items": []})["code"], "bad_input")
        self.assertEqual(self.error("record_research", {"company_id": self.acme,
                                                        "items": [item(value=str(i), num_low=i) for i in range(9)]}
                                    )["code"], "bad_input")

    def test_locked_and_confidence_by_another_name_are_bad_input(self):
        for extra in ({"locked": 1}, {"confirmed": True}, {"recorded_at": "2026-11-10T00:00:00Z"}):
            with self.subTest(extra=extra):
                self.assertEqual(self.error("record_research", {"company_id": self.acme,
                                                                "items": [item(**extra)]})["code"], "bad_input")


class FlagReviewTest(ResearchCase):
    def test_a_flag_is_a_flagged_item_raised_by_claude_with_an_event(self):
        got = self.ok("flag_review", {"company_id": self.acme, "reason": "Careers page lists only senior roles."})
        self.assertTrue(got["created"])
        row = self.reviews()[0]
        self.assertEqual((row["review_id"], row["kind"], row["raised_by"], row["state"], row["company_id"]),
                         (got["review_id"], "flagged", "claude", "open", self.acme))
        self.assertEqual(row["reason"], "Careers page lists only senior roles.")
        self.assertTrue(row["evidence_key"].startswith("claude:"))
        self.assertEqual([(e["kind"], e["actor"], e["entity_id"]) for e in self.events("review_item")],
                         [("raised", "claude", got["review_id"])])

    def test_the_same_reason_twice_is_one_item_and_spacing_or_case_does_not_matter(self):
        first = self.ok("flag_review", {"company_id": self.acme, "reason": "Board looks stale."})
        again = self.ok("flag_review", {"company_id": self.acme, "reason": "  board looks   STALE. "})
        self.assertEqual((again["review_id"], again["created"]), (first["review_id"], False))
        self.assertEqual(len(self.reviews()), 1)
        self.assertEqual(len(self.events("review_item")), 1)
        other = self.ok("flag_review", {"company_id": self.acme, "reason": "Another thing entirely."})
        self.assertTrue(other["created"])

    def test_posting_and_person_must_belong_to_the_company(self):
        other = seed.company(self.db, "Other")
        their_post = seed.posting(self.db, other)
        their_person = seed.person(self.db, other, "Sam Other")
        for extra in ({"posting_id": their_post}, {"person_id": their_person}, {"posting_id": 999},
                      {"person_id": 999}):
            with self.subTest(extra=extra):
                err = self.error("flag_review", {"company_id": self.acme, "reason": "Look at this one.", **extra})
                self.assertEqual(err["code"], "bad_input")
        self.assertEqual(self.reviews(), [])
        post = seed.posting(self.db, self.acme)
        got = self.ok("flag_review", {"company_id": self.acme, "reason": "Look at this one.", "posting_id": post,
                                      "person_id": self.pat})
        row = self.reviews()[0]
        self.assertEqual((row["review_id"], row["posting_id"], row["person_id"]), (got["review_id"], post, self.pat))

    def test_a_resolved_flag_is_not_raised_again_for_the_same_reason(self):
        first = self.ok("flag_review", {"company_id": self.acme, "reason": "Board looks stale."})
        self.db.execute("UPDATE review_item SET state = 'resolved', resolution = 'marked_ok', resolved_at = "
                        "'2026-11-10T16:00:00Z' WHERE review_id = ?", (first["review_id"],))
        again = self.ok("flag_review", {"company_id": self.acme, "reason": "Board looks stale."})
        self.assertEqual((again["review_id"], again["created"], again["state"]), (first["review_id"], False,
                                                                                  "resolved"))
        self.assertEqual(self.reviews()[0]["state"], "resolved")

    def test_a_merged_company_flags_the_survivor_and_a_person_of_the_duplicate_counts(self):
        dup = seed.company(self.db, "Acme Dup")
        dup_person = seed.person(self.db, dup, "Dee Dup")
        merge(self.db, dup, self.acme)
        self.ok("flag_review", {"company_id": dup, "reason": "Two names for one company.", "person_id": dup_person})
        self.assertEqual(self.reviews()[0]["company_id"], self.acme)

    def test_a_dash_in_the_reason_is_cleaned_not_refused(self):
        self.ok("flag_review", {"company_id": self.acme, "reason": f"Hiring {chr(0x2014)} maybe"})
        self.assertEqual(self.reviews()[0]["reason"], "Hiring - maybe")

    def test_reason_bounds_and_unknown_company(self):
        self.assertEqual(self.error("flag_review", {"company_id": self.acme, "reason": "   "})["code"], "bad_input")
        self.assertEqual(self.error("flag_review", {"company_id": self.acme, "reason": "x" * 501})["code"],
                         "bad_input")
        self.assertEqual(self.error("flag_review", {"company_id": 999, "reason": "Look at this."})["code"],
                         "not_found")
        self.assertEqual(self.error("flag_review", {"company_id": self.acme, "reason": "Look.", "kind": "merge"}
                                    )["code"], "bad_input")


class MarkNoGoTest(ResearchCase):
    def no_go_rows(self):
        return self.db.execute("SELECT COUNT(*) FROM no_go").fetchone()[0]

    def test_writes_one_needs_you_item_and_no_no_go_row(self):
        got = self.ok("mark_no_go", {"person_id": self.pat, "reason": "said_no", "quote": "Please do not email me."})
        self.assertTrue(got["created"])
        self.assertEqual(self.no_go_rows(), 0)
        row = self.reviews()[0]
        self.assertEqual((row["review_id"], row["kind"], row["state"], row["raised_by"], row["person_id"],
                          row["company_id"], row["evidence_key"]),
                         (got["review_id"], "flagged", "open", "claude", self.pat, self.acme,
                          f"claude-no-go:{self.pat}:said_no"))
        self.assertIn("Devanshu", got["message"])
        self.assertEqual([(e["kind"], e["actor"]) for e in self.events("review_item")], [("raised", "claude")])

    def test_the_item_names_the_person_and_carries_the_quote(self):
        self.ok("mark_no_go", {"person_id": self.pat, "reason": "stop_reply", "quote": "Take me off your list."})
        reason = self.reviews()[0]["reason"]
        self.assertIn("Pat Lee", reason)
        self.assertIn("Take me off your list.", reason)
        self.assertIn("no-go", reason)

    def test_a_quote_with_a_dash_is_cleaned_not_refused(self):
        self.ok("mark_no_go", {"person_id": self.pat, "reason": "said_no", "quote": f"No thanks {chr(0x2014)} busy"})
        self.assertIn("No thanks - busy", self.reviews()[0]["reason"])

    def test_said_no_and_stop_reply_need_the_reply_quote(self):
        for reason in ("said_no", "stop_reply"):
            with self.subTest(reason=reason):
                err = self.error("mark_no_go", {"person_id": self.pat, "reason": reason, "note": "They said no."})
                self.assertEqual(err["code"], "bad_input")
                self.assertIn("quote", err["message"])
        self.assertEqual(self.reviews(), [])

    def test_other_reasons_need_a_note(self):
        for reason in ("not_a_fit", "other"):
            with self.subTest(reason=reason):
                err = self.error("mark_no_go", {"person_id": self.pat, "reason": reason})
                self.assertEqual(err["code"], "bad_input")
                self.assertIn("note", err["message"])
        self.ok("mark_no_go", {"person_id": self.pat, "reason": "not_a_fit", "note": "Works in sales, not eng."})

    def test_no_company_no_go_and_no_other_reason_exist(self):
        for args in ({"person_id": self.pat, "reason": "said_no", "quote": "No.", "company_id": self.acme},
                     {"person_id": self.pat, "reason": "no_sponsorship", "note": "x"},
                     {"person_id": self.pat, "reason": "said_no", "quote": "No.", "source": "devanshu"}):
            with self.subTest(args=args):
                self.assertEqual(self.error("mark_no_go", args)["code"], "bad_input")

    def test_a_second_call_returns_the_existing_item(self):
        first = self.ok("mark_no_go", {"person_id": self.pat, "reason": "said_no", "quote": "No."})
        again = self.ok("mark_no_go", {"person_id": self.pat, "reason": "said_no", "quote": "No, really."})
        self.assertEqual((again["review_id"], again["created"]), (first["review_id"], False))
        self.assertEqual(len(self.reviews()), 1)

    def test_a_person_already_on_the_no_go_list_is_said_plainly(self):
        no_go(self.db, person_id=self.pat)
        err = self.error("mark_no_go", {"person_id": self.pat, "reason": "said_no", "quote": "No."})
        self.assertEqual(err["code"], "already_no_go")
        self.assertEqual(self.reviews(), [])

    def test_a_person_at_a_merged_company_raises_it_on_the_survivor(self):
        dup = seed.company(self.db, "Acme Dup")
        dee = seed.person(self.db, dup, "Dee Dup")
        merge(self.db, dup, self.acme)
        self.ok("mark_no_go", {"person_id": dee, "reason": "said_no", "quote": "No."})
        self.assertEqual(self.reviews()[0]["company_id"], self.acme)

    def test_unknown_person_is_not_found(self):
        self.assertEqual(self.error("mark_no_go", {"person_id": 999, "reason": "said_no", "quote": "No."})["code"],
                         "not_found")


if __name__ == "__main__":
    unittest.main()
