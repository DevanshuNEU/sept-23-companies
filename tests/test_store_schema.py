import itertools
import json
import re
import sqlite3
import unittest

from tests.storetest import fresh_store, seed_company, seed_posting, seed_touch

NOW = "2026-10-19T12:00:00Z"
TODAY = "2026-10-19"
SHA = "0123456789abcdef" * 4
# outreach_touch.context: the one key set build_context writes (#30 Task 1, controller ruling 3).
CONTEXT = json.dumps({"source": "plan", "lane": "engineering", "roleKind": "engineer", "roleAgeDays": 3,
                      "rankerVersion": "k-1", "signals": {"lca": 0.4}, "roleNamed": True})
LCA = json.dumps({"fy": 2025, "jr": 3, "level1": 2, "newemp_jr": 2, "software": 5})
_n = itertools.count(1)

STATES = ("planned", "drafted", "approved", "sending", "send_unknown", "sent", "failed", "skipped")
LEGAL = {
    ("planned", "drafted"), ("planned", "skipped"),
    ("drafted", "approved"), ("drafted", "skipped"),
    ("approved", "drafted"), ("approved", "sending"), ("approved", "skipped"),
    ("sending", "sent"), ("sending", "send_unknown"),
    ("send_unknown", "sent"), ("send_unknown", "failed"),
    ("failed", "approved"), ("failed", "drafted"), ("failed", "skipped"),
    ("skipped", "drafted"),
}
# The legal steps from a new drafted message to each state.
PATH = {
    "planned": [],
    "drafted": [],
    "approved": ["approved"],
    "sending": ["approved", "sending"],
    "send_unknown": ["approved", "sending", "send_unknown"],
    "sent": ["approved", "sending", "sent"],
    "failed": ["approved", "sending", "send_unknown", "failed"],
    "skipped": ["skipped"],
}


def insert(conn, table, row, verb="INSERT"):
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    return conn.execute(f"{verb} INTO {table}({cols}) VALUES ({marks})", list(row.values())).lastrowid


def at(hour, minute=0):
    return f"2026-10-19T{hour:02d}:{minute:02d}:00Z"


def fields_for(state):
    """Every workflow column a message in `state` must carry, set explicitly, so a refused
    UPDATE is refused by the transition trigger and not by a CHECK."""
    n = next(_n)
    pending = state in ("sending", "send_unknown")
    return {
        "approved_at": NOW if state in ("approved", "sending", "send_unknown", "sent") else None,
        "pending_rfc_id": f"<pending{n}@acme.co>" if pending else None,
        "claimed_at": NOW if pending else None,
        "rfc_message_id": f"<sent{n}@acme.co>" if state == "sent" else None,
        "sent_at": NOW if state == "sent" else None,
    }


