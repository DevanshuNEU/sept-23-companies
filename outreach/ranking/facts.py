"""Company facts for the signals: `RankFacts` and the bulk loader (#6, ruling 2026-10-22).

`load_facts` reads each source table once (never one query per company), groups the rows by the company a merged
duplicate belongs to, and builds one `RankFacts` per listed company. It also grades every signal, once, with the pure
functions of `confidence.py`; a signal copies `facts.grade(name)` into its `Assessment` and never grades by itself.

A stale posting is never in `Roles.visible`, so it gives no lane, best posting, sponsor stance or pay level here.
Unknown stays unknown: a missing size, stage or evidence row is `None`, and its grade is `low`.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date

from outreach.insights.bands import Features, default_bands, features_of
from outreach.ranking.common import age_days, clean_text, fact_order_key, size_order_key
from outreach.ranking.config import RankerConfig
from outreach.ranking.confidence import (Confidence, grade_evidence, grade_no_role, grade_pay, grade_reach,
                                         grade_sponsor, weaker)
from outreach.ranking.pay import Area, lca_pay_level, level_at, matching_areas, pay_level
from outreach.ranking.roles import Best, Roles, company_board_states
from outreach.ranking.types import CompanyFacts

__all__ = ["SHARED_DOMAIN_FLAG", "RankFacts", "recent_fiscal_years", "is_large", "load_facts"]

SHARED_DOMAIN_FLAG = "domain shared with other employers"
LISTED_STATUSES = ("candidate", "backlog", "working")
_FACTS = ("everify", "cap_exempt", "staffing_risk", "stage", "size", "headcount")


@dataclass(frozen=True)
class RankFacts(CompanyFacts):
    """`CompanyFacts` plus what the signals also need. Every added field has a default, so a signal that reads only
    `CompanyFacts` fields works on either."""
    name: str = ""
    best_title: str = ""
    best_level_tier: str | None = None
    best_level_basis: str = ""           # the rules' level basis ("Title says new grad", "Asks 2 years")
    best_sponsorship: str | None = None  # yes, opt_only, unclear or silent; None with no live posting
    best_dated: bool = True              # False when the posting has no posted date: its age is from first seen
    pay_basis: str = ""                  # posted, lca or empty
    pay_note: str = ""
    weak_flags: tuple[str, ...] = ()     # reasons to doubt the company is small
    size_confidence: str | None = None
    work_tier: int | None = None
    work_promoted: bool = False
    reach_via: str = ""                  # person, hn_post or empty
    reach_note: str = ""
    ignored_postings: int = 0            # postings on untrusted boards, doubtful or inactive
    inactive_postings: int = 0           # the part of ignored_postings on inactive boards
    stale_postings: tuple[Best, ...] = ()
    grades: Mapping[str, Confidence] = field(default_factory=dict)
    features: Features = Features()      # the four traits the interview tilt compares (#81)

    def grade(self, name: str) -> Confidence:
        """The stored grade of a signal, else the low default: unknown is never `high`."""
        got = self.grades.get(name)
        return got if got is not None else Confidence("low", f"no data for {name.replace('_', ' ')}")


def recent_fiscal_years(conn: sqlite3.Connection, n: int) -> tuple[int, ...]:
    """The `n` highest distinct fiscal years across all `lca` evidence, newest first. Data lags the calendar, so the
    newest years present define "recent", not today's date."""
    if n <= 0:
        return ()
    rows = conn.execute("SELECT DISTINCT json_extract(value, '$.fy') AS fy FROM evidence WHERE fact = 'lca' "
                        "ORDER BY fy DESC LIMIT ?", (n,))
    return tuple(r[0] for r in rows)


def _days_ago(days: int | None) -> str:
    if days is None:
        return ""
    return "today" if days == 0 else "1 day ago" if days == 1 else f"{days} days ago"


