"""Level priority (part of #6 and #15): "try to focus on early careers, new grad, SD1. Give them more priority."

The `level_role` lane's base value now follows the level tier of the best posting (`[live_role.tier_values]`): new
grad, early career and SWE I above SWE II and an open level. A title with no level whose posting asks 2 years or fewer
counts as early career. Only values move, so the order changes and nothing is hidden: no hide, no no-go row."""
import re
import tempfile
import unittest
from pathlib import Path

from outreach.ranking.config import CONFIG_PATH, default_ranker_config, load_ranker_config
from outreach.ranking.job import compute_rank, run_rank
from outreach.ranking.roles import EARLY_CAREER_TIERS, early_career_fit, rank_tier
from outreach.ranking.signals.live_role import LiveRole
from tests import ranking_store as rs
from tests.ranking_helpers import check_assessment, neutral_facts
from tests.storetest import fresh_store
from tests.test_ranking_job import TODAY, Base

CFG = default_ranker_config()
TIERS = ("new_grad", "early_career", "swe1", "swe2", "open")
# What the shipped config said before this change: every tier at the old level_role value.
FLAT_TIERS = "\n".join(f"{t} = 0.7" for t in TIERS)


def tier_block(text: str) -> tuple[int, int]:
    """The start and end of the [live_role.tier_values] table's body in the config text."""
    head = text.index("\n[live_role.tier_values]\n") + len("\n[live_role.tier_values]\n")
    nxt = text.find("\n[", head)
    return head, (len(text) if nxt == -1 else nxt + 1)


def config_with_tiers(body: str):
    """The shipped config with the [live_role.tier_values] body replaced by `body` (TOML lines)."""
    text = CONFIG_PATH.read_text(encoding="utf-8")
    head, end = tier_block(text)
    text = text[:head] + body.strip() + "\n\n" + text[end:]
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "config.toml"
        p.write_text(text, encoding="utf-8")
        return load_ranker_config(p)


class EarlyCareerFitTests(unittest.TestCase):
    def test_new_grad_early_career_and_swe1_fit(self):
        self.assertEqual(EARLY_CAREER_TIERS, ("new_grad", "early_career", "swe1"))
        for tier, basis in (("new_grad", "Title says new grad"), ("early_career", "Title says early career"),
                            ("early_career", "Posting says entry level"), ("swe1", "Title says Engineer I")):
            with self.subTest(tier=tier, basis=basis):
                self.assertTrue(early_career_fit(tier, basis))

    def test_swe2_open_senior_and_no_tier_do_not_fit(self):
        for tier, basis in (("swe2", "Title says Engineer II"), ("open", "MTS role, no level in the title"),
                            ("swe2", "Asks 3 years"), ("senior", "Title says Senior, above entry level"),
                            (None, ""), ("not_stated", "Posting does not state a level")):
            with self.subTest(tier=tier, basis=basis):
                self.assertFalse(early_career_fit(tier, basis))

    def test_a_title_with_no_level_that_asks_two_years_or_fewer_fits(self):
        # The rules store "Software Engineer" asking 2 years as swe2, basis "Asks 2 years", years_asked 2.
        for tier in ("swe2", "open"):
            for years in (0, 1, 2):
                with self.subTest(tier=tier, years=years):
                    self.assertTrue(early_career_fit(tier, f"Asks {years} years", years))
                    self.assertEqual(rank_tier(tier, f"Asks {years} years", years), "early_career")
            self.assertFalse(early_career_fit(tier, "Asks 3 years", 3))
            self.assertEqual(rank_tier(tier, "Asks 3 years", 3), tier)
            self.assertFalse(early_career_fit(tier, "Asks 12 years", 12))

    def test_a_title_level_is_never_overridden_by_years(self):
        self.assertEqual(rank_tier("swe2", "Title says II", 2), "swe2")  # "Software Engineer II" asking 2 years
        self.assertFalse(early_career_fit("swe2", "Title says Engineer II", 1))
        self.assertFalse(early_career_fit("senior", "Asks 2 years", 2))  # only open and swe2 read the years
        self.assertFalse(early_career_fit("not_stated", "Posting does not state a level", 1))
        self.assertTrue(early_career_fit("swe1", "", None))
        self.assertTrue(early_career_fit("swe1", "Title says Engineer I", 3))
        self.assertEqual(rank_tier(None, None, None), None)

    def test_with_no_years_nothing_is_promoted(self):
        # The two argument form the plan can call: the basis alone never promotes a tier.
        self.assertFalse(early_career_fit("swe2", "Asks 2 years"))
        self.assertFalse(early_career_fit("open", "MTS role, no level in the title"))
        self.assertTrue(early_career_fit("new_grad", "Title says new grad"))
        self.assertEqual(rank_tier("swe2", "Asks 2 years"), "swe2")


