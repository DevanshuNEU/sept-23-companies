"""The labeled set and the sitting commands (#32, Task 7).

Every item here is synthetic: made-up companies and bodies written for the test. Real labels never live in a test;
they reach `tests/gold/items.jsonl` only through Devanshu's own sittings (`gold confirm`). No network, and no store
except for `sample_gold`, which reads a scratch one.
"""
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from outreach.reader import __main__ as cli
from outreach.reader import gold
from outreach.reader.gold import (STRATA, TARGET_PER_CLASS, FieldReading, GoldItem, confirm, coverage, load_gold,
                                  pending, sample_gold, show_text)
from outreach.reader.types import FIELDS
from outreach.store import body_sha256
from tests.storetest import fresh_store, seed_company, seed_posting

TITLE = "Software Engineer, New Grad"
FILLER = " We build billing software for small clinics and ship to real customers every week." * 8


def make_body(lead: str = "") -> str:
    return (lead + FILLER).strip()


def blank_labels() -> dict:
    out = {}
    for name in FIELDS:
        if name in ("years_low", "years_high"):
            out[name] = {"value": None, "quote": ""}
        elif name in ("start_date", "location", "pay"):
            out[name] = {"value": "", "quote": ""}
        else:
            out[name] = {"value": "unknown", "quote": ""}
    return out


def item(gold_id="g0001", *, stratum="random", lead="We do not sponsor visas.", status="prelabeled",
         confirmed_on=None, title=TITLE, location="Austin, TX", **labels):
    out = blank_labels()
    out["engineering"] = {"value": "yes", "quote": "Software Engineer, New Grad"}
    for name, (value, quote) in labels.items():
        out[name] = {"value": value, "quote": quote}
    return {"gold_id": gold_id, "stratum": stratum, "title": title, "location": location, "body": make_body(lead),
            "labels": out, "status": status, "confirmed_on": confirmed_on}


class GoldCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name)
        self.path = self.dir / "items.jsonl"

    def write(self, *items):
        self.path.write_text("".join(json.dumps(i) + "\n" for i in items), encoding="utf-8")
        return self.path


class LoadGoldTests(GoldCase):
    def test_load_gold_accepts_a_valid_item(self):
        self.write(item(sponsorship=("wont_sponsor", "We do not sponsor visas.")),
                   item("g0002", status="confirmed", confirmed_on="2026-10-24"))
        items = load_gold(self.path)
        self.assertEqual([i.gold_id for i in items], ["g0001", "g0002"])
        first = items[0]
        self.assertIsInstance(first, GoldItem)
        self.assertEqual(first.labels["sponsorship"], FieldReading("wont_sponsor", "We do not sponsor visas."))
        self.assertEqual(set(first.labels), set(FIELDS))
        self.assertEqual((items[1].status, items[1].confirmed_on), ("confirmed", "2026-10-24"))

    def test_load_gold_rejects_each_bad_case(self):
        missing = item()
        del missing["labels"]["pay"]
        outside = item(sponsorship=("maybe_sponsor", "We do not sponsor visas."))
        paraphrase = item(sponsorship=("wont_sponsor", "no visa sponsorship is offered"))
        cases = {
            "missing field": ([item("g0001"), missing | {"gold_id": "g0002"}], 2, "pay"),
            "value outside ALLOWED": ([outside], 1, "sponsorship"),
            "quote not in the text": ([paraphrase], 1, "sponsorship"),
            "duplicate id": ([item("g0001"), item("g0001")], 2, "g0001"),
            "confirmed with no date": ([item(status="confirmed")], 1, "confirmed_on"),
        }
        for name, (rows, line, word) in cases.items():
            with self.subTest(name):
                self.write(*rows)
                with self.assertRaises(ValueError) as caught:
                    load_gold(self.path)
                self.assertIn(f"line {line}", str(caught.exception))
                self.assertIn(word, str(caught.exception))

    def test_level_quote_may_come_from_the_title(self):
        self.write(item(level=("new_grad", "Software Engineer, New Grad")))
        (only,) = load_gold(self.path)
        self.assertEqual(only.labels["level"].value, "new_grad")
        self.assertNotIn("new grad", only.body.lower())

    def test_an_unlabeled_prelabeled_item_loads_and_a_confirmed_one_must_be_complete(self):
        blank = item()
        blank["labels"] = {}
        self.write(blank)
        (only,) = load_gold(self.path)
        self.assertEqual(only.labels, {})
        self.write(blank | {"status": "confirmed", "confirmed_on": "2026-10-24"})
        with self.assertRaises(ValueError):
            load_gold(self.path)

    def test_the_shipped_gold_file_loads(self):
        self.assertTrue(gold.GOLD_PATH.is_file())
        self.assertTrue(gold.GOLD_READINGS.is_dir())
        load_gold()  # empty until Devanshu's first sitting; whatever it holds must load


