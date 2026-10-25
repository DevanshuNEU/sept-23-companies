"""Progress, findings and the win log, read from `outreach_touch` (an undone touch is no touch, so every read
filters `undone_at IS NULL`).

The counting rules are Task 9's (`outreach.outcomes`): `funnel`, `base_rates`, `is_quiet`, `findings`, the win
helpers and the New York windows. This module loads rows, adds the words and the dates the page shows, and reads the
two stores of record around them: `win_seen` and the `weights_changed` events.

A funnel row is a count with its denominator (`from`), never a rate: accepts and replies of every channel are counted
against the period's `linkedin_request` requests, so a rate made from one row would mix channels. `base` is the
honest all-time rate and the page makes it from counts.
"""
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from outreach.api.config import ApiConfig
from outreach.api.errors import ApiProblem
from outreach.api.idempotency import WriteCtx
from outreach.api.models.company import CompanyRef
from outreach.api.models.progress import (
    BaseRates, EffortLine, Finding, FindingChange, FindingCount, FindingsView, FunnelFrom, FunnelStage,
    ProgressPeriod, ProgressView, TodayProgress, Win, WinCounts, WinsSeen, WinsView,
)
from outreach.api.queries.company import merge_parents, person_ref, survivor_of
from outreach.api.queries.outcomes import CHANNEL_WORDS, LANE_WORDS, by_ids, company_refs, people_by_ids
from outreach.outcomes import report
from outreach.outcomes.wins import WIN_KINDS, decode_win_id, is_log_win, is_recognition, win_id, win_line
from outreach.outcomes.windows import month_bounds, ny_day, week_bounds
from outreach.store.timefmt import ISO_FMT

NY = ZoneInfo("America/New_York")
WIN_COLUMNS = {"accepted": "accepted_at", "replied": "replied_at", "referred": "referred_at",
               "interview": "interview_at", "offer": "offer_at"}
OUTCOME_DATES = tuple(WIN_COLUMNS.values()) + ("quiet_at",)
STAGE_LABELS = {"requests": "Requests", "accepts": "Accepts", "replies": "Replies", "referrals": "Referrals",
                "interviews": "Interviews"}
# A finding needs finding_group_min touches in each group (Task 9). A group under this many times that minimum is
# still small: a handful of replies more or less would change its rate a lot, so the finding carries a caution.
CAUTION_FACTOR = 2
MAX_CHANGES = 20
LINKEDIN = ("linkedin_request", "linkedin_message")
UNKNOWN_COMPANY = "Unknown company"  # the name a win shows when its company row cannot be read
MAX_TOUCH_ID = 2 ** 62  # a win id past this cannot name a touch the store holds (and SQLite integers stop at 2 ** 63)


