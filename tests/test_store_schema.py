import itertools
import sqlite3
import unittest

from tests.storetest import fresh_store, seed_company, seed_posting, seed_touch

NOW = "2026-10-19T12:00:00Z"
TODAY = "2026-10-19"
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

    def message_in(self, state):
        """A message on its own touch, walked along legal steps to `state`."""
        touch_id = self.fresh_touch()
        if state == "planned":
            return self.new_message(touch_id, state="planned")
        message_id = self.new_message(touch_id)
        for step in PATH[state]:
            self.transition(message_id, step)
        return message_id

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
            "company": {"name": "Base", "status_at": NOW, "created_at": NOW},
            "company_alias": {"alias_key": "base", "company_id": self.company, "alias": "Base",
                              "source": "name", "created_at": NOW},
            "board": {"company_id": self.company, "ats": "lever", "token": "base", "created_at": NOW},
            "board_read": {"board_id": self.board, "run_id": self.run, "at": NOW, "result": "ok"},
            "posting": {"company_id": self.company, "source_key": "url:base", "title": "T",
                        "first_seen_at": NOW, "last_seen_at": NOW},
            "ruleset": {"ruleset_version": "r-bbbbbbbbbbbb", "config_toml": "", "created_at": NOW},
            "decision": {"posting_id": self.posting, "ruleset_version": self.ruleset_a, "decided_at": NOW,
                         "lane": "engineering", "level_tier": "new_grad", "level_ok": 1,
                         "level_basis": "title", "us": "yes", "sponsorship": "silent",
                         "citizenship": 0, "eligibility": "ok", "start_2027": 0},
            "evidence": {"company_id": self.company, "fact": "sponsorship", "value": "yes",
                         "confidence": "estimate", "source_kind": "posting", "recorded_at": NOW},
            "person": {"company_id": self.company, "name": "P", "role_kind": "engineer",
                       "email": "base2@acme.co", "channel": "email", "added_by": "devanshu",
                       "created_at": NOW, "updated_at": NOW},
            "touch": {"person_id": self.spare_person, "created_at": NOW, "updated_at": NOW},
            "message": {"touch_id": self.touch, "seq": 0, "state": "drafted", "subject": "S", "body": "B",
                        "created_at": NOW, "updated_at": NOW},
            "inbound_message": {"touch_id": self.touch, "gmail_message_id": "g1", "from_addr": "a@b.co",
                                "kind": "human", "received_at": NOW, "recorded_at": NOW},
            "linkedin_log": {"person_id": self.person, "kind": "note", "at": NOW, "recorded_at": NOW},
            "sender": {"sender_id": 1, "paused": 1, "pause_kind": "by_you", "paused_at": NOW,
                       "updated_at": NOW},
            "send_day": {"day": "2026-10-20", "send_limit": 5, "created_at": NOW},
            "no_go": {"company_id": self.company, "reason": "other", "source": "devanshu", "created_at": NOW},
            "review_item": {"kind": "flagged", "company_id": self.company, "reason": "r",
                            "evidence_key": "k", "raised_by": "claude", "created_at": NOW},
            "daily_list": {"list_date": "2026-10-02", "built_at": NOW, "capacity": 5},
            "daily_list_entry": {"list_date": "2026-10-01", "company_id": self.company,
                                 "list_group": "level_role", "rank": 1, "proposed_at": NOW},
            "event": {"at": NOW, "recorded_at": NOW, "entity": "company", "kind": "k", "actor": "system"},
            "job_run": {"job": "rebuild", "started_at": NOW},
            "import_record": {"run_id": self.run, "source": "data_js", "source_key": "k", "raw": "r",
                              "raw_sha256": "a" * 64, "outcome": "imported", "entity": "company",
                              "entity_id": self.company, "imported_at": NOW},
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
        self.assertEqual([(r[0], r[1]) for r in row], [(1, "initial")])
        self.assertEqual(self.db.execute("PRAGMA user_version").fetchone()[0], 1)

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
        seen = 0
        for table in self.tables():
            for column in self.columns(table):
                if column == "at" or column.endswith("_at"):
                    seen += 1
                    self.assertTrue(self._has_base(table), f"no base row for {table}")
                    self.assert_bad_value_refused(table, column, bad, f"{column} IS ")
        self.assertGreater(seen, 40)

    def _has_base(self, table):
        try:
            self.base_row(table)
            return True
        except KeyError:
            return False

    def test_every_date_column_rejects_timestamps(self):
        bad = ["2026-10-19T00:00:00Z", "2026-13-01"]
        extra = {("daily_list", "list_date"), ("daily_list_entry", "list_date"), ("send_day", "day")}
        seen = 0
        for table in self.tables():
            for column in self.columns(table):
                if column.endswith("_on") or (table, column) in extra:
                    seen += 1
                    self.assertTrue(self._has_base(table), f"no base row for {table}")
                    # daily_list_entry.list_date has no CHECK of its own: the foreign key to
                    # daily_list refuses a value that is not a stored date.
                    expect = "FOREIGN KEY" if (table, column) == ("daily_list_entry", "list_date") \
                        else f"{column} IS "
                    self.assert_bad_value_refused(table, column, bad, expect)
        self.assertEqual(seen, 6)  # posted_on, observed_on, due_on and the three named ones

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
                self.db.execute("DELETE FROM message WHERE message_id = ?", (message_id,))

    def test_every_other_transition_is_refused_by_the_trigger(self):
        for old, new in itertools.permutations(STATES, 2):
            if (old, new) in LEGAL:
                continue
            with self.subTest(change=f"{old}>{new}"):
                message_id = self.message_in(old)
                with self.assertRaises(sqlite3.IntegrityError) as cm:
                    self.transition(message_id, new)
                self.assertIn("illegal message state change", str(cm.exception))
                self.db.execute("DELETE FROM message WHERE message_id = ?", (message_id,))

    def test_a_claimed_message_is_frozen(self):
        for state in ("sending", "send_unknown", "sent"):
            with self.subTest(state=state):
                message_id = self.message_in(state)
                self.assert_refused("UPDATE message SET subject = 'Changed' WHERE message_id = ?",
                                    (message_id,), "frozen")
                self.assert_refused("UPDATE message SET body = 'Changed' WHERE message_id = ?",
                                    (message_id,), "frozen")
                self.db.execute("DELETE FROM message WHERE message_id = ?", (message_id,))
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


if __name__ == "__main__":
    unittest.main()
