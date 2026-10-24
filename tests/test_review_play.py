import unittest

from outreach.review.play import emailing, in_play, live_company_ids, ny_date
from tests.review_seed import seed_email_person, seed_person, seed_plan_row, seed_queued_message, seed_status
from tests.storetest import fresh_store, seed_company

TODAY = "2026-11-10"
DAYS = 4


class PlayTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)

    def company(self, name=None, status=None):
        cid = seed_company(self.conn, name or f"Co{self.conn.execute('SELECT COUNT(*) FROM company').fetchone()[0]}")
        if status:
            seed_status(self.conn, cid, status)
        return cid

    def play(self, today=TODAY):
        return in_play(self.conn, today=today, planned_days=DAYS)

    def email(self, today=TODAY):
        return emailing(self.conn, today=today, planned_days=DAYS)

    def test_working_company_is_in_play_as_picked(self):
        working = self.company(status="working")
        self.company()
        self.assertEqual(self.play(), {working: "picked"})

    def test_ready_plan_row_is_in_play_as_planned_and_a_find_someone_row_is_not(self):
        ready, find = self.company(), self.company()
        person = seed_email_person(self.conn, ready)
        seed_plan_row(self.conn, ready, TODAY, "proposed", person_id=person)
        seed_plan_row(self.conn, find, TODAY, "proposed")
        self.assertEqual(self.play(), {ready: "planned"})

    def test_picked_entry_beats_planned(self):
        both = self.company()
        person = seed_email_person(self.conn, both)
        seed_plan_row(self.conn, both, TODAY, "picked", person_id=person)
        picked_alone = self.company()
        seed_plan_row(self.conn, picked_alone, TODAY, "picked")
        self.assertEqual(self.play(), {both: "picked", picked_alone: "picked"})

    def test_picked_beats_planned_across_the_working_status(self):
        cid = self.company(status="working")
        person = seed_email_person(self.conn, cid)
        seed_plan_row(self.conn, cid, TODAY, "proposed", person_id=person)
        self.assertEqual(self.play(), {cid: "picked"})

    def test_backlog_and_removed_rows_are_not_in_play(self):
        a, b = self.company(), self.company()
        for cid, state in ((a, "backlog"), (b, "removed")):
            seed_plan_row(self.conn, cid, TODAY, state, person_id=seed_email_person(self.conn, cid))
        self.assertEqual(self.play(), {})

    def test_only_the_latest_list_counts(self):
        old_pick, new_pick = self.company(), self.company()
        seed_plan_row(self.conn, old_pick, "2026-11-09", "picked")
        seed_plan_row(self.conn, new_pick, TODAY, "picked")
        self.assertEqual(self.play(), {new_pick: "picked"})

    def test_a_list_older_than_planned_days_puts_nobody_in_play_by_plan(self):
        cid = self.company()
        seed_plan_row(self.conn, cid, "2026-11-05", "picked")
        self.assertEqual(self.play(), {})
        self.assertEqual(self.play(today="2026-11-09"), {cid: "picked"})
        working = self.company(status="working")
        self.assertEqual(self.play(), {working: "picked"})

    def test_merged_removed_closed_and_no_go_companies_are_never_in_play(self):
        keep = self.company()
        bad = {"removed": self.company(status="removed"), "closed": self.company(status="closed"),
               "no_go": self.company(), "lifted": self.company(), "merged": self.company()}
        seed_status(self.conn, bad["merged"], "removed")
        self.conn.execute("UPDATE company SET merged_into = ? WHERE company_id = ?", (keep, bad["merged"]))
        for name in ("no_go", "lifted"):
            self.conn.execute("INSERT INTO no_go(company_id, reason, source, created_at) "
                              "VALUES (?, 'other', 'devanshu', '2026-11-01T00:00:00Z')", (bad[name],))
        self.conn.execute("UPDATE no_go SET lifted_at = '2026-11-02T00:00:00Z', lift_reason = 'changed my mind' "
                          "WHERE company_id = ?", (bad["lifted"],))
        for cid in (keep, *bad.values()):
            seed_plan_row(self.conn, cid, TODAY, "picked")
        self.assertEqual(live_company_ids(self.conn), frozenset({keep, bad["lifted"]}))
        self.assertEqual(self.play(), {keep: "picked", bad["lifted"]: "picked"})
        person = seed_email_person(self.conn, bad["no_go"])
        seed_queued_message(self.conn, person, "drafted")
        self.assertNotIn(bad["no_go"], self.email())

    def test_backlog_company_is_live(self):
        cid = self.company(status="backlog")
        self.assertIn(cid, live_company_ids(self.conn))

    def test_emailing_needs_an_open_touch_with_a_live_message(self):
        sent_only, planned_follow, drafted, approved, failed, closed = (self.company() for _ in range(6))
        p = {c: seed_email_person(self.conn, c) for c in (sent_only, planned_follow, drafted, approved, failed, closed)}
        seed_queued_message(self.conn, p[sent_only], "sent")
        seed_queued_message(self.conn, p[planned_follow], "sent")
        seed_queued_message(self.conn, p[planned_follow], "planned", seq=1)
        seed_queued_message(self.conn, p[drafted], "drafted")
        seed_queued_message(self.conn, p[approved], "approved")
        seed_queued_message(self.conn, p[failed], "failed")
        seed_queued_message(self.conn, p[closed], "drafted", touch_state="closed")
        self.assertEqual(self.email(), frozenset({planned_follow, drafted, approved, failed}))

    def test_sending_and_send_unknown_count_as_emailing(self):
        a, b = self.company(), self.company()
        # One message sends at a time, so the one that passes through `sending` goes first.
        seed_queued_message(self.conn, seed_email_person(self.conn, b), "send_unknown")
        seed_queued_message(self.conn, seed_email_person(self.conn, a), "sending")
        self.assertEqual(self.email(), frozenset({a, b}))

    def test_email_row_on_the_latest_list_counts_as_emailing(self):
        mail, linkedin, none = self.company(), self.company(), self.company()
        seed_plan_row(self.conn, mail, TODAY, "proposed", person_id=seed_email_person(self.conn, mail))
        seed_plan_row(self.conn, linkedin, TODAY, "proposed", person_id=seed_person(self.conn, linkedin, "Li"),
                      channel="linkedin_request")
        seed_plan_row(self.conn, none, TODAY, "picked")
        self.assertEqual(self.email(), frozenset({mail}))
        self.assertEqual(self.email(today="2026-11-20"), frozenset())

    def test_a_removed_email_row_is_not_emailing(self):
        cid = self.company()
        seed_plan_row(self.conn, cid, TODAY, "removed", person_id=seed_email_person(self.conn, cid))
        self.assertEqual(self.email(), frozenset())

    def test_ny_date_uses_new_york_not_utc(self):
        self.assertEqual(ny_date("2026-11-10T03:30:00Z"), "2026-11-09")
        self.assertEqual(ny_date("2026-11-10T14:30:00Z"), "2026-11-10")
        self.assertEqual(ny_date("2026-07-10T03:30:00Z"), "2026-07-09")


if __name__ == "__main__":
    unittest.main()
