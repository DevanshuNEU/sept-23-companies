"""The judge's run: pick the companies, read their pages, ask the model once each, check and store (Part of #6 and #15).

Who: the top `[judge] top_n` companies of the stored ranking (hidden ones and companies no longer listed are left
out), or the companies he names. A company is due when it has no judgment, its judgment is `cache_days` old or older,
the judge version changed (prompt, model or his profile), or its best posting changed. A company whose last answer was
refused under this version waits out the window too. At most `per_run` companies a run, in ranked order, each at most
one call: 15 calls a night at the most.

What it reads (`gather`): the homepage (`https://<domain>/`) and the about page it links to, and the best posting:
its stored body when the crawler has one, else its page. A page read inside the cache window is never read again: the
judgment stores the page text, and a due company inside the window reuses it. After the window the pages are read
again; when the version, the best posting and every page are as they were (the cache key), the judgment is stored
again as reused, with no call.

How a call ends (`classify_run`, the reader's): ok (checked by `validate.check_judgment` and stored, or refused and
recorded as a `judge_refused` event); a session limit or a login problem stops the run at once; a failed call
(non-zero exit, timeout, unreadable reply) is counted, and `max_consecutive_failures` failed or refused answers in a
row stop the run. A store error is raised. The model never sees the store; code writes only `judged` and
`judge_refused` events.

Off (`[judge] enabled = false`, as shipped) touches nothing. A dry run lists the due companies and what would be read,
and fetches, calls and writes nothing.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from outreach.judge.config import JudgeConfig
from outreach.judge.fetch import unavailable
from outreach.judge.prompt import judge_version, render_prompt, user_message
from outreach.judge.store import cache_key, pages_hash, posting_key, record_judgment
from outreach.judge.validate import Refused, check_judgment, parse_reply
from outreach.ranking.common import canonical_ids, fact_order_key
from outreach.ranking.confidence import scrub_text
from outreach.ranking.judged import Judgment, load_judgments
from outreach.ranking.scores import ranked_list
from outreach.reader.night import READER_CWD, RunResult, Runner, _claude_binary, classify_run, claude_command
from outreach.store import append_event, describe_error
from outreach.store.timefmt import ISO_FMT
from outreach.store.writes import name_key

__all__ = ["REFUSED_KIND", "OFF_LINE", "Target", "JudgeSummary", "select_targets", "gather", "run_judge",
           "judge_runner"]

REFUSED_KIND = "judge_refused"
OFF_LINE = ("judge is off; set [judge] enabled = true in outreach/judge/config.toml to turn it on "
            "(python -m outreach.judge eval checks it first)")
_LISTED = ("candidate", "backlog", "working")
_MAX_DETAIL = 300


@dataclass(frozen=True)
class Target:
    company_id: int
    name: str
    rank: int
    domain: str | None
    posting: dict | None  # posting_id, title, url, location
    body: str | None  # the posting's stored body, when the crawler has one
    latest: Judgment | None
    why: str  # why it is due, or why it is not


@dataclass(frozen=True)
class JudgeSummary:
    status: str  # off, dry_run, no_targets, done, session_limit, auth, failed
    due: int = 0
    judged: int = 0
    reused: int = 0
    refused: int = 0
    failures: int = 0
    calls: int = 0
    reason: str = ""
    lines: tuple[str, ...] = field(default=())

    def line(self) -> str:
        if self.status == "off":
            return OFF_LINE
        if self.status == "dry_run":
            return f"judge: dry run, {self.due} due; nothing was fetched, called or written"
        text = (f"judge: {self.status}, {self.judged} judged, {self.reused} reused, {self.refused} refused, "
                f"{self.failures} failed calls, {self.calls} model calls")
        return text + (f"; {self.reason}" if self.reason else "")


def _short(text: str) -> str:
    line = " ".join(str(text).split())
    if len(line) > _MAX_DETAIL:
        line = line[:_MAX_DETAIL - 3] + "..."
    return line.encode("ascii", "backslashreplace").decode("ascii")


def _iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime(ISO_FMT)


def _age_days(at: str, now: datetime) -> float:
    try:
        then = datetime.strptime(at, ISO_FMT).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return float("inf")
    return (now - then).total_seconds() / 86400


def _plain_domain(value: str) -> str | None:
    host = (value or "").strip().lower().removeprefix("www.")
    if not host or "." not in host or any(c in host for c in "/:?#@ \t"):
        return None
    return host


def _domains(conn: sqlite3.Connection, canon: dict[int, int], survivors: set[int]) -> dict[int, str]:
    """Each survivor's best domain that is more than a guess and not shared with other employers."""
    shared = {r[0] for r in conn.execute("SELECT value FROM shared_key WHERE kind = 'domain'")}
    best: dict[int, sqlite3.Row] = {}
    for r in conn.execute("SELECT evidence_id, company_id, value, confidence, locked, recorded_at FROM evidence "
                          "WHERE fact = 'domain' AND confidence <> 'guess'"):
        cid = canon.get(r["company_id"], r["company_id"])
        if cid not in survivors or r["value"] in shared:
            continue
        if cid not in best or fact_order_key(r) > fact_order_key(best[cid]):
            best[cid] = r
    return {cid: d for cid, r in best.items() if (d := _plain_domain(r["value"])) is not None}


