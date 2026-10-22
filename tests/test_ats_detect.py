"""ATS detection (#31 Task 10): which boards a careers page points at, as BoardRef values."""
import re
import unittest
from pathlib import Path

from outreach.crawl import base
from outreach.sources.ats_detect import detect_ats, detect_unsupported
from outreach.sources.boards import SUPPORTED_ATS, BoardRef


def refs(text):
    return [(r.ats, r.token) for r in detect_ats(text)]


class DetectAtsTest(unittest.TestCase):
    def test_greenhouse_embed_and_board_urls(self):
        self.assertEqual(refs('<a href="https://boards.greenhouse.io/acme/jobs/123">Apply</a>'), [("greenhouse", "acme")])
        self.assertEqual(refs('<a href="https://job-boards.greenhouse.io/acme-co">Jobs</a>'), [("greenhouse", "acme-co")])
        self.assertEqual(refs('<a href="https://job-boards.eu.greenhouse.io/euco">Jobs</a>'), [("greenhouse", "euco")])
        self.assertEqual(
            refs('<script src="https://boards.greenhouse.io/embed/job_board/js?for=embedco"></script>'),
            [("greenhouse", "embedco")])
        self.assertEqual(
            refs('<iframe src="https://boards.greenhouse.io/embed/job_board?for=Frameco&amp;b=x"></iframe>'),
            [("greenhouse", "frameco")])

    def test_lever(self):
        self.assertEqual(refs('<a href="https://jobs.lever.co/acme/0f1e2d3c">Engineer</a>'), [("lever", "acme")])
        self.assertEqual(refs('<a href="https://jobs.eu.lever.co/eucompany">Jobs</a>'), [("lever", "eucompany")])

    def test_ashby(self):
        self.assertEqual(refs('<a href="https://jobs.ashbyhq.com/Acme-Labs/abc">Engineer</a>'),
                         [("ashby", "acme-labs")])

    def test_ashby_slug_with_percent_is_not_a_board(self):
        # no valid token: never a board row and never a truncated guess
        self.assertEqual(refs('<a href="https://jobs.ashbyhq.com/acme%20labs">Jobs</a>'), [])

    def test_workday_token_is_tenant_wd_n_slash_site(self):
        self.assertEqual(refs('<a href="https://riverside.wd5.myworkdayjobs.com/en-US/External">Jobs</a>'),
                         [("workday", "riverside.wd5/External")])
        self.assertEqual(refs('<a href="https://Riverside.WD5.myworkdayjobs.com/External/job/Boston/x_R1">x</a>'),
                         [("workday", "riverside.wd5/External")])
        self.assertEqual(refs("see https://acme.wd1.myworkdayjobs.com/Acme_Careers?q=eng"),
                         [("workday", "acme.wd1/Acme_Careers")])

    def test_workday_api_shape_gives_the_site_not_wday(self):
        self.assertEqual(refs("https://acme.wd3.myworkdayjobs.com/wday/cxs/acme/Careers_Site/jobs"),
                         [("workday", "acme.wd3/Careers_Site")])

    def test_workday_host_with_no_site_is_not_a_board(self):
        self.assertEqual(refs("https://acme.wd3.myworkdayjobs.com/"), [])
        self.assertEqual(refs("https://acme.wd3.myworkdayjobs.com/en-US"), [])

    def test_oracle_and_icims_tokens(self):
        html = ('<a href="https://ebwg.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/jobs">Jobs</a>'
                '<a href="https://careers-Acme.icims.com/jobs/search">More</a>')
        self.assertEqual(refs(html), [("oracle_orc", "ebwg.fa.us2.oraclecloud.com/CX"),
                                      ("icims", "careers-acme.icims.com")])
        self.assertEqual(refs("https://EEHO.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en-US/sites/CX_1001"),
                         [("oracle_orc", "eeho.fa.us2.oraclecloud.com/CX_1001")])

    def test_icims_marketing_hosts_are_ignored(self):
        self.assertEqual(refs('<a href="https://www.icims.com/products">iCIMS</a> https://api.icims.com/x'), [])

    def test_rippling(self):
        self.assertEqual(refs('<a href="https://ats.rippling.com/acme-inc/jobs">Jobs</a>'),
                         [("rippling", "acme-inc")])

    def test_the_rest_of_the_supported_hosts(self):
        cases = {
            "https://apply.workable.com/acme/j/ABC123/": ("workable", "acme"),
            "https://jobs.smartrecruiters.com/AcmeCorp/743": ("smartrecruiters", "acmecorp"),
            "https://careers.smartrecruiters.com/acmecorp": ("smartrecruiters", "acmecorp"),
            "https://acme.bamboohr.com/careers": ("bamboohr", "acme"),
            "https://acme.applytojob.com/apply": ("jazzhr", "acme"),
            "https://acme.breezy.hr/p/123": ("breezy", "acme"),
            "https://acme.recruitee.com/o/engineer": ("recruitee", "acme"),
            "https://acme.teamtailor.com/jobs": ("teamtailor", "acme"),
            "https://acme.pinpointhq.com/en": ("pinpoint", "acme"),
            "https://jobs.gem.com/acme": ("gem", "acme"),
        }
        for url, want in cases.items():
            with self.subTest(url=url):
                self.assertEqual(refs(f'<a href="{url}">x</a>'), [want])

    def test_generic_tokens_are_ignored(self):
        self.assertEqual(refs("https://boards.greenhouse.io/embed"), [])
        self.assertEqual(refs('<script src="https://boards.greenhouse.io/embed/job_board/js"></script>'), [])
        self.assertEqual(refs("https://www.bamboohr.com/ https://api.bamboohr.com/x https://static.breezy.hr/a"), [])
        self.assertEqual(refs("https://jobs.lever.co/api https://jobs.lever.co/widget"), [])
        self.assertEqual(refs("https://careers.smartrecruiters.com/jobs https://jobs.ashbyhq.com/assets"), [])
        self.assertEqual(refs("https://jobs.lever.co/v1"), [])

    def test_first_seen_order_without_duplicates(self):
        html = ('<a href="https://jobs.lever.co/acme">a</a><a href="https://boards.greenhouse.io/beta">b</a>'
                '<a href="https://jobs.lever.co/Acme/xyz">again</a>')
        self.assertEqual(refs(html), [("lever", "acme"), ("greenhouse", "beta")])
        # the same text read twice gives the same refs
        self.assertEqual(refs(html), refs(html))

    def test_cap_of_three(self):
        html = " ".join(f"https://jobs.lever.co/co{i}" for i in range(6))
        self.assertEqual(refs(html), [("lever", "co0"), ("lever", "co1"), ("lever", "co2")])

    def test_a_trailing_dot_in_prose_is_not_part_of_the_token(self):
        self.assertEqual(refs("Apply at https://jobs.lever.co/acme."), [("lever", "acme")])

    def test_no_text_no_boards(self):
        self.assertEqual(detect_ats(""), [])
        self.assertEqual(detect_ats("<p>We are hiring, email us.</p>"), [])

    def test_every_detected_ats_is_a_supported_board_ats(self):
        text = (" https://boards.greenhouse.io/a https://jobs.lever.co/a https://jobs.ashbyhq.com/a"
                " https://apply.workable.com/a https://jobs.smartrecruiters.com/a https://ats.rippling.com/a"
                " https://a.bamboohr.com https://a.applytojob.com https://a.breezy.hr https://a.recruitee.com"
                " https://a.teamtailor.com https://a.pinpointhq.com https://jobs.gem.com/a")
        names = set()
        for chunk in text.split():
            names.update(r.ats for r in detect_ats(chunk))
        self.assertEqual(len(names), 13)
        self.assertLessEqual(names, SUPPORTED_ATS)