class SchemaTest(unittest.TestCase):
    def setUp(self):
        self.db = fresh_store()
        self.addCleanup(self.db.close)
        self.company = seed_company(self.db)
        self.touch = seed_touch(self.db, self.company, "base@acme.co")
        self.person = self.db.execute(
            "SELECT person_id FROM touch WHERE touch_id = ?", (self.touch,)
        ).fetchone()[0]
        self.posting = seed_posting(self.db, self.company, "Software Engineer")
        self.ruleset_a = self.ruleset("r-aaaaaaaaaaaa")
        self.run = insert(self.db, "job_run", {"job": "crawl", "started_at": NOW})
        self.board = self.new_board()
        insert(self.db, "daily_list", {"list_date": "2026-10-01", "built_at": NOW, "capacity": 5})
        self.spare_person = self.new_person()
        insert(self.db, "source", {"source_name": "lca", "version": "lca-1"})
        self.otouch = self.new_otouch()

    # ---- builders -------------------------------------------------------

    def ruleset(self, version, current=0):
        insert(self.db, "ruleset", {
            "ruleset_version": version, "config_toml": "", "created_at": NOW, "is_current": current,
        })
        return version

    def make_current(self, version):
        """Switch the current ruleset in the two statements the schema needs."""
        self.db.execute("UPDATE ruleset SET is_current = 0 WHERE is_current = 1")
        self.db.execute("UPDATE ruleset SET is_current = 1 WHERE ruleset_version = ?", (version,))

    def new_board(self, **over):
        n = next(_n)
        row = {"company_id": self.company, "ats": "ashby", "token": f"tok{n}", "created_at": NOW}
        row.update(over)
        return insert(self.db, "board", row)

    def new_person(self, **over):
        n = next(_n)
        row = {
            "company_id": self.company, "name": f"Person {n}", "role_kind": "engineer",
            "email": f"person{n}@acme.co", "channel": "email", "added_by": "devanshu",
            "created_at": NOW, "updated_at": NOW,
        }
        row.update(over)
        return insert(self.db, "person", row)

    def fresh_touch(self):
        return seed_touch(self.db, self.company, f"touch{next(_n)}@acme.co")

    def new_message(self, touch_id=None, seq=0, state="drafted", **over):
        row = {
            "touch_id": touch_id or self.touch, "seq": seq, "state": state,
            "subject": "Hello", "body": "A short note.", "created_at": NOW, "updated_at": NOW,
        }
        if seq:
            row["due_on"] = "2026-11-01"
        row.update(over)
        return insert(self.db, "message", row)

    def transition(self, message_id, state, **extra):
        values = {"state": state, **fields_for(state), **extra}
        sets = ", ".join(f"{c} = ?" for c in values)
        self.db.execute(f"UPDATE message SET {sets} WHERE message_id = ?", [*values.values(), message_id])

    def retire(self, message_id):
        """Free the one 'sending' slot for the next test row. Claimed messages can no longer be deleted, so a
        message still sending moves on to sent, a legal step."""
        state = self.db.execute("SELECT state FROM message WHERE message_id = ?", (message_id,)).fetchone()[0]
        if state == "sending":
            self.transition(message_id, "sent")
        elif state in ("planned", "drafted", "approved", "failed", "skipped"):
            self.db.execute("DELETE FROM message WHERE message_id = ?", (message_id,))

    def message_in(self, state):
        """A message on its own touch, walked along legal steps to `state`."""
        touch_id = self.fresh_touch()
        if state == "planned":
            return self.new_message(touch_id, state="planned")
        message_id = self.new_message(touch_id)
        for step in PATH[state]:
            self.transition(message_id, step)
        return message_id

    def new_otouch(self, **over):
        """An outreach_touch row (the outcome record of #30), default a LinkedIn request sent at NOW."""
        row = {"person_id": self.person, "company_id": self.company, "channel": "linkedin_request",
               "sent_at": NOW, "context": CONTEXT, "created_at": NOW}
        row.update(over)
        return insert(self.db, "outreach_touch", row)

    def evidence(self, **over):
        row = {
            "company_id": self.company, "fact": "size", "value": "50", "num_low": 50, "num_high": 50,
            "confidence": "estimate", "source_kind": "posting", "recorded_at": NOW,
        }
        row.update(over)
        return insert(self.db, "evidence", row)

    def hand(self, **over):
        return self.evidence(locked=1, source_kind="hand", confidence="confirmed", **over)

    def decision(self, posting_id, version, **over):
        row = {
            "posting_id": posting_id, "ruleset_version": version, "decided_at": NOW,
            "lane": "engineering", "level_tier": "new_grad", "level_ok": 1,
            "level_basis": "the title says new grad", "us": "yes", "sponsorship": "silent",
            "citizenship": 0, "eligibility": "ok", "start_2027": 0,
        }
        row.update(over)
        return insert(self.db, "decision", row)

    def review(self, **over):
        row = {
            "kind": "board_empty", "company_id": self.company, "board_id": self.board,
            "reason": "Board returned no postings twice", "evidence_key": "run-1",
            "raised_by": "crawler", "created_at": NOW,
        }
        row.update(over)
        return insert(self.db, "review_item", row)

    def base_row(self, table):
        """A valid row for each table that has a timestamp or date column."""
        rows = {
            "schema_migration": {"version": 99, "name": "x", "applied_at": NOW},
            "company": {"name": "Base", "status": "working", "status_at": NOW, "created_at": NOW,
                        "paused_at": NOW, "held_by_touch": self.touch, "held_at": NOW},
            "company_alias": {"alias_key": "base", "company_id": self.company, "alias": "Base",
                              "source": "name", "created_at": NOW},
            "board": {"company_id": self.company, "ats": "lever", "token": "base", "created_at": NOW,
                      "last_read": "ok", "last_read_at": NOW, "streak_since": NOW},
            "board_read": {"board_id": self.board, "run_id": self.run, "at": NOW, "result": "ok"},
            "posting": {"company_id": self.company, "source_key": "url:base", "title": "T",
                        "first_seen_at": NOW, "last_seen_at": NOW, "posted_on": TODAY,
                        "closed_at": NOW, "close_reason": "hand"},
            "ruleset": {"ruleset_version": "r-bbbbbbbbbbbb", "config_toml": "", "created_at": NOW},
            "decision": {"posting_id": self.posting, "ruleset_version": self.ruleset_a, "decided_at": NOW,
                         "lane": "engineering", "level_tier": "new_grad", "level_ok": 1,
                         "level_basis": "title", "us": "yes", "sponsorship": "silent",
                         "citizenship": 0, "eligibility": "ok", "start_2027": 0},
            "evidence": {"company_id": self.company, "fact": "sponsorship", "value": "yes",
                         "confidence": "estimate", "source_kind": "posting", "recorded_at": NOW,
                         "observed_on": TODAY},
            "person": {"company_id": self.company, "name": "P", "role_kind": "engineer",
                       "email": "base2@acme.co", "channel": "email", "added_by": "devanshu",
                       "created_at": NOW, "updated_at": NOW},
            "touch": {"person_id": self.spare_person, "created_at": NOW, "updated_at": NOW,
                      "first_sent_at": NOW, "first_rfc_id": "<base@acme.co>", "gmail_thread_id": "t1",
                      "replied_at": NOW, "bounced_at": NOW, "auto_reply_at": NOW, "closed_at": NOW},
            "message": {"touch_id": self.touch, "seq": 0, "state": "sent", "subject": "S", "body": "B",
                        "created_at": NOW, "updated_at": NOW, "due_on": TODAY, "approved_at": NOW,
                        "sent_at": NOW, "rfc_message_id": "<base-sent@acme.co>", "reconciled_at": NOW},
            "inbound_message": {"touch_id": self.touch, "gmail_message_id": "g1", "from_addr": "a@b.co",
                                "kind": "human", "received_at": NOW, "recorded_at": NOW},
            "linkedin_log": {"person_id": self.person, "kind": "note", "at": NOW, "recorded_at": NOW},
            "sender": {"sender_id": 1, "paused": 1, "pause_kind": "by_you", "paused_at": NOW,
                       "updated_at": NOW},
            "send_day": {"day": "2026-10-20", "send_limit": 5, "created_at": NOW},
            "no_go": {"company_id": self.company, "reason": "other", "source": "devanshu", "created_at": NOW,
                      "lifted_at": NOW, "lift_reason": "changed my mind"},
            "review_item": {"kind": "flagged", "company_id": self.company, "reason": "r",
                            "evidence_key": "k", "raised_by": "claude", "created_at": NOW,
                            "state": "resolved", "resolution": "marked_ok", "resolved_at": NOW},
            "daily_list": {"list_date": "2026-10-02", "built_at": NOW, "capacity": 5},
            "daily_list_entry": {"list_date": "2026-10-01", "company_id": self.company,
                                 "list_group": "level_role", "rank": 1, "proposed_at": NOW,
                                 "state": "picked", "decided_at": NOW},
            "event": {"at": NOW, "recorded_at": NOW, "entity": "company", "kind": "k", "actor": "system"},
            "job_run": {"job": "rebuild", "started_at": NOW, "status": "ok", "finished_at": NOW},
            "import_record": {"run_id": self.run, "source": "data_js", "source_key": "k", "raw": "r",
                              "raw_sha256": "a" * 64, "outcome": "imported", "entity": "company",
                              "entity_id": self.company, "imported_at": NOW},
            "posting_body": {"posting_id": self.posting, "body": "B", "fetched_at": NOW},
            "shared_key": {"kind": "fein", "value": "111111111", "reason": "payroll provider", "recorded_at": NOW},
            "company_source": {"company_id": self.company, "source_name": "lca", "source_ref": "lca:acme",
                               "first_seen_at": NOW, "last_seen_at": NOW},
            "watched_page": {"company_id": self.company, "url": "https://acme.example/careers",
                             "last_fetch_at": NOW, "last_change_at": NOW, "next_fetch_at": NOW},
            "read_queue": {"posting_id": self.posting, "body_sha256": SHA, "reader_version": "rv-1",
                           "priority": 1, "state": "leased", "lease_until": NOW, "enqueued_at": NOW},
            "posting_reading": {"posting_id": self.posting, "body_sha256": SHA, "reader_version": "rv-1",
                                "read_at": NOW, "fields": "{}", "verified": 1},
            "work_tier": {"company_id": self.company, "tier": 1, "reason": "small LCA filer", "set_at": NOW},
            "company_post": {"company_id": self.company, "source_name": "lca", "url": "https://hn.example/1",
                             "posted_at": NOW},
            "company_score": {"company_id": self.company, "ranker_version": "k-1", "score": 0.5,
                              "signals": "{}", "lanes": "[]", "scored_at": NOW},
            "application": {"company_id": self.company, "posting_id": self.posting, "applied_on": TODAY,
                            "outcome": "screen", "outcome_at": NOW},
            "outreach_touch": {"person_id": self.person, "company_id": self.company, "channel": "linkedin_request",
                               "sent_at": NOW, "accepted_at": NOW, "replied_at": NOW, "referred_at": NOW,
                               "interview_at": NOW, "offer_at": NOW, "context": CONTEXT, "created_at": NOW},
            "person_source": {"person_id": self.person, "kind": "team", "url": "https://acme.example/team",
                              "observed_on": TODAY, "recorded_at": NOW},
            "linkedin_draft": {"person_id": self.person, "body": "Hi", "saved_by": "claude",
                               "created_at": NOW, "updated_at": NOW},
            "api_request": {"request_id": "req-00001", "method": "POST", "path": "/api/x", "fingerprint": "f" * 64,
                            "status": 200, "response": "{}", "created_at": NOW},
            "mx_check": {"domain": "acme.co", "status": "ok", "checked_at": NOW},
            "win_seen": {"touch_id": self.otouch, "kind": "accepted", "seen_at": NOW},
            "watch_dismissal": {"kind": "new_reason_no_role", "ref_id": 1, "dismissed_at": NOW},
            "inbound_seen": {"gmail_message_id": "g9", "why": "untracked", "seen_at": NOW},
        }
        return dict(rows[table])

    def tables(self):
        return [r[0] for r in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]

    def columns(self, table):
        return [r[0] for r in self.db.execute("SELECT name FROM pragma_table_info(?)", (table,))]

    def assert_refused(self, sql, params=(), contains=None):
        with self.assertRaises(sqlite3.IntegrityError) as cm:
            self.db.execute(sql, params)
        if contains:
            self.assertIn(contains, str(cm.exception))

    def assert_row_refused(self, table, row, contains=None):
        with self.assertRaises(sqlite3.IntegrityError) as cm:
            insert(self.db, table, row)
        if contains:
            self.assertIn(contains, str(cm.exception))

    def assert_bad_value_refused(self, table, column, bad_values, expect):
        """The base row inserts; the same row with `column` set to each bad value does not."""
        verb = "INSERT OR REPLACE" if table == "sender" else "INSERT"
        for bad in bad_values:
            with self.subTest(table=table, column=column, value=bad):
                self.db.execute("SAVEPOINT probe")
                try:
                    insert(self.db, table, self.base_row(table), verb)
                    self.db.execute("ROLLBACK TO probe")
                    row = self.base_row(table)
                    row[column] = bad
                    with self.assertRaises(sqlite3.IntegrityError) as cm:
                        insert(self.db, table, row, verb)
                    self.assertIn(expect, str(cm.exception))
                finally:
                    self.db.execute("ROLLBACK TO probe")
                    self.db.execute("RELEASE probe")

    # ---- generic checks -------------------------------------------------

    def test_migration_is_recorded(self):
        row = self.db.execute("SELECT version, name FROM schema_migration").fetchall()
        self.assertEqual([(r[0], r[1]) for r in row][:4], [
            (1, "initial"), (2, "sending"), (3, "inbound_seen"), (4, "import_history")])
        self.assertEqual(self.db.execute("PRAGMA user_version").fetchone()[0], len(row))

    def test_store_is_sound(self):
        self.assertEqual(self.db.execute("PRAGMA integrity_check").fetchall()[0][0], "ok")
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_every_table_is_strict(self):
        loose = self.db.execute(
            "SELECT name FROM pragma_table_list WHERE schema = 'main' AND type = 'table' "
            "AND strict = 0 AND name NOT LIKE 'sqlite_%'").fetchall()
        self.assertEqual([r[0] for r in loose], [])

    def test_every_at_column_rejects_offsets_and_fractions(self):
        bad = ["2026-10-19T12:00:00+00:00", "2026-10-19 12:00:00", "2026-13-01T00:00:00Z",
               "2026-10-19T12:00:00.5Z"]
        columns = self.checked_columns("strftime")
        # Every timestamp column named like one, and streak_since, has the format CHECK.
        named = {(t, c) for t in self.tables() for c in self.columns(t)
                 if c == "at" or c.endswith("_at") or c in ("streak_since", "lease_until")}
        self.assertEqual(columns, named)
        self.assertEqual(len(columns), 85)  # 50 before the pipeline tables (#30), 31 with them, 3 + 1 for #14
        for table, column in sorted(columns):
            self.assertTrue(self._has_base(table), f"no base row for {table}")
            self.assert_bad_value_refused(table, column, bad, f"{column} IS strftime(")

    def checked_columns(self, function):
        """(table, column) pairs whose CREATE TABLE text has a `<column> IS <function>(` CHECK."""
        found = set()
        for name, sql in self.db.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"):
            for column in re.findall(rf"\b(\w+) IS {function}\(", sql):
                found.add((name, column))
        return found

    def test_claimed_and_sent_at_are_checked_in_their_own_states(self):
        # A message cannot be inserted as sending, so these two are also tried on a live row.
        for state, column in (("sending", "claimed_at"), ("send_unknown", "claimed_at"), ("sent", "sent_at")):
            message_id = self.message_in(state)
            for bad in ("2026-10-19T12:00:00+00:00", "2026-13-01T00:00:00Z"):
                with self.subTest(state=state, column=column, value=bad):
                    self.assert_refused(f"UPDATE message SET {column} = ? WHERE message_id = ?",
                                        (bad, message_id), f"{column} IS strftime(")
            self.retire(message_id)

    def _has_base(self, table):
        try:
            self.base_row(table)
            return True
        except KeyError:
            return False

    def test_every_date_column_rejects_timestamps(self):
        bad = ["2026-10-19T00:00:00Z", "2026-13-01"]
        extra = {("daily_list", "list_date"), ("daily_list_entry", "list_date"), ("send_day", "day")}
        columns = {(t, c) for t in self.tables() for c in self.columns(t) if c.endswith("_on")} | extra
        # Every column the schema checks with date() is tested, and nothing is tested that is not named.
        self.assertLessEqual(self.checked_columns("date"), columns)
        # posted_on, observed_on (evidence and person_source), due_on, applied_on and the three named ones
        self.assertEqual(len(columns), 8)
        for table, column in sorted(columns):
            self.assertTrue(self._has_base(table), f"no base row for {table}")
            # daily_list_entry.list_date has no CHECK of its own: the foreign key to
            # daily_list refuses a value that is not a stored date.
            expect = "FOREIGN KEY" if (table, column) == ("daily_list_entry", "list_date") \
                else f"{column} IS date("
            self.assert_bad_value_refused(table, column, bad, expect)

    def test_foreign_keys_are_enforced(self):
        self.assert_row_refused("posting", {
            "company_id": 999, "source_key": "url:nobody", "title": "T",
            "first_seen_at": NOW, "last_seen_at": NOW}, "FOREIGN KEY")

    def test_ruleset_version_has_one_shape(self):
        self.assert_row_refused("ruleset", {
            "ruleset_version": "r-xyz", "config_toml": "", "created_at": NOW}, "CHECK")

    # ---- append-only history --------------------------------------------

    def test_events_are_append_only(self):
        event_id = insert(self.db, "event", self.base_row("event"))
        self.assert_refused("UPDATE event SET kind = 'other' WHERE event_id = ?", (event_id,), "append-only")
        self.assert_refused("DELETE FROM event WHERE event_id = ?", (event_id,), "append-only")

    def test_evidence_import_record_linkedin_log_inbound_are_append_only(self):
        evidence_id = self.evidence()
        import_id = insert(self.db, "import_record", self.base_row("import_record"))
        entry_id = insert(self.db, "linkedin_log", self.base_row("linkedin_log"))
        inbound_id = insert(self.db, "inbound_message", self.base_row("inbound_message"))
        cases = [
            ("evidence", "evidence_id", evidence_id, "value = 'other'"),
            ("import_record", "import_id", import_id, "reason = 'other'"),
            ("linkedin_log", "entry_id", entry_id, "note = 'other'"),
            ("inbound_message", "inbound_id", inbound_id, "subject = 'other'"),
        ]
        for table, key, row_id, change in cases:
            with self.subTest(table=table):
                self.assert_refused(f"UPDATE {table} SET {change} WHERE {key} = ?", (row_id,), "append-only")
                self.assert_refused(f"DELETE FROM {table} WHERE {key} = ?", (row_id,), "")
                self.assertEqual(
                    self.db.execute(f"SELECT count(*) FROM {table} WHERE {key} = ?", (row_id,)).fetchone()[0], 1)

    def test_postings_are_never_deleted(self):
        self.assert_refused("DELETE FROM posting WHERE posting_id = ?", (self.posting,), "never deleted")

    # ---- company --------------------------------------------------------

    def test_company_close_needs_a_reason(self):
        self.assert_refused("UPDATE company SET status = 'closed' WHERE company_id = ?",
                            (self.company,), "CHECK")
        self.db.execute("UPDATE company SET status = 'closed', close_reason = 'other' WHERE company_id = ?",
                        (self.company,))

    def test_merge_and_hold(self):
        other = seed_company(self.db, "Duplicate")
        self.assert_refused(
            "UPDATE company SET status = 'removed', remove_reason = 'dup', merged_into = company_id "
            "WHERE company_id = ?", (other,), "CHECK")
        self.assert_refused(
            "UPDATE company SET merged_into = ? WHERE company_id = ?", (self.company, other), "CHECK")
        self.db.execute(
            "UPDATE company SET status = 'removed', remove_reason = 'dup', merged_into = ? WHERE company_id = ?",
            (self.company, other))
        # His pause survives a reply hold: both on one working company.
        self.db.execute(
            "UPDATE company SET status = 'working', paused_at = ?, held_by_touch = ?, held_at = ? "
            "WHERE company_id = ?", (NOW, self.touch, NOW, self.company))
        row = self.db.execute("SELECT paused_at, held_by_touch FROM company WHERE company_id = ?",
                              (self.company,)).fetchone()
        self.assertEqual((row[0], row[1]), (NOW, self.touch))

    # ---- boards ---------------------------------------------------------

    def test_board_streaks_follow_the_read_contract(self):
        def board(last_read, empty, error, since=NOW):
            return {"company_id": self.company, "ats": "greenhouse", "token": f"b{next(_n)}",
                    "created_at": NOW, "last_read": last_read,
                    "last_read_at": None if last_read is None else NOW,
                    "streak_since": since if last_read is not None else None,
                    "empty_streak": empty, "error_streak": error}

        self.assert_row_refused("board", board("ok", 1, 0), "CHECK")
        self.assert_row_refused("board", board("ok", 0, 1), "CHECK")
        self.assert_row_refused("board", board("empty", 0, 1), "CHECK")
        self.assert_row_refused("board", board("empty", 0, 0), "CHECK")
        self.assert_row_refused("board", board("error", 0, 0), "CHECK")
        insert(self.db, "board", board("ok", 0, 0))
        insert(self.db, "board", board("empty", 2, 0))
        # An error leaves the empty count alone (board_status.py:43-44).
        insert(self.db, "board", board("error", 1, 1))

    def test_board_streak_since_follows_last_read(self):
        row = {"company_id": self.company, "ats": "greenhouse", "token": "since", "created_at": NOW,
               "last_read": "ok", "last_read_at": NOW, "streak_since": None}
        self.assert_row_refused("board", row, "CHECK")
        row["streak_since"] = NOW
        insert(self.db, "board", row)

    # ---- decisions and rulesets -----------------------------------------

    def test_decision_quotes_are_required(self):
        self.assert_row_refused("decision", {
            **self.base_row("decision"), "sponsorship": "no", "sponsorship_quote": ""}, "CHECK")
        self.assert_row_refused("decision", {
            **self.base_row("decision"), "eligibility": "excluded", "level_ok": 0,
            "eligibility_quote": "Graduating in 2027"}, "CHECK")
        self.assert_row_refused("decision", {
            **self.base_row("decision"), "eligibility": "excluded", "level_ok": 0,
            "eligibility_reason": "grad_window", "eligibility_quote": ""}, "CHECK")
        self.decision(self.posting, self.ruleset_a, level_ok=0, eligibility="excluded",
                      eligibility_reason="grad_window", eligibility_quote="Graduating in 2027",
                      sponsorship="no", sponsorship_quote="We do not sponsor visas")

    def test_only_one_ruleset_is_current(self):
        b = self.ruleset("r-bbbbbbbbbbbb")
        self.make_current(self.ruleset_a)
        self.assert_refused("UPDATE ruleset SET is_current = 1 WHERE ruleset_version = ?", (b,), "UNIQUE")

    def test_ruleset_revert_takes_two_statements(self):
        # The earlier row is the one reverted to, so a single UPDATE meets the current row second.
        old = self.ruleset("r-cccccccccccc")
        new = self.ruleset("r-dddddddddddd")
        self.make_current(new)
        self.assert_refused("UPDATE ruleset SET is_current = (ruleset_version = ?)", (old,), "UNIQUE")
        self.make_current(old)
        current = self.db.execute("SELECT ruleset_version FROM ruleset WHERE is_current = 1").fetchall()
        self.assertEqual([r[0] for r in current], [old])

    def test_current_decision_view_follows_the_current_ruleset(self):
        b = self.ruleset("r-bbbbbbbbbbbb")
        later = seed_posting(self.db, self.company, "Backend Engineer")
        self.decision(self.posting, self.ruleset_a, decided_at=at(10))
        self.decision(self.posting, b, decided_at=at(11))
        self.decision(later, b, decided_at=at(11))

        def current():
            return sorted((r[0], r[1]) for r in self.db.execute(
                "SELECT posting_id, ruleset_version FROM current_decision"))

        def chosen():
            return sorted((r[0], r[1]) for r in self.db.execute(
                "SELECT posting_id, ruleset_version FROM posting_decision"))

        self.make_current(self.ruleset_a)
        self.assertEqual(current(), [(self.posting, self.ruleset_a)])
        # One decision per posting: the current one, else the newest kept.
        self.assertEqual(chosen(), sorted([(self.posting, self.ruleset_a), (later, b)]))
        self.make_current(b)
        self.assertEqual(current(), sorted([(self.posting, b), (later, b)]))
        self.assertEqual(chosen(), sorted([(self.posting, b), (later, b)]))

    # ---- evidence -------------------------------------------------------

    def test_hand_value_wins_and_is_never_deduplicated(self):
        self.evidence(value="300-400", num_low=300, num_high=400, recorded_at=at(12))
        self.hand(value="50", num_low=50, num_high=50, recorded_at=at(12, 1))
        self.hand(value="80", num_low=80, num_high=80, recorded_at=at(12, 2))
        last = self.hand(value="50", num_low=50, num_high=50, recorded_at=at(12, 3))
        fact = self.db.execute(
            "SELECT evidence_id, num_low FROM company_fact WHERE company_id = ? AND fact = 'size'",
            (self.company,)).fetchone()
        self.assertEqual((fact[0], fact[1]), (last, 50))
        hand_rows = self.db.execute("SELECT count(*) FROM evidence WHERE locked = 1").fetchone()[0]
        self.assertEqual(hand_rows, 3)

    def test_automation_never_outranks_a_hand_value(self):
        self.hand(fact="sponsorship", value="yes", num_low=None, num_high=None, recorded_at=at(10))
        self.evidence(fact="sponsorship", value="no", num_low=None, num_high=None,
                      confidence="confirmed", recorded_at=at(11))
        value = self.db.execute(
            "SELECT value FROM company_fact WHERE company_id = ? AND fact = 'sponsorship'",
            (self.company,)).fetchone()[0]
        self.assertEqual(value, "yes")

    def test_newest_hand_row_wins_whatever_its_confidence(self):
        self.hand(value="80", num_low=80, num_high=80, recorded_at=at(10))
        self.evidence(value="120", num_low=120, num_high=120, confidence="estimate", locked=1,
                      source_kind="hand", recorded_at=at(11))
        value = self.db.execute(
            "SELECT value FROM company_fact WHERE company_id = ? AND fact = 'size'",
            (self.company,)).fetchone()[0]
        self.assertEqual(value, "120")

    def test_automation_repeats_are_ignored(self):
        sql = ("INSERT INTO evidence(company_id, fact, value, num_low, num_high, confidence, source_kind, "
               "source_ref, quote, recorded_at) VALUES (?, 'size', '300', 300, 300, 'estimate', 'posting', "
               "'url', 'about 300 people', ?) ON CONFLICT DO NOTHING")
        self.db.execute(sql, (self.company, at(10)))
        self.db.execute(sql, (self.company, at(11)))
        count = self.db.execute("SELECT count(*) FROM evidence WHERE locked = 0").fetchone()[0]
        self.assertEqual(count, 1)

    def test_on_conflict_do_nothing_still_raises_on_check(self):
        stage = ("INSERT INTO evidence(company_id, fact, value, confidence, source_kind, recorded_at) "
                 "VALUES (?, 'stage', 'series_z', 'estimate', 'posting', ?) ON CONFLICT DO NOTHING")
        size = ("INSERT INTO evidence(company_id, fact, value, num_low, confidence, source_kind, recorded_at) "
                "VALUES (?, 'size', '300', NULL, 'estimate', 'posting', ?) ON CONFLICT DO NOTHING")
        self.assert_refused(stage, (self.company, NOW), "CHECK")
        self.assert_refused(size, (self.company, NOW), "CHECK")

    def test_everify_needs_a_date(self):
        row = {"fact": "everify", "value": "enrolled", "num_low": None, "num_high": None}
        self.assert_row_refused("evidence", {**self.base_row("evidence"), **row, "observed_on": None}, "CHECK")
        self.assert_row_refused("evidence", {
            **self.base_row("evidence"), **row, "value": "maybe", "observed_on": TODAY}, "CHECK")
        insert(self.db, "evidence", {**self.base_row("evidence"), **row, "observed_on": TODAY})

    # ---- messages -------------------------------------------------------

    def test_message_states_carry_their_fields(self):
        message_id = self.new_message()
        self.transition(message_id, "approved")
        # sending without pending_rfc_id
        self.assert_refused(
            "UPDATE message SET state = 'sending', claimed_at = ? WHERE message_id = ?",
            (NOW, message_id), "pending_rfc_id")
        # sent without rfc_message_id (sent_at set, pending and claim cleared)
        self.transition(message_id, "sending")
        self.assert_refused(
            "UPDATE message SET state = 'sent', sent_at = ?, pending_rfc_id = NULL, claimed_at = NULL "
            "WHERE message_id = ?", (NOW, message_id), "rfc_message_id")
        # a follow-up without a due date
        self.assert_row_refused("message", {
            "touch_id": self.touch, "seq": 1, "state": "planned", "created_at": NOW, "updated_at": NOW},
            "due_on")

    def test_only_one_message_is_sending(self):
        first = self.new_message(self.fresh_touch())
        second = self.new_message(self.fresh_touch())
        for message_id in (first, second):
            self.transition(message_id, "approved")
        self.transition(first, "sending")
        with self.assertRaises(sqlite3.IntegrityError) as cm:
            self.transition(second, "sending")
        self.assertIn("UNIQUE", str(cm.exception))
        self.transition(first, "sent")
        self.transition(second, "sending")

    def test_message_state_machine(self):
        # A message is born planned or drafted (or sent, for imported history), never approved.
        self.assert_row_refused("message", {
            "touch_id": self.touch, "seq": 0, "state": "approved", "subject": "S", "body": "B",
            "approved_at": NOW, "created_at": NOW, "updated_at": NOW}, "starts planned or drafted")
        message_id = self.new_message()
        for state in ("approved", "sending", "sent"):
            self.transition(message_id, state)
        # sent is final: re-drafting it with every drafted field set is still refused.
        self.assert_refused(
            "UPDATE message SET state = 'drafted', sent_at = NULL, rfc_message_id = NULL WHERE message_id = ?",
            (message_id,), "illegal message state change")
        # A skipped step with every target field set.
        other = self.new_message(self.fresh_touch())
        self.assert_refused(
            "UPDATE message SET state = 'sending', approved_at = ?, pending_rfc_id = '<skip@acme.co>', "
            "claimed_at = ? WHERE message_id = ?", (NOW, NOW, other), "illegal message state change")

    def test_every_legal_transition_is_accepted(self):
        for old, new in sorted(LEGAL):
            with self.subTest(change=f"{old}>{new}"):
                message_id = self.message_in(old)
                self.transition(message_id, new)
                state = self.db.execute("SELECT state FROM message WHERE message_id = ?",
                                        (message_id,)).fetchone()[0]
                self.assertEqual(state, new)
                self.retire(message_id)

    def test_every_other_transition_is_refused_by_the_trigger(self):
        for old, new in itertools.permutations(STATES, 2):
            if (old, new) in LEGAL:
                continue
            with self.subTest(change=f"{old}>{new}"):
                message_id = self.message_in(old)
                with self.assertRaises(sqlite3.IntegrityError) as cm:
                    self.transition(message_id, new)
                self.assertIn("illegal message state change", str(cm.exception))
                self.retire(message_id)

    def test_a_claimed_message_is_frozen(self):
        for state in ("sending", "send_unknown", "sent"):
            with self.subTest(state=state):
                message_id = self.message_in(state)
                self.assert_refused("UPDATE message SET subject = 'Changed' WHERE message_id = ?",
                                    (message_id,), "frozen")
                self.assert_refused("UPDATE message SET body = 'Changed' WHERE message_id = ?",
                                    (message_id,), "frozen")
                self.retire(message_id)
        drafted = self.message_in("drafted")
        self.db.execute("UPDATE message SET subject = 'Changed' WHERE message_id = ?", (drafted,))

    def test_follow_up_waits_for_the_first_send(self):
        follow_up = self.new_message(seq=1, state="planned", subject="", body="")
        # Every field a drafted follow-up needs is set, so only the trigger can refuse.
        self.assert_refused(
            "UPDATE message SET state = 'drafted', subject = 'Re: Hello', body = 'Following up.' "
            "WHERE message_id = ?", (follow_up,), "after the first email is sent")
        self.assert_row_refused("message", {
            "touch_id": self.touch, "seq": 2, "state": "drafted", "subject": "Re: Hello",
            "body": "Again.", "due_on": "2026-11-10", "created_at": NOW, "updated_at": NOW},
            "waits for the first send")
        self.db.execute(
            "UPDATE touch SET first_sent_at = ?, first_rfc_id = '<first@acme.co>', "
            "gmail_thread_id = 'thread1' WHERE touch_id = ?", (NOW, self.touch))
        self.db.execute(
            "UPDATE message SET state = 'drafted', subject = 'Re: Hello', body = 'Following up.' "
            "WHERE message_id = ?", (follow_up,))
        self.new_message(seq=2, state="drafted")

    def test_checks_must_be_a_json_array(self):
        for bad in ("{}", "not json", "3"):
            with self.subTest(checks=bad):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.new_message(self.fresh_touch(), checks=bad)
        self.new_message(self.fresh_touch(), checks="[]")

    # ---- people and touches ---------------------------------------------

    def test_touch_flags_are_one_way(self):
        self.db.execute(
            "UPDATE touch SET state = 'replied', replied_at = ?, reply_kind = 'interested' WHERE touch_id = ?",
            (NOW, self.touch))
        self.db.execute(
            "UPDATE touch SET state = 'closed', closed_at = ?, close_reason = 'no_reply' WHERE touch_id = ?",
            (NOW, self.touch))
        row = self.db.execute("SELECT state, replied_at, reply_kind FROM touch WHERE touch_id = ?",
                              (self.touch,)).fetchone()
        self.assertEqual((row[0], row[1], row[2]), ("closed", NOW, "interested"))

    def test_imported_history(self):
        no_address = {"channel": "email", "email": None}
        self.new_person(added_by="import", **no_address)
        with self.assertRaises(sqlite3.IntegrityError):
            self.new_person(added_by="claude", **no_address)
        imported = self.new_person()
        insert(self.db, "touch", {"person_id": imported, "origin": "import", "first_sent_at": NOW,
                                  "created_at": NOW, "updated_at": NOW})
        sender_made = self.new_person()
        with self.assertRaises(sqlite3.IntegrityError):
            insert(self.db, "touch", {"person_id": sender_made, "origin": "sender", "first_sent_at": NOW,
                                      "created_at": NOW, "updated_at": NOW})

    def test_import_record_accepts_the_new_sources(self):
        for source in ("interview_prep", "curated_touches", "send_log"):
            with self.subTest(source=source):
                insert(self.db, "import_record", {**self.base_row("import_record"), "source": source})
        self.assert_row_refused("import_record", {**self.base_row("import_record"), "source": "gmail"}, "CHECK")

    def test_import_record_held_has_no_entity_and_a_reason(self):
        held = {**self.base_row("import_record"), "outcome": "held", "reason": "send never settled"}
        self.assert_row_refused("import_record", held, "CHECK")  # a held record names no entity
        self.assert_row_refused("import_record", {**held, "entity": None, "entity_id": None, "reason": ""}, "CHECK")
        insert(self.db, "import_record", {**held, "entity": None, "entity_id": None})
        # a skipped record still has no entity, and an imported or merged one still has one
        skipped = {**held, "outcome": "skipped", "entity": None, "entity_id": None, "source_key": "s"}
        insert(self.db, "import_record", skipped)
        self.assert_row_refused("import_record", {**skipped, "source_key": "t", "entity_id": self.company}, "CHECK")
        self.assert_row_refused("import_record", {**self.base_row("import_record"), "source_key": "u",
                                                  "entity": None, "entity_id": None}, "CHECK")

    def test_import_record_is_still_append_only_after_0004(self):
        import_id = insert(self.db, "import_record", {**self.base_row("import_record"), "source": "send_log"})
        self.assert_refused("UPDATE import_record SET reason = 'other' WHERE import_id = ?", (import_id,),
                            "append-only")
        self.assert_refused("DELETE FROM import_record WHERE import_id = ?", (import_id,), "append-only")
        self.assertEqual(self.db.execute("SELECT count(*) FROM import_record").fetchone()[0], 1)
        index = self.db.execute("SELECT sql FROM sqlite_master WHERE name = 'import_record_run'").fetchone()
        self.assertIn("run_id, source, outcome", index[0])

    # ---- review, linkedin, sender ---------------------------------------

    def test_review_item_is_raised_once_per_evidence(self):
        self.review(evidence_key="run-1")
        with self.assertRaises(sqlite3.IntegrityError) as cm:
            self.review(evidence_key="run-1")
        self.assertIn("UNIQUE", str(cm.exception))
        self.review(evidence_key="run-2")

    def test_review_item_can_clear_itself(self):
        review_id = self.review()
        self.db.execute(
            "UPDATE review_item SET state = 'resolved', resolution = 'cleared', resolved_at = ? "
            "WHERE review_id = ?", (NOW, review_id))

    def test_linkedin_undo_is_a_new_row(self):
        entry = insert(self.db, "linkedin_log", {
            "person_id": self.person, "kind": "request_sent", "at": NOW, "recorded_at": NOW})
        undo = {"person_id": self.person, "kind": "undo", "undoes": entry, "at": NOW, "recorded_at": NOW}
        undo_id = insert(self.db, "linkedin_log", undo)
        self.assert_row_refused("linkedin_log", undo, "UNIQUE")
        self.assert_refused("DELETE FROM linkedin_log WHERE entry_id = ?", (undo_id,), "append-only")

    def test_sender_row_exists_and_is_single(self):
        rows = self.db.execute("SELECT sender_id FROM sender").fetchall()
        self.assertEqual([r[0] for r in rows], [1])
        self.assert_row_refused("sender", {"sender_id": 2, "updated_at": NOW}, "CHECK")

    # ---- hardening from the 2026-10-20 store audit ------------------------

    def test_posting_bodies_live_in_their_own_table(self):
        # A body inline in posting pushed closed_at onto overflow pages: the #6 join took 2.5 s at 300k postings.
        columns = {r["name"] for r in self.db.execute("PRAGMA table_info(posting)")}
        self.assertNotIn("body", columns)
        posting = seed_posting(self.db, self.company, "Software Engineer", body="We sponsor H-1B visas.")
        body = self.db.execute("SELECT body FROM posting_body WHERE posting_id = ?", (posting,)).fetchone()[0]
        self.assertEqual(body, "We sponsor H-1B visas.")
        self.assert_refused("INSERT INTO posting_body(posting_id, body) VALUES (999999, 'x')", (), "FOREIGN KEY")

    def test_a_claimed_or_sent_message_is_never_deleted(self):
        for state in ("sent", "send_unknown", "sending"):  # "sending" last: only one row may be sending
            message_id = self.message_in(state)
            self.assert_refused("DELETE FROM message WHERE message_id = ?", (message_id,), "never deleted")
        drafted = self.message_in("drafted")
        self.db.execute("DELETE FROM message WHERE message_id = ?", (drafted,))

    def test_insert_or_replace_cannot_erase_protected_rows(self):
        sent = self.message_in("sent")
        touch = self.db.execute("SELECT touch_id FROM message WHERE message_id = ?", (sent,)).fetchone()[0]
        self.assert_refused(
            "INSERT OR REPLACE INTO message(touch_id, seq, state, subject, body, created_at, updated_at) "
            "VALUES (?, 0, 'drafted', 's', 'b', ?, ?)", (touch, NOW, NOW), "never deleted")
        event = insert(self.db, "event", {"entity": "company", "entity_id": self.company, "kind": "note",
                                          "actor": "devanshu", "at": NOW, "recorded_at": NOW})
        self.assert_refused(
            "INSERT OR REPLACE INTO event(event_id, entity, entity_id, kind, actor, at, recorded_at) "
            "VALUES (?, 'company', ?, 'note', 'devanshu', ?, ?)", (event, self.company, NOW, NOW), "append-only")
        self.assert_refused(
            "INSERT OR REPLACE INTO posting(posting_id, company_id, source_key, title, first_seen_at, last_seen_at) "
            "VALUES (?, ?, 'url:x', 'T', ?, ?)", (self.posting, self.company, NOW, NOW))

    def test_replace_cannot_erase_a_sent_message_without_recursive_triggers(self):
        # The sqlite3 shell, a DB browser or a future script opens the store without recursive_triggers, so the
        # delete triggers do not fire for REPLACE there. The INSERT and UPDATE guards must hold anyway.
        sent = self.message_in("sent")
        touch, rfc = self.db.execute("SELECT touch_id, rfc_message_id FROM message WHERE message_id = ?",
                                     (sent,)).fetchone()
        sending = self.message_in("sending")
        follow_up = self.new_message(touch, seq=1, state="planned")
        approved = self.message_in("approved")
        other = self.posting
        second = seed_posting(self.db, self.company, "Backend Engineer")
        second_key = self.db.execute("SELECT source_key FROM posting WHERE posting_id = ?", (second,)).fetchone()[0]
        path = self.db.execute("PRAGMA database_list").fetchone()["file"]
        plain = sqlite3.connect(path, isolation_level=None)
        self.addCleanup(plain.close)
        self.assertEqual(plain.execute("PRAGMA recursive_triggers").fetchone()[0], 0)
        refused = [
            ("INSERT OR REPLACE INTO message(touch_id, seq, state, subject, body, created_at, updated_at) "
             "VALUES (?, 0, 'drafted', 's', 'b', ?, ?)", (touch, NOW, NOW)),
            ("REPLACE INTO message(message_id, touch_id, seq, state, subject, body, created_at, updated_at) "
             "VALUES (?, ?, 0, 'drafted', 's', 'b', ?, ?)", (sent, self.fresh_touch(), NOW, NOW)),
            ("INSERT OR REPLACE INTO message(touch_id, seq, state, subject, body, rfc_message_id, sent_at, "
             "approved_at, created_at, updated_at) VALUES (?, 0, 'sent', 's', 'b', ?, ?, ?, ?, ?)",
             (self.fresh_touch(), rfc, NOW, NOW, NOW, NOW)),
            ("UPDATE OR REPLACE message SET seq = 0 WHERE message_id = ?", (follow_up,)),
            ("UPDATE OR REPLACE message SET message_id = ? WHERE message_id = ?", (sending, follow_up)),
            ("INSERT OR REPLACE INTO posting(posting_id, company_id, source_key, title, first_seen_at, last_seen_at) "
             "VALUES (?, ?, 'url:x', 'T', ?, ?)", (other, self.company, NOW, NOW)),
            ("UPDATE OR REPLACE message SET state = 'sending', pending_rfc_id = '<p9@acme.co>', claimed_at = ? "
             "WHERE message_id = ?", (NOW, approved)),
            ("UPDATE OR REPLACE posting SET source_key = ? WHERE posting_id = ?", (second_key, other)),
            ("UPDATE OR REPLACE posting SET posting_id = ? WHERE posting_id = ?", (second, other)),
        ]
        for sql, params in refused:
            with self.subTest(sql=sql):
                with self.assertRaises(sqlite3.IntegrityError):
                    plain.execute(sql, params)
        for message_id in (sent, sending, approved):
            self.assertIsNotNone(self.db.execute("SELECT 1 FROM message WHERE message_id = ?", (message_id,)).fetchone())
        for posting_id in (other, second):
            self.assertIsNotNone(self.db.execute("SELECT 1 FROM posting WHERE posting_id = ?", (posting_id,)).fetchone())

    def test_the_replace_guards_leave_ordinary_writes_alone(self):
        # A drafted message can still be replaced, renumbered or moved, and a posting's own key can be rewritten.
        drafted = self.message_in("drafted")
        touch = self.db.execute("SELECT touch_id FROM message WHERE message_id = ?", (drafted,)).fetchone()[0]
        self.db.execute("INSERT OR REPLACE INTO message(touch_id, seq, state, subject, body, created_at, updated_at) "
                        "VALUES (?, 0, 'drafted', 's2', 'b2', ?, ?)", (touch, NOW, NOW))
        self.db.execute("UPDATE message SET subject = 'x' WHERE touch_id = ? AND seq = 0", (touch,))
        key = self.db.execute("SELECT source_key FROM posting WHERE posting_id = ?", (self.posting,)).fetchone()[0]
        self.db.execute("UPDATE posting SET source_key = ? WHERE posting_id = ?", (key, self.posting))
        # the crawler's upsert on source_key (#21) still works
        self.db.execute("INSERT INTO posting(company_id, source_key, title, first_seen_at, last_seen_at) "
                        "VALUES (?, ?, 'New title', ?, ?) ON CONFLICT(source_key) DO UPDATE SET title = excluded.title",
                        (self.company, key, NOW, NOW))
        self.assertEqual(self.db.execute("SELECT title FROM posting WHERE posting_id = ?", (self.posting,)).fetchone()[0],
                         "New title")

    def test_connect_sets_the_audit_pragmas(self):
        self.assertEqual(self.db.execute("PRAGMA recursive_triggers").fetchone()[0], 1)
        self.assertEqual(self.db.execute("PRAGMA cache_size").fetchone()[0], -65536)
        self.assertEqual(self.db.execute("PRAGMA fullfsync").fetchone()[0], 0)

    def test_the_sender_connection_can_ask_for_fullfsync(self):
        from outreach.store import connect
        path = self.db.execute("PRAGMA database_list").fetchone()["file"]
        durable = connect(path, fullfsync=True)
        self.addCleanup(durable.close)
        self.assertEqual(durable.execute("PRAGMA fullfsync").fetchone()[0], 1)
        self.assertEqual(durable.execute("PRAGMA checkpoint_fullfsync").fetchone()[0], 1)

    def test_audit_indexes_exist(self):
        names = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        for name in ("no_go_company_any", "no_go_person_any", "posting_board_open", "company_held",
                     "review_item_company", "review_item_board", "review_item_posting", "daily_entry_company"):
            self.assertIn(name, names)
        self.assertNotIn("posting_board", names)

    def plan(self, sql, params=()):
        return " | ".join(r["detail"] for r in self.db.execute("EXPLAIN QUERY PLAN " + sql, params))

    def test_joined_views_do_not_scan_whole_tables(self):
        # The window-function views were materialised in full for every join (company page 1.1 s at 300k postings).
        decisions = self.plan("SELECT * FROM posting p JOIN posting_decision pd ON pd.posting_id = p.posting_id "
                              "WHERE p.company_id = ?", (self.company,))
        self.assertNotRegex(decisions, r"SCAN (d|decision)\b")
        facts = self.plan("SELECT * FROM company c JOIN company_fact f ON f.company_id = c.company_id "
                          "AND f.fact = 'size' WHERE c.company_id = ?", (self.company,))
        self.assertNotRegex(facts, r"SCAN (e|evidence)\b")


    # ---- the opportunity pipeline (#30 Task 1) ----------------------------

    def board_row(self, **over):
        row = {"company_id": self.company, "ats": "teamtailor", "token": f"pipe{next(_n)}", "created_at": NOW}
        row.update(over)
        return row

    def test_board_accepts_unchanged_read_and_new_ats(self):
        insert(self.db, "board", self.board_row(last_read="unchanged", last_read_at=NOW, streak_since=NOW))
        for ats in ("pinpoint", "teamtailor", "jazzhr", "paycom", "icims", "personio", "join_com", "cornerstone",
                    "successfactors"):
            with self.subTest(ats=ats):
                insert(self.db, "board", self.board_row(ats=ats))
        # An unchanged read clears both streaks, as ok does.
        for streaks in ({"empty_streak": 1}, {"error_streak": 1}):
            with self.subTest(streaks=streaks):
                self.assert_row_refused("board", self.board_row(
                    last_read="unchanged", last_read_at=NOW, streak_since=NOW, **streaks), "CHECK")
        self.assert_row_refused("board", self.board_row(ats="taleo"), "CHECK")
        insert(self.db, "board_read", {"board_id": self.board, "run_id": self.run, "at": NOW, "result": "unchanged"})
        self.assert_row_refused("board_read", {"board_id": self.new_board(), "run_id": self.run, "at": NOW,
                                               "result": "same"}, "CHECK")

    def test_board_read_validators_and_schedule(self):
        board_id = insert(self.db, "board", self.board_row(etag='W/"abc"', ids_fingerprint="f1", next_read_at=NOW,
                                                           change_interval_s=3600))
        row = self.db.execute("SELECT etag, ids_fingerprint, next_read_at, change_interval_s FROM board "
                              "WHERE board_id = ?", (board_id,)).fetchone()
        self.assertEqual(tuple(row), ('W/"abc"', "f1", NOW, 3600))
        fresh = insert(self.db, "board", self.board_row())
        row = self.db.execute("SELECT etag, ids_fingerprint, next_read_at, change_interval_s FROM board "
                              "WHERE board_id = ?", (fresh,)).fetchone()
        self.assertEqual(tuple(row), ("", "", None, None))  # NULL next_read_at means due now
        for bad in (0, -5):
            with self.subTest(change_interval_s=bad):
                self.assert_row_refused("board", self.board_row(change_interval_s=bad), "change_interval_s")

    def test_board_found_by_check(self):
        self.assert_row_refused("board", self.board_row(found_by="bogus"), "found_by")
        default = insert(self.db, "board", self.board_row())
        self.assertEqual(self.db.execute("SELECT found_by FROM board WHERE board_id = ?", (default,)).fetchone()[0],
                         "import")
        for how in ("stapply_name", "slug_probe", "careers_page", "import", "hand"):
            with self.subTest(found_by=how):
                insert(self.db, "board", self.board_row(found_by=how))

    def test_company_alias_takes_the_identity_sources(self):
        for source in ("fein", "dba", "legal_name", "ats", "formd", "linkedin"):
            with self.subTest(source=source):
                insert(self.db, "company_alias", {"alias_key": f"key-{source}", "company_id": self.company,
                                                  "alias": source, "source": source, "created_at": NOW})
        self.assert_row_refused("company_alias", {"alias_key": "key-x", "company_id": self.company, "alias": "x",
                                                  "source": "guess", "created_at": NOW}, "CHECK")

    def test_shared_key_is_unique_per_kind_value(self):
        sql = ("INSERT INTO shared_key(kind, value, reason, recorded_at) VALUES (?, ?, ?, ?) "
               "ON CONFLICT DO NOTHING")
        self.db.execute(sql, ("fein", "12-3456789", "payroll provider files for many clients", NOW))
        self.db.execute(sql, ("fein", "12-3456789", "seen again", at(13)))
        rows = self.db.execute("SELECT reason, recorded_at FROM shared_key").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("payroll provider files for many clients", NOW)])
        # The same value as a domain is another key.
        self.db.execute(sql, ("domain", "12-3456789", "r", NOW))
        self.assert_row_refused("shared_key", {"kind": "ein", "value": "1", "reason": "r", "recorded_at": NOW},
                                "CHECK")
        self.assert_row_refused("shared_key", {"kind": "domain", "value": "Gusto.com", "reason": "r",
                                               "recorded_at": NOW}, "CHECK")
        self.assert_row_refused("shared_key", {"kind": "domain", "value": "gusto.com", "reason": " ",
                                               "recorded_at": NOW}, "CHECK")

    def test_evidence_lca_value_must_be_json(self):
        lca = {"fact": "lca", "num_low": None, "num_high": None, "confidence": "confirmed", "source_kind": "lca"}
        self.assert_row_refused("evidence", {**self.base_row("evidence"), **lca, "value": "x"}, "CHECK")
        missing = json.dumps({"fy": 2025, "software": 5, "jr": 3, "level1": 2})  # no newemp_jr
        self.assert_row_refused("evidence", {**self.base_row("evidence"), **lca, "value": missing}, "CHECK")
        text = json.dumps({"fy": "2025", "software": 5, "jr": 3, "level1": 2, "newemp_jr": 2})
        self.assert_row_refused("evidence", {**self.base_row("evidence"), **lca, "value": text}, "CHECK")
        wage = json.dumps({**json.loads(LCA), "median_jr_wage": "lots"})
        self.assert_row_refused("evidence", {**self.base_row("evidence"), **lca, "value": wage}, "CHECK")
        self.assert_row_refused("evidence", {**self.base_row("evidence"), **lca, "value": "[1, 2]"}, "CHECK")
        insert(self.db, "evidence", {**self.base_row("evidence"), **lca, "value": LCA})
        insert(self.db, "evidence", {**self.base_row("evidence"), **lca,
                                     "value": json.dumps({**json.loads(LCA), "median_jr_wage": 120000})})

    def test_evidence_lca_and_funding_numbers_cannot_be_negative(self):
        lca = {**self.base_row("evidence"), "fact": "lca", "num_low": None, "num_high": None,
               "confidence": "confirmed", "source_kind": "lca"}
        for key in ("fy", "software", "jr", "level1", "newemp_jr", "median_jr_wage"):
            with self.subTest(key=key):
                value = json.dumps({**json.loads(LCA), "median_jr_wage": 1, key: -1})
                self.assert_row_refused("evidence", {**lca, "value": value}, "CHECK")
        insert(self.db, "evidence", {**lca, "value": json.dumps({**json.loads(LCA), "jr": 0, "median_jr_wage": 0})})
        funding = {**self.base_row("evidence"), "fact": "funding", "num_low": None, "num_high": None,
                   "source_kind": "formd"}
        self.assert_row_refused("evidence", {**funding, "value": json.dumps(
            {"amount_usd": -1, "filed_on": "2026-08-14", "kind": "form_d"})}, "CHECK")
        for amount in (0, None):
            insert(self.db, "evidence", {**funding, "value": json.dumps(
                {"amount_usd": amount, "filed_on": "2026-08-14", "kind": "form_d"})})

    def test_evidence_domain_must_be_a_bare_lower_case_host(self):
        base = {**self.base_row("evidence"), "num_low": None, "num_high": None, "fact": "domain"}
        for value in ("acme.com", "careers.acme.co.uk", "acme-2.io"):
            with self.subTest(value=value):
                insert(self.db, "evidence", {**base, "value": value})
        for value in ("https://acme.com", "acme.com/careers", "acme.com/", "acme.com:8080", "http:acme.com",
                      "acme .com", "acme.com ", "\tacme.com", "acme.com\n", "acme\u00a0.com",
                      ".acme.com", "acme.com.", "a@acme.com", "Acme.com", "acme", ""):
            with self.subTest(value=value):
                self.assert_row_refused("evidence", {**base, "value": value}, "CHECK")

    def test_evidence_takes_the_pipeline_facts_and_sources(self):
        base = {**self.base_row("evidence"), "num_low": None, "num_high": None}
        good = [("cap_exempt", v) for v in ("likely", "confirmed", "no")] + \
            [("staffing_risk", v) for v in ("low", "medium", "high")] + \
            [("funding", json.dumps({"amount_usd": 4500000, "filed_on": "2026-08-14", "kind": "form_d"})),
             ("funding", json.dumps({"amount_usd": None, "filed_on": "2026-08-14", "kind": "form_d_amendment"}))]
        for fact, value in good:
            with self.subTest(fact=fact, value=value):
                insert(self.db, "evidence", {**base, "fact": fact, "value": value})
        bad = [("cap_exempt", "maybe"), ("staffing_risk", "none"), ("funding", "4.5M"),
               ("funding", json.dumps({"amount_usd": 1, "kind": "form_d"})),
               ("funding", json.dumps({"filed_on": "2026-08-14", "kind": "form_d"})),
               ("funding", json.dumps({"amount_usd": "1", "filed_on": "2026-08-14", "kind": "form_d"})),
               ("funding", json.dumps({"amount_usd": 1, "filed_on": "2026-08-14T00:00:00Z", "kind": "form_d"})),
               ("funding", json.dumps({"amount_usd": 1, "filed_on": "2026-08-14", "kind": ""}))]
        for fact, value in bad:
            with self.subTest(fact=fact, value=value):
                self.assert_row_refused("evidence", {**base, "fact": fact, "value": value}, "CHECK")
        # headcount uses num_low and num_high like size; no other new fact carries numbers.
        self.assert_row_refused("evidence", {**base, "fact": "headcount", "value": "40"}, "CHECK")
        insert(self.db, "evidence", {**base, "fact": "headcount", "value": "40-60", "num_low": 40, "num_high": 60})
        self.assert_row_refused("evidence", {**base, "fact": "cap_exempt", "value": "likely", "num_low": 1},
                                "CHECK")
        for kind in ("stapply", "commoncrawl", "hn", "simplify"):
            with self.subTest(source_kind=kind):
                insert(self.db, "evidence", {**base, "fact": "hq", "value": f"Boston, MA {kind}", "source_kind": kind})

    def test_read_queue_lease_states(self):
        row = {**self.base_row("read_queue"), "state": "leased", "lease_until": NOW}
        insert(self.db, "read_queue", row)
        state = self.db.execute("SELECT state, attempts, last_error FROM read_queue").fetchone()
        self.assertEqual(tuple(state), ("leased", 0, ""))
        self.assert_row_refused("read_queue", {**row, "reader_version": "rv-2", "state": "bogus"}, "CHECK")
        self.assert_row_refused("read_queue", {**row, "reader_version": "rv-3", "lease_until": None}, "CHECK")
        for state in ("queued", "done", "refused"):
            with self.subTest(state=state):
                insert(self.db, "read_queue", {**row, "reader_version": f"rv-{state}", "state": state,
                                               "lease_until": None})
        self.assert_row_refused("read_queue", {**row, "reader_version": "rv-9", "body_sha256": "abc"}, "CHECK")
        self.assert_row_refused("read_queue", {**row, "reader_version": "rv-9", "body_sha256": "G" * 64}, "CHECK")
        # one row per posting, body and reader version
        self.assert_row_refused("read_queue", row, "UNIQUE")

    def test_posting_reading_fields_must_be_json(self):
        row = self.base_row("posting_reading")
        self.assert_row_refused("posting_reading", {**row, "fields": "not json"}, "CHECK")
        self.assert_row_refused("posting_reading", {**row, "verified": 2}, "CHECK")
        self.assert_row_refused("posting_reading", {**row, "refused_fields": "{}"}, "CHECK")
        insert(self.db, "posting_reading", row)
        self.assertEqual(self.db.execute("SELECT refused_fields FROM posting_reading").fetchone()[0], "[]")
        self.assert_row_refused("posting_reading", row, "UNIQUE")

    def test_decision_defaults_provisional(self):
        self.decision(self.posting, self.ruleset_a)
        row = self.db.execute("SELECT provisional, reader_version FROM decision").fetchone()
        self.assertEqual(tuple(row), (1, None))
        later = seed_posting(self.db, self.company, "Backend Engineer")
        self.assertRaises(sqlite3.IntegrityError, self.decision, later, self.ruleset_a, provisional=2)
        # reader_version is NULL while provisional; a final row may have one (or none, when the body was unreadable).
        self.assertRaises(sqlite3.IntegrityError, self.decision, later, self.ruleset_a, provisional=1,
                          reader_version="rv-1")
        self.decision(later, self.ruleset_a, provisional=0, reader_version="rv-1")
        self.decision(seed_posting(self.db, self.company, "Data Engineer"), self.ruleset_a, provisional=0)
        self.make_current(self.ruleset_a)
        chosen = self.db.execute("SELECT provisional, reader_version FROM posting_decision WHERE posting_id = ?",
                                 (later,)).fetchone()
        self.assertEqual(tuple(chosen), (0, "rv-1"))

    def test_outreach_touch_outcome_not_before_sent(self):
        before = "2026-10-19T11:59:59Z"
        for column in ("accepted_at", "replied_at", "referred_at", "interview_at", "offer_at", "quiet_at"):
            with self.subTest(column=column):
                self.assert_row_refused("outreach_touch", {**self.base_row("outreach_touch"),
                                                           "replied_at": None, column: before}, "CHECK")
        insert(self.db, "outreach_touch", {**self.base_row("outreach_touch"), "replied_at": NOW,
                                           "reply_kind": "interested"})

    def drop_touch_replace_guards(self):
        """The insert and update guards fire before the UNIQUE checks, so a test of a unique key itself drops them
        on its own scratch store; the guards have their own tests."""
        for kind in ("", "key_", "email_"):
            for verb in ("insert", "update"):
                self.db.execute(f"DROP TRIGGER outreach_touch_{kind}no_replace_{verb}")

    def test_outreach_touch_idempotency_key_unique(self):
        self.new_otouch(idempotency_key="click-1")
        # the guard refuses a repeated key first (log_touch looks the key up instead), and says which key
        with self.assertRaises(sqlite3.IntegrityError) as cm:
            self.new_otouch(idempotency_key="click-1")
        self.assertIn("idempotency_key", str(cm.exception))
        self.assertNotIn("never deleted", str(cm.exception))
        self.drop_touch_replace_guards()
        with self.assertRaises(sqlite3.IntegrityError) as cm:
            self.new_otouch(idempotency_key="click-1")
        self.assertIn("UNIQUE", str(cm.exception))
        other = self.new_otouch(idempotency_key="click-2")
        with self.assertRaises(sqlite3.IntegrityError) as cm:
            self.db.execute("UPDATE outreach_touch SET idempotency_key = 'click-1' WHERE touch_id = ?", (other,))
        self.assertIn("UNIQUE", str(cm.exception))
        self.assertEqual(self.db.execute(
            "SELECT count(*) FROM outreach_touch WHERE idempotency_key = 'click-1'").fetchone()[0], 1)

    def test_outreach_touch_email_needs_email_channel(self):
        self.assert_row_refused("outreach_touch", {**self.base_row("outreach_touch"), "channel": "linkedin_request",
                                                   "email_touch_id": self.touch}, "CHECK")
        self.new_otouch(channel="email", email_touch_id=self.touch)
        self.assertRaises(sqlite3.IntegrityError, self.new_otouch, channel="email", email_touch_id=999999)
        self.assertRaises(sqlite3.IntegrityError, self.new_otouch, channel="fax")

    def test_an_outreach_touch_is_never_deleted(self):
        touch = self.new_otouch(idempotency_key="keep-1")
        self.assert_refused("DELETE FROM outreach_touch WHERE touch_id = ?", (touch,), "never deleted")
        undone = self.new_otouch(undone_at=NOW)
        self.assert_refused("DELETE FROM outreach_touch WHERE touch_id = ?", (undone,), "never deleted")
        self.assertEqual(self.db.execute("SELECT count(*) FROM outreach_touch WHERE touch_id IN (?, ?)",
                                         (touch, undone)).fetchone()[0], 2)

    def test_replace_cannot_overwrite_an_outreach_touch_without_recursive_triggers(self):
        touch = self.new_otouch(idempotency_key="keep-2")
        self.new_otouch(channel="email", email_touch_id=self.touch)
        spare = self.new_otouch(channel="email")
        path = self.db.execute("PRAGMA database_list").fetchone()["file"]
        plain = sqlite3.connect(path, isolation_level=None)
        self.addCleanup(plain.close)
        self.assertEqual(plain.execute("PRAGMA recursive_triggers").fetchone()[0], 0)
        cols = "company_id, channel, sent_at, context, created_at"
        for sql, params in [
            (f"INSERT OR REPLACE INTO outreach_touch(touch_id, person_id, {cols}) "
             "VALUES (?, ?, ?, 'linkedin_request', ?, ?, ?)", (touch, self.person, self.company, NOW, CONTEXT, NOW)),
            (f"INSERT OR REPLACE INTO outreach_touch(person_id, {cols}, idempotency_key) "
             "VALUES (?, ?, 'linkedin_request', ?, ?, ?, 'keep-2')",
             (self.person, self.company, NOW, CONTEXT, NOW)),
            (f"INSERT OR REPLACE INTO outreach_touch(person_id, {cols}, email_touch_id) "
             "VALUES (?, ?, 'email', ?, ?, ?, ?)", (self.person, self.company, NOW, CONTEXT, NOW, self.touch)),
            ("UPDATE OR REPLACE outreach_touch SET idempotency_key = 'keep-2' WHERE touch_id = ?", (spare,)),
            ("UPDATE OR REPLACE outreach_touch SET touch_id = ? WHERE touch_id = ?", (touch, spare)),
            ("UPDATE OR REPLACE outreach_touch SET email_touch_id = ? WHERE touch_id = ?", (self.touch, spare)),
        ]:
            with self.subTest(sql=sql):
                with self.assertRaises(sqlite3.IntegrityError):
                    plain.execute(sql, params)
        self.assertEqual(self.db.execute("SELECT count(*) FROM outreach_touch WHERE touch_id = ? "
                                         "AND idempotency_key = 'keep-2'", (touch,)).fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM outreach_touch WHERE email_touch_id = ?",
                                         (self.touch,)).fetchone()[0], 1)
        self.assertIsNotNone(self.db.execute("SELECT 1 FROM outreach_touch WHERE touch_id = ?", (spare,)).fetchone())
        for column, value, text in (("idempotency_key", "keep-2", "idempotency_key"),
                                    ("email_touch_id", self.touch, "email_touch_id")):
            with self.subTest(update=column):
                with self.assertRaises(sqlite3.IntegrityError) as cm:
                    self.db.execute(f"UPDATE outreach_touch SET {column} = ? WHERE touch_id = ?", (value, spare))
                self.assertIn(text, str(cm.exception))
        with self.assertRaises(sqlite3.IntegrityError) as cm:
            self.db.execute("UPDATE outreach_touch SET touch_id = ? WHERE touch_id = ?", (touch, spare))
        self.assertIn("never deleted", str(cm.exception))
        # an ordinary insert of a new touch is untouched by the guard
        self.new_otouch(idempotency_key="fresh")

    def test_one_email_touch_is_counted_once(self):
        self.new_otouch(channel="email", email_touch_id=self.touch)
        with self.assertRaises(sqlite3.IntegrityError) as cm:
            self.new_otouch(channel="email", email_touch_id=self.touch)
        self.assertIn("email_touch_id", str(cm.exception))
        self.assertNotIn("never deleted", str(cm.exception))
        self.drop_touch_replace_guards()  # so the index itself is what refuses the second
        with self.assertRaises(sqlite3.IntegrityError) as cm:
            self.new_otouch(channel="email", email_touch_id=self.touch)
        self.assertIn("UNIQUE", str(cm.exception))
        # touches with no email touch are not limited
        self.new_otouch()
        self.new_otouch()

    def test_outreach_touch_undone_excludes_outcomes(self):
        self.assertRaises(sqlite3.IntegrityError, self.new_otouch, undone_at=NOW, accepted_at=NOW)
        for column in ("replied_at", "referred_at", "interview_at", "offer_at", "quiet_at"):
            with self.subTest(column=column):
                self.assertRaises(sqlite3.IntegrityError, self.new_otouch, undone_at=NOW, **{column: NOW})
        undone = self.new_otouch(undone_at=NOW)
        self.assert_refused("UPDATE outreach_touch SET accepted_at = ? WHERE touch_id = ?", (NOW, undone), "CHECK")

    def test_outreach_touch_quiet_reply_person_and_context(self):
        self.assertRaises(sqlite3.IntegrityError, self.new_otouch, quiet_at=NOW, replied_at=NOW)
        self.assertRaises(sqlite3.IntegrityError, self.new_otouch, reply_kind="interested")  # a kind needs a reply
        self.assertRaises(sqlite3.IntegrityError, self.new_otouch, replied_at=NOW, reply_kind="maybe")
        # an out-of-office is not an outcome: it lives on touch.auto_reply_at, never here
        self.assertRaises(sqlite3.IntegrityError, self.new_otouch, replied_at=NOW, reply_kind="auto_reply")
        # person_id is NULL only for an application.
        self.assertRaises(sqlite3.IntegrityError, self.new_otouch, person_id=None)
        self.new_otouch(person_id=None, channel="application")
        for channel in ("linkedin_message", "hn_reply", "other"):
            self.new_otouch(channel=channel)
        context = json.loads(CONTEXT)
        bad_contexts = ["not json", "[]", json.dumps({**context, "source": "import"}),
                        json.dumps({k: v for k, v in context.items() if k != "roleNamed"}),
                        json.dumps({**context, "signals": [1]}), json.dumps({**context, "roleNamed": 1})]
        for bad in bad_contexts:
            with self.subTest(context=bad):
                self.assertRaises(sqlite3.IntegrityError, self.new_otouch, context=bad)
        # Values may be JSON null where build_context has none (no posting, no ranker run yet).
        self.new_otouch(context=json.dumps({**context, "source": "own", "roleKind": None, "roleAgeDays": None,
                                            "rankerVersion": None}))
        row = self.db.execute("SELECT revision, undone_at FROM outreach_touch WHERE touch_id = ?",
                              (self.otouch,)).fetchone()
        self.assertEqual(tuple(row), (1, None))

    def test_work_tier_one_row_per_company_and_tier_4_refused(self):
        row = self.base_row("work_tier")
        self.assert_row_refused("work_tier", {**row, "tier": 4}, "CHECK")
        self.assert_row_refused("work_tier", {**row, "promoted": 2}, "CHECK")
        insert(self.db, "work_tier", row)
        self.assert_row_refused("work_tier", {**row, "tier": 3}, "UNIQUE")
        stored = self.db.execute("SELECT promoted, source_version FROM work_tier").fetchone()
        self.assertEqual(tuple(stored), (0, ""))

    def test_posting_body_hash_must_be_64_chars_or_empty(self):
        self.assert_refused("INSERT INTO posting_body(posting_id, body, body_sha256) VALUES (?, 'b', ?)",
                            (self.posting, "a" * 10), "CHECK")
        self.assert_refused("INSERT INTO posting_body(posting_id, body, body_sha256) VALUES (?, 'b', ?)",
                            (self.posting, "Z" * 64), "CHECK")
        self.db.execute("INSERT INTO posting_body(posting_id, body) VALUES (?, 'b')", (self.posting,))
        row = self.db.execute("SELECT body_sha256, fetched_at FROM posting_body WHERE posting_id = ?",
                              (self.posting,)).fetchone()
        self.assertEqual(tuple(row), ("", None))
        self.db.execute("UPDATE posting_body SET body_sha256 = ?, fetched_at = ? WHERE posting_id = ?",
                        (SHA, NOW, self.posting))

    def test_source_and_company_source(self):
        self.assert_row_refused("source", {"source_name": "hn", "version": ""}, "CHECK")
        insert(self.db, "source", {"source_name": "hn", "version": "hn-1", "last_run_id": self.run})
        self.assert_row_refused("source", {"source_name": "formd", "version": "1", "last_run_id": 999999},
                                "FOREIGN KEY")
        row = self.base_row("company_source")
        insert(self.db, "company_source", row)
        self.db.execute("INSERT INTO company_source(company_id, source_name, source_ref, first_seen_at, last_seen_at) "
                        "VALUES (?, 'lca', 'lca:acme', ?, ?) ON CONFLICT DO NOTHING", (self.company, NOW, NOW))
        self.assertEqual(self.db.execute("SELECT count(*) FROM company_source").fetchone()[0], 1)
        self.assert_row_refused("company_source", {**row, "source_name": "nobody"}, "FOREIGN KEY")
        self.assert_row_refused("company_source", {**row, "source_ref": "lca:other", "last_seen_at": at(11)},
                                "CHECK")

    def test_watched_page_one_per_company(self):
        row = self.base_row("watched_page")
        self.assert_row_refused("watched_page", {**row, "url": "acme.example/careers"}, "CHECK")
        self.assert_row_refused("watched_page", {**row, "error_streak": -1}, "CHECK")
        insert(self.db, "watched_page", row)
        self.assertEqual(tuple(self.db.execute("SELECT fingerprint, error_streak FROM watched_page").fetchone()),
                         ("", 0))
        self.assert_row_refused("watched_page", row, "UNIQUE")

    def test_company_post_unique_per_source_and_url(self):
        row = self.base_row("company_post")
        insert(self.db, "company_post", row)
        self.db.execute("INSERT INTO company_post(company_id, source_name, url) VALUES (?, 'lca', ?) "
                        "ON CONFLICT DO NOTHING", (self.company, row["url"]))
        self.assertEqual(self.db.execute("SELECT count(*), max(asks_email), max(text) FROM company_post")
                         .fetchone()[:], (1, 0, ""))
        self.assert_row_refused("company_post", {**row, "url": "https://hn.example/2", "asks_email": 2}, "CHECK")

    def test_company_score_and_application(self):
        row = self.base_row("company_score")
        self.assert_row_refused("company_score", {**row, "signals": "nope"}, "CHECK")
        self.assert_row_refused("company_score", {**row, "lanes": "nope"}, "CHECK")
        insert(self.db, "company_score", row)
        self.assert_row_refused("company_score", row, "UNIQUE")
        insert(self.db, "company_score", {**row, "ranker_version": "k-2"})
        app = {"company_id": self.company, "applied_on": TODAY}
        app_id = insert(self.db, "application", app)
        self.assertEqual(tuple(self.db.execute("SELECT outcome, url, revision FROM application WHERE "
                                               "application_id = ?", (app_id,)).fetchone()), ("open", "", 1))
        for outcome in ("screen", "interview", "offer", "rejected", "closed"):
            with self.subTest(outcome=outcome):
                insert(self.db, "application", {**app, "outcome": outcome, "outcome_at": NOW})
        self.assert_row_refused("application", {**app, "outcome": "ghosted", "outcome_at": NOW}, "CHECK")
        self.assert_row_refused("application", {**app, "outcome": "offer"}, "CHECK")  # an outcome has a date

    def test_new_job_and_review_kinds_insert(self):
        for job in ("discover", "resolve", "find_boards", "read_boards", "watch_pages", "read_postings", "rank",
                    "plan", "prepare"):
            with self.subTest(job=job):
                insert(self.db, "job_run", {"job": job, "started_at": NOW})
        for kind in ("merge_candidate", "reader_disagrees", "careers_changed", "size_check"):
            for raised_by in ("source", "reader", "ranker"):
                with self.subTest(kind=kind, raised_by=raised_by):
                    self.review(kind=kind, raised_by=raised_by, posting_id=self.posting,
                                evidence_key=f"{kind}-{raised_by}")
        self.assert_row_refused("job_run", {"job": "crawl_boards", "started_at": NOW}, "CHECK")
        for entity in ("source", "reading", "outreach_touch", "score"):
            for actor in ("source", "reader"):
                with self.subTest(entity=entity, actor=actor):
                    insert(self.db, "event", {"at": NOW, "recorded_at": NOW, "entity": entity, "kind": "k",
                                              "actor": actor})

    def test_reader_disagrees_needs_a_posting(self):
        self.assert_row_refused("review_item", {
            "kind": "reader_disagrees", "company_id": self.company, "reason": "r", "evidence_key": "rd-none",
            "raised_by": "reader", "created_at": NOW}, "CHECK")
        self.review(kind="reader_disagrees", raised_by="reader", posting_id=self.posting, evidence_key="rd-1")

    def test_daily_list_entry_person_and_channel(self):
        insert(self.db, "daily_list", {"list_date": "2026-10-03", "built_at": NOW, "capacity": 60})
        self.assert_row_refused("daily_list", {"list_date": "2026-10-04", "built_at": NOW, "capacity": 61}, "CHECK")
        entry = {"list_date": "2026-10-03", "company_id": self.company, "rank": 1, "proposed_at": NOW,
                 "list_group": "intern_junior", "person_id": self.person, "channel": "linkedin_request"}
        insert(self.db, "daily_list_entry", entry)
        other = seed_company(self.db, "Other")
        insert(self.db, "daily_list_entry", {**entry, "company_id": other, "rank": 2, "list_group": "reason_no_role",
                                             "person_id": None, "channel": None})
        self.assert_row_refused("daily_list_entry", {**entry, "company_id": seed_company(self.db, "Third"),
                                                     "rank": 3, "channel": "pigeon"}, "CHECK")

    # ---- the people plan's tables (#15 Task 1, copied here) ----------------

    def test_person_source_refuses_linkedin_hosts(self):
        row = self.base_row("person_source")
        for url in ("https://www.linkedin.com/in/x", "https://LinkedIn.com/in/x", "https://lnkd.in/abc"):
            with self.subTest(url=url):
                self.assert_row_refused("person_source", {**row, "url": url}, "CHECK")
        self.assert_row_refused("person_source", {**row, "url": "http://acme.example/team"}, "CHECK")
        insert(self.db, "person_source", {**row, "url": "https://acme.example/team"})

    def test_person_source_email_needs_basis_and_only_email_has_one(self):
        row = self.base_row("person_source")
        self.assert_row_refused("person_source", {**row, "kind": "email"}, "CHECK")
        self.assert_row_refused("person_source", {**row, "kind": "team", "email_basis": "published"}, "CHECK")
        self.assert_row_refused("person_source", {**row, "kind": "email", "email_basis": "guessed"}, "CHECK")
        for basis in ("published", "verified", "pattern"):
            insert(self.db, "person_source", {**row, "kind": "email", "email_basis": basis,
                                              "url": f"https://acme.example/{basis}"})

    def test_person_source_unique_per_person_kind_url(self):
        sql = ("INSERT INTO person_source(person_id, kind, url, label, recorded_at) VALUES (?, 'context', ?, ?, ?) "
               "ON CONFLICT DO NOTHING")
        self.db.execute(sql, (self.person, "https://acme.example/neu", "Northeastern", NOW))
        self.db.execute(sql, (self.person, "https://acme.example/neu", "again", NOW))
        self.assertEqual([r[0] for r in self.db.execute("SELECT label FROM person_source")], ["Northeastern"])

    def test_linkedin_draft_claude_cannot_confirm(self):
        row = self.base_row("linkedin_draft")
        for state in ("confirmed", "written"):
            with self.subTest(reason_state=state):
                self.assert_row_refused("linkedin_draft", {**row, "reason_state": state}, "CHECK")
        insert(self.db, "linkedin_draft", {**row, "saved_by": "devanshu", "reason_state": "written",
                                           "reason_line": "We were both at Northeastern."})
        self.assert_row_refused("linkedin_draft", {**row, "person_id": self.spare_person, "saved_by": "sender"},
                                "CHECK")

    def test_linkedin_draft_one_per_person_and_checks_must_be_json_array(self):
        row = self.base_row("linkedin_draft")
        for column in ("checks", "facts"):
            for bad in ("{}", "not json"):
                with self.subTest(column=column, value=bad):
                    self.assert_row_refused("linkedin_draft", {**row, column: bad}, "CHECK")
        self.assert_row_refused("linkedin_draft", {**row, "body": "  "}, "CHECK")
        insert(self.db, "linkedin_draft", row)
        stored = self.db.execute("SELECT reason_state, facts, checks, revision FROM linkedin_draft").fetchone()
        self.assertEqual(tuple(stored), ("proposed", "[]", "[]", 1))
        self.assert_row_refused("linkedin_draft", row, "UNIQUE")

    def test_daily_entry_person_needs_channel_and_is_unique_per_date(self):
        entry = {"list_date": "2026-10-01", "company_id": self.company, "list_group": "level_role", "rank": 1,
                 "proposed_at": NOW, "person_id": self.person}
        self.assert_row_refused("daily_list_entry", entry, "CHECK")
        insert(self.db, "daily_list_entry", {**entry, "channel": "email"})
        other = seed_company(self.db, "Other")
        self.assert_row_refused("daily_list_entry", {**entry, "company_id": other, "rank": 2, "channel": "email"},
                                "UNIQUE")
        insert(self.db, "daily_list", {"list_date": "2026-10-02", "built_at": NOW, "capacity": 5})
        insert(self.db, "daily_list_entry", {**entry, "list_date": "2026-10-02", "channel": "email"})

    # ---- the API's tables (#11 Task 4, copied here) ------------------------

    def test_api_tables_take_one_good_row_and_refuse_bad_ones(self):
        bad = {
            "api_request": [("status", 500), ("status", 199), ("request_id", "short"), ("request_id", "r" * 81),
                            ("fingerprint", "f" * 63), ("response", "not json")],
            "mx_check": [("domain", "Acme.co"), ("status", "unknown")],
            "win_seen": [("kind", "quiet"), ("touch_id", 999999)],
            "watch_dismissal": [("kind", "other")],
        }
        for table, cases in bad.items():
            for column, value in cases:
                with self.subTest(table=table, column=column, value=value):
                    self.assert_row_refused(table, {**self.base_row(table), column: value})
            insert(self.db, table, self.base_row(table))
            with self.subTest(table=table, repeat=True):
                self.assert_row_refused(table, self.base_row(table), "UNIQUE")
        mx_none = {**self.base_row("mx_check"), "domain": "nomail.example", "status": "none"}
        insert(self.db, "mx_check", mx_none)

    def test_pipeline_indexes_exist(self):
        names = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        for name in ("evidence_domain", "company_source_ref", "read_queue_next", "person_source_person",
                     "daily_entry_person"):
            self.assertIn(name, names)
        plan = self.plan("SELECT company_id FROM evidence WHERE fact = 'domain' AND value = ?", ("acme.co",))
        self.assertIn("evidence_domain", plan)
        plan = self.plan("SELECT company_id FROM company_source WHERE source_name = ? AND source_ref = ?",
                         ("lca", "lca:acme"))
        self.assertIn("company_source_ref", plan)
        plan = self.plan("SELECT posting_id FROM read_queue WHERE state = 'queued' "
                         "ORDER BY priority DESC, enqueued_at LIMIT 5")
        self.assertIn("read_queue_next", plan)
        self.assertNotIn("TEMP B-TREE", plan)

if __name__ == "__main__":
    unittest.main()
