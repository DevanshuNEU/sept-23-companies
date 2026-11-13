"""When each approved email is expected to go out (#145).

A walk down the approved queue on a virtual clock. At each moment it asks the sender's own rule
(`queue.decide_all`, the code the tick uses) which message goes first, places it there, counts that send against its
company and the day, and moves on: to the next moment a wait ends when nothing can go. So the business days, the
recipient's hours, the per-company limit and gap and the daily limit are the sender's, not a second copy of them.

Two things are estimates. Spacing between sends is random (`human.spread_gap`), so the walk uses its expected
gap (`human.expected_gap_minutes`: the formula with its factor at 1.0); the first send of a day
and each company's gap use their fixed offsets. Later days
use today's limit as it stands (the ramp may allow more, so mail can only come earlier than shown). Read only: the
store is read once, by `queue.prepare`.
"""
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
    pending = list(p.queue)
    sends = {company: list(sent) for company, sent in p.sends.items()}
    sent_by_day = {cal.ny_date(now, cfg): p.sent_today}
    next_send = p.next_send
    placed: dict[int, datetime] = {}
    at = now
    while pending and at <= end:
        found = queue.decide_all(p, at, cfg, gate=None, pending=pending, sends=sends, sent_by_day=sent_by_day,
                                 next_send=next_send, limit=p.limit)
        ready = next((v.message_id for v in found if v.reason == "ready"), None)
        if ready is not None:
            q = next(q for q in pending if q.message_id == ready)
            placed[ready] = at
            sends.setdefault(q.company_id, []).append((at, q.seq == 0))
            day = cal.ny_date(at, cfg)
            sent_by_day[day] = sent_by_day.get(day, 0) + 1
            pending.remove(q)
            # The sender's rule: minutes left in the window of the recipients still queued over the sends left.
            left = max(p.limit - sent_by_day[day], 0)
            zones = [cal.recipient_zone(name) for name in {r.tz for r in pending}]
            minutes_left = queue.minutes_left_among(zones, at, cfg)
            next_send = at + timedelta(minutes=human.expected_gap_minutes(minutes_left, left, cfg))
            continue
        later = [v.earliest_at for v in found
                 if v.reason in queue.TIME_REASONS and v.earliest_at is not None and v.earliest_at > at]
        if not later:
            break
        at = min(later)
    return placed
