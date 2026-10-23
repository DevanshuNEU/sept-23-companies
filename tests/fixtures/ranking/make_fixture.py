"""Builds `employers.json` and `postings.json` for the ranking tests (#6, Task 13). Run it by hand, once:

    uv run python tests/fixtures/ranking/make_fixture.py --universe PATH/universe.json --jobs PATH/jobs.json

It reads a copy of the interim list's data (`universe.json`: employers with LCA counts; `jobs.json`: board postings)
and only writes the two JSON files next to itself. The committed output is public LCA and job-board data: employer
names and counts from the Department of Labor disclosure files, posting titles, places and texts from public job
boards, bodies trimmed to about 1,500 characters and every dash turned into a hyphen. Nothing here is personal data.

Each employer in the fixture plays one role (A to U, see ROLES). Rows are picked by named criteria, with the real
rules (`outreach.rules.decide`) judging every posting, so a role that needs "a live new-grad posting that says it
sponsors" gets a posting the rules read exactly that way, also after the trim. Anything the interim data does not
hold is written here by hand, in the same shape: the board read states, the evidence rows (E-Verify, size, stage,
cap-exempt, staffing risk), the 2016 dates, the twins (the same profile and the same postings under another real
employer's name), the lifted no-go and the merge. Those values are fixture values, not facts about the companies.

Dates are fixed to the fixture day 2026-10-21: a live posting is posted a few days before it, so it is not stale.
LCA rows: the interim list holds one total per employer, so FY2026 takes `fy26_jr` and FY2025 the rest of the junior
filings (the split is the fixture's, not DOL's).

File shapes: see the docstring of `tests/ranking_seed.py`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent.parent))

from outreach.rules import PostingInput, decide, passes  # noqa: E402

MAX_BODY = 1500
LIVE_DAYS = {"fresh": "2026-10-16", "recent": "2026-10-12", "older": "2026-09-14"}
DASHES = {chr(0x2014): " - ", chr(0x2013): "-", chr(0x2012): "-", chr(0x2015): " - ", chr(0x2212): "-"}
ENG_TITLE = re.compile(r"engineer|developer|software|swe|programmer|scientist|intern", re.I)

# What a role needs from a picked posting, as a test on the real decision.
KEYS = ("lane", "level_tier", "level_ok", "us", "sponsorship", "citizenship", "eligibility")


def scrub(text: str) -> str:
    for dash, plain in DASHES.items():
        text = text.replace(dash, plain)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"[ \t]+\n", "\n", re.sub(r" {2,}", " ", text)).strip()


def decision_key(d) -> tuple:
    return tuple(getattr(d, k) for k in KEYS) + (passes(d),)


def decide_job(j: dict, body: str | None = None):
    return decide(PostingInput(j["title"], j["description"] if body is None else body, j["location"], j["country"] or None))


def trim(j: dict, d, limit: int = MAX_BODY) -> str | None:
    """The body cut to `limit` characters that the rules still read the same, or None when no cut does."""
    body = scrub(j["description"])
    want = decision_key(decide_job(j, body))
    if len(body) <= limit:
        return body
    quotes = [q for q in (d.sponsorship_quote, d.citizenship_quote, d.years_quote, d.eligibility_quote) if q]
    for head_len in (1100, 800, 500, 300):
        head = body[:head_len]
        cut = max(head.rfind("\n"), head.rfind(". "))
        head = head[:cut + 1] if cut > head_len // 2 else head
        tail = ""
        for q in quotes:
            at = body.find(scrub(q)[:60])
            if at >= len(head):
                start = max(body.rfind("\n", 0, at), body.rfind(". ", 0, at))
                end = body.find("\n", at + len(q))
                tail += "\n\n" + body[start + 1:end if end != -1 else None].strip()
        cand = (head.rstrip() + tail)[:limit + 400]
        if len(cand) <= limit and decision_key(decide_job(j, cand)) == want:
            return cand
    return None


class Data:
    def __init__(self, universe: list[dict], jobs: list[dict]):
        self.universe = universe
        self.by_company: dict[int, list[dict]] = {}
        for j in jobs:
            self.by_company.setdefault(j["company_idx"], []).append(j)
        self.used: set[int] = set()
        self._decided: dict[int, object] = {}

    def decided(self, j: dict):
        key = id(j)
        if key not in self._decided:
            self._decided[key] = decide_job(j)
        return self._decided[key]

    def postings(self, idx: int, test) -> list[dict]:
        """The company's postings the real decision satisfies `test` for, in file order (an engineering-looking
        title first, so the rules run on few bodies)."""
        out = []
        for j in self.by_company.get(idx, []):
            if not j["description"] or not ENG_TITLE.search(j["title"]) and not test.wants_non_eng:
                continue
            if test(self.decided(j), j):
                out.append(j)
        return out


class Test:
    def __init__(self, fn, wants_non_eng=False):
        self.fn, self.wants_non_eng = fn, wants_non_eng

    def __call__(self, d, j):
        return self.fn(d, j)


def eng(**want):
    def fn(d, j):
        return d.lane == "engineering" and all(getattr(d, k) == v for k, v in want.items())
    return Test(fn)


NEWGRAD = ("new_grad", "early_career", "swe1")
T_YES = Test(lambda d, j: d.lane == "engineering" and d.level_ok and d.us == "yes" and d.sponsorship == "yes"
             and passes(d))
T_STRICT_NEWGRAD = Test(lambda d, j: d.lane == "engineering" and d.level_ok and d.level_tier == "new_grad"
                        and d.us == "yes" and d.sponsorship == "silent" and passes(d) and j["country"] in ("US", ""))
T_NEWGRAD = Test(lambda d, j: d.lane == "engineering" and d.level_ok and d.level_tier in NEWGRAD and d.us == "yes"
                 and d.sponsorship == "silent" and passes(d) and j["country"] in ("US", ""))
T_LEVEL2 = Test(lambda d, j: d.lane == "engineering" and d.level_ok and d.level_tier == "swe2" and d.us == "yes"
                and d.sponsorship == "silent" and passes(d))
T_REFUSE = Test(lambda d, j: d.lane == "engineering" and d.us != "no" and d.sponsorship == "no" and not d.citizenship
                and d.eligibility == "ok" and d.level_ok)
T_CLEARANCE = Test(lambda d, j: d.lane == "engineering" and d.citizenship == 1 and d.us != "no" and d.sponsorship
                   != "no")
T_QUIET = Test(lambda d, j: d.sponsorship in ("silent", "unclear") and not d.citizenship and d.us != "no", True)
T_ANY = Test(lambda d, j: d.lane == "engineering" and d.us == "yes" and passes(d) and d.level_ok)


for _name, _test in list(globals().items()):
    if isinstance(_test, Test):
        _test.name = _name


def fy_rows(u: dict) -> list[dict]:
    jr, fy26 = int(u["jr_software_lcas"]), int(u["fy26_jr"])
    fy25 = max(jr - fy26, 0)
    level1 = min(int(u["level1_lcas"]), fy25)
    wage = int(float(u["median_jr_wage"])) if u.get("median_jr_wage") else None
    rows = []
    for fy, n, l1, new in ((2025, fy25, level1, min(int(u["newemp_jr"]), fy25)), (2026, fy26, 0, 0)):
        row = {"fy": fy, "software": n, "jr": n, "level1": l1, "newemp_jr": new}
        if wage:
            row["median_jr_wage"] = wage
        rows.append(row)
    return rows


def hq(u: dict) -> str:
    return f"{u['city'].title()}, {u['state']}" if u.get("city") and len(u.get("state", "")) == 2 else ""


def clean_name(name: str) -> str:
    return scrub(name).replace(" - ", " ")


def posting(job: dict, d, employer: str, board: int | None, *, posted: str | None = None, append: str = "",
            **over) -> dict:
    """A posting row. `append` is a sentence written by hand onto the end of the body (the interim data has no
    new-grad posting that says it sponsors)."""
    body = trim(job, d, MAX_BODY - len(append) - 2)
    if body is None:
        raise ValueError(f"cannot trim {job['title']!r} of {job['slug']}")
    if append:
        body += "\n\n" + append
    row = {"employer": employer, "board": board, "title": scrub(job["title"]), "location": scrub(job["location"]),
           "country": job["country"] or None, "url": job["url"], "posted_on": posted, "pay": "", "body": body}
    row.update(over)
    return row


def employer(key: str, u: dict, **extra) -> dict:
    row = {"key": key, "name": clean_name(u["name"]), "role": ROLES[key], "status": "candidate"}
    if hq(u):
        row["hq"] = hq(u)
    if u["jr_software_lcas"] or u["fy26_jr"]:
        row["lca"] = fy_rows(u)
    row.update(extra)
    return row


ROLES = {
    "A": "small employer, FY25 junior filings, E-Verify, a live new-grad posting that says it sponsors",
    "B": "clean small employer, a level II posting, no other signal",
    "C": "staffing risk high, the twin of B",
    "D": "large later-stage company (series D, a guessed 5,000 people) with a new-grad posting",
    "E": "its only posting says we do not sponsor visas, a final decision",
    "F": "the same with the decision still provisional",
    "G": "a lifted no-go and a refusing posting",
    "H": "a clearance-required posting",
    "I": "a cap-exempt university lab",
    "J": "LCA history only, no board",
    "K": "its only posting is on a board with an open board match doubtful item",
    "L": "a merged duplicate of B",
    "M": "posted Boston pay of $130,000 - $150,000",
    "N": "a final refusal and a second open posting that says it sponsors",
    "O": "clean twin of P, its only posting is a 2016 new-grad role its board still lists",
    "P": "the profile of O with one live new-grad posting 3 days old and one 2016 posting",
    "Q": "a new-grad posting last seen on 2026-10-18, its board then had a first empty read",
    "R": "a live new-grad posting on a board whose last read errored",
    "S": "its only posting is a 2016 refusal with a final decision",
    "T": "twin of U, E-Verify confirmed",
    "U": "twin of T, E-Verify only a guess",
}
ENROLLED = {"fact": "everify", "value": "enrolled", "confidence": "confirmed", "observed_on": "2026-09-20"}
SMALL = {"fact": "size", "value": "20 to 60 people", "num_low": 20, "num_high": 60, "confidence": "estimate"}
LOW_RISK = {"fact": "staffing_risk", "value": "low", "confidence": "confirmed"}


def pick(data: Data, candidates, test, *, where=lambda u: True, key=lambda i, u: (i,), skip: int = 0):
    """The first (company index, posting) whose posting passes `test`, ordered by `key`; used companies are skipped."""
    found = []
    for idx in candidates:
        u = data.universe[idx]
        if idx in data.used or not where(u):
            continue
        for j in data.postings(idx, test):
            try:
                if trim(j, data.decided(j)) is not None:
                    found.append((key(idx, u), idx, j))
                    break
            except ValueError:
                continue
    found.sort(key=lambda t: t[0])
    if len(found) <= skip:
        raise SystemExit(f"no company left for {test.name} (skip {skip}, found {len(found)})")
    _k, idx, j = found[skip]
    data.used.add(idx)
    return idx, j


def build(universe: list[dict], jobs: list[dict]) -> tuple[list[dict], list[dict]]:
    data = Data(universe, jobs)
    small = lambda u: u["total_lcas"] <= 60 and u["staffing_risk"] == "low" and u["group"] == "product"
    with_lca = lambda u: small(u) and int(u["jr_software_lcas"]) - int(u["fy26_jr"]) >= 1 and u["city"]
    everyone = sorted(range(len(universe)), key=lambda i: universe[i]["name"].lower())
    with_jobs = [i for i in everyone if i in data.by_company]
    employers: list[dict] = []
    postings: list[dict] = []

    def add(emp: dict, *rows: dict) -> None:
        employers.append(emp)
        postings.extend(rows)

    def filler(idx: int, key: str, board: int, non_eng: int = 1, senior: int = 0, intern: int = 0) -> list[dict]:
        """Real postings of the same employer that earn no credit a test reads: sales or off-lane roles (never
        visible), senior engineering roles (no level role), intern roles (a lane the employer already has). None
        says anything about sponsorship or citizenship."""
        kinds = {"non_eng": (non_eng, lambda d: d.lane in ("not_engineering", "off_lane")),
                 "senior": (senior, lambda d: d.lane == "engineering" and not d.level_ok),
                 "intern": (intern, lambda d: d.lane == "intern" and d.eligibility == "ok")}
        rows = []
        for kind, (want, test) in kinds.items():
            got = 0
            for j in data.by_company.get(idx, []):
                if got == want:
                    break
                d = data.decided(j)
                if (test(d) and d.sponsorship in ("silent", "unclear") and not d.citizenship and d.us != "no"
                        and j["description"] and trim(j, d) is not None):
                    rows.append(posting(j, d, key, board, posted=LIVE_DAYS["older"]))
                    got += 1
        return rows

    # A: a real new-grad posting with a sentence about sponsoring written onto it, at an employer with FY25 junior
    # filings. N: a real posting of another employer that says it sponsors, with a real refusal added below.
    ia, ja = pick(data, with_jobs, T_STRICT_NEWGRAD, where=with_lca, key=lambda i, u: (-(int(u["jr_software_lcas"]) - int(u["fy26_jr"])), i))
    ua = universe[ia]
    add(employer("A", ua, facts=[ENROLLED, SMALL, LOW_RISK],
                 boards=[{"read": "ok"}], people=[{"name": "Casey Fixture", "role_kind": "engineer",
                                                  "email": "casey.fixture@example.com"}]),
        posting(ja, data.decided(ja), "A", 0, posted="2026-10-18",
                append="We will sponsor work visas for this role."), *filler(ia, "A", 0, 2, 1, 1))

    in_, jn = pick(data, with_jobs, T_YES, where=small)
    un = universe[in_]
    in_refuse, jrefuse = pick(data, with_jobs, T_REFUSE, where=lambda u: u["group"] == "product")
    add(employer("N", un, facts=[LOW_RISK], boards=[{"read": "ok"}]),
        posting(jrefuse, data.decided(jrefuse), "N", 0, posted="2026-10-14", provisional=0),
        posting(jn, data.decided(jn), "N", 0, posted="2026-10-17"), *filler(in_, "N", 0))

    # B and its twin C, and L the merged duplicate of B.
    ib, jb = pick(data, with_jobs, T_LEVEL2, where=with_lca)
    ub = universe[ib]
    b_fill = filler(ib, "B", 0, 1, 1)
    add(employer("B", ub, facts=[LOW_RISK], boards=[{"read": "ok"}]),
        posting(jb, data.decided(jb), "B", 0, posted="2026-10-15"), *b_fill)
    ic = sorted(i for i in everyone if i not in data.used and universe[i]["staffing_risk"] == "medium")[0]
    data.used.add(ic)
    add(employer("C", universe[ic], lca=fy_rows(ub), hq=hq(ub), facts=[{**LOW_RISK, "value": "high",
                                                                        "confidence": "estimate"}],
                 boards=[{"read": "ok"}]),
        posting(jb, data.decided(jb), "C", 0, posted="2026-10-15"),
        *[{**r, "employer": "C"} for r in b_fill])
    legal = clean_name(ub["legal_name"]) if ub.get("legal_name") else ""
    twin_name = legal if legal and legal.lower() != clean_name(ub["name"]).lower() else clean_name(ub["name"]) + " LLC"
    emp_l = employer("L", ub, merged_into="B")
    emp_l["name"] = twin_name
    emp_l.pop("lca", None)
    add(emp_l, posting(jb, data.decided(jb), "L", None, posted="2026-09-20", url=jb["url"] + "?dup=1"))

    # D: a large later-stage company with a new-grad posting.
    big = sorted((i for i in with_jobs if i not in data.used), key=lambda i: (-universe[i]["total_lcas"], i))
    idd, jd = pick(data, big, T_NEWGRAD, key=lambda i, u: (-u["total_lcas"], i))
    add(employer("D", universe[idd], facts=[
        {"fact": "stage", "value": "series_d", "confidence": "guess"},
        {"fact": "size", "value": "about 5,000 people", "num_low": 5000, "num_high": 5000, "confidence": "guess"},
        {"fact": "everify", "value": "not_enrolled", "confidence": "estimate", "observed_on": "2026-09-20"}],
        boards=[{"read": "ok"}]),
        posting(jd, data.decided(jd), "D", 0, posted="2026-10-12"), *filler(idd, "D", 0, 2, 1))

    # E, F, G: a refusing posting each. E is final, F provisional (the default), G final with a lifted no-go.
    for key, final, extra in (("E", True, {}), ("F", False, {}),
                              ("G", True, {"no_go": {"reason": "no_sponsorship", "lifted": True}})):
        i, j = pick(data, with_jobs, T_REFUSE, where=lambda u: u["group"] == "product")
        row = posting(j, data.decided(j), key, 0, posted="2026-10-13", **({"provisional": 0} if final else {}))
        add(employer(key, universe[i], facts=[LOW_RISK], boards=[{"read": "ok"}], **extra), row,
            *filler(i, key, 0))

    ih, jh = pick(data, with_jobs, T_CLEARANCE, where=lambda u: u["group"] == "product")
    add(employer("H", universe[ih], facts=[LOW_RISK], boards=[{"read": "ok"}]),
        posting(jh, data.decided(jh), "H", 0, posted="2026-10-10", provisional=0), *filler(ih, "H", 0))

    ii, ji = pick(data, with_jobs, T_ANY, where=lambda u: u["group"] == "lottery_exempt")
    add(employer("I", universe[ii], facts=[{"fact": "cap_exempt", "value": "confirmed", "confidence": "confirmed"}],
                 boards=[{"read": "ok"}]),
        posting(ji, data.decided(ji), "I", 0, posted="2026-10-11"), *filler(ii, "I", 0, 1, 1))

    nojobs = [i for i in everyone if i not in data.by_company and i not in data.used and small(universe[i])
              and 3 <= int(universe[i]["jr_software_lcas"]) - int(universe[i]["fy26_jr"]) <= 6 and universe[i]["city"]]
    ij = nojobs[0]
    data.used.add(ij)
    add(employer("J", universe[ij]))

    ik, jk = pick(data, with_jobs, T_NEWGRAD, where=small)
    add(employer("K", universe[ik], facts=[LOW_RISK],
                 boards=[{"read": "ok", "found_by": "stapply_name", "doubtful": "the organisation name differs"}]),
        posting(jk, data.decided(jk), "K", 0, posted="2026-10-14"), *filler(ik, "K", 0))

    im, jm = pick(data, with_jobs, T_NEWGRAD, where=small)
    add(employer("M", universe[im], facts=[LOW_RISK], boards=[{"read": "ok"}]),
        posting(jm, data.decided(jm), "M", 0, posted="2026-10-14", location="Boston, MA", country="US",
                pay="$130,000 - $150,000"), *filler(im, "M", 0, 1, 1))

    # O to U: states the interim data lacks (a 2016 date, an empty or failed read, a guessed fact), written by hand
    # onto real new-grad postings. O and P share a profile, T and U share a profile and their postings.
    profile = {"lca": [], "facts": [ENROLLED, LOW_RISK]}  # no LCA rows: with none, no junior filings give the company a lane
    io_, jo = pick(data, with_jobs, T_NEWGRAD, where=small)
    ip, jp = pick(data, with_jobs, T_NEWGRAD, where=small)
    uo = universe[io_]
    old_o = posting(jo, data.decided(jo), "O", 0, posted="2016-03-02")
    add(employer("O", uo, **profile, boards=[{"read": "ok"}]), old_o)
    up = universe[ip]
    add(employer("P", up, **profile, boards=[{"read": "ok"}]),
        posting(jp, data.decided(jp), "P", 0, posted="2026-10-18"),
        {**old_o, "employer": "P", "url": old_o["url"] + "?p=1"}, *filler(ip, "P", 0))

    iq, jq = pick(data, with_jobs, T_NEWGRAD, where=small)
    add(employer("Q", universe[iq], lca=[], facts=[LOW_RISK],
                 boards=[{"read": "empty", "read_at": "2026-10-21T08:00:00Z", "empty_streak": 1}]),
        posting(jq, data.decided(jq), "Q", 0, posted="2026-10-10", last_seen_at="2026-10-18T08:00:00Z"))

    ir, jr_ = pick(data, with_jobs, T_NEWGRAD, where=small)
    add(employer("R", universe[ir], facts=[LOW_RISK],
                 boards=[{"read": "error", "read_at": "2026-10-21T08:00:00Z", "error_streak": 1}]),
        posting(jr_, data.decided(jr_), "R", 0, posted="2026-10-14"))

    i_s, js = pick(data, with_jobs, T_REFUSE, where=lambda u: u["group"] == "product")
    add(employer("S", universe[i_s], facts=[LOW_RISK], boards=[{"read": "ok"}]),
        posting(js, data.decided(js), "S", 0, posted="2016-03-02", provisional=0), *filler(i_s, "S", 0))

    it, jt = pick(data, with_jobs, T_NEWGRAD, where=with_lca)
    iu = pick(data, with_jobs, T_NEWGRAD, where=with_lca)[0]
    ut = universe[it]
    t_post = posting(jt, data.decided(jt), "T", 0, posted="2026-10-14")
    t_fill = filler(it, "T", 0)
    add(employer("T", ut, lca=fy_rows(ut), hq=hq(ut), facts=[ENROLLED, LOW_RISK], boards=[{"read": "ok"}]), t_post,
        *t_fill)
    add(employer("U", universe[iu], lca=fy_rows(ut), hq=hq(ut),
                 facts=[{**ENROLLED, "confidence": "guess"}, LOW_RISK], boards=[{"read": "ok"}]),
        {**t_post, "employer": "U", "url": t_post["url"] + "?u=1"},
        *[{**r, "employer": "U", "url": r["url"] + "?u=1"} for r in t_fill])

    employers.sort(key=lambda e: e["key"])
    return employers, postings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--universe", type=Path, required=True)
    ap.add_argument("--jobs", type=Path, required=True)
    args = ap.parse_args(argv)
    universe = json.loads(args.universe.read_text(encoding="utf-8"))
    jobs = json.loads(args.jobs.read_text(encoding="utf-8"))
    employers, postings = build(universe, jobs)
    for name, rows in (("employers.json", employers), ("postings.json", postings)):
        text = json.dumps(rows, indent=1, ensure_ascii=False, sort_keys=True) + "\n"
        if any(c in text for c in (chr(0x2013), chr(0x2014))):
            raise SystemExit(f"{name} holds a dash")
        (HERE / name).write_text(text, encoding="utf-8")
    print(f"{len(employers)} employers, {len(postings)} postings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