def _median_level(wage: int, hq: str | None, wages: tuple[Area, ...]) -> tuple[int, str] | None:
    """The wage level of an LCA median junior wage. With a usable hq value ("City, ST"): the areas it names, matched
    as a posting location is, and the lowest level across them; an hq that names no listed area is outside the table,
    so it is None. With no usable hq: only when every listed area gives the same level. Else None (the caller falls
    back to the level I share)."""
    if hq is not None:
        areas = matching_areas(hq, wages)
        if not areas:
            return None
        level, where = min(level_at(wage, a) for a in areas), " and ".join(a.name for a in areas)
    else:
        levels = {level_at(wage, a) for a in wages}
        if len(levels) != 1:
            return None
        level, where = levels.pop(), "every listed area"
    return level, f"LCA median junior wage ${wage:,} in {where}"  # the signal's reason names the level


def _one_hq(values: set[str], wages: tuple[Area, ...]) -> str | None:
    """The company's hq value when its values all name the same areas (one of them, in a stable order), else None."""
    if not values:
        return None
    named = {tuple(a.name for a in matching_areas(v, wages)) for v in values}
    return min(values) if len(named) == 1 else None


def is_large(stage: str | None, size_low: int | None, small: Mapping) -> bool:
    """A later-stage or over-borderline company (`small` is the `[small]` section). The loader drops any_us_role for
    it and `large_later_stage` pushes it to the bottom; both ask this one function."""
    return (stage in small["later_stages"]) or (size_low is not None and size_low > small["borderline_headcount"])


