import json
import subprocess
import sys
import unittest
from datetime import date
from pathlib import Path

from outreach.drafts.followups import RELEASE_KIND, canonical_company, company_map, due_followups, held_survivors
from outreach.store.writes import append_event, record_outcome
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
        # A reply holds every follow-up at its company (#9), so the replier works somewhere else.
        replied = self.followup("2026-11-09", company=seed_company(self.db, "Replied"))
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

    def test_a_hold_on_a_merged_away_duplicate_holds_the_survivor_and_the_duplicate(self):
        survivor = seed_company(self.db, "Survivor")
        duplicate = seed_company(self.db, "Duplicate")
        held = self.followup("2026-11-09", company=duplicate)
        self.db.execute("UPDATE company SET held_by_touch = ?, held_at = '2026-11-05T15:00:00Z' "
                        "WHERE company_id = ?", (self.touch_of(held), duplicate))
        self.merge(duplicate, survivor)
        at_survivor = self.followup("2026-11-09", company=survivor)
        at_duplicate = self.followup("2026-11-09", company=duplicate)
        self.assertEqual(due_followups(self.db, TODAY), [])
        self.assertNotIn(at_survivor, due_followups(self.db, TODAY))
        self.assertNotIn(at_duplicate, due_followups(self.db, TODAY))

    def no_go(self, *, person_id=None, company_id=None, lifted=False):
        lift = ", lifted_at, lift_reason" if lifted else ""
        marks = ", '2026-11-02T01:00:00Z', 'wrong'" if lifted else ""
        self.db.execute(f"INSERT INTO no_go(person_id, company_id, reason, source, created_at{lift}) "
                        f"VALUES (?, ?, 'said_no', 'reply', '2026-11-01T12:00:00Z'{marks})", (person_id, company_id))

    def person_of(self, message_id):
        return self.db.execute("SELECT t.person_id FROM message m JOIN touch t USING (touch_id) "
                               "WHERE m.message_id = ?", (message_id,)).fetchone()[0]

    def test_an_active_no_go_on_the_person_or_the_company_skips_the_follow_up(self):
        keep = self.followup("2026-11-09")
        on_person = self.followup("2026-11-09")
        self.no_go(person_id=self.person_of(on_person))
        no_go_company = seed_company(self.db, "NoGo")
        on_company = self.followup("2026-11-09", company=no_go_company)
        self.no_go(company_id=no_go_company)
        lifted_company = seed_company(self.db, "Lifted")
        lifted = self.followup("2026-11-09", company=lifted_company)
        self.no_go(company_id=lifted_company, lifted=True)
        self.assertEqual(due_followups(self.db, TODAY), [keep, lifted])
        self.assertNotIn(on_person, due_followups(self.db, TODAY))
        self.assertNotIn(on_company, due_followups(self.db, TODAY))

    def test_a_no_go_on_a_merged_away_duplicate_skips_the_survivors_people(self):
        survivor = seed_company(self.db, "Survivor")
        duplicate = seed_company(self.db, "Duplicate")
        self.no_go(company_id=duplicate)
        self.merge(duplicate, survivor)
        self.followup("2026-11-09", company=survivor)
        self.followup("2026-11-09", company=duplicate)
        self.assertEqual(due_followups(self.db, TODAY), [])

    def test_a_touch_with_no_thread_has_no_follow_up(self):
        message = self.followup("2026-11-09")
        self.db.execute("UPDATE touch SET origin = 'import', gmail_thread_id = NULL WHERE touch_id = ?",
                        (self.touch_of(message),))
        self.assertEqual(due_followups(self.db, TODAY), [])

    def test_it_imports_without_the_sending_package(self):
        code = ("import sys; import outreach.drafts.followups; "
                "assert not [m for m in sys.modules if m.startswith('outreach.sending')], 'imported outreach.sending'")
        root = Path(__file__).resolve().parent.parent
        done = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)

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


CONTEXT = json.dumps({"source": "own", "lane": "own", "roleKind": "engineer", "roleAgeDays": None,
                      "rankerVersion": None, "signals": {}, "roleNamed": False})