def _postings(conn: sqlite3.Connection, ids: set[int]) -> dict[int, sqlite3.Row]:
    if not ids:
        return {}
    marks = ", ".join("?" for _ in ids)
    return {r["posting_id"]: r for r in conn.execute(
        f"SELECT p.posting_id, p.title, p.url, p.location, b.body FROM posting p "
        f"LEFT JOIN posting_body b ON b.posting_id = p.posting_id WHERE p.posting_id IN ({marks})", sorted(ids))}


def _refused(conn: sqlite3.Connection, canon: dict[int, int], version: str, now: datetime,
             days: int) -> dict[int, float]:
    """Companies whose answer was refused under `version` inside the window, with the age in days."""
    out: dict[int, float] = {}
    for r in conn.execute(f"SELECT company_id, at FROM event WHERE entity = 'company' AND kind = '{REFUSED_KIND}' "
                          "AND company_id IS NOT NULL AND json_extract(detail, '$.version') = ?", (version,)):
        age = _age_days(r["at"], now)
        if age < days:
            cid = canon.get(r["company_id"], r["company_id"])
            out[cid] = min(age, out.get(cid, age))
    return out


def select_targets(conn: sqlite3.Connection, canon: dict[int, int], cfg: JudgeConfig, *, now: datetime,
                   version: str, names: Sequence[str] | None = None) -> tuple[list[Target], list[Target]]:
    """(due, not due), each in ranked order. Without `names` only the top `top_n` are looked at; with them, the
    listed companies of those names at any rank."""
    listed = {r[0] for r in conn.execute(
        f"SELECT company_id FROM company WHERE merged_into IS NULL AND status IN ({', '.join('?' for _ in _LISTED)})",
        _LISTED)}
    names_of = {r[0]: r[1] for r in conn.execute("SELECT company_id, name FROM company")}
    ranked: list[tuple[int, int, dict]] = []
    seen: set[int] = set()
    for s in ranked_list(conn):
        cid = canon.get(s.company_id, s.company_id)
        if cid in seen or cid not in listed:
            continue
        seen.add(cid)
        ranked.append((len(ranked) + 1, cid, s.signals))
    if names is not None:
        wanted = {name_key(n) for n in names}
        ranked = [r for r in ranked if name_key(names_of.get(r[1], "")) in wanted]
    else:
        ranked = ranked[:cfg.top_n]
    ids = {cid for _rank, cid, _s in ranked}
    domains = _domains(conn, canon, ids)
    postings = _postings(conn, {s["best_posting_id"] for _r, _c, s in ranked
                                if isinstance(s.get("best_posting_id"), int)})
    latest = load_judgments(conn, canon, with_pages=True)
    refused = _refused(conn, canon, version, now, cfg.cache_days)
    due, fresh = [], []
    for rank, cid, signals in ranked:
        row = postings.get(signals.get("best_posting_id"))
        posting = None if row is None else {"posting_id": row["posting_id"], "title": scrub_text(row["title"]),
                                            "url": row["url"] or "", "location": row["location"] or ""}
        j = latest.get(cid)
        if j is None:
            why = "not judged yet"
        elif _age_days(j.at, now) >= cfg.cache_days:
            why = f"judged {int(_age_days(j.at, now))} days ago"
        elif j.version != version:
            why = "the judge changed"
        elif j.posting_key != posting_key(posting):
            why = "the best posting changed"
        else:
            why = ""
        if why and cid in refused:
            why, is_due = f"its last answer was refused {int(refused[cid])} days ago", False
        else:
            is_due = bool(why)
        target = Target(company_id=cid, name=names_of.get(cid, ""), rank=rank, domain=domains.get(cid),
                        posting=posting, body=(row["body"] if row is not None and row["body"] else None), latest=j,
                        why=why or f"judged {int(_age_days(j.at, now))} days ago, still fresh")
        (due if is_due else fresh).append(target)
    return due, fresh


