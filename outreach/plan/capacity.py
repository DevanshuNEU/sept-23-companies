"""LinkedIn and email capacity, and the lane quotas (#15, Task 4).

LinkedIn counts requests over any 7 days (#9). The plan's own share is `linkedin.plan_quota` in the last 7 New York
days (today and the 6 before it), counted from touches the plan proposed (`context.source = 'plan'`); a hard total
(`linkedin.hard_total`) counts every request in the same 7 days, his own included, and wins when it is lower.

Today is a queue of companies (#9 company-first ruling, 2026-10-26): he sends one wave of up to
`queue.requests_per_company` requests at each, so the day holds `companies_today`:
`min(queue.companies_per_day, floor(remaining / queue.requests_per_company))`, where `remaining` is what the 7-day
windows have left. The weekly limit is a ceiling, never a target, so plenty left in the week does not raise the day,
and a missed day brings no catch-up; a heavy day lowers the days after it. Email: the sender's limit for the day
minus what is already committed. Capacity is never negative and a zero day is a finished plan, not an error. Reads
only; `today` is a parameter.
"""
from __future__ import annotations

import math
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from outreach.plan.config import PlanConfig
from outreach.plan.dates import last_seven_days, utc_window
from outreach.plan.outbound import load_outbound

__all__ = ["LinkedinCapacity", "EmailCapacity", "linkedin_capacity", "companies_today", "current_send_limit",
           "email_capacity", "plan_capacity", "plan_email_slots", "lane_quotas"]

_EPS = 1e-9  # round half up on a float that may sit a hair under .5


@dataclass(frozen=True)
class LinkedinCapacity:
    budget: int  # plan_quota, in any 7 days
    used: int  # plan-sourced requests in the last 7 days
    hard_limit: int  # hard_total, in any 7 days
    hard_used: int  # every request in the last 7 days, his own included
    remaining: int
    today: int  # companies on the day's queue: `companies_today(remaining)`
    reason_used: int  # plan-sourced requests in the last 7 days in the reason_no_role lane


@dataclass(frozen=True)
class EmailCapacity:
    send_limit: int
    committed: int
    paused: bool
    slots: int


def linkedin_capacity(conn: sqlite3.Connection, canon: dict[int, int], today: date, cfg: PlanConfig) -> LinkedinCapacity:
    tz = cfg.get("plan", "timezone")
    first, last = last_seven_days(today)
    week = [o for o in load_outbound(conn, canon, tz=tz, since=first)
            if o.sent_on <= last and o.channel == "linkedin_request"]
    plan = [o for o in week if o.context_source == "plan"]
    budget, hard_limit = cfg.get("linkedin", "plan_quota"), cfg.get("linkedin", "hard_total")
    used, hard_used = len(plan), len(week)
    remaining = max(0, min(budget - used, hard_limit - hard_used))
    day = companies_today(remaining, cfg)
    return LinkedinCapacity(budget=budget, used=used, hard_limit=hard_limit, hard_used=hard_used,
                            remaining=remaining, today=day,
                            reason_used=sum(1 for o in plan if o.lane == "reason_no_role"))


def companies_today(remaining: int, cfg: PlanConfig) -> int:
    """The companies on a day's queue: `queue.companies_per_day`, cut to the whole waves of
    `queue.requests_per_company` that the 7-day windows have left. Never negative."""
    return max(0, min(cfg.get("queue", "companies_per_day"), remaining // cfg.get("queue", "requests_per_company")))


def current_send_limit(conn: sqlite3.Connection, today: date, cfg: PlanConfig) -> int:
    """Today's `send_day.send_limit`, else the latest earlier row's, else `email.start_limit`. The seam #14's ramp
    fills: this plan only reads what the ramp wrote."""
    row = conn.execute("SELECT send_limit FROM send_day WHERE day <= ? ORDER BY day DESC LIMIT 1",
                       (today.isoformat(),)).fetchone()
    return row[0] if row else cfg.get("email", "start_limit")


def email_capacity(conn: sqlite3.Connection, today: date, cfg: PlanConfig, *,
                   limit_fn: Callable[[sqlite3.Connection, date, PlanConfig], int] = current_send_limit
                   ) -> EmailCapacity:
    start, end = utc_window(today, today, cfg.get("plan", "timezone"))
    committed = conn.execute(
        "SELECT COUNT(*) FROM message WHERE state IN ('approved', 'sending', 'send_unknown') "
        "OR (state IN ('planned', 'drafted') AND seq >= 1 AND due_on <= ?) "
        "OR (state = 'sent' AND sent_at >= ? AND sent_at < ?)", (today.isoformat(), start, end)).fetchone()[0]
    paused = bool(conn.execute("SELECT paused FROM sender WHERE sender_id = 1").fetchone()[0])
    limit = limit_fn(conn, today, cfg)
    slots = 0 if paused else min(cfg.get("email", "rows_max"), max(0, limit - committed))
    return EmailCapacity(send_limit=limit, committed=committed, paused=paused, slots=slots)


def plan_capacity(conn: sqlite3.Connection, canon: dict[int, int], day: date, cfg: PlanConfig, *,
                  limit_fn: Callable[[sqlite3.Connection, date, PlanConfig], int] = current_send_limit) -> int:
    """Queue rows (companies) plus email slots for the weekday `day`: what `build_plan` stores in `daily_list.capacity`, and
    tomorrow's total for the next weekday. With `plan.propose_people` false the plan has no email rows, so it counts
    no email slots (`plan_email_slots`)."""
    return linkedin_capacity(conn, canon, day, cfg).today + plan_email_slots(
        email_capacity(conn, day, cfg, limit_fn=limit_fn), cfg)


def plan_email_slots(email: EmailCapacity, cfg: PlanConfig) -> int:
    """The email rows the plan may write: the sender's free slots, or 0 when the plan proposes no people (company
    first, #9). The sender's own limits are not touched."""
    return email.slots if cfg.get("plan", "propose_people") else 0


def lane_quotas(n: int, used: int, reason_used: int, cfg: PlanConfig) -> tuple[int, int]:
    """`(role_n, reason_n)` for `n` rows, with `role_n + reason_n == n`. The reason share is tracked over the last 7
    days, not per day, so one row of seven a day does not drift to 14%: `used` and `reason_used` are the window's
    plan requests. The reason lane takes at most `max(1, ceil(0.4 * n))` of a day, and never more than `n`."""
    if n <= 0:
        return 0, 0
    share = cfg.get("lanes", "reason_share")
    owed = math.floor(share * (used + n) + 0.5 + _EPS) - reason_used
    cap = max(1, math.ceil(2 * share * n - _EPS))
    reason_n = min(n, cap, max(0, owed))
    return n - reason_n, reason_n
