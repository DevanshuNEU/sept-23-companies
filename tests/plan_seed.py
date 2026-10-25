"""Seeders for the daily people plan tests (#15). Every plan task adds to this file.

All of them write through the real tables and the real writers (`log_touch`, `record_outcome`), so a test states only
what it is about.
"""
from __future__ import annotations

import itertools
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from outreach.drafts.checks import load_banned
from outreach.plan.config import default_plan_config
from outreach.plan.drafts import save_linkedin_draft
from outreach.plan.notecheck import Fact, load_unconfirmed
from outreach.store.timefmt import utcnow
from outreach.store.writes import log_touch, record_outcome

__all__ = ["TODAY", "seed_scored_company", "seed_person", "seed_source", "seed_outbound", "seed_log_only",
           "seed_send_day",
           "seed_email_touch", "seed_message", "seed_daily_entry", "PlanSituation", "seed_plan_situation"]

TODAY = date(2026, 11, 10)
_counter = itertools.count(1)


def _at(day: date, hour: int = 15) -> str:
    """A UTC timestamp that falls on `day` in New York all year (15:00Z is 10:00 or 11:00 there)."""
    return f"{day.isoformat()}T{hour:02d}:00:00Z"


def seed_scored_company(conn: sqlite3.Connection, name: str, score: float, lanes, *, status: str = "candidate",
                        with_posting: bool = True, size_high: int | None = None, version: str = "rk-test") -> int:
    now = utcnow()
    close_reason = "other" if status == "closed" else None
    remove_reason = "seeded as removed" if status == "removed" else None
    company_id = conn.execute(
        "INSERT INTO company(name, status, close_reason, remove_reason, status_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)", (name, status, close_reason, remove_reason, now, now)).lastrowid
    posting_id = None
    if with_posting:
        n = next(_counter)
        posting_id = conn.execute(
            "INSERT INTO posting(company_id, source_key, title, location, country, url, posted_on, first_seen_at, "
            "last_seen_at) VALUES (?, ?, 'Software Engineer', 'Remote', 'US', ?, ?, ?, ?)",
            (company_id, f"url:plan-seed-{n}", f"https://jobs.example.com/{n}", TODAY.isoformat(), now, now)
        ).lastrowid
    if size_high is not None:
        conn.execute(
            "INSERT INTO evidence(company_id, fact, value, num_low, num_high, confidence, source_kind, observed_on, "
            "recorded_at) VALUES (?, 'size', ?, 1, ?, 'confirmed', 'website', ?, ?)",
            (company_id, f"1-{size_high}", size_high, TODAY.isoformat(), now))
    signals = {"values": {}, "reasons": ["Open new grad role: Software Engineer, posted 3 days ago"], "hide": None,
               "flags": [], "best_posting_id": posting_id, "ignored_postings": 0}
    conn.execute(
        "INSERT INTO company_score(company_id, ranker_version, score, signals, lanes, scored_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (company_id, version, score, json.dumps(signals, sort_keys=True), json.dumps(list(lanes)), now))
    return company_id


def seed_person(conn: sqlite3.Connection, company_id: int, name: str, role_kind: str = "engineer", *,
                email: str | None = None, linkedin_url: str | None = None, sources=(),
                added_by: str = "claude") -> int:
    """A person at the company. `sources` are `(kind, url, quote)` tuples observed today; an `email` source gets the
    basis `published`. The channel is a hint for the CHECK only: email with an address, else LinkedIn."""
    now = utcnow()
    channel = "email" if email else "linkedin"
    person_id = conn.execute(
        "INSERT INTO person(company_id, name, role_kind, email, linkedin_url, channel, linkedin_reason, added_by, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (company_id, name, role_kind, email, linkedin_url, channel, None if email else "no_email", added_by, now, now)
    ).lastrowid
    for kind, url, quote in sources:
        conn.execute(
            "INSERT INTO person_source(person_id, kind, url, quote, email_basis, observed_on, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (person_id, kind, url, quote, "published" if kind == "email" else None, TODAY.isoformat(), now))
    return person_id


def seed_source(conn: sqlite3.Connection, person_id: int, kind: str, url: str, *, observed_on: date | None = TODAY,
                quote: str = "", email_basis: str | None = None, label: str = "") -> int:
    """One `person_source` row with a chosen observation date (an `email` source needs a basis)."""
    return conn.execute(
        "INSERT INTO person_source(person_id, kind, url, label, quote, email_basis, observed_on, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (person_id, kind, url, label, quote, email_basis, observed_on.isoformat() if observed_on else None,
         utcnow())).lastrowid


def seed_outbound(conn: sqlite3.Connection, person_id: int, company_id: int, channel: str, sent_on: date, *,
                  accepted_on: date | None = None, replied_on: date | None = None, lane: str | None = None,
                  context_source: str = "plan", key: str | None = None) -> int:
    """An `outreach_touch` through `log_touch` and `record_outcome`; the context carries `source` and `lane`."""
    context = {"source": context_source, "lane": lane, "roleKind": None, "roleAgeDays": None,
               "rankerVersion": None, "signals": {}, "roleNamed": False}
    touch_id = log_touch(conn, person_id=person_id, company_id=company_id, channel=channel,
                         sent_at=_at(sent_on), context=context,
                         idempotency_key=key or f"plan-seed-{next(_counter)}")
    revision = 1
    if accepted_on is not None:
        revision = record_outcome(conn, touch_id, kind="accepted", at=_at(accepted_on, 16), revision=revision)
    if replied_on is not None:
        record_outcome(conn, touch_id, kind="replied", at=_at(replied_on, 17), revision=revision)
    return touch_id


def seed_log_only(conn: sqlite3.Connection, person_id: int, kind: str, on: date, *,
                  undoes: int | None = None) -> int:
    """A `linkedin_log` row with no touch behind it (imported history). `undoes` is for `kind='undo'`."""
    at = _at(on)
    return conn.execute(
        "INSERT INTO linkedin_log(person_id, kind, undoes, at, recorded_at) VALUES (?, ?, ?, ?, ?)",
        (person_id, kind, undoes, at, at)).lastrowid


def seed_send_day(conn: sqlite3.Connection, day: date, limit: int, outcome: str | None = None) -> None:
    conn.execute("INSERT INTO send_day(day, send_limit, outcome, created_at) VALUES (?, ?, ?, ?)",
                 (day.isoformat(), limit, outcome, utcnow()))


def seed_email_touch(conn: sqlite3.Connection, person_id: int, first_sent_on: date | None, *, state: str = "open",
                     company_id: int | None = None, outreach: bool = False) -> int:
    """A legacy email `touch` for the person, its first email sent on `first_sent_on` (None: not sent yet). With
    `outreach` and a `company_id` it also gets the `outreach_touch` the sender writes at the first send."""
    now = utcnow()
    first = _at(first_sent_on) if first_sent_on else None
    later = _at(first_sent_on, 17) if first_sent_on else now
    touch_id = conn.execute(
        "INSERT INTO touch(person_id, origin, state, first_sent_at, replied_at, reply_kind, closed_at, close_reason, "
        "created_at, updated_at) VALUES (?, 'import', ?, ?, ?, ?, ?, ?, ?, ?)",
        (person_id, state, first, later if state == "replied" else None, "interested" if state == "replied" else None,
         later if state == "closed" else None, "no_reply" if state == "closed" else None, now, now)).lastrowid
    if outreach:
        context = {"source": "own", "lane": None, "roleKind": None, "roleAgeDays": None, "rankerVersion": None,
                   "signals": {}, "roleNamed": False}
        log_touch(conn, person_id=person_id, company_id=company_id, channel="email", sent_at=first, context=context,
                  email_touch_id=touch_id, idempotency_key=f"plan-seed-email-{next(_counter)}")
    return touch_id


def seed_message(conn: sqlite3.Connection, touch_id: int, seq: int, state: str, *, sent_on: date | None = None,
                 due_on: date | None = None) -> int:
    """A message in `state` (planned, drafted, approved, sending, send_unknown, failed, skipped or sent). The store
    makes a message born planned, drafted or sent and moves it by UPDATE, so this does the same."""
    now = utcnow()
    n = next(_counter)
    born = "sent" if state == "sent" else ("planned" if state == "planned" else "drafted")
    if seq >= 1 and due_on is None:
        due_on = date(2026, 11, 12)
    full = born != "planned"
    message_id = conn.execute(
        "INSERT INTO message(touch_id, seq, state, subject, body, due_on, sent_at, rfc_message_id, created_at, "
        "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (touch_id, seq, born, "s" if full else "", "b" if full else "", due_on.isoformat() if due_on else None,
         _at(sent_on) if state == "sent" else None, f"<plan-seed-{n}@x>" if state == "sent" else None, now,
         now)).lastrowid
    steps = {"approved": ("approved",), "sending": ("approved", "sending"),
             "send_unknown": ("approved", "sending", "send_unknown"),
             "failed": ("approved", "sending", "send_unknown", "failed")}.get(state, ())
    for step in steps:
        held = step in ("sending", "send_unknown")
        conn.execute("UPDATE message SET state = ?, approved_at = ?, pending_rfc_id = ?, claimed_at = ? "
                     "WHERE message_id = ?", (step, now, f"<plan-seed-p{n}@x>" if held else None,
                                              now if held else None, message_id))
    if state == "skipped":
        conn.execute("UPDATE message SET state = 'skipped' WHERE message_id = ?", (message_id,))
    return message_id


