"""Tasks 2 and 3 of #30: the source, crawl, reader and ranking contracts every stream imports."""
import unittest

from outreach.crawl.base import Adapter, JobRef, ReadResult, Validators, ids_fingerprint, valid_token
from outreach.ranking.types import CompanyFacts, Signal, SignalValue
from outreach.reader.types import (ALLOWED, FIELDS, INT_FIELDS, READING_SCHEMA_VERSION, TEXT_FIELDS, FieldReading,
                                   PostingReading, ReadRequest, quote_in_body)
from outreach.rules.rebuild import EvidenceDraft
from outreach.sources.base import Candidate, EvidenceDraft as ReExported, Source


def job(native_id="1"):
    return JobRef(native_id=native_id, title="Engineer", url="https://x.com/1", location="Boston, MA",
                  country="US", posted_on=None, body=None)


class CandidateTests(unittest.TestCase):
    def test_candidate_builds_with_every_allowed_key(self):
        keys = {"fein": "12-3456789", "dba": "Acme", "legal_name": "Acme Inc", "ats": "greenhouse:acme",
                "linkedin": "acme", "formd_cik": "0001"}
        c = Candidate(name="Acme", domain="acme.com", keys=keys, source_ref="hn:1")
        self.assertEqual(c.evidence, ())
        self.assertEqual(c.keys["ats"], "greenhouse:acme")
        self.assertEqual(c.source_ref, "hn:1")

    def test_candidate_allows_no_domain(self):
        self.assertIsNone(Candidate(name="Acme", domain=None, keys={}).domain)

    def test_candidate_rejects_unknown_key(self):
        with self.assertRaises(ValueError):
            Candidate(name="Acme", domain="acme.com", keys={"twitter": "acme"})

    def test_candidate_rejects_empty_name(self):
        for name in ("", "   "):
            with self.assertRaises(ValueError):
                Candidate(name=name, domain="acme.com", keys={})

    def test_candidate_rejects_bad_domain(self):
        for domain in ("a@acme.com", "Acme.com", "acme", ""):
            with self.subTest(domain=domain):
                with self.assertRaises(ValueError):
                    Candidate(name="Acme", domain=domain, keys={})

    def test_evidence_draft_is_the_rules_one(self):
        self.assertIs(ReExported, EvidenceDraft)

    def test_source_protocol_is_structural(self):
        class S:
            name = "hn"
            version = "1"

            def discover(self, since):
                yield Candidate(name="Acme", domain=None, keys={})

        s: Source = S()
        self.assertEqual(len(list(s.discover(None))), 1)


class ReadResultTests(unittest.TestCase):
    def test_read_result_invariants(self):
        v = Validators()
        bad = [
            dict(result="unchanged", jobs=(job(),)),
            dict(result="error", jobs=(job(),), error="boom"),
            dict(result="error", jobs=(), error=""),
            dict(result="empty", jobs=(job(),)),
            dict(result="ok", jobs=()),
            dict(result="nonsense", jobs=()),
        ]
        for kw in bad:
            with self.subTest(**{k: v2 for k, v2 in kw.items() if k != "jobs"}, n=len(kw["jobs"])):
                with self.assertRaises(ValueError):
                    ReadResult(validators=v, **kw)
        ReadResult(result="unchanged", jobs=(), validators=v)
        ReadResult(result="error", jobs=(), validators=v, error="boom", http_status=503)
        ReadResult(result="empty", jobs=(), validators=v, http_status=200)
        r = ReadResult(result="ok", jobs=(job(),), validators=Validators(etag="e", ids_fingerprint="f"))
        self.assertEqual(r.validators.etag, "e")

    def test_read_result_board_name_defaults_empty(self):
        r = ReadResult(result="ok", jobs=(job(),), validators=Validators())
        self.assertEqual(r.board_name, "")
        self.assertIsNone(r.http_status)
        self.assertEqual(r.error, "")
        self.assertEqual(ReadResult(result="ok", jobs=(job(),), validators=Validators(), board_name="Acme").board_name,
                         "Acme")

    def test_validators_default_empty(self):
        self.assertEqual((Validators().etag, Validators().ids_fingerprint), ("", ""))

    def test_adapter_protocol_is_structural(self):
        class A:
            ats = "greenhouse"

            def list(self, token, validators):
                return ReadResult(result="empty", jobs=(), validators=validators)

            def body(self, token, j):
                return None

        a: Adapter = A()
        self.assertEqual(a.list("acme", Validators()).result, "empty")