class DetectUnsupportedTest(unittest.TestCase):
    def test_unsupported_hosts_are_reported_not_returned(self):
        html = ('<a href="https://jobs.jobvite.com/acme/jobs">a</a>'
                '<a href="https://recruiting.paylocity.com/recruiting/jobs/All/abc/Acme">b</a>'
                '<a href="https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=1">c</a>'
                '<a href="https://recruiting.ultipro.com/ACM1000/JobBoard/x">d</a>'
                '<a href="https://acme.ukg.net/careers">e</a>'
                '<a href="https://acme.notion.site/Careers-abc">f</a>'
                '<a href="https://www.linkedin.com/company/acme/jobs">g</a>')
        self.assertEqual(refs(html), [])
        self.assertEqual(detect_unsupported(html), [
            "jobs.jobvite.com", "recruiting.paylocity.com", "workforcenow.adp.com", "ultipro.com", "ukg.net",
            "notion.site", "linkedin.com"])

    def test_deduplicated_in_first_seen_order(self):
        html = ("https://acme.notion.site/a https://jobs.jobvite.com/x https://other.notion.site/b "
                "https://jobs.jobvite.com/y")
        self.assertEqual(detect_unsupported(html), ["notion.site", "jobs.jobvite.com"])

    def test_a_supported_board_beside_an_unsupported_one(self):
        html = "https://jobs.lever.co/acme and https://jobs.jobvite.com/acme"
        self.assertEqual(refs(html), [("lever", "acme")])
        self.assertEqual(detect_unsupported(html), ["jobs.jobvite.com"])

    def test_a_plain_linkedin_link_is_not_a_jobs_board(self):
        self.assertEqual(detect_unsupported("https://www.linkedin.com/company/acme"), [])
        self.assertEqual(detect_unsupported(""), [])


class SupportedAtsTest(unittest.TestCase):
    def test_supported_ats_equals_the_schema_check(self):
        sql = (Path(base.__file__).resolve().parents[1] / "store" / "migrations" / "0001_initial.sql").read_text()
        block = re.search(r"CREATE TABLE board \(.*?ats\s+TEXT NOT NULL CHECK \(ats IN \((.*?)\)\)", sql, re.S)
        self.assertEqual(set(SUPPORTED_ATS), set(re.findall(r"'([a-z_]+)'", block.group(1))))
        self.assertIsInstance(SUPPORTED_ATS, frozenset)

    def test_boardref_normalizes_and_rejects(self):
        self.assertEqual(BoardRef("greenhouse", "Acme").token, "acme")
        with self.assertRaises(ValueError):
            BoardRef("greenhouse", "ac%me")


if __name__ == "__main__":
    unittest.main()
