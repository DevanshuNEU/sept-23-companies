"""ATS detection (#31 Task 10): which boards a careers page points at, as BoardRef values."""
import re
import unittest
from pathlib import Path

from outreach.crawl import base
from outreach.sources.ats_detect import detect_all, detect_ats, detect_unsupported
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


class VendorShapesTest(unittest.TestCase):
    """Vendor pages, app paths and locale segments that look like a token but are not a board."""

    def test_smartrecruiters_app_paths_are_not_tokens(self):
        self.assertEqual(refs("https://jobs.smartrecruiters.com/oneclick-ui/company/AcmeCorp/publication/x"), [])
        self.assertEqual(refs("https://jobs.smartrecruiters.com/ni/AcmeCorp/743"), [])
        self.assertEqual(refs("https://jobs.smartrecruiters.com/AcmeCorp/743"), [("smartrecruiters", "acmecorp")])

    def test_workable_job_path_is_not_a_token(self):
        self.assertEqual(refs("https://apply.workable.com/j/ABC123"), [])
        self.assertEqual(refs("https://apply.workable.com/acme/j/ABC123"), [("workable", "acme")])

    def test_rippling_skips_a_locale_segment(self):
        self.assertEqual(refs("https://ats.rippling.com/en-GB/acme/jobs"), [("rippling", "acme")])
        self.assertEqual(refs("https://ats.rippling.com/en/acme/jobs"), [("rippling", "acme")])
        self.assertEqual(refs("https://ats.rippling.com/en-GB"), [])

    def test_vendor_subdomains_are_not_boards(self):
        for host in ("docs", "help", "support", "app", "login", "community", "info", "go", "developers", "blog",
                     "status", "marketplace", "partners", "learn"):
            with self.subTest(host=host):
                self.assertEqual(refs(f"https://{host}.bamboohr.com/x https://{host}.breezy.hr/x"), [])
                self.assertEqual(refs(f"https://{host}.recruitee.com/x https://{host}.teamtailor.com/x"), [])
                self.assertEqual(refs(f"https://{host}.pinpointhq.com/x https://{host}.applytojob.com/x"), [])

    def test_icims_vendor_hosts_are_not_boards(self):
        for host in ("login", "community", "info", "www", "api"):
            with self.subTest(host=host):
                self.assertEqual(refs(f"https://{host}.icims.com/x"), [])

    def test_icims_keeps_the_full_host(self):
        self.assertEqual(refs("https://careers.Acme.icims.com/jobs"), [("icims", "careers.acme.icims.com")])


class BoundaryTest(unittest.TestCase):
    def test_a_slug_cut_by_a_non_ascii_letter_is_not_a_board(self):
        self.assertEqual(refs("https://boards.greenhouse.io/caf\u00e9"), [])
        self.assertEqual(refs("https://jobs.lever.co/caf\u00e9/x"), [])
        self.assertEqual(refs("https://caf\u00e9.bamboohr.com/x"), [])
        self.assertEqual(refs("https://acme.wd5.myworkdayjobs.com/Caf\u00e9"), [])

    def test_workday_skips_a_bare_locale_and_non_site_paths(self):
        self.assertEqual(refs("https://acme.wd5.myworkdayjobs.com/en/External"), [("workday", "acme.wd5/External")])
        self.assertEqual(refs("https://acme.wd5.myworkdayjobs.com/en"), [])
        self.assertEqual(refs("https://acme.wd5.myworkdayjobs.com/login.html"), [])
        self.assertEqual(refs("https://acme.wd5.myworkdayjobs.com/assets/app.js"), [])
        self.assertEqual(refs("https://acme.wd5.myworkdayjobs.com/Careers."), [("workday", "acme.wd5/Careers")])

    def test_a_subdomain_board_must_end_at_the_host(self):
        self.assertEqual(refs("mailto:hr@acme.bamboohr.com"), [("bamboohr", "acme")])
        self.assertEqual(refs("https://acme.teamtailor.com.evil.com/jobs"), [])
        self.assertEqual(refs("https://acme.icims.com.evil.com/jobs"), [])
        self.assertEqual(refs("see acme.teamtailor.com."), [("teamtailor", "acme")])

    def test_a_host_must_start_at_a_boundary(self):
        self.assertEqual(refs("https://notjobs.lever.co/acme https://xboards.greenhouse.io/acme"), [])


