"""DOL LCA disclosure data, aggregated to one summary per employer per fiscal year (#31 Task 4).

The counts must be exact and explainable, so every rule is a plain filter:

- Per fiscal year, a CASE_NUMBER appearing in two quarters keeps the row with the latest DECISION_DATE (a later
  row wins a tie, and a row with no readable date loses to one with a date). That is decided before any other
  filter, so a case certified in Q1 and withdrawn in Q2 is gone.
- Then only H-1B rows with status Certified, Certified - Withdrawn or Certified-Withdrawn count.
- `software` is a row whose SOC (first seven characters) is in SOFT_SOC. `jr` is a software row at wage level I or
  II, `level1` at level I, `newemp_jr` a `jr` row with NEW_EMPLOYMENT above zero.
- Employers are grouped by `norm_employer`, the same rule the research script used, so its counts reproduce. A
  group holding more than one valid FEIN is split into one employer per FEIN (two companies that normalize alike are
  never blended); rows with no valid FEIN join the group's most frequent FEIN.
- A median wage uses a wage annualized from its unit (Year x1, Hour x2080, Week x52, Bi-Weekly x26, Month x12) and
  skips anything outside 10,000 to 2,000,000 USD a year, never clamping.

A whole run is read before anything is returned, so a damaged file raises (a TabularError from the reader) and
never yields a partial or empty answer. One fiscal year is held in memory at a time.
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, Sequence

from outreach.rules.facts import EvidenceDraft
from outreach.sources.base import Candidate
from outreach.sources.capabilities import SharedKey
from outreach.sources.keys import classify_domain, normalize_fein, registrable_domain
from outreach.sources.tabular import EmptyTable, read_rows

if TYPE_CHECKING:
    from outreach.sources.lca_rules import LcaConfig

COLUMNS = [
    "CASE_NUMBER", "CASE_STATUS", "DECISION_DATE", "VISA_CLASS", "SOC_CODE", "PW_WAGE_LEVEL", "NEW_EMPLOYMENT",
    "EMPLOYER_NAME", "TRADE_NAME_DBA", "EMPLOYER_CITY", "EMPLOYER_STATE", "EMPLOYER_FEIN", "NAICS_CODE",
    "SECONDARY_ENTITY", "TOTAL_WORKSITE_LOCATIONS", "WORKSITE_STATE", "H-1B_DEPENDENT", "WILLFUL_VIOLATOR",
    "WAGE_RATE_OF_PAY_FROM", "WAGE_UNIT_OF_PAY", "EMPLOYER_POC_EMAIL", "AGENT_ATTORNEY_EMAIL_ADDRESS",
    "PREPARER_EMAIL",
]

SOFT_SOC = frozenset(("15-1252", "15-1253", "15-1211", "15-1221", "15-1242", "15-1243", "15-1244", "15-1299",
                      "15-2051"))
COMPUTER_SOC = ("17-2061", "17-2072")  # with every `15-` code, the computer_share numerator
CERTIFIED = frozenset(("certified", "certified - withdrawn", "certified-withdrawn"))
PERIODS_PER_YEAR = {"year": 1, "hour": 2080, "week": 52, "bi-weekly": 26, "biweekly": 26, "month": 12}
MIN_WAGE, MAX_WAGE = 10_000, 2_000_000  # a yearly figure outside this is a typo or a wrong unit, never data


def fiscal_year_of(path: Path) -> int:
    """`FY(\\d{4})` from the file name. ValueError without one."""
    m = re.search(r"FY(\d{4})", Path(path).name)
    if not m:
        raise ValueError(f"{Path(path).name}: no fiscal year (FYyyyy) in the file name")
    return int(m.group(1))


_LEGAL = (
    r"\b(incorporated|inc|llc|l l c|llp|lp|ltd|limited|corp|corporation|co|company|plc|pllc|pc|pbc|"
    r"gmbh|ag|sa|bv|nv|pty|usa|us|america|the)\b"
)


def norm_employer(name: str) -> str:
    """The research's `norm_name`: lower case, `&` to `and`, parentheses and a `dba` tail dropped, punctuation and
    company-form words removed. Letters outside a-z count as punctuation, as in the research."""
    s = str(name or "").lower()
    s = s.replace("&", " and ")
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"\bd/?b/?a\b.*$", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(_LEGAL, " ", s)
    return re.sub(r"\s+", " ", s).strip()


_EXCEL_EPOCH = datetime.date(1899, 12, 30)
_MAX_SERIAL = 2958465  # 9999-12-31
_ISO = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:$|[T\s])")
_SERIAL = re.compile(r"^\d+(\.\d*)?$")


def parse_decision_date(raw: str | None) -> str | None:
    """An Excel serial ('45930' gives '2025-09-30'; epoch 1899-12-30) or an ISO date or datetime prefix, else None."""
    text = (raw or "").strip()
    m = _ISO.match(text)
    if m:
        try:
            return datetime.date.fromisoformat(m.group(1)).isoformat()
        except ValueError:
            return None
    if _SERIAL.match(text):
        days = int(float(text))
        if 1 <= days <= _MAX_SERIAL:
            return (_EXCEL_EPOCH + datetime.timedelta(days=days)).isoformat()
    return None


@dataclass(frozen=True)
class FyStats:
    fy: int
    total: int
    software: int
    jr: int
    level1: int
    newemp_jr: int
    last_decision: str
    computer_share: float
    jr_share_soft: float
    newemp_share_soft: float
    multi_worksite_share: float
    n_worksite_states: int
    h1b_dependent: bool
    willful: bool
    secondary: int
    median_jr_wage: int | None

    def evidence(self) -> dict[str, int]:
        """The `lca` evidence value: fy, software, jr, level1, newemp_jr and, only when known, median_jr_wage.
        Every value is a non-negative whole number (the schema refuses anything else)."""
        out = {"fy": self.fy, "software": self.software, "jr": self.jr, "level1": self.level1,
               "newemp_jr": self.newemp_jr}
        if self.median_jr_wage is not None:
            out["median_jr_wage"] = self.median_jr_wage
        return out


@dataclass(frozen=True)
class Employer:
    key: str
    legal_name: str
    dba: str
    fein: str | None
    city: str
    state: str
    naics: str
    domain: str | None
    domain_kind: str
    fy: dict[int, FyStats]
    feins_seen: tuple[str, ...] = ()  # the distinct valid FEINs of the name group, sorted (more than one: split)


class _Filing(NamedTuple):
    name: str
    dba: str
    city: str
    state: str
    fein: str
    naics: str
    soc: str
    level: str
    new: bool
    secondary: bool
    multi: bool
    wstate: str
    dependent: bool
    willful: bool
    wage: float | None
    domain: str
    kind: str  # of the POC mailbox: company, free, peo, attorney, or '' (none or not a mailbox)


def _num(text: str) -> float:
    try:
        v = float(text.replace(",", "").replace("$", "").strip())
    except ValueError:
        return 0.0
    return v if math.isfinite(v) else 0.0


def _yes(text: str) -> bool:
    return text.strip().lower() in ("y", "yes")


def _annual_wage(wage: str, unit: str) -> float | None:
    """The annualized wage, or None. The unit must be one of PERIODS_PER_YEAR, the wage a finite number, and the
    yearly figure within MIN_WAGE to MAX_WAGE; anything else is skipped, never clamped."""
    periods = PERIODS_PER_YEAR.get(unit.strip().lower())
    if periods is None:
        return None
    try:
        v = float(wage.replace(",", "").replace("$", "").strip())
    except ValueError:
        return None
    if not math.isfinite(v):
        return None
    yearly = v * periods
    return yearly if MIN_WAGE <= yearly <= MAX_WAGE else None  # inf fails the comparison too


def _poc(r: dict[str, str]) -> tuple[str, str]:
    """(domain, kind) of the row's point-of-contact mailbox. Only a `company` domain that is not the attorney's or
    the preparer's is a domain; those two make `attorney`."""
    poc = registrable_domain(r["EMPLOYER_POC_EMAIL"])
    if poc is None:
        return "", ""
    cls = classify_domain(poc)
    if cls == "platform":
        return "", ""
    if cls != "company":
        return "", cls
    if poc in (registrable_domain(r["AGENT_ATTORNEY_EMAIL_ADDRESS"]), registrable_domain(r["PREPARER_EMAIL"])):
        return "", "attorney"
    return sys.intern(poc), "company"


