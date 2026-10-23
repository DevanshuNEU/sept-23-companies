"""People, their touches and the plain-words status line of each, for the company page and the search index.

One query per table, grouped in Python: a company with 50 people costs the same number of statements as one with 1.
"""
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from outreach.store.timefmt import ISO_FMT

NY = ZoneInfo("America/New_York")
LINKEDIN_STEPS = ("request_sent", "accepted", "message_sent", "replied")
CLOSE_WORDS = {"no_reply": "no reply", "skipped": "skipped", "company_closed": "company closed", "no_go": "no-go"}


def day(iso: str) -> str:
    """`Nov 5` on the New York calendar (the sender's own day)."""
    moment = datetime.strptime(iso, ISO_FMT).replace(tzinfo=timezone.utc).astimezone(NY)
    return f"{moment:%b} {moment.day}"


def scope(ids, column: str) -> tuple[str, list]:
    """A WHERE clause for `column IN ids`; no clause at all when `ids` is None (everything)."""
    if ids is None:
        return "", []
    ids = list(ids)
    return f" WHERE {column} IN ({','.join('?' * len(ids))})", ids


@dataclass
class Line:
    status_line: str
    tone: str
    last_touch_at: str | None


@dataclass
class PeopleData:
    persons: list[sqlite3.Row] = field(default_factory=list)
    touch_by_person: dict[int, sqlite3.Row] = field(default_factory=dict)
    messages_by_touch: dict[int, list[sqlite3.Row]] = field(default_factory=lambda: defaultdict(list))
    inbound_by_touch: dict[int, list[sqlite3.Row]] = field(default_factory=lambda: defaultdict(list))
    linkedin_by_person: dict[int, list[sqlite3.Row]] = field(default_factory=lambda: defaultdict(list))
    sender_account: str = ""


def load(conn: sqlite3.Connection, company_ids, *, bodies: bool) -> PeopleData:
    """Every person of the companies (all companies when `company_ids` is None) with the rows their lines need."""
    data = PeopleData()
    where, params = scope(company_ids, "company_id")
    data.persons = conn.execute(f"SELECT * FROM person{where} ORDER BY person_id", params).fetchall()
    if not data.persons:
        return data
    where, params = scope(company_ids, "p.company_id")
    for t in conn.execute(f"SELECT t.* FROM touch t JOIN person p ON p.person_id = t.person_id{where}", params):
        data.touch_by_person[t["person_id"]] = t
    text = "m.subject, m.body" if bodies else "'' AS subject, '' AS body"
    for m in conn.execute(
        f"SELECT m.touch_id, m.seq, m.state, {text}, m.sent_at FROM message m "
        f"JOIN touch t ON t.touch_id = m.touch_id JOIN person p ON p.person_id = t.person_id{where} "
        "ORDER BY m.touch_id, m.seq", params,
    ):
        data.messages_by_touch[m["touch_id"]].append(m)
    text = "i.subject, i.body_text" if bodies else "'' AS subject, '' AS body_text"
    for i in conn.execute(
        f"SELECT i.touch_id, i.from_addr, {text}, i.received_at FROM inbound_message i "
        f"JOIN touch t ON t.touch_id = i.touch_id JOIN person p ON p.person_id = t.person_id{where} "
        "ORDER BY i.received_at, i.inbound_id", params,
    ):
        data.inbound_by_touch[i["touch_id"]].append(i)
    for e in conn.execute(
        f"SELECT l.* FROM linkedin_log l JOIN person p ON p.person_id = l.person_id{where} ORDER BY l.at, l.entry_id",
        params,
    ):
        data.linkedin_by_person[e["person_id"]].append(e)
    row = conn.execute("SELECT account FROM sender WHERE sender_id = 1").fetchone()
    data.sender_account = row["account"] if row else ""
    return data


def _latest_message(messages: list[sqlite3.Row]):
    live = [m for m in messages if m["state"] != "planned"]
    return live[-1] if live else None