def load_facts(conn: sqlite3.Connection, canon: dict[int, int], doubtful: frozenset[int], roles: dict[int, Roles],
               today: date, cfg: RankerConfig, wages: tuple[Area, ...]) -> list[RankFacts]:
    """One `RankFacts` per listed company (status candidate, backlog or working, not merged away), in `company_id`
    order. Everything a merged-away duplicate holds counts for its survivor. `roles` comes from `load_roles`, which
    already left stale and untrusted postings out of every role field."""
    def owner(cid: int) -> int:
        return canon.get(cid, cid)

    marks = ", ".join("?" for _ in LISTED_STATUSES)
    companies = conn.execute(f"SELECT company_id, name FROM company WHERE status IN ({marks}) AND merged_into IS NULL "
                             "ORDER BY company_id", LISTED_STATUSES).fetchall()
    if not companies:
        return []
    ranker, small = cfg.ranker, cfg.section("small")
    conf = cfg.section("confidence")
    old_days = conf["evidence_old_days"]

    # The winning company_fact row of each fact, across the company and the duplicates merged into it.
    won: dict[tuple[int, str], tuple[tuple, sqlite3.Row]] = {}
    marks = ", ".join("?" for _ in _FACTS)
    for r in conn.execute(f"SELECT * FROM company_fact WHERE fact IN ({marks})", _FACTS):
        slot = (owner(r["company_id"]), r["fact"])
        key = fact_order_key(r)
        if slot not in won or key > won[slot][0]:
            won[slot] = (key, r)

    def row(cid: int, fact: str) -> sqlite3.Row | None:
        return won[(cid, fact)][1] if (cid, fact) in won else None

    # LCA: the newest row of each fiscal year, summed over the recent years of the whole store.
    recent = set(recent_fiscal_years(conn, ranker["lca_recent_years"]))
    lca_year: dict[int, dict[int, tuple[tuple, sqlite3.Row, dict]]] = {}
    for r in conn.execute("SELECT evidence_id, company_id, value, confidence, locked, source_kind, observed_on, "
                          "recorded_at FROM evidence WHERE fact = 'lca'"):
        data = json.loads(r["value"])
        years = lca_year.setdefault(owner(r["company_id"]), {})
        key = (r["recorded_at"], r["evidence_id"])
        if data["fy"] not in years or key > years[data["fy"]][0]:
            years[data["fy"]] = (key, r, data)

    # Funding: the newest filing per company.
    funding: dict[int, tuple[tuple, sqlite3.Row, date]] = {}
    for r in conn.execute("SELECT evidence_id, company_id, value, confidence, locked, source_kind, observed_on "
                          "FROM evidence WHERE fact = 'funding'"):
        filed = date.fromisoformat(json.loads(r["value"])["filed_on"])
        cid = owner(r["company_id"])
        key = (filed, r["evidence_id"])
        if cid not in funding or key > funding[cid][0]:
            funding[cid] = (key, r, filed)

    # The hq values from hq evidence that is not a guess (as identity reads it). Two values that name different
    # areas are no usable hq.
    hq_values: dict[int, set[str]] = {}
    for r in conn.execute("SELECT company_id, value FROM evidence WHERE fact = 'hq' AND confidence <> 'guess'"):
        if isinstance(r["value"], str) and r["value"].strip():
            hq_values.setdefault(owner(r["company_id"]), set()).add(r["value"].strip())

    shared = {r[0] for r in conn.execute("SELECT value FROM shared_key WHERE kind = 'domain'")}
    shared_cids = {owner(r["company_id"]) for r in conn.execute("SELECT company_id, value FROM evidence "
                                                                "WHERE fact = 'domain'") if r["value"] in shared}

    # Reach: people of the right kind with no active no-go, the best of them per company, and HN posts.
    kinds = tuple(cfg.section("reachable_person")["role_kinds"])
    team = {r[0] for r in conn.execute("SELECT DISTINCT person_id FROM person_source WHERE kind = 'team'")}
    marks = ", ".join("?" for _ in kinds)
    people: dict[int, tuple[tuple, sqlite3.Row]] = {}
    for r in conn.execute(
            f"SELECT p.person_id, p.company_id, p.name, p.role_kind FROM person p WHERE p.role_kind IN ({marks}) "
            "AND NOT EXISTS (SELECT 1 FROM no_go g WHERE g.person_id = p.person_id AND g.lifted_at IS NULL)", kinds):
        cid = owner(r["company_id"])
        key = (r["person_id"] not in team, kinds.index(r["role_kind"]), r["person_id"])
        if cid not in people or key < people[cid][0]:
            people[cid] = (key, r)
    hn: dict[int, int] = {}  # company to the age in days of its newest post that asks for email
    for r in conn.execute("SELECT company_id, posted_at FROM company_post WHERE asks_email = 1 "
                          "AND posted_at IS NOT NULL"):
        age = age_days(r["posted_at"], today)
        cid = owner(r["company_id"])
        if age is None or r["posted_at"][:10] > today.isoformat():  # a post dated after today is not evidence
            continue
        if cid not in hn or age < hn[cid]:
            hn[cid] = age

    tiers: dict[int, sqlite3.Row] = {}
    for r in conn.execute("SELECT company_id, tier, promoted, reason FROM work_tier ORDER BY company_id"):
        cid = owner(r["company_id"])
        # The worst tier among the merged duplicates counts: the higher tier, and unpromoted over promoted.
        if cid not in tiers or (r["tier"], not r["promoted"]) > (tiers[cid]["tier"], not tiers[cid]["promoted"]):
            tiers[cid] = r
    watched = {owner(r[0]) for r in conn.execute("SELECT company_id FROM watched_page")}
    states = company_board_states(conn, canon, doubtful)
    # Imported here, not at the top: outreach.insights.dataset imports the ranking's stored scores, which import
    # this module.
    from outreach.insights.dataset import load_board_systems
    board_systems = load_board_systems(conn, canon)  # the interview tilt's board trait (#81)
    bands = default_bands()

    basis = cfg.section("pay_level")["basis"]
    out: list[RankFacts] = []
    for company_id, name in ((c["company_id"], c["name"]) for c in companies):
        cid = company_id
        rl = roles.get(cid)
        best = rl.best if rl else None

        # Evidence values.
        size_row = max((r for r in (row(cid, "size"), row(cid, "headcount")) if r is not None), key=size_order_key,
                       default=None)
        stage_row = row(cid, "stage")
        stage = stage_row["value"] if stage_row else None
        size_low = size_row["num_low"] if size_row else None
        size_high = size_row["num_high"] if size_row else None
        years = lca_year.get(cid, {})
        counted = [years[fy] for fy in sorted(years) if fy in recent]
        jr = sum(d["jr"] for _k, _r, d in counted)
        level1 = sum(d["level1"] for _k, _r, d in counted)
        months = None
        if cid in funding:
            months = max((today - funding[cid][2]).days, 0) // 30
        work = tiers.get(cid)
        work_tier = work["tier"] if work else None
        promoted = bool(work["promoted"]) if work else False

        # Reach.
        reach_via, reach_note, person, has_team = "", "", people.get(cid), False
        if person is not None:
            reach_via = "person"
            has_team = person[1]["person_id"] in team
            reach_note = f"Reachable person: {clean_text(person[1]['name'])}, {person[1]['role_kind']}"
        elif cid in hn and hn[cid] <= ranker["hn_post_days"]:
            reach_via, reach_note = "hn_post", f"An HN post asks for email, posted {_days_ago(hn[cid])}"

        # Pay: the first visible live posting (best first) with a readable range in a listed area, else the
        # employer's junior LCA history.
        pay_basis, pay_note, level, straddles = "", "", None, False
        for posting in (b for b in (rl.visible if rl else ()) if b.lane == best.lane):
            found = pay_level(posting.pay, posting.location, wages, basis)
            if found is not None:
                pay_basis, pay_note, level, straddles = "posted", found.note, found.level, found.at_min != found.at_mid
                break
        if level is None:  # the LCA median wage of the newest counted year that has one, then the level I share
            median = next((d["median_jr_wage"] for _k, _r, d in reversed(counted) if d.get("median_jr_wage")), None)
            found_median = _median_level(median, _one_hq(hq_values.get(cid, set()), wages), wages) if median else None
            if found_median is not None:
                pay_basis, level, pay_note = "lca", found_median[0], found_median[1]
        if level is None:
            history = lca_pay_level(jr, level1, min_filings=ranker["lca_wage_min_filings"])
            if history is not None:
                pay_basis, level, pay_note = "lca", history[0], history[1]

        # Lanes, in design order. A role lane blocks reason_no_role and any_us_role.
        role_lanes = rl.lanes if rl else ()
        large = is_large(stage, size_low, small)
        lanes: list[str] = [lane for lane in ("level_role", "intern_junior") if lane in role_lanes]
        if not lanes:
            if (months is not None and months <= ranker["funding_window_months"]) or jr > 0 or cid in watched:
                lanes.append("reason_no_role")
            if "any_us_role" in role_lanes and not large:
                lanes.append("any_us_role")

        # Weak reasons to doubt the company is small. None of them hides anything.
        flags: list[str] = []
        if rl and rl.open_count > ranker["many_postings"]:
            flags.append(f"{rl.open_count} open roles")
        most_software = max((d["software"] for _k, _r, d in years.values()), default=0)
        if most_software >= ranker["lca_software_many"]:
            flags.append(f"{most_software} software H-1B filings in one fiscal year")
        if cid in shared_cids:
            flags.append(SHARED_DOMAIN_FLAG)
        if stage in small["later_stages"] and size_row is None:
            flags.append("later stage, size unknown")

        grades = _grade_all(
            today=today, old_days=old_days, cfg=cfg, rl=rl, best=best, state=states.get(cid, "no_board"),
            everify=row(cid, "everify"), cap=row(cid, "cap_exempt"), staffing=row(cid, "staffing_risk"),
            stage_row=stage_row, size_row=size_row, counted=counted, funding=funding.get(cid), work=work,
            promoted=promoted, large=large, stage=stage, size_low=size_low, pay_basis=pay_basis, straddles=straddles,
            jr=jr, reach_via=reach_via, has_team=has_team, has_old_lca=bool(years))

        out.append(RankFacts(
            company_id=cid, lanes=tuple(lanes), best_posting_id=best.posting_id if best else None,
            best_role_age_days=best.age_days if best else None,
            everify=_value(row(cid, "everify")), cap_exempt=_value(row(cid, "cap_exempt")),
            staffing_risk=_value(row(cid, "staffing_risk")), lca_jr_recent=jr, lca_level1_recent=level1,
            pay_level=level, size_low=size_low, size_high=size_high, stage=stage, funded_months_ago=months,
            reachable_person=bool(reach_via), name=name, best_title=best.title if best else "",
            best_level_tier=best.level_tier if best else None, best_level_basis=best.level_basis if best else "",
            best_dated=best.posted_on is not None if best else True,
            best_sponsorship=best.sponsorship if best else None,
            pay_basis=pay_basis, pay_note=pay_note, weak_flags=tuple(flags),
            size_confidence=size_row["confidence"] if size_row else None, work_tier=work_tier,
            work_promoted=promoted, reach_via=reach_via, reach_note=reach_note,
            ignored_postings=rl.ignored if rl else 0, inactive_postings=rl.inactive if rl else 0,
            stale_postings=rl.stale if rl else (), grades=grades,
            features=features_of(size_low=size_low, size_high=size_high, stage=stage,
                                 level_tier=best.level_tier if best else None,
                                 intern=tuple(lanes[:1]) == ("intern_junior",),
                                 board_system=board_systems.get(cid), bands=bands)))
    return out


