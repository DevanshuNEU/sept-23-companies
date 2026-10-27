"""Selecting the day's rows (#15, Task 8). Pure: no store, no clock, no network.

`choose_rows` takes the prospects (companies the plan may reach, each with its contact state and candidates), the day's
capacities and quotas, and returns the rows of the plan. A company is used once. Order of work: carried rows first,
then an email pass, then a LinkedIn pass that fills the role share (80%) and the reason share (20%) and hands unused
slots on (role leftovers, reason leftovers, then the `any_us_role` fill pool). A company the plan cannot name a person
for still gets a find-someone row: nothing is hidden. The output is in display order: rows with a person by score,
then find-someone rows by score.

With `plan.propose_people = false` (shipped: company first, #9 2026-10-26) the only option is the find-someone row:
every row is a company row on LinkedIn, no person and no email, and a carried person row comes back as its company.

Top picks (the owner's spec, 2026-10-26; `outreach.plan.fresh` has the rules): before anything else, up to
`queue.fresh_slots` rows go to companies within the top `queue.fresh_pool_top_n` of the ranking with a fresh early
career opening that clears the visa gate, newest opening first, then by score; they come first in the display order. A company he said Not now to is held back for
`queue.pass_cooldown_days`, and one he applied to is held back, each until an opening of that kind was first seen
after it (then it is fresh again). `bench` is the next best companies not on the day's list.
"""
from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from outreach.plan.config import PlanConfig
from outreach.plan.contact import ContactState
from outreach.plan.fresh import Opening, visa_clear
from outreach.plan.people import Candidate, pick_channel, rank_people

__all__ = ["CarriedRow", "Prospect", "Caps", "Chosen", "BenchPick", "group_of", "qualifying", "held_back", "in_fresh_pool",
           "fresh_pick", "fresh_any", "fresh_lane", "eligibility", "options", "choose_rows", "bench"]

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
    # Top picks: the company's fresh openings (`fresh.load_openings`), the visa facts, his newest Not now inside the
    # cooldown and how many days ago it was, and his newest application day.
    openings: tuple[Opening, ...] = ()
    everify: str | None = None
    company_sponsorship: str | None = None
    passed_at: datetime | None = None
    passed_days: int | None = None
    applied_on: date | None = None


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
    opening: Opening | None = None  # set on a fresh lane row: the opening the row names


@dataclass(frozen=True)
class BenchPick:
    prospect: Prospect
    opening: Opening | None  # the fresh opening it is flagged for, if any
    early_fit: bool  # that opening is an early career fit (the fresh lane's kind)


def group_of(lanes: Sequence[str], cfg: PlanConfig) -> str | None:
    """The pool of the company's strongest lane (`lanes[0]`): `role`, `reason` or `fill`; None with no lane."""
    if not lanes:
        return None
    for group in ("role", "reason", "fill"):
        if lanes[0] in cfg.get("lanes", group):
            return group
    return None


def qualifying(p: Prospect) -> list[Opening]:
    """The company's fresh openings that clear the visa gate, in `load_openings` order (early career fits first)."""
    return [o for o in p.openings if visa_clear(o.stance, p.everify, p.company_sponsorship)]


def _holds(p: Prospect, cfg: PlanConfig) -> list:
    """The tests an opening must pass to bring back a company that is held back: first seen after his Not now (while
    it is inside the cooldown), and first seen on a day after his application."""
    tests = []
    if p.passed_at is not None and p.passed_days is not None and p.passed_days < cfg.get("queue", "pass_cooldown_days"):
        tests.append(lambda o: o.first_seen_at > p.passed_at)
    if p.applied_on is not None:
        tests.append(lambda o: o.first_seen_on > p.applied_on)
    return tests


def in_fresh_pool(p: Prospect, cfg: PlanConfig) -> bool:
    """The quality floor: only a company within the top `queue.fresh_pool_top_n` of the stored ranking may take the
    fresh lane, so a new opening at a low scored company never pushes out a strong one. Below it the company still
    competes by rank like any other."""
    return p.position <= cfg.get("queue", "fresh_pool_top_n")


def fresh_pick(p: Prospect, cfg: PlanConfig) -> Opening | None:
    """The opening the fresh lane would name: the first early career fit that clears the gate and, when the company
    is held back, came after what holds it. None when there is none, or below the quality floor (`in_fresh_pool`)."""
    if not in_fresh_pool(p, cfg):
        return None
    tests = _holds(p, cfg)
    return next((o for o in qualifying(p) if o.early_fit and all(t(o) for t in tests)), None)


def fresh_any(p: Prospect, cfg: PlanConfig) -> Opening | None:
    """As `fresh_pick` for any opening at his level (the bench flags these as new too), with the same floor."""
    if not in_fresh_pool(p, cfg):
        return None
    tests = _holds(p, cfg)
    return next((o for o in qualifying(p) if all(t(o) for t in tests)), None)


def held_back(p: Prospect, cfg: PlanConfig) -> str | None:
    """`passed` or `applied` when the company stays out of the picks (no early career opening came after it), else
    None. Neither is a no-go: the company stays everywhere else and comes back on its own."""
    if not _holds(p, cfg) or fresh_pick(p, cfg) is not None:
        return None
    if p.passed_days is not None and p.passed_days < cfg.get("queue", "pass_cooldown_days"):
        return "passed"
    return "applied"


