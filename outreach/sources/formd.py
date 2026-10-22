"""SEC Form D filings as funding evidence for companies the store already knows (#31 Task 11).

Input is one quarterly Form D data set from sec.gov (the zip, or a folder holding its files). Three files are read and
joined on ACCESSIONNUMBER: FORMDSUBMISSION.tsv (FILING_DATE, SUBMISSIONTYPE, TESTORLIVE), ISSUERS.tsv (the issuer
marked IS_PRIMARYISSUER_FLAG YES: CIK, ENTITYNAME, CITY, STATEORCOUNTRY) and OFFERING.tsv (INDUSTRYGROUPTYPE,
TOTALAMOUNTSOLD). Dates are the SEC's `14-AUG-2026` or ISO `2026-08-14`.

One candidate per issuer (CIK) that filed on or after `since`, named as on its latest filing, with one `funding`
evidence draft per filing (value `{"amount_usd", "filed_on", "kind"}`, kind `form_d` or `form_d_amendment`). When the
latest filing's address is in a US state, the candidate's `state` is that state (identity matches on it and never
stores it) and an `hq` draft at confidence `guess` records the address: a filing address is often a lawyer's, so it
never changes a company's own state. `amount_usd` is TOTALAMOUNTSOLD, null when it is blank
or not a number (the offering amount may read "Indefinite"; the amount sold is what counts).

Per-source policy:

- Pooled investment funds are skipped (INDUSTRYGROUPTYPE "Pooled Investment Fund", or ISPOOLEDINVESTMENTFUNDTYPE
  true), and so are TEST filings.
- `creates_companies` is False: Form D never creates a company. An issuer that matches nothing strongly is counted as
  `skipped_unmatched` by the discover job and dropped. SPV issuers (investor vehicles named after a brand) are not
  matched: exact names only, since fuzzy matching was right 0 of 10 times.
- Form D never writes `stage` (money raised is not a round, design 5.4), `size` or `headcount`.
- It raises no merge item for a weak or an unmatched issuer: a weak match (the same name in another state) is dropped
  without an item, because nothing is written for a candidate that does not attach. It can raise one in a single
  case: an issuer that attaches strongly (by FEIN, or by name and state) while another company already goes by its
  name gets identity's item for that pair (a weak name hit, or the alias collision `alias:formd`; one item per
  pair), because a collision must be visible.
- The FEIN path: for an issuer whose name key relates to a stored company's (`name_lookup`, which the discover job
  binds to the store: `jobs.store_name_lookup` accepts an equal key or one that is a prefix of the other, 4 or more
  characters, because the SEC name often adds a word such as "Holdings". That widening only costs a request; it never
  decides a match, which is identity's), `https://data.sec.gov/submissions/CIK<10 digits>.json` is fetched once and its `ein` becomes
  `keys["fein"]` when it is a valid FEIN (not blank, not nine zeros). A FEIN that LCA marked shared never attaches
  (identity's rule; a PEO's `ein` joins none of its clients). A failed lookup (a transport error, a non-200 answer,
  a body that is not a JSON object) drops that issuer's `fein` key, is counted in `fetch_errors` and never drops the
  filing; `summary()` carries the count to the job summary, and a run where every lookup failed is `partial` (the discover
  job says "<n> SEC lookups failed"). A redirect is not followed.
- Every request to SEC carries the User-Agent `SEC_USER_AGENT` (SEC's access policy asks for contact details; the
  contact is approved for SEC hosts only). `sec_headers` returns it for SEC's own hosts and nothing for any other
  host. Requests go through a `SpacedClient` at about one a second.
- Error is not empty: a data set that cannot be read, lacks a column or a file, has a file with a header and no rows,
  or whose files share no accession numbers raises a `TabularError`. A quiet period (nothing filed since `since`) is
  not an error.
"""
from __future__ import annotations

import contextlib
import csv
import datetime
import io
import json
import re
import zipfile
import zlib
from collections.abc import Callable, Iterator, Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TextIO
from urllib.parse import urlsplit

from outreach.net import FetchError, HttpClient, SpacedClient
from outreach.rules.facts import EvidenceDraft
from outreach.sources.base import Candidate
from outreach.sources.keys import US_STATES, normalize_fein
from outreach.sources.tabular import EmptyTable, MissingColumns, TabularError
from outreach.store import name_key

