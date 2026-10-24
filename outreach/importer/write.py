"""The writer: an `ImportPlan` into the store, through the foundation's write helpers and #31's identity.

`apply_plan` refuses a plan with a held record before it writes anything. Then it runs one `job_run` `import` and
writes one company per transaction: the company (through `identity.resolve_candidate`), its aliases, hand
evidence, no-gos, close and notes, its boards and postings, its people, touches and applications, its review items
and its `import_record` rows. A failure keeps every finished company and ends the run `partial` with the error; a
rerun picks up where it stopped, because every write is found again before it is made:

- a company by its `company_source` link (source `import`, the company's first record id);
- a person, an email touch's `touch` row and an application by their `imported` event (`import:<kind>:<plan key>`);
- an outreach touch by its idempotency key (`import:<touch key>`) or, for email, by its email touch;
- a board by `(ats, token)`, a posting by `source_key`, a message by `(touch, seq)`;
- an outcome by its column (the same value is skipped; another stored value is kept and raises
  `outcome_conflict`); evidence, events, review items, wins and import records by their unique keys.

So a second run on the same sources adds only its `job_run` row. Nothing here sends, approves, writes the `sender`
row or `send_day`, or opens a source.
"""
from __future__ import annotations

import importlib
import re
import sqlite3
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from outreach.identity import follow_merged, raise_merge_candidate, resolve_candidate
from outreach.importer import IMPORTER_VERSION
from outreach.importer.model import ApplicationIn, CompanyIn, IssueIn, OutcomeIn, PersonIn, TouchIn
from outreach.importer.plan import ImportPlan
from outreach.importer.vocab import noon_ny, ny_date
from outreach.outcomes.context import build_context
from outreach.review import raise_item
from outreach.review.words import clean
from outreach.rules.facts import EvidenceDraft
from outreach.rules.rebuild import rebuild
from outreach.sources.base import Candidate
from outreach.sources.boards import BoardRef, add_board
from outreach.sources.keys import US_STATES
from outreach.sources.tiers import register_source
from outreach.store import (add_alias, append_event, body_sha256, describe_error, finish_job, iso_utc, log_touch,
                            name_key, record_outcome, start_job, transaction)

__all__ = ["WriteResult", "apply_plan", "IMPORT_LANE", "IMPORT_SOURCE_KEY", "PLACEHOLDER_RFC"]

IMPORT_LANE = "own"  # imported touches have no lane; the API and the sender use "own" for that
IMPORT_SOURCE_KEY = "importSource"  # the API reads it from the touch's `imported` event (one constant)
PLACEHOLDER_RFC = "<import-{contact}-{seq}@outreach.invalid>"  # never put in a header (#14 skips the domain)
_OUTCOME_COLUMNS = {"accepted": "accepted_at", "replied": "replied_at", "referred": "referred_at",
                    "interview": "interview_at", "offer": "offer_at"}
_WINS = ("accepted", "referred", "interview", "offer")
_STATE = re.compile(r",\s*([A-Z]{2})\b")
_COMBINE = "outreach.reader.combine"


@dataclass(frozen=True)
class WriteResult:
    run_id: int
    ids: dict[str, int]  # "company:<key>", "person:<key>", "touch:<key>", "application:<key>", "posting:<key>",
    # "board:<ats>:<token>" to the store id
    written: dict[str, int]  # rows added per table
    status: str = "ok"  # ok, or partial when a company failed (`error` says which and why)
    error: str = ""


def apply_plan(conn: sqlite3.Connection, plan: ImportPlan, *, now: str) -> WriteResult:
    held = plan.held()
    if held:
        raise ValueError(f"the plan holds {len(held)} record(s), first {held[0]}; settle them before the real run")
    return _Writer(conn, plan, iso_utc(now)).run()


