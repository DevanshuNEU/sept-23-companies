import unittest

from outreach.api.config import load_config
from outreach.outcomes import prompt
from outreach.outcomes.context import build_context
from outreach.store import log_touch, record_outcome, transaction
from tests.storetest import fresh_store, seed_company

CFG = load_config()
SENT = "2026-10-01T15:00:00Z"   # a Thursday, 11:00 in New York


def touch(**over):
    row = {"sent_at": SENT, "accepted_at": None, "replied_at": None, "referred_at": None, "interview_at": None,
           "offer_at": None, "quiet_at": None, "undone_at": None}
    row.update(over)
    return row


def on_day(n: int, hour: str = "15:00:00") -> str:
    """The moment `n` days after the send, as a canonical UTC time (October, so no DST change in range)."""
    return f"2026-10-{1 + n:02d}T{hour}Z"


class PromptRuleTest(unittest.TestCase):
    def test_config_numbers_are_the_ruling(self):
        self.assertEqual((CFG.prompt_after_days, CFG.reask_after_days, CFG.quiet_after_days), (7, 7, 21))

    def test_asks_at_day_7_and_not_before(self):
        t = touch()
        self.assertFalse(prompt.due_for_prompt(t, [], on_day(0), CFG))
        self.assertFalse(prompt.due_for_prompt(t, [], on_day(6, "23:00:00"), CFG))
        self.assertTrue(prompt.due_for_prompt(t, [], on_day(7), CFG))
        self.assertTrue(prompt.due_for_prompt(t, [], on_day(10), CFG))

    def test_days_are_new_york_days(self):
        # Sent 2026-10-01 23:30 in New York (03:30Z the next day in UTC). Day 7 is 2026-10-08 in New York.
        t = touch(sent_at="2026-10-02T03:30:00Z")
        self.assertFalse(prompt.due_for_prompt(t, [], "2026-10-08T03:00:00Z", CFG))   # 2026-10-07 23:00 in New York
        self.assertTrue(prompt.due_for_prompt(t, [], "2026-10-08T04:00:00Z", CFG))    # 2026-10-08 00:00 in New York

    def test_one_snooze_asks_again_after_7_days(self):
        t = touch()
        snooze = on_day(7)
        self.assertFalse(prompt.due_for_prompt(t, [snooze], on_day(7, "18:00:00"), CFG))
        self.assertFalse(prompt.due_for_prompt(t, [snooze], on_day(13), CFG))
        self.assertTrue(prompt.due_for_prompt(t, [snooze], on_day(14), CFG))

    def test_two_snoozes_never_ask_again(self):
        t = touch()
        snoozes = [on_day(7), on_day(14)]
        for day in (14, 15, 20, 21, 30):
            self.assertFalse(prompt.due_for_prompt(t, snoozes, on_day(day), CFG), day)

    def test_quiet_at_day_21_even_without_an_answer(self):
        t = touch()
        self.assertFalse(prompt.should_mark_quiet(t, on_day(20, "23:00:00"), CFG))
        self.assertTrue(prompt.should_mark_quiet(t, "2026-10-22T04:00:00Z", CFG))
        self.assertTrue(prompt.should_mark_quiet(t, "2026-11-30T15:00:00Z", CFG))

    def test_snoozes_do_not_postpone_quiet(self):
        # should_mark_quiet takes no snoozes: day 21 is day 21 however many times he said "Nothing yet".
        self.assertTrue(prompt.should_mark_quiet(touch(), "2026-10-22T15:00:00Z", CFG))

    def test_an_outcome_stops_the_prompt(self):
        late = "2026-11-20T15:00:00Z"
        for column in ("accepted_at", "replied_at", "referred_at", "interview_at", "offer_at", "quiet_at"):
            t = touch(**{column: on_day(3)})
            self.assertFalse(prompt.due_for_prompt(t, [], late, CFG), column)
            self.assertFalse(prompt.should_mark_quiet(t, late, CFG), column)

    def test_an_undone_touch_is_never_asked_about(self):
        t = touch(undone_at=on_day(1))
        self.assertFalse(prompt.due_for_prompt(t, [], "2026-11-20T15:00:00Z", CFG))
        self.assertFalse(prompt.should_mark_quiet(t, "2026-11-20T15:00:00Z", CFG))

    def test_a_snooze_may_use_any_offset_spelling(self):
        t = touch()
        self.assertTrue(prompt.due_for_prompt(t, ["2026-10-08T11:00:00-04:00"], "2026-10-15T15:00:00Z", CFG))

    def test_sqlite_rows_work_as_touches(self):
        conn = fresh_store()
        company = seed_company(conn)
        touch_id = log_touch(conn, person_id=None, company_id=company, channel="application", sent_at=SENT,
                             context=_context(), idempotency_key="k-row")
        row = conn.execute("SELECT * FROM outreach_touch WHERE touch_id = ?", (touch_id,)).fetchone()
        self.assertFalse(prompt.due_for_prompt(row, [], on_day(6), CFG))
        self.assertTrue(prompt.due_for_prompt(row, [], on_day(7), CFG))


def _context():
    return build_context(source="own", lane="role_at_level", role_kind=None, role_age_days=None,
                         ranker_version=None, signals={}, role_named=False)


class LateReplyTest(unittest.TestCase):
    def test_a_late_reply_after_quiet_clears_quiet(self):
        # Ruling 3, through the foundation's record_outcome (it fails loudly if #30 lacks it).
        conn = fresh_store()
        company = seed_company(conn)
        touch_id = log_touch(conn, person_id=None, company_id=company, channel="application", sent_at=SENT,
                             context=_context(), idempotency_key="k-late")
        with transaction(conn):
            revision = record_outcome(conn, touch_id, kind="quiet", at="2026-10-22T09:00:00Z", revision=1)
        row = conn.execute("SELECT quiet_at, replied_at FROM outreach_touch WHERE touch_id = ?",
                           (touch_id,)).fetchone()
        self.assertEqual((row["quiet_at"], row["replied_at"]), ("2026-10-22T09:00:00Z", None))
        # The prompt rule treats a quiet touch as settled.
        full = conn.execute("SELECT * FROM outreach_touch WHERE touch_id = ?", (touch_id,)).fetchone()
        self.assertFalse(prompt.due_for_prompt(full, [], "2026-11-30T15:00:00Z", CFG))
        with transaction(conn):
            record_outcome(conn, touch_id, kind="replied", at="2026-10-30T09:00:00Z", reply_kind="interested",
                           revision=revision)
        row = conn.execute("SELECT quiet_at, replied_at, reply_kind FROM outreach_touch WHERE touch_id = ?",
                           (touch_id,)).fetchone()
        self.assertEqual((row["quiet_at"], row["replied_at"], row["reply_kind"]),
                         (None, "2026-10-30T09:00:00Z", "interested"))


if __name__ == "__main__":
    unittest.main()
