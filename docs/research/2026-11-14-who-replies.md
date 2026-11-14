> **Evidence only.** Decisions live in issue #9; where this file disagrees, #9 wins. Nothing here is built. Section 8
> is a proposal for the Reach out design (`superpowers/specs/2026-11-14-reach-out-design.md`) and decides nothing on
> its own. Follow-up counts and timing stay as #9 sets them; this file only adds the order and spacing between
> different people at one company.

# Who to email for each job, and how to find them in Apollo (research, 2026-11-14)

Read 2026-11-14. Builds on `docs/research/2026-10-20-people-and-channels.md` (referral studies, posting timing) and
does not repeat it beyond what the rules need.

**How to read the labels.** Every claim carries a source number from the list at the end ([S1] and so on) and one
of these labels:

| Label | Means | Weight |
|---|---|---|
| PRIMARY | Peer-reviewed or working paper with methods, or the vendor's own API docs | Highest |
| VENDOR | A company's data from its own platform, large n, methods partly public | High for direction, low for exact numbers |
| HIS DATA | Knock's history as given in the brief: 522 cold emails, 21 human replies | On point but small |
| PRACTITIONER | A named recruiter, founder or engineer writing about what worked for them | Anecdote with a name |
| FORUM | Blind, Hacker News, X, Q&A sites; anonymous or unverified | Lowest |
| *inference* | My reasoning from the sources, not a finding | Treat as opinion |

**Direction warning.** Most large reply-rate datasets (Belkins, Hunter, Sales.co, Lavender) measure sellers emailing
buyers, not job seekers emailing employees. I use them only for direction (smaller companies reply more, executives
at big companies reply less), never for exact rates.

**What I could not read.** Reddit refuses this agent's fetches and its search tool rejects reddit.com, so no Reddit
thread is cited. X posts are cited only from search snippets. LinkedIn was not fetched, by instruction; LinkedIn
figures below come from other sites quoting LinkedIn and are marked so.

## 0. Short answer

1. No study ranks founders, managers, engineers and recruiters by replies to a job seeker's cold email. The one
   small survey found (n = 24) rates early career engineers and grad students most receptive (7.5 of 10) and managers
   least (4.5) [S23, SURVEY].
2. His role numbers do not separate the roles. Engineers 2.9%, founders 4.1%, managers 4.9%, recruiters 6.2%, other
   3.9%; every 95% interval overlaps, and a chi-square test gives p = 0.82 [S1, my arithmetic]. Company size and
   company type separate his outcomes better than role: both interviews came from AI startups of 50 and 150 to 300
   people [S1].
3. Size is the strongest signal with data behind it. Small companies reply 2 to 3 times as often as the largest in
   every seller dataset: Belkins 0.72% at 0 to 10 employees versus 0.22% at 10,000+ [S2], Sales.co 2.40% at 1 to 10
   versus 1.37% at 5,000+ [S4] (VENDOR, seller direction).
4. Founders lead on replies at small companies. Belkins: founders and owners 0.57%, C-level 0.42%, VPs 0.32% [S2].
   YC: founders sent 53% of first messages on Work at a Startup and 25% of hires started with the candidate [S8].
   Practitioners agree: "At smaller companies (<30 people), CEO's or CTO's work well" [S20].
5. At big companies the right first person is a recruiter tied to the role: university or campus recruiters for new
   grad roles, technical recruiters otherwise. Nick Singh got Airbnb, Snapchat and Cloudflare interviews from
   recruiter cold emails and had "the most luck" with campus recruiters [S20, PRACTITIONER]. Big-company managers on
   Blind tell candidates to stop emailing them [S26, S27, FORUM].
6. A stranger's referral is weak at big companies. Meta's form asks how well the referrer knows you; a "never worked
   together" referral is "the same quality as a cold application" [S24, FORUM, named engineers]. At small companies a
   referral stands out more: referrals are 0.57% of applications at 1 to 249 employees but about 16% of hires [S6,
   VENDOR].
7. Location: no study measures whether same-city recipients reply more. Indirect evidence says referrals run inside
   the same division (73% of referrals at one firm) [S9, PRIMARY] and through local ties [S16, PRIMARY]. Use city as a
   filter at companies over 1,000 (offices are separate teams) and as a tie-breaker below that. This is *inference*.
8. Shared ties help in hiring, but Knock cannot see most of them in Apollo. Alumni of the same team raise hiring odds
   by 194% in Ivy League data [S12, PRIMARY]; moderately weak ties move the most jobs [S11, PRIMARY]. Apollo's people
   search has no school filter, and my test keyword "Northeastern University" matched 0 of 1,823 Amazon people in
   Boston [S34].
9. Count and order: email 1 or 2 people at a company first, not 3 or more at once. Hunter: 5.1% reply when contacting
   one or two people per company versus 3.5% for three or more [S3, VENDOR]. Then add people one at a time: Dalton's
   3B7 routine contacts a second person after 3 business days of silence [S29, PRACTITIONER].
10. Never: executives at companies over 200, anyone outside the US for a US role, agency, RPO or contract recruiters,
    GTM or sales recruiters, interns and students, people whose record is stale or who left (section 7).

## 1. What his own data can and cannot say

### 1.1 Replies by role, with 95% intervals

| Role | Emailed | Human replies | Rate | 95% interval (Wilson) |
|---|---|---|---|---|
| Engineers | 175 | 5 | 2.9% | 1.2% to 6.5% |
| Founders | 74 | 3 | 4.1% | 1.4% to 11.3% |
| Managers | 81 | 4 | 4.9% | 1.9% to 12.0% |
| Recruiters | 65 | 4 | 6.2% | 2.4% to 14.8% |
| Other | 127 | 5 | 3.9% | 1.7% to 8.9% |
| All | 522 | 21 | 4.0% | |

Source: [S1]; intervals and test are my arithmetic (script in `~/Desktop/outreach-scratch/who-replies/scripts/stats.py`).

- The chi-square test across the five roles gives 1.56 on 4 degrees of freedom, p = 0.82. The data cannot tell the
  roles apart.
- To tell a 3% role from a 6% role with the usual 80% power needs about 750 emails per role. To tell 3% from 9% needs
  about 245 per role. At 200 more emails, no role comparison will be settled (my arithmetic).
