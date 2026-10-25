"""The sender's simulated weeks: ten business days of real ticks on a temporary store, a fake Gmail and a fake clock.

This is issue #14's "Done when". `simulate(scenario)` seeds companies and people with drafted emails, then ticks the
real `run_tick` every couple of minutes from 07:00 to 20:00 New York on each business day, with the clock jumping
to the next moment anything can change: a scripted event, a waiting message's earliest time, the sender's own gap,
or 30 minutes on (never less than 2, the launchd interval). Nothing here sleeps, opens a socket or reads the real
clock, `OUTREACH_DB` or the real Gmail folder: the store is a new temporary file, Gmail is `FakeGmail`.

Events stand in for people and for Gmail. Approve, resume, tag, no-go and follow-up drafting stand in for Devanshu
and Claude, and write the store directly with seed SQL (the simulation only: the product code has no such path).
Reply, bounce, send error and sign-out script what Gmail does. A person is named `c<company>p<index>` (company 0,
person 0 is `c0p0`), or by `nth`, the position of a first email among those sent so far.

`TWO_WEEKS` is the scenario the command line prints and the main test asserts: Monday 2026-11-02 (the day after
daylight saving ends) to Friday 2026-11-13, with a policy bounce on day 4, a Gmail 429 on day 8, a reply that holds
a company on day 7, and every first email of day 1 followed up on day 8.
"""
import random
import re
import shutil
import sqlite3
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from outreach.install import install
from outreach.sending import calendar as cal
from outreach.sending import ramp, signals
from outreach.sending.config import SendConfig, load_config
from outreach.sending.fakegmail import FakeClock, FakeGmail
from outreach.sending.mail import GmailError, Sent
from outreach.sending.status import queue_schedule
from outreach.sending.tick import TickDeps, run_tick
from outreach.store import connect, transaction
from outreach.store.timefmt import ISO_FMT

__all__ = ["Event", "Scenario", "DayRow", "SendRecord", "PersonInfo", "SimResult", "simulate", "TWO_WEEKS", "ny",
           "format_table", "SIM_BANNED"]

_NY = ZoneInfo("America/New_York")
TICK = timedelta(minutes=2)       # launchd's StartInterval: no two ticks closer than this
STEP = timedelta(minutes=30)      # the longest the clock jumps when nothing is due sooner
DAY_START, DAY_END = "07:00", "20:00"
STAMP = "2026-10-30T12:00:00Z"    # before every simulated moment
ROLES = ("engineer", "manager", "founder")
ACTIONS = ("approve", "reply", "bounce", "send_error", "resume", "tag", "no_go", "draft_followups", "sign_out",
           "sign_in")
# What the pre-claim check reads in place of DevVault's list (the sender blocks every claim without one).
SIM_BANNED = [("never matches anything the simulation writes", re.compile(r"\bzzz-never-written-zzz\b", re.I))]


def ny(day: date, clock: str) -> datetime:
    """The New York wall-clock moment `clock` ("HH:MM") on `day`, as an aware datetime."""
    hour, minute = clock.split(":")
    return datetime(day.year, day.month, day.day, int(hour), int(minute), tzinfo=_NY)


