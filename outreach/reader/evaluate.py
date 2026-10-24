"""Eval per field and class, and the promotion gate: when a reader version may decide postings (#32).

Ported from Job Radar's `eval.py` and `gate.py` (~/Desktop/DevVault/job-search/pipeline). There a ruleset gives each
posting one verdict (show or hide) and is scored against apply/maybe/no labels. Here a reader version gives every
posting a value per field, so it is scored per field and class, three ways:

- `reader`: the stored reading, with every field whose quote is not in the text turned to unknown (what `submit`
  would refuse).
- `rules`: `decide()` on the same text, in the reader's words (`rules_view`).
- `both`: a value only where reader and rules give the same one, else unknown. This is what can hide a posting: a
  final hide needs both (combine.py), so `both` on the four hide classes is the number promotion rests on.

    uv run python -m outreach.reader eval [--version V] [--against OLD] [--run] [--json]
    uv run python -m outreach.reader promote --version V [--against OLD]

The asymmetry (gate.py's, and research section 3.3's): a false hide (a posting the labels say is fine, hidden) loses
a job silently; a miss (a posting that should hide, shown) costs Devanshu a few seconds. So for every hide class the
eval prints both rates, and the gate holds them to very different bars:

    false hides, both    0                (gate.py: "no previously-shown positive becomes hidden", zero tolerance)
    false hides, reader  at most 5%       (gate.py's RECALL_TARGET 0.95: at most 5 in 100 good postings lost; a
                                           reader-only false hide does not hide, it raises a review item, and a
                                           reader wrong that often is one wrong rule away from a false hide)
    misses, both         at most 85%      (gate.py's PRECISION_FLOOR 0.15, "collapse detector only": a reader that
                                           never confirms a hide makes nothing final and must not pass as perfect)

Fail closed, as gate.py is: the verdict is reject unless every check passes, and a check that cannot be evaluated is
a reject too ("cannot"), with nothing after it computed. The preconditions:

    at least 300 confirmed items          (gate.py's MIN_LABELS 300; ten strata at TARGET_PER_CLASS 30)
    at least 20 labeled per hide class    (controller ruling 5 on the plan: a class under 20 is reported, not gated;
                                           here a hide class under 20 blocks promotion, because its hides would turn
                                           final with nothing measured)
    a reading for every confirmed item    (gate.py's "candidate replay covers the same postings")
    the version is the one the prompt, model and schema make now (gate.py warns on "a REPLAY of an older
                                           ruleset"; the store only ever holds readings of the current files' version)

Then `not_worse` against the promoted version: any drop in precision or recall, reader or both, on a class with at
least 20 labeled examples in both (ruling 5), blocks. With no promoted version yet (the first promotion) the
thresholds alone decide.

Every ratio is printed with its n and a 95% Wilson interval (gate.py and eval.py's `wilson`), and a hide class with
no false hide also prints the rule-of-three bound 3/n.

Not ported, and why: the shown-per-day budget (60) and the flip share (20%) measure how a ruleset changes the queue,
which a reader version does not move on its own; the time hold-out (60 labels) needs a date the gold item does not
carry; the unit-test run inside the gate (promotion is a reviewed commit, and the suite runs before it); the
Horvitz-Thompson weights (no population weights here, see gold.py) and the maybe-both-ways report (no maybe label).

Promotion is Devanshu's command, never automatic: `promote` writes the one `promoted_version` line in config.toml
when the gate passes and never commits. The CLI refuses it outside the live checkout unless OUTREACH_DB names a
scratch store. Then `python -m outreach.reader apply` re-combines the store with the new version.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
import re
from collections.abc import Iterable
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Literal

from outreach.reader.config import CONFIG_PATH, ReaderConfig, load_reader_config
from outreach.reader.gold import (GOLD_READINGS, HIDE_CLASSES, GoldItem, atomic_write, check_label, rules_fields,
                                  value_class)
from outreach.reader.night import Runner, classify_run, parse_reply
from outreach.reader.types import ALLOWED, FIELDS, INT_FIELDS, TEXT_FIELDS, FieldReading, PostingReading
from outreach.reader.version import reader_version
from outreach.rules import PostingInput, decide
from outreach.rules.config import Config, default_config
from outreach.store import body_sha256

__all__ = ["MIN_CONFIRMED", "MIN_CLASS_N", "MAX_BOTH_FALSE_HIDES", "MAX_READER_FALSE_HIDE_RATE", "MAX_BOTH_MISS_RATE",
           "N_FLOOR", "ClassScore", "Gate", "rules_view", "reader_view", "score", "load_readings",
           "run_reader_on_gold", "not_worse", "gate", "promote", "table_text", "wilson", "scores_json"]

# The gate's numbers. Each one's source is in the module docstring.
MIN_CONFIRMED = 300
MIN_CLASS_N = 20
MAX_BOTH_FALSE_HIDES = 0
MAX_READER_FALSE_HIDE_RATE = 0.05
MAX_BOTH_MISS_RATE = 0.85
# Below this n a ratio is printed with a "low n" mark (eval.py's N_FLOOR 30 printed nothing below it).
N_FLOOR = 30

Predictor = Literal["reader", "rules", "both"]
_PREDICTORS: tuple[Predictor, ...] = ("reader", "rules", "both")
# Fields the rules have no counterpart for: scored for the reader alone.
_READER_ONLY = ("years_high", "remote", "pay")
_FIXED_CLASSES = {"location": ("us", "non_us"), "start_date": ("starts_2027", "other"), "pay": ("stated",)}
_HIDE = set(HIDE_CLASSES)
_VERSION = re.compile(r"rv-[0-9a-f]{12}\Z")
_PROMOTED_LINE = re.compile(r'^promoted_version = "[^"\n]*"$', re.MULTILINE)


@dataclass(frozen=True)
class ClassScore:
    """One predictor on one class of one field, over the confirmed items. `precision` and `recall` are None when
    their denominator is 0. `n_items` is how many confirmed items were scored."""
    field: str
    value: str
    predictor: Predictor
    tp: int
    fp: int
    fn: int
    n_truth: int
    precision: float | None
    recall: float | None
    n_items: int = 0

    @property
    def predicted(self) -> int:
        return self.tp + self.fp

    @property
    def hide(self) -> bool:
        return (self.field, self.value) in _HIDE


# ---- the three views -------------------------------------------------------------------------------------------

def rules_view(item: GoldItem, cfg: Config | None = None) -> dict[str, str | None]:
    """`decide()` on the item's title, body and location, as one class per field in the reader's words: sponsorship
    no is wont_sponsor and unclear unknown, citizenship true is yes else unknown, eligibility excluded is yes else
    unknown, us is a location class (us, non_us, unknown), lane is engineering, tier is level, years_asked is
    years_low (as text). None where the rules have no counterpart (years_high, remote, pay)."""
    if cfg is None or cfg is default_config():
        return dict(_rules_default(item.title, item.body, item.location))
    return rules_fields(decide(PostingInput(item.title, item.body, item.location), cfg))


@lru_cache(maxsize=8192)
def _rules_default(title: str, body: str, location: str) -> tuple:
    """`rules_fields` under the shipped rules config, remembered: eval scores the same items many times."""
    return tuple(rules_fields(decide(PostingInput(title, body, location))).items())


def _blank(name: str):
    if name in INT_FIELDS:
        return None
    if name in TEXT_FIELDS:
        return ""
    return "unknown"


def reader_view(reading_fields: dict, item: GoldItem) -> dict:
    """The reading's value per field, with every field that is missing, malformed, or fails the quote check against
    the item's text turned to unknown (None for years, '' for text): the refusal `submit` applies."""
    out = {}
    raw = reading_fields if isinstance(reading_fields, dict) else {}
    for name in FIELDS:
        entry = raw.get(name)
        value = _blank(name)
        if isinstance(entry, dict) and set(entry) == {"value", "quote"}:
            try:
                check_label(name, FieldReading(entry["value"], entry["quote"]), item.title, item.location, item.body)
                value = entry["value"]
            except (ValueError, TypeError):
                pass
        out[name] = value
    return out