- The sender confound is large. Northeastern address: about 10 replies in 214 (4.7%, interval 2.6% to 8.4%). Gmail:
  about 1 in 355 (0.3%, interval 0.05% to 1.6%), with known deliverability problems [S1]. Any role table that mixes
  accounts measures the account as much as the role.

### 1.2 Where the positive replies came from

- Interviews: a 150 to 300 person AI startup (co-founder and CEO, founding head of data science and a technical
  recruiter all engaged) and a 50 person AI startup (a member of technical staff) [S1].
- Other positives: a CEO and co-founder at a small bio AI startup, a forward deployed tech lead at a consultancy, a
  senior sales engineering director at Docker, a partner manager at a large IT firm [S1].
- Said no: a co-founder CEO, a recruiter at a 500+ company, two engineers [S1].
- Five of the eight positive people work at AI startups under 300. Two of the eight are outside engineering and
  recruiting ("other" replies at 3.9%, the same as the rest). *Inference:* the company's size and stage predicted a
  good outcome better than the person's role did.
- His belief that startup CEOs and founding engineers reply most fits the startup positives. It is not yet shown in
  the role table: founders replied at 4.1%, inside every other role's interval.

## 2. Who replies and who helps, by company size and role

Columns. **Replies** is how likely a reply to a short, specific cold email is. **Helps** is how likely the reply
turns into a referral, a forward to the hiring manager, or an interview. **Annoyed** is the risk of a bad reaction
that hurts him at that company. Ratings are High, Medium, Low, or Rare (the role usually does not exist at that size).
Where a cell rests only on opinion it says so.

### 2.1 Under 50 employees

| Role | Replies | Helps | Annoyed | Evidence |
|---|---|---|---|---|
| CEO or founder | High | High | Low | Founders reply most in Belkins (0.57%) and small companies most (0.72% at 0 to 10) [S2, VENDOR]. Founders send 53% of first messages on YC's platform [S8, VENDOR]. "At smaller companies (<30 people), CEO's or CTO's work well" [S20]; "If you are in doubt ... email the CEO" [S21]; "The CEO is the one with the power to give you the job" [S22] (PRACTITIONER). Founder-CEOs post their own address in HN "Who is hiring" threads [S38, FORUM]. His data: 2 of 8 positives were small-startup CEOs, 1 CEO said no [S1]. |
| CTO | High | High | Low | Named with the CEO for under 30 [S20, PRACTITIONER]. No data for CTOs alone. Opinion that a CTO is the hiring manager for engineers at this size. |
| Founding engineer or member of technical staff | Medium to High | Medium to High | Low | His data: a member of technical staff at a 50 person AI startup led to an interview [S1]. At this size every engineer can walk the email to the founder (*inference*). |
| Engineering manager | Rare | High when present | Low | Few companies under 50 have one; when present it is the hiring manager (*inference*). |
| Senior or staff engineer | Medium | Medium | Low | Opinion only. Lavender's seller data has individual contributors replying most among technical buyers (5.3%) [S5, VENDOR, seller direction]. |
| Recent grad engineer, 0 to 3 years | Medium | Low to Medium | Low | Early career people rated most receptive, 7.5 of 10, n = 24 [S23, SURVEY]. Less pull with the founder (*inference*). |
| Technical recruiter | Rare | High when present | Low | First in-house recruiter usually arrives at 20 to 50 employees or Series A [S31, PRACTITIONER]. Agencies make 4.99% of hires at 1 to 249 employees [S6, VENDOR]; an agency recruiter is not the employer (section 7). |
| University or campus recruiter | Rare | n/a | n/a | No campus programs at this size (*inference*). |
| Talent acquisition partner or lead | Rare | High when present | Low | Same as technical recruiter: usually the only recruiter (*inference*). |
| Hiring manager named on the posting | High | High | Low | At this size it is usually a founder or the CTO (*inference*). |

### 2.2 50 to 200 employees

| Role | Replies | Helps | Annoyed | Evidence |
|---|---|---|---|---|
| CEO or founder | Medium | Medium to High | Low to Medium | His interview at a 150 to 300 person startup came after the CEO, a founding leader and the recruiter all engaged [S1]. Belkins shows replies fall with size [S2]. Ben Lang's guide still says CEO or hiring manager [S21, PRACTITIONER]. |
| CTO or VP engineering | Medium | High | Low to Medium | Opinion. Owns engineering hiring; may forward to a manager. |
| Founding or early engineer | Medium | Medium | Low | His data: a founding head of data science engaged at the 150 to 300 startup [S1]. |
| Engineering manager | Medium | High | Low | His managers replied 4.9% [S1]. Referrers sit at the same or a higher level than the person referred and 73.2% of referrals stay in the same division [S9, PRIMARY]: the manager of the team is the closest match. |
| Senior or staff engineer | Medium | Medium | Low | Opinion. A team referral counts; Ashby shows referred applications reach interview at 40% versus 3% inbound [S7, VENDOR]; at a large high-tech firm referred applicants were hired at about 6 times the rate, with similar skills [S10, PRIMARY]. |
| Recent grad engineer | Medium | Low to Medium | Low | [S23, SURVEY]; less pull (*inference*). |
| Technical recruiter | High | High | Low | His recruiters replied most (6.2%) and the 150 to 300 interview involved the technical recruiter [S1]. Recruiters at companies under 250 saw 4.4 times more applications per recruiter in 2024 than in 2021 [S6, VENDOR], so short and specific matters. |
| University or campus recruiter | Rare | n/a | n/a | *Inference.* |
| Talent acquisition lead | Medium to High | High | Low | Often the head of a 1 to 3 person team (*inference*). |
| Hiring manager named on the posting | High | High | Low | Opinion; the posting names the decider. |

### 2.3 200 to 1,000 employees