def _fresh_page(page: dict | None, now: datetime, cfg: JudgeConfig) -> bool:
    return page is not None and _age_days(page.get("fetched_at", ""), now) < cfg.cache_days


def gather(target: Target, reader, cfg: JudgeConfig, now: datetime) -> list[dict]:
    """The pages for one company: reused when read inside the window, else read now (see the module text)."""
    at = _iso(now)
    old = {p.get("kind"): p for p in (target.latest.pages if target.latest is not None else ())}
    pages: list[dict] = []
    home_url = f"https://{target.domain}/" if target.domain else None
    if home_url is None:
        pages.append(unavailable("home", "", "no domain on record", at).page)
    elif _fresh_page(old.get("home"), now, cfg) and old["home"].get("url", "").startswith(home_url.rstrip("/")):
        pages.append(old["home"])
        if _fresh_page(old.get("about"), now, cfg):
            pages.append(old["about"])
    else:
        home = reader.read(home_url, "home")
        pages.append(home.page)
        about = reader.about_url(home)
        if about is not None:
            pages.append(reader.read(about, "about").page)
    posting = target.posting
    if posting is not None:
        url = posting["url"]
        if target.body:
            text = " ".join(target.body.split())[:cfg.page_max_chars]
            pages.append({"kind": "posting", "url": url, "status": "ok", "why": "", "text": text, "fetched_at": at,
                          "sha256": ""})
        elif _fresh_page(old.get("posting"), now, cfg) and old["posting"].get("url") == url:
            pages.append(old["posting"])
        elif url:
            pages.append(reader.read(url, "posting").page)
    return pages


def _for_model(pages: list[dict]) -> list[dict]:
    return [{k: p.get(k, "") for k in ("kind", "url", "status", "why", "text")} for p in pages]


class _Run:
    def __init__(self, conn, runner: Runner, reader, cfg: JudgeConfig, me_text: str, now: datetime):
        self.conn, self.runner, self.reader, self.cfg, self.now = conn, runner, reader, cfg, now
        self.prompt = render_prompt(me_text, cfg)
        self.version = judge_version(self.prompt, cfg)
        self.judged = self.reused = self.refused = self.failures = self.calls = 0
        self.lines: list[str] = []

    def summary(self, status: str, due: int, reason: str = "") -> JudgeSummary:
        return JudgeSummary(status, due, self.judged, self.reused, self.refused, self.failures, self.calls, reason,
                            tuple(self.lines))

    def one(self, t: Target) -> tuple[str, str]:
        """How one company went: judged, reused, refused, failed, session_limit or auth, and the detail."""
        pages = gather(t, self.reader, self.cfg, self.now)
        pkey = posting_key(t.posting)
        key = cache_key(self.version, pkey, pages_hash(pages))
        if t.latest is not None and t.latest.key == key:
            detail = json.loads(self.conn.execute("SELECT detail FROM event WHERE event_id = ?",
                                                  (t.latest.event_id,)).fetchone()[0])
            record_judgment(self.conn, t.company_id, detail, version=self.version, key=key, posting=pkey,
                            pages=pages, reused_from=t.latest.event_id, at=_iso(self.now))
            return "reused", f"fit {detail['fit']}, nothing it read had changed"
        message = user_message({"company_id": t.company_id, "name": t.name, "domain": t.domain},
                               None if t.posting is None else {k: t.posting[k] for k in ("title", "url", "location")},
                               _for_model(pages))
        self.calls += 1
        try:
            result = self.runner(self.prompt, message, self.cfg.model)
        except Exception as err:  # noqa: BLE001 - a timeout or a missing binary: a failed call, never the run's end
            return "failed", _short(describe_error(err))
        if not isinstance(result, RunResult):
            return "failed", f"the runner returned {type(result).__name__}, not a RunResult"
        outcome = classify_run(result, session_limit_patterns=self.cfg.session_limit_patterns,
                               auth_patterns=self.cfg.auth_patterns)
        if outcome != "ok":
            return outcome, _short(f"exit {result.returncode}: {result.stderr.strip() or result.stdout.strip()}")
        try:
            fields = check_judgment(parse_reply(result.stdout), company_id=t.company_id, pages=pages)
        except Refused as err:
            why = _short(str(err))
            append_event(self.conn, entity="company", kind=REFUSED_KIND, actor="claude", entity_id=t.company_id,
                         company_id=t.company_id, at=_iso(self.now),
                         detail={"by": "judge", "version": self.version, "why": why,
                                 "text": f"Judge: its answer was refused ({why})"})
            return "refused", why
        record_judgment(self.conn, t.company_id, fields, version=self.version, key=key, posting=pkey, pages=pages,
                        at=_iso(self.now))
        return "judged", f"fit {fields['fit']}" + (", thin evidence" if fields["thin"] else "")

    def loop(self, targets: list[Target]) -> tuple[str, str]:
        in_a_row = 0
        for t in targets:
            outcome, detail = self.one(t)
            self.lines.append(f"{t.rank}. {t.name} (company {t.company_id}): {outcome}, {detail}")
            if outcome in ("session_limit", "auth"):
                return outcome, detail
            if outcome in ("judged", "reused"):
                in_a_row = 0
                if outcome == "judged":
                    self.judged += 1
                else:
                    self.reused += 1
                continue
            if outcome == "refused":
                self.refused += 1
            else:
                self.failures += 1
            in_a_row += 1
            if in_a_row >= self.cfg.max_consecutive_failures:
                return "failed", f"{in_a_row} failed or refused answers in a row; last: {t.name}: {detail}"
        return "done", ""


