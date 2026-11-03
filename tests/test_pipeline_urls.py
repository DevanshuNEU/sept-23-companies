import unittest

from outreach.crawl.postings import source_key
from outreach.pipeline.urls import BoardKey, board_key, posting_key


class BoardKeyTest(unittest.TestCase):
    def test_board_key_parses_every_shape(self):
        cases = {
            "https://job-boards.greenhouse.io/Acme/jobs/4012345": ("greenhouse", "acme", "4012345"),
            "https://boards.greenhouse.io/acme/jobs/4012345?gh_src=x": ("greenhouse", "acme", "4012345"),
            "https://job-boards.eu.greenhouse.io/acme/jobs/77": ("greenhouse", "acme", "77"),
            "https://boards.greenhouse.io/embed/job_app?for=Acme&token=4012345": ("greenhouse", "acme", "4012345"),
            "https://boards.greenhouse.io/acme": ("greenhouse", "acme", None),
            "https://www.acme.com/careers/open?gh_jid=123": ("greenhouse", None, "123"),
            "https://jobs.ashbyhq.com/Acme/3f2b9c1e-1111-4222-8333-444455556666":
                ("ashby", "acme", "3f2b9c1e-1111-4222-8333-444455556666"),
            "https://jobs.ashbyhq.com/acme/3f2b9c1e-1111-4222-8333-444455556666/application":
                ("ashby", "acme", "3f2b9c1e-1111-4222-8333-444455556666"),
            "https://jobs.lever.co/acme/a1b2c3d4-0000-4111-8222-333344445555":
                ("lever", "acme", "a1b2c3d4-0000-4111-8222-333344445555"),
            "https://jobs.lever.co/acme/a1b2c3d4-0000-4111-8222-333344445555/apply":
                ("lever", "acme", "a1b2c3d4-0000-4111-8222-333344445555"),
            "https://jobs.eu.lever.co/acme": ("lever", "acme", None),
            "https://Acme.wd5.myworkdayjobs.com/en-US/External/job/Seattle-WA/Software-Engineer_R-1001":
                ("workday", "acme.wd5/External", "/job/Seattle-WA/Software-Engineer_R-1001"),
            "https://acme.wd5.myworkdayjobs.com/External/job/Seattle-WA/Software-Engineer_R-1001/apply":
                ("workday", "acme.wd5/External", "/job/Seattle-WA/Software-Engineer_R-1001"),
            "https://acme.wd3.myworkdaysite.com/Careers_Site/job/Remote/Data-Engineer_R2":
                ("workday", "acme.wd3/Careers_Site", "/job/Remote/Data-Engineer_R2"),
            "https://wd3.myworkdaysite.com/en-US/recruiting/acme/Careers_Site/job/Remote/Data-Engineer_R2":
                ("workday", "acme.wd3/Careers_Site", "/job/Remote/Data-Engineer_R2"),
            "https://acme.wd1.myworkdayjobs.com/en-US/External": ("workday", "acme.wd1/External", None),
            "https://apply.workable.com/acme/j/ABC123DEF4/": ("workable", "acme", "ABC123DEF4"),
            "https://apply.workable.com/acme/": ("workable", "acme", None),
            "https://ats.rippling.com/acme/jobs/5d1b7c52-0000-4aaa-8bbb-ccccddddeeee":
                ("rippling", "acme", "5d1b7c52-0000-4aaa-8bbb-ccccddddeeee"),
            "https://ats.rippling.com/en-GB/acme/jobs/5d1b7c52-0000-4aaa-8bbb-ccccddddeeee":
                ("rippling", "acme", "5d1b7c52-0000-4aaa-8bbb-ccccddddeeee"),
            "https://careers-acme.icims.com/jobs/2210/software-engineer/job":
                ("icims", "careers-acme.icims.com", "2210"),
            "https://careers-acme.icims.com/jobs/2210/login": ("icims", "careers-acme.icims.com", "2210"),
            "https://acme.applytojob.com/apply/AbC123xYz/Software-Engineer": ("jazzhr", "acme", "AbC123xYz"),
            "https://acme.applytojob.com/apply/jobs/details/AbC123xYz": ("jazzhr", "acme", "AbC123xYz"),
        }
        for url, (ats, token, native_id) in cases.items():
            with self.subTest(url=url):
                self.assertEqual(board_key(url), BoardKey(ats, token, native_id))

    def test_a_link_without_a_scheme_still_parses(self):
        self.assertEqual(board_key("jobs.lever.co/acme/a1b2c3d4-0000-4111-8222-333344445555"),
                         BoardKey("lever", "acme", "a1b2c3d4-0000-4111-8222-333344445555"))

    def test_the_greenhouse_path_wins_over_gh_jid(self):
        self.assertEqual(board_key("https://boards.greenhouse.io/acme/jobs/5?gh_jid=9"),
                         BoardKey("greenhouse", "acme", "5"))

    def test_gh_jid_must_be_a_number(self):
        self.assertIsNone(board_key("https://www.acme.com/careers?gh_jid=abc"))

    def test_unknown_site_is_none(self):
        for url in ("https://www.acme.com/careers/software-engineer", "https://example.org/jobs/1",
                    "https://www.linkedin.com/jobs/view/123", "", "   ", "not a url", "mailto:a@b.co",
                    "ftp://jobs.lever.co/acme/x", "https://jobs.lever.co/", "https://jobs.lever.co/bad%20token",
                    "https://boards.greenhouse.io/embed/job_app", "https://evil-icims.com/jobs/1",
                    "https://notmyworkdayjobs.com/x/job/y", "https://jobs.lever.co.evil.com/acme/1"):
            with self.subTest(url=url):
                self.assertIsNone(board_key(url))

    def test_non_text_is_none(self):
        self.assertIsNone(board_key(None))  # type: ignore[arg-type]

    def test_board_key_is_frozen(self):
        key = board_key("https://jobs.lever.co/acme")
        with self.assertRaises(AttributeError):
            key.token = "other"  # type: ignore[misc]


