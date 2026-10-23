"""
Crawl the boards that refresh/crawl_boards.py does not cover, with the same entry-level filters.

Board list: refresh/work/crawl/slugs_more.json, entries [ats, token, name|null]. It holds
  - Ashby / Greenhouse / Lever boards that are not in slugs.json or slugs_live.json, and
  - boards on ATSes crawl_boards.py does not read: workable, rippling, gem, recruitee, bamboohr, breezy.
SmartRecruiters and Dover are no longer read (#46): api.smartrecruiters.com's robots.txt allows only LinkedInBot,
and app.dover.com's disallows /api/. Their boards in the list are left out, and naming them in --only or --skip
prints one line and the run carries on.
Tokens came from the Common Crawl URL index (two most recent crawls) and public slug lists on GitHub
(stapply-ai/ats-scrapers, Feashliaa/job-board-aggregator); every token was checked to answer before it was kept.

The keep rule is crawl_boards.py's own: its crawl() runs unchanged with board() swapped for a dispatcher that
adds the new ATSes, so title / location / seniority / citizenship / 45-day filters stay identical.
For ATSes whose list call carries no date or description (rippling, bamboohr, breezy),
the detail call is made only for titles that already pass the title and location filters.
Where the ATS has a structured country, non-US rows are dropped before the shared filters see them.
Workable is read through its cross-company search (jobs.workable.com, US, last DAYS days) instead of the
per-account boards, because apply.workable.com answers 429 after a short burst; --workable-boards adds those back.
On top of crawl_boards' rules: non-US rows that only passed on the word "remote" are dropped, a few non-software
"developer" titles are dropped, and so are boards with 100+ open postings, government, universities and hospitals.

Writes refresh/work/research/leads_ats_more.json, sorted best fit first.
Run from the project root:  python3 refresh/crawl_more.py [--days 45] [--only ats1,ats2] [--skip ats1,ats2] [--workable-boards]
A run takes about 16 minutes: the October runs with --skip dover took about 17, and SmartRecruiters was about 6% of
their boards. --skip dover (refresh.py still passes it) now only prints that Dover is no longer read.
"""
import json, re, sys, os, time, datetime, collections, urllib.request, urllib.error, concurrent.futures as cf
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crawl_boards as cb
from crawl_boards import SOFT, BAD, OFFLANE, US, TITLE_ENTRY, SINCE, get, strip, day, region
from discover_extra import in_lane
from discover_radar import BIG, NONUS

SLUGS = "refresh/work/crawl/slugs_more.json"
OUT = "refresh/work/research/leads_ats_more.json"
UA = dict(cb.UA)
ENTRY_LEVEL = re.compile(r"entry|junior|graduate|associate", re.I)
CAREERS = {"ashby": "https://jobs.ashbyhq.com/{}", "greenhouse": "https://job-boards.greenhouse.io/{}", "lever": "https://jobs.lever.co/{}",
           "workable": "https://apply.workable.com/{}",
           "rippling": "https://ats.rippling.com/{}/jobs", "gem": "https://jobs.gem.com/{}", "recruitee": "https://{}.recruitee.com",
           "bamboohr": "https://{}.bamboohr.com/careers", "breezy": "https://{}.breezy.hr"}
# ATSes no longer read, and why (#46, 2026-10-23). Their boards are left out before anything is sent.
DROPPED = {"smartrecruiters": "api.smartrecruiters.com/robots.txt allows only LinkedInBot",
           "dover": "app.dover.com/robots.txt disallows /api/"}

def getj(u, t=25):
    st, d = get(u, t)
    if st != 200: return None
    try: return json.loads(d)
    except Exception: return None

