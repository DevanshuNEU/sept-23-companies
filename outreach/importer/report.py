"""The count report: what happened to every record of every source, and the check that nothing was lost unexplained.

`count_report` counts the plan's record outcomes per source; `check_report` lists every failure in plain lines (an
empty list is a pass); `render_report` is the text the dry run and the run print. A radar table record stands for
all its rows, so it counts as that many skipped rows of `radar_db`, and `radar_tables` names every table of the
radar file with its rows (#5's retire guard reads it from the stored summary).
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from outreach.importer.plan import ImportPlan
from outreach.importer.read_radar import RADAR_ROW_TABLES
from outreach.importer.records import SOURCES, SourceRecord

__all__ = ["EXPLAINED_SKIPS", "MERGE_REASONS", "REQUIRED_SOURCES", "SourceCount", "CountReport", "count_report",
           "check_report", "render_report", "report_to_json", "report_from_json", "unplaced_issues"]

EXPLAINED_SKIPS = frozenset({
    "comment_line", "blank_line", "untouched_default", "old_queue_skip", "reference_data", "old_mailer_setting",
    "log_line", "triage_ignore", "opened_only", "not_a_company", "run_history", "older_version", "derived_decision",
    "outbox_delivery", "label_rubric", "schema_meta",
    "by_override",  # his [skip_record] for a settleable hold (Task 10)
})
MERGE_REASONS = frozenset({"same_company", "same_person", "same_touch", "same_application", "same_posting",
                           "annotation"})
REQUIRED_SOURCES = SOURCES  # every source must give at least one record: error is not empty
FIRST_ISSUES = 50


@dataclass(frozen=True)
class SourceCount:
    read: int
    imported: int
    merged: dict[str, int]
    skipped: dict[str, int]
    held: dict[str, int]


@dataclass(frozen=True)
class CountReport:
    sources: dict[str, SourceCount]
    radar_tables: dict[str, tuple[int, str]]
    would_write: dict[str, int]
    issues: dict[str, int]
    done_set: tuple[str, ...]
    followups_not_planned: int
    first_issues: tuple[str, ...]
    # issues that name no company: no review item can hold them, so the report is where he reads them
    unplaced_issues: tuple[str, ...] = ()


def unplaced_issues(plan: ImportPlan) -> tuple[str, ...]:
    """The issue lines the writer cannot raise as items (no company), in the writer's `unplaced_issues` form."""
    return tuple(f"{i.code} {i.record}: {i.detail}" for i in plan.issues
                 if i.code != "near_name" and i.company_key not in plan.companies)


def count_report(plan: ImportPlan, records: Sequence[SourceRecord], *,
                 written: dict[str, int] | None = None) -> CountReport:
    """Counts per source from the plan's outcomes. `would_write` is the plan's rows per kind, or, after a run,
    the rows the writer added per table (`written`)."""
    counts = {s: {"read": 0, "imported": 0, "merged": Counter(), "skipped": Counter(), "held": Counter()}
              for s in SOURCES}
    tables: dict[str, tuple[int, str]] = {}
    for record in records:
        rid = f"{record.source}:{record.source_key}"
        _, outcome = plan.records[rid]
        c = counts[record.source]
        weight = 1
        if record.source == "radar_db":
            kind, _, name = record.source_key.partition(":")
            if kind == "table":
                rows = int(record.raw.get("rows") or 0)
                tables[name] = (rows, str(record.raw.get("reason")))
                weight = rows
            elif kind in RADAR_ROW_TABLES:
                n, _ = tables.get(kind, (0, "rows"))
                tables[kind] = (n + 1, "rows")
        c["read"] += weight
        if outcome.outcome == "imported":
            c["imported"] += weight
        else:
            c[outcome.outcome][outcome.reason] += weight
    sources = {s: SourceCount(c["read"], c["imported"], dict(sorted(c["merged"].items())),
                              dict(sorted(c["skipped"].items())), dict(sorted(c["held"].items())))
               for s, c in counts.items()}
    if written is not None:
        would = dict(sorted(written.items()))
    else:
        would = {"companies": len(plan.companies), "people": len(plan.people), "touches": len(plan.touches),
                 "applications": len(plan.applications), "boards": len(plan.boards), "postings": len(plan.postings),
                 "issues": len(plan.issues)}
    lines = tuple(f"{i.code} {i.company_key or 'no company'} {i.record}: {i.detail}"
                  for i in plan.issues[:FIRST_ISSUES])
    return CountReport(sources=sources, radar_tables=dict(sorted(tables.items())), would_write=would,
                       issues=dict(sorted(Counter(i.code for i in plan.issues).items())), done_set=plan.done_set,
                       followups_not_planned=plan.followups_not_planned, first_issues=lines,
                       unplaced_issues=unplaced_issues(plan))


