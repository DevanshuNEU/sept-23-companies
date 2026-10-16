import json, re, os, datetime, urllib.request, urllib.error, concurrent.futures as cf
T=json.load(open("refresh/work/final_targets.json"))
OUT="refresh/work/finalcheck.json"
UA={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
def get(u,t=30):
    try:
        with urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=t) as f:
            return f.status, f.geturl(), f.read(1500000).decode("utf-8","replace")
    except urllib.error.HTTPError as e: return e.code, u, ""
    except Exception: return 0, u, ""
def clean(h):
    h=re.sub(r"<script[^>]*>.*?</script>"," ",h,flags=re.S|re.I)
    h=re.sub(r"<style[^>]*>.*?</style>"," ",h,flags=re.S|re.I)
    h=re.sub(r"<[^>]+>"," ",h); h=re.sub(r"&nbsp;?|&#?[a-z0-9]{1,8};"," ",h)
    return re.sub(r"\s+"," ",h)
def d10(v):
    if not v: return None
    s=str(v); m=re.match(r"(\d{4}-\d{2}-\d{2})",s)
    if m: return m.group(1)
    if s.isdigit():
        n=int(s); n=n/1000 if n>1e11 else n
        try: return datetime.datetime.utcfromtimestamp(n).strftime("%Y-%m-%d")
        except Exception: return None
    return None

# ---------- role lane ----------
ENG   = re.compile(r"(engineer|developer|\bMTS\b|member of technical staff|\bswe\b|\bsde\b|programmer)", re.I)
SEN   = re.compile(r"\b(senior|sr\.?|staff(?! engineer i)|principal|lead|head|director|manager|architect|vp|vice president|chief|distinguished|fellow|iii|iv)\b|\b(engineer|developer)\s*(3|4|5)\b", re.I)
INTERN= re.compile(r"\b(intern|internship|co-?op|apprenticeship program for students)\b", re.I)
OFFLANE=re.compile(r"\b(gtm|go.to.market|it systems|it support|help ?desk|desktop support|technical support|support engineer|customer support|"
                   r"sales|solutions? (engineer|consultant|architect)|pre.?sales|account|recruit|designer|design engineer|mechanical|electrical|"
                   r"hardware|firmware|embedded|\bsoc\b|mechatronic\w*|robotics hardware|analog|\brf\b|fpga|asic|silicon|manufactur|process engineer|quality engineer|\bqa\b|"
                   r"test engineer|validation|technician|scientist|research engineer, (bio|chem)|actuar|clinical|network engineer|"
                   r"field (service|technician)|installation|facilities|construction|civil|structural|signal integrity|packaging|"
                   r"documentation|technical writ\w*|developer advoca\w*|developer relations|devrel|evangelist|community|"
                   r"it engineer|customer engineer|customer experience|marketing engineer|growth marketing|(product|revenue|business) operations)\b", re.I)
# ---------- level ----------
NG_T  = re.compile(r"(new grad|new graduate|university grad|college grad|recent grad|campus|graduate (software|engineer|developer|program)|"
                   r"\bgrad\b|emerging talent|20(26|27) (new )?grad|graduates?\b(?! program manager))", re.I)
EC_T  = re.compile(r"(early.?career|\bassociate\b|junior|entry.?level|rotation|trainee|residency|apprentice)", re.I)
L1_T  = re.compile(r"\b(engineer|developer|sde|swe|programmer)\s*[-,]?\s*(i|1)\b(?!\s*/\s*(ii|2))|\blevel\s*1\b|\b(i|1)\s*/\s*(ii|2)\b", re.I)
L2_T  = re.compile(r"\b(engineer|developer|sde|swe|programmer)\s*[-,]?\s*(ii|2)\b|\blevel\s*2\b", re.I)
YOE   = re.compile(r"(\d{1,2})\s*\+?\s*(?:(?:-|to|–)\s*\d{1,2}\s*)?(?:years?|yrs?)\b[^.]{0,70}?(experience|industry|building|engineering|software|professional|of work)", re.I)
STRONG= re.compile(r"(new grad|new graduate|recent(ly)? grad|recent college|graduating|university (grad|hire)|college hire|campus hire|"
                   r"entry.?level|early.?career|0\s*(?:-|to|–)\s*[12]\s*years?|no (prior |professional )?experience (is )?(required|necessary|needed)|"
                   r"less than (1|one|2|two) years?)", re.I)
