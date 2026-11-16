"""Who the first-email drafter writes to tonight (#298), decided in code, never by the model.

Who: a person on the email channel with an address and no first email yet (no touch, or a touch whose first email
never went out and has no seq 0 message in any state, so a draft, an approved one, a sent one or one he skipped all
count as had). Their company, after merges, is not closed, removed, held (someone there replied) or on the no-go list,
and the person is not on the no-go list and never replied on any channel. Held and replied are the sender's own stops
(`due_followups` reads them the same way): a draft there could not go out, and it would sit in the Outbox counting
toward the target.

How many: the Outbox is topped up to `outbox_target` first emails waiting (drafted plus approved, at any company),
at most `max_per_run`, and `--limit` lowers that. At most `per_company` at one company a run.

Order: companies take turns (one person from each, then a second from each), the companies with the fewest first
emails sent going first, then the fewest waiting, so the next night starts with companies that got nobody. Inside a
company, the #9 reach out order by size, as `pick_people` reads it (`role_of`, `ORDER`, `band_of`, `company_sizes`).
A person whose title `pick_people` would never pick (an intern, sales, an executive at a company over 200) goes last
and is never dropped: they were loaded on purpose.

`read_pool` reads the store (only); `order_in_company`, `queue` and `how_many` are pure.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from outreach.drafts.followups import held_survivors, no_go_sets, replied_people
from outreach.mcp.tools.people import said_no_ids
from outreach.mcp.tools.picking import ORDER, band_of, role_of
from outreach.mcp.tools.reads import SENT_STATES, Family
from outreach.pipeline.sizes import company_sizes

__all__ = ["Company", "Person", "Pool", "WAITING_STATES", "how_many", "order_in_company", "queue", "read_pool"]

WAITING_STATES = ("drafted", "approved")


@dataclass(frozen=True)
class Person:
    person_id: int
    name: str
    title: str
    company_id: int  # the surviving company
    company: str


@dataclass(frozen=True)
class Company:
    company_id: int
    name: str
    band: str      # under_50, 50_to_200, over_200 or unknown
    sent: int      # first emails that went out (or are going) to anyone there
    waiting: int   # first emails drafted or approved there


@dataclass(frozen=True)
class Pool:
    people: list[Person]           # everyone who may get a first email, by person id
    companies: dict[int, Company]  # their surviving companies
    waiting: int                   # first emails waiting in the Outbox, every company


def how_many(*, outbox_target: int, waiting: int, max_per_run: int, limit: int | None) -> int:
    """People to try this run: what tops the Outbox up to the target, at most max_per_run, at most limit."""
    cap = min(max_per_run, limit) if limit else max_per_run
    return max(0, min(outbox_target - waiting, cap))


def order_in_company(people: Sequence[Person], band: str) -> list[Person]:
    """Everyone at one company in #9's reach out order for its size: the size's turns taken in a round (the best of
    the first turn, then of the second, ...), so five are never five recruiters. Inside a group a better title goes
    first (a university or technical recruiter before another), then the lower person id. Anyone `role_of` would not
    pick, or an executive at a company over 200, follows everyone else, by person id."""
    turns = ORDER[band]
    in_turns = {group for turn in turns for group in turn}
    groups: dict[str, list[tuple[int, Person]]] = {}
    last: list[Person] = []
    for p in sorted(people, key=lambda p: p.person_id):
        role = role_of(p.title or "")
        if isinstance(role, str) or role.group not in in_turns or (role.executive and band == "over_200"):
            last.append(p)
            continue
        groups.setdefault(role.group, []).append((role.sub, p))
    for members in groups.values():
        members.sort(key=lambda m: (m[0], m[1].person_id))
    out: list[Person] = []
    while any(groups.values()):
        for turn in turns:
            options = [(groups[g][0][0], turn.index(g), groups[g][0][1].person_id, g) for g in turn if groups.get(g)]
            if options:
                out.append(groups[min(options)[3]].pop(0)[1])
    return out + last


def queue(people: Sequence[Person], companies: dict[int, Company], *, per_company: int) -> list[Person]:
    """The night's order: companies in turn (fewest sent, then fewest waiting, then name), each giving its next person
    in size order, for at most `per_company` rounds."""
    by: dict[int, list[Person]] = {}
    for p in people:
        by.setdefault(p.company_id, []).append(p)
    order = sorted(by, key=lambda cid: (companies[cid].sent, companies[cid].waiting, companies[cid].name.lower(), cid))
    lines = {cid: order_in_company(by[cid], companies[cid].band)[:per_company] for cid in order}
    return [lines[cid][n] for n in range(per_company) for cid in order if n < len(lines[cid])]


def read_pool(conn: sqlite3.Connection) -> Pool:
    """Everyone who may get a first email tonight, their companies and the Outbox's waiting count. Reads only."""
    family = Family(conn)
    companies = family.companies
    held = held_survivors(companies)
    no_go_people, no_go_companies = no_go_sets(conn, companies)
    replied = replied_people(conn)
    names = {r["company_id"]: r["name"] for r in conn.execute("SELECT company_id, name FROM company")}
    rows = conn.execute(
        "SELECT p.person_id, p.name, p.title, p.company_id FROM person p "
        "WHERE p.channel = 'email' AND p.email IS NOT NULL AND trim(p.email) <> '' "
        "AND NOT EXISTS (SELECT 1 FROM touch t WHERE t.person_id = p.person_id AND (t.first_sent_at IS NOT NULL "
        "OR EXISTS (SELECT 1 FROM message m WHERE m.touch_id = t.touch_id AND m.seq = 0))) "
        "ORDER BY p.person_id").fetchall()
    said_no = said_no_ids(conn, [r["person_id"] for r in rows])
    people = []
    for r in rows:
        survivor = family.survivor(r["company_id"])
        if (companies[survivor]["status"] in ("closed", "removed") or survivor in no_go_companies
                or survivor in held or r["person_id"] in no_go_people or r["person_id"] in replied
                or r["person_id"] in said_no):
            continue
        people.append(Person(person_id=r["person_id"], name=r["name"], title=r["title"] or "", company_id=survivor,
                             company=names[survivor]))
    sent: dict[int, int] = {}
    waiting: dict[int, int] = {}
    for r in conn.execute("SELECT p.company_id, m.state, COUNT(*) AS n FROM message m "
                          "JOIN touch t ON t.touch_id = m.touch_id JOIN person p ON p.person_id = t.person_id "
                          "WHERE m.seq = 0 GROUP BY p.company_id, m.state"):
        survivor = family.survivor(r["company_id"])
        if r["state"] in SENT_STATES:
            sent[survivor] = sent.get(survivor, 0) + r["n"]
        elif r["state"] in WAITING_STATES:
            waiting[survivor] = waiting.get(survivor, 0) + r["n"]
    survivors = sorted({p.company_id for p in people})
    sizes = company_sizes(conn, survivors)
    pool_companies = {cid: Company(company_id=cid, name=names[cid], band=band_of(*sizes[cid]),
                                   sent=sent.get(cid, 0), waiting=waiting.get(cid, 0)) for cid in survivors}
    return Pool(people=people, companies=pool_companies, waiting=sum(waiting.values()))