# ---- scoring ---------------------------------------------------------------------------------------------------

def _classes(name: str, seen: Iterable[str]) -> tuple[str, ...]:
    if name in ALLOWED:
        return tuple(v for v in ALLOWED[name] if v != "unknown")
    if name in _FIXED_CLASSES:
        return _FIXED_CLASSES[name]
    return tuple(sorted({c for c in seen if c != "unknown"}, key=int))


def score(items: list[GoldItem], readings: dict[str, dict], cfg: Config | None = None) -> list[ClassScore]:
    """Precision and recall per field, class and predictor over the confirmed items. A confirmed item with no
    reading counts as a reading that said unknown everywhere. Rows come in FIELDS order, then class, then predictor
    (reader, rules, both); fields with no rules counterpart have a reader row only."""
    cfg = cfg if cfg is not None else default_config()
    confirmed = [i for i in items if i.status == "confirmed"]
    table = []
    for item in confirmed:
        truth = {n: value_class(n, item.labels[n].value, cfg) for n in FIELDS}
        seen = reader_view(readings.get(item.gold_id, {}), item)
        reader = {n: value_class(n, seen[n], cfg) for n in FIELDS}
        rules = rules_view(item, None if cfg is default_config() else cfg)
        table.append((truth, reader, rules))
    out: list[ClassScore] = []
    for name in FIELDS:
        observed = [row[k][name] for row in table for k in (0, 1, 2) if row[k][name] is not None]
        predictors = ("reader",) if name in _READER_ONLY else _PREDICTORS
        for cls in _classes(name, observed):
            for predictor in predictors:
                tp = fp = fn = n_truth = 0
                for truth, reader, rules in table:
                    if predictor == "reader":
                        guess = reader[name]
                    elif predictor == "rules":
                        guess = rules[name]
                    else:
                        guess = reader[name] if reader[name] == rules[name] else "unknown"
                    is_truth, said = truth[name] == cls, guess == cls
                    n_truth += is_truth
                    tp += is_truth and said
                    fp += said and not is_truth
                    fn += is_truth and not said
                out.append(ClassScore(name, cls, predictor, tp, fp, fn, n_truth,
                                      tp / (tp + fp) if tp + fp else None, tp / n_truth if n_truth else None,
                                      len(table)))
    return out


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    """95% Wilson score interval for k of n (eval.py's): (p, low, high). Safe at n = 0 and at k in {0, n}."""
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (p, max(0.0, centre - half), min(1.0, centre + half))


