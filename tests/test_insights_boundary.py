"""The insights package's boundary (#81): standard library and `outreach` only, never `outreach.api`, and a full read
(the dataset, the findings, the duplicates and an export) writes nothing to the store."""
import sys
import tempfile
import unittest
from pathlib import Path

import live_checkout
from outreach.insights import export as E
from outreach.insights.config import default_insights_config
from outreach.insights.dataset import load_outcome_rows
from outreach.insights.duplicates import possible_duplicates
from outreach.insights.findings import shared_findings
from tests import apiseed as S
from tests.insights_seed import application, outcome_touch
from tests.test_api_boundary import imports_of, package_of
from tests.test_insights_cli import all_counts
from tests.storetest import fresh_store

ROOT = Path(__file__).resolve().parent.parent
INSIGHTS = ROOT / "outreach" / "insights"
ALLOWED = set(sys.stdlib_module_names) | {"outreach"}


class BoundaryTest(unittest.TestCase):
    def test_insights_imports_only_the_standard_library_and_outreach(self):
        files = sorted(INSIGHTS.rglob("*.py"))
        self.assertIn(INSIGHTS / "dataset.py", files)
        found = []
        for path in files:
            names = [n for n in imports_of(path, package_of(ROOT, path)) if n]
            found += [f"{path.name} imports {n}" for n in names if n.split(".")[0] not in ALLOWED]
            found += [f"{path.name} imports {n}" for n in names if n == "outreach.api" or n.startswith("outreach.api.")]
        self.assertEqual([], found)

    def test_bands_import_nothing_from_ranking(self):
        names = imports_of(INSIGHTS / "bands.py", "outreach.insights")
        self.assertEqual([], [n for n in names if n.startswith(("outreach.ranking", "outreach.api"))])

    def test_repo_root_is_the_checkout(self):
        self.assertEqual(Path(live_checkout.REPO_ROOT).resolve(), E.REPO_ROOT)

    def test_a_full_read_writes_nothing(self):
        conn = fresh_store()
        for n in range(6):
            cid = S.company(conn, f"Company {n}")
            pid = S.person(conn, cid, f"Person {n}", email=f"p{n}@c{n}.example")
            outcome_touch(conn, cid, pid, sent_at="2026-10-01T15:00:00Z",
                          interview_at="2026-10-05T15:00:00Z" if n % 2 else None)
            S.evidence(conn, cid, "size", "40", low=40)
            S.score(conn, cid, {"live_role": n})
        application(conn, S.company(conn, "gm"), outcome="interview")
        S.company(conn, "General Motors", status="closed", close_reason="interview")
        before, changes = all_counts(conn), conn.total_changes
        rows = load_outcome_rows(conn, with_people=True)
        shared_findings(rows, default_insights_config())
        self.assertTrue(possible_duplicates(conn, rows))
        folder = Path(tempfile.mkdtemp(prefix="insights-boundary-test-"))
        E.write_csv(E.to_records(rows, with_people=True), E.COMPANY_FIELDS + E.PEOPLE_FIELDS, folder / "o.csv")
        self.assertEqual(before, all_counts(conn))
        self.assertEqual(changes, conn.total_changes)


if __name__ == "__main__":
    unittest.main()