# ---------- eligibility ----------
GRADREQ=re.compile(r"(graduat\w*[^.]{0,40}\b2027\b|class of 2027|2027 (incoming |university |college )?(grad|graduate|hire)s?\b|"
                   r"expected graduation[^.]{0,30}\b2027\b)", re.I)
STARTL= re.compile(r"(start date:?\s*[a-z]*\s*,?\s*2027|2027 start\b|start(ing|s)? (in |on )?(early |mid |late )?"
                   r"(january|february|march|april|may|june|july|august|september|summer|spring|fall|winter)?\s*,?\s*2027|"
                   r"(summer|fall|winter|spring|july|august|june) 2027 start)", re.I)
PHD   = re.compile(r"(\(phd\)|phd (required|candidates|students)|pursuing a phd|ph\.d\. (required|in progress)|completed phd)", re.I)
COMP  = re.compile(r"(\$\s?\d{2,3}(?:[,.]\d{3})?\s?[kK]?\s*(?:-|to|\u2013|\u2014)\s*\$?\s?\d{2,3}(?:[,.]\d{3})?\s?[kK]?)")
START27=re.compile(r"(20(27)\s*(start|new grad|grad|graduat|class|cohort)|(start|starting|begin)s?\b[^.]{0,30}\b(20(27))|"
                   r"(summer|spring|fall|winter|january|february|march|april|may|june|july|august)\s*,?\s*2027|class of 2027|"
                   r"graduat\w*[^.]{0,50}\b2027)", re.I)
IMMED = re.compile(r"(immediate start|start (immediately|asap|as soon as)|20(26)\s*(start|new grad|grad)|december 2026 grad|"
                   r"graduat\w*[^.]{0,40}\b(2025|2026)\b)", re.I)
GRADWIN=re.compile(r"[^.]{0,120}graduat\w*[^.]{0,120}\b20(2[4-8])\b[^.]{0,80}\.", re.I)
SCHOOL= re.compile(r"(only (open|available) to (current )?(students|graduates|alumni) (of|from)|"
                   r"(university|college|school) of [A-Z][A-Za-z]+ (students|graduates|alumni) only|\b(UMiami|UCLA|USC|MIT|Stanford|CMU|UMich) only\b)")
CITIZ = re.compile(r"(u\.?s\.? citizen(ship)?( is)? (required|only)|must be (a )?u\.?s\.? citizen|security clearance|active clearance|"
                   r"clearance (is )?required|ts/sci|secret clearance|green card holders? only|permanent residents? only|"
                   r"u\.?s\.? persons?\b[^.]{0,20}(only|required|as defined)|\bitar\b|export control laws require)", re.I)
NOSPON= re.compile(r"(unable to sponsor|not able to sponsor|(do|does|will) not (offer|provide|sponsor)[^.]{0,30}(sponsor|visa)|cannot sponsor|"
                   r"no visa sponsorship|not (offering|providing) (visa )?sponsorship|without (the need for )?(visa )?sponsorship|"
                   r"sponsor h-?1b transfers?;? we'?re unable)", re.I)
YESSPON=re.compile(r"(we sponsor|we will sponsor|open to sponsor\w*|happy to sponsor|willing to (provide )?sponsor|sponsorship (is )?available|"
                   r"visa sponsorship (is )?(available|offered|provided)|we can sponsor|provide visa sponsorship)", re.I)
