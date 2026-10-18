const STAGES = ["Not started","Researching","Referral asked","Applied","Replied","Interviewing","Offer","Closed"];
const KEY = "outreach.v1";
const PREFS = "outreach.prefs.v1";
const WEEKLY_GOAL = 100;
const $ = (s,r=document)=>r.querySelector(s);
const $$ = (s,r=document)=>[...r.querySelectorAll(s)];
const esc = s => String(s??"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");

/* ============================================================================
   Scoring and selection. Unchanged in substance from the previous console:
   the interview score is what has actually predicted a callback for him.
   ============================================================================ */
const NEWGRAD_T = /new.?grad|university grad|college grad|early.?career|campus|graduate (software|engineer|program)/i;
const SHAPE_T   = /founding|member of (technical|product) staff|\bMTS\b|forward.?deploy|\bFDE\b/i;
const JUNIOR_T  = /\b(I|1|II|2)\b|associate|junior|entry.?level|\bgrad\b|apprentice|rotation/i;
const BLOCKED   = c => (c.blockers||[]).some(b=>/ITAR|clearance|citizenship|NRC/i.test(b));

function reqText(c){ return (c.reqs||[]).map(r=>r.t).join(" | "); }

// Age of the req he would name, not of the company's newest posting of any kind.
function freshness(c){
  const d = (c.level && c.level.entryPosted) ? c.level.entryPosted
          : (c.reqs||[]).map(r=>r.d).filter(Boolean).sort().pop();
  if (!d) return 9999;
  return Math.max(0, Math.round((Date.now() - new Date(d+"T00:00:00")) / 86400000));
}
// Opened new-grad roles around this time last year and has nothing open yet: worth a message before it posts.
const ahead = c => !!c.seasonal && (c.noRoleInBand || !(c.reqs||[]).length);
function nameThisReq(c){
  if (ahead(c)) return c.seasonal.title;
  if (c.level && c.level.entryReq) return c.level.entryReq;
  const rs = c.reqs || [];
  const r = rs.find(x=>NEWGRAD_T.test(x.t)) || rs.find(x=>SHAPE_T.test(x.t))
         || rs.find(x=>JUNIOR_T.test(x.t)) || rs[0];
  return r ? r.t.replace(/\s+-\s+[^-]*$/, "") : "";
}
function reqUrl(c){
  if (ahead(c)) return c.seasonal.board || c.seasonal.url || "";
  if (c.level && c.level.entryUrl) return c.level.entryUrl;
  const want = nameThisReq(c).slice(0,24);
  const r = (c.reqs||[]).find(x=>(x.t||"").startsWith(want)) || (c.reqs||[])[0];
  return r ? r.u : "";
}
function scoreParts(c){
  const ap = c.apparatus;
  const reach = ap == null ? 13 : ap === 0 ? 38 : ap <= 6 ? 34 : ap <= 20 ? 27 : ap <= 60 ? 15 : 5;
  const t = reqText(c), lv = c.level;
  const role = lv && lv.levelOk === false ? 0
    : lv && lv.entryTier ? ({1:26, 2:23, 3:20, 4:16, 5:13})[lv.entryTier]
    : !t ? 4 : NEWGRAD_T.test(t) ? 24 : SHAPE_T.test(t) ? 21 : JUNIOR_T.test(t) ? 18 : 10;
  const d = freshness(c);
  const fresh = d <= 7 ? 22 : d <= 16 ? 19 : d <= 31 ? 15 : d <= 62 ? 8 : d <= 120 ? 3 : 0;
  const shots = Math.min(10, (c.velocity || (c.reqs||[]).length || 0) * 3);
  const s = c.stageClass;
  const stage = s === "early" ? 12 : s === "mid" ? 9 : s === "frequent" ? 7 : s === "established" ? 2
    : (ap != null && ap > 300) ? 2 : c.yc ? 10 : 6;
  return { reach, role, fresh, shots, stage };
}
function interviewScore(c){
  const p = scoreParts(c);
  const total = Math.min(100, p.reach + p.role + p.fresh + p.shots + p.stage + (indiaLink(c) ? 6 : 0));
  if (c.visaDenied) return Math.round(total * 0.35);
  if (BLOCKED(c))   return Math.round(total * 0.3);
  if (c.level && c.level.levelOk === false) return Math.round(total * 0.5);
  return total;
}
// A founder who studied or worked in India, or an engineering team there: referrals from him land better.
const indiaLink = c => !!(c.india && (c.india.founderIndia || c.india.indiaOffice));
const STAGE_LABEL = c => {
  if (c.stageClass === "early")       return `${c.formD} raise${c.formD>1?"s":""} since ${c.formDFirst}`;
  if (c.stageClass === "mid")         return `${c.formD} raise${c.formD>1?"s":""} from ${c.formDFirst}`;
  if (c.stageClass === "frequent")    return `${c.formD} raises since ${c.formDFirst}`;
  if (c.stageClass === "established") return `Filing since ${c.formDFirst}`;
  if (c.yc)                           return `YC ${c.yc.batch}`;
  return "";
};
function reachOf(c){
  if (c.reach === 0) return 0;
  if (c.reach) return c.reach;
  const n = c.apparatus != null ? c.apparatus : null;
  if (n == null) return 3;
  return n === 0 ? 1 : n <= 20 ? 2 : n <= 60 ? 3 : 4;
}
// His order: new grad, then early career, then SE1, then SDE2 (up to 3 years asked).
const TIER_NAME = {1:"New grad", 2:"Early career", 3:"SE1", 4:"SDE2", 5:"Level not stated", 7:"Hired new grads around now last year", 8:"OPT only, will not sponsor", 9:"Older postings, still open"};
const isOld = c => freshness(c) > 60;
const groupOf = c => ahead(c) ? 7 : c.optOnly ? 8 : isOld(c) ? 9 : tierOf(c);
const groupRank = c => { const g = groupOf(c); return g <= 5 ? 0 : g; };
function tierOf(c){
  if (c.level && c.level.entryTier) return c.level.entryTier;
  const t = nameThisReq(c);
  if (/new.?grad|university grad|college grad|campus|\bgrad\b/i.test(t)) return 1;
  if (/early.?career|associate|junior|entry.?level/i.test(t)) return 2;
  if (/\b(engineer|developer|sde|swe)\s*[-,]?\s*(i|1)\b/i.test(t)) return 3;
  if (/\b(engineer|developer|sde|swe)\s*[-,]?\s*(ii|2)\b/i.test(t)) return 4;
  return 5;
}
const starts2027 = c => !!(c.level && c.level.start2027);
const byLevel = (a,b) => tierOf(a) - tierOf(b)
  || (starts2027(a) ? 1 : 0) - (starts2027(b) ? 1 : 0)
  || interviewScore(b) - interviewScore(a);
