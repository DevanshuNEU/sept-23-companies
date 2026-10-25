"""Every reason and note sentence a review item uses (#16).

Pure functions, no store. They are UI copy: plain words, short sentences, never an en or em dash (`clean` removes
them from any text that came from outside). The UI words are Devanshu's own: "ok", "no-go list", "mark ok".
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from zoneinfo import ZoneInfo

from outreach.review.kinds import NO_GO_REASONS

__all__ = ["clean", "day", "sponsorship_reason", "size_reason", "domain_reason", "board_reason", "reopened",
           "fix_note", "no_go_note", "ok_note", "already_no_go_note", "person_no_go_note", "keep_separate_note",
           "settled_by_field", "NO_GO_SETTLED", "TIE_WARNING", "merge_note", "merged_away_note", "same_company_note",
           "shared_domain_note", "moved_pair_note", "MERGE_TAKEN_BACK", "UNDONE_TEXT", "hand_set_text",
           "board_replaced_text"]

_NEW_YORK = ZoneInfo("America/New_York")
_DASHES = str.maketrans({chr(0x2013): "-", chr(0x2014): "-"})

_ATS_NAMES = {
    "ashby": "Ashby", "greenhouse": "Greenhouse", "lever": "Lever", "workday": "Workday", "workable": "Workable",
    "smartrecruiters": "SmartRecruiters", "oracle_orc": "Oracle Recruiting", "rippling": "Rippling",
    "breezy": "Breezy", "pinpoint": "Pinpoint", "teamtailor": "Teamtailor", "recruitee": "Recruitee",
    "bamboohr": "BambooHR", "jazzhr": "JazzHR", "gem": "Gem", "icims": "iCIMS", "cornerstone": "Cornerstone",
}

_NO_GO_PHRASES = {
    "no_sponsorship": "does not sponsor",
    "citizenship": "needs citizenship",
    "over_300": "over 300 people",
    "defence_contractor": "defence contractor",
    "agency": "a staffing agency",
    "said_no": "they said no",
    "stop_reply": "they asked us to stop",
    "not_a_fit": "not a fit",
    "other": "another reason",
}

_FIELD_LABELS = {"size": "Size", "domain": "Email domain", "sponsorship": "Sponsorship", "board_url": "Job board"}
_SPONSORSHIP_VALUES = {"yes": "Yes", "no": "No", "opt_only": "OPT only", "unclear": "Unclear"}

_PREVIOUS = {"marked_ok": "you marked it ok", "marked_no_go": "you added it to the no-go list",
             "field_edited": "you changed it", "cleared": "it cleared itself"}

# What changed, for the kinds whose key is not a date. A kind named here reads the same whatever the key holds.
_CHANGED = {
    "sponsorship_unclear": "the posting wording changed",
    "size_unknown": "the size is still not recorded",
    "size_guess": "a new size estimate arrived",
    "size_check": "the size flags changed",
    "domain_missing": "the domain evidence changed",
    "flagged": "something new was flagged",
    "reader_disagrees": "the posting reading changed",
    "careers_changed": "the careers page changed again",
    "merge_candidate": "another company looks like the same one",
}


def clean(text) -> str:
    """The text with each en or em dash turned into a hyphen."""
    return str(text).translate(_DASHES)


def day(iso: str) -> str:
    """`'Oct 22'`: the New York date of a canonical UTC timestamp."""
    moment = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(_NEW_YORK)
    return f"{moment:%b} {moment.day}"


def sponsorship_reason(company: str, title: str) -> str:
    return clean(f'The "{title}" posting is neither a clear yes nor a clear no on sponsorship. '
                 f"Setting it to No adds {company} to the no-go list.")


def size_reason(company: str, basis: str) -> str:
    if basis == "picked":
        lead = f"You picked {company}, and nobody has recorded its size."
    elif basis == "planned":
        lead = f"{company} is in today's plan, and nobody has recorded its size."
    else:
        raise ValueError(f"basis is 'picked' or 'planned', not {basis!r}")
    return clean(f"{lead} Over 300 people hides a company, so one number with a source settles it.")


def domain_reason(company: str) -> str:
    return clean(f"{company} has people queued for email and no email domain on file. "
                 "Add the domain so each address can be checked against it.")


def _ats_name(ats: str) -> str:
    return clean(_ATS_NAMES.get(ats, ats.title()))


def _postings(n: int, verb_one: str, verb_many: str) -> str:
    return f"its {n} posting {verb_one}" if n == 1 else f"its {n} postings {verb_many}"


def board_reason(kind: str, *, ats: str, error_streak: int, empty_streak: int, last_error: str | None, since: str,
                 last_ok_at: str | None, open_postings: int, closed_postings: int) -> str:
    """The sentence for a board item, built from the board's state now (a stored sentence would go stale)."""
    name = _ats_name(ats)
    if kind == "board_unreadable":
        text = f"The {name} board has answered with an error on every read since {day(since)}"
        if last_error and last_error.strip():
            text += f" ({clean(last_error.strip())})"
        text += "."
        if open_postings > 0:
            kept = _postings(open_postings, "stays open", "stay open")
            seen = f" with what we last saw on {day(last_ok_at)}" if last_ok_at else ""
            return f"{text} An error never closes a company, so {kept}{seen}."
        return f"{text} An error never closes a company."
    if kind == "board_empty":
        if empty_streak < 2:
            text = f"The {name} board returned no jobs once."
            if open_postings > 0:
                kept = _postings(open_postings, "stays open", "stay open").capitalize()
                return f"{text} {kept} until a second empty read."
            return f"{text} It has no open postings."
        times = "twice" if empty_streak == 2 else f"{empty_streak} times"
        text = f"The {name} board returned no jobs {times} in a row"
        if closed_postings > 0:
            text += f", so {_postings(closed_postings, 'was closed', 'were closed')}."
        else:
            text += ". It had no open postings to close."
        return f"{text} If they moved boards, give the new one. If they stopped hiring, mark ok."
    raise ValueError(f"{kind!r} is not a board item kind")


