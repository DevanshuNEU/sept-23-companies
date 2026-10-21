"""Tasks 2 and 3 of #30: the source, crawl, reader and ranking contracts every stream imports."""
import json
import random
import unittest
from types import SimpleNamespace

from outreach.crawl.base import Adapter, JobRef, ReadResult, Validators, ids_fingerprint, valid_token
from outreach.ranking.types import CompanyFacts, Signal, SignalValue
from outreach.reader.types import (ALLOWED, FIELDS, INT_FIELDS, READING_SCHEMA_VERSION, TEXT_FIELDS, FieldReading,
                                   PostingReading, ReadRequest, check_value, quote_in_body)
from outreach.rules.rebuild import EvidenceDraft
from outreach.sources.base import Candidate, EvidenceDraft as ReExported, Source
from tests.contracts import check_adapter, check_signal, check_source
from tests.fakes import FakeClient, json_response, status_response


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
        self.assertTrue(valid_token("oracle_orc", "eeho.fa.us2.oraclecloud.com/CX_1"))
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
        self.assertFalse(valid_token("oracle_orc", "eeho.fa.us2.oraclecloud.com"))
        self.assertFalse(valid_token("oracle_orc", "evil.example.com/CX_1"))
        self.assertFalse(valid_token("icims", "careers-acme.example.com"))
        self.assertFalse(valid_token("icims", "Careers.icims.com"))

    def test_oracle_is_keyed_on_the_board_ats_name(self):
        self.assertTrue(valid_token("oracle_orc", "eeho.fa.us2.oraclecloud.com/CX_1"))
        self.assertFalse(valid_token("oracle_orc", "cx_1"))
        self.assertFalse(valid_token("oracle_orc", "evil.example.com/CX_1"))

    def test_an_ats_name_outside_the_board_vocabulary_is_refused(self):
        self.assertFalse(valid_token("oracle", "eeho.fa.us2.oraclecloud.com/CX_1"))
        self.assertFalse(valid_token("oracle", "cx_1"))
        self.assertFalse(valid_token("Greenhouse", "acme"))
        self.assertFalse(valid_token("", "acme"))
        self.assertFalse(valid_token("nonsense", "acme"))

    def test_the_ats_list_matches_the_board_check_in_the_schema(self):
        import re
        from pathlib import Path
        from outreach.crawl import base
        sql = (Path(base.__file__).resolve().parents[1] / "store" / "migrations" / "0001_initial.sql").read_text()
        block = re.search(r"CREATE TABLE board \(.*?ats\s+TEXT NOT NULL CHECK \(ats IN \((.*?)\)\)", sql, re.S)
        names = set(re.findall(r"'([a-z_]+)'", block.group(1)))
        self.assertEqual(set(base.BOARD_ATS), names)
        self.assertEqual(set(base.BOARD_ATS) - set(base._GRAMMAR), names - set(base._GRAMMAR))
        for name in names:
            self.assertIsInstance(valid_token(name, "x"), bool)


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

    def test_min_words_lowers_the_floor_for_short_exact_values(self):
        body = "Software Engineer\nAustin, TX\n$120,000 - $150,000 a year. Remote."
        self.assertFalse(quote_in_body("Austin, TX", body))
        self.assertTrue(quote_in_body("Austin, TX", body, min_words=1))
        self.assertTrue(quote_in_body("Remote", body, min_words=1))
        self.assertTrue(quote_in_body("$120,000 - $150,000", body, min_words=1))
        self.assertFalse(quote_in_body("Remote", body))
        self.assertFalse(quote_in_body("", body, min_words=1))  # no words is still no quote
        self.assertFalse(quote_in_body(". . .", body, min_words=1))
        self.assertFalse(quote_in_body("Boston, MA", body, min_words=1))  # presence is still required
        self.assertFalse(quote_in_body("Aust", body, min_words=1))  # and a word edge

    def test_min_words_can_raise_the_floor_and_must_be_at_least_one(self):
        self.assertTrue(quote_in_body("not sponsor visas", self.BODY, min_words=3))
        self.assertFalse(quote_in_body("not sponsor visas", self.BODY, min_words=4))
        for bad in (0, -1):
            with self.subTest(min_words=bad):
                with self.assertRaises(ValueError):
                    quote_in_body("not sponsor visas", self.BODY, min_words=bad)

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


