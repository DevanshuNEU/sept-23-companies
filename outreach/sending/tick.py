"""The tick: the one run, every couple of minutes, that does everything in order (#14 Task 13).

The steps, in order. Gmail is called only in steps 2, 4 and 7, and never inside a store transaction:
1. A `job_run` (job `sender`) starts; a run some earlier tick left `running` is closed `failed` (it was killed).
2. Gmail: `profile`. A dead token is `signed_out`, another account is `wrong_account`; either ends the tick, with
   one notice a day. Else the state is `ok`. Its write shares one transaction with step 3.
3. Leftover claims: a `sending` row is a tick that died mid-send, so it becomes `send_unknown`.
4. Gmail reads: a Sent scan for each unclear send, new inbound mail, and the check of imported mail (which ends in
   its own short transaction).
5. One transaction: reconcile, requeue proven failures (before quiet touches close, so a touch never closes on a
   message waiting for its one retry), apply inbound mail, stop tags, open the day, the bounce rules, lapse
   follow-ups, close quiet touches, sweep stopped messages, and once a New York day prune old quiet runs.
6. The gate (paused, signed out, old sender loaded), the verdicts and the pick.
7. At most one message: the approve checks again (`precheck`), then `send_one`. A Gmail error from the send is
   applied in one short transaction after the call.
8. The `job_run` finishes with integer counts and `noop`.

Nothing is sent in a tick whose inbound mail was not fully read or failed to apply (a missed reply could mean
mail to someone who already answered), nor before `verify_imported` has proven at least once that Gmail keeps the
Message-ID the sender sets (otherwise a send in doubt could never be found in Sent, and would go out twice).

A dry run does steps 2 and 4 (but not the imported-mail check, which writes), runs 5 and 6 inside one transaction
that is rolled back, never sends, and writes nothing, `job_run` included; its notices go nowhere.

`fcntl.flock` on `.sender.lock` beside the store keeps two ticks from overlapping; the second one returns at once
having written nothing. The lock goes with the file descriptor, so a killed tick never leaves it held. Every step
is safe to cut short: a transaction either committed or did not, and the next tick settles any send in doubt by
Message-ID. Report lines are one line each, scrubbed, and never carry a traceback.
"""
import contextlib
import fcntl
import json
import logging
import os
import random
import sqlite3
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from outreach.sending import calendar as cal
from outreach.sending import claim, followups, inbound, queue, ramp, signals
from outreach.sending.config import SendConfig
from outreach.sending.mail import GmailError, MailClient, scrub
from outreach.sending.notify import Notifier, notify_once
from outreach.sending.precheck import LIST_MISSING, precheck
from outreach.sending.ramp import DayState
from outreach.store.atomic import describe_error
from outreach.store.db import transaction
from outreach.store.timefmt import ISO_FMT
from outreach.store.writes import append_event

__all__ = ["LOCK_NAME", "BUSY_LINE", "STALE_RUN", "COUNT_KEYS", "TickDeps", "TickReport", "run_tick"]

LOCK_NAME = ".sender.lock"
BUSY_LINE = "another tick is running"
STALE_RUN = "the tick stopped before it finished"
COUNT_KEYS = ("sent", "reconciled", "replies", "autoReplies", "bounces", "skipped", "lapsed", "closed")

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TickDeps:
    client: MailClient
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


class _Busy(Exception):
    """Another tick holds the lock."""


def _lock_path(conn: sqlite3.Connection) -> Path:
    for row in conn.execute("PRAGMA database_list"):
        if row[1] == "main" and row[2]:
            return Path(row[2]).with_name(LOCK_NAME)
    raise RuntimeError("the sender runs on a store file, not an in-memory database")


