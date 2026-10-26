"""Every reason and note sentence a review item uses (#16).

Pure functions, no store. They are UI copy: plain words, short sentences, never an en or em dash (`clean` removes
them from any text that came from outside). The UI words are Devanshu's own: "ok", "no-go list", "mark ok".
"""
from __future__ import annotations

import ast
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

from outreach.review.kinds import NO_GO_REASONS

__all__ = ["clean", "day", "sponsorship_reason", "size_reason", "domain_reason", "board_reason", "reopened",
           "fix_note", "no_go_note", "ok_note", "already_no_go_note", "person_no_go_note", "keep_separate_note",
           "settled_by_field", "NO_GO_SETTLED", "TIE_WARNING", "merge_note", "merged_away_note", "same_company_note",
           "shared_domain_note", "moved_pair_note", "MERGE_TAKEN_BACK", "UNDONE_TEXT", "hand_set_text",
           "board_replaced_text", "merge_reason", "reader_reason", "shared_domain_size_reason"]

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


# ---- plain sentences for stored reasons that are not plain words (Needs you shows these; the store keeps its text) --

_MAYBE = "so they may be one company."
_UNSURE = "so the sources could not tell whether they are one company."
# How one source record matched each company, for a strong pair basis `<a>+<b>` (`outreach.identity.BASES`).
_MATCHED_BY = {"source_ref": "source id", "fein": "tax ID number", "domain": "domain", "name_state": "name and state",
               "ambiguous_repeat": "name"}
_NAME_ALIASES = ("name", "hand", "import", "crawl", "merge", "dba", "legal_name", "formd")
_ALIAS_SAID = {  # (with the value, without it)
    "fein": ("Both have the tax ID number {v}", "Both have the same tax ID number"),
    "ats": ("Both claim the job board {v}", "Both claim the same job board"),
    "linkedin": ("Both have the LinkedIn page {v}", "Both have the same LinkedIn page"),
    **{source: ('Both go by the name "{v}"', "Both go by the same name") for source in _NAME_ALIASES},
}


_PIECE_MAX = 160
_UNSAFE = str.maketrans("", "", "<>")  # `&` stays: the web shows these as escaped text


def _piece(value) -> str:
    """Outside text (a name, a domain, a board, an alias value, a quote) made fit to sit inside a sentence: as text,
    without `<` and `>` (`&` stays, as in "AT&T"), every run of whitespace (newlines too) one space, at most 160
    characters (a longer one is cut and ends in three periods), and no en or em dash. None is empty text."""
    text = " ".join(("" if value is None else str(value)).translate(_UNSAFE).split())
    if len(text) > _PIECE_MAX:
        text = text[:_PIECE_MAX - 3].rstrip() + "..."
    return clean(text)


def _found(pattern: str, text: str) -> re.Match | None:
    return re.search(pattern, text)


def _domain_in(detail: str) -> str | None:
    m = _found(r"the domain ([^\s,]+)", detail)
    d = _piece(m[1].rstrip(".")) if m else ""
    return d or None


def _alias_value(detail: str) -> str | None:
    """The alias value `identity` wrote with `!r` ("has the fein '12-3456789' (key ...)"), or None."""
    m = _found(r" has the \S+ ('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\") \(key ", detail)
    if m is None:
        return None
    try:
        value = ast.literal_eval(m[1])
    except (ValueError, SyntaxError):
        return None
    return (_piece(value) or None) if isinstance(value, str) else None


