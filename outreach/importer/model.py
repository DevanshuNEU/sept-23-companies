"""The import plan's parts: what the planner decided, before anything is written.

Every part is frozen and carries `records`, the record ids (`<source>:<source_key>`) it came from, so the writer can
say where each row came from and the report can count every record. Keys are plan keys, not store ids:

- a company is its canonical name key (`link.company_key`, after his `same_company` rulings);
- a person is `<company key>/<address, LinkedIn profile or name key>`, or `<company key>/placeholder:<record id>`
  for someone the sources never named;
- a touch is `<company key>|<person key or ->|<channel>|<New York date>`.
"""
from __future__ import annotations

from dataclasses import dataclass

from outreach.rules.facts import EvidenceDraft

__all__ = ["CompanyIn", "PersonIn", "OutcomeIn", "EmailIn", "TouchIn", "ApplicationIn", "BoardIn", "PostingIn",
           "IssueIn", "RecordOutcome", "RECORD_OUTCOMES"]

# What happened to one source record. `conflict` is not one: a record that disagrees with another is imported or
# merged and raises an `outcome_conflict` or `person_detail_conflict` issue, so it is counted once and shown.
RECORD_OUTCOMES = ("imported", "merged", "skipped", "held")


@dataclass(frozen=True)
class CompanyIn:
    key: str
    name: str
    names: frozenset[str]
    domain: str | None
    boards: tuple[tuple[str, str], ...]
    evidence: tuple[EvidenceDraft, ...]
    hand: tuple[EvidenceDraft, ...]  # source_kind 'hand'; the writer writes them locked
    no_go: tuple[tuple[str, str], ...]  # (reason, quote)
    close: tuple[str, str] | None  # (reason, note)
    notes: tuple[tuple[str, str], ...]  # (record id, text); the import never removes a company
    records: tuple[str, ...]


@dataclass(frozen=True)
class PersonIn:
    key: str
    company_key: str
    name: str
    placeholder: bool
    title: str
    role_kind: str
    email: str | None
    linkedin_url: str | None
    profile_text: str
    tz: str
    records: tuple[str, ...]
    # (no_go reason, record id): a person who said no or asked to stop in any source, or a namesake of one
    # (`link.carry_stops`). The writer adds a person no_go with source 'import'.
    no_go: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class OutcomeIn:
    kind: str  # vocab.OUTCOME_KINDS
    at: str  # canonical UTC, always set: a reply without its own date has the touch's date and estimated True
    reply_kind: str | None
    estimated: bool
    record: str


@dataclass(frozen=True)
class EmailIn:
    """An email touch's own state, as the `touch` row and its messages need it.

    For a contacts.json contact it carries the old mailer's messages: `sent0_at` None means nothing was sent (a
    draft, skipped or failed contact; the writer logs no outreach_touch for it). A historic email (contacted.txt, a
    company file, the tracker) has `contact_id` None and no message text: only `sent0_at`, the state (closed
    `no_reply` unless it has a reply or a bounce) and the bounce and auto-reply times. `old_approval` says he had
    approved it in the old mailer: the writer notes it in an event and the message stays a draft."""
    contact_id: str | None
    subject0: str | None
    body0: str | None
    rfc0: str | None
    thread_id: str | None
    gmail_id0: str | None
    sent0_at: str | None
    subject1: str | None
    body1: str | None
    rfc1: str | None
    sent1_at: str | None
    due1_on: str | None
    touch_state: str | None
    close_reason: str | None
    bounced_at: str | None
    auto_reply_at: str | None
    history: tuple[tuple[str, str], ...]
    old_approval: bool = False


@dataclass(frozen=True)
class TouchIn:
    key: str
    company_key: str
    person_key: str | None
    channel: str
    sent_at: str
    posting_key: str | None
    role_named: bool
    role_named_known: bool
    outcomes: tuple[OutcomeIn, ...]
    email: EmailIn | None
    followups_sent: int
    records: tuple[str, ...]


@dataclass(frozen=True)
class ApplicationIn:
    key: str
    company_key: str
    posting_key: str | None
    applied_on: str
    url: str
    outcome: str
    outcome_at: str | None
    records: tuple[str, ...]
    # replies, referrals, interviews and offers on the application's own touch (radar events, a placed interview)
    outcomes: tuple[OutcomeIn, ...] = ()


@dataclass(frozen=True)
class BoardIn:
    ats: str
    token: str
    company_key: str
    active: bool
    records: tuple[str, ...]


@dataclass(frozen=True)
class PostingIn:
    source_key: str
    company_key: str
    board: tuple[str, str] | None
    title: str
    location: str
    url: str
    posted_on: str | None
    first_seen_at: str | None
    last_seen_at: str | None
    closed: bool
    body: str | None
    records: tuple[str, ...]


@dataclass(frozen=True)
class IssueIn:
    """One line for his review. `holds` means the real run refuses until it is settled."""
    code: str
    company_key: str | None
    record: str
    detail: str
    holds: bool
    key: str | None = None  # the review item's evidence key when it is not `import:<code>:<record>` (ruled_out)


@dataclass(frozen=True)
class RecordOutcome:
    outcome: str  # RECORD_OUTCOMES
    reason: str
    entity: str | None
    entity_key: str | None

    def __post_init__(self):
        if self.outcome not in RECORD_OUTCOMES:
            raise ValueError(f"unknown record outcome {self.outcome!r}")
        if self.outcome != "imported" and not self.reason:
            raise ValueError(f"a {self.outcome} record needs a reason")
        if (self.outcome in ("skipped", "held")) != (self.entity is None):
            raise ValueError(f"a {self.outcome} record {'has no' if self.entity else 'needs an'} entity")
