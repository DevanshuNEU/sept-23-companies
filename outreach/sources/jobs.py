"""The discover job (#31 Task 8): run each source, resolve its candidates, set work tiers, keep its posts.

One `job_run` row of job `discover`. A source that raises is an error in its own report and the others still run
(error is not empty: a source that fails before yielding anything is never "0 candidates, ok"). A candidate that
fails on the store or on its own data is listed in `failed` and the run goes on. Each candidate is one transaction:
its company, aliases, evidence, work tier and source link land together or not at all.

A source that errors partway keeps the companies it already resolved, and its posts are not written until a re-run:
`posts()` describes a finished pass. `source.last_run_id` names the latest run that used the source, so it changes on
every re-run by design; the table holds one row per source, not a history.
"""
from __future__ import annotations

import importlib
import sqlite3
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from dataclasses import asdict, dataclass, field

from outreach import identity
from outreach.identity import StoreResolveReport, resolve_store
from outreach.sources.base import Source
from outreach.sources.boards import BoardRef, add_board
from outreach.sources.careers import CareersChecker, CareersResult
from outreach.sources.keys import classify_domain, registrable_domain
from outreach.sources.stapply import StapplyDirectory
from outreach.sources.tiers import effective_tier, register_source, set_work_tier
from outreach.store import (append_event, describe_error, finish_job, is_shared_key, iso_utc, mark_shared_key,
                            start_job, transaction, utcnow)

__all__ = ["SourceReport", "run_discover", "run_resolve", "store_name_lookup", "POST_TEXT_CAP", "FindBoardsReport",
           "run_find_boards"]

MIN_LOOKUPS_FOR_ALL_FAILED = 3  # one or two failed lookups prove nothing; three of three is a pattern

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
    version: str = ""  # the source version this run used (also in `source.version`)
    notes: dict[str, int] = field(default_factory=dict)  # the source's own summary(), such as the LCA fy_mismatch


_RAW_ALIAS_SOURCES = ("fein", "ats", "linkedin")  # aliases that are not names
_MIN_RELATED = identity.MIN_NAME_KEY


def store_name_lookup(conn: sqlite3.Connection) -> Callable[[str], bool]:
    """A `name_lookup` for sources that fetch more about an issuer only when the store might know it (Form D).

    True when a stored name key (a `company_name_key` row, or an alias that is a name) equals the key, is a prefix
    of it ("nimbusrobotics" for "nimbusroboticsholdings": the SEC name often adds a word) or starts with it
    ("orchardlabs" for "orchard"). A key under 4 characters never matches, as in identity. A false yes costs one
    request; a false no would lose the FEIN path, so this errs wide. It reads the store at each call, so companies
    added while the source runs count.
    """
    def lookup(key: str) -> bool:
        if len(key) < _MIN_RELATED:
            return False
        prefixes = [key[:n] for n in range(_MIN_RELATED, len(key) + 1)]
        marks = ", ".join("?" for _ in prefixes)
        raw = ", ".join("?" for _ in _RAW_ALIAS_SOURCES)
        upper = key + "~"  # above every character a name key holds ([a-z0-9])
        return conn.execute(
            f"SELECT 1 FROM company_name_key WHERE name_key IN ({marks}) OR (name_key >= ? AND name_key < ?) "
            f"UNION ALL SELECT 1 FROM company_alias WHERE source NOT IN ({raw}) "
            f"AND (alias_key IN ({marks}) OR (alias_key >= ? AND alias_key < ?)) LIMIT 1",
            (*prefixes, key, upper, *_RAW_ALIAS_SOURCES, *prefixes, key, upper)).fetchone() is not None

    return lookup


