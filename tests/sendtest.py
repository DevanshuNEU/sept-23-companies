"""Seed helpers for the sending engine's tests (every sending task adds to this file).

`seed_message` stands in for #13 (a draft) and for Devanshu's click (approval): it is test seeding, never product
code. Every helper writes the smallest valid rows and takes moments as aware datetimes or canonical UTC text.
"""
import dataclasses
import itertools
import re
import sqlite3
from datetime import date, datetime, timezone

from outreach.sending import accounts as _accounts
from outreach.store.timefmt import ISO_FMT, iso_utc

__all__ = ["MON", "UTC", "iso", "with_ramp", "solo", "beside", "use_solo_config", "fakes", "seed_email_person", "seed_message", "seed_sent",
           "bounce", "put_day", "prove", "settle_and_send"]

UTC = timezone.utc
# Monday 2026-11-02, 09:00 in New York (daylight saving ended on the 1st, so UTC-5).
MON = datetime(2026, 11, 2, 14, 0, tzinfo=UTC)

_counter = itertools.count(1)
_NOW_SEED = "2026-11-02T12:00:00Z"
_APPROVED_AT = "2026-11-02T13:00:00Z"


def iso(at: datetime | str) -> str:
    """Canonical UTC text for an aware datetime or any ISO-8601 string."""
    if isinstance(at, datetime):
        if at.tzinfo is None:
            raise ValueError("a naive datetime is ambiguous; pass an aware one")
        return at.astimezone(UTC).strftime(ISO_FMT)
    return iso_utc(at)


def with_ramp(cfg, address: str | None = None, **numbers):
    """`cfg` with ramp numbers (`ramp_start`, `ramp_step`, `ramp_floor`, `ramp_ceiling`) set on the account at
    `address`, or on every account when `address` is None. The ramp lives on each account, not on the config."""
    accounts = tuple(dataclasses.replace(a, **numbers) if address is None or a.address == address else a
                     for a in cfg.accounts)
    return dataclasses.replace(cfg, accounts=accounts)


def solo(cfg):
    """`cfg` with one account, its first block (account 1), sending the general lane: the one-account shape most
    sending tests were written for before there were several accounts. The shipped shape (account 1 rests, account 2
    sends the general lane, account 3 waits) is the plain `load_config()`."""
    first = dataclasses.replace(cfg.accounts[0], sends=True, lane="general")
    return dataclasses.replace(cfg, accounts=(first,))


def beside(cfg):
    """`cfg` (one account: `solo`) with the other shipped accounts after it: account 1 still sends, beside account 2
    (the general lane) and the Northeastern account. For tests of one account's rows next to another's."""
    from outreach.sending.config import load_config
    return dataclasses.replace(cfg, accounts=(cfg.accounts[0],) + load_config().accounts[1:])


def use_solo_config(case) -> None:
    """For the rest of `case`, every default `load_config()` (the API's, the plan's, the sender's) reads a copy of
    the scratch config with one account, account 1, sending the general lane (`solo`), as the tests written before
    there were several accounts expect. Restored when the test ends."""
    import pathlib
    import tempfile
    from outreach.sending import config as send_config
    current = send_config.load_config.__defaults__
    text = pathlib.Path(current[0]).read_text(encoding="utf-8")
    blocks = re.split(r"^\[\[account\]\]$", text, flags=re.M)  # the header line only, not a comment naming it
    first = blocks[1].replace("sends = false", 'lane = "general"\nsends = true')
    path = pathlib.Path(tempfile.mkdtemp(prefix="outreach-sending-solo-")) / "config.toml"
    path.write_text(blocks[0] + "[[account]]" + first, encoding="utf-8")
    send_config.load_config.__defaults__ = (path,)
    case.addCleanup(setattr, send_config.load_config, "__defaults__", current)


def fakes(clock, *addresses: str, **kwargs) -> dict:
    """`clients={address: FakeGmail(...)}` for a tick: one FakeGmail per address, signed in as it, each with its own
    id prefix (`a`, `b`, ...) so two mailboxes never make the same Message-ID. `kwargs` go to every FakeGmail."""
    from outreach.sending.fakegmail import FakeGmail
    return {address: FakeGmail(clock=clock, account=address, id_prefix="" if i == 0 else chr(ord("a") + i),
                               **kwargs) for i, address in enumerate(addresses)}


