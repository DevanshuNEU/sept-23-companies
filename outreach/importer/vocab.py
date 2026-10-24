"""One table for every old value: roles, mailer statuses, reply words, curated outcomes, tracker stages, channels,
radar events, bands, frozen boards and the time of a date-only value.

Every mapping is pure and returns None for a value it does not know, so the planner raises `role_unmapped` or
`reply_text` (or holds the record) instead of guessing. Values are compared lower case with runs of spaces made one.

Outcome kinds (`OutcomeKind`) are the store's outcomes (`accepted`, `replied`, `referred`, `interview`, `offer`)
plus the two an email touch carries on its own (`auto_reply`, `bounced`). The old tracker's stages also say a touch
happened (`touch`), an application was made (`application`) or the company was closed (`close`).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from outreach.store import iso_utc

__all__ = [
    "OUTCOME_KINDS", "TRACKER_KINDS", "REPLY_KINDS", "STOP_KINDS", "OutcomeKind", "role_kind", "ContactPlan",
    "contact_status_plan", "note_reply", "curated_outcome", "tracker_stage", "TRACKER_REPLY_KIND", "tracker_channel",
    "curated_channel", "RadarEffect", "radar_event", "band_stage", "FROZEN_ATS", "MAYBE_FROZEN_ATS", "board_frozen",
    "req_named", "followups_count", "NEW_YORK", "NOON_NY", "noon_ny", "ny_date", "to_utc",
]

OutcomeKind = str
OUTCOME_KINDS = frozenset({"accepted", "replied", "referred", "interview", "offer", "auto_reply", "bounced"})
TRACKER_KINDS = frozenset({"touch", "application", "close"})
REPLY_KINDS = ("unsorted", "interested", "not_now", "said_no", "asked_to_stop")
# The reply kinds that stop a person for good (a person no-go after import), with the no_go reason each becomes.
STOP_KINDS = {"said_no": "said_no", "asked_to_stop": "stop_reply"}


def _norm(text) -> str:
    return " ".join(str(text).lower().split()) if text is not None else ""


# ---- roles -------------------------------------------------------------------------------------------------------

_ROLES = {
    "founder": "founder", "recruiter": "recruiter", "hiring-manager": "manager", "hiring manager": "manager",
    "engineer": "engineer", "alum": "alum", "invalid": "other",
    # contacts.json `kind` values are already the store's
    "manager": "manager", "other": "other",
}


def role_kind(text) -> str | None:
    """The store's role kind for a company file `role` or a contacts.json `kind`; None for anything else."""
    return _ROLES.get(_norm(text))


# ---- the old mailer's contact statuses ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ContactPlan:
    """What a contacts.json status becomes. `message0` and `message1` are `draft`, `sent`, `skipped` or `none`;
    `touch_state` is the email touch's state with `close_reason` when closed. `held` is the hold code when the old
    mailer never settled the send (then nothing else is set). `old_approval` notes that he had approved it in the
    old mailer: an event says so, and the message is a draft he approves again (no plan can say approved)."""
    message0: str | None
    message1: str | None
    touch_state: str | None
    close_reason: str | None
    held: str | None
    old_approval: bool


_STATUS = {
    "drafted": ("draft", "none", "open", None, False),
    "approved": ("draft", "none", "open", None, True),
    "failed": ("draft", "none", "open", None, False),
    "skipped": ("skipped", "none", "closed", "skipped", False),
    "sent": ("sent", "none", "open", None, False),
    "replied": ("sent", "followup", "replied", None, False),
    "bounced": ("sent", "none", "bounced", None, False),
    "followup_ready": ("sent", "draft", "open", None, False),
    "followup_approved": ("sent", "draft", "open", None, True),
    "followup_sent": ("sent", "sent", "open", None, False),
    "closed": ("sent", "followup", "closed", "no_reply", False),
}
_UNCLEAR = frozenset({"sending", "send_unknown"})


def contact_status_plan(status, *, followup_sent: bool = False) -> ContactPlan | None:
    """The plan for one contacts.json status. `followup_sent` is whether the contact has `followupSentAt`: a
    replied or closed contact then has a sent follow-up. None for a status the old mailer never wrote."""
    key = _norm(status)
    if key in _UNCLEAR:
        return ContactPlan(None, None, None, None, "unclear_send", False)
    row = _STATUS.get(key)
    if row is None:
        return None
    message0, message1, state, reason, approval = row
    if message1 == "followup":
        message1 = "sent" if followup_sent else "none"
    return ContactPlan(message0, message1, state, reason, None, approval)


# ---- reply words -------------------------------------------------------------------------------------------------

Reply = tuple[list[OutcomeKind], str | None]

_NOTE_REPLY: dict[str, Reply] = {
    "": ([], None), "null": ([], None), "n/a": ([], None),
    "replied": (["replied"], "unsorted"),
    "polite-no": (["replied"], "said_no"),
    "soft-positive": (["replied"], "interested"),
    "scheduled-call": (["replied", "interview"], "interested"),
    "take-home": (["replied", "interview"], "interested"),
    "bounced": (["bounced"], None),
}


def _copy(reply: Reply) -> Reply:
    return list(reply[0]), reply[1]


def note_reply(text) -> Reply | None:
    """(outcome kinds, reply kind) for a company file `reply` word. Any word starting `linkedin-refer` is an
    interested reply plus a referral (Pending Devanshu Q14). None for free text: the planner keeps it as an unsorted
    reply and an item quoting it."""
    key = _norm(text)
    if key.startswith("linkedin-refer"):
        return ["replied", "referred"], "interested"
    found = _NOTE_REPLY.get(key)
    return _copy(found) if found is not None else None