| Role | Replies | Helps | Annoyed | Evidence |
|---|---|---|---|---|
| CEO or founder | Low | Low | Medium | Replies fall with size and seniority [S2, S4, VENDOR]. Gatekeeping and forwarding chains (*inference*). |
| CTO or VP engineering | Low | Medium | Medium | Same. Lavender has VPs lowest among technical buyers (3.4%) [S5, VENDOR, seller direction]. |
| Founding engineer | Low to Medium | Low to Medium | Low | Often now a principal or a manager with other duties (*inference*). |
| Engineering manager | Medium | High | Medium | The decider. Some managers want candidates to go through recruiting [S27, FORUM]. |
| Senior or staff engineer | Medium | Medium | Low to Medium | "It's a number game ... [you are] not the only one being asked" [S25, FORUM]. Bare requests get ignored [S25]. |
| Recent grad engineer | Medium | Low to Medium | Low | Receptive [S23, SURVEY], but a stranger referral may count as a "sourcing" referral only [S24, FORUM]. |
| Technical recruiter | Medium to High | High | Low | Owns the requisition. A recruiter at a 500+ company said no in his data [S1]. Gem: tech recruiters handled 2,971 applications each in 2024 [S6, VENDOR]. |
| University or campus recruiter | Medium (where it exists) | High for new grad roles | Low | [S20, PRACTITIONER]. |
| Talent acquisition partner | Medium | Medium | Low | Broad scope; may not own this role (*inference*). |
| Hiring manager named on the posting | Medium to High | High | Low to Medium | Opinion. |

### 2.4 Over 1,000 employees

| Role | Replies | Helps | Annoyed | Evidence |
|---|---|---|---|---|
| CEO, founder, C-suite, VP | Very low | Very low | High | Belkins: 0.22% at 10,000+ [S2]; Sales.co: 1.37% at 5,000+ [S4] (VENDOR, seller direction). Never write (section 7). |
| Director of engineering | Low | Medium | Medium | Opinion. |
| Engineering manager | Low to Medium | High if it is the team | Medium to High | Amazon hiring manager on Blind: "Speaking as a hiring manager, please stop ... I'm also not going to respond" [S26, FORUM]. Others call direct emails to managers "very bad form" [S27, FORUM]. One Microsoft engineer's emails to 3 or 4 managers for a friend got next-day phone screens [S27, FORUM]. |
| Senior or staff engineer | Low to Medium | Low | Medium | Many requests since listing a big company on their profile [S25, FORUM]. Stranger referral is weak [S24]. |
| Recent grad engineer | Medium | Low to Medium | Low | Most receptive group [S23, SURVEY]. Can submit a referral; it carries little weight from a stranger [S24, FORUM]. Referral programs pay bonuses, which motivates some [S25, FORUM]. |
| Technical recruiter | Medium | High for experienced roles | Low | Singh's Airbnb, Snapchat and Cloudflare interviews came from recruiter emails [S20, PRACTITIONER]. Gem: tech recruiters at 5,000+ handled 2,667 applications each in 2024 [S6, VENDOR]. |
| University or campus recruiter | Medium | High for new grad roles | Low | "These are the right people to reach out to if you are still in school"; "where I had the most luck" [S20, PRACTITIONER]. Apollo lists 425 people with these titles at Amazon in the US [S34]. |
| Talent acquisition partner or lead | Low to Medium | Medium | Low | Often a business partner role over many teams (*inference*). |
| Hiring manager named on the posting | Medium | High | Medium | Rare at this size; when named, best first manager (*inference*). |

### 2.5 Ranked roles per size band (what section 8 codes)

*Inference*, built from the tables above. The first role is the first email.

| Band | Order |
|---|---|
| Under 50 | 1. CEO or founder (CTO instead for 21 to 49 employees when a separate CTO exists). 2. Founding engineer or member of technical staff on the team. 3. The one recruiter or talent lead, if any. |
| 50 to 200 | 1. Hiring manager: engineering manager, head or director of engineering for the team. 2. Technical recruiter or talent lead. 3. CEO or CTO (only up to about 120 employees, or 200 if founder-led and technical). 4. A senior or early career engineer on the team. |
| 200 to 1,000 | 1. Technical recruiter (university or early career recruiter for a new grad role). 2. Engineering manager for the team, in the posting's city. 3. An engineer on the team, 0 to 3 years or senior, in the posting's city. |
| Over 1,000 | 1. University or early career recruiter for a new grad role, technical recruiter otherwise, in the posting's city or state. 2. Engineering manager whose title matches the team, in the posting's city. 3. An early career engineer on the team, in the posting's city. No director or above. |

## 3. Location

**Direct evidence: none found.** No study or vendor report measures whether people in the job's own city reply more
to a job seeker's cold email. Treat every rule here as a test to measure (section 9).

Indirect evidence:

- Referrals happen inside the same unit. At one US financial firm, 73.2% of referrals were within the same division,
  and nearly all referrers were at the same (47.9%) or a higher (48.1%) level [S9, PRIMARY].
- Local ties carry job information. People on the same census block are 6.9 points more likely to work together than
  chance would give, which the authors read as neighbors referring neighbors [S16, PRIMARY, working paper figure].
- Alumni presence shapes where graduates take their first job, strongest for alumni of the same discipline and cohort
  [S17, PRIMARY, abstract seen only in search results].
- LinkedIn research, as quoted by a LinkedIn editor: "more than 70% of professionals get hired at companies where they already have a connection" [S36, VENDOR, not opened].
- Employers still lean local for many roles. A BioSpace outlook quoted by a recruiting firm: 52% of employers "prefer
  local candidates but will consider remote" [S39, secondary, life sciences].
- A stranger referral from an unrelated part of a big company counts little. Meta's form option "never worked
  together" makes it a "sourcing" referral, and recruiters favor referrers who know the work [S24, FORUM, named
  engineers].

**Same team or same company.** The division finding [S9] and the Meta form [S24] point the same way: someone on or
near the hiring team is worth more than anyone else at the company. At companies under 200 the whole company is
close to one team, so this matters less (*inference*).

**What follows (*inference*).**

- Over 1,000: require the posting's city, then its state. A manager in another office usually runs another team.
- 200 to 1,000: prefer the city, accept the state, accept the US for recruiters (recruiters often cover many offices).
- Under 200: location is a tie-breaker only. Many small teams are remote or one office.
- Remote posting: prefer the company's headquarters metro, then the US.

## 4. Shared ties