def _value(r: sqlite3.Row | None) -> str | None:
    return r["value"] if r else None


def _row_grade(r: sqlite3.Row | None, label: str, today: date, old_days: int) -> Confidence:
    if r is None:
        return grade_evidence(confidence=None, locked=False, source_kind=None, observed_on=None, today=today,
                              old_days=old_days, label=label)
    return grade_evidence(confidence=r["confidence"], locked=bool(r["locked"]), source_kind=r["source_kind"],
                          observed_on=r["observed_on"], today=today, old_days=old_days, label=label)


def _grade_all(*, today, old_days, cfg, rl, best, state, everify, cap, staffing, stage_row, size_row, counted,
               funding, work, promoted, large, stage, size_low, pay_basis, straddles, jr, reach_via, has_team,
               has_old_lca) -> dict[str, Confidence]:
    """The eleven grades of one company, each from its own evidence. A grade is `low` when the evidence is missing."""
    def graded(r, label):
        return _row_grade(r, label, today, old_days)

    size_label = "headcount" if size_row is not None and size_row["fact"] == "headcount" else "size"
    small_cfg = cfg.section("small")

    # live_role: the roles' own grade, else what the company's boards say about having no roles at all.
    live = rl.confidence if rl else grade_no_role(company_board_state=state, stale_kinds=(), stale_reason="")

    # lca_junior: the weakest of the rows it sums.
    if counted:
        lca = graded(counted[0][1], "lca")
        for _key, r, _data in counted[1:]:
            lca = weaker(lca, graded(r, "lca"))
    elif has_old_lca:
        lca = Confidence("low", "no lca evidence in the recent fiscal years")
    else:
        lca = graded(None, "lca")

    # large_later_stage: the evidence that triggered it (the stage row, else the size row).
    if stage in small_cfg["later_stages"]:
        large_grade = graded(stage_row, "stage")
    elif large:
        large_grade = graded(size_row, size_label)
    elif size_row is not None:
        large_grade = graded(size_row, size_label)
    else:
        large_grade = graded(stage_row, "stage")

    # staffing_risk: the evidence row; work tier 3 (not promoted) with no row saying so is medium.
    staffing_grade = graded(staffing, "staffing risk")
    if work is not None and work["tier"] == 3 and not promoted:
        tier_grade = Confidence("medium", f"work tier 3: {work['reason']}")
        row_says_high = staffing is not None and staffing["value"] == "high"
        if not row_says_high or staffing_grade.level == "low":
            staffing_grade = tier_grade

    # pay_level.
    pay = grade_pay(source=pay_basis, straddles=straddles, filings=jr,
                    medium_filings=cfg.section("confidence")["lca_pay_medium_filings"])

    return {
        "live_role": live,
        "posting_sponsors": grade_sponsor(provisional=best.provisional if best else None),
        "everify": graded(everify, "everify"),
        "cap_exempt": graded(cap, "cap exempt"),
        "pay_level": pay,
        "lca_junior": lca,
        "small": graded(size_row, size_label),
        "reachable_person": grade_reach(via=reach_via, has_team_source=has_team),
        "funding": graded(funding[1] if funding else None, "funding"),
        "staffing_risk": staffing_grade,
        "large_later_stage": large_grade,
    }