def _ratio(k: int, n: int) -> str:
    if n == 0:
        return "-"
    p, lo, hi = wilson(k, n)
    return f"{p:.2f} [{lo:.2f}-{hi:.2f}]" + (" low n" if n < N_FLOOR else "")


def _rates(s: ClassScore) -> str:
    """Both error rates of a hide class: false hides over what the predictor would hide, misses over the truth."""
    fh = f"false hides {s.fp} of {s.predicted}" + (f" ({100 * s.fp / s.predicted:.1f}%)" if s.predicted else "")
    miss = f"misses {s.fn} of {s.n_truth}" + (f" ({100 * s.fn / s.n_truth:.1f}%)" if s.n_truth else "")
    return f"{fh}, {miss}"


def table_text(scores: list[ClassScore]) -> str:
    """One line per field, class and predictor: n (labeled in that class), precision and recall with Wilson
    intervals, and for a hide class both error rates."""
    lines = [f"{'field':<25} {'class':<14} {'predictor':<9} {'n':<6} {'precision':<22} {'recall':<22}"]
    for s in scores:
        line = (f"{s.field:<25} {s.value:<14} {s.predictor:<9} {'n=' + str(s.n_truth):<6} "
                f"{_ratio(s.tp, s.predicted):<22} {_ratio(s.tp, s.n_truth):<22}")
        if s.hide:
            line += " " + _rates(s)
        lines.append(line.rstrip())
    return "\n".join(lines)