def _filing(r: dict[str, str]) -> _Filing | None:
    """The row's compact record, or None when it is not a certified H-1B filing (or has no employer name)."""
    if r["VISA_CLASS"].strip().upper() != "H-1B" or r["CASE_STATUS"].strip().lower() not in CERTIFIED:
        return None
    name = r["EMPLOYER_NAME"].strip()
    if not name:
        return None
    domain, kind = _poc(r)
    i = sys.intern
    return _Filing(
        name=i(name), dba=i(r["TRADE_NAME_DBA"].strip()), city=i(r["EMPLOYER_CITY"].strip()),
        state=i(r["EMPLOYER_STATE"].strip()), fein=i(normalize_fein(r["EMPLOYER_FEIN"]) or ""),
        naics=i(r["NAICS_CODE"].strip()), soc=i(r["SOC_CODE"].strip()[:7]),
        level=i(r["PW_WAGE_LEVEL"].strip().upper()), new=_num(r["NEW_EMPLOYMENT"]) > 0,
        secondary=_yes(r["SECONDARY_ENTITY"]), multi=_num(r["TOTAL_WORKSITE_LOCATIONS"]) > 1,
        wstate=i(r["WORKSITE_STATE"].strip()), dependent=_yes(r["H-1B_DEPENDENT"]),
        willful=_yes(r["WILLFUL_VIOLATOR"]), wage=_annual_wage(r["WAGE_RATE_OF_PAY_FROM"], r["WAGE_UNIT_OF_PAY"]),
        domain=domain, kind=kind)


