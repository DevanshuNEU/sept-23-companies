"""The outcome dataset (#81, Task 3): one row per canonical company he reached, with its best outcome and its facts,
read in a few set-based queries."""
import unittest

from outreach.insights.bands import Features
from outreach.insights.dataset import (INTERVIEW_OUTCOMES, OUTCOME_ORDER, OutcomeRow, interview_rows,
                                       load_board_systems, load_outcome_rows)
from outreach.ranking.common import canonical_ids
from tests import apiseed as S
from tests.insights_seed import application, merge, outcome_touch
from tests.storetest import fresh_store

SENT = "2026-10-01T15:00:00Z"
LATER = "2026-10-05T15:00:00Z"


def rows_by_id(rows: list[OutcomeRow]) -> dict[int, OutcomeRow]:
    return {r.company_id: r for r in rows}


class DatasetTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()

    def reached(self, name: str, **touch) -> tuple[int, int]:
        cid = S.company(self.conn, name)
        pid = S.person(self.conn, cid, f"{name} Person")
        touch.setdefault("sent_at", SENT)
        outcome_touch(self.conn, cid, pid, **touch)
        return cid, pid

    def test_outcome_order(self):
        self.assertEqual(("none", "accepted", "replied", "referred", "interview", "offer"), OUTCOME_ORDER)
        self.assertEqual(("interview", "offer"), INTERVIEW_OUTCOMES)

    def test_one_row_per_reached_company_and_none_for_unreached(self):
        a, _ = self.reached("Alpha")
        b, _ = self.reached("Beta", replied_at=LATER)
        S.company(self.conn, "Never Reached")
        rows = load_outcome_rows(self.conn)
        self.assertEqual([a, b], [r.company_id for r in rows])
        alpha, beta = rows
        self.assertEqual(("none", "none", 1), (alpha.best_outcome, alpha.outcome_source, alpha.touches))
        self.assertEqual("linkedin_request", alpha.best_channel)  # the way in tried, even with no outcome
        self.assertEqual(("replied", "touch"), (beta.best_outcome, beta.outcome_source))
        self.assertEqual("2026-10-01", alpha.first_touch_on)
        self.assertEqual([], interview_rows(rows))

    def test_best_outcome_takes_the_highest_and_its_touch(self):
        cid = S.company(self.conn, "Retell")
        p1 = S.person(self.conn, cid, "Ann Email")
        p2 = S.person(self.conn, cid, "Bo Manager", role_kind="manager")
        outcome_touch(self.conn, cid, p1, channel="email", sent_at=SENT, replied_at=LATER, role_kind="engineer")
        outcome_touch(self.conn, cid, p2, channel="linkedin_request", sent_at=LATER, accepted_at=LATER,
                      interview_at="2026-10-09T15:00:00Z", role_kind="manager", lane="intern_junior")
        (row,) = load_outcome_rows(self.conn)
        self.assertEqual("interview", row.best_outcome)
        self.assertEqual("touch", row.outcome_source)
        self.assertEqual("linkedin_request", row.best_channel)
        self.assertEqual("manager", row.best_role_kind)
        self.assertEqual("intern_junior", row.best_lane)
        self.assertEqual(("email", "linkedin_request"), row.channels)
        self.assertEqual(2, row.touches)
        self.assertEqual("2026-10-09", row.interview_on)
        self.assertEqual([row], interview_rows([row]))

    def test_merged_duplicates_count_once(self):
        gm_big = S.company(self.conn, "General Motors", company_id=3)
        gm = S.company(self.conn, "gm", company_id=7)
        p_big = S.person(self.conn, gm_big, "Reply Person")
        p_small = S.person(self.conn, gm, "Interview Person")
        outcome_touch(self.conn, gm_big, p_big, sent_at=SENT, replied_at=LATER)
        outcome_touch(self.conn, gm, p_small, sent_at=SENT, interview_at=LATER)
        S.evidence(self.conn, gm, "size", "120", low=120, high=200)
        merge(self.conn, gm, gm_big)
        rows = load_outcome_rows(self.conn)
        self.assertEqual(1, len(rows))
        (row,) = rows
        self.assertEqual(3, row.company_id)
        self.assertEqual("General Motors", row.name)
        self.assertEqual((3, 7), row.merged_ids)
        self.assertEqual("interview", row.best_outcome)
        self.assertEqual(2, row.touches)
        self.assertEqual((120, 200), (row.size_low, row.size_high))
        self.assertEqual("51 to 300", row.features.size_band)
        # The same with a canon passed in.
        self.assertEqual(rows, load_outcome_rows(self.conn, canon=canonical_ids(self.conn)))

    def test_undone_touches_count_nowhere(self):
        only_undone = S.company(self.conn, "Undone Only")
        pid = S.person(self.conn, only_undone)
        outcome_touch(self.conn, only_undone, pid, sent_at=SENT, undone_at=LATER)
        mixed, mixed_person = self.reached("Mixed", replied_at=LATER)
        outcome_touch(self.conn, mixed, mixed_person, channel="email", sent_at=SENT, undone_at=LATER)
        rows = load_outcome_rows(self.conn)
        self.assertEqual([mixed], [r.company_id for r in rows])
        self.assertEqual((1, ("linkedin_request",)), (rows[0].touches, rows[0].channels))

    def test_application_and_closed_status_outcomes(self):
        applied = S.company(self.conn, "Applied")
        application(self.conn, applied, outcome="interview", outcome_at="2026-10-12T15:00:00Z")
        closed = S.company(self.conn, "Closed", status="closed", close_reason="interview",
                           status_at="2026-10-20T03:00:00Z")
        offer = S.company(self.conn, "Offer Co")
        application(self.conn, offer, outcome="offer", outcome_at="2026-10-14T15:00:00Z")
        S.company(self.conn, "Closed Otherwise", status="closed", close_reason="referral")
        by = rows_by_id(load_outcome_rows(self.conn))
        self.assertEqual({applied, closed, offer}, set(by))
        a = by[applied]
        self.assertEqual(("interview", "application", "application"), (a.best_outcome, a.outcome_source,
                                                                        a.best_channel))
        self.assertEqual(("2026-10-12", 0, "2026-10-01"), (a.interview_on, a.touches, a.first_touch_on))
        c = by[closed]
        self.assertEqual(("interview", "company_status", None), (c.best_outcome, c.outcome_source, c.best_channel))
        self.assertEqual("2026-10-19", c.interview_on)  # 03:00 UTC is the evening before in New York
        self.assertEqual("closed", c.status)
        self.assertEqual(("offer", "2026-10-14", None), (by[offer].best_outcome, by[offer].offer_on,
                                                         by[offer].interview_on))
        self.assertEqual({applied, closed, offer}, {r.company_id for r in interview_rows(by.values())})

    def test_days_to_reply_uses_the_earliest_replied_touch(self):
        cid, pid = self.reached("Quick", sent_at="2026-10-01T15:00:00Z", replied_at="2026-10-04T03:00:00Z")
        outcome_touch(self.conn, cid, pid, channel="email", sent_at="2026-10-02T15:00:00Z",
                      replied_at="2026-10-20T15:00:00Z")
        (row,) = load_outcome_rows(self.conn)
        self.assertEqual(2.5, row.days_to_reply)
        self.assertEqual("2026-10-03", row.first_reply_on)
        quiet, _ = self.reached("Quiet")
        self.assertIsNone(rows_by_id(load_outcome_rows(self.conn))[quiet].days_to_reply)

    def test_level_comes_from_the_touch_then_the_application_then_open_at_the_time(self):
        # The touch's own posting.
        t = S.company(self.conn, "Touch Posting")
        p_t = S.posting(self.conn, t)
        S.decision(self.conn, p_t, level_tier="swe2")
        self.reached_at(t, posting_id=p_t)
        # The application's posting.
        a = S.company(self.conn, "App Posting")
        p_a = S.posting(self.conn, a)
        S.decision(self.conn, p_a, level_tier="new_grad")
        self.reached_at(a)
        application(self.conn, a, posting_id=p_a)
        # Open at the time: one open before the touch, one first seen after it, one closed before it.
        o = S.company(self.conn, "Open Then")
        before = S.posting(self.conn, o, first_seen_at="2026-09-01T12:00:00Z")
        S.decision(self.conn, before, level_tier="early_career")
        after = S.posting(self.conn, o, first_seen_at="2026-10-02T12:00:00Z")
        S.decision(self.conn, after, level_tier="swe2")
        gone = S.posting(self.conn, o, first_seen_at="2026-09-10T12:00:00Z", closed_at="2026-09-20T12:00:00Z")
        S.decision(self.conn, gone, level_tier="swe1")
        not_eng = S.posting(self.conn, o, first_seen_at="2026-09-15T12:00:00Z")
        S.decision(self.conn, not_eng, lane="not_engineering", level_tier="open", level_ok=0)
        self.reached_at(o)
        # An intern posting on the touch.
        i = S.company(self.conn, "Intern Co")
        p_i = S.posting(self.conn, i)
        S.decision(self.conn, p_i, lane="intern", level_tier="not_stated", level_ok=0)
        self.reached_at(i, posting_id=p_i)
        # Nothing to go on.
        n = S.company(self.conn, "No Posting")
        self.reached_at(n)
        later_only = S.company(self.conn, "Later Only")
        p_l = S.posting(self.conn, later_only, first_seen_at="2026-10-02T12:00:00Z")
        S.decision(self.conn, p_l)
        self.reached_at(later_only)

        by = rows_by_id(load_outcome_rows(self.conn))
        self.assertEqual(("swe2", "touch posting", False, "SWE II"),
                         (by[t].level_tier, by[t].level_basis, by[t].intern, by[t].features.level_band))
        self.assertEqual(("new_grad", "application posting"), (by[a].level_tier, by[a].level_basis))
        self.assertEqual(("early_career", "open at the time"), (by[o].level_tier, by[o].level_basis))
        self.assertEqual((True, "intern"), (by[i].intern, by[i].features.level_band))
        for cid in (n, later_only):
            self.assertEqual((None, "", None), (by[cid].level_tier, by[cid].level_basis, by[cid].features.level_band))

    def reached_at(self, cid: int, **touch) -> None:
        pid = S.person(self.conn, cid)
        outcome_touch(self.conn, cid, pid, sent_at=SENT, **touch)

    def test_board_system_prefers_the_active_board_with_most_open_postings(self):
        cid, _ = self.reached("Boards")
        inactive = S.board(self.conn, cid, "greenhouse", active=0)
        small = S.board(self.conn, cid, "lever")
        big = S.board(self.conn, cid, "ashby")
        for _ in range(5):
            S.posting(self.conn, cid, board_id=inactive)
        S.posting(self.conn, cid, board_id=small)
        S.posting(self.conn, cid, board_id=big)
        S.posting(self.conn, cid, board_id=big)
        for _ in range(3):
            S.posting(self.conn, cid, board_id=small, closed_at="2026-10-20T12:00:00Z")
        tie, _ = self.reached("Tie")
        S.board(self.conn, tie, "workable")
        S.board(self.conn, tie, "greenhouse")
        none, _ = self.reached("No Board")
        canon = canonical_ids(self.conn)
        self.assertEqual({cid: "ashby", tie: "workable"}, load_board_systems(self.conn, canon))
        by = rows_by_id(load_outcome_rows(self.conn))
        self.assertEqual("ashby", by[cid].board_system)
        self.assertEqual("ashby", by[cid].features.board_system)
        self.assertIsNone(by[none].board_system)

    def test_board_of_a_merged_duplicate_counts_for_its_survivor(self):
        keep, _ = self.reached("Keep")
        gone = S.company(self.conn, "Gone")
        S.board(self.conn, gone, "lever")
        merge(self.conn, gone, keep)
        self.assertEqual({keep: "lever"}, load_board_systems(self.conn, canonical_ids(self.conn)))

    def test_facts_follow_the_company_fact_winner_with_confidence(self):
        cid, _ = self.reached("Facts")
        S.evidence(self.conn, cid, "size", "500", low=500, confidence="confirmed")
        S.evidence(self.conn, cid, "size", "80", low=80, high=90, confidence="estimate", locked=True)
        S.evidence(self.conn, cid, "headcount", "40", low=40, confidence="confirmed")
        S.evidence(self.conn, cid, "stage", "series_a")
        S.evidence(self.conn, cid, "stage", "seed", confidence="guess")
        S.evidence(self.conn, cid, "hq", "Austin, TX")
        S.evidence(self.conn, cid, "everify", "enrolled")
        S.evidence(self.conn, cid, "sponsorship", "yes")
        (row,) = load_outcome_rows(self.conn)
        self.assertEqual((80, 90, "estimate"), (row.size_low, row.size_high, row.size_confidence))
        self.assertEqual(("series_a", "Austin, TX", "enrolled", "yes"),
                         (row.stage, row.hq, row.everify, row.sponsorship))
        self.assertEqual(Features("51 to 300", "Series A or B", None, None), row.features)

    def test_people_only_with_the_flag_and_never_a_placeholder(self):
        named = S.company(self.conn, "Named")
        pat = S.person(self.conn, named, "Pat Lee")
        outcome_touch(self.conn, named, pat, sent_at=SENT, replied_at=LATER)
        blank = S.company(self.conn, "Blank")
        placeholder = S.person(self.conn, blank, "Unnamed engineer (imported)", email=None, channel="linkedin")
        outcome_touch(self.conn, blank, placeholder, channel="other", sent_at=SENT, interview_at=LATER)
        self.assertEqual([None, None], [r.best_person for r in load_outcome_rows(self.conn)])
        by = rows_by_id(load_outcome_rows(self.conn, with_people=True))
        self.assertEqual("Pat Lee", by[named].best_person)
        self.assertIsNone(by[blank].best_person)

    def test_score_and_rank_from_the_latest_version(self):
        x, _ = self.reached("X")
        y, _ = self.reached("Y")
        z, _ = self.reached("Z")
        other = S.company(self.conn, "Unreached Top")
        S.score(self.conn, x, {"live_role": 99}, version="rk-old", at="2026-10-01T08:00:00Z")
        for cid, pts in ((x, 10), (y, 20), (other, 50)):
            S.score(self.conn, cid, {"live_role": pts}, version="rk-new")
        S.score(self.conn, z, {"live_role": 30}, version="rk-new", hide={"kind": "size_over"})
        by = rows_by_id(load_outcome_rows(self.conn))
        self.assertEqual((10.0, 3), (by[x].score, by[x].rank))
        self.assertEqual((20.0, 2), (by[y].score, by[y].rank))
        self.assertEqual((30.0, None), (by[z].score, by[z].rank))
        unscored = rows_by_id(load_outcome_rows(self.conn, scores=False))
        self.assertEqual((None, None), (unscored[x].score, unscored[x].rank))

    def test_no_scores_at_all_gives_none(self):
        cid, _ = self.reached("Unscored")
        row = rows_by_id(load_outcome_rows(self.conn))[cid]
        self.assertEqual((None, None), (row.score, row.rank))

    def test_an_empty_store_gives_no_rows(self):
        self.assertEqual([], load_outcome_rows(self.conn))

    def test_reads_are_set_based(self):
        for n in range(40):
            cid, pid = self.reached(f"Company {n}", replied_at=LATER if n % 3 else None,
                                    interview_at=LATER if n % 7 == 0 else None)
            S.evidence(self.conn, cid, "size", str(20 + n), low=20 + n)
            S.evidence(self.conn, cid, "stage", "seed")
            board = S.board(self.conn, cid, "ashby")
            posting = S.posting(self.conn, cid, board_id=board, first_seen_at="2026-09-01T12:00:00Z")
            S.decision(self.conn, posting)
            application(self.conn, cid, posting_id=posting)
            S.score(self.conn, cid, {"live_role": n})
        statements: list[str] = []
        self.conn.set_trace_callback(statements.append)
        try:
            rows = load_outcome_rows(self.conn, with_people=True)
        finally:
            self.conn.set_trace_callback(None)
        self.assertEqual(40, len(rows))
        self.assertLess(len(statements), 15, statements)


if __name__ == "__main__":
    unittest.main()