def merge_reason(basis: str, detail: str) -> str | None:
    """The plain sentence for a `merge_candidate` reason, from its basis and detail (`identity.raise_merge_candidate`
    writes "Basis <basis>, confidence <c>: <detail>"). A known basis whose detail does not read still gets a sentence
    without the value; an unknown basis is None (the caller shows the stored text)."""
    detail = detail or ""
    if basis == "domain":
        d = _domain_in(detail)
        return f"Both use the domain {d}, {_MAYBE}" if d else f"Both use the same domain, {_MAYBE}"
    if basis.startswith("fein_conflict+"):
        what = basis.partition("+")[2]
        if what == "domain":
            d = _domain_in(detail)
            lead = f"Both use the domain {d}" if d else "Both use the same domain"
        elif what == "name_state":
            m = _found(r"the same name and state \(([^)]+)\)", detail)
            lead = f'Both names come down to "{_piece(m[1])}" in the same state' if m \
                else "Both have the same name and state"
        else:
            return None
        return f"{lead}, but their tax ID numbers differ, {_UNSURE}"
    if basis == "name":
        m = _found(r"the same name \(([^)]+)\), but the state is (.+) here and (.+) there$", detail)
        if m:
            return (f'Both names come down to "{_piece(m[1])}", but their states differ ({_piece(m[2])} and '
                    f"{_piece(m[3])}), {_UNSURE}")
        m = _found(r"the same name key \(([^)]+)\), from a name with letters outside ASCII", detail)
        if m:
            return (f'Both names come down to "{_piece(m[1])}", but one is written with letters a name match '
                    f"cannot check, {_UNSURE}")
        return f"Their names are alike, but nothing else matched, {_UNSURE}"
    if basis == "ambiguous_name_state":
        m = _found(r"the same name \(([^)]+)\) and state \(([^)]+)\) as (\d+) companies", detail)
        if m:
            return (f'Both names come down to "{_piece(m[1])}" in {_piece(m[2])}, and {_piece(m[3])} companies '
                    f"match that, {_UNSURE}")
        return f"Their name and state match other companies too, {_UNSURE}"
    if basis == "same_board":
        m = _found(r"both claim the (\S+) board (.+)$", detail)
        if m:
            return f"Both claim the same {_ats_name(_piece(m[1]))} job board, {_piece(m[2])}, {_MAYBE}"
        return f"Both claim the same job board, {_MAYBE}"
    if basis == "source_ref":
        return f"One source record points at both companies, {_MAYBE}"
    if basis == "import_near_name":
        return f"The old lists spell the two names almost the same, {_MAYBE}"
    if basis in ("insights_domain", "insights_email_domain"):
        m = _found(r"(?:the domain|people at) ([^\s,]+)", detail)
        d = _piece(m[1].rstrip(".")) if m else ""
        what = "use the domain" if basis == "insights_domain" else "have people with email at"
        return f"Both {what} {d}, {_MAYBE}" if d else f"Both share an email domain, {_MAYBE}"
    if basis == "insights_name_key":
        return f"The two names come down to the same words, {_MAYBE}"
    if basis == "insights_name_prefix":
        m = _found(r"^(\S+) is the start of (\S+)$", detail)
        if m:
            return f'One name key, "{_piece(m[1])}", is the start of the other, "{_piece(m[2])}", {_MAYBE}'
        return f"One name is the start of the other, {_MAYBE}"
    if basis == "insights_initials":
        return f"One name is the initials of the other, {_MAYBE}"
    if basis.startswith("alias:"):
        said = _ALIAS_SAID.get(basis[len("alias:"):])
        if said is None:
            return None
        value = _alias_value(detail)
        return f"{said[0].format(v=value)}, {_MAYBE}" if value else f"{said[1]}, {_MAYBE}"
    first, plus, second = basis.partition("+")
    if plus and first in _MATCHED_BY and second in _MATCHED_BY:
        if first == second:
            return f"One source record matched both companies by their {_MATCHED_BY[first]}, {_MAYBE}"
        return (f"One source record matched one company by its {_MATCHED_BY[first]} and the other by its "
                f"{_MATCHED_BY[second]}, {_MAYBE}")
    return None


_RULES_SAID = {
    "sponsorship": {
        "silent": "The rules found nothing about sponsorship in this posting.",
        "unclear": "The rules found sponsorship wording in this posting but could not tell yes or no.",
        "no": "The rules read this posting as no sponsorship.",
        "yes": "The rules read this posting as offering sponsorship.",
        "opt_only": "The rules read this posting as sponsoring OPT only.",
    },
    "student_or_grad_window": {
        "excluded": "The rules read this posting as open only to current students or a set graduation window.",
        "ok": "The rules found no student or graduation limit in this posting.",
    },
    "location": {
        "no": "The rules read this job as outside the US.",
        "yes": "The rules read this job as in the US.",
        "unknown": "The rules could not tell where this job is.",
    },
}
_READER_HEARD = {
    "sponsorship": {"wont_sponsor": "reads it as no sponsorship", "will_sponsor": "reads it as offering sponsorship",
                    "opt_only": "reads it as sponsoring OPT only", "silent": "found nothing about sponsorship"},
    "citizenship_or_clearance": {"yes": "reads it as needing US citizenship or a clearance",
                                 "no": "reads it as needing neither"},
    "student_or_grad_window": {"yes": "reads it as open only to current students or a set graduation window",
                               "no": "reads it as open to graduates"},
    "engineering": {"yes": "reads it as an engineering role", "no": "reads it as not an engineering role"},
}
READER_CLOSE_READER = "Mark no-go if the reader is right; mark ok to keep the rules' reading."
READER_CLOSE_RULES = "Mark no-go if the rules are right; mark ok to go with the reader's reading."
READER_CLOSE_EITHER = "Mark no-go if this posting rules the company out; mark ok if it does not."


