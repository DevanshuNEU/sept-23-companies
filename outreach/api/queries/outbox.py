"""The Outbox: one row per email thread, its group, its place in the list, and one thread in full.

A row shows the thread's newest message that is not a bare planned follow-up (a skipped follow-up gives way to the
message before it). The group follows the web fixture's `deriveGroup`: a bounced or closed thread is Closed, then a
reply wins, then an unsent email at a paused company is Paused, then the message state decides. One more rule
stands in for the fixture's closing of touches: an unsent email at a closed or removed company is Closed. Every
company is read as its survivor (`merged_into` to its end), with the sender's own hold (`company_holds`).

An approved row's `schedule` is the sender's own verdict (`outreach.sending.status.queue_schedule`): its reason is
one of the queue's reasons and its time is the queue's. A draft has no schedule: the sender only judges what is
approved. Nothing here writes.
"""
import difflib
import json
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from outreach.api.errors import ApiProblem
from outreach.api.models.checks import Check
from outreach.api.models.company import CompanyRef, ThreadMessage
from outreach.api.models.outbox import (
    ClosedBreakdown, CompareMatch, CompareSide, DraftCompare, HeldMessage, NextDue, OutboxCounts, OutboxDetail,
    OutboxFollowup, OutboxHook, OutboxItem, OutboxReply, OutboxView, Schedule,
)
from outreach.api.queries import people as people_queries
from outreach.api.queries.company import CLOSE_WORDS, Holds, company_holds, person_ref, rank_postings, role_to_name
from outreach.api.queries.outcomes import company_refs, people_by_ids
from outreach.api.queries.sending import moment, sending_status
from outreach.drafts.checks import HOOK_DATE
from outreach.drafts.followups import company_map, resolve_company
from outreach.sending import calendar as cal
from outreach.sending.config import SendConfig
from outreach.sending.queue import REASON_TEXT, TIME_REASONS, load_queue
from outreach.sending.status import queue_schedule
from outreach.store.timefmt import ISO_FMT

GROUPS = ("replied", "review", "approved", "sending", "checking", "sent", "paused", "closed")
GROUP_INDEX = {g: i for i, g in enumerate(GROUPS)}
UNSENT = ("drafted", "approved", "planned")  # what a pause holds and a closed company stops
WAITING = ("drafted", "approved")  # what a hold keeps back: the emails listed on the holding thread
CLAIMED = ("sending", "send_unknown")
STATE_GROUP = {"drafted": "review", "planned": "review", "approved": "approved", "sending": "sending",
               "send_unknown": "checking", "sent": "sent"}
GMAIL_THREAD = "https://mail.google.com/mail/u/0/#all/{}"
PREVIEW_CHARS = 140
COMPARE_MIN_MATCH = 12  # shorter shared stretches are words in common, not copied text
NOT_SHOWN = "planned"


def derive_group(state: str, touch_state: str, company: CompanyRef) -> str:
    """The fixture's `deriveGroup`, with the closed-company rule above."""
    if touch_state in ("bounced", "closed"):
        return "closed"
    if touch_state == "replied":
        return "replied"
    unsent = state in UNSENT
    if unsent and company.status in ("closed", "removed"):
        return "closed"
    if unsent and company.paused_at:
        return "paused"
    return STATE_GROUP.get(state, "closed")


def current_message(messages: list[sqlite3.Row]) -> sqlite3.Row | None:
    """The message a row shows: the newest that is neither planned nor skipped, else the newest skipped one."""
    live = [m for m in messages if m["state"] not in (NOT_SHOWN, "skipped")]
    if live:
        return live[-1]
    skipped = [m for m in messages if m["state"] == "skipped"]
    return skipped[-1] if skipped else None


def stored_checks(text: str) -> list[Check]:
    return [Check.model_validate(c) for c in json.loads(text or "[]")]


def _ny(iso: str, cfg: SendConfig) -> datetime:
    return moment(iso).astimezone(ZoneInfo(cfg.day_tz))


def schedule_text(reason: str, when: str | None, now: str, cfg: SendConfig) -> str:
    """`Sends 10:42 ET` when it is ready, `Sends after 11:05 ET` later today, `Sends tomorrow`, `Sends Wed Nov 11`;
    a wait with no time (and every stop) is the queue's own sentence."""
    if when is None or (reason != "ready" and reason not in TIME_REASONS):
        return REASON_TEXT[reason]
    at, today = _ny(when, cfg), _ny(now, cfg).date()
    if at.date() == today:
        clock = f"{at.hour}:{at.minute:02d} ET"
        return f"Sends {clock}" if reason == "ready" else f"Sends after {clock}"
    if at.date() == today + timedelta(days=1):
        return "Sends tomorrow"
    return f"Sends {at:%a %b} {at.day}"