def _store_fein_check(conn: sqlite3.Connection, source_name: str) -> Callable[[str], bool]:
    """Whether the source's record is linked to a company that already holds a fein alias (from any source): there is
    nothing more to look up about it. A linked company with no fein alias is not known, so a FEIN missed once (a
    failed lookup) is looked up again."""
    def known(source_ref: str) -> bool:
        for (company_id,) in conn.execute("SELECT company_id FROM company_source WHERE source_name = ? AND "
                                          "source_ref = ?", (source_name, source_ref)).fetchall():
            ids = {company_id, identity.follow_merged(conn, company_id)}
            marks = ", ".join("?" for _ in ids)
            if conn.execute(f"SELECT 1 FROM company_alias WHERE source = 'fein' AND company_id IN ({marks})",
                            tuple(ids)).fetchone():
                return True
        return False

    return known


POST_FAILURE = "post "  # a `failed` entry for a post starts with this; a candidate's starts with its source_ref


def _discover_one(conn: sqlite3.Connection, source: Source, run_id: int, creates: bool, since: str | None,
                  report: SourceReport) -> None:
    """Everything for one source; raises on a source-level failure (the caller records it)."""
    register_source(conn, source.name, source.version, run_id)
    report.version = source.version
    bind = getattr(source, "bind_name_lookup", None)
    if bind is not None:
        bind(store_name_lookup(conn))
    bind_fein = getattr(source, "bind_fein_known", None)
    if bind_fein is not None:
        bind_fein(_store_fein_check(conn, source.name))
    shared = getattr(source, "shared_keys", None)
    if shared is not None:
        keys = shared()
        with transaction(conn):
            for key in keys:
                mark_shared_key(conn, key.kind, key.value, key.reason)
    tier_of = getattr(source, "work_tier", None)
    resolved: dict[str, int] = {}  # source_ref -> company id, for the candidates that resolved
    yielded: set[str] = set()
    in_flight = True  # an error is on its way out unless the loop finishes
    try:
        for cand in source.discover(since):
            report.candidates += 1
            yielded.add(cand.source_ref)
            try:
                with transaction(conn):
                    res = identity.resolve_candidate(conn, cand, source_name=source.name, creates=creates)
                    if res is not None and tier_of is not None:
                        tier = tier_of(cand.source_ref)
                        if tier is not None:
                            set_work_tier(conn, res.company_id, tier[0], tier[1], source.version)
            except (sqlite3.Error, ValueError) as err:
                report.failed.append(f"{cand.source_ref or cand.name}: {err}")
                continue
            if res is None:
                report.skipped_unmatched += 1
                continue
            resolved[cand.source_ref] = res.company_id
            report.created += res.created
            report.attached += not res.created
            report.items_raised += res.items_raised
            report.evidence_added += res.evidence_added
        in_flight = False
    finally:
        summary = getattr(source, "summary", None)
        if summary is not None:
            try:
                report.notes = dict(summary())
            except Exception:
                if not in_flight:  # a clean pass whose summary fails is a source error; never hide it
                    raise
                # else the failure already on its way out is the one to report
    posts = getattr(source, "posts", None)
    if posts is not None:
        _write_posts(conn, source.name, posts(), resolved, yielded, report)


