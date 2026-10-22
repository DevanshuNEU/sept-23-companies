"""The discover job (#31 Task 8): run each source, resolve its candidates, set work tiers, keep its posts.

One `job_run` row of job `discover`. A source that raises is an error in its own report and the others still run
(error is not empty: a source that fails before yielding anything is never "0 candidates, ok"). A candidate that
fails on the store or on its own data is listed in `failed` and the run goes on. Each candidate is one transaction:
its company, aliases, evidence, work tier and source link land together or not at all.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field

from outreach import identity
from outreach.sources.base import Source
from outreach.sources.tiers import register_source, set_work_tier
from outreach.store import finish_job, iso_utc, mark_shared_key, start_job, transaction

__all__ = ["SourceReport", "run_discover", "POST_TEXT_CAP"]

POST_TEXT_CAP = 20_000


@dataclass
class SourceReport:
    candidates: int = 0
    created: int = 0
    attached: int = 0
    skipped_unmatched: int = 0
    items_raised: int = 0
    evidence_added: int = 0
    posts: int = 0
    failed: list[str] = field(default_factory=list)
    error: str = ""
    notes: dict[str, int] = field(default_factory=dict)  # the source's own summary(), such as the LCA fy_mismatch


def _describe(err: BaseException) -> str:
    return f"{type(err).__name__}: {err}"


def _discover_one(conn: sqlite3.Connection, source: Source, run_id: int, creates: bool, since: str | None,
                  report: SourceReport) -> None:
    """Everything for one source; raises on a source-level failure (the caller records it)."""
    register_source(conn, source.name, source.version, run_id)
    shared = getattr(source, "shared_keys", None)
    if shared is not None:
        keys = shared()
        with transaction(conn):
            for key in keys:
                mark_shared_key(conn, key.kind, key.value, key.reason)
    tier_of = getattr(source, "work_tier", None)
    resolved: dict[str, int] = {}  # source_ref -> company id, for the candidates that resolved
    try:
        for cand in source.discover(since):
            report.candidates += 1
            try:
                with transaction(conn):
                    res = identity.resolve_candidate(conn, cand, source_name=source.name, creates=creates)
                    if res is not None and tier_of is not None:
                        tier = tier_of(cand.source_ref)
                        if tier is not None:
                            set_work_tier(conn, res.company_id, tier[0], tier[1], source.version)
            except (sqlite3.Error, ValueError) as err:
                report.failed.append(f"{cand.source_ref}: {err}")
                continue
            if res is None:
                report.skipped_unmatched += 1
                continue
            resolved[cand.source_ref] = res.company_id
            report.created += res.created
            report.attached += not res.created
            report.items_raised += res.items_raised
            report.evidence_added += res.evidence_added
    finally:
        summary = getattr(source, "summary", None)
        if summary is not None:
            try:
                report.notes = dict(summary())
            except Exception:  # the load that failed is the error already being recorded
                pass
    posts = getattr(source, "posts", None)
    if posts is not None:
        _write_posts(conn, source.name, posts(), resolved, report)


def _write_posts(conn: sqlite3.Connection, source_name: str, posts, resolved: dict[str, int],
                 report: SourceReport) -> None:
    """One `company_post` row per post of a candidate that resolved; a post seen before adds nothing."""
    for post in posts:
        company_id = resolved.get(post.source_ref)
        if company_id is None:
            continue
        try:
            posted_at = iso_utc(post.posted_at) if post.posted_at else None
            cur = conn.execute(
                "INSERT INTO company_post(company_id, source_name, url, posted_at, text, asks_email) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(source_name, url) DO NOTHING",
                (company_id, source_name, post.url, posted_at, post.text[:POST_TEXT_CAP], int(bool(post.asks_email))))
        except (sqlite3.Error, ValueError) as err:
            report.failed.append(f"post {post.url}: {err}")
            continue
        report.posts += cur.rowcount


def run_discover(conn: sqlite3.Connection, sources: Sequence[Source], *, creates: dict[str, bool] | None = None,
                 since: str | None = None) -> tuple[int, dict[str, SourceReport]]:
    """Run `discover` for every source. Returns the job run id and one report per source name.

    `creates` overrides a source's `creates_companies` (default True) by source name. Status: `ok`; `partial` when
    any source errored or any candidate or post failed; `failed` when every source errored. The summary is the
    reports as JSON.
    """
    run_id = start_job(conn, "discover")
    reports: dict[str, SourceReport] = {}
    for source in sources:
        report = reports.setdefault(source.name, SourceReport())
        may_create = (creates or {}).get(source.name, getattr(source, "creates_companies", True))
        try:
            _discover_one(conn, source, run_id, may_create, since, report)
        except Exception as err:  # a source failing must not stop the others; it is reported, never swallowed
            report.error = _describe(err)
    errored = {name: r.error for name, r in reports.items() if r.error}
    if sources and len(errored) == len(reports):
        status = "failed"
    elif errored or any(r.failed for r in reports.values()):
        status = "partial"
    else:
        status = "ok"
    finish_job(conn, run_id, status, {name: asdict(r) for name, r in reports.items()},
               "; ".join(f"{name}: {text}" for name, text in errored.items()))
    return run_id, reports