def email_line(touch, messages, inbound, company) -> Line:
    last = max([m["sent_at"] for m in messages if m["sent_at"]] + [i["received_at"] for i in inbound], default=None)
    if touch is None:
        return Line("Not started", "idle", None)
    if touch["state"] == "replied":
        return Line("Replied", "ok", last)
    if touch["state"] == "bounced":
        return Line("Bounced", "bad", last)
    if touch["state"] == "closed":
        return Line("Closed", "idle", last)
    latest = _latest_message(messages)
    if latest is None:
        return Line("Not started", "idle", last)
    state = latest["state"]
    text, tone = {
        "drafted": ("Draft to review", "idle"), "approved": ("Approved", "info"), "sending": ("Sending", "info"),
        "send_unknown": ("Checking", "info"), "sent": ("Sent", "idle"), "failed": ("Failed", "bad"),
        "skipped": ("Skipped", "idle"),
    }[state]
    if state in ("drafted", "approved") and company is not None:
        held = company["held_by_touch"]
        if company["paused_at"]:
            text = f"Paused, {text.lower()}"
        elif held is not None and held != touch["touch_id"]:
            text = f"Held, {text.lower()}"
    return Line(text, tone, last)


def live_steps(entries) -> list:
    """A person's `linkedin_log` rows (oldest first) that are steps and were not taken back."""
    undone = {e["undoes"] for e in entries if e["undoes"] is not None}
    return [e for e in entries if e["kind"] in LINKEDIN_STEPS and e["entry_id"] not in undone]


def linkedin_line(entries) -> Line:
    steps = live_steps(entries)
    if not steps:
        return Line("Not started", "idle", None)
    last = steps[-1]
    text, tone = {
        "request_sent": (f"Requested {day(last['at'])}", "info"), "accepted": ("Accepted", "ok"),
        "message_sent": (f"Messaged {day(last['at'])}", "info"), "replied": ("Replied", "ok"),
    }[last["kind"]]
    return Line(text, tone, last["at"])


def lines(data: PeopleData, companies: dict) -> dict[int, Line]:
    """person_id to the status line of the person, with the company's pause and hold taken into account."""
    out = {}
    for p in data.persons:
        if p["channel"] == "linkedin":
            out[p["person_id"]] = linkedin_line(data.linkedin_by_person.get(p["person_id"], []))
            continue
        touch = data.touch_by_person.get(p["person_id"])
        tid = touch["touch_id"] if touch else None
        out[p["person_id"]] = email_line(
            touch, data.messages_by_touch.get(tid, []), data.inbound_by_touch.get(tid, []), companies.get(p["company_id"])
        )
    return out


def touch_summary(touch, messages) -> str:
    """What happened on one thread, in a few plain words: `First email sent Nov 5. Replied Nov 9`."""
    parts = []
    for m in messages:
        name = "First email" if m["seq"] == 0 else f"Follow-up {m['seq']}"
        if m["state"] == "sent" and m["sent_at"]:
            parts.append(f"{name} sent {day(m['sent_at'])}")
        elif touch["state"] == "open" and m["state"] in ("drafted", "approved", "sending", "send_unknown", "failed"):
            word = {"drafted": "drafted", "approved": "approved", "sending": "sending", "send_unknown": "being checked",
                    "failed": "failed"}[m["state"]]
            parts.append(f"{name} {word}")
    if touch["state"] == "replied" and touch["replied_at"]:
        parts.append(f"Replied {day(touch['replied_at'])}")
    elif touch["state"] == "bounced" and touch["bounced_at"]:
        parts.append(f"Bounced {day(touch['bounced_at'])}")
    elif touch["state"] == "closed":
        parts.append(f"Closed, {CLOSE_WORDS.get(touch['close_reason'], 'closed')}")
    return ". ".join(parts) or "Not started"


def thread(data: PeopleData, touch) -> list[dict]:
    """Sent emails and what came back, in time order (a draft is not part of the thread until it goes out)."""
    out = [
        (m["sent_at"], 0, {"seq": m["seq"], "direction": "out", "subject": m["subject"], "body": m["body"],
                           "at": m["sent_at"], "from": data.sender_account})
        for m in data.messages_by_touch.get(touch["touch_id"], []) if m["state"] == "sent" and m["sent_at"]
    ]
    out += [
        (i["received_at"], 1, {"seq": None, "direction": "in", "subject": i["subject"], "body": i["body_text"],
                               "at": i["received_at"], "from": i["from_addr"]})
        for i in data.inbound_by_touch.get(touch["touch_id"], [])
    ]
    return [row for _, _, row in sorted(out, key=lambda x: (x[0], x[1]))]
