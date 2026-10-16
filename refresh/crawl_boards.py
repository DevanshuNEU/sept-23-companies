"""
Crawl every known Ashby / Greenhouse / Lever board for entry-level US engineering roles posted recently.

The board list (refresh/work/crawl/slugs.json, [ats, token, name?]) is built from: Simplify new-grad listings
(current and historical snapshots), the Job Radar's boards (read-only), joblist.today's company directory,
YC hiring companies probed by name, and every board already on the dashboard.

A role is kept when it is a US engineering role (not senior, intern or off-lane), first published in the last
45 days, and entry-shaped: the title says new grad / early career / junior / associate / Engineer I, or the
posting text says new grad, entry level, recent graduate, or asks for at most ~3 years.
Writes refresh/work/research/leads_ats_crawl.json, which refresh/discover_extra.py imports.

Run from the project root:  python3 refresh/crawl_boards.py [--days 45] [--all]
(--all re-probes every address in slugs.json, including ones that did not answer last time.)
"""
import json, re, sys, os, html, datetime, collections, urllib.request, urllib.error, concurrent.futures as cf
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from discover_radar import BIG
from discover_extra import in_lane

DAYS = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 45
SINCE = (datetime.date.today() - datetime.timedelta(days=DAYS)).isoformat()
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
OUT = "refresh/work/research/leads_ats_crawl.json"

TITLE_ENTRY = re.compile(r"new.?grad|university|college grad|graduate|early.?career|entry.?level|\bjunior\b|\bjr\.?\b|associate|"
                         r"apprentice|resident|\b(engineer|developer|swe|sde)\s*[-,(]?\s*(i|1)\b|\b(i|1)\s*$", re.I)
TITLE_OPEN  = re.compile(r"forward.?deploy|\bFDE\b|founding|member of (technical|product) staff|\bMTS\b", re.I)
BODY_ENTRY  = re.compile(r"new grad|recent(ly)? grad\w*|entry.?level|early.?career|\b(0|zero)\s*(-|to|–)\s*[123]\+?\s*years|"
                         r"\b[12]\s*(-|to|–)\s*3\s*years|\b1\+\s*years?\b|no (prior )?experience (is )?required|"
                         r"all (experience|seniority) levels|new graduates", re.I)
BODY_SENIOR = re.compile(r"\b([4-9]|1[0-9])\+?\s*(\+|-|to|–)?\s*\d*\s*years", re.I)
# State codes are matched case-sensitively: with re.I, ordinary words like "in", "or" and "me" counted as states.
_STATES = re.compile(r"\b(AL|AK|AZ|AR|CA|CO|CT|DE|DC|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY)\b")
_NON_US_PLACE = re.compile(r"\b(europe|emea|latam|apac|canada|uk|united kingdom|india|mexico|brazil|colombia|argentina|philippines|"
                           r"germany|poland|spain|portugal|netherlands|france|ireland|israel|singapore|australia)\b", re.I)
_US_MARK = re.compile(r"\b(us|usa|u\.s\.|united states)\b", re.I)
class _US:
    """A location counts as US when it names the US, a US state code (upper case), or a US city; a 'remote'
    that names only a non-US region does not count."""
    def search(self, loc):
        if not loc: return None
        if _NON_US_PLACE.search(loc) and not (_US_MARK.search(loc) or _STATES.search(loc)): return None
        return _US_WORDS.search(loc) or _STATES.search(loc)
_US_WORDS = re.compile(r"united states|\bUSA?\b|remote|"
                r"san francisco|bay area|new york|nyc|brooklyn|austin|dallas|houston|seattle|boston|los angeles|chicago|"
                r"palo alto|mountain view|menlo park|redwood city|san mateo|sunnyvale|san jose|oakland|berkeley|santa clara|"
                r"cambridge|denver|atlanta|miami|washington|philadelphia|pittsburgh|san diego|plano|irving|frisco", re.I)
US = _US()
REGION = [("SF Bay Area", r"san francisco|\bsf\b|bay area|palo alto|mountain view|menlo park|redwood city|san mateo|sunnyvale|"
                          r"san jose|oakland|berkeley|santa clara|foster city|burlingame|south san francisco|emeryville"),
          ("New York", r"new york|\bnyc\b|brooklyn|manhattan|jersey city|hoboken"),
          ("Texas", r"\btx\b|texas|austin|dallas|houston|san antonio|plano|irving|frisco|fort worth"),
          ("Remote US", r"remote")]

SOFT = re.compile(r"software|full.?stack|back.?end|front.?end|platform|infrastructure|cloud|devops|\bSRE\b|site reliability|\bAI\b|\bML\b|"
                  r"machine learning|forward.?deploy|deployed engineer|\bFDE\b|member of technical staff|\bMTS\b|founding (software |product )?engineer|"
                  r"product engineer|developer|\bSWE\b|\bSDE\b|agent engineer|applied ai|llm|data engineer|security engineer|mobile|ios|android|web", re.I)
BAD = re.compile(r"\b(apac|emea|latam|vice president|avp|mid.?level|senior|staff|principal|contract|summer 20\d\d|intern|native language|"
                 r"civil|fire protection|corrosion|manufactur|cnc|machinist|mechanical|clinical|automation service|biw|consultant|crm developer|"
                 r"gis|value engineer|field engineer|lab strategy|robotics)\b", re.I)