def post_j(u, body, t=25):
    try:
        req = urllib.request.Request(u, data=json.dumps(body).encode(), headers={**UA, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=t) as f: return json.loads(f.read().decode("utf-8", "replace"))
    except Exception: return None

def maybe(title, loc):
    """Cheap pre-check before paying for a detail call: the title and location filters crawl() applies anyway."""
    return bool(in_lane(title, loc) and US.search(loc or "") and SOFT.search(title) and not BAD.search(title) and not OFFLANE.search(title))

def us_country(c):
    return (c or "").strip().lower() in ("", "us", "usa", "united states", "united states of america")
def is_us(c):
    return (c or "").strip().lower() in ("us", "usa", "united states", "united states of america")

def level_hint(v):
    return "Experience level: entry level. " if v and ENTRY_LEVEL.search(str(v)) else ""

import threading
_locks, _last = collections.defaultdict(threading.Lock), collections.defaultdict(float)
def polite_get(u, key, gap, t=25, tries=5):
    """For hosts that answer 429 after a burst (Workable): space calls across all threads, retry with backoff."""
    for i in range(tries):
        with _locks[key]:
            wait = _last[key] + gap - time.time()
            if wait > 0: time.sleep(wait)
            _last[key] = time.time()
        st, d = get(u, t)
        if st == 429 or st == 0: time.sleep(4 * (i + 1)); continue
        if st != 200: return None
        try: return json.loads(d)
        except Exception: return None
    return None
def workable_get(u): return polite_get(u, "workable", 0.4)
def recruitee_get(u): return polite_get(u, "recruitee", 0, t=90, tries=2)   # offers come with every translation: MBs per board

def workable(tok):
    js = workable_get(f"https://apply.workable.com/api/v1/widget/accounts/{tok}?details=true")
    if not isinstance(js, dict) or "jobs" not in js: return None
    rows = []
    for j in js["jobs"]:
        locs = j.get("locations") or [{"country": j.get("country"), "city": j.get("city"), "region": j.get("state")}]
        if not any(us_country(l.get("countryCode") or l.get("country")) for l in locs): continue
        loc = "; ".join(", ".join(x for x in (l.get("city"), l.get("region"), l.get("country")) if x) for l in locs)
        if j.get("telecommuting"): loc += " (Remote)"
        rows.append((j.get("title", ""), loc, j.get("url") or j.get("shortlink", ""), day(j.get("published_on") or j.get("created_at")),
                     level_hint(j.get("experience")) + strip(j.get("description"))))
    return rows, js.get("name")

def rippling(tok):
    js = getj(f"https://api.rippling.com/platform/api/ats/v1/board/{tok}/jobs")
    if not isinstance(js, list): return None
    SIZE[("rippling", tok)] = len(js)
    rows, org = [], None
    for j in js:
        title, loc = j.get("name", ""), (j.get("workLocation") or {}).get("label", "")
        if not maybe(title, loc): continue
        d = getj(f"https://api.rippling.com/platform/api/ats/v1/board/{tok}/jobs/{j.get('uuid')}") or {}
        org = org or d.get("companyName")
        desc = d.get("description") or {}
        body = strip(" ".join(v for v in desc.values() if isinstance(v, str)) if isinstance(desc, dict) else str(desc))
        rows.append((title, "; ".join(d.get("workLocations") or [loc]), j.get("url", ""), day(d.get("createdOn")), body))
    return rows, org

def gem(tok):
    js = getj(f"https://api.gem.com/job_board/v0/{tok}/job_posts/")
    if not isinstance(js, list): return None
    rows = []
    for j in js:
        locs = [(j.get("location") or {}).get("name") or ""] + [((o.get("location") or {}).get("name") or o.get("name") or "") for o in j.get("offices") or []]
        loc = "; ".join(dict.fromkeys(x for x in locs if x)) + (" (Remote)" if j.get("location_type") == "remote" else "")
        rows.append((j.get("title", ""), loc, j.get("absolute_url", ""), day(j.get("first_published_at") or j.get("created_at")),
                     j.get("content_plain") or strip(j.get("content"))))
    return rows, None

def recruitee(tok):
    js = recruitee_get(f"https://{tok}.recruitee.com/api/offers/")
    if not isinstance(js, dict) or "offers" not in js: return None
    SIZE[("recruitee", tok)] = len(js["offers"])
    rows, org = [], None
    for j in js["offers"]:
        org = org or j.get("company_name")
        if not us_country(j.get("country_code")): continue
        loc = (j.get("location") or "") + (" United States" if j.get("country_code") == "US" else "") + (" (Remote)" if j.get("remote") else "")
        rows.append((j.get("title", ""), loc, j.get("careers_url", ""), day(j.get("published_at") or j.get("created_at")),
                     level_hint(j.get("experience_code")) + strip((j.get("description") or "") + " " + (j.get("requirements") or ""))))
    return rows, org

def bamboohr(tok):
    js = getj(f"https://{tok}.bamboohr.com/careers/list")
    if not isinstance(js, dict) or "result" not in js: return None
    SIZE[("bamboohr", tok)] = len(js["result"])
    rows = []
    for j in js["result"]:
        l = j.get("location") or {}; a = j.get("atsLocation") or {}
        if not us_country(a.get("country") or l.get("country")) and not re.search(r"united states|\bUSA?\b", str(a.get("country") or l.get("country")), re.I): continue
        ctry = a.get("country") or l.get("country")
        loc = ", ".join(x for x in (l.get("city") or a.get("city"), l.get("state") or a.get("state")) if x)
        loc += (" United States" if is_us(ctry) or re.search(r"united states", str(ctry), re.I) else "") + (" (Remote)" if j.get("isRemote") else "")
        title = j.get("jobOpeningName", "")
        if not maybe(title, loc): continue
        d = (getj(f"https://{tok}.bamboohr.com/careers/{j['id']}/detail") or {}).get("result", {}).get("jobOpening", {})
        rows.append((title, loc, f"https://{tok}.bamboohr.com/careers/{j['id']}", day(d.get("datePosted")), strip(d.get("description"))))
    return rows, None

def breezy(tok):
    st, d = get(f"https://{tok}.breezy.hr/json")
    if st != 200: return None
    try: js = json.loads(d)
    except Exception: return None
    if not isinstance(js, list): return None
    SIZE[("breezy", tok)] = len(js)
    rows, org = [], None
    for j in js:
        org = org or (j.get("company") or {}).get("name")
        ls = j.get("locations") or [j.get("location") or {}]
        if not any(us_country(((l.get("country") or {}).get("id")) if isinstance(l.get("country"), dict) else l.get("country")) for l in ls): continue
        loc = "; ".join(l.get("name") or ", ".join(x.get("name", "") for x in (l.get("city"), l.get("state")) if isinstance(x, dict)) for l in ls)
        cc = [((l.get("country") or {}).get("id")) if isinstance(l.get("country"), dict) else l.get("country") for l in ls]
        loc += (" United States" if any(is_us(c) for c in cc) else "") + (" (Remote)" if any(l.get("is_remote") for l in ls) else "")
        title, posted = j.get("name", ""), day(j.get("published_date"))
        if not posted or posted < SINCE or not maybe(title, loc): continue
        st2, page = get(j.get("url", ""))
        rows.append((title, loc, j.get("url", ""), posted, strip(page) if st2 == 200 else ""))
    return rows, org

WS_QUERIES = ["software engineer", "software developer", "junior engineer", "new grad", "entry level engineer", "full stack",
              "backend engineer", "frontend engineer", "machine learning engineer", "ai engineer", "forward deployed engineer",
              "platform engineer", "data engineer", "engineer i", "associate engineer", "founding engineer", "product engineer"]
_WS = {}
def workable_search():
    """Workable's own cross-company job search (jobs.workable.com) for US roles from the last DAYS days.
    One sweep replaces thousands of per-account calls on apply.workable.com, which rate-limits hard."""
    seen = set()
    for q in WS_QUERIES:
        tok = None
        for page in range(40):
            u = (f"https://jobs.workable.com/api/v1/jobs?query={urllib.request.quote(q)}&location=United%20States&day_range={cb.DAYS}"
                 + (f"&pageToken={urllib.request.quote(tok)}" if tok else ""))
            js = getj(u)
            if not isinstance(js, dict): break
            for j in js.get("jobs") or []:
                if j["id"] in seen: continue
                seen.add(j["id"])
                c = j.get("company") or {}
                l = j.get("location") or {}
                if not is_us(l.get("countryName")) and not any("United States" in x for x in j.get("locations") or []): continue
                loc = "; ".join(j.get("locations") or []) + (" (Remote)" if j.get("workplace") == "remote" else "")
                body = strip((j.get("description") or "") + " " + (j.get("requirementsSection") or ""))
                _WS.setdefault(c.get("id") or c.get("title"), ([], c.get("title"), c.get("website")))[0].append(
                    (j.get("title", ""), loc, j.get("url", ""), day(j.get("created")), body))
            tok = js.get("nextPageToken")
            if not tok or not js.get("jobs"): break
    return len(seen)

NEW = {"workable_search": lambda tok: (_WS[tok][0], _WS[tok][1]) if tok in _WS else None, "workable": workable, "rippling": rippling, "gem": gem, "recruitee": recruitee,
       "bamboohr": bamboohr, "breezy": breezy}
_orig_board = cb.board
def board_any(ats, tok):
    res = NEW[ats](tok) if ats in NEW else _orig_board(ats, tok)
    if res and (ats, tok) not in SIZE and ats != "workable_search": SIZE[(ats, tok)] = len(res[0])
    return res
cb.board = board_any          # crawl_boards.crawl() looks board() up at call time, so its filters run unchanged

# Extra checks on top of crawl_boards' rules, for things these broader boards surface more often.
NONUS_X = re.compile(NONUS.pattern + r"|europe|belgi|colombia|costa rica|philippines|vietnam|pakistan|nigeria|kenya|egypt|turkey|"
                     r"romania|ukraine|serbia|ital|greece|czech|hungary|denmark|norway|finland|austria|chile|peru|uruguay|"
                     r"new zealand|south africa|dubai|\buae\b|m[eé]xico|ciudad de|bogot[aá]|medell[ií]n|s[aã]o paulo|bulgaria|croatia|lithuania|latvia|cyprus|malta|morocco|indonesia|malaysia|thailand", re.I)
# crawl_boards' US places minus "remote": state codes case-sensitive, names and cities case-insensitive
US_PLACE = re.compile(cb._STATES.pattern + r"|(?i:" + cb._US_WORDS.pattern.replace("remote|", "") + ")")
OFF_TITLE = re.compile(r"business develop|project develop|product develop(er|ment)\b(?!.*software)|rpa\b|salesforce developer|"
                       r"building engineer|curriculum developer|channel developer|contractor|netsuite|oracle analytics", re.I)
NOT_STARTUP = re.compile(r"\b(city|county|state|commonwealth|department|district|town|village) of\b|\buniversity\b|\bcollege\b|school district|"
                         r"public schools|hospital|health system|medical center|^(pinterest|roku|roblox|qualtrics|jamf|intuitive|abbvie|"
                         r"bertelsmann|hunter douglas|bitdeer|eversana|nationsbenefits|ixl|seatgeek|justworks|bosch|siemens|accenture|"
                         r"deloitte|capgemini|cognizant|infosys|wipro|tata|hcl|kpmg|ey\b|pwc|leidos|saic|caci|booz|lockheed|raytheon|"
                         r"northrop|general dynamics|bae systems|boeing|honeywell|cisco|vmware|broadcom|qualcomm|amd|hp\b|hewlett)", re.I)
LARGE_BOARD = 100             # a board with this many open postings belongs to a company well past 500 people
SIZE = {}

def crawl(item):
    """crawl_boards.crawl, then drop non-US rows that slipped through on the word "remote", and non-software titles."""
    item, org, keep = cb.crawl(item)
    out = []
    for r in keep:
        place = r["title"] + " " + r["location"]
        if NONUS_X.search(place) and not (US_PLACE.search(r["location"]) or re.search(r"\b(US|USA|United States)\b", r["title"])): continue
        if OFF_TITLE.search(r["title"]): continue
        out.append(r)
    return item, org, out

FIT_BASIS = {"title": 3, "posting text": 2, "open level": 1}
GOOD_TITLE = re.compile(r"software|full.?stack|back.?end|front.?end|product engineer|platform|\bai\b|\bml\b|machine learning|forward.?deploy|founding", re.I)
STRONG = re.compile(r"new.?grad|early.?career|university|college grad|entry.?level|\bjunior\b|engineer\s*[-,(]?\s*(i|1)\b", re.I)
def fit(lead):
    best = 0
    for r in lead["roles"]:
        s = FIT_BASIS.get(r["basis"], 0) * 10 + (8 if STRONG.search(r["title"]) else 0) + (4 if GOOD_TITLE.search(r["title"]) else 0)
        s += 3 if r["region"] != "Other US" else 0
        s += max(0, 45 - (datetime.date.today() - datetime.date.fromisoformat(r["posted"])).days) / 15
        best = max(best, s)
    n = lead.get("openings") or 0                       # big boards mean big companies: push them down
    return round(best + min(len(lead["roles"]), 4) * 0.5 - (6 if n > 60 else 3 if n > 30 else 0), 2)

def main():
    t0 = time.time()
    slugs = json.load(open(SLUGS))
    only = set(sys.argv[sys.argv.index("--only") + 1].split(",")) if "--only" in sys.argv else set()
    skip = set(sys.argv[sys.argv.index("--skip") + 1].split(",")) if "--skip" in sys.argv else set()
    for ats in sorted((only | skip) & set(DROPPED)):  # e.g. refresh.py's --skip dover
        print(f"{ats} is no longer read ({DROPPED[ats]}); carrying on without it", flush=True)
    only -= set(DROPPED)
    if only: slugs = [s for s in slugs if s[0] in only]
    elif "--only" in sys.argv: slugs = []            # --only named nothing that is still read
    dropped = collections.Counter(s[0] for s in slugs if s[0] in DROPPED)
    if dropped:
        print(f"left out {sum(dropped.values())} boards on ATSes no longer read: {dict(sorted(dropped.items()))}", flush=True)
    slugs = [s for s in slugs if s[0] not in DROPPED]
    slugs = [s for s in slugs if not (len(s) > 2 and s[2] and BIG.search(s[2]))]
    wnames = {}
    if skip: slugs = [s for s in slugs if s[0] not in skip]
    if "--workable-boards" not in sys.argv:          # per-account Workable crawl is slow (429s); the search sweep covers it
        slugs = [s for s in slugs if s[0] != "workable"]
    if ("--only" not in sys.argv and not only) or "workable" in only:
        n = workable_search()
        wnames = {(s[2] or "").lower(): s[1] for s in json.load(open(SLUGS)) if s[0] == "workable" and s[2]}
        slugs += [["workable_search", cid, v[1]] for cid, v in _WS.items()]
        print(f"workable search: {n} US postings from {len(_WS)} companies ({time.time() - t0:.0f}s)", flush=True)
    print(f"crawling {len(slugs)} boards, roles first published since {SINCE}", flush=True)
    found, dead, per = [], 0, collections.Counter()
    slow = [s for s in slugs if s[0] == "workable"]    # a rate-limited host: own small pool, runs alongside
    fast = [s for s in slugs if s[0] != "workable"]
    def results():
        with cf.ThreadPoolExecutor(max_workers=16) as ex, cf.ThreadPoolExecutor(max_workers=2) as ex2:
            futs = [ex2.submit(crawl, s) for s in slow]
            yield from ex.map(crawl, fast)
            for fu in futs: yield fu.result()
    for i, (item, org, keep) in enumerate(results()):
        if org is None: dead += 1; continue
        per[item[0]] += 1
        name = item[2] if (len(item) > 2 and item[2] and org == item[1]) else org
        n_open = SIZE.get((item[0], item[1]), 0)
        if keep and not BIG.search(name or "") and not NOT_STARTUP.search(name or "") and n_open < LARGE_BOARD:
            ats, tok, url = item[0], item[1], None
            if ats == "workable_search":
                ats, url = "workable", _WS[tok][2]
                tok = wnames.get((name or "").lower(), tok)
                if tok in wnames.values(): url = CAREERS["workable"].format(tok)
            found.append({"company": name, "ats": ats, "token": tok, "roles": keep, "careersUrl": url or CAREERS[ats].format(tok), "openings": n_open})
        if i % 500 == 0: print(f"  {i}/{len(slugs)}  boards with a match: {len(found)}  ({time.time() - t0:.0f}s)", flush=True)
    merged = {}                                       # one lead per company
    for f in found:
        k = (f["ats"], (f["company"] or f["token"]).lower())
        if k in merged:
            urls = {r["url"] for r in merged[k]["roles"]}
            merged[k]["roles"] += [r for r in f["roles"] if r["url"] not in urls and not r["url"].endswith(tuple(u.rsplit("/", 1)[-1] for u in urls))]
        else: merged[k] = f
    found = list(merged.values())
    out = [{"company": f["company"], "ats": f["ats"], "token": f["token"], "careersUrl": f["careersUrl"],
            "roles": [{k: r[k] for k in ("title", "url", "posted", "location")} for r in sorted(f["roles"], key=lambda r: r["posted"], reverse=True)],
            "stage": "unknown", "headcount": "unknown", "visa": "unstated",
            "source": f'{f["ats"]} board {f["token"]}', "why": "; ".join(sorted({r["basis"] for r in f["roles"]})),
            "regions": sorted({r["region"] for r in f["roles"]}), "openings": f["openings"] or None, "fit": fit(f)} for f in found]
    out.sort(key=lambda o: -o["fit"])
    json.dump(out, open(OUT, "w"), indent=1)
    reg = collections.Counter(r for o in out for r in o["regions"])
    print(f"boards answering: {sum(per.values())} {dict(per)}; dead: {dead}")
    print(f"companies with a fresh entry-level US role: {len(out)} {dict(collections.Counter(o['ats'] for o in out))}; by region {dict(reg)}")
    print(f"took {time.time() - t0:.0f}s")

if __name__ == "__main__":
    main()
