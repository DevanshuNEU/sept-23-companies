"""What each review item kind means: its trigger, family, fixes and who raises it (#16).

The kinds are the ones the `review_item` CHECK allows. A new kind fails `tests/test_review_kinds.py` until someone
decides what it is.
"""
from dataclasses import dataclass
from typing import Literal

__all__ = ["KindSpec", "KINDS", "spec_of", "trigger_of", "flag_family", "BOARD_DOUBTFUL_PREFIX", "CONFLICT_PREFIX",
           "CAREERS_UNREADABLE_PREFIX", "NO_GO_REASONS"]

BOARD_DOUBTFUL_PREFIX = "board match doubtful"
CONFLICT_PREFIX = "Postings disagree on sponsorship"
CAREERS_UNREADABLE_PREFIX = "careers page unreadable"

# The `no_go.reason` values, in the schema's order.
NO_GO_REASONS = ("no_sponsorship", "citizenship", "over_300", "defence_contractor", "agency", "said_no", "stop_reply",
                 "not_a_fit", "other")


@dataclass(frozen=True)
class KindSpec:
    kind: str
    trigger: int | None  # the issue's trigger 1 to 5, or None for a kind that is not one of the five
    family: str  # boards, sponsorship, size, domain, merge, posting or other
    fixes: tuple[str, ...]  # a subset of ok, edit, no_go, merge
    edit_field: str | None  # the field the edit fix sets
    holds_email: bool  # an open item of this kind holds up an email
    raised_by: tuple[str, ...]  # the producers that raise it
    # Needs you shows an open item of this kind only for a company that asks for his attention (`scope.attention`);
    # the rest wait in Data gaps. True for every kind today: a kind that must always show is a decision.
    scoped: bool = True


def _spec(kind, trigger, family, fixes, edit_field, raised_by, holds_email=False) -> KindSpec:
    return KindSpec(kind, trigger, family, tuple(fixes), edit_field, holds_email, tuple(raised_by))


_BOARD_FIXES = ("ok", "no_go", "edit")
_THREE = ("ok", "no_go", "edit")

KINDS: dict[str, KindSpec] = {s.kind: s for s in (
    _spec("board_unreadable", 1, "boards", _BOARD_FIXES, "board_url", ("crawler",)),
    _spec("board_empty", 2, "boards", _BOARD_FIXES, "board_url", ("crawler",)),
    _spec("sponsorship_unclear", 3, "sponsorship", _THREE, "sponsorship", ("rules",)),
    _spec("size_unknown", 4, "size", _THREE, "size", ("daily_list",), holds_email=True),
    _spec("domain_missing", 5, "domain", _THREE, "domain", ("sender", "crawler"), holds_email=True),
    _spec("size_guess", None, "size", _THREE, "size", ("ranker",), holds_email=True),
    _spec("size_check", None, "size", _THREE, "size", ("ranker", "source"), holds_email=True),
    _spec("flagged", None, "other", ("ok", "no_go"), None, ("ranker", "crawler", "claude", "import")),
    _spec("reader_disagrees", None, "posting", ("ok", "no_go"), None, ("reader",)),
    _spec("careers_changed", None, "other", ("ok", "no_go"), None, ("crawler",)),
    _spec("merge_candidate", None, "merge", ("ok", "merge"), None, ("source",)),
)}


def spec_of(kind: str) -> KindSpec:
    try:
        return KINDS[kind]
    except KeyError:
        raise KeyError(f"unknown review kind {kind!r}") from None


def flag_family(reason: str) -> Literal["board_identity", "sponsorship_conflict", "careers_unreadable", "other"]:
    """Which family a `flagged` item belongs to, by the start of its reason (case does not matter)."""
    low = reason.lower()
    if low.startswith(BOARD_DOUBTFUL_PREFIX.lower()):
        return "board_identity"
    if low.startswith(CONFLICT_PREFIX.lower()):
        return "sponsorship_conflict"
    if low.startswith(CAREERS_UNREADABLE_PREFIX.lower()):
        return "careers_unreadable"
    return "other"


def trigger_of(kind: str, reason: str = "") -> int | None:
    """The issue's trigger number for an item. A `flagged` item takes its family's: a sponsorship conflict is 3 and
    an unreadable careers page is 1."""
    spec = spec_of(kind)
    if kind == "flagged":
        return {"sponsorship_conflict": 3, "careers_unreadable": 1}.get(flag_family(reason))
    return spec.trigger