class PostingKeyTest(unittest.TestCase):
    def test_posting_key_matches_crawler_source_key(self):
        cases = [
            ("https://boards.greenhouse.io/Acme/jobs/4012345", source_key("greenhouse", "acme", "4012345")),
            ("https://jobs.ashbyhq.com/acme/3F2B9C1E-1111-4222-8333-444455556666",
             source_key("ashby", "acme", "3f2b9c1e-1111-4222-8333-444455556666")),
            ("https://jobs.lever.co/acme/A1B2C3D4-0000-4111-8222-333344445555",
             source_key("lever", "acme", "a1b2c3d4-0000-4111-8222-333344445555")),
            ("https://acme.wd5.myworkdayjobs.com/en-US/External/job/Seattle-WA/Software-Engineer_R-1001",
             source_key("workday", "acme.wd5/External", "/job/Seattle-WA/Software-Engineer_R-1001").lower()),
            ("https://apply.workable.com/acme/j/ABC123DEF4", source_key("workable", "acme", "abc123def4")),
        ]
        for url, crawler_key in cases:
            with self.subTest(url=url):
                self.assertEqual(posting_key(board_key(url)), crawler_key.lower())

    def test_the_crawler_stores_the_workday_key_in_this_shape(self):
        self.assertEqual(posting_key(BoardKey("workday", "acme.wd5/External", "/job/A/B_R1")),
                         "workday:acme.wd5/external:/job/a/b_r1")

    def test_none_without_a_token_or_a_native_id(self):
        self.assertIsNone(posting_key(board_key("https://www.acme.com/c?gh_jid=123")))
        self.assertIsNone(posting_key(board_key("https://jobs.lever.co/acme")))
        self.assertIsNone(posting_key(BoardKey("lever", "acme", "")))

    def test_none_for_no_key(self):
        self.assertIsNone(posting_key(None))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