_CURATED: dict[str, Reply] = {
    "": ([], None), "none": ([], None),
    "ooo": (["auto_reply"], None),
    "bounce": (["bounced"], None),
    "no": (["replied"], "said_no"),
    "soft": (["replied"], "unsorted"),
    "routed": (["replied", "referred"], "interested"),
    "call": (["replied", "interview"], "interested"),
}


def curated_outcome(text) -> Reply | None:
    """(outcome kinds, reply kind) for a curated row's `outcome`; an empty cell is no outcome. None for any other
    word."""
    found = _CURATED.get(_norm(text))
    return _copy(found) if found is not None else None


# ---- the old tracker ---------------------------------------------------------------------------------------------

TRACKER_REPLY_KIND = "unsorted"  # the tracker's `Replied` says nothing about what the reply said
_STAGES = {
    "not started": [], "researching": [],
    "referral asked": ["touch"], "applied": ["application"], "replied": ["replied"],
    "interviewing": ["interview"], "offer": ["offer"], "closed": ["close"],
}


def tracker_stage(stage) -> list[OutcomeKind] | None:
    """What an old tracker stage says happened. `close` closes the company (reason `other`, note from `notes`)."""
    found = _STAGES.get(_norm(stage))
    return list(found) if found is not None else None


_TRACKER_CHANNELS = {"linkedin": "linkedin_request", "email": "email", "": "other"}
_CURATED_CHANNELS = {"email": "email", "linkedin": "linkedin_request", "imessage": "other"}


def tracker_channel(text) -> str | None:
    return _TRACKER_CHANNELS.get(_norm(text))


def curated_channel(text) -> str | None:
    return _CURATED_CHANNELS.get(_norm(text))


# ---- Job Radar -------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class RadarEffect:
    """What one radar `app_event` does: it belongs to an application (`application`), may move that application's
    outcome (`app_outcome`), adds outcomes on its touch (`outcomes`, with `reply_kind` for a reply), or is skipped
    with an explained reason (`skip`)."""
    application: bool
    app_outcome: str | None
    outcomes: tuple[OutcomeKind, ...]
    reply_kind: str | None
    skip: str | None


_RADAR = {
    "applied": RadarEffect(True, None, (), None, None),
    "reply": RadarEffect(True, None, ("replied",), "unsorted", None),
    "screen": RadarEffect(True, "screen", ("replied",), "interested", None),
    "onsite": RadarEffect(True, "interview", ("interview",), None, None),
    "offer": RadarEffect(True, "offer", ("offer",), None, None),
    "rejected": RadarEffect(True, "rejected", (), None, None),
    "withdrawn": RadarEffect(True, "closed", (), None, None),
    "opened": RadarEffect(False, None, (), None, "opened_only"),
    "ignored": RadarEffect(False, None, (), None, "triage_ignore"),
    "unignored": RadarEffect(False, None, (), None, "triage_ignore"),
}


def radar_event(kind) -> RadarEffect | None:
    return _RADAR.get(_norm(kind))


_BANDS = {"seed": "seed", "a": "series_a", "b": "series_b"}


def band_stage(band) -> str | None:
    """The stage value for a curated `band`; `growth`, `large` and `agency` have no matching stage, so None."""
    return _BANDS.get(_norm(band))


# Boards #21 never reads again (Pending Devanshu Q18): always for these, and for Workday and Oracle only when the
# company has size evidence over 300, so a small company on Workday keeps being read.
FROZEN_ATS = frozenset({"amazon", "apple", "google", "eightfold"})
MAYBE_FROZEN_ATS = frozenset({"workday", "oracle_orc"})


def board_frozen(ats: str, size_low: int | None) -> bool:
    return ats in FROZEN_ATS or (ats in MAYBE_FROZEN_ATS and size_low is not None and size_low > 300)


# ---- curated columns -------------------------------------------------------------------------------------------

_NO = frozenset({"n", "no", "false", "0", "none"})
_YES = frozenset({"y", "yes", "true"})


def req_named(text) -> bool | None:
    """Whether a curated row's touch named a live role: empty is unknown (None); `n`, `no`, `false`, `0`, `none`
    are False; anything else (`y`, a req id) is True."""
    key = _norm(text)
    if not key:
        return None
    return key not in _NO


def followups_count(text) -> int | None:
    """Follow-ups sent, from a curated row's `fu`: empty or no is 0, yes is 1, a number is itself; None otherwise."""
    key = _norm(text)
    if not key or key in _NO:
        return 0
    if key in _YES:
        return 1
    return int(key) if key.isdigit() else None


# ---- time --------------------------------------------------------------------------------------------------------

NEW_YORK = ZoneInfo("America/New_York")
NOON_NY = time(12, 0)  # a date-only value is this time in New York, so its New York day never moves


def noon_ny(day: str) -> str:
    """`YYYY-MM-DD` at 12:00 America/New_York, as canonical UTC. ValueError for a bad date."""
    local = datetime.combine(date.fromisoformat(day), NOON_NY, tzinfo=NEW_YORK)
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_utc(value) -> str | None:
    """Canonical UTC for a source time: a bare date is noon in New York, anything else goes through `iso_utc`
    (naive is UTC). None for an empty value; ValueError for one that does not parse."""
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    if len(text) == 10:
        return noon_ny(text)
    return iso_utc(text)


def ny_date(value: str) -> str:
    """The New York day of a timestamp (or a bare date, which is its own day)."""
    text = str(value).strip()
    if len(text) == 10:
        return date.fromisoformat(text).isoformat()
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(NEW_YORK).date().isoformat()