def _read_year(paths: Sequence[Path], fy: int, summary: Counter) -> dict[str, tuple[str, _Filing | None]]:
    """case -> (decision date, filing or None). A row is kept in the map even when it is not countable, so a later
    withdrawal or denial of the same case cancels the earlier certification. Every row read whose decision date is
    readable but outside the fiscal year (Oct 1 of fy-1 to Sep 30 of fy) is counted in `summary['fy_mismatch']`; it
    is still aggregated."""
    cases: dict[str, tuple[str, _Filing | None]] = {}
    first, last = f"{fy - 1}-10-01", f"{fy}-09-30"
    seq = 0
    for path in paths:
        for r in read_rows(path, COLUMNS):
            seq += 1
            date = parse_decision_date(r["DECISION_DATE"]) or ""
            if date and not first <= date <= last:
                summary["fy_mismatch"] += 1
            case = r["CASE_NUMBER"].strip() or f"\0{seq}"  # a row with no case number cannot duplicate anything
            held = cases.get(case)
            if held is not None and held[0] > date:
                continue
            cases[case] = (date, _filing(r))
    return cases


class _Year:
    __slots__ = ("total", "software", "jr", "level1", "newemp_jr", "newemp_soft", "computer", "multi",
                 "secondary", "dependent", "willful", "last", "states", "wages")

    def __init__(self):
        self.total = self.software = self.jr = self.level1 = self.newemp_jr = self.newemp_soft = 0
        self.computer = self.multi = self.secondary = 0
        self.dependent = self.willful = False
        self.last = ""
        self.states: set[str] = set()
        self.wages: list[float] = []


class _Acc:
    __slots__ = ("names", "dbas", "feins", "cities", "states", "naics", "domains", "kinds", "years")

    def __init__(self):
        self.names, self.dbas, self.feins, self.cities = Counter(), Counter(), Counter(), Counter()
        self.states, self.naics, self.domains, self.kinds = Counter(), Counter(), Counter(), Counter()
        self.years: dict[int, _Year] = {}


