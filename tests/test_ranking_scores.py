"""Stored scores (issue #6, Task 9): one row per company and ranker version, one shared scored_at per run, the
three newest versions kept, and the read the list surfaces use (hidden rows and unlaned rows filtered in SQL)."""
import dataclasses
import json
import sqlite3
import unittest

from outreach.ranking.config import default_ranker_config
from outreach.ranking.hide import Hide
from outreach.ranking.score import Scored, score_company, to_columns
from outreach.ranking.scores import (StoredScore, hidden_list, latest_version, load_scores, previous_version,
                                     prune_versions, ranked_list, write_scores)
from outreach.ranking.signals import build_signals
from tests.ranking_helpers import neutral_facts
from tests.storetest import fresh_store, seed_company

CFG = default_ranker_config()
SIGNALS = build_signals(CFG)
T1, T2, T3, T4 = ("2026-10-%02dT08:00:00Z" % d for d in (19, 20, 21, 22))


def scored(company_id: int, points: float, *, lanes=("level_role",), hide: Hide | None = None) -> Scored:
    got = score_company(neutral_facts(company_id=company_id, lanes=lanes, name=f"Co {company_id}"), hide, SIGNALS, CFG)
    return dataclasses.replace(got, score=points)


class Store(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.ids = [seed_company(self.conn, f"Co {i}") for i in range(1, 6)]

    def rows(self, version: str | None = None) -> list[tuple]:
        sql = "SELECT company_id, ranker_version, score, signals, lanes, scored_at FROM company_score"
        args: tuple = ()
        if version:
            sql, args = sql + " WHERE ranker_version = ?", (version,)
        return [tuple(r) for r in self.conn.execute(sql + " ORDER BY ranker_version, company_id", args)]

    def put(self, version: str, at: str, *pairs) -> int:
        return write_scores(self.conn, [scored(c, p) for c, p in pairs], version, at)


class WriteTests(Store):
    def test_write_is_idempotent_and_updates_in_place(self):
        batch = [scored(self.ids[0], 50.0), scored(self.ids[1], 40.0)]
        self.assertEqual(write_scores(self.conn, batch, "rk-a", T1), 2)
        first = self.rows()
        self.assertEqual(write_scores(self.conn, batch, "rk-a", T1), 2)
        self.assertEqual(self.rows(), first)
        self.assertEqual(len(first), 2)
        self.assertEqual({r[5] for r in first}, {T1})
        # a changed score under the same version and a later scored_at updates the row, it adds none
        write_scores(self.conn, [scored(self.ids[0], 77.5)], "rk-a", T2)
        got = {r[0]: r for r in self.rows()}
        self.assertEqual(len(got), 2)
        self.assertEqual((got[self.ids[0]][2], got[self.ids[0]][5]), (77.5, T2))
        self.assertEqual((got[self.ids[1]][2], got[self.ids[1]][5]), (40.0, T1))

    def test_every_row_of_a_run_shares_one_scored_at(self):
        write_scores(self.conn, (scored(c, float(c)) for c in self.ids), "rk-a", T3)
        self.assertEqual({r[5] for r in self.rows()}, {T3})

    def test_a_failing_row_writes_nothing(self):
        batch = [scored(self.ids[0], 5.0), scored(9999, 6.0)]  # no such company: a foreign key failure
        with self.assertRaises(sqlite3.IntegrityError):
            write_scores(self.conn, batch, "rk-a", T1)
        self.assertEqual(self.rows(), [])
        self.assertFalse(self.conn.in_transaction)

    def test_a_write_inside_the_callers_transaction_waits_for_its_commit(self):
        self.conn.execute("BEGIN IMMEDIATE")
        write_scores(self.conn, [scored(self.ids[0], 5.0)], "rk-a", T1)
        self.assertTrue(self.conn.in_transaction)
        self.conn.execute("ROLLBACK")
        self.assertEqual(self.rows(), [])

    def test_table_checks_accept_the_stored_json(self):
        s = scored(self.ids[0], 12.0, hide=Hide("no_go", "On the no-go list: no sponsorship", False))
        signals, lanes = to_columns(s)
        write_scores(self.conn, [s], "rk-a", T1)
        row = self.conn.execute("SELECT signals, lanes, json_valid(signals) AS a, json_valid(lanes) AS b "
                                "FROM company_score").fetchone()
        self.assertEqual((row["signals"], row["lanes"], row["a"], row["b"]), (signals, lanes, 1, 1))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO company_score(company_id, ranker_version, score, signals, lanes, "
                              "scored_at) VALUES (?, 'rk-b', 1, '{broken', '[]', ?)", (self.ids[0], T1))


class VersionTests(Store):
    def test_latest_and_previous_version_follow_scored_at(self):
        self.assertIsNone(latest_version(self.conn))
        self.put("rk-new-name-sorts-first", T2, (self.ids[0], 1.0))
        self.put("rk-z", T1, (self.ids[0], 1.0))
        self.put("rk-m", T3, (self.ids[0], 1.0))
        self.assertEqual(latest_version(self.conn), "rk-m")
        self.assertEqual(previous_version(self.conn, "rk-m"), "rk-new-name-sorts-first")
        self.assertEqual(previous_version(self.conn, "rk-new-name-sorts-first"), "rk-z")
        self.assertIsNone(previous_version(self.conn, "rk-z"))
        self.assertIsNone(previous_version(self.conn, "rk-unknown"))

    def test_a_version_is_as_new_as_its_newest_row(self):
        self.put("rk-a", T1, (self.ids[0], 1.0), (self.ids[1], 1.0))
        self.put("rk-b", T2, (self.ids[0], 1.0))
        self.put("rk-a", T3, (self.ids[0], 2.0))  # one row of rk-a is rewritten later, the other keeps T1
        self.assertEqual(latest_version(self.conn), "rk-a")
        self.assertEqual(previous_version(self.conn, "rk-a"), "rk-b")

    def test_prune_keeps_the_newest_three_versions(self):
        for version, at in (("rk-1", T1), ("rk-2", T2), ("rk-3", T3), ("rk-4", T4)):
            self.put(version, at, (self.ids[0], 1.0), (self.ids[1], 2.0))
        self.assertEqual(prune_versions(self.conn, 3), 2)
        self.assertEqual({r[1] for r in self.rows()}, {"rk-2", "rk-3", "rk-4"})
        self.assertEqual(prune_versions(self.conn, 3), 0)
        self.assertEqual(prune_versions(self.conn, 1), 4)
        self.assertEqual({r[1] for r in self.rows()}, {"rk-4"})

    def test_prune_refuses_to_keep_nothing(self):
        self.put("rk-1", T1, (self.ids[0], 1.0))
        for bad in (0, -1):
            with self.assertRaises(ValueError):
                prune_versions(self.conn, bad)
        self.assertEqual(len(self.rows()), 1)


class ReadTests(Store):
    def setUp(self):
        super().setUp()
        a, b, c, d, e = self.ids
        gone = Hide("no_go", "On the no-go list", False)
        pending = Hide("no_sponsorship", "hidden, pending reader: a refusal", True)
        write_scores(self.conn, [
            scored(a, 10.0), scored(b, 30.0), scored(c, 30.0), scored(d, 99.0, hide=gone),
            scored(e, 20.0, lanes=(), hide=None)], "rk-a", T1)
        write_scores(self.conn, [scored(a, 1.0), scored(d, 2.0, hide=pending)], "rk-b", T2)

    def test_ranked_list_excludes_hidden_and_orders_by_score(self):
        a, b, c, d, e = self.ids
        got = ranked_list(self.conn, "rk-a")
        self.assertEqual([s.company_id for s in got], [b, c, e, a])  # a tie breaks to the lower company id
        self.assertTrue(all(isinstance(s, StoredScore) for s in got))
        self.assertEqual([s.score for s in got], [30.0, 30.0, 20.0, 10.0])
        self.assertEqual([s.company_id for s in ranked_list(self.conn, "rk-a", limit=2)], [b, c])

    def test_default_version_is_the_latest(self):
        a, *_ = self.ids
        self.assertEqual([s.company_id for s in ranked_list(self.conn)], [a])
        self.assertEqual(ranked_list(self.conn)[0].score, 1.0)

    def test_an_empty_store_has_an_empty_list(self):
        fresh = fresh_store()
        self.addCleanup(fresh.close)
        self.assertEqual(ranked_list(fresh), [])
        self.assertEqual(hidden_list(fresh), [])
        self.assertEqual(load_scores(fresh, "rk-a"), {})

    def test_laned_only_drops_companies_without_a_lane(self):
        a, b, c, d, e = self.ids
        self.assertEqual([s.company_id for s in ranked_list(self.conn, "rk-a", laned_only=True)], [b, c, a])
        self.assertEqual([s.company_id for s in ranked_list(self.conn, "rk-a", laned_only=True, limit=1)], [b])

    def test_hidden_list_has_provisional_and_final_hides(self):
        a, b, c, d, e = self.ids
        self.assertEqual([s.company_id for s in hidden_list(self.conn, "rk-a")], [d])
        self.assertEqual(hidden_list(self.conn, "rk-a")[0].signals["hide"]["kind"], "no_go")
        latest = hidden_list(self.conn)
        self.assertEqual([s.company_id for s in latest], [d])
        self.assertTrue(latest[0].signals["hide"]["provisional"])
        self.assertEqual(latest[0].signals["hide"]["reason"], "hidden, pending reader: a refusal")

    def test_a_hidden_company_keeps_its_score(self):
        a, b, c, d, e = self.ids
        self.assertEqual(hidden_list(self.conn, "rk-a")[0].score, 99.0)

    def test_load_scores_returns_the_stored_json_and_lanes(self):
        a, b, c, d, e = self.ids
        got = load_scores(self.conn, "rk-a")
        self.assertEqual(set(got), set(self.ids))
        self.assertEqual(got[a].lanes, ("level_role",))
        self.assertEqual(got[e].lanes, ())
        self.assertEqual(got[a].scored_at, T1)
        self.assertEqual(json.dumps(got[a].signals, sort_keys=True), to_columns(scored(a, 10.0))[0])
        self.assertIsNone(got[a].signals["hide"])
        self.assertEqual(load_scores(self.conn, "rk-none"), {})


if __name__ == "__main__":
    unittest.main()