# ---- readings of the gold set ----------------------------------------------------------------------------------

def _readings_path(version: str, folder: Path) -> Path:
    if not isinstance(version, str) or not version or "/" in version or version.startswith("."):
        raise ValueError(f"not a reader version: {version!r}")
    return Path(folder) / f"{version}.jsonl"


def load_readings(version: str, folder: Path = GOLD_READINGS) -> dict[str, dict]:
    """`readings/<version>.jsonl` as {gold_id: fields}. FileNotFoundError when there is none (a version never run on
    the gold set must not score as a reader that said nothing); ValueError naming the line for a bad or repeated
    one."""
    path = _readings_path(version, folder)
    out: dict[str, dict] = {}
    for number, text in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not text.strip():
            continue
        try:
            obj = json.loads(text)
        except ValueError as err:
            raise ValueError(f"{path.name} line {number}: not JSON: {err}") from None
        if not isinstance(obj, dict) or set(obj) != {"gold_id", "fields"} or not isinstance(obj["gold_id"], str):
            raise ValueError(f"{path.name} line {number}: each line is {{\"gold_id\", \"fields\"}}")
        if obj["gold_id"] in out:
            raise ValueError(f"{path.name} line {number}: {obj['gold_id']} is read twice")
        out[obj["gold_id"]] = obj["fields"]
    return out


def _write_readings(path: Path, readings: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, "".join(json.dumps({"gold_id": k, "fields": v}, ensure_ascii=False) + "\n"
                               for k, v in readings.items()))


def run_reader_on_gold(items: list[GoldItem], runner: Runner, cfg: ReaderConfig, version: str, *,
                       folder: Path = GOLD_READINGS) -> dict[str, dict]:
    """Read the gold items (confirmed and pending) through the night job's prompt and `parse_reply`, `batch_size`
    at a time, with the injected `runner`, and write `readings/<version>.jsonl`. Items already in that file are not
    read again, so a run stopped by a session limit carries on where it stopped. A reading that is not a valid
    reading of its item is dropped (the item stays unread). Progress is written after every batch; a call that does
    not come back ok raises RuntimeError after that write. Only `eval --run` calls this with the real runner: it is
    the one part of eval that spends seat usage."""
    path = _readings_path(version, folder)
    got = load_readings(version, folder) if path.exists() else {}
    todo = [i for i in items if i.gold_id not in got]
    prompt = cfg.prompt_path.read_text(encoding="utf-8")
    for start in range(0, len(todo), cfg.batch_size):
        batch = todo[start:start + cfg.batch_size]
        keys = {n: (item, body_sha256(item.body)) for n, item in enumerate(batch, start=1)}
        payload = {"reader_version": version, "postings": [
            {"posting_id": n, "body_sha256": sha, "reader_version": version, "title": item.title,
             "location": item.location, "body": item.body} for n, (item, sha) in keys.items()]}
        try:
            result = runner(prompt, json.dumps(payload, ensure_ascii=False), cfg.model)
            outcome = classify_run(result, session_limit_patterns=cfg.session_limit_patterns,
                                   auth_patterns=cfg.auth_patterns)
            replies = parse_reply(result.stdout) if outcome == "ok" else None
        except Exception as err:  # noqa: BLE001 - a failed call stops the run; what was read is kept
            outcome, replies = f"failed ({type(err).__name__}: {err})", None
        if replies is None:
            _write_readings(path, got)
            raise RuntimeError(f"gold read stopped at item {start + 1} of {len(todo)}: {outcome}; "
                               f"{len(got)} readings kept in {path.name}")
        for obj in replies:
            if not isinstance(obj, dict) or obj.get("posting_id") not in keys:
                continue
            item, sha = keys[obj["posting_id"]]
            if obj.get("body_sha256") != sha or item.gold_id in got:
                continue
            try:
                reading = PostingReading.from_json({**obj, "reader_version": version})
            except ValueError:
                continue
            got[item.gold_id] = reading.to_json()["fields"]
        _write_readings(path, got)
    return got