| Tie | Evidence | What it means for Knock |
|---|---|---|
| Same school (Northeastern) | Ivy League graduates: one more alum from the same sports team at a firm raises the chance a same-team athlete joins it by 193.70%, even when they never overlapped [S12, PRIMARY]. Moderately weak ties (about 10 mutual connections) moved the most jobs in LinkedIn's randomized experiments, more so in software-heavy fields [S11, PRIMARY]. Vendor and blog claims of 25% to 40% alumni reply rates have no method [search results in S37, UNVERIFIED]. | Real but Apollo cannot see it. Its search has no school filter [S32], enrichment returns no education field [S33], and `q_keywords` "Northeastern University" returned 0 of 1,823 Amazon people in Boston [S34]. Use it only when Devanshu supplies it (pasted LinkedIn text, Northeastern's alumni tools). |
| Same prior company | A shared former employer is said to lift InMail replies 27%, attributed to LinkedIn by third parties [S37, UNVERIFIED]. Incidental similarity raises compliance with a request, but only when the shared trait is uncommon [S14, PRIMARY, abstract]. | Apollo search cannot filter by past employer: keyword "Stripe" matched 0 at Retool [S34]. Enrichment's `employment_history` has past employers [S33], but costs a credit per person. Score it after enrichment, not before. |
| Same visa or international background | Mixed. Immigrant managers in Sweden hire immigrants more, most in small owner-managed firms [S15, PRIMARY, abstract]. In a field experiment with 6,548 US professors, same-race and same-gender matches brought no reply benefit except for Chinese students [S13, PRIMARY, press release]. | Do not build. Guessing background from a name is unreliable and unfair, and #9 bars any visa mention. |
| Same projects or tech | Personalization with two custom details lifts replies 56% (5.6% versus 3.6%) [S3, VENDOR]. Engineers reply to emails that cite something specific to their work [S5, VENDOR]. | Score title words that match the posting's team (section 8.4). The email text already carries the project link. |

People underestimate how often strangers agree to help when asked directly [S45, PRIMARY, summary]. So a small,
clear ask to a person with no tie is still worth sending.

## 5. Timing and freshness

| Signal | Evidence | Weight |
|---|---|---|
| Posting age | Dice: posting durations have a median of 7 days, and about half of applications go to postings from the last 48 hours [S18, PRIMARY]. Ashby: the first week brings 2.5 to 3 times the applications of later weeks [S19, VENDOR]. Gem: average time to hire is 41 days with 20 interviews per hire [S6, VENDOR]. No study shows early applicants are hired more often, all else equal [S42]. | Strong that competition comes early; no data on reply lift |
| Recently joined employee | No job-seeker data. A seller study saw 0.8% human replies from 630 new-in-role leads, too small to read [S35, VENDOR]. | Thin. Log tenure and test |
| Person posted "we are hiring" | His earlier data: several winning DMs went to people who had just posted that their team was hiring [S42, HIS DATA]. Founders on YC's platform send most first messages, so a founder who is hiring is already looking [S8]. | Thin but on point. Apollo cannot see posts; only Devanshu can add it |
| Recruiter who posted the role | No data found. The poster owns the requisition (*inference*). | Opinion. Rank first when he supplies the name |

*Inference.* Reach out in the first 7 days of a posting when Knock can. A posting up to about 30 days old is still
worth it, since hiring takes 41 days on average. Over 45 days, only if the posting was re-checked and is still open.

## 6. How many people, in what order, how far apart

### 6.1 What the evidence says

- **Fewer people at once reply more.** Hunter, 31 million emails in 2025: one or two people per company replied 5.1%,
  three or more 3.5%. Hunter's reason is that several similar emails to one domain look like spam [S3, VENDOR].
- **Sequential beats parallel.** Dalton's 3B7: after the first email, if no reply in 3 business days, contact a
  second person at the same company; at 7 business days, follow up with the first [S29, PRACTITIONER, book summary].
- **A second recruiter is fine after the first goes quiet.** Singh follows up three times with one recruiter, then
  "It's okay to reach out to multiple people at a company" [S20, PRACTITIONER].
- **One follow-up, then the next person.** Wait 3 to 4 days, follow up once, then "move on to the next one on your list" [S44, PRACTITIONER].
- **Small companies notice.** A corporate recruiter: at small companies, hiring managers notice a candidate applying
  across teams, "You're probably gonna look desperate" [S28, PRACTITIONER].
- **Engineers know it is a numbers game and judge effort.** Effort gets a 15 minute call; bare asks get ignored or
  mocked [S25, FORUM].
- **Going around the recruiter can backfire at big companies.** "a way to get blacklisted", "very bad form" (Amazon
  commenters) [S27, FORUM]. A safer path seen in the same thread: a contact asks the manager first [S27].
- **Every email reads different.** Gergely Orosz on a templated student reply: "This is how you wreck cold email for
  everyone else, even from university domains" [S30, FORUM, snippet only]. The drafting rules already make every
  email at a company unlike the others [S43].
- **Most people will not help, a few help a lot.** Dalton's split: about 60% never answer, 20% answer from duty, 20%
  are "Boosters" who give most of the help [S29, PRACTITIONER, figures disputed between summaries].

### 6.2 What follows (*inference*)

