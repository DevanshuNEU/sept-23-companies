"""Who the morning prepare step works on (#15, Task 12).

`select_prepare_targets` walks the laned ranked list in order and keeps a company that may get a row by the plan's own
rule (`select.eligibility`), has no candidate left to ask (everyone on record was contacted, has a no-go, or there is
nobody), and had no prepare attempt that found nobody in the last `prepare.retry_days` days. Each `Target` is the
payload one `claude -p` call gets: the company, its best role, its top reasons, an HN post that asks for email, and the
names already on record (so the model looks for someone new). Reads only, except `record_attempt`, which writes the
one `event` row an attempt leaves (entity `company`, kind `prepare_attempt`, actor `claude`).
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from outreach.plan.config import PlanConfig
from outreach.plan.dates import ny_date
from outreach.plan.outbound import load_outbound
from outreach.plan.prospects import load_postings, load_prospects
from outreach.plan.select import eligibility
from outreach.ranking.common import fact_order_key
from outreach.ranking.confidence import scrub_text
from outreach.ranking.scores import StoredScore, ranked_list
from outreach.store.writes import append_event

__all__ = ["Target", "POST_TEXT_MAX", "target_count", "select_prepare_targets", "record_attempt"]

POST_TEXT_MAX = 1500
_KNOWN_MAX = 30
_CHUNK = 500


@dataclass(frozen=True)
class Target:
    company_id: int  # the surviving company
    name: str
    domain: str | None  # a domain the store holds as more than a guess, and not shared with other employers
    lane: str  # the strongest lane
    role: dict | None  # {posting_id, title, url, age_days}
    reasons: tuple[str, ...]  # the top 3 signal reasons
    post: dict | None  # {url, text}: an HN post that asks for email, within email.hn_post_days
    known_people: tuple[str, ...]  # names already on record at the company, contacted or not


def target_count(capacity_today: int, cfg: PlanConfig) -> int:
    """`ceil(prepare.multiple * capacity_today)`, never below 0."""
    return max(0, math.ceil(cfg.get("prepare", "multiple") * capacity_today))


def _marks(n: int) -> str:
    return ", ".join("?" for _ in range(n))


def _family(canon: dict[int, int], survivors: set[int]) -> list[int]:
    return sorted(c for c in {*canon, *survivors} if canon.get(c, c) in survivors)


def _rows(conn: sqlite3.Connection, sql: str, ids: Sequence[int]) -> list[sqlite3.Row]:
    out = []
    for i in range(0, len(ids), _CHUNK):
        part = ids[i:i + _CHUNK]
        out += conn.execute(sql.format(marks=_marks(len(part))), part).fetchall()
    return out


def _recent_misses(conn: sqlite3.Connection, canon: dict[int, int], today: date, cfg: PlanConfig) -> set[int]:
    """Companies with a `prepare_attempt` that found nobody within `retry_days` days of `today`, the attempt's own
    day counted as day 1 (an attempt on the 3rd blocks the 3rd to the 9th; the 10th tries again)."""
    retry = cfg.get("prepare", "retry_days")
    tz = cfg.get("plan", "timezone")
    out = set()
    for r in conn.execute("SELECT company_id, at FROM event WHERE entity = 'company' AND kind = 'prepare_attempt' "
                          "AND company_id IS NOT NULL AND json_extract(detail, '$.found') = 0"):
        if (today - ny_date(r["at"], tz)).days < retry:
            out.add(canon.get(r["company_id"], r["company_id"]))
    return out


def _domains(conn: sqlite3.Connection, canon: dict[int, int], survivors: set[int]) -> dict[int, str]:
    shared = {r[0] for r in conn.execute("SELECT value FROM shared_key WHERE kind = 'domain'")}
    best: dict[int, sqlite3.Row] = {}
    for r in _rows(conn, "SELECT evidence_id, company_id, value, confidence, locked, recorded_at FROM evidence "
                         "WHERE fact = 'domain' AND confidence <> 'guess' AND company_id IN ({marks})",
                   _family(canon, survivors)):
        if r["value"] in shared:
            continue
        cid = canon.get(r["company_id"], r["company_id"])
        if cid not in best or fact_order_key(r) > fact_order_key(best[cid]):
            best[cid] = r
    return {cid: r["value"] for cid, r in best.items()}


def _posts(conn: sqlite3.Connection, canon: dict[int, int], survivors: set[int], today: date,
           cfg: PlanConfig) -> dict[int, dict]:
    days = cfg.get("email", "hn_post_days")
    tz = cfg.get("plan", "timezone")
    best: dict[int, tuple[date, int, dict]] = {}
    for r in _rows(conn, "SELECT post_id, company_id, url, posted_at, text FROM company_post WHERE asks_email = 1 "
                         "AND posted_at IS NOT NULL AND company_id IN ({marks})", _family(canon, survivors)):
        posted = ny_date(r["posted_at"], tz)
        if not 0 <= (today - posted).days <= days:
            continue
        cid = canon.get(r["company_id"], r["company_id"])
        key = (posted, r["post_id"])
        if cid not in best or key > best[cid][:2]:
            best[cid] = (posted, r["post_id"], {"url": r["url"], "text": (r["text"] or "")[:POST_TEXT_MAX]})
    return {cid: v[2] for cid, v in best.items()}


def _known(conn: sqlite3.Connection, canon: dict[int, int], survivors: set[int]) -> dict[int, tuple[str, ...]]:
    out: dict[int, list[str]] = {}
    for r in _rows(conn, "SELECT company_id, name FROM person WHERE company_id IN ({marks}) ORDER BY person_id",
                   _family(canon, survivors)):
        names = out.setdefault(canon.get(r["company_id"], r["company_id"]), [])
        if r["name"] not in names and len(names) < _KNOWN_MAX:
            names.append(r["name"])
    return {cid: tuple(names) for cid, names in out.items()}


def select_prepare_targets(conn: sqlite3.Connection, canon: dict[int, int], today: date, cfg: PlanConfig, *,
                           count: int) -> list[Target]:
    """Up to `count` targets in ranked order: eligible by the plan's rule, no candidate left to ask, and no attempt
    that found nobody in the last `prepare.retry_days` days. An attempt that found someone never blocks by itself:
    the person it stored makes the company drop out."""
    if count <= 0:
        return []
    ranked = ranked_list(conn, laned_only=True)
    outbound = load_outbound(conn, canon, tz=cfg.get("plan", "timezone"))
    prospects = load_prospects(conn, ranked, canon, today, cfg, outbound)
    missed = _recent_misses(conn, canon, today, cfg)
    chosen = []
    for p in prospects:  # in ranked order
        if p.candidates or p.company_id in missed or not eligibility(p, cfg)[0]:
            continue
        chosen.append(p)
        if len(chosen) >= count:
            break
    if not chosen:
        return []
    ids = {p.company_id for p in chosen}
    stored: dict[int, StoredScore] = {}
    for s in ranked:
        stored.setdefault(canon.get(s.company_id, s.company_id), s)
    postings = load_postings(conn, [stored[c].signals.get("best_posting_id") for c in ids], cfg)
    domains = _domains(conn, canon, ids)
    posts = _posts(conn, canon, ids, today, cfg)
    known = _known(conn, canon, ids)
    out = []
    for p in chosen:
        signals = stored[p.company_id].signals
        posting = postings.get(signals.get("best_posting_id"))
        role = None
        if posting is not None:
            since = posting.posted_on or posting.first_seen_on
            role = {"posting_id": posting.posting_id, "title": scrub_text(posting.title), "url": posting.url,
                    "age_days": max(0, (today - since).days)}
        reasons = tuple(scrub_text(str(r)) for r in (signals.get("reasons") or [])[:3])
        out.append(Target(company_id=p.company_id, name=p.name, domain=domains.get(p.company_id), lane=p.lanes[0],
                          role=role, reasons=reasons, post=posts.get(p.company_id),
                          known_people=known.get(p.company_id, ())))
    return out


def record_attempt(conn: sqlite3.Connection, company_id: int, *, found: int, run_id: int, version: str,
                   at: str | None = None) -> None:
    """The event an attempt leaves: entity `company`, kind `prepare_attempt`, actor `claude`, detail `{found, run_id,
    version}`. One per run and company (a repeat in the same run writes nothing). `at` defaults to now."""
    append_event(conn, entity="company", kind="prepare_attempt", actor="claude", entity_id=company_id,
                 company_id=company_id, detail={"found": int(found), "run_id": run_id, "version": version}, at=at,
                 idempotency_key=f"prepare-attempt:{run_id}:{company_id}")