class QuoteBoundaryTests(unittest.TestCase):
    def test_quote_must_start_and_end_on_a_word_edge(self):
        self.assertFalse(quote_in_body("able to sponsor visas", "We are unable to sponsor visas."))
        self.assertFalse(quote_in_body("not sponsor visa", "We do not sponsor visas."))
        self.assertFalse(quote_in_body("ot sponsor visas", "We do not sponsor visas."))
        self.assertTrue(quote_in_body("unable to sponsor visas", "We are unable to sponsor visas."))

    def test_a_hyphen_or_apostrophe_between_letters_joins_words(self):
        self.assertFalse(quote_in_body("us citizens welcome", "Non-US citizens welcome to apply."))
        self.assertTrue(quote_in_body("non-us citizens welcome", "Non-US citizens welcome to apply."))
        self.assertTrue(quote_in_body("us citizens welcome", "Open to all. US citizens welcome to apply."))
        self.assertFalse(quote_in_body("we offer full time", "We offer full time-off and more"))
        self.assertFalse(quote_in_body("they don at all", "they don\u2019t at all"))

    def test_other_hyphen_characters_between_letters_join_words(self):
        # U+2010 hyphen, U+2011 non-breaking hyphen, U+2012 figure dash, U+2013 en dash, U+00AD soft hyphen.
        for joiner in ("\u2010", "\u2011", "\u2012", "\u2013", "\u00ad"):
            with self.subTest(joiner=repr(joiner)):
                self.assertFalse(quote_in_body("able to sponsor", f"We are un{joiner}able to sponsor visas."))
                self.assertFalse(quote_in_body("sponsor visas for", f"We sponsor visas for{joiner}now."))
                self.assertTrue(quote_in_body("able to sponsor", f"We are able to sponsor{joiner} visas."))

    def test_full_word_quotes_and_line_breaks_still_pass(self):
        self.assertTrue(quote_in_body("we do not sponsor visas", "We do not sponsor visas."))
        self.assertTrue(quote_in_body("we do not sponsor visas", "Note: we do not sponsor visas, sorry."))
        self.assertTrue(quote_in_body("we do  not\nsponsor visas", "Hi\nWe do not\n\nsponsor visas\n"))
        self.assertTrue(quote_in_body("not sponsor visas.", "We do not sponsor visas."))

    def test_a_later_whole_word_occurrence_is_found_after_an_earlier_partial_one(self):
        self.assertTrue(quote_in_body("able to sponsor visas", "We are unable to sponsor visas. Some are able to sponsor visas."))

    def test_regex_characters_in_a_quote_are_literal(self):
        self.assertTrue(quote_in_body("pay is $40+ per hour (usd)", "Pay is $40+ per hour (USD) plus equity"))
        self.assertFalse(quote_in_body("pay is .* per hour", "Pay is $40 per hour"))

    def test_the_word_gate_counts_only_tokens_with_a_word_character(self):
        self.assertFalse(quote_in_body(". . .", "Wait . . . what"))
        self.assertFalse(quote_in_body("- - -", "Rule - - - end"))
        self.assertFalse(quote_in_body("a - -", "a - - b"))
        self.assertFalse(quote_in_body("must --- do", "we must --- do it"))
        self.assertTrue(quote_in_body("to be a", "Ready to be a part of it"))
        self.assertTrue(quote_in_body("to - be a", "Ready to - be a part"))


class CheckValueTests(unittest.TestCase):
    def test_check_value_validates_all_eleven_fields(self):
        check_value("level", "intern")
        check_value("years_low", None)
        check_value("years_high", 30)
        check_value("pay", "")
        check_value("location", "Boston")
        for name, bad in (("level", "wizard"), ("years_low", 31), ("years_high", True), ("pay", None),
                          ("remote", "maybe")):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    check_value(name, bad)
        with self.assertRaises(ValueError):
            check_value("salary", "x")

    def test_every_field_name_is_known_to_check_value(self):
        for name in FIELDS:
            check_value(name, "" if name in TEXT_FIELDS else None if name in INT_FIELDS else "unknown")


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



class FixedSource:
    name = "fixed"
    version = "1"

    def discover(self, since):
        yield Candidate(name="Acme", domain="acme.example", keys={"fein": "12-3456789"})
        yield Candidate(name="Orchard", domain=None, keys={})


class RandomNameSource(FixedSource):
    def discover(self, since):
        yield Candidate(name=f"Acme {random.random()}", domain=None, keys={})