| Band | Most people per job | Wave 1 (day 0) | Wave 2 (3 business days, no reply yet) | Wave 3 (7 business days, no reply yet) |
|---|---|---|---|---|
| Under 50 | 2 (3 if 30+ employees and a recruiter exists) | Founder, CEO or CTO | Founding engineer on the team | Recruiter or talent lead, if one exists |
| 50 to 200 | 3 | Hiring manager, plus technical recruiter (two functions on the same day is within Hunter's one or two) | Founder or CTO, if the band rule allows; else an engineer on the team | An engineer on the team |
| 200 to 1,000 | 3 | Recruiter | Engineering manager, same city | Engineer on the team, same city |
| Over 1,000 | 3 | University or technical recruiter | Engineering manager, same city | Early career engineer on the team, same city |

Stop rules:

- Any human reply from anyone at the company holds every unsent wave. Devanshu decides what happens next.
- A "no" or "please stop" closes the company for this job and for 90 days.
- An auto-reply (out of office, on leave, left the company) skips that person and sends the next wave now.
- A bounce replaces the person with the next candidate in the same slot.
- Follow-ups to each person follow #9 unchanged. Wave timing counts from the first email of the previous wave.

## 7. Who never to write to

| Who | Why | Source |
|---|---|---|
| Founders, C-suite, VPs and partners at companies over 200; directors and "head of" titles too over 1,000 | Lowest base rates at big companies; gatekeepers; the request lands far from the team | [S2, S4, VENDOR]; *inference* |
| People outside the US for a US role | Different team and time zone; referrals run inside the division | [S9, PRIMARY]; *inference* |
| Agency, staffing, RPO and contract recruiters | Not the employer. An agency may claim a fee if it submits him, and duplicate submissions can get a candidate dropped | [S40, PRACTITIONER]; Amazon and Alphabet cut contractor recruiters first when hiring slows [S41, snippet] |
| GTM, sales, finance, executive or legal recruiters, recruiting coordinators | Not the engineering requisition. My Retool test returned two "GTM Recruiter" titles for the word "recruiter" | [S34] |
| Interns, students, apprentices | No standing to refer or forward. Amazon's Boston results include a "Robotics SDE Intern" | [S34] |
| "Fractional", "advisor", "board", "investor", "consultant", "freelance" titles | Not on the payroll team; Retool results include "Growth (Fractional)" | [S34] |
| People on leave | Auto-replies say so; Apollo cannot see it | *inference* |
| People who left | Enrichment shows `employment_history` entries with `current` and `end_date` [S33]; search shows `last_refreshed_at` [S32] | [S32, S33] |
| Anyone already on record at the company, no-go people and companies, never-send addresses | Existing Knock rules | [S43] |
| HR generalists, payroll, benefits, executive assistants | Not the hiring path (*inference*) | Opinion |
| Big-company engineers with no team overlap | A stranger referral from them is a "sourcing" referral at best | [S24, FORUM] |

## 8. Rules Knock can run with Apollo

### 8.1 Apollo facts the rules rest on

- Endpoint `POST https://api.apollo.io/api/v1/mixed_people/api_search`, 0 credits per call, 100 results per page at
  most, up to 500 pages; the 429 example cites 600 calls per hour [S32].
- It returns no emails or phones and no location. Each person comes back as `id`, `first_name`,
  `last_name_obfuscated`, `title`, `last_refreshed_at`, `has_email`, `has_city`, `has_state`, `has_country`,
  `has_direct_phone`, `organization` [S32]. My searches through the MCP tool returned the same shape [S34].
  **The reach-out design assumes search returns cities; it does not.** Knock must learn a person's location from the
  location filter that found them, or pay for enrichment.
- Documented filters [S32]: `person_titles[]`, `include_similar_titles` (default true; false for strict),
  `person_not_titles[]` (titles plus not-titles under 100), `person_not_past_titles[]`, `person_seniorities[]` with
  values `owner`, `founder`, `c_suite`, `partner`, `vp`, `head`, `director`, `manager`, `senior`, `entry`, `intern`
  (current title only), `q_keywords`, `q_person_name`, `person_locations[]` and `person_not_locations[]` (cities,
  states, countries, postal codes), `contact_email_status[]` with `verified`, `unverified`, `likely to engage`,
  `unavailable`, `organization_ids[]`, `q_organization_domains_list[]`, `organization_num_employees_ranges[]`
  (`"min,max"`), `organization_locations[]` (headquarters), `q_organization_job_titles[]`,
  `organization_job_locations[]`, `page`, `per_page`.
- Apollo's MCP tool also offers `person_days_in_current_title_range` and `person_total_yoe_range` [S34]. The REST page
  does not list them [S32]. Check them against the REST API in the spike before coding.
- No person department filter and no school filter appear on the REST page [S32]. The MCP tool's department option
  counts the employer's departments, not the person's [S34]. Enrichment returns `departments`, `subdepartments`,
  `functions`, `seniority`, `city`, `state`, `country` and `employment_history`, with no education field, at 1
  credit for demographics or email [S33].
- Behavior seen in my tests on 2026-11-14 [S34]:
  - Retool, US: 275 people. "San Francisco, CA" plus title "software engineer": 29. "California" and "California, US"
    both 153. "San Francisco": 122. City and state names work without a country.
  - Title "recruiter" with similar titles on matched "GTM Recruiter". Strict titles or `person_not_titles` are needed.
  - Seniorities `entry` and `senior` with no title returned account executives and marketers. Seniority must always
    go with titles, since there is no department filter.
  - `person_days_in_current_title_range` max 180 worked (32 people at Retool).
  - Keywords did not match school or past employer (both 0).
  - Many results have `has_email: false` (3 of 5 Amazon university recruiters).

### 8.2 Inputs

- Posting: company domain, title, city, state, remote flag, posted date, Knock's level chip (new grad or entry, mid,
  senior).
- Company: headcount from Knock's record (else Apollo's organization), giving the band: `lt50` (1 to 49), `b50_200`,
  `b200_1000`, `gt1000`.
- Team words: posting title words minus a stop list (`software`, `engineer`, `engineering`, `developer`, `senior`,
  `staff`, `lead`, `principal`, `junior`, `associate`, `new grad`, `graduate`, `i`, `ii`, `iii`, `iv`, `level`, `the`,
  `and`, `of`). For example "Software Engineer, AI Platform" gives `ai`, `platform`.
- Supplied by Devanshu (optional): the job poster's name, a hiring manager named in the posting, known ties.

### 8.3 Slots and searches

Every search sets `q_organization_domains_list = [domain]`, `per_page = 25`, `page = 1`, and
`person_not_titles = EXCLUDE_TITLES`. Every search runs down the location ladder for its band and stops at the first
rung that returns at least 3 people with `has_email: true`.

