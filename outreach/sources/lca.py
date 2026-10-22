"""DOL LCA disclosure data, aggregated to one summary per employer per fiscal year (#31 Task 4).

The counts must be exact and explainable, so every rule is a plain filter:

- Per fiscal year, a CASE_NUMBER appearing in two quarters keeps the row with the latest DECISION_DATE (a later
  row wins a tie, and a row with no readable date loses to one with a date). That is decided before any other
  filter, so a case certified in Q1 and withdrawn in Q2 is gone.
- Then only H-1B rows with status Certified, Certified - Withdrawn or Certified-Withdrawn count.
- `software` is a row whose SOC (first seven characters) is in SOFT_SOC. `jr` is a software row at wage level I or
  II, `level1` at level I, `newemp_jr` a `jr` row with NEW_EMPLOYMENT above zero.
- Employers are grouped by `norm_employer`, the same rule the research script used, so its counts reproduce.

A whole run is read before anything is returned, so a damaged file raises (a TabularError from the reader) and
never yields a partial or empty answer. One fiscal year is held in memory at a time.
"""
import datetime
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, Sequence

from outreach.sources.keys import classify_domain, normalize_fein, registrable_domain
from outreach.sources.tabular import read_rows

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
HOURS_PER_YEAR = 2080


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
    """The annualized wage: a `Year` wage as is, `Hour` times 2080, any other unit or an unreadable or non-positive
    wage None."""
    kind = unit.strip().lower()
    if kind not in ("year", "hour"):
        return None
    try:
        v = float(wage.replace(",", "").replace("$", "").strip())
    except ValueError:
        return None
    if not math.isfinite(v) or v <= 0:
        return None
    return v * (HOURS_PER_YEAR if kind == "hour" else 1)


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


def _read_year(paths: Sequence[Path]) -> dict[str, tuple[str, _Filing | None]]:
    """case -> (decision date, filing or None). A row is kept in the map even when it is not countable, so a later
    withdrawal or denial of the same case cancels the earlier certification."""
    cases: dict[str, tuple[str, _Filing | None]] = {}
    seq = 0
    for path in paths:
        for r in read_rows(path, COLUMNS):
            seq += 1
            date = parse_decision_date(r["DECISION_DATE"]) or ""
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


def _employer(key: str, acc: _Acc) -> Employer:
    if acc.domains:
        domain, kind = _top(acc.domains), "company"
    else:
        domain, kind = None, _top(acc.kinds) or "none"
    return Employer(
        key=key, legal_name=_top(acc.names), dba=_top(acc.dbas), fein=_top(acc.feins) or None,
        city=_top(acc.cities), state=_top(acc.states), naics=_top(acc.naics), domain=domain, domain_kind=kind,
        fy={fy: _stats(fy, y) for fy, y in sorted(acc.years.items())})


def _employer_key(name: str) -> str:
    """The grouping key. A name made only of company-form words (`The Company`) falls back to its letters and digits
    so unrelated employers do not collapse onto an empty key."""
    return norm_employer(name) or re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def load_employers(paths: Sequence[Path]) -> dict[str, Employer]:
    """Every employer with at least one counted filing, large ones included, keyed by `norm_employer`. Files are
    grouped by the fiscal year in their name and each year is deduplicated on its own. Reads every file fully before
    returning: a TabularError (damage, missing columns, no data rows) propagates, never a partial result. No files
    at all is a ValueError, never an empty answer."""
    if not paths:
        raise ValueError("no LCA files given")
    by_year: dict[int, list[Path]] = {}
    for p in paths:
        by_year.setdefault(fiscal_year_of(Path(p)), []).append(Path(p))
    accs: dict[str, _Acc] = {}
    for fy in sorted(by_year):
        for date, filing in _read_year(by_year[fy]).values():
            if filing is not None:
                _add(accs.setdefault(_employer_key(filing.name), _Acc()), fy, date, filing)
    return {key: _employer(key, accs[key]) for key in sorted(accs)}
