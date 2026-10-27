"""The profile_fit signal (Part of #6 and #15): the judge's fit as a one-way lift. It only adds points, never lowers a
score, never hides; no judgment is 0 and says "Not judged yet"; it is capped like the interview tilt and versioned."""
import re
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from outreach.judge.store import record_judgment
from outreach.ranking.config import CONFIG_PATH, default_ranker_config, load_ranker_config
from outreach.ranking.job import compute_rank
from outreach.ranking.judged import Judgment
from outreach.ranking.signals import build_signals
from outreach.ranking.signals.profile_fit import ProfileFit
from outreach.ranking.version import RANKING_DIR, RANKING_FILES, ranker_version
from tests import ranking_store as rs
from tests.ranking_helpers import neutral_facts
from tests.storetest import fresh_store, seed_company

CFG = default_ranker_config()
TODAY = date(2026, 10, 22)
FIELDS = {"fit": 72, "builds": "Builds email inboxes for AI agents.", "domains": ["ai_agents"],
          "why": "Agent infrastructure is his work.", "risk": "No E-Verify statement.", "level_ok": "yes",
          "quotes": ["Acme builds email inboxes"], "evidence": [], "thin": False}


def judgment(fit=72, thin=False) -> Judgment:
    return Judgment(company_id=1, event_id=1, at="2026-10-20T06:10:00Z", fit=fit, builds=FIELDS["builds"],
                    domains=("ai_agents",), why=FIELDS["why"], risk=FIELDS["risk"], level_ok="yes",
                    quotes=() if thin else ("Acme builds email inboxes",), thin=thin, version="jv-1", key="k")


def edited(tc, *pairs):
    text = CONFIG_PATH.read_text(encoding="utf-8")
    for section, key, value in pairs:
        head = text.index(f"\n[{section}]\n") + 1
        nxt = text.find("\n[", head + 1)
        end = len(text) if nxt == -1 else nxt + 1
        block, n = re.subn(rf"(?m)^{key}\s*=.*$", f"{key} = {value}", text[head:end])
        tc.assertEqual(1, n, (section, key))
        text = text[:head] + block + text[end:]
    folder = Path(tempfile.mkdtemp())
    tc.addCleanup(shutil.rmtree, folder, ignore_errors=True)
    path = folder / "config.toml"
    path.write_text(text, encoding="utf-8")
    return load_ranker_config(path)


class Signal(unittest.TestCase):
    def signal(self):
        return ProfileFit(CFG.section("profile_fit"))

    def test_the_fit_is_the_value_and_the_judge_says_why(self):
        got = self.signal().score(neutral_facts(judgment=judgment(72)))
        self.assertAlmostEqual(0.72, got.value)
        self.assertIn("Builds email inboxes for AI agents.", got.reason)
        self.assertIn("Agent infrastructure is his work.", got.reason)
        self.assertIn("72", got.reason)
        self.assertEqual("medium", got.confidence.level)

    def test_thin_evidence_is_low_confidence(self):
        got = self.signal().score(neutral_facts(judgment=judgment(72, thin=True)))
        self.assertEqual("low", got.confidence.level)
        self.assertAlmostEqual(0.72, got.value)

    def test_no_judgment_is_zero_and_says_so(self):
        got = self.signal().score(neutral_facts())
        self.assertEqual((0.0, "Not judged yet", "low"), (got.value, got.reason, got.confidence.level))

    def test_profile_fit_never_goes_below_zero(self):
        for fit in range(0, 101):
            with self.subTest(fit=fit):
                self.assertGreaterEqual(self.signal().score(neutral_facts(judgment=judgment(fit))).value, 0.0)

    def test_it_is_the_last_signal(self):
        self.assertEqual("profile_fit", build_signals(CFG)[-1].name)


