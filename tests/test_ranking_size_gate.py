"""The size gate (2026-10-27): a giant or a probably large company is never a pick.

Twitch (Amazon) was picked with no facts at all, because the over-300 hide fired only on a size fact. Now a company
is hidden as `over_size` when it is on the giants list (`outreach/ranking/giants.toml`), or when its size is not on
file and it has more open roles than `[hide] likely_large_open_roles` or more software H-1B filings in one fiscal
year than `[hide] likely_large_lca_software`. The hide only keeps it off the ranked list, so the plan, the fresh lane
and the bench never see it; nothing is deleted and no status changes. Unknown size with no sign of being large still
passes: most startups have no size on file."""
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from outreach.plan.build import build_plan
from outreach.plan.config import default_plan_config
from outreach.plan.fresh import plan_moment
from outreach.plan.picks import bench_picks
from outreach.ranking.config import CONFIG_PATH, default_ranker_config, load_ranker_config
from outreach.ranking.giants import Giant, default_giants, load_giants
from outreach.ranking.scores import hidden_list, ranked_list
from outreach.store.writes import add_alias, name_key
from tests import ranking_store as rs
from tests.test_ranking_job import TODAY, Base

CFG = default_ranker_config()
PLAN = default_plan_config()
ROLES = CFG.hide["likely_large_open_roles"]
LCA = CFG.hide["likely_large_lca_software"]
FRESH_SEEN = "2026-10-22T06:00:00Z"  # 2 a.m. in New York on TODAY: fresh at the plan's 08:00 moment


def lca(fy: int, software: int) -> str:
    return json.dumps({"fy": fy, "software": software, "jr": 0, "level1": 0, "newemp_jr": 0})


class GiantsListTests(unittest.TestCase):
    def test_the_shipped_list_names_the_companies_that_slipped_through(self):
        giants = default_giants()
        want = {"Twitch": "Amazon", "Whole Foods Market": "Amazon", "YouTube": "Alphabet",
                "GLG": "Gerson Lehrman Group",
                "ALTEN Technology USA": "ALTEN", "Intel": "", "Relativity Space": "", "Jain Global": ""}
        for name, parent in want.items():
            with self.subTest(name=name):
                got = giants.match([name_key(name)])
                self.assertIsNotNone(got, name)
                self.assertEqual(got.parent, parent)

    def test_case_punctuation_and_a_legal_suffix_do_not_matter_but_a_longer_name_does(self):
        giants = default_giants()
        for spelling in ("Twitch, Inc.", "TWITCH", "twitch llc"):
            with self.subTest(spelling=spelling):
                self.assertEqual(giants.match([name_key(spelling)]).name, "Twitch")
        self.assertIsNone(giants.match([name_key("Lucid Computing"), name_key("Twitch Labs Startup")]))
        self.assertIsNone(giants.match([]))

    def test_the_reason_names_the_parent(self):
        self.assertEqual(Giant("Twitch", "Amazon").reason(300), "Over 300 people: part of Amazon (giants list)")
        self.assertEqual(Giant("Intel", "").reason(300), "Over 300 people: Intel is on the giants list")
        self.assertEqual(Giant("Amazon", "Amazon").reason(300), "Over 300 people: Amazon is on the giants list")

    def test_a_bad_file_fails_loudly(self):
        bad = {
            "a name twice": ('[[group]]\nparent = "A"\nnames = ["Twitch"]\n'
                             '[[group]]\nparent = "B"\nnames = ["twitch, inc."]\n'),
            "no parent": '[[group]]\nnames = ["Twitch"]\n',
            "no names": '[[group]]\nparent = "Amazon"\nnames = []\n',
            "a name with no key": '[[group]]\nparent = "Amazon"\nnames = ["!!"]\n',
            "an unknown field": '[[group]]\nparent = "Amazon"\nnames = ["Twitch"]\nsize = 1000\n',
            "an unknown table": '[giants]\nnames = ["Twitch"]\n',
        }
        for case, text in bad.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as d:
                p = Path(d) / "giants.toml"
                p.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_giants(p)