```toml
# Title lists. Strict means include_similar_titles = false.
[titles]
founder        = ["founder", "co-founder", "cofounder", "ceo", "chief executive officer"]
cto            = ["cto", "chief technology officer", "co-founder and cto"]
founding_eng   = ["founding engineer", "founding software engineer", "member of technical staff", "first engineer"]
eng_manager    = ["engineering manager", "software engineering manager", "manager, software engineering",
                  "manager software development", "software development manager"]
eng_head       = ["head of engineering", "director of engineering", "director, software engineering",
                  "vp of engineering", "vp engineering"]
eng_senior     = ["senior software engineer", "staff software engineer", "lead software engineer",
                  "principal software engineer", "senior software developer"]
eng_early      = ["software engineer", "software developer", "software engineer i", "software engineer ii",
                  "associate software engineer", "software development engineer", "member of technical staff"]
recruiter_uni  = ["university recruiter", "campus recruiter", "early career recruiter", "early talent recruiter",
                  "emerging talent recruiter", "university programs recruiter"]
recruiter_tech = ["technical recruiter", "senior technical recruiter", "engineering recruiter", "tech recruiter",
                  "recruiter", "senior recruiter", "talent partner"]
talent_lead    = ["head of talent", "head of recruiting", "talent acquisition lead", "talent acquisition manager",
                  "talent acquisition partner", "recruiting lead", "recruiting manager"]

EXCLUDE_TITLES = ["intern", "internship", "student", "apprentice", "assistant", "coordinator", "contract",
                  "contractor", "fractional", "freelance", "consultant", "advisor", "board", "investor",
                  "gtm recruiter", "go-to-market recruiter", "sales recruiter", "executive recruiter",
                  "finance recruiter", "legal recruiter", "sourcer"]

[ladder]                       # person_locations rungs, top first
city  = ["<City>, <ST>"]       # e.g. "San Francisco, CA"
state = ["<State name>"]       # e.g. "California"
us    = ["United States"]
remote_first = ["<HQ city>, <HQ ST>"]   # for remote postings, before "us"
```

| Band | Slot (wave) | Titles (strict?) | Seniorities | Ladder | Keep |
|---|---|---|---|---|---|
| lt50 | exec (1) | `cto` if 21 to 49 employees and the role is engineering, else `founder` (strict) | `founder`, `owner`, `c_suite` | us | 1 |
| lt50 | team_eng (2) | `founding_eng`, then `eng_senior`, then `eng_early` (similar on) | none | us | 1 |
| lt50 | recruiter (3) | `talent_lead` + `recruiter_tech` (strict) | none | us | 1 if 30+ employees |
| b50_200 | manager (1) | named hiring manager by `q_person_name`, else `eng_manager` + `eng_head` (similar on) | `manager`, `head`, `director`, `vp` | city, state, us | 1 |
| b50_200 | recruiter (1) | `recruiter_tech` + `talent_lead` (strict) | none | city, state, us | 1 |
| b50_200 | exec (2) | `cto`, then `founder` (strict); only at 120 or fewer employees, or up to 200 if the CEO is a technical founder | `founder`, `c_suite` | us | 1 |
| b50_200 | team_eng (2 or 3) | `eng_senior` + `eng_early` (similar on) | `senior`, `entry` | city, state, us | 1 |
| b200_1000 | recruiter (1) | `recruiter_uni` if level is new grad or entry, then `recruiter_tech` (strict) | none | city, state, us | 1 |
| b200_1000 | manager (2) | named hiring manager, else `eng_manager` (similar on) | `manager` | city, state | 1 |
| b200_1000 | team_eng (3) | `eng_early` for new grad or entry roles, else `eng_senior` (similar on) | `entry` or `senior` | city, state | 1 |
| gt1000 | recruiter (1) | `recruiter_uni` if level is new grad or entry, else `recruiter_tech` (strict) | none | city, state, us | 1 |
| gt1000 | manager (2) | named hiring manager, else `eng_manager` (similar on) | `manager` | city, state | 1 |
| gt1000 | team_eng (3) | `eng_early` (similar on) | `entry` | city, state | 1 |

- A slot with no result moves its wave to the next slot; Knock never fills an empty slot with an excluded role.
- If the posting names a recruiter or hiring manager (or Devanshu supplies the job poster), that person takes the
  slot, found with `q_person_name` and the domain.
- Show Devanshu the top pick plus up to 2 alternates per slot, so a rejection does not need a new search.
- Searches per job: at most 4 slots times 3 rungs, so 12 calls at 0 credits [S32].

### 8.4 Ranking inside a slot

Score each person found for a slot; highest wins. Ties go to the more recent `last_refreshed_at`.

| Signal | Points | Source |
|---|---|---|
| Found on the city rung | +3 | Section 3, *inference* |
| Found on the state rung (or HQ rung for remote) | +1 | Section 3, *inference* |
| Each team word in the title (cap 2) | +2 | Section 4 [S9, S24] |
| Title is exactly a strict-list title | +1 | Avoids drift from similar titles [S34] |
| Known tie supplied by Devanshu: Northeastern, same past employer | +3 | Section 4 [S11, S12] |
| Days in current title 90 to 1,460, if the filter works on REST | +1 | Opinion: past onboarding, still current |
| Days in current title under 90 | 0, and log it | Thin evidence both ways (section 5) |
| `last_refreshed_at` older than 365 days | exclude | Stale record [S32] |
| `has_email: false` | exclude | Cannot be revealed |

After his tick and the reveal, drop the person if enrichment shows a non-US `country`, a `current` employment entry at
another company, or an excluded title [S33].

### 8.5 Exclusions in the request itself

- `person_not_titles = EXCLUDE_TITLES` on every call.
- `person_seniorities` never includes `intern`. At `b200_1000` and `gt1000` it never includes `owner`, `founder`,
  `c_suite`, `partner` or `vp`; at `gt1000` also not `head` or `director`.
- Location rungs are always US places. Add `person_not_locations = ["India", "Canada", "United Kingdom"]` only if the
  US rung leaks; my tests showed none.
- `contact_email_status = ["verified"]` on the first pass of each rung. If that rung returns fewer than 3, run it once
  more without the filter (Knock keeps verified emails only after the reveal [S43], so this saves credits).

### 8.6 Differences from the 2026-11-14 reach-out design

| Design today [S43] | This research | Why |
|---|---|---|
| "People search returns names, titles and locations" | Search returns no location; take it from the rung | [S32, S34] |
| 4 people per job (1 recruiter, 1 manager, 2 engineers) | 2 to 3 per job, by band | Hunter 1 or 2 versus 3+ [S3]; small companies notice [S28] |
| All drafted and sent together | Drafted together, sent in waves at 0, 3 and 7 business days, held on any reply | Dalton 3B7 [S29]; Hunter [S3] |
| At 200 or fewer: 1 recruiter or founder, 1 manager, 1 engineer | Under 50: founder first, no manager slot; 50 to 200: manager and recruiter first, founder in wave 2 | Section 2 |
| 2 senior or staff engineers | 1 engineer; early career at big companies | Receptiveness [S23]; stranger referral weight [S24] |

The sender would need a "not before" date on wave 2 and 3 rows and a hold when the company replies. That is a design
change for Devanshu to accept or reject.

## 9. Making the rules smarter with his own replies

