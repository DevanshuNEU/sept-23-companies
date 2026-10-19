import json, os, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _load():
    # Import from a temp cwd so a module that reads refresh/work/ at import time fails loudly
    # instead of touching the real files.
    old = os.getcwd()
    with tempfile.TemporaryDirectory() as d:
        os.chdir(d)
        try:
            import refresh.finalcheck as fc
        finally:
            os.chdir(old)
    return fc


fc = _load()

GH_JOB = {"title": "Software Engineer", "content": "Build things.", "location": {"name": "New York"},
          "first_published": "2026-10-01T00:00:00Z", "absolute_url": "https://example.com/1"}
ASHBY_JOB = {"title": "Software Engineer", "descriptionPlain": "Build things.", "location": "New York",
             "publishedAt": "2026-10-01", "jobUrl": "https://example.com/2"}


class FetchReadTests(unittest.TestCase):
    def setUp(self):
        self._get = fc.get
        self.addCleanup(setattr, fc, "get", self._get)

    def fake(self, responses):
        # responses: substring of URL -> (status, body)
        def get(u, t=30):
            for key, (st, body) in responses.items():
                if key in u:
                    return st, u, body
            raise AssertionError("unexpected url " + u)
        fc.get = get

    def test_board_with_jobs_is_ok(self):
        self.fake({"greenhouse": (200, json.dumps({"jobs": [GH_JOB]}))})
        rows, read = fc.board({"slugs": {"greenhouse": "acme"}})
        self.assertEqual(read, "ok")
        self.assertEqual(len(rows), 1)

    def test_board_with_no_jobs_is_empty(self):
        self.fake({"greenhouse": (200, json.dumps({"jobs": []}))})
        rows, read = fc.board({"slugs": {"greenhouse": "acme"}})
        self.assertEqual(read, "empty")
        self.assertEqual(rows, [])

    def test_network_failure_is_error(self):
        self.fake({"greenhouse": (0, "")})
        self.assertEqual(fc.board({"slugs": {"greenhouse": "acme"}})[1], "error")

    def test_rate_limit_is_error(self):
        self.fake({"greenhouse": (429, "")})
        self.assertEqual(fc.board({"slugs": {"greenhouse": "acme"}})[1], "error")

    def test_html_instead_of_json_is_error(self):
        self.fake({"greenhouse": (200, "<html>Just a moment...</html>")})
        self.assertEqual(fc.board({"slugs": {"greenhouse": "acme"}})[1], "error")

    def test_non_object_job_entries_are_error(self):
        self.fake({"greenhouse": (200, json.dumps({"jobs": ["oops"]}))})
        self.assertEqual(fc.board({"slugs": {"greenhouse": "acme"}})[1], "error")

    def test_one_board_failing_makes_company_error(self):
        self.fake({"ashby": (200, json.dumps({"jobs": [ASHBY_JOB]})), "lever": (0, "")})
        rows, read = fc.board({"slugs": {"ashby": "acme", "lever": "acme"}})
        self.assertEqual(read, "error")
        self.assertEqual(len(rows), 1)

    def test_work_sets_read_for_board_company(self):
        self.fake({"greenhouse": (200, json.dumps({"jobs": []}))})
        cid, out = fc.work({"id": "x", "list": "linkedin", "slugs": {"greenhouse": "acme"}, "urls": []})
        self.assertEqual(cid, "x")
        self.assertEqual(out["read"], "empty")
        self.assertTrue(out["boardEmpty"])

    def test_import_does_no_work(self):
        # _load() already imported inside an empty temp cwd without raising; confirm the module
        # exposes main() and left no output behind.
        self.assertTrue(callable(fc.main))
        old = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            try:
                import importlib
                importlib.reload(fc)
                self.assertEqual(os.listdir(d), [])
            finally:
                os.chdir(old)


if __name__ == "__main__":
    unittest.main()