class TierValueTests(unittest.TestCase):
    def value(self, tier, basis="", age=90, years=None, **facts):
        return check_assessment(self, LiveRole(CFG.section("live_role")),
                                neutral_facts(lanes=("level_role",), best_role_age_days=age, best_title="T",
                                              best_level_tier=tier, best_level_basis=basis, best_years_asked=years,
                                              **facts))

    def test_the_shipped_tier_values(self):
        self.assertEqual(CFG.section("live_role")["tier_values"],
                         {"new_grad": 0.8, "early_career": 0.8, "swe1": 0.8, "swe2": 0.45, "open": 0.45})
        self.assertEqual(CFG.section("live_role")["level_role"], 0.7)
        self.assertEqual(CFG.section("live_role")["intern_junior"], 0.4)
        self.assertEqual(CFG.section("live_role")["any_us_role"], 0.15)

    def test_new_grad_is_point_eight(self):
        self.assertAlmostEqual(self.value("new_grad", "Title says new grad").value, 0.8)

    def test_early_career_is_point_eight(self):
        self.assertAlmostEqual(self.value("early_career", "Title says early career").value, 0.8)
        self.assertAlmostEqual(self.value("early_career", "Posting says entry level").value, 0.8)

    def test_swe1_is_point_eight(self):
        self.assertAlmostEqual(self.value("swe1", "Title says Engineer I").value, 0.8)

    def test_swe2_is_point_four_five(self):
        self.assertAlmostEqual(self.value("swe2", "Title says Engineer II").value, 0.45)
        self.assertAlmostEqual(self.value("swe2", "Asks 3 years", years=3).value, 0.45)
        self.assertAlmostEqual(self.value("swe2", "Title says II", years=2).value, 0.45)

    def test_open_is_point_four_five(self):
        self.assertAlmostEqual(self.value("open", "MTS role, no level in the title").value, 0.45)

    def test_no_level_asking_two_years_takes_the_early_career_value(self):
        self.assertAlmostEqual(self.value("swe2", "Asks 2 years", years=2).value, 0.8)
        self.assertAlmostEqual(self.value("open", "Asks 1 year", years=1).value, 0.8)
        self.assertAlmostEqual(self.value("swe2", "Asks 2 years").value, 0.45)  # no years stored: as stored

    def test_a_tier_with_no_value_falls_back_to_level_role(self):
        for tier in (None, "not_stated", "something_new"):
            with self.subTest(tier=tier):
                self.assertAlmostEqual(self.value(tier).value, 0.7)

    def test_the_freshness_bonus_is_unchanged_and_the_value_is_capped(self):
        self.assertEqual(self.value("new_grad", age=3).value, 1.0)       # 0.8 + 0.3 capped at 1.0
        self.assertAlmostEqual(self.value("new_grad", age=20).value, 0.95)
        self.assertAlmostEqual(self.value("new_grad", age=45).value, 0.85)
        self.assertAlmostEqual(self.value("swe2", "Title says II", age=3).value, 0.75)
        self.assertAlmostEqual(self.value("open", age=20).value, 0.6)
        self.assertAlmostEqual(self.value("swe2", "Title says II", age=3, best_dated=False).value, 0.45)
        for tier in TIERS:
            for age in (0, 3, 14, 20, 45, 90, None):
                with self.subTest(tier=tier, age=age):
                    self.assertLessEqual(self.value(tier, age=age).value, 1.0)

    def test_a_tier_value_of_one_is_still_capped_at_one(self):
        params = {**CFG.section("live_role"), "tier_values": {**CFG.section("live_role")["tier_values"],
                                                              "new_grad": 1.0}}
        got = check_assessment(self, LiveRole(params), neutral_facts(lanes=("level_role",), best_role_age_days=1,
                                                                     best_level_tier="new_grad"))
        self.assertEqual(got.value, 1.0)

    def test_intern_and_any_us_role_ignore_the_tier(self):
        intern = check_assessment(self, LiveRole(CFG.section("live_role")), neutral_facts(
            lanes=("intern_junior",), best_role_age_days=90, best_level_tier="new_grad"))
        self.assertAlmostEqual(intern.value, 0.4)
        anyrole = check_assessment(self, LiveRole(CFG.section("live_role")), neutral_facts(
            lanes=("any_us_role",), best_role_age_days=90, best_level_tier="new_grad"))
        self.assertAlmostEqual(anyrole.value, 0.15)

    def test_every_early_tier_scores_above_every_other_level_role_tier(self):
        values = CFG.section("live_role")["tier_values"]
        low = max(values["swe2"], values["open"])
        for tier in EARLY_CAREER_TIERS:
            self.assertGreater(values[tier], low)
            self.assertGreater(values[tier], CFG.section("live_role")["level_role"])
        self.assertGreater(low, CFG.section("live_role")["intern_junior"])  # interns stay the lowest real role


