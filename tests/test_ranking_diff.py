"""What moved between two ranker versions (issue #6, Task 12).

`what_moved` is pure: two stored versions in, a report out. A company moves when its rank changes by `min_move` or
more; one that crosses the `top_n` line is listed as entered or left instead; a new or changed hide is reported
apart from the rank moves. The CLI reads the two newest stored versions, and `rank --diff-with` dry-runs the current
code and config against a stored version and writes nothing."""
import contextlib
import dataclasses
import io
import unittest
from unittest import mock

from outreach.ranking.config import default_ranker_config
from outreach.ranking.diff import Move, MovedReport, rank_positions, scored_to_stored, what_moved
from outreach.ranking.hide import Hide
from outreach.ranking.score import score_company
from outreach.ranking.scores import StoredScore, load_scores, write_scores
from outreach.ranking.signals import build_signals
from outreach.ranking.version import ranker_version
from outreach.rules.version import ruleset_version
from tests import ranking_store as rs
from tests.ranking_helpers import neutral_facts
from tests.storetest import fresh_store, seed_company

CFG = default_ranker_config()
SIGNALS = build_signals(CFG)
T1, T2 = "2026-10-21T08:00:00Z", "2026-10-22T08:00:00Z"
DASHES = (chr(0x2013), chr(0x2014))


def stored(cid: int, score: float, *, points: dict | None = None, hide: dict | None = None) -> StoredScore:
    """A stored row: `points` is the points of each named signal, `hide` the stored hide (kind, provisional)."""
    values = {n: {"points": p} for n, p in (points or {}).items()}
    h = None if hide is None else {"kind": hide["kind"], "reason": "r", "provisional": hide.get("provisional", False)}
    return StoredScore(cid, score, (), {"values": values, "hide": h}, T1)


def version(*rows: StoredScore) -> dict[int, StoredScore]:
    return {r.company_id: r for r in rows}


def names(*ids: int) -> dict[int, str]:
    return {i: f"Company {i}" for i in ids}


def move(old, new, **kw):
    kw.setdefault("min_move", 25)
    kw.setdefault("top_n", 1000)
    return what_moved(old, new, names(*sorted(set(old) | set(new))), **kw)


class RankPositionTests(unittest.TestCase):
    def test_positions_are_one_based_in_list_order_and_skip_hidden_rows(self):
        rows = version(stored(5, 10.0), stored(2, 10.0), stored(9, 99.0), stored(3, 50.0, hide={"kind": "no_go"}))
        self.assertEqual(rank_positions(rows), {9: 1, 2: 2, 5: 3})  # a tie breaks on the lower company id

    def test_a_provisional_hide_is_not_ranked_either(self):
        rows = version(stored(1, 5.0, hide={"kind": "no_sponsorship", "provisional": True}), stored(2, 1.0))
        self.assertEqual(rank_positions(rows), {2: 1})

    def test_no_rows_no_positions(self):
        self.assertEqual(rank_positions({}), {})


