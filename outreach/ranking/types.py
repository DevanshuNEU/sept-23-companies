"""Ranking's contract: the facts a signal may read, what a signal returns, and the signal protocol (#30)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

__all__ = ["CompanyFacts", "SignalValue", "Signal"]


@dataclass(frozen=True)
class CompanyFacts:
    """Everything the signals know about one company. Built once per company; a signal never reads the store."""
    company_id: int
    lanes: tuple[str, ...]
    best_posting_id: int | None
    best_role_age_days: int | None
    everify: str | None
    cap_exempt: str | None
    staffing_risk: str | None
    lca_jr_recent: int
    lca_level1_recent: int
    pay_level: int | None
    size_low: int | None
    size_high: int | None
    stage: str | None
    funded_months_ago: int | None
    reachable_person: bool


@dataclass(frozen=True)
class SignalValue:
    """One signal's verdict: a number from -1.0 to 1.0 and the reason in plain words."""
    value: float
    reason: str

    def __post_init__(self) -> None:
        v = self.value
        if isinstance(v, bool) or not isinstance(v, (int, float)) or math.isnan(v) or not -1.0 <= v <= 1.0:
            raise ValueError(f"a signal value must be a number from -1.0 to 1.0, got {v!r}")


class Signal(Protocol):
    name: str

    def score(self, facts: CompanyFacts) -> SignalValue:
        ...
