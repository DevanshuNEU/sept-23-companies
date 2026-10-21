# Opportunity pipeline: design

Date: 2026-10-20. Owner: Devanshu Chicholikar. Status: draft for Devanshu's approval.

This document designs how the platform finds every company Devanshu could reach, reads their postings, ranks
them, and plans each day's outreach. It builds on `2026-10-18-outreach-platform-design.md` (store, rules,
sending, MCP, app) and changes some decisions in issue #9; section 13 lists each change. Until Devanshu approves
this document and #9 is edited to match, #9 stands.

Research behind it (all in `docs/research/`, dated 2026-10-20):

| Report | What it established |
|---|---|
| `outreach-history.md` | What his recorded outreach (129 first touches, April to September) says about who replies |
| `lca-small-junior-employers.md` | Which small employers filed junior software H-1Bs in FY2025 (DOL LCA data) |
| `visa-rules-and-employer-targeting.md` | OPT, STEM OPT, the weighted lottery and fee rules as of October 2026 |
| `company-discovery-and-job-data.md` | Which job boards small sponsors use, how to find and diff them, free and paid sources |
| `llm-posting-extraction.md` | Reading postings with a model: features, cost, accuracy evidence, schema |
| `people-and-channels.md` | Referrals, who to contact, LinkedIn rules, finding emails safely |

## 1. Why

Devanshu graduated in May 2026 (MS, Northeastern), is on F-1 OPT until July 8, 2027, and can extend with STEM
OPT through 2029. He has reached far more companies than are recorded, with few replies. Employers' fear of OPT
and H-1B is the biggest blocker.

The worst failure of this system is **hiding a company he could have reached**. Time spent on a company that
turns out to be a no is cheap. In his words: "I wanna sell myself as much as possible."

What the evidence says the current approach gets wrong:

1. **The list misses most of the right companies.** 2,902 small software employers filed junior (wage level I or
   II) H-1Bs in FY2025 after removing staffing firms and likely cap-exempt employers. 2,805 of them are not in
   his radar, dashboard or contacted list (`lca-small-junior-employers.md`).
2. **The crawler reads only three job-board systems.** Of 40 sampled small junior filers, 15% use Greenhouse,
   Lever or Ashby, 32.5% have some machine-readable board, and 52.5% have none (`company-discovery-and-job-data.md`
   section 1).
3. **Most postings have no text to read.** 29% of stored postings have a body of 500 characters or more; the
   radar never asked Greenhouse for content, and Workday and Oracle need a second request per posting.
4. **Phrase lists misread postings.** On 8,000 real postings, 12 of 179 "will sponsor" and 6 of 31 student-only
   readings were wrong (#4 whole-branch review).
5. **Outreach went to the wrong people about nothing specific.** Recruiters: 43 emails, 0 positive replies.
   Emails naming a live role: 9 replies of 77; without one: 0 of 38. Follow-ups: 0 of 10. Since August every good
   outcome came from a LinkedIn message to an engineer or manager about a live role (`outreach-history.md`).
6. **Outcomes are not recorded,** so nothing can learn what works.

## 2. Goals, non-goals, anti-goals

**Goals**
- Every company that could hire him is in the store, from more than one kind of source, each fact with its source.
- Each board is read only when it is likely to have changed, and only changed postings are read further.
- Every posting is read accurately, and the accuracy is measured on his own labeled postings.
- Each day he gets a short, specific plan: about 7 people to request on LinkedIn, the applications that go with
  them, and the emails the send limit allows, each with a reason.
- Every touch and outcome is recorded, so the ranking can be checked against real replies every month.
- Every part can be replaced or extended without touching the others (section 4).

**Non-goals**
- Applying to jobs automatically.
- Anything automated on LinkedIn: no scraping, no extensions, no sending. LinkedIn's User Agreement section 8.2
  bans them (`people-and-channels.md` section 3).
- Paid data services for now. They are a plug-in later if measured gaps justify them (section 5.1).
- The Claude API. Reading runs through Devanshu's Claude Code seat (section 7).

**Anti-goals (failure even if it works)**
- A company hidden on a guess. Only the hide list in section 8 hides, and only with evidence.
- A ranking nobody can explain. Every score shows the signals behind it.
- Re-crawling unchanged boards, or re-reading unchanged postings.
- A request that breaks a site's terms or hammers a server.
- A confident fact with no quote behind it.

## 3. Constraints

- Runs on his Mac, one SQLite store (`~/Library/Application Support/outreach/outreach.db`), launchd jobs. The
  Mac sleeps; every job must be safe to miss and resume.
- Python 3.14, the `outreach/` package, the #4 store and rules as they are on the #4 branch (migrations
  `0001_initial.sql`; this design adds `0002`).