class WhatMovedTests(unittest.TestCase):
    def test_identical_versions_move_nothing(self):
        rows = version(*(stored(i, 100.0 - i) for i in range(1, 40)))
        got = move(rows, dict(rows))
        self.assertEqual((got.moved_up, got.moved_down, got.entered_top, got.left_top, got.hide_changes), ([], [], [], [], []))
        self.assertEqual(got.unchanged, 39)

    def test_a_weight_change_moves_the_company_that_leans_on_it(self):
        # Company 30 sits at 30th in the old version; with the E-Verify weight 12 to 40 it climbs to 1st.
        old = version(*(stored(i, 200.0 - i, points={"live_role": 10.0}) for i in range(1, 30)),
                      stored(30, 100.0, points={"everify": 12.0, "live_role": 10.0}))
        new = version(*(stored(i, 200.0 - i, points={"live_role": 10.0}) for i in range(1, 30)),
                      stored(30, 300.0, points={"everify": 40.0, "live_role": 10.0}))
        got = what_moved(old, new, names(*range(1, 31)), min_move=25, top_n=50)
        self.assertEqual([m.company_id for m in got.moved_up], [30])
        m = got.moved_up[0]
        self.assertEqual((m.name, m.old_rank, m.new_rank, m.old_score, m.new_score), ("Company 30", 30, 1, 100.0, 300.0))
        self.assertEqual(m.top_signal_change, "everify +28.0")
        self.assertEqual(len(got.moved_down), 0)  # the 29 it passed moved down by one, under min_move

    def test_the_signal_with_the_largest_change_in_points_wins_and_a_drop_has_a_minus(self):
        old = version(stored(1, 50.0, points={"small": 12.0, "funding": 6.0}), *(stored(i, 40.0 - i) for i in range(2, 50)))
        new = version(stored(1, -20.0, points={"small": 12.0, "funding": 6.0, "large_later_stage": -200.0, "live_role": 5.0}),
                      *(stored(i, 40.0 - i) for i in range(2, 50)))
        got = move(old, new, min_move=25)
        self.assertEqual([m.top_signal_change for m in got.moved_down], ["large_later_stage -200.0"])

    def test_a_tie_between_signals_breaks_on_the_name(self):
        old = version(stored(1, 1.0, points={"small": 0.0, "everify": 0.0}), *(stored(i, 0.5 - i / 100) for i in range(2, 40)))
        new = version(stored(1, -50.0, points={"small": -5.0, "everify": -5.0}), *(stored(i, 0.5 - i / 100) for i in range(2, 40)))
        self.assertEqual(move(old, new).moved_down[0].top_signal_change, "everify -5.0")

    def test_a_move_with_no_signal_change_says_so(self):
        old = version(stored(1, 50.0, points={"small": 5.0}), *(stored(i, 40.0 - i) for i in range(2, 50)))
        new = version(stored(1, 1.0, points={"small": 5.0}), *(stored(i, 40.0 - i) for i in range(2, 50)))
        self.assertEqual(move(old, new).moved_down[0].top_signal_change, "no signal changed")

    def test_small_rank_changes_are_not_listed(self):
        def ranks(n):
            old = version(*(stored(i, 1000.0 - i) for i in range(1, 60)))
            # company 1 falls n places: it gets the score just under the company at position 1 + n
            rows = [stored(1, 1000.0 - (1 + n) - 0.5)] + [stored(i, 1000.0 - i) for i in range(2, 60)]
            return old, version(*rows)

        old, new = ranks(24)
        self.assertEqual(rank_positions(new)[1], 25)
        self.assertEqual(move(old, new, min_move=25).moved_down, [])
        old, new = ranks(25)
        self.assertEqual(rank_positions(new)[1], 26)
        got = move(old, new, min_move=25)
        self.assertEqual([(m.company_id, m.old_rank, m.new_rank) for m in got.moved_down], [(1, 1, 26)])

    def test_moves_outside_the_top_n_in_both_versions_are_not_listed(self):
        old = version(*(stored(i, 1000.0 - i) for i in range(1, 200)))
        rows = [stored(i, 1000.0 - i) for i in range(1, 200) if i != 120] + [stored(120, 1000.0 - 1.5)]
        new = version(*rows)  # 120 climbs to 2nd: it entered the top 50
        got = what_moved(old, new, names(*range(1, 200)), min_move=25, top_n=50)
        self.assertEqual([m.company_id for m in got.entered_top], [120])
        rows = [stored(i, 1000.0 - i) for i in range(1, 200) if i != 180] + [stored(180, 1000.0 - 100.5)]
        got = what_moved(old, version(*rows), names(*range(1, 200)), min_move=25, top_n=50)
        self.assertEqual((got.moved_up, got.entered_top), ([], []))  # 180 to 101st: both far outside the top 50

    def test_entering_and_leaving_the_top_n(self):
        old = version(*(stored(i, 100.0 - i) for i in range(1, 8)))
        # 4th falls to 6th (leaves the top 5 by a short step), 6th rises to 5th (enters it by one place)
        new = version(stored(1, 99.0), stored(2, 98.0), stored(3, 97.0), stored(5, 95.5), stored(6, 95.2),
                      stored(4, 94.0), stored(7, 93.0))
        got = what_moved(old, new, names(*range(1, 8)), min_move=25, top_n=5)
        self.assertEqual([(m.company_id, m.old_rank, m.new_rank) for m in got.entered_top], [(6, 6, 5)])
        self.assertEqual([(m.company_id, m.old_rank, m.new_rank) for m in got.left_top], [(4, 4, 6)])
        self.assertEqual(got.moved_up, [])
        self.assertEqual(got.moved_down, [])

    def test_a_company_only_in_one_version_is_listed_as_entered_or_left(self):
        old = version(stored(1, 10.0), stored(2, 5.0))
        new = version(stored(1, 10.0), stored(3, 7.0))
        got = move(old, new, top_n=10)
        self.assertEqual([(m.company_id, m.old_rank, m.new_rank, m.old_score, m.new_score) for m in got.entered_top],
                         [(3, None, 2, None, 7.0)])
        self.assertEqual([(m.company_id, m.old_rank, m.new_rank, m.old_score, m.new_score) for m in got.left_top],
                         [(2, 2, None, 5.0, None)])
        self.assertEqual(got.unchanged, 1)

    def test_a_new_hide_is_reported_apart_from_rank_moves(self):
        old = version(stored(1, 90.0), stored(2, 80.0), stored(3, 70.0))
        new = version(stored(1, 90.0, hide={"kind": "no_go"}), stored(2, 80.0), stored(3, 70.0))
        got = move(old, new, top_n=10)
        self.assertEqual([(m.company_id, m.old_rank, m.new_rank) for m in got.hide_changes], [(1, 1, None)])
        self.assertEqual((got.moved_up, got.moved_down, got.entered_top, got.left_top), ([], [], [], []))
        # company 2 and 3 each gained a place, which is under min_move: nothing else is reported
        self.assertEqual(got.unchanged, 2)

    def test_a_lifted_or_provisional_hide_change_is_a_hide_change_too(self):
        old = version(stored(1, 90.0, hide={"kind": "no_sponsorship", "provisional": True}), stored(2, 80.0))
        new = version(stored(1, 90.0, hide={"kind": "no_sponsorship", "provisional": False}), stored(2, 80.0))
        self.assertEqual([m.company_id for m in move(old, new).hide_changes], [1])
        lifted = version(stored(1, 90.0), stored(2, 80.0))
        got = move(old, lifted)
        self.assertEqual([(m.company_id, m.old_rank, m.new_rank) for m in got.hide_changes], [(1, None, 1)])
        self.assertEqual(got.entered_top, [])

    def test_a_company_hidden_in_both_versions_with_the_same_hide_is_unchanged(self):
        rows = version(stored(1, 90.0, hide={"kind": "over_size"}), stored(2, 80.0))
        got = move(rows, dict(rows))
        self.assertEqual((got.hide_changes, got.unchanged), ([], 2))

    def test_order_is_the_same_on_every_run(self):
        old = version(*(stored(i, 100.0 - i) for i in range(1, 120)))
        flipped = [stored(i, 100.0 - i + (60 if i > 100 else 0)) for i in range(1, 120)]
        new = version(*flipped)
        a = move(old, new, top_n=200)
        b = move(dict(reversed(list(old.items()))), dict(reversed(list(new.items()))), top_n=200)
        self.assertEqual(a, b)
        gains = [m.old_rank - m.new_rank for m in a.moved_up]
        self.assertEqual(gains, sorted(gains, reverse=True))
        pairs = [(m.old_rank - m.new_rank, m.new_rank, m.company_id) for m in a.moved_up]
        self.assertEqual(pairs, sorted(pairs, key=lambda t: (-t[0], t[1], t[2])))

    def test_a_missing_name_falls_back_to_the_company_id(self):
        old = version(stored(1, 1.0))
        got = what_moved(old, version(stored(1, 1.0), stored(2, 9.0)), {}, min_move=25, top_n=10)
        self.assertEqual(got.entered_top[0].name, "company 2")