def _write_posts(conn: sqlite3.Connection, source_name: str, posts, resolved: dict[str, int], yielded: set[str],
                 report: SourceReport) -> None:
    """One `company_post` row per post of a candidate that resolved; a post seen before adds nothing. A post of a
    candidate that failed or went unmatched is skipped (the candidate is already counted); a post whose
    `source_ref` was never yielded is listed in `failed`."""
    for post in posts:
        company_id = resolved.get(post.source_ref)
        if company_id is None:
            if post.source_ref not in yielded:
                report.failed.append(f"{POST_FAILURE}{post.url}: unknown source_ref")
            continue
        try:
            posted_at = iso_utc(post.posted_at) if post.posted_at else None
            cur = conn.execute(
                "INSERT INTO company_post(company_id, source_name, url, posted_at, text, asks_email) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(source_name, url) DO NOTHING",
                (company_id, source_name, post.url, posted_at, post.text[:POST_TEXT_CAP], int(bool(post.asks_email))))
        except (sqlite3.Error, ValueError) as err:
            report.failed.append(f"{POST_FAILURE}{post.url}: {err}")
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
    errors: list[tuple[str, str]] = []  # one per source that errored, in run order (names may repeat)
    for source in sources:
        report = reports.setdefault(source.name, SourceReport())
        may_create = (creates or {}).get(source.name, getattr(source, "creates_companies", True))
        try:
            _discover_one(conn, source, run_id, may_create, since, report)
        except Exception as err:  # a source failing must not stop the others; it is reported, never swallowed
            text = describe_error(err)
            report.error = "; ".join(t for t in (report.error, text) if t)
            errors.append((source.name, text))
    problems = [f"{name}: {text}" for name, text in errors]
    for name, report in reports.items():
        candidates = sum(not f.startswith(POST_FAILURE) for f in report.failed)
        if candidates:
            problems.append(f"{candidates} candidates failed in {name}")
        if len(report.failed) > candidates:
            problems.append(f"{len(report.failed) - candidates} posts failed in {name}")
        lookups, lookup_errors = report.notes.get("fetches", 0), report.notes.get("fetch_errors", 0)
        if lookups >= MIN_LOOKUPS_FOR_ALL_FAILED and lookup_errors == lookups:  # every one failed: a pattern
            problems.append(f"{lookups} lookups failed in {name}")
        unreadable = report.notes.get("unreadable", 0)
        if unreadable:  # Form D's count of filings that did not read; a stray one is fine, never silent
            problems.append(f"{unreadable} unreadable Form D filings")
    if sources and len(errors) == len(sources):
        status = "failed"
    elif problems:
        status = "partial"
    else:
        status = "ok"
    finish_job(conn, run_id, status, {name: asdict(r) for name, r in reports.items()}, "; ".join(problems))
    return run_id, reports


def run_resolve(conn: sqlite3.Connection) -> tuple[int, StoreResolveReport]:
    """The `resolve` job: `identity.resolve_store` under one `job_run`. Returns the run id and the report.

    Status `ok`; `partial` when some items failed (each is in the report and the error text); `failed` when every
    item failed. A pass that cannot run at all is recorded `failed` with its error and the error is raised: never an
    empty report.
    """
    run_id = start_job(conn, "resolve")
    try:
        report = resolve_store(conn)
    except BaseException as err:
        try:
            finish_job(conn, run_id, "failed", {}, describe_error(err))
        except Exception:
            pass  # the run stays 'running' and the original error is the one raised; this one must not replace it
        raise
    error = f"{len(report.failed)} failed: " + "; ".join(report.failed[:5]) if report.failed else ""
    if not report.failed:
        status = "ok"
    elif len(report.failed) == report.attempted:
        status = "failed"  # every item failed: nothing was raised, nothing was already there
    else:
        status = "partial"
    finish_job(conn, run_id, status, asdict(report), error)
    return run_id, report


@dataclass
class FindBoardsReport:
    considered: int = 0  # live companies with no active board and no watched page, tier 3 not counted
    from_alias: int = 0  # boards made from an `ats` alias
    from_stapply: int = 0  # boards made from a stapply list match
    skipped_tier3: int = 0
    conflicts: int = 0  # boards another company already holds (one merge item per pair)
    from_careers: int = 0  # boards made from a careers page naming exactly one
    watched_pages: int = 0  # watched pages added (a careers page with no board, or no crawler adapter for the boards)
    careers_checked: int = 0  # companies whose careers page was fetched this run
    careers_none: int = 0  # definite "no careers page": marked, not fetched again for 30 days
    careers_errors: int = 0  # fetch errors, 5xx and 429: nothing concluded, no marker, asked again next run
    careers_skipped: int = 0  # the company's only domain is a shared key (a parent's portal)
    domain_missing: int = 0  # tier 1 companies with no usable domain: one item each
    ambiguous_boards: int = 0  # a careers page naming two or more boards: none added, the page is watched instead
    adapter_unknown: int = 0  # companies not checked for a crawler adapter: `outreach.crawl.ats` does not exist yet
    unreadable_boards: int = 0  # boards with no adapter and no page to watch: nobody reads these companies
    bad_aliases: list[str] = field(default_factory=list)  # "<company id>: <alias>": an `ats` alias that is no board
    failed: list[str] = field(default_factory=list)
    version: str = ""  # the stapply version used, "" when no directory was given


