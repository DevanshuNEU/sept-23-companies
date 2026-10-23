"""The company page: every field of `CompanyView` in `web/src/api/types.ts`, as the API sends it."""
from typing import Literal

from pydantic import Field

from outreach.api.models.base import Wire

CompanyStatus = Literal["candidate", "backlog", "working", "closed", "removed"]
CloseReason = Literal["referral", "interview", "no_sponsorship", "no_referrals", "already_hired", "not_now", "other"]
RoleKind = Literal["engineer", "manager", "founder", "recruiter", "alum", "other"]
LevelTier = Literal["new_grad", "early_career", "swe1", "swe2", "open", "not_stated", "senior"]
Confidence = Literal["confirmed", "estimate", "guess"]
# Every value `evidence.fact` and `evidence.source_kind` allow (the web's labels cover them all).
FactKind = Literal[
    "size", "stage", "sponsorship", "domain", "hq", "everify", "lca", "cap_exempt", "staffing_risk", "funding", "headcount",
]
SourceKind = Literal[
    "hand", "posting", "old_dashboard", "yc", "builtin", "linkedin", "website", "formd", "lca", "everify", "other",
    "stapply", "commoncrawl", "hn", "simplify",
]
SponsorshipShown = Literal["has_sponsored", "says_yes", "says_no", "opt_only", "unclear", "not_stated"]
TouchState = Literal["open", "replied", "bounced", "closed"]
Actor = Literal["devanshu", "claude", "sender", "crawler", "rules", "daily_list", "import", "system", "source", "reader"]
ImportSource = Literal["data_js", "local_storage", "contacts_json", "radar_db", "devboard", "contacted_txt", "devvault_notes"]
ReadResult = Literal["ok", "empty", "error"]
# The kinds the web's Needs you tables know. The four newer kinds (merge_candidate, reader_disagrees,
# careers_changed, size_check) join with the Needs you view (Task 20), which widens this and the web's tables.
ReviewKind = Literal[
    "board_unreadable", "board_empty", "sponsorship_unclear", "size_unknown", "size_guess", "domain_missing", "flagged",
]
Tone = Literal["ok", "info", "warn", "bad", "idle"]


class CompanyRef(Wire):
    id: int
    name: str
    status: CompanyStatus
    paused_at: str | None
    held_by_touch_id: int | None


class PersonRef(Wire):
    id: int
    name: str
    title: str
    role_kind: RoleKind
    email: str | None
    linkedin_url: str | None
    revision: int


class Fact(Wire):
    id: int
    fact: FactKind
    value: str
    low: int | None
    high: int | None
    confidence: Confidence
    locked: bool
    source_kind: SourceKind
    source_ref: str
    quote: str
    observed_on: str | None


class RoleToName(Wire):
    posting_id: int
    title: str
    url: str
    location: str
    first_seen_at: str
    level_tier: LevelTier
    years_asked: int | None
    start2027: bool


class PostingFlag(Wire):
    """One field where the reader and the phrase rules disagree (or the reader could not say), with both quotes."""

    field: str
    reader_value: str | None
    rules_value: str | None
    reader_quote: str
    rules_quote: str


class PostingRow(Wire):
    id: int
    title: str
    location: str
    url: str
    first_seen_at: str
    last_seen_at: str
    closed_at: str | None
    close_reason: str | None
    level_tier: LevelTier
    sponsorship: Literal["yes", "no", "opt_only", "unclear", "silent"]
    state: Literal["open", "closed", "out_of_lane", "hidden"]
    hidden_rule: str | None
    flags: list[PostingFlag]


class CompanyPerson(Wire):
    person: PersonRef
    channel: Literal["email", "linkedin"]
    status_line: str
    tone: Tone
    last_touch_at: str | None


class CompanyEvent(Wire):
    id: int
    at: str
    kind: str
    text: str
    actor: Actor
    import_source: ImportSource | None


class ThreadMessage(Wire):
    seq: Literal[0, 1, 2, 3] | None
    direction: Literal["out", "in"]
    subject: str
    body: str
    at: str
    from_: str = Field(alias="from")


class CompanyTouch(Wire):
    touch_id: int
    person: PersonRef
    state: TouchState
    summary: str
    messages: list[ThreadMessage]


class CompanyBoard(Wire):
    ats: str
    token: str
    url: str
    last_read: ReadResult | None
    last_read_at: str | None
    error_streak: int
    empty_streak: int


class CompanyClosed(Wire):
    reason: CloseReason
    note: str
    at: str


class CompanyHeader(CompanyRef):
    domain: str | None
    aliases: list[str]
    hq: str
    picked_on: str | None
    day: int | None
    closed: CompanyClosed | None
    revision: int


class ReviewCompany(CompanyRef):
    picked: bool


class ReviewQuote(Wire):
    text: str
    posting_id: int
    posting_title: str
    url: str
    first_seen_at: str


class ReviewRead(Wire):
    at: str
    result: ReadResult
    detail: str


class ReviewReopened(Wire):
    previous: str
    changed: str


class ReviewItem(Wire):
    id: int
    kind: ReviewKind
    company: ReviewCompany
    holds_email: bool
    reason: str
    opened_at: str
    age_days: int
    reopened: ReviewReopened | None
    quote: ReviewQuote | None
    reads: list[ReviewRead]
    last_ok_at: str | None
    open_postings: int | None
    size_facts: list[Fact]
    revision: int


class CompanyView(Wire):
    as_of: str
    company: CompanyHeader
    summary: str
    facts: list[Fact]
    sponsorship_shown: SponsorshipShown
    mail_server_ok: bool | None
    board: CompanyBoard | None
    role: RoleToName | None
    postings: list[PostingRow]
    people: list[CompanyPerson]
    touches: list[CompanyTouch]
    evidence: list[Fact]
    events: list[CompanyEvent]
    review_items: list[ReviewItem]
    ruleset_version: str
