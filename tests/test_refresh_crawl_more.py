"""refresh/crawl_more.py no longer reads SmartRecruiters or Dover's /api/ (#46): both hosts' robots.txt forbid it.

A fake `get` stands in for the network and fails the test on any URL it does not expect. The module is imported and
run from a temp folder, so nothing here reads or writes the real refresh/work/.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _load():
    old = os.getcwd()
    with tempfile.TemporaryDirectory() as d:
        os.chdir(d)
        try:
            import refresh.crawl_more as cm
        finally:
            os.chdir(old)
    return cm


cm = _load()
FORBIDDEN = ("smartrecruiters.com", "dover.com")


class Network:
    """A fake `get`: answers by URL prefix, records every URL and refuses the two forbidden hosts outright."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.urls = []

    def __call__(self, u, t=25):
        self.urls.append(u)
        if any(host in u for host in FORBIDDEN):
            raise AssertionError(f"crawl_more asked a host whose robots.txt forbids it: {u}")
        for prefix, answer in self.answers.items():
            if u.startswith(prefix):
                return answer
        raise AssertionError(f"unexpected url {u}")


class CrawlMoreTests(unittest.TestCase):
    def setUp(self):
        self.net = Network({"https://api.gem.com/": (200, "[]"), "https://jobs.workable.com/": (200, '{"jobs": []}')})
        for module in (cm, cm.cb):
            patch = mock.patch.object(module, "get", self.net)
            patch.start()
            self.addCleanup(patch.stop)

    def test_the_dropped_ats_make_no_request(self):
        for ats in ("smartrecruiters", "dover"):
            self.assertNotIn(ats, cm.NEW)
            self.assertIn(ats, cm.DROPPED)
            self.assertIsNone(cm.board_any(ats, "acme"))
            self.assertEqual(cm.crawl([ats, "acme", None]), ([ats, "acme", None], None, []))
        self.assertEqual(self.net.urls, [])
        source = Path(cm.__file__).read_text(encoding="utf-8")
        self.assertNotIn("https://api.smartrecruiters.com", source)
        self.assertNotIn("https://app.dover.com/api", source)

    def run_main(self, argv, slugs):
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            work = Path(d) / "refresh" / "work"
            (work / "crawl").mkdir(parents=True)
            (work / "research").mkdir()
            (work / "crawl" / "slugs_more.json").write_text(json.dumps(slugs), encoding="utf-8")
            old = os.getcwd()
            os.chdir(d)
            try:
                with mock.patch.object(sys, "argv", ["crawl_more.py", *argv]), contextlib.redirect_stdout(out):
                    cm.main()
            finally:
                os.chdir(old)
            written = json.loads((work / "research" / "leads_ats_more.json").read_text(encoding="utf-8"))
        return out.getvalue(), written

    SLUGS = [["smartrecruiters", "acme", None], ["dover", "bcme", None], ["gem", "ccme", None]]

    def test_only_and_skip_naming_a_dropped_ats_print_one_plain_line_each_and_carry_on(self):
        out, written = self.run_main(["--only", "smartrecruiters,dover,gem", "--skip", "dover"], self.SLUGS)
        lines = out.splitlines()
        self.assertEqual(len([ln for ln in lines if ln.startswith("smartrecruiters is no longer read")]), 1)
        self.assertEqual(len([ln for ln in lines if ln.startswith("dover is no longer read")]), 1)
        self.assertIn("robots.txt", out)
        self.assertIn("crawling 1 boards", out)  # gem only
        self.assertEqual(self.net.urls, ["https://api.gem.com/job_board/v0/ccme/job_posts/"])
        self.assertEqual(written, [])

    def test_the_refresh_command_line_still_runs(self):
        # refresh.py runs `crawl_more.py --days 45 --skip dover`.
        out, _ = self.run_main(["--days", "45", "--skip", "dover"], self.SLUGS)
        self.assertIn("dover is no longer read", out)
        self.assertIn("left out 2 boards on ATSes no longer read", out)
        self.assertIn("crawling 1 boards", out)  # gem only: neither dropped board reaches the crawl
        self.assertTrue(self.net.urls)
        self.assertFalse(any(host in u for u in self.net.urls for host in FORBIDDEN))


if __name__ == "__main__":
    unittest.main()