def _company_names(conn: sqlite3.Connection, company_id: int, name: str) -> list[str]:
    names = [name]
    names += [alias for (alias,) in conn.execute(
        "SELECT alias FROM company_alias WHERE company_id = ? AND source IN ('legal_name', 'dba') ORDER BY alias_key",
        (company_id,))]
    return names


def _shared_domain(conn: sqlite3.Connection, domain: str) -> bool:
    root = registrable_domain(domain)
    return is_shared_key(conn, "domain", domain) or (root is not None and is_shared_key(conn, "domain", root))


def _company_domains(conn: sqlite3.Connection, company_id: int) -> tuple[list[str], list[str]]:
    """(usable, shared): the company's company-class domains from `domain` evidence, best first (locked, then
    confirmed, then the oldest row). Usable ones are not a shared key (a parent's portal, a PEO) and not a `guess`;
    the shared ones are listed so a caller can tell "the only domain is shared" from "no domain"."""
    usable: list[str] = []
    shared: list[str] = []
    for value, confidence in conn.execute(
            "SELECT value, confidence FROM evidence WHERE company_id = ? AND fact = 'domain' "
            "ORDER BY locked DESC, confidence = 'confirmed' DESC, evidence_id", (company_id,)):
        domain = value[4:] if value.startswith("www.") and "." in value[4:] else value
        if classify_domain(domain) != "company":
            continue
        if _shared_domain(conn, domain):
            if domain not in shared:
                shared.append(domain)
        elif confidence != "guess" and domain not in usable:
            usable.append(domain)
    return usable, shared


def _live_companies(conn: sqlite3.Connection, where: str) -> list[sqlite3.Row]:
    """Live companies (candidate, backlog or working; not merged) matching `where`, by effective tier then id."""
    rows = conn.execute(
        "SELECT c.company_id, c.name FROM company c WHERE c.status IN ('candidate', 'backlog', 'working') "
        "AND c.merged_into IS NULL AND " + where + " ORDER BY c.company_id").fetchall()
    return sorted(rows, key=lambda r: (effective_tier(conn, r["company_id"]), r["company_id"]))


NO_BOARD = "NOT EXISTS (SELECT 1 FROM board b WHERE b.company_id = c.company_id AND b.active = 1)"


def _alias_boards(conn: sqlite3.Connection, company_id: int, report: FindBoardsReport) -> int:
    """Step 0: every `ats` alias becomes a board (`found_by` hand for a by-hand company, else import). Returns the
    boards added."""
    found_by = "hand" if conn.execute("SELECT 1 FROM company_source WHERE company_id = ? AND source_name = 'hand'",
                                      (company_id,)).fetchone() else "import"
    added = 0
    for (alias,) in conn.execute("SELECT alias FROM company_alias WHERE company_id = ? AND source = 'ats' "
                                 "ORDER BY alias_key", (company_id,)).fetchall():
        ats, _, token = alias.partition(":")
        try:
            ref = BoardRef(ats, token)
        except ValueError:
            report.bad_aliases.append(f"{company_id}: {alias}")
            continue
        result = add_board(conn, company_id, ref, via="alias", matched_on=alias, found_by=found_by)
        report.from_alias += result == "added"
        report.conflicts += result == "conflict"
        added += result == "added"
    return added


