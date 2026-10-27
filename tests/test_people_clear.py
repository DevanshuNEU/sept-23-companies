"""`python -m outreach.people clear-reach` (Part of #15): clear the LinkedIn To reach list for the new system.

It removes only LinkedIn people nobody ever contacted and nobody of his touched, with the prepare drafts and source
rows that exist only for them, and clears their name from past plan rows. Everyone with history stays.
"""
import io
import json
import os
import sqlite3
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from outreach.people import __main__ as cli
from outreach.people.clear import BackupFailed, apply_clear, plan_clear, table_counts, write_backup
from outreach.store import DEFAULT_PATH, append_event, connect
from outreach.store.timefmt import utcnow
from tests.plan_seed import seed_daily_entry, seed_email_touch, seed_log_only, seed_outbound, seed_person
from tests.storetest import fresh_store, seed_company

NOW = "2026-11-10T15:00:00Z"  # 10:00 in New York, 2026-11-10
TODAY = date(2026, 11, 10)
PAST = date(2026, 11, 9)


def _path(conn: sqlite3.Connection) -> str:
    return conn.execute("PRAGMA database_list").fetchone()[2]


def _draft(conn, person_id: int, *, saved_by: str = "claude", reason_state: str = "proposed") -> int:
    now = utcnow()
    return conn.execute(
        "INSERT INTO linkedin_draft(person_id, body, saved_by, reason_state, created_at, updated_at) "
        "VALUES (?, 'Hi, I read your post.', ?, ?, ?, ?)", (person_id, saved_by, reason_state, now, now)).lastrowid


def _source(conn, person_id: int, url: str = "https://acme.example/team") -> int:
    return conn.execute(
        "INSERT INTO person_source(person_id, kind, url, quote, recorded_at) VALUES (?, 'team', ?, 'Dana at Acme', ?)",
        (person_id, url, utcnow())).lastrowid


def _ids(plan) -> set[int]:
    return {r.person_id for r in plan.remove}


def _kept(plan) -> dict[int, tuple[str, ...]]:
    return {k.person_id: k.reasons for k in plan.kept}