def _add(acc: _Acc, fy: int, date: str, f: _Filing) -> None:
    for counter, value in ((acc.names, f.name), (acc.dbas, f.dba), (acc.feins, f.fein), (acc.cities, f.city),
                           (acc.states, f.state), (acc.naics, f.naics), (acc.domains, f.domain),
                           (acc.kinds, f.kind)):
        if value:
            counter[value] += 1
    y = acc.years.setdefault(fy, _Year())
    y.total += 1
    y.last = max(y.last, date)
    y.multi += f.multi
    y.secondary += f.secondary
    y.dependent = y.dependent or f.dependent
    y.willful = y.willful or f.willful
    if f.soc.startswith("15-") or f.soc in COMPUTER_SOC:
        y.computer += 1
    if f.soc in SOFT_SOC:
        y.software += 1
        y.newemp_soft += f.new
        if f.wstate:
            y.states.add(f.wstate)
        if f.level in ("I", "II"):
            y.jr += 1
            y.newemp_jr += f.new
            if f.wage is not None:
                y.wages.append(f.wage)
        if f.level == "I":
            y.level1 += 1


def _top(counter: Counter) -> str:
    """The most common value; ties break alphabetically. '' for an empty counter."""
    return min(counter.items(), key=lambda kv: (-kv[1], kv[0]))[0] if counter else ""


def _median_whole(values: list[float]) -> int | None:
    if not values:
        return None
    v = sorted(values)
    mid = len(v) // 2
    m = v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2
    return math.floor(m + 0.5)


def _stats(fy: int, y: _Year) -> FyStats:
    return FyStats(
        fy=fy, total=y.total, software=y.software, jr=y.jr, level1=y.level1, newemp_jr=y.newemp_jr,
        last_decision=y.last, computer_share=y.computer / y.total,
        jr_share_soft=y.jr / y.software if y.software else 0.0,
        newemp_share_soft=y.newemp_soft / y.software if y.software else 0.0,
        multi_worksite_share=y.multi / y.total, n_worksite_states=len(y.states), h1b_dependent=y.dependent,
        willful=y.willful, secondary=y.secondary, median_jr_wage=_median_whole(y.wages))


def _merge(into: _Acc, other: _Acc) -> None:
    for name in ("names", "dbas", "feins", "cities", "states", "naics", "domains", "kinds"):
        getattr(into, name).update(getattr(other, name))
    for fy, o in other.years.items():
        y = into.years.setdefault(fy, _Year())
        for name in ("total", "software", "jr", "level1", "newemp_jr", "newemp_soft", "computer", "multi",
                     "secondary"):
            setattr(y, name, getattr(y, name) + getattr(o, name))
        y.dependent = y.dependent or o.dependent
        y.willful = y.willful or o.willful
        y.last = max(y.last, o.last)
        y.states |= o.states
        y.wages.extend(o.wages)


def _employer(key: str, acc: _Acc, feins_seen: tuple[str, ...]) -> Employer:
    if acc.domains:
        domain, kind = _top(acc.domains), "company"
    else:
        domain, kind = None, _top(acc.kinds) or "none"
    return Employer(
        key=key, legal_name=_top(acc.names), dba=_top(acc.dbas), fein=_top(acc.feins) or None,
        city=_top(acc.cities), state=_top(acc.states), naics=_top(acc.naics), domain=domain, domain_kind=kind,
        fy={fy: _stats(fy, y) for fy, y in sorted(acc.years.items())}, feins_seen=feins_seen)


def _employer_key(name: str) -> str:
    """The grouping key. A name made only of company-form words (`The Company`) falls back to its letters and digits
    so unrelated employers do not collapse onto an empty key."""
    return norm_employer(name) or re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _rows(acc: _Acc) -> int:
    return sum(y.total for y in acc.years.values())


class Employers(dict):
    """The employers by key, plus a `summary` Counter of what the load saw: `fy_mismatch` is the number of rows read
    whose decision date is outside their file's fiscal year (counted, still aggregated)."""

    def __init__(self, employers: dict[str, Employer], summary: Counter):
        super().__init__(employers)
        self.summary = summary

    @property
    def fy_mismatch(self) -> int:
        return self.summary["fy_mismatch"]