# ---- the gate --------------------------------------------------------------------------------------------------

def not_worse(new: list[ClassScore], old: list[ClassScore], *, min_n: int = MIN_CLASS_N) -> list[str]:
    """The regressions, one line each; empty means no class got worse. A regression is a reader or both row whose
    precision or recall is below the old one where both are defined, on a class with at least `min_n` labeled
    examples in both sets (ruling 5: smaller classes are reported, not gated). A class missing from `old` is not
    compared."""
    before = {(s.field, s.value, s.predictor): s for s in old}
    lines = []
    for s in new:
        if s.predictor not in ("reader", "both"):
            continue
        o = before.get((s.field, s.value, s.predictor))
        if o is None or s.n_truth < min_n or o.n_truth < min_n:
            continue
        for what, was, now in (("precision", o.precision, s.precision), ("recall", o.recall, s.recall)):
            if was is not None and now is not None and now < was:
                lines.append(f"{s.field} = {s.value} ({s.predictor}): {what} {was:.3f} -> {now:.3f} "
                             f"(n={s.n_truth})")
    return lines


@dataclass
class Gate:
    """The gate's lines and verdict: `promote`, `reject`, or `cannot` (could not be evaluated, also a reject)."""
    lines: list[str] = dc_field(default_factory=list)
    cannot: bool = False
    reject: bool = False

    def check(self, ok: bool | None, label: str, detail: str = "", *, fatal: bool = False) -> None:
        """gate.py's `Result.check`: None is UNKNOWN and a FAIL of a `fatal` check means cannot evaluate."""
        if ok is None:
            mark, self.cannot = "UNKNOWN", True
        elif ok:
            mark = "PASS"
        else:
            mark = "FAIL"
            if fatal:
                self.cannot = True
            else:
                self.reject = True
        self.lines.append(f"  [{mark:^7}] {label}" + (f"   {detail}" if detail else ""))

    def note(self, text: str) -> None:
        self.lines.append(f"  [{'-':^7}] {text}")

    @property
    def verdict(self) -> str:
        return "cannot" if self.cannot else "reject" if self.reject else "promote"

    @property
    def ok(self) -> bool:
        return self.verdict == "promote"

    def text(self) -> str:
        tail = {"promote": "VERDICT: may be promoted (Devanshu's command: promote)",
                "reject": "VERDICT: REJECT",
                "cannot": "VERDICT: CANNOT EVALUATE -> reject"}[self.verdict]
        return "\n".join(self.lines + ["", "  " + tail])


def _by_key(scores: list[ClassScore]) -> dict[tuple[str, str, str], ClassScore]:
    return {(s.field, s.value, s.predictor): s for s in scores}