def eligibility(p: Prospect, cfg: PlanConfig) -> tuple[bool, str]:
    """Whether the company may get a row today, and why not. `free` is eligible; `stale` only while the company is
    within the top `rotate_top_n` of the laned list and fewer than `max_people_per_company` people were contacted.
    A company held back (`held_back`: his Not now, or his application) is not."""
    if group_of(p.lanes, cfg) is None:
        return False, "no_lane"
    held = held_back(p, cfg)
    if held is not None:
        return False, held
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
    carried person who is still a candidate goes to the very front. The find-someone option is always last, and with
    `plan.propose_people` false it is the only one."""
    if not cfg.get("plan", "propose_people"):
        return [FIND_SOMEONE]
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
    dropped: set[int] = set()  # carried rows that can never be placed: the company competes as a new row

    def take(p: Prospect, opt: tuple[str, int | None, str], opening: Opening | None = None) -> None:
        nonlocal linkedin, email
        if opt[0] == "email":
            email -= 1
        else:
            linkedin -= 1
        used.add(p.company_id)
        status = p.contact.status
        lane = opening.lane if opening is not None else p.lanes[0]
        rows.append(Chosen(
            company_id=p.company_id, channel=opt[0], person_id=opt[1], group=group_of((lane,), cfg),
            list_group=lane, channel_reason=opt[2],
            carried_from=p.carried.carried_from if p.carried and p.company_id not in dropped else None,
            first_proposed=p.carried.first_proposed if p.carried and p.company_id not in dropped else None,
            rotation_from=p.contact.last_person_id if status == "stale" else None, opening=opening))

    # 0. The fresh lane: early career openings that clear the visa gate, newest first, then by score. A LinkedIn
    # row each, counted in the role share.
    fresh_n = min(max(0, cfg.get("queue", "fresh_slots")), linkedin)
    for p, opening in fresh_lane(eligible, cfg):
        if fresh_n <= 0 or linkedin <= 0:
            break
        opt = _first_with_room([o for o in options(p, cfg) if o[0] != "email"], linkedin, 0)
        if opt is None:
            continue
        take(p, opt, opening)
        fresh_n -= 1
        quota["role"] = max(0, quota["role"] - 1)

    # 1. Carried rows, oldest first. They count against their pool's quota (a LinkedIn row only).
    carried = sorted((p for p in eligible if p.carried),
                     key=lambda p: (p.carried.first_proposed, -p.score, p.company_id))
    for p in carried:
        if p.company_id in used:
            continue
        opts = options(p, cfg)
        # With people off, a carried person row is carried as its company: the else branch below.
        person = p.carried.person_id if cfg.get("plan", "propose_people") else None
        if person is not None and any(c.person_id == person for c in p.candidates):
            # Their row, or none today: never another person in their place. Without room today, the company waits and
            # the row stays carried (the next build reads it again from the plan that proposed it). With no option
            # at all (their email row was dropped: `email.people_per_company` is reached), the carried row can never
            # be placed: it is dropped, quietly, and the company competes in the passes below as a new row.
            mine = next((o for o in opts if o[1] == person), None)
            if mine is None:
                dropped.add(p.company_id)
                continue
            if (email if mine[0] == "email" else linkedin) <= 0:
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

    rows.sort(key=lambda r: (r.opening is None, -r.opening.opened_at.timestamp() if r.opening else 0.0,
                             r.person_id is None, -by_id[r.company_id].score, r.company_id))
    return rows


def fresh_lane(eligible: Sequence[Prospect], cfg: PlanConfig) -> list[tuple[Prospect, Opening]]:
    """The eligible companies with a fresh lane opening (`fresh_pick`), newest opening first, then by score."""
    found = [(p, o) for p in eligible for o in (fresh_pick(p, cfg),) if o is not None]
    return sorted(found, key=lambda x: (-x[1].opened_at.timestamp(), -x[0].score, x[0].company_id))


def bench_pick_of(p: Prospect, cfg: PlanConfig) -> BenchPick:
    """The company as a bench or browse row: its fresh early career fit, else any fresh opening at his level, else
    none. It says nothing about whether the company may be picked (`eligibility` does)."""
    fit = fresh_pick(p, cfg)
    return BenchPick(p, fit or fresh_any(p, cfg), fit is not None)


def bench(prospects: Sequence[Prospect], taken: Collection[int], cfg: PlanConfig,
          n: int | None = None) -> list[BenchPick]:
    """The next best companies not in `taken` (every company on the day's list, in any state): eligible ones only
    (not held back, not in flight), fresh early career fits first, then other fresh openings at his level, each newest
    first, then the rest by score. Up to `n` (default `queue.bench_size`)."""
    n = cfg.get("queue", "bench_size") if n is None else n
    picks = [bench_pick_of(p, cfg) for p in prospects if p.company_id not in taken and eligibility(p, cfg)[0]]
    picks.sort(key=lambda b: (0 if b.early_fit else 1 if b.opening else 2,
                              -b.opening.opened_at.timestamp() if b.opening else 0.0, -b.prospect.score,
                              b.prospect.company_id))
    return picks[:max(0, n)]