def run_judge(conn: sqlite3.Connection, runner: Runner, reader, *, cfg: JudgeConfig, me_text: str,
              now: datetime | None = None, limit: int | None = None, names: Sequence[str] | None = None,
              dry_run: bool = False) -> JudgeSummary:
    """One run (see the module text). Off unless `cfg.enabled` is true, except a dry run, which only lists."""
    if not dry_run and cfg.enabled is not True:
        return JudgeSummary("off")
    now = now or datetime.now(timezone.utc)
    work = _Run(conn, runner, reader, cfg, me_text, now)
    canon = canonical_ids(conn)
    due, _fresh = select_targets(conn, canon, cfg, now=now, version=work.version, names=names)
    if names is not None:
        found = {name_key(t.name) for t in due + _fresh}
        work.lines += [f"{n}: not on the ranked list (hidden, not listed, or no such company)" for n in names
                       if name_key(n) not in found]
        work.lines += [f"{t.rank}. {t.name} (company {t.company_id}): not due, {t.why}" for t in _fresh]
    cap = cfg.per_run if limit is None else max(0, min(limit, cfg.per_run))
    chosen = due[:cap]
    if dry_run:
        for t in chosen:
            reads = [f"https://{t.domain}/ and its about page" if t.domain else "no homepage (no domain on record)"]
            if t.posting is not None:
                reads.append("the stored posting body" if t.body else (t.posting["url"] or "no posting url"))
            work.lines.append(f"{t.rank}. {t.name} (company {t.company_id}): {t.why}; would read {', '.join(reads)} "
                              "(pages read inside the window are reused)")
        return work.summary("dry_run", len(due))
    if not chosen:
        return work.summary("no_targets", 0)
    status, reason = work.loop(chosen)
    return work.summary(status, len(due), reason)


def judge_runner(cfg: JudgeConfig, *, cwd: Path | None = None) -> Runner:
    """The real Runner: the reader's `claude -p` command (no tools at all, no MCP, no skills, no CLAUDE.md, no saved
    session: the pages are in the message) run from the reader's neutral folder, killed after `call_timeout_s`. No
    test runs it."""
    folder = Path(cwd) if cwd is not None else READER_CWD

    def run(system_prompt: str, message: str, model: str) -> RunResult:
        folder.mkdir(parents=True, exist_ok=True)
        done = subprocess.run(claude_command(model, system_prompt, _claude_binary()), input=message,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=folder,
                              timeout=cfg.call_timeout_s, check=False)
        return RunResult(done.returncode, done.stdout or "", done.stderr or "")

    return run