def _stapply_boards(conn: sqlite3.Connection, company_id: int, name: str, directory: StapplyDirectory,
                    report: FindBoardsReport) -> int:
    """Step 1: the stapply lookup over the company's names and usable domain. Returns the boards added."""
    usable, _ = _company_domains(conn, company_id)
    added = 0
    for hit in directory.lookup(_company_names(conn, company_id, name), usable[0] if usable else None):
        found_by = "import" if hit.matched_on == "host" else "stapply_name"
        with transaction(conn):
            result = add_board(conn, company_id, hit.ref, via="stapply", matched_on=hit.matched_on, found_by=found_by)
            if result == "added":
                now = utcnow()
                conn.execute(
                    "INSERT INTO company_source(company_id, source_name, source_ref, first_seen_at, last_seen_at) "
                    "VALUES (?, 'stapply', ?, ?, ?) ON CONFLICT DO NOTHING",
                    (company_id, f"{hit.ref.ats}:{hit.ref.token}", now, now))
        report.from_stapply += result == "added"
        report.conflicts += result == "conflict"
        added += result == "added"
    return added


CAREERS_RECHECK_DAYS = 30


def _supported_adapters() -> frozenset[str] | None:
    """The ATS names with a crawler adapter, or None while `outreach.crawl.ats` does not exist (#21)."""
    try:
        return frozenset(importlib.import_module("outreach.crawl.ats").supported_ats())
    except (ImportError, AttributeError):
        return None


def _watch(conn: sqlite3.Connection, company_id: int, url: str, report: FindBoardsReport) -> None:
    """A watched page, due now (no fingerprint, no fetch time), unless the company already has one."""
    report.watched_pages += conn.execute(
        "INSERT INTO watched_page(company_id, url) VALUES (?, ?) ON CONFLICT(company_id) DO NOTHING",
        (company_id, url)).rowcount


def _last_careers_attempt(conn: sqlite3.Connection, company_id: int) -> str:
    """When the careers page was last tried (a check or an error), or "" when never: it sorts first."""
    return conn.execute("SELECT coalesce(max(at), '') FROM event WHERE company_id = ? AND kind IN "
                        "('careers_checked', 'careers_error')", (company_id,)).fetchone()[0]


def _checked_recently(conn: sqlite3.Connection, company_id: int) -> bool:
    cutoff = (datetime.strptime(utcnow(), "%Y-%m-%dT%H:%M:%SZ") - timedelta(days=CAREERS_RECHECK_DAYS)
              ).strftime("%Y-%m-%dT%H:%M:%SZ")
    return conn.execute("SELECT 1 FROM event WHERE company_id = ? AND kind = 'careers_checked' AND at > ?",
                        (company_id, cutoff)).fetchone() is not None


class _Careers:
    """One run's careers checks: the limit, the results already fetched, and what each answer leaves behind."""

    def __init__(self, conn: sqlite3.Connection, checker: CareersChecker | None, max_checks: int,
                 report: FindBoardsReport):
        self.conn, self.checker, self.max_checks, self.report = conn, checker, max_checks, report
        self.results: dict[int, CareersResult] = {}

    def check(self, company_id: int, domain: str) -> CareersResult | None:
        """The careers result, fetched once per run. None when no check was made: no checker, the company was
        marked "no page" in the last 30 days, or the run's limit is used up. A `none` answer leaves the marker (an
        event, one per day); an `error` leaves a `careers_error` event that blocks nothing, so the next run asks
        again."""
        if self.checker is None:
            return None
        if company_id in self.results:
            return self.results[company_id]
        if self.report.careers_checked >= self.max_checks or _checked_recently(self.conn, company_id):
            return None
        result = self.results[company_id] = self.checker.check(domain)
        self.report.careers_checked += 1
        now = utcnow()
        if result.kind == "none":
            self.report.careers_none += 1
            append_event(self.conn, entity="company", kind="careers_checked", actor="crawler", entity_id=company_id,
                         company_id=company_id, detail={"result": "none"},
                         at=now, idempotency_key=f"careers-checked:{company_id}:{now[:10]}")
        elif result.kind == "error":
            # Not a conclusion and never a block: it only records the attempt, so the queue puts this company
            # behind the ones not tried yet.
            self.report.careers_errors += 1
            append_event(self.conn, entity="company", kind="careers_error", actor="crawler", entity_id=company_id,
                         company_id=company_id, detail={"error": result.error}, at=now)
        return result

    def no_domain(self, company_id: int, shared: list[str]) -> None:
        """A company with no usable domain: a shared domain also leaves a `careers_skipped` event; a tier 1 company
        raises one `domain_missing` item either way (the reason names the shared domain, if any); a lower tier raises
        no item."""
        if self.checker is None:
            return
        if shared:
            self.report.careers_skipped += 1
            append_event(self.conn, entity="company", kind="careers_skipped", actor="crawler", entity_id=company_id,
                         company_id=company_id, detail={"domain": shared[0]},
                         idempotency_key=f"careers-skipped:{company_id}:{shared[0]}")
            reason = (f"Its only domain, {shared[0]}, is shared with other employers, so its careers page "
                      "cannot be checked for a board.")
        else:
            reason = "No company domain is known, so its careers page cannot be checked for a board."
        if effective_tier(self.conn, company_id) == 1:
            self.report.domain_missing += self.conn.execute(
                "INSERT INTO review_item(kind, company_id, reason, evidence_key, raised_by, created_at) "
                "VALUES ('domain_missing', ?, ?, 'no-usable-domain', 'crawler', ?) ON CONFLICT DO NOTHING",
                (company_id, reason, utcnow())).rowcount