class CheckSourceTests(unittest.TestCase):
    def test_check_source_returns_the_candidates(self):
        got = check_source(self, FixedSource())
        self.assertEqual([c.name for c in got], ["Acme", "Orchard"])

    def test_check_source_passes_since_through(self):
        seen = []

        class S(FixedSource):
            def discover(self, since):
                seen.append(since)
                return super().discover(since)

        check_source(self, S(), since="2026-10-01")
        self.assertEqual(seen, ["2026-10-01", "2026-10-01"])

    def test_check_source_flags_nondeterminism(self):
        with self.assertRaises(AssertionError):
            check_source(self, RandomNameSource())

    def test_check_source_needs_a_name_and_a_version(self):
        for field in ("name", "version"):
            with self.subTest(field=field):
                class S(FixedSource):
                    pass

                setattr(S, field, "")
                with self.assertRaises(AssertionError):
                    check_source(self, S())

    def test_check_source_flags_an_item_that_is_not_a_candidate(self):
        class S(FixedSource):
            def discover(self, since):
                yield {"name": "Acme"}

        with self.assertRaises(AssertionError):
            check_source(self, S())


BOARD_URL = "https://boards.example/api/{token}/jobs"
LIST_BODY = {"jobs": [{"id": 1, "title": "Engineer", "absolute_url": "https://boards.example/1",
                       "location": {"name": "Boston, MA"}}]}


class EtagAdapter:
    """A toy adapter wired to a client: sends the stored ETag and treats a 304 as unchanged."""
    ats = "greenhouse"

    def __init__(self, client):
        self.client = client

    def list(self, token, validators):
        headers = {"If-None-Match": validators.etag} if validators.etag else {}
        r = self.client.get(BOARD_URL.format(token=token), headers)
        if r.status == 304:
            return ReadResult("unchanged", (), validators, 304)
        if r.status != 200:
            return ReadResult("error", (), validators, r.status, error=f"HTTP {r.status}")
        jobs = tuple(JobRef(str(j["id"]), j["title"], j["absolute_url"], j["location"]["name"], None, None, None)
                     for j in json.loads(r.body)["jobs"])
        etag = r.headers.get("etag", "")
        if not jobs:
            return ReadResult("empty", (), Validators(etag=etag), 200)
        return ReadResult("ok", jobs, Validators(etag, ids_fingerprint(j.native_id for j in jobs)), 200)

    def body(self, token, job):
        return None


class BlindAdapter(EtagAdapter):
    """Ignores the validators it is given, so a second read of the same board is never unchanged."""

    def list(self, token, validators):
        return super().list(token, Validators())


class SwallowingAdapter(EtagAdapter):
    """Catches every exception, so a request nobody routed shows up as a plain error read."""

    def list(self, token, validators):
        try:
            return super().list(token, validators)
        except Exception as err:
            return ReadResult("error", (), validators, None, error=str(err))


def scripted_client(url, *answers):
    return FakeClient({url: list(answers)})


