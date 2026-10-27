"""The judge's golden-set eval: the file, the arithmetic, and a run on a stub model (Part of #6 and #15)."""
import dataclasses
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from outreach.judge import __main__ as cli
from outreach.judge.config import load_judge_config
from outreach.judge.evaluate import (GOLDEN_PATH, Golden, golden_score, load_golden, resolve, run_eval, score,
                                     spearman)
from outreach.ranking.common import canonical_ids
from outreach.ranking.judged import load_judgments
from tests.plan_seed import seed_scored_company
from tests.storetest import fresh_store
from tests.test_judge_run import ME, StubReader, StubRunner, _Keep

T0 = datetime(2026, 10, 27, 10, 10, tzinfo=timezone.utc)


def g(name, verdict, confidence, domains=()):
    return Golden(name=name, domains=tuple(domains), verdict=verdict, confidence=confidence)


class GoldenFile(unittest.TestCase):
    def test_the_shipped_set_is_the_owners_verdicts(self):
        got = {x.name: (x.verdict, x.confidence) for x in load_golden(GOLDEN_PATH)}
        self.assertEqual({
            "Sim": ("yes", 70), "Metaview": ("yes", 65), "Hone": ("yes", 55), "Clark": ("yes", 55),
            "Exa": ("yes", 50), "Roadrunner": ("yes", 50), "Prophet Security": ("yes", 50), "AgentMail": ("yes", 45),
            "Tessera Labs": ("maybe", 45), "matx": ("no", 70), "Laminar": ("no", 60), "Conversion": ("no", 60),
            "Clera": ("no", 55)}, got)

    def test_a_bad_entry_is_refused(self):
        for entry in ('name = "A"\nverdict = "sure"\nconfidence = 50', 'name = "A"\nverdict = "yes"\nconfidence = 150',
                      'name = ""\nverdict = "yes"\nconfidence = 50', 'verdict = "yes"\nconfidence = 50'):
            with self.subTest(entry=entry):
                path = Path(tempfile.mkdtemp()) / "golden.toml"
                path.write_text("[[company]]\n" + entry + "\n", encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_golden(path)


class Arithmetic(unittest.TestCase):
    def test_golden_score_is_signed_confidence(self):
        self.assertEqual((70, -60, 0), (golden_score(g("a", "yes", 70)), golden_score(g("b", "no", 60)),
                                        golden_score(g("c", "maybe", 45))))

    def test_spearman(self):
        self.assertAlmostEqual(1.0, spearman([1, 2, 3, 4], [10, 20, 30, 40]))
        self.assertAlmostEqual(-1.0, spearman([1, 2, 3, 4], [9, 7, 5, 1]))
        # ties take the average rank: x ranks 1, 2.5, 2.5, 4 against y ranks 1, 2, 3, 4
        self.assertAlmostEqual(0.9486832980505138, spearman([1, 2, 2, 3], [1, 2, 3, 4]))
        self.assertIsNone(spearman([1, 2], [1, 2]), "too few to tell")
        self.assertIsNone(spearman([1, 1, 1], [1, 2, 3]), "a constant list has no order")

    def test_agreement_counts_yes_and_no_only_and_maybe_only_in_the_correlation(self):
        golden = [g("Sim", "yes", 70), g("Exa", "yes", 50), g("matx", "no", 70), g("Laminar", "no", 60),
                  g("Tessera", "maybe", 45), g("Gone", "yes", 40)]
        fits = {"Sim": 85, "Exa": 40, "matx": 20, "Laminar": 65, "Tessera": 50, "Gone": None}
        r = score(golden, fits, yes_at=60)
        self.assertEqual((2, 4), (r.agree, r.counted))  # Sim yes and matx no agree; Exa and Laminar do not
        self.assertEqual(5, r.judged)
        by = {row.golden.name: row for row in r.rows}
        self.assertEqual(("yes", True), (by["Sim"].judge_verdict, by["Sim"].agree))
        self.assertEqual(("no", False), (by["Exa"].judge_verdict, by["Exa"].agree))
        self.assertIsNone(by["Tessera"].agree)
        self.assertIsNone(by["Gone"].fit)
        want = spearman([70, 50, -70, -60, 0], [85, 40, 20, 65, 50])
        self.assertAlmostEqual(want, r.rho)
        text = r.text()
        self.assertIn("2 of 4", text)
        self.assertIn("not judged", text)
        self.assertFalse(any(d in text for d in (chr(0x2013), chr(0x2014))))


class Resolve(unittest.TestCase):
    def test_by_domain_first_then_by_name(self):
        conn = fresh_store()
        a = seed_scored_company(conn, "Laminar", 50, ("level_role",))
        b = seed_scored_company(conn, "Laminar Inc", 40, ("level_role",))
        conn.execute("INSERT INTO evidence(company_id, fact, value, confidence, source_kind, recorded_at) "
                     "VALUES (?, 'domain', 'runlaminar.com', 'confirmed', 'website', '2026-10-01T00:00:00Z')", (b,))
        c = seed_scored_company(conn, "Exa", 30, ("level_role",))
        got = resolve(conn, canonical_ids(conn), [g("Laminar", "no", 60, ["runlaminar.com"]), g("Exa", "yes", 50),
                                                  g("Laminar", "no", 60), g("Nobody", "yes", 50)])
        self.assertEqual([b, c, None, None], got, "a name two companies share is ambiguous, not a guess")
        self.assertNotEqual(a, got[0])


class RunEval(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.ids = {}
        for name, domain in (("Sim", "sim.ai"), ("matx", "matx.com")):
            cid = seed_scored_company(self.conn, name, 50, ("level_role",))
            self.conn.execute("INSERT INTO evidence(company_id, fact, value, confidence, source_kind, recorded_at) "
                              "VALUES (?, 'domain', ?, 'confirmed', 'website', '2026-10-01T00:00:00Z')", (cid, domain))
            self.ids[name] = cid
        self.golden = [g("Sim", "yes", 70, ["sim.ai"]), g("matx", "no", 70, ["matx.com"]), g("Ghost", "yes", 50)]

    def test_missing_judgments_are_made_once_each_even_with_the_job_off(self):
        fits = {"Sim": 88, "matx": 15}
        runner = StubRunner()
        runner.fit = None

        def answer(msg):
            name = msg["company"]["name"]
            ok = [p for p in msg["pages"] if p["status"] == "ok"]
            from tests.test_judge_run import ok_result
            return ok_result(json.dumps({"company_id": msg["company"]["company_id"], "fit": fits[name],
                                         "builds": f"Builds {name} things.", "domains": ["other"], "why": "w.",
                                         "risk": "r.", "level_ok": "unknown",
                                         "evidence": [{"page": "home", "quote": " ".join(ok[0]["text"].split()[:4])}]}))
        runner.answer = answer
        off = load_judge_config()
        self.assertFalse(off.enabled)
        report = run_eval(self.conn, self.golden, cfg=off, runner=runner, reader=StubReader(), me_text=ME, now=T0,
                          judge_missing=True)
        self.assertEqual(2, len(runner.calls))
        self.assertEqual((2, 2), (report.agree, report.counted))
        stored = load_judgments(self.conn, canonical_ids(self.conn))
        self.assertEqual({self.ids["Sim"]: 88, self.ids["matx"]: 15}, {c: j.fit for c, j in stored.items()})
        again = StubRunner()
        run_eval(self.conn, self.golden, cfg=off, runner=again, reader=StubReader(), me_text=ME, now=T0,
                 judge_missing=True)
        self.assertEqual([], again.calls, "a judgment under the current version is reused")

    def test_stored_only_never_calls_the_model(self):
        runner = StubRunner()
        report = run_eval(self.conn, self.golden, cfg=load_judge_config(), runner=runner, reader=StubReader(),
                          me_text=ME, now=T0, judge_missing=False)
        self.assertEqual([], runner.calls)
        self.assertEqual(0, report.judged)

    def test_the_cli_outside_the_live_checkout_scores_what_is_stored(self):
        out = io.StringIO()
        runner = StubRunner()
        with mock.patch.object(cli.live_checkout, "is_live", return_value=False), \
                mock.patch("outreach.judge.evaluate.connect", return_value=_Keep(self.conn)):
            code = cli.main(["eval"], stdout=out, runner=runner, reader=StubReader(), me_text=ME)
        self.assertEqual(0, code)
        self.assertEqual([], runner.calls)
        self.assertIn("not the live checkout", out.getvalue())
        self.assertIn("Sim", out.getvalue())


if __name__ == "__main__":
    unittest.main()
