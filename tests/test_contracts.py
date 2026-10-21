"""Task 2 of #30: the source and crawl contracts every stream imports."""
import unittest

from outreach.crawl.base import Adapter, JobRef, ReadResult, Validators, ids_fingerprint, valid_token
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