def _careers_boards(conn: sqlite3.Connection, company_id: int, name: str, careers: _Careers,
                    report: FindBoardsReport) -> int:
    """Step 2: the careers page of a company with no board. Exactly one board on the page is added; two or more
    add none (which one is the company's cannot be told) and a page with none is watched. Returns boards added."""
    usable, shared = _company_domains(conn, company_id)
    if not usable:
        careers.no_domain(company_id, shared)
        return 0
    result = careers.check(company_id, usable[0])
    if result is None or result.kind in ("none", "error"):
        return 0
    if result.kind == "boards" and len(result.boards) == 1:
        ref = result.boards[0]
        with transaction(conn):
            added = add_board(conn, company_id, ref, via="careers", matched_on=result.page_url or "",
                              found_by="careers_page")
            if added == "added":
                now = utcnow()
                conn.execute(
                    "INSERT INTO company_source(company_id, source_name, source_ref, first_seen_at, last_seen_at) "
                    "VALUES (?, 'careers', ?, ?, ?) ON CONFLICT DO NOTHING",
                    (company_id, f"{ref.ats}:{ref.token}", now, now))
        report.conflicts += added == "conflict"
        if added == "added":
            report.from_careers += 1
            return 1
    elif result.kind == "boards":
        report.ambiguous_boards += 1
    if result.page_url:  # a page with no board, an ambiguous one, or a board another company holds
        _watch(conn, company_id, result.page_url, report)
    return 0


def _unread_boards(conn: sqlite3.Connection, careers: _Careers, report: FindBoardsReport) -> None:
    """Companies whose active boards all lack a crawler adapter and which have no watched page get one, so they are
    never silently unread: the careers page, else an iCIMS board's public host, else nothing (`unreadable_boards`).
    A company whose careers check errored is left for the next run. While `outreach.crawl.ats` does not exist every
    such company is counted in `adapter_unknown` and left alone."""
    companies = [r for r in _live_companies(
        conn, "EXISTS (SELECT 1 FROM board b WHERE b.company_id = c.company_id AND b.active = 1) AND NOT EXISTS "
              "(SELECT 1 FROM watched_page w WHERE w.company_id = c.company_id)")
        if effective_tier(conn, r["company_id"]) != 3]
    supported = _supported_adapters()
    if supported is None:
        report.adapter_unknown += len(companies)
        return
    for row in companies:
        company_id = row["company_id"]
        boards = conn.execute("SELECT ats, token FROM board WHERE company_id = ? AND active = 1 ORDER BY board_id",
                              (company_id,)).fetchall()
        if any(b["ats"] in supported for b in boards):
            continue
        try:
            usable, _ = _company_domains(conn, company_id)
            result = careers.check(company_id, usable[0]) if usable else None
            if result is not None and result.kind == "error":
                continue  # nothing concluded: the next run asks again
            url = result.page_url if result is not None and result.kind in ("page", "boards") else None
            if url is None:
                icims = next((b["token"] for b in boards if b["ats"] == "icims"), None)
                url = f"https://{icims}" if icims else None
            if url is None:
                report.unreadable_boards += 1
            else:
                _watch(conn, company_id, url, report)
        except (sqlite3.Error, ValueError) as err:
            report.failed.append(f"#{company_id} {row['name']}: {describe_error(err)}")


