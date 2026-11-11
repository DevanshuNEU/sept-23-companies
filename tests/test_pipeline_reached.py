"""His own reached mark (#195, the first to see ruling in #9): `outreach.pipeline.reached.marks`.

Done on the job list writes a `company_reached` event and its undo a `company_reached_cleared` event. A fresh store
and events written by hand: nothing here reaches the network or the live store.
"""
import unittest

from outreach.pipeline import reached
from outreach.store import append_event
from tests.ranking_store import merge
from tests.storetest import fresh_store, seed_company


class ReachedMarksTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.co = seed_company(self.conn, "Acme")

    def event(self, kind, at, company=None, actor="devanshu"):
        cid = company or self.co
        append_event(self.conn, entity="company", entity_id=cid, company_id=cid, kind=kind, actor=actor, at=at,
                     detail={"company_id": cid})

    def test_no_events_no_marks(self):
        self.assertEqual(reached.marks(self.conn), {})

    def test_a_mark_is_its_new_york_date(self):
        self.event(reached.REACHED, "2026-11-10T03:30:00Z")  # 22:30 on Nov 9 in New York
        self.assertEqual(reached.marks(self.conn), {self.co: "2026-11-09"})

    def test_clear_mark_removes_reached(self):
        self.event(reached.REACHED, "2026-11-09T15:00:00Z")
        self.event(reached.CLEARED, "2026-11-09T15:01:00Z")
        self.assertEqual(reached.marks(self.conn), {})

    def test_mark_after_clear_counts_again(self):
        self.event(reached.REACHED, "2026-11-08T15:00:00Z")
        self.event(reached.CLEARED, "2026-11-08T15:01:00Z")
        self.event(reached.REACHED, "2026-11-10T15:00:00Z")
        self.assertEqual(reached.marks(self.conn), {self.co: "2026-11-10"})

    def test_a_clear_in_the_same_second_still_clears(self):
        self.event(reached.REACHED, "2026-11-09T15:00:00Z")
        self.event(reached.CLEARED, "2026-11-09T15:00:00Z")  # written after the mark, so it wins
        self.assertEqual(reached.marks(self.conn), {})

    def test_only_his_marks_count(self):
        self.event(reached.REACHED, "2026-11-09T15:00:00Z", actor="claude")
        self.assertEqual(reached.marks(self.conn), {})

    def test_a_merged_away_company_marks_its_survivor(self):
        dupe = seed_company(self.conn, "Acme Inc")
        self.event(reached.REACHED, "2026-11-09T15:00:00Z", company=dupe)
        merge(self.conn, dupe, self.co)
        self.assertEqual(reached.marks(self.conn), {self.co: "2026-11-09"})

    def test_the_newest_mark_across_a_merge_wins(self):
        dupe = seed_company(self.conn, "Acme Inc")
        self.event(reached.REACHED, "2026-11-05T15:00:00Z")
        self.event(reached.REACHED, "2026-11-09T15:00:00Z", company=dupe)
        merge(self.conn, dupe, self.co)
        self.assertEqual(reached.marks(self.conn), {self.co: "2026-11-09"})


if __name__ == "__main__":
    unittest.main()
