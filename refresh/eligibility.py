import json, re, urllib.request, html as H, concurrent.futures as cf
T={t["id"]:t for t in json.load(open("refresh/work/final_targets.json"))}
R=json.load(open("refresh/work/finalcheck.json"))
UA={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
def get(u):
    try:
        with urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=30) as f: return f.read(1500000).decode("utf-8","replace")
    except Exception: return ""
def clean(h):
    h=H.unescape(h or ""); h=re.sub(r"<script[^>]*>.*?</script>"," ",h,flags=re.S|re.I); h=re.sub(r"<style[^>]*>.*?</style>"," ",h,flags=re.S|re.I)
    h=re.sub(r"<[^>]+>"," ",h); h=re.sub(r"&nbsp;?|&#?[a-z0-9]{1,8};"," ",h); return re.sub(r"\s+"," ",h)
def bodies(cid):
    t=T[cid]; s=t["slugs"]; out={}
    if s.get("ashby"):
        try:
            for j in json.loads(get(f"https://api.ashbyhq.com/posting-api/job-board/{s['ashby']}")).get("jobs",[]):
                out[j.get("jobUrl","")]=clean(j.get("descriptionHtml") or j.get("descriptionPlain"))
        except Exception: pass
    if s.get("greenhouse"):
        try:
            for j in json.loads(get(f"https://boards-api.greenhouse.io/v1/boards/{s['greenhouse']}/jobs?content=true")).get("jobs",[]):
                out[j.get("absolute_url","")]=clean(j.get("content"))
        except Exception: pass
    if s.get("lever"):
        try:
            for j in json.loads(get(f"https://api.lever.co/v0/postings/{s['lever']}?mode=json")):
                b=(j.get("descriptionPlain") or "")+" "+" ".join((l.get("text") or "")+" "+clean(l.get("content")) for l in (j.get("lists") or []))+" "+(j.get("additionalPlain") or "")
                out[j.get("hostedUrl","")]=clean(b)
        except Exception: pass
    return out

GRAD_MONTH = 2026*12 + 5          # he finished his MS in May 2026
MON={"jan":1,"january":1,"feb":2,"february":2,"mar":3,"march":3,"apr":4,"april":4,"may":5,"jun":6,"june":6,"jul":7,"july":7,
     "aug":8,"august":8,"sep":9,"sept":9,"september":9,"oct":10,"october":10,"nov":11,"november":11,"dec":12,"december":12,
     "spring":5,"summer":8,"fall":12,"autumn":12,"winter":12}
DATE=re.compile(r"(?:\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|spring|summer|fall|autumn|winter)\b(?:\s*[/&-]\s*[a-z]+)?\s*,?\s*)?\b(20(2[3-9]))\b", re.I)
GRADSENT=re.compile(r"[^.]*\b(graduat\w*|class of|degree (earned|completed|by)|university hire|campus hire|incoming grad)\b[^.]*\.", re.I)
PURSUING=re.compile(r"(currently (pursuing|enrolled|working toward|in the process of obtaining|a student)|actively pursuing|"
                    r"current (college |university )?(senior|student|undergrad)|must be (a )?(current(ly)? )?(enrolled|student)|"
                    r"in your final year|final.year (student|undergrad))", re.I)
ALTGRAD=re.compile(r"(or (a )?recent(ly)? grad(uate)?s?\b|recently graduated (or|and)|recent (college |university )?graduates? (are |is )?(also )?(welcome|encouraged|eligible|considered)|"
                   r"open to recent graduates|earned or expected|or have (recently )?graduated|or (have )?completed (your|a|the) degree|"
                   r"graduated within the (last|past)|already graduated|recent grads? welcome)", re.I)
def months_in(sent):
    out=[]
    for m in DATE.finditer(sent):
        y=int(m.group(2)); mo=m.group(1)
        mo=MON.get(mo.lower()[:4].rstrip("t"), None) if mo else None
        if mo is None and m.group(1): mo=MON.get(m.group(1).lower(), None)
        out.append((y, mo))
    return out