def seed_email_person(conn: sqlite3.Connection, company_id: int, name: str, email: str, *,
                      role_kind: str = "engineer", tz: str = "America/New_York") -> int:
    """A person with an address at the company. Returns the person id."""
    return conn.execute(
        "INSERT INTO person(company_id, name, role_kind, email, tz, channel, added_by, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 'email', 'devanshu', ?, ?)",
        (company_id, name, role_kind, email, tz, _NOW_SEED, _NOW_SEED)).lastrowid


def _touch_for(conn: sqlite3.Connection, person_id: int) -> int:
    row = conn.execute("SELECT touch_id FROM touch WHERE person_id = ?", (person_id,)).fetchone()
    if row:
        return row[0]
    return conn.execute("INSERT INTO touch(person_id, created_at, updated_at) VALUES (?, ?, ?)",
                        (person_id, _NOW_SEED, _NOW_SEED)).lastrowid


def seed_message(conn: sqlite3.Connection, person_id: int, *, state: str = "approved", seq: int = 0,
                 subject: str = "Hi", body: str = "Hello there.", approved_at: str | None = None,
                 due_on: str | None = None) -> int:
    """A message in `planned`, `drafted` or `approved` (the touch is created when missing). A message is born
    planned or drafted and approval is an UPDATE, so `approved` does both. Returns the message id. A follow-up
    (seq above 0) needs `due_on` (default 2026-11-02) and, past `planned`, a touch whose first email was sent."""
    if state not in ("planned", "drafted", "approved"):
        raise ValueError(f"seed_message makes planned, drafted or approved messages, not {state!r}; use seed_sent")
    touch_id = _touch_for(conn, person_id)
    if seq > 0 and due_on is None:
        due_on = "2026-11-02"
    born = "planned" if state == "planned" else "drafted"
    if state == "planned":
        subject = body = ""
    message_id = conn.execute(
        "INSERT INTO message(touch_id, seq, state, subject, body, due_on, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (touch_id, seq, born, subject, body, due_on, _NOW_SEED, _NOW_SEED)).lastrowid
    if state == "approved":
        conn.execute("UPDATE message SET state = 'approved', approved_at = ? WHERE message_id = ?",
                     (iso(approved_at) if approved_at else _APPROVED_AT, message_id))
    return message_id


def seed_sent(conn: sqlite3.Connection, person_id: int, *, at: datetime | str, seq: int = 0,
              rfc: str | None = None, thread: str | None = None, account_id: int | None = None) -> int:
    """A message already sent at `at` (the touch is created when missing; a first email also fills the touch's
    first_sent_at, first_rfc_id and gmail_thread_id). Returns the message id."""
    n = next(_counter)
    when = iso(at)
    touch_id = _touch_for(conn, person_id)
    if account_id is not None:
        conn.execute("UPDATE touch SET account_id = ? WHERE touch_id = ?", (account_id, touch_id))
    rfc = rfc or f"<seed.{n}@sendtest.local>"
    row = conn.execute("SELECT first_sent_at, gmail_thread_id FROM touch WHERE touch_id = ?", (touch_id,)).fetchone()
    if seq == 0:
        thread = thread or f"thread-{touch_id}"
        conn.execute("UPDATE touch SET first_sent_at = ?, first_rfc_id = ?, gmail_thread_id = ?, updated_at = ? "
                     "WHERE touch_id = ?", (when, rfc, thread, when, touch_id))
        due_on = None
    else:
        if row["first_sent_at"] is None:
            raise ValueError("a follow-up is sent only after the first email")
        due_on = when[:10]
    return conn.execute(
        "INSERT INTO message(touch_id, seq, state, subject, body, due_on, sent_at, rfc_message_id, gmail_message_id, "
        "created_at, updated_at) VALUES (?, ?, 'sent', ?, 'Hello there.', ?, ?, ?, ?, ?, ?)",
        (touch_id, seq, f"Hello {n}", due_on, when, rfc, f"gmail-{n}", when, when)).lastrowid


def bounce(conn: sqlite3.Connection, touch_id: int, at: datetime | str) -> None:
    """The touch bounced at `at`."""
    when = iso(at)
    conn.execute("UPDATE touch SET state = 'bounced', bounced_at = ?, updated_at = ? WHERE touch_id = ?",
                 (when, when, touch_id))


def put_day(conn: sqlite3.Connection, day: date | str, limit: int, *, outcome: str | None = None,
            closed: bool = False, account_id: int = 1) -> None:
    """A `send_day` row as the ramp would have left it (for tests that start part way up the ramp)."""
    day = day.isoformat() if isinstance(day, date) else day
    conn.execute("INSERT INTO send_day(account_id, day, send_limit, outcome, closed_at, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?)", (account_id, day, limit, outcome, _NOW_SEED if closed else None,
                                               _NOW_SEED))


def prove(conn: sqlite3.Connection, account_id: int = 1) -> None:
    """Gmail is shown to keep the sender's marker on this account (one `marker_verified` event), so any number of
    emails may be claimed from it. For tests of the claim, the record and the reconcile; the one first email sent
    before that proof (`outreach.sending.proof`) has tests of its own. An event that names no message is account
    1's, so another account's proof is on an old email of its own, at a company of its own."""
    from outreach.store.writes import append_event
    entity_id = None
    if account_id != 1:
        company = conn.execute("INSERT INTO company(name, status_at, created_at) VALUES (?, ?, ?)",
                               (f"Proof Co {account_id}", _NOW_SEED, _NOW_SEED)).lastrowid
        person = seed_email_person(conn, company, "Proof Person", f"proof{account_id}@proof.example")
        entity_id = seed_sent(conn, person, at="2026-10-01T15:00:00Z", account_id=account_id)
    append_event(conn, entity="message", entity_id=entity_id, kind="marker_verified", actor="sender",
                 at=_NOW_SEED, idempotency_key=f"sendtest:proven:{account_id}")


def settle_and_send(conn: sqlite3.Connection, clients, clock, rng, cfg) -> str | None:
    """One tick's settle and send steps for one sending account, in the order the tick runs them: leftover claims; a
    Sent scan for each of its unclear sends with no transaction open; reconcile and requeue in one transaction; then
    the queue's pick for that account and one send. `clients` is `{address: client}` (the first sending account in
    `cfg` with a client sends) or one client for the first sending account. Returns what `send_one` returned, the
    kind of the GmailError it raised, or None when nothing was ready."""
    from outreach.sending import claim, queue, ramp
    from outreach.sending.mail import GmailError
    from outreach.store import transaction
    now = clock()
    sending = [a for a in cfg.accounts if a.sends]
    if not isinstance(clients, dict):
        clients = {sending[0].address: clients}
    acct = next(a for a in sending if a.address in clients)
    client = clients[acct.address]
    claim.leftover_claims(conn, iso(now))
    with transaction(conn):
        rows = _accounts.sync(conn, cfg, iso(now))
    account = rows[acct.address]
    found = {u.message_id: (*client.find_sent(u.rfc_id, u.not_before_ms, send_returned_ms=u.send_returned_ms),
                            u.rfc_id)
             for u in claim.unclear(conn, account.account_id)}
    with transaction(conn):
        claim.reconcile(conn, found, account.account_id, now=iso(now), cfg=cfg, rng=rng)
        claim.requeue_failed(conn, now=iso(now), cfg=cfg)
        day = ramp.open_day(conn, account, now, cfg)
    rows = _accounts.in_play(conn, cfg)
    picked = queue.pick(queue.verdicts(conn, now, cfg, rows, day={acct.address: day}, gate=None),
                        queue.load_queue(conn, cfg), now=now, cfg=cfg, account=acct.address)
    if picked is None:
        return None
    try:
        return claim.send_one(conn, client, picked, cfg, now=iso(now), clock=clock, rng=rng, account=rows[acct.address])
    except GmailError as err:
        return err.kind