const REACH_LABEL = {0:"Not established", 1:"Founder-direct", 2:"Reaches a hiring engineer", 3:"Usually a recruiter first", 4:"Full recruiting funnel"};

/* ---------------- state ---------------- */
let state = load();
let prefs = loadPrefs();
let view = prefs.view || "linkedin";
let current = null;          // company in focus on the LinkedIn view
let selected = null;         // company open in the drawer
let query = "";

function load(){
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch(e){ saved = {}; }
  const out = {};
  for (const c of COMPANIES){
    out[c.id] = Object.assign(
      { stage: c.seed ? (STAGES.includes(c.seed) ? c.seed : "Replied") : "Not started",
        contact:"", channel:"", reached:"", repliedOn:"", next:"", notes:"" },
      saved[c.id] || {});
  }
  return out;
}
function loadPrefs(){
  try { return Object.assign({ newGradOnly:false, thisMonth:false, unsure:true, view:"linkedin" },
                             JSON.parse(localStorage.getItem(PREFS) || "{}")); }
  catch(e){ return { newGradOnly:false, thisMonth:false, unsure:true, view:"linkedin" }; }
}
const save      = () => { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch(e){} };
const savePrefs = () => { try { localStorage.setItem(PREFS, JSON.stringify(prefs)); } catch(e){} };
const today     = () => new Date().toISOString().slice(0,10);

/* ---------------- the three lists ---------------- */
const isLive = c => state[c.id].stage !== "Not started" && state[c.id].stage !== "Closed";

// LinkedIn: under ~500 people, the shape of company he converts at, a live req in his band.
function inRange(c){
  if (c.seed) return true;
  if (c.offLane) return false;
  return c.sizeClass === "small" || c.sizeClass === "unsure";
}
function linkedinQueue(){
  const t = today();
  return COMPANIES.filter(c=>{
    if (state[c.id].stage !== "Not started") return false;
    if (!inRange(c) || c.visaDenied || BLOCKED(c)) return false;
    if (ahead(c)) return prefs.unsure || c.sizeClass !== "unsure";
    if (c.noRoleInBand) return false;
    if (c.level && c.level.levelOk === false) return false;
    const txt = reqText(c);
    if (!txt) return false;
    const leveled = c.level && c.level.levelChecked && c.level.entryTier && c.level.entryTier <= 4;
    if (!leveled && !(NEWGRAD_T.test(txt) || SHAPE_T.test(txt) || JUNIOR_T.test(txt))) return false;
    if (!prefs.unsure && c.sizeClass === "unsure") return false;
    if (prefs.newGradOnly && tierOf(c) > 2) return false;
    if (prefs.no2027 && starts2027(c)) return false;
    if (prefs.thisMonth && freshness(c) > 31) return false;
    return true;
  }).sort((a,b)=>{
    const sa = state[a.id].skipped === t, sb = state[b.id].skipped === t;
    if (sa !== sb) return sa ? 1 : -1;               // skipped today goes to the back
    const ga = groupRank(a), gb = groupRank(b);
    if (ga !== gb) return ga - gb;                   // recent postings, then hiring season, then OPT only, then old
    if (ga === 8) { const ea = everified(a), eb = everified(b); if (ea !== eb) return ea ? -1 : 1; }
    if (ga === 7 && !!a.seasonal.repeat !== !!b.seasonal.repeat) return a.seasonal.repeat ? -1 : 1;
    return byLevel(a,b);
  });
}

// Cold email: the tech companies above the 500 line, which LinkedIn cannot reach, with a live req in band.
const COLD_TECH = new Set(("Abnormal AI|Adobe|Alation|Appian|Apple|Applied Intuition|Atlassian|ByteDance|C3.ai|" +
  "Cadence Design Systems|CarGurus|Cerebras|Cisco|CoStar Group|CoreWeave|DoorDash|F5|Google|ID.me|IXL Learning|" +
  "InterSystems|Klaviyo|NVIDIA|Noom|Notion|Omnissa|OpenAI|Oracle|PROS Holdings, Inc.|PowerSchool Group|Quora|" +
  "Roblox|Rover|Salesforce|Scale AI|SeatGeek|ServiceNow|SimpliSafe|Snowflake|Stripe|Superhuman|Tebra|" +
  "Torc Robotics|Upstart|Veeva Systems|Verkada|WeRide|Zip|ZipRecruiter|iManage|Mimecast|Quantinuum|AspenTech|" +
  "Aptean|Asure|Harris Computer|Perplexity AI|Ramp|Cursor (Anysphere)|Dialpad|Workato|Machinify|Commure|" +
  "Tenstorrent|Elastic|Symbotic|Remitly|Crowdstrike|Palo Alto Networks|Intuit|Databricks|Cyera").split("|"));
function coldList(){
  return COMPANIES.filter(c=>{
    if (state[c.id].stage !== "Not started" || c.seed) return false;
    if (inRange(c) || !COLD_TECH.has(c.name)) return false;
    if (c.visaDenied || BLOCKED(c) || c.noRoleInBand || (c.level && c.level.levelOk === false)) return false;
    const txt = reqText(c);
    if (!txt || !(NEWGRAD_T.test(txt) || SHAPE_T.test(txt) || JUNIOR_T.test(txt))) return false;
    return freshness(c) <= 60;
  }).sort(byLevel);
}
const STAGE_ORDER = {"Offer":0,"Interviewing":1,"Replied":2,"Applied":3,"Referral asked":4,"Researching":5};
function liveRank(c){ return c.round ? 0 : c.referralNote ? 1 : 2; }
function liveList(){
  return COMPANIES.filter(isLive).sort((a,b)=>
    liveRank(a) - liveRank(b)
    || (STAGE_ORDER[state[a.id].stage]??9) - (STAGE_ORDER[state[b.id].stage]??9)
    || (state[b.id].reached||"").localeCompare(state[a.id].reached||""));
}
function searchList(q){
  const terms = q.toLowerCase().split(/\s+/).filter(Boolean);
  const SYN = { mts:"member of technical staff", fde:"forward deployed", sf:"san francisco", nyc:"new york" };
  return COMPANIES.filter(c=>{
    const st = state[c.id];
    const hay = [c.name, c.hq, c.cat, reqText(c), st.contact, st.notes, st.next].join(" ").toLowerCase();
    return terms.every(t => hay.includes(t) || (SYN[t] && hay.includes(SYN[t])));
  }).sort((a,b)=>interviewScore(b) - interviewScore(a)).slice(0,120);
}

