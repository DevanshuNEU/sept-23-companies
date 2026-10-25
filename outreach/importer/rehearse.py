"""The rehearsal: the whole import on copies of the sources, into a scratch store, never the live one.

`rehearse` copies every source into `<into>/sources/<source>/` (files with `copy2`, folders with `copytree`,
radar.db with SQLite's backup API from a read-only connection), writes `<into>/manifest.toml` naming the copies and
the same overrides file, installs a store at `<into>/outreach.db`, then runs the dry run, the run and the second run
against it and writes `<into>/report.txt`. The originals are only read.

Two guards come first. On his real files (the committed manifest) the rehearsal runs only from the live checkout,
so an agent in a worktree can never copy his history anywhere. And the target is never the live checkout, the live
store's folder or the live store itself, whoever runs it.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tomllib
from pathlib import Path

import live_checkout
from outreach.importer.manifest import DEFAULT_SOURCES, Manifest, _expand, load_manifest, load_overrides
from outreach.importer.plan import ImportPlan, build_plan, read_all
from outreach.importer.records import SOURCES, SourceRecord, open_radar
from outreach.importer.report import CountReport, check_report, count_report, render_report, report_to_json
from outreach.importer.write import apply_plan
from outreach.install import install
from outreach.sending.config import load_config
from outreach.store import connect
from outreach.store.db import DEFAULT_PATH, is_live_path, normalise

__all__ = ["rehearse", "import_twice", "refuse_live_target", "default_into", "is_real", "marks", "store_report"]

PASSED, FAILED = "Rehearsal passed", "Rehearsal failed:"


def default_into(now: str) -> Path:
    return Path(os.path.expanduser("~/Desktop/outreach-scratch/import-rehearsal")) / now[:10]


def _inside(path: str, folder: str) -> bool:
    folder = normalise(folder)
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def refuse_live_target(into: Path) -> None:
    """SystemExit when `into` is in the live checkout, in the live store's folder, or is the live store."""
    path = normalise(str(into))
    store = str(Path(path) / "outreach.db")
    for folder, what in ((live_checkout.LIVE_ROOT, "the live checkout"),
                         (os.path.dirname(normalise(DEFAULT_PATH)), "the live store's folder"),
                         (os.path.expanduser("~/Library/Application Support/outreach"), "the live store's folder")):
        if _inside(path, folder):
            raise SystemExit(f"the rehearsal never writes into {what} ({folder}); pick a scratch folder")
    if is_live_path(path) or is_live_path(store):
        raise SystemExit(f"the rehearsal never writes the live store ({path})")


def is_real(manifest: Manifest) -> bool:
    """Whether any source path is one the committed manifest names (his real files). Read from the committed file
    without expanding the backup glob, so nothing of his is listed or read to decide it."""
    with open(DEFAULT_SOURCES, "rb") as f:
        listed = tomllib.load(f).get("paths", {})
    for source, text in listed.items():
        real = Path(_expand(text, os.environ))
        mine = Path(manifest.paths.get(source, ""))
        if source == "local_storage":
            if normalise(str(mine.parent)) == normalise(str(real.parent)):
                return True
        elif normalise(str(mine)) == normalise(str(real)):
            return True
    return False


def _free(into: Path) -> Path:
    """`into`, or `into-2`, `into-3` when an earlier rehearsal is there (he reruns it after each overrides edit)."""
    if not into.exists() or not any(into.iterdir()):
        return into
    n = 2
    while (candidate := into.with_name(f"{into.name}-{n}")).exists():
        n += 1
    return candidate


def _copy(manifest: Manifest, sources: Path) -> dict[str, Path]:
    copies = {}
    for source in SOURCES:
        original = Path(manifest.paths[source])
        target = sources / source / original.name
        target.parent.mkdir(parents=True)
        if source == "radar_db":
            ro = open_radar(original)
            try:
                out = sqlite3.connect(target)
                try:
                    ro.backup(out)
                finally:
                    out.close()
            finally:
                ro.close()
        elif original.is_dir():
            shutil.copytree(original, target)
        else:
            if not original.is_file():
                raise FileNotFoundError(f"{source}: no file at {original}")
            shutil.copy2(original, target)
        copies[source] = target
    return copies