def seed_daily_entry(conn: sqlite3.Connection, list_date: date, company_id: int, *, rank: int = 1,
                     person_id: int | None = None, channel: str | None = None, state: str = "proposed",
                     list_group: str = "level_role", why: dict | None = None, capacity: int = 7) -> None:
    """A `daily_list` for the date (made once) and one entry on it. A person row needs a channel; a decided state
    (anything but proposed) gets a `decided_at`."""
    now = utcnow()
    conn.execute("INSERT INTO daily_list(list_date, built_at, capacity) VALUES (?, ?, ?) "
                 "ON CONFLICT(list_date) DO NOTHING", (list_date.isoformat(), now, capacity))
    if person_id is not None and channel is None:
        channel = "linkedin_request"
    conn.execute(
        "INSERT INTO daily_list_entry(list_date, company_id, list_group, rank, state, why, proposed_at, decided_at, "
        "person_id, channel) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (list_date.isoformat(), company_id, list_group, rank, state, json.dumps(why or {}), now,
         None if state == "proposed" else now, person_id, channel))


# The plan flow stage's seed (Task 15)

_FIX = Path(__file__).resolve().parent / "fixtures" / "plan"
_NOTE_REASON = "I read your post on the queue rewrite"
_NOTE_BODY = (f"Hi Dana, {_NOTE_REASON} and built a similar job runner. "
              "I would like to hear how your platform team works.")