class ConfigTests(unittest.TestCase):
    def config_with(self, old: str, new: str):
        text = CONFIG_PATH.read_text(encoding="utf-8")
        self.assertIn(old, text)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            p.write_text(text.replace(old, new), encoding="utf-8")
            return load_ranker_config(p)

    def test_the_shipped_thresholds(self):
        self.assertEqual((ROLES, LCA), (100, 100))

    def test_a_threshold_below_25_or_not_whole_is_refused(self):
        for key in ("likely_large_open_roles", "likely_large_lca_software"):
            for bad in ("24", "0", "100.5", "true"):
                with self.subTest(key=key, bad=bad), self.assertRaises(ValueError):
                    self.config_with(f"{key} = 100", f"{key} = {bad}")
            with self.subTest(key=key, missing=True), self.assertRaises(KeyError):
                self.config_with(f"{key} = 100\n", "")


class GateTests(Base):
    def new_grad(self, name: str, *, roles: int = 1) -> int:
        cid, board = self.company(name)
        self.role(cid, board)
        for _ in range(roles - 1):
            rs.posting(self.conn, cid, "Account Executive", board_id=board, posted_on="2026-10-17")
        return cid

    def hides(self) -> dict:
        return {s.company_id: s.hide for s in self.rank().scored}

    def test_twitch_and_intel_with_no_facts_are_hidden_as_over_size(self):
        twitch, intel, tiny = self.new_grad("Twitch"), self.new_grad("Intel"), self.new_grad("Tiny Labs")
        hides = self.hides()
        self.assertEqual((hides[twitch].kind, hides[twitch].provisional), ("over_size", False))
        self.assertEqual(hides[twitch].reason, "Over 300 people: part of Amazon (giants list)")
        self.assertEqual(hides[intel].reason, "Over 300 people: Intel is on the giants list")
        self.assertIsNone(hides[tiny], "an unknown size startup with one role is still shown")
        self.assertEqual([s.company_id for s in ranked_list(self.conn)], [tiny])
        hidden = {s.company_id: s.signals["hide"] for s in hidden_list(self.conn)}
        self.assertEqual(set(hidden), {twitch, intel})
        self.assertEqual(hidden[twitch]["kind"], "over_size")

    def test_a_giant_matches_through_a_name_alias_and_a_merged_duplicate(self):
        alias_co = self.new_grad("TI Holdco")
        add_alias(self.conn, alias_co, "Twitch Interactive, Inc.", "legal_name")
        add_alias(self.conn, alias_co, "Twitch", "dba")
        survivor = self.new_grad("Chipmaker Labs")
        dup = self.new_grad("Intel Corp")
        rs.merge(self.conn, dup, survivor)
        board_only = self.new_grad("Board Name Co")
        add_alias(self.conn, board_only, "greenhouse:twitch", "ats")  # a board key is not a name
        hides = self.hides()
        self.assertIn("Amazon", hides[alias_co].reason)
        self.assertIn("Intel", hides[survivor].reason)
        self.assertIsNone(hides[board_only])

    def test_a_size_he_set_by_hand_at_or_under_300_puts_a_giant_back(self):
        locked, estimate = self.new_grad("Twitch"), self.new_grad("Intel")
        self.conn.execute("INSERT INTO evidence(company_id, fact, value, num_low, num_high, confidence, locked, "
                          "source_kind, source_ref, recorded_at) VALUES (?, 'size', '250', 250, 300, 'estimate', 1, "
                          "'hand', '', '2026-10-20T08:00:00Z')", (locked,))
        rs.evidence(self.conn, estimate, "size", value="50", num_low=50, num_high=50, confidence="estimate")
        hides = self.hides()
        self.assertIsNone(hides[locked])
        self.assertEqual(hides[estimate].kind, "over_size", "an estimate is not his call: the list wins")

    def test_open_roles_over_the_threshold_hide_an_unknown_size_and_at_it_do_not(self):
        at, over = self.new_grad("At Edge", roles=ROLES), self.new_grad("Over Edge", roles=ROLES + 1)
        hides = self.hides()
        self.assertIsNone(hides[at])
        self.assertEqual(hides[over].kind, "over_size")
        self.assertEqual(hides[over].reason, f"Likely over 300 people, size not on file: {ROLES + 1} open roles")

    def test_a_known_size_is_never_overruled_by_open_roles(self):
        small = self.new_grad("Known Small", roles=ROLES + 50)
        rs.evidence(self.conn, small, "size", value="50-100", num_low=50, num_high=100, confidence="estimate")
        guess = self.new_grad("Guessed Small", roles=ROLES + 50)
        rs.evidence(self.conn, guess, "size", value="100-250", num_low=100, num_high=250, confidence="guess")
        hides = self.hides()
        self.assertIsNone(hides[small])
        self.assertIsNone(hides[guess])

    def test_lca_software_filings_over_the_threshold_hide_an_unknown_size_and_at_it_do_not(self):
        at, over, known = self.new_grad("At Lca"), self.new_grad("Over Lca"), self.new_grad("Known Lca")
        self.ev(at, "lca", lca(2025, LCA))
        self.ev(over, "lca", lca(2024, LCA + 1))
        self.ev(over, "lca", lca(2025, 3))
        self.ev(known, "lca", lca(2025, LCA + 1))
        rs.evidence(self.conn, known, "size", value="200", num_low=200, num_high=200, confidence="estimate")
        hides = self.hides()
        self.assertIsNone(hides[at])
        self.assertEqual(hides[over].reason, "Likely over 300 people, size not on file: "
                                             f"{LCA + 1} software H-1B filings in one fiscal year")
        self.assertIsNone(hides[known])

    def test_the_hide_changes_no_status_and_writes_no_no_go(self):
        ids = [self.new_grad("Twitch"), self.new_grad("Over Edge", roles=ROLES + 1)]
        self.conn.execute("UPDATE company SET status = 'working' WHERE company_id = ?", (ids[0],))
        before = self.all_rows("company")
        events = self.conn.execute("SELECT count(*) FROM event WHERE entity = 'company'").fetchone()[0]
        hides = self.hides()
        self.assertTrue(all(hides[c].kind == "over_size" for c in ids))
        self.assertEqual(self.all_rows("company"), before)
        self.assertEqual(self.all_rows("no_go"), [])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM event WHERE entity = 'company'").fetchone()[0], events)
        self.hides()  # a second run is the same
        self.assertEqual(self.all_rows("company"), before)

    def test_a_lifted_no_go_still_puts_a_giant_back(self):
        twitch = self.new_grad("Twitch")
        rs.no_go(self.conn, twitch, "over_300", lifted=True)
        self.assertIsNone(self.hides()[twitch])


