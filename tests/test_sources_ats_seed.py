"""The ATS seed (#192): the gate (Task 2.1: one list answer in, a verdict out, no store, no network) and the seed run
(Task 2.2: the stapply Greenhouse and Ashby rows, each gated once, on a scratch store and a fake client)."""
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from outreach.crawl.ats import reading_ats
from outreach.crawl.base import JobRef, ReadResult, Validators
from outreach.crawl.cadence import due_boards
from outreach.crawl.config import load_crawl_config
from outreach.crawl.polite import PoliteClient
from outreach.rules import record_evidence
from outreach.rules.facts import EvidenceDraft
from outreach.rules.config import default_config
from outreach.sources.ats_seed import (RETRY_DAYS, SEED_ATS, Gate, SeedConfig, SeedReport, gate, load_seed_config,
                                       run_seed)
from tests.fakes import FakeClient, json_response, status_response
from tests.storetest import fresh_store, seed_board, seed_company


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

    def test_gate_counts_an_unplaced_posting_as_not_outside_the_us(self):
        for location in ("", "Remote"):
            with self.subTest(location=location):
                got = self.gate(ok(job(1, "Software Engineer", location)))
                self.assertEqual((got.result, got.open_us_engineering), ("promoted", 1))

    def test_gate_does_not_count_an_off_lane_posting(self):
        got = self.gate(ok(job(1, "Mechanical Engineer", "Austin, TX"),
                           job(2, "Software Engineering Intern", "Austin, TX")))
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


NOW = "2026-11-10T06:30:00Z"
GH = "https://boards-api.greenhouse.io/v1/boards/{}/jobs"
GH_EU = "https://boards-api.eu.greenhouse.io/v1/boards/{}/jobs"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/{}?includeCompensation=true"


