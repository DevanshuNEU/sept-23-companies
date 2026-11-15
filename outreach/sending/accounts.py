"""Sender accounts in the store: sync from config, the sponsor or general lane of a company, and routing.

A company's first email goes from its pin when that account sends, else from the sending account of its lane.
A follow-up always goes from the account that sent the first email. Routing only reads; `pin` and `sync` write.

A company the H-1B list never saw has no lane yet (#281, final review). The build matches USCIS employers to the
companies in the store when it runs, so a company added later is missing from the list without that meaning it has
no approvals, and its first email would pin it to the general lane for good. So a company with no thread, no pin, no
sponsorship yes and no approvals on the list waits as `lane_waits` when the newest `created_at` in its merge family
falls on a UTC day later than the list's `Built` day (`lookup.built_on`, the UTC day the build ran). The newest member
counts because the list checked only the names it saw. The build line holds a day, not a time, so a company added
later on the build day itself routes as one the list saw. A list with no build line (the build always writes one) has
no day to compare, so nothing waits on it.
"""
import sqlite3
from dataclasses import dataclass
from typing import Literal

from outreach.drafts.followups import canonical_company, company_map, resolve_company
from outreach.sending.config import AccountConfig, SendConfig
from outreach.h1b.lookup import built_on, has_list, tag_for
from outreach.store.writes import NAME_ALIAS_SOURCES, append_event


@dataclass(frozen=True)
class AccountRow:
    """One sender_account row joined with its AccountConfig."""
    account_id: int
    cfg: AccountConfig
    paused: bool
    auth_state: str
    next_send_at: str | None
    revision: int