/* ---------------- small helpers for display ---------------- */
// Where the role is. Prefers a US city when a role lists several, and ignores team names.
const NONUS_CITY = /amsterdam|london|toronto|vancouver|montreal|bangalore|bengaluru|hyderabad|pune|berlin|munich|paris|dublin|singapore|tokyo|sydney|tel aviv|warsaw|krakow|zurich|madrid|barcelona|lisbon|estonia|canada|india|\buk\b|united kingdom|emea|apac|latam|europe/i;
const NOT_PLACE  = /\b(team|engineer|developer|grad|graduate|intern|platform|product|helios|engine|multiple teams)\b/i;
function cleanPlace(p){
  let x = p.replace(/,?\s*(United States|USA|US)\b.*$/i,"").replace(/\b(HQ|Office|Headquarters)\b/gi,"")
           .replace(/[\s,\-\u2013]+$/,"").replace(/\s*,\s*[A-Z]{2}$/,"").replace(/\s+[A-Z]{2}$/,"").trim();
  const MAP = {"sf":"San Francisco","san francisco bay area":"San Francisco Bay Area","nyc":"New York",
               "new york city":"New York","la":"Los Angeles","remote":"Remote","remote in usa":"Remote"};
  return MAP[x.toLowerCase()] || x;
}
function city(c){
  const pick = raw => {
    if (!raw || /^(unverified|unknown|united states|us|usa)$/i.test(raw.trim())) return "";
    const parts = raw.split(/[;|\/]/).map(x=>x.trim()).filter(x=>x && !NOT_PLACE.test(x));
    const us = parts.find(x=>!NONUS_CITY.test(x)) || "";
    return us ? cleanPlace(us) : "";
  };
  return pick(c.level && c.level.entryLoc) || pick(c.hq) || "";
}
function ago(c){
  const d = freshness(c);
  if (d >= 9999) return "";
  if (d === 0) return "today";
  if (d === 1) return "1 day";
  return `${d} days`;
}
function agoShort(c){ if (ahead(c)) return "last yr"; const d = freshness(c); return d >= 9999 ? "" : `${d}d`; }

// Added by the twice-weekly refresh in the last 7 days.
const isNew = c => !!c.addedOn && (Date.now() - new Date(c.addedOn+"T00:00:00")) / 86400000 <= 7;
// "Updated Mon 8:02am", from the stamp the refresh writes into data.js.
function updatedLabel(){
  const at = window.META && window.META.refreshedAt; if (!at) return "";
  const d = new Date(at), days = Math.floor((Date.now() - d) / 86400000);
  const when = days === 0 ? "today" : days === 1 ? "yesterday" : d.toLocaleDateString(undefined,{weekday:"short", month:"short", day:"numeric"});
  return `Updated ${when}, ${d.toLocaleTimeString(undefined,{hour:"numeric", minute:"2-digit"})}`;
}

// Exactly one flag per company, the most decision-relevant one.
const everified = c => !!(c.everify && c.everify.status === "enrolled");
function flag(c){
  if (c.round)                                   return { t:c.round, k:"good" };
  if (c.visaDenied)                              return { t:"Says no to sponsorship", k:"warn" };
  if (c.optOnly)                                 return everified(c) ? { t:"OPT only, E-Verify yes", k:"good" }
                                                                     : { t:"OPT only, no sponsorship", k:"warn" };
  if (starts2027(c))                             return { t:"Starts 2027", k:"warn" };
  if (c.everify && c.everify.status === "none")  return { t:"No E-Verify", k:"warn" };
  if (c.visaConfirmed)                           return { t:"Sponsors H-1B", k:"good" };
  if (indiaLink(c))                              return { t:"India connection", k:"good" };
  if (c.seasonal)                                return { t: c.seasonal.repeat ? "Hires new grads every fall" : "Hired new grads last fall", k:"good" };
  if (isNew(c))                                  return { t:"New this week", k:"good" };
  if (c.sizeClass === "unsure")                  return { t:"Size unsure", k:"warn" };
  if (c.outreach && c.outreach.oss)              return { t:"Public repo", k:"good" };
  if (c.everify && c.everify.status === "enrolled") return { t:"E-Verify", k:"good" };
  return null;
}
const flagHtml = c => { const f = flag(c); return f ? `<span class="flag ${f.k}">${esc(f.t)}</span>` : ""; };