def _company_sends_today(conn: sqlite3.Connection, companies: dict, now: str, cfg: SendConfig) -> dict[int, int]:
    """Emails out to each surviving company today (New York), counting a claimed one that may have gone out."""
    start, end = cal.ny_day_bounds(cal.ny_date(moment(now), cfg), cfg)
    out: dict[int, int] = defaultdict(int)
    for row in conn.execute(
            "SELECT p.company_id FROM message m JOIN touch t ON t.touch_id = m.touch_id "
            "JOIN person p ON p.person_id = t.person_id WHERE (m.state = 'sent' AND m.sent_at >= ? AND m.sent_at < ?) "
            "OR (m.state IN ('sending', 'send_unknown') AND m.claimed_at >= ? AND m.claimed_at < ?)",
            (start, end, start, end)):
        out[resolve_company(companies, row["company_id"])[-1]] += 1
    return out


def _first_line(text: str, skip_greeting: bool) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if skip_greeting and len(lines) > 1 and lines[0].endswith(","):
        lines = lines[1:]
    line = lines[0] if lines else ""
    return line if len(line) <= PREVIEW_CHARS else line[:PREVIEW_CHARS - 3].rstrip() + "..."


class _Outbox:
    """Everything the list and one thread read, loaded once per request (one query per table)."""

    def __init__(self, conn: sqlite3.Connection, now: str, cfg: SendConfig, *, old_sender_loaded: bool):
        self.conn, self.now, self.cfg = conn, now, cfg
        self.touches = conn.execute(
            "SELECT t.*, p.company_id FROM touch t JOIN person p ON p.person_id = t.person_id "
            "ORDER BY t.touch_id").fetchall()
        self.messages: dict[int, list[sqlite3.Row]] = defaultdict(list)
        for m in conn.execute(
                "SELECT message_id, touch_id, seq, state, subject, due_on, checks, approved_at, claimed_at, sent_at, "
                "updated_at, revision, error FROM message ORDER BY touch_id, seq"):
            self.messages[m["touch_id"]].append(m)
        self.replies: dict[int, sqlite3.Row] = {}  # the newest human reply of each thread
        self.bounces: dict[int, sqlite3.Row] = {}
        for i in conn.execute("SELECT touch_id, kind, body_text, received_at, from_addr FROM inbound_message "
                              "WHERE kind IN ('human', 'bounce') ORDER BY received_at, inbound_id"):
            (self.replies if i["kind"] == "human" else self.bounces)[i["touch_id"]] = i
        self.companies = company_map(conn)
        self.close_reasons = dict(conn.execute(
            "SELECT company_id, close_reason FROM company WHERE close_reason IS NOT NULL").fetchall())
        self.survivor = {t["touch_id"]: resolve_company(self.companies, t["company_id"])[-1] for t in self.touches}
        self.holds: Holds = company_holds(conn, self.companies)
        self.refs = company_refs(conn, set(self.survivor.values()), holds=self.holds)
        self.people = people_by_ids(conn, {t["person_id"] for t in self.touches})
        self.schedule = queue_schedule(conn, moment(now), cfg, old_sender_loaded=old_sender_loaded)
        self.queue_order = {mid: n for n, mid in enumerate(self.schedule)}
        self.tz = {q.message_id: q.tz for q in load_queue(conn, cfg)}
        self.sends_today = _company_sends_today(conn, self.companies, now, cfg)
        self.waiting: dict[int, list[tuple[sqlite3.Row, sqlite3.Row]]] = defaultdict(list)  # survivor: (touch, message)
        for t in self.touches:
            if t["state"] == "open":
                for m in self.messages[t["touch_id"]]:
                    if m["state"] in WAITING:
                        self.waiting[self.survivor[t["touch_id"]]].append((t, m))

    # ------------------------------------------------------------------------------------------ one row

    def held_by(self, touch: sqlite3.Row) -> list[tuple[sqlite3.Row, sqlite3.Row]]:
        """The emails a replied thread holds at its company (none when the company is not held)."""
        survivor = self.survivor[touch["touch_id"]]
        if touch["state"] != "replied" or survivor not in self.holds.held:
            return []
        return [(t, m) for t, m in self.waiting[survivor] if t["touch_id"] != touch["touch_id"]]

    def _schedule(self, m: sqlite3.Row, survivor: int) -> Schedule | None:
        verdict = self.schedule.get(m["message_id"]) if m["state"] == "approved" else None
        if verdict is None:
            return None
        when = self.now if verdict.reason == "ready" else (
            verdict.earliest_at.strftime(ISO_FMT) if verdict.earliest_at is not None else None)
        sent = self.sends_today.get(survivor, 0)
        return Schedule(earliest_at=when, tz=self.tz.get(m["message_id"], self.cfg.day_tz), reason=verdict.reason,
                        text=schedule_text(verdict.reason, when, self.now, self.cfg), company_sent_today=sent,
                        company_slots_left=max(0, self.cfg.per_company_daily - sent))

    def _summary(self, group: str, touch, m, company: CompanyRef, checks: list[Check], schedule, next_due) -> str:
        if group == "replied":
            held = len(self.held_by(touch))
            if held:
                return f"Holding {held} queued email{'' if held == 1 else 's'} at {company.name}"
            return f"Replied {people_queries.day(touch['replied_at'])}" if touch["replied_at"] else "Replied"
        if group == "review":
            for severity in ("block", "warn"):
                found = next((c for c in checks if c.severity == severity), None)
                if found is not None:
                    return found.title
            return "All checks passed" if checks else "Not checked yet"
        if group == "approved":
            return schedule.text if schedule is not None else "Approved"
        if group == "sending":
            return "Sending now: it cannot be edited, approved or skipped"
        if group == "checking":
            return "Gmail did not answer. The sender is looking for it in Sent"
        if group == "sent":
            if next_due is not None:
                on = date.fromisoformat(next_due.on)
                return f"Follow-up {next_due.seq} due {on:%a %b} {on.day}"
            return f"Sent {people_queries.day(m['sent_at'])}" if m["sent_at"] else "Sent"
        if group == "paused":
            return "Paused by you"
        if touch["state"] == "bounced":
            return "Bounced"
        if company.status in ("closed", "removed") and m["state"] in UNSENT:
            if company.status == "removed":
                return "Company removed"
            return f"Company closed: {CLOSE_WORDS.get(self.close_reasons.get(company.id, ''), 'closed')}"
        if touch["state"] == "closed":
            return f"Closed: {people_queries.CLOSE_WORDS.get(touch['close_reason'], 'closed')}"
        if m["state"] == "skipped":
            return "Skipped"
        return f"Failed: {m['error']}" if m["state"] == "failed" and m["error"] else "Failed"

    def _at(self, group: str, touch, m, schedule) -> str:
        if group == "replied" and touch["replied_at"]:
            return touch["replied_at"]
        if group == "approved" and schedule is not None and schedule.earliest_at:
            return schedule.earliest_at
        if m["state"] in CLAIMED and m["claimed_at"]:
            return m["claimed_at"]
        if group == "closed" and (touch["bounced_at"] or touch["closed_at"]):
            return touch["bounced_at"] or touch["closed_at"]
        return m["sent_at"] or m["updated_at"]

    def _next_due(self, touch_id: int, seq: int) -> NextDue | None:
        later = [m for m in self.messages[touch_id] if m["seq"] > seq and m["state"] in UNSENT and m["due_on"]]
        return NextDue(seq=later[0]["seq"], on=later[0]["due_on"]) if later else None

    def item(self, touch: sqlite3.Row) -> tuple[OutboxItem, sqlite3.Row] | None:
        m = current_message(self.messages[touch["touch_id"]])
        if m is None:
            return None
        survivor = self.survivor[touch["touch_id"]]
        company = self.refs[survivor]
        group = derive_group(m["state"], touch["state"], company)
        checks = stored_checks(m["checks"])
        schedule = self._schedule(m, survivor)
        next_due = self._next_due(touch["touch_id"], m["seq"]) if group == "sent" else None
        reply = self.replies.get(touch["touch_id"])
        preview = _first_line(reply["body_text"], True) if group == "replied" and reply is not None else m["subject"]
        blocks = sum(1 for c in checks if c.severity == "block")
        warns = sum(1 for c in checks if c.severity == "warn")
        item = OutboxItem(
            touch_id=touch["touch_id"], message_id=m["message_id"], seq=m["seq"], state=m["state"],
            touch_state=touch["state"], group=group, sort_key="", person=person_ref(self.people[touch["person_id"]]),
            company=company, subject=m["subject"], preview=preview,
            summary=self._summary(group, touch, m, company, checks, schedule, next_due),
            worst=None if not checks else "block" if blocks else "warn" if warns else "pass", block_count=blocks,
            warn_count=warns, at=self._at(group, touch, m, schedule), schedule=schedule, next_due=next_due,
            revision=m["revision"])
        return item, m

    # ------------------------------------------------------------------------------------------ the list

    def items(self) -> list[OutboxItem]:
        """Every row with its `sortKey`: replies newest first, drafts newest first, the approved queue by when the
        sender will send (the queue's own order breaks ties), then the rest newest first."""
        built = [x for x in (self.item(t) for t in self.touches) if x is not None]
        far = "9999-12-31T00:00:00Z"

        def key(pair):
            item, m = pair
            if item.group == "approved":
                when = item.schedule.earliest_at if item.schedule is not None and item.schedule.earliest_at else far
                return GROUP_INDEX[item.group], when, self.queue_order.get(m["message_id"], len(self.queue_order))
            return GROUP_INDEX[item.group], "", 0

        built.sort(key=lambda pair: pair[0].touch_id, reverse=True)
        built.sort(key=lambda pair: pair[0].at if pair[0].group != "approved" else "", reverse=True)
        built.sort(key=key)
        for rank, (item, _) in enumerate(built):
            item.sort_key = f"{GROUP_INDEX[item.group]}.{rank:05d}"
        return [item for item, _ in built]

    # ------------------------------------------------------------------------------------------ one thread

    def detail(self, touch_id: int, item: OutboxItem) -> OutboxDetail:
        conn = self.conn
        touch = next(t for t in self.touches if t["touch_id"] == touch_id)
        m = conn.execute("SELECT * FROM message WHERE message_id = ?", (item.message_id,)).fetchone()
        hook = m["hook"].strip()
        found = HOOK_DATE.search(hook)
        on = None
        if found is not None:
            try:
                on = date.fromisoformat(found.group(1)).isoformat()
                hook = hook[:found.start()].rstrip()
            except ValueError:
                on = None
        person = self.people[touch["person_id"]]
        reply = None
        if touch["replied_at"] is not None:
            got = self.replies.get(touch_id)
            reply = OutboxReply(from_=person["name"], at=got["received_at"] if got else touch["replied_at"],
                                text=got["body_text"] if got else "", kind=touch["reply_kind"] or "unsorted")
        bounce = self.bounces.get(touch_id)
        return OutboxDetail(
            as_of=self.now, item=item, body=m["body"], hook=OutboxHook(text=hook, on=on), project=m["project"],
            role=self._role(self.survivor[touch_id]), checks=stored_checks(m["checks"]), saved_by=m["saved_by"],
            saved_at=m["updated_at"],
            followups=[OutboxFollowup(seq=f["seq"], due_on=f["due_on"], sent_at=f["sent_at"], state=f["state"])
                       for f in self.messages[touch_id] if f["seq"] >= 1],
            first_sent_at=touch["first_sent_at"],
            gmail_thread_url=GMAIL_THREAD.format(touch["gmail_thread_id"]) if touch["gmail_thread_id"] else None,
            reply=reply, touch_revision=touch["revision"],
            held=[HeldMessage(touch_id=t["touch_id"], person_name=self.people[t["person_id"]]["name"], state=h["state"],
                              seq=h["seq"], due_on=h["due_on"]) for t, h in self.held_by(touch)],
            thread=self._thread(touch),
            bounce_reason=(_first_line(bounce["body_text"], False) or "Bounced") if bounce is not None
            else ("Bounced" if touch["state"] == "bounced" else None))

    def _thread(self, touch: sqlite3.Row) -> list[ThreadMessage]:
        """Sent emails and what came back, in time order: the company page's own `thread`."""
        data = people_queries.PeopleData()
        tid = touch["touch_id"]
        data.messages_by_touch[tid] = self.conn.execute(
            "SELECT seq, state, subject, body, sent_at FROM message WHERE touch_id = ? ORDER BY seq", (tid,)).fetchall()
        data.inbound_by_touch[tid] = self.conn.execute(
            "SELECT from_addr, subject, body_text, received_at FROM inbound_message WHERE touch_id = ? "
            "ORDER BY received_at, inbound_id", (tid,)).fetchall()
        data.sender_account = self.conn.execute("SELECT account FROM sender WHERE sender_id = 1").fetchone()["account"]
        return [ThreadMessage(**row) for row in people_queries.thread(data, touch)]

    def _role(self, survivor: int):
        family = [cid for cid in self.companies if resolve_company(self.companies, cid)[-1] == survivor]
        marks = ",".join("?" * len(family))
        postings = self.conn.execute(f"SELECT * FROM posting WHERE company_id IN ({marks})", family).fetchall()
        decisions = {r["posting_id"]: r for r in self.conn.execute(
            f"SELECT d.* FROM posting_decision d JOIN posting p ON p.posting_id = d.posting_id "
            f"WHERE p.company_id IN ({marks})", family)}
        return role_to_name(rank_postings(postings, decisions, set(), self.conn)[1])  # no flags: no reader calls