def _toml(value: str) -> str:
    return json.dumps(str(value))  # a JSON string is a TOML basic string for paths


def _write_manifest(path: Path, copies: dict[str, Path], overrides: Path | None) -> None:
    lines = ["# Written by the rehearsal: copies of every source.", "[paths]"]
    lines += [f"{s} = {_toml(p)}" for s, p in copies.items()]
    if overrides:
        lines += ["[overrides]", f"path = {_toml(overrides)}"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def marks(conn: sqlite3.Connection) -> dict[str, tuple[int, int]]:
    """(rows, highest rowid) per table: what a second run must leave unchanged."""
    out = {}
    for name, sql in conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name"):
        if "WITHOUT ROWID" in (sql or "").upper():
            out[name] = (conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0], 0)
        else:
            out[name] = tuple(conn.execute(f'SELECT count(*), coalesce(max(rowid), 0) FROM "{name}"').fetchone())
    return out


def store_report(conn: sqlite3.Connection, run_id: int, report: CountReport) -> None:
    """The count report beside the writer's own summary in `job_run.summary` (an update of the run's own row)."""
    row = conn.execute("SELECT summary FROM job_run WHERE run_id = ?", (run_id,)).fetchone()
    summary = json.loads(row[0] or "{}")
    summary["report"] = report_to_json(report)
    conn.execute("UPDATE job_run SET summary = ? WHERE run_id = ?", (json.dumps(summary, sort_keys=True), run_id))


def import_twice(conn: sqlite3.Connection, plan: ImportPlan, records: list[SourceRecord], *,
                 now: str) -> tuple[CountReport, list[str], list[str]]:
    """The check, the run and the same plan again. Returns the report, the lines to print and the failures (none
    is a pass). A failed check writes nothing."""
    report = count_report(plan, records)
    failures = check_report(report)
    if failures:
        return report, [render_report(report), "Refused: the check failed, nothing was written."], failures
    result = apply_plan(conn, plan, now=now)
    report = count_report(plan, records, written=result.written)
    store_report(conn, result.run_id, report)
    lines = [render_report(report)]
    if result.status != "ok":
        lines.append(f"The run ended {result.status}: {result.error}. Run it again to finish.")
        return report, lines, [f"the run ended {result.status}: {result.error}"]
    before = marks(conn)
    again = apply_plan(conn, plan, now=now)
    store_report(conn, again.run_id, count_report(plan, records, written=again.written))
    after = marks(conn)
    changed = sorted(t for t in after if t != "job_run" and after[t] != before.get(t))
    if again.status != "ok" or changed:
        lines.append(f"Second run changed: {', '.join(changed) or again.error}")
        return report, lines, [f"the second run changed {', '.join(changed) or again.error}"]
    lines.append("Second run changed nothing")
    return report, lines, []


def rehearse(manifest: Manifest, into: Path, *, now: str, real: bool = False) -> tuple[CountReport, list[str]]:
    """The whole import on copies. `real` (his committed manifest) needs the live checkout. Prints the report and
    `Rehearsal passed` or `Rehearsal failed:` with each failure; returns the report and the failures."""
    if real or is_real(manifest):
        live_checkout.require_live("the rehearsal on real data")
    refuse_live_target(into)
    into = _free(Path(into))
    into.mkdir(parents=True, exist_ok=True)
    copies = _copy(manifest, into / "sources")
    _write_manifest(into / "manifest.toml", copies, manifest.overrides)
    copied = load_manifest(into / "manifest.toml")
    store = into / "outreach.db"
    install(str(store))
    records = read_all(copied)
    plan = build_plan(records, load_overrides(copied.overrides), now=now, send_cfg=load_config())
    conn = connect(str(store))
    try:
        report, lines, failures = import_twice(conn, plan, records, now=now)
    finally:
        conn.close()
    lines.append(PASSED if not failures else "\n".join([FAILED] + [f"  {f}" for f in failures]))
    lines.insert(0, f"Rehearsal folder: {into}")
    text = "\n".join(lines)
    (into / "report.txt").write_text(text + "\n", encoding="utf-8")
    print(text)
    return report, failures
