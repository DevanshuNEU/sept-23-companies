import unittest
from datetime import date

from outreach.drafts.followups import canonical_company, due_followups
from tests.sendtest import MON, bounce, seed_email_person, seed_message, seed_sent
from tests.storetest import fresh_store, seed_company

TODAY = date(2026, 11, 9)


class DueFollowupsTest(unittest.TestCase):
    def setUp(self):
        self.db = fresh_store()
        self.company = seed_company(self.db)
        self.n = 0

    def person(self, company=None):
        self.n += 1
        return seed_email_person(self.db, company or self.company, f"P{self.n}", f"p{self.n}@acme.co")

    def followup(self, due_on, *, company=None, sent=True):
        """A planned follow-up due on `due_on`; its first email was sent unless `sent` is False."""
        person = self.person(company)
        if sent:
            seed_sent(self.db, person, at=MON)
        else:
            seed_message(self.db, person, state="drafted")
        return seed_message(self.db, person, state="planned", seq=1, due_on=due_on)

    def touch_of(self, message_id):
        return self.db.execute("SELECT touch_id FROM message WHERE message_id = ?", (message_id,)).fetchone()[0]

    def merge(self, duplicate, survivor):
        self.db.execute("UPDATE company SET status = 'removed', remove_reason = 'merged', merged_into = ? "
                        "WHERE company_id = ?", (survivor, duplicate))

    def test_due_followups_lists_planned_rows_due_on_or_before_today(self):
        late = self.followup("2026-11-06")
        today = self.followup("2026-11-09")
        tomorrow = self.followup("2026-11-10")
        self.assertEqual(due_followups(self.db, TODAY), [late, today])
        self.assertEqual(due_followups(self.db, TODAY, days_ahead=1), [late, today, tomorrow])
        self.assertEqual(due_followups(self.db, TODAY, days_ahead=0), [late, today])

    def test_oldest_due_date_first(self):
        newer = self.followup("2026-11-09")
        older = self.followup("2026-11-03")
        self.assertEqual(due_followups(self.db, TODAY), [older, newer])

    def test_due_followups_skips_replied_closed_held_removed_and_never_sent_touches(self):
        keep = self.followup("2026-11-09")
        replied = self.followup("2026-11-09")
        self.db.execute("UPDATE touch SET state = 'replied', replied_at = '2026-11-05T15:00:00Z', "
                        "reply_kind = 'interested' WHERE touch_id = ?", (self.touch_of(replied),))
        bounced = self.followup("2026-11-09")
        bounce(self.db, self.touch_of(bounced), MON)
        closed_touch = self.followup("2026-11-09")
        self.db.execute("UPDATE touch SET state = 'closed', close_reason = 'no_reply', "
                        "closed_at = '2026-11-05T15:00:00Z' WHERE touch_id = ?", (self.touch_of(closed_touch),))
        never_sent = self.followup("2026-11-09", sent=False)
        held_company = seed_company(self.db, "Held")
        held = self.followup("2026-11-09", company=held_company)
        self.db.execute("UPDATE company SET held_by_touch = ?, held_at = '2026-11-05T15:00:00Z' "
                        "WHERE company_id = ?", (self.touch_of(held), held_company))
        closed_company = seed_company(self.db, "Closed")
        closed = self.followup("2026-11-09", company=closed_company)
        self.db.execute("UPDATE company SET status = 'closed', close_reason = 'other' WHERE company_id = ?",
                        (closed_company,))
        removed_company = seed_company(self.db, "Removed")
        removed = self.followup("2026-11-09", company=removed_company)
        self.db.execute("UPDATE company SET status = 'removed', remove_reason = 'gone' WHERE company_id = ?",
                        (removed_company,))
        for skipped in (replied, bounced, closed_touch, never_sent, held, closed, removed):
            self.assertNotIn(skipped, due_followups(self.db, TODAY))
        self.assertEqual(due_followups(self.db, TODAY), [keep])

    def test_a_hold_on_the_survivor_reaches_a_person_at_the_merged_duplicate(self):
        survivor = seed_company(self.db, "Survivor")
        duplicate = seed_company(self.db, "Duplicate")
        self.merge(duplicate, survivor)
        message = self.followup("2026-11-09", company=duplicate)
        self.assertEqual(due_followups(self.db, TODAY), [message])
        self.db.execute("UPDATE company SET held_by_touch = ?, held_at = '2026-11-05T15:00:00Z' "
                        "WHERE company_id = ?", (self.touch_of(message), survivor))
        self.assertEqual(due_followups(self.db, TODAY), [])

    def test_a_merged_duplicate_is_not_a_removed_company(self):
        survivor = seed_company(self.db, "Survivor")
        duplicate = seed_company(self.db, "Duplicate")
        self.merge(duplicate, survivor)
        message = self.followup("2026-11-09", company=duplicate)
        self.assertEqual(due_followups(self.db, TODAY), [message])
        self.db.execute("UPDATE company SET status = 'closed', close_reason = 'other' WHERE company_id = ?",
                        (survivor,))
        self.assertEqual(due_followups(self.db, TODAY), [])

    def test_only_planned_rows_are_listed(self):
        person = self.person()
        seed_sent(self.db, person, at=MON)
        seed_message(self.db, person, state="drafted", seq=1, due_on="2026-11-09")
        self.assertEqual(due_followups(self.db, TODAY), [])

    def test_due_followups_writes_nothing(self):
        self.followup("2026-11-09")
        before = self.db.total_changes
        due_followups(self.db, TODAY)
        self.assertEqual(self.db.total_changes, before)
        self.assertFalse(self.db.in_transaction)

    def test_canonical_company_follows_merges_and_refuses_a_cycle(self):
        a, b, c = (seed_company(self.db, n) for n in "ABC")
        self.assertEqual(canonical_company(self.db, a), a)
        self.merge(a, b)
        self.merge(b, c)
        self.assertEqual(canonical_company(self.db, a), c)
        self.db.execute("PRAGMA foreign_keys = OFF")
        self.db.execute("UPDATE company SET merged_into = ?, status = 'removed', remove_reason = 'merged' "
                        "WHERE company_id = ?", (a, c))
        with self.assertRaises(RuntimeError):
            canonical_company(self.db, a)


if __name__ == "__main__":
    unittest.main()