NONUS = re.compile(r"(india|bangalore|bengaluru|hyderabad|pune|chennai|canada|toronto|vancouver|montreal|london|united kingdom|\buk\b|ireland|"
                   r"germany|berlin|munich|poland|warsaw|krakow|israel|tel aviv|singapore|australia|sydney|japan|tokyo|korea|seoul|china|"
                   r"beijing|shanghai|taiwan|mexico|brazil|argentina|colombia|netherlands|amsterdam|paris|france|spain|madrid|barcelona|"
                   r"sweden|stockholm|zurich|switzerland|portugal|lisbon|romania|ukraine|philippines|vietnam|estonia|tallinn|latvia|lithuania|"
                   r"czech|prague|hungary|budapest|greece|athens|italy|milan|rome|austria|vienna, austria|belgium|brussels|denmark|copenhagen|"
                   r"norway|oslo|finland|helsinki|turkey|istanbul|\buae\b|dubai|abu dhabi|saudi|egypt|cairo|nigeria|lagos|kenya|nairobi|"
                   r"south africa|pakistan|bangladesh|sri lanka|nepal|indonesia|jakarta|malaysia|kuala lumpur|thailand|bangkok|new zealand|"
                   r"auckland|chile|santiago|peru|lima|costa rica|uruguay|guatemala|serbia|belgrade|croatia|bulgaria|sofia|slovakia|"
                   r"mexicali|tijuana|monterrey|guadalajara|juarez|queretaro|bogota|medellin|"
                   r"haiyan|shenzhen|hangzhou|suzhou|wuhan|chengdu|nanjing|guangzhou|hong kong|macau|taipei|manila|cebu|"
                   r"ho chi minh|hanoi|kuala lumpur|penang|bengal|noida|gurgaon|gurugram|mumbai|delhi|kolkata|ahmedabad|"
                   r"\bemea\b|\bapac\b|\blatam\b|\beurope\b|remote\s*-\s*(emea|apac|latam|europe))", re.I)
USOK  = re.compile(r"(united states|\bUSA?\b|san francisco|new york|nyc|seattle|austin|boston|chicago|denver|los angeles|palo alto|mountain view|"
                   r"sunnyvale|san jose|san mateo|redwood|menlo|oakland|berkeley|remote\s*[-(]?\s*us)", re.I)
CLOSED= re.compile(r"(no longer (accepting|available|open|active)|position (has been )?(filled|closed)|job (posting )?(is )?(not found|closed|expired|no longer)|"
                   r"this (job|position|role) (has|is) (expired|closed|no longer)|page (not found|does not exist)|404)", re.I)

def sent(body, rx):
    m = rx.search(body)
    if not m: return None
    a = body.rfind(".", 0, m.start()); b = body.find(".", m.end())
    return body[(a+1 if a>=0 else max(0,m.start()-160)):(b+1 if b>=0 else m.end()+160)].strip()[:260]
def tier(title, body, y):
    if y is not None and y > 3: return None
    if NG_T.search(title): return 1
    if EC_T.search(title): return 2
    if STRONG.search(body) and (y is None or y <= 1): return 2
    if y is not None and y <= 1: return 2
    if L1_T.search(title): return 3
    if L2_T.search(title): return 4
    if y is not None and y <= 3: return 4
    return 5
def analyse(title, body, loc, posted, url):
    tt=re.sub(r"member of (technical|product|engineering) staff","MTS",title,flags=re.I)
    if not ENG.search(title) or INTERN.search(title) or SEN.search(tt) or OFFLANE.search(title): return None
    if re.search(r"(submissions?|talent (pool|network|community)|general (application|interest)|future (opportunities|openings)|"
                 r"don'?t see (a|the) (role|fit)|expression of interest|join our network)", title, re.I): return None
    if loc and NONUS.search(loc) and not USOK.search(loc): return None
    yrs=[int(m.group(1)) for m in YOE.finditer(body) if 0 <= int(m.group(1)) <= 15]
    y=min(yrs) if yrs else None
    t=tier(title, body, y)
    gw=GRADWIN.search(body)
    # graduation window: a 2027-only requirement excludes him (MS, May 2026); a window that also names 2025/2026, or
    # "earned or expected by", includes him
    gr=None; grs=None
    m=GRADREQ.search(title+" "+body)
    if m:
        a=(title+" "+body).rfind(".",0,m.start()); b=(title+" "+body).find(".",m.end())
        grs=(title+" "+body)[a+1 if a>=0 else max(0,m.start()-140):(b+1 if b>=0 else m.end()+140)].strip()[:240]
        incl = re.search(r"(20(2[3-6]))", grs) or re.search(r"(earned or expected|graduated or|already graduated|recent(ly)? graduated|within the (last|past))", grs, re.I)
        gr = "window" if incl else "2027only"
    sl=STARTL.search(title+" "+body)
    cm=COMP.search(body)
    s27=bool(START27.search(title+" "+body)); imm=bool(IMMED.search(title+" "+body))
    return {"title":title[:110],"loc":(loc or "")[:60],"posted":posted,"url":url,"minYears":y,"tier":t,
            "start2027": bool(sl), "immediate": imm, "gradReq": gr, "gradSnip": grs,
            "phd": bool(PHD.search(title+" "+body[:3000])), "comp": (cm.group(1) if cm else None),
            "gradWindow": (gw.group(0).strip()[:220] if gw else None),
            "schoolOnly": bool(SCHOOL.search(body[:6000])) and not re.search(r"currently enrolled in (a|an|our) (benefits|401)",body,re.I),
            "citizen": bool(CITIZ.search(body)), "noSponsor": bool(NOSPON.search(body)), "yesSponsor": bool(YESSPON.search(body)),
            "snip": {k: sent(body, rx) for k, rx in (("citizen",CITIZ),("noSponsor",NOSPON),("yesSponsor",YESSPON),
                                                      ("schoolOnly",SCHOOL),("start2027",STARTL)) if rx.search(body if k!="start2027" else title+" "+body)}}