@dataclass
class PlanSituation:
    """What `seed_plan_situation` made, by role in the story. Company ids are the survivors of the ranked list.
    `people` is every person the seeder made: an invariant counts only these."""
    team: int  # rank 1: a team engineer with a draft
    waiting: int  # rank 2: a request 5 days old, sent to a person at a merged-away duplicate
    rotating: int  # rank 3: a request 20 days old and a second candidate
    accepted: int  # rank 4: accepted 9 days ago
    engaged: int  # rank 5: someone replied
    founder: int  # rank 6: a founder with a published address
    nobody: int  # rank 7: no people at all
    recruiter_only: tuple[int, ...]  # the 8th and later, every other one
    no_people: tuple[int, ...]  # the 8th and later, the rest
    team_person: int
    team_draft: int
    waiting_duplicate: int  # the merged-away company the waiting request went to (0 when there is none)
    waiting_first: int  # the person the 5 day old request went to
    waiting_second: int  # the candidate the waiting company still has
    rotating_first: int
    rotating_second: int
    accepted_person: int
    engaged_person: int
    founder_person: int
    people: set[int] = field(default_factory=set)

    @property
    def blocked(self) -> tuple[int, ...]:
        """The companies in play: none of them may get a row today."""
        return self.waiting, self.accepted, self.engaged