def reopened(kind: str, previous: Mapping, changed_key: str) -> dict[str, str]:
    """What an item that came back should say: what he did before and what is new. `previous` is the settled row
    (`resolution`, `resolved_at`); `changed_key` is the new evidence key."""
    before = _PREVIOUS.get(previous["resolution"], "it was settled")
    if previous.get("resolved_at"):
        before = f"{before} on {day(previous['resolved_at'])}"
    if kind in ("board_unreadable", "board_empty"):
        what = "failed" if kind == "board_unreadable" else "empty"
        try:
            changed = f"a new run of {what} reads started {day(changed_key)}"
        except ValueError:  # the crawler's key is a time; any other key still says what changed, without the day
            changed = f"a new run of {what} reads started"
    elif kind in _CHANGED:
        changed = _CHANGED[kind]
    else:
        raise ValueError(f"unknown review kind {kind!r}")
    return {"previous": clean(before), "changed": clean(changed)}


def fix_note(field: str, value: str, source: str) -> str:
    """The note on an item he settled by setting a field."""
    if field not in _FIELD_LABELS:
        raise ValueError(f"cannot edit {field!r}")
    shown = _SPONSORSHIP_VALUES.get(value, value) if field == "sponsorship" else value
    text = f"{_FIELD_LABELS[field]} set to {shown} by you"
    if source and source.strip():
        text += f", from {source.strip()}"
    return clean(text)


def no_go_note(reason: str) -> str:
    if reason not in NO_GO_REASONS:
        raise ValueError(f"unknown no-go reason {reason!r}")
    return f"Added to the no-go list by you: {_NO_GO_PHRASES[reason]}"


def ok_note() -> str:
    return "Marked ok by you"


def already_no_go_note() -> str:
    """The note when he marks no-go on a company or person already on the list: no second row is written."""
    return "Marked no-go by you. It was already on the no-go list"


def person_no_go_note(name: str, reason: str) -> str:
    if reason not in NO_GO_REASONS:
        raise ValueError(f"unknown no-go reason {reason!r}")
    return clean(f"Added {name} to the no-go list by you: {_NO_GO_PHRASES[reason]}")


def keep_separate_note() -> str:
    """A merge question he answered "keep separate" (the ok fix)."""
    return "Kept separate by you"


def settled_by_field(field: str) -> str:
    """The note on another open item of the same family that a field he set also settles."""
    return f"Settled by the {field} you set."


NO_GO_SETTLED = "This company is on the no-go list."

TIE_WARNING = "This record collects name-only mentions that could be either company."


def merge_note(loser: str, keeper: str) -> str:
    return clean(f"Merged {loser} into {keeper} by you")


def merged_away_note(keeper: str) -> str:
    """The note on the other open items of the company that a merge took away."""
    return clean(f"Merged into {keeper}.")


def same_company_note(loser: str) -> str:
    """The note on a merge question that a merge answered: its two sides are one company now, or another open
    question asks the same pair."""
    return clean(f"Same company after merging {loser}")


def moved_pair_note(loser: str, keeper: str) -> str:
    """The note on a merge question whose pair moved to the keeper after a merge."""
    return clean(f"Moved to {keeper} after merging {loser}")


MERGE_TAKEN_BACK = "The merge it came from was taken back."

# The company history's line for an item he took back with Undo (`reopened` event).
UNDONE_TEXT = "Undone by you, so it is open again"


def hand_set_text(field: str, value: str, source: str) -> str:
    """The company history's line for a hand fact (`hand_set` event)."""
    if field in _FIELD_LABELS:
        return fix_note(field, value, source)
    text = f"{field.replace('_', ' ').capitalize()} set to {value} by you"
    return clean(f"{text}, from {source.strip()}" if source and source.strip() else text)


def board_replaced_text(new: str, source: str) -> str:
    """The company history's line for a board he replaced (`replaced` event)."""
    text = f"Job board replaced by {new} by you"
    return clean(f"{text}, from {source.strip()}" if source and source.strip() else text)


def shared_domain_note(domain: str) -> str:
    return clean(f"{domain} is shared by several employers, so it no longer joins them")
