"""The one query of due follow-ups, and the merged-company walk it needs.

Standard library only and nothing from `outreach.sending`: the sender (#14) and the web server (#13) both import
it, so there is one copy of "which follow-ups are due".
"""
import sqlite3
from datetime import date, timedelta

__all__ = ["RELEASE_KIND", "canonical_company", "company_map", "resolve_company", "company_reply_held",
           "held_survivors", "no_go_sets", "replied_people", "due_followups"]

# The `company` event that releases a hold (#11 Task 19's release-hold action appends one on every company of the
# merged family, beside clearing `held_by_touch`). A company's reply recorded before its newest release no longer
# holds it.
RELEASE_KIND = "hold_released"

# Each company row with three derived marks, each an event_id (the order things were recorded in, never a date a
# person typed): the newest recorded reply on its outreach touches, the newest on its email touches, and its newest
# release. A reply is an outreach touch with `replied_at` that is not undone (any channel, any reply kind: the store
# has no `auto_reply` outcome), or an email touch with `replied_at` whose kind is not `auto_reply`; its mark is its
# newest `replied` (or reply `corrected`) event, 0 when it has none (an imported row). Undoing a touch or clearing a
# reply clears `replied_at`, so it stops holding by itself.
_COMPANIES = f"""
SELECT c.company_id, c.status, c.paused_at, c.held_by_touch, c.merged_into,
  (SELECT max(ifnull((SELECT max(e.event_id) FROM event e
                      WHERE e.entity = 'outreach_touch' AND e.entity_id = o.touch_id
                        AND (e.kind = 'replied'
                             OR (e.kind = 'corrected' AND json_extract(e.detail, '$.outcome') = 'replied'))), 0))
   FROM outreach_touch o
   WHERE o.company_id = c.company_id AND o.replied_at IS NOT NULL AND o.undone_at IS NULL
     AND ifnull(o.reply_kind, '') <> 'auto_reply') AS outreach_reply_mark,
  (SELECT max(ifnull((SELECT max(e.event_id) FROM event e
                      WHERE e.entity = 'touch' AND e.entity_id = t.touch_id AND e.kind = 'replied'), 0))
   FROM touch t JOIN person p ON p.person_id = t.person_id
   WHERE p.company_id = c.company_id AND t.replied_at IS NOT NULL
     AND ifnull(t.reply_kind, '') <> 'auto_reply') AS email_reply_mark,
  (SELECT max(e.event_id) FROM event e WHERE e.entity = 'company' AND e.entity_id = c.company_id
     AND e.kind = '{RELEASE_KIND}') AS release_mark
FROM company c
"""


def canonical_company(conn: sqlite3.Connection, company_id: int) -> int:
    """The surviving company: `company_id` followed through `merged_into` to its end. A cycle raises RuntimeError."""
    seen = {company_id}
    current = company_id
    while True:
        row = conn.execute("SELECT merged_into FROM company WHERE company_id = ?", (current,)).fetchone()
        if row is None or row["merged_into"] is None:
            return current
        current = row["merged_into"]
        if current in seen:
            raise RuntimeError(f"company {company_id} is in a merge cycle at {current}")
        seen.add(current)