// Only the part of a quote that is actually about graduation, trimmed to whole words.
function gradQuote(c){
  const q = c.level && c.level.eligQuote; if (!q) return "";
  if (/^The posting asks/.test(q)) return "";                       // a note, rendered separately
  const m = q.match(/graduat|recent grad|class of|earned or expected|current(ly)? (pursuing|enrolled)/i);
  if (!m) return "";
  let a = Math.max(0, m.index - 90), b = Math.min(q.length, m.index + 150);
  let out = q.slice(a, b);
  if (a > 0) out = out.slice(out.indexOf(" ") + 1);
  if (b < q.length) out = out.slice(0, out.lastIndexOf(" ")) + "\u2026";
  return out.trim();
}
function levelLine(c){
  const lv = c.level;
  if (!lv || !lv.entryBasis) return "Level taken from the title";
  return lv.entryBasis + (lv.unverified ? ", page would not load for a check" : "");
}
function whyLine(c){
  const bits = [];
  if (c.apparatus === 0) bits.push("No visa filings yet, so no recruiting funnel");
  else if (c.apparatus != null) bits.push(`${c.apparatus} lifetime visa filing${c.apparatus===1?"":"s"}`);
  const st = STAGE_LABEL(c); if (st) bits.push(st);
  const n = (c.reqs||[]).length; if (n > 1 && !ahead(c)) bits.push(`${n} open roles`);
  if (c.seasonal) bits.push(c.seasonal.repeat ? `Opened new-grad roles in October or November of both ${c.seasonal.years.join(" and ")}`
                                              : `Opened a new-grad role around this time in ${c.seasonal.years.join(", ")}`);
  if (indiaLink(c)) bits.push(c.india.founderIndia ? (c.india.founderNote || "A founder studied or worked in India")
                                                 : (c.india.officeNote || "Has an engineering team in India"));
  if (c.optOnly) bits.push(everified(c) ? "Will not sponsor, but enrolled in E-Verify, so STEM OPT works there"
                                        : "Will not sponsor. OPT works now; STEM OPT needs them to enroll in E-Verify");
  return bits.join(". ") + (bits.length ? "." : "");
}
const peopleSearch = q => "https://www.linkedin.com/search/results/people/?keywords=" + encodeURIComponent(q);
function linkedinSearch(c){
  return "https://www.linkedin.com/search/results/people/?keywords=" + encodeURIComponent(`${c.name} engineer`);
}

/* ---------------- week counter ---------------- */
function weekStart(){
  const d = new Date(); const day = (d.getDay()+6)%7;        // Monday = 0
  d.setDate(d.getDate()-day); return d.toISOString().slice(0,10);
}
function sentThisWeek(){
  const w = weekStart();
  return Object.values(state).filter(s => s.reached && s.reached >= w).length;
}

/* ============================================================================
   Rendering
   ============================================================================ */
function renderTop(){
  const tabs = [
    ["linkedin", "LinkedIn", linkedinQueue().length],
    ["email",    "Cold email", coldList().length],
    ["live",     "In conversation", liveList().length],
    ["outbox",   "Outbox", mail ? mail.contacts.filter(c=>REVIEW.includes(c.status)).length : ""],
  ];
  $("#tabs").innerHTML = tabs.map(([k,label,n]) =>
    `<button role="tab" class="tab" data-view="${k}" aria-selected="${view===k && !query}">
       ${label}<span class="n">${n}</span></button>`).join("");
  const n = sentThisWeek(), pct = Math.min(100, n / WEEKLY_GOAL * 100);
  $("#week").innerHTML = `<span><b>${n}</b> of ${WEEKLY_GOAL} this week</span>
    <span class="wbar" aria-hidden="true"><i style="width:${pct}%"></i></span>
    ${updatedLabel() ? `<span class="upd" title="Roles re-checked every Monday and Thursday at 8am">${updatedLabel()}</span>` : ""}`;
}

function render(){
  renderTop();
  const main = $("#main");
  main.dataset.mode = query ? "search" : view;
  if (query)              main.innerHTML = listView(searchList(query), "search");
  else if (view==="linkedin") main.innerHTML = linkedinView();
  else if (view==="email")    main.innerHTML = listView(coldList(), "email");
  else if (view==="outbox")   main.innerHTML = outboxView();
  else                         main.innerHTML = liveView();
  renderDrawer();
}

/* ---- Outbox: cold emails drafted by Claude, approved by you, sent by mail/sender.py ---- */
const MAIL = "http://127.0.0.1:3002";
const REVIEW = ["drafted", "followup_ready", "failed"];
let mail = null, mailErr = "";
async function loadMail(){
  try { const r = await fetch(MAIL + "/api/state", {cache:"no-store"}); mail = await r.json(); mailErr = ""; }
  catch(e){ mail = null; mailErr = "The email backend is not running (mail/server.py on port 3002)."; }
  if (view === "outbox" && !query) render(); else renderTop();
}
async function mailPost(path, body){
  const r = await fetch(MAIL + path, {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(body)});
  const j = await r.json(); if (j.error) toast(j.error); return j;
}
const OB_GROUPS = [
  ["Waiting for your approval", REVIEW],
  ["Approved, sending soon", ["approved", "followup_approved"]],
  ["Checking whether it went out", ["sending", "send_unknown"]],
  ["Sent, waiting for a reply", ["sent", "followup_sent"]],
  ["Replied", ["replied"]],
  ["Bounced, closed or skipped", ["bounced", "closed", "skipped"]],
];
function obCard(c){
  const fu = ["followup_ready", "followup_approved"].includes(c.status), msg = fu ? c.followup : c.draft, editable = REVIEW.includes(c.status);
  const role = c.role && c.role.title ? `, re: ${esc(c.role.title)}` : "";
  const when = c.status === "sent" || c.status === "followup_sent" ? ` Sent ${esc((c.followupSentAt || c.sentAt || "").slice(0,10))}.` : "";
  return `<article class="ob" data-ob="${c.id}">
    <header><b>${esc(c.name)}</b> <span>${esc(c.title || c.kind || "")} at ${esc(c.company)}${role}</span>
      <small>${esc(c.email)}.${when}${fu ? " Follow-up." : ""}${c.status === "failed" ? " Last send failed: " + esc(c.error || "") : ""}</small></header>
    ${(c.hooks||[]).length ? `<p class="hooks">${c.hooks.map(esc).join(". ")}</p>` : ""}
    ${editable ? `<input class="ob-subj" value="${esc(msg.subject)}" aria-label="Subject">
      <textarea class="ob-body" rows="${Math.min(16, msg.body.split("\n").length + 2)}" aria-label="Email body">${esc(msg.body)}</textarea>
      <div class="ob-acts"><button class="btn primary" data-mact="approve" data-id="${c.id}">Approve and queue</button>
        <button class="btn quiet" data-mact="save" data-id="${c.id}">Save edits</button>
        <button class="linkish" data-mact="skip" data-id="${c.id}">Skip</button></div>`
    : `<p class="ob-subj-ro">${esc(msg ? msg.subject : "")}</p><pre class="ob-body-ro">${esc(msg ? msg.body : "")}</pre>
      ${["approved","followup_approved"].includes(c.status) ? `<div class="ob-acts"><button class="linkish" data-mact="unapprove" data-id="${c.id}">Withdraw approval</button></div>` : ""}`}
  </article>`;
}
function outboxView(){
  if (!mail) return `<section class="empty"><h2>Outbox</h2><p>${esc(mailErr || "Loading...")}</p></section>`;
  const head = `<div class="ob-top">
      <span>${mail.signedIn ? "Gmail connected." : "Gmail not connected yet. Nothing can send until you run python3 mail/gmail.py auth."}</span>
      <span><b>${mail.today}</b> of ${mail.cap} sent today, between ${mail.window[0]} and ${mail.window[1]} their time.</span>
      <button class="btn quiet sm" data-mact="pause">${mail.paused ? "Resume sending" : "Pause sending"}</button>
      ${mail.paused ? `<span class="warnline">Paused: ${esc(mail.pausedWhy)}</span>` : ""}</div>`;
  if (!mail.contacts.length) return head + `<section class="empty"><h2>No emails yet</h2>
      <p>Send Claude a name, their email and their LinkedIn profile. The draft shows up here for you to approve.</p></section>`;
  return head + OB_GROUPS.map(([label, sts]) => {
    const items = mail.contacts.filter(c => sts.includes(c.status));
    return items.length ? `<h3 class="lgroup">${label}<span>${items.length}</span></h3>${items.map(obCard).join("")}` : "";
  }).join("");
}
document.addEventListener("click", async e => {
  const b = e.target.closest("[data-mact]"); if (!b) return;
  const act = b.dataset.mact, id = b.dataset.id;
  if (act === "pause"){ await mailPost("/api/pause", {paused: !mail.paused}); return loadMail(); }
  const card = id && document.querySelector(`[data-ob="${id}"]`), c = mail && mail.contacts.find(x => x.id === id);
  if ((act === "approve" || act === "save") && card && c){
    const which = c.status === "followup_ready" ? "followup" : "draft", cur = c[which];
    const subject = card.querySelector(".ob-subj").value, body = card.querySelector(".ob-body").value;
    if (subject !== cur.subject || body !== cur.body){ const r = await mailPost("/api/save", {id, which, subject, body}); if (r.error) return; }
    if (act === "save"){ toast("Saved"); return loadMail(); }
  }
  const r = await mailPost("/api/" + act, {id});
  if (!r.error) toast(act === "approve" ? "Approved. It sends inside the sending window." : act === "skip" ? "Skipped" : "Approval withdrawn");
  loadMail();
});
loadMail(); setInterval(() => { if (view === "outbox") loadMail(); }, 60000);

