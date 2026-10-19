"""
End to end: finalcheck -> final_merge -> node refresh/write_level.js -> dashboard/data.js, on a scratch copy.

A fake network stands in for the job boards. Nothing here reads or writes the real refresh/work/ or
dashboard/data.js. Skipped when node is not installed.
"""
import contextlib, io, json, os, shutil, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from refresh import final_merge  # noqa: E402
from tests.test_refresh_fetch import fc  # noqa: E402  (finalcheck, imported without touching refresh/work/)

NODE = shutil.which("node")

DATA_JS = """window.REFERENCE = {};
window.COMPANIES = [
  { id:"acme", name:"Acme", cat:"AI", level:{"entryReq":"Old Role","levelOk":true,"levelChecked":true}, reqs:[{"t":"Old Role","u":"https://acme.example/1","d":"2026-09-01"}] },
  { id:"beta", name:"Beta", cat:"AI", level:{"levelOk":false,"levelChecked":true}, noRoleInBand:true, noRoleWhy:"no open software role in your lane", reqs:[] },
];
window.RULED_OUT = [];
"""

NEW_GRAD = {"title": "Software Engineer, New Grad", "content": "Join us as a new grad engineer.",
            "location": {"name": "New York, NY"}, "first_published": "2026-10-10T00:00:00Z",
            "absolute_url": "https://beta.example/jobs/9"}


@unittest.skipUnless(NODE, "node is not installed")
class RefreshPipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.work = os.path.join(self.root, "refresh", "work") + os.sep
        os.makedirs(self.work)
        os.makedirs(os.path.join(self.root, "dashboard"))
        shutil.copy(os.path.join(ROOT, "refresh", "write_level.js"), os.path.join(self.root, "refresh"))
        with open(os.path.join(self.root, "dashboard", "data.js"), "w") as f:
            f.write(DATA_JS)
        targets = [{"id": "acme", "name": "Acme", "list": "linkedin", "slugs": {"greenhouse": "acme"}, "urls": []},
                   {"id": "beta", "name": "Beta", "list": "linkedin", "slugs": {"greenhouse": "beta"}, "urls": []}]
        self.write("final_targets.json", targets)
        self.write("eligibility.json", {})
        self.addCleanup(setattr, fc, "get", fc.get)

    def write(self, name, data):
        with open(self.work + name, "w") as f:
            json.dump(data, f)

    def boards(self, acme, beta):
        """acme/beta: (status, body) the fake network returns for each company's Greenhouse board."""
        def get(u, t=30, limit=1500000):
            status, body = acme if "/acme/" in u else beta
            return status, u, body
        fc.get = get

    def refresh(self):
        """One refresh run of the three steps under test; returns the companies in data.js by id."""
        with contextlib.redirect_stdout(io.StringIO()):
            fc.main(self.work + "final_targets.json", self.work + "finalcheck.json")
            final_merge.main(self.work, today="2026-10-18")
        subprocess.run([NODE, "refresh/write_level.js"], cwd=self.root, check=True, capture_output=True)
        return self.companies()

    def companies(self):
        """The companies in the scratch data.js, by id."""
        js = subprocess.run([NODE, "-e", "global.window={};eval(require('fs').readFileSync('dashboard/data.js','utf8'));"
                             "console.log(JSON.stringify(window.COMPANIES))"],
                            cwd=self.root, check=True, capture_output=True, text=True)
        return {c["id"]: c for c in json.loads(js.stdout)}

    def test_board_that_cannot_be_read_leaves_the_company_exactly_as_it_was(self):
        before = self.companies()
        self.boards(acme=(0, ""), beta=(200, json.dumps({"jobs": [NEW_GRAD]})))
        after = self.refresh()
        self.assertEqual(after["acme"], before["acme"])
        self.assertEqual(after["beta"]["level"]["entryReq"], "Software Engineer, New Grad")
        self.assertNotIn("noRoleInBand", after["beta"])

    def test_html_where_json_was_expected_keeps_the_company(self):
        before = self.companies()
        self.boards(acme=(200, "<html>Just a moment...</html>"), beta=(200, json.dumps({"jobs": []})))
        after = self.refresh()
        self.assertEqual(after["acme"], before["acme"])

    def test_empty_board_closes_only_on_the_second_run(self):
        before = self.companies()
        self.boards(acme=(200, json.dumps({"jobs": []})), beta=(200, json.dumps({"jobs": [NEW_GRAD]})))
        first = self.refresh()
        self.assertEqual(first["acme"], before["acme"])
        second = self.refresh()
        self.assertTrue(second["acme"]["noRoleInBand"])
        self.assertFalse(second["acme"]["level"]["levelOk"])

    def test_one_malformed_posting_does_not_crash_the_run(self):
        before = self.companies()
        bad = dict(NEW_GRAD, content={"not": "a string"})
        self.boards(acme=(200, json.dumps({"jobs": [bad]})), beta=(200, json.dumps({"jobs": [NEW_GRAD]})))
        after = self.refresh()
        self.assertEqual(after["acme"], before["acme"])
        self.assertEqual(after["beta"]["level"]["entryReq"], "Software Engineer, New Grad")

    def test_three_errors_in_a_row_are_flagged_for_review(self):
        self.boards(acme=(503, ""), beta=(200, json.dumps({"jobs": [NEW_GRAD]})))
        for _ in range(3):
            self.refresh()
        with open(self.work + "board_status.json") as f:
            status = json.load(f)
        self.assertEqual(status["acme"]["error"], 3)
        self.assertEqual(status["acme"]["last"], "error")


if __name__ == "__main__":
    unittest.main()