class LinesTests(unittest.TestCase):
    def report(self) -> MovedReport:
        old = version(stored(1, 90.0, points={"everify": 12.0}), stored(2, 80.0), stored(3, 70.0), stored(4, 60.0))
        new = version(stored(1, 10.0, points={"everify": -5.0}), stored(2, 80.0, hide={"kind": "no_go"}),
                      stored(3, 70.0), stored(5, 65.0))
        return what_moved(old, new, {1: f"Acme {chr(0x2014)} Robotics", 2: "Beta", 3: "Gamma", 4: "Delta", 5: "Eps"},
                          old_version="rk-aaaaaaaaaaaa", new_version="rk-bbbbbbbbbbbb", min_move=1, top_n=4)

    def test_lines_have_no_dashes_and_name_both_versions(self):
        lines = self.report().lines()
        text = "\n".join(lines)
        for dash in DASHES:
            self.assertNotIn(dash, text)
        self.assertIn("rk-aaaaaaaaaaaa", lines[0])
        self.assertIn("rk-bbbbbbbbbbbb", lines[0])
        self.assertIn("Acme - Robotics", text)
        self.assertIn("everify", text)
        self.assertTrue(all(isinstance(l, str) and l.strip() == l and "\n" not in l for l in lines))

    def test_an_empty_report_still_names_both_versions_and_says_nothing_moved(self):
        got = what_moved(version(stored(1, 1.0)), version(stored(1, 1.0)), names(1), old_version="rk-a",
                         new_version="rk-b", min_move=25, top_n=50)
        lines = got.lines()
        self.assertEqual(got.old, "rk-a")
        self.assertEqual(got.new, "rk-b")
        self.assertIn("rk-a", lines[0])
        self.assertIn("rk-b", lines[0])
        self.assertIn("nothing moved", " ".join(lines).lower())

    def test_every_section_has_a_heading_when_it_has_rows(self):
        text = "\n".join(self.report().lines()).lower()
        for heading in ("moved up", "moved down", "entered the top 4", "left the top 4", "hide changes"):
            self.assertIn(heading, text, heading)