def _rules_said(field: str, rules) -> str | None:
    if field == "citizenship_or_clearance":
        if rules is True:
            return "The rules read this posting as needing US citizenship or a clearance."
        if rules is False:
            return "The rules found no citizenship or clearance need in this posting."
        return None
    if field == "engineering":
        if not isinstance(rules, str) or not rules:
            return None
        if rules == "engineering":
            return "The rules read this as an engineering role."
        if rules == "intern":
            return "The rules read this as an engineering internship."
        if rules in ("off_lane", "not_engineering"):
            return "The rules read this as not an engineering role."
        if rules == "general_application":
            return "The rules read this as a general application, not a named role."
        return None
    return _RULES_SAID.get(field, {}).get(rules) if isinstance(rules, str) else None


def _reader_heard(field: str, value) -> str | None:
    if field == "location" and value is not None:
        place = _piece(value)
        return f"reads the place as {place}" if place else "found no place in it"
    if value == "unknown" and field in _READER_HEARD:
        return "could not tell"
    return _READER_HEARD.get(field, {}).get(value) if isinstance(value, str) else None


def _rules_hide(field: str, rules) -> bool:
    return ((field == "sponsorship" and rules == "no") or (field == "citizenship_or_clearance" and rules is True)
            or (field == "student_or_grad_window" and rules == "excluded") or (field == "location" and rules == "no"))


def _reader_hides(field: str, rules, value) -> bool:
    if value is None:
        return False
    if field == "location":  # a raised place with a value: the reader hides it exactly when the rules do not
        return rules != "no" and bool(str(value).strip())
    return ((field == "sponsorship" and value == "wont_sponsor")
            or (field in ("citizenship_or_clearance", "student_or_grad_window") and value == "yes"))


def reader_reason(parts: Sequence[tuple[str, object, object, str]]) -> str | None:
    """The plain sentences for a `reader_disagrees` reason. Each part is (field, the rules' value, the reader's value
    or None when the reader gave no answer, the reader's quote). One sentence for the rules, one for the reader (with
    its line when it quoted one), then one line on what ok and no-go mean here. None when any part is a field or a
    value these words do not know (the caller shows the stored text)."""
    if not parts:
        return None
    sentences: list[str] = []
    rules_hide: list[bool] = []
    reader_hide: list[bool] = []
    for field, rules, value, quote in parts:
        said = _rules_said(field, rules)
        if said is None:
            return None
        sentences.append(said)
        quote = _piece(quote)
        if value is None:
            sentences.append("The night reader gave no answer on this.")
        else:
            heard = _reader_heard(field, value)
            if heard is None:
                return None
            if quote and not (field == "location" and quote == _piece(value)):
                sentences.append(f'The night reader found this line and {heard}: "{quote}"')
            else:
                sentences.append(f"The night reader {heard}.")
        rules_hide.append(_rules_hide(field, rules))
        reader_hide.append(_reader_hides(field, rules, value))
    if all(reader_hide) and not any(rules_hide):
        close = READER_CLOSE_READER
    elif all(rules_hide) and not any(reader_hide):
        close = READER_CLOSE_RULES
    else:
        close = READER_CLOSE_EITHER
    return clean(" ".join([*sentences, close]))


def shared_domain_size_reason(company: str, domain: str, others: int | None) -> str:
    """The plain sentence for a `size_check` raised because the company's domain is shared (`identity.resolve_store`).
    `others` is how many other employers the visa filings show on it, None when that count is not known."""
    company, domain = _piece(company) or "This company", _piece(domain)
    if isinstance(others, int) and not isinstance(others, bool) and others > 0:
        where = f"with {others} other {'employer' if others == 1 else 'employers'} in the visa filings"
    else:
        where = "with other employers"
    its = f"its domain, {domain}," if domain else "its domain"
    return f"{company} shares {its} {where}, so it may be part of a larger company. Nobody has recorded its size."