def live_touches(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every touch that counts, oldest first."""
    return conn.execute("SELECT * FROM outreach_touch WHERE undone_at IS NULL ORDER BY sent_at, touch_id").fetchall()


def _since(rows: list[sqlite3.Row], now: str) -> str:
    """The New York date of the first touch, else today."""
    return ny_day(min(r["sent_at"] for r in rows)) if rows else ny_day(now)


def _on(stamp: str | None, day: str) -> bool:
    return stamp is not None and ny_day(stamp) == day


# ---------------------------------------------------------------------------------------------- the plan's counts

def plan_counts(conn: sqlite3.Connection, today: str) -> tuple[int, int]:
    """`(planned, done)` for the people plan of the New York date `today`: exactly `PlanView.total` and
    `PlanView.done`, from the plan reader's own rows (`outreach.api.queries.plan.plan_rows`), so the shell, progress
    and the plan page always agree. Planned is every row on the plan, a skipped one included (it is still today's
    row); a removed row is not on the plan. A day with no plan, or a list the lean Today stored, is `(0, 0)`."""
    from outreach.api.queries import plan  # inside: the plan reader imports this module
    from outreach.plan.config import default_plan_config

    rows = plan.plan_rows(conn, today, default_plan_config()) or []
    return len(rows), sum(1 for r in rows if r.state == "done")


# ---------------------------------------------------------------------------------------------- progress

def _period(label: str, bounds: tuple[str, str], rows: list[sqlite3.Row], cfg: ApiConfig) -> ProgressPeriod:
    counts = report.funnel(rows, bounds)
    by_stage = {c.stage: c.count for c in counts}
    stages = [FunnelStage(stage=c.stage, label=STAGE_LABELS[c.stage], count=c.count,
                          from_=None if c.of_stage is None else FunnelFrom(stage=c.of_stage, count=c.of or 0))
              for c in counts]

    def sent(channel: str) -> int:
        return sum(1 for r in rows if r["channel"] == channel and report.in_period(r["sent_at"], bounds))

    effort = [EffortLine(label="Requests sent", count=by_stage["requests"]),
              EffortLine(label="Applications sent", count=sent("application")),
              EffortLine(label="Emails sent", count=sent("email"))]
    return ProgressPeriod(label=label, from_=bounds[0], to=bounds[1], stages=stages, effort=effort,
                          enough=by_stage["requests"] >= cfg.min_requests, quiet=report.is_quiet(rows, bounds))


def progress_view(conn: sqlite3.Connection, now: str, cfg: ApiConfig) -> ProgressView:
    rows = live_touches(conn)
    today = ny_day(now)
    planned, done = plan_counts(conn, today)
    base = report.base_rates(rows, cfg)
    return ProgressView(
        as_of=now,
        today=TodayProgress(
            planned=planned, done=done,
            requests=sum(1 for r in rows if r["channel"] == "linkedin_request" and _on(r["sent_at"], today)),
            applications=sum(1 for r in rows if r["channel"] == "application" and _on(r["sent_at"], today)),
            accepted=sum(1 for r in rows if _on(r["accepted_at"], today)),
            replied=sum(1 for r in rows if _on(r["replied_at"], today))),
        week=_period("This week", week_bounds(now), rows, cfg),
        month=_period("This month", month_bounds(now), rows, cfg),
        base=BaseRates(since=_since(rows, now), requests=base.requests, accepts=base.accepts, replies=base.replies,
                       median_days_to_reply=base.median_days_to_reply, enough=base.enough,
                       min_requests=cfg.min_requests),
    )


# ---------------------------------------------------------------------------------------------- findings

ROLE_KIND_WORDS = {"engineer": "engineers", "manager": "managers", "founder": "founders", "recruiter": "recruiters",
                   "alum": "alumni", "other": "other contacts"}
CHANNEL_PLURAL = {"linkedin_request": "LinkedIn requests", "linkedin_message": "LinkedIn messages", "email": "emails",
                  "application": "applications", "hn_reply": "HN replies", "other": "other touches"}
BUCKET_WORDS = {"above_zero": "above zero", "zero": "at zero", "below_zero": "below zero"}


def group_phrase(dimension: str, group: str) -> str:
    """A group as a plain noun phrase: "touches to engineers", "touches that named the role"."""
    if dimension == "role_kind":
        return f"touches to {ROLE_KIND_WORDS.get(group) or group.replace('_', ' ')}"
    if dimension == "lane":
        return f"touches {LANE_WORDS.get(group) or 'in the ' + group.replace('_', ' ') + ' lane'}"
    if dimension == "role_named":
        return "touches that named the role" if group == "named" else "touches that did not name the role"
    if dimension == "channel":
        return CHANNEL_PLURAL.get(group) or CHANNEL_WORDS.get(group, group).lower()
    if dimension.startswith("signal:"):
        return f"touches with {dimension[len('signal:'):].replace('_', ' ')} {BUCKET_WORDS.get(group, group)}"
    return f"{dimension.replace('_', ' ')} {group.replace('_', ' ')}"


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def weight_changes(conn: sqlite3.Connection) -> list[dict]:
    """The `weights_changed` events, newest first, each as `{at, text, signals}`. Read by kind: the event key carries
    the versions and a run id, and nothing here parses it. A detail that is not what #6 writes is read as far as it
    goes (no text becomes a plain sentence from the versions)."""
    found = []
    for r in conn.execute("SELECT event_id, at, detail FROM event WHERE entity = 'score' AND kind = 'weights_changed' "
                          "ORDER BY at DESC, event_id DESC"):
        try:
            detail = json.loads(r["detail"])
        except ValueError:
            detail = None
        detail = detail if isinstance(detail, dict) else {}
        text = detail.get("text")
        if not isinstance(text, str) or not text.strip():
            old, new = detail.get("old_version"), detail.get("new_version")
            text = (f"The ranker changed from {old} to {new}." if old and new else "The ranker weights changed.")
        changed = detail.get("changed")
        found.append({"at": r["at"], "text": text.strip(), "signals": set(changed) if isinstance(changed, dict) else set()})
    return found


def _caution(draft: report.FindingDraft, cfg: ApiConfig) -> str | None:
    """A plain caution when a group is small, from the counts alone: a group under `CAUTION_FACTOR` times the group
    minimum. Task 9 deliberately adds none, so the rule is here. It names the smaller group's size."""
    threshold = CAUTION_FACTOR * cfg.finding_group_min
    smallest = min((draft.high_n, draft.high_group, "high"), (draft.low_n, draft.low_group, "low"))
    if smallest[0] >= threshold:
        return None
    return (f"One group has only {smallest[0]} touches so far, so a few more replies could change this. "
            f"It is a lead, not a rule.")


def _month_for_report(now: str) -> str:
    today = date.fromisoformat(ny_day(now))
    if today.day <= 7:  # the first week still reports the month that just ended
        today = date(today.year - 1, 12, 1) if today.month == 1 else date(today.year, today.month - 1, 1)
    return today.replace(day=1).isoformat()


def _newest_input(rows: list[sqlite3.Row]) -> str:
    stamps = [r[c] for r in rows for c in ("sent_at",) + OUTCOME_DATES if r[c] is not None]
    return ny_day(max(stamps))


def findings_view(conn: sqlite3.Connection, now: str, cfg: ApiConfig) -> FindingsView:
    rows = live_touches(conn)
    drafts = report.findings(rows, cfg)
    changes = weight_changes(conn)
    findings = []
    for d in drafts:
        changed = None
        if d.dimension.startswith("signal:"):
            name = d.dimension[len("signal:"):]
            changed = next((c["text"] for c in changes if name in c["signals"]), None)
        high, low = group_phrase(d.dimension, d.high_group), group_phrase(d.dimension, d.low_group)
        text = (f"{_sentence(high)} got replies and {low} got none." if d.ratio is None
                else f"{_sentence(high)} got replies at least twice as often as {low}.")
        findings.append(Finding(
            id=int(d.id, 16), text=text, caution=_caution(d, cfg), changed=changed,
            counts=[FindingCount(label=_sentence(high), count=d.high_replies, of=d.high_n),
                    FindingCount(label=_sentence(low), count=d.low_replies, of=d.low_n)]))
    return FindingsView(
        as_of=now, month=_month_for_report(now), reported_on=_newest_input(rows) if findings else None,
        findings=findings, changes=[FindingChange(at=c["at"], text=c["text"]) for c in changes[:MAX_CHANGES]])


# ---------------------------------------------------------------------------------------------- wins

@dataclass(frozen=True)
class Candidate:
    """One outcome of one touch that is, or was, worth a moment: it is in the log for good (`log`) or only while
    unseen (recognition)."""
    touch: sqlite3.Row
    kind: str
    at: str
    reply_kind: str | None
    log: bool

    @property
    def id(self) -> int:
        return win_id(self.touch["touch_id"], self.kind)


def candidates(rows: list[sqlite3.Row]) -> list[Candidate]:
    """Every outcome that can be a win (ruling 7). A said-no, an asked-to-stop and an auto reply never are."""
    found = []
    for r in rows:
        for kind in WIN_KINDS:
            at = r[WIN_COLUMNS[kind]]
            if at is None:
                continue
            reply_kind = r["reply_kind"] if kind == "replied" else None
            log = is_log_win(kind, reply_kind)
            if log or is_recognition(kind, reply_kind):
                found.append(Candidate(r, kind, at, reply_kind, log))
    return found


def seen_ids(conn: sqlite3.Connection) -> set[int]:
    return {win_id(r["touch_id"], r["kind"]) for r in conn.execute("SELECT touch_id, kind FROM win_seen")}


def wins_unseen(conn: sqlite3.Connection) -> int:
    """How many wins he has not seen (the shell's badge)."""
    seen = seen_ids(conn)
    return sum(1 for c in candidates(live_touches(conn)) if c.id not in seen)


def _clock(at: str) -> str:
    """The New York time of day, `9:41` or `14:05`."""
    moment = datetime.strptime(at, ISO_FMT).replace(tzinfo=timezone.utc).astimezone(NY)
    return f"{moment.hour}:{moment.minute:02d}"


def wins_view(conn: sqlite3.Connection, now: str) -> WinsView:
    rows = live_touches(conn)
    seen = seen_ids(conn)
    found = candidates(rows)
    counts = {k: 0 for k in WIN_KINDS}
    for c in found:
        counts[c.kind] += 1
    shown = sorted((c for c in found if c.log or c.id not in seen), key=lambda c: (c.at, c.touch["touch_id"],
                   WIN_KINDS.index(c.kind)), reverse=True)
    parents = merge_parents(conn)
    touches = [c.touch for c in shown]
    companies = company_refs(conn, {survivor_of(parents, t["company_id"]) for t in touches})
    people = people_by_ids(conn, {t["person_id"] for t in touches if t["person_id"] is not None})
    postings = {r["posting_id"]: r["title"] for r in by_ids(
        conn, "SELECT posting_id, title FROM posting WHERE posting_id IN ({ids})",
        {t["posting_id"] for t in touches if t["posting_id"] is not None})}
    notes = {r["person_id"]: r["body"] for r in by_ids(
        conn, "SELECT person_id, body FROM linkedin_draft WHERE person_id IN ({ids})",
        {t["person_id"] for t in touches if t["person_id"] is not None and t["channel"] in LINKEDIN})}
    wins = []
    for c in shown:
        t = c.touch
        company = companies.get(survivor_of(parents, t["company_id"]))
        if company is None:  # the company row is gone: still listed, so the list matches the counts
            company = CompanyRef(id=t["company_id"], name=UNKNOWN_COMPANY, status="removed", paused_at=None,
                                 held_by_touch_id=None, held=False, held_by_reply=None)
        person = people.get(t["person_id"]) if t["person_id"] is not None else None
        wins.append(Win(
            win_id=c.id, kind=c.kind, at=c.at, touch_id=t["touch_id"],
            person=person_ref(person) if person is not None else None, company=company,
            line=win_line(c.kind, person["name"] if person is not None else None, company.name, c.reply_kind,
                          _clock(c.at)),
            sent_note=notes.get(t["person_id"]) if t["channel"] in LINKEDIN else None,
            role=postings.get(t["posting_id"]), reply_kind=c.reply_kind, unseen=c.id not in seen))
    return WinsView(as_of=now, wins=wins, unseen=sum(1 for c in found if c.id not in seen),
                    counts=WinCounts(**counts), since=_since(rows, now))


def mark_seen(ctx: WriteCtx, ids: list[int]) -> WinsSeen:
    """Mark wins seen. Every id must name a touch that has that outcome, else nothing is marked. Writes `win_seen`
    with `ON CONFLICT DO NOTHING`, so a repeat marks nothing new."""
    missing = ApiProblem("not_found", "There is no such win. Nothing was marked seen.")
    wanted = []
    for wid in sorted(set(ids)):
        try:
            touch_id, kind = decode_win_id(wid)
        except ValueError:
            raise missing from None
        if touch_id > MAX_TOUCH_ID:
            raise missing
        wanted.append((touch_id, kind))
    for touch_id, kind in wanted:
        row = ctx.conn.execute(f"SELECT {WIN_COLUMNS[kind]} AS at FROM outreach_touch "
                               "WHERE touch_id = ? AND undone_at IS NULL", (touch_id,)).fetchone()
        if row is None or row["at"] is None:
            raise missing
    marked = 0
    for touch_id, kind in wanted:
        marked += ctx.conn.execute(
            "INSERT INTO win_seen(touch_id, kind, seen_at) VALUES (?, ?, ?) ON CONFLICT(touch_id, kind) DO NOTHING",
            (touch_id, kind, ctx.now)).rowcount
    return WinsSeen(marked=marked)