def days_before(now: str, days: int) -> str:
    return (datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ") - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def gh_answer(*jobs, name="Acme"):
    """A Greenhouse list answer: each job a (title, location) pair."""
    return json_response({"jobs": [{"id": 100 + n, "title": title, "location": {"name": location},
                                    "absolute_url": f"https://boards.greenhouse.io/x/jobs/{100 + n}",
                                    "company_name": name} for n, (title, location) in enumerate(jobs)],
                          "meta": {"total": len(jobs)}})


def ashby_answer(*jobs):
    return json_response({"apiVersion": "1", "jobs": [
        {"id": f"job-{n}", "title": title, "location": location, "jobUrl": f"https://jobs.ashbyhq.com/x/{n}",
         "isListed": True} for n, (title, location) in enumerate(jobs)]})


US_ENG = ("Software Engineer", "New York, NY")
UK_ENG = ("Software Engineer", "London, UK")


class SeedRunTest(unittest.TestCase):
    """`run_seed` over a folder holding the two lists it reads and the two it must never read."""

    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.folder = Path(tempfile.mkdtemp(prefix="outreach-seed-test-"))
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.write("greenhouse", "Acme,acme,https://boards.greenhouse.io/acme\n"
                                 "Beta Labs,betalabs,https://job-boards.greenhouse.io/betalabs\n")
        self.write("ashby", "Gamma,gamma,https://jobs.ashbyhq.com/gamma\n")
        self.write("wellfound", "Wf Co,wfco,https://boards.greenhouse.io/wfco\n")
        self.write("ycombinator", "Yc Co,ycco,https://jobs.ashbyhq.com/ycco\n")
        self.routes = {GH.format("acme"): gh_answer(US_ENG), GH.format("betalabs"): gh_answer(UK_ENG),
                       ASHBY.format("gamma"): ashby_answer(US_ENG)}

    def write(self, name, rows):
        (self.folder / f"{name}.csv").write_text("name,slug,url\n" + rows, encoding="utf-8")

    def run_seed(self, now=NOW, **kw):
        self.client = FakeClient(self.routes)
        polite = PoliteClient(self.client, load_crawl_config(), sleep=lambda s: None)
        report = run_seed(self.conn, polite, folder=self.folder, now=now, **kw)
        self.assertEqual(self.client.unrouted, [])
        return report

    def candidate(self, ats, token):
        return self.conn.execute("SELECT * FROM board_candidate WHERE ats = ? AND token = ?", (ats, token)).fetchone()

    def board(self, ats, token):
        return self.conn.execute("SELECT * FROM board WHERE ats = ? AND token = ?", (ats, token)).fetchone()

    def count(self, sql, *args):
        return self.conn.execute(sql, args).fetchone()[0]

    def test_promotes_rejects_and_reports(self):
        report = self.run_seed()
        self.assertEqual(report, SeedReport(read=3, promoted=2, rejected=1, dead=0, errors=0, known=0, left=0))
        acme = self.candidate("greenhouse", "acme")
        self.assertEqual((acme["gated_at"], acme["gate_result"], acme["open_us_engineering"]), (NOW, "promoted", 1))
        self.assertEqual(acme["board_id"], self.board("greenhouse", "acme")["board_id"])
        self.assertEqual((acme["name"], acme["source"]), ("Acme", "stapply"))
        beta = self.candidate("greenhouse", "betalabs")
        self.assertEqual((beta["gate_result"], beta["open_us_engineering"], beta["board_id"]),
                         ("no_us_engineering", 0, None))
        self.assertEqual(self.board("ashby", "gamma")["found_by"], "stapply_name")
        self.assertEqual(sorted(self.client.requests), sorted(self.routes))

    def test_rejected_and_dead_rows_write_only_their_candidate_row(self):
        self.routes[GH.format("acme")] = gh_answer(("Account Executive", "Austin, TX"))
        self.routes[ASHBY.format("gamma")] = ashby_answer()
        report = self.run_seed()
        self.assertEqual((report.rejected, report.dead, report.promoted), (2, 1, 0))
        self.assertEqual(self.candidate("ashby", "gamma")["gate_result"], "dead")
        for table in ("company", "board", "evidence", "company_alias", "company_source"):
            self.assertEqual(self.count(f"SELECT count(*) FROM {table}"), 0, table)

    def test_known_board_skipped_without_request(self):
        board_id = seed_board(self.conn, seed_company(self.conn, "Acme Inc"), "greenhouse", "acme")
        report = self.run_seed()
        self.assertEqual((report.known, report.promoted), (1, 1))
        self.assertNotIn(GH.format("acme"), self.client.requests)
        acme = self.candidate("greenhouse", "acme")
        self.assertEqual((acme["gated_at"], acme["gate_result"], acme["board_id"], acme["open_us_engineering"]),
                         (NOW, "known", board_id, None))
        self.assertEqual(self.count("SELECT count(*) FROM company"), 2)  # Acme Inc and Gamma: no second Acme

    def test_a_promoted_row_is_known_on_the_next_run_and_keeps_its_verdict(self):
        self.run_seed()
        later = days_before(NOW, -40)
        report = self.run_seed(now=later)
        self.assertEqual((report.known, report.promoted, report.rejected), (2, 0, 1))  # betalabs is due again
        self.assertEqual(self.client.requests, [GH.format("betalabs")])
        acme = self.candidate("greenhouse", "acme")
        self.assertEqual((acme["gated_at"], acme["gate_result"]), (NOW, "promoted"))

    def test_failed_gate_retried_after_30_days(self):
        self.run_seed()
        for days, gated in ((29, False), (31, True)):
            with self.subTest(days=days):
                self.conn.execute("UPDATE board_candidate SET gated_at = ? WHERE token = 'betalabs'",
                                  (days_before(NOW, days),))
                report = self.run_seed()
                self.assertEqual(GH.format("betalabs") in self.client.requests, gated)
                self.assertEqual(report.rejected, int(gated))
                self.assertEqual(self.candidate("greenhouse", "betalabs")["gated_at"],
                                 NOW if gated else days_before(NOW, days))

    def test_error_gate_retried_next_run(self):
        self.routes[GH.format("betalabs")] = status_response(503)
        report = self.run_seed()
        self.assertEqual((report.errors, report.rejected), (1, 0))
        row = self.candidate("greenhouse", "betalabs")
        self.assertEqual((row["gate_result"], row["open_us_engineering"]), ("error", None))
        tomorrow = days_before(NOW, -1)
        self.routes[GH.format("betalabs")] = gh_answer(US_ENG, name="Beta Labs")
        report = self.run_seed(now=tomorrow)
        self.assertEqual(self.client.requests, [GH.format("betalabs")])
        self.assertEqual((report.promoted, report.errors), (1, 0))
        self.assertEqual(self.candidate("greenhouse", "betalabs")["gate_result"], "promoted")

    def test_a_404_on_both_greenhouse_hosts_is_an_error_not_a_rejection(self):
        self.routes[GH.format("betalabs")] = status_response(404)
        self.routes[GH_EU.format("betalabs")] = status_response(404)
        report = self.run_seed()
        self.assertEqual((report.errors, report.rejected), (1, 0))

    def test_excluded_lists_never_read(self):
        self.run_seed()
        tokens = {r["token"] for r in self.conn.execute("SELECT token FROM board_candidate")}
        self.assertEqual(tokens, {"acme", "betalabs", "gamma"})
        self.assertFalse([u for u in self.client.requests if "wfco" in u or "ycco" in u])
        for ats in (("wellfound",), ("ycombinator",), ("lever",)):
            with self.subTest(ats=ats), self.assertRaises(ValueError):
                self.run_seed(ats=ats)

    def test_ats_picks_one_list(self):
        report = self.run_seed(ats=("ashby",))
        self.assertEqual((report.read, report.promoted), (1, 1))
        self.assertEqual(self.client.requests, [ASHBY.format("gamma")])

    def test_promoted_board_joins_existing_company_by_domain(self):
        self.write("greenhouse", "Acme Robotics,acme,https://acme.com/careers\n")
        company = seed_company(self.conn, "Acme Corporation")
        record_evidence(self.conn, company, [EvidenceDraft("domain", "acme.com", "confirmed", "website")],
                        actor="source")
        report = self.run_seed(ats=("greenhouse",))
        self.assertEqual(report.promoted, 1)
        self.assertEqual(self.board("greenhouse", "acme")["company_id"], company)
        self.assertEqual(self.count("SELECT count(*) FROM company"), 1)
        self.assertEqual(self.candidate("greenhouse", "acme")["domain"], "acme.com")

    def test_two_rows_of_one_domain_make_one_company(self):
        self.write("greenhouse", "Acme,acme,https://acme.com/careers\nAcme Labs,acmelabs,https://jobs.acme.com/\n")
        self.routes[GH.format("acmelabs")] = gh_answer(US_ENG)
        report = self.run_seed(ats=("greenhouse",))
        self.assertEqual(report.promoted, 2)
        self.assertEqual(self.count("SELECT count(DISTINCT company_id) FROM board"), 1)

    def test_a_new_company_is_made_with_the_list_name(self):
        self.run_seed()
        company = self.conn.execute("SELECT c.* FROM company c JOIN board b USING (company_id) "
                                    "WHERE b.token = 'gamma'").fetchone()
        self.assertEqual((company["name"], company["status"]), ("Gamma", "candidate"))
        self.assertEqual(self.count("SELECT count(*) FROM company_source WHERE source_name = 'stapply' "
                                    "AND source_ref = 'ashby:gamma'"), 1)

    def test_promoted_board_is_read_on_next_tick(self):
        self.run_seed()
        board = self.board("ashby", "gamma")
        self.assertEqual((board["next_read_at"], board["last_read"], board["etag"]), (None, None, ""))
        cfg = load_crawl_config()
        due = due_boards(self.conn, NOW, supported=reading_ats(cfg), plan_ids=(), cfg=cfg, limit=cfg.boards_per_tick)
        self.assertEqual({(b.ats, b.token) for b in due}, {("greenhouse", "acme"), ("ashby", "gamma")})

    def test_dry_run_writes_nothing(self):
        seed_board(self.conn, seed_company(self.conn, "Acme Inc"), "greenhouse", "acme")
        before = self.conn.total_changes
        report = self.run_seed(dry_run=True)
        self.assertEqual(report, SeedReport(read=3, promoted=0, rejected=0, dead=0, errors=0, known=1, left=2))
        self.assertEqual(self.client.requests, [])
        self.assertEqual(self.conn.total_changes, before)

    def test_limit_leaves_the_rest_ungated_and_the_next_run_gates_them(self):
        report = self.run_seed(limit=1)
        self.assertEqual((report.promoted + report.rejected, report.left), (1, 2))
        self.assertEqual(len(self.client.requests), 1)
        ungated = [r for r in self.conn.execute("SELECT * FROM board_candidate") if r["gated_at"] is None]
        self.assertEqual(len(ungated), 2)
        self.assertTrue(all(r["gate_result"] is None for r in ungated))
        report = self.run_seed(limit=5)
        self.assertEqual((report.left, len(self.client.requests)), (0, 2))
        for row in ungated:
            got = self.conn.execute("SELECT gated_at, gate_result FROM board_candidate WHERE candidate_id = ?",
                                    (row["candidate_id"],)).fetchone()
            self.assertEqual(got["gated_at"], NOW)
            self.assertIn(got["gate_result"], ("promoted", "no_us_engineering"))

    def test_a_tripped_bucket_ends_that_ats_and_counts_left(self):
        self.write("greenhouse", "".join(f"Co {n},co{n},https://boards.greenhouse.io/co{n}\n" for n in range(4)))
        self.routes = {GH.format("co0"): status_response(429), ASHBY.format("gamma"): ashby_answer(US_ENG)}
        report = self.run_seed()
        self.assertEqual((report.left, report.errors, report.rejected, report.promoted), (3, 1, 0, 1))
        self.assertEqual(report.tripped, ("greenhouse.io",))
        self.assertEqual([u for u in self.client.requests if "greenhouse" in u], [GH.format("co0")])
        self.assertEqual(self.count("SELECT count(*) FROM board_candidate WHERE gated_at IS NULL"), 3)

    def test_workers_list_on_threads_and_write_the_same(self):
        report = self.run_seed(workers=load_crawl_config().workers)
        self.assertEqual(report, SeedReport(read=3, promoted=2, rejected=1, dead=0, errors=0, known=0, left=0))

    def test_every_run_records_an_event_and_a_dry_run_none(self):
        self.run_seed(dry_run=True)
        self.run_seed(limit=1)
        events = self.conn.execute("SELECT * FROM event WHERE entity = 'source' AND kind = 'ats_seed_run'").fetchall()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["at"], NOW)
        self.assertIn('"limit": 1', events[0]["detail"])

    def test_a_missing_list_is_an_error(self):
        (self.folder / "ashby.csv").unlink()
        with self.assertRaises(ValueError) as caught:
            self.run_seed()
        self.assertIn("fetch stapply", str(caught.exception))
        self.assertEqual(self.client.requests, [])

    def test_an_open_transaction_is_refused(self):
        self.conn.execute("BEGIN")
        self.addCleanup(self.conn.execute, "ROLLBACK")
        with self.assertRaises(ValueError):
            self.run_seed()


class SeedConfigTest(unittest.TestCase):
    def test_shipped_values(self):
        self.assertEqual(load_seed_config(), SeedConfig(enabled=True, weekly_limit=3000, retry_days=30))

    def test_bad_values_are_refused(self):
        folder = Path(tempfile.mkdtemp(prefix="outreach-seed-cfg-"))
        self.addCleanup(shutil.rmtree, folder, True)
        for text in ("enabled = true\nweekly_limit = 0\nretry_days = 30\n",
                     "enabled = true\nweekly_limit = 3000\nretry_days = 0\n",
                     "enabled = 1\nweekly_limit = 3000\nretry_days = 30\n",
                     "enabled = true\nweekly_limit = 3000\n"):
            with self.subTest(text=text):
                path = folder / "seed.toml"
                path.write_text(text, encoding="utf-8")
                with self.assertRaises((ValueError, KeyError)):
                    load_seed_config(path)


if __name__ == "__main__":
    unittest.main()
