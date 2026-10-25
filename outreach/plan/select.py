"""Selecting the day's rows (#15, Task 8). Pure: no store, no clock, no network.

`choose_rows` takes the prospects (companies the plan may reach, each with its contact state and candidates), the day's
capacities and quotas, and returns the rows of the plan. A company is used once. Order of work: carried rows first,
then an email pass, then a LinkedIn pass that fills the role share (80%) and the reason share (20%) and hands unused
slots on (role leftovers, reason leftovers, then the `any_us_role` fill pool). A company the plan cannot name a person
for still gets a find-someone row: nothing is hidden. The output is in display order: rows with a person by score,
then find-someone rows by score.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from outreach.plan.config import PlanConfig
from outreach.plan.contact import ContactState
from outreach.plan.people import Candidate, pick_channel, rank_people

__all__ = ["CarriedRow", "Prospect", "Caps", "Chosen", "group_of", "eligibility", "options", "choose_rows"]

FIND_SOMEONE = ("linkedin_request", None, "find_someone")
_HN = "hn_post_asks_email"


@dataclass(frozen=True)
class CarriedRow:
    company_id: int
    person_id: int | None
    carried_from: date
    first_proposed: date


@dataclass(frozen=True)
class Prospect:
    company_id: int
    name: str
    score: float
    position: int  # 1-based in the laned ranked list
    lanes: tuple[str, ...]
    contact: ContactState
    candidates: tuple[Candidate, ...]  # people not yet contacted, no active no_go
    small: bool
    carried: CarriedRow | None


@dataclass(frozen=True)
class Caps:
    linkedin: int
    email: int
    role: int
    reason: int


@dataclass(frozen=True)
class Chosen:
    company_id: int
    channel: str
    person_id: int | None
    group: Literal["role", "reason", "fill"]
    list_group: str
    channel_reason: str
    carried_from: date | None
    first_proposed: date | None
    rotation_from: int | None


def group_of(lanes: Sequence[str], cfg: PlanConfig) -> str | None:
    """The pool of the company's strongest lane (`lanes[0]`): `role`, `reason` or `fill`; None with no lane."""
    if not lanes:
        return None
    for group in ("role", "reason", "fill"):
        if lanes[0] in cfg.get("lanes", group):
            return group
    return None


def eligibility(p: Prospect, cfg: PlanConfig) -> tuple[bool, str]:
    """Whether the company may get a row today, and why not. `free` is eligible; `stale` only while the company is
    within the top `rotate_top_n` of the laned list and fewer than `max_people_per_company` people were contacted."""
    if group_of(p.lanes, cfg) is None:
        return False, "no_lane"
    status = p.contact.status
    if status == "free":
        return True, "free"
    if status in ("waiting", "engaged"):
        return False, status
    if p.position > cfg.get("linkedin", "rotate_top_n"):
        return False, "stale_below_rotation_cutoff"
    if p.contact.people_contacted >= cfg.get("linkedin", "max_people_per_company"):
        return False, "max_people_contacted"
    return True, "stale"


def options(p: Prospect, cfg: PlanConfig) -> list[tuple[str, int | None, str]]:
    """`(channel, person_id, channel_reason)` in preference order. The contact of an HN post that asks for email comes
    first (an explicit invitation outranks the order), then each candidate in `rank_people` order with the channel
    `pick_channel` gives; an email option is dropped once `email.people_per_company` people were emailed there. A
    carried person who is still a candidate goes to the very front. The find-someone option is always last."""
    cands = rank_people(p.candidates)
    email_ok = p.contact.email_people < cfg.get("email", "people_per_company")
    picked = []
    for c in cands:
        channel, reason = pick_channel(c, [s for s in cands if s is not c], cfg)
        picked.append((c.person_id, channel, reason))
    picked.sort(key=lambda x: x[2] != _HN)  # stable: the HN contacts first, the rest keep their order
    if p.carried is not None and p.carried.person_id is not None:
        picked.sort(key=lambda x: x[0] != p.carried.person_id)
    out: list[tuple[str, int | None, str]] = [(channel, pid, reason) for pid, channel, reason in picked
                                              if channel != "email" or email_ok]
    out.append(FIND_SOMEONE)
    return out