__all__ = ["SEC_USER_AGENT", "FormDSource", "sec_headers", "parse_filing_date"]

SEC_USER_AGENT = "outreach-research chicholikar.d@northeastern.edu"
_SEC_HOSTS = frozenset({"sec.gov", "www.sec.gov", "data.sec.gov"})
_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{}.json"

_MONTHS = {m: i for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)}
_SEC_DATE = re.compile(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})")
_ISO_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})(?:$|[T ])")
_AMOUNT = re.compile(r"\d+(?:\.\d+)?")
_KINDS = {"D": "form_d", "D/A": "form_d_amendment"}
_FUND = "pooled investment fund"

_SUBMISSION_COLUMNS = ("ACCESSIONNUMBER", "FILING_DATE", "SUBMISSIONTYPE")
_ISSUER_COLUMNS = ("ACCESSIONNUMBER", "IS_PRIMARYISSUER_FLAG", "CIK", "ENTITYNAME", "CITY", "STATEORCOUNTRY")
_OFFERING_COLUMNS = ("ACCESSIONNUMBER", "INDUSTRYGROUPTYPE", "TOTALAMOUNTSOLD")
_POOLED_COLUMN = "ISPOOLEDINVESTMENTFUNDTYPE"  # optional: "true" on a pooled investment fund's offering
_TEST_COLUMN = "TESTORLIVE"  # optional: older sets may not carry it


def sec_headers(url: str) -> dict[str, str]:
    """The SEC User-Agent for a URL on sec.gov, www.sec.gov or data.sec.gov; an empty dict for any other host."""
    host = (urlsplit(url).hostname or "").lower() if isinstance(url, str) else ""
    return {"User-Agent": SEC_USER_AGENT} if host in _SEC_HOSTS else {}


def parse_filing_date(text: str) -> str | None:
    """`14-AUG-2026` or `2026-08-14` (a time after it is ignored) as `2026-08-14`; None when it is neither."""
    text = (text or "").strip()
    try:
        m = _SEC_DATE.fullmatch(text)
        if m:
            month = _MONTHS.get(m[2].upper())
            return datetime.date(int(m[3]), month, int(m[1])).isoformat() if month else None
        m = _ISO_DATE.match(text)
        return datetime.date.fromisoformat(m[1]).isoformat() if m else None
    except ValueError:
        return None


def _amount(text: str) -> int | None:
    text = (text or "").strip()
    if not _AMOUNT.fullmatch(text):
        return None  # blank, "Indefinite" or anything else that is not a dollar figure
    try:
        return int(Decimal(text).to_integral_value())
    except InvalidOperation:  # pragma: no cover - the pattern already admits only decimals
        return None


@contextlib.contextmanager
def _open_member(dataset: Path, name: str, cap: int) -> Iterator[TextIO]:
    """The named file of a data set (a zip, or a folder with the files in it or one folder down) as text."""
    label = dataset.name
    try:
        if dataset.is_dir():
            found = _find_file(dataset, name)
            if found is None:
                raise TabularError(f"{label}: no {name}")
            if found.stat().st_size > cap:
                raise TabularError(f"{label}: {name} is over the {cap} byte cap")
            with open(found, encoding="utf-8-sig", newline="") as fh:
                yield fh
        elif dataset.is_file():
            with zipfile.ZipFile(dataset) as zf:
                member = next((i for i in zf.infolist() if not i.is_dir()
                               and Path(i.filename).name.upper() == name.upper()), None)
                if member is None:
                    raise TabularError(f"{label}: no {name}")
                if member.file_size > cap:
                    raise TabularError(f"{label}: {name} is {member.file_size} bytes, over the {cap} byte cap")
                with zf.open(member) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as fh:
                    yield fh
        else:
            raise TabularError(f"{label}: no such data set")
    except (zipfile.BadZipFile, zlib.error, EOFError, OSError, UnicodeDecodeError, csv.Error) as err:
        raise TabularError(f"{label}: cannot read {name}: {err}") from err


def _find_file(folder: Path, name: str) -> Path | None:
    for parent in (folder, *sorted(p for p in folder.iterdir() if p.is_dir())):
        for entry in sorted(parent.iterdir()):
            if entry.is_file() and entry.name.upper() == name.upper():
                return entry
    return None