/* ---- LinkedIn: one company at a time ---- */
function linkedinView(){
  const q = linkedinQueue();
  if (!q.length) return `<section class="empty">
      <h2>Nothing left in today's queue</h2>
      <p>Everyone who fits your settings has been messaged or skipped. Loosen the queue settings, or work through Cold email.</p>
      <button class="btn" data-act="settings">Queue settings</button></section>`;
  if (!current || !q.find(c=>c.id===current)) current = q[0].id;
  const c = COMPANIES.find(x=>x.id===current);
  const idx = q.findIndex(x=>x.id===current);
  const rest = q.filter(x=>x.id!==current);
  const role = nameThisReq(c), url = reqUrl(c), more = Math.max(0, (c.reqs||[]).length - 1);

  return `<div class="li">
    <section class="card" aria-label="${esc(c.name)}">
      <div class="card-head">
        <div>
          <h2 class="co">${esc(c.name)}</h2>
          <p class="place">${esc(city(c))}</p>
        </div>
        <div class="odds" title="Interview odds out of 100"><b>${interviewScore(c)}</b><span>odds</span></div>
      </div>

      <button class="role" data-act="copy" title="Copy this role title">${esc(role)}</button>
      <p class="role-meta">
        ${ahead(c) ? `<span class="lvl t1">No posting yet</span>
        <span>Last year: this role, posted ${esc(c.seasonal.last)}</span>`
        : `<span class="lvl t${tierOf(c)}">${TIER_NAME[tierOf(c)]}</span>
        <span>${levelLine(c)}</span>
        <span>Posted ${ago(c)} ago</span>`}
        ${more ? `<button class="linkish" data-act="details">${more} more role${more>1?"s":""}</button>` : ""}
      </p>

      <p class="why">${esc(whyLine(c))} ${flag(c) ? flagHtml(c) : ""}</p>

      <div class="card-acts">
        <a class="btn" href="${linkedinSearch(c)}" target="_blank" rel="noopener" data-key="l">Find engineers on LinkedIn</a>
        <a class="btn quiet" href="${peopleSearch(`${c.name} Northeastern University`)}" target="_blank" rel="noopener">Northeastern alumni</a>
        <a class="btn quiet" href="${peopleSearch(`${c.name} (IIT OR NIT OR BITS OR IIIT)`)}" target="_blank" rel="noopener">IIT, NIT, BITS alumni</a>
        ${url ? `<a class="btn" href="${esc(url)}" target="_blank" rel="noopener" data-key="o">${ahead(c) ? "Open their careers page" : "Open the posting"}</a>` : ""}
        <button class="btn quiet" data-act="copy">Copy role</button>
      </div>

      <div class="card-foot">
        <button class="linkish" data-act="skip">Skip for today</button>
        <button class="linkish" data-act="details">Details</button>
        <span class="pos">${idx+1} of ${q.length}</span>
        <button class="btn primary" data-act="messaged">Messaged, next</button>
      </div>
      <p class="keys">Keys: <kbd>C</kbd> copy role, <kbd>M</kbd> messaged, <kbd>S</kbd> skip, <kbd>D</kbd> details, <kbd>J</kbd>/<kbd>K</kbd> move</p>
    </section>

    <aside class="next" aria-label="Up next">
      <div class="next-head">
        <h3>Up next</h3>
        <button class="linkish" data-act="settings">Queue settings</button>
      </div>
      ${settingsPanel()}
      <div class="nlist">${groupByTier(rest.slice(0,160)).map(([t,items])=>`
        <h4 class="ngroup">${TIER_NAME[t]}<span>${items.length}</span></h4>
        <ol>${items.map(x=>`
        <li><button class="nrow" data-pick="${x.id}">
          <span class="nodds">${interviewScore(x)}</span>
          <span class="nname">${esc(x.name)}<small>${esc(city(x))}${starts2027(x) ? " \u00a0 starts 2027" : ""}</small></span>
          <span class="nage">${agoShort(x)}</span>
        </button></li>`).join("")}</ol>`).join("")}</div>
      ${rest.length > 60 ? `<p class="nmore">${rest.length-60} more after these</p>` : ""}
    </aside>
  </div>`;
}
function groupByTier(list){
  const out=[]; for (const c of list){ const t=groupOf(c);
    if (!out.length || out[out.length-1][0]!==t) out.push([t,[]]); out[out.length-1][1].push(c); }
  return out;
}
function settingsPanel(){
  if (!prefs.open) return "";
  const box = (k,label,hint) => `<label class="opt"><input type="checkbox" data-pref="${k}" ${prefs[k]?"checked":""}>
     <span>${label}<small>${hint}</small></span></label>`;
  return `<div class="settings">
    ${box("newGradOnly","New grad and early career only","Hide SE1 and SDE2 roles")}
    ${box("no2027","Hide roles that start in 2027","On OPT you get 90 days of unemployment in total")}
    ${box("thisMonth","Posted in the last month","Hide roles older than 31 days")}
    ${box("unsure","Include size-uncertain companies","About 300 to 700 people")}
  </div>`;
}