class TierReasonTests(unittest.TestCase):
    def reason(self, tier, basis, title="T", years=None):
        return check_assessment(self, LiveRole(CFG.section("live_role")), neutral_facts(
            lanes=("level_role",), best_role_age_days=3, best_title=title, best_level_tier=tier,
            best_level_basis=basis, best_years_asked=years)).reason

    def test_the_reason_names_the_tier(self):
        self.assertEqual(self.reason("new_grad", "Title says new grad", "Software Engineer, New Grad"),
                         "Open new grad role: Software Engineer, New Grad, posted 3 days ago")
        self.assertEqual(self.reason("early_career", "Title says early career"), "Open early career role: T, posted 3 days ago")
        self.assertEqual(self.reason("swe1", "Title says Engineer I"), "Open SWE I role: T, posted 3 days ago")
        self.assertEqual(self.reason("swe2", "Title says Engineer II", "Software Engineer II"),
                         "Open SWE II role, ranked after new grad and SWE I: Software Engineer II, posted 3 days ago")

    def test_a_level_from_the_posting_names_the_tier_and_the_basis(self):
        self.assertEqual(self.reason("swe2", "Asks 2 years", years=2),
                         "Open early career role (asks 2 years): T, posted 3 days ago")
        self.assertEqual(self.reason("early_career", "Posting says entry level"),
                         "Open early career role (posting says entry level): T, posted 3 days ago")
        self.assertEqual(self.reason("swe2", "Asks 3 years", years=3),
                         "Open SWE II role (asks 3 years), ranked after new grad and SWE I: T, posted 3 days ago")

    def test_an_open_level_says_so_and_that_it_ranks_after_new_grad_and_swe1(self):
        self.assertEqual(self.reason("open", "MTS role, no level in the title", "MTS"),
                         "Open role, no level in the title, ranked after new grad and SWE I: MTS, posted 3 days ago")


def _tier_value_lines(text: str) -> str:
    head, end = tier_block(text)
    return text[head:end]