class SampleGoldTests(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.company = seed_company(self.conn, "Fernbrook Clinics")

    def post(self, n, lead, *, title=TITLE, location="Austin, TX"):
        return [seed_posting(self.conn, self.company, title, make_body(f"{lead} Role {title} {location} {k}."),
                             location) for k in range(n)]

    def test_sample_gold_is_deterministic_and_skips_existing(self):
        self.post(6, "We do not sponsor visas.")
        self.post(6, "We are a small team.")
        first = sample_gold(self.conn, 2, seed=7, existing=set())
        again = sample_gold(self.conn, 2, seed=7, existing=set())
        self.assertEqual([i["body"] for i in first], [i["body"] for i in again])
        self.assertEqual([i["gold_id"] for i in first], [f"g{n:04d}" for n in range(1, len(first) + 1)])
        self.assertTrue(all(i["status"] == "prelabeled" and i["labels"] == {} for i in first))
        self.assertEqual([i["stratum"] for i in first].count("random"), 2, "random takes from what the classes left")
        seen = {body_sha256(i["body"]) for i in first}
        later = sample_gold(self.conn, 2, seed=7, existing=seen)
        self.assertTrue(later)
        self.assertFalse(seen & {body_sha256(i["body"]) for i in later})

    def test_sample_gold_fills_each_stratum_up_to_availability(self):
        self.post(2, "We do not sponsor visas.")
        self.post(3, "US citizenship is required for this role.")
        self.post(2, "We are a small team.", location="Toronto, Ontario, Canada")
        self.post(4, "We are a small team.", title="Software Engineering Intern")
        self.post(8, "We are a small team.")
        self.post(2, "We are a small team.", title="Account Executive")  # not engineering: never drawn
        drawn = sample_gold(self.conn, 5, seed=1, existing=set())
        counts = {}
        for i in drawn:
            counts[i["stratum"]] = counts.get(i["stratum"], 0) + 1
        self.assertEqual(counts.get("sponsorship:wont_sponsor"), 2)
        self.assertEqual(counts.get("citizenship_or_clearance:yes"), 3)
        self.assertEqual(counts.get("location:non_us"), 2)
        self.assertEqual(counts.get("level:intern"), 4)
        self.assertTrue(all(n <= 5 for n in counts.values()), counts)
        self.assertEqual(len(drawn), 19, "every eligible posting fits somewhere at 5 per stratum")
        self.assertEqual(len({i["body"] for i in drawn}), len(drawn), "a posting is drawn once")
        self.assertFalse(any(i["title"] == "Account Executive" for i in drawn))
        self.assertTrue(set(counts) <= {name for name, _, _ in STRATA})


class ConfirmTests(GoldCase):
    def test_confirm_marks_and_rewrites_atomically(self):
        self.write(item("g0001"), item("g0002", sponsorship=("wont_sponsor", "We do not sponsor visas.")))
        second_line = self.path.read_text(encoding="utf-8").splitlines()[1]
        done = confirm(self.path, "g0001", today="2026-10-24",
                       set_fields={"sponsorship": FieldReading("wont_sponsor", "We do not sponsor visas.")})
        self.assertEqual((done.status, done.confirmed_on), ("confirmed", "2026-10-24"))
        items = {i.gold_id: i for i in load_gold(self.path)}
        self.assertEqual((items["g0001"].status, items["g0001"].confirmed_on), ("confirmed", "2026-10-24"))
        self.assertEqual(items["g0001"].labels["sponsorship"].value, "wont_sponsor")
        self.assertEqual(self.path.read_text(encoding="utf-8").splitlines()[1], second_line, "other lines untouched")
        before = self.path.read_bytes()
        with mock.patch.object(gold.os, "replace", side_effect=OSError("disk gone")):
            with self.assertRaises(OSError):
                confirm(self.path, "g0002", today="2026-10-24")
        self.assertEqual(self.path.read_bytes(), before, "a crash before the replace leaves the file as it was")
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["items.jsonl"], "no temp file left behind")

    def test_confirm_refuses_a_correction_with_a_paraphrased_quote(self):
        self.write(item("g0001"))
        before = self.path.read_bytes()
        with self.assertRaises(ValueError) as caught:
            confirm(self.path, "g0001", today="2026-10-24",
                    set_fields={"sponsorship": FieldReading("wont_sponsor", "they cannot sponsor anyone")})
        self.assertIn("sponsorship", str(caught.exception))
        self.assertEqual(self.path.read_bytes(), before)

    def test_confirm_refuses_an_unknown_id_and_a_bad_date(self):
        self.write(item("g0001"))
        with self.assertRaises(ValueError):
            confirm(self.path, "g9999", today="2026-10-24")
        with self.assertRaises(ValueError):
            confirm(self.path, "g0001", today="24/10/2026")


