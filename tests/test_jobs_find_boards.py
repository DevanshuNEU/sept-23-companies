"""The find_boards job (#31 Tasks 14 and 15): alias boards, the stapply lookup, tiers and the report, on the LCA and HN
fixtures after run_discover."""
import json
import unittest
from pathlib import Path

from outreach.sources.byhand import add_by_hand
from outreach.sources.hn import HnSource
from outreach.sources.jobs import FindBoardsReport, run_discover, run_find_boards
from outreach.sources.lca import LcaSource
from outreach.sources.stapply import StapplyDirectory
from outreach.sources.tiers import promote
from outreach.store import add_alias
from tests.storetest import fresh_store, seed_company, table_marks
from tests.test_hn import fixture_client
from tests.test_lca_source import FILES

STAPPLY = Path(__file__).parent / "fixtures" / "stapply"


def count(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


class FindBoardsBase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        run_discover(self.conn, [LcaSource(FILES), HnSource(fixture_client())])
        self.directory = StapplyDirectory.load(STAPPLY)

    def company(self, name):
        return self.conn.execute("SELECT company_id FROM company WHERE name = ?", (name,)).fetchone()[0]

    def boards(self):
        return {(r["ats"], r["token"]): dict(r) for r in self.conn.execute(
            "SELECT b.*, c.name AS company FROM board b JOIN company c USING (company_id)")}

    def run_job(self, **kw):
        kw.setdefault("directory", self.directory)
        return run_find_boards(self.conn, **kw)


class StapplyStepTest(FindBoardsBase):
    def test_nimbus_and_orchard_get_boards(self):
        run_id, report = self.run_job()
        boards = self.boards()
        nimbus, orchard = boards[("greenhouse", "nimbusrobotics")], boards[("ashby", "orchard")]
        self.assertEqual((nimbus["company"], orchard["company"]), ("Nimbus Robotics, Inc.", "Orchard"))
        for b in (nimbus, orchard):  # due now for #21
            self.assertEqual((b["active"], b["last_read"], b["next_read_at"], b["found_by"]),
                             (1, None, None, "stapply_name"))
        for alias in ("greenhouse:nimbusrobotics", "ashby:orchard"):
            row = self.conn.execute("SELECT company_id, source FROM company_alias WHERE alias_key = ?",
                                    (alias,)).fetchone()
            self.assertEqual(row["source"], "ats", alias)
        links = {(r["company_id"], r["source_ref"]) for r in self.conn.execute(
            "SELECT * FROM company_source WHERE source_name = 'stapply'")}
        self.assertEqual(links, {(self.company("Nimbus Robotics, Inc."), "greenhouse:nimbusrobotics"),
                                 (self.company("Orchard"), "ashby:orchard"),
                                 (self.company("Mesh Systems Inc"), "greenhouse:meshsystems")})
        event = self.conn.execute("SELECT * FROM event WHERE entity = 'board' AND entity_id = ?",
                                  (nimbus["board_id"],)).fetchone()
        self.assertEqual((event["kind"], event["actor"], event["company_id"]),
                         ("found", "crawler", self.company("Nimbus Robotics, Inc.")))
        self.assertEqual(json.loads(event["detail"]), {"via": "stapply", "matched_on": "name"})
        source = self.conn.execute("SELECT version, last_run_id FROM source WHERE source_name = 'stapply'").fetchone()
        self.assertEqual(tuple(source), (self.directory.version, run_id))
        self.assertEqual((report.from_stapply, report.from_alias, report.version),
                         (3, 1, self.directory.version))  # 3 by stapply, Tidepool's lever alias
        self.assertEqual(report.conflicts, 1)  # the HN Mesh Systems: the LCA Mesh Systems holds the board
        job = self.conn.execute("SELECT job, status, error, summary FROM job_run WHERE run_id = ?",
                                (run_id,)).fetchone()
        self.assertEqual((job["job"], job["status"], job["error"]), ("find_boards", "ok", ""))
        self.assertEqual(json.loads(job["summary"])["from_stapply"], 3)

    def test_a_host_match_is_found_by_import(self):
        hand = add_by_hand(self.conn, name="Wholly Different Name", reason="a friend works there",
                           domain="plumehealth.io").company_id
        self.run_job()
        board = self.boards()[("lever", "plumehealth")]
        self.assertEqual((board["company_id"], board["found_by"]), (hand, "import"))
        event = self.conn.execute("SELECT detail FROM event WHERE entity = 'board' AND entity_id = ?",
                                  (board["board_id"],)).fetchone()
        self.assertEqual(json.loads(event["detail"]), {"via": "stapply", "matched_on": "host"})

    def test_a_slug_match_is_found_by_stapply_name(self):
        seed_company(self.conn, "Granite Peak Inc")
        self.run_job()
        self.assertEqual(self.boards()[("lever", "granite-peak")]["found_by"], "stapply_name")

    def test_the_legal_name_and_dba_aliases_are_looked_up_too(self):
        cid = seed_company(self.conn, "Some Brand")
        add_alias(self.conn, cid, "GP Labs LLC", "legal_name")
        self.run_job()
        self.assertEqual(self.boards()[("lever", "granite-peak")]["company_id"], cid)

    def test_tier_3_company_is_skipped(self):
        _, report = self.run_job()
        self.assertNotIn(("greenhouse", "lumenapps"), self.boards())  # a Greenhouse row exists in the fixture
        self.assertEqual(report.skipped_tier3, 4)  # Bridgeway, Depco, Lumen Apps, Quill
        self.assertEqual(count(self.conn, "SELECT count(*) FROM board WHERE company_id = ?",
                               self.company("Lumen Apps Inc")), 0)

    def test_promoted_tier_3_company_gets_its_board(self):
        promote(self.conn, self.company("Lumen Apps Inc"), "devanshu")
        _, report = self.run_job()
        self.assertEqual(self.boards()[("greenhouse", "lumenapps")]["company"], "Lumen Apps Inc")
        self.assertEqual(report.skipped_tier3, 3)

    def test_alias_becomes_a_board(self):
        hand = add_by_hand(self.conn, name="Zephyr Labs", reason="a friend works there", ats="lever:zephyr")
        _, report = self.run_job()
        board = self.boards()[("lever", "zephyr")]
        self.assertEqual((board["company_id"], board["found_by"]), (hand.company_id, "hand"))
        tidepool = self.boards()[("lever", "tidepoolapps")]  # an HN company's alias: not by hand
        self.assertEqual((tidepool["company"], tidepool["found_by"]), ("Tidepool Apps", "import"))
        event = self.conn.execute("SELECT detail FROM event WHERE entity = 'board' AND entity_id = ?",
                                  (board["board_id"],)).fetchone()
        self.assertEqual(json.loads(event["detail"])["via"], "alias")
        self.assertEqual(report.from_alias, 2)

    def test_bad_alias_token_is_counted_not_fatal(self):
        cid = seed_company(self.conn, "Broken Alias Co")
        add_alias(self.conn, cid, "greenhouse:Bad%Token", "ats")
        run_id, report = self.run_job()
        self.assertEqual(report.bad_aliases, [f"{cid}: greenhouse:Bad%Token"])
        self.assertIn(("greenhouse", "nimbusrobotics"), self.boards())  # the run went on
        job = self.conn.execute("SELECT status, error FROM job_run WHERE run_id = ?", (run_id,)).fetchone()
        self.assertEqual(job["status"], "partial")
        self.assertIn("greenhouse:Bad%Token", job["error"])

    def test_board_on_another_company_raises_one_item(self):
        self.run_job()
        copy = seed_company(self.conn, "Nimbus Robotics")  # the same name, another company
        owner = self.company("Nimbus Robotics, Inc.")
        _, report = self.run_job()
        self.assertEqual(report.conflicts, 2)  # the copy, and the HN Mesh Systems whose board the LCA one holds
        self.assertEqual(count(self.conn, "SELECT count(*) FROM board WHERE company_id = ?", copy), 0)
        items = [dict(r) for r in self.conn.execute(
            "SELECT * FROM review_item WHERE kind = 'merge_candidate' AND company_id = ?", (owner,))]
        self.assertEqual(len(items), 1)
        self.assertIn("same_board", items[0]["reason"])
        _, again = self.run_job()
        self.assertEqual(again.conflicts, 2)  # still seen, still no second item
        self.assertEqual(count(self.conn, "SELECT count(*) FROM review_item WHERE kind = 'merge_candidate' AND "
                                          "company_id = ?", owner), 1)

    def test_rerun_writes_nothing_new(self):
        self.run_job()
        before = table_marks(self.conn)
        _, report = self.run_job()
        self.assertEqual(table_marks(self.conn), before)
        self.assertEqual((report.from_alias, report.from_stapply), (0, 0))

    def test_companies_with_a_board_are_not_considered_again(self):
        _, first = self.run_job()
        _, second = self.run_job()
        self.assertEqual(first.considered, 14)  # 18 companies, 4 of tier 3
        self.assertEqual(second.considered, first.considered - 4)  # Tidepool, Nimbus, Orchard, LCA Mesh have boards
        self.assertEqual(second.skipped_tier3, 4)

    def test_no_directory_still_makes_alias_boards_and_says_the_lookup_did_not_run(self):
        run_id, report = self.run_job(directory=None)
        self.assertIn(("lever", "tidepoolapps"), self.boards())
        self.assertNotIn(("greenhouse", "nimbusrobotics"), self.boards())
        job = self.conn.execute("SELECT status, error FROM job_run WHERE run_id = ?", (run_id,)).fetchone()
        self.assertEqual(job["status"], "partial")
        self.assertIn("no stapply directory", job["error"])
        self.assertIsNone(self.conn.execute("SELECT 1 FROM source WHERE source_name = 'stapply'").fetchone())
        self.assertIsInstance(report, FindBoardsReport)

    def test_removed_and_closed_companies_are_not_considered(self):
        for status, extra in (("removed", ", remove_reason = 'dupe'"), ("closed", ", close_reason = 'not_now'")):
            cid = seed_company(self.conn, "Nimbus Robotics")
            self.conn.execute(f"UPDATE company SET status = '{status}'{extra} WHERE company_id = ?", (cid,))
        self.run_job()
        self.assertEqual(self.boards()[("greenhouse", "nimbusrobotics")]["company"], "Nimbus Robotics, Inc.")
        self.assertEqual(count(self.conn, "SELECT count(*) FROM review_item WHERE kind = 'merge_candidate'"), 1)


if __name__ == "__main__":
    unittest.main()
