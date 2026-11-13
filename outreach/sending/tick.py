"""The tick: the one run, every couple of minutes, that does everything in order (#14 Task 13), over every sender
account (#9 multi-sender ruling, 2026-11-12).

The steps, in order. Gmail is called only in steps 2, 4 and 7, and never inside a store transaction:
1. A `job_run` (job `sender`) starts; a run some earlier tick left `running` is closed `failed` (it was killed).
2. Gmail, per account in config order: `profile` with that account's own client (`TickDeps.clients`, by address).
   A dead token, or a Gmail account with no client (no token file), is `signed_out`; a token for another address
   is `wrong_account`; each with one notice a day naming the account. Else `ok`. A Mail.app account with no client
   (Phase A) is not read and not told about. When no account signs in, the tick ends there. Each state is written
   on the account's own row (`sender_account`; account 1's also on `sender` row 1, as before accounts), in one
   transaction with `accounts.sync` and step 3.
3. Leftover claims (every account): a `sending` row is a tick that died mid-send, so it becomes `send_unknown`.
4. Gmail reads, per signed-in account with its own client: a Sent scan by marker for each of its unclear sends, its
   new inbound mail, the read back of each of its marked emails sent since the last tick (the first one for its
   proof), and for account 1 the check of imported mail (each check ends in its own short transaction).
5. One transaction: `accounts.sync`, reconcile per account, requeue proven failures (before quiet touches close, so
   a touch never closes on a message waiting for its one retry), apply each account's inbound mail, stop tags (per
   account), open the day of each sending account, the bounce rules per account, lapse follow-ups, close quiet
   touches, sweep stopped messages, and once a New York day prune old quiet runs.
6. The clock is read again. Then each account that may send this tick (`sends = true`, signed in this tick, not
   paused, the old sender not loaded, its marker proof open, its mail read in full), in random order: the verdicts,
   decided again before each account so a send by one is seen by the next (two accounts never send to one company
   in one tick), with each account's own waits in front (`queue.gate_accounts`), and that account's pick.
7. At most one message per account: the approve checks again (`precheck`), then `send_one` with that account's
   client. A Gmail error from the send is applied in one short transaction after the call.
8. The `job_run` finishes with integer counts and `noop`.

Nothing goes out from an account whose inbound mail was not fully read this tick or failed to apply, nor to any
company with a touch on an account that was not read (a missed reply could mean mail to someone who already
answered, or their colleague): those messages wait as `inbound_unread`, and every other company goes on. An account
counts as not read when it has touches and its read did not complete: no client, signed out, the wrong account, no
answer, or a read that failed or stopped part way.

Every email carries the sender's marker, `X-Outreach-Id`, the token it claimed before the call; a send in doubt is
found in Sent by that marker, never by its Message-ID (Gmail's API replaces the Message-ID the sender sets with
one of its own, and that is normal: the one Gmail stored is recorded and follow-ups thread on it). Nothing goes out
freely from an account before Gmail is proven to keep the marker there (otherwise a send in doubt could never be
found in Sent, and would go out twice): the proof is that account's first marked email read back from its own
Gmail by its Gmail id (`proof.verify_marker`). Nothing older counts, neither an imported email's check
(`verify_imported`) nor an email sent before the marker. Until then exactly one marked email may be claimed from
that account, ever, and only through every other gate unchanged: its first ready message goes alone, and every
other one of its messages waits as `message_id_unproven` ("The first email goes out alone, then Gmail is checked.
The rest follow."). The next tick reads it back (step 4): the `SENT` label and its own marker append
`marker_verified` and the rest follow; anything else appends `marker_missing`, which stops that account for good
with a notice once a day. Every later marked email is read back the same way once. A first email in doubt is never
retried while unproven; reconcile may settle it from Sent by its marker, and then it is read back like any other.
`claim.claim` checks the same rule inside its own transaction (`proof.may_claim`), and refuses an account that does
not send.

A dry run does steps 2 and 4 (but not the imported-mail check or the read back, which write), runs 5, 6 and the
pre-claim check inside one transaction that is rolled back, never sends, and writes nothing, `job_run` included;
its notices go nowhere. It names the first message a real tick would send (`would_send`) and stops there.

`fcntl.flock` on `.sender.lock` beside the store keeps two ticks from overlapping; the second one returns at once
having written nothing. The lock goes with the file descriptor, so a killed tick never leaves it held. Every step
is safe to cut short: a transaction either committed or did not, and the next tick settles any send in doubt by
its marker. Report lines are one line each, scrubbed, and never carry a traceback. A notice raised inside a
transaction is shown only after that transaction commits, so a rolled-back step never shows one.
"""
import contextlib
import fcntl
import json
import logging
import os
import random
import sqlite3
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from outreach.sending import calendar as cal
from outreach.sending import accounts, claim, followups, inbound, proof, queue, ramp, signals
from outreach.sending.config import SendConfig
from outreach.sending.mail import GmailError, MailClient, scrub
from outreach.sending.notify import Notifier, notify_once
from outreach.sending.precheck import LIST_MISSING, precheck
from outreach.sending.proof import PROOF_SQL  # noqa: F401 (the proof's one query; status and tests read it here)
from outreach.sending.ramp import DayState
from outreach.store.atomic import describe_error
from outreach.store.db import transaction
from outreach.store.timefmt import ISO_FMT
from outreach.store.writes import append_event