- Claude never approves or sends; approval is Devanshu's click, sending is the sender's job.
- Claude Code usage has session limits (hit twice on 2026-10-20). Nothing may depend on Claude being available.
- LinkedIn: Premium, about 200 connection requests a month. At least 150 go to people this system picks; he
  spends the rest on people he finds himself.
- Sending: one Gmail account, the ramp and stop rules in #9.
- Visa facts that shape ranking (`visa-rules-and-employer-targeting.md`, this is not legal advice):
  - STEM OPT needs an employer enrolled in E-Verify, a signed I-983 and a direct employer relationship; he needs
    such an employer by July 8, 2027.
  - The weighted lottery has been in force since March 2026: level I gets 1 entry, level IV gets 4; DHS estimated
    about 15% odds at level I and 31% at level II.
  - Cap-exempt employers (universities, affiliated nonprofits, nonprofit research organizations) skip the lottery
    and the proposed $103,265 fee (91 FR 54817, not final).
  - Proposed OPT fees (91 FR 64566, comments close 2026-11-09) could affect his STEM OPT extension. Ranking
    weights must be easy to change if rules change.

## 4. Shape

Seven stages. Each reads from the store and writes to it, is idempotent, records a `job_run`, carries a version,
and can be rerun alone.

```
 sources ──> identity ──> board finder ──> diff reader ──> posting reader ──> rules ──> ranking ──> daily plan
  (plug-ins)   (one company     (ATS adapter or      (only what         (Claude Code     (#4)     (config     (people,
               per domain)       careers page)        changed)           queue, quotes)            weights)    channels)
                                                                                                      ^
                                                                         outcomes (every touch) ─────┘
```

Contracts, so any part can be swapped:

| Stage | Contract | Swappable unit |
|---|---|---|
| Source | yields company candidates: name, domain if known, identifiers, evidence rows, source ref | one module per source in `outreach/sources/` |
| Identity | maps a candidate to one company or raises a Needs-you merge item | match rules in config |
| Board finder | yields boards (ATS + token) or a careers page URL per company | one adapter per ATS in `outreach/crawl/ats/` |
| Diff reader | `list(board, validators) -> ok / empty / error / unchanged` plus job ids; `body(job)` | the same adapters |
| Posting reader | takes a posting body, returns facts with quotes; the store verifies | any reader: headless Claude Code, the side session, rules only |
| Rules | `decide()` from #4, now from reader facts plus phrase rules | ruleset version |
| Ranking | signal functions over company facts, weights in `outreach/ranking/config.toml` | one function per signal |
| Daily plan | picks people and channels within capacity | capacity config |

Every derived row stores the version that produced it (source version, reader version, ruleset version, ranker
version). A version change reruns only over stored data. It never triggers a crawl.

## 5. Finding companies

### 5.1 Sources

Each source is a module with `name`, `version`, a schedule and `discover(since)`. A new source is one new module
and one config entry. Day-one sources, in build order:

1. **DOL LCA disclosure files** (quarterly, xlsx). Yields employers with FEIN, legal name, DBA, city, state,
   NAICS, the contact email domain (`EMPLOYER_POC_EMAIL`, a domain for 94% of the 2,902), software LCA counts by
   wage level, new-employment counts, and the H-1B dependent, willful violator and secondary-entity flags. Writes
   `lca` evidence per fiscal year. The staffing filter from `lca-small-junior-employers.md` becomes a scored
   `staffing_risk` fact, never a silent drop. Cap-exempt employers (487 in the small list) are kept and tagged.
   Download needs browser-like headers (DOL returns 403 to bare clients).
   Which employers become companies (my call): all 18,273 small junior filers are stored, because a stored
   company costs nothing. Work is spent in tiers. The 2,902 product employers and the 487 cap-exempt ones get
   board finding and watched pages first. The rest of the 11,492 follow as capacity allows. The 6,781
   staffing-flagged employers are stored with `staffing_risk` and ranked at the bottom. They get no board
   finding unless he promotes one. Large filers (over 25 LCAs) are not imported from this source.
2. **stapply company lists** (free, daily, 80,390 companies across 49 ATSs). Yields ATS boards by company name;
   matched 521 of the 2,902 by exact name, 459 of them untracked.
3. **Today's boards** (radar's 494, the dashboard's 1,024 companies) via the #17 import.
4. **SEC EDGAR Form D** (bulk and full-text search, declared User-Agent, at most 10 requests a second). Yields
   "raised money", date and amount. Matched to companies by FEIN (`ein` in the SEC submissions JSON equals the
   LCA FEIN) or exact issuer name.
5. **HN "Who is hiring"** (official HN API and Algolia search). Yields companies and posts; only 9 of 266 October
   posts mention early-career roles, so it is a small source.
6. **Common Crawl index** for ATS hosts (found 283 extra Ashby boards from half of one host). Flaky; runs monthly
   and resumes.
7. **By hand**: Devanshu or the side session adds a company with a reason.

Not used: YC directory and Wellfound scraping (their terms forbid it; only 104 of the 2,902 are YC anyway), paid
aggregators (TheirStack from $49 a month, Fantastic.jobs from $95 a month) until a measured gap justifies one.

### 5.2 Identity

One company per registrable domain. Aliases join everything else: legal names, DBA, FEIN, ATS tokens, Form D
issuer names, LinkedIn company slug. The research found exact name matches about 95% right, matches after
dropping words like "AI" or "Technologies" about 45%, and fuzzy matches 0 of 10. So:

- Merge automatically only on the same domain, the same FEIN, or an exact normalized name plus the same state.
- Never merge automatically on a FEIN or domain that is shared by more than one LCA employer (payroll and
  employer-of-record providers such as TriNet, Deel and Justworks; a parent's email domain used by its
  subsidiaries, as with Seqirus and `cslbehring.com`). Those keys are recorded as shared and never used to merge.
- Anything weaker raises a Needs-you merge item with both records side by side. He confirms with one key.
- A subsidiary is its own company for outreach. It never inherits a parent's size evidence, so a parent's "over
  300" cannot hide it.