def _read_tsv(dataset: Path, name: str, required: tuple[str, ...], optional: tuple[str, ...] = (),
              cap: int = 0) -> Iterator[dict[str, str]]:
    """Yield one dict per data row with the required and optional columns ('' for a short row or an absent optional
    column). Raises MissingColumns, EmptyTable (no header, or a header and no rows) or TabularError."""
    with _open_member(dataset, name, cap) as fh:
        try:
            header = None
            count = 0
            for record in csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE):
                if not any(c.strip() for c in record):
                    continue
                if header is None:
                    header = {c.strip().upper(): i for i, c in enumerate(record)}
                    missing = [c for c in required if c not in header]
                    if missing:
                        raise MissingColumns(f"{dataset.name}: {name} lacks columns {', '.join(missing)}")
                    continue
                count += 1
                yield {c: (record[header[c]].strip() if c in header and header[c] < len(record) else "")
                       for c in (*required, *optional)}
        except (UnicodeDecodeError, csv.Error, zlib.error, EOFError) as err:
            raise TabularError(f"{dataset.name}: cannot read {name}: {err}") from err
        if header is None:
            raise EmptyTable(f"{dataset.name}: {name} has no header row")
        if count == 0:
            raise EmptyTable(f"{dataset.name}: {name} has a header but no data rows")


