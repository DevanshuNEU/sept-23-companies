import json
import sqlite3
import unittest

from outreach.store import append_event, finish_job, name_key, start_job
from outreach.store.writes import (add_alias, body_sha256, is_shared_key, log_touch, mark_shared_key, set_reply_kind,
                                   record_outcome, set_reply_kind, undo_touch)
from tests.storetest import fresh_store, seed_company, seed_touch


class NameKeyTests(unittest.TestCase):
    def test_name_key(self):
        self.assertEqual(name_key("Retell AI, Inc."), "retellai")
        self.assertEqual(name_key("Acme Corp"), "acme")
        self.assertEqual(name_key("Corpay"), "corpay")

    def test_suffix_is_dropped_only_as_a_whole_trailing_word(self):
        self.assertEqual(name_key("Coinbase"), "coinbase")
        self.assertEqual(name_key("Inc Studio"), "incstudio")
        self.assertEqual(name_key("Foo Co., Ltd."), "foo")
        self.assertEqual(name_key("Siemens GmbH"), "siemens")
        self.assertEqual(name_key("Acme LLC"), "acme")

    def test_a_name_that_is_only_a_suffix_is_kept(self):
        self.assertEqual(name_key("Inc."), "inc")

    def test_empty_and_none(self):
        self.assertEqual(name_key(""), "")
        self.assertEqual(name_key(None), "")