@contextlib.contextmanager
def _lock(conn: sqlite3.Connection) -> Iterator[None]:
    fd = os.open(_lock_path(conn), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise _Busy() from None
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


def _rewritten(err: BaseException) -> GmailError | None:
    """The `message_id_rewritten` GmailError in `err` or anything it was raised from or during, if any."""
    seen = set()
    while err is not None and id(err) not in seen:
        seen.add(id(err))
        if isinstance(err, GmailError) and err.kind == "message_id_rewritten":
            return err
        err = err.__cause__ or err.__context__
    return None


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
        return TickReport(auth="skipped", day=None, reconciled={}, inbound={}, sent=(), would_send=None, waiting={},
                          dry_run=dry_run, lines=(BUSY_LINE,))


class _Tick:
    def __init__(self, conn: sqlite3.Connection, deps: TickDeps, cfg: SendConfig, dry_run: bool):
        self.conn, self.deps, self.cfg, self.dry_run = conn, deps, cfg, dry_run
        self.client = deps.client
        self.notify = _quiet if dry_run else deps.notify
        self.now = deps.clock()
        self.stamp = _stamp(self.now)
        self.today = cal.ny_date(self.now, cfg)
        self.lines: list[str] = []
        self.status, self.error = "ok", ""
        self.run_id: int | None = None
        self.auth = "unknown"
        self.account = ""
        self.day: DayState | None = None
        self.reconciled: dict[str, int] = {}
        self.inbound: dict[str, int] = {}
        self.sent: list[int] = []
        self.would_send: int | None = None
        self.waiting: dict[str, int] = {}
        self.counts = Counter()
        self.hold: str | None = None  # why nothing may be sent this tick, though a message is ready

    # -- the frame ----------------------------------------------------------------------------------------------

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
        return TickReport(auth=self.auth, day=self.day, reconciled=self.reconciled, inbound=self.inbound,
                          sent=tuple(self.sent), would_send=self.would_send, waiting=self.waiting,
                          dry_run=self.dry_run, lines=tuple(self.lines))

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
            with transaction(self.conn):
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
        with transaction(self.conn):
            self._write_run(self.status, self.error)

    # -- the steps ----------------------------------------------------------------------------------------------

    def _steps(self) -> bool:
        """Steps 1 to 7. False when the tick already wrote its own end (it ended at sign-in)."""
        if not self.dry_run:
            self._start()
        if not self._sign_in():
            if not self.dry_run:
                with transaction(self.conn):
                    self._write_run(self.status, self.error)
            return False
        self._settle_leftovers()
        found, read = self._gmail_reads()
        old_loaded = self._old_sender_loaded()
        if self.dry_run:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                self._apply(found, read)
                picked = self._pick(old_loaded)
            finally:
                if self.conn.in_transaction:
                    self.conn.execute("ROLLBACK")
            self.would_send = picked.message_id if picked is not None and self.hold is None else None
            if self.would_send is not None:
                self._line(f"dry run: would send message {self.would_send}")
            return True
        with transaction(self.conn):
            self._apply(found, read)
        picked = self._pick(old_loaded)
        if picked is not None and self.hold is None:
            self._send(picked)
        return True

    def _start(self) -> None:
        """Step 1."""
        with transaction(self.conn):
            stale = self.conn.execute(
                "UPDATE job_run SET status = 'failed', finished_at = ?, error = ? "
                "WHERE job = 'sender' AND status = 'running'", (self.stamp, STALE_RUN)).rowcount
            self.run_id = self.conn.execute("INSERT INTO job_run(job, started_at) VALUES ('sender', ?)",
                                            (self.stamp,)).lastrowid
        if stale:
            self._line(f"{stale} earlier tick(s) stopped before finishing; closed as failed")

    def _sign_in(self) -> bool:
        """Step 2. True when the token signs in as the sender's address."""
        try:
            profile = self.client.profile()
        except GmailError as err:
            text = scrub(str(err))
            if err.kind != "auth":
                self.status, self.error = "failed", scrub(f"Gmail did not answer the sign-in check: {text}")
                self._line(f"Gmail did not answer the sign-in check ({text}); the next tick tries again")
                return False
            self.auth = "signed_out"
            self.status, self.error = "failed", "Gmail is signed out"
            self._line(f"Gmail is signed out ({text}); nothing is read or sent until he signs in again")
            if not self.dry_run:
                with transaction(self.conn):
                    self._signed_out(text)
            return False
        email = (profile.email or "").strip()
        if email.lower() != self.cfg.from_address.lower():
            self.auth, self.account = "wrong_account", email
            self.status, self.error = "failed", "the Gmail token is for another account"
            self._line(f"the Gmail token signs in as {email or 'no address'}, not {self.cfg.from_address}; "
                       "nothing is read or sent")
            if not self.dry_run:
                with transaction(self.conn):
                    self._wrong_account(email)
            return False
        self.auth, self.account = "ok", email
        return True

    def _signed_out(self, text: str) -> None:
        self.conn.execute("UPDATE sender SET auth_state = 'signed_out', auth_error = ?, auth_checked_at = ? "
                          "WHERE sender_id = 1", (text, self.stamp))
        append_event(self.conn, entity="sender", entity_id=1, kind="signed_out", actor="sender", at=self.stamp,
                     detail={"error": text}, idempotency_key=f"signed_out:{self.today.isoformat()}")
        notify_once(self.conn, self.notify, "signed_out", self.today, "Outreach sending stopped",
                    "Gmail is signed out, so nothing is sent. Sign in again to resume.", at=self.stamp)

    def _wrong_account(self, email: str) -> None:
        text = f"the token signs in as {email or 'no address'}, not {self.cfg.from_address}"
        self.conn.execute("UPDATE sender SET auth_state = 'wrong_account', account = ?, auth_error = ?, "
                          "auth_checked_at = ? WHERE sender_id = 1", (email, scrub(text), self.stamp))
        append_event(self.conn, entity="sender", entity_id=1, kind="wrong_account", actor="sender", at=self.stamp,
                     detail={"account": email}, idempotency_key=f"wrong_account:{self.today.isoformat()}")
        notify_once(self.conn, self.notify, "wrong_account", self.today, "Outreach sending stopped",
                    f"The Gmail token is for {email or 'another account'}, not {self.cfg.from_address}. "
                    "Nothing is sent.", at=self.stamp)

    def _settle_leftovers(self) -> None:
        """Step 2's write and step 3, in one transaction (read only in a dry run)."""
        if self.dry_run:
            n = self.conn.execute("SELECT COUNT(*) FROM message WHERE state = 'sending'").fetchone()[0]
        else:
            with transaction(self.conn):
                self.conn.execute("UPDATE sender SET auth_state = 'ok', account = ?, auth_error = '', "
                                  "auth_checked_at = ? WHERE sender_id = 1", (self.account, self.stamp))
                n = claim.leftover_claims(self.conn, self.stamp)
        self.counts["leftover"] = n
        if n:
            self._line(f"{n} send(s) were cut short by an earlier tick; they are settled against Sent")

    def _gmail_reads(self) -> tuple[dict, inbound.InboundRead]:
        """Step 4, with no transaction open."""
        found = {}
        for u in claim.unclear(self.conn):
            if u.stored is not None:
                continue  # settled from the ids Gmail kept, no scan needed
            try:
                hit, complete = self.client.find_sent(u.rfc_id, u.not_before_ms, send_returned_ms=u.send_returned_ms)
            except GmailError as err:
                self._partial(f"Sent could not be searched for message {u.message_id} ({scrub(str(err))}); "
                              "it stays in doubt until a later tick")
                continue
            found[u.message_id] = (hit, complete, u.rfc_id)
        snap = inbound.snapshot(self.conn, now=self.now, cfg=self.cfg)
        read = inbound.read_inbound(self.client, snap, cfg=self.cfg, now=self.now)
        if not read.complete:
            self.hold = "inbound"
            self._partial("Gmail mail was not fully read; the rest is read next tick, and nothing is sent until then")
        if not self.dry_run:
            verified = inbound.verify_imported(self.conn, self.client, now=self.now)
            self.counts["verified"] = verified["verified"]
            if verified["not_in_sent"]:
                self._line(f"{verified['not_in_sent']} imported email(s) are not in Sent under their Message-ID")
            if verified["errors"]:
                self._partial("imported mail could not be checked against Gmail; tried again next tick")
        return found, read

    def _old_sender_loaded(self) -> bool:
        try:
            return bool(self.deps.old_sender_loaded())
        except Exception as err:
            self._line(f"could not tell whether the old sender is loaded ({_text(err)}), so it is taken as loaded")
            return True

    def _apply(self, found: dict, read: inbound.InboundRead) -> None:
        """Step 5, inside the caller's transaction."""
        conn, cfg, now, stamp = self.conn, self.cfg, self.now, self.stamp
        self.reconciled = claim.reconcile(conn, found, now=stamp, cfg=cfg, rng=self.deps.rng)
        self.counts["reconciled"] = self.reconciled["sent"] + self.reconciled["failed"]
        if self.reconciled["errors"]:
            n = self.reconciled["errors"]
            self._partial(f"{n} send(s) in doubt could not be settled; they stay in doubt")
            notify_once(conn, self.notify, "reconcile_errors", self.today, "Outreach sender needs a look",
                        f"{n} email(s) in doubt could not be settled against Sent. Nothing is resent; check the "
                        "sender's log.", at=stamp)
        self.counts["requeued"] = claim.requeue_failed(conn, now=stamp, cfg=cfg)
        self.reconciled = {**self.reconciled, "requeued": self.counts["requeued"], "leftover": self.counts["leftover"]}
        try:
            self.inbound = inbound.apply_inbound(conn, read, account=self.account, now=now, cfg=cfg,
                                                 notify=self.notify)
        except Exception as err:
            if not conn.in_transaction:
                raise
            self.hold = "inbound"
            self._partial(f"Gmail mail could not be applied ({_text(err)}); nothing is sent until it is")
        self.counts["replies"] = self.inbound.get("replies", 0)
        self.counts["autoReplies"] = self.inbound.get("auto_replies", 0)
        self.counts["bounces"] = self.inbound.get("bounces", 0)
        inbound.observe_stop_tags(conn, now=now, cfg=cfg, notify=self.notify)
        self.day = ramp.open_day(conn, now, cfg)
        self._bounce_rules()
        self.counts["lapsed"] = followups.lapse_followups(conn, self.today, cfg, now=stamp)
        self.counts["closed"] = followups.close_quiet_touches(conn, self.today, cfg, now=stamp)
        verdicts = queue.verdicts(conn, now, cfg, day=self.day, gate=None)
        self.counts["skipped"] = followups.sweep_stopped(conn, verdicts, now=stamp)
        self._prune()

    def _bounce_rules(self) -> None:
        """The bounce rules on whatever wrote the bounces (Gmail inbound or another path): today's 3-day rate when a
        bounce is dated today, and the hard rate every tick. Their keys match inbound's, so a rule fires once."""
        conn, cfg = self.conn, self.cfg
        signal = {"cfg": cfg, "notify": self.notify, "now": self.now, "at": self.stamp}
        start, end = cal.ny_day_bounds(self.today, cfg)
        if conn.execute("SELECT 1 FROM touch WHERE bounced_at >= ? AND bounced_at < ? LIMIT 1",
                        (start, end)).fetchone():
            rate = signals.bounce_rate_3d(conn, self.today, cfg)
            if signals.bounce_rate_warns(rate, cfg):
                ramp.note_signal(conn, "bounce_warning", detail={"rate": rate, "source": "tick"},
                                 key=f"bounce_warning:{self.today.isoformat()}", **signal)
        if signals.hard_bounce_tripped(conn, cfg):
            rate, count = signals.hard_bounce_rate(conn, cfg)
            bounced = conn.execute("SELECT COUNT(*) FROM touch WHERE bounced_at IS NOT NULL").fetchone()[0]
            ramp.note_signal(conn, "hard_bounce_rate",
                             detail={"rate": rate, "first_emails": count, "bounced": bounced, "source": "tick"},
                             key=f"hard_bounce_rate:{bounced}", **signal)

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

    def _pick(self, old_loaded: bool) -> queue.Queued | None:
        """Step 6."""
        row = self.conn.execute("SELECT paused, pause_kind, paused_reason FROM sender WHERE sender_id = 1").fetchone()
        gate = None
        if row["paused"]:
            gate = "sender_paused"
            how = "by you" if row["pause_kind"] == "by_you" else f"by a hard block: {row['paused_reason']}"
            self._line(f"sending is paused {how}")
        elif self.auth != "ok":
            gate = "signed_out"
        elif old_loaded:
            gate = "old_sender_running"
            self._line("the old mail sender is still loaded, so nothing is sent")
        verdicts = queue.verdicts(self.conn, self.now, self.cfg, day=self.day, gate=gate)
        self.waiting = dict(Counter(v.reason for v in verdicts if v.reason != "ready"))
        picked = queue.pick(verdicts, queue.load_queue(self.conn, self.cfg))
        if picked is not None and self.conn.execute(
                "SELECT 1 FROM event WHERE entity = 'message' AND kind = 'import_verified' LIMIT 1").fetchone() is None:
            self.hold = self.hold or "message_id_unproven"
            self._line("nothing is sent until Gmail is shown to keep the Message-ID the sender sets (no imported "
                       "email has been found in Sent under its own Message-ID yet)")
        return picked

    def _send(self, picked: queue.Queued) -> None:
        """Step 7."""
        blocks = precheck(self.conn, picked, now=self.stamp, banned=self.deps.banned)
        if blocks is not None:
            codes = ", ".join(b["code"] for b in blocks)
            if all(b["code"] == LIST_MISSING for b in blocks):
                self._partial("the banned phrases list could not be read, so nothing is sent until it is fixed")
            else:
                self.counts["blocked"] += 1
                self._line(f"message {picked.message_id} went back to review: {codes}")
            return
        try:
            result = claim.send_one(self.conn, self.client, picked, self.cfg, now=self.stamp, clock=self.deps.clock,
                                    rng=self.deps.rng, account=self.account)
        except claim.AnotherSendInFlight:
            self._line("another message is being sent, so this tick sends nothing")
            return
        except GmailError as err:
            self._send_failed(picked, err)
            return
        except Exception as err:
            gone = _rewritten(err)
            if gone is not None:
                self._pause_rewritten(picked, gone)
            raise
        if result == "sent":
            self.sent.append(picked.message_id)
            self.counts["sent"] += 1
            self._line(f"sent message {picked.message_id}")
        else:
            self._line(f"message {picked.message_id} changed before it could be claimed; nothing was sent")

    def _send_failed(self, picked: queue.Queued, err: GmailError) -> None:
        text = scrub(str(err))
        rfc = self.conn.execute(
            "SELECT json_extract(detail, '$.pending_rfc_id') FROM event WHERE entity = 'message' AND entity_id = ? "
            "AND kind = 'claimed' ORDER BY event_id DESC LIMIT 1", (picked.message_id,)).fetchone()
        rfc = rfc[0] if rfc is not None else f"message-{picked.message_id}"
        detail = {"message_id": picked.message_id, "error_kind": err.kind, "status": err.status, "source": "send"}
        signal = {"at": self.stamp, "cfg": self.cfg, "notify": self.notify, "now": self.now}
        if err.kind == "message_id_rewritten":
            self._pause_rewritten(picked, err)
            if self.conn.execute("SELECT state FROM message WHERE message_id = ?",
                                 (picked.message_id,)).fetchone()[0] == "sent":
                self.sent.append(picked.message_id)
                self.counts["sent"] += 1
            self._partial(f"message {picked.message_id} went out, but Gmail stored it under another Message-ID; "
                          "sending is paused")
            return
        self._partial(f"the send of message {picked.message_id} failed ({text}); it is checked against Sent "
                      "before any retry")
        with transaction(self.conn):
            if err.kind in ("rate_limit", "daily_limit"):
                ramp.note_signal(self.conn, "gmail_limit", detail=detail, key=f"gmail_limit:send:{rfc}", **signal)
            elif err.kind == "account_blocked":
                ramp.note_signal(self.conn, "gmail_policy", detail=detail, key=f"gmail_policy:send:{rfc}", **signal)
            elif err.kind == "auth":
                self.auth = "signed_out"
                self._signed_out(text)

    def _pause_rewritten(self, picked: queue.Queued, err: GmailError) -> None:
        """Gmail kept the email under another Message-ID: pause (a hard block, over his own pause too) and tell
        him. `send_one` pauses first; this makes sure of it, since a send in doubt could otherwise go twice."""
        try:
            with transaction(self.conn):
                row = self.conn.execute("SELECT paused, pause_kind FROM sender WHERE sender_id = 1").fetchone()
                if not (row["paused"] and row["pause_kind"] == "hard_block"):
                    self.conn.execute(
                        "UPDATE sender SET paused = 1, pause_kind = 'hard_block', paused_reason = ?, paused_at = ?, "
                        "updated_at = ?, revision = revision + 1 WHERE sender_id = 1",
                        (claim.REWRITTEN_REASON, self.stamp, self.stamp))
                append_event(self.conn, entity="sender", entity_id=1, kind="hard_block", actor="sender",
                             at=self.stamp, idempotency_key=f"hard_block:message_id_rewritten:{picked.message_id}",
                             detail={"kind": "message_id_rewritten", "message_id": picked.message_id,
                                     "stored_rfc_id": err.sent.rfc_message_id, "reason": claim.REWRITTEN_REASON})
                notify_once(self.conn, self.notify, "message_id_rewritten", self.today, "Outreach sending paused",
                            claim.REWRITTEN_REASON, at=self.stamp)
        except sqlite3.Error as again:
            self._line(f"the pause after a rewritten Message-ID could not be written: {_text(again)}")