class CheckAdapterTests(unittest.TestCase):
    url = BOARD_URL.format(token="acme")

    def test_check_adapter_passes_an_etag_adapter(self):
        client = scripted_client(self.url, json_response(LIST_BODY, etag='"v1"'), status_response(304),
                                 status_response(500))
        check_adapter(self, EtagAdapter(client), "acme")
        self.assertEqual(client.calls[1][2], {"If-None-Match": '"v1"'})

    def test_check_adapter_passes_an_empty_first_read(self):
        client = scripted_client(self.url, json_response({"jobs": []}, etag='"e"'), status_response(304),
                                 status_response(500))
        check_adapter(self, EtagAdapter(client), "acme")

    def test_check_adapter_passes_a_fingerprint_adapter_whose_second_read_has_the_same_ids(self):
        class Fingerprint(EtagAdapter):
            def list(self, token, validators):
                got = super().list(token, Validators())
                if got.result == "ok" and got.validators.ids_fingerprint == validators.ids_fingerprint:
                    return ReadResult("unchanged", (), validators, 200)
                return got

        client = scripted_client(self.url, json_response(LIST_BODY), json_response(LIST_BODY), status_response(500))
        check_adapter(self, Fingerprint(client), "acme")

    def test_check_adapter_needs_unchanged(self):
        client = scripted_client(self.url, json_response(LIST_BODY, etag='"v1"'), json_response(LIST_BODY),
                                 status_response(500))
        with self.assertRaises(AssertionError):
            check_adapter(self, BlindAdapter(client), "acme")

    def test_check_adapter_fails_a_blind_adapter_that_handles_a_304(self):
        client = scripted_client(self.url, json_response(LIST_BODY, etag='"v1"'), status_response(304),
                                 status_response(500))
        with self.assertRaises(AssertionError) as caught:
            check_adapter(self, BlindAdapter(client), "acme")
        self.assertIn("If-None-Match", str(caught.exception))

    def test_check_adapter_fails_an_ok_read_that_returns_no_validators(self):
        class NoValidators(EtagAdapter):
            def list(self, token, validators):
                got = super().list(token, validators)
                return ReadResult(got.result, got.jobs, Validators(), got.http_status, got.error)

        client = scripted_client(self.url, json_response(LIST_BODY, etag='"v1"'), status_response(304),
                                 status_response(500))
        with self.assertRaises(AssertionError):
            check_adapter(self, NoValidators(client), "acme")

    def test_check_adapter_accepts_if_modified_since(self):
        class Modified(EtagAdapter):
            def list(self, token, validators):
                headers = {"If-Modified-Since": "Tue, 20 Oct 2026 00:00:00 GMT"} if validators.etag else {}
                r = self.client.get(BOARD_URL.format(token=token), headers)
                if r.status == 304:
                    return ReadResult("unchanged", (), validators, 304)
                if r.status != 200:
                    return ReadResult("error", (), validators, r.status, error=f"HTTP {r.status}")
                return ReadResult("ok", (JobRef("1", "Engineer", "https://boards.example/1", "Boston, MA", None,
                                                None, None),), Validators(etag="lm1"), 200)

        client = scripted_client(self.url, json_response(LIST_BODY), status_response(304), status_response(500))
        check_adapter(self, Modified(client), "acme")

    def test_check_adapter_fails_when_no_fake_client_is_reachable(self):
        class Hidden(EtagAdapter):
            def __init__(self, client):
                self._client = client

            def list(self, token, validators):
                self.client = self._client  # only set while reading, so it is not reachable up front
                try:
                    return super().list(token, validators)
                finally:
                    del self.client

        client = scripted_client(self.url, json_response(LIST_BODY, etag='"v1"'), status_response(304),
                                 status_response(500))
        with self.assertRaises(AssertionError) as caught:
            check_adapter(self, Hidden(client), "acme")
        self.assertIn("client=", str(caught.exception))

    def test_check_adapter_fails_when_the_client_has_no_unrouted_list(self):
        client = scripted_client(self.url, json_response(LIST_BODY, etag='"v1"'), status_response(304),
                                 status_response(500))
        with self.assertRaises(AssertionError):
            check_adapter(self, EtagAdapter(client), "acme", client=SimpleNamespace(calls=client.calls))

    def test_check_adapter_needs_an_error_for_a_500(self):
        client = scripted_client(self.url, json_response(LIST_BODY, etag='"v1"'), status_response(304),
                                 json_response(LIST_BODY))
        with self.assertRaises(AssertionError):
            check_adapter(self, EtagAdapter(client), "acme")

    def test_check_adapter_fails_on_an_unrouted_request(self):
        client = scripted_client(self.url, json_response(LIST_BODY, etag='"v1"'), status_response(304))
        with self.assertRaises(AssertionError) as caught:
            check_adapter(self, SwallowingAdapter(client), "acme")
        self.assertIn("unrouted", str(caught.exception))

    def test_check_adapter_checks_the_client_it_is_given(self):
        client = scripted_client(self.url, json_response(LIST_BODY, etag='"v1"'), status_response(304),
                                 status_response(500))
        other = FakeClient({})
        other.unrouted.append("https://elsewhere.example/")
        with self.assertRaises(AssertionError):
            check_adapter(self, EtagAdapter(client), "acme", client=other)


class Funded:
    name = "funded"

    def __init__(self, value=0.5, reason="raised money last month"):
        self.result = SignalValue(value, reason)
        self.raw = None

    def score(self, f):
        return self.raw if self.raw is not None else self.result


class CheckSignalTests(unittest.TestCase):
    def test_check_signal_returns_the_value(self):
        got = check_signal(self, Funded(), facts())
        self.assertEqual((got.value, got.reason), (0.5, "raised money last month"))

    def test_check_signal_flags_an_empty_reason(self):
        for reason in ("", "   "):
            with self.subTest(reason=reason):
                with self.assertRaises(AssertionError):
                    check_signal(self, Funded(reason=reason), facts())

    def test_check_signal_flags_an_em_or_en_dash(self):
        for dash in (chr(0x2014), chr(0x2013)):
            with self.subTest(dash=hex(ord(dash))):
                with self.assertRaises(AssertionError):
                    check_signal(self, Funded(reason=f"new grad {dash} live role"), facts())

    def test_check_signal_flags_a_value_out_of_range_or_not_a_signal_value(self):
        for raw in (SimpleNamespace(value=2.0, reason="why"), SimpleNamespace(value=-1.5, reason="why"), (0.5, "why")):
            with self.subTest(raw=raw):
                s = Funded()
                s.raw = raw
                with self.assertRaises(AssertionError):
                    check_signal(self, s, facts())

    def test_check_signal_needs_a_name(self):
        s = Funded()
        s.name = ""
        with self.assertRaises(AssertionError):
            check_signal(self, s, facts())


if __name__ == "__main__":
    unittest.main()
