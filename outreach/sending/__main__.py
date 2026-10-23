"""`python -m outreach.sending <command>`: the new sender from launchd and by hand (#14, Task 14).

- `run [--dry-run]`: one tick (launchd runs it every 2 minutes). With `--dry-run` it reads the store and Gmail, shows
  what it would do, and writes nothing. Live checkout only.
- `status`: the sender's state, today's count, the ramp and why the next email waits, from the store at `OUTREACH_DB`
  (the live store only from the live checkout). No Gmail call.
- `whoami`: the address the Gmail token signs in as. Live checkout only.
- `auth`: sign in to Gmail in the browser (writes the token). Live checkout only.
- `simulate [--days N] [--seed N]`: the two-week simulation on a temporary store and a fake Gmail; prints one line a
  day. Runs anywhere, reads neither `OUTREACH_DB` nor the real Gmail folder, and builds no Gmail client.

There is no `resume` and no `approve`: resuming after a hard block is his click in the app, and approving is the
app's route only. `run`, `whoami` and `auth` call `live_checkout.require_live` before they open a file, a store or a
client; no flag, variable or file skips it. Exit code: 0 when the tick was `ok` (or another tick held the lock), 1
when it was partial or failed, or the command failed (the message on stderr).
"""
import argparse
import dataclasses
import os
import random
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import datetime, timezone

import live_checkout
from outreach.drafts.checks import load_banned
from outreach.sending.config import load_config
from outreach.sending.gmail import GmailClient
from outreach.sending.mail import GmailError
from outreach.sending.notify import mac_notifier
from outreach.sending.tick import BUSY_LINE, TickDeps, run_tick
from outreach.store import StoreError, connect, db_path, describe_error
from outreach.store.timefmt import ISO_FMT

__all__ = ["main", "launchctl_loaded"]

_EXPECTED = (ValueError, OSError, StoreError, sqlite3.Error, GmailError)
LAUNCHCTL_TIMEOUT_S = 10


def launchctl_loaded(label: str) -> Callable[[], bool]:
    """A check that the launchd job `label` is loaded for this user: `launchctl print gui/<uid>/<label>` exits 0.
    The check raises when `launchctl` cannot be run; the tick then takes the job as loaded (the safe answer)."""
    def loaded() -> bool:
        target = f"gui/{os.getuid()}/{label}"
        done = subprocess.run(["launchctl", "print", target], capture_output=True, timeout=LAUNCHCTL_TIMEOUT_S,
                              check=False)
        return done.returncode == 0
    return loaded


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m outreach.sending", description="The new sender (#14).")
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="one tick: read Gmail, settle, send at most one email")
    run.add_argument("--dry-run", action="store_true", help="show what would happen and write nothing")
    sub.add_parser("status", help="the sender's state and why the next email waits (no Gmail call)")
    sub.add_parser("whoami", help="the address the Gmail token signs in as")
    sub.add_parser("auth", help="sign in to Gmail in the browser")
    sim = sub.add_parser("simulate", help="ten business days on a fake Gmail and a fake clock")
    sim.add_argument("--days", type=int, default=10, metavar="N")
    sim.add_argument("--seed", type=int, default=7, metavar="N")
    return p


def _say(text: str) -> None:
    print(text, file=sys.stderr)


def _exit_code(conn: sqlite3.Connection, report, dry_run: bool) -> int:
    if report.lines == (BUSY_LINE,):
        return 0
    if report.auth not in ("ok", "skipped"):
        return 1
    if dry_run:
        return 0
    row = conn.execute("SELECT status FROM job_run WHERE job = 'sender' ORDER BY run_id DESC LIMIT 1").fetchone()
    return 0 if row is not None and row["status"] == "ok" else 1


def _run(dry_run: bool) -> int:
    cfg = load_config()
    client = GmailClient()
    conn = connect(fullfsync=True)
    try:
        banned, _ = load_banned()  # an unusable list comes back empty and blocks every claim, with a notice
        deps = TickDeps(client=client, clock=lambda: datetime.now(timezone.utc), rng=random.Random(),
                        notify=mac_notifier, old_sender_loaded=launchctl_loaded(cfg.old_sender_label), banned=banned)
        report = run_tick(conn, deps, cfg, dry_run=dry_run)
        stamp = datetime.now(timezone.utc).strftime(ISO_FMT)
        print(f"{stamp} {'dry run' if dry_run else 'tick'}: auth {report.auth}, sent {len(report.sent)}")
        for line in report.lines:
            print(f"  {line}")
        return _exit_code(conn, report, dry_run)
    finally:
        conn.close()