def load_employers(paths: Sequence[Path]) -> Employers:
    """Every employer with at least one counted filing, large ones included, keyed by `norm_employer`. Files are
    grouped by the fiscal year in their name and each year is deduplicated on its own. Reads every file fully before
    returning: a TabularError (damage, missing columns, no data rows) propagates, never a partial result. No files
    at all is a ValueError, never an empty answer.

    A name group with more than one valid FEIN becomes one Employer per FEIN, keyed `<name>#<fein>`, each with its
    own legal name, counts, domain and decisions; rows with no valid FEIN join the group's most frequent FEIN (ties
    alphabetical). The same FEIN under different names stays several employers: payroll providers share FEINs, and
    `mark_shared_key` deals with that later. `feins_seen` lists the group's valid FEINs on every member."""
    if not paths:
        raise ValueError("no LCA files given")
    by_year: dict[int, list[Path]] = {}
    for p in paths:
        by_year.setdefault(fiscal_year_of(Path(p)), []).append(Path(p))
    summary: Counter = Counter()
    groups: dict[str, dict[str, _Acc]] = {}  # name key -> valid FEIN ('' for none) -> accumulator
    for fy in sorted(by_year):
        for date, filing in _read_year(by_year[fy], fy, summary).values():
            if filing is not None:
                by_fein = groups.setdefault(_employer_key(filing.name), {})
                _add(by_fein.setdefault(filing.fein, _Acc()), fy, date, filing)
    out: dict[str, Employer] = {}
    for key in sorted(groups):
        by_fein = groups[key]
        feins = tuple(sorted(f for f in by_fein if f))
        none = by_fein.pop("", None)
        if not feins:
            out[key] = _employer(key, none, ())
            continue
        if none is not None:
            top = min(feins, key=lambda f: (-_rows(by_fein[f]), f))
            _merge(by_fein[top], none)
        for fein in feins:
            out[key if len(feins) == 1 else f"{key}#{fein}"] = _employer(
                key if len(feins) == 1 else f"{key}#{fein}", by_fein[fein], feins)
    return Employers(dict(sorted(out.items())), summary)


def _config_version(cfg) -> str:
    """The first 8 hex digits of sha256 over every setting (regexes by pattern), so any config edit changes it and a
    comment or spacing edit in lca.toml does not."""
    plain = {f.name: getattr(cfg, f.name) for f in dataclasses.fields(cfg)}
    text = json.dumps(plain, sort_keys=True, default=lambda v: v.pattern if hasattr(v, "pattern") else list(v))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


class _Loaded(NamedTuple):
    employers: Employers
    candidates: tuple[Candidate, ...]
    shared: tuple[SharedKey, ...]
    tiers: dict[str, tuple[int, str]]
    summary: dict[str, int]


class LcaSource:
    """Small software employers from DOL LCA files, one candidate per employer with a qualifying fiscal year
    (staffing-excluded ones included, stored with `staffing_risk` high, never dropped). The files are the input, so
    `discover(since)` ignores `since`. Nothing is read until the first call; the load runs once and is shared by
    `discover`, `shared_keys`, `work_tier` and `summary`. A load that finds no employers, or none that qualify, raises
    EmptyTable: an empty stream is never an answer. No evidence here says anything about company size."""
    name = "lca"
    schedule = "quarterly"

    def __init__(self, paths: Sequence[Path], config: LcaConfig | None = None):
        from outreach.sources.lca_rules import load_lca_config  # lca_rules imports this module: no top-level cycle
        self.paths = [Path(p) for p in paths]
        self.config = config if config is not None else load_lca_config()
        self.version = f"lca-1-{_config_version(self.config)}"
        self._loaded: _Loaded | None = None

    def discover(self, since: str | None = None):
        yield from self._load().candidates

    def shared_keys(self) -> list[SharedKey]:
        return list(self._load().shared)

    def work_tier(self, source_ref: str) -> tuple[int, str] | None:
        return self._load().tiers.get(source_ref)

    def summary(self) -> dict[str, int]:
        """fy_mismatch (rows dated outside their file's fiscal year), employers (loaded), qualifying (yielded),
        excluded (staffing-excluded among those) and cap_exempt (tagged likely among those)."""
        return dict(self._load().summary)

    def _load(self) -> _Loaded:
        if self._loaded is None:
            self._loaded = self._build()
        return self._loaded

    def _build(self) -> _Loaded:
        from outreach.sources.lca_rules import classify, source_fy
        cfg = self.config
        employers = load_employers(self.paths)
        if not employers:
            raise EmptyTable(f"{len(self.paths)} LCA file(s) read but no employers found: no certified H-1B "
                             "filing with an employer name")
        shared = _shared_keys(employers)
        shared_feins = {k.value for k in shared if k.kind == "fein"}
        shared_domains = {k.value for k in shared if k.kind == "domain"}
        candidates, tiers = [], {}
        excluded = cap_exempt = 0
        for emp in employers.values():
            fy = source_fy(emp, cfg)
            if fy is None:
                continue
            verdict = classify(emp, fy, cfg)
            cand = _candidate(emp, fy, verdict, shared_feins, shared_domains)
            candidates.append(cand)
            tiers[cand.source_ref] = (verdict.work_tier, verdict.work_reason)
            excluded += verdict.excluded
            cap_exempt += verdict.cap_exempt == "likely"
        if not candidates:
            software = sum(s.software for e in employers.values() for s in e.fy.values())
            seen = (f"{software} software filing(s) among them" if software
                    else "no software filings among them")
            raise EmptyTable(f"{len(employers)} employer(s) loaded but none qualifies (1 to {cfg.max_small_filings} "
                             f"certified filings in a fiscal year with at least one junior software filing); {seen}")
        summary = {"fy_mismatch": employers.fy_mismatch, "employers": len(employers),
                   "qualifying": len(candidates), "excluded": excluded, "cap_exempt": cap_exempt}
        return _Loaded(employers, tuple(candidates), tuple(shared), tiers, summary)