class Base(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.co = seed_company(self.conn, "Acme")

    def person(self, name: str, added_by: str = "import", company: int | None = None) -> int:
        return seed_person(self.conn, company or self.co, name, added_by=added_by)


class WhoStays(Base):
    def test_a_person_with_an_outreach_touch_stays(self):
        pid = self.person("Touched")
        seed_outbound(self.conn, pid, self.co, "email", PAST, context_source="own")
        plan = plan_clear(self.conn, now=NOW)
        self.assertNotIn(pid, _ids(plan))
        self.assertIn("outreach touch", " ".join(_kept(plan)[pid]))

    def test_a_person_with_an_undone_touch_stays(self):
        pid = self.person("Undone")
        touch = seed_outbound(self.conn, pid, self.co, "linkedin_request", PAST, context_source="own")
        self.conn.execute("UPDATE outreach_touch SET undone_at = ? WHERE touch_id = ?", (NOW, touch))
        self.assertNotIn(pid, _ids(plan_clear(self.conn, now=NOW)))

    def test_a_person_with_a_linkedin_log_row_stays(self):
        pid = self.person("Logged")
        seed_log_only(self.conn, pid, "note", PAST)
        plan = plan_clear(self.conn, now=NOW)
        self.assertNotIn(pid, _ids(plan))
        self.assertIn("LinkedIn log", " ".join(_kept(plan)[pid]))

    def test_a_person_with_an_application_touch_stays(self):
        pid = self.person("Applied")
        seed_outbound(self.conn, pid, self.co, "application", PAST, context_source="own")
        plan = plan_clear(self.conn, now=NOW)
        self.assertNotIn(pid, _ids(plan))
        self.assertIn("application", " ".join(_kept(plan)[pid]))

    def test_a_person_with_an_email_thread_stays(self):
        pid = self.person("Emailed")
        seed_email_touch(self.conn, pid, PAST)
        plan = plan_clear(self.conn, now=NOW)
        self.assertNotIn(pid, _ids(plan))
        self.assertIn("email thread", " ".join(_kept(plan)[pid]))

    def test_his_own_saved_note_keeps_the_person(self):
        saved = self.person("Saved", added_by="claude")
        _draft(self.conn, saved, saved_by="devanshu")
        confirmed = self.person("Confirmed", added_by="claude")
        _draft(self.conn, confirmed, saved_by="devanshu", reason_state="confirmed")
        plan = plan_clear(self.conn, now=NOW)
        self.assertFalse({saved, confirmed} & _ids(plan))
        self.assertIn("note he saved", " ".join(_kept(plan)[saved]))

    def test_a_person_he_added_stays(self):
        pid = self.person("His", added_by="devanshu")
        plan = plan_clear(self.conn, now=NOW)
        self.assertNotIn(pid, _ids(plan))
        self.assertIn("he added", " ".join(_kept(plan)[pid]))

    def test_a_no_go_person_stays(self):
        pid = self.person("Stopped")
        self.conn.execute("INSERT INTO no_go(person_id, reason, source, created_at) "
                          "VALUES (?, 'said_no', 'devanshu', ?)", (pid, utcnow()))
        plan = plan_clear(self.conn, now=NOW)
        self.assertNotIn(pid, _ids(plan))
        self.assertIn("no-go", " ".join(_kept(plan)[pid]))

    def test_a_person_named_in_a_review_item_stays(self):
        pid = self.person("Flagged")
        self.conn.execute(
            "INSERT INTO review_item(kind, company_id, person_id, reason, evidence_key, raised_by, state, resolution, "
            "created_at, resolved_at) VALUES ('flagged', ?, ?, 'odd', 'k', 'claude', 'resolved', 'marked_ok', ?, ?)",
            (self.co, pid, utcnow(), utcnow()))
        plan = plan_clear(self.conn, now=NOW)
        self.assertNotIn(pid, _ids(plan))
        self.assertIn("Needs-you", " ".join(_kept(plan)[pid]))

    def test_a_person_he_changed_stays(self):
        pid = self.person("Edited")
        append_event(self.conn, entity="person", kind="role_kind_changed", actor="devanshu", entity_id=pid)
        plan = plan_clear(self.conn, now=NOW)
        self.assertNotIn(pid, _ids(plan))
        self.assertIn("he changed", " ".join(_kept(plan)[pid]))

    def test_a_claude_event_does_not_keep_a_person(self):
        pid = self.person("Added", added_by="claude")
        append_event(self.conn, entity="person", kind="added", actor="claude", entity_id=pid)
        self.assertIn(pid, _ids(plan_clear(self.conn, now=NOW)))

    def test_a_person_on_todays_plan_stays(self):
        pid = self.person("Today")
        seed_daily_entry(self.conn, TODAY, self.co, person_id=pid)
        plan = plan_clear(self.conn, now=NOW)
        self.assertNotIn(pid, _ids(plan))
        self.assertIn("plan for 2026-11-10", " ".join(_kept(plan)[pid]))

    def test_someone_at_a_company_with_a_person_row_today_stays(self):
        other = self.person("Named today")
        pid = self.person("Colleague")
        seed_daily_entry(self.conn, TODAY, self.co, person_id=other)
        plan = plan_clear(self.conn, now=NOW)
        self.assertNotIn(pid, _ids(plan))
        self.assertIn("company has a person row", " ".join(_kept(plan)[pid]))

    def test_a_find_someone_row_today_does_not_keep_people_at_the_company(self):
        pid = self.person("Untouched")
        seed_daily_entry(self.conn, TODAY, self.co, channel="linkedin_request")
        self.assertIn(pid, _ids(plan_clear(self.conn, now=NOW)))

    def test_a_past_plan_row_he_decided_keeps_the_person(self):
        picked = self.person("Picked")
        removed = self.person("Removed", company=seed_company(self.conn, "Beta"))
        seed_daily_entry(self.conn, PAST, self.co, person_id=picked, state="picked")
        seed_daily_entry(self.conn, PAST, self.conn.execute(
            "SELECT company_id FROM person WHERE person_id = ?", (removed,)).fetchone()[0], rank=2,
            person_id=removed, state="removed")
        plan = plan_clear(self.conn, now=NOW)
        self.assertFalse({picked, removed} & _ids(plan))
        self.assertIn("picked", " ".join(_kept(plan)[picked]))

    def test_email_people_are_never_in_scope(self):
        pid = seed_person(self.conn, self.co, "Mail", email="mail@acme.example", added_by="import")
        plan = plan_clear(self.conn, now=NOW)
        self.assertNotIn(pid, _ids(plan) | set(_kept(plan)))


class WhoGoes(Base):
    def test_an_untouched_imported_person_and_a_prepare_person_go_with_their_draft_and_sources(self):
        imported = self.person("Imported")
        prepared = self.person("Prepared", added_by="claude")
        draft = _draft(self.conn, prepared)
        source = _source(self.conn, prepared)
        plan = plan_clear(self.conn, now=NOW)
        self.assertEqual(_ids(plan), {imported, prepared})
        found = {r.person_id: r for r in plan.remove}
        self.assertEqual([d for d, _ in found[prepared].drafts], [draft])
        self.assertEqual(found[prepared].sources, (source,))
        apply_clear(self.conn, plan, backup=Path("/b.db"), now=NOW)
        for table in ("person", "linkedin_draft", "person_source"):
            self.assertEqual(self.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0, table)

    def test_a_past_undecided_plan_row_loses_the_person_and_keeps_the_row(self):
        pid = self.person("Carried")
        seed_daily_entry(self.conn, PAST, self.co, person_id=pid, state="backlog")
        other = seed_company(self.conn, "Beta")
        pid2 = self.person("Proposed", company=other)
        seed_daily_entry(self.conn, PAST, other, rank=2, person_id=pid2)
        plan = plan_clear(self.conn, now=NOW)
        self.assertEqual(_ids(plan), {pid, pid2})
        apply_clear(self.conn, plan, backup=Path("/b.db"), now=NOW)
        rows = self.conn.execute("SELECT person_id, channel, state, revision FROM daily_list_entry "
                                 "ORDER BY rank").fetchall()
        self.assertEqual([tuple(r) for r in rows],
                         [(None, "linkedin_request", "backlog", 2), (None, "linkedin_request", "proposed", 2)])

    def test_the_event_records_who_went_so_it_can_be_undone(self):
        pid = self.person("Prepared", added_by="claude")
        draft = _draft(self.conn, pid)
        seed_daily_entry(self.conn, PAST, self.co, person_id=pid, state="backlog")
        plan = plan_clear(self.conn, now=NOW)
        apply_clear(self.conn, plan, backup=Path("/b/clear.db"), now=NOW)
        rows = self.conn.execute("SELECT * FROM event WHERE kind = 'people_cleared'").fetchall()
        self.assertEqual(len(rows), 1)
        ev = rows[0]
        self.assertEqual((ev["entity"], ev["actor"]), ("person", "system"))
        detail = json.loads(ev["detail"])
        self.assertEqual(detail["backup"], "/b/clear.db")
        self.assertEqual(detail["counts"], {"people": 1, "drafts": 1, "sources": 0, "plan_rows_cleared": 1})
        self.assertEqual(detail["people"], [{
            "person_id": pid, "name": "Prepared", "company_id": self.co, "company": "Acme", "added_by": "claude",
            "draft_ids": [draft], "source_ids": [],
            "plan_rows": [{"list_date": PAST.isoformat(), "company_id": self.co}]}])

    def test_a_second_run_removes_nothing_and_writes_nothing(self):
        self.person("Imported")
        apply_clear(self.conn, plan_clear(self.conn, now=NOW), backup=Path("/b.db"), now=NOW)
        again = plan_clear(self.conn, now=NOW)
        self.assertEqual(again.remove, ())
        before = self.conn.total_changes
        result = apply_clear(self.conn, again, backup=Path("/b.db"), now=NOW)
        self.assertEqual(result.removed, ())
        self.assertEqual(self.conn.total_changes, before)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM event WHERE kind = 'people_cleared'").fetchone()[0], 1)

    def test_a_person_touched_after_the_dry_plan_is_not_removed(self):
        touched = self.person("Touched late")
        untouched = self.person("Untouched")
        plan = plan_clear(self.conn, now=NOW)
        self.assertEqual(_ids(plan), {touched, untouched})
        seed_log_only(self.conn, touched, "request_sent", TODAY)
        result = apply_clear(self.conn, plan, backup=Path("/b.db"), now=NOW)
        self.assertEqual({r.person_id for r in result.removed}, {untouched})
        self.assertEqual({r.person_id for r in result.changed}, {touched})
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM person WHERE person_id = ?", (touched,)).fetchone())

    def test_a_person_given_a_new_draft_after_the_dry_plan_is_not_removed(self):
        pid = self.person("Drafted late", added_by="claude")
        plan = plan_clear(self.conn, now=NOW)
        _draft(self.conn, pid)
        result = apply_clear(self.conn, plan, backup=Path("/b.db"), now=NOW)
        self.assertEqual(result.removed, ())
        self.assertEqual(self.conn.execute("SELECT count(*) FROM linkedin_draft").fetchone()[0], 1)

    def test_a_person_added_after_the_dry_plan_is_not_removed(self):
        plan = plan_clear(self.conn, now=NOW)
        late = self.person("Added late")
        apply_clear(self.conn, plan, backup=Path("/b.db"), now=NOW)
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM person WHERE person_id = ?", (late,)).fetchone())

    def test_one_failed_delete_rolls_back_everything(self):
        first = self.person("First", added_by="claude")
        _draft(self.conn, first)
        _source(self.conn, first)
        seed_daily_entry(self.conn, PAST, self.co, person_id=first, state="backlog")
        second = self.person("Second")
        self.conn.execute(f"CREATE TEMP TRIGGER stop_second BEFORE DELETE ON main.person WHEN OLD.person_id = {second} "
                          "BEGIN SELECT RAISE(ABORT, 'test: this delete fails'); END")
        plan = plan_clear(self.conn, now=NOW)
        counts = table_counts(self.conn)
        with self.assertRaises(sqlite3.IntegrityError):
            apply_clear(self.conn, plan, backup=Path("/b.db"), now=NOW)
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(table_counts(self.conn), counts)
        self.assertEqual(self.conn.execute("SELECT person_id FROM daily_list_entry").fetchone()[0], first)


class History(Base):
    def test_a_person_with_old_system_history_stays_in_the_store(self):
        touched = self.person("Old import")
        seed_email_touch(self.conn, touched, PAST, company_id=self.co, outreach=True)
        untouched = self.person("Untouched")
        plan = plan_clear(self.conn, now=NOW)
        self.assertEqual(_ids(plan), {untouched})
        self.assertIn("never deleted", " ".join(_kept(plan)[touched]))
        apply_clear(self.conn, plan, backup=Path("/b.db"), now=NOW)
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM person WHERE person_id = ?", (touched,)).fetchone())


class Backup(Base):
    def test_the_backup_is_a_full_copy_next_to_the_store(self):
        pid = self.person("Imported")
        target = write_backup(self.conn, _path(self.conn), NOW)
        self.assertEqual(target, Path(_path(self.conn)).parent / "backups" / "clear-reach-20261110T150000Z.db")
        copy = sqlite3.connect(target)
        self.addCleanup(copy.close)
        self.assertEqual(copy.execute("SELECT person_id FROM person").fetchall(), [(pid,)])

    def test_a_backup_that_cannot_be_written_raises(self):
        (Path(_path(self.conn)).parent / "backups").write_text("a file, not a folder")
        with self.assertRaises(BackupFailed):
            write_backup(self.conn, _path(self.conn), NOW)

    def test_an_existing_backup_is_never_overwritten(self):
        target = write_backup(self.conn, _path(self.conn), NOW)
        before = target.read_bytes()
        self.person("Later")
        with self.assertRaises(BackupFailed):
            write_backup(self.conn, _path(self.conn), NOW)
        self.assertEqual(target.read_bytes(), before)


class Command(Base):
    def run_cli(self, *argv: str, env: dict | None = None) -> tuple[int, str]:
        out = io.StringIO()
        with mock.patch.dict(os.environ, env if env is not None else {"OUTREACH_DB": _path(self.conn)}), \
                mock.patch.object(cli, "utcnow", return_value=NOW):
            code = cli.main(["clear-reach", *argv], stdout=out)
        return code, out.getvalue()

    def test_the_dry_run_prints_who_and_writes_nothing(self):
        gone = self.person("Imported")
        kept = self.person("His", added_by="devanshu")
        path = Path(_path(self.conn))
        before = path.read_bytes(), self.conn.total_changes
        code, out = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn(f"Imported (person {gone})", out)
        self.assertIn(f"His (person {kept})", out)
        self.assertIn("he added this person himself", out)
        self.assertIn("DRY RUN", out)
        self.assertEqual((path.read_bytes(), self.conn.total_changes), before)
        self.assertFalse((path.parent / "backups").exists())
        self.assertEqual(self.conn.execute("SELECT count(*) FROM person").fetchone()[0], 2)

    def test_apply_writes_the_backup_then_removes_and_prints_counts(self):
        gone = self.person("Imported")
        code, out = self.run_cli("--apply")
        self.assertEqual(code, 0)
        backup = Path(_path(self.conn)).parent / "backups" / "clear-reach-20261110T150000Z.db"
        self.assertIn(f"Backup written: {backup}", out)
        copy = sqlite3.connect(backup)
        self.addCleanup(copy.close)
        self.assertEqual(copy.execute("SELECT person_id FROM person").fetchall(), [(gone,)])
        self.assertIsNone(self.conn.execute("SELECT 1 FROM person WHERE person_id = ?", (gone,)).fetchone())
        self.assertIn("Rows before:", out)
        self.assertIn("Rows after:", out)
        ev = self.conn.execute("SELECT detail FROM event WHERE kind = 'people_cleared'").fetchone()
        self.assertEqual(json.loads(ev["detail"])["backup"], str(backup))

    def test_a_second_apply_writes_no_backup_and_no_event(self):
        self.person("Imported")
        self.run_cli("--apply")
        backups = Path(_path(self.conn)).parent / "backups"
        made = sorted(backups.iterdir())
        with mock.patch.object(cli, "utcnow", return_value="2026-11-10T15:05:00Z"):
            out = io.StringIO()
            with mock.patch.dict(os.environ, {"OUTREACH_DB": _path(self.conn)}):
                self.assertEqual(cli.main(["clear-reach", "--apply"], stdout=out), 0)
        self.assertIn("Nothing to remove", out.getvalue())
        self.assertEqual(sorted(backups.iterdir()), made)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM event WHERE kind = 'people_cleared'").fetchone()[0], 1)

    def test_apply_refuses_when_the_backup_fails(self):
        pid = self.person("Imported")
        (Path(_path(self.conn)).parent / "backups").write_text("a file, not a folder")
        code, out = self.run_cli("--apply")
        self.assertEqual(code, 1)
        self.assertIn("backup failed", out.lower())
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM person WHERE person_id = ?", (pid,)).fetchone())

    def test_apply_outside_the_live_checkout_refuses_the_default_store(self):
        self.person("Imported")
        env = {k: v for k, v in os.environ.items() if k != "OUTREACH_DB"}
        with mock.patch.object(cli, "connect") as opened, mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(cli.live_checkout, "is_live", return_value=False):
            with self.assertRaises(SystemExit) as stop:
                cli.main(["clear-reach", "--apply"], stdout=io.StringIO())
            self.assertIn("live checkout", str(stop.exception))
            with mock.patch.dict(os.environ, {"OUTREACH_DB": DEFAULT_PATH}):
                with self.assertRaises(SystemExit):
                    cli.main(["clear-reach", "--apply"], stdout=io.StringIO())
        opened.assert_not_called()

    def test_there_is_no_include_history_mode(self):
        with self.assertRaises(SystemExit), mock.patch("sys.stderr", io.StringIO()):
            cli.main(["clear-reach", "--include-history"], stdout=io.StringIO())

if __name__ == "__main__":
    unittest.main()
