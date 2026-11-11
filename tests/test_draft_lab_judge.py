"""The draft lab judge library (#211). Made-up people only."""

import json
import tempfile
import unittest
from pathlib import Path

from outreach.draft_lab import judge
from outreach.draft_lab.judge import Draft


def d(i, subject="Your work on parsers", body="Hi Sam,\n\nI read your post on parsers and built a small one myself. Would you pass my note on?\n\nDev", who="Sam Test"):
    return Draft(id=str(i), recipient=who, subject=subject, body=body)


def make_sets(n=12, b_body="Hi Sam,\n\nA different note about your compiler talk that I really liked a lot.\n\nDev"):
    a = [d(i) for i in range(n)]
    b = [d(i, subject="Compiler talk", body=b_body) for i in range(n)]
    return a, b


class LoadAndPair(unittest.TestCase):
    def test_load_set_and_pair(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "a.json"
            p.write_text(json.dumps([{"id": 1, "recipient": "R", "subject": "S", "body": "B"}]))
            a = judge.load_set(p)
        self.assertEqual(a, [Draft("1", "R", "S", "B")])
        pairs, only = judge.pair_sets(a, [d(1)])
        self.assertEqual(len(pairs), 1)
        self.assertEqual(only, [])

    def test_load_set_refuses_a_duplicate_id(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "a.json"
            p.write_text(json.dumps([{"id": 1, "recipient": "R", "subject": "S", "body": "B"},
                                     {"id": "1", "recipient": "R2", "subject": "S", "body": "B"}]))
            with self.assertRaisesRegex(ValueError, "duplicate id"):
                judge.load_set(p)

    def test_sets_with_different_ids(self):
        pairs, only = judge.pair_sets([d(1), d(2)], [d(2), d(3)])
        self.assertEqual([p[0].id for p in pairs], ["2"])
        self.assertEqual(sorted(only), ["1", "3"])


class BodyMode(unittest.TestCase):
    def test_seed_repeats_the_shuffle(self):
        a, b = make_sets()
        pairs, _ = judge.pair_sets(a, b)
        t1, k1 = judge.body_prompt(pairs, seed=5)
        t2, k2 = judge.body_prompt(pairs, seed=5)
        self.assertEqual((t1, k1), (t2, k2))
        keys = {json.dumps(judge.body_prompt(pairs, seed=s)[1], sort_keys=True) for s in range(10)}
        self.assertGreater(len(keys), 1)
        self.assertTrue(t1.rstrip().split("ITEMS")[0].startswith("You are judging"))
        payload = json.loads(t1.split("ITEMS", 1)[1])
        self.assertEqual(set(payload[0]["versions"]), {"A", "B"})
        self.assertNotIn("a", payload[0]["versions"])

    def _reply(self, key, ids, b_best=True, human_a=5, human_b=7):
        items = []
        for i in ids:
            b_label = "A" if key[i]["A"] == "b" else "B"
            a_label = "B" if b_label == "A" else "A"
            sc = lambda a, b: {a_label: a, b_label: b}
            items.append({"id": i, "human": sc(human_a, human_b), "personal": sc(5, 6), "warm": sc(5, 6),
                          "reply": sc(4, 6), "best": b_label if b_best else a_label, "why": "w" + i,
                          "too_much": sc("", "")})
        return {"items": items, "repeats": ["opens with I read"]}

    def test_body_scoring_counts_wins_and_averages(self):
        a, b = make_sets()
        pairs, _ = judge.pair_sets(a, b)
        _, key = judge.body_prompt(pairs, seed=1)
        ids = [str(i) for i in range(12)]
        reply = self._reply(key, ids[:10])
        reply["items"][0]["best"] = "A" if key["0"]["A"] == "a" else "B"
        res = judge.score_body(reply, key, pairs)
        self.assertEqual((res.wins_a, res.wins_b, res.ties), (1, 9, 0))
        self.assertEqual(res.missing, ["10", "11"])
        self.assertAlmostEqual(res.avg_a["human"], 5)
        self.assertAlmostEqual(res.avg_b["human"], 7)
        self.assertEqual(res.repeats, ["opens with I read"])
        self.assertEqual(res.why["1"], "w1")

    def test_identical_versions_are_ties(self):
        a = [d(0), d(1)]
        b = [d(0), d(1, subject="Other")]
        pairs, _ = judge.pair_sets(a, b)
        _, key = judge.body_prompt(pairs, seed=1)
        reply = self._reply(key, ["0", "1"])
        res = judge.score_body(reply, key, pairs)
        self.assertEqual(res.ties, 1)
        self.assertEqual(res.tie_ids, ["0"])
        self.assertEqual(res.wins_a + res.wins_b, 1)
        self.assertIn("tie", judge.report(res, seed=1, mode="body").lower())

    def test_reply_with_prose_and_missing_items(self):
        r = judge.parse_reply('Sure! Here {not json} you go: {"items": [{"id": "1"}], "repeats": []} thanks {"x": 1}')
        self.assertEqual(r["items"][0]["id"], "1")
        with self.assertRaises(ValueError):
            judge.parse_reply("no json at all")

    def test_verdict_pass_and_fail(self):
        a, b = make_sets()
        pairs, _ = judge.pair_sets(a, b)
        _, key = judge.body_prompt(pairs, seed=2)
        ids = [str(i) for i in range(12)]

        def run(b_wins, human_b=7):
            reply = self._reply(key, ids, human_b=human_b)
            for it in reply["items"][b_wins:]:
                it["best"] = "A" if key[it["id"]]["A"] == "a" else "B"
            return judge.verdict(judge.score_body(reply, key, pairs))

        self.assertEqual(run(8), "PASS")
        self.assertEqual(run(7), "FAIL")
        self.assertEqual(run(12, human_b=4), "FAIL")
        self.assertEqual(run(12, human_b=5), "PASS")

    def test_report_names_the_seed_and_the_verdict(self):
        a, b = make_sets()
        pairs, _ = judge.pair_sets(a, b)
        _, key = judge.body_prompt(pairs, seed=1234)
        res = judge.score_body(self._reply(key, [str(i) for i in range(12)]), key, pairs)
        text = judge.report(res, seed=1234, mode="body")
        self.assertIn("1234", text)
        self.assertIn("PASS", text)
        self.assertNotIn("\u2014", text)


class InboxMode(unittest.TestCase):
    def setUp(self):
        self.a = [d(0), d(1)]
        self.b = [d(0, subject="Compiler talk", body="Hi Sam,\n\n" + "x" * 200), d(1)]
        self.pairs, _ = judge.pair_sets(self.a, self.b)
        self.decoys = judge.load_decoys()

    def test_inbox_rows_hide_the_source(self):
        text, key = judge.inbox_prompt(self.pairs, seed=3, decoys=self.decoys)
        items = json.loads(text.split("ITEMS", 1)[1])
        self.assertEqual(items[0]["you_are"], "Sam Test")
        rows = items[0]["inbox"]
        self.assertEqual(len(rows), 6)
        self.assertEqual(sorted(key["0"].values()), ["a", "b", "decoy", "decoy", "decoy", "decoy"])
        self.assertTrue(all(set(r) == {"row", "from", "subject", "preview"} for r in rows))
        self.assertNotIn("Hi Sam", text.split("ITEMS", 1)[1])
        row_b = next(r for r in rows if key["0"][r["row"]] == "b")
        self.assertEqual(len(row_b["preview"]), 110)
        # item 1 has identical a and b: one shared row
        self.assertEqual(len(items[1]["inbox"]), 5)
        self.assertIn("ab", key["1"].values())
        self.assertEqual(judge.inbox_prompt(self.pairs, seed=3, decoys=self.decoys), (text, key))

    def test_inbox_scoring(self):
        _, key = judge.inbox_prompt(self.pairs, seed=3, decoys=self.decoys)
        row = lambda i, who: next(r for r, w in key[i].items() if w == who)
        reply = {"items": [{"id": "0", "open": [int(row("0", "b")), int(row("0", "a"))],
                            "never": [row("0", "decoy")], "why": "because"}]}
        res = judge.score_inbox(reply, key)
        self.assertEqual(res.first_a, 0)
        self.assertEqual(res.first_b, 1)
        self.assertEqual((res.opened_a, res.opened_b), (1, 1))
        self.assertEqual(res.archived_decoy, 1)
        self.assertEqual(res.missing, ["1"])
        self.assertEqual(res.why["0"], "because")


class Robustness(unittest.TestCase):
    def setUp(self):
        a, b = make_sets()
        self.pairs, _ = judge.pair_sets(a, b)
        _, self.key = judge.body_prompt(self.pairs, seed=1)
        self.ids = [str(i) for i in range(12)]
        self.helper = BodyMode()

    def full(self):
        return self.helper._reply(self.key, self.ids)

    def test_malformed_replies_do_not_crash(self):
        for bad in ({"items": None}, {"items": "x"}, {"items": 5}, {"repeats": "abc"}, [], "x", None,
                    {"items": [3, None, "s"]}):
            res = judge.score_body(bad, self.key, self.pairs)
            self.assertEqual(judge.verdict(res), "INCOMPLETE")
            judge.report(res, seed=1, mode="body")
        res = judge.score_body({"repeats": "abc"}, self.key, self.pairs)
        self.assertEqual(res.repeats, [])
        inbox_pairs, _ = judge.pair_sets([d(0)], [d(0, subject="Z")])
        _, ikey = judge.inbox_prompt(inbox_pairs, seed=1, decoys=judge.load_decoys())
        for bad in ({"items": [{"id": "0", "open": None}]}, {"items": [{"id": "0", "open": 3}]}, [], {"items": [5]},
                    {"items": [{"id": "0", "open": [1], "never": 4}]}):
            res = judge.score_inbox(bad, ikey)
            self.assertEqual(res.items, 0)
            judge.report(res, seed=1, mode="inbox")

    def test_scores_must_be_finite_real_numbers_from_1_to_10(self):
        for bad in (float("inf"), float("nan"), True, 11, 0, "7", None):
            reply = self.full()
            lab = "B" if self.key["0"]["B"] == "b" else "A"
            reply["items"][0]["human"][lab] = bad
            res = judge.score_body(reply, self.key, self.pairs)
            self.assertEqual(res.malformed, ["0"], bad)
            self.assertEqual(judge.verdict(res), "INCOMPLETE")

    def test_pass_needs_at_least_8_compared_items(self):
        a, b = make_sets()
        ties = [d(i) for i in range(1, 12)]
        a = [d(0)] + ties
        b = [d(0, body="Hi Sam,\n\nA different note about your compiler talk that I really liked a lot.\n\nDev")] + ties
        pairs, _ = judge.pair_sets(a, b)
        _, key = judge.body_prompt(pairs, seed=2)
        reply = BodyMode()._reply(key, ["0"])
        res = judge.score_body(reply, key, pairs)
        self.assertEqual((res.compared, res.wins_b, res.ties), (1, 1, 11))
        self.assertEqual(judge.verdict(res), "INCOMPLETE")

    def test_missing_items_make_the_verdict_incomplete(self):
        reply = self.full()
        reply["items"] = reply["items"][:6]
        res = judge.score_body(reply, self.key, self.pairs)
        self.assertEqual(judge.verdict(res), "INCOMPLETE")
        self.assertIn("6 missing", judge.report(res, seed=1, mode="body"))

    def test_partial_scores_are_malformed_not_averaged(self):
        reply = self.full()
        lab = "B" if self.key["0"]["B"] == "b" else "A"
        reply["items"][0]["human"] = {lab: 7}
        res = judge.score_body(reply, self.key, self.pairs)
        self.assertEqual(res.malformed, ["0"])
        self.assertEqual(res.compared, 11)
        self.assertEqual(judge.verdict(res), "INCOMPLETE")
        reply = self.full()
        reply["items"][1]["best"] = "C"
        self.assertEqual(judge.score_body(reply, self.key, self.pairs).malformed, ["1"])
        reply = self.full()
        reply["items"][2]["reply"] = {"A": "high", "B": 3}
        self.assertEqual(judge.score_body(reply, self.key, self.pairs).malformed, ["2"])

    def test_duplicate_open_rows_and_why_whitespace(self):
        inbox_pairs, _ = judge.pair_sets([d(0)], [d(0, subject="Z")])
        _, ikey = judge.inbox_prompt(inbox_pairs, seed=1, decoys=judge.load_decoys())
        a_row = next(int(r) for r, w in ikey["0"].items() if w == "a")
        res = judge.score_inbox({"items": [{"id": "0", "open": [a_row, a_row, a_row], "never": [],
                                            "why": "x\n- y   z"}]}, ikey)
        self.assertEqual(res.opened_a, 1)
        self.assertEqual(res.why["0"], "x - y z")


class KeyMatchesWhatIsShown(unittest.TestCase):
    def test_body_key_maps_labels_to_the_right_set(self):
        a = [d(i, subject="alpha %d" % i, body="Hi Sam,\n\nalpha body %d" % i) for i in range(12)]
        b = [d(i, subject="beta %d" % i, body="Hi Sam,\n\nbeta body %d" % i) for i in range(12)]
        pairs, _ = judge.pair_sets(a, b)
        text, key = judge.body_prompt(pairs, seed=9)
        payload = json.loads(text.split("ITEMS", 1)[1])
        sets = {"a": {x.id: x for x in a}, "b": {x.id: x for x in b}}
        for it in payload:
            for label in ("A", "B"):
                src = sets[key[it["id"]][label]][it["id"]]
                self.assertEqual(it["versions"][label]["body"], src.body)
                self.assertEqual(it["versions"][label]["subject"], src.subject)

    def test_inbox_key_maps_rows_to_the_right_set(self):
        a = [d(i, subject="alpha %d" % i) for i in range(6)]
        b = [d(i, subject="beta %d" % i) for i in range(6)]
        pairs, _ = judge.pair_sets(a, b)
        decoys = judge.load_decoys()
        text, key = judge.inbox_prompt(pairs, seed=9, decoys=decoys)
        payload = json.loads(text.split("ITEMS", 1)[1])
        decoy_subjects = {x["subject"] for x in decoys}
        for it in payload:
            for row in it["inbox"]:
                src = key[it["id"]][row["row"]]
                if src == "decoy":
                    self.assertIn(row["subject"], decoy_subjects)
                else:
                    self.assertEqual(row["subject"], ("alpha " if src == "a" else "beta ") + it["id"])


if __name__ == "__main__":
    unittest.main()
