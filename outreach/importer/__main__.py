"""The import command line: `uv run python -m outreach.importer <command> [--manifest PATH] [--overrides PATH]`.

    preflight        read every source, no store: paths, record counts, contacts by status, unclear sends
    dry-run          the plan and its count report; nothing is written (exit 1 when the check fails)
    run              the dry run, then the import, then the same plan again, which must change nothing
    report           the stored report of the newest (or --run-id) import run, and import_record counts
    reconcile        old mailer sends against the store's sent messages; exit 1 when one is missing
    rollback-check   people the new sender emailed after --since whom the old mailer could email again

Every store is opened with `outreach.store.connect()`, whose guard refuses the live store outside the live
checkout before any file is touched. Use a scratch `OUTREACH_DB` anywhere else. Sources are only read.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from outreach.importer.manifest import load_manifest, load_overrides
from outreach.importer.plan import READERS, build_plan, read_all
from outreach.importer.records import SOURCES, SourceError
from outreach.importer.report import check_report, count_report, render_report, report_from_json, report_to_json
from outreach.importer.vocab import contact_status_plan
from outreach.importer.write import apply_plan
from outreach.sending.config import load_config
from outreach.sources.keys import email_key
from outreach.store import connect, iso_utc, utcnow

__all__ = ["main"]

# Old mailer statuses after which it may still send something (a first email, a retry or a follow-up).
STILL_SENDS = frozenset({"drafted", "approved", "failed", "sending", "send_unknown", "sent", "followup_ready",
                         "followup_approved"})


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m outreach.importer",
                                     description="Import Devanshu's outreach history into the store (#17).")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "dry-run", "run", "report", "reconcile", "rollback-check"):
        sub = commands.add_parser(name)
        sub.add_argument("--manifest", type=Path, default=None, help="sources file (default: the committed one)")
        sub.add_argument("--overrides", type=Path, default=None, help="his rulings (default: the manifest's)")
        sub.add_argument("--now", default=None, help=argparse.SUPPRESS)  # tests and the rehearsal fix the clock
        if name == "report":
            sub.add_argument("--run-id", type=int, default=None)
        if name == "rollback-check":
            sub.add_argument("--since", required=True, help="the switch time, ISO 8601")
    return parser


def _contacts(manifest) -> list[dict]:
    return [r.raw for r in READERS["contacts_json"](manifest.paths["contacts_json"])]


def _plan(args, manifest):
    records = read_all(manifest)
    overrides = load_overrides(args.overrides or manifest.overrides)
    plan = build_plan(records, overrides, now=args.now, send_cfg=load_config())
    return records, plan


def _marks(conn: sqlite3.Connection) -> dict[str, tuple[int, int]]:
    """(rows, highest rowid) per table: what a second run must leave unchanged."""
    marks = {}
    for name, sql in conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name"):
        if "WITHOUT ROWID" in (sql or "").upper():
            marks[name] = (conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0], 0)
        else:
            marks[name] = tuple(conn.execute(f'SELECT count(*), coalesce(max(rowid), 0) FROM "{name}"').fetchone())
    return marks


# -- commands ------------------------------------------------------------------------------------------------------

def preflight(args, manifest) -> int:
    bad = 0
    for source in SOURCES:
        path = manifest.paths[source]
        try:
            n = sum(1 for _ in READERS[source](path))
            print(f"{source}: found, {n} records ({path})")
        except SourceError as err:
            bad += 1
            print(f"{source}: missing ({err})")
    print(f"Tracker backup: {manifest.paths['local_storage'].name}")
    try:
        contacts = _contacts(manifest)
    except SourceError:
        contacts = []
    statuses = Counter(str(c.get("status")) for c in contacts)
    print("Contacts by status: " + (", ".join(f"{s} {n}" for s, n in sorted(statuses.items())) or "none"))
    unclear = [c for c in contacts if str(c.get("status")) in ("sending", "send_unknown")]
    print(f"Unclear sends: {len(unclear)}")
    for c in unclear:
        print(f"  {c.get('id')} {c.get('name')} <{c.get('email')}> at {c.get('company')}: {c.get('status')}")
    return 1 if bad or unclear else 0


def dry_run(args, manifest) -> int:
    conn = connect()
    try:
        before = conn.total_changes
        stored = conn.execute("SELECT count(*) FROM import_record").fetchone()[0]
        records, plan = _plan(args, manifest)
        report = count_report(plan, records)
        print(render_report(report))
        print(f"Import records already in the store: {stored}")
        if conn.total_changes != before:
            raise RuntimeError("the dry run wrote to the store")
    finally:
        conn.close()
    return 0 if not check_report(report) else 1


def run(args, manifest) -> int:
    conn = connect()
    try:
        records, plan = _plan(args, manifest)
        failures = check_report(count_report(plan, records))
        if failures:
            print(render_report(count_report(plan, records)))
            print("Refused: the check failed, nothing was written.")
            return 1
        result = apply_plan(conn, plan, now=args.now)
        report = count_report(plan, records, written=result.written)
        _store_report(conn, result.run_id, report)
        print(render_report(report))
        if result.status != "ok":
            print(f"The run ended {result.status}: {result.error}. Run it again to finish.")
            return 1
        before = _marks(conn)
        again = apply_plan(conn, plan, now=args.now)
        _store_report(conn, again.run_id, count_report(plan, records, written=again.written))
        after = _marks(conn)
        changed = sorted(t for t in after if t != "job_run" and after[t] != before.get(t))
        if again.status != "ok" or changed:
            print(f"Second run changed: {', '.join(changed) or again.error}")
            return 1
        print("Second run changed nothing")
        return 0
    finally:
        conn.close()


def _store_report(conn: sqlite3.Connection, run_id: int, report) -> None:
    """The count report beside the writer's own summary in `job_run.summary` (an update of the run's own row)."""
    row = conn.execute("SELECT summary FROM job_run WHERE run_id = ?", (run_id,)).fetchone()
    summary = json.loads(row[0] or "{}")
    summary["report"] = report_to_json(report)
    conn.execute("UPDATE job_run SET summary = ? WHERE run_id = ?", (json.dumps(summary, sort_keys=True), run_id))


def report(args, manifest) -> int:
    conn = connect()
    try:
        sql = "SELECT run_id, status, started_at, error, summary FROM job_run WHERE job = 'import'"
        row = (conn.execute(sql + " AND run_id = ?", (args.run_id,)).fetchone() if args.run_id is not None
               else conn.execute(sql + " ORDER BY run_id DESC LIMIT 1").fetchone())
        if row is None:
            print("No import run in this store.")
            return 1
        print(f"Import run {row['run_id']}: {row['status']} at {row['started_at']}"
              + (f" ({row['error']})" if row["error"] else ""))
        summary = json.loads(row["summary"] or "{}")
        if "report" in summary:
            print(render_report(report_from_json(summary["report"])))
        print("Import records in the store:")
        for r in conn.execute("SELECT source, outcome, count(*) AS n FROM import_record GROUP BY source, outcome "
                              "ORDER BY source, outcome"):
            print(f"  {r['source']} {r['outcome']} {r['n']}")
    finally:
        conn.close()
    return 0


def reconcile(args, manifest) -> int:
    conn = connect()
    try:
        expected = []  # (contact id, address, seq, rfc id or None)
        unclear = 0
        for c in _contacts(manifest):
            plan = contact_status_plan(c.get("status"), followup_sent=bool(c.get("followupSentAt")))
            if plan is None or plan.held:
                unclear += 1
                continue
            if plan.message0 == "sent":
                expected.append((c.get("id"), email_key(c.get("email")), 0, c.get("rfcMessageId") or None))
            if plan.message1 == "sent":
                expected.append((c.get("id"), email_key(c.get("email")), 1, None))
        missing = []
        for cid, address, seq, rfc in expected:
            found = rfc and conn.execute("SELECT 1 FROM message WHERE state = 'sent' AND rfc_message_id = ?",
                                         (rfc,)).fetchone()
            if not found:
                found = conn.execute(
                    "SELECT 1 FROM message m JOIN touch t USING(touch_id) JOIN person p USING(person_id) "
                    "WHERE m.state = 'sent' AND m.seq = ? AND p.email = ?", (seq, address)).fetchone()
            if not found:
                missing.append(f"{cid} <{address}> {'first email' if seq == 0 else 'follow-up'}")
        in_store = conn.execute("SELECT count(*) FROM message WHERE state = 'sent'").fetchone()[0]
        log_lines = sum(1 for r in READERS["send_log"](manifest.paths["send_log"]) if r.raw.get("kind") == "sent")
        print(f"Sent in the old mailer: {len(expected)}")
        print(f"Sent in the store: {in_store}")
        print(f"Sent lines in send.log: {log_lines}")
        if unclear:
            print(f"Unclear in the old mailer (not counted): {unclear}")
        print(f"Missing: {len(missing)}")
        for line in missing:
            print(f"  {line}")
    finally:
        conn.close()
    return 1 if missing else 0


def rollback_check(args, manifest) -> int:
    since = iso_utc(args.since)
    status = {email_key(c.get("email")): str(c.get("status")) for c in _contacts(manifest) if c.get("email")}
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT p.name, p.email, m.seq, m.sent_at FROM message m JOIN touch t USING(touch_id) "
            "JOIN person p USING(person_id) WHERE m.state = 'sent' AND m.sent_at >= ? ORDER BY m.sent_at",
            (since,)).fetchall()
    finally:
        conn.close()
    found = [r for r in rows if status.get(r["email"]) in STILL_SENDS]
    if not found:
        print("Nothing to check")
        return 0
    print(f"Emailed by the new sender since {since} and still sendable in the old mailer: {len(found)}")
    for r in found:
        print(f"  {r['name']} <{r['email']}> {'first email' if r['seq'] == 0 else 'follow-up'} at {r['sent_at']}; "
              f"old mailer status {status[r['email']]}. Mark it in contacts.json before the old mailer runs.")
    return 1


COMMANDS = {"preflight": preflight, "dry-run": dry_run, "run": run, "report": report, "reconcile": reconcile,
            "rollback-check": rollback_check}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    args.now = iso_utc(args.now) if args.now else utcnow()
    try:
        manifest = load_manifest(args.manifest)
    except SourceError as err:
        print(err)
        return 1
    try:
        return COMMANDS[args.command](args, manifest)
    except SourceError as err:
        print(err)
        return 1


if __name__ == "__main__":
    sys.exit(main())