def run_find_boards(conn: sqlite3.Connection, *, directory: StapplyDirectory | None,
                    careers: CareersChecker | None = None, max_careers: int = 100) -> tuple[int, FindBoardsReport]:
    """The `find_boards` job: give each live company with no active board and no watched page its boards.

    Companies with no active board go in order of effective tier, then id; tier 3 is skipped unless promoted. Per
    company: (0) an `ats` alias with no board becomes one; (1) only when 0 added nothing, the stapply lookup (a name
    or slug match is `found_by` `stapply_name`, a host match `import`); (2) only when 0 and 1 added nothing, the
    company has no watched page and `careers` is given, at most `max_careers` companies a run, in the order of the
    last careers attempt (oldest first, never tried first, then tier and id): the careers page (see
    `_careers_boards`). A company with a watched page and no board still gets steps 0 and 1, never step 2. With no directory the lookup does not run, and the run
    is `partial` with that said: never a quiet "nothing found". A last pass gives every company whose boards have no
    crawler adapter a watched page (see `_unread_boards`).

    Returns the job run id and the report. Status `partial` also covers a bad alias, a company that failed and any
    careers check that errored (those companies are asked again next run).
    """
    run_id = start_job(conn, "find_boards")
    report = FindBoardsReport()
    problems: list[str] = []
    try:
        if directory is not None:
            register_source(conn, "stapply", directory.version, run_id)
            report.version = directory.version
        else:
            problems.append("no stapply directory: the name lookup did not run")
        if careers is not None:
            register_source(conn, "careers", "careers-1", run_id)
        step = _Careers(conn, careers, max_careers, report)
        needs_careers: list[sqlite3.Row] = []
        for row in _live_companies(conn, NO_BOARD):
            company_id = row["company_id"]
            if effective_tier(conn, company_id) == 3:
                report.skipped_tier3 += 1
                continue
            report.considered += 1
            try:
                found = _alias_boards(conn, company_id, report)
                if directory is not None and not found:
                    found += _stapply_boards(conn, company_id, row["name"], directory, report)
                if not found and not conn.execute("SELECT 1 FROM watched_page WHERE company_id = ?",
                                                  (company_id,)).fetchone():
                    needs_careers.append(row)
            except (sqlite3.Error, ValueError) as err:
                report.failed.append(f"#{company_id} {row['name']}: {describe_error(err)}")
        # The careers queue: the page tried longest ago first (never tried before any), then tier and id.
        needs_careers.sort(key=lambda r: (_last_careers_attempt(conn, r["company_id"]),
                                          effective_tier(conn, r["company_id"]), r["company_id"]))
        for row in needs_careers:
            try:
                _careers_boards(conn, row["company_id"], row["name"], step, report)
            except (sqlite3.Error, ValueError) as err:
                report.failed.append(f"#{row['company_id']} {row['name']}: {describe_error(err)}")
        _unread_boards(conn, step, report)
    except BaseException as err:
        try:
            finish_job(conn, run_id, "failed", asdict(report), describe_error(err))
        except Exception:
            pass  # the original error is the one raised
        raise
    if report.bad_aliases:
        problems.append(f"{len(report.bad_aliases)} ats aliases are not boards: " + ", ".join(report.bad_aliases[:5]))
    if report.failed:
        problems.append(f"{len(report.failed)} companies failed: " + "; ".join(report.failed[:5]))
    if report.careers_errors:
        problems.append(f"{report.careers_errors} careers checks errored (nothing concluded, asked again next run)")
    finish_job(conn, run_id, "partial" if problems else "ok", asdict(report), "; ".join(problems))
    return run_id, report