### 9.1 Log on every first email

| Field | Why |
|---|---|
| `role_kind` (founder, cto, founding_eng, eng_manager, eng_head, eng_senior, eng_early, recruiter_uni, recruiter_tech, talent_lead, other) and the raw title | The role comparison |
| `seniority` from Apollo | Second cut of role |
| `size_band` and headcount at send time | The strongest signal so far |
| Company stage or funding, and an AI or not flag if Knock has them | His positives cluster at AI startups [S1] |
| `location_rung` (city, state, hq, us) | Section 3 is untested |
| `team_word_matches` (count and words) | Section 4 |
| `tie` (none, alumni, past_company, supplied_other) | Section 4 |
| `days_in_title` and `last_refreshed_at` | Section 5 |
| `posting_id`, posting age in days at send, level chip | Section 5 |
| `wave` (1, 2, 3), `slot`, and `arm` (see 9.2) | Order effects |
| `days_since_company_last_email` | Spacing |
| Sending account and its domain (northeastern.edu, gmail.com, other) | The largest confound [S1] |
| Email verification status at send, bounce | Deliverability, not interest |
| Reply class: none, auto, out of office or on leave, bounce, human negative, human neutral ("apply online"), human positive | Separate noise from people |
| Outcome: referral submitted, forwarded to manager or recruiter, call, interview, offer; with dates | "Helps", not just "replies" |
| Credit: which person at the company led to the outcome | Company-level learning |

### 9.2 Test, do not just watch

- Randomize the first wave where the evidence is split. In `b50_200`, flip a coin per job between manager first and
  recruiter first. In `lt50`, flip between CEO and CTO when both exist. In `gt1000`, flip between city-only managers
  and state-wide managers. Log the arm. A coin flip makes the comparison fair even with few emails (*inference*).
- Report per company too: "any human reply at the company" and "positive outcome per company reached". These grow
  faster than per-role counts.
- Read the numbers with intervals. At 200 more emails expect about 8 human replies at his 4.0% rate. That can show a
  size band effect of 3 times or more (1 versus 3 or more positives per band), not a role effect of 1.5 times
  (section 1.1).
- Start each role and band at his pooled rate (21 of 522) as the prior and update with new replies (a beta-binomial
  average). A role moves the ranking only when its interval no longer overlaps the others.
- Compare only within one sending account, or adjust for account. The Gmail history should be left out of role
  tables (section 1.1).
- Review after every 100 first emails: which slot rarely finds anyone, which exclusions fired, which bounces came
  from which rung.

## 10. Gaps

- No source, primary or vendor, measures replies to job seekers by recipient role. Section 2 is a judgment from
  indirect data, his small sample and practitioners.
- No source measures a location effect on replies (section 3).
- The 3B7 rule comes from book summaries, not the book [S29].
- `person_days_in_current_title_range` and `person_total_yoe_range` are confirmed only on the MCP tool [S34].
- Reddit and X threads could not be read; Blind and HN stood in.
- The Gem PDF figures come from the report's own text, extracted with pypdf; charts were not read [S6].

## Sources

Read in full unless marked. "Snippet" means only the search result text was seen.

