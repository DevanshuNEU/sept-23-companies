"""Eval per field and class, and the promotion gate (#32, Task 8).

Every gold item and every reading here is synthetic and built in the test; no runner is real (the one run test
passes a fake), no store is opened except where the CLI's promote guard is checked, and nothing reaches the network.
"""
import dataclasses
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from outreach.reader import __main__ as cli
from outreach.reader import evaluate
from outreach.reader.evaluate import (MIN_CLASS_N, MIN_CONFIRMED, ClassScore, gate, load_readings, not_worse, promote,
                                      reader_view, rules_view, run_reader_on_gold, score, table_text)
from outreach.reader.config import CONFIG_PATH, load_reader_config
from outreach.reader.gold import GoldItem, load_gold
from outreach.reader.night import RunResult
from outreach.reader.types import FIELDS, FieldReading
from outreach.reader.version import reader_version

TITLE = "Software Engineer, New Grad"
FILLER = " We build billing software for small clinics and ship to real customers every week." * 8
REFUSAL = "We do not sponsor visas."
CITIZEN = "US citizenship is required for this role."
STUDENT = "This role is open only to current students graduating in 2027."
FLIPPED = "Visa questions are welcome during the interview."
CANADA = "Toronto, Ontario, Canada"
OLD, NEW = "rv-000000000001", "rv-000000000002"


def blank() -> dict:
    out = {}
    for name in FIELDS:
        if name in ("years_low", "years_high"):
            out[name] = FieldReading(None, "")
        elif name in ("start_date", "location", "pay"):
            out[name] = FieldReading("", "")
        else:
            out[name] = FieldReading("unknown", "")
    return out


def make(gold_id, lead="", *, where="Austin, TX", status="confirmed", **labels) -> GoldItem:
    """A confirmed item posted at `where`; `labels` replace whole labels as (value, quote)."""
    got = blank()
    got["engineering"] = FieldReading("yes", TITLE)
    got["sponsorship"] = FieldReading("silent", "")
    for name, (value, quote) in labels.items():
        got[name] = FieldReading(value, quote)
    return GoldItem(gold_id, "random", TITLE, where, (lead + FILLER).strip(), got, status,
                    "2026-10-24" if status == "confirmed" else None)


def fields(item: GoldItem, **over) -> dict:
    """A reading's `fields` JSON: the item's labels (a perfect reader), then `over` as (value, quote)."""
    out = {n: {"value": f.value, "quote": f.quote} for n, f in item.labels.items()}
    for name, (value, quote) in over.items():
        out[name] = {"value": value, "quote": quote}
    return out


def perfect(items) -> dict:
    return {i.gold_id: fields(i) for i in items}


def big_set(per_hide: int = 25, fine: int = 220) -> list[GoldItem]:
    """A gold set the gate can evaluate: `per_hide` items of each hide class and `fine` plain ones."""
    items, n = [], 0

    def add(*args, **kw):
        nonlocal n
        n += 1
        items.append(make(f"g{n:04d}", *args, **kw))

    for k in range(per_hide):
        add(f"{REFUSAL} Team {k}.", sponsorship=("wont_sponsor", REFUSAL))
        add(f"{CITIZEN} Team {k}.", citizenship_or_clearance=("yes", CITIZEN))
        add(f"{STUDENT} Team {k}.", student_or_grad_window=("yes", STUDENT))
        add(f"Team {k} works on site.", where=CANADA, location=(CANADA, CANADA))
    for k in range(fine):
        add(f"Team {k} works on site.", location=("Austin, TX", "Austin, TX"))
    return items


def by_key(scores):
    return {(s.field, s.value, s.predictor): s for s in scores}


