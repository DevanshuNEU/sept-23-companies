"""When to ask "what came of this?" and when to call a touch quiet.

The ruling (#33 rulings 2 and 4): ask once a touch is `prompt_after_days` old with no outcome and no snooze; ask once
more `reask_after_days` after the first snooze when there is exactly one; never after a second snooze; at
`quiet_after_days` the touch is marked quiet whatever was answered. Days are New York calendar days from the touch's
send date, so a prompt never comes an hour early because of a time zone. `cfg` is anything with those three numbers
(the API's `ApiConfig`). Pure: `now` is a parameter.
"""
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Protocol

from outreach.outcomes.windows import ny_day

_OUTCOME_COLUMNS = ("accepted_at", "replied_at", "referred_at", "interview_at", "offer_at", "quiet_at")


class PromptConfig(Protocol):
    prompt_after_days: int
    reask_after_days: int
    quiet_after_days: int


def _days_between(earlier: str, later: str) -> int:
    return (date.fromisoformat(ny_day(later)) - date.fromisoformat(ny_day(earlier))).days


def _settled(touch: Mapping) -> bool:
    """Undone, or it already has an outcome (a quiet mark is one)."""
    return touch["undone_at"] is not None or any(touch[c] is not None for c in _OUTCOME_COLUMNS)


def due_for_prompt(touch: Mapping, snoozes: Sequence[str], now: str, cfg: PromptConfig) -> bool:
    """Whether to ask about this touch now. `touch` is an `outreach_touch` row (or a dict with its date columns);
    `snoozes` are the times of its "Nothing yet" answers. A touch past day 21 that nobody has marked quiet yet is
    still asked about: the sweeper marks it, and a reply that day is accepted."""
    if _settled(touch):
        return False
    if _days_between(touch["sent_at"], now) < cfg.prompt_after_days:
        return False
    if not snoozes:
        return True
    if len(snoozes) > 1:
        return False
    return _days_between(snoozes[0], now) >= cfg.reask_after_days


def should_mark_quiet(touch: Mapping, now: str, cfg: PromptConfig) -> bool:
    """Whether the sweeper marks this touch quiet: `quiet_after_days` old and still no outcome."""
    if _settled(touch):
        return False
    return _days_between(touch["sent_at"], now) >= cfg.quiet_after_days
