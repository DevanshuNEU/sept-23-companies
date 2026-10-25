"""The one shape of `outreach_touch.context`.

The schema CHECK pins these seven keys (#30). `source` is `plan` for a touch logged from a plan row and `own`
otherwise; #15's 150 a month quota counts `source = 'plan'`. The API writes it for LinkedIn and application touches,
and the sender writes it for email touches.
"""
import math
from collections.abc import Mapping
from typing import Literal

CONTEXT_KEYS = ("source", "lane", "roleKind", "roleAgeDays", "rankerVersion", "signals", "roleNamed")


def build_context(
    *,
    source: Literal["plan", "own"],
    lane: str,
    role_kind: str | None,
    role_age_days: int | None,
    ranker_version: str | None,
    signals: Mapping[str, float],
    role_named: bool,
) -> dict:
    """The dict `log_touch` stores as the touch's context. `ValueError` for anything the schema would refuse, so the
    mistake is named here and not as an IntegrityError at the insert."""
    if source not in ("plan", "own"):
        raise ValueError(f"source must be 'plan' or 'own', not {source!r}")
    if not isinstance(lane, str) or not lane:
        raise ValueError("lane must be a non-empty string")
    if role_kind is not None and not isinstance(role_kind, str):
        raise ValueError("role_kind must be a string or None")
    if role_age_days is not None and (isinstance(role_age_days, bool) or not isinstance(role_age_days, int)
                                      or role_age_days < 0):
        raise ValueError("role_age_days must be a whole number of days or None")
    if ranker_version is not None and not isinstance(ranker_version, str):
        raise ValueError("ranker_version must be a string or None")
    if not isinstance(role_named, bool):
        raise ValueError("role_named must be True or False")
    if not isinstance(signals, Mapping):
        raise ValueError("signals must be a mapping of signal name to value")
    clean: dict[str, float] = {}
    for name, value in signals.items():
        if not isinstance(name, str) or not name:
            raise ValueError("a signal name must be a non-empty string")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"signal {name!r} must be a finite number, not {value!r}")
        clean[name] = float(value)
    return {
        "source": source,
        "lane": lane,
        "roleKind": role_kind,
        "roleAgeDays": role_age_days,
        "rankerVersion": ranker_version,
        "signals": clean,
        "roleNamed": role_named,
    }
