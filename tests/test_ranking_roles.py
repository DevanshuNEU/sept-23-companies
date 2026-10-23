"""Visible postings, the best posting and the role lanes (issue #6, Task 4; ruling 2026-10-22 on stale postings).

A stale posting is never a role: no lane, never the best posting, not open, listed last with its reason. A failed
board read never makes one stale."""
import unittest
from datetime import date

from outreach.crawl.stale import load_stale
from outreach.ranking.common import canonical_ids, doubtful_board_ids
from outreach.ranking.roles import LANE_ORDER, Best, Roles, company_board_states, load_roles, role_sort_key
from tests import ranking_store as rs
from tests.storetest import fresh_store, seed_company

TODAY = date(2026, 10, 22)
NG, INTERN, SENIOR = "Software Engineer, New Grad", "Software Engineering Intern", "Senior Software Engineer"
OLD = "posted 2016-03-02, over 12 months"


def ago(days: int) -> str:
    return date.fromordinal(TODAY.toordinal() - days).isoformat()


class Store(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.co = seed_company(self.conn, "Acme")
        self.board = rs.board(self.conn, self.co)

    def roles(self, stale=None) -> dict[int, Roles]:
        return load_roles(self.conn, canonical_ids(self.conn), doubtful_board_ids(self.conn), TODAY,
                          load_stale(self.conn, TODAY, max_age_months=12) if stale is None else stale)

    def role(self, company_id=None) -> Roles:
        return self.roles()[company_id or self.co]

    def add(self, kind="ng", *, company_id=None, board_id="default", **fields) -> int:
        """A posting decided directly. kind: ng (new grad), intern, senior, or any decision fields."""
        title = {"ng": NG, "intern": INTERN, "senior": SENIOR}[kind]
        posting_fields = {k: fields.pop(k) for k in ("posted_on", "first_seen_at", "last_seen_at", "closed",
                                                     "location", "pay") if k in fields}
        posting_fields.setdefault("posted_on", ago(5))
        pid = rs.posting(self.conn, company_id or self.co, title,
                         board_id=self.board if board_id == "default" else board_id, **posting_fields)
        lane = {"ng": dict(), "intern": dict(lane="intern", level_tier="not_stated"),
                "senior": dict(level_tier="senior")}[kind]
        rs.decision(self.conn, pid, **{**lane, **fields})
        return pid


class LaneTests(Store):
    def test_new_grad_beats_intern_beats_senior(self):
        senior = self.add("senior")
        intern = self.add("intern")
        ng = self.add("ng")
        r = self.role()
        self.assertEqual(r.best.posting_id, ng)
        self.assertEqual(r.best.lane, "level_role")
        self.assertEqual([b.posting_id for b in r.visible], [ng, intern, senior])
        self.assertEqual([b.lane for b in r.visible], ["level_role", "intern_junior", "any_us_role"])
        self.assertEqual(r.lanes, ("level_role", "intern_junior", "any_us_role"))
        self.assertEqual(r.lanes, LANE_ORDER)
        self.assertEqual(r.open_count, 3)

    def test_lanes_are_distinct_and_strongest_first(self):
        self.add("senior")
        self.add("senior")
        self.add("intern")
        self.assertEqual(self.role().lanes, ("intern_junior", "any_us_role"))

    def test_freshest_wins_inside_a_lane(self):
        self.add("ng", posted_on=ago(20))
        fresh = self.add("ng", posted_on=ago(3))
        r = self.role()
        self.assertEqual((r.best.posting_id, r.best.age_days), (fresh, 3))
        other = seed_company(self.conn, "Tie")
        silent = self.add("ng", company_id=other, board_id=None, posted_on=ago(7))
        sponsor = self.add("ng", company_id=other, board_id=None, posted_on=ago(7), sponsorship="yes")
        opt = self.add("ng", company_id=other, board_id=None, posted_on=ago(7), sponsorship="opt_only")
        later = self.add("ng", company_id=other, board_id=None, posted_on=ago(7))
        r = self.role(other)
        self.assertEqual([b.posting_id for b in r.visible], [sponsor, opt, later, silent])

    def test_role_sort_key_puts_unknown_age_last(self):
        def best(pid, lane="level_role", age=None, sponsorship="silent"):
            return Best(pid, lane, "t", "new_grad", sponsorship, age, "", "", None, "2026-10-21", "read_ok", True)
        ordered = sorted([best(1, age=None), best(2, age=40), best(3, lane="intern_junior", age=1),
                          best(4, age=40, sponsorship="yes")], key=role_sort_key)
        self.assertEqual([b.posting_id for b in ordered], [4, 2, 1, 3])

    def test_hidden_postings_are_not_roles(self):
        citizen = self.add("ng", citizenship=1)
        senior = self.add("senior")
        r = self.role()
        self.assertNotIn(citizen, [b.posting_id for b in r.visible])
        self.assertEqual(r.best.posting_id, senior)
        self.assertEqual(r.open_count, 2)
        only = seed_company(self.conn, "Only hidden")
        self.add("ng", company_id=only, board_id=None, sponsorship="no")
        self.add("ng", company_id=only, board_id=None, us="no")
        r = self.role(only)
        self.assertIsNone(r.best)
        self.assertEqual((r.lanes, r.visible, r.stale), ((), (), ()))

    def test_student_only_intern_is_not_visible(self):
        self.add("intern", eligibility="excluded", eligibility_reason="current_student")
        self.add("intern", eligibility="excluded", eligibility_reason="grad_window")
        r = self.role()
        self.assertIsNone(r.best)
        self.assertEqual(r.lanes, ())

    def test_off_lane_and_not_engineering_postings_are_not_roles(self):
        self.add("ng", lane="off_lane", level_tier="not_stated")
        self.add("ng", lane="not_engineering", level_tier="not_stated")
        self.assertIsNone(self.role().best)

    def test_closed_postings_are_skipped(self):
        self.add("ng", closed=True)
        self.assertNotIn(self.co, self.roles())
        live = self.add("senior")
        r = self.role()
        self.assertEqual(([b.posting_id for b in r.visible], r.open_count), ([live], 1))

    def test_posting_without_a_current_decision_is_counted_undecided(self):
        rs.posting(self.conn, self.co, NG, board_id=self.board)
        r = self.role()
        self.assertEqual((r.undecided, r.open_count, r.best), (1, 1, None))

    def test_a_decision_under_an_old_ruleset_is_not_current(self):
        self.add("ng")
        self.conn.execute("UPDATE ruleset SET is_current = 0")
        r = self.role()
        self.assertEqual((r.undecided, r.best), (1, None))


class TrustTests(Store):
    def test_doubtful_board_postings_count_for_nothing(self):
        rs.flag_board(self.conn, self.co, self.board)
        self.add("ng")
        r = self.role()
        self.assertIsNone(r.best)
        self.assertEqual((r.ignored, r.open_count, r.lanes), (1, 0, ()))
        url_co = seed_company(self.conn, "Url")
        pid = self.add("ng", company_id=url_co, board_id=None)
        r = self.role(url_co)
        self.assertEqual((r.best.posting_id, r.best.board_state, r.ignored, r.open_count), (pid, "no_board", 0, 1))

    def test_an_unread_stapply_board_counts_for_nothing(self):
        b = rs.board(self.conn, self.co, found_by="stapply_name", last_read=None)
        self.add("ng", board_id=b)
        r = self.role()
        self.assertEqual((r.best, r.ignored), (None, 1))

    def test_postings_on_an_inactive_board_are_not_roles(self):
        b = rs.board(self.conn, self.co, active=0)
        self.add("ng", board_id=b)
        r = self.role()
        self.assertEqual((r.best, r.ignored, r.open_count, r.lanes), (None, 1, 0, ()))

    def test_a_company_whose_only_board_is_doubtful_grades_no_role_low(self):
        rs.flag_board(self.conn, self.co, self.board)
        self.add("ng")
        canon, doubtful = canonical_ids(self.conn), doubtful_board_ids(self.conn)
        self.assertEqual(company_board_states(self.conn, canon, doubtful)[self.co], "unconfirmed")
        c = self.role().confidence
        self.assertEqual((c.level, c.basis), ("low", "its only board is not confirmed yet"))
        guessed = seed_company(self.conn, "Guessed")
        b = rs.board(self.conn, guessed, found_by="stapply_name", last_read=None)
        self.add("ng", company_id=guessed, board_id=b)
        self.assertEqual(self.role(guessed).confidence.level, "low")
        both = seed_company(self.conn, "Both")
        rs.flag_board(self.conn, both, rs.board(self.conn, both))
        rs.board(self.conn, both, last_read="ok")
        self.add("ng", company_id=both, board_id=None, us="no")
        self.assertEqual(self.role(both).confidence.level, "high")  # a confirmed board read ok and listed nothing

    def test_merged_away_company_postings_credit_the_survivor(self):
        dup = seed_company(self.conn, "Acme Inc")
        pid = self.add("ng", company_id=dup)
        rs.merge(self.conn, dup, self.co)
        got = self.roles()
        self.assertNotIn(dup, got)
        self.assertEqual(got[self.co].best.posting_id, pid)


class AgeTests(Store):
    def test_missing_posted_on_uses_first_seen_date_and_age_never_negative(self):
        pid = self.add("ng", posted_on=None, first_seen_at="2026-10-12T23:00:00Z")
        b = self.role().best
        self.assertEqual((b.posting_id, b.age_days, b.posted_on), (pid, 10, None))
        future = seed_company(self.conn, "Future")
        self.add("ng", company_id=future, board_id=None, posted_on="2026-11-02")
        self.add("ng", company_id=future, board_id=None, posted_on=None, first_seen_at="2026-11-01T00:00:00Z",
                 last_seen_at="2026-11-01T00:00:00Z")
        self.assertEqual([v.age_days for v in self.role(future).visible], [0, 0])

    def test_company_with_no_postings_has_no_entry_or_an_empty_one(self):
        empty = seed_company(self.conn, "Nothing")
        got = self.roles()
        self.assertNotIn(empty, got)  # absent: Task 5 reads absence as no roles
        self.assertNotIn(self.co, got)  # a board with no postings is no entry either

    def test_best_carries_the_posting_fields(self):
        pid = self.add("ng", posted_on=ago(4), location="Austin, TX", pay="$120,000 to $140,000",
                       sponsorship="yes", provisional=0)
        self.conn.execute("UPDATE posting SET title = ? WHERE posting_id = ?",
                          ("Engineer " + chr(0x2014) + " New Grad", pid))
        b = self.role().best
        self.assertEqual(b, Best(pid, "level_role", "Engineer - New Grad", "new_grad", "yes", 4,
                                 "$120,000 to $140,000", "Austin, TX", ago(4), "2026-10-21", "read_ok", False,
                                 level_basis="Title says new grad"))


class StaleTests(Store):
    def test_a_2016_posting_is_not_a_role_and_is_listed_stale_last(self):
        live = self.add("ng", posted_on=ago(3))
        old = self.add("ng", posted_on="2016-03-02")
        stale = load_stale(self.conn, TODAY, max_age_months=12)
        self.assertEqual(set(stale), {old})
        r = self.roles(stale)[self.co]
        self.assertEqual([b.posting_id for b in r.visible], [live])
        self.assertEqual(r.best.posting_id, live)
        self.assertEqual([(b.posting_id, b.stale_reason) for b in r.stale], [(old, OLD)])
        self.assertEqual(r.best.stale_reason, "")
        self.assertEqual(r.open_count, 1)

    def test_a_company_with_only_a_2016_posting_has_no_lane_no_best_and_a_high_grade(self):
        self.add("ng", posted_on="2016-03-02")
        r = self.role()
        self.assertEqual((r.lanes, r.best, r.visible, r.open_count), ((), None, (), 0))
        self.assertEqual(len(r.stale), 1)
        self.assertEqual(r.confidence.level, "high")
        self.assertIn(OLD, r.confidence.basis)

    def test_stale_postings_never_make_a_lane_best_posting_or_open_count(self):
        ng = self.add("ng", posted_on="2016-03-02")
        intern = self.add("intern", posted_on="2017-05-01")
        r = self.role()
        self.assertEqual((r.lanes, r.best, r.visible, r.open_count), ((), None, (), 0))
        self.assertEqual([b.posting_id for b in r.stale], [ng, intern])  # lane then age
        self.assertEqual([b.lane for b in r.stale], ["level_role", "intern_junior"])

    def test_a_first_empty_read_makes_the_posting_stale_not_closed(self):
        b = rs.board(self.conn, self.co, last_read="empty", read_at="2026-10-21T08:00:00Z")
        pid = self.add("ng", board_id=b, last_seen_at="2026-10-18T08:00:00Z")
        r = self.role()
        self.assertEqual([s.posting_id for s in r.stale], [pid])
        self.assertEqual(r.stale[0].stale_reason,
                         "not on the board at its latest read (last listed 2026-10-18 or later)")
        self.assertIsNone(self.conn.execute("SELECT closed_at FROM posting WHERE posting_id = ?", (pid,)).fetchone()[0])
        self.assertIsNone(r.best)
        self.assertEqual(r.confidence.level, "medium")

    def test_a_failed_board_read_keeps_postings_live_with_a_medium_grade(self):
        b = rs.board(self.conn, self.co, last_read="error", error_streak=1, empty_streak=0)
        pid = self.add("ng", board_id=b, last_seen_at="2026-10-19T08:00:00Z")
        r = self.role()
        self.assertEqual([v.posting_id for v in r.visible], [pid])
        self.assertEqual(r.stale, ())
        self.assertEqual(r.best.board_state, "read_failed")
        self.assertEqual(r.confidence.level, "medium")
        self.assertIn("last board read failed", r.confidence.basis)
        self.assertIn("2026-10-19", r.confidence.basis)

    def test_a_stale_posting_that_would_be_hidden_is_not_listed_as_stale(self):
        self.add("ng", posted_on="2016-03-02", citizenship=1)
        r = self.role()
        self.assertEqual((r.visible, r.stale), ((), ()))

    def test_stale_postings_on_a_doubtful_board_count_as_ignored_not_stale(self):
        rs.flag_board(self.conn, self.co, self.board)
        self.add("ng", posted_on="2016-03-02")
        r = self.role()
        self.assertEqual((r.ignored, r.stale, r.open_count), (1, (), 0))


class GradeTests(Store):
    def test_role_grade_by_board_state(self):
        self.add("ng", posted_on=ago(3))
        self.assertEqual(self.role().confidence.level, "high")
        no_date = seed_company(self.conn, "No date")
        self.add("ng", company_id=no_date, board_id=rs.board(self.conn, no_date), posted_on=None)
        self.assertEqual(self.role(no_date).confidence.level, "medium")
        imported = seed_company(self.conn, "Imported")
        self.add("ng", company_id=imported, board_id=rs.board(self.conn, imported, last_read=None))
        c = self.role(imported).confidence
        self.assertEqual(c.level, "low")
        self.assertIn("not read by this system yet", c.basis)
        url = seed_company(self.conn, "Url")
        self.add("ng", company_id=url, board_id=None)
        self.assertEqual(self.role(url).confidence.level, "low")

    def test_no_live_role_is_graded_from_the_company_boards(self):
        self.add("senior", citizenship=1)  # an open posting, but hidden: no role
        r = self.role()
        self.assertIsNone(r.best)
        self.assertEqual(r.confidence.level, "high")  # the board read ok and listed nothing he can take
        url = seed_company(self.conn, "Url")
        self.add("ng", company_id=url, board_id=None, us="no")
        self.assertEqual(self.role(url).confidence.level, "low")  # no board: roles unknown, not absent


class EmptyReadTests(Store):
    def test_an_empty_last_read_grades_no_live_role_medium_not_unread(self):
        co = seed_company(self.conn, "Empty")
        b = rs.board(self.conn, co, last_read="empty")
        self.add("ng", company_id=co, board_id=b, citizenship=1)  # stale and hidden, so listed nowhere
        self.assertEqual(company_board_states(self.conn, canonical_ids(self.conn), frozenset())[co], "read_empty")
        r = self.role(co)
        self.assertEqual((r.best, r.stale), (None, ()))
        self.assertEqual((r.confidence.level, r.confidence.basis),
                         ("medium", "the board listed no roles at its last read"))
        self.assertNotIn("not read", r.confidence.basis)

    def test_board_state_order_is_ok_then_empty_then_failed_then_never_read(self):
        a, b, c = (seed_company(self.conn, n) for n in "abc")
        rs.board(self.conn, a, last_read="empty")
        rs.board(self.conn, a, last_read="ok")
        rs.board(self.conn, b, last_read="error")
        rs.board(self.conn, b, last_read="empty")
        rs.board(self.conn, c, last_read=None)
        states = company_board_states(self.conn, canonical_ids(self.conn), frozenset())
        self.assertEqual((states[a], states[b], states[c]), ("read_ok", "read_empty", "never_read"))

    def test_a_posting_on_an_empty_board_carries_that_state(self):
        b = rs.board(self.conn, self.co, last_read="empty")
        self.add("ng", board_id=b, last_seen_at="2026-10-18T08:00:00Z")
        r = self.role()
        self.assertEqual(r.stale[0].board_state, "read_empty")
        self.assertEqual(r.confidence.level, "medium")  # stale by absence only
        live = self.roles(stale={})[self.co]  # the same posting if it were not stale
        self.assertEqual((live.best.board_state, live.confidence.level), ("read_empty", "medium"))


class BoardStateTests(Store):
    def test_company_board_states_cover_the_four_cases(self):
        ok, unchanged, failed, never, none = (seed_company(self.conn, n) for n in ("ok", "un", "err", "nev", "none"))
        rs.board(self.conn, ok, last_read="error")
        rs.board(self.conn, ok, last_read="ok")
        rs.board(self.conn, unchanged, last_read="unchanged")
        rs.board(self.conn, failed, last_read="error")
        rs.board(self.conn, failed, last_read=None)
        rs.board(self.conn, never, last_read=None)
        rs.board(self.conn, none, last_read="ok", active=0)
        dup = seed_company(self.conn, "dup")
        rs.board(self.conn, dup, last_read="ok")
        rs.merge(self.conn, dup, never)
        states = company_board_states(self.conn, canonical_ids(self.conn), frozenset())
        self.assertEqual(states[self.co], "read_ok")
        self.assertEqual(states[ok], "read_ok")
        self.assertEqual(states[unchanged], "read_ok")
        self.assertEqual(states[failed], "read_failed")
        self.assertEqual(states[never], "read_ok")  # the merged-away company's board counts for the survivor
        self.assertNotIn(none, states)
        self.assertNotIn(dup, states)
        lone = seed_company(self.conn, "lone")
        rs.board(self.conn, lone, last_read=None)
        self.assertEqual(company_board_states(self.conn, canonical_ids(self.conn), frozenset())[lone], "never_read")


if __name__ == "__main__":
    unittest.main()


class UndecidedGradeTests(Store):
    def test_open_postings_with_no_decision_grade_live_role_low(self):
        rs.posting(self.conn, self.co, NG, board_id=self.board)
        r = self.role()
        self.assertEqual((r.best, r.undecided), (None, 1))
        self.assertEqual((r.confidence.level, r.confidence.basis), ("low", "1 open posting not decided yet, roles unknown"))
        rs.posting(self.conn, self.co, NG, board_id=self.board)
        self.assertEqual(self.role().confidence.basis, "2 open postings not decided yet, roles unknown")

    def test_a_live_best_posting_keeps_its_own_grade_when_others_are_undecided(self):
        self.add("ng")
        rs.posting(self.conn, self.co, NG, board_id=self.board)
        r = self.role()
        self.assertEqual((r.undecided, r.confidence.level), (1, "high"))

    def test_undecided_postings_beat_a_stale_only_grade(self):
        self.add("ng", posted_on="2016-03-02")
        rs.posting(self.conn, self.co, NG, board_id=self.board)
        r = self.role()
        self.assertEqual((r.best, len(r.stale)), (None, 1))
        self.assertEqual(r.confidence.level, "low")


class QueryPlanTests(Store):
    def plan(self, sql: str) -> list[str]:
        return [row["detail"] for row in self.conn.execute("EXPLAIN QUERY PLAN " + sql)]

    def test_the_roles_query_looks_decisions_up_by_key_not_by_scanning_a_view(self):
        from outreach.ranking.roles import _QUERY
        for line in self.plan(_QUERY):
            self.assertFalse(line.startswith("SCAN d") or "MATERIALIZE" in line or "current_decision" in line, line)
        self.assertTrue(any(line.startswith("SEARCH d USING") for line in self.plan(_QUERY)), self.plan(_QUERY))

    def test_the_hide_query_never_scans_the_decision_side(self):
        from outreach.ranking.hide import _DECISION_QUERY
        for line in self.plan(_DECISION_QUERY):
            self.assertFalse(line.startswith("SCAN d") or "MATERIALIZE" in line or "current_decision" in line, line)