def _status(now: datetime | None, old_sender_loaded: Callable[[], bool] | None) -> int:
    from outreach.sending.status import queue_schedule, sending_snapshot
    cfg = load_config()
    now = now or datetime.now(timezone.utc)
    check = old_sender_loaded or launchctl_loaded(cfg.old_sender_label)
    try:
        old = bool(check())
        old_note = ""
    except Exception as err:
        old, old_note = False, f"could not tell whether the old sender is loaded ({describe_error(err)})"
    conn = connect(db_path())  # refuses the live store outside the live checkout
    try:
        snap = sending_snapshot(conn, now, cfg, old_sender_loaded=old)
        schedule = queue_schedule(conn, now, cfg, old_sender_loaded=old)
    finally:
        conn.close()
    print(f"state: {snap.state}" + (f" ({snap.paused_reason})" if snap.paused_reason else ""))
    if snap.paused_at:
        print(f"paused at: {snap.paused_at}")
    if snap.gmail_message:
        print(f"gmail: {snap.gmail_message}")
    print(f"sent today: {snap.sent_today} of {snap.limit_today}")
    print(f"ramp: starts at {snap.start_limit} (level {snap.ramp_level} of {len(snap.ramp_levels)}), "
          f"{snap.next_limit_if_clean} next sending day if today is clean")
    if snap.last_step_down:
        print(f"last step down: {snap.last_step_down[0]} ({snap.last_step_down[1]})")
    print(f"signals: bounce rate {snap.bounce_rate_3d:.1%} over 3 days, {snap.gmail_errors_today} Gmail error(s) "
          f"today, {snap.stop_replies_today} stop reply(ies) today")
    if snap.imported_unverified:
        print(f"imported emails not found in Sent: {snap.imported_unverified}")
    if snap.next_send:
        message_id, when, zone = snap.next_send
        print(f"next send: message {message_id} at {when} (recipient in {zone})")
    else:
        print(f"next send: none ({snap.none_reason})")
    waiting: dict[str, int] = {}
    for verdict in schedule.values():
        waiting[verdict.reason] = waiting.get(verdict.reason, 0) + 1
    print("queue: " + (", ".join(f"{n} {reason}" for reason, n in sorted(waiting.items())) or "empty"))
    if old_note:
        print(old_note)
    return 0


def _whoami() -> int:
    try:
        print(GmailClient().profile().email)
    except GmailError as err:
        _say(f"Gmail sign-in is not working ({err}); run: python -m outreach.sending auth")
        return 1
    return 0


def _auth() -> int:
    from outreach.sending import gmail
    return gmail.main(["auth"])


def _simulate(days: int, seed: int) -> int:
    from outreach.sending.simulate import TWO_WEEKS, format_table, simulate
    if days < 1:
        raise ValueError("--days must be at least 1")
    result = simulate(dataclasses.replace(TWO_WEEKS, days=days), seed=seed)
    try:
        for line in format_table(result):
            print(line)
    finally:
        result.close()
    return 0


def main(argv: Sequence[str] | None = None, *, root: str | None = None, live_root: str | None = None,
         now: datetime | None = None, old_sender_loaded: Callable[[], bool] | None = None) -> int:
    """Run one command and return the exit code. `root` and `live_root` default to this checkout and the live one;
    `now` and `old_sender_loaded` stand in for the clock and `launchctl` in tests. Outside the live checkout `run`,
    `whoami` and `auth` raise SystemExit before they open a file, the store or a client."""
    args = _parser().parse_args(argv)
    what = {"run": "the sender", "whoami": "the Gmail sign-in check", "auth": "Gmail sign-in"}.get(args.command)
    if what is not None:
        live_checkout.require_live(what, root or live_checkout.REPO_ROOT, live_root or live_checkout.LIVE_ROOT)
    try:
        if args.command == "run":
            return _run(args.dry_run)
        if args.command == "status":
            return _status(now, old_sender_loaded)
        if args.command == "whoami":
            return _whoami()
        if args.command == "auth":
            return _auth()
        return _simulate(args.days, args.seed)
    except _EXPECTED as err:
        _say(f"error: {describe_error(err)}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