def _utc(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime(ISO_FMT)


@dataclass(frozen=True)
class Event:
    """Something that happens at `at` (aware). It is applied just before the first tick at or after `at`.
    `action` is one of `ACTIONS`; `args` as below.

    - approve: `count` (the next n drafted first emails, company by company) or `people` (keys)
    - reply: `person` or `nth`; `new_thread` files the reply outside the thread
    - bounce: `person` or `nth`; `dsn` is hard (default), policy, delay or limit
    - send_error: `kind` (a GmailError kind) and `status`: the next send fails (nothing is accepted)
    - resume: clears a pause (his click in the app)
    - tag: `person` or `nth`, `kind` (default asked_to_stop): his tag on a reply already read
    - no_go: `person`: a no-go row on that person
    - draft_followups: Claude drafts and he approves every follow-up that is due
    - sign_out, sign_in: the Gmail token dies, or is replaced
    """
    at: datetime
    action: str
    args: Mapping = field(default_factory=dict)

    def __post_init__(self):
        if self.action not in ACTIONS:
            raise ValueError(f"unknown simulation action {self.action!r}")
        if self.at.tzinfo is None:
            raise ValueError("an event needs an aware time")


@dataclass(frozen=True)
class Scenario:
    start: date                       # a business day
    days: int                         # business days to run
    companies: int
    people_per_company: int
    zones: tuple[str, ...]            # company n sits in zones[n % len(zones)]
    events: tuple[Event, ...] = ()
    held_back: frozenset[int] = frozenset()   # companies `approve count` skips (only named events approve them)
    roles: Mapping[str, str] = field(default_factory=dict)  # person key to role kind (default engineer, manager, founder)


@dataclass(frozen=True)
class PersonInfo:
    key: str
    company: int
    index: int
    name: str
    email: str
    role_kind: str
    zone: str
    person_id: int
    touch_id: int
    message_id: int      # the first email
    subject: str


@dataclass(frozen=True)
class DayRow:
    day: date
    start_limit: int
    effective_limit: int
    sent: int
    outcome: str | None


@dataclass(frozen=True)
class SendRecord:
    at: datetime         # when Gmail took it (aware UTC)
    company: int
    person: str
    role_kind: str
    seq: int
    message_id: str      # the Message-ID it went out under
    in_reply_to: str | None
    thread_id: str       # the Gmail thread it landed in
    to: str
    zone: str


@dataclass
class SimResult:
    scenario: Scenario
    cfg: SendConfig
    days: tuple[DayRow, ...]
    sends: tuple[SendRecord, ...]
    conn: sqlite3.Connection
    fake: FakeGmail
    people: dict[str, PersonInfo]
    notices: list[tuple[datetime, str, str]]
    folder: Path
    ticks: int
    seconds: float

    def close(self) -> None:
        """Close the store and remove its folder."""
        try:
            self.conn.close()
        finally:
            shutil.rmtree(self.folder, ignore_errors=True)


# -- the text of the emails (a plain generator, so every colleague's email differs) ----------------------------------

TOPICS = ("queue rewrite", "billing service", "search ranking", "edge cache", "data pipeline", "deploy tooling",
          "ingest service", "feature store", "payments ledger", "rate limiter")
ANGLES = ("your {t} work", "a question on {t}", "{t} and a referral", "the {t} at your team")
OPENERS = ("Last year I built", "At work I shipped", "In class I wrote", "I still run", "In a lab I tuned",
           "I once rewrote", "On a team I owned", "I helped ship", "For a friend I made", "I profiled",
           "At school I made", "In a job I ran")
MIDDLES = ("a job queue in Go", "a bill sync in Python", "an edge cache on Redis", "a search index in Rust",
           "a stats agent in Go", "a retry tool in Python", "a type check in TS", "a log relay in Rust",
           "a rate cap in Go", "a bulk load in Python", "a feed parser in Go", "a cron host in Rust")
CLOSERS = ("that ran for months with no page.", "that cut p99 by half.", "that three teams then used.",
           "that replaced a cron job.", "that I still keep up.", "that made on call calm.",
           "that held through a spike.", "that shipped on time.", "that taught me to measure.",
           "that users never saw fail.", "that fit on one box.", "that my lead praised.")
ADDITIONS = ("I just posted a short write-up on how I tested a retry tool under load",
             "I added a benchmark to the project I linked, with numbers in the readme",
             "I fixed a bug in a library your team uses and the patch was merged",
             "I finished a small tool that replays live traffic against a staging build",
             "I wrote up what I learned running a queue through a failure drill")
PORTFOLIO = "https://www.devanshuchicholikar.com"


INTROS = ("I read about the {t} work at {c} and it is the backend work I like best.",
          "A post on how {c} runs its {t} made me want to write to you.",
          "I have followed the {t} at {c} for a while and had one question.",
          "Your team's {t} at {c} is close to what I built in my own projects.")
ASKS = ("Would you be open to referring me?", "Could you point me to the right person to ask?",
        "Is a short referral something you could consider?", "Might you pass my name to the hiring team?")


def _first_email(rng: random.Random, name: str, company: str, index: int) -> tuple[str, str]:
    """A first email. Colleagues at one company draw from different slices of each pool (by `index`), so their
    emails differ enough to pass the near-identical check."""
    topic = TOPICS[rng.randrange(len(TOPICS))]
    subject = f"{ANGLES[index % len(ANGLES)].format(t=topic).capitalize()} at {company}"

    def pick(pool: tuple) -> str:
        return pool[rng.randrange(len(pool) // 4) * 4 + index % 4]

    work = " ".join(f"{pick(OPENERS)} {pick(MIDDLES)} {pick(CLOSERS)}" for _ in range(4))
    body = (f"Hi {name.split()[0]},\n\n{INTROS[index % 4].format(t=topic, c=company)} {work}\n\n"
            f"My work is at {PORTFOLIO} and one project is at https://github.com/devanshu/p{rng.randrange(10 ** 6)}.\n\n"
            f"{ASKS[index % 4]}\n\nThanks,\nDevanshu")
    return subject, body


def _followup(rng: random.Random, name: str, first_subject: str, index: int) -> tuple[str, str]:
    """A follow-up: a short note that adds one new thing, different for each colleague (`index`)."""
    extra = ADDITIONS[index % len(ADDITIONS)]
    body = (f"Hi {name.split()[0]},\n\nOne thing I should have said: {extra} (number {rng.randrange(10 ** 6)}). "
            f"{ASKS[(index + 1) % 4]}\n\nThanks,\nDevanshu")
    return "Re: " + first_subject, body


# -- the run ---------------------------------------------------------------------------------------------------------

class _Run:
    def __init__(self, scenario: Scenario, seed: int, cfg: SendConfig):
        self.scenario, self.cfg = scenario, cfg
        self.rng = random.Random(seed)
        self.text = random.Random(seed + 1)   # the emails' words, apart from the sender's own draws
        self.folder = Path(tempfile.mkdtemp(prefix="outreach-sim-"))
        path = str(self.folder / "outreach.db")
        install(path)
        self.conn = connect(path)
        self.conn.execute("PRAGMA fullfsync = ON")      # the tick refuses a connection without it
        self.conn.execute("PRAGMA synchronous = OFF")   # a temporary store: nothing here needs to survive a crash
        self.days = cal_days(scenario.start, scenario.days, cfg)
        self.clock = FakeClock(ny(self.days[0], DAY_START))
        self.fake = FakeGmail(clock=self.clock, reconcile_after_minutes=cfg.reconcile_after_minutes)
        self.notices: list[tuple[datetime, str, str]] = []
        self.people: dict[str, PersonInfo] = {}
        self.by_email: dict[str, PersonInfo] = {}
        self.pending = sorted(scenario.events, key=lambda e: e.at)
        self.ticks = 0
        self.deps = TickDeps(client=self.fake, clock=self.clock, rng=self.rng, notify=self._notice,
                             old_sender_loaded=lambda: False, banned=SIM_BANNED)

    def _notice(self, title: str, text: str) -> None:
        self.notices.append((self.clock(), title, text))

    # seeding

    def seed(self) -> None:
        s = self.scenario
        with transaction(self.conn):
            self._seed_import()
            for c in range(s.companies):
                company = f"Simco {c:03d}"
                company_id = self.conn.execute("INSERT INTO company(name, status_at, created_at) VALUES (?, ?, ?)",
                                               (company, STAMP, STAMP)).lastrowid
                zone = s.zones[c % len(s.zones)]
                for p in range(s.people_per_company):
                    self._seed_person(company_id, company, c, p, zone)

    def _seed_import(self) -> None:
        """One email the old mailer sent, found in Gmail's Sent under its own Message-ID: the proof that Gmail
        keeps the Message-ID the sender sets, without which the sender never sends (it is the switch's own check)."""
        conn = self.conn
        company = conn.execute("INSERT INTO company(name, status_at, created_at) VALUES ('Simco imported', ?, ?)",
                               (STAMP, STAMP)).lastrowid
        person = conn.execute(
            "INSERT INTO person(company_id, name, role_kind, email, tz, channel, added_by, created_at, updated_at) "
            "VALUES (?, 'Old Contact', 'engineer', 'old@imported.example', 'America/New_York', 'email', 'import', "
            "?, ?)", (company, STAMP, STAMP)).lastrowid
        rfc, at = "<old.1@devanshu.local>", self.clock() - timedelta(days=3)
        meta = self.fake.add_to_sent("old@imported.example", "An email from last week", message_id=rfc, at=at)
        stamp = _utc(at)
        touch = conn.execute(
            "INSERT INTO touch(person_id, origin, first_sent_at, first_rfc_id, gmail_thread_id, created_at, "
            "updated_at) VALUES (?, 'import', ?, ?, ?, ?, ?)", (person, stamp, rfc, meta.thread_id, stamp, stamp)).lastrowid
        conn.execute(
            "INSERT INTO message(touch_id, seq, state, subject, body, sent_at, rfc_message_id, gmail_message_id, "
            "saved_by, created_at, updated_at) VALUES (?, 0, 'sent', 'An email from last week', 'An old body.', ?, ?, "
            "?, 'import', ?, ?)", (touch, stamp, rfc, meta.gmail_message_id, stamp, stamp))

    def _seed_person(self, company_id: int, company: str, c: int, p: int, zone: str) -> None:
        key = f"c{c}p{p}"
        role = self.scenario.roles.get(key, ROLES[p % len(ROLES)])
        name, email = f"Person {c:03d}{p}", f"{key}@sim{c}.example"
        subject, body = _first_email(self.text, name, company, p)
        conn = self.conn
        person = conn.execute(
            "INSERT INTO person(company_id, name, role_kind, email, tz, channel, added_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'email', 'devanshu', ?, ?)", (company_id, name, role, email, zone, STAMP,
                                                                  STAMP)).lastrowid
        touch = conn.execute("INSERT INTO touch(person_id, created_at, updated_at) VALUES (?, ?, ?)",
                             (person, STAMP, STAMP)).lastrowid
        message = conn.execute(
            "INSERT INTO message(touch_id, seq, state, subject, body, created_at, updated_at) "
            "VALUES (?, 0, 'drafted', ?, ?, ?, ?)", (touch, subject, body, STAMP, STAMP)).lastrowid
        info = PersonInfo(key, c, p, name, email, role, zone, person, touch, message, subject)
        self.people[key] = info
        self.by_email[email] = info

    # events

    def apply_due(self) -> None:
        end = ny(self.days[-1], DAY_END)
        while self.pending and self.pending[0].at <= self.clock() and self.pending[0].at <= end:
            self._apply(self.pending.pop(0))

    def _apply(self, event: Event) -> None:
        getattr(self, "_do_" + event.action)(event.args)

    def _first_emails(self) -> list[tuple]:
        return [(m, s) for m, s, _ in self.fake.sent if m.in_reply_to is None]

    def _target(self, args: Mapping, what: str) -> tuple[PersonInfo, Sent]:
        firsts = self._first_emails()
        if "nth" in args:
            try:
                outgoing, sent = firsts[args["nth"]]
            except IndexError:
                raise ValueError(f"{what}: no first email number {args['nth']} was sent yet") from None
            return self.by_email[outgoing.to], sent
        info = self.people[args["person"]]
        for outgoing, sent in firsts:
            if outgoing.to == info.email:
                return info, sent
        raise ValueError(f"{what}: no email to {info.key} was sent yet")

    def _do_approve(self, args: Mapping) -> None:
        stamp = _utc(self.clock())
        if "people" in args:
            ids = [self.people[key].message_id for key in args["people"]]
        else:
            held = self.scenario.held_back
            rows = self.conn.execute("SELECT m.message_id, p.email FROM message m JOIN touch t ON t.touch_id = m.touch_id "
                                     "JOIN person p ON p.person_id = t.person_id WHERE m.state = 'drafted' AND m.seq = 0 "
                                     "ORDER BY m.message_id").fetchall()
            ids = [r["message_id"] for r in rows if self.by_email[r["email"]].company not in held][:args["count"]]
        with transaction(self.conn):
            for message_id in ids:
                self.conn.execute("UPDATE message SET state = 'approved', approved_at = ?, updated_at = ? "
                                  "WHERE message_id = ? AND state = 'drafted'", (stamp, stamp, message_id))

    def _do_reply(self, args: Mapping) -> None:
        info, sent = self._target(args, "reply")
        self.fake.deliver_reply(sent, f"{info.name} <{info.email}>", new_thread=bool(args.get("new_thread")))

    def _do_bounce(self, args: Mapping) -> None:
        _, sent = self._target(args, "bounce")
        self.fake.deliver_bounce(sent, dsn=args.get("dsn", "hard"))

    def _do_send_error(self, args: Mapping) -> None:
        self.fake.fail_next_send(GmailError(args.get("kind", "rate_limit"), args.get("status", 429),
                                            args.get("detail", "simulated")))

    def _do_resume(self, args: Mapping) -> None:
        stamp = _utc(self.clock())
        with transaction(self.conn):
            self.conn.execute("UPDATE sender SET paused = 0, pause_kind = NULL, paused_reason = '', paused_at = NULL, "
                              "updated_at = ?, revision = revision + 1 WHERE sender_id = 1", (stamp,))

    def _do_tag(self, args: Mapping) -> None:
        info, _ = self._target(args, "tag")
        state = self.conn.execute("SELECT state FROM touch WHERE touch_id = ?", (info.touch_id,)).fetchone()[0]
        if state != "replied":
            raise ValueError(f"tag: {info.key} has not been read as a reply yet (touch is {state})")
        with transaction(self.conn):
            self.conn.execute("UPDATE touch SET reply_kind = ?, updated_at = ?, revision = revision + 1 "
                              "WHERE touch_id = ?", (args.get("kind", "asked_to_stop"), _utc(self.clock()),
                                                     info.touch_id))

    def _do_no_go(self, args: Mapping) -> None:
        info = self.people[args["person"]]
        with transaction(self.conn):
            self.conn.execute("INSERT INTO no_go(person_id, reason, source, created_at) VALUES (?, 'said_no', "
                              "'devanshu', ?)", (info.person_id, _utc(self.clock())))

    def _do_draft_followups(self, args: Mapping) -> None:
        """Claude drafts each due follow-up and he approves it (one SQL stand-in for both clicks)."""
        stamp = _utc(self.clock())
        today = cal.ny_date(self.clock(), self.cfg).isoformat()
        with transaction(self.conn):
            rows = self.conn.execute(
                "SELECT m.message_id, p.email FROM message m JOIN touch t ON t.touch_id = m.touch_id "
                "JOIN person p ON p.person_id = t.person_id WHERE m.state = 'planned' AND m.due_on <= ? "
                "ORDER BY m.message_id", (today,)).fetchall()
            for row in rows:
                info = self.by_email[row["email"]]
                subject, body = _followup(self.text, info.name, info.subject, info.index)
                self.conn.execute("UPDATE message SET state = 'drafted', subject = ?, body = ?, updated_at = ? "
                                  "WHERE message_id = ?", (subject, body, stamp, row["message_id"]))
                self.conn.execute("UPDATE message SET state = 'approved', approved_at = ? WHERE message_id = ?",
                                  (stamp, row["message_id"]))

    def _do_sign_out(self, args: Mapping) -> None:
        self.fake.fail_profile(GmailError("auth", 401, "invalid_grant"))

    def _do_sign_in(self, args: Mapping) -> None:
        self.fake.fail_profile(None)

    # the clock

    def next_time(self, now: datetime, end: datetime) -> datetime:
        """The earliest moment anything can change, at least one tick on and no later than `end`."""
        candidates = [now + STEP, end]
        if self.pending:
            candidates.append(self.pending[0].at)
        gap = self.conn.execute("SELECT next_send_at FROM sender WHERE sender_id = 1").fetchone()[0]
        gap_at = datetime.strptime(gap, ISO_FMT).replace(tzinfo=timezone.utc) if gap else None
        if gap_at is not None and gap_at > now:
            candidates.append(gap_at)  # while the gap runs, nothing sooner can send
        else:
            candidates += [v.earliest_at for v in queue_schedule(self.conn, now, self.cfg).values()
                           if v.earliest_at is not None and v.earliest_at > now]
        return min(max(min(candidates), now + TICK), end)

    def run(self) -> None:
        for day in self.days:
            end = ny(day, DAY_END)
            self.clock.set(ny(day, DAY_START))
            while True:
                self.apply_due()
                run_tick(self.conn, self.deps, self.cfg)
                self.ticks += 1
                now = self.clock()
                if now >= end:
                    break
                self.clock.set(self.next_time(now, end))
        # The first tick of the next date closes the last day (its outcome is frozen then), as it does for real.
        closing = datetime.combine(self.days[-1] + timedelta(days=1), datetime.min.time(), tzinfo=_NY) + timedelta(minutes=5)
        self.clock.set(closing)
        run_tick(self.conn, self.deps, self.cfg)
        self.ticks += 1

    # the result

    def day_rows(self) -> tuple[DayRow, ...]:
        rows = []
        for day in self.days:
            row = self.conn.execute("SELECT send_limit, outcome FROM send_day WHERE day = ?", (day.isoformat(),)).fetchone()
            rows.append(DayRow(day=day, start_limit=row["send_limit"] if row else 0,
                               effective_limit=ramp.effective_limit(self.conn, day, self.cfg),
                               sent=signals.sends_on(self.conn, day, self.cfg),
                               outcome=row["outcome"] if row else None))
        return tuple(rows)

    def send_records(self) -> tuple[SendRecord, ...]:
        out = []
        for outgoing, sent, at in self.fake.sent:
            info = self.by_email.get(outgoing.to)
            if info is None:
                continue
            out.append(SendRecord(at=at, company=info.company, person=info.key, role_kind=info.role_kind,
                                  seq=1 if outgoing.in_reply_to else 0, message_id=outgoing.message_id,
                                  in_reply_to=outgoing.in_reply_to, thread_id=sent.thread_id, to=outgoing.to,
                                  zone=info.zone))
        return tuple(out)


def cal_days(start: date, n: int, cfg: SendConfig) -> list[date]:
    """The first `n` business days from `start`, which must be one."""
    if not cal.is_business_day(start, cfg):
        raise ValueError(f"{start} is not a business day")
    days = [start]
    while len(days) < n:
        days.append(cal.add_business_days(days[-1], 1, cfg))
    return days


def simulate(scenario: Scenario, *, seed: int = 7, cfg: SendConfig | None = None) -> SimResult:
    """Run the scenario and return what happened. The caller closes the result (`SimResult.close`) when done with
    its store."""
    cfg = cfg or load_config()
    began = time.monotonic()
    run = _Run(scenario, seed, cfg)
    try:
        run.seed()
        run.run()
        return SimResult(scenario=scenario, cfg=cfg, days=run.day_rows(), sends=run.send_records(), conn=run.conn,
                         fake=run.fake, people=run.people, notices=run.notices, folder=run.folder, ticks=run.ticks,
                         seconds=time.monotonic() - began)
    except BaseException:
        run.conn.close()
        shutil.rmtree(run.folder, ignore_errors=True)
        raise


def format_table(result: SimResult) -> list[str]:
    """One line per day, then the totals: what the command line prints."""
    lines = ["day                 start  effective  sent  outcome"]
    for row in result.days:
        lines.append(f"{row.day.isoformat()} {row.day.strftime('%a')}  {row.start_limit:>5}  {row.effective_limit:>9}  "
                     f"{row.sent:>4}  {row.outcome or '-'}")
    firsts = sum(1 for s in result.sends if s.seq == 0)
    lines.append(f"{len(result.sends)} emails sent ({firsts} first emails, {len(result.sends) - firsts} follow-ups), "
                 f"{len(result.notices)} notice(s), {result.ticks} ticks in {result.seconds:.1f} s")
    return lines


# -- the two weeks ---------------------------------------------------------------------------------------------------

def _two_weeks() -> Scenario:
    cfg = load_config()
    days = cal_days(date(2026, 11, 2), 10, cfg)
    events: list[Event] = []

    def at(n: int, clock: str) -> datetime:
        return ny(days[n - 1], clock)

    # He approves a batch every morning, a few more than the day's limit so the queue stays fuller than it.
    counts = [28, 38, 48, 58, 48, 58, 68, 78, 68, 78]
    for n, count in enumerate(counts, start=1):
        if n == 1:   # a recruiter and an engineer at once (company 0), and one person at a Pacific company (2)
            events.append(Event(at(1, "07:00"), "approve", {"people": ["c0p0", "c0p1", "c2p0"]}))
        if n == 7:   # her colleagues, approved before she answers (Pacific hours begin at 11:30 New York time)
            events.append(Event(at(7, "07:00"), "approve", {"people": ["c2p1", "c2p2"]}))
        if n == 8:   # the first emails of day 1 are due: Claude drafts, he approves
            events.append(Event(at(8, "07:00"), "draft_followups"))
        events.append(Event(at(n, "07:01"), "approve", {"count": count}))
    events += [
        Event(at(4, "11:00"), "bounce", {"nth": 3, "dsn": "policy"}),
        Event(at(7, "10:00"), "reply", {"person": "c2p0"}),
        Event(at(8, "13:00"), "send_error", {"kind": "rate_limit", "status": 429}),
        Event(at(9, "08:00"), "resume"),
    ]
    return Scenario(start=days[0], days=10, companies=150, people_per_company=4,
                    zones=("America/New_York", "America/Chicago", "America/Los_Angeles", "Europe/London"),
                    events=tuple(events), held_back=frozenset({0, 2}), roles={"c0p1": "recruiter"})


TWO_WEEKS = _two_weeks()