def board(t):
    s=t["slugs"]; rows=[]
    if s.get("ashby"):
        st,_,d=get(f"https://api.ashbyhq.com/posting-api/job-board/{s['ashby']}?includeCompensation=true")
        try:
            for j in json.loads(d).get("jobs",[]):
                rows.append((j.get("title",""), clean(j.get("descriptionPlain") or j.get("descriptionHtml") or ""),
                             j.get("location",""), d10(j.get("publishedAt") or j.get("updatedAt")), j.get("jobUrl","")))
        except Exception: pass
    if s.get("greenhouse"):
        st,_,d=get(f"https://boards-api.greenhouse.io/v1/boards/{s['greenhouse']}/jobs?content=true")
        try:
            for j in json.loads(d).get("jobs",[]):
                loc=(j.get("location") or {}).get("name","") if isinstance(j.get("location"),dict) else (j.get("location") or "")
                import html as H
                rows.append((j.get("title",""), clean(H.unescape(j.get("content") or "")), loc,
                             d10(j.get("first_published") or j.get("updated_at")), j.get("absolute_url","")))
        except Exception: pass
    if s.get("lever"):
        st,_,d=get(f"https://api.lever.co/v0/postings/{s['lever']}?mode=json")
        try:
            for j in json.loads(d):
                body=(j.get("descriptionPlain") or j.get("description") or "")
                for l in (j.get("lists") or []): body+=" "+(l.get("text") or "")+" "+clean(l.get("content") or "")
                body+=" "+(j.get("additionalPlain") or "")
                rows.append((j.get("text",""), clean(body), ((j.get("categories") or {}).get("location") or ""),
                             d10(j.get("createdAt")), j.get("hostedUrl","")))
        except Exception: pass
    return rows
def urlonly(t):
    rows=[]
    for r in t["urls"]:
        st,final,h=get(r["u"])
        body=clean(h)
        closed = st in (404,410) or bool(CLOSED.search(body[:4000]))
        unver  = (not closed) and (st == 0 or st >= 400 or len(body) < 1500)
        title=re.sub(r"\s+-\s+[^-]*$","",r["t"])
        loc=(r["t"].split(" - ")[-1] if " - " in r["t"] else "")
        rows.append((title, body, loc, r.get("d"), r["u"], closed, st, unver))
    return rows
def work(t):
    out={"list":t["list"],"source":None,"jobs":[],"closedUrls":0,"checked":0}
    if t["slugs"]:
        out["source"]="board"
        for title,body,loc,posted,url in board(t):
            a=analyse(title,body,loc,posted,url)
            if a: out["jobs"].append(a)
        out["boardEmpty"] = not out["jobs"]
    else:
        out["source"]="url"
        for title,body,loc,posted,url,closed,st,unver in urlonly(t):
            out["checked"]+=1
            if closed: out["closedUrls"]+=1; continue
            if unver:
                # page would not render for a script: keep the role from the aggregator, level from its title only
                a=analyse(title,"",loc,posted,url)
                if a: a["unverified"]=True; out["jobs"].append(a)
                out["unverified"]=out.get("unverified",0)+1
                continue
            a=analyse(title,body,loc,posted,url)
            if a: out["jobs"].append(a)
    return t["id"], out
res={}
with cf.ThreadPoolExecutor(max_workers=14) as ex:
    for i,(cid,o) in enumerate(ex.map(work,T)):
        res[cid]=o
        if i%40==0: print(f"  {i}/{len(T)}", flush=True)
json.dump(res, open(OUT,"w"))
allj=[j for v in res.values() for j in v["jobs"]]
print("DONE companies:",len(res),"| in-lane US jobs:",len(allj))
