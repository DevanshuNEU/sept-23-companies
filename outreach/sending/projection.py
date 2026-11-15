"""When each approved email is expected to go out (#145).

A walk down the approved queue on a virtual clock. At each moment it asks the sender's own rule
(`queue.decide_all`, the code the tick uses) which message goes first, places it there, counts that send against its
company and the day, and moves on: to the next moment a wait ends when nothing can go. So the business days, the
recipient's hours, the per-company limit and gap and the daily limit are the sender's, not a second copy of them.

Each sending account (`Prepared.senders`) places its own messages with its own daily limit and spacing; the company
counts and gaps are shared, as in the sender. Two things are estimates. Spacing between sends is random (`human.spread_gap`), so the walk uses its expected
gap (`human.expected_gap_minutes`: the formula, with its full-day floor, at factor 1.0); the first send of a day
and each company's gap use their fixed offsets. Later days
use today's limit as it stands (the ramp may allow more, so mail can only come earlier than shown). Read only: the
store is read once, by `queue.prepare`. Projected times can run up to about 15 minutes early over a day (the real
sender ticks every 2 minutes and each gap lands on the next tick), and a claim in doubt (`sending`, `send_unknown`)
counts as a send.
"""
import dataclasses
from datetime import datetime, timedelta, timezone

from outreach.sending import calendar as cal
from outreach.sending import human, queue
from outreach.sending.config import SendConfig

__all__ = ["MAX_DAYS", "project"]

MAX_DAYS = 30  # calendar days the walk looks ahead; an email that cannot go within them has no projection


def project(p: queue.Prepared, now: datetime, cfg: SendConfig) -> dict[int, datetime]:
    """Message id to the moment it is expected to send. A message the sender will never send as it stands (a stop, a
    paused or held company, no address, no thread, an earlier follow-up still waiting, a bad time zone) or one that
    cannot go within `MAX_DAYS` days is absent."""
    now = now.astimezone(timezone.utc)
    end = now + timedelta(days=MAX_DAYS)
    # A pause (his, or an account's hard block) is not a reason to show nothing: the walk runs as if he resumed now.
    p = dataclasses.replace(p, paused=False,
                            days={a: dataclasses.replace(d, paused=False) for a, d in p.days.items()})
    pending = list(p.queue)
    sends = {company: list(sent) for company, sent in p.sends.items()}
    today = cal.ny_date(now, cfg)
    sent_by_day = {a: {today: d.sent_today} for a, d in p.days.items()}
    next_send = {a: d.next_send for a, d in p.days.items()}
    placed: dict[int, datetime] = {}
    at = now
    while pending and at <= end:
        found = queue.decide_all(p, at, cfg, gate=None, pending=pending, sends=sends, sent_by_day=sent_by_day,
                                 next_send=next_send)
        # The sender's pick for each account: earliest window close, then queue order. After one placement the
        # queue is decided again at the same moment, so the next account sees that send at its company.
        account = q = None
        for account in p.senders:
            q = queue.pick(found, pending, now=at, cfg=cfg, account=account)
            if q is not None:
                break
        if q is not None:
            placed[q.message_id] = at
            sends.setdefault(q.company_id, []).append((at, q.seq == 0, p.days[account].account_id))
            day = cal.ny_date(at, cfg)
            sent_by_day[account][day] = sent_by_day[account].get(day, 0) + 1
            pending.remove(q)
            # The sender's rule: minutes left in the window of the account's recipients that can still go today (as
            # decided at `at`, before this placement) over the account's sends left, never below its full window
            # today over its limit (a late start sends fewer, not faster).
            limit = p.days[account].limit
            left = max(limit - sent_by_day[account][day], 0)
            others = [v for v in found if v.message_id != q.message_id]
            minutes_left = queue.minutes_left_for(others, pending, at, cfg, account)
            day_minutes = queue.day_minutes_for(others, pending, at, cfg, account)
            next_send[account] = at + timedelta(minutes=human.expected_gap_minutes(
                minutes_left, left, cfg, day_minutes=day_minutes, limit=limit))
            continue
        later = [v.earliest_at for v in found
                 if v.reason in queue.TIME_REASONS and v.earliest_at is not None and v.earliest_at > at]
        if not later:
            break
        at = min(later)
    return placed