class EventTests(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)

    def test_event_is_written_with_canonical_times(self):
        company_id = seed_company(self.conn)
        event_id = append_event(
            self.conn, entity="company", kind="created", actor="devanshu",
            entity_id=company_id, company_id=company_id, detail={"why": "test"},
            at="2026-10-19T08:00:00-04:00",
        )
        row = self.conn.execute("SELECT * FROM event WHERE event_id = ?", (event_id,)).fetchone()
        self.assertEqual(row["at"], "2026-10-19T12:00:00Z")
        self.assertRegex(row["recorded_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(json.loads(row["detail"]), {"why": "test"})
        self.assertEqual((row["entity"], row["kind"], row["actor"]), ("company", "created", "devanshu"))
        self.assertEqual((row["entity_id"], row["company_id"]), (company_id, company_id))
        self.assertIsNone(row["idempotency_key"])

    def test_defaults_are_now_and_empty_detail(self):
        event_id = append_event(self.conn, entity="sender", kind="tick", actor="system")
        row = self.conn.execute("SELECT * FROM event WHERE event_id = ?", (event_id,)).fetchone()
        self.assertEqual(row["at"], row["recorded_at"])
        self.assertRegex(row["at"], r"Z$")
        self.assertEqual(row["detail"], "{}")
        self.assertIsNone(row["entity_id"])
        self.assertIsNone(row["company_id"])

    def test_same_idempotency_key_is_a_no_op_even_with_a_different_time(self):
        first = append_event(
            self.conn, entity="sender", kind="tick", actor="sender",
            at="2026-10-19T12:00:00Z", idempotency_key="tick-1",
        )
        second = append_event(
            self.conn, entity="sender", kind="tick", actor="sender",
            at="2026-10-20T12:00:00Z", idempotency_key="tick-1", detail={"later": True},
        )
        self.assertIsInstance(first, int)
        self.assertIsNone(second)
        rows = self.conn.execute("SELECT at, detail FROM event WHERE idempotency_key = 'tick-1'").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["at"], "2026-10-19T12:00:00Z")
        self.assertEqual(rows[0]["detail"], "{}")

    def test_different_keys_and_no_key_each_insert(self):
        a = append_event(self.conn, entity="sender", kind="k", actor="sender", idempotency_key="a")
        b = append_event(self.conn, entity="sender", kind="k", actor="sender", idempotency_key="b")
        c = append_event(self.conn, entity="sender", kind="k", actor="sender")
        d = append_event(self.conn, entity="sender", kind="k", actor="sender")
        self.assertEqual(len({a, b, c, d}), 4)

    def test_unknown_actor_is_refused(self):
        with self.assertRaises(sqlite3.IntegrityError):
            append_event(self.conn, entity="sender", kind="k", actor="robot")

    def test_unreadable_time_is_refused_before_writing(self):
        with self.assertRaises(ValueError):
            append_event(self.conn, entity="sender", kind="k", actor="sender", at="not a time")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM event").fetchone()[0], 0)


CONTEXT = {"source": "plan", "lane": "engineering", "roleKind": "engineer", "roleAgeDays": 3,
           "rankerVersion": "k-1", "signals": {}, "roleNamed": True}
SENT = "2026-10-19T12:00:00Z"
LATER = "2026-10-20T12:00:00Z"


class BodySha256Tests(unittest.TestCase):
    def test_body_sha256_collapses_whitespace_and_keeps_case(self):
        self.assertEqual(body_sha256("A  b\n"), body_sha256("A b"))
        self.assertEqual(body_sha256("  A\t\r\n b  "), body_sha256("A b"))
        self.assertNotEqual(body_sha256("a b"), body_sha256("A b"))
        self.assertRegex(body_sha256("A b"), r"^[0-9a-f]{64}$")

    def test_body_sha256_is_the_sha256_of_the_collapsed_utf8_text(self):
        import hashlib
        self.assertEqual(body_sha256("caf\u00e9  au\u00a0lait"),
                         hashlib.sha256("caf\u00e9 au lait".encode("utf-8")).hexdigest())
        self.assertEqual(body_sha256(""), hashlib.sha256(b"").hexdigest())


class AliasTests(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.acme = seed_company(self.conn, "Acme")
        self.other = seed_company(self.conn, "Other")

    def keys(self, company_id):
        return {r["alias_key"]: (r["alias"], r["source"]) for r in self.conn.execute(
            "SELECT alias_key, alias, source FROM company_alias WHERE company_id = ?", (company_id,))}

    def test_a_name_alias_is_stored_under_its_name_key(self):
        self.assertTrue(add_alias(self.conn, self.acme, "Acme Corp, Inc.", "name"))
        self.assertEqual(self.keys(self.acme), {"acme": ("Acme Corp, Inc.", "name")})
        self.assertTrue(add_alias(self.conn, self.acme, "Acme Labs LLC", "dba"))
        self.assertEqual(set(self.keys(self.acme)), {"acme", "acmelabs"})

    def test_the_same_key_for_the_same_company_returns_false(self):
        self.assertTrue(add_alias(self.conn, self.acme, "Acme Labs", "name"))
        self.assertFalse(add_alias(self.conn, self.acme, "ACME LABS, Inc.", "hand"))
        self.assertEqual(self.keys(self.acme), {"acmelabs": ("Acme Labs", "name")})

    def test_add_alias_refuses_other_company(self):
        add_alias(self.conn, self.acme, "Acme Labs", "name")
        with self.assertRaises(ValueError) as cm:
            add_alias(self.conn, self.other, "Acme Labs Inc", "name")
        self.assertIn("another company", str(cm.exception))
        self.assertEqual(self.keys(self.other), {})

    def test_add_alias_fein_kept_raw(self):
        self.assertTrue(add_alias(self.conn, self.acme, "12-3456789", "fein"))
        self.assertEqual(list(self.keys(self.acme)), ["12-3456789"])  # name_key would have given 123456789
        self.assertTrue(add_alias(self.conn, self.acme, "Lever:ACME", "ats"))
        self.assertTrue(add_alias(self.conn, self.acme, "HTTPS://LinkedIn.com/company/Acme", "linkedin"))
        self.assertEqual(set(self.keys(self.acme)),
                         {"12-3456789", "lever:acme", "https://linkedin.com/company/acme"})
        with self.assertRaises(ValueError):
            add_alias(self.conn, self.other, "12-3456789", "fein")

    def test_a_key_with_nothing_in_it_and_an_unknown_source_are_refused(self):
        with self.assertRaises(ValueError):
            add_alias(self.conn, self.acme, "!!!", "name")
        with self.assertRaises(ValueError):
            add_alias(self.conn, self.acme, "", "fein")
        with self.assertRaises(ValueError):
            add_alias(self.conn, self.acme, "Acme", "guess")
        self.assertEqual(self.keys(self.acme), {})


class SharedKeyTests(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)

    def test_shared_key_round_trip(self):
        self.assertFalse(is_shared_key(self.conn, "domain", "gusto.com"))
        mark_shared_key(self.conn, "domain", "Gusto.com", "payroll provider")
        self.assertTrue(is_shared_key(self.conn, "domain", "gusto.com"))
        self.assertTrue(is_shared_key(self.conn, "domain", "GUSTO.COM"))
        self.assertFalse(is_shared_key(self.conn, "fein", "gusto.com"))
        mark_shared_key(self.conn, "domain", "gusto.com", "a different reason")  # idempotent, first row stands
        rows = self.conn.execute("SELECT value, reason FROM shared_key").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("gusto.com", "payroll provider")])

    def test_a_bad_kind_or_empty_reason_is_refused(self):
        with self.assertRaises(sqlite3.IntegrityError):
            mark_shared_key(self.conn, "ein", "1", "r")
        with self.assertRaises(sqlite3.IntegrityError):
            mark_shared_key(self.conn, "fein", "111111111", " ")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM shared_key").fetchone()[0], 0)