def _shared_keys(employers: Employers) -> list[SharedKey]:
    """Every FEIN and every company-kind domain used by more than one employer, large ones counted, sorted. The reason
    counts employer groups and lists up to 5 distinct legal names (a FEIN-split pair shares one name)."""
    users: dict[tuple[str, str], list[str]] = {}
    for emp in employers.values():
        fein = normalize_fein(emp.fein)
        if fein:
            users.setdefault(("fein", fein), []).append(emp.legal_name)
        if emp.domain and emp.domain_kind == "company":
            users.setdefault(("domain", emp.domain), []).append(emp.legal_name)
    return [SharedKey(kind, value, f"{len(names)} employers: {', '.join(sorted(set(names))[:5])}")
            for (kind, value), names in sorted(users.items()) if len(names) > 1]


def _candidate(emp: Employer, fy: int, verdict, shared_feins: set[str], shared_domains: set[str]) -> Candidate:
    ref = f"lca:FY{fy % 100:02d}"
    evidence = [EvidenceDraft("lca", json.dumps(s.evidence(), sort_keys=True, separators=(",", ":")), "confirmed",
                              "lca", f"lca:FY{year % 100:02d}", observed_on=s.last_decision or None)
                for year, s in emp.fy.items() if s.software >= 1]
    evidence.append(EvidenceDraft("staffing_risk", verdict.staffing_risk, "estimate", "lca", ref,
                                  quote=verdict.staffing_reason))
    if verdict.cap_exempt == "likely":
        evidence.append(EvidenceDraft("cap_exempt", "likely", "estimate", "lca", ref, quote=verdict.cap_reason))
    if emp.state:  # "City, ST" or bare "ST"; a bare city is not an hq (the company's state could not be read)
        hq = f"{emp.city.title()}, {emp.state}" if emp.city else emp.state
        evidence.append(EvidenceDraft("hq", hq, "estimate", "lca", ref))
    domain = emp.domain if emp.domain_kind == "company" else None
    if domain:
        evidence.append(EvidenceDraft("domain", domain, "guess" if domain in shared_domains else "estimate", "lca",
                                      ref))
    keys = {"legal_name": emp.legal_name}
    if emp.dba:
        keys["dba"] = emp.dba
    fein = normalize_fein(emp.fein)
    if fein and fein not in shared_feins:
        keys["fein"] = fein
    return Candidate(name=emp.dba or emp.legal_name, domain=domain, keys=keys, evidence=tuple(evidence),
                     source_ref="lca:" + emp.key)