_fc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "finalcheck.py")).read()
exec(_fc[_fc.index("OFFLANE="):_fc.index("re.I)", _fc.index("OFFLANE=")) + 5])      # same off-lane list as the sweep

def get(u, t=25):
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=t) as f:
            return f.status, f.read(40_000_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e: return e.code, ""
    except Exception: return 0, ""
def strip(h): return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(h or "")))
def day(v):
    if not v: return None
    if isinstance(v, (int, float)): return datetime.datetime.fromtimestamp(v / 1000 if v > 1e11 else v).date().isoformat()
    return str(v)[:10]

def board(ats, tok):
    """[(title, location, url, posted, body)] or None."""
    if ats == "ashby":
        st, d = get(f"https://api.ashbyhq.com/posting-api/job-board/{tok}")
        if st != 200: return None
        js = json.loads(d)
        return [(j.get("title", ""), " ".join([j.get("location") or ""] + [(x.get("location") or "") for x in j.get("secondaryLocations") or []]),
                 j.get("jobUrl", ""), day(j.get("publishedAt")), j.get("descriptionPlain") or "")
                for j in js.get("jobs", []) if j.get("isListed", True)], js.get("organizationName") if isinstance(js, dict) else None
    if ats == "greenhouse":
        st, d = get(f"https://boards-api.greenhouse.io/v1/boards/{tok}/jobs?content=true")
        if st != 200: return None
        js = json.loads(d)
        return [(j.get("title", ""), (j.get("location") or {}).get("name", ""), j.get("absolute_url", ""),
                 day(j.get("first_published") or j.get("updated_at")), strip(j.get("content")))
                for j in js.get("jobs", [])], (js.get("jobs") or [{}])[0].get("company_name")
    if ats == "lever":
        st, d = get(f"https://api.lever.co/v0/postings/{tok}?mode=json")
        if st != 200: return None
        js = json.loads(d)
        if not isinstance(js, list): return None
        return [(j.get("text", ""), " ".join([(j.get("categories") or {}).get("location") or ""] + ((j.get("categories") or {}).get("allLocations") or [])),
                 j.get("hostedUrl", ""), day(j.get("createdAt")),
                 (j.get("descriptionPlain") or "") + " " + " ".join(strip(l.get("content")) for l in j.get("lists") or []))
                for j in js], None
    return None

def entry_shaped(title, body):
    if TITLE_ENTRY.search(title): return "title"
    head = body[:6000]
    if BODY_ENTRY.search(head) and not BODY_SENIOR.search(head): return "posting text"
    if TITLE_OPEN.search(title) and not BODY_SENIOR.search(head): return "open level"
    return None

def region(loc):
    return next((name for name, rx in REGION if re.search(rx, loc or "", re.I)), "Other US")

def crawl(item):
    ats, tok, name = item[0], item[1], (item[2] if len(item) > 2 else None)
    try: res = board(ats, tok)
    except Exception: res = None
    if not res: return item, None, []
    rows, org = res
    keep = []
    for title, loc, url, posted, body in rows:
        if not posted or posted < SINCE: continue
        if not in_lane(title, loc) or not US.search(loc or ""): continue
        if not SOFT.search(title) or BAD.search(title) or OFFLANE.search(title): continue
        why = entry_shaped(title, body)
        if not why: continue
        if re.search(r"u\.?s\.? citizen(ship)? (is )?required|must be a u\.?s\.? citizen|security clearance|\bitar\b", body, re.I): continue
        keep.append({"title": title, "url": url, "posted": posted, "location": (loc or "").strip()[:120], "basis": why, "region": region(loc)})
    return item, (org or name or tok), keep

def main():
    live_file = "refresh/work/crawl/slugs_live.json"
    use_all = "--all" in sys.argv or not os.path.exists(live_file)
    slugs = json.load(open("refresh/work/crawl/slugs.json" if use_all else live_file))
    slugs = [s for s in slugs if not (len(s) > 2 and s[2] and BIG.search(s[2]))]
    print(f"crawling {len(slugs)} boards, roles first published since {SINCE}", flush=True)
    found, dead, alive = [], 0, []
    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        for i, (item, org, keep) in enumerate(ex.map(crawl, slugs)):
            if org is None: dead += 1; continue
            alive.append(item)
            if keep and not BIG.search(org): found.append({"company": org, "ats": item[0], "token": item[1], "roles": keep})
            if i % 250 == 0: print(f"  {i}/{len(slugs)}  boards with a match: {len(found)}", flush=True)
    out = [{"company": f["company"], "ats": f["ats"], "token": f["token"], "careersUrl": None,
            "roles": [{k: r[k] for k in ("title", "url", "posted", "location")} for r in f["roles"]],
            "stage": "unknown", "headcount": "unknown", "visa": "unstated",
            "source": f'{f["ats"]} board {f["token"]}', "why": "; ".join(sorted({r["basis"] for r in f["roles"]})),
            "regions": sorted({r["region"] for r in f["roles"]})} for f in found]
    json.dump(out, open(OUT, "w"), indent=1)
    if use_all: json.dump(alive, open(live_file, "w"))      # later runs skip addresses that never answered
    reg = collections.Counter(r for o in out for r in o["regions"])
    print(f"boards answering: {len(slugs) - dead}; companies with a fresh entry-level US role: {len(out)}; by region {dict(reg)}")

if __name__ == "__main__":
    main()