def company_map(conn: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    """Every company row by id, for resolving many companies with one query. Besides its own columns each row has
    `outreach_reply_mark`, `email_reply_mark` and `release_mark` (event ids, None when there is none), which
    `held_survivors` reads."""
    return {row["company_id"]: row for row in conn.execute(_COMPANIES)}


def resolve_company(companies: dict[int, sqlite3.Row], company_id: int) -> list[int]:
    """The ids from `company_id` to its survivor, in order (the survivor is last). A cycle raises RuntimeError."""
    chain = [company_id]
    current = companies[company_id]["merged_into"]
    while current is not None:
        if current in chain:
            raise RuntimeError(f"company {company_id} is in a merge cycle at {current}")
        chain.append(current)
        current = companies[current]["merged_into"]
    return chain


def company_reply_held(row: sqlite3.Row) -> bool:
    """Whether this company row is held by a reply: it has one recorded after its own newest release."""
    marks = [m for m in (row["outreach_reply_mark"], row["email_reply_mark"]) if m is not None]
    return bool(marks) and (row["release_mark"] is None or max(marks) > row["release_mark"])


def held_survivors(companies: dict[int, sqlite3.Row]) -> set[int]:
    """The surviving companies that are held (#9: any reply from anyone at the company stops its queued email).

    The one hold rule, read by the queue, the claim's stop re-check and `due_followups` alike. A survivor is held
    when any company that resolves to it has `held_by_touch` (the sender's hold on an email reply), or has a reply
    on any channel recorded after that same company's newest release (`company_reply_held`). Each company is judged
    on its own releases, so a merge never drops a hold, and by when a reply was recorded, so a reply logged after a
    release holds even when it is dated before it. An undone or cleared reply stops holding."""
    return {resolve_company(companies, cid)[-1] for cid, row in companies.items()
            if row["held_by_touch"] is not None or company_reply_held(row)}


def no_go_sets(conn: sqlite3.Connection, companies: dict[int, sqlite3.Row]) -> tuple[set[int], set[int]]:
    """The people and the surviving companies with an active `no_go`. A company's row counts for its survivor, so a
    no-go on a duplicate that was merged away still stops everyone at the survivor."""
    people, survivors = set(), set()
    for row in conn.execute("SELECT person_id, company_id FROM no_go WHERE lifted_at IS NULL"):
        if row["person_id"] is not None:
            people.add(row["person_id"])
        else:
            survivors.add(resolve_company(companies, row["company_id"])[-1])
    return people, survivors


def replied_people(conn: sqlite3.Connection) -> set[int]:
    """The people with a live reply on any channel: an outreach touch with `replied_at` that is not undone, or an
    email touch with `replied_at`, of any kind but `auto_reply`. The sender sends such a person nothing more (a
    stop, `reply_stop`), whether or not their company is still held: releasing a hold frees their colleagues only."""
    return {row[0] for row in conn.execute(
        "SELECT person_id FROM outreach_touch WHERE person_id IS NOT NULL AND replied_at IS NOT NULL "
        "AND undone_at IS NULL AND ifnull(reply_kind, '') <> 'auto_reply' UNION "
        "SELECT person_id FROM touch WHERE replied_at IS NOT NULL AND ifnull(reply_kind, '') <> 'auto_reply'")}


def due_followups(conn: sqlite3.Connection, today: date, *, days_ahead: int = 0) -> list[int]:
    """The `message_id`s of `planned` rows due on or before `today + days_ahead`, oldest `due_on` first.

    Only rows on a touch that is still `open`, whose first email was sent and which has its thread count. The
    surviving company (through `merged_into`) must not be held, `closed` or `removed`, the person must not have
    replied on any channel (`replied_people`), and neither the person nor the company family may have an active
    `no_go`: the queue skips those, so nobody drafts them. A paused company
    still lists its due follow-ups: pausing is Devanshu's, and the sender holds the send, not the draft. Read only.
    """
    horizon = (today + timedelta(days=days_ahead)).isoformat()
    rows = conn.execute(
        "SELECT m.message_id, p.person_id, p.company_id FROM message m "
        "JOIN touch t ON t.touch_id = m.touch_id JOIN person p ON p.person_id = t.person_id "
        "WHERE m.state = 'planned' AND m.due_on <= ? AND t.state = 'open' AND t.first_sent_at IS NOT NULL "
        "AND t.gmail_thread_id IS NOT NULL "
        "ORDER BY m.due_on, m.message_id", (horizon,)).fetchall()
    if not rows:
        return []
    companies = company_map(conn)
    held = held_survivors(companies)
    no_go_people, no_go_companies = no_go_sets(conn, companies)
    replied = replied_people(conn)
    due = []
    for row in rows:
        survivor_id = resolve_company(companies, row["company_id"])[-1]
        if (survivor_id not in held and companies[survivor_id]["status"] not in ("closed", "removed")
                and survivor_id not in no_go_companies and row["person_id"] not in no_go_people
                and row["person_id"] not in replied):
            due.append(row["message_id"])
    return due