def outbox_view(conn: sqlite3.Connection, now: str, cfg: SendConfig, *, old_sender_loaded: bool) -> OutboxView:
    box = _Outbox(conn, now, cfg, old_sender_loaded=old_sender_loaded)
    items = box.items()
    counts, breakdown = OutboxCounts(), ClosedBreakdown()
    for item in items:
        setattr(counts, item.group, getattr(counts, item.group) + 1)
        if item.group != "closed":
            continue
        touch_close = next(t["close_reason"] for t in box.touches if t["touch_id"] == item.touch_id)
        if item.touch_state == "bounced":
            breakdown.bounced += 1
        elif item.company.status in ("closed", "removed") or touch_close == "company_closed":
            breakdown.company_closed += 1
        elif item.state == "skipped" or touch_close in ("skipped", "no_go"):
            breakdown.skipped += 1
    return OutboxView(as_of=now, sending=sending_status(conn, now, cfg, old_sender_loaded=old_sender_loaded),
                      items=items, counts=counts, closed_breakdown=breakdown)


def outbox_detail(conn: sqlite3.Connection, touch_id: int, now: str, cfg: SendConfig, *,
                  old_sender_loaded: bool) -> OutboxDetail | None:
    """One thread, with the row exactly as the list shows it (its `sortKey` too). None when there is no such touch
    or it has nothing but planned follow-ups."""
    box = _Outbox(conn, now, cfg, old_sender_loaded=old_sender_loaded)
    item = next((i for i in box.items() if i.touch_id == touch_id), None)
    return box.detail(touch_id, item) if item is not None else None