| # | Source | Type |
|---|---|---|
| S1 | Knock history as given in this task's brief (role counts, positives, sending accounts), 2026-11-14 | HIS DATA |
| S2 | Belkins, "B2B Cold Email Response Rates (2026 Study)", 7,530,489 emails in 2025, replies over sends: https://belkins.io/blog/cold-email-response-rates | VENDOR |
| S3 | Hunter, "The State of Cold Email", 31 million emails sent in 2025: https://hunter.io/the-state-of-cold-email | VENDOR |
| S4 | Sales.co, "Cold Email Statistics", 2M+ emails, 2024 to early 2026: https://sales.co/research/cold-email-statistics | VENDOR |
| S5 | Lavender, "Benchmark Learnings: Emailing Technical Buyers", 231,818 emails: https://lavender.ai/blog/benchmark-learnings-emailing-technical-buyers | VENDOR |
| S6 | Gem, "2025 Recruiting Benchmarks Report", 140M+ applications, 1.3M hires (PDF text, pages 9, 38 to 40, 49, 57, 59): https://lp.gem.com/rs/972-IVV-330/images/2025%20Recruiting%20Benchmarks%20-%20Gem.pdf | VENDOR |
| S7 | Ashby, "Referrals" talent trends report, 38M applications, 2021 to 2024: https://www.ashbyhq.com/talent-trends-report/reports/referrals | VENDOR |
| S8 | Y Combinator, "What startup hiring looks like in 2021": https://www.ycombinator.com/blog/what-startup-hiring-looks-like-in-2021-and-during-a-pandemic/ | VENDOR |
| S9 | Brown, Setren and Topa, NY Fed Staff Report 568 (2013), as summarized in `docs/research/2026-10-20-people-and-channels.md`: https://resources.newyorkfed.org/medialibrary/media/research/staff_reports/sr568.pdf | PRIMARY |
| S10 | Burks, Cowgill, Hoffman and Housman, "The Facts About Referrals" (2013 draft) and QJE 2015: https://www.aeaweb.org/conference/2014/retrieve.php?pdfid=722 | PRIMARY |
| S11 | Rajkumar et al., Science 2022, weak ties experiment: https://digitaleconomy.stanford.edu/publication/a-causal-test-of-the-strength-of-weak-ties (via the 2026-10-20 doc) | PRIMARY |
| S12 | Gompers, Hu, Levinson and Srivastava, "From Field to Firm", NBER WP 35527, July 2026 (abstract): https://www.nber.org/papers/w35527 | PRIMARY |
| S13 | Milkman, Akinola and Chugh field experiment, APA press release 2015: https://www.apa.org/news/press/releases/2015/04/doctoral-white-male (snippet) | PRIMARY |
| S14 | Burger et al., "What a coincidence!", PSPB 2004, DOI 10.1177/0146167203258838 (abstract via snippet) | PRIMARY |
| S15 | Åslund, Hensvik and Skans, "Seeking Similarity", JLE 32(3) 2014: https://econpapers.repec.org/RePEc:ucp:jlabec:doi:10.1086/674985 (abstract via snippet) | PRIMARY |
| S16 | Bayer, Ross and Topa, "Place of Work and Place of Residence", JPE 2008: https://www.nber.org/papers/w11019 (snippet) | PRIMARY |
| S17 | Zou, "The Impact of Alumni Networks on First Job Location", Empirical Economics 2026: https://link.springer.com/article/10.1007/s00181-026-02968-5 (snippet; page behind a login redirect) | PRIMARY |
| S18 | Davis and Samaniego de la Parra, NBER WP 32320 (2024): https://www.nber.org/papers/w32320 (via the 2026-10-20 doc) | PRIMARY |
| S19 | Ashby, 2023 applications per job report: https://www.ashbyhq.com/talent-trends-report/reports/2023-trends-report-applications-per-job (via the 2026-10-20 doc) | VENDOR |
| S20 | Nick Singh, "8 Cold Email Tips To Land Your Dream Job": https://www.nicksingh.com/posts/cold-email-tips-to-land-your-dream-job-with-examples | PRACTITIONER |
| S21 | Ben Lang, "The guide to getting a job with cold email", Next Play, 2025-08-03: https://nextplayso.substack.com/p/the-guide-to-getting-a-job-with-cold | PRACTITIONER |
| S22 | Colin Keeley, "Cold Email Your Way Into an Internship/Job at Any Startup": https://www.colinkeeley.com/blog/cold-email-your-way-into-an-internshipjob-at-any-startup | PRACTITIONER |
| S23 | Abby Lee, "Cold Contacting", MIT AeroAstro Communication Lab, 2025 (24 survey responses): http://mitcommlab.mit.edu/aeroastro/2025/07/23/cold-contacting/ | SURVEY |
| S24 | Taro, "Meta specific question for referrals" (answers by a Google staff engineer and a Robinhood tech lead): https://www.jointaro.com/question/QSKWSO0Js71BbJXma1oj/meta-specific-question-for-referrals/ | FORUM |
| S25 | Blind, "Why do people cold InMail for referral like this", 2023-07-27: https://www.teamblind.com/post/why-do-people-cold-inmail-for-referral-like-this-p5bvtq5j | FORUM |
| S26 | Blind, "How do you actually reach a hiring manager these days?", 2025-07-21: https://www.teamblind.com/post/how-do-you-actually-reach-a-hiring-manager-these-days-kguw0c3n | FORUM |
| S27 | Blind, "Direct email to hiring managers", 2020-04-22: https://www.teamblind.com/post/direct-email-to-hiring-managers-waxdfijx | FORUM |
| S28 | CNBC, "Applying to multiple jobs at the same company can 'look desperate'", 2025-10-17: https://www.cnbc.com/make-it/2025/10/17/applying-to-multiple-jobs-at-the-same-company-can-look-desperate-experts-say.html | PRACTITIONER |
| S29 | Steve Dalton, The 2-Hour Job Search: 3B7 from a book summary (https://www.slideshare.net/slideshow/the-2-hour-job-search-book-summary-63004716/63004716, snippet); Boosters split from IE University's summary (https://static.ie.edu/CMC/T%26C%20UNI/2hour-job-search-Steve-Dalton-infographic.html) | PRACTITIONER |
| S30 | Gergely Orosz on X: https://x.com/GergelyOrosz/status/2110766384426922152 (snippet) | FORUM |
| S31 | First recruiter timing: Pin (https://www.pin.com/blog/hiring-for-startups/) and a16z crypto (https://a16zcrypto.com/posts/article/why-startups-should-hire-recruiters/) (snippets) | PRACTITIONER |
| S32 | Apollo, People API Search reference: https://docs.apollo.io/reference/people-api-search | PRIMARY |
| S33 | Apollo, People Enrichment reference: https://docs.apollo.io/reference/people-enrichment | PRIMARY |
| S34 | My Apollo searches through the `apollo_mixed_people_api_search` MCP tool, 2026-11-14, 0 credits, no reveals: Retool (`retool.com`) and Amazon (`amazon.com`), and the tool's schema | PRIMARY (own test) |
| S35 | Salesmotion, "Cold Outreach Best Practices", 19,043 leads: https://salesmotion.io/blog/cold-outreach-best-practices (snippet) | VENDOR |
| S36 | George Anders on LinkedIn: "more than 70% of professionals get hired at companies where they already have a connection" and referred applicants "nine times more likely" to be hired: https://www.linkedin.com/pulse/theres-art-career-networking-new-college-data-provides-george-anders (snippet; not opened) | VENDOR |
| S37 | Third-party InMail claims (27% lift for a shared former employer), e.g. https://skrapp.io/blog/linkedin-inmail-best-practices/ (snippet) | UNVERIFIED |
| S38 | Hacker News, Academia.edu CEO asking candidates to email him directly in "Who is hiring", 2019-02: https://news.ycombinator.com/item?id=19058379 (via HN search API) | FORUM |
| S39 | Rx2 Solutions quoting BioSpace's 2026 outlook: https://rx2solutions.com/2026/07/29/When-the-Candidate-Pool-Goes-National-(and-When-It-Shouldn't).html (snippet) | PRACTITIONER |
| S40 | Murray Resources, "Seeking a Job, Finding a Fight" (agency fee disputes): https://murrayresources.com/news-articles/employment-seeking-a-job-finding-a-fight-recruiters-commission-disputes-can-hurt-applicants-jobs-disputes-ensnare-employers/ (snippet) | PRACTITIONER |
| S41 | Recruiting News Network on contractor recruiter layoffs: https://www.recruitingnewsnetwork.com/posts/when-layoffs-happen-at-tech-companies-this-position-is-the-first-to-go (snippet) | PRACTITIONER |
| S42 | `docs/research/2026-10-20-people-and-channels.md` (timing studies, his earlier DM history) | Earlier Knock research |
| S43 | `docs/superpowers/specs/2026-11-14-reach-out-design.md` | Knock design |
| S44 | Deepika Ahlawat, "How to Message a Recruiter or Engineer So They Actually Refer You", 2026-07-30 (wait 3 to 4 days, one follow-up, then the next person): https://semiconductorcompass.substack.com/p/how-to-message-a-recruiter-or-engineer | PRACTITIONER |
| S45 | Flynn and Lake, "If you need support, just ask", JPSP 2008, Stanford GSB summary: https://www.gsb.stanford.edu/index%2Ephp/node/374971 (snippet) | PRIMARY |