class Cap(unittest.TestCase):
    def test_the_shipped_weight_is_10_and_inside_its_cap(self):
        w = CFG.weights
        self.assertEqual(10.0, w["profile_fit"])
        top = sum(v for k, v in w.items() if k not in ("staffing_risk", "large_later_stage", "interview_like",
                                                         "profile_fit"))
        self.assertEqual(120.0, top)
        self.assertEqual(0.1, CFG.section("profile_fit")["max_weight_share"])
        self.assertLessEqual(w["profile_fit"], 0.1 * top)

    def test_profile_fit_weight_above_the_cap_is_refused(self):
        with self.assertRaises(ValueError) as cm:
            edited(self, ("weights", "profile_fit", "12.5"))
        self.assertIn("profile_fit", str(cm.exception))
        self.assertEqual(12.0, edited(self, ("weights", "profile_fit", "12.0")).weights["profile_fit"])

    def test_the_two_tilts_do_not_raise_each_others_cap(self):
        cfg = edited(self, ("weights", "profile_fit", "12.0"), ("weights", "interview_like", "12.0"))
        self.assertEqual((12.0, 12.0), (cfg.weights["profile_fit"], cfg.weights["interview_like"]))
        with self.assertRaises(ValueError):
            edited(self, ("weights", "profile_fit", "12.0"), ("weights", "interview_like", "12.5"))

    def test_zero_switches_it_off_and_below_zero_is_refused(self):
        self.assertEqual(0.0, edited(self, ("weights", "profile_fit", "0.0")).weights["profile_fit"])
        with self.assertRaises(ValueError):
            edited(self, ("weights", "profile_fit", "-1.0"))

    def test_its_share_and_age_are_checked(self):
        for value in ("0.0", "0.3", '"0.1"', "true"):
            with self.subTest(max_weight_share=value):
                with self.assertRaises(ValueError) as cm:
                    edited(self, ("profile_fit", "max_weight_share", value))
                self.assertIn("max_weight_share", str(cm.exception))
        for value in ("0", "366", "2.5", "true"):
            with self.subTest(max_age_days=value):
                with self.assertRaises(ValueError) as cm:
                    edited(self, ("profile_fit", "max_age_days", value))
                self.assertIn("max_age_days", str(cm.exception))


class Version(unittest.TestCase):
    def test_the_judgment_reader_and_the_signal_are_in_the_ranker_version(self):
        self.assertIn("judged.py", RANKING_FILES)
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        ranking = folder / "ranking"
        shutil.copytree(RANKING_DIR, ranking, ignore=shutil.ignore_patterns("__pycache__"))
        before = ranker_version(ranking)
        (ranking / "judged.py").write_text((ranking / "judged.py").read_text() + "\n# changed\n")
        after = ranker_version(ranking)
        (ranking / "signals" / "profile_fit.py").write_text("# changed\n")
        self.assertEqual(3, len({before, after, ranker_version(ranking)}))


class Rank(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.ids = {}
        for name in ("Judged", "Thin", "Unjudged", "Old", "Refuses"):
            cid = seed_company(self.conn, name)
            board = rs.board(self.conn, cid)
            pid = rs.posting(self.conn, cid, board_id=board, posted_on="2026-10-17")
            if name == "Refuses":
                rs.decision(self.conn, pid, provisional=0, sponsorship="no",
                            sponsorship_quote="We are unable to sponsor visas for this role.")
            else:
                rs.decision(self.conn, pid)
            self.ids[name] = cid
        pages = [{"kind": "home", "url": "https://a.example", "status": "ok", "text": "x"}]
        for name, fit, thin, at in (("Judged", 72, False, "2026-10-20T06:10:00Z"),
                                    ("Thin", 72, True, "2026-10-20T06:10:00Z"),
                                    ("Old", 90, False, "2026-09-01T06:10:00Z"),
                                    ("Refuses", 95, False, "2026-10-20T06:10:00Z")):
            record_judgment(self.conn, self.ids[name], {**FIELDS, "fit": fit, "thin": thin}, version="jv-1", key="k",
                            posting="p", pages=pages, at=at)

    def test_it_only_adds_points_and_never_hides_or_changes_the_rank_confidence(self):
        scored, _ = compute_rank(self.conn, today=TODAY)
        off, _ = compute_rank(self.conn, today=TODAY, cfg=edited(self, ("weights", "profile_fit", "0.0")))
        on, flat = {s.name: s for s in scored}, {s.name: s for s in off}
        for name in self.ids:
            with self.subTest(company=name):
                self.assertGreaterEqual(on[name].score, flat[name].score)
                self.assertEqual(on[name].hide, flat[name].hide)
                self.assertEqual(on[name].confidence, flat[name].confidence)
        self.assertAlmostEqual(10 * 0.72 * 0.85, on["Judged"].values["profile_fit"].points)  # medium: factor 0.85
        self.assertAlmostEqual(10 * 0.72 * 0.5, on["Thin"].values["profile_fit"].points)  # low: factor 0.5
        self.assertEqual((0.0, "Not judged yet"), (on["Unjudged"].values["profile_fit"].points,
                                                   on["Unjudged"].values["profile_fit"].reason))
        self.assertEqual("Not judged yet", on["Old"].values["profile_fit"].reason, "older than max_age_days")
        self.assertEqual("no_sponsorship", on["Refuses"].hide.kind, "a good fit never lifts a hide")


if __name__ == "__main__":
    unittest.main()