class TouchTests(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.company = seed_company(self.conn)
        email_touch = seed_touch(self.conn, self.company, "pat@acme.co")
        self.email_touch = email_touch
        self.person = self.conn.execute("SELECT person_id FROM touch WHERE touch_id = ?",
                                        (email_touch,)).fetchone()[0]

    def log(self, key="k1", **over):
        args = dict(person_id=self.person, company_id=self.company, channel="linkedin_request", sent_at=SENT,
                    context=CONTEXT, idempotency_key=key)
        args.update(over)
        return log_touch(self.conn, **args)

    def row(self, touch_id):
        return self.conn.execute("SELECT * FROM outreach_touch WHERE touch_id = ?", (touch_id,)).fetchone()

    def events(self, touch_id):
        return [dict(r) for r in self.conn.execute(
            "SELECT entity, kind, actor, at, company_id, detail FROM event WHERE entity = 'outreach_touch' "
            "AND entity_id = ? ORDER BY event_id", (touch_id,))]

    def test_log_touch_writes_the_row_and_a_sent_event(self):
        touch_id = self.log(sent_at="2026-10-19T08:00:00-04:00")
        row = self.row(touch_id)
        self.assertEqual((row["person_id"], row["company_id"], row["channel"], row["sent_at"], row["revision"]),
                         (self.person, self.company, "linkedin_request", SENT, 1))
        self.assertEqual(json.loads(row["context"]), CONTEXT)
        self.assertIsNone(row["undone_at"])
        events = self.events(touch_id)
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["kind"], events[0]["at"], events[0]["company_id"]), ("sent", SENT, self.company))

    def test_log_touch_is_idempotent(self):
        first = self.log("click-1")
        second = self.log("click-1", sent_at=LATER)
        self.assertEqual(first, second)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM outreach_touch").fetchone()[0], 1)
        self.assertEqual(self.row(first)["sent_at"], SENT)
        self.assertEqual(len(self.events(first)), 1)
        self.assertNotEqual(self.log("click-2"), first)

    def test_log_touch_takes_an_application_with_no_person_and_an_email_touch(self):
        application = self.log("app-1", person_id=None, channel="application")
        self.assertIsNone(self.row(application)["person_id"])
        email = self.log("mail-1", channel="email", email_touch_id=self.email_touch)
        self.assertEqual(self.row(email)["email_touch_id"], self.email_touch)
        self.assertEqual(self.log("mail-1", channel="email", email_touch_id=self.email_touch), email)
        with self.assertRaises(sqlite3.IntegrityError):  # a person is needed for any other channel
            self.log("x", person_id=None)

    def test_log_touch_refuses_a_bad_context_and_writes_nothing(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.log("bad", context={"source": "plan"})
        self.assertEqual(self.conn.execute("SELECT count(*) FROM outreach_touch").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM event").fetchone()[0], 0)

    def test_record_outcome_sets_the_column_bumps_the_revision_and_appends_an_event(self):
        touch_id = self.log()
        revision = record_outcome(self.conn, touch_id, kind="accepted", at=LATER, revision=1)
        self.assertEqual(revision, 2)
        self.assertEqual((self.row(touch_id)["accepted_at"], self.row(touch_id)["revision"]), (LATER, 2))
        for kind, column in (("referred", "referred_at"), ("interview", "interview_at"), ("offer", "offer_at"),
                             ("quiet", "quiet_at")):
            revision = record_outcome(self.conn, touch_id, kind=kind, at=LATER, revision=revision)
            self.assertEqual(self.row(touch_id)[column], LATER)
        self.assertEqual(self.row(touch_id)["revision"], 6)
        self.assertEqual([e["kind"] for e in self.events(touch_id)],
                         ["sent", "accepted", "referred", "interview", "offer", "quiet"])
        self.assertEqual(self.events(touch_id)[1]["at"], LATER)

    def test_a_second_outcome_of_the_same_kind_is_refused(self):
        for kind, column in (("accepted", "accepted_at"), ("replied", "replied_at"), ("referred", "referred_at"),
                             ("interview", "interview_at"), ("offer", "offer_at"), ("quiet", "quiet_at")):
            with self.subTest(kind=kind):
                touch_id = self.log(f"twice-{kind}")
                revision = record_outcome(self.conn, touch_id, kind=kind, at=LATER, revision=1)
                with self.assertRaises(ValueError) as cm:
                    record_outcome(self.conn, touch_id, kind=kind, at="2026-10-25T12:00:00Z", revision=revision)
                self.assertIn("correct_outcome", str(cm.exception))
                row = self.row(touch_id)
                self.assertEqual((row[column], row["revision"]), (LATER, revision))
                self.assertEqual([e["kind"] for e in self.events(touch_id)], ["sent", kind])

    def test_a_repeated_replied_keeps_the_sorted_reply_kind(self):
        touch_id = self.log()
        revision = record_outcome(self.conn, touch_id, kind="replied", at=LATER, reply_kind="interested", revision=1)
        for again in (None, "not_now"):
            with self.subTest(reply_kind=again):
                with self.assertRaises(ValueError):
                    record_outcome(self.conn, touch_id, kind="replied", at=LATER, reply_kind=again, revision=revision)
        row = self.row(touch_id)
        self.assertEqual((row["reply_kind"], row["replied_at"], row["revision"]), ("interested", LATER, revision))

    def test_a_first_replied_with_no_reply_kind_is_unsorted(self):
        touch_id = self.log()
        record_outcome(self.conn, touch_id, kind="replied", at=LATER, revision=1)
        self.assertEqual(self.row(touch_id)["reply_kind"], "unsorted")

    def test_an_auto_reply_is_not_an_outcome(self):
        touch_id = self.log()
        with self.assertRaises(ValueError):
            record_outcome(self.conn, touch_id, kind="replied", at=LATER, reply_kind="auto_reply", revision=1)
        row = self.row(touch_id)
        self.assertEqual((row["replied_at"], row["reply_kind"], row["revision"]), (None, None, 1))

    def test_set_reply_kind_changes_only_the_kind(self):
        touch_id = self.log()
        revision = record_outcome(self.conn, touch_id, kind="replied", at=LATER, revision=1)
        before = self.row(touch_id)
        new = set_reply_kind(self.conn, touch_id, reply_kind="interested", revision=revision)
        after = self.row(touch_id)
        self.assertEqual(new, revision + 1)
        self.assertEqual((after["reply_kind"], after["revision"]), ("interested", new))
        for column in before.keys():
            if column not in ("reply_kind", "revision"):
                self.assertEqual(after[column], before[column], column)
        last = self.events(touch_id)[-1]
        self.assertEqual((last["kind"], last["actor"], json.loads(last["detail"])),
                         ("reply_kind", "devanshu", {"from": "unsorted", "to": "interested"}))
        new2 = set_reply_kind(self.conn, touch_id, reply_kind="said_no", revision=new, actor="import")
        self.assertEqual(self.row(touch_id)["reply_kind"], "said_no")
        self.assertEqual(self.events(touch_id)[-1]["actor"], "import")
        self.assertEqual(new2, new + 1)

    def test_set_reply_kind_refuses_a_touch_with_no_reply(self):
        touch_id = self.log()
        with self.assertRaises(ValueError):
            set_reply_kind(self.conn, touch_id, reply_kind="interested", revision=1)
        row = self.row(touch_id)
        self.assertEqual((row["reply_kind"], row["revision"]), (None, 1))
        self.assertEqual(len(self.events(touch_id)), 1)

    def test_set_reply_kind_refuses_a_bad_kind_and_checks_revision(self):
        touch_id = self.log()
        revision = record_outcome(self.conn, touch_id, kind="replied", at=LATER, revision=1)
        for bad in ("auto_reply", "maybe", None):
            with self.subTest(reply_kind=bad):
                with self.assertRaises(ValueError):
                    set_reply_kind(self.conn, touch_id, reply_kind=bad, revision=revision)
        with self.assertRaises(LookupError):
            set_reply_kind(self.conn, touch_id, reply_kind="interested", revision=revision - 1)
        with self.assertRaises(LookupError):
            set_reply_kind(self.conn, 999, reply_kind="interested", revision=1)
        row = self.row(touch_id)
        self.assertEqual((row["reply_kind"], row["revision"]), ("unsorted", revision))

    def test_record_outcome_checks_revision(self):
        touch_id = self.log()
        record_outcome(self.conn, touch_id, kind="accepted", at=LATER, revision=1)
        with self.assertRaises(LookupError):
            record_outcome(self.conn, touch_id, kind="replied", at=LATER, reply_kind="interested", revision=1)
        with self.assertRaises(LookupError):
            record_outcome(self.conn, 999, kind="accepted", at=LATER, revision=1)
        row = self.row(touch_id)
        self.assertEqual((row["replied_at"], row["revision"]), (None, 2))
        self.assertEqual(len(self.events(touch_id)), 2)

    def test_record_outcome_refuses_before_sent(self):
        touch_id = self.log()
        with self.assertRaises(ValueError):
            record_outcome(self.conn, touch_id, kind="accepted", at="2026-10-19T11:59:59Z", revision=1)
        row = self.row(touch_id)
        self.assertEqual((row["accepted_at"], row["revision"]), (None, 1))
        self.assertEqual(len(self.events(touch_id)), 1)

    def test_record_outcome_refuses_a_bad_kind_or_a_reply_kind_on_another_outcome(self):
        touch_id = self.log()
        for kwargs in ({"kind": "ghosted"}, {"kind": "accepted", "reply_kind": "interested"},
                       {"kind": "replied", "reply_kind": "maybe"}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    record_outcome(self.conn, touch_id, at=LATER, revision=1, **kwargs)
        self.assertEqual(self.row(touch_id)["revision"], 1)

    def test_replied_stores_the_kind_and_clears_quiet(self):
        touch_id = self.log()
        revision = record_outcome(self.conn, touch_id, kind="quiet", at=LATER, revision=1)
        self.assertEqual(self.row(touch_id)["quiet_at"], LATER)
        revision = record_outcome(self.conn, touch_id, kind="replied", at="2026-10-22T12:00:00Z",
                                  reply_kind="interested", revision=revision)
        row = self.row(touch_id)
        self.assertEqual((row["replied_at"], row["reply_kind"], row["quiet_at"]),
                         ("2026-10-22T12:00:00Z", "interested", None))
        self.assertEqual(revision, 3)

    def test_a_touch_that_replied_cannot_go_quiet(self):
        touch_id = self.log()
        revision = record_outcome(self.conn, touch_id, kind="replied", at=LATER, reply_kind="not_now", revision=1)
        with self.assertRaises(ValueError):
            record_outcome(self.conn, touch_id, kind="quiet", at=LATER, revision=revision)
        self.assertIsNone(self.row(touch_id)["quiet_at"])

    def test_undo_touch_sets_undone_at_and_blocks_outcomes(self):
        touch_id = self.log()
        revision = undo_touch(self.conn, touch_id, at=LATER, revision=1)
        self.assertEqual(revision, 2)
        row = self.row(touch_id)
        self.assertEqual((row["undone_at"], row["revision"]), (LATER, 2))  # kept, never deleted
        self.assertEqual([e["kind"] for e in self.events(touch_id)], ["sent", "undone"])
        with self.assertRaises(ValueError):
            record_outcome(self.conn, touch_id, kind="accepted", at=LATER, revision=revision)
        self.assertIsNone(self.row(touch_id)["accepted_at"])
        with self.assertRaises(ValueError):  # already undone
            undo_touch(self.conn, touch_id, at=LATER, revision=revision)

    def test_undo_touch_refuses_after_an_outcome(self):
        for kind, extra in (("accepted", {}), ("replied", {"reply_kind": "interested"}), ("referred", {}),
                            ("interview", {}), ("offer", {}), ("quiet", {})):
            with self.subTest(kind=kind):
                touch_id = self.log(f"u-{kind}")
                revision = record_outcome(self.conn, touch_id, kind=kind, at=LATER, revision=1, **extra)
                with self.assertRaises(ValueError):
                    undo_touch(self.conn, touch_id, at=LATER, revision=revision)
                self.assertIsNone(self.row(touch_id)["undone_at"])
        self.assertEqual(self.row(self.log("u-accepted"))["revision"], 2)

    def test_undo_touch_checks_revision(self):
        touch_id = self.log()
        # an outcome would block the undo, so bump the revision directly
        self.conn.execute("UPDATE outreach_touch SET revision = 2 WHERE touch_id = ?", (touch_id,))
        with self.assertRaises(LookupError):
            undo_touch(self.conn, touch_id, at=LATER, revision=1)
        with self.assertRaises(LookupError):
            undo_touch(self.conn, 999, at=LATER, revision=1)
        self.assertIsNone(self.row(touch_id)["undone_at"])

    def test_the_helpers_are_each_atomic_and_wrap_in_a_transaction(self):
        from outreach.store import transaction
        with self.assertRaises(RuntimeError):
            with transaction(self.conn):
                touch_id = self.log("tx")
                raise RuntimeError("boom")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM outreach_touch").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM event").fetchone()[0], 0)


class JobRunTests(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)

    def test_job_run_round_trip(self):
        run_id = start_job(self.conn, "crawl")
        row = self.conn.execute("SELECT * FROM job_run WHERE run_id = ?", (run_id,)).fetchone()
        self.assertEqual(row["status"], "running")
        self.assertIsNone(row["finished_at"])
        self.assertEqual(row["job"], "crawl")
        self.assertIsNone(row["ruleset_version"])
        finish_job(self.conn, run_id, "ok", {"n": 3})
        row = self.conn.execute("SELECT * FROM job_run WHERE run_id = ?", (run_id,)).fetchone()
        self.assertEqual(row["status"], "ok")
        self.assertRegex(row["finished_at"], r"Z$")
        self.assertEqual(json.loads(row["summary"]), {"n": 3})
        self.assertEqual(row["error"], "")

    def test_failed_run_keeps_its_error(self):
        run_id = start_job(self.conn, "rebuild")
        finish_job(self.conn, run_id, "failed", error="boom")
        row = self.conn.execute("SELECT * FROM job_run WHERE run_id = ?", (run_id,)).fetchone()
        self.assertEqual((row["status"], row["error"], row["summary"]), ("failed", "boom", "{}"))

    def test_ruleset_version_is_stored(self):
        self.conn.execute(
            "INSERT INTO ruleset(ruleset_version, config_toml, created_at) "
            "VALUES ('r-0123456789ab', '', '2026-10-19T12:00:00Z')"
        )
        run_id = start_job(self.conn, "rebuild", "r-0123456789ab")
        row = self.conn.execute("SELECT ruleset_version FROM job_run WHERE run_id = ?", (run_id,)).fetchone()
        self.assertEqual(row["ruleset_version"], "r-0123456789ab")

    def test_unknown_job_is_refused(self):
        with self.assertRaises(sqlite3.IntegrityError):
            start_job(self.conn, "mystery")

    def test_finishing_as_running_is_refused(self):
        run_id = start_job(self.conn, "crawl")
        with self.assertRaises(sqlite3.IntegrityError):
            finish_job(self.conn, run_id, "running")

    def test_finishing_a_missing_run_raises(self):
        with self.assertRaises(LookupError):
            finish_job(self.conn, 999, "ok")


if __name__ == "__main__":
    unittest.main()