class PicksTests(Base):
    """Through the real ranker into the plan, the fresh lane and the bench."""

    def fresh_new_grad(self, name: str, *, roles: int = 1) -> int:
        cid, board = self.company(name)
        pid = rs.posting(self.conn, cid, board_id=board, posted_on=TODAY.isoformat(), first_seen_at=FRESH_SEEN,
                         last_seen_at=FRESH_SEEN)
        rs.decision(self.conn, pid)
        for _ in range(roles - 1):
            rs.posting(self.conn, cid, "Account Executive", board_id=board, posted_on="2026-10-17")
        return cid

    def test_giants_and_probably_large_companies_are_never_picked_fresh_or_benched(self):
        names = {"Twitch": 1, "Intel": 1, "Many Roles": ROLES + 1, "Tiny Labs": 1, "Small Robotics": 1}
        ids = {n: self.fresh_new_grad(n, roles=k) for n, k in names.items()}
        self.rank()
        picked = build_plan(self.conn, today=TODAY, read_boards=False, dry_run=True).chosen
        bench, _ = bench_picks(self.conn, TODAY, plan_moment(TODAY, PLAN.get("plan", "timezone")), PLAN)
        names_of = {v: k for k, v in ids.items()}
        self.assertEqual({names_of[r.company_id] for r in picked}, {"Tiny Labs", "Small Robotics"})
        self.assertEqual({names_of[r.company_id] for r in picked if r.opening is not None},
                         {"Tiny Labs", "Small Robotics"}, "the fresh lane takes the small ones")
        self.assertEqual({names_of[b.prospect.company_id] for b in bench}, {"Tiny Labs", "Small Robotics"})
        self.assertTrue(all(b.early_fit for b in bench))

    def test_with_the_gate_off_by_lifting_the_giant_would_have_been_picked(self):
        """The control: the same world with Twitch put back by hand picks it, so the test above is not vacuous."""
        twitch = self.fresh_new_grad("Twitch")
        rs.no_go(self.conn, twitch, "over_300", lifted=True)
        self.rank()
        picked = build_plan(self.conn, today=TODAY, read_boards=False, dry_run=True).chosen
        self.assertEqual([r.company_id for r in picked], [twitch])


if __name__ == "__main__":
    unittest.main()
