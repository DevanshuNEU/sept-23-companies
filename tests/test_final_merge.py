import json
import os
import tempfile
import unittest

from refresh import final_merge


def job(url="https://boards.example.com/a/1", tier=1, title="Software Engineer, New Grad"):
    return {"title": title, "url": url, "tier": tier, "minYears": 0, "citizen": False,
            "phd": False, "start2027": False, "posted": "2026-10-10", "loc": "Boston, MA"}


class FinalMergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name + os.sep

    def write(self, name, data):
        with open(os.path.join(self.dir, name), "w") as f:
            json.dump(data, f)

    def read(self, name):
        with open(os.path.join(self.dir, name)) as f:
            return json.load(f)

    def run_merge(self, finalcheck, status=None):
        self.write("final_targets.json", [{"id": cid, "name": "Co " + cid} for cid in finalcheck])
        self.write("finalcheck.json", finalcheck)
        self.write("eligibility.json", {})
        if status is not None:
            self.write("board_status.json", status)
        final_merge.main(self.dir, today="2026-10-18")
        return self.read("final_merge.json")

    def test_errored_company_is_left_out(self):
        out = self.run_merge({"a": {"read": "error", "jobs": []},
                              "b": {"read": "ok", "jobs": [job()]}})
        self.assertNotIn("a", out)
        self.assertIn("b", out)

    def test_first_empty_company_is_left_out(self):
        out = self.run_merge({"a": {"read": "empty", "jobs": []},
                              "b": {"read": "ok", "jobs": [job()]}})
        self.assertNotIn("a", out)

    def test_second_empty_marks_nothing_open(self):
        out = self.run_merge({"a": {"read": "empty", "jobs": []}},
                             status={"a": {"empty": 1, "error": 0, "last": "empty"}})
        self.assertTrue(out["a"]["noRoleInBand"])

    def test_ok_company_with_role_is_written(self):
        out = self.run_merge({"a": {"read": "ok", "jobs": [job()]}})
        self.assertEqual(out["a"]["entryReq"], "Software Engineer, New Grad")
        self.assertNotIn("noRoleInBand", out["a"])

    def test_url_only_company_unchanged(self):
        out = self.run_merge({"a": {"jobs": [], "closedUrls": 1}})
        self.assertTrue(out["a"]["noRoleInBand"])
        self.assertEqual(out["a"]["noRoleWhy"], "every posting link is closed")

    def test_status_file_updated(self):
        self.run_merge({"a": {"read": "error", "jobs": []}})
        self.assertEqual(self.read("board_status.json")["a"]["error"], 1)

    def test_corrupt_status_entry_does_not_abort(self):
        out = self.run_merge({"a": {"read": "ok", "jobs": [job()]},
                              "b": {"read": "error", "jobs": []}},
                             status={"a": "junk", "b": {"empty": "x", "error": None}})
        self.assertIn("a", out)
        self.assertNotIn("b", out)
        self.assertEqual(self.read("board_status.json")["b"]["error"], 1)

    def test_today_defaults_to_real_date(self):
        self.write("final_targets.json", [{"id": "a", "name": "Co a"}])
        self.write("finalcheck.json", {"a": {"read": "ok", "jobs": [job()]}})
        self.write("eligibility.json", {})
        final_merge.main(self.dir)
        self.assertEqual(len(self.read("board_status.json")["a"]["since"]), 10)


if __name__ == "__main__":
    unittest.main()