__all__ = ["LOCK_NAME", "BUSY_LINE", "STALE_RUN", "COUNT_KEYS", "WAIT_TITLE", "STOP_TITLE", "BANNED_TEXT",
           "PROOF_SQL", "TickDeps", "TickReport", "run_tick"]

LOCK_NAME = ".sender.lock"
BUSY_LINE = "another tick is running"
STALE_RUN = "the tick stopped before it finished"
COUNT_KEYS = ("sent", "reconciled", "replies", "autoReplies", "bounces", "skipped", "planned", "lapsed", "closed")
WAIT_TITLE = "Outreach sending waits"
STOP_TITLE = "Outreach sending stopped"
BANNED_TEXT = "Sending waits: the banned phrases list could not be read. Fix it to resume."
# What each phase of the first-send proof adds to the tick's report (see `proof`).
_PHASE_LINE = {
    proof.FIRST_SEND: "Gmail is not yet shown to keep the sender's marker, so the first email goes out alone and is "
                      "checked in Sent before any other",
    proof.CHECKING: "the first email was sent; nothing else goes out until it is checked in Sent",
    proof.IN_DOUBT: "the first email's send is in doubt; it is not retried and nothing else goes out until it is "
                    "found in Sent",
    proof.STOPPED: "sending is stopped: the first email could not be found in Sent, so Gmail is not shown to keep "
                   "the sender's marker",
    proof.MISSING: "sending is stopped for good: Gmail did not keep the sender's marker on a sent email",
}

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TickDeps:
    # Each account's own client, by its address (any case). An account with none is neither read nor sent from.
    clients: Mapping[str, MailClient]
    clock: Callable[[], datetime]
    rng: random.Random
    notify: Notifier
    old_sender_loaded: Callable[[], bool]
    banned: Sequence  # what `outreach.drafts.checks.load_banned` returned; empty blocks every claim


@dataclass(frozen=True)
class TickReport:
    auth: str
    day: DayState | None
    reconciled: dict[str, int]
    inbound: dict[str, int]
    sent: tuple[int, ...]
    would_send: int | None
    waiting: dict[str, int]
    dry_run: bool
    lines: tuple[str, ...]
    accounts: dict[str, str] = field(default_factory=dict)  # each account's sign-in this tick, by address


class _Busy(Exception):
    """Another tick holds the lock."""


def _lock_path(conn: sqlite3.Connection) -> Path:
    for row in conn.execute("PRAGMA database_list"):
        if row[1] == "main" and row[2]:
            return Path(row[2]).with_name(LOCK_NAME)
    raise RuntimeError("the sender runs on a store file, not an in-memory database")


class _NoLock(Exception):
    """The lock file could not be opened or locked (not because another tick holds it)."""


def _open_lock(path: Path) -> int:
    return os.open(path, os.O_RDWR | os.O_CREAT, 0o600)


@contextlib.contextmanager
def _lock(conn: sqlite3.Connection) -> Iterator[None]:
    path = _lock_path(conn)
    try:
        fd = _open_lock(path)
    except OSError as err:
        raise _NoLock(f"the tick lock {path} could not be opened ({_text(err)}), so this tick did nothing") from None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise _Busy() from None
        except OSError as err:
            raise _NoLock(f"the tick lock {path} could not be taken ({_text(err)}), so this tick did nothing") from None
        yield
    finally:
        os.close(fd)


def _quiet(title: str, text: str) -> None:
    """The dry run's notifier: a dry run tells nobody anything."""