def sync(conn: sqlite3.Connection, cfg: SendConfig, now: str) -> dict[str, AccountRow]:
    """Make sure every configured account has a row. Matches by address ignoring case; never deletes or renames.

    Returns the rows keyed by the address as written in config."""
    out: dict[str, AccountRow] = {}
    for acct in cfg.accounts:
        # sender_account.address is COLLATE NOCASE (0017), so `=` ignores case.
        row = conn.execute("SELECT * FROM sender_account WHERE address = ?", (acct.address,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO sender_account(address, kind, created_at, updated_at) VALUES (?, ?, ?, ?)",
                         (acct.address, acct.kind, now, now))
            row = conn.execute("SELECT * FROM sender_account WHERE address = ?", (acct.address,)).fetchone()
        out[acct.address] = AccountRow(account_id=row["account_id"], cfg=acct, paused=bool(row["paused"]),
                                       auth_state=row["auth_state"], next_send_at=row["next_send_at"],
                                       revision=row["revision"])
    return out


# A company fact that puts a company in the sponsor lane (he marked sponsorship yes). E-Verify and "OPT only" do not
# decide it any more (#281); the H-1B list does.
_SPONSOR_FACT = "fact = 'sponsorship' AND value = 'yes'"


# The survivor (the one parameter) and every company merged into it, at any depth.
_FAMILY = ("WITH RECURSIVE family(id) AS (SELECT ? UNION SELECT c.company_id FROM company c "
           "JOIN family f ON c.merged_into = f.id) ")


def _h1b_sponsor(names: list[str]) -> bool:
    """True when the H-1B list shows approvals under any of these names. No list on disk: False."""
    return any((tag := tag_for(n)) is not None and tag.total > 0 for n in dict.fromkeys(names))


def lane_of(conn: sqlite3.Connection, company_id: int) -> Literal["sponsor", "general"]:
    """Sponsor when the H-1B list shows approvals for the company (its name, its merged companies' names and its name
    aliases, as the MCP `h1b_history` reads them) or the company or anyone merged into it has sponsorship yes. With
    no list on disk only the sponsorship fact counts."""
    survivor = canonical_company(conn, company_id)
    family = _FAMILY
    if conn.execute(family + f"SELECT 1 FROM company_fact WHERE company_id IN family AND ({_SPONSOR_FACT}) LIMIT 1",
                    (survivor,)).fetchone():
        return "sponsor"
    src = ", ".join("?" for _ in NAME_ALIAS_SOURCES)
    names = [r[0] for r in conn.execute(
        family + "SELECT name FROM company WHERE company_id = ? OR company_id IN family ORDER BY company_id = ? DESC, "
        "company_id", (survivor, survivor, survivor))]
    names += [r[0] for r in conn.execute(
        family + f"SELECT alias FROM company_alias WHERE company_id IN family AND source IN ({src}) ORDER BY alias_key",
        (survivor, *NAME_ALIAS_SOURCES))]
    return "sponsor" if _h1b_sponsor(names) else "general"


def _family_newest(conn: sqlite3.Connection, companies: dict) -> dict[int, str]:
    """survivor -> the newest `created_at` in its merge family (the survivor and every company merged into it)."""
    out: dict[int, str] = {}
    for cid, created in conn.execute("SELECT company_id, created_at FROM company"):
        top = resolve_company(companies, cid)[-1] if cid in companies else cid
        if created and (top not in out or created > out[top]):
            out[top] = created
    return out


def added_after_list(conn: sqlite3.Connection, company_id: int) -> bool:
    """True when the company's merge family has a company added on a UTC day after the H-1B list's build day (module
    text). False with no list or no build line."""
    built = built_on()
    if built is None:
        return False
    newest = conn.execute(
        _FAMILY + "SELECT MAX(created_at) FROM company WHERE company_id IN family",
        (canonical_company(conn, company_id),)).fetchone()[0]
    return newest is not None and newest[:10] > built


def _family_names(conn: sqlite3.Connection, companies: dict, survivors: set[int]) -> dict[int, list[str]]:
    """survivor -> the survivor's name, its merged companies' names, then their name aliases: two queries however many
    survivors. The same names `lane_of` reads."""
    if not survivors:
        return {}
    survivor_of = {cid: resolve_company(companies, cid)[-1] for cid in companies}
    out: dict[int, list[str]] = {cid: [] for cid in survivors}
    names = {r["company_id"]: r["name"] for r in conn.execute("SELECT company_id, name FROM company")}
    for cid in survivors:  # the survivor's own name first
        if cid in names:
            out[cid].append(names[cid])
    for cid in sorted(names):
        top = survivor_of.get(cid)
        if top in out and top != cid:
            out[top].append(names[cid])
    src = ", ".join("?" for _ in NAME_ALIAS_SOURCES)
    for r in conn.execute(f"SELECT company_id, alias FROM company_alias WHERE source IN ({src}) ORDER BY alias_key",
                          NAME_ALIAS_SOURCES):
        top = survivor_of.get(r["company_id"])
        if top in out:
            out[top].append(r["alias"])
    return out


def route(conn: sqlite3.Connection, cfg: SendConfig, touch_id: int,
          accounts: dict[str, AccountRow]) -> tuple[AccountRow | None, str]:
    """The account that sends this touch, and why: thread, pinned, lane, (None, thread_missing) or (None, lane_waits).

    thread_missing: the touch's account is not in `accounts`. A touch with a Gmail thread but no account was sent
    before touches carried one, so its thread is account 1's (as everywhere else a NULL account is). A pin to an
    account that does not send (or is not in config) counts as no pin. The company is followed through
    `merged_into`; a pin on the raw id still counts. The lane is the sponsor lane when the H-1B list shows approvals
    under the company's merge family names or the family has sponsorship yes. With no usable H-1B list, a company with
    no thread, no pin and no sponsorship yes is (None, lane_waits): its lane is not decided. So is one with no
    approvals on the list that was added after the list was built (`added_after_list`, module text)."""
    return routes(conn, cfg, [touch_id], accounts)[touch_id]


def routes(conn: sqlite3.Connection, cfg: SendConfig, touch_ids, accounts: dict[str, AccountRow]
           ) -> dict[int, tuple[AccountRow | None, str]]:
    """`route` for many touches with a fixed number of queries (the queue routes every approved message)."""
    ids = sorted(set(touch_ids))
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    touches = conn.execute(
        "SELECT t.touch_id, t.account_id, t.gmail_thread_id, p.company_id FROM touch t "
        f"JOIN person p ON p.person_id = t.person_id WHERE t.touch_id IN ({marks})", ids).fetchall()
    companies = company_map(conn)
    pins = {row["company_id"]: row["account_id"] for row in conn.execute(
        "SELECT company_id, account_id FROM company_sender")}
    # Each sponsorship-yes fact counts for its company's survivor: a merge does not move evidence.
    sponsors = {resolve_company(companies, row[0])[-1] if row[0] in companies else row[0] for row in conn.execute(
        f"SELECT DISTINCT company_id FROM company_fact WHERE {_SPONSOR_FACT}")}
    survivors = {resolve_company(companies, t["company_id"])[-1] if t["company_id"] in companies else t["company_id"]
                 for t in touches}
    listed = has_list()
    h1b_names = _family_names(conn, companies, survivors - sponsors) if listed else {}
    sponsors |= {cid for cid, names in h1b_names.items() if _h1b_sponsor(names)}
    # Added after the list was built: the list cannot say such a company has no approvals.
    built = built_on() if listed else None
    newest = _family_newest(conn, companies) if built is not None else {}
    unseen = {cid for cid in survivors - sponsors if built is not None and newest.get(cid, "")[:10] > built}
    by_id = {a.account_id: a for a in accounts.values()}
    out: dict[int, tuple[AccountRow | None, str]] = {}
    for touch in touches:
        thread_account = touch["account_id"]
        if thread_account is None and touch["gmail_thread_id"]:
            thread_account = 1
        if thread_account is not None:
            found = by_id.get(thread_account)
            out[touch["touch_id"]] = (found, "thread") if found is not None else (None, "thread_missing")
            continue
        raw = touch["company_id"]
        cid = resolve_company(companies, raw)[-1] if raw in companies else raw
        pin = pins.get(cid, pins.get(raw))
        pinned = by_id.get(pin) if pin is not None else None
        if pinned is not None and pinned.cfg.sends:
            out[touch["touch_id"]] = (pinned, "pinned")
            continue
        if cid not in sponsors and not listed:  # the H-1B list cannot be read: the lane is not decided
            out[touch["touch_id"]] = (None, "lane_waits")
            continue
        if cid in unseen:
            out[touch["touch_id"]] = (None, "lane_waits")
            continue
        lane_cfg = cfg.lane_account("sponsor" if cid in sponsors else "general")
        if lane_cfg is not None and lane_cfg.address in accounts:
            out[touch["touch_id"]] = (accounts[lane_cfg.address], "lane")
        else:
            out[touch["touch_id"]] = (None, "lane_waits")
    return out


def pin(conn: sqlite3.Connection, company_id: int, account_id: int, by: Literal["rule", "you"], now: str) -> None:
    """Pin a company to an account, replacing any earlier pin, and log it. The company is resolved through
    `merged_into` first, so the pin lands on the surviving company whatever id the caller passes."""
    company_id = canonical_company(conn, company_id)
    prev = conn.execute("SELECT account_id FROM company_sender WHERE company_id = ?", (company_id,)).fetchone()
    conn.execute(
        "INSERT INTO company_sender(company_id, account_id, pinned_by, pinned_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(company_id) DO UPDATE SET account_id = excluded.account_id, pinned_by = excluded.pinned_by, "
        "pinned_at = excluded.pinned_at", (company_id, account_id, by, now))
    append_event(conn, entity="company", entity_id=company_id, company_id=company_id, kind="company_pinned",
                 actor="rules" if by == "rule" else "devanshu", at=now,
                 detail={"account_id": account_id, "by": by,
                         "previous_account_id": prev["account_id"] if prev else None})


def _row(row: sqlite3.Row, acct: AccountConfig) -> AccountRow:
    return AccountRow(account_id=row["account_id"], cfg=acct, paused=bool(row["paused"]),
                      auth_state=row["auth_state"], next_send_at=row["next_send_at"], revision=row["revision"])


def in_play(conn: sqlite3.Connection, cfg: SendConfig) -> dict[str, AccountRow]:
    """Read only: every configured account by its address as written in config, in config order, as `sync` would
    return it. An account with no row yet gets the id `sync` will give it (the next free id, in config order) and a
    fresh row's state, so a reader (status, the Outbox) sees the same routing before the first tick as after it."""
    out: dict[str, AccountRow] = {}
    next_id = (conn.execute("SELECT MAX(account_id) FROM sender_account").fetchone()[0] or 0) + 1
    for acct in cfg.accounts:
        row = conn.execute("SELECT * FROM sender_account WHERE address = ?", (acct.address,)).fetchone()
        if row is not None:
            out[acct.address] = _row(row, acct)
            continue
        out[acct.address] = AccountRow(account_id=next_id, cfg=acct, paused=False, auth_state="unknown",
                                       next_send_at=None, revision=1)
        next_id += 1
    return out


def sending(rows: dict[str, AccountRow]) -> list[AccountRow]:
    """The accounts that send (`sends = true`), in config order."""
    return [row for row in rows.values() if row.cfg.sends]


def primary(conn: sqlite3.Connection, cfg: SendConfig) -> AccountRow:
    """Read only: the row of the first account block (account 1, the old single sender, which rests now). For the
    readers that still show one account and for tests; the tick and the queue route every message to its own account.
    Falls back to account id 1 when the address has no row yet."""
    acct = cfg.accounts[0]
    row = conn.execute("SELECT * FROM sender_account WHERE address = ?", (acct.address,)).fetchone()
    if row is None:
        row = conn.execute("SELECT * FROM sender_account WHERE account_id = 1").fetchone()
    if row is None:
        return AccountRow(account_id=1, cfg=acct, paused=False, auth_state="unknown", next_send_at=None, revision=1)
    return AccountRow(account_id=row["account_id"], cfg=acct, paused=bool(row["paused"]),
                      auth_state=row["auth_state"], next_send_at=row["next_send_at"], revision=row["revision"])


def by_id(conn: sqlite3.Connection, cfg: SendConfig, account_id: int | None) -> AccountRow | None:
    """Read only: the row of `account_id` (None counts as account 1, a touch sent before accounts existed), or None
    when it has no row or its address is not in the config."""
    row = conn.execute("SELECT * FROM sender_account WHERE account_id = ?",
                       (1 if account_id is None else account_id,)).fetchone()
    if row is None:
        return None
    for acct in cfg.accounts:
        if acct.address.lower() == row["address"].lower():
            return AccountRow(account_id=row["account_id"], cfg=acct, paused=bool(row["paused"]),
                              auth_state=row["auth_state"], next_send_at=row["next_send_at"],
                              revision=row["revision"])
    return None


@dataclass(frozen=True)
class Pause:
    kind: str  # 'by_you' (sender row 1) or 'hard_block'
    reason: str
    at: str | None
    own: bool  # True when the account's own row holds it, False when it is sender row 1


def pause_of(conn: sqlite3.Connection, account_id: int) -> Pause | None:
    """Read only: why this account may not send, or None. The account's own hard block comes first (it needs his
    acknowledgement and he must see its reason), then sender row 1 (his button, or an old hard block), which
    pauses every account."""
    own = conn.execute("SELECT paused, pause_kind, paused_reason, paused_at FROM sender_account "
                       "WHERE account_id = ?", (account_id,)).fetchone()
    if own is not None and own["paused"]:
        return Pause(own["pause_kind"], own["paused_reason"], own["paused_at"], own=True)
    row = conn.execute("SELECT paused, pause_kind, paused_reason, paused_at FROM sender WHERE sender_id = 1").fetchone()
    if row["paused"]:
        return Pause(row["pause_kind"] or "by_you", row["paused_reason"], row["paused_at"], own=False)
    return None


def hard_pause(conn: sqlite3.Connection, account_id: int, reason: str, now: str) -> bool:
    """Hard-block this account's own row. False (nothing written) when it is already paused."""
    return conn.execute(
        "UPDATE sender_account SET paused = 1, pause_kind = 'hard_block', paused_reason = ?, paused_at = ?, "
        "updated_at = ?, revision = revision + 1 WHERE account_id = ? AND paused = 0",
        (reason, now, now, account_id)).rowcount > 0


def clear_hard_blocks(conn: sqlite3.Connection, now: str) -> int:
    """Lift every account's hard block (his click). Returns how many rows were cleared."""
    return conn.execute(
        "UPDATE sender_account SET paused = 0, pause_kind = NULL, paused_reason = '', paused_at = NULL, "
        "updated_at = ?, revision = revision + 1 WHERE paused = 1", (now,)).rowcount