class ConfigTests(unittest.TestCase):
    def test_every_tier_must_be_present(self):
        for tier in TIERS:
            body = "\n".join(f"{t} = 0.5" for t in TIERS if t != tier)
            with self.subTest(tier=tier), self.assertRaises(KeyError) as cm:
                config_with_tiers(body)
            self.assertIn(tier, str(cm.exception))

    def test_the_table_must_be_present(self):
        text = CONFIG_PATH.read_text(encoding="utf-8")
        head, end = tier_block(text)
        text = text[:head - len("[live_role.tier_values]\n")] + text[end:]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            p.write_text(text, encoding="utf-8")
            with self.assertRaises(KeyError) as cm:
                load_ranker_config(p)
        self.assertIn("tier_values", str(cm.exception))

    def test_each_value_must_be_a_number_from_zero_to_one(self):
        for bad in ("1.01", "-0.1", "true", '"high"', "nan", "inf"):
            body = "\n".join(f"{t} = {bad if t == 'swe1' else '0.5'}" for t in TIERS)
            with self.subTest(value=bad), self.assertRaises(ValueError) as cm:
                config_with_tiers(body)
            self.assertIn("swe1", str(cm.exception))
        for ok in ("0", "0.0", "1", "1.0"):
            body = "\n".join(f"{t} = {ok}" for t in TIERS)
            with self.subTest(value=ok):
                self.assertEqual(config_with_tiers(body).section("live_role")["tier_values"]["swe1"], float(ok))

    def test_an_unknown_tier_is_refused(self):
        body = "\n".join(f"{t} = 0.5" for t in TIERS) + "\nswe3 = 0.5"
        with self.assertRaises(ValueError) as cm:
            config_with_tiers(body)
        self.assertIn("swe3", str(cm.exception))

    def test_the_table_must_be_a_table(self):
        text = CONFIG_PATH.read_text(encoding="utf-8")
        head, end = tier_block(text)
        text = text[:head - len("[live_role.tier_values]\n")] + text[end:]
        text = re.sub(r"(?m)^level_role = 0\.7$", "level_role = 0.7\ntier_values = 0.8", text, count=1)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            p.write_text(text, encoding="utf-8")
            with self.assertRaises(ValueError) as cm:
                load_ranker_config(p)
        self.assertIn("tier_values", str(cm.exception))

    def test_the_weights_are_untouched_so_the_bottom_push_and_the_tilt_cap_hold(self):
        # Only values move: the weights (and so the bottom push and the tilt cap) are the same as before.
        w = CFG.weights
        others = sum(v for k, v in w.items() if k not in ("staffing_risk", "large_later_stage"))
        self.assertEqual(others, 126.0)
        self.assertEqual(w["live_role"], 40.0)
        self.assertGreater(w["staffing_risk"], others)
        self.assertGreater(w["large_later_stage"], others)
        self.assertLessEqual(max(CFG.section("live_role")["tier_values"].values()), 1.0)

    def test_the_flat_config_loads(self):
        flat = config_with_tiers(FLAT_TIERS)
        self.assertEqual(set(flat.section("live_role")["tier_values"].values()), {0.7})