- A merge is an event and can be undone (the store's `merged_into`).

### 5.4 Size and stage

None of the day-one sources gives headcount, and Form D gives money raised but not the round. So (my call):

- **Unknown size is shown and scored neutral.** It never counts as small and never hides. The over-300 hide still
  needs evidence, as in #9.
- **Weak "not small" flags** (a known large parent, many board postings, a domain shared with a large filer) flag
  the company for a size check. They never hide it.
- **Size is filled where it matters:** for companies that reach the top of the ranking, the side session looks for
  size and stage on public pages and records them as evidence with a source. Devanshu can enter the headcount
  from a LinkedIn company page with one field. Both are evidence rows like any other.
- Existing size evidence (the old dashboard, #17 import) carries over.

### 5.3 Finding the board

For each company with a domain and no board:

1. stapply's lists by name and domain.
2. Probe likely slugs on the ATSs we support (one request each, polite).
3. Fetch the careers page once and look for ATS embeds.
4. If nothing machine-readable exists, store the careers page URL as a **watched page** (section 6.3).

New ATS adapters are added in order of how many target companies use them. The order comes from the measured
mix, not a guess.

## 6. Reading only what changed

### 6.1 Board reads

Every adapter returns `ok`, `empty`, `error` or the new **`unchanged`**. The #3 and #21 rules stay: error never
closes, empty closes only after two in a row, three errors raise a Needs-you item. `unchanged` counts as ok and
touches nothing.

- **Conditional requests** where the ATS supports them: Greenhouse, Lever, Ashby, SmartRecruiters, Breezy,
  Pinpoint and Teamtailor return 304 to a stored ETag.
- **Elsewhere, a fingerprint of the sorted job ids** (not the raw body: Recruitee and JazzHR bodies change on
  every request).
- **Bodies are fetched once per new or changed posting**, and only for engineering titles: Greenhouse with
  `?content=true`, Workday and Oracle with their one detail request. Stored once in `posting_body` with a hash;
  a changed hash queues a re-read.

### 6.2 Cadence

Each board gets a `next_read_at`. The interval grows with the time since its last new posting and shrinks when
it changes, between a floor and a ceiling in config (my starting values: 6 hours and 7 days). Half of the 499
measured boards add a posting less than once every two weeks, so most boards settle near the ceiling. Errors
back off. A board whose company is in today's plan is read before the plan is built.

One scheduler tick (launchd, every 30 minutes) reads whatever is due, with a per-host rate limit and a declared
User-Agent. A sleeping Mac just means the next tick finds more due.

### 6.3 Watched pages

Companies with no readable board (about half the small sponsors) get their careers page fetched weekly. The
fingerprint is the page text with scripts and dates stripped. A change raises "careers page changed" on the
company, with a link, so he or the side session can look. These companies stay rankable on their other signals.

## 7. Reading postings

### 7.1 Facts with quotes

A model reads each new engineering posting once and returns, per field, a value, a verbatim quote, and
"unknown" when the text says nothing. Fields:

- engineering role or not; level (intern, new grad, early career, mid, senior and up); years of experience asked
  (low and high)
- sponsorship stance (won't sponsor, will sponsor, OPT only, silent)
- US citizenship or clearance required; current student or graduation window required
- start date; location and remote rule; pay range if stated

Structured output guarantees the shape, not the truth. Anthropic's citations feature cannot be combined with
structured output, so **code checks every quote**: it must appear in the stored body after normalizing
whitespace and case, or the answer is refused. A refused or missing answer leaves the posting on the phrase rules.

### 7.2 Who reads: the queue

No API. Reading runs through Devanshu's Claude Code seat:

- New bodies go into a **read queue** in the store (posting, body hash, reader version, state, attempts).
- `python -m outreach.reader next --limit 20` hands out a batch as JSON. `python -m outreach.reader submit`
  takes the answers, checks schema and quotes, and saves what passes. The MCP server (#13) exposes the same two
  tools.
- **At night**, a launchd job runs `claude -p` with a pinned prompt file and the model set in config (starting
  choice: Haiku), working the queue in batches until it is empty or the session limit is hit. About 350 new
  engineering postings a day comes to roughly 18 batches.
- **Side by side**, he can say "read the queue" in a Claude session and it uses the same tools.
- A session limit, an expired login or a sleeping Mac only delays reading. The queue persists, and nothing waits on
  it.

The reader version is the prompt file hash plus the model name plus the schema version. Changing any of them
queues a re-read of stored bodies, never a crawl.

### 7.3 Reader and rules together

The #4 phrase rules keep running on every posting. Per field:

- Reader and rules agree: the fact stands.
- They disagree, or the reader is missing or refused: the posting is **shown and flagged**. A disagreement that
  would hide a posting goes to Needs you.
- A hide needs the reader's verified quote **and** the rules to agree, until the labeled set proves the reader
  alone is good enough (section 7.4).
- **Before the reader gets to a posting** (a skipped night, the backlog), the rules decide alone, as the #4
  rules do today. A rules-only hide is marked provisional, and those postings are read first.
- When the reader then disagrees with a provisional hide, the posting is shown again and a Needs-you item is
  raised. When it agrees, the hide becomes final.
- **Queue order:** provisional hides first, then lane 1 postings at the top-ranked companies, then everything
  else, newest first.
- **The backlog** of about 12,000 stored engineering postings with real bodies is read in that order over a few
  weeks, inside the night job's cap in config.

### 7.4 Measuring accuracy

A labeled set from his own postings in `tests/gold/`, sampled by field and class so rare classes (will sponsor,
citizenship, student-only) have enough examples. To show a field's error rate is at most 2% with 95%
confidence takes about 150 to 190 examples of that class with no errors. Claude pre-labels; Devanshu confirms in
sittings of about 30 minutes. `python -m outreach.reader eval` reports precision and recall per field for the
reader, the rules and both together. A new reader version must not be worse on the set before it becomes
current.

## 8. Deciding: hide, show, flag

**Hidden (unchanged from #9, with evidence only):**
- the posting says the company won't sponsor;
- US citizenship or clearance required;
- requires being a current student or graduating in 2027 or later;
- the company has over 300 people **with evidence**.

**Shown and flagged, never hidden:**
- size unknown or a guess; sponsorship silent or unclear; reader and rules disagree;
- staffing risk (STEM OPT cannot be a third-party placement), shown with the reason;
- 2027 start dates; OPT-only; PhD-only.

**Lanes (new):** a company is shown if any lane applies, with the strongest lane first.
1. **Role at his level:** new grad, early career, SWE I and II, MTS, FDE, up to about 3 years asked.
2. **Intern or junior work** he can do on OPT (not student-only).
3. **No role yet, but a reason to reach out:** money raised (Form D or a known round), junior H-1B filings, or a
   watched careers page.
4. **Any other open US engineering role** at a small company.

Today `passes()` drops interns; lane 2 changes that.

## 9. Ranking

A score is a weighted sum of named signals. Each signal is one function that returns a value and a one-line
reason. Weights live in `outreach/ranking/config.toml`. The company page shows every signal and its share of the
score.

Starting signals, strongest first (weights are my calls, to be checked against outcomes):

| Signal | Why | Source |
|---|---|---|
| Live role at his level, by freshness | 9 of 77 replies with a named role, 0 of 38 without | postings |
| E-Verify enrolled | STEM OPT needs it by July 2027 | evidence `everify` |
| Cap-exempt | No lottery, no proposed fee | LCA tag, hand |
| Pays at level II or above for the area | Twice the lottery odds of level I | posted pay against OFLC wage tables, or the employer's LCA wage history |
| Junior H-1B filings in the last two fiscal years | They have done it before | LCA evidence |
| Small: under about 250 people, seed to Series B | All email-led calls came from seed and Series B; Series C to E: 0 of 34 | size and stage evidence |
| Reachable person on the team | Referrals are 6% of applicants and 29% of hires in one study | person records |
| Money raised in the last 18 months | Funded companies hire | Form D |
| Staffing risk (negative) | Cannot employ him on STEM OPT for placements | LCA flags |

Large later-stage names drop to the bottom and are never hidden. Every month the outcome report (section 11)
shows, for each signal bucket, touches and replies with counts and a warning when the sample is too small. He
decides whether weights change. A weight change is a config commit with a new ranker version.

## 10. The daily plan

At 08:00 on weekdays, after the due boards for top companies are read, Today shows:

- **LinkedIn: about 7 people.** Capacity comes from config (150 a month), minus requests already logged this
  month. One person per company at a time. Preference order: engineer or manager on the team for the role, then
  someone with shared context (Northeastern, past employer, same stack), then a founder at a very small company,
  then a recruiter. About 80% of requests go to lane 1 companies and about 20% to lane 3 as long bets (my call).
- **For each LinkedIn person:**
  - the profile link;
  - the role link;
  - one specific reason (what their team shipped and what he built that fits);
  - a note under LinkedIn's limit, drafted in the side session;
  - an **apply today** link to the role, so his application is in their system when the person answers.
- **Email: what the send limit allows,** for:
  - founders who publish an address;
  - HN posts that ask for email;
  - companies with no good LinkedIn person.

  Only to addresses that are published, verified by a provider, or follow a pattern already proven at that
  company. Never to GitHub commit emails, whose use for unsolicited mail GitHub's terms forbid.
- **Watch list:** careers pages that changed, new lane 3 companies, Needs-you merges.

No answer in 14 days on LinkedIn moves on: the next request at that company goes to a different person, and
only if the company is still near the top.

People come from:
- what he pastes;
- what the side session finds on public pages (team pages, blogs, GitHub orgs, HN posts);
- LinkedIn search links the system builds for him to open by hand.

The system never reads LinkedIn itself.

**How the morning gets its people (my call):**
- Today is keyed on companies. Each row has person slots.
- A **prepare** step runs after the night reader: a headless Claude Code run takes the top companies without a
  person (about twice the day's LinkedIn capacity). For each, it looks on public pages for one or two candidates,
  records each with the page it came from, and drafts the note.
- A row with no candidate shows **find someone**, with the LinkedIn search link. He pastes a profile URL and
  name, and the side session drafts on demand.
- This needs MCP tools for adding people with a source link, saving a LinkedIn draft, and reading the prepared
  rows. The builder extends #13's tool list.

## 11. Outcomes

Every touch is a row, for every channel: LinkedIn request, LinkedIn message, email, application, HN reply,
other. Each row records the person's role kind, the company's lane and signals at the time, how old the role
was, and dated outcomes: sent, accepted, replied (with the #9 reply tags), referred, interview, offer, quiet.

- Email outcomes come from the sender and reply reader.
- LinkedIn and application outcomes are one click each in the app. The system asks about any touch older than 7
  days with no outcome.
- The 50 requests a month he sends on his own can be logged the same way, so they count.
- Monthly report: reply rate by signal, lane, role kind and channel, with counts. It feeds section 9.

## 12. Store changes (migration 0002)

The #4 store already has company, company_alias, board, posting, posting_body, evidence, decision, person,
touch, linkedin_log, review_item, daily_list and job_run. Additions (names are the builder's to settle):

- `source` and `company_source`: which source produced each company, its version, when it was first and last seen.
- Evidence facts: `lca` (per fiscal year counts by level), `cap_exempt`, `staffing_risk`, `funding`
  (Form D), `headcount`. Source kinds: `stapply`, `commoncrawl`, `hn`.
- Alias kinds: `fein`, `dba`, `legal_name`, `ats`.
- `board`: more ATS values, `etag`, `ids_fingerprint`, `next_read_at`, `change_interval`; the read result
  `unchanged`.
- `watched_page`: URL, fingerprint, last change.
- `read_queue` and `posting_reading`: per posting body hash and reader version, the fields with quotes, the
  verification result.
- `application`: role, applied date, outcome.
- A general `touch` for every channel, or channel tables beside the email `touch`; the builder decides,
  keeping the email state machine intact.
- `ranker_version` and a stored score per company per version, so a change shows what moved.

## 13. Changes to issue #9

Each needs Devanshu's yes, then #9 is edited before any plan uses it.

| #9 today | Proposed | Evidence |
|---|---|---|
| Email is the main channel; the best 6 to 8 people per company | LinkedIn plus an application is the main channel (about 7 people a day); email goes to 1 or 2 people per company, for the cases in section 10 | `outreach-history.md`: LinkedIn produced every good outcome since August |
| 3 follow-ups, at 3, 7 and 15 business days | 1 follow-up at about 7 business days that adds something new; configurable | 0 replies from 10 follow-ups (small sample; revisit with outcome data) |
| Recruiters in scope on day 2 | Recruiters only when no engineer, manager or founder is found; ranked last | 43 recruiter emails, 0 positive |
| Daily list proposes 15 to 20 companies | Today is a plan of people across channels, sized by LinkedIn and send capacity | Capacity is people, not companies |
| Order: role at his level, then any US engineering role | Lanes in section 8 and the signals in section 9 | Sections 8 and 9 |
| E-Verify recorded, not ranked | E-Verify ranked high, never hidden | STEM OPT requirement |
| Interns dropped | Intern and junior work is a lane | His decision, 2026-10-20 |
| Discovery: the existing scripts in #21 | Source modules (section 5) | `company-discovery-and-job-data.md` |

## 14. Build order

Value first. Each step is shippable and useful alone.

1. **More companies, real text, fewer requests.** The LCA source with identity and the Needs-you merge item;
   stapply board matching; Greenhouse content and the Workday and Oracle detail requests; `unchanged`, ETags and
   id fingerprints; cadence. This folds into #21.
2. **The reader.** Queue, `next` and `submit`, quote checks, the night job, MCP tools, the labeled set and
   `eval`. Lanes and the reader plus rules combination in #4's rules and #6.
3. **Ranking and the daily plan.** Signals and config, the company page breakdown, Today as a people plan,
   `apply today`, LinkedIn drafts (#6, #15, #12).
4. **Outcomes.** Touch rows for every channel, one-click logging, the 7-day prompt, the monthly report.
5. **More sources.** Form D, watched careers pages, HN, Common Crawl, new ATS adapters in measured order,
   E-Verify checks for top companies.

**Interim, before the platform switch (needs his yes; a throwaway spike):** a one-time ranked list of the 2,902
plus the 487 cap-exempt employers, with domains, LCA signals and any stapply board, so he can start spending
LinkedIn requests this week.

## 15. Left to the builder

- Table and column names in migration 0002, and whether channel touches share one table.
- The exact cadence formula within the floor and ceiling.
- Quote normalization details, as long as a paraphrase never passes.
- The prompt text, as long as it is a pinned file and every change bumps the reader version.
- Adapter internals and per-host rate limits, within the research's limits.
- How the night job detects a session limit and stops cleanly.

## 16. Calls I made (Devanshu to check)

- Cadence floor 6 hours, ceiling 7 days.
- LinkedIn split about 80% lane 1, 20% lane 3.
- No answer in 14 days moves to another person.
- Starting signal order and weights in section 9.
- Haiku as the night reader's model.
- Cap-exempt employers (universities, research nonprofits) shown as a full lane, since he wants every reachable
  job.
- Paid data stays out until a measured gap justifies it.
- Which LCA employers are stored and in which order work is spent on them (section 5.1).
- Shared FEINs and domains never merge automatically; subsidiaries are their own companies (section 5.2).
- Unknown size is shown and scored neutral; size is looked up for top companies (section 5.4).
- Rules-only hides are provisional and read first; the queue order (section 7.3).
- Today is keyed on companies, with a night prepare step that finds people and drafts notes (section 10).
- `message.seq` can keep allowing 0 to 3; a one-follow-up policy only uses fewer.

## 17. Open questions for Devanshu

1. **When:** does this reshape the switch-over (the platform switches later, but switches to this), or does the
   switch-over go ahead as planned with this work right after it? Either way, the interim list in section 14
   comes first. This also decides whether the #7 items this design needs (LCA wage levels, Form D, HN) leave
   the parked list now.
2. **Facts in LinkedIn notes:** today drafts may use only `mail/me.md` and what he supplies about the person.
   May a note also use facts the side session found on public pages, each shown to him with its link before he
   sends? Notes also need their own checks: length, and his voice, since two past contacts said his messages
   read as AI-written.
3. **Section 13:** which #9 changes he accepts.