def gate(items: list[GoldItem], new_scores: list[ClassScore], old_scores: list[ClassScore] | None, *,
         readings: dict[str, dict], version: str, current_version: str) -> Gate:
    """Whether reader `version` may be promoted, with its numbers. Fail closed: every check must pass, and an
    unmet precondition stops the gate before any threshold is computed. `old_scores` are the promoted version's
    (None before the first promotion). `current_version` is the version the files make now."""
    g = Gate()
    keys = _by_key(new_scores)
    confirmed = [i for i in items if i.status == "confirmed"]
    g.lines.append("  PRECONDITIONS (an UNKNOWN or FAIL here is a reject, and nothing below is computed)")
    g.check(version == current_version, "the version is the one the prompt, model and schema make now",
            f"{version} vs {current_version}", fatal=True)
    g.check(len(confirmed) >= MIN_CONFIRMED, f"at least {MIN_CONFIRMED} confirmed items", f"have {len(confirmed)}",
            fatal=True)
    unread = [i.gold_id for i in confirmed if i.gold_id not in readings]
    g.check(not unread, "every confirmed item has a reading at this version",
            f"{len(unread)} unread" + (f", first {unread[0]}" if unread else ""), fatal=True)
    for field, cls in HIDE_CLASSES:
        s = keys.get((field, cls, "both"))
        n = s.n_truth if s is not None else 0
        g.check(n >= MIN_CLASS_N, f"at least {MIN_CLASS_N} labeled {field} = {cls}", f"have {n}", fatal=True)
    if g.cannot:
        return g
    g.lines.append("")
    g.lines.append("  HIDE CLASSES (a false hide is the worst error: zero for both; a miss only shows a posting)")
    for field, cls in HIDE_CLASSES:
        reader, rules, both = (keys[(field, cls, p)] for p in ("reader", "rules", "both"))
        bound = f"; 0 errors bound the rate at 3/{both.predicted} = {300 / both.predicted:.1f}%" if (
            both.fp == 0 and both.predicted) else ""
        g.check(both.fp <= MAX_BOTH_FALSE_HIDES, f"{field} = {cls}: false hides (both) {both.fp} of "
                f"{both.predicted} it would hide", f"must be {MAX_BOTH_FALSE_HIDES}{bound}")
        rate = reader.fp / reader.predicted if reader.predicted else None
        g.check(rate is not None and rate <= MAX_READER_FALSE_HIDE_RATE,
                f"{field} = {cls}: false hides (reader) {reader.fp} of {reader.predicted}"
                + (f" ({100 * rate:.1f}%)" if rate is not None else ""),
                f"at most {100 * MAX_READER_FALSE_HIDE_RATE:.0f}%" + ("" if rate is not None else
                                                                      "; the reader predicts none"))
        miss = both.fn / both.n_truth
        g.check(miss <= MAX_BOTH_MISS_RATE, f"{field} = {cls}: misses (both) {both.fn} of {both.n_truth} "
                f"({100 * miss:.1f}%)", f"at most {100 * MAX_BOTH_MISS_RATE:.0f}% (collapse floor only)")
        g.note(f"{field} = {cls}: rules alone, {_rates(rules)} (reported, the rules are #4's)")
    g.lines.append("")
    g.lines.append(f"  AGAINST THE PROMOTED VERSION (classes with at least {MIN_CLASS_N} labeled examples)")
    if old_scores is None:
        g.note("no promoted version yet: the thresholds above decide")
    else:
        regressions = not_worse(new_scores, old_scores)
        g.check(not regressions, "no class gets worse, reader or both",
                f"{len(regressions)} regressions" if regressions else "")
        g.lines.extend(f"      {line}" for line in regressions)
    return g


def _write_promoted(config_path: Path, version: str) -> None:
    text = Path(config_path).read_text(encoding="utf-8")
    found = _PROMOTED_LINE.findall(text)
    if len(found) != 1:
        raise ValueError(f"{config_path} must hold exactly one promoted_version line, found {len(found)}")
    atomic_write(Path(config_path), _PROMOTED_LINE.sub(f'promoted_version = "{version}"', text, count=1))


def promote(version: str, items: list[GoldItem], new_scores: list[ClassScore], old_scores: list[ClassScore] | None,
            config_path: Path = CONFIG_PATH, *, readings: dict[str, dict],
            current_version: str | None = None) -> Gate:
    """Run the gate and, only when it passes, rewrite the single `promoted_version = "..."` line in `config_path`
    (atomic replace; every other line byte for byte). Raises ValueError with the gate's text, the regressions
    among it, and leaves the file untouched, when it does not pass. Never commits: Devanshu does."""
    if not isinstance(version, str) or not _VERSION.match(version):
        raise ValueError(f"not a reader version (rv- and 12 hex): {version!r}")
    if current_version is None:
        current_version = reader_version(load_reader_config(config_path))
    result = gate(items, new_scores, old_scores, readings=readings, version=version, current_version=current_version)
    if not result.ok:
        raise ValueError(result.text())
    _write_promoted(config_path, version)
    return result


def scores_json(scores: list[ClassScore]) -> list[dict]:
    return [{"field": s.field, "value": s.value, "predictor": s.predictor, "tp": s.tp, "fp": s.fp, "fn": s.fn,
             "n_truth": s.n_truth, "precision": s.precision, "recall": s.recall, "n_items": s.n_items}
            for s in scores]

