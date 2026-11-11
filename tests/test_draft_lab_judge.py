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
        self.assertNotIn("—", text)


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


if __name__ == "__main__":
    unittest.main()