class FingerprintTests(unittest.TestCase):
    def test_ids_fingerprint_is_order_free(self):
        self.assertEqual(ids_fingerprint(["b", "a"]), ids_fingerprint(["a", "b"]))
        self.assertNotEqual(ids_fingerprint(["a"]), ids_fingerprint(["a", "b"]))
        self.assertNotEqual(ids_fingerprint(["a"]), ids_fingerprint(["b"]))

    def test_ids_fingerprint_is_sha256_of_sorted_ids_joined_by_newline(self):
        import hashlib
        self.assertEqual(ids_fingerprint(iter(["b", "a"])), hashlib.sha256(b"a\nb").hexdigest())
        self.assertEqual(len(ids_fingerprint([])), 64)


class TokenTests(unittest.TestCase):
    def test_valid_token_grammar(self):
        self.assertTrue(valid_token("workday", "adobe.wd5/External_Experienced"))
        self.assertTrue(valid_token("oracle", "eeho.fa.us2.oraclecloud.com/CX_1"))
        self.assertTrue(valid_token("icims", "careers-acme.icims.com"))
        self.assertTrue(valid_token("greenhouse", "acme-co"))
        self.assertTrue(valid_token("lever", "acme.io_2"))
        self.assertFalse(valid_token("greenhouse", "acme%20co"))
        self.assertFalse(valid_token("greenhouse", "Acme"))
        self.assertFalse(valid_token("greenhouse", ""))
        self.assertFalse(valid_token("greenhouse", "-acme"))
        self.assertFalse(valid_token("greenhouse", "acme\n"))
        self.assertFalse(valid_token("workday", "adobe/External"))
        self.assertFalse(valid_token("workday", "Adobe.wd5/External"))
        self.assertFalse(valid_token("workday", "adobe.wd5/"))
        self.assertFalse(valid_token("workday", "adobe.wd\u0665/External"))  # an Arabic-Indic digit is not a digit here
        self.assertFalse(valid_token("workday", "adobe.wd/External"))
        self.assertFalse(valid_token("oracle", "eeho.fa.us2.oraclecloud.com"))
        self.assertFalse(valid_token("oracle", "evil.example.com/CX_1"))
        self.assertFalse(valid_token("icims", "careers-acme.example.com"))
        self.assertFalse(valid_token("icims", "Careers.icims.com"))


if __name__ == "__main__":
    unittest.main()


def reading_json(**over):
    """A valid reading object; `over` replaces whole fields as (value, quote)."""
    fields = {
        "engineering": ("yes", "software engineer"),
        "level": ("new_grad", "new grad engineer"),
        "years_low": (0, "no experience needed"),
        "years_high": (None, ""),
        "sponsorship": ("wont_sponsor", "we do not sponsor visas"),
        "citizenship_or_clearance": ("unknown", ""),
        "student_or_grad_window": ("unknown", ""),
        "start_date": ("", ""),
        "location": ("Boston, MA", "boston, ma"),
        "remote": ("no", "onsite in boston"),
        "pay": ("", ""),
    }
    fields.update(over)
    return {"posting_id": 7, "body_sha256": "ab" * 32, "reader_version": "rv-123456789abc",
            "fields": {k: {"value": v, "quote": q} for k, (v, q) in fields.items()}}


class ReadingVocabularyTests(unittest.TestCase):
    def test_schema_version_and_field_order(self):
        self.assertEqual(READING_SCHEMA_VERSION, 1)
        self.assertEqual(FIELDS, ("engineering", "level", "years_low", "years_high", "sponsorship",
                                  "citizenship_or_clearance", "student_or_grad_window", "start_date",
                                  "location", "remote", "pay"))

    def test_allowed_values_verbatim(self):
        self.assertEqual(ALLOWED["level"], ("intern", "new_grad", "early_career", "mid", "senior_plus", "unknown"))
        self.assertEqual(ALLOWED["sponsorship"], ("wont_sponsor", "will_sponsor", "opt_only", "silent", "unknown"))
        for f in ("engineering", "citizenship_or_clearance", "student_or_grad_window", "remote"):
            self.assertEqual(ALLOWED[f], ("yes", "no", "unknown"))
        self.assertEqual(set(ALLOWED), set(FIELDS) - {"years_low", "years_high", "start_date", "location", "pay"})
        self.assertEqual(INT_FIELDS, ("years_low", "years_high"))
        self.assertEqual(TEXT_FIELDS, ("start_date", "location", "pay"))
        for values in ALLOWED.values():
            self.assertTrue(all(isinstance(v, str) for v in values))