/* ---- Cold email and search: one clean row per company ---- */
function listView(list, kind){
  const heading = kind === "search"
    ? `<div class="lhead"><h2>${list.length} result${list.length===1?"":"s"} for “${esc(query)}”</h2>
         <button class="linkish" data-act="clearq">Clear search</button></div>`
    : `<div class="lhead"><h2>Cold email</h2>
         <p>Tech companies over 500 people, outside your LinkedIn range, with a live role in your band.
            Email an engineer on the team, and send it only when you have something specific to show them.</p></div>`;
  if (!list.length) return heading + `<section class="empty"><p>${kind==="search"
      ? "No company or role matches that. Try a company name, a city, or a title like new grad."
      : "No cold email targets right now."}</p></section>`;
  const groups = kind === "search" ? [[0, list]] : groupByTier(list);
  return heading + groups.map(([t, items]) => `
    ${t ? `<h3 class="lgroup">${TIER_NAME[t]}<span>${items.length}</span></h3>` : ""}
    <div class="rows" role="list">${items.map(c=>{
    const st = state[c.id], live = st.stage !== "Not started";
    return `<div class="row" role="listitem" data-open="${c.id}">
      <span class="rodds">${interviewScore(c)}</span>
      <span class="rco"><b>${esc(c.name)}</b><small>${esc(city(c))}</small></span>
      <span class="rrole">${esc(nameThisReq(c))}</span>
      <span class="rflag">${live ? `<span class="flag good">${esc(st.stage)}</span>` : flagHtml(c)}</span>
      <span class="rage">${agoShort(c)}</span>
      <span class="racts">
        <button class="btn quiet sm" data-act="copy" data-id="${c.id}">Copy role</button>
        ${live ? "" : `<button class="btn sm" data-act="${kind==="email"?"emailed":"messaged"}" data-id="${c.id}">${kind==="email"?"Emailed":"Messaged"}</button>`}
      </span>
    </div>`;}).join("")}</div>`).join("");
}

/* ---- In conversation ---- */
function liveView(){
  const list = liveList();
  const rows = list.map(c=>{
    const st = state[c.id];
    const note = st.next
      || (c.round ? `${c.round}. Forward Deployed Engineer, New Grad is the live req.` : "")
      || (c.referralNote ? "Referral in hand. Ask them to name the Forward Deployed Engineer (New Grad) req." : "");
    return `<div class="row live" role="listitem" data-open="${c.id}">
      <span class="rco"><b>${esc(c.name)}</b><small>${esc(city(c))}</small></span>
      <span class="rstage"><select data-stage-for="${c.id}" aria-label="Stage for ${esc(c.name)}">
        ${STAGES.map(s=>`<option ${s===st.stage?"selected":""}>${s}</option>`).join("")}</select></span>
      <span class="rnext">${note ? esc(note) : `<span class="muted">Add a next step</span>`}</span>
      <span class="rage">${st.reached ? esc(st.reached.slice(5)) : ""}</span>
    </div>`;}).join("");
  return `<div class="lhead"><h2>In conversation</h2>
      <p>Everyone you have messaged or who has replied. Write the next step on each so nothing goes quiet.</p></div>
    <div class="rows" role="list">${rows || `<section class="empty"><p>No conversations yet. Mark someone as messaged from the LinkedIn queue.</p></section>`}</div>
    <div class="backup">
      <button class="linkish" data-act="export">Download a backup of your tracker</button>
      <button class="linkish" data-act="import">Restore from a backup</button>
      <input type="file" id="file" accept="application/json" hidden>
    </div>`;
}