class GoldenTests(Base):
    """The tier values only reorder: against every tier at the old 0.7, no hide and no no-go row changes, and the
    same companies are scored."""

    def seed_levels(self) -> dict[str, int]:
        ids = {}
        for key, title, tier, basis, years in (
                ("NewGrad", "Software Engineer, New Grad", "new_grad", "Title says new grad", None),
                ("Early", "Early Career Software Engineer", "early_career", "Title says early career", None),
                ("SweI", "Software Engineer I", "swe1", "Title says Engineer I", None),
                ("SweII", "Software Engineer II", "swe2", "Title says Engineer II", 2),
                ("Open", "Member of Technical Staff", "open", "MTS role, no level in the title", None),
                ("Asks2", "Backend Engineer", "swe2", "Asks 2 years", 2),
                ("Asks3", "Backend Engineer", "swe2", "Asks 3 years", 3)):
            cid, board = self.company(key)
            self.role(cid, board, title, posted_on="2026-07-01", level_tier=tier, level_basis=basis,
                      years_asked=years, years_quote=f"{years} years" if years is not None else "")
            ids[key] = cid
        cid, board = self.company("Refuses")  # a final refusal: hidden, one no-go row
        self.refusal(cid, board)
        ids["Refuses"] = cid
        cid, board = self.company("Pending")  # a provisional refusal: hidden, pending the reader
        self.refusal(cid, board, final=False)
        ids["Pending"] = cid
        cid, board = self.company("Big")  # over 300 with evidence: hidden by size
        self.ev(cid, "size", "400 people", num_low=400, num_high=400)
        self.role(cid, board)
        ids["Big"] = cid
        cid, _ = self.company("Banned", board=False)  # his own no-go
        rs.no_go(self.conn, cid)
        ids["Banned"] = cid
        cid, board = self.company("Later")  # later stage: the bottom push, never hidden
        self.ev(cid, "stage", "series_d")
        self.role(cid, board, "Software Engineer II", level_tier="swe2", level_basis="Title says Engineer II")
        ids["Later"] = cid
        cid, board = self.company("Intern")
        self.role(cid, board, "Software Engineer Intern", posted_on="2026-07-01", lane="intern", level_tier="not_stated",
                  level_basis="Title says intern")
        ids["Intern"] = cid
        return ids

    def test_tier_values_change_no_hide_and_no_no_go_row(self):
        ids = self.seed_levels()
        flat = config_with_tiers(FLAT_TIERS)
        shipped, _ = compute_rank(self.conn, today=TODAY)
        before, _ = compute_rank(self.conn, today=TODAY, cfg=flat)
        now, then = {s.company_id: s for s in shipped}, {s.company_id: s for s in before}
        self.assertEqual(set(now), set(then))
        for cid, s in now.items():
            with self.subTest(company=s.name):
                self.assertEqual(s.hide, then[cid].hide)
                self.assertEqual(s.flags, then[cid].flags)
                for name, p in s.values.items():  # only live_role moves
                    if name != "live_role":
                        self.assertEqual(p, then[cid].values[name])
        live = {k: now[ids[k]].values["live_role"].value for k in ids}
        old = {k: then[ids[k]].values["live_role"].value for k in ids}
        for key in ("NewGrad", "Early", "SweI", "Asks2"):
            self.assertAlmostEqual(live[key], 0.8)
            self.assertAlmostEqual(old[key], 0.7)
            self.assertGreater(now[ids[key]].score, then[ids[key]].score)
        for key in ("SweII", "Open", "Asks3"):
            self.assertAlmostEqual(live[key], 0.45)
            self.assertLess(now[ids[key]].score, then[ids[key]].score)
        self.assertEqual(live["Intern"], old["Intern"])
        self.assertAlmostEqual(live["Intern"], 0.4)
        self.assertEqual("no_sponsorship", now[ids["Refuses"]].hide.kind)
        self.assertTrue(now[ids["Pending"]].hide.provisional)
        self.assertEqual("over_size", now[ids["Big"]].hide.kind)
        self.assertEqual("no_go", now[ids["Banned"]].hide.kind)
        self.assertIsNone(now[ids["Later"]].hide)
        # The bottom push still holds: the later-stage company sorts below every company it does not hit.
        listed = [s for s in shipped if s.hide is None and s.company_id != ids["Later"]]
        self.assertTrue(all(s.score > now[ids["Later"]].score for s in listed))
        # An early-career role now outranks a SWE II one at the same company facts.
        self.assertGreater(now[ids["NewGrad"]].score, now[ids["SweII"]].score)
        self.assertGreater(now[ids["Asks2"]].score, now[ids["Asks3"]].score)

        def no_go_rows(conn):
            return [tuple(r) for r in conn.execute(
                "SELECT company_id, person_id, reason, source, lifted_at FROM no_go ORDER BY company_id, no_go_id")]

        run_rank(self.conn, today=TODAY)
        with_tiers = no_go_rows(self.conn)
        other = fresh_store()
        self.addCleanup(other.close)
        self.conn, mine = other, self.conn
        try:
            self.assertEqual(self.seed_levels(), ids)
            run_rank(other, today=TODAY, cfg=flat)
            with_flat = no_go_rows(other)
        finally:
            self.conn = mine
        self.assertEqual(with_tiers, with_flat)
        self.assertEqual(sorted([ids["Refuses"], ids["Banned"]]), sorted({r[0] for r in with_tiers}))


if __name__ == "__main__":
    unittest.main()