def check_report(report: CountReport) -> list[str]:
    """One plain line per failure; an empty list means the run may go ahead."""
    failures = []
    for source in REQUIRED_SOURCES:
        c = report.sources.get(source)
        if c is None or c.read == 0:
            failures.append(f"{source}: no records at all; a required source never counts as empty")
    for source, c in report.sources.items():
        total = c.imported + sum(c.merged.values()) + sum(c.skipped.values()) + sum(c.held.values())
        if c.read != total:
            failures.append(f"{source}: read {c.read} but accounted for {total}")
        for reason in sorted(set(c.skipped) - EXPLAINED_SKIPS):
            failures.append(f"{source}: {c.skipped[reason]} skipped for {reason!r}, which is not an explained skip")
        for reason in sorted(set(c.merged) - MERGE_REASONS):
            failures.append(f"{source}: {c.merged[reason]} merged for {reason!r}, which is not a merge reason")
        for code, n in c.held.items():
            failures.append(f"{source}: {n} held ({code}); settle them before the real run")
    return failures


def _by_reason(counts: dict[str, int]) -> str:
    return ", ".join(f"{k} {v}" for k, v in counts.items()) if counts else "none"


def render_report(report: CountReport) -> str:
    out = []
    for source in SOURCES:
        c = report.sources.get(source)
        if c is None:
            out.append(f"{source}: no records")
            continue
        out += [f"{source}",
                f"  read {c.read}, imported {c.imported}",
                f"  merged: {_by_reason(c.merged)}",
                f"  skipped: {_by_reason(c.skipped)}",
                f"  held: {_by_reason(c.held)}"]
    out.append("Radar tables")
    out += [f"  {name}: {rows} {reason}" for name, (rows, reason) in report.radar_tables.items()]
    out.append("Rows: " + _by_reason(report.would_write))
    out.append(f"Done set ({len(report.done_set)}): {', '.join(report.done_set) or 'none'}")
    out.append("Issues: " + _by_reason(report.issues))
    out.append(f"Follow-ups not planned: due before the switch: {report.followups_not_planned}")
    out.append(f"Issues with no company (no review item, read them here): {len(report.unplaced_issues)}")
    out += [f"  {line}" for line in report.unplaced_issues]
    failures = check_report(report)
    if failures:
        out.append(f"Unexplained losses or holds: {len(failures)}")
        out += [f"  {line}" for line in failures]
    else:
        out.append("Unexplained losses: 0")
    if report.first_issues:
        out.append(f"First issues ({len(report.first_issues)})")
        out += [f"  {line}" for line in report.first_issues]
    return "\n".join(out)


def report_to_json(report: CountReport) -> dict:
    return {
        "sources": {s: {"read": c.read, "imported": c.imported, "merged": c.merged, "skipped": c.skipped,
                        "held": c.held} for s, c in report.sources.items()},
        "radar_tables": {k: [rows, reason] for k, (rows, reason) in report.radar_tables.items()},
        "would_write": report.would_write, "issues": report.issues, "done_set": list(report.done_set),
        "followups_not_planned": report.followups_not_planned, "first_issues": list(report.first_issues),
        "unplaced_issues": list(report.unplaced_issues),
    }


def report_from_json(obj: dict) -> CountReport:
    return CountReport(
        sources={s: SourceCount(c["read"], c["imported"], dict(c["merged"]), dict(c["skipped"]), dict(c["held"]))
                 for s, c in obj["sources"].items()},
        radar_tables={k: (int(v[0]), str(v[1])) for k, v in obj["radar_tables"].items()},
        would_write=dict(obj["would_write"]), issues=dict(obj["issues"]), done_set=tuple(obj["done_set"]),
        followups_not_planned=int(obj["followups_not_planned"]), first_issues=tuple(obj["first_issues"]),
        unplaced_issues=tuple(obj.get("unplaced_issues", ())))