def _stamp(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime(ISO_FMT)


def _text(err: BaseException) -> str:
    """One scrubbed line for an error: a GmailError's own words, else `Type: text`. Never a traceback."""
    return scrub(str(err) if isinstance(err, GmailError) else describe_error(err))


def run_tick(conn: sqlite3.Connection, deps: TickDeps, cfg: SendConfig, *, dry_run: bool = False) -> TickReport:
    """One tick (see the module text for its steps). `conn` must be opened with `connect(fullfsync=True)` and have
    no transaction open (RuntimeError otherwise, before anything else). Returns what it did. A store that stays
    locked, or any other failure, ends the tick with a line and a `failed` run when one can be written; a send
    that was cut short is settled by the next tick."""
    if conn.in_transaction:
        raise RuntimeError("run_tick needs a connection with no transaction open")
    if conn.execute("PRAGMA fullfsync").fetchone()[0] != 1:
        raise RuntimeError("the sender runs only on a connection opened with connect(fullfsync=True)")
    try:
        with _lock(conn):
            return _Tick(conn, deps, cfg, dry_run).run()
    except _Busy:
        return _skipped(dry_run, BUSY_LINE)
    except _NoLock as err:
        line = scrub(str(err))
        _log.warning("sender tick did not run: %s", line)
        if not dry_run:
            try:
                with transaction(conn):
                    at = _stamp(deps.clock())
                    conn.execute("INSERT INTO job_run(job, started_at, finished_at, status, error) "
                                 "VALUES ('sender', ?, ?, 'failed', ?)", (at, at, line))
            except sqlite3.Error as again:
                _log.warning("its failed run could not be recorded: %s", _text(again))
        return _skipped(dry_run, line)


def _skipped(dry_run: bool, line: str) -> TickReport:
    return TickReport(auth="skipped", day=None, reconciled={}, inbound={}, sent=(), would_send=None, waiting={},
                      dry_run=dry_run, lines=(line,))


class _Tick:
    def __init__(self, conn: sqlite3.Connection, deps: TickDeps, cfg: SendConfig, dry_run: bool):
        self.conn, self.deps, self.cfg, self.dry_run = conn, deps, cfg, dry_run
        self.clients = {address.lower(): client for address, client in deps.clients.items()}
        # Notices raised inside a transaction wait here and are shown only once it commits, so a rolled-back step
        # never shows a notice whose once-a-day record was rolled back with it (and shows it again every tick).
        self.pending: list[tuple[str, str]] = []
        self.notify = _quiet if dry_run else self._queue_notice
        self.now = deps.clock()
        self.stamp = _stamp(self.now)
        self.today = cal.ny_date(self.now, cfg)
        self.lines: list[str] = []
        self.status, self.error = "ok", ""
        self.run_id: int | None = None
        self.auth = "unknown"
        self.rows: dict[str, accounts.AccountRow] = {}  # the accounts in play, by address, in config order
        self.signed: dict[str, str] = {}  # each account's sign-in this tick: ok, signed_out, wrong_account, ...
        self.emails: dict[str, str] = {}  # the address each token signed in as
        self.no_token: set[str] = set()  # the Gmail accounts with no token file (never signed in, or it was removed)
        self.day: DayState | None = None  # the first sending account's day
        self.days: dict[str, DayState | None] = {}
        self.reconciled: dict[str, int] = {}
        self.inbound: dict[str, int] = {}
        self.sent: list[int] = []
        self.would_send: int | None = None
        self.waiting: dict[str, int] = {}
        self.counts = Counter()
        self.unread: set[int] = set()  # the accounts whose inbound mail this tick did not fully read or apply

    # -- the frame ----------------------------------------------------------------------------------------------

    def _queue_notice(self, title: str, text: str) -> None:
        self.pending.append((title, text))

    @contextlib.contextmanager
    def _tx(self) -> Iterator[None]:
        """One store transaction; the notices it raised are shown after it commits, and dropped if it does not."""
        try:
            with transaction(self.conn):
                yield
        except BaseException:
            self.pending.clear()
            raise
        notices, self.pending = self.pending, []
        for title, text in notices:
            try:
                self.deps.notify(title, text)
            except Exception as err:  # a notice is best effort; what it reports is already committed
                self._line(f"a notice could not be shown: {_text(err)}")

    def run(self) -> TickReport:
        try:
            if self._steps():
                self._finish()
        except sqlite3.OperationalError as err:
            locked = "locked" in str(err) or "busy" in str(err)
            self._fail("the store is locked by another writer, so this tick stopped; the next one retries" if locked
                       else "the tick stopped on a store error", err)
        except Exception as err:
            self._fail("the tick stopped", err)
        return TickReport(auth=self._auth_summary(), day=self.day, reconciled=self.reconciled, inbound=self.inbound,
                          sent=tuple(self.sent), would_send=self.would_send, waiting=self.waiting,
                          dry_run=self.dry_run, lines=tuple(self.lines), accounts=dict(self.signed))

    def _auth_summary(self) -> str:
        """One word for the report: the worst sign-in of any account read this tick, else ok."""
        for state in ("signed_out", "wrong_account", "unknown"):
            if state in self.signed.values():
                return state
        return "ok" if "ok" in self.signed.values() else self.auth

    def _line(self, text: str) -> None:
        self.lines.append(" ".join(text.split()))

    def _partial(self, text: str) -> None:
        self._line(text)
        if self.status == "ok":
            self.status = "partial"
            self.error = scrub(text)

    def _fail(self, text: str, err: BaseException) -> None:
        detail = _text(err)
        self._line(f"{text}: {detail}")
        _log.warning("sender tick failed: %s", detail)
        if self.dry_run or self.conn.in_transaction:
            return
        try:
            with self._tx():
                self._write_run("failed", scrub(f"{text}: {detail}"))
        except sqlite3.Error as again:
            self._line(f"the failed run could not be recorded either: {_text(again)}")

    def _write_run(self, status: str, error: str) -> None:
        summary = json.dumps(self._summary(status), sort_keys=True)
        finished = _stamp(self.deps.clock())
        if self.run_id is None:
            self.run_id = self.conn.execute(
                "INSERT INTO job_run(job, started_at, finished_at, status, summary, error) "
                "VALUES ('sender', ?, ?, ?, ?, ?)", (self.stamp, finished, status, summary, error)).lastrowid
            return
        self.conn.execute("UPDATE job_run SET status = ?, finished_at = ?, summary = ?, error = ? WHERE run_id = ?",
                          (status, finished, summary, error, self.run_id))

    def _summary(self, status: str) -> dict[str, int]:
        out = {key: int(self.counts[key]) for key in COUNT_KEYS}
        for key in ("requeued", "leftover", "blocked", "verified"):
            out[key] = int(self.counts[key])
        out["noop"] = int(status == "ok" and not any(out.values()))
        return out

    def _finish(self) -> None:
        """Step 8."""
        if self.dry_run:
            return
        with self._tx():
            self._write_run(self.status, self.error)

    # -- the steps ----------------------------------------------------------------------------------------------

    def _steps(self) -> bool:
        """Steps 1 to 7. False when the tick already wrote its own end (no account signed in)."""
        if not self.dry_run:
            self._start()
        self.rows = accounts.in_play(self.conn, self.cfg)
        checks = self._sign_in()
        if "ok" not in self.signed.values():
            if not self.dry_run:
                with self._tx():
                    self._write_sign_ins(checks)
                    self._write_run(self.status, self.error)
            return False
        self._settle_leftovers(checks)
        found, reads = self._gmail_reads()
        old_loaded = self._old_sender_loaded()
        if self.dry_run:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                self._apply(found, reads)
                self._reclock()
                self._send_step(old_loaded)
            finally:
                if self.conn.in_transaction:
                    self.conn.execute("ROLLBACK")
            return True
        with self._tx():
            self._apply(found, reads)
        self._reclock()
        self._send_step(old_loaded)
        return True

    def _reclock(self) -> None:
        """Read the clock again just before the pick: the Gmail reads can be long, and the window, the gaps and the
        claim time must be judged at the moment of the send, never at the start of the tick. Never backwards."""
        self.now = max(self.now, self.deps.clock())
        self.stamp = _stamp(self.now)
        self.today = cal.ny_date(self.now, self.cfg)

    def _start(self) -> None:
        """Step 1."""
        with self._tx():
            stale = self.conn.execute(
                "UPDATE job_run SET status = 'failed', finished_at = ?, error = ? "
                "WHERE job = 'sender' AND status = 'running'", (self.stamp, STALE_RUN)).rowcount
            self.run_id = self.conn.execute("INSERT INTO job_run(job, started_at) VALUES ('sender', ?)",
                                            (self.stamp,)).lastrowid
        if stale:
            self._line(f"{stale} earlier tick(s) stopped before finishing; closed as failed")

    # -- step 2: each account's sign-in -------------------------------------------------------------------------

    def _client(self, address: str) -> MailClient | None:
        return self.clients.get(address.lower())

    def _sign_in(self) -> list[tuple[str, str, str]]:
        """Step 2's Gmail half, no writes: each account's `profile` with its own client, in config order. Returns
        (address, state, text) for each account whose state is written (ok, signed out, wrong account)."""
        checks: list[tuple[str, str, str]] = []
        failures: list[str] = []
        for acct in self.cfg.accounts:
            address = acct.address
            client = self._client(address)
            if client is None and acct.kind != "gmail":
                self.signed[address] = "not_read"  # a Mail.app account: no client until its sender lands
                continue
            if client is None:
                state, text = "signed_out", f"no Gmail token; run: {_sign_in_command(self.cfg, address)}"
                self.no_token.add(address)
            else:
                try:
                    profile = client.profile()
                except GmailError as err:
                    text = scrub(str(err))
                    if err.kind != "auth":
                        self.signed[address] = "unknown"
                        failures.append(f"Gmail did not answer the sign-in check on {address}: {text}")
                        self._line(f"Gmail did not answer the sign-in check on {address} ({text}); nothing is read "
                                   "or sent from it this tick, and the next tick tries again")
                        continue
                    state = "signed_out"
                else:
                    email = (profile.email or "").strip()
                    self.emails[address] = email
                    state, text = ("ok", "") if email.lower() == address.lower() else (
                        "wrong_account", f"the token for {address} signs in as {email or 'no address'}")
            self.signed[address] = state
            checks.append((address, state, text))
            if state == "signed_out":
                failures.append(f"Gmail is signed out on {address}")
                self._line(f"Gmail is signed out on {address} ({text}); nothing is read or sent from it until he "
                           "signs in again")
            elif state == "wrong_account":
                failures.append(f"the Gmail token for {address} is for another account")
                self._line(f"the Gmail token for {address} signs in as {self.emails[address] or 'no address'}, not "
                           f"{address}; nothing is read or sent from it")
        self.auth = "ok" if "ok" in self.signed.values() else "signed_out"
        if "ok" not in self.signed.values():
            self.status = "failed"
            self.error = scrub(failures[0]) if failures else "no sender account has a Gmail client"
            if not failures:
                self._line("no sender account has a Gmail client, so nothing is read or sent")
        elif failures:
            self.status, self.error = "partial", scrub(failures[0])
        return checks

    def _write_sign_ins(self, checks: list[tuple[str, str, str]]) -> None:
        """Step 2's write, inside the caller's transaction: every account has a row, and each one checked holds its
        state (account 1's also on `sender` row 1, which the readers of one account still show)."""
        self.rows = accounts.sync(self.conn, self.cfg, self.stamp)
        for address, state, text in checks:
            row = self.rows[address]
            if state == "ok":
                self._signed_ok(row)
            elif state == "signed_out":
                self._signed_out(row, text)
            else:
                self._wrong_account(row, text)

    def _signed_ok(self, row: accounts.AccountRow) -> None:
        self.conn.execute("UPDATE sender_account SET auth_state = 'ok', auth_error = '', auth_checked_at = ? "
                          "WHERE account_id = ?", (self.stamp, row.account_id))
        if row.account_id == 1:
            self.conn.execute("UPDATE sender SET auth_state = 'ok', account = ?, auth_error = '', auth_checked_at = ? "
                              "WHERE sender_id = 1", (self.emails.get(row.cfg.address, ""), self.stamp))

    def _signed_out(self, row: accounts.AccountRow, text: str) -> None:
        address = row.cfg.address
        self.conn.execute("UPDATE sender_account SET auth_state = 'signed_out', auth_error = ?, auth_checked_at = ? "
                          "WHERE account_id = ?", (text, self.stamp, row.account_id))
        if row.account_id == 1:
            self.conn.execute("UPDATE sender SET auth_state = 'signed_out', auth_error = ?, auth_checked_at = ? "
                              "WHERE sender_id = 1", (text, self.stamp))
        append_event(self.conn, entity="sender", entity_id=row.account_id, kind="signed_out", actor="sender",
                     at=self.stamp, detail={"error": text, "address": address},
                     idempotency_key=f"signed_out:{row.account_id}:{self.today.isoformat()}")
        command = _sign_in_command(self.cfg, address)
        if address in self.no_token:
            start = f"start sending from {address}" if row.cfg.sends else f"read the replies to {address}"
            notice = f"{address} has no Gmail sign-in yet. Sign in to {start}: {command}"
        else:
            what = "nothing is sent from it and its replies are not read" if row.cfg.sends else "its replies are not read"
            notice = f"Gmail is signed out on {address}, so {what}. Sign in again to resume: {command}"
        notify_once(self.conn, self.notify, "signed_out", self.today, STOP_TITLE, notice, at=self.stamp,
                    account_id=row.account_id)

    def _wrong_account(self, row: accounts.AccountRow, text: str) -> None:
        address, email = row.cfg.address, self.emails.get(row.cfg.address, "")
        self.conn.execute("UPDATE sender_account SET auth_state = 'wrong_account', auth_error = ?, "
                          "auth_checked_at = ? WHERE account_id = ?", (scrub(text), self.stamp, row.account_id))
        if row.account_id == 1:
            self.conn.execute("UPDATE sender SET auth_state = 'wrong_account', account = ?, auth_error = ?, "
                              "auth_checked_at = ? WHERE sender_id = 1", (email, scrub(text), self.stamp))
        append_event(self.conn, entity="sender", entity_id=row.account_id, kind="wrong_account", actor="sender",
                     at=self.stamp, detail={"account": email, "address": address},
                     idempotency_key=f"wrong_account:{row.account_id}:{self.today.isoformat()}")
        notify_once(self.conn, self.notify, "wrong_account", self.today, STOP_TITLE,
                    f"The Gmail token for {address} is for {email or 'another account'}. Nothing is read or sent "
                    f"from {address} until he signs in as it: {_sign_in_command(self.cfg, address)}",
                    at=self.stamp, account_id=row.account_id)

    def _settle_leftovers(self, checks: list[tuple[str, str, str]]) -> None:
        """Step 2's write and step 3, in one transaction (read only in a dry run)."""
        if self.dry_run:
            n = self.conn.execute("SELECT COUNT(*) FROM message WHERE state = 'sending'").fetchone()[0]
        else:
            with self._tx():
                self._write_sign_ins(checks)
                n = claim.leftover_claims(self.conn, self.stamp)
        self.counts["leftover"] = n
        if n:
            self._line(f"{n} send(s) were cut short by an earlier tick; they are settled against Sent")

    # -- step 4: each account's reads --------------------------------------------------------------------------

    def _has_touches(self, account_id: int) -> bool:
        return self.conn.execute("SELECT 1 FROM touch WHERE COALESCE(account_id, 1) = ? LIMIT 1",
                                 (account_id,)).fetchone() is not None

    def _gmail_reads(self) -> tuple[dict[str, dict], dict[str, inbound.InboundRead]]:
        """Step 4, with no transaction open: each signed-in account reads its own mailbox with its own client. An
        account not read (no client, signed out, the wrong account, no answer) that has touches counts as unread."""
        found: dict[str, dict] = {}
        reads: dict[str, inbound.InboundRead] = {}
        for acct in self.cfg.accounts:
            address = acct.address
            row = self.rows[address]
            if self.signed.get(address) != "ok":
                if self._has_touches(row.account_id):
                    self.unread.add(row.account_id)
                continue
            client = self._client(address)
            found[address] = self._scan_sent(client, row)
            read = inbound.read_inbound(self.conn, client, row, cfg=self.cfg, now=self.now)
            reads[address] = read
            if not read.complete:
                self.unread.add(row.account_id)
                self._partial(f"Gmail mail on {address} was not fully read; the rest is read next tick, and nothing "
                              "goes from it or to a company it emailed until then")
            if not self.dry_run:
                self._read_back(client, row)
        return found, reads

    def _scan_sent(self, client: MailClient, row: accounts.AccountRow) -> dict:
        """A Sent scan by marker for each of this account's sends in doubt, with its own client."""
        found = {}
        for u in claim.unclear(self.conn, row.account_id):
            if u.stored is not None:
                continue  # settled from the ids Gmail kept, no scan needed
            if not u.marked:
                self._partial(f"message {u.message_id} was claimed before the sender set its marker, so Sent cannot "
                              "show whether it went out; it stays in doubt and is never sent again")
                continue
            try:
                hit, complete = client.find_sent(u.rfc_id, u.not_before_ms, send_returned_ms=u.send_returned_ms)
            except GmailError as err:
                self._partial(f"Sent could not be searched for message {u.message_id} on {row.cfg.address} "
                              f"({scrub(str(err))}); it stays in doubt until a later tick")
                continue
            found[u.message_id] = (hit, complete, u.rfc_id)
        return found

    def _read_back(self, client: MailClient, row: accounts.AccountRow) -> None:
        """The checks that write: imported mail (account 1's, sent by the old mailer) and this account's read back."""
        address, account_id = row.cfg.address, row.account_id
        if account_id == 1:
            verified = inbound.verify_imported(self.conn, client, now=self.now)
            self.counts["verified"] += verified["verified"]
            if verified["not_in_sent"]:
                self._line(f"{verified['not_in_sent']} imported email(s) are not in Sent under their Message-ID")
            if verified["errors"]:
                self._partial("imported mail could not be checked against Gmail; tried again next tick")
        before = proof.phase(self.conn, account_id)
        checked = proof.verify_marker(self.conn, client, account_id, now=self.now)
        self.counts["verified"] += checked["verified"]
        if before == proof.CHECKING and proof.phase(self.conn, account_id) == proof.PROVEN:
            self._line(f"the first email from {address} is in Sent with the sender's marker, so the rest may follow")
        if checked["missing"]:
            self._line(f"{checked['missing']} sent email(s) from {address} are not in Sent with the sender's marker")
        if checked["errors"]:
            self._partial(f"sent email from {address} could not be read back from Gmail; tried again next tick" +
                          (", and nothing else goes out from it until then" if before == proof.CHECKING else ""))

    def _old_sender_loaded(self) -> bool:
        try:
            return bool(self.deps.old_sender_loaded())
        except Exception as err:
            self._line(f"could not tell whether the old sender is loaded ({_text(err)}), so it is taken as loaded")
            return True

    # -- step 5: one transaction -------------------------------------------------------------------------------

    def _apply(self, found: dict[str, dict], reads: dict[str, inbound.InboundRead]) -> None:
        """Step 5, inside the caller's transaction."""
        conn, cfg, now, stamp = self.conn, self.cfg, self.now, self.stamp
        self.rows = accounts.sync(conn, cfg, stamp)  # the only thing that creates sender_account rows
        reconciled = Counter({"sent": 0, "failed": 0, "waiting": 0, "errors": 0})
        for address, scans in found.items():
            reconciled.update(claim.reconcile(conn, scans, self.rows[address].account_id, now=stamp, cfg=cfg,
                                              rng=self.deps.rng))
        self.reconciled = dict(reconciled)
        self.counts["reconciled"] = self.reconciled["sent"] + self.reconciled["failed"]
        if self.reconciled["errors"]:
            n = self.reconciled["errors"]
            self._partial(f"{n} send(s) in doubt could not be settled; they stay in doubt")
            notify_once(conn, self.notify, "reconcile_errors", self.today, "Outreach sender needs a look",
                        f"{n} email(s) in doubt could not be settled against Sent. Nothing is resent; check the "
                        "sender's log.", at=stamp)
        self.counts["requeued"] = claim.requeue_failed(conn, now=stamp, cfg=cfg)
        self.reconciled = {**self.reconciled, "requeued": self.counts["requeued"], "leftover": self.counts["leftover"]}
        applied = Counter()
        for address, read in reads.items():
            account = self.rows[address]
            try:
                applied.update(inbound.apply_inbound(conn, read, account, now=now, cfg=cfg, notify=self.notify))
            except Exception as err:
                if not conn.in_transaction:
                    raise
                self.unread.add(account.account_id)
                self._partial(f"Gmail mail on {address} could not be applied ({_text(err)}); nothing goes "
                              "from it or to a company it emailed until it is")
        self.inbound = dict(applied)
        self.counts["replies"] = self.inbound.get("replies", 0)
        self.counts["autoReplies"] = self.inbound.get("auto_replies", 0)
        self.counts["bounces"] = self.inbound.get("bounces", 0)
        inbound.observe_stop_tags(conn, now=now, cfg=cfg, notify=self.notify)
        for address, row in self.rows.items():
            if row.cfg.sends:
                self.days[address] = ramp.open_day(conn, row, now, cfg)
        self.day = next(iter(self.days.values()), None)
        for row in self.rows.values():
            self._bounce_rules(row)
        self.counts["planned"] = followups.sync_followups(conn, cfg, now=stamp)
        self.counts["lapsed"] = followups.lapse_followups(conn, self.today, cfg, now=stamp)
        self.counts["closed"] = followups.close_quiet_touches(conn, self.today, cfg, now=stamp)
        verdicts = queue.verdicts(conn, now, cfg, self.rows, day=self.days, gate=None)
        self.counts["skipped"] = followups.sweep_stopped(conn, verdicts, now=stamp)
        self._prune()

    def _bounce_rules(self, account: accounts.AccountRow) -> None:
        """The bounce rule on whatever wrote the bounces (Gmail inbound or another path), per account: today's 3-day
        rate when a bounce is dated today. Its key matches inbound's, so the warning fires once. 2% of the account's
        last 100 first emails bounced, once 30 are sent, pauses that account (#9 multi-sender ruling, 2026-11-12)."""
        conn, cfg = self.conn, self.cfg
        signal = {"cfg": cfg, "notify": self.notify, "now": self.now, "at": self.stamp, "account": account}
        start, end = cal.ny_day_bounds(self.today, cfg)
        if conn.execute("SELECT 1 FROM touch WHERE COALESCE(account_id, 1) = ? AND bounced_at >= ? "
                        "AND bounced_at < ? LIMIT 1", (account.account_id, start, end)).fetchone():
            rate = signals.bounce_rate_3d(conn, account.account_id, self.today, cfg)
            if signals.bounce_rate_warns(rate, cfg):
                ramp.note_signal(conn, "bounce_warning", detail={"rate": rate, "source": "tick"},
                                 key=signals.day_key("bounce_warning", account.account_id, self.today), **signal)
            bounced, sent = signals.bounce_rate_last_100(conn, account.account_id)
            if signals.bounce_rate_hard(bounced, sent):
                ramp.note_signal(conn, "bounce_rate_hard", detail={"bounced": bounced, "sent": sent,
                                                                   "source": "tick"},
                                 key=signals.day_key("bounce_rate_hard", account.account_id, self.today), **signal)

    def _prune(self) -> None:
        """Once a New York day: delete this job's `ok` no-op runs older than `prune_noop_after_days`."""
        key = f"runs_pruned:{self.today.isoformat()}"
        if self.conn.execute("SELECT 1 FROM event WHERE idempotency_key = ?", (key,)).fetchone():
            return
        cutoff = _stamp(self.now - timedelta(days=self.cfg.prune_noop_after_days))
        deleted = self.conn.execute(
            "DELETE FROM job_run WHERE job = 'sender' AND status = 'ok' AND json_extract(summary, '$.noop') = 1 "
            "AND started_at < ?", (cutoff,)).rowcount
        append_event(self.conn, entity="sender", entity_id=1, kind="runs_pruned", actor="sender", at=self.stamp,
                     detail={"deleted": deleted, "before": cutoff}, idempotency_key=key)

    # -- steps 6 and 7: each sending account ---------------------------------------------------------------------

    def _decide(self, old_loaded: bool, phases: Mapping[str, str]) -> list[queue.Verdict]:
        """Step 6's verdicts as the store stands now, with each account's own waits in front: not signed in this
        tick (`signed_out`), the old sender (`old_sender_running`, every message), its marker not proven
        (`message_id_unproven`, or its first email alone)."""
        found = queue.verdicts(self.conn, self.now, self.cfg, self.rows, day=self.days, gate=None,
                               unread=frozenset(self.unread))
        gates: dict[str | None, str | None] = {None: "old_sender_running" if old_loaded else None}
        first: list[str] = []
        for address in self.rows:
            stage = phases[address]
            if self.signed.get(address) != "ok":
                gates[address] = "signed_out"
            elif old_loaded:
                gates[address] = "old_sender_running"
            elif stage not in proof.OPEN:
                gates[address] = "message_id_unproven"
            elif stage == proof.FIRST_SEND:
                first.append(address)
        return queue.gate_accounts(found, gates, first)

    def _senders(self, old_loaded: bool, phases: Mapping[str, str]) -> list[accounts.AccountRow]:
        """The accounts that may send this tick, in config order, with a line for each that may not and says why."""
        out = []
        told_pause = False
        for address, row in self.rows.items():
            if not row.cfg.sends or self.signed.get(address) != "ok":
                continue
            stage = phases[address]
            if stage in (proof.STOPPED, proof.MISSING, proof.IN_DOUBT):
                self._partial(f"{_PHASE_LINE[stage]} ({address})")
                if stage != proof.IN_DOUBT:
                    self._notice(f"first_send_{stage}", STOP_TITLE, f"{address}: {proof.PHASE_TEXT[stage]}",
                                 account_id=row.account_id)
            elif stage == proof.CHECKING:
                self._line(f"{_PHASE_LINE[stage]} ({address})")
            pause = accounts.pause_of(self.conn, row.account_id)
            if pause is not None:
                how = "by you" if pause.kind == "by_you" else f"by a hard block: {pause.reason}"
                if pause.own:
                    self._line(f"sending from {address} is paused {how}")
                elif not told_pause:
                    self._line(f"sending is paused {how}")
                    told_pause = True
                continue
            if old_loaded or row.account_id in self.unread or stage not in proof.OPEN:
                continue
            out.append(row)
        if old_loaded:
            self._line("the old mail sender is still loaded, so nothing is sent")
        return out

    def _send_step(self, old_loaded: bool) -> None:
        """Steps 6 and 7: each account that may send, in random order, decides on the store as it is after the sends
        before it, then sends at most one message. A dry run names the first message a real tick would send."""
        phases = {address: proof.phase(self.conn, row.account_id) for address, row in self.rows.items()}
        senders = self._senders(old_loaded, phases)
        self.deps.rng.shuffle(senders)
        decided = False
        for row in senders or [None]:
            found = self._decide(old_loaded, phases)
            if not decided:
                self.waiting = dict(Counter(v.reason for v in found if v.reason != "ready"))
                decided = True
            if row is None:
                return
            address = row.cfg.address
            picked = queue.pick(found, queue.load_queue(self.conn, self.cfg), self.deps.rng, self.cfg.pick_pool,
                                now=self.now, cfg=self.cfg, account=address)
            if picked is None:
                continue
            if phases[address] == proof.FIRST_SEND:
                self._line(f"{_PHASE_LINE[proof.FIRST_SEND]} ({address})")
            if self.dry_run:
                if self._prechecked(picked):
                    self.would_send = picked.message_id
                    self._line(f"dry run: would send message {picked.message_id} from {address}")
                    return
                continue
            if not self._send(picked, row):
                return

    def _notice(self, kind: str, title: str, text: str, *, account_id: int | None = None) -> None:
        """Tell him once a New York day, in a short transaction of its own (inside the caller's in a dry run, which
        tells nobody and is rolled back)."""
        if self.dry_run:
            notify_once(self.conn, self.notify, kind, self.today, title, text, at=self.stamp, account_id=account_id)
            return
        with self._tx():
            notify_once(self.conn, self.notify, kind, self.today, title, text, at=self.stamp, account_id=account_id)

    def _prechecked(self, picked: queue.Queued) -> bool:
        """The approve checks again, on the store as it is now (`precheck`). True when it may be claimed. A dry run
        runs it too, inside its rolled-back transaction, so `would_send` is what a real tick would send."""
        blocks = precheck(self.conn, picked, now=self.stamp, banned=self.deps.banned)
        if blocks is None:
            return True
        codes = ", ".join(b["code"] for b in blocks)
        if all(b["code"] == LIST_MISSING for b in blocks):
            self._partial("the banned phrases list could not be read, so nothing is sent until it is fixed")
            self._notice("banned_list_missing", WAIT_TITLE, BANNED_TEXT)
        elif self.dry_run:
            self._line(f"dry run: message {picked.message_id} would go back to review: {codes}")
        else:
            self.counts["blocked"] += 1
            self._line(f"message {picked.message_id} went back to review: {codes}")
        return False

    def _send(self, picked: queue.Queued, row: accounts.AccountRow) -> bool:
        """Step 7 for one account. False when no other account may send this tick either (another send in flight)."""
        if not self._prechecked(picked):
            return True
        address = row.cfg.address
        account = accounts.by_id(self.conn, self.cfg, row.account_id) or row
        try:
            result = claim.send_one(self.conn, self._client(address), picked, self.cfg, now=self.stamp,
                                    clock=self.deps.clock, rng=self.deps.rng, account=account)
        except claim.AnotherSendInFlight:
            self._line("another message is being sent, so this tick sends nothing more")
            return False
        except claim.WrongAccount as err:
            self._partial(f"{_text(err)}; nothing was sent")
            return True
        except claim.Unproven:
            self._partial(f"an email was already claimed from {address} while Gmail is not shown to keep the "
                          "sender's marker there, so nothing more is sent from it this tick")
            return True
        except GmailError as err:
            self._send_failed(picked, err, account)
            return True
        if result == "sent":
            self.sent.append(picked.message_id)
            self.counts["sent"] += 1
            self._line(f"sent message {picked.message_id} from {address}")
            if proof.phase(self.conn, account.account_id) == proof.MISSING:  # its own read back showed it dropped
                self._partial(f"{_PHASE_LINE[proof.MISSING]} ({address})")
                self._notice(f"first_send_{proof.MISSING}", STOP_TITLE, f"{address}: {proof.PHASE_TEXT[proof.MISSING]}",
                             account_id=account.account_id)
        else:
            self._line(f"message {picked.message_id} changed before it could be claimed; nothing was sent")
        return True

    def _send_failed(self, picked: queue.Queued, err: GmailError, account: accounts.AccountRow) -> None:
        text = scrub(str(err))
        rfc = self.conn.execute(
            "SELECT json_extract(detail, '$.pending_rfc_id') FROM event WHERE entity = 'message' AND entity_id = ? "
            "AND kind = 'claimed' ORDER BY event_id DESC LIMIT 1", (picked.message_id,)).fetchone()
        rfc = rfc[0] if rfc is not None else f"message-{picked.message_id}"
        detail = {"message_id": picked.message_id, "error_kind": err.kind, "status": err.status, "source": "send"}
        signal = {"at": self.stamp, "cfg": self.cfg, "notify": self.notify, "now": self.now, "account": account}
        self._partial(f"the send of message {picked.message_id} from {account.cfg.address} failed ({text}); it is "
                      "checked against Sent before any retry")
        with self._tx():
            if err.kind in ("rate_limit", "daily_limit"):
                ramp.note_signal(self.conn, "gmail_limit", detail=detail, key=f"gmail_limit:send:{rfc}", **signal)
            elif err.kind == "account_blocked":
                ramp.note_signal(self.conn, "gmail_policy", detail=detail, key=f"gmail_policy:send:{rfc}", **signal)
            elif err.kind == "auth":
                self.signed[account.cfg.address] = "signed_out"
                self._signed_out(account, text)


def _sign_in_command(cfg: SendConfig, address: str) -> str:
    """How he signs this account in (`gmail.sign_in_command`; imported here so the tick needs no Gmail module)."""
    from outreach.sending.gmail import sign_in_command
    return sign_in_command(cfg, address)
