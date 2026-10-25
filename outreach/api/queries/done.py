"""Done companies and the reply counts: what came of the work.

A reply is a person answering, on any channel: an `outreach_touch` with `replied_at` that is not undone, or an email
`touch` that replied (an out-of-office is no reply). An email reply that also has an `outreach_touch` row (its
`email_touch_id`) is counted once, as the email touch. Days and weeks are New York's.
"""
import sqlite3
from datetime import date, timedelta

from outreach.api.models.done import DoneCompany, DoneMetrics, DoneView, ReplyRate, WeekReplies
from outreach.api.queries.company import CLOSE_WORDS, company_holds, merge_parents, survivor_of
from outreach.api.queries.outcomes import by_ids, company_refs
from outreach.outcomes.windows import day_bounds_utc, month_bounds, ny_day, week_bounds

WEEKS = 8
DONE_REASONS = ("interview", "referral")
CHANNEL_START = {"linkedin_request": "LinkedIn request to {name}, {kind}",
                 "linkedin_message": "LinkedIn message to {name}, {kind}", "email": "Email to {name}, {kind}",
                 "hn_reply": "Reply to {name} on Hacker News", "other": "Contact with {name}"}
BEFORE_THE_SWITCH = "Before the switch"
BY_HAND = "Added by hand"

_REPLIES = (
    "SELECT replied_at FROM outreach_touch WHERE replied_at IS NOT NULL AND undone_at IS NULL "
    "AND replied_at >= ?1 AND replied_at < ?2 "
    "AND NOT (email_touch_id IS NOT NULL AND EXISTS (SELECT 1 FROM touch t WHERE t.touch_id = email_touch_id "
    "AND t.replied_at IS NOT NULL AND ifnull(t.reply_kind, '') <> 'auto_reply')) "
    "UNION ALL SELECT replied_at FROM touch WHERE replied_at IS NOT NULL AND ifnull(reply_kind, '') <> 'auto_reply' "
    "AND replied_at >= ?1 AND replied_at < ?2")


def reply_times(conn: sqlite3.Connection, start: str, end: str) -> list[str]:
    """When each reply in [start, end) (UTC moments) came in."""
    return [r[0] for r in conn.execute(_REPLIES, (start, end))]


def replies_this_month(conn: sqlite3.Connection, now: str) -> int:
    first, last = month_bounds(now)
    return len(reply_times(conn, day_bounds_utc(first)[0], day_bounds_utc(last)[1]))


def _week_starts(now: str) -> list[str]:
    monday = date.fromisoformat(week_bounds(now)[0])
    return [(monday - timedelta(weeks=n)).isoformat() for n in range(WEEKS - 1, -1, -1)]


def _reply_rate(conn: sqlite3.Connection, now: str) -> ReplyRate:
    first, last = month_bounds(now)
    start, end = day_bounds_utc(first)[0], day_bounds_utc(last)[1]
    row = conn.execute(
        "SELECT count(*) AS sent, coalesce(sum(replied_at IS NOT NULL AND ifnull(reply_kind, '') <> 'auto_reply'), 0) "
        "AS replied FROM touch WHERE first_sent_at >= ? AND first_sent_at < ?", (start, end)).fetchone()
    return ReplyRate(replies=row["replied"], first_emails=row["sent"])


def done_count(conn: sqlite3.Connection) -> int:
    marks = ",".join("?" * len(DONE_REASONS))
    return conn.execute(f"SELECT count(*) FROM company WHERE status = 'closed' AND close_reason IN ({marks}) "
                        "AND merged_into IS NULL", DONE_REASONS).fetchone()[0]


def _how_started(conn: sqlite3.Connection, family: list[int]) -> tuple[str, str | None, bool]:
    """(how the work began, the role named, whether it came from the old system) for one company's family."""
    marks = ",".join("?" * len(family))
    imported = conn.execute(f"SELECT 1 FROM event WHERE company_id IN ({marks}) AND actor = 'import' LIMIT 1",
                            family).fetchone() is not None
    touches = conn.execute(
        f"SELECT t.channel, t.sent_at, t.posting_id, p.name, p.role_kind FROM outreach_touch t "
        f"LEFT JOIN person p ON p.person_id = t.person_id WHERE t.company_id IN ({marks}) AND t.undone_at IS NULL "
        "ORDER BY t.sent_at, t.touch_id", family).fetchall()
    emails = conn.execute(
        f"SELECT t.first_sent_at AS sent_at, p.name, p.role_kind FROM touch t JOIN person p ON p.person_id = t.person_id "
        f"WHERE p.company_id IN ({marks}) AND t.first_sent_at IS NOT NULL ORDER BY t.first_sent_at", family).fetchall()
    first = None  # (sent_at, text)
    for t in touches:
        if t["channel"] == "application":
            text = "Application"
        else:
            text = CHANNEL_START[t["channel"]].format(name=t["name"] or "someone", kind=t["role_kind"] or "contact")
        first = (t["sent_at"], text)
        break
    if emails and (first is None or emails[0]["sent_at"] < first[0]):
        first = (emails[0]["sent_at"], CHANNEL_START["email"].format(name=emails[0]["name"], kind=emails[0]["role_kind"]))
    posting = next((t["posting_id"] for t in reversed(touches) if t["posting_id"] is not None), None)
    role = None
    if posting is not None:
        row = conn.execute("SELECT title FROM posting WHERE posting_id = ?", (posting,)).fetchone()
        role = row["title"] if row else None
    how = BEFORE_THE_SWITCH if imported and first is None else first[1] if first else BY_HAND
    return how, role, imported


def done_view(conn: sqlite3.Connection, now: str) -> DoneView:
    marks = ",".join("?" * len(DONE_REASONS))
    rows = conn.execute(
        f"SELECT company_id, close_reason, status_at, status_note FROM company WHERE status = 'closed' "
        f"AND close_reason IN ({marks}) AND merged_into IS NULL ORDER BY status_at DESC, company_id DESC",
        DONE_REASONS).fetchall()
    parents = merge_parents(conn)
    refs = company_refs(conn, {r["company_id"] for r in rows}, holds=company_holds(conn) if rows else None)
    members: dict[int, list[int]] = {r["company_id"]: [r["company_id"]] for r in rows}
    for child in parents:
        owner = survivor_of(parents, child)
        if owner in members:
            members[owner].append(child)
    companies = []
    for r in rows:
        how, role, imported = _how_started(conn, members[r["company_id"]])
        companies.append(DoneCompany(
            company=refs[r["company_id"]], outcome=r["close_reason"],
            outcome_label=CLOSE_WORDS[r["close_reason"]].capitalize(), closed_at=r["status_at"], how_started=how,
            imported=imported, role=role, note=r["status_note"]))
    weeks = _week_starts(now)
    stamps = reply_times(conn, day_bounds_utc(weeks[0])[0], day_bounds_utc(week_bounds(now)[1])[1])
    counts = dict.fromkeys(weeks, 0)
    for at in stamps:
        monday = week_bounds(at)[0]
        if monday in counts:
            counts[monday] += 1
    return DoneView(
        as_of=now, companies=companies,
        metrics=DoneMetrics(
            replies_this_month=replies_this_month(conn, now),
            replies_per_week=[WeekReplies(week_of=w, count=counts[w]) for w in weeks],
            reply_rate=_reply_rate(conn, now),
            referrals=sum(1 for c in companies if c.outcome == "referral"),
            interviews=sum(1 for c in companies if c.outcome == "interview")))
