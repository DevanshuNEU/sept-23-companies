"""The ATS seed's gate (#192 Task 2.1): one list answer in, a verdict out. No store, no network."""
import unittest

from outreach.crawl.base import JobRef, ReadResult, Validators
from outreach.rules.config import default_config
from outreach.sources.ats_seed import RETRY_DAYS, SEED_ATS, Gate, gate


def job(n, title, location="San Francisco, CA", country=None):
    return JobRef(native_id=str(n), title=title, url=f"https://boards.example/{n}", location=location,
                  country=country, posted_on=None, body=None)


def ok(*jobs, name="Acme"):
    return ReadResult("ok", tuple(jobs), Validators(), http_status=200, board_name=name)


class GateTest(unittest.TestCase):
    def gate(self, result):
        return gate(result, default_config())

    def test_gate_promotes_board_with_us_engineering_posting(self):
        got = self.gate(ok(job(1, "Account Executive"), job(2, "Software Engineer"),
                           job(3, "Backend Engineer", "New York, NY"), job(4, "Backend Engineer", "London, UK")))
        self.assertEqual(got, Gate("promoted", 2, "Acme"))

    def test_gate_reads_the_structured_country(self):
        got = self.gate(ok(job(1, "Software Engineer", "Remote", country="United States")))
        self.assertEqual((got.result, got.open_us_engineering), ("promoted", 1))

    def test_gate_rejects_board_with_only_non_us_engineering(self):
        got = self.gate(ok(job(1, "Software Engineer", "London, UK"), job(2, "Data Engineer", "Bengaluru, India"),
                           job(3, "Software Engineer", "Toronto, Canada", country="CA")))
        self.assertEqual(got, Gate("no_us_engineering", 0, "Acme"))

    def test_gate_rejects_board_with_no_engineering(self):
        got = self.gate(ok(job(1, "Account Executive"), job(2, "Product Designer"), job(3, "Recruiter")))
        self.assertEqual(got, Gate("no_us_engineering", 0, "Acme"))

    def test_gate_does_not_count_an_unplaced_or_off_lane_posting(self):
        got = self.gate(ok(job(1, "Software Engineer", ""), job(2, "Mechanical Engineer", "Austin, TX"),
                           job(3, "Software Engineering Intern", "Austin, TX")))
        self.assertEqual(got.result, "no_us_engineering")

    def test_gate_error_is_error_not_rejection(self):
        got = self.gate(ReadResult("error", (), Validators(), http_status=503, error="HTTP 503"))
        self.assertEqual(got, Gate("error", 0, ""))

    def test_gate_empty_is_dead(self):
        got = self.gate(ReadResult("empty", (), Validators(), http_status=200))
        self.assertEqual(got, Gate("dead", 0, ""))

    def test_gate_unchanged_is_never_a_rejection(self):
        got = self.gate(ReadResult("unchanged", (), Validators()))
        self.assertEqual(got.result, "error")

    def test_constants(self):
        self.assertEqual((SEED_ATS, RETRY_DAYS), (("greenhouse", "ashby"), 30))


if __name__ == "__main__":
    unittest.main()