class ViewTests(unittest.TestCase):
    def test_rules_view_maps_each_field(self):
        refusal = rules_view(make("g1", REFUSAL))
        self.assertEqual(refusal["sponsorship"], "wont_sponsor")
        self.assertEqual(refusal["engineering"], "yes")
        self.assertEqual(refusal["level"], "new_grad")
        self.assertEqual(refusal["location"], "us")
        self.assertEqual(rules_view(make("g2", CITIZEN))["citizenship_or_clearance"], "yes")
        self.assertEqual(rules_view(make("g3", STUDENT))["student_or_grad_window"], "yes")
        self.assertEqual(rules_view(make("g4", "", where=CANADA))["location"], "non_us")
        self.assertEqual(rules_view(make("g5", FLIPPED))["sponsorship"], "silent")
        self.assertEqual(rules_view(make("g6", "We sponsor work visas."))["sponsorship"], "will_sponsor")
        plain = rules_view(make("g7"))
        self.assertEqual((plain["citizenship_or_clearance"], plain["student_or_grad_window"]), ("unknown", "unknown"))
        self.assertEqual(set(plain), set(FIELDS))
        self.assertIsNone(plain["pay"])

    def test_reader_view_blanks_an_unquoted_fact(self):
        item = make("g1", REFUSAL, sponsorship=("wont_sponsor", REFUSAL))
        seen = reader_view(fields(item, sponsorship=("wont_sponsor", "no visa sponsorship is offered")), item)
        self.assertEqual(seen["sponsorship"], "unknown")
        self.assertEqual(reader_view(fields(item), item)["sponsorship"], "wont_sponsor")
        broken = fields(item)
        del broken["level"]
        broken["remote"] = {"value": "perhaps", "quote": ""}
        seen = reader_view(broken, item)
        self.assertEqual((seen["level"], seen["remote"]), ("unknown", "unknown"))
        scores = by_key(score([item], {"g1": fields(item, sponsorship=("wont_sponsor", "no visa sponsorship"))}))
        self.assertEqual(scores[("sponsorship", "wont_sponsor", "reader")].fn, 1)


class ScoreTests(unittest.TestCase):
    def small_set(self):
        refusals = [make(f"g{k}", f"{REFUSAL} Team {k}.", sponsorship=("wont_sponsor", REFUSAL)) for k in (1, 2, 3)]
        silent = make("g4", FLIPPED)
        plain = [make("g5"), make("g6", "We sponsor work visas.", sponsorship=("will_sponsor", "We sponsor work visas"))]
        items = refusals + [silent] + plain
        readings = perfect(items)
        readings["g3"] = fields(refusals[2], sponsorship=("unknown", ""))           # a miss
        readings["g4"] = fields(silent, sponsorship=("wont_sponsor", "Visa questions are welcome"))  # a false hide
        return items, readings

    def test_score_precision_and_recall_per_class(self):
        items, readings = self.small_set()
        s = by_key(score(items, readings))[("sponsorship", "wont_sponsor", "reader")]
        self.assertEqual((s.tp, s.fp, s.fn, s.n_truth), (2, 1, 1, 3))
        self.assertEqual(s.precision, 2 / 3)
        self.assertEqual(s.recall, 2 / 3)
        rules = by_key(score(items, readings))[("sponsorship", "wont_sponsor", "rules")]
        self.assertEqual((rules.tp, rules.fp, rules.precision, rules.recall), (3, 0, 1.0, 1.0))

    def test_both_needs_agreement(self):
        items, readings = self.small_set()
        both = by_key(score(items, readings))[("sponsorship", "wont_sponsor", "both")]
        # g4: reader wont_sponsor, rules silent, so both predicts unknown: no false hide. g3: reader unknown.
        self.assertEqual((both.tp, both.fp, both.fn), (2, 0, 1))
        self.assertEqual(both.precision, 1.0)
        self.assertEqual(both.recall, 2 / 3)

    def test_unconfirmed_items_are_ignored(self):
        items, readings = self.small_set()
        before = score(items, readings)
        extra = make("g9", f"{REFUSAL} Pending.", status="prelabeled", sponsorship=("wont_sponsor", REFUSAL))
        readings["g9"] = fields(extra, sponsorship=("unknown", ""))
        self.assertEqual(score(items + [extra], readings), before)

    def test_reader_only_fields_have_no_rules_rows(self):
        items, readings = self.small_set()
        keys = by_key(score(items, readings))
        self.assertIn(("remote", "no", "reader"), keys)
        self.assertNotIn(("remote", "no", "rules"), keys)
        self.assertNotIn(("pay", "stated", "both"), keys)

    def test_output_has_a_row_for_reader_rules_and_both(self):
        items, readings = self.small_set()
        text = table_text(score(items, readings))
        rows = [line for line in text.splitlines() if line.split()[:2] == ["sponsorship", "wont_sponsor"]]
        self.assertEqual([r.split()[2] for r in rows], ["reader", "rules", "both"])
        for r in rows:
            self.assertIn("n=3", r)
        self.assertIn("false hides 1 of 3", rows[0])
        self.assertIn("misses 1 of 3", rows[0])


