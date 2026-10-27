"""The profile judge's stored judgments, read for ranking and for the pages (Part of #6 and #15).

The judge (`outreach.judge`) stores each judgment as one `event` row: entity `company`, kind `judged`, actor `claude`
(a model wrote it), `detail` a JSON object. No table was added for it. This module is the one reader: `load_judgments`
takes the newest judgment of every company in one set-based query (a window over the `judged` events), folds merged
duplicates into their survivor (the newest wins), and checks each detail again, so a row that does not hold a usable
judgment is skipped rather than trusted. It never calls a model and never writes.

The judgment only ever adds ranking points and a visible reason (`signals/profile_fit.py`); nothing here decides a
hide, a no-go, an email or a note.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from outreach.ranking.confidence import scrub_text

__all__ = ["JUDGED_KIND", "DOMAINS", "LEVEL_OK", "TEXT_MAX", "Judgment", "judgment_from_detail", "judge_line",
           "load_judgments"]

JUDGED_KIND = "judged"
# The fixed vocabulary of what a company builds. The judge's output is checked against it.
DOMAINS = ("ai_agents", "llm_apps", "devtools", "mcp", "search_retrieval", "evals", "voice", "full_stack_web", "infra",
           "data", "security", "robotics_hardware", "fintech", "healthcare", "marketing_sales", "recruiting", "other")
LEVEL_OK = ("yes", "no", "unknown")
TEXT_MAX = 300  # builds, why and risk, each

# The newest `judged` event of each company id, in one pass. `pages` (the page text the judge read) is left out unless
# asked for: ranking never needs it.
_LATEST = (
    "SELECT event_id, company_id, at, {detail} AS detail FROM ("
    "SELECT event_id, company_id, at, detail, row_number() OVER (PARTITION BY company_id "
    "ORDER BY at DESC, event_id DESC) AS n FROM event "
    "WHERE entity = 'company' AND kind = '" + JUDGED_KIND + "' AND company_id IS NOT NULL) WHERE n = 1"
)


@dataclass(frozen=True)
class Judgment:
    company_id: int  # the surviving company
    event_id: int
    at: str  # when it was judged (UTC, ISO)
    fit: int  # 0 to 100
    builds: str  # one plain sentence
    domains: tuple[str, ...]
    why: str
    risk: str
    level_ok: str  # yes, no or unknown
    quotes: tuple[str, ...]  # checked against the page text when stored
    thin: bool  # the evidence was thin: no page read, or no quote
    version: str
    key: str  # the cache key: version, best posting and page text
    posting_key: str = ""  # the hash of the best posting it read (title and url)
    reused: bool = False  # stored again with no model call: nothing it read had changed
    pages: tuple[dict, ...] = ()  # only with `with_pages`: what was read, with its text

    @property
    def line(self) -> str:
        return judge_line(self)


def _lower_first(text: str) -> str:
    """"Builds X" as "builds X", but never "AI tools" as "aI tools"."""
    if len(text) > 1 and text[0].isupper() and text[1].islower():
        return text[0].lower() + text[1:]
    return text


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def judge_line(j: Judgment | None) -> str:
    """The one line Today shows: "Judge: builds X. Fit 72. Risk: Y." or "Judge: not judged yet."."""
    if j is None:
        return "Judge: not judged yet."
    return f"Judge: {_lower_first(_sentence(j.builds))} Fit {j.fit}. Risk: {_lower_first(_sentence(j.risk))}"


def _text(value) -> str | None:
    if not isinstance(value, str):
        return None
    text = scrub_text(value)
    return text[:TEXT_MAX] if text else None


def judgment_from_detail(detail: object, *, company_id: int, event_id: int, at: str) -> Judgment | None:
    """A Judgment from an event detail, or None when the detail is not a usable judgment (wrong types, a fit out of
    0 to 100, an unknown level or domain). Every text is one plain line with no long dash."""
    if not isinstance(detail, dict):
        return None
    fit = detail.get("fit")
    if isinstance(fit, bool) or not isinstance(fit, int) or not 0 <= fit <= 100:
        return None
    builds, why, risk = _text(detail.get("builds")), _text(detail.get("why")), _text(detail.get("risk"))
    if builds is None or why is None or risk is None:
        return None
    level = detail.get("level_ok")
    domains = detail.get("domains")
    if level not in LEVEL_OK or not isinstance(domains, list) or not all(d in DOMAINS for d in domains):
        return None
    quotes = detail.get("quotes", [])
    if not isinstance(quotes, list) or not all(isinstance(q, str) for q in quotes):
        return None
    pages = detail.get("pages", [])
    return Judgment(company_id=company_id, event_id=event_id, at=at, fit=fit, builds=builds,
                    domains=tuple(domains), why=why, risk=risk, level_ok=level, quotes=tuple(quotes),
                    thin=detail.get("thin") is not False, version=str(detail.get("version", "")),
                    key=str(detail.get("key", "")), posting_key=str(detail.get("posting_key", "")),
                    reused=detail.get("reused") is True,
                    pages=tuple(p for p in pages if isinstance(p, dict)) if isinstance(pages, list) else ())


def _parse_at(at: str) -> datetime:
    return datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def load_judgments(conn: sqlite3.Connection, canon: dict[int, int], *, today: date | None = None,
                   max_age_days: int | None = None, with_pages: bool = False) -> dict[int, Judgment]:
    """The newest usable judgment of each surviving company (a merged duplicate's counts for its survivor; the newest
    wins). With `today` and `max_age_days`, a judgment from before the start of the UTC day `max_age_days` days before
    `today` is left out. One query."""
    cutoff = None
    if today is not None and max_age_days is not None:
        cutoff = datetime(today.year, today.month, today.day, tzinfo=timezone.utc) - timedelta(days=max_age_days)
    detail = "detail" if with_pages else "json_remove(detail, '$.pages')"
    out: dict[int, Judgment] = {}
    for r in conn.execute(_LATEST.format(detail=detail)):
        try:
            when = _parse_at(r["at"])
            data = json.loads(r["detail"])
        except (TypeError, ValueError):
            continue
        if cutoff is not None and when < cutoff:
            continue
        owner = canon.get(r["company_id"], r["company_id"])
        j = judgment_from_detail(data, company_id=owner, event_id=r["event_id"], at=r["at"])
        if j is None:
            continue
        held = out.get(owner)
        if held is None or (j.at, j.event_id) > (held.at, held.event_id):
            out[owner] = j
    return out
