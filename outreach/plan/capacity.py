"""LinkedIn and email capacity, and the lane quotas (#15, Task 4).

LinkedIn: the plan's own share is `linkedin.plan_quota` a month, counted from touches the plan proposed
(`context.source = 'plan'`); a hard total (`linkedin.hard_total`) counts every request, his own included, and wins
when it is lower. A day's number is the remaining budget over the weekdays left, rounded half up, so it lands near 7
and corrects itself after a missed day with no catch-up pressure. Email: the sender's limit for the day minus what is
already committed. Capacity is never negative and a zero day is a finished plan, not an error. Reads only; `today` is
a parameter.
"""
from __future__ import annotations

import math
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from outreach.plan.config import PlanConfig
from outreach.plan.dates import month_range, utc_window, weekdays_left
from outreach.plan.outbound import load_outbound

__all__ = ["LinkedinCapacity", "EmailCapacity", "linkedin_capacity", "current_send_limit", "email_capacity",
           "plan_capacity", "lane_quotas"]

_EPS = 1e-9  # round half up on a float that may sit a hair under .5


@dataclass(frozen=True)
class LinkedinCapacity:
    budget: int  # plan_quota
    used: int  # plan-sourced requests this month
    hard_limit: int  # hard_total
    hard_used: int  # every request this month, his own included
    remaining: int
    days_left: int
    today: int
    reason_used: int  # plan-sourced requests this month in the reason_no_role lane


@dataclass(frozen=True)
class EmailCapacity:
    send_limit: int
    committed: int
    paused: bool
    slots: int


def linkedin_capacity(conn: sqlite3.Connection, canon: dict[int, int], today: date, cfg: PlanConfig) -> LinkedinCapacity:
    tz = cfg.get("plan", "timezone")
    first, last = month_range(today)
    month = [o for o in load_outbound(conn, canon, tz=tz, since=first)
             if o.sent_on <= last and o.channel == "linkedin_request"]
    plan = [o for o in month if o.context_source == "plan"]
    budget, hard_limit = cfg.get("linkedin", "plan_quota"), cfg.get("linkedin", "hard_total")
    used, hard_used = len(plan), len(month)
    remaining = max(0, min(budget - used, hard_limit - hard_used))
    days_left = max(1, weekdays_left(today))
    if remaining <= 0:
        day = 0
    else:
        day = min(cfg.get("linkedin", "daily_max"), max(1, math.floor(remaining / days_left + 0.5)))
    return LinkedinCapacity(budget=budget, used=used, hard_limit=hard_limit, hard_used=hard_used,
                            remaining=remaining, days_left=days_left, today=day,
                            reason_used=sum(1 for o in plan if o.lane == "reason_no_role"))


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
    """LinkedIn rows plus email slots for the weekday `day`: what `build_plan` stores in `daily_list.capacity`, and
    tomorrow's total for the next weekday."""
    return (linkedin_capacity(conn, canon, day, cfg).today
            + email_capacity(conn, day, cfg, limit_fn=limit_fn).slots)


def lane_quotas(n: int, used: int, reason_used: int, cfg: PlanConfig) -> tuple[int, int]:
    """`(role_n, reason_n)` for `n` rows, with `role_n + reason_n == n`. The reason share is tracked over the month,
    not per day, so one row of seven a day does not drift to 14%: `used` and `reason_used` are the month's plan
    requests so far. The reason lane takes at most `max(1, ceil(0.4 * n))` of a day, and never more than `n`."""
    if n <= 0:
        return 0, 0
    share = cfg.get("lanes", "reason_share")
    owed = math.floor(share * (used + n) + 0.5 + _EPS) - reason_used
    cap = max(1, math.ceil(2 * share * n - _EPS))
    reason_n = min(n, cap, max(0, owed))
    return n - reason_n, reason_n