def cs(field="sponsorship", value="wont_sponsor", predictor="both", tp=20, fp=0, fn=5, n_truth=25):
    return ClassScore(field, value, predictor, tp, fp, fn, n_truth, tp / (tp + fp) if tp + fp else None,
                      tp / n_truth if n_truth else None, 300)


class NotWorseTests(unittest.TestCase):
    def test_not_worse_flags_a_precision_drop_on_a_hide_class(self):
        old = [cs(tp=24, fp=0, fn=1)]
        new = [cs(tp=24, fp=1, fn=1)]
        lines = not_worse(new, old)
        self.assertEqual(len(lines), 1)
        self.assertIn("sponsorship", lines[0])
        self.assertIn("wont_sponsor", lines[0])
        self.assertIn("precision", lines[0])
        self.assertEqual(not_worse(old, old), [])
        self.assertEqual(len(not_worse([cs(tp=20, fn=5)], [cs(tp=24, fn=1)])), 1, "a recall drop is a regression")

    def test_not_worse_ignores_classes_missing_from_old(self):
        self.assertEqual(not_worse([cs(value="opt_only", tp=1, fp=5)], [cs()]), [])

    def test_not_worse_does_not_gate_a_class_under_twenty(self):
        self.assertEqual(MIN_CLASS_N, 20)
        small_old, small_new = cs(tp=19, fp=0, fn=0, n_truth=19), cs(tp=10, fp=4, fn=9, n_truth=19)
        self.assertEqual(not_worse([small_new], [small_old]), [])

    def test_not_worse_ignores_the_rules_rows(self):
        self.assertEqual(not_worse([cs(predictor="rules", tp=10, fp=10)], [cs(predictor="rules")]), [])


class GateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = big_set()
        cls.good = perfect(cls.items)
        cls.good_scores = score(cls.items, cls.good)

    def run_gate(self, readings, old=None, version=NEW, current=NEW):
        return gate(self.items, score(self.items, readings), old, readings=readings, version=version,
                    current_version=current)

    def test_a_perfect_reader_on_enough_labels_may_be_promoted(self):
        self.assertGreaterEqual(len(self.items), MIN_CONFIRMED)
        result = self.run_gate(self.good)
        self.assertEqual(result.verdict, "promote", result.text())

    def test_one_agreed_false_hide_rejects(self):
        readings = dict(self.good)
        fine = next(i for i in self.items if i.labels["sponsorship"].value == "silent")
        flipped = dataclasses.replace(fine, body=REFUSAL + " " + fine.body, gold_id=fine.gold_id)
        items = [flipped if i.gold_id == fine.gold_id else i for i in self.items]
        # The labels say the posting is fine (silent), the rules read the refusal and so does the reader.
        readings[fine.gold_id] = fields(flipped, sponsorship=("wont_sponsor", REFUSAL))
        result = gate(items, score(items, readings), None, readings=readings, version=NEW, current_version=NEW)
        self.assertEqual(result.verdict, "reject", result.text())
        self.assertIn("false hides", result.text())

    def test_misses_above_the_floor_do_not_reject(self):
        readings = dict(self.good)
        refusals = [i for i in self.items if i.labels["sponsorship"].value == "wont_sponsor"]
        for item in refusals[:15]:  # 15 of 25 missed: a 60% miss rate is cheap, the posting is only shown
            readings[item.gold_id] = fields(item, sponsorship=("unknown", ""))
        result = self.run_gate(readings)
        self.assertEqual(result.verdict, "promote", result.text())
        self.assertIn("misses (both) 15 of 25", result.text())

    def test_a_reader_that_never_hides_is_a_collapse_and_rejects(self):
        readings = dict(self.good)
        for item in self.items:
            if item.labels["sponsorship"].value == "wont_sponsor":
                readings[item.gold_id] = fields(item, sponsorship=("unknown", ""))
        self.assertEqual(self.run_gate(readings).verdict, "reject")

    def test_reader_false_hides_over_five_percent_reject_even_when_the_rules_disagree(self):
        readings = dict(self.good)
        flipped = [i for i in self.items if i.labels["sponsorship"].value == "silent"][:2]
        for item in flipped:  # the quote is real, the value is wrong; the rules say silent, so nothing hides
            readings[item.gold_id] = fields(item, sponsorship=("wont_sponsor", "billing software for small clinics"))
        result = self.run_gate(readings)
        self.assertEqual(result.verdict, "reject", result.text())  # 2 of 27 is 7.4%

    def test_too_few_labels_cannot_be_evaluated(self):
        items = big_set(per_hide=10, fine=40)
        readings = perfect(items)
        result = gate(items, score(items, readings), None, readings=readings, version=NEW, current_version=NEW)
        self.assertEqual(result.verdict, "cannot", result.text())

    def test_enough_per_class_but_too_few_in_all_cannot_be_evaluated(self):
        items = big_set(per_hide=20, fine=100)
        readings = perfect(items)
        result = gate(items, score(items, readings), None, readings=readings, version=NEW, current_version=NEW)
        self.assertEqual(result.verdict, "cannot", result.text())
        self.assertIn("at least 300 confirmed items", result.text())

    def test_the_reader_rate_check_reads_the_reader_row_and_the_agreed_check_the_both_row(self):
        readings = dict(self.good)
        flipped = [i for i in self.items if i.labels["sponsorship"].value == "silent"][:2]
        for item in flipped:
            readings[item.gold_id] = fields(item, sponsorship=("wont_sponsor", "billing software for small clinics"))
        text = self.run_gate(readings).text()
        self.assertIn("false hides (reader) 2 of 27", text)
        self.assertIn("false hides (both) 0 of 25", text)

    def test_an_unread_item_or_a_stale_version_cannot_be_evaluated(self):
        readings = dict(self.good)
        readings.pop(self.items[0].gold_id)
        self.assertEqual(self.run_gate(readings).verdict, "cannot")
        self.assertEqual(self.run_gate(self.good, current=OLD).verdict, "cannot")

    def test_a_regression_against_the_promoted_version_rejects(self):
        readings = dict(self.good)
        refusals = [i for i in self.items if i.labels["sponsorship"].value == "wont_sponsor"]
        readings[refusals[0].gold_id] = fields(refusals[0], sponsorship=("unknown", ""))
        result = self.run_gate(readings, old=self.good_scores)
        self.assertEqual(result.verdict, "reject", result.text())
        self.assertIn("recall", result.text())


class PromoteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = big_set()
        cls.good = perfect(cls.items)
        cls.good_scores = score(cls.items, cls.good)

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.config = Path(folder.name) / "config.toml"
        self.config.write_bytes(CONFIG_PATH.read_bytes())

    def test_promote_refuses_on_regression_and_leaves_config_untouched(self):
        before = self.config.read_bytes()
        readings = dict(self.good)
        refusal = next(i for i in self.items if i.labels["sponsorship"].value == "wont_sponsor")
        readings[refusal.gold_id] = fields(refusal, sponsorship=("unknown", ""))
        with self.assertRaises(ValueError) as caught:
            promote(NEW, self.items, score(self.items, readings), self.good_scores, self.config, readings=readings,
                    current_version=NEW)
        self.assertIn("sponsorship = wont_sponsor", str(caught.exception))
        self.assertEqual(self.config.read_bytes(), before)

    def test_promote_rewrites_only_the_version_line(self):
        before = self.config.read_text(encoding="utf-8").splitlines()
        promote(NEW, self.items, self.good_scores, None, self.config, readings=self.good, current_version=NEW)
        after = self.config.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(after), len(before))
        changed = [(a, b) for a, b in zip(before, after) if a != b]
        self.assertEqual(changed, [('promoted_version = ""', f'promoted_version = "{NEW}"')])
        self.assertEqual(load_reader_config(self.config).promoted_version, NEW)
        self.assertEqual(sorted(p.name for p in self.config.parent.iterdir()), ["config.toml"])

    def test_promote_refuses_a_version_that_is_not_a_reader_version(self):
        with self.assertRaises(ValueError):
            promote('rv-1"\nmodel = "x', self.items, self.good_scores, None, self.config, readings=self.good,
                    current_version='rv-1"\nmodel = "x')


