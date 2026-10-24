"""The plan builder: every source record linked into one `ImportPlan`, with exactly one outcome per record.

`build_plan` is pure. It reads no store and no clock: the import time (`now`) and #14's send config are passed in,
so the same records give the same plan. The order of work:

1. Each source's records become companies, people, touches, applications, boards and postings (one method per
   source, in `SOURCES` order). data.js companies are the base the other names link to.
2. `PersonIndex.finalize()` folds name-only mentions into the person they name; every person key in a touch goes
   through `resolve()`.
3. One email touch per person (the store's `touch.person_id` is unique): the touch with a sent contacts.json email
   is kept, else the earliest historic one; the rest merge into it (`same_touch`), a later day counting as a
   follow-up sent. A contacts.json draft for someone a historic record says was already emailed is not imported
   as a draft (`draft_after_send`), so nobody gets a second first email.
4. Curated rows annotate the touches (`join_curated`); then interviews (interview prep, the old tracker's stage
   without a date, a data.js `seed`) are placed on their origin touch (`origin_touch`), dated ones first.
5. Historic email touches are closed `no_reply` unless they have a reply or a bounce; a contacts.json `sent`
   touch still open gets its planned follow-up when it is due on or after the import day.
6. Stops (`carry_stops`) fill `PersonIn.no_go`; the done set closes its companies.

A record the planner cannot place is `held` with an issue that `holds`; `held()` lists them and the real run
refuses while any is left. Some holds (`SETTLEABLE`) can be settled with his `[skip_record]` override; an unclear
send or a sent line with no contact never can, because skipping it could lose a sent email.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date

from outreach.importer.link import (CompanyIndex, PersonIndex, carry_stops, interview_date, interview_touch,
                                    is_placeholder, join_curated, merge_outcome, origin_touch, parse_person_name,
                                    touch_key)
from outreach.importer.manifest import Manifest, Overrides
from outreach.importer.model import (ApplicationIn, BoardIn, CompanyIn, EmailIn, IssueIn, OutcomeIn, PersonIn,
                                     PostingIn, RecordOutcome, TouchIn)
from outreach.importer.read_data_js import read_data_js
from outreach.importer.read_notes import read_devvault_notes, read_interview_prep
from outreach.importer.read_radar import radar_body, read_radar
from outreach.importer.read_text import (read_contacted_txt, read_contacts_json, read_curated_touches, read_devboard,
                                         read_local_storage, read_send_log)
from outreach.importer.records import SOURCES, SourceError, SourceRecord
from outreach.importer.vocab import (band_stage, board_frozen, contact_status_plan, note_reply, noon_ny, ny_date,
                                     radar_event, role_kind, to_utc, tracker_channel, tracker_stage)
from outreach.rules.facts import EvidenceDraft, from_old_dashboard, old_dashboard_signals, parse_stage
from outreach.sending.calendar import add_business_days
from outreach.sending.config import SendConfig
from outreach.sources.boards import normalize_token
from outreach.sources.keys import email_key

__all__ = ["ImportPlan", "read_all", "build_plan", "READERS", "SETTLEABLE", "DONE_CLOSE", "DEFAULT_TZ"]

READERS = {
    "data_js": read_data_js, "local_storage": read_local_storage, "contacts_json": read_contacts_json,
    "send_log": read_send_log, "radar_db": read_radar, "devboard": read_devboard, "contacted_txt": read_contacted_txt,
    "devvault_notes": read_devvault_notes, "interview_prep": read_interview_prep,
    "curated_touches": read_curated_touches,
}

# Holds his [skip_record] may settle: records that carry no send. An unclear send, a send line with no contact and
# a contact status the old mailer never wrote are fixed at the source and exported again, never skipped.
SETTLEABLE = frozenset({"unparsed_record", "company_unknown"})
DONE_CLOSE = ("interview", "Done: interviewed (imported)")
DEFAULT_TZ = "America/New_York"
_HOST = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
_DATE_IN_TEXT = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_APP_RANK = {"open": 0, "screen": 1, "interview": 2, "offer": 3}
_MERGE_REASON = {"outreach_touch": "same_touch", "company": "same_company", "person": "same_person",
                 "application": "same_application", "posting": "same_posting", "board": "same_company"}


@dataclass(frozen=True)
class ImportPlan:
    companies: dict[str, CompanyIn]
    people: dict[str, PersonIn]
    touches: dict[str, TouchIn]
    applications: dict[str, ApplicationIn]
    boards: dict[tuple[str, str], BoardIn]
    postings: dict[str, PostingIn]
    issues: tuple[IssueIn, ...]
    records: dict[str, tuple[SourceRecord, RecordOutcome]]
    done_set: tuple[str, ...]
    near_names: tuple[tuple[str, str], ...] = ()  # company key pairs, for raise_merge_candidate
    followups_not_planned: int = 0  # contacts.json follow-ups due before the import day (report line)

    def held(self) -> tuple[str, ...]:
        """The record ids the real run refuses on, in record order."""
        return tuple(rid for rid, (_, out) in self.records.items() if out.outcome == "held")


def read_all(manifest: Manifest) -> list[SourceRecord]:
    """Every record of every source, in `SOURCES` order. One `SourceError` (a missing or unreadable source) stops
    it: a source that cannot be read never counts as zero records."""
    out: list[SourceRecord] = []
    for source in SOURCES:
        path = manifest.paths.get(source)
        if path is None:
            raise SourceError(f"{source}: the manifest names no path")
        out.extend(READERS[source](path))
    return out


def build_plan(records: Sequence[SourceRecord], overrides: Overrides, *, now: str, send_cfg: SendConfig) -> ImportPlan:
    return _Planner(records, overrides, now=now, cfg=send_cfg).build()


# ---- small helpers -----------------------------------------------------------------------------------------------

def _rid(record: SourceRecord) -> str:
    return f"{record.source}:{record.source_key}"


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _day(value) -> str | None:
    text = _text(value)[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _utc(value) -> str | None:
    try:
        return to_utc(value)
    except ValueError:
        return None


def _bare_host(value) -> str | None:
    text = _text(value).lower().rstrip(".")
    return text if _HOST.match(text) else None


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _note_role(note) -> str | None:
    text = _text(note)
    return role_kind(text) or role_kind(text.split(",")[0]) if text else None


@dataclass
class _Co:
    domain: str | None = None
    boards: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    hand: list = field(default_factory=list)
    no_go: list = field(default_factory=list)
    close: tuple | None = None
    notes: list = field(default_factory=list)


@dataclass
class _Later:
    """An outcome on a company whose touch is decided after linking (an interview, a seed, a dateless stage)."""
    company: str
    kind: str
    reply_kind: str | None
    day: str | None
    estimated: bool
    record: str
    explicit: bool  # the record's outcome is the touch it lands on (interview prep)


class _Planner:
    def __init__(self, records: Sequence[SourceRecord], overrides: Overrides, *, now: str, cfg: SendConfig):
        self.now = to_utc(now)
        self.today = ny_date(self.now)
        self.cfg = cfg
        self.ov = overrides
        self.input: dict[str, SourceRecord] = {}
        for record in records:
            rid = _rid(record)
            if rid in self.input:
                raise ValueError(f"two records have the id {rid}")
            self.input[rid] = record
        self.companies = CompanyIndex(overrides)
        self.people = PersonIndex(overrides)
        self.co: dict[str, _Co] = {}
        self.details: dict[str, dict] = {}  # person mention key to title, role, profile, tz
        self.raw_touches: list[TouchIn] = []
        self.apps: dict[str, ApplicationIn] = {}
        self.boards: dict[tuple[str, str], BoardIn] = {}
        self.postings: dict[str, PostingIn] = {}
        self.issues: list[IssueIn] = []
        self.later: list[_Later] = []
        self.outcomes: dict[str, RecordOutcome] = {}
        self.claims: dict[str, list[tuple[str, str]]] = {}
        self.held_code: dict[str, str] = {}
        self.base_ids: dict[str, str] = {}
        self.contact_sends: dict[str, list[tuple[str, str, bool]]] = {}  # email to (touch key, NY day, follow-up)
        self.held_emails: dict[str, str] = {}
        self.plan_followup: set[str] = set()  # contact ids whose open sent touch may get a planned follow-up
        self.radar_boards: dict[object, tuple[tuple[str, str] | None, str]] = {}
        self.radar_postings: dict[object, str] = {}
        self.radar_merged: dict[str, object] = {}
        self.alias: dict[str, str] = {}  # touch key before consolidation to the touch it ended in
        self.followups_not_planned = 0

    # -- bookkeeping --

    def issue(self, code, company, record, detail, holds=False, key=None):
        self.issues.append(IssueIn(code, company, record, detail, holds, key))

    def skip(self, rid, reason):
        self.outcomes[rid] = RecordOutcome("skipped", reason, None, None)

    def hold(self, rid, code, detail, company=None):
        self.outcomes[rid] = RecordOutcome("held", code, None, None)
        self.held_code[rid] = code
        self.issue(code, company, rid, detail, True)

    def claim(self, rid, entity, key):
        self.claims.setdefault(rid, []).append((entity, key))

    def c(self, key) -> _Co:
        return self.co.setdefault(key, _Co())

    def set_domain(self, key, domain, rid):
        c = self.c(key)
        if c.domain is None:
            c.domain = domain
        elif c.domain != domain:
            self.issue("domain_conflict", key, rid, f"{rid} gives the domain {domain}; {c.domain} is kept.")

    def evidence(self, key, draft: EvidenceDraft, rid):
        draft = replace(draft, source_ref=f"import:{rid}")
        c = self.c(key)
        if draft not in c.evidence:
            c.evidence.append(draft)

    def add_board(self, ats, token, company, rid) -> tuple[str, str] | None:
        ats = _text(ats).lower()
        try:
            ref = (ats, normalize_token(ats, _text(token)))
        except ValueError:
            self.issue("board_unparsed", company, rid, f"{rid} names the board {ats}:{token}, which is not one the "
                                                       f"store can keep; it was left out.")
            return None
        board = self.boards.get(ref)
        if board is None:
            self.boards[ref] = BoardIn(ref[0], ref[1], company, True, (rid,))
            self.c(company).boards.append(ref)
        else:
            if board.company_key != company:
                self.issue("board_conflict", company, rid, f"{rid} puts the board {ref[0]}:{ref[1]} at {company}; it "
                                                           f"stays at {board.company_key}.")
            if rid not in board.records:
                self.boards[ref] = replace(board, records=board.records + (rid,))
        return ref

    def add_posting(self, posting: PostingIn):
        old = self.postings.get(posting.source_key)
        if old is None:
            self.postings[posting.source_key] = posting
        else:
            self.postings[posting.source_key] = replace(old, records=old.records + tuple(
                r for r in posting.records if r not in old.records))

    def person(self, company, name, rid, *, email=None, linkedin=None, title="", role=None, profile="", tz=""):
        key = self.people.add(company, name, email=email, linkedin=linkedin, record=rid)
        self.details.setdefault(key, {})
        d = self.details[key]
        for k, v in (("title", _text(title)), ("role", role), ("profile", _text(profile)), ("tz", _text(tz))):
            if v and not d.get(k):
                d[k] = v
        return key

    def placeholder(self, company, rid, role="other", n=0):
        key = self.people.placeholder(company, rid if n == 0 else f"{rid}#{n + 1}", role or "other")
        self.details.setdefault(key, {"role": role or "other"})
        return key

    def touch(self, rid, company, person, channel, sent_at, *, outcomes=(), email=None, followups=0,
              role_named=False, known=False) -> str:
        key = touch_key(company, person, channel, sent_at)
        self.raw_touches.append(TouchIn(key=key, company_key=company, person_key=person, channel=channel,
                                        sent_at=sent_at, posting_key=None, role_named=role_named,
                                        role_named_known=known, outcomes=tuple(outcomes), email=email,
                                        followups_sent=followups, records=(rid,)))
        self.claim(rid, "outreach_touch", key)
        return key

    # -- the build --

    def build(self) -> ImportPlan:
        by_source: dict[str, list[SourceRecord]] = {s: [] for s in SOURCES}
        for record in self.input.values():
            by_source[record.source].append(record)
        for record in by_source["data_js"]:
            self.data_js(record, _rid(record))
        for record in by_source["contacts_json"]:
            self.contact(record, _rid(record))
        for record in by_source["send_log"]:
            self.send_line(record, _rid(record))
        for record in by_source["local_storage"]:
            self.tracker(record, _rid(record))
        for record in by_source["contacted_txt"]:
            if _dict(record.raw).get("kind") == "touch":
                self.contacted(record, _rid(record))
        for record in by_source["devvault_notes"]:
            self.note_file(record, _rid(record))
        for record in by_source["radar_db"]:
            self.radar(record, _rid(record))
        for record in by_source["devboard"]:
            self.devboard(record, _rid(record))
        for record in by_source["contacted_txt"]:
            if _dict(record.raw).get("kind") != "touch":
                self.contacted_other(record, _rid(record))
        for record in by_source["interview_prep"]:
            self.interview_prep(record, _rid(record))
        curated = []
        for record in by_source["curated_touches"]:
            band = band_stage(_dict(record.raw).get("band"))
            curated.append(record)
            if band and _text(_dict(record.raw).get("company")):
                key = self.companies.add(_text(record.raw["company"]), _rid(record))
                self.evidence(key, EvidenceDraft("stage", band, "guess", "other", quote="curated band"),
                              _rid(record))

        self.people.finalize()
        self.issues.extend(self.people.issues)
        touches = self.consolidate()
        people = self.freeze_people()
        joined, issues, outs = join_curated(list(touches.values()), curated, self.companies, people=people)
        self.issues.extend(issues)
        self.outcomes.update(outs)
        self.held_code.update({r: o.reason for r, o in outs.items() if o.outcome == "held"})
        touches = {}
        for t in joined:
            touches[t.key] = t
        self.place_later(touches)
        self.close_and_plan(touches)
        self.freeze_frozen_boards()
        people = self.add_placeholders(people, touches)
        people = self.stops(people, touches)
        done = self.done_set(touches)
        self.issues.extend(self.companies.issues())
        near = tuple(self.companies.near_names())
        for a, b in near:
            self.issue("near_name", a, self.companies.records(a)[0],
                       f"{self.companies.name(a)!r} and {self.companies.name(b)!r} may be one company; they stay two. "
                       f"Rule [same_company] or [not_same_company].")
        companies = self.freeze_companies(done)
        self.settle_overrides()
        records = self.finish_outcomes(companies, people, touches)
        return ImportPlan(companies=companies, people=people, touches=touches, applications=dict(self.apps),
                          boards=dict(self.boards), postings=dict(self.postings), issues=tuple(self.issues),
                          records=records, done_set=done, near_names=near,
                          followups_not_planned=self.followups_not_planned)

    # -- data.js --

    def data_js(self, record, rid):
        raw = _dict(record.raw)
        key_part = record.source_key
        if key_part in ("meta", "reference"):
            return self.skip(rid, "reference_data")
        if key_part.startswith("ruled_out:"):
            return self.ruled_out(record, rid)
        name = _text(raw.get("name"))
        if not name:
            return self.hold(rid, "unparsed_record", f"{rid} has no company name, so it cannot be placed.")
        key = self.companies.add(name, rid, base=True)
        self.base_ids[key_part.split(":", 1)[1]] = key
        self.c(key)
        self.claim(rid, "company", key)
        outreach = _dict(raw.get("outreach"))
        if outreach.get("domain"):
            domain = _bare_host(outreach.get("domain"))
            if domain:
                self.set_domain(key, domain, rid)
            else:
                self.issue("domain_unparsed", key, rid, f"{rid} gives the domain {outreach.get('domain')!r}, which "
                                                        f"is not a bare host; it was left out.")
        board = _dict(raw.get("board"))
        if board:
            self.add_board(board.get("ats"), board.get("token"), key, rid)
        for req in raw.get("reqs") or []:
            req = _dict(req)
            url = _text(req.get("u"))
            if not url:
                self.issue("req_no_url", key, rid, f"{rid} lists the role {req.get('t')!r} with no link; it was "
                                                   f"left out.")
                continue
            day = _day(req.get("d"))
            seen = noon_ny(day) if day else self.now
            self.add_posting(PostingIn(source_key=f"url:{url}", company_key=key, board=None, title=_text(req.get("t")),
                                       location="", url=url, posted_on=day, first_seen_at=seen, last_seen_at=seen,
                                       closed=False, body=None, records=(rid,)))
        for draft in from_old_dashboard(raw):
            self.evidence(key, draft, rid)
        visa = _text(raw.get("visa"))
        ref = f"import:{rid}"
        hand = self.c(key).hand
        if raw.get("visaConfirmed") is True:
            hand.append(EvidenceDraft("sponsorship", "yes", "confirmed", "hand", ref, visa))
        if raw.get("visaDenied") is True:
            hand.append(EvidenceDraft("sponsorship", "no", "confirmed", "hand", ref, visa))
            self.issue("old_no_sponsorship", key, rid, f"The old dashboard says {name} does not sponsor "
                                                       f"({visa or 'no quote'}). Add a no-go if that still holds.")
        if raw.get("optOnly") is True:
            hand.append(EvidenceDraft("sponsorship", "opt_only", "confirmed", "hand", ref, visa))
        for signal in old_dashboard_signals(raw):
            if signal.kind in ("defence_contractor", "agency"):
                self.c(key).no_go.append((signal.kind, signal.quote))
            elif signal.kind == "over_500":
                if not any(d.fact == "size" and (d.num_low or 0) >= 501 for d in self.c(key).evidence):
                    self.evidence(key, EvidenceDraft("size", "501+", "estimate", "old_dashboard", quote=signal.quote,
                                                     num_low=501), rid)
            else:
                self.issue("old_size_signal", key, rid, f"The old dashboard hid {name} as too big ({signal.kind}: "
                                                        f"{signal.quote}). Check its size.")
        seed = raw.get("seed")
        if _text(seed):
            kinds = tracker_stage(seed)
            if kinds is None:
                self.issue("seed_unmapped", key, rid, f"{rid} has the seed {seed!r}, which means nothing here.")
            for kind in kinds or []:
                if kind in ("replied", "interview", "offer"):
                    self.later.append(_Later(key, kind, "unsorted" if kind == "replied" else None, None, True, rid,
                                             False))

    def ruled_out(self, record, rid):
        raw = _dict(record.raw)
        why = _text(raw.get("why"))
        n = record.source_key.split(":", 1)[1]
        first = None
        for part in (p.strip() for p in _text(raw.get("n")).split(" / ")):
            if not part:
                continue
            key = self.companies.add(part, rid)
            self.c(key).notes.append((rid, why))
            self.issue("ruled_out", key, rid, f"The old dashboard ruled this out: {why.rstrip('.')}. Remove it if "
                                              f"that still holds.", key=f"import:ruled_out:{n}:{key}")
            first = first or key
        if first is None:
            return self.hold(rid, "unparsed_record", f"{rid} names no company.")
        self.outcomes[rid] = RecordOutcome("imported", "", "company", first)

    # -- the old mailer --

    def contact(self, record, rid):
        c = _dict(record.raw)
        company = _text(c.get("company"))
        email = email_key(c.get("email"))
        status = c.get("status")
        plan = contact_status_plan(status, followup_sent=bool(c.get("followupSentAt")))
        if plan is None:
            return self.hold(rid, "status_unmapped", f"{rid} has the status {status!r}, which the old mailer never "
                                                     f"wrote; settle it in the old mailer and export again.")
        if plan.held:
            if email:
                self.held_emails[email] = rid
            return self.hold(rid, plan.held, f"{rid} ({c.get('name')}, {company}) is {status!r} in the old mailer: "
                                             f"it may or may not have been sent. Let the old mailer settle it (it "
                                             f"checks Sent) and export again.")
        if not company:
            return self.hold(rid, "unparsed_record", f"{rid} names no company.")
        key = self.companies.add(company, rid)
        base = self.base_ids.get(_text(c.get("companyId")))
        if base and base != key:
            self.issue("company_id_mismatch", key, rid, f"{rid} is at {company!r} ({key}) but its company id points "
                                                        f"at {base}; it was linked by name.")
        role = role_kind(c.get("kind"))
        if role is None:
            if _text(c.get("kind")):
                self.issue("role_unmapped", key, rid, f"{rid} has the kind {c.get('kind')!r}; kept as other.")
            role = "other"
        name = _text(c.get("name")) or _text(c.get("email"))
        pk = self.person(key, name, rid, email=c.get("email"), linkedin=c.get("linkedinUrl"),
                         title=c.get("title"), role=role, profile=c.get("profileText"), tz=c.get("tz")) if name \
            else self.placeholder(key, rid, role)
        history = tuple((_text(h.get("at")), _text(h.get("what"))) for h in c.get("history") or []
                        if isinstance(h, dict))

        def when(what):
            return next((_utc(at) for at, w in history if w == what and _utc(at)), None)

        draft, followup = _dict(c.get("draft")), _dict(c.get("followup"))
        sent0 = None
        if plan.message0 == "sent":
            sent0 = _utc(c.get("sentAt")) or when("sent")
            if sent0 is None:
                return self.hold(rid, "unclear_send", f"{rid} is {status!r} but has no send time; settle it in the "
                                                      f"old mailer and export again.")
        sent1 = _utc(c.get("followupSentAt")) if plan.message1 == "sent" else None
        if plan.message1 == "sent" and sent1 is None:
            return self.hold(rid, "unclear_send", f"{rid} has a sent follow-up with no time; settle it in the old "
                                                  f"mailer and export again.")
        due1 = None
        if plan.message1 == "draft" and sent0:
            due1 = self.due_on(sent0)
        elif plan.message0 == "sent" and plan.message1 == "none" and plan.touch_state == "open":
            self.plan_followup.add(_text(c.get("id")))
        outcomes = []
        bounced_at = auto_at = None
        if plan.touch_state == "replied":
            at = _utc(c.get("replyAt")) or when("replied")
            outcomes.append(OutcomeIn("replied", at or sent0, "unsorted", at is None, rid))
        if plan.touch_state == "bounced":
            bounced_at = when("bounced")
            outcomes.append(OutcomeIn("bounced", bounced_at or sent0, None, bounced_at is None, rid))
            bounced_at = bounced_at or sent0
        if _utc(c.get("autoReplyAt")):
            auto_at = _utc(c.get("autoReplyAt"))
            outcomes.append(OutcomeIn("auto_reply", auto_at, None, False, rid))
        role_ref = _dict(c.get("role"))
        named = bool(_text(role_ref.get("title")) or _text(role_ref.get("url")))
        mail = EmailIn(
            contact_id=_text(c.get("id")),
            subject0=_text(draft.get("subject")) or None, body0=_text(draft.get("body")) or None,
            rfc0=_text(c.get("rfcMessageId")) or None if sent0 else None,
            thread_id=_text(c.get("threadId")) or None if sent0 else None,
            gmail_id0=_text(c.get("messageId")) or None if sent0 else None, sent0_at=sent0,
            subject1=_text(followup.get("subject")) or None if plan.message1 in ("sent", "draft") else None,
            body1=_text(followup.get("body")) or None if plan.message1 in ("sent", "draft") else None,
            rfc1=None, sent1_at=sent1, due1_on=due1, touch_state=plan.touch_state, close_reason=plan.close_reason,
            bounced_at=bounced_at, auto_reply_at=auto_at, history=history, old_approval=plan.old_approval)
        sent_at = sent0 or _utc(c.get("createdAt")) or (history and _utc(history[0][0])) or self.now
        tkey = self.touch(rid, key, pk, "email", sent_at, outcomes=outcomes, email=mail,
                          followups=1 if sent1 else 0, role_named=named, known=named)
        if not sent0:
            self.claims[rid] = [("person", pk)]  # nothing was sent: no outreach_touch, the record is the person
        if email:
            sends = self.contact_sends.setdefault(email, [])
            if sent0:
                sends.append((tkey, ny_date(sent0), False))
            if sent1:
                sends.append((tkey, ny_date(sent1), True))

    def due_on(self, sent_at: str) -> str:
        first = date.fromisoformat(ny_date(sent_at))
        return add_business_days(first, self.cfg.followup_business_days[0], self.cfg).isoformat()

    def send_line(self, record, rid):
        raw = _dict(record.raw)
        if raw.get("kind") != "sent":
            return self.skip(rid, "log_line")
        email = email_key(raw.get("email"))
        day = _day(raw.get("at"))
        if email in self.held_emails:
            return self.hold(rid, "unclear_send", f"{rid} is a send to {email}, whose contact "
                                                  f"({self.held_emails[email]}) is not settled.")
        follow_up = bool(raw.get("follow_up"))
        match = [k for k, d, f in self.contact_sends.get(email, []) if d == day and f == follow_up]
        if not match:
            return self.hold(rid, "sent_not_in_contacts",
                             f"{rid} says {'a follow-up' if follow_up else 'an email'} went to {raw.get('name')} "
                             f"<{raw.get('email')}> at {raw.get('company')} on {day}, and no contact matches. Find "
                             f"the contact (or add it to contacts.json) and export again.")
        self.claim(rid, "outreach_touch", match[0])

    # -- the old tracker --

    def tracker(self, record, rid):
        entry = _dict(record.raw)
        stage = _text(entry.get("stage"))
        fields = ("contact", "channel", "reached", "repliedOn", "next", "notes")
        if stage.lower() in ("", "not started") and not any(_text(entry.get(f)) for f in fields):
            return self.skip(rid, "old_queue_skip" if _text(entry.get("skipped")) else "untouched_default")
        cid = record.source_key.split(":", 1)[1]
        base = self.base_ids.get(cid)
        if base is None:
            return self.hold(rid, "company_unknown", f"{rid} is a tracker entry for the company id {cid!r}, which "
                                                     f"the old dashboard does not have.")
        key = self.companies.add(self.companies.name(base), rid)
        kinds = tracker_stage(stage)
        if kinds is None:
            self.issue("stage_unmapped", key, rid, f"{rid} has the stage {stage!r}; read as no stage.")
            kinds = []
        channel = tracker_channel(entry.get("channel"))
        if channel is None:
            self.issue("channel_unmapped", key, rid, f"{rid} has the channel {entry.get('channel')!r}; kept as other.")
            channel = "other"
        reached, replied = _day(entry.get("reached")), _day(entry.get("repliedOn"))
        contact = _text(entry.get("contact"))
        heard = [k for k in kinds if k in ("replied", "interview", "offer")]
        made = False
        if reached:
            at = noon_ny(reached)
            name = parse_person_name(contact)
            if name:
                pk = self.person(key, name, rid)
            else:
                pk = self.placeholder(key, rid)
                if contact:
                    self.issue("name_unparsed", key, rid, f"{rid} names the contact {contact!r}, which is not a "
                                                          f"name; kept as an unnamed person.")
            outcomes = []
            if replied or "replied" in kinds:
                r_at = max(noon_ny(replied), at) if replied else at
                outcomes.append(OutcomeIn("replied", r_at, "unsorted", not replied, rid))
            for kind in heard:
                if kind != "replied":
                    outcomes.append(OutcomeIn(kind, max(noon_ny(replied), at) if replied else at, None, True, rid))
            self.touch(rid, key, pk, channel, at, outcomes=outcomes)
            made = True
        else:
            if replied and "replied" not in heard:
                heard = ["replied"] + heard
            for kind in heard:
                self.later.append(_Later(key, kind, "unsorted" if kind == "replied" else None,
                                         replied if kind == "replied" else None, True, rid, False))
            if contact or "touch" in kinds:
                self.issue("date_unknown", key, rid, f"{rid} says {stage or 'a touch'} with {contact or 'someone'} "
                                                     f"but gives no date, so no touch was made for it.")
        if "application" in kinds:
            if reached:
                akey = f"app:{key}:{rid}"
                self.apps[akey] = ApplicationIn(akey, key, None, reached, "", "open", None, (rid,))
                self.claim(rid, "application", akey)
                made = True
            else:
                self.issue("date_unknown", key, rid, f"{rid} says Applied with no date; no application was made.")
        notes = _text(entry.get("notes"))
        if "close" in kinds:
            self.c(key).close = ("other", notes or "Closed in the old tracker")
        if notes:
            self.c(key).notes.append((rid, notes))
        if _text(entry.get("next")):
            self.c(key).notes.append((rid, f"Next: {_text(entry.get('next'))}"))
        if not made:
            self.claim(rid, "company", key)

    # -- contacted.txt --

    def contacted(self, record, rid):
        raw = _dict(record.raw)
        day = _day(raw.get("date"))
        company = _text(raw.get("company"))
        if not day or not company:
            return self.hold(rid, "unparsed_record", f"{rid} has no date or no company.")
        key = self.companies.add(company, rid)
        at = noon_ny(day)
        for n, mention in enumerate(raw.get("people") or []):
            mention = _dict(mention)
            text, note = _text(mention.get("name")), mention.get("note")
            name = parse_person_name(text)
            if name is None and " - " in text:
                head, tail = text.split(" - ", 1)
                name, note = parse_person_name(head), note or tail
            role = _note_role(note)
            if name:
                pk = self.person(key, name, rid, role=role)
            else:
                pk = self.placeholder(key, rid, role, n)
                self.issue("name_unparsed", key, rid, f"{rid} names {text!r}, which is not a name; kept as an "
                                                      f"unnamed person.")
            self.touch(rid, key, pk, "email", at)
        if rid not in self.claims:
            self.claim(rid, "company", key)

    def contacted_other(self, record, rid):
        raw = _dict(record.raw)
        kind = raw.get("kind")
        if kind == "comment":
            return self.skip(rid, "comment_line")
        if kind == "blank":
            return self.skip(rid, "blank_line")
        line = _text(raw.get("line"))
        found = self.find_company(line)
        if found is None:
            return self.hold(rid, "unparsed_record", f"{rid} ({line!r}) does not parse and names no known company. "
                                                     f"Fix the line, or rule [skip_record] for it.")
        key = self.companies.add(found, rid)
        day = _DATE_IN_TEXT.search(line)
        if day and _day(day.group(1)):
            pk = self.placeholder(key, rid)
            self.touch(rid, key, pk, "email", noon_ny(_day(day.group(1))))
            self.issue("name_unparsed", key, rid, f"{rid} ({line!r}) does not parse; kept as a touch at {found} "
                                                  f"with an unnamed person.")
        else:
            self.c(key).notes.append((rid, line))
            self.claim(rid, "company", key)
            self.issue("unparsed_line", key, rid, f"{rid} ({line!r}) does not parse and has no date; kept as a note "
                                                  f"on {found}.")

    def find_company(self, text: str) -> str | None:
        """The longest known company name the text contains as whole words, case aside."""
        best = None
        for key in self.companies.keys():
            for name in self.companies.names(key):
                if len(name) >= 3 and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.I):
                    if best is None or len(name) > len(best):
                        best = name
        return best

    # -- company files --

    def note_file(self, record, rid):
        raw = _dict(record.raw)
        fm = raw.get("frontmatter")
        if not isinstance(fm, dict):
            return self.hold(rid, "unparsed_record", f"{rid} has no frontmatter, so it names no company. Rule "
                                                     f"[skip_record] if it is not a company file.")
        name = _text(fm.get("company"))
        if not name:
            return self.hold(rid, "unparsed_record", f"{rid} names no company.")
        key = self.companies.add(name, rid)
        self.c(key)
        if fm.get("domain"):
            domain = _bare_host(fm.get("domain"))
            if domain:
                self.set_domain(key, domain, rid)
        stage = parse_stage(fm.get("stage"), "other") if _text(fm.get("stage")) else None
        if stage:
            self.evidence(key, stage, rid)
        if _text(fm.get("location")):
            self.evidence(key, EvidenceDraft("hq", _text(fm.get("location")), "estimate", "other",
                                             quote=_text(fm.get("location"))), rid)
        people = fm.get("people")
        if people is not None and not isinstance(people, list):
            self.issue("people_unparsed", key, rid, f"{rid} has a people list it cannot read; no one was taken "
                                                    f"from it.")
            people = []
        for p in people or []:
            p = _dict(p)
            role = role_kind(p.get("role"))
            if role is None and _text(p.get("role")):
                self.issue("role_unmapped", key, rid, f"{rid} gives {p.get('name')} the role {p.get('role')!r}; "
                                                      f"kept as other.")
            pname = _text(p.get("name"))
            pk = self.person(key, pname, rid, email=p.get("email"), linkedin=p.get("linkedin"), title=p.get("title"),
                             role=role) if pname else self.placeholder(key, rid, role)
            sent = _day(p.get("sent"))
            if not sent:
                continue
            at = noon_ny(sent)
            effect = note_reply(p.get("reply"))
            if effect is None:
                self.issue("reply_text", key, rid, f"{rid} says {pname or 'someone'} replied {p.get('reply')!r}; "
                                                   f"kept as a reply to sort.")
                effect = (["replied"], "unsorted")
            kinds, reply_kind = effect
            rday = _day(p.get("reply_date"))
            r_at = max(noon_ny(rday), at) if rday else at
            outcomes = [OutcomeIn(k, r_at, reply_kind if k == "replied" else None, not rday, rid) for k in kinds]
            followups = 0
            count = _text(p.get("touch_count"))
            if count.isdigit() and int(count) > 1:
                followups = int(count) - 1
            last = _day(p.get("last_sent"))
            if last and last > sent:
                followups = max(followups, 1)
            self.touch(rid, key, pk, "email", at, outcomes=outcomes, followups=followups)
        if rid not in self.claims:
            self.claim(rid, "company", key)

    # -- Job Radar --

    def radar(self, record, rid):
        raw = _dict(record.raw)
        kind, _, _ = record.source_key.partition(":")
        if kind == "table":
            return self.skip(rid, _text(raw.get("reason")) or "run_history")
        if kind == "board":
            key = self.companies.add(_text(raw.get("company")) or _text(raw.get("token")), rid)
            ref = self.add_board(raw.get("ats"), raw.get("token"), key, rid)
            self.radar_boards[raw.get("board_id")] = (ref, key)
            if ref:
                self.outcomes[rid] = RecordOutcome("imported", "", "board", f"{ref[0]}:{ref[1]}")
            else:
                self.claim(rid, "company", key)
            return
        if kind == "posting":
            return self.radar_posting(record, rid, raw)
        if kind == "app_event":
            return self.radar_event(rid, raw)
        self.hold(rid, "unparsed_record", f"{rid} is a radar record of a kind the importer does not know.")

    def radar_posting(self, record, rid, raw):
        ref, key = self.radar_boards.get(raw.get("board_id"), (None, None))
        if key is None:
            return self.hold(rid, "unparsed_record", f"{rid} is on a board radar.db does not list.")
        source_key = _text(raw.get("source_key"))
        parts = source_key.split(":", 2)
        if ref and len(parts) == 3:
            skey = f"{ref[0]}:{ref[1]}:{parts[2]}"
            board = ref
        else:
            skey, board = f"url:{_text(raw.get('apply_url'))}", None
        self.radar_postings[raw.get("posting_id")] = skey
        if raw.get("merged_into") is not None:
            self.radar_merged[rid] = raw.get("merged_into")
            return
        precision = _text(raw.get("posted_at_precision"))
        posted = raw.get("posted_at")
        posted_on = None
        if posted and precision == "exact":
            posted_on = ny_date(_utc(posted)) if _utc(posted) else None
        elif posted and precision == "day":
            posted_on = _day(posted)
        first = _utc(raw.get("first_seen_at")) or self.now
        last = max(_utc(raw.get("last_seen_at")) or first, first)
        self.add_posting(PostingIn(source_key=skey, company_key=key, board=board, title=_text(raw.get("title")),
                                   location=_text(raw.get("location")), url=_text(raw.get("apply_url")),
                                   posted_on=posted_on, first_seen_at=first, last_seen_at=last,
                                   closed=raw.get("closed") == 1, body=radar_body(record), records=(rid,)))
        self.outcomes[rid] = RecordOutcome("imported", "", "posting", skey)

    def radar_event(self, rid, raw):
        effect = radar_event(raw.get("kind"))
        if effect is None:
            return self.hold(rid, "unparsed_record", f"{rid} is a radar event of kind {raw.get('kind')!r}, which "
                                                     f"the importer does not know.")
        if effect.skip:
            return self.skip(rid, effect.skip)
        skey = self.radar_postings.get(raw.get("posting_id"))
        posting = self.postings.get(skey) if skey else None
        if posting is None:
            return self.hold(rid, "unparsed_record", f"{rid} is on a posting radar.db does not list.")
        at = _utc(raw.get("at")) or self.now
        akey = f"app:{skey}"
        app = self.apps.get(akey) or ApplicationIn(akey, posting.company_key, skey, ny_date(at), posting.url, "open",
                                                   None, ())
        applied_on = min(app.applied_on, ny_date(at))
        outcome, outcome_at = app.outcome, app.outcome_at
        if effect.app_outcome and (outcome_at is None or at >= outcome_at):
            outcome, outcome_at = effect.app_outcome, at
        outcomes = app.outcomes
        for kind in effect.outcomes:
            outcomes, conflict = merge_outcome(outcomes, OutcomeIn(kind, at, effect.reply_kind if kind == "replied"
                                                                   else None, False, rid))
        self.apps[akey] = replace(app, applied_on=applied_on, outcome=outcome, outcome_at=outcome_at,
                                  outcomes=outcomes, records=app.records + (rid,))
        self.claim(rid, "application", akey)

    def devboard(self, record, rid):
        raw = _dict(record.raw)
        status = _text(raw.get("status")).lower()
        if status == "ignored":
            return self.skip(rid, "triage_ignore")
        if status != "applied":
            return self.skip(rid, "opened_only")  # new or saved: seen, never acted on
        key = self.companies.add(_text(raw.get("company")) or _text(raw.get("key")), rid)
        parsed = _dict(raw.get("parsed_key"))
        skey = None
        if parsed:
            try:
                skey = f"{parsed['ats']}:{normalize_token(parsed['ats'], parsed['token'])}:{parsed['native_id']}"
            except (KeyError, ValueError):
                skey = None
        url = _text(raw.get("url"))
        if skey not in self.postings:
            skey = f"url:{url}" if url else None
            if skey and skey not in self.postings:
                seen = _utc(raw.get("posted")) or _utc(raw.get("updated")) or self.now
                self.add_posting(PostingIn(source_key=skey, company_key=key, board=None, title=_text(raw.get("title")),
                                           location="", url=url, posted_on=_day(raw.get("posted")),
                                           first_seen_at=seen, last_seen_at=max(_utc(raw.get("updated")) or seen,
                                                                                seen),
                                           closed=False, body=None, records=(rid,)))
        akey = f"app:{skey}" if skey else f"app:{key}:{rid}"
        day = ny_date(_utc(raw.get("updated")) or self.now)
        app = self.apps.get(akey)
        if app is None:
            company = self.postings[skey].company_key if skey else key
            self.apps[akey] = ApplicationIn(akey, company, skey, day, url, "open", None, (rid,))
        else:
            self.apps[akey] = replace(app, records=app.records + (rid,))
        self.claim(rid, "application", akey)

    # -- interview prep --

    def interview_prep(self, record, rid):
        raw = _dict(record.raw)
        slug = _text(raw.get("slug"))
        if slug == "general":
            return self.skip(rid, "not_a_company")
        brief = _dict(raw.get("brief"))
        name = _text(brief.get("company")) or slug.replace("-", " ")
        key = self.companies.add(name, rid)
        day, estimated = interview_date(record)
        self.later.append(_Later(key, "interview", None, day, estimated, rid, True))

    # -- after linking --

    def consolidate(self) -> dict[str, TouchIn]:
        """Resolve every person key, then one email touch per person and one touch per key for the rest."""
        resolved: list[tuple[str, TouchIn]] = []
        for t in self.raw_touches:
            pk = self.people.resolve(t.person_key) if t.person_key else None
            new = replace(t, person_key=pk, key=touch_key(t.company_key, pk, t.channel, t.sent_at))
            resolved.append((t.key, new))
        groups: dict[str, list[tuple[str, TouchIn]]] = {}
        for old, t in resolved:
            group = f"email:{t.person_key}" if t.channel == "email" and t.person_key else f"key:{t.key}"
            groups.setdefault(group, []).append((old, t))
        out: dict[str, TouchIn] = {}
        for group, members in groups.items():
            ts = [t for _, t in members]
            sent = [t for t in ts if t.email and t.email.sent0_at]
            historic = [t for t in ts if not t.email]
            drafts = [t for t in ts if t.email and not t.email.sent0_at]
            if sent:
                kept = min(sent, key=lambda t: t.sent_at)
            elif historic:
                kept = min(historic, key=lambda t: t.sent_at)
                for d in drafts:
                    self.issue("draft_after_send", d.company_key, d.records[0],
                               f"{d.records[0]} is an unsent draft to someone {kept.records[0]} says was already "
                               f"emailed on {ny_date(kept.sent_at)}; the draft was not imported, so no second first "
                               f"email can go out.")
            else:
                kept = drafts[0]
            merged = kept
            known_days = {ny_date(kept.sent_at)}
            if kept.email and kept.email.sent1_at:
                known_days.add(ny_date(kept.email.sent1_at))
            later_days = set()
            for t in ts:
                if t is kept:
                    continue
                if t.email and t.email.contact_id and t.email.sent0_at:
                    self.issue("two_contacts", t.company_key, t.records[0],
                               f"{t.records[0]} and {kept.records[0]} are two old mailer contacts for one person; "
                               f"the earlier is kept and the other counts as a follow-up.")
                day = ny_date(t.sent_at) if not (t.email and not t.email.sent0_at) else None
                if day and day not in known_days:
                    if day > ny_date(kept.sent_at):
                        later_days.add(day)
                    elif kept.email and kept.email.contact_id:
                        self.issue("email_dates_differ", t.company_key, t.records[0],
                                   f"{t.records[0]} has an email on {day}, before the old mailer's first send on "
                                   f"{ny_date(kept.sent_at)}; the mailer's date is kept.")
                outcomes = merged.outcomes
                for o in t.outcomes:
                    outcomes, conflict = merge_outcome(outcomes, o)
                    if conflict:
                        self.issue("outcome_conflict", t.company_key, o.record, conflict)
                named, known = merged.role_named, merged.role_named_known
                if not known and t.role_named_known:
                    named, known = t.role_named, True
                merged = replace(merged, outcomes=outcomes, role_named=named, role_named_known=known,
                                 followups_sent=max(merged.followups_sent, t.followups_sent),
                                 records=merged.records + tuple(r for r in t.records if r not in merged.records))
            merged = replace(merged, followups_sent=merged.followups_sent + len(later_days))
            for old, _ in members:
                self.alias[old] = merged.key
            if merged.key in out:  # two groups ending on one key cannot happen: email groups are by person
                raise RuntimeError(f"two touches with the key {merged.key}")
            out[merged.key] = merged
        return out

    def freeze_people(self) -> dict[str, PersonIn]:
        details: dict[str, dict] = {}
        for mention, d in self.details.items():
            into = details.setdefault(self.people.resolve(mention), {})
            for k, v in d.items():
                if v and not into.get(k):
                    into[k] = v
        out = {}
        for p in self.people.people():
            d = details.get(p.key, {})
            out[p.key] = PersonIn(key=p.key, company_key=p.company_key, name=p.name, placeholder=p.placeholder,
                                  title=d.get("title", ""), role_kind=d.get("role") or "other", email=p.email,
                                  linkedin_url=p.linkedin, profile_text=d.get("profile", ""),
                                  tz=d.get("tz") or DEFAULT_TZ,
                                  records=tuple(dict.fromkeys(r.split("#", 1)[0] for r in p.records)))
        return out

    def target_of(self, key, touches) -> TouchIn | ApplicationIn:
        return touches[key] if key in touches else self.apps[key]

    def place_later(self, touches: dict[str, TouchIn]):
        """Interviews, seeds and dateless tracker outcomes onto their origin touch; dated ones first, so a dateless
        one joins a touch a dated one made."""
        for item in sorted(self.later, key=lambda x: x.day is None):
            at_day = noon_ny(item.day) if item.day else None
            target, code = origin_touch(item.company, at_day, list(touches.values()), list(self.apps.values()),
                                        record=item.record, overrides=self.ov)
            estimated = item.estimated
            if target is None:
                t = interview_touch(item.company, item.day or self.today, item.record)
                touches.setdefault(t.key, t)
                target = t.key
            if item.day is None and item.explicit:
                self.issue("date_unknown", item.company, item.record,
                           f"{item.record} gives no date for the {item.kind}; "
                           f"{'the import day' if code == 'interview_origin' else 'its touch' + chr(39) + 's date'} "
                           f"is used.")
            obj = self.target_of(target, touches)
            start = obj.sent_at if isinstance(obj, TouchIn) else noon_ny(obj.applied_on)
            at = at_day
            if at is None or at < start:
                at, estimated = start, True
            if code:
                self.issue(code, item.company, item.record, f"{item.record}: the {item.kind} at {item.company} was "
                                                            f"put on {target}. Rule [touch_for] if it came another "
                                                            f"way.")
            new = OutcomeIn(item.kind, at, item.reply_kind, estimated, item.record)
            outcomes, conflict = merge_outcome(obj.outcomes, new)
            if conflict:
                self.issue("outcome_conflict", item.company, item.record, conflict)
            records = obj.records + (() if item.record in obj.records else (item.record,))
            if isinstance(obj, TouchIn):
                touches[target] = replace(obj, outcomes=outcomes, records=records)
                entity = "outreach_touch"
            else:
                outcome, outcome_at = obj.outcome, obj.outcome_at
                if item.kind in _APP_RANK and _APP_RANK[item.kind] > _APP_RANK.get(outcome, -1):
                    outcome, outcome_at = item.kind, at
                self.apps[target] = replace(obj, outcomes=outcomes, outcome=outcome, outcome_at=outcome_at,
                                            records=records)
                entity = "application"
            if item.explicit:
                self.outcomes[item.record] = RecordOutcome("imported", "", entity, target)

    def close_and_plan(self, touches: dict[str, TouchIn]):
        """Historic email touches get their state; contacts.json touches take a reply or bounce another source
        recorded; an open sent touch gets its planned follow-up when it is due on or after the import day."""
        for key, t in list(touches.items()):
            if t.channel != "email":
                continue
            replied = next((o for o in t.outcomes if o.kind == "replied"), None)
            bounced = next((o for o in t.outcomes if o.kind == "bounced"), None)
            auto = next((o for o in t.outcomes if o.kind == "auto_reply"), None)
            mail = t.email
            if mail is None:
                state = "replied" if replied else "bounced" if bounced else "closed"
                mail = EmailIn(None, None, None, None, None, None, t.sent_at, None, None, None, None, None, state,
                               "no_reply" if state == "closed" else None, bounced.at if bounced else None,
                               auto.at if auto else None, ())
            else:
                state, reason, bounced_at = mail.touch_state, mail.close_reason, mail.bounced_at
                if replied and state in ("open", "closed") and mail.sent0_at:
                    state, reason = "replied", None
                elif bounced and state == "open" and mail.sent0_at:
                    state, bounced_at = "bounced", bounced.at
                due = mail.due1_on
                if mail.contact_id in self.plan_followup and state == "open" and t.followups_sent == 0:
                    due = self.due_on(mail.sent0_at)
                    if due < self.today:
                        self.followups_not_planned += 1
                        due = None
                mail = replace(mail, touch_state=state, close_reason=reason, bounced_at=bounced_at, due1_on=due,
                               auto_reply_at=mail.auto_reply_at or (auto.at if auto else None))
            touches[key] = replace(t, email=mail)

    def freeze_frozen_boards(self):
        sizes: dict[str, int] = {}
        for key, c in self.co.items():
            lows = [d.num_low for d in c.evidence + c.hand if d.fact in ("size", "headcount") and d.num_low is not None]
            if lows:
                sizes[key] = max(lows)
        frozen = set()
        for ref, board in list(self.boards.items()):
            if board_frozen(board.ats, sizes.get(board.company_key)):
                self.boards[ref] = replace(board, active=False)
                frozen.add(ref)
        for skey, posting in list(self.postings.items()):
            if posting.board in frozen and not posting.closed:
                self.postings[skey] = replace(posting, closed=True)

    def add_placeholders(self, people: dict[str, PersonIn], touches: dict[str, TouchIn]) -> dict[str, PersonIn]:
        """Placeholder people made after linking (an unmatched curated row, an interview with no way in)."""
        out = dict(people)
        for t in touches.values():
            if t.person_key and t.person_key not in out:
                if not is_placeholder(t.person_key):
                    raise RuntimeError(f"touch {t.key} names the unknown person {t.person_key}")
                record = t.person_key.split("/placeholder:", 1)[1]
                out[t.person_key] = PersonIn(key=t.person_key, company_key=t.company_key,
                                             name="Unnamed other (imported)", placeholder=True, title="",
                                             role_kind="other", email=None, linkedin_url=None, profile_text="",
                                             tz=DEFAULT_TZ, records=(record,))
        return out

    def stops(self, people: dict[str, PersonIn], touches: dict[str, TouchIn]) -> dict[str, PersonIn]:
        """Everyone who said no or asked to stop, and their same-name namesakes, get a person no-go."""
        known = {p.key for p in self.people.people()}
        indexed = [t for t in touches.values() if t.person_key in known]
        stops, issues = carry_stops(self.people, indexed)
        self.issues.extend(issues)
        for t in touches.values():  # placeholders made after linking have no namesakes
            if t.person_key and t.person_key not in known:
                for o in t.outcomes:
                    if o.kind == "replied" and o.reply_kind in ("said_no", "asked_to_stop"):
                        stops.setdefault(t.person_key, ("said_no" if o.reply_kind == "said_no" else "stop_reply",
                                                        o.record))
        out = dict(people)
        for key, stop in stops.items():
            if key in out:
                out[key] = replace(out[key], no_go=(stop,))
        return out

    def done_set(self, touches: dict[str, TouchIn]) -> tuple[str, ...]:
        done = {t.company_key for t in touches.values() if any(o.kind in ("interview", "offer") for o in t.outcomes)}
        done |= {a.company_key for a in self.apps.values()
                 if a.outcome in ("interview", "offer") or any(o.kind in ("interview", "offer") for o in a.outcomes)}
        known = set(self.companies.keys())
        for name, add in ((self.ov.done_add, True), (self.ov.done_remove, False)):
            for text in sorted(name):
                key = self.companies.canonical(text)
                if key not in known:
                    try:
                        key = self.companies.lookup(text)
                    except ValueError:
                        key = None
                if key is None:
                    self.issue("done_unknown", None, f"overrides:{'done_add' if add else 'done_remove'}",
                               f"[{'done_add' if add else 'done_remove'}] names {text!r}, which is no company here.")
                    continue
                (done.add if add else done.discard)(key)
        return tuple(sorted(done))

    def freeze_companies(self, done: tuple[str, ...]) -> dict[str, CompanyIn]:
        out = {}
        for key in self.companies.keys():
            c = self.c(key)
            close = DONE_CLOSE if key in done else c.close
            out[key] = CompanyIn(key=key, name=self.companies.name(key), names=self.companies.names(key),
                                 domain=c.domain, boards=tuple(dict.fromkeys(c.boards)), evidence=tuple(c.evidence),
                                 hand=tuple(c.hand), no_go=tuple(dict.fromkeys(c.no_go)), close=close,
                                 notes=tuple(c.notes), records=self.companies.records(key))
        return out

    def settle_overrides(self):
        """His [skip_record]: a settleable hold becomes a skip; any other hold stays and says why."""
        for rid, why in sorted(self.ov.skip_record.items()):
            code = self.held_code.get(rid)
            if code is None:
                self.issue("skip_record_unused", None, rid, f"[skip_record] names {rid}, which is not held.")
                continue
            for i, issue in enumerate(self.issues):
                if issue.record == rid and issue.code == code and issue.holds:
                    if code in SETTLEABLE:
                        self.issues[i] = replace(issue, holds=False, detail=f"{issue.detail} Skipped by "
                                                                            f"[skip_record]: {why}")
                    else:
                        self.issues[i] = replace(issue, detail=f"{issue.detail} [skip_record] cannot settle "
                                                               f"{code}; fix it at the source.")
            if code in SETTLEABLE:
                self.outcomes[rid] = RecordOutcome("skipped", "by_override", None, None)

    def finish_outcomes(self, companies, people, touches) -> dict[str, tuple[SourceRecord, RecordOutcome]]:
        records_of = {
            "company": lambda k: companies[k].records,
            "person": lambda k: people[k].records,
            "outreach_touch": lambda k: touches[k].records,
            "application": lambda k: self.apps[k].records,
            "posting": lambda k: self.postings[k].records,
        }
        for rid, target in self.radar_merged.items():
            skey = self.radar_postings.get(target)
            if skey in self.postings:
                self.outcomes[rid] = RecordOutcome("merged", "same_posting", "posting", skey)
            else:
                self.hold(rid, "unparsed_record", f"{rid} was merged in radar.db into a posting it does not list.")
        out = {}
        for rid, record in self.input.items():
            outcome = self.outcomes.get(rid)
            if outcome is None:
                claims = [(e, self.alias.get(k, k) if e == "outreach_touch" else
                           self.people.resolve(k) if e == "person" else k) for e, k in self.claims.get(rid, [])]
                if not claims:
                    raise RuntimeError(f"the planner gave {rid} no outcome")
                first = next(((e, k) for e, k in claims if records_of[e](k)[:1] == (rid,)), None)
                if first:
                    outcome = RecordOutcome("imported", "", first[0], first[1])
                else:
                    outcome = RecordOutcome("merged", _MERGE_REASON[claims[0][0]], claims[0][0], claims[0][1])
            out[rid] = (record, outcome)
        return out