class FormDSource:
    """Form D funding evidence. See the module docstring for what is read, skipped and fetched."""

    name = "formd"
    schedule = "daily"
    version = "formd-1"
    creates_companies = False
    max_member_bytes = 1024 ** 3  # a quarter is about 30 MB unpacked; over this the member is refused unread

    def __init__(self, dataset: Path, client: HttpClient, name_lookup: Callable[[str], bool] | None = None, *,
                 interval_s: float = 1.0):
        """`dataset` is the quarterly zip or a folder of its files. `name_lookup(name_key)` says whether an issuer
        is worth an SEC lookup (default: always); the discover job binds a store-backed one unless one is given
        here. `interval_s` spaces requests to data.sec.gov (SEC allows 10 a second; about 1 is asked)."""
        if interval_s < 0:
            raise ValueError("interval_s cannot be negative")
        self.dataset = Path(dataset)
        self.client = SpacedClient(client, per_host={"data.sec.gov": interval_s})
        self._explicit_lookup = name_lookup is not None
        self.name_lookup: Callable[[str], bool] = name_lookup or (lambda key: True)
        self._summary = self._fresh_summary()

    @staticmethod
    def _fresh_summary() -> dict[str, int]:
        return {"filings": 0, "issuers": 0, "funds_skipped": 0, "test_filings": 0, "unreadable": 0, "fetches": 0,
                "fetch_errors": 0}

    def bind_name_lookup(self, lookup: Callable[[str], bool]) -> None:
        """Used by the discover job: a store-backed lookup, unless the caller gave one at construction."""
        if not self._explicit_lookup:
            self.name_lookup = lookup

    def summary(self) -> dict[str, int]:
        """What the latest `discover` pass saw: filings (rows read), issuers (yielded), funds_skipped, test_filings,
        unreadable (rows with no usable date, type, issuer, CIK or name), fetches (SEC lookups made) and
        fetch_errors (lookups that failed; each dropped only its issuer's `fein`)."""
        return dict(self._summary)

    # --- reading -------------------------------------------------------------------------------------------

    def _load(self):
        cap = self.max_member_bytes
        submissions: dict[str, dict[str, str]] = {}
        for row in _read_tsv(self.dataset, "FORMDSUBMISSION.tsv", _SUBMISSION_COLUMNS, (_TEST_COLUMN,), cap):
            submissions.setdefault(row["ACCESSIONNUMBER"], row)
        offerings: dict[str, dict[str, str]] = {}
        for row in _read_tsv(self.dataset, "OFFERING.tsv", _OFFERING_COLUMNS, (_POOLED_COLUMN,), cap):
            offerings.setdefault(row["ACCESSIONNUMBER"], row)
        issuers: dict[str, dict[str, str]] = {}
        for row in _read_tsv(self.dataset, "ISSUERS.tsv", _ISSUER_COLUMNS, (), cap):
            if row["IS_PRIMARYISSUER_FLAG"].upper() == "YES":
                issuers.setdefault(row["ACCESSIONNUMBER"], row)
        if not any(acc in issuers and acc in offerings for acc in submissions):
            raise TabularError(f"{self.dataset.name}: no filing joins its ISSUERS and OFFERING rows "
                               f"({len(submissions)} filings, {len(issuers)} primary issuers, "
                               f"{len(offerings)} offerings)")
        return submissions, offerings, issuers

    def discover(self, since: str | None = None) -> Iterator[Candidate]:
        floor = None
        if since is not None:
            floor = parse_filing_date(since)
            if floor is None:
                raise ValueError(f"since {since!r} is not a date")
        self._summary = summary = self._fresh_summary()
        submissions, offerings, issuers = self._load()
        summary["filings"] = len(submissions)
        groups: dict[str, dict] = {}
        for acc, sub in submissions.items():
            if sub[_TEST_COLUMN].upper() == "TEST":
                summary["test_filings"] += 1
                continue
            filed = parse_filing_date(sub["FILING_DATE"])
            kind = _KINDS.get(sub["SUBMISSIONTYPE"].upper())
            if filed is None or kind is None:
                summary["unreadable"] += 1
                continue
            if floor is not None and filed < floor:
                continue
            issuer, offering = issuers.get(acc), offerings.get(acc)
            if issuer is None or offering is None:
                summary["unreadable"] += 1
                continue
            if offering["INDUSTRYGROUPTYPE"].lower() == _FUND or offering[_POOLED_COLUMN].lower() == "true":
                summary["funds_skipped"] += 1
                continue
            cik, name = issuer["CIK"], issuer["ENTITYNAME"]
            if not cik.isdigit() or len(cik) > 10 or not name:
                summary["unreadable"] += 1
                continue
            group = groups.setdefault(cik.zfill(10), {"filings": [], "latest": None})
            group["filings"].append((filed, acc, kind, _amount(offering["TOTALAMOUNTSOLD"])))
            if group["latest"] is None or (filed, acc) > group["latest"][:2]:
                group["latest"] = (filed, acc, name, issuer["CITY"], issuer["STATEORCOUNTRY"].upper())
        for cik, group in groups.items():
            summary["issuers"] += 1
            yield self._candidate(cik, group)

    def _candidate(self, cik: str, group: dict) -> Candidate:
        latest_date, latest_acc, name, city, state = group["latest"]
        evidence = [EvidenceDraft(
            "funding",
            json.dumps({"amount_usd": amount, "filed_on": filed, "kind": kind}, sort_keys=True,
                       separators=(",", ":")),
            "confirmed", "formd", acc, observed_on=filed)
            for filed, acc, kind, amount in sorted(group["filings"], key=lambda f: (f[0], f[1]))]
        us_state = state if state in US_STATES else None
        if us_state:
            # A guess: the filing address is often a lawyer's or an agent's, so it never counts as the company's hq
            # (identity ignores guess hq for a company's state); `state` below is what matching uses.
            hq = f"{city.title()}, {us_state}" if city else us_state
            evidence.append(EvidenceDraft("hq", hq, "guess", "formd", latest_acc, observed_on=latest_date))
        keys = {"formd_cik": cik}
        if self.name_lookup(name_key(name)):
            fein = self._fein(cik)
            if fein:
                keys["fein"] = fein
        return Candidate(name=name, domain=None, keys=keys, evidence=tuple(evidence), source_ref=f"formd:{cik}",
                         state=us_state)

    # --- SEC lookup ----------------------------------------------------------------------------------------

    def _fein(self, cik: str) -> str | None:
        """The issuer's `ein` as a FEIN, or None (no ein, an invalid one, or a failed lookup, which is counted)."""
        url = _SUBMISSIONS.format(cik)
        self._summary["fetches"] += 1
        try:
            response = self.client.get(url, sec_headers(url))
            if response.status != 200:
                raise FetchError("network", f"{url}: HTTP {response.status}")
            doc = response.json()
            if not isinstance(doc, Mapping):
                raise ValueError("not a JSON object")
        except (FetchError, ValueError):
            self._summary["fetch_errors"] += 1
            return None
        ein = doc.get("ein")
        return normalize_fein(ein if isinstance(ein, str) else None)