/* ---- Drawer: everything that does not belong on a row ---- */
function renderDrawer(){
  const host = $("#drawer");
  if (!selected){ host.innerHTML = ""; document.body.classList.remove("locked"); return; }
  const c = COMPANIES.find(x=>x.id===selected), st = state[c.id];
  document.body.classList.add("locked");
  const parts = scoreParts(c);
  const MAX = {reach:38, role:26, fresh:22, shots:10, stage:12};
  const LBL = {reach:"How close to the person who decides", role:"Is the role named for your level",
               fresh:"How recent the posting is", shots:"Open roles to aim at", stage:"Company stage"};
  const mixTotal = (c.mix||[]).reduce((a,b)=>a+b,0);

  host.innerHTML = `
  <button class="scrim" data-act="close" aria-label="Close details"></button>
  <aside class="drawer" role="dialog" aria-modal="true" aria-label="${esc(c.name)}">
    <header class="dhead">
      <div><h2>${esc(c.name)}</h2><p>${esc(city(c))}</p></div>
      <button class="x" data-act="close" aria-label="Close">Close</button>
    </header>

    <section class="dsec">
      <h3>Your tracker</h3>
      <div class="stages">${STAGES.map(s=>
        `<button class="stage" aria-pressed="${st.stage===s}" data-setstage="${s}">${s}</button>`).join("")}</div>
      <div class="f2">
        <label>Referral contact<input type="text" data-field="contact" value="${esc(st.contact)}" placeholder="Name and role"></label>
        <label>How you reached them<input type="text" data-field="channel" value="${esc(st.channel)}" placeholder="LinkedIn, email, intro"></label>
      </div>
      <div class="f2">
        <label>Reached out<input type="date" data-field="reached" value="${esc(st.reached)}"></label>
        <label>They replied<input type="date" data-field="repliedOn" value="${esc(st.repliedOn)}"></label>
      </div>
      <label>Next step<input type="text" data-field="next" value="${esc(st.next)}" placeholder="Follow up Friday, send resume"></label>
      <label>Notes<textarea data-field="notes" placeholder="What they said, who they pointed you to">${esc(st.notes)}</textarea></label>
      <span class="saved" id="saved">Saved</span>
    </section>

    ${c.roundNote ? `<section class="dsec"><h3>Where this stands</h3><p class="lead good">${esc(c.roundNote)}</p></section>` : ""}
    ${c.referralNote ? `<section class="dsec"><h3>Referral in hand</h3><p class="lead good">${esc(c.referralNote)}</p></section>` : ""}

    <section class="dsec">
      <h3>Role to name</h3>
      <p class="lead"><b>${esc(nameThisReq(c))}</b></p>
      <p class="sub">${TIER_NAME[tierOf(c)]}. ${levelLine(c)}. Posted ${ago(c)} ago.${c.level && c.level.reqsRead ? ` ${c.level.inBand} of the ${c.level.reqsRead} in-lane postings I read there are open to you.` : ""}</p>
      ${c.level && c.level.comp ? `<p class="sub">Listed pay: ${esc(c.level.comp)}.</p>` : ""}
      ${gradQuote(c) ? `<p class="sub">On graduation dates the posting says: \u201c${esc(gradQuote(c))}\u201d</p>` : ""}
      ${c.level && /^The posting asks/.test(c.level.eligQuote||"") ? `<p class="sub warnline">${esc(c.level.eligQuote)}</p>` : ""}
      ${starts2027(c) ? `<p class="sub warnline">This role starts in 2027. On OPT you get 90 days of unemployment in total, so ask whether an earlier start is possible before investing time.</p>` : ""}
      ${c.noRoleInBand ? `<p class="sub warnline">Nothing open here fits you right now: ${esc(c.noRoleWhy)}.</p>` : ""}
    </section>

    <section class="dsec">
      <h3>Interview odds ${interviewScore(c)}</h3>
      <div class="bd">${Object.entries(parts).map(([k,v])=>
        `<div class="bdrow"><span>${LBL[k]}</span><span class="bdbar"><i style="width:${Math.min(100,v/MAX[k]*100)}%"></i></span><b>${v} of ${MAX[k]}</b></div>`).join("")}</div>
      <p class="sub">The visa lottery is not part of this score.</p>
    </section>

    ${(c.blockers||[]).length || c.offLane ? `<section class="dsec"><h3>Watch out for</h3><ul class="blk">
      ${c.offLane ? `<li>${esc(c.offLaneWhy)}</li>` : ""}
      ${(c.blockers||[]).map(b=>`<li>${esc(b)}</li>`).join("")}</ul></section>` : ""}

    <section class="dsec">
      <h3>Reaching them</h3>
      <p class="lead">${REACH_LABEL[reachOf(c)]}.</p>
      <p class="sub">${esc(whyLine(c))}${c.sizeWhy ? ` Size: ${esc(c.sizeWhy)}.` : ""}</p>
      <ul class="links">
        <li><a href="${linkedinSearch(c)}" target="_blank" rel="noopener">Find engineers at ${esc(c.name)} on LinkedIn</a></li>
        ${c.yc ? `<li><a href="https://www.workatastartup.com/companies/${esc(c.yc.slug)}" target="_blank" rel="noopener">Message the founders on Work at a Startup</a></li>` : ""}
        ${c.outreach && c.outreach.oss ? `<li><a href="https://github.com/${esc(c.outreach.oss.org)}/${esc(c.outreach.oss.repo)}" target="_blank" rel="noopener">${esc(c.outreach.oss.org)}/${esc(c.outreach.oss.repo)}</a>, ${c.outreach.oss.issues} open issues. A merged pull request is a better introduction than any message.</li>` : ""}
        ${c.outreach && c.outreach.contact ? `<li>They publish <b>${esc(c.outreach.contact)}</b> on their own site.</li>` : ""}
      </ul>
    </section>

    <section class="dsec">
      <h3>Sponsorship and E-Verify</h3>
      <p class="lead">${esc(c.visa || "No statement on their postings.")}</p>
      <p class="sub">${c.everify
        ? (c.everify.status === "enrolled"
            ? `E-Verify enrolled as ${esc(c.everify.entity)} since ${esc(c.everify.enrolled)}, so your STEM OPT extension works here.`
            : `No E-Verify record found on ${esc(c.everify.checked)}. Without it you cannot use the STEM OPT extension. Enrolment is free and quick, so ask early.`)
        : `E-Verify not checked yet. <a href="https://www.e-verify.gov/e-verify-employer-search" target="_blank" rel="noopener">Check the employer search</a> before you accept anything.`}</p>
      <dl class="kv">
        <dt>Lottery odds at their wage mix</dt><dd>${c.odds!=null ? c.odds.toFixed(1)+"%" : "No filing record"}</dd>
        ${c.uscis ? `<dt>H-1B approvals, FY24 to FY26</dt><dd>${c.uscis.join(", ")}</dd>` : ""}
        ${STAGE_LABEL(c) ? `<dt>Funding</dt><dd>${esc(STAGE_LABEL(c))}</dd>` : ""}
      </dl>
    </section>

    <section class="dsec">
      <h3>Open roles</h3>
      ${(c.reqs||[]).length ? `<ul class="reqs">${c.reqs.map(r=>
        `<li><a href="${esc(r.u)}" target="_blank" rel="noopener">${esc(r.t)}</a><span>${r.d ? esc(r.d) : ""}</span></li>`).join("")}</ul>`
        : `<p class="sub">No open role recorded.</p>`}
    </section>
  </aside>`;
  requestAnimationFrame(()=>{ const x = $(".drawer .x"); if (x) x.focus({preventScroll:true}); });
}

/* ============================================================================
   Actions
   ============================================================================ */