class RunReaderTests(unittest.TestCase):
    def test_eval_run_uses_the_injected_runner_and_writes_readings(self):
        items = [make("g1", REFUSAL, sponsorship=("wont_sponsor", REFUSAL)), make("g2"),
                 make("g3", status="prelabeled")]
        calls = []

        def runner(system_prompt, user_message, model):
            payload = json.loads(user_message)
            calls.append((system_prompt, payload, model))
            out = []
            for p in payload["postings"]:
                item = next(i for i in items if i.body == p["body"])
                out.append({"posting_id": p["posting_id"], "body_sha256": p["body_sha256"], "fields": fields(item)})
            text = json.dumps({"readings": out})
            return RunResult(0, json.dumps({"type": "result", "is_error": False, "result": text}), "")

        cfg = dataclasses.replace(load_reader_config(), batch_size=2)
        with tempfile.TemporaryDirectory() as folder:
            got = run_reader_on_gold(items, runner, cfg, NEW, folder=Path(folder))
            self.assertEqual(sorted(got), ["g1", "g2", "g3"], "confirmed and pending items are both read")
            self.assertEqual(len(calls), 2, "two batches of at most batch_size")
            self.assertEqual(calls[0][0], cfg.prompt_path.read_text(encoding="utf-8"))
            self.assertEqual(calls[0][2], cfg.model)
            self.assertEqual(calls[0][1]["reader_version"], NEW)
            self.assertEqual(load_readings(NEW, Path(folder)), got)
            self.assertTrue((Path(folder) / f"{NEW}.jsonl").is_file())
            again = run_reader_on_gold(items, runner, cfg, NEW, folder=Path(folder))
            self.assertEqual(len(calls), 2, "items already read at this version are not read again")
            self.assertEqual(again, got)

    def test_a_failed_call_keeps_what_was_read_and_raises(self):
        items = [make("g1"), make("g2", "Second team.")]
        answers = iter([None, RunResult(1, "", "boom")])

        def runner(system_prompt, user_message, model):
            answer = next(answers)
            if answer is not None:
                return answer
            p = json.loads(user_message)["postings"][0]
            text = json.dumps({"readings": [{"posting_id": p["posting_id"], "body_sha256": p["body_sha256"],
                                             "fields": fields(items[0])}]})
            return RunResult(0, json.dumps({"type": "result", "is_error": False, "result": text}), "")

        cfg = dataclasses.replace(load_reader_config(), batch_size=1)
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(RuntimeError):
                run_reader_on_gold(items, runner, cfg, NEW, folder=Path(folder))
            self.assertEqual(sorted(load_readings(NEW, Path(folder))), ["g1"])

    def test_load_readings_refuses_a_missing_file_and_a_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(FileNotFoundError):
                load_readings(NEW, Path(folder))
            (Path(folder) / f"{NEW}.jsonl").write_text(
                '{"gold_id": "g1", "fields": {}}\n{"gold_id": "g1", "fields": {}}\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_readings(NEW, Path(folder))


class EvalCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = big_set()

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name)
        self.gold = self.dir / "items.jsonl"
        self.gold.write_text("".join(json.dumps(i.to_json()) + "\n" for i in self.items), encoding="utf-8")
        self.readings = self.dir / "readings"
        self.readings.mkdir()
        self.config = self.dir / "config.toml"
        self.config.write_bytes(CONFIG_PATH.read_bytes())

    def write_readings(self, version, readings):
        (self.readings / f"{version}.jsonl").write_text(
            "".join(json.dumps({"gold_id": k, "fields": v}) + "\n" for k, v in readings.items()), encoding="utf-8")

    def run_cli(self, *argv, current=NEW):
        out = io.StringIO()
        with mock.patch.object(cli, "reader_version", return_value=current), \
                mock.patch.object(cli, "CONFIG_PATH", self.config), \
                mock.patch.object(cli, "load_reader_config", side_effect=lambda path=self.config:
                                  load_reader_config(path)):
            code = cli.main(list(argv), stdout=out)
        return code, out.getvalue()

    def worse(self):
        readings = perfect(self.items)
        refusal = next(i for i in self.items if i.labels["sponsorship"].value == "wont_sponsor")
        readings[refusal.gold_id] = fields(refusal, sponsorship=("unknown", ""))
        return readings

    def test_eval_against_exits_1_on_regression(self):
        self.write_readings(OLD, perfect(self.items))
        self.write_readings(NEW, self.worse())
        common = ("--file", str(self.gold), "--readings", str(self.readings))
        code, out = self.run_cli("eval", "--version", NEW, "--against", OLD, *common)
        self.assertEqual(code, 1, out)
        self.assertIn("sponsorship = wont_sponsor", out)
        code, out = self.run_cli("eval", "--version", OLD, "--against", OLD, *common)
        self.assertEqual(code, 0, out)
        for predictor in ("reader", "rules", "both"):
            self.assertIn(predictor, out)
        code, out = self.run_cli("eval", "--version", NEW, "--json", *common)
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["version"], NEW)
        self.assertIn("gate", data)

    def test_against_compares_only_items_both_versions_read(self):
        # Final review: an item the old version never read scored as an old miss, which hid a real regression.
        refusals = [i for i in self.items if i.labels["sponsorship"].value == "wont_sponsor"]
        worse = self.worse()  # misses refusals[0]
        old = perfect(self.items)
        for item in refusals[1:5]:
            old.pop(item.gold_id)  # 21 refusals read by both, still a gated class
        self.write_readings(OLD, old)
        self.write_readings(NEW, worse)
        common = ("--file", str(self.gold), "--readings", str(self.readings))
        code, out = self.run_cli("eval", "--version", NEW, "--against", OLD, *common)
        self.assertEqual(code, 1, out)
        self.assertIn("sponsorship = wont_sponsor (reader): recall 1.000 -> 0.952", out)
        self.assertIn("no class gets worse", out)
        self.assertIn("REJECT", out)
        code, out = self.run_cli("promote", "--version", NEW, "--against", OLD, *common)
        self.assertEqual(code, 1, out)
        self.assertEqual(load_reader_config(self.config).promoted_version, "")

    def test_eval_run_refuses_a_version_the_files_do_not_make(self):
        with mock.patch.object(cli, "claude_runner", side_effect=AssertionError("no runner")), \
                mock.patch.object(cli, "run_reader_on_gold", side_effect=AssertionError("no read")):
            err = io.StringIO()
            with mock.patch("sys.stderr", err):
                code, _ = self.run_cli("eval", "--run", "--version", OLD, "--file", str(self.gold), "--readings",
                                       str(self.readings))
        self.assertEqual(code, 2)
        self.assertIn(NEW, err.getvalue())
        self.assertFalse((self.readings / f"{OLD}.jsonl").exists())

    def test_eval_and_promote_default_to_the_real_set_with_its_readings_beside_it(self):
        self.write_readings(NEW, perfect(self.items))
        with mock.patch.object(cli.gold, "GOLD_PATH", self.gold):
            code, out = self.run_cli("eval", "--version", NEW)
            self.assertEqual(code, 0, out)
            self.assertIn(f"reader {NEW}: {len(self.items)} confirmed items, {len(self.items)} read", out)
            code, out = self.run_cli("promote", "--version", NEW)
            self.assertEqual(code, 0, out)
        self.assertEqual(load_reader_config(self.config).promoted_version, NEW)

    def test_readings_default_beside_the_file_given(self):
        self.write_readings(NEW, perfect(self.items))
        code, out = self.run_cli("eval", "--version", NEW, "--file", str(self.gold))
        self.assertEqual(code, 0, out)
        self.assertIn(f"{len(self.items)} read", out)

    def test_eval_on_a_missing_gold_file_is_a_clear_error(self):
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            code, _ = self.run_cli("eval", "--file", str(self.dir / "nowhere.jsonl"))
        self.assertEqual(code, 2)
        self.assertIn("gold sample", err.getvalue())

    def test_eval_run_calls_the_runner_only_with_run(self):
        self.write_readings(NEW, perfect(self.items))
        with mock.patch.object(cli, "claude_runner", side_effect=AssertionError("no runner without --run")):
            code, _ = self.run_cli("eval", "--version", NEW, "--file", str(self.gold), "--readings",
                                   str(self.readings))
        self.assertEqual(code, 0)
        with mock.patch.object(cli, "claude_runner") as made, mock.patch.object(cli, "run_reader_on_gold",
                                                                                 return_value={}) as ran:
            self.run_cli("eval", "--run", "--version", NEW, "--file", str(self.gold), "--readings",
                         str(self.readings))
        made.assert_called_once()
        self.assertIs(ran.call_args.args[1], made.return_value)

    def test_promote_on_the_command_line_writes_the_line_only_when_the_gate_passes(self):
        self.write_readings(OLD, perfect(self.items))
        self.write_readings(NEW, self.worse())
        common = ("--file", str(self.gold), "--readings", str(self.readings))
        before = self.config.read_bytes()
        code, out = self.run_cli("promote", "--version", NEW, "--against", OLD, *common)
        self.assertEqual(code, 1, out)
        self.assertEqual(self.config.read_bytes(), before)
        code, out = self.run_cli("promote", "--version", OLD, *common, current=OLD)
        self.assertEqual(code, 0, out)
        self.assertEqual(load_reader_config(self.config).promoted_version, OLD)
        self.assertIn("apply", out)

    def test_promote_refuses_outside_the_live_checkout_without_a_scratch_store(self):
        self.write_readings(NEW, perfect(self.items))
        before = self.config.read_bytes()
        env = {k: v for k, v in os.environ.items() if k != "OUTREACH_DB"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(cli.live_checkout, "is_live", return_value=False):
            with self.assertRaises(SystemExit) as caught:
                self.run_cli("promote", "--version", NEW, "--file", str(self.gold), "--readings", str(self.readings))
        self.assertIn("live checkout", str(caught.exception.code))
        self.assertEqual(self.config.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