def compare(conn: sqlite3.Connection, message_id: int, other_id: int) -> DraftCompare:
    """Two drafts to people at one company, side by side: how alike they are (the near-identical check's measure,
    case folded, difflib without autojunk) and the stretches they share."""
    if message_id == other_id:
        raise ApiProblem("bad_request", "Pick two different drafts to compare.")
    rows = {r["message_id"]: r for r in conn.execute(
        "SELECT m.message_id, m.body, p.name, p.company_id FROM message m JOIN touch t ON t.touch_id = m.touch_id "
        "JOIN person p ON p.person_id = t.person_id WHERE m.message_id IN (?, ?)", (message_id, other_id))}
    for mid in (message_id, other_id):
        if mid not in rows:
            raise ApiProblem("not_found", f"There is no draft {mid}.")
    companies = company_map(conn)
    a, b = rows[message_id], rows[other_id]
    if resolve_company(companies, a["company_id"])[-1] != resolve_company(companies, b["company_id"])[-1]:
        raise ApiProblem("bad_request", "Only two drafts to people at the same company can be compared.")
    left, right = a["body"].lower(), b["body"].lower()
    if len(left) != len(a["body"]) or len(right) != len(b["body"]):
        left, right = a["body"], b["body"]  # a letter whose lower case is longer would shift every span
    matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
    return DraftCompare(
        a=CompareSide(message_id=message_id, person_name=a["name"], body=a["body"]),
        b=CompareSide(message_id=other_id, person_name=b["name"], body=b["body"]),
        similarity=round(matcher.ratio(), 4),
        matches=[CompareMatch(a_start=i, a_end=i + n, b_start=j, b_end=j + n)
                 for i, j, n in matcher.get_matching_blocks() if n >= COMPARE_MIN_MATCH])