def _tables(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {t: conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in _tables(conn)}


def _event_id(conn: sqlite3.Connection, key: str) -> int | None:
    row = conn.execute("SELECT entity_id FROM event WHERE idempotency_key = ?", (key,)).fetchone()
    return row[0] if row else None


class _Writer:
    def __init__(self, conn: sqlite3.Connection, plan: ImportPlan, now: str):
        self.conn, self.plan, self.now = conn, plan, now
        self.ids: dict[str, int] = {}
        self.new_postings = 0
        self.unplaced: list[str] = []
        self.by_company: dict[str, dict[str, list]] = {k: {"touches": [], "apps": [], "people": [], "postings": [],
                                                           "issues": [], "records": []} for k in plan.companies}
        for t in plan.touches.values():
            self.by_company[t.company_key]["touches"].append(t)
        for a in plan.applications.values():
            self.by_company[a.company_key]["apps"].append(a)
        for p in plan.people.values():
            self.by_company[p.company_key]["people"].append(p)
        for p in plan.postings.values():
            self.by_company[p.company_key]["postings"].append(p)
        self.issue_keys = self._issue_keys(plan.issues)
        for index, issue in enumerate(plan.issues):
            if issue.code == "near_name":
                continue
            if issue.company_key in self.by_company:
                self.by_company[issue.company_key]["issues"].append(index)
            else:
                self.unplaced.append(f"{issue.code} {issue.record}: {issue.detail}")
        self.rest: list[str] = []
        for record_id, (_, outcome) in plan.records.items():
            home = self._home(outcome.entity, outcome.entity_key)
            (self.by_company[home]["records"] if home else self.rest).append(record_id)

    # -- the run --

    def run(self) -> WriteResult:
        conn = self.conn
        before = _counts(conn)
        self.run_id = start_job(conn, "import")
        status, error = "ok", ""
        try:
            with transaction(conn):
                register_source(conn, "import", IMPORTER_VERSION, self.run_id)
            for key in self.plan.companies:
                snapshot = dict(self.ids)
                try:
                    with transaction(conn):
                        self.write_company(key)
                except Exception as err:
                    self.ids = snapshot
                    status, error = "partial", f"company {key}: {describe_error(err)}"
                    break
            if status == "ok":
                with transaction(conn):
                    self.near_names()
                    self.records(self.rest)
                if self.new_postings:
                    self.rules()
        except Exception as err:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            status, error = "partial", describe_error(err)
        after = _counts(conn)
        written = {t: n - before.get(t, 0) for t, n in after.items() if n != before.get(t, 0)}
        written["job_run"] = written.get("job_run", 0)
        summary = {"records": len(self.plan.records), "written": written, "done_set": list(self.plan.done_set),
                   "issues": dict(Counter(i.code for i in self.plan.issues)),
                   "followups_not_planned": self.plan.followups_not_planned, "unplaced_issues": self.unplaced}
        finish_job(conn, self.run_id, status, summary=summary, error=error)
        return WriteResult(self.run_id, dict(self.ids), {k: v for k, v in written.items() if v}, status, error)

    def rules(self) -> None:
        """The rules over the new postings: `rebuild`, with the reader's combiner when it exists."""
        try:
            combine = importlib.import_module(_COMBINE)
        except ModuleNotFoundError as err:
            if err.name not in (_COMBINE, "outreach.reader"):
                raise
            combine = None
        if combine is None:
            rebuild(self.conn)
        else:
            rebuild(self.conn, combiner=combine.reader_combiner(self.conn))
            combine.apply_all(self.conn)

    def write_company(self, key: str) -> None:
        group = self.by_company[key]
        company_id = self.company_id(key)
        c = self.plan.companies[key]
        self.company_details(c, company_id)
        for ref in c.boards:
            self.board(ref, company_id)
        for posting in group["postings"]:
            self.posting(posting)
        for person in group["people"]:
            self.person_id(person.key)
        for touch in group["touches"]:
            self.touch(touch)
        for app in group["apps"]:
            self.application(app)
        self.status(c, company_id, bool(group["touches"] or group["apps"]))
        for index in group["issues"]:
            self._flag(company_id, self.issue_keys[index], self.plan.issues[index].detail)
        self.records(group["records"])

    # -- companies --

    def company_id(self, key: str) -> int:
        found = self.ids.get(f"company:{key}")
        if found is not None:
            return found
        c = self.plan.companies[key]
        drafts = [d for d in c.evidence if d.source_kind != "hand"]
        if c.domain:
            kind = "old_dashboard" if c.records[0].startswith("data_js:") else "other"
            drafts.append(EvidenceDraft("domain", c.domain, "estimate", kind, f"import:{c.records[0]}"))
        keys = {"ats": f"{c.boards[0][0]}:{c.boards[0][1]}"} if c.boards else {}
        cand = Candidate(name=c.name, domain=c.domain, keys=keys, evidence=tuple(drafts), source_ref=c.records[0],
                         state=self._state(c))
        resolution = resolve_candidate(self.conn, cand, source_name="import", actor="import")
        company_id = follow_merged(self.conn, resolution.company_id)
        self.ids[f"company:{key}"] = company_id
        return company_id

    @staticmethod
    def _state(c: CompanyIn) -> str | None:
        for d in c.evidence:
            if d.fact == "hq" and (m := _STATE.search(d.value)) and m.group(1) in US_STATES:
                return m.group(1)
        return None

    def company_details(self, c: CompanyIn, company_id: int) -> None:
        conn = self.conn
        for name in sorted(c.names):
            if not name_key(name):
                continue
            try:
                add_alias(conn, company_id, name, "import")
            except ValueError as err:
                self._flag(company_id, f"import:alias_conflict:{name_key(name)}",
                           f"The import has {name!r} as a name of {c.name}, but that name already belongs to another "
                           f"company ({err}). Nothing was moved; merge them if they are one.")
        for d in c.hand:
            ref = d.source_ref or f"import:{c.records[0]}"
            exists = conn.execute(
                "SELECT 1 FROM evidence WHERE company_id = ? AND fact = ? AND value = ? AND source_ref = ? "
                "AND locked = 1", (company_id, d.fact, d.value, ref)).fetchone()
            if exists:
                continue
            conn.execute(
                "INSERT INTO evidence(company_id, fact, value, num_low, num_high, confidence, locked, source_kind, "
                "source_ref, quote, observed_on, recorded_at) VALUES (?, ?, ?, ?, ?, ?, 1, 'hand', ?, ?, ?, ?)",
                (company_id, d.fact, d.value, d.num_low, d.num_high, d.confidence, ref, d.quote, d.observed_on,
                 self.now))
            append_event(conn, entity="evidence", kind="recorded", actor="import", company_id=company_id,
                         detail={"fact": d.fact, "value": d.value, "locked": True, "source_ref": ref}, at=self.now,
                         idempotency_key=f"import:hand:{company_id}:{d.fact}:{d.value}:{ref}")
        for reason, quote in c.no_go:
            self._no_go(company_id=company_id, person_id=None, reason=reason, quote=quote, record=c.records[0])
        for n, (record, text) in enumerate(c.notes):
            same = sum(1 for r, _ in c.notes[:n] if r == record)
            append_event(conn, entity="company", kind="note", actor="import", entity_id=company_id,
                         company_id=company_id, detail={"text": text, "record": record}, at=self.now,
                         idempotency_key=f"import:note:{record}:{same}:{company_id}")

    def _no_go(self, *, company_id: int | None, person_id: int | None, reason: str, quote: str, record: str) -> None:
        """One no-go with source `import`, unless the company or person already has an active one (the store keeps
        one active no-go each, whatever its reason) or he lifted the import's own (his choice: never added back)."""
        column, value = ("company_id", company_id) if person_id is None else ("person_id", person_id)
        ref = f"import:{record}"
        found = self.conn.execute(
            f"SELECT 1 FROM no_go WHERE {column} = ? AND (lifted_at IS NULL OR (reason = ? AND source_ref = ?))",
            (value, reason, ref)).fetchone()
        if found:
            return
        cur = self.conn.execute(
            "INSERT INTO no_go(company_id, person_id, reason, quote, source, source_ref, created_at) "
            "VALUES (?, ?, ?, ?, 'import', ?, ?)", (company_id, person_id, reason, quote, ref, self.now))
        append_event(self.conn, entity="no_go", kind="added", actor="import", entity_id=cur.lastrowid,
                     company_id=company_id, detail={"reason": reason, "source_ref": ref, "personId": person_id},
                     at=self.now, idempotency_key=f"import:no_go:{cur.lastrowid}")

    def status(self, c: CompanyIn, company_id: int, active: bool) -> None:
        """The done set's close (or the old tracker's), else `working` when the company has a touch or an
        application. The import never sets `removed` and never reopens a company he closed."""
        conn = self.conn
        status = conn.execute("SELECT status FROM company WHERE company_id = ?", (company_id,)).fetchone()[0]
        if c.close and status not in ("closed", "removed"):
            conn.execute("UPDATE company SET status = 'closed', close_reason = ?, status_note = ?, status_at = ?, "
                         "paused_at = NULL, revision = revision + 1 WHERE company_id = ?",
                         (c.close[0], c.close[1], self.now, company_id))
            append_event(conn, entity="company", kind="closed", actor="import", entity_id=company_id,
                         company_id=company_id, detail={"reason": c.close[0], "note": c.close[1]}, at=self.now,
                         idempotency_key=f"import:close:{company_id}")
        elif not c.close and active and status in ("candidate", "backlog"):
            conn.execute("UPDATE company SET status = 'working', status_at = ?, revision = revision + 1 "
                         "WHERE company_id = ?", (self.now, company_id))
            append_event(conn, entity="company", kind="status", actor="import", entity_id=company_id,
                         company_id=company_id, detail={"from": status, "to": "working"}, at=self.now,
                         idempotency_key=f"import:working:{company_id}")

    # -- boards and postings --

    def board(self, ref: tuple[str, str], company_id: int) -> None:
        board = self.plan.boards[ref]
        add_board(self.conn, company_id, BoardRef(*ref), via="import", matched_on="import", found_by="import",
                  actor="import", now=self.now)
        row = self.conn.execute("SELECT board_id, active FROM board WHERE ats = ? AND token = ?", ref).fetchone()
        self.ids[f"board:{ref[0]}:{ref[1]}"] = row["board_id"]
        if not board.active and row["active"]:
            self.conn.execute("UPDATE board SET active = 0 WHERE board_id = ?", (row["board_id"],))

    def posting(self, p) -> None:
        conn = self.conn
        board_id, company_id = None, self.company_id(p.company_key)
        if p.board:
            row = conn.execute("SELECT board_id, company_id FROM board WHERE ats = ? AND token = ?",
                               p.board).fetchone()
            if row is None:
                return  # the board was not written (another company claims it, raised by add_board)
            board_id, company_id = row["board_id"], row["company_id"]  # a posting is where its board is
        closed_at = p.last_seen_at or self.now if p.closed else None
        cur = conn.execute(
            "INSERT INTO posting(company_id, board_id, source_key, title, location, url, posted_on, first_seen_at, "
            "last_seen_at, closed_at, close_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(source_key) DO NOTHING",
            (company_id, board_id, p.source_key, p.title, p.location, p.url, p.posted_on,
             p.first_seen_at or self.now, p.last_seen_at or p.first_seen_at or self.now, closed_at,
             "imported_closed" if p.closed else None))
        posting_id = conn.execute("SELECT posting_id FROM posting WHERE source_key = ?", (p.source_key,)).fetchone()[0]
        self.ids[f"posting:{p.source_key}"] = posting_id
        if cur.rowcount:
            self.new_postings += 1
            if p.body:
                conn.execute("INSERT INTO posting_body(posting_id, body, fetched_at, body_sha256) "
                             "VALUES (?, ?, NULL, ?) ON CONFLICT(posting_id) DO NOTHING",
                             (posting_id, p.body, body_sha256(p.body)))

    # -- people --

    def person_id(self, key: str) -> int:
        found = self.ids.get(f"person:{key}")
        if found is not None:
            return found
        conn = self.conn
        p: PersonIn = self.plan.people[key]
        company_id = self.company_id(p.company_key)
        event_key = f"import:person:{key}"
        person_id = _event_id(conn, event_key)
        if person_id is None:
            owner = None
            for column, value in (("email", p.email), ("linkedin_url", p.linkedin_url)):
                if value and owner is None:
                    owner = conn.execute(f"SELECT person_id, company_id FROM person WHERE {column} = ?",
                                         (value,)).fetchone()
            if owner is not None:
                # The address or profile names a person the store already has: that is this person, never a second
                # one (a second row would be a second first email). Another company is raised, not moved.
                person_id = owner["person_id"]
                if follow_merged(conn, owner["company_id"]) != follow_merged(conn, company_id):
                    self._flag(company_id, f"import:person_conflict:{key}",
                               f"{p.name} ({p.email or p.linkedin_url}) is already in the store at another company "
                               f"(person {person_id}); the import's history was put on that person. Check which "
                               f"company they are at.", person_id=None)
                detail = {"reused": True}
            else:
                channel = "linkedin" if p.linkedin_url and not p.email else "email"
                notes = f"Imported from {', '.join(p.records)}." if p.placeholder else ""
                person_id = conn.execute(
                    "INSERT INTO person(company_id, name, title, role_kind, email, linkedin_url, profile_text, notes, "
                    "tz, channel, linkedin_reason, added_by, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'import', ?, ?)",
                    (company_id, p.name, p.title, p.role_kind, p.email, p.linkedin_url, p.profile_text, notes, p.tz,
                     channel, "no_email" if channel == "linkedin" else None, self.now, self.now)).lastrowid
                detail = {"reused": False}
            append_event(conn, entity="person", kind="imported", actor="import", entity_id=person_id,
                         company_id=company_id, detail={**detail, "records": list(p.records)}, at=self.now,
                         idempotency_key=event_key)
        self.ids[f"person:{key}"] = person_id
        for reason, record in p.no_go:
            self._no_go(company_id=None, person_id=person_id, reason=reason, quote="", record=record)
        return person_id

    # -- touches --

    def touch(self, t: TouchIn) -> None:
        conn = self.conn
        company_id = self.company_id(t.company_key)
        person = self.plan.people[t.person_key]
        person_id = self.person_id(t.person_key)
        email_touch_id = None
        if t.channel == "email":
            email_touch_id = self.email_touch(t, person_id, company_id)
            if t.email is None or t.email.sent0_at is None:
                return  # nothing was sent: no outreach touch, the person and the draft are the record
        context = build_context(source="own", lane=IMPORT_LANE, role_kind=person.role_kind, role_age_days=None,
                                ranker_version=None, signals={}, role_named=t.role_named)
        idem = f"import:{t.key}"
        existing = None
        if email_touch_id is not None:
            existing = conn.execute("SELECT touch_id FROM outreach_touch WHERE email_touch_id = ?",
                                    (email_touch_id,)).fetchone()
        if existing is None:
            existing = conn.execute("SELECT touch_id FROM outreach_touch WHERE idempotency_key = ?", (idem,)).fetchone()
        if existing is not None:
            touch_id = existing[0]
        else:
            touch_id = log_touch(conn, person_id=person_id, company_id=company_id, channel=t.channel,
                                 sent_at=t.sent_at, context=context, posting_id=self._posting_id(t.posting_key),
                                 email_touch_id=email_touch_id, idempotency_key=idem, actor="import")
            if t.channel == "linkedin_request":
                self._linkedin(person_id, "request_sent", t.sent_at, None)
        self.ids[f"touch:{t.key}"] = touch_id
        estimated = self.outcomes(touch_id, company_id, t.outcomes, channel=t.channel, person_id=person_id)
        append_event(conn, entity="outreach_touch", kind="imported", actor="import", entity_id=touch_id,
                     company_id=company_id, at=self.now, idempotency_key=f"import:touch-imported:{touch_id}",
                     detail={IMPORT_SOURCE_KEY: t.records[0].split(":", 1)[0], "records": list(t.records),
                             "estimatedDates": estimated or any(o.estimated for o in t.outcomes),
                             "roleNamedKnown": t.role_named_known, "followupsSent": t.followups_sent})

    def email_touch(self, t: TouchIn, person_id: int, company_id: int) -> int | None:
        """The `touch` row (origin `import`) and its messages. A person with a thread the import did not write
        keeps it: the import adds no second one and raises an item."""
        conn = self.conn
        mail = t.email
        row = conn.execute("SELECT * FROM touch WHERE person_id = ?", (person_id,)).fetchone()
        replied = next((o for o in t.outcomes if o.kind == "replied"), None)
        if row is not None and row["origin"] != "import":
            self._flag(company_id, f"import:email_thread_exists:{t.key}",
                       f"{self.plan.people[t.person_key].name} already has an email thread in the store; the import's "
                       f"history ({', '.join(t.records)}) was logged beside it, with no new thread. Check it before "
                       f"approving anything to them.", person_id=person_id)
            return row["touch_id"]
        state = mail.touch_state or "closed"
        sent0 = mail.sent0_at
        fields = {
            "state": state,
            "close_reason": mail.close_reason if state == "closed" else None,
            "replied_at": max(replied.at, sent0 or replied.at) if replied and state == "replied" else None,
            "reply_kind": replied.reply_kind or "unsorted" if replied and state == "replied" else None,
            "bounced_at": mail.bounced_at if state == "bounced" else mail.bounced_at,
            "auto_reply_at": mail.auto_reply_at,
            "closed_at": self.now if state == "closed" else None,
        }
        if state == "closed" and not fields["close_reason"]:
            fields["close_reason"] = "no_reply"
        if state == "bounced" and not fields["bounced_at"]:
            fields["bounced_at"] = sent0 or t.sent_at
        if state == "replied" and not fields["replied_at"]:
            state = fields["state"] = "closed"
            fields.update(close_reason="no_reply", closed_at=self.now, reply_kind=None)
        key = f"import:email-touch:{t.key}"
        if row is None:
            touch_id = conn.execute(
                "INSERT INTO touch(person_id, origin, state, close_reason, gmail_thread_id, first_rfc_id, "
                "first_sent_at, replied_at, reply_kind, bounced_at, auto_reply_at, closed_at, created_at, updated_at) "
                "VALUES (?, 'import', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (person_id, state, fields["close_reason"], mail.thread_id, mail.rfc0 if sent0 else None, sent0,
                 fields["replied_at"], fields["reply_kind"], fields["bounced_at"], fields["auto_reply_at"],
                 fields["closed_at"], self.now, self.now)).lastrowid
            append_event(conn, entity="touch", kind="imported", actor="import", entity_id=touch_id,
                         company_id=company_id, detail={"records": list(t.records), "state": state}, at=self.now,
                         idempotency_key=key)
        else:
            touch_id = row["touch_id"]
            if sent0 and row["first_sent_at"] is None:  # the first email went out after the last import
                conn.execute("UPDATE touch SET first_sent_at = ?, first_rfc_id = ?, gmail_thread_id = "
                             "coalesce(gmail_thread_id, ?), updated_at = ?, revision = revision + 1 "
                             "WHERE touch_id = ? AND first_sent_at IS NULL",
                             (sent0, mail.rfc0, mail.thread_id, self.now, touch_id))
            if row["state"] == "open" and state != "open":  # a new fact (a reply, a bounce); nothing is overwritten
                conn.execute("UPDATE touch SET state = ?, close_reason = ?, replied_at = ?, reply_kind = ?, "
                             "bounced_at = coalesce(bounced_at, ?), closed_at = ?, updated_at = ?, "
                             "revision = revision + 1 WHERE touch_id = ? AND state = 'open'",
                             (state, fields["close_reason"], fields["replied_at"], fields["reply_kind"],
                              fields["bounced_at"], fields["closed_at"], self.now, touch_id))
        self.messages(touch_id, company_id, mail, t)
        return touch_id

    def messages(self, touch_id: int, company_id: int, mail, t: TouchIn) -> None:
        conn = self.conn
        have = {r[0] for r in conn.execute("SELECT seq FROM message WHERE touch_id = ?", (touch_id,))}
        contact = mail.contact_id

        def insert(seq, state, subject, body, *, rfc=None, gmail=None, sent_at=None, due_on=None) -> int:
            return conn.execute(
                "INSERT INTO message(touch_id, seq, state, subject, body, due_on, saved_by, rfc_message_id, "
                "gmail_message_id, sent_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'import', ?, ?, ?, ?, ?)",
                (touch_id, seq, state, subject, body, due_on, rfc, gmail, sent_at, self.now, self.now)).lastrowid

        def placeholder(message_id, seq):
            append_event(conn, entity="message", kind="import_placeholder_rfc", actor="import", entity_id=message_id,
                         company_id=company_id, at=self.now, idempotency_key=f"import:placeholder-rfc:{message_id}",
                         detail={"seq": seq, "why": "the old mailer kept no Message-ID for this email; this one is a "
                                                    "placeholder and is never put in a header"})

        subject0 = mail.subject0 or ""
        self.sent_since(touch_id, company_id, 0, mail.sent0_at, t)
        self.sent_since(touch_id, company_id, 1, mail.sent1_at, t)
        if 0 not in have and contact and mail.body0:
            if mail.sent0_at:
                rfc = mail.rfc0 or PLACEHOLDER_RFC.format(contact=contact, seq=0)
                message_id = insert(0, "sent", subject0 or "(no subject)", mail.body0, rfc=rfc, gmail=mail.gmail_id0,
                                    sent_at=mail.sent0_at)
                if not mail.rfc0:
                    placeholder(message_id, 0)
            else:
                message_id = insert(0, "drafted", subject0 or "(no subject)", mail.body0)
                if mail.touch_state == "closed" and mail.close_reason == "skipped":
                    conn.execute("UPDATE message SET state = 'skipped', updated_at = ? WHERE message_id = ?",
                                 (self.now, message_id))
            if mail.old_approval:
                append_event(conn, entity="message", kind="old_approval", actor="import", entity_id=message_id,
                             company_id=company_id, at=self.now, idempotency_key=f"import:old_approval:{message_id}",
                             detail={"note": "approved in the old mailer; a draft he approves again",
                                     "contact": contact})
        if 1 not in have and mail.sent0_at:
            subject1 = mail.subject1 or (f"Re: {subject0}" if subject0 else "(no subject)")
            if mail.sent1_at:
                message_id = insert(1, "sent", subject1, mail.body1 or "(the old mailer kept no text)",
                                    rfc=mail.rfc1 or PLACEHOLDER_RFC.format(contact=contact or t.key, seq=1),
                                    sent_at=mail.sent1_at, due_on=ny_date(mail.sent1_at))
                if not mail.rfc1:
                    placeholder(message_id, 1)
            elif mail.body1 and mail.due1_on:
                insert(1, "drafted", subject1, mail.body1, due_on=mail.due1_on)
            elif mail.due1_on and mail.touch_state == "open":
                insert(1, "planned", "", "", due_on=mail.due1_on)

    def sent_since(self, touch_id: int, company_id: int, seq: int, sent_at: str | None, t: TouchIn) -> None:
        """A message the store still holds as sendable that the sources say went out (the old mailer sent it after
        the last import): it is made unsendable (`skipped`; the import never approves, so it cannot record the send
        on the message) and an item says so. A message in flight (`sending`, `send_unknown`) is left to the sender
        and the item says that instead."""
        if not sent_at:
            return
        conn = self.conn
        row = conn.execute("SELECT message_id, state FROM message WHERE touch_id = ? AND seq = ?",
                           (touch_id, seq)).fetchone()
        if row is None or row["state"] in ("sent", "skipped"):
            return
        what = "first email" if seq == 0 else "follow-up"
        name = self.plan.people[t.person_key].name
        if row["state"] in ("planned", "drafted", "approved", "failed"):
            conn.execute("UPDATE message SET state = 'skipped', updated_at = ?, revision = revision + 1 "
                         "WHERE message_id = ?", (self.now, row["message_id"]))
            append_event(conn, entity="message", kind="skipped", actor="import", entity_id=row["message_id"],
                         company_id=company_id, at=self.now,
                         idempotency_key=f"import:sent-after-import:{row['message_id']}",
                         detail={"why": "sent by the old mailer after the last import", "sent_at": sent_at,
                                 "was": row["state"]})
            said = (f"The old mailer sent the {what} to {name} on {ny_date(sent_at)}, after the last import, so "
                    f"the store's {row['state']} copy was skipped and can never be sent.")
        else:
            said = (f"The old mailer sent the {what} to {name} on {ny_date(sent_at)}, and the store's copy is "
                    f"{row['state']} in the new sender. Check Gmail Sent before anything else goes to them.")
        self._flag(company_id, f"import:sent_after_import:{touch_id}:{seq}", said,
                   person_id=self.ids.get(f"person:{t.person_key}"))

    def outcomes(self, touch_id: int, company_id: int, outcomes: Iterable[OutcomeIn], *, channel: str,
                 person_id: int | None) -> bool:
        """Each outcome in date order through `record_outcome`; one already stored with the same value is skipped,
        another stored value is kept and raises `outcome_conflict`. Wins the import recorded or confirmed are
        marked seen. Returns whether a date was raised to the send time (an estimate)."""
        conn = self.conn
        raised = False
        for o in sorted(outcomes, key=lambda o: (o.at, o.kind)):
            column = _OUTCOME_COLUMNS.get(o.kind)
            if column is None:
                continue  # auto replies and bounces stay on the email touch
            row = conn.execute("SELECT * FROM outreach_touch WHERE touch_id = ?", (touch_id,)).fetchone()
            if row["undone_at"] is not None:
                continue
            at = o.at
            if at < row["sent_at"]:
                at, raised = row["sent_at"], True
            reply_kind = (o.reply_kind or "unsorted") if o.kind == "replied" else None
            stored = row[column]
            if stored is not None:
                if stored != at or (reply_kind and row["reply_kind"] != reply_kind):
                    said = f"{o.kind} {reply_kind or ''}".strip()
                    had = f"{o.kind} {row['reply_kind'] or ''}".strip() if o.kind == "replied" else o.kind
                    self._flag(company_id, f"import:outcome_conflict:{touch_id}:{o.kind}:{at}:{reply_kind or ''}",
                               f"{o.record} says {said} on {ny_date(at)}; the store has {had} on {ny_date(stored)}, "
                               f"which is kept. Correct it on the touch if the import is right.",
                               person_id=person_id)
                else:
                    self._win(touch_id, o.kind, row["reply_kind"])
                continue
            record_outcome(conn, touch_id, kind=o.kind, at=at, reply_kind=reply_kind, revision=row["revision"],
                           actor="import")
            if channel == "linkedin_request" and o.kind in ("accepted", "replied") and person_id is not None:
                self._linkedin(person_id, o.kind, at, reply_kind if reply_kind != "unsorted" else None)
            self._win(touch_id, o.kind, reply_kind)
        return raised

    def _win(self, touch_id: int, kind: str, reply_kind: str | None) -> None:
        if kind in _WINS or (kind == "replied" and reply_kind == "interested"):
            self.conn.execute("INSERT INTO win_seen(touch_id, kind, seen_at) VALUES (?, ?, ?) "
                              "ON CONFLICT DO NOTHING", (touch_id, kind, self.now))

    def _linkedin(self, person_id: int, kind: str, at: str, reply_kind: str | None) -> None:
        self.conn.execute("INSERT INTO linkedin_log(person_id, kind, reply_kind, at, note, recorded_at) "
                          "VALUES (?, ?, ?, ?, 'imported', ?)", (person_id, kind, reply_kind, at, self.now))

    def _posting_id(self, posting_key: str | None) -> int | None:
        if not posting_key:
            return None
        found = self.ids.get(f"posting:{posting_key}")
        if found is None:
            row = self.conn.execute("SELECT posting_id FROM posting WHERE source_key = ?", (posting_key,)).fetchone()
            found = row[0] if row else None
        return found

    # -- applications --

    def application(self, a: ApplicationIn) -> None:
        conn = self.conn
        company_id = self.company_id(a.company_key)
        posting_id = self._posting_id(a.posting_key)
        key = f"import:application:{a.key}"
        app_id = _event_id(conn, key)
        if app_id is None:
            app_id = conn.execute(
                "INSERT INTO application(company_id, posting_id, applied_on, url, outcome, outcome_at) "
                "VALUES (?, ?, ?, ?, ?, ?)", (company_id, posting_id, a.applied_on, a.url, a.outcome,
                                              a.outcome_at if a.outcome != "open" else None)).lastrowid
            append_event(conn, entity="application", kind="imported", actor="import", entity_id=app_id,
                         company_id=company_id, detail={"records": list(a.records)}, at=self.now, idempotency_key=key)
        else:
            row = conn.execute("SELECT outcome FROM application WHERE application_id = ?", (app_id,)).fetchone()
            if row["outcome"] == "open" and a.outcome != "open":  # a new fact; a stored outcome is never changed
                conn.execute("UPDATE application SET outcome = ?, outcome_at = ?, revision = revision + 1 "
                             "WHERE application_id = ? AND outcome = 'open'", (a.outcome, a.outcome_at, app_id))
        self.ids[f"application:{a.key}"] = app_id
        context = build_context(source="own", lane=IMPORT_LANE, role_kind=None, role_age_days=None,
                                ranker_version=None, signals={}, role_named=posting_id is not None)
        touch_id = log_touch(conn, person_id=None, company_id=company_id, channel="application",
                             sent_at=noon_ny(a.applied_on), context=context, posting_id=posting_id,
                             idempotency_key=f"import:{a.key}", actor="import")
        self.ids[f"touch:{a.key}"] = touch_id
        estimated = self.outcomes(touch_id, company_id, a.outcomes, channel="application", person_id=None)
        append_event(conn, entity="outreach_touch", kind="imported", actor="import", entity_id=touch_id,
                     company_id=company_id, at=self.now, idempotency_key=f"import:touch-imported:{touch_id}",
                     detail={IMPORT_SOURCE_KEY: a.records[0].split(":", 1)[0], "records": list(a.records),
                             "estimatedDates": estimated or any(o.estimated for o in a.outcomes),
                             "roleNamedKnown": posting_id is not None, "followupsSent": 0})

    # -- review items --

    @staticmethod
    def _issue_keys(issues: tuple[IssueIn, ...]) -> dict[int, str]:
        """Each issue's evidence key: its own (`ruled_out`), else `import:<code>:<record>`, with `:<n>` for the
        n-th repeat, so two issues of one record are two items."""
        seen: Counter = Counter()
        keys = {}
        for i, issue in enumerate(issues):
            base = issue.key or f"import:{issue.code}:{issue.record}"
            seen[(base, issue.company_key)] += 1
            n = seen[(base, issue.company_key)]
            keys[i] = base if n == 1 else f"{base}:{n}"
        return keys

    def _flag(self, company_id: int, evidence_key: str, reason: str, *, person_id: int | None = None) -> None:
        raise_item(self.conn, kind="flagged", company_id=company_id, reason=clean(reason), evidence_key=evidence_key,
                   raised_by="import", actor="import", person_id=person_id, now=self.now)

    def near_names(self) -> None:
        for a, b in self.plan.near_names:
            ia, ib = self.ids.get(f"company:{a}"), self.ids.get(f"company:{b}")
            if ia is None or ib is None or ia == ib:
                continue
            raise_merge_candidate(self.conn, ia, ib, "import_near_name",
                                  f"the old sources spell {self.plan.companies[a].name!r} and "
                                  f"{self.plan.companies[b].name!r}; they may be one company", confidence="weak")

    # -- import records --

    def _home(self, entity: str | None, key: str | None) -> str | None:
        plan = self.plan
        if entity == "company":
            return key
        if entity == "person":
            return plan.people[key].company_key
        if entity == "outreach_touch":
            return plan.touches[key].company_key
        if entity == "application":
            return plan.applications[key].company_key
        if entity == "posting":
            return plan.postings[key].company_key
        if entity == "board":
            ats, token = key.split(":", 1)
            return plan.boards[(ats, token)].company_key
        return None

    def _entity_id(self, entity: str, key: str) -> tuple[str, int]:
        prefix = {"company": "company", "person": "person", "outreach_touch": "touch", "application": "application",
                  "posting": "posting", "board": "board"}[entity]
        found = self.ids.get(f"{prefix}:{key}")
        if found is not None:
            return entity, found
        home = self._home(entity, key)  # a posting under another company's board, a touch never logged
        return "company", self.company_id(home)

    def records(self, record_ids: Iterable[str]) -> None:
        for record_id in record_ids:
            record, outcome = self.plan.records[record_id]
            entity, entity_id = (None, None)
            if outcome.entity is not None:
                entity, entity_id = self._entity_id(outcome.entity, outcome.entity_key)
            self.conn.execute(
                "INSERT INTO import_record(run_id, source, source_key, raw, raw_sha256, outcome, reason, entity, "
                "entity_id, imported_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source, source_key, raw_sha256) DO NOTHING",
                (self.run_id, record.source, record.source_key, record.raw_json(), record.raw_sha256(),
                 outcome.outcome, outcome.reason, entity, entity_id, self.now))