class ReplyHoldTest(unittest.TestCase):
    """#9: any reply from anyone at the company, or at a merged duplicate, on any channel, holds its queued email."""

    def setUp(self):
        self.db = fresh_store()
        self.company = seed_company(self.db)
        self.n = 0

    def person(self, company=None):
        self.n += 1
        return seed_email_person(self.db, company or self.company, f"P{self.n}", f"p{self.n}@acme.co")

    def reach(self, *, company=None, channel="linkedin_request", replied_at=None, reply_kind="interested"):
        """An outreach touch at the company, replied at `replied_at` when given. Returns its id."""
        person = None if channel == "application" else self.person(company)
        return self.db.execute(
            "INSERT INTO outreach_touch(person_id, company_id, channel, sent_at, replied_at, reply_kind, context, "
            "created_at) VALUES (?, ?, ?, '2026-11-02T15:00:00Z', ?, ?, ?, '2026-11-02T15:00:00Z')",
            (person, company or self.company, channel, replied_at, reply_kind if replied_at else None,
             CONTEXT)).lastrowid

    def held(self):
        return held_survivors(company_map(self.db))

    def release(self, company, at):
        append_event(self.db, entity="company", entity_id=company, company_id=company, kind=RELEASE_KIND,
                     actor="devanshu", at=at)

    def test_a_reply_on_any_channel_holds_the_company(self):
        for channel in ("linkedin_request", "linkedin_message", "email", "application", "hn_reply", "other"):
            with self.subTest(channel=channel):
                company = seed_company(self.db, f"Co {channel}")
                self.reach(company=company, channel=channel)
                self.assertNotIn(company, self.held())
                self.reach(company=company, channel=channel, replied_at="2026-11-05T15:00:00Z")
                self.assertIn(company, self.held())

    def test_a_reply_holds_a_colleagues_due_follow_up(self):
        colleague = self.person()
        seed_sent(self.db, colleague, at=MON)
        followup = seed_message(self.db, colleague, state="planned", seq=1, due_on="2026-11-09")
        self.assertEqual(due_followups(self.db, TODAY), [followup])
        self.reach(replied_at="2026-11-05T15:00:00Z", reply_kind="not_now")
        self.assertEqual(due_followups(self.db, TODAY), [])

    def test_a_reply_at_a_merged_duplicate_holds_the_survivor(self):
        survivor = seed_company(self.db, "Survivor")
        duplicate = seed_company(self.db, "Duplicate")
        self.reach(company=duplicate, replied_at="2026-11-05T15:00:00Z")
        self.db.execute("UPDATE company SET status = 'removed', remove_reason = 'merged', merged_into = ? "
                        "WHERE company_id = ?", (survivor, duplicate))
        self.assertIn(survivor, self.held())

    def test_a_cleared_reply_does_not_hold(self):
        touch = self.reach(replied_at="2026-11-05T15:00:00Z")
        self.db.execute("UPDATE outreach_touch SET replied_at = NULL, reply_kind = NULL WHERE touch_id = ?", (touch,))
        self.assertNotIn(self.company, self.held())

    def record_reply(self, touch_id, at):
        """A reply recorded as the API and the sender record one (with its `replied` event)."""
        record_outcome(self.db, touch_id, kind="replied", at=at, reply_kind="interested", revision=1)

    def test_a_release_ends_the_reply_hold_and_a_later_reply_holds_again(self):
        survivor = seed_company(self.db, "Survivor")
        duplicate = seed_company(self.db, "Duplicate")
        self.record_reply(self.reach(company=duplicate), "2026-11-05T15:00:00Z")
        self.db.execute("UPDATE company SET status = 'removed', remove_reason = 'merged', merged_into = ? "
                        "WHERE company_id = ?", (survivor, duplicate))
        for company in (survivor, duplicate):  # the release action releases every company in the family
            self.release(company, "2026-11-06T15:00:00Z")
        self.assertNotIn(survivor, self.held())
        self.record_reply(self.reach(company=survivor), "2026-11-07T15:00:00Z")
        self.assertIn(survivor, self.held())

    def test_a_merge_cannot_drop_a_hold(self):
        survivor = seed_company(self.db, "Survivor")
        duplicate = seed_company(self.db, "Duplicate")
        self.record_reply(self.reach(company=survivor), "2026-11-04T15:00:00Z")
        self.record_reply(self.reach(company=duplicate), "2026-11-05T15:00:00Z")
        self.release(survivor, "2026-11-06T15:00:00Z")  # the survivor's own hold was released before the merge
        self.assertNotIn(survivor, self.held())
        self.db.execute("UPDATE company SET status = 'removed', remove_reason = 'merged', merged_into = ? "
                        "WHERE company_id = ?", (survivor, duplicate))
        self.assertIn(survivor, self.held())  # the duplicate's reply was never released

    def test_a_back_dated_reply_recorded_after_a_release_still_holds(self):
        self.record_reply(self.reach(), "2026-11-04T15:00:00Z")
        self.release(self.company, "2026-11-06T15:00:00Z")
        self.assertNotIn(self.company, self.held())
        # Logged on the 7th for a LinkedIn reply that came on the 5th, before the release: it is news, so it holds.
        self.record_reply(self.reach(), "2026-11-05T15:00:00Z")
        self.assertIn(self.company, self.held())

    def test_a_release_does_not_clear_a_hold_by_touch(self):
        person = self.person()
        seed_sent(self.db, person, at=MON)
        touch = self.db.execute("SELECT touch_id FROM touch WHERE person_id = ?", (person,)).fetchone()[0]
        self.db.execute("UPDATE company SET held_by_touch = ?, held_at = '2026-11-05T15:00:00Z' WHERE company_id = ?",
                        (touch, self.company))
        self.release(self.company, "2026-11-06T15:00:00Z")
        self.assertIn(self.company, self.held())  # the release action clears held_by_touch itself


if __name__ == "__main__":
    unittest.main()
