import json
import sqlite3
import unittest

from outreach.outcomes import correct, wins
from outreach.outcomes.context import build_context
from outreach.store import log_touch, record_outcome, transaction, writes
from tests.storetest import fresh_store, seed_company

SENT = "2026-10-01T15:00:00Z"
KEYS = {"source", "lane", "roleKind", "roleAgeDays", "rankerVersion", "signals", "roleNamed"}


def context(**over):
    args = dict(source="own", lane="role_at_level", role_kind="swe", role_age_days=4, ranker_version="v1",
                signals={"live_role": 1.0}, role_named=True)
    args.update(over)
    return build_context(**args)


def new_touch(conn, key="k1", channel="application", person_id=None):
    company = seed_company(conn, f"Acme {key}")
    return log_touch(conn, person_id=person_id, company_id=company, channel=channel, sent_at=SENT,
                     context=context(), idempotency_key=key)


def row(conn, touch_id):
    return conn.execute("SELECT * FROM outreach_touch WHERE touch_id = ?", (touch_id,)).fetchone()


def events(conn, touch_id):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM event WHERE entity = 'outreach_touch' AND entity_id = ? ORDER BY event_id", (touch_id,))]


class CorrectOutcomeTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.touch = new_touch(self.conn)

    def fix(self, kind, at, *, reply_kind=None, revision):
        with transaction(self.conn):
            return correct.correct_outcome(self.conn, self.touch, kind, at=at, reply_kind=reply_kind,
                                           revision=revision)

    def test_clear_and_set_keep_the_check_constraints(self):
        with transaction(self.conn):
            rev = record_outcome(self.conn, self.touch, kind="accepted", at="2026-10-03T10:00:00Z", revision=1)
        # set a different date
        rev = self.fix("accepted", "2026-10-04T10:00:00Z", revision=rev)
        self.assertEqual(row(self.conn, self.touch)["accepted_at"], "2026-10-04T10:00:00Z")
        # set one that was empty
        rev = self.fix("referred", "2026-10-06T10:00:00-04:00", revision=rev)
        self.assertEqual(row(self.conn, self.touch)["referred_at"], "2026-10-06T14:00:00Z")
        # clear
        rev = self.fix("accepted", None, revision=rev)
        self.assertIsNone(row(self.conn, self.touch)["accepted_at"])
        # setting quiet and then a reply clears quiet
        rev = self.fix("quiet", "2026-10-22T09:00:00Z", revision=rev)
        self.assertEqual(row(self.conn, self.touch)["quiet_at"], "2026-10-22T09:00:00Z")
        rev = self.fix("replied", "2026-10-30T09:00:00Z", reply_kind="not_now", revision=rev)
        r = row(self.conn, self.touch)
        self.assertEqual((r["quiet_at"], r["replied_at"], r["reply_kind"]),
                         (None, "2026-10-30T09:00:00Z", "not_now"))
        self.assertEqual(r["revision"], rev)

    def test_every_correction_bumps_the_revision_by_one_and_returns_it(self):
        self.assertEqual(self.fix("accepted", "2026-10-03T10:00:00Z", revision=1), 2)
        self.assertEqual(self.fix("accepted", None, revision=2), 3)
        self.assertEqual(row(self.conn, self.touch)["revision"], 3)

    def test_a_correction_appends_a_corrected_event_with_old_and_new(self):
        with transaction(self.conn):
            rev = record_outcome(self.conn, self.touch, kind="accepted", at="2026-10-03T10:00:00Z", revision=1)
        self.fix("accepted", "2026-10-04T10:00:00Z", revision=rev)
        corrected = [e for e in events(self.conn, self.touch) if e["kind"] == "corrected"]
        self.assertEqual(len(corrected), 1)
        self.assertEqual(corrected[0]["actor"], "devanshu")
        detail = json.loads(corrected[0]["detail"])
        self.assertEqual(detail["outcome"], "accepted")
        self.assertEqual((detail["old"], detail["new"]), ("2026-10-03T10:00:00Z", "2026-10-04T10:00:00Z"))

    def test_clearing_records_a_null_new_value_and_the_actor_is_passed_on(self):
        with transaction(self.conn):
            rev = record_outcome(self.conn, self.touch, kind="offer", at="2026-10-09T10:00:00Z", revision=1)
        with transaction(self.conn):
            correct.correct_outcome(self.conn, self.touch, "offer", at=None, reply_kind=None, revision=rev,
                                    actor="claude")
        event = [e for e in events(self.conn, self.touch) if e["kind"] == "corrected"][-1]
        self.assertEqual(event["actor"], "claude")
        detail = json.loads(event["detail"])
        self.assertEqual((detail["old"], detail["new"]), ("2026-10-09T10:00:00Z", None))

    def test_correct_outcome_stale_revision_changes_nothing(self):
        with transaction(self.conn):
            rev = record_outcome(self.conn, self.touch, kind="accepted", at="2026-10-03T10:00:00Z", revision=1)
        before = (dict(row(self.conn, self.touch)), events(self.conn, self.touch))
        with self.assertRaises(LookupError):
            self.fix("accepted", "2026-10-04T10:00:00Z", revision=rev - 1)
        with self.assertRaises(LookupError):
            self.fix("accepted", None, revision=rev + 5)
        self.assertEqual((dict(row(self.conn, self.touch)), events(self.conn, self.touch)), before)

    def test_an_unknown_touch_is_a_lookup_error(self):
        with self.assertRaises(LookupError):
            with transaction(self.conn):
                correct.correct_outcome(self.conn, 99999, "accepted", at=None, reply_kind=None, revision=1)

    def test_correcting_replied_to_none_clears_the_reply_kind(self):
        with transaction(self.conn):
            rev = record_outcome(self.conn, self.touch, kind="replied", at="2026-10-03T10:00:00Z",
                                 reply_kind="interested", revision=1)
        self.fix("replied", None, revision=rev)
        r = row(self.conn, self.touch)
        self.assertEqual((r["replied_at"], r["reply_kind"]), (None, None))
        detail = json.loads([e for e in events(self.conn, self.touch) if e["kind"] == "corrected"][0]["detail"])
        self.assertEqual((detail["oldReplyKind"], detail["newReplyKind"]), ("interested", None))

    def test_correcting_the_reply_date_keeps_the_reply_kind_unless_a_new_one_is_given(self):
        with transaction(self.conn):
            rev = record_outcome(self.conn, self.touch, kind="replied", at="2026-10-03T10:00:00Z",
                                 reply_kind="interested", revision=1)
        rev = self.fix("replied", "2026-10-04T10:00:00Z", revision=rev)
        self.assertEqual(row(self.conn, self.touch)["reply_kind"], "interested")
        self.fix("replied", "2026-10-04T10:00:00Z", reply_kind="said_no", revision=rev)
        self.assertEqual(row(self.conn, self.touch)["reply_kind"], "said_no")

    def test_a_reply_set_by_correction_without_a_kind_is_unsorted(self):
        self.fix("replied", "2026-10-04T10:00:00Z", revision=1)
        self.assertEqual(row(self.conn, self.touch)["reply_kind"], "unsorted")

    def test_refusals_say_why_and_change_nothing(self):
        before = dict(row(self.conn, self.touch))
        cases = [
            ("accepted", "2026-09-30T10:00:00Z", None, "before"),                 # before it was sent
            ("replied", "2026-10-04T10:00:00Z", "auto_reply", "reply"),           # not a stored reply kind
            ("accepted", "2026-10-04T10:00:00Z", "interested", "reply"),          # reply kind on another outcome
            ("replied", None, "interested", "reply"),                             # a kind with no reply
            ("nonsense", "2026-10-04T10:00:00Z", None, "outcome"),
        ]
        for kind, at, reply_kind, word in cases:
            with self.subTest(kind=kind, at=at, reply_kind=reply_kind):
                with self.assertRaises(ValueError) as caught:
                    self.fix(kind, at, reply_kind=reply_kind, revision=1)
                self.assertIn(word, str(caught.exception))
        self.assertEqual(dict(row(self.conn, self.touch)), before)
        self.assertEqual([e["kind"] for e in events(self.conn, self.touch)], ["sent"])

    def test_quiet_on_a_touch_that_replied_is_refused(self):
        with transaction(self.conn):
            rev = record_outcome(self.conn, self.touch, kind="replied", at="2026-10-03T10:00:00Z", revision=1)
        with self.assertRaises(ValueError) as caught:
            self.fix("quiet", "2026-10-22T10:00:00Z", revision=rev)
        self.assertIn("reply", str(caught.exception))

    def test_an_undone_touch_cannot_be_corrected(self):
        from outreach.store import undo_touch
        with transaction(self.conn):
            rev = undo_touch(self.conn, self.touch, at="2026-10-02T10:00:00Z", revision=1)
        with self.assertRaises(ValueError) as caught:
            self.fix("accepted", "2026-10-04T10:00:00Z", revision=rev)
        self.assertIn("undone", str(caught.exception))

    def test_a_failed_correction_inside_a_transaction_leaves_no_event(self):
        # A bad date raises before any write; the transaction rolls back and the touch is as it was.
        with self.assertRaises(ValueError):
            self.fix("accepted", "2026-09-01T00:00:00Z", revision=1)
        self.assertEqual(row(self.conn, self.touch)["revision"], 1)


class AtomicAndClockTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()      # autocommit: no transaction is open unless the code opens one
        self.touch = new_touch(self.conn)

    def test_a_failing_event_write_leaves_the_touch_unchanged_on_an_autocommit_connection(self):
        self.assertFalse(self.conn.in_transaction)
        before = (dict(row(self.conn, self.touch)), events(self.conn, self.touch))
        with self.assertRaises(sqlite3.IntegrityError):
            correct.correct_outcome(self.conn, self.touch, "accepted", at="2026-10-04T10:00:00Z",
                                    reply_kind=None, revision=1, actor="bogus")
        self.assertEqual((dict(row(self.conn, self.touch)), events(self.conn, self.touch)), before)
        self.assertEqual(row(self.conn, self.touch)["revision"], 1)
        self.assertFalse(self.conn.in_transaction)
        # and the same call with a real actor still works afterwards
        self.assertEqual(correct.correct_outcome(self.conn, self.touch, "accepted", at="2026-10-04T10:00:00Z",
                                                 reply_kind=None, revision=1), 2)

    def test_inside_a_callers_transaction_a_failure_undoes_only_the_correction(self):
        with transaction(self.conn):
            rev = record_outcome(self.conn, self.touch, kind="accepted", at="2026-10-03T10:00:00Z", revision=1)
            with self.assertRaises(sqlite3.IntegrityError):
                correct.correct_outcome(self.conn, self.touch, "accepted", at="2026-10-04T10:00:00Z",
                                        reply_kind=None, revision=rev, actor="bogus")
        r = row(self.conn, self.touch)
        self.assertEqual((r["accepted_at"], r["revision"]), ("2026-10-03T10:00:00Z", rev))

    def test_the_event_takes_the_time_it_is_given(self):
        correct.correct_outcome(self.conn, self.touch, "accepted", at="2026-10-04T10:00:00Z", reply_kind=None,
                                revision=1, now="2026-10-05T08:30:00Z")
        event = [e for e in events(self.conn, self.touch) if e["kind"] == "corrected"][0]
        self.assertEqual(event["at"], "2026-10-05T08:30:00Z")

    def test_a_correction_that_changes_nothing_bumps_nothing_and_writes_no_event(self):
        # clearing an outcome that is already empty
        self.assertEqual(correct.correct_outcome(self.conn, self.touch, "accepted", at=None, reply_kind=None,
                                                 revision=1), 1)
        # setting the value it already has (same moment in another spelling)
        rev = correct.correct_outcome(self.conn, self.touch, "referred", at="2026-10-06T10:00:00Z",
                                      reply_kind=None, revision=1)
        self.assertEqual(rev, 2)
        self.assertEqual(correct.correct_outcome(self.conn, self.touch, "referred", at="2026-10-06T06:00:00-04:00",
                                                 reply_kind=None, revision=2), 2)
        # a reply with the same date and the same kind
        rev = correct.correct_outcome(self.conn, self.touch, "replied", at="2026-10-07T10:00:00Z",
                                      reply_kind="interested", revision=2)
        self.assertEqual(correct.correct_outcome(self.conn, self.touch, "replied", at="2026-10-07T10:00:00Z",
                                                 reply_kind="interested", revision=rev), rev)
        self.assertEqual(correct.correct_outcome(self.conn, self.touch, "replied", at="2026-10-07T10:00:00Z",
                                                 reply_kind=None, revision=rev), rev)
        self.assertEqual(row(self.conn, self.touch)["revision"], rev)
        self.assertEqual([e["kind"] for e in events(self.conn, self.touch)], ["sent", "corrected", "corrected"])

    def test_a_no_op_with_a_stale_revision_is_still_a_conflict(self):
        with self.assertRaises(LookupError):
            correct.correct_outcome(self.conn, self.touch, "accepted", at=None, reply_kind=None, revision=7)

    def test_only_the_reply_kind_changing_is_a_change(self):
        rev = correct.correct_outcome(self.conn, self.touch, "replied", at="2026-10-07T10:00:00Z",
                                      reply_kind="interested", revision=1)
        self.assertEqual(correct.correct_outcome(self.conn, self.touch, "replied", at="2026-10-07T10:00:00Z",
                                                 reply_kind="said_no", revision=rev), rev + 1)

    def test_the_copied_constants_match_the_foundation(self):
        # correct.py and wins.py keep their own copies so they need no private names; drift must fail loudly.
        self.assertEqual(wins.REPLY_KINDS, writes._REPLY_KINDS)
        self.assertEqual(correct.OUTCOME_COLUMNS, writes._OUTCOME_COLUMNS)