class FalseNegativeTest(unittest.TestCase):
    def test_json_escaped_urls(self):
        self.assertEqual(refs('{"url":"https:\\/\\/boards.greenhouse.io\\/acme"}'), [("greenhouse", "acme")])
        self.assertEqual(refs('"https:\\/\\/jobs.lever.co\\/acme\\/123"'), [("lever", "acme")])

    def test_api_and_other_embed_shapes(self):
        self.assertEqual(refs("https://boards-api.greenhouse.io/v1/boards/acme/jobs"), [("greenhouse", "acme")])
        self.assertEqual(refs("https://api.lever.co/v0/postings/acme?mode=json"), [("lever", "acme")])
        self.assertEqual(refs("https://boards.greenhouse.io/embed/job_app?for=acme&token=1"), [("greenhouse", "acme")])
        self.assertEqual(refs("https://boards-api.greenhouse.io/v1/boards"), [])

    def test_known_misses_stay_missed(self):
        self.assertEqual(refs("https://acme.wd5.myworkdaysite.com/recruiting/acme/External"), [])
        self.assertEqual(refs("https://ebwg.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/"), [])


class DetectAllTest(unittest.TestCase):
    TEXT = " ".join(f"https://jobs.lever.co/co{i}" for i in range(6)) + " https://jobs.lever.co/CO0"

    def test_detect_all_is_uncapped_in_first_seen_order(self):
        self.assertEqual([r.token for r in detect_all(self.TEXT)], [f"co{i}" for i in range(6)])
        self.assertEqual(detect_all(""), [])

    def test_detect_ats_is_the_first_three_of_detect_all(self):
        self.assertEqual(detect_ats(self.TEXT), detect_all(self.TEXT)[:3])


class LocaleRuleTest(unittest.TestCase):
    """A locale prefix is `xx-XX` or bare `en`; any other short segment is a site or a slug."""

    def test_workday_two_letter_sites_are_sites(self):
        self.assertEqual(refs("https://acme.wd3.myworkdayjobs.com/hr/jobs"), [("workday", "acme.wd3/hr")])
        self.assertEqual(refs("https://acme.wd3.myworkdayjobs.com/HR"), [("workday", "acme.wd3/HR")])
        self.assertEqual(refs("https://acme.wd3.myworkdayjobs.com/en-US/HR/job/x"), [("workday", "acme.wd3/HR")])
        self.assertEqual(refs("https://acme.wd3.myworkdayjobs.com/en-US/Ab"), [("workday", "acme.wd3/Ab")])
        self.assertEqual(refs("https://acme.wd3.myworkdayjobs.com/en/Ab"), [("workday", "acme.wd3/Ab")])

    def test_workday_a_lone_locale_is_not_a_site(self):
        self.assertEqual(refs("https://acme.wd3.myworkdayjobs.com/en-US/"), [])
        self.assertEqual(refs("https://acme.wd3.myworkdayjobs.com/en"), [])

    def test_rippling_two_letter_and_xx_xx_slugs_are_read(self):
        self.assertEqual(refs("https://ats.rippling.com/ab"), [("rippling", "ab")])
        self.assertEqual(refs("https://ats.rippling.com/ab-cd"), [("rippling", "ab-cd")])
        self.assertEqual(refs("https://ats.rippling.com/ab-cd/jobs"), [("rippling", "ab-cd")])
        self.assertEqual(refs("https://ats.rippling.com/en-GB/ab/jobs"), [("rippling", "ab")])
        self.assertEqual(refs("https://ats.rippling.com/en/ab-cd/jobs"), [("rippling", "ab-cd")])

    def test_rippling_a_lone_locale_is_not_a_slug(self):
        self.assertEqual(refs("https://ats.rippling.com/en-GB"), [])
        self.assertEqual(refs("https://ats.rippling.com/en"), [])


class EncodedTextTest(unittest.TestCase):
    def test_percent_encoded_urls_are_decoded(self):
        self.assertEqual(refs("https%3A%2F%2Facme.bamboohr.com%2Fcareers"), [("bamboohr", "acme")])
        self.assertEqual(refs("u=https%3a%2f%2fjobs.lever.co%2Facme%2F1"), [("lever", "acme")])

    def test_unicode_escaped_slashes_are_decoded(self):
        self.assertEqual(refs("https:\\u002F\\u002Facme.bamboohr.com"), [("bamboohr", "acme")])
        self.assertEqual(refs("https:\\u002f\\u002fboards.greenhouse.io\\u002facme"), [("greenhouse", "acme")])

    def test_a_leftover_encoded_prefix_never_forms_a_token(self):
        self.assertEqual(refs("%40acme.bamboohr.com"), [])
        self.assertEqual(refs("x%2Dacme.bamboohr.com"), [])
        self.assertEqual(refs("%2facme.bamboohr.com"), [("bamboohr", "acme")])
        self.assertEqual(detect_unsupported("https%3A%2F%2Fjobs.jobvite.com%2Facme"), ["jobs.jobvite.com"])


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