def _first_with_room(opts: Sequence[tuple[str, int | None, str]], linkedin: int, email: int):
    for opt in opts:
        if (email if opt[0] == "email" else linkedin) > 0:
            return opt
    return None


def choose_rows(prospects: Sequence[Prospect], caps: Caps, cfg: PlanConfig) -> list[Chosen]:
    """The rows of the day, in display order. Never more LinkedIn rows than `caps.linkedin`, never more email rows
    than `caps.email`, no company twice."""
    linkedin, email = max(0, caps.linkedin), max(0, caps.email)
    quota = {"role": max(0, caps.role), "reason": max(0, caps.reason)}
    if linkedin == 0 and email == 0:
        return []
    eligible = [p for p in prospects if eligibility(p, cfg)[0]]
    by_score = sorted(eligible, key=lambda p: (-p.score, p.company_id))
    by_id = {p.company_id: p for p in prospects}
    used: set[int] = set()
    rows: list[Chosen] = []

    def take(p: Prospect, opt: tuple[str, int | None, str]) -> None:
        nonlocal linkedin, email
        if opt[0] == "email":
            email -= 1
        else:
            linkedin -= 1
        used.add(p.company_id)
        status = p.contact.status
        rows.append(Chosen(
            company_id=p.company_id, channel=opt[0], person_id=opt[1], group=group_of(p.lanes, cfg),
            list_group=p.lanes[0], channel_reason=opt[2],
            carried_from=p.carried.carried_from if p.carried else None,
            first_proposed=p.carried.first_proposed if p.carried else None,
            rotation_from=p.contact.last_person_id if status == "stale" else None))

    # 1. Carried rows, oldest first. They count against their pool's quota (a LinkedIn row only).
    carried = sorted((p for p in eligible if p.carried),
                     key=lambda p: (p.carried.first_proposed, -p.score, p.company_id))
    for p in carried:
        if p.company_id in used:
            continue
        opts = options(p, cfg)
        person = p.carried.person_id
        if person is not None and any(c.person_id == person for c in p.candidates):
            # Their row, or none today: never another person in their place. Without room, the company waits and the
            # row stays carried (the next build reads it again from the plan that proposed it).
            mine = next((o for o in opts if o[1] == person), None)
            if mine is None or (email if mine[0] == "email" else linkedin) <= 0:
                used.add(p.company_id)
                continue
            opt = mine
        else:
            opt = _first_with_room(opts, linkedin, email)
            if opt is None:
                continue
        take(p, opt)
        group = group_of(p.lanes, cfg)
        if opt[0] != "email" and group in quota:
            quota[group] = max(0, quota[group] - 1)

    # 2. Email: by score, the companies whose first option with room is email.
    for p in by_score:
        if email <= 0:
            break
        if p.company_id in used:
            continue
        opt = _first_with_room(options(p, cfg), linkedin, email)
        if opt is not None and opt[0] == "email":
            take(p, opt)

    # 3. LinkedIn: the role and reason shares from their pools, then leftovers, then the fill pool.
    pools: dict[str, list[tuple[Prospect, tuple[str, int | None, str]]]] = {"role": [], "reason": [], "fill": []}
    for p in by_score:
        if p.company_id in used:
            continue
        opt = _first_with_room(options(p, cfg), 1, email)
        if opt is not None and opt[0] != "email":
            pools[group_of(p.lanes, cfg)].append((p, opt))

    def fill(pool: list, n: int) -> None:
        while pool and n > 0 and linkedin > 0:
            p, opt = pool.pop(0)
            take(p, opt)
            n -= 1

    fill(pools["role"], quota["role"])
    fill(pools["reason"], quota["reason"])
    for pool in (pools["role"], pools["reason"], pools["fill"]):
        fill(pool, linkedin)

    rows.sort(key=lambda r: (r.person_id is None, -by_id[r.company_id].score, r.company_id))
    return rows