class CoverageAndShowTests(GoldCase):
    def test_coverage_counts_confirmed_per_stratum(self):
        self.write(item("g0001", stratum="sponsorship:wont_sponsor", status="confirmed", confirmed_on="2026-10-24"),
                   item("g0002", stratum="sponsorship:wont_sponsor"),
                   item("g0003", stratum="random", status="confirmed", confirmed_on="2026-10-24"))
        cov = coverage(load_gold(self.path))
        self.assertEqual(set(cov), {name for name, _, _ in STRATA})
        self.assertEqual(cov["sponsorship:wont_sponsor"], (1, TARGET_PER_CLASS))
        self.assertEqual(cov["random"], (1, TARGET_PER_CLASS))
        self.assertEqual(cov["level:intern"], (0, TARGET_PER_CLASS))
        self.assertEqual([i.gold_id for i in pending(load_gold(self.path))], ["g0002"])

    def test_coverage_prints_the_rule_of_three_bound_for_hide_classes(self):
        self.write(item("g0001", stratum="sponsorship:wont_sponsor", status="confirmed", confirmed_on="2026-10-24",
                        sponsorship=("wont_sponsor", "We do not sponsor visas.")))
        out = io.StringIO()
        code = cli.main(["gold", "--file", str(self.path), "coverage"], stdout=out)
        self.assertEqual(code, 0)
        self.assertIn("n=1, 0 errors would bound the error rate at 3/1", out.getvalue())

    def test_show_prints_pending_items_with_prelabels(self):
        self.write(item("g0001", sponsorship=("wont_sponsor", "We do not sponsor visas."),
                        level=("new_grad", "Software Engineer, New Grad")))
        text = show_text(pending(load_gold(self.path)))
        for name in FIELDS:
            self.assertIn(name, text)
        self.assertIn("We do not sponsor visas.", text)
        self.assertIn("g0001", text)
        self.assertIn(TITLE, text)

    def test_confirm_all_shown_confirms_only_what_show_printed_untouched(self):
        self.write(item("g0001"), item("g0002"), item("g0003"))
        out = io.StringIO()
        self.assertEqual(cli.main(["gold", "--file", str(self.path), "show", "--limit", "2"], stdout=out), 0)
        # g0002 is edited after the sitting saw it, so it is not "untouched" any more.
        rows = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]
        rows[1]["labels"]["sponsorship"] = {"value": "wont_sponsor", "quote": "We do not sponsor visas."}
        self.write(*rows)
        out = io.StringIO()
        self.assertEqual(cli.main(["gold", "--file", str(self.path), "confirm", "--all-shown"], stdout=out), 0)
        status = {i.gold_id: i.status for i in load_gold(self.path)}
        self.assertEqual(status, {"g0001": "confirmed", "g0002": "prelabeled", "g0003": "prelabeled"})
        self.assertIn("g0002", out.getvalue())

    def test_confirm_on_the_command_line_takes_a_correction(self):
        self.write(item("g0001"))
        out = io.StringIO()
        code = cli.main(["gold", "--file", str(self.path), "confirm", "g0001", "--set", "sponsorship=wont_sponsor",
                         "--quote", "We do not sponsor visas."], stdout=out)
        self.assertEqual(code, 0)
        (only,) = load_gold(self.path)
        self.assertEqual((only.status, only.labels["sponsorship"].value), ("confirmed", "wont_sponsor"))
        code = cli.main(["gold", "--file", str(self.path), "confirm", "g0001", "--set", "sponsorship=wont_sponsor",
                         "--quote", "they cannot sponsor"], stdout=io.StringIO())
        self.assertEqual(code, 1)


class SampleCliTests(unittest.TestCase):
    def test_gold_sample_appends_prelabeled_items_to_the_file(self):
        conn = fresh_store()
        self.addCleanup(conn.close)
        company = seed_company(conn, "Fernbrook Clinics")
        for k in range(3):
            seed_posting(conn, company, TITLE, make_body(f"We do not sponsor visas. Role {k}."), "Austin, TX")
        path_db = conn.execute("PRAGMA database_list").fetchone()["file"]
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {"OUTREACH_DB": path_db}):
            path = Path(folder) / "items.jsonl"
            path.write_text(json.dumps(item("g0001")) + "\n", encoding="utf-8")
            code = cli.main(["gold", "--file", str(path), "sample", "--per-class", "2", "--seed", "3"],
                            stdout=io.StringIO())
            self.assertEqual(code, 0)
            items = load_gold(path)
            self.assertEqual(items[0].gold_id, "g0001")
            self.assertEqual([i.gold_id for i in items[1:]], [f"g{n:04d}" for n in range(2, len(items) + 1)])
            self.assertGreaterEqual(len(items), 3)


if __name__ == "__main__":
    unittest.main()