def fake_scored(cid: int, name: str, points: dict[str, float], *, hide: Hide | None = None):
    """A Scored row whose named signals carry the given points and whose score is their sum."""
    base = score_company(neutral_facts(company_id=cid, name=name), hide, SIGNALS, CFG)
    values = dict(base.values)
    for n, p in points.items():
        values[n] = dataclasses.replace(values[n], points=p, raw_points=p)
    return dataclasses.replace(base, values=values, score=round(sum(v.points for v in values.values()), 4))


class ScoredToStoredTests(unittest.TestCase):
    def test_a_scored_row_reads_like_the_stored_one(self):
        rows = [fake_scored(1, "Alpha", {"everify": 12.0}), fake_scored(2, "Beta", {"live_role": 30.0})]
        conn = fresh_store()
        self.addCleanup(conn.close)
        for i in (1, 2):
            seed_company(conn, f"Co {i}")
        write_scores(conn, rows, "rk-x", T1)
        from_store = load_scores(conn, "rk-x")
        converted = scored_to_stored(rows)
        self.assertEqual(sorted(converted), [1, 2])
        for cid in (1, 2):
            self.assertEqual(converted[cid].score, from_store[cid].score)
            self.assertEqual(converted[cid].lanes, from_store[cid].lanes)
            self.assertEqual(converted[cid].signals, from_store[cid].signals)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.path = self.conn.execute("PRAGMA database_list").fetchone()["file"]
        self.ids = {n: seed_company(self.conn, n) for n in ("Alpha", "Beta", "Gamma")}

    def put(self, version: str, at: str, rows) -> None:
        write_scores(self.conn, rows, version, at)

    def main(self, *argv: str) -> tuple[int, str, str]:
        from outreach.ranking.__main__ import main
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict("os.environ", {"OUTREACH_DB": self.path}), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def snapshot(self) -> dict:
        names = [r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {n: [tuple(r) for r in self.conn.execute(f"SELECT * FROM {n}")] for n in names}

    def three_versions(self):
        a, b, c = self.ids["Alpha"], self.ids["Beta"], self.ids["Gamma"]
        self.put("rk-oldest", "2026-10-20T08:00:00Z", [fake_scored(a, "Alpha", {"live_role": 90.0}),
                                                       fake_scored(b, "Beta", {"live_role": 10.0})])
        self.put("rk-middle", "2026-10-21T08:00:00Z", [fake_scored(a, "Alpha", {"live_role": 50.0}),
                                                       fake_scored(b, "Beta", {"live_role": 20.0})])
        self.put("rk-newest", "2026-10-22T08:00:00Z", [fake_scored(a, "Alpha", {"live_role": 50.0}),
                                                       fake_scored(b, "Beta", {"live_role": 20.0, "everify": 40.0}),
                                                       fake_scored(c, "Gamma", {"live_role": 5.0})])

    def test_cli_diff_defaults_to_the_two_newest_versions(self):
        self.three_versions()
        code, out, err = self.main("diff", "--min-move", "1", "--top-n", "10")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("rk-middle", out)
        self.assertIn("rk-newest", out)
        self.assertNotIn("rk-oldest", out)
        self.assertIn("Beta", out)
        self.assertIn("everify +40.0", out)
        self.assertIn("Gamma", out)  # only in the newest version
        for dash in DASHES:
            self.assertNotIn(dash, out)

    def test_cli_diff_takes_two_named_versions(self):
        self.three_versions()
        code, out, _ = self.main("diff", "--old", "rk-oldest", "--new", "rk-newest", "--min-move", "1", "--top-n", "10")
        self.assertEqual(code, 0)
        self.assertIn("rk-oldest", out)
        self.assertIn("rk-newest", out)
        self.assertNotIn("rk-middle", out)

    def test_cli_diff_uses_the_config_numbers_by_default(self):
        self.three_versions()
        code, out, _ = self.main("diff")
        self.assertEqual(code, 0)
        self.assertIn(f"{CFG.ranker['diff_min_rank_move']}", out)
        self.assertIn(f"{CFG.ranker['diff_top_n']}", out)

    def test_cli_diff_with_fewer_than_two_versions_says_so_and_fails(self):
        code, out, err = self.main("diff")
        self.assertEqual(code, 1)
        self.assertIn("two", err)
        self.put("rk-only", T1, [fake_scored(self.ids["Alpha"], "Alpha", {})])
        code, _, err = self.main("diff")
        self.assertEqual(code, 1)
        self.assertIn("two", err)

    def test_cli_diff_with_an_unknown_version_fails_and_names_the_stored_ones(self):
        self.three_versions()
        code, _, err = self.main("diff", "--old", "rk-nope")
        self.assertEqual(code, 1)
        self.assertIn("rk-nope", err)
        self.assertIn("rk-newest", err)

    def test_rank_diff_with_dry_runs_and_writes_nothing(self):
        assert rs.current_ruleset(self.conn) == ruleset_version()
        # An old stored version in which the one company with a live role scored far lower.
        cid, board = seed_company(self.conn, "Delta"), None
        board = rs.board(self.conn, cid)
        pid = rs.posting(self.conn, cid, "Software Engineer, New Grad", board_id=board, posted_on="2026-10-17")
        rs.decision(self.conn, pid)
        self.put("rk-old", "2026-10-21T08:00:00Z", [fake_scored(self.ids["Alpha"], "Alpha", {"live_role": 20.0}),
                                                    fake_scored(self.ids["Beta"], "Beta", {"live_role": 10.0}),
                                                    fake_scored(self.ids["Gamma"], "Gamma", {"live_role": 5.0}),
                                                    fake_scored(cid, "Delta", {"live_role": -50.0})])
        before = self.snapshot()
        code, out, err = self.main("rank", "--diff-with", "rk-old", "--today", "2026-10-22", "--min-move", "1")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.snapshot(), before)  # no company_score row, no job_run, no event, no review item
        self.assertIn("rk-old", out)
        self.assertIn(ranker_version(), out)
        self.assertIn("Delta", out)
        self.assertIn("dry run", out.lower())
        self.assertIn("live_role", out)

    def test_rank_diff_with_an_unknown_version_fails_before_ranking(self):
        before = self.snapshot()
        code, _, err = self.main("rank", "--diff-with", "rk-nope", "--today", "2026-10-22")
        self.assertEqual(code, 1)
        self.assertIn("rk-nope", err)
        self.assertEqual(self.snapshot(), before)

    def test_rank_without_diff_with_still_prints_the_plain_report(self):
        code, out, _ = self.main("rank", "--dry-run", "--today", "2026-10-22")
        self.assertEqual(code, 0)
        self.assertNotIn("What moved", out)


if __name__ == "__main__":
    unittest.main()