def _host(company_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", company_name.lower()) + ".example"


def _team_source(company_name: str) -> tuple[str, str, str]:
    return ("team", f"https://{_host(company_name)}/team", f"an engineer on the {company_name} team")


def seed_plan_situation(conn: sqlite3.Connection, ranked, *, today: date = TODAY) -> PlanSituation:
    """People and touches relative to the ranked order of whatever the earlier stages produced (`ranked` is
    `ranked_list(conn, laned_only=True)`, at least 7 long): rank 1 has a team engineer with a draft; rank 2 has a
    request 5 days old (sent to a person at its merged-away duplicate when it has one) and another candidate;
    rank 3 a request 20 days old and a second candidate; rank 4 a request accepted 9 days ago; rank 5 a reply;
    rank 6 a founder with a published address; rank 7 nobody; the 8th and later alternate recruiter only and nobody.
    Everything goes through the real writers; the draft is Claude's, with its reason line still proposed."""
    if len(ranked) < 8:
        raise ValueError(f"the situation needs at least 8 ranked companies, got {len(ranked)}")
    ids = [s.company_id for s in ranked]
    name = {r[0]: r[1] for r in conn.execute("SELECT company_id, name FROM company")}
    made: set[int] = set()

    def person(company, who, role="engineer", **kw):
        pid = seed_person(conn, company, who, role, **kw)
        made.add(pid)
        return pid

    def team(company, who):
        return person(company, who, "engineer", sources=[_team_source(name[company])])

    day = timedelta  # day(5): five days
    c1, c2, c3, c4, c5, c6, c7 = ids[:7]

    team_person = team(c1, "Tess Team")
    draft = save_linkedin_draft(
        conn, person_id=team_person, body=_NOTE_BODY, reason_line=_NOTE_REASON, saved_by="claude",
        facts=[Fact("queue rewrite", "Tess's post", f"https://{_host(name[c1])}/blog/queue", True)],
        cfg=default_plan_config(), banned=load_banned(_FIX / "banned_phrases.txt", scopes=("both", "dm"))[0],
        unconfirmed=load_unconfirmed(_FIX / "me.md"))

    duplicate = conn.execute("SELECT company_id FROM company WHERE merged_into = ? ORDER BY company_id",
                             (c2,)).fetchone()
    duplicate_id = duplicate[0] if duplicate else c2
    waiting_first = person(duplicate_id, "Wes Waiting")
    seed_outbound(conn, waiting_first, duplicate_id, "linkedin_request", today - day(5))
    waiting_second = team(c2, "Wren Second")

    rotating_first = person(c3, "Rob Rotated")
    seed_outbound(conn, rotating_first, c3, "linkedin_request", today - day(20))
    rotating_second = team(c3, "Rae Second")

    accepted_person = person(c4, "Ann Accepted")
    seed_outbound(conn, accepted_person, c4, "linkedin_request", today - day(20), accepted_on=today - day(9))

    engaged_person = person(c5, "Evie Engaged")
    seed_outbound(conn, engaged_person, c5, "linkedin_request", today - day(30), accepted_on=today - day(25),
                  replied_on=today - day(20))

    email = f"fay@{_host(name[c6])}"
    founder_person = person(c6, "Fay Founder", "founder", email=email,
                            sources=[("email", f"https://{_host(name[c6])}/contact", f"Write to {email}")])

    recruiter_only, no_people = [], []
    for n, company in enumerate(ids[7:149]):
        if n % 2 == 0:
            person(company, f"Rhea Recruiter {n}", "recruiter")
            recruiter_only.append(company)
        else:
            no_people.append(company)
    return PlanSituation(
        team=c1, waiting=c2, rotating=c3, accepted=c4, engaged=c5, founder=c6, nobody=c7,
        recruiter_only=tuple(recruiter_only), no_people=tuple(no_people), team_person=team_person,
        team_draft=draft.draft_id, waiting_duplicate=duplicate_id if duplicate else 0, waiting_first=waiting_first,
        waiting_second=waiting_second, rotating_first=rotating_first, rotating_second=rotating_second,
        accepted_person=accepted_person, engaged_person=engaged_person, founder_person=founder_person,
        people=made)
