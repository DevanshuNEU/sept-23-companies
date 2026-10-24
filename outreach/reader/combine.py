"""The reader and the rules together: a hide needs both (#32, design section 7.3).

The rules (`outreach.rules.decide`) read phrases; the reader reads the posting and quotes it. Neither alone may hide
a posting for good. This module is the only code that sets `decision.provisional = 0`.

Per field the two answers are compared (the mapping table in the plan's "Decisions"): `agree`, `disagree`, `missing`
(the reader said unknown, or the field was refused, where the rules claim something) or `reader_only` (no rules
counterpart). Four fields can hide a posting (`HIDE_FIELDS`); the others only flag.

- A rules hide survives only when its field agrees. Otherwise it is relaxed in the stored row (sponsorship no to
  unclear, citizenship 1 to 0, eligibility excluded to ok with no reason, us no to unknown; the rules' quote stays),
  so `passes()` shows the posting again.
- A reader hide the rules do not claim (reader `wont_sponsor`, rules `silent`) never hides. A real quote can carry
  the opposite value (a flipped negation), so the rules are the guard.
- A hide field that disagrees or is missing where either side would hide is `raised`: one `reader_disagrees` review
  item for the posting. So is the engineering field, when it is not `agree` and the row still hides: a refusal on a
  posting the reader does not call engineering is no evidence about the role ranking would hide the company for.

When a row is final (controller rulings 1 and 2, 2026-10-23): `provisional = 0` only when a reading at the promoted
version exists for the posting's current body, nothing is raised, a rules sponsor (yes or opt_only) has the
reader's agreement, and, if the combined row still hides, the posting is neither stale nor on a doubtful board. Everything else stays provisional with no reader version: no reading, an
unreadable posting (relaxed and flagged), any raised field, and an agreed hide on a posting ranking does not trust.

A reading counts only for the posting's current body hash at `promoted_version` (config.toml; "" means none, so
nothing is final and nothing the reader said changes a row; any value that is not `rv-` and 12 hex, a side version
included, counts as none too: `version.promoted_version`). A newer candidate version runs in shadow: `explain`
shows it, it decides nothing. The title and location are not in the body hash, so the quote of each hide field and
of pay is checked again against the current title, location and body; a quote no longer there counts as refused.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from typing import Literal

from outreach.crawl.stale import load_stale
from outreach.ranking.common import doubtful_board_ids
from outreach.reader.config import ReaderConfig, load_reader_config
from outreach.reader.submit import min_words
from outreach.reader.types import FIELDS, TEXT_FIELDS, PostingReading, _collapse, quote_in_body
from outreach.reader.version import promoted_version, reader_version, side_version
from outreach.rules import Decision, PostingInput, RulesetNotCurrent, decide, decide_and_store, ruleset_version
from outreach.rules.config import Config, default_config
from outreach.rules.eligibility import starts_2027
from outreach.rules.location import us_status
from outreach.store import append_event, atomic, body_sha256, utcnow

__all__ = ["HIDE_FIELDS", "FieldVerdict", "Combined", "compare", "combine", "reading_state", "apply_reading",
           "apply_all", "explain", "reader_combiner"]

HIDE_FIELDS = ("sponsorship", "citizenship_or_clearance", "student_or_grad_window", "location")
_READER_ONLY = ("years_high", "remote", "pay")
_SPONSORSHIP = {"wont_sponsor": "no", "will_sponsor": "yes", "opt_only": "opt_only", "silent": "silent"}
_LEVEL = {"new_grad": ("new_grad",), "early_career": ("early_career", "swe1"), "mid": ("swe2", "open"),
          "senior_plus": ("senior",), "unknown": ("not_stated",)}
_NO_FACT = ("unknown", "silent", None, "")

State = Literal["agree", "disagree", "missing", "reader_only"]


@dataclass(frozen=True)
class FieldVerdict:
    field: str
    state: State
    reader_value: str | int | None
    rules_value: str | int | bool | None
    reader_quote: str
    would_hide_rules: bool
    would_hide_reader: bool


@dataclass(frozen=True)
class Combined:
    decision: Decision
    provisional: bool
    reader_version: str | None
    verdicts: tuple[FieldVerdict, ...]

    @property
    def raised(self) -> tuple[FieldVerdict, ...]:
        """The hide fields the two sides do not agree on, where either side would hide; and, while the row still
        hides, an engineering verdict that is not `agree` (the reader doubts the lane the hide rests on)."""
        hides = _hides(self.decision)
        return tuple(v for v in self.verdicts if v.state in ("disagree", "missing") and (
            (v.field in HIDE_FIELDS and (v.would_hide_rules or v.would_hide_reader))
            or (v.field == "engineering" and hides)))


def _hides(d: Decision) -> bool:
    """A hide on one of the four hide fields (lane is the title's filter, not evidence)."""
    return d.us == "no" or d.sponsorship == "no" or d.citizenship or d.eligibility == "excluded"


def _unconfirmed_sponsor(rules: Decision, verdicts: tuple[FieldVerdict, ...]) -> bool:
    """The rules read a sponsor (yes or opt_only) and the reader did not say the same (unknown, refused, silent or
    another stance): a final row would tell ranking the reader confirmed the wording."""
    return rules.sponsorship in ("yes", "opt_only") and any(
        v.field == "sponsorship" and v.state != "agree" for v in verdicts)


def _state(known: bool, agrees: bool, rules_claims: bool) -> State:
    """`known`: the reader stated a value. Unknown agrees only where the rules claim nothing."""
    if not known:
        return "missing" if rules_claims else "agree"
    return "agree" if agrees else "disagree"


def compare(rules: Decision, reading: PostingReading | None, refused: frozenset[str],
            cfg: Config) -> tuple[FieldVerdict, ...]:
    """One verdict per reading field. Pure. A field in `refused`, or every field when `reading` is None, is
    `missing` and states nothing (so it never hides); a field the reader read as unknown is `missing` only where the
    rules claim something."""
    out: list[FieldVerdict] = []
    for name in FIELDS:
        gone = reading is None or name in refused
        value = None if gone else reading.fields[name].value
        quote = "" if gone else reading.fields[name].quote
        known = value not in _NO_FACT

        def add(state: State, rules_value, hide_rules: bool = False, hide_reader: bool = False) -> None:
            if gone and state != "reader_only":
                state = "missing"  # refused or unread is never an agreement, even where the rules claim nothing
            out.append(FieldVerdict(name, state, value, rules_value, quote, hide_rules, hide_reader))

        if name == "sponsorship":
            add(_state(value not in (None, "unknown"), _SPONSORSHIP.get(value) == rules.sponsorship,
                       rules.sponsorship not in ("unclear", "silent")),
                rules.sponsorship, rules.sponsorship == "no", value == "wont_sponsor")
        elif name == "citizenship_or_clearance":
            add(_state(known, (value == "yes") == rules.citizenship, rules.citizenship),
                rules.citizenship, rules.citizenship, value == "yes")
        elif name == "student_or_grad_window":
            excluded = rules.eligibility == "excluded"
            add(_state(known, (value == "yes") == excluded, excluded), rules.eligibility, excluded, value == "yes")
        elif name == "location":
            status = us_status(value, None, cfg) if known else "unknown"
            add(_state(status != "unknown", status == rules.us, rules.us != "unknown"),
                rules.us, rules.us == "no", status == "no")
        elif name == "engineering":
            in_lane = rules.lane in ("engineering", "intern")
            add(_state(known, (value == "yes") == in_lane, True), rules.lane)
        elif name == "level":
            if value == "intern":
                agrees = rules.lane == "intern"
            else:
                agrees = rules.lane != "intern" and rules.level_tier in _LEVEL.get(value, ())
            add(_state(value is not None, agrees, True), "intern" if rules.lane == "intern" else rules.level_tier)
        elif name == "years_low":
            add(_state(value is not None, value == rules.years_asked, rules.years_asked is not None),
                rules.years_asked)
        elif name == "start_date":
            add(_state(known, known and starts_2027("", value, cfg) == rules.start_2027, rules.start_2027),
                rules.start_2027)
        else:
            add("reader_only", None)
    return tuple(out)


def _relax(rules: Decision, verdicts: tuple[FieldVerdict, ...]) -> Decision:
    """Every rules hide whose field is not `agree`, turned to its non-hide value; the rules' quotes stay."""
    states = {v.field: v.state for v in verdicts}
    d = rules
    if d.sponsorship == "no" and states["sponsorship"] != "agree":
        d = replace(d, sponsorship="unclear")
    if d.citizenship and states["citizenship_or_clearance"] != "agree":
        d = replace(d, citizenship=False)
    if d.eligibility == "excluded" and states["student_or_grad_window"] != "agree":
        d = replace(d, eligibility="ok", eligibility_reason=None)
    if d.us == "no" and states["location"] != "agree":
        d = replace(d, us="unknown")
    return d


def combine(rules: Decision, reading: PostingReading | None, *, refused: frozenset[str] = frozenset(),
            unreadable: bool = False, cfg: Config | None = None, untrusted: bool = False) -> Combined:
    """The row to store for one posting. Pure.

    No reading and not `unreadable`: the rules' decision unchanged, provisional. Otherwise every rules hide whose
    field is not `agree` is relaxed. The row is final (`provisional` False, the reading's version) only with a
    reading, nothing raised, and, when it still hides, a trusted posting (`untrusted`: stale or on a doubtful
    board). An unreadable posting (no reading) is relaxed and stays provisional.
    """
    if reading is None and not unreadable:
        return Combined(rules, True, None, ())
    cfg = cfg if cfg is not None else default_config()
    verdicts = compare(rules, reading, refused, cfg)
    decision = _relax(rules, verdicts)
    # A final row names its reader, so a reading with no version (or only spaces) decides nothing for good.
    provisional = reading is None or not isinstance(reading.reader_version, str) or not reading.reader_version.strip()
    draft = Combined(decision, provisional, None, verdicts)
    if draft.raised or (untrusted and _hides(decision)) or _unconfirmed_sponsor(rules, verdicts):
        provisional = True
    return Combined(decision, provisional, None if provisional else reading.reader_version, verdicts)


# ---- the store --------------------------------------------------------------------------------------------------

def _posting(conn: sqlite3.Connection, posting_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT p.posting_id, p.company_id, p.board_id, p.title, p.location, p.country, p.pay, p.closed_at, "
        "b.body FROM posting p LEFT JOIN posting_body b ON b.posting_id = p.posting_id WHERE p.posting_id = ?",
        (posting_id,)).fetchone()


# The fields whose quotes are checked again at combine time: the four that can hide, and pay, which is written to
# the posting row.
_RECHECKED = HIDE_FIELDS + ("pay",)


def _gone_quotes(reading: PostingReading, post: sqlite3.Row) -> set[str]:
    """Rechecked fields stating a fact whose quote (or free-text value) is no longer in the title, location or
    body."""
    parts = (post["title"] or "", post["location"] or "", post["body"] or "")

    def found(text: str, floor: int) -> bool:
        return any(quote_in_body(text, part, min_words=floor) for part in parts)

    gone = set()
    for name in _RECHECKED:
        f = reading.fields[name]
        if f.value in _NO_FACT:
            continue
        ok = found(f.quote, min_words(name))
        if ok and name in TEXT_FIELDS:
            ok = found(f.value, 1) or _collapse(f.value) == _collapse(f.quote)
        if not ok:
            gone.add(name)
    return gone


def _state_for(conn: sqlite3.Connection, post: sqlite3.Row | None,
               version: str) -> tuple[PostingReading | None, frozenset[str], bool]:
    if not version or post is None or post["body"] is None:
        return None, frozenset(), False
    sha = body_sha256(post["body"])
    row = conn.execute("SELECT fields, refused_fields FROM posting_reading WHERE posting_id = ? AND body_sha256 = ? "
                       "AND reader_version = ?", (post["posting_id"], sha, version)).fetchone()
    if row is None:
        queued = conn.execute("SELECT state FROM read_queue WHERE posting_id = ? AND body_sha256 = ? "
                              "AND reader_version = ?", (post["posting_id"], sha, version)).fetchone()
        return None, frozenset(), queued is not None and queued["state"] == "refused"
    try:
        reading = PostingReading.from_json(json.loads(row["fields"]))
        refused = {str(f) for f in json.loads(row["refused_fields"])}
    except (ValueError, TypeError):
        # A stored reading this code cannot read is no evidence: the posting fails open, as an unreadable one does.
        return None, frozenset(), True
    if (reading.posting_id, reading.body_sha256, reading.reader_version) != (post["posting_id"], sha, version):
        return None, frozenset(), True
    return reading, frozenset(refused | _gone_quotes(reading, post)), False


def reading_state(conn: sqlite3.Connection, posting_id: int, *,
                  promoted: str) -> tuple[PostingReading | None, frozenset[str], bool]:
    """(the reading of the posting's current body at version `promoted`, the fields refused at submit or whose
    hide quote is no longer in the text, whether the queue row for that key ended refused with no reading). Nothing
    when `promoted` is ''. Read-only."""
    return _state_for(conn, _posting(conn, posting_id), promoted)


def _today(today: date | None) -> date:
    return today if today is not None else datetime.now(timezone.utc).date()


def _untrusted(conn: sqlite3.Connection, post: sqlite3.Row, today: date) -> bool:
    """Stale (`load_stale`) or on a board ranking does not trust (`doubtful_board_ids`): no final hide."""
    if load_stale(conn, today, posting_ids=[post["posting_id"]]):
        return True
    return post["board_id"] is not None and post["board_id"] in doubtful_board_ids(conn)


def _combine_stored(conn: sqlite3.Connection, post: sqlite3.Row | None, rules: Decision, version: str,
                    today: date | None, rules_cfg: Config | None) -> Combined:
    reading, refused, unreadable = _state_for(conn, post, version)
    combined = combine(rules, reading, refused=refused, unreadable=unreadable, cfg=rules_cfg)
    # The trust check costs two queries, so it runs only where it can matter: a row about to be a final hide.
    if not combined.provisional and _hides(combined.decision) and _untrusted(conn, post, _today(today)):
        combined = combine(rules, reading, refused=refused, unreadable=unreadable, cfg=rules_cfg, untrusted=True)
    return combined


def _decide(post: sqlite3.Row, rules_cfg: Config | None) -> Decision:
    return decide(PostingInput(post["title"], post["body"] or "", post["location"], post["country"]), rules_cfg)


def reader_combiner(conn: sqlite3.Connection, cfg: ReaderConfig | None = None, *,
                    rules_cfg: Config | None = None) -> Callable[[sqlite3.Connection, int, Decision], Combined]:
    """The `combiner` for `rebuild`, `decide_and_store` and the crawler: the stored reading at
    `cfg.promoted_version` combined with the rules' decision. It writes nothing itself. `rules_cfg` is the rules
    config the caller hands `rebuild` or `decide_and_store` (default: the shipped one), so the reader's location is
    read with the same word lists as the rules' own."""
    cfg = cfg if cfg is not None else load_reader_config()
    version = promoted_version(cfg)

    def combiner(c: sqlite3.Connection, posting_id: int, rules: Decision) -> Combined:
        if not version:
            return Combined(rules, True, None, ())
        return _combine_stored(c, _posting(c, posting_id), rules, version, None, rules_cfg)
    return combiner


def _reason(raised: tuple[FieldVerdict, ...]) -> str:
    parts = []
    for v in raised:
        quote = f' ("{" ".join(v.reader_quote.split())}")' if v.reader_quote.strip() else ""
        if v.state == "missing":
            reader = "unknown or refused"
        else:
            reader = json.dumps(v.reader_value, ensure_ascii=False)
        parts.append(f"{v.field}: rules say {json.dumps(v.rules_value)}, reader says {reader}{quote}")
    return "The reader and the rules disagree on " + "; ".join(parts)


def _sync_item(conn: sqlite3.Connection, post: sqlite3.Row, combined: Combined, version: str, now: str) -> None:
    """One `reader_disagrees` item per posting while something is raised; cleared when nothing is."""
    raised = combined.raised
    key = ""
    if raised:
        sha = body_sha256(post["body"] or "")
        key = f"{version}:{sha[:12]}:{','.join(sorted(v.field for v in raised))}"
    for item in conn.execute("SELECT review_id FROM review_item WHERE kind = 'reader_disagrees' AND posting_id = ? "
                             "AND state = 'open' AND evidence_key <> ?", (post["posting_id"], key)).fetchall():
        conn.execute("UPDATE review_item SET state = 'resolved', resolution = 'cleared', resolved_at = ?, "
                     "revision = revision + 1 WHERE review_id = ?", (now, item["review_id"]))
    if not raised:
        return
    reason = _reason(raised)
    made = conn.execute(
        "INSERT INTO review_item(kind, company_id, posting_id, reason, evidence_key, raised_by, created_at) "
        "VALUES ('reader_disagrees', ?, ?, ?, ?, 'reader', ?) ON CONFLICT DO NOTHING",
        (post["company_id"], post["posting_id"], reason, key, now)).rowcount
    if not made:  # a cleared item on the same evidence comes back; one he resolved by hand stays resolved
        conn.execute("UPDATE review_item SET state = 'open', resolution = NULL, resolved_at = NULL, reason = ?, "
                     "revision = revision + 1 WHERE kind = 'reader_disagrees' AND company_id = ? AND posting_id = ? "
                     "AND evidence_key = ? AND state = 'resolved' AND resolution = 'cleared'",
                     (reason, post["company_id"], post["posting_id"], key))


def _fill_pay(conn: sqlite3.Connection, post: sqlite3.Row, reading: PostingReading | None,
              refused: frozenset[str]) -> None:
    """Pay stated only in the posting text reaches `posting.pay` (ruling 8), never over a pay the source gave, with
    a `pay_from_reading` event. A pay refused at submit, or whose quote has left the text, fills nothing."""
    if reading is None or "pay" in refused or post["pay"]:
        return
    pay = reading.fields["pay"].value
    if not isinstance(pay, str) or not pay.strip():
        return
    filled = conn.execute("UPDATE posting SET pay = ? WHERE posting_id = ? AND pay = ''",
                          (pay.strip(), post["posting_id"])).rowcount
    if filled:
        append_event(conn, entity="posting", kind="pay_from_reading", actor="reader", entity_id=post["posting_id"],
                     company_id=post["company_id"],
                     detail={"pay": pay.strip(), "quote": reading.fields["pay"].quote,
                             "reader_version": reading.reader_version, "body_sha256": reading.body_sha256})


def _row_of(combined: Combined) -> dict:
    return {**combined.decision.as_row(), "provisional": int(combined.provisional),
            "reader_version": combined.reader_version}


def apply_reading(conn: sqlite3.Connection, posting_id: int, *, cfg: ReaderConfig | None = None,
                  today: date | None = None, rules_cfg: Config | None = None) -> Combined:
    """Decide the posting with the rules, combine with its promoted reading, store the row, raise or clear its
    `reader_disagrees` item and fill an empty pay from the reading. Idempotent: a call that would change nothing
    writes nothing. `rules_cfg` is the rules config (default: the shipped one). Raises `RulesetNotCurrent` as
    `decide_and_store` does and LookupError for a missing posting."""
    cfg = cfg if cfg is not None else load_reader_config()
    version = promoted_version(cfg)
    with atomic(conn, "reader_apply"):
        current = conn.execute("SELECT ruleset_version FROM ruleset WHERE is_current = 1").fetchone()
        if current is None or current[0] != ruleset_version():
            raise RulesetNotCurrent("ruleset changed; run: uv run python -m outreach.rules.rebuild")
        post = _posting(conn, posting_id)
        if post is None:
            raise LookupError(f"no posting {posting_id}")
        rules = _decide(post, rules_cfg)
        combined = (_combine_stored(conn, post, rules, version, today, rules_cfg) if version
                    else Combined(rules, True, None, ()))
        stored = conn.execute("SELECT * FROM decision WHERE posting_id = ? AND ruleset_version = ?",
                              (posting_id, current[0])).fetchone()
        wanted = _row_of(combined)
        if stored is None or any(stored[k] != v for k, v in wanted.items()):
            decide_and_store(conn, posting_id, cfg=rules_cfg, combiner=lambda _c, _p, _d: combined)
        if version:
            reading, refused, _ = _state_for(conn, post, version)
            _sync_item(conn, post, combined, version, utcnow())
            _fill_pay(conn, post, reading, refused)
    return combined


def apply_all(conn: sqlite3.Connection, *, cfg: ReaderConfig | None = None, today: date | None = None,
              rules_cfg: Config | None = None) -> int:
    """`apply_reading` for every open posting with a reading or a refused queue row at the promoted version, and for
    every open posting whose current row is final under any other reader version (run after a rebuild, a promotion
    or a rollback: no model call, no crawl). With no promoted version ("") that returns every final row to
    provisional. Open `reader_disagrees` items raised under another version are closed. Returns how many postings
    were applied."""
    cfg = cfg if cfg is not None else load_reader_config()
    version = promoted_version(cfg)
    ids: set[int] = set()
    if version:
        for (posting_id,) in conn.execute(
                "SELECT p.posting_id FROM posting p WHERE p.closed_at IS NULL AND (EXISTS (SELECT 1 FROM "
                "posting_reading r WHERE r.posting_id = p.posting_id AND r.reader_version = ?) OR EXISTS (SELECT 1 "
                "FROM read_queue q WHERE q.posting_id = p.posting_id AND q.reader_version = ? "
                "AND q.state = 'refused'))", (version, version)):
            post = _posting(conn, posting_id)
            reading, _, unreadable = _state_for(conn, post, version)
            if reading is not None or unreadable:  # else the reading is of an older body
                ids.add(posting_id)
    # A final row made under a version that no longer decides: a rollback, or a newer promotion.
    ids.update(r[0] for r in conn.execute(
        "SELECT d.posting_id FROM decision d JOIN posting p ON p.posting_id = d.posting_id "
        "WHERE p.closed_at IS NULL AND d.provisional = 0 AND coalesce(d.reader_version, '') <> ? "
        "AND d.ruleset_version = (SELECT ruleset_version FROM ruleset WHERE is_current = 1)", (version,)))
    for posting_id in sorted(ids):
        apply_reading(conn, posting_id, cfg=cfg, today=today, rules_cfg=rules_cfg)
    _close_other_versions(conn, version)
    return len(ids)


def _close_other_versions(conn: sqlite3.Connection, version: str) -> None:
    """Resolve, as cleared, every open `reader_disagrees` item whose evidence names another reader version (its key
    starts with the version it was raised under; with no promoted version, every one)."""
    prefix = f"{version}:" if version else ""
    with atomic(conn, "reader_close_items"):
        conn.execute(
            "UPDATE review_item SET state = 'resolved', resolution = 'cleared', resolved_at = ?, "
            "revision = revision + 1 WHERE kind = 'reader_disagrees' AND state = 'open' "
            "AND (? = '' OR substr(evidence_key, 1, length(?)) <> ?)", (utcnow(), prefix, prefix, prefix))


def explain(conn: sqlite3.Connection, posting_id: int, *, cfg: ReaderConfig | None = None,
            rules_cfg: Config | None = None) -> tuple[FieldVerdict, ...]:
    """The verdicts for the posting, for the API and UI to show flagged fields with both quotes: the promoted
    version's reading when there is one, else the candidate version's, else a side session's (`side_version`). The
    rules side is decide() on the stored text (the stored row holds the combined facts). Read-only."""
    cfg = cfg if cfg is not None else load_reader_config()
    post = _posting(conn, posting_id)
    if post is None:
        return ()
    rules = _decide(post, rules_cfg)
    rules_cfg = rules_cfg if rules_cfg is not None else default_config()
    for version in (promoted_version(cfg), reader_version(cfg), side_version(cfg)):
        reading, refused, unreadable = _state_for(conn, post, version)
        if reading is not None or unreadable:
            return compare(rules, reading, refused, rules_cfg)
    return ()

