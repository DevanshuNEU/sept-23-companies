import unittest

from outreach.plan.careers import careers_url, pick_careers_url


class CareersUrlTest(unittest.TestCase):
    def test_each_board_has_its_public_page(self):
        self.assertEqual(careers_url("ashby", "tessera"), "https://jobs.ashbyhq.com/tessera")
        self.assertEqual(careers_url("greenhouse", "tessera"), "https://job-boards.greenhouse.io/tessera")
        self.assertEqual(careers_url("lever", "tessera-labs"), "https://jobs.lever.co/tessera-labs")
        self.assertEqual(careers_url("workable", "tessera"), "https://apply.workable.com/tessera/")

    def test_an_unknown_board_or_a_strange_token_has_none(self):
        self.assertIsNone(careers_url("workday", "tessera"))
        for bad in ("", "a/b", "a b", "../x", "x?y=1", "a#b", "-lead", None):
            with self.subTest(bad=bad):
                self.assertIsNone(careers_url("ashby", bad))
        self.assertEqual(careers_url("ASHBY", "Tessera.Labs_1"), "https://jobs.ashbyhq.com/Tessera.Labs_1")


class PickCareersUrlTest(unittest.TestCase):
    BOARDS = [("workday", "x"), ("lever", "tessera")]

    def test_the_order_posting_board_page_domain(self):
        pick = pick_careers_url
        self.assertEqual(pick(posting_url="https://jobs.lever.co/tessera/123", boards=self.BOARDS,
                              watched_url="https://tessera.example/careers", domain="tessera.example"),
                         "https://jobs.lever.co/tessera/123")
        self.assertEqual(pick(posting_url=None, boards=self.BOARDS, watched_url="https://tessera.example/careers",
                              domain="tessera.example"), "https://jobs.lever.co/tessera")
        self.assertEqual(pick(posting_url="", boards=[("workday", "x")], watched_url="https://tessera.example/jobs",
                              domain="tessera.example"), "https://tessera.example/jobs")
        self.assertEqual(pick(posting_url=None, boards=[], watched_url=None, domain="Tessera.Example"),
                         "https://tessera.example")
        self.assertIsNone(pick(posting_url=None, boards=[], watched_url=None, domain=None))

    def test_a_bad_link_falls_through_to_the_next(self):
        self.assertEqual(pick_careers_url(posting_url="javascript:alert(1)", boards=[], watched_url="ftp://x.example",
                                          domain="tessera.example"), "https://tessera.example")
        self.assertIsNone(pick_careers_url(posting_url="https://", boards=[("ashby", "a b")], watched_url=None,
                                           domain="not a domain"))


if __name__ == "__main__":
    unittest.main()