let undoSnap = null;
function toast(msg, undo){
  const t = $("#toast");
  t.innerHTML = `<span>${esc(msg)}</span>${undo ? `<button data-act="undo">Undo</button>` : ""}`;
  t.classList.add("on");
  clearTimeout(toast.h); toast.h = setTimeout(()=>t.classList.remove("on"), undo ? 6000 : 1800);
}
async function copyRole(id){
  const c = COMPANIES.find(x=>x.id===id); const text = nameThisReq(c);
  try { await navigator.clipboard.writeText(text); toast(`Copied: ${text}`); }
  catch(e){ toast("Copy failed. Select the role title and copy it by hand."); }
}
function markSent(id, channel){
  const s = state[id];
  undoSnap = { id, prev: Object.assign({}, s) };
  s.stage = "Referral asked"; s.reached = today(); s.channel = s.channel || channel;
  save();
  const c = COMPANIES.find(x=>x.id===id);
  toast(`${c.name} marked as ${channel === "Email" ? "emailed" : "messaged"}`, true);
}
function advanceAfter(id){
  const q = linkedinQueue();
  current = q.length ? q[0].id : null;
  const card = $(".card");
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (card && !reduce){ card.classList.add("leave"); setTimeout(render, 170); }
  else render();
}
function move(delta){
  const q = linkedinQueue(); if (!q.length) return;
  const i = Math.max(0, q.findIndex(x=>x.id===current));
  current = q[(i + delta + q.length) % q.length].id; render();
}
function setView(v){ view = v; query = ""; $("#q").value = ""; prefs.view = v; savePrefs(); render(); }

document.addEventListener("click", e => {
  const tab = e.target.closest(".tab[data-view]");
  if (tab){ setView(tab.dataset.view); return; }

  const pick = e.target.closest("[data-pick]");
  if (pick){ current = pick.dataset.pick; render(); window.scrollTo({top:0, behavior:"smooth"}); return; }

  const a = e.target.closest("[data-act]");
  if (a){
    const act = a.dataset.act, id = a.dataset.id || current;
    if (act === "copy")      { e.stopPropagation(); copyRole(id); return; }
    if (act === "messaged")  { e.stopPropagation(); markSent(id, "LinkedIn"); view==="linkedin" && !query ? advanceAfter(id) : render(); return; }
    if (act === "emailed")   { e.stopPropagation(); markSent(id, "Email"); render(); return; }
    if (act === "skip")      { state[id].skipped = today(); save(); advanceAfter(id); return; }
    if (act === "details")   { selected = id; renderDrawer(); return; }
    if (act === "settings")  { prefs.open = !prefs.open; savePrefs(); render(); return; }
    if (act === "clearq")    { query = ""; $("#q").value = ""; render(); return; }
    if (act === "close")     { selected = null; renderDrawer(); return; }
    if (act === "undo" && undoSnap){ state[undoSnap.id] = undoSnap.prev; save(); current = undoSnap.id; undoSnap = null;
                                     $("#toast").classList.remove("on"); render(); return; }
    if (act === "export"){
      const blob = new Blob([JSON.stringify(state,null,2)], {type:"application/json"});
      const link = document.createElement("a"); link.href = URL.createObjectURL(blob);
      link.download = `outreach-backup-${today()}.json`; link.click(); URL.revokeObjectURL(link.href); return;
    }
    if (act === "import"){ $("#file").click(); return; }
  }

  const st = e.target.closest("[data-setstage]");
  if (st){ state[selected].stage = st.dataset.setstage; save(); render(); flash(); return; }

  const row = e.target.closest("[data-open]");
  if (row && !e.target.closest("select")){ selected = row.dataset.open; renderDrawer(); }
});

document.addEventListener("change", e => {
  const p = e.target.closest("[data-pref]");
  if (p){ prefs[p.dataset.pref] = p.checked; savePrefs(); render(); return; }
  const sf = e.target.closest("[data-stage-for]");
  if (sf){ state[sf.dataset.stageFor].stage = sf.value; save(); render(); toast(`Stage set to ${sf.value}`); return; }
  if (e.target.id === "file"){
    const f = e.target.files[0]; if (!f) return;
    const fr = new FileReader();
    fr.onload = () => { try { const inc = JSON.parse(fr.result);
        for (const id in inc) if (state[id]) Object.assign(state[id], inc[id]);
        save(); render(); toast("Backup restored"); } catch(err){ toast("That file is not a tracker backup."); } };
    fr.readAsText(f);
  }
});

document.addEventListener("input", e => {
  if (e.target.id === "q"){ query = e.target.value.trim(); render(); return; }
  const f = e.target.closest("[data-field]");
  if (f && selected){ state[selected][f.dataset.field] = f.value; save(); flash(); renderTop(); }
});

document.addEventListener("keydown", e => {
  if (e.key === "Escape"){
    if (selected){ selected = null; renderDrawer(); return; }
    if (query){ query = ""; $("#q").value = ""; render(); return; }
  }
  const tg = e.target && e.target.closest ? e.target : document.body;
  if (tg.closest("input, textarea, select") || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === "/"){ e.preventDefault(); $("#q").focus(); return; }
  if (selected || query || view !== "linkedin" || !current) return;
  const k = e.key.toLowerCase();
  if (k === "c") copyRole(current);
  else if (k === "m"){ markSent(current, "LinkedIn"); advanceAfter(current); }
  else if (k === "s"){ state[current].skipped = today(); save(); advanceAfter(current); }
  else if (k === "d"){ selected = current; renderDrawer(); }
  else if (k === "j" || e.key === "ArrowDown") { e.preventDefault(); move(1); }
  else if (k === "k" || e.key === "ArrowUp")   { e.preventDefault(); move(-1); }
  else if (k === "l"){ window.open(linkedinSearch(COMPANIES.find(x=>x.id===current)), "_blank", "noopener"); }
  else if (k === "o"){ const u = reqUrl(COMPANIES.find(x=>x.id===current)); if (u) window.open(u, "_blank", "noopener"); }
});

function flash(){ const s = $("#saved"); if (!s) return; s.classList.add("on");
  clearTimeout(flash.t); flash.t = setTimeout(()=>s.classList.remove("on"), 1100); }

render();