class PostingReadingTests(unittest.TestCase):
    def test_posting_reading_round_trip(self):
        obj = reading_json()
        r = PostingReading.from_json(obj)
        self.assertEqual(r.posting_id, 7)
        self.assertEqual(r.reader_version, "rv-123456789abc")
        self.assertEqual(r.fields["sponsorship"], FieldReading("wont_sponsor", "we do not sponsor visas"))
        self.assertEqual(r.fields["years_high"], FieldReading(None, ""))
        self.assertEqual(r.fields["years_low"], FieldReading(0, "no experience needed"))
        self.assertEqual(tuple(r.fields), FIELDS)
        self.assertEqual(r.to_json(), obj)
        self.assertEqual(PostingReading.from_json(r.to_json()), r)

    def test_to_json_does_not_share_state_with_the_reading(self):
        r = PostingReading.from_json(reading_json())
        out = r.to_json()
        out["fields"]["pay"]["value"] = "mutated"
        self.assertEqual(r.fields["pay"].value, "")

    def test_posting_reading_rejects_extra_field(self):
        obj = reading_json()
        obj["fields"]["salary"] = {"value": "1", "quote": "one"}
        with self.assertRaises(ValueError):
            PostingReading.from_json(obj)

    def test_rejects_extra_key_on_a_field_or_the_top_level(self):
        obj = reading_json()
        obj["fields"]["pay"]["confidence"] = 0.9
        with self.assertRaises(ValueError):
            PostingReading.from_json(obj)
        obj = reading_json()
        obj["extra"] = 1
        with self.assertRaises(ValueError):
            PostingReading.from_json(obj)

    def test_rejects_missing_field_and_missing_keys(self):
        for name in FIELDS:
            with self.subTest(missing=name):
                obj = reading_json()
                del obj["fields"][name]
                with self.assertRaises(ValueError):
                    PostingReading.from_json(obj)
        for key in ("posting_id", "body_sha256", "reader_version", "fields"):
            with self.subTest(missing=key):
                obj = reading_json()
                del obj[key]
                with self.assertRaises(ValueError):
                    PostingReading.from_json(obj)
        for part in ("value", "quote"):
            with self.subTest(part=part):
                obj = reading_json()
                del obj["fields"]["pay"][part]
                with self.assertRaises(ValueError):
                    PostingReading.from_json(obj)

    def test_rejects_malformed_shapes(self):
        for bad in (None, [], "x", 3):
            with self.subTest(obj=bad):
                with self.assertRaises(ValueError):
                    PostingReading.from_json(bad)
        for key, bad in (("fields", []), ("fields", None), ("posting_id", "7"), ("posting_id", True),
                         ("body_sha256", ""), ("body_sha256", 5), ("reader_version", ""), ("reader_version", None)):
            with self.subTest(key=key, bad=bad):
                obj = reading_json()
                obj[key] = bad
                with self.assertRaises(ValueError):
                    PostingReading.from_json(obj)
        obj = reading_json()
        obj["fields"]["pay"] = "10 an hour"
        with self.assertRaises(ValueError):
            PostingReading.from_json(obj)

    def test_rejects_a_value_outside_allowed(self):
        cases = {
            "level": "staff", "sponsorship": "maybe", "engineering": "YES", "remote": True,
            "citizenship_or_clearance": "required", "student_or_grad_window": "",
            "years_low": 31, "years_high": -1,
        }
        for name, value in cases.items():
            with self.subTest(field=name, value=value):
                obj = reading_json(**{name: (value, "some quoted text")})
                with self.assertRaises(ValueError):
                    PostingReading.from_json(obj)

    def test_years_must_be_a_real_int_in_range_or_none(self):
        for good in (0, 1, 30, None):
            with self.subTest(good=good):
                quote = "" if good is None else "some quoted text"
                PostingReading.from_json(reading_json(years_low=(good, quote)))
        for bad in (True, False, 2.0, "3", 30.5, 31, -1):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    PostingReading.from_json(reading_json(years_high=(bad, "some quoted text")))

    def test_text_fields_must_be_strings(self):
        for name in ("start_date", "location", "pay"):
            for bad in (None, 5, ["x"]):
                with self.subTest(field=name, bad=bad):
                    with self.assertRaises(ValueError):
                        PostingReading.from_json(reading_json(**{name: (bad, "some quoted text")}))

    def test_posting_reading_needs_quote_for_known_value(self):
        known = {"engineering": "yes", "level": "intern", "years_low": 2, "years_high": 5,
                 "sponsorship": "will_sponsor", "citizenship_or_clearance": "yes",
                 "student_or_grad_window": "no", "start_date": "June 2027", "location": "Austin",
                 "remote": "yes", "pay": "$40 an hour"}
        self.assertEqual(set(known), set(FIELDS))
        for name, value in known.items():
            for quote in ("", "   ", "\n\t"):
                with self.subTest(field=name, quote=quote):
                    with self.assertRaises(ValueError):
                        PostingReading.from_json(reading_json(**{name: (value, quote)}))
            with self.subTest(field=name, quote="a real quote"):
                PostingReading.from_json(reading_json(**{name: (value, "a real quote")}))

    def test_quote_may_be_empty_only_for_unknown_silent_none_or_blank(self):
        for name, value in (("sponsorship", "silent"), ("sponsorship", "unknown"), ("level", "unknown"),
                            ("years_low", None), ("years_high", None), ("pay", ""), ("location", ""),
                            ("start_date", ""), ("remote", "unknown")):
            with self.subTest(field=name, value=value):
                PostingReading.from_json(reading_json(**{name: (value, "")}))

    def test_quote_must_be_a_string(self):
        for bad in (None, 5, ["x"]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    PostingReading.from_json(reading_json(sponsorship=("wont_sponsor", bad)))
                with self.assertRaises(ValueError):
                    PostingReading.from_json(reading_json(sponsorship=("unknown", bad)))


class QuoteInBodyTests(unittest.TestCase):
    BODY = "We do not sponsor visas."

    def test_quote_in_body_collapses_whitespace(self):
        self.assertTrue(quote_in_body("we do  not\nsponsor visas", self.BODY))

    def test_quote_in_body_rejects_paraphrase(self):
        self.assertFalse(quote_in_body("no sponsorship offered", self.BODY))
        self.assertFalse(quote_in_body("we will not sponsor visas", self.BODY))

    def test_quote_in_body_rejects_short_quote(self):
        self.assertFalse(quote_in_body("sponsor", self.BODY))
        self.assertFalse(quote_in_body("not sponsor", self.BODY))
        self.assertFalse(quote_in_body("   not    sponsor   ", self.BODY))

    def test_three_words_pass(self):
        self.assertTrue(quote_in_body("not sponsor visas", self.BODY))

    def test_case_is_ignored_on_both_sides(self):
        self.assertTrue(quote_in_body("WE DO NOT SPONSOR visas", "we Do Not Sponsor VISAS"))

    def test_whitespace_in_the_body_collapses_too(self):
        body = "Sorry:\n\n  we do\tnot \r\n sponsor\u00a0visas"
        self.assertTrue(quote_in_body("we do not sponsor visas", body))
        self.assertTrue(quote_in_body("we do\nnot sponsor\nvisas", body))

    def test_quote_spanning_a_line_break_matches(self):
        body = "Requirements\nUS citizens only\nand no exceptions"
        self.assertTrue(quote_in_body("us citizens only and no exceptions", body))
        self.assertTrue(quote_in_body("requirements us citizens only", body))

    def test_empty_or_blank_quote_never_passes(self):
        for quote in ("", " ", "\n\n", "\t \r\n"):
            with self.subTest(quote=repr(quote)):
                self.assertFalse(quote_in_body(quote, self.BODY))
                self.assertFalse(quote_in_body(quote, ""))
                self.assertFalse(quote_in_body(quote, "   "))

    def test_empty_body_never_passes(self):
        self.assertFalse(quote_in_body("we do not sponsor visas", ""))
        self.assertFalse(quote_in_body("we do not sponsor visas", "  \n "))

    def test_quote_longer_than_the_body_fails(self):
        self.assertFalse(quote_in_body("we do not sponsor visas for anyone at all", self.BODY))
        self.assertFalse(quote_in_body(self.BODY + " Really.", self.BODY))

    def test_quote_equal_to_the_whole_body_passes(self):
        self.assertTrue(quote_in_body(self.BODY, self.BODY))

    def test_punctuation_is_not_ignored(self):
        # Dropping the full stop makes two separate sentences read as one phrase. That must not pass.
        body = "We offer relocation help. Visas are not available to anyone."
        self.assertTrue(quote_in_body("relocation help. visas are not", body))
        self.assertFalse(quote_in_body("relocation help visas are not", body))
        self.assertFalse(quote_in_body("we do not, sponsor visas", self.BODY))

    def test_sentences_joined_only_by_collapse_do_not_match_without_their_punctuation(self):
        body = "We love interns.\n\nSponsorship is not offered.\n\nApply today."
        self.assertFalse(quote_in_body("we love interns sponsorship is not offered", body))
        self.assertTrue(quote_in_body("we love interns. sponsorship is not offered.", body))

    def test_smart_quotes_and_dashes_are_not_folded(self):
        body = "We don\u2019t sponsor visas - ever."
        self.assertTrue(quote_in_body("we don\u2019t sponsor visas", body))
        self.assertFalse(quote_in_body("we don't sponsor visas", body))
        self.assertFalse(quote_in_body("we don\u2019t sponsor visas \u2013 ever", body))
        self.assertFalse(quote_in_body("we don\u2018t sponsor visas", body))

    def test_quote_is_not_stripped_of_punctuation_or_wrapped_quotes(self):
        self.assertFalse(quote_in_body('"we do not sponsor visas"', self.BODY))
        self.assertFalse(quote_in_body("we do not sponsor visas...", self.BODY))

    def test_word_order_matters(self):
        self.assertFalse(quote_in_body("visas sponsor not do we", self.BODY))

    def test_non_text_inputs_never_pass(self):
        self.assertFalse(quote_in_body(None, self.BODY))
        self.assertFalse(quote_in_body("we do not sponsor visas", None))
        self.assertFalse(quote_in_body(["we", "do", "not"], self.BODY))

    def test_unrelated_words_inside_a_longer_text(self):
        body = "Great team. " * 50 + "Candidates must be authorized to work in the US. " + "More text. " * 50
        self.assertTrue(quote_in_body("must be authorized to work in the us.", body))
        self.assertFalse(quote_in_body("must be authorised to work in the us.", body))


class ReadRequestTests(unittest.TestCase):
    def test_read_request_holds_what_the_reader_needs(self):
        r = ReadRequest(posting_id=1, body_sha256="ab" * 32, reader_version="rv-1", title="SWE", body="text")
        self.assertEqual((r.posting_id, r.title, r.body), (1, "SWE", "text"))
        with self.assertRaises(Exception):
            r.title = "other"


def facts(**over):
    base = dict(company_id=1, lanes=("level_role",), best_posting_id=None, best_role_age_days=None,
                everify=None, cap_exempt=None, staffing_risk=None, lca_jr_recent=0, lca_level1_recent=0,
                pay_level=None, size_low=None, size_high=None, stage=None, funded_months_ago=None,
                reachable_person=False)
    base.update(over)
    return CompanyFacts(**base)


class RankingContractTests(unittest.TestCase):
    def test_company_facts_fields_are_exactly_the_contract(self):
        import dataclasses
        self.assertEqual([f.name for f in dataclasses.fields(CompanyFacts)], [
            "company_id", "lanes", "best_posting_id", "best_role_age_days", "everify", "cap_exempt",
            "staffing_risk", "lca_jr_recent", "lca_level1_recent", "pay_level", "size_low", "size_high",
            "stage", "funded_months_ago", "reachable_person"])
        f = facts(lanes=("a", "b"), pay_level=3)
        self.assertEqual((f.company_id, f.lanes, f.pay_level), (1, ("a", "b"), 3))
        with self.assertRaises(Exception):
            f.company_id = 2

    def test_signal_value_bounds(self):
        with self.assertRaises(ValueError):
            SignalValue(1.5, "x")
        with self.assertRaises(ValueError):
            SignalValue(-1.01, "x")
        with self.assertRaises(ValueError):
            SignalValue(float("nan"), "x")
        for ok in (-1.0, 0.0, 0.25, 1.0, 1, -1):
            with self.subTest(value=ok):
                self.assertEqual(SignalValue(ok, "why").value, ok)

    def test_signal_value_keeps_its_reason(self):
        self.assertEqual(SignalValue(0.5, "funded last month").reason, "funded last month")

    def test_signal_protocol_is_structural(self):
        class Funded:
            name = "funded"

            def score(self, f):
                return SignalValue(0.5 if f.funded_months_ago is not None else 0.0, "funding")

        s: Signal = Funded()
        self.assertEqual(s.score(facts(funded_months_ago=3)).value, 0.5)