SOFT=re.compile(r"(preferred|prioriti[sz]ed|also (be )?considered|ideal(ly)?|a plus|nice to have|bonus|welcome to apply)", re.I)
COHORT_T=re.compile(r"(\b2027\s*(new\s*)?grad(uate)?s\b|\b2027 (university|college|campus) (grad|hire)|class of 2027|2027 incoming grad|"
                    r"university hire 2027|campus full time 2027|graduate (developer|software engineer)[^|]*\b2027\b|\b2027 graduate\b|"
                    r"\(college grad 2027\)|(december 2026|spring 2027|fall 2026) graduates)", re.I)
def near_dates(s):
    """Dates that sit within ~80 characters of a graduation word, so a year elsewhere in a run-on paragraph does not count."""
    out=[]
    for g in re.finditer(r"(graduat\w*|class of)", s, re.I):
        win=s[max(0,g.start()-40):g.end()+140]
        out+=months_in(win)
    return out
def eligibility(title, body):
    # 1) an explicit graduation window in the body decides it
    for m in GRADSENT.finditer(body):
        sn=m.group(0).strip()
        if re.search(r"\b(we|our (company|team|founders)|the company)\b[^.]{0,30}graduated|graduated from (y ?combinator|yc|techstars|a16z)|"
                     r"(y ?combinator|yc)'?s? [a-z]+ 20\d\d (batch|cohort)", sn, re.I):
            continue                                   # company history, not an eligibility rule
        ds=near_dates(sn)
        if not ds: continue
        if ALTGRAD.search(sn): return "ok", sn[:220]
        if SOFT.search(sn): return "ok", sn[:220]
        lo=min(y*12+(mo or 1) for y,mo in ds); hi=max(y*12+(mo or 12) for y,mo in ds)
        # "by Summer 2027" / "no later than" is an upper bound: anyone who already graduated qualifies
        if re.search(r"\b(by|no later than|before|prior to)\s+(the\s+)?(end of\s+)?(\w+\s+)?20(2[5-8])", sn, re.I): lo = 0
        if lo <= GRAD_MONTH <= hi: return "ok", sn[:220]
        return "excluded", sn[:220]
    # 2) wording that explicitly lets graduates in
    alt=ALTGRAD.search(body)
    if alt:
        a=body.rfind(".",0,alt.start()); b=body.find(".",alt.end())
        return "ok", body[a+1 if a>=0 else max(0,alt.start()-120):(b+1 if b>=0 else alt.end()+120)].strip()
    # 3) a current-student requirement
    p=PURSUING.search(body)
    if p:
        a=body.rfind(".",0,p.start()); b=body.find(".",p.end())
        return "excluded", body[a+1 if a>=0 else max(0,p.start()-120):(b+1 if b>=0 else p.end()+120)].strip()[:220]
    # 4) the title names a graduating cohort he is not in
    if COHORT_T.search(title): return "excluded", "The role is titled for a graduating cohort: " + title
    return "ok", None
cache={}
def run(cid):
    if T[cid]["slugs"]: bs=bodies(cid)
    else: bs={j["url"]: clean(get(j["url"])) for j in R[cid]["jobs"]}
    res=[]
    for j in R[cid]["jobs"]:
        b=bs.get(j["url"],"")
        e,q=eligibility(j["title"], b) if (b or j["tier"] in (1,2)) else ("ok",None)
        res.append((j["url"],e,q))
    return cid,res
out={}
with cf.ThreadPoolExecutor(12) as ex:
    for cid,res in ex.map(run, list(R.keys())):
        out[cid]={u:{"elig":e,"eligQuote":q} for u,e,q in res}
json.dump(out, open("refresh/work/eligibility.json","w"))
allr=[(cid,u,v) for cid,d in out.items() for u,v in d.items()]
ex=[x for x in allr if x[2]["elig"]=="excluded"]
print("roles checked:",len(allr),"| excluded for graduation date or current-student requirement:",len(ex))
names={k:v["name"] for k,v in T.items()}
titles={j["url"]:j["title"] for v in R.values() for j in v["jobs"]}
for cid,u,v in ex:
    print(f"  {names[cid][:18]:<20}| {titles.get(u,'')[:44]:<46}| {str(v['eligQuote'])[:120]}")