class ContextTest(unittest.TestCase):
    def test_context_has_the_pinned_keys_including_source(self):
        c = context()
        self.assertEqual(set(c), KEYS)
        self.assertEqual(c, {"source": "own", "lane": "role_at_level", "roleKind": "swe", "roleAgeDays": 4,
                             "rankerVersion": "v1", "signals": {"live_role": 1.0}, "roleNamed": True})
        # the schema CHECK accepts exactly this shape
        conn = fresh_store()
        touch_id = new_touch(conn, "k-ctx")
        self.assertEqual(set(json.loads(row(conn, touch_id)["context"])), KEYS)

    def test_a_context_with_nothing_known_still_satisfies_the_schema(self):
        c = context(source="plan", role_kind=None, role_age_days=None, ranker_version=None, signals={},
                    role_named=False)
        self.assertEqual((c["roleKind"], c["roleAgeDays"], c["rankerVersion"], c["signals"], c["roleNamed"]),
                         (None, None, None, {}, False))
        conn = fresh_store()
        company = seed_company(conn)
        log_touch(conn, person_id=None, company_id=company, channel="application", sent_at=SENT, context=c,
                  idempotency_key="k-null")

    def test_context_source_must_be_plan_or_own(self):
        for bad in ("", "import", "Plan", None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                context(source=bad)
        self.assertEqual(context(source="plan")["source"], "plan")

    def test_context_refuses_what_the_schema_would_refuse(self):
        for bad in ({"lane": ""}, {"lane": None}, {"role_named": None}, {"role_named": 1},
                    {"signals": None}, {"signals": {"a": "high"}}, {"signals": {"a": float("nan")}},
                    {"signals": {"a": True}}, {"role_age_days": -1}, {"role_age_days": 2.5}):
            with self.assertRaises(ValueError, msg=repr(bad)):
                context(**bad)

    def test_signals_are_copied_not_shared(self):
        signals = {"live_role": 1}
        c = context(signals=signals)
        signals["live_role"] = 0
        self.assertEqual(c["signals"], {"live_role": 1.0})
        self.assertIsInstance(c["signals"]["live_role"], float)


if __name__ == "__main__":
    unittest.main()
