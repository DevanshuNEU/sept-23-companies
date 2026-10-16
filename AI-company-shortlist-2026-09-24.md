# AI-native companies that sponsor, hire non-senior, and are worth your time

**Compiled 2026-09-24 for Devanshu Chicholikar.** Every H-1B number traces to a query I ran against
the government's own datasets, held locally. Every company claim carries a dated source.
Where I could not verify something, it says so.

**This is research, not legal advice.** Several rules below are mid-litigation. Confirm anything you
act on with an immigration attorney, and confirm anything touching your F-1 record with Northeastern's OGS.

---

## 1. Read this first: two things that change your strategy

### 1.1 Your first lottery is FY2028, not FY2027

Your brief assumed a registration in early 2027 for the FY2027 cap. The March 2027 registration is for
**FY2028**, an October 2027 start. The FY2027 lottery already ran in March 2026.

### 1.2 The lottery is no longer a coin flip, and wage level is the coin

This is the single most important finding in this document.

DHS finalized the **weighted selection rule** on 2025-12-29. It took effect **2026-02-27** and already ran
once, in the March 2026 FY2027 lottery. Registrations are entered into the pool once per wage level:

| OEWS wage level | Entries in pool | DHS projected selection probability |
|---|---|---|
| Level I | 1 | **15.29%** |
| Level II | 2 | **30.58%** |
| Level III | 3 | over 45% |
| Level IV | 4 | over 61% |

Source: [Weighted Selection Process for Registrants and Petitioners Seeking To File Cap-Subject H-1B Petitions](https://www.govinfo.gov/content/pkg/FR-2025-12-29/html/2025-23853.htm),
final rule, published 2025-12-29, effective 2026-02-27. Confirmed against the
[USCIS H-1B Electronic Registration Process page](https://www.uscis.gov/working-in-the-united-states/temporary-workers/h-1b-specialty-occupations/h-1b-electronic-registration-process)
(observed 2026-09-24), which states USCIS "will conduct a weighted selection among properly submitted
registrations for unique beneficiaries based generally on the highest OEWS wage level."
It applies to the advanced-degree exemption too, so your master's does not route around it.

**What this means concretely.** A Level I offer gives you roughly a 15% shot. A Level II offer roughly 31%.
Same you, same company, double the odds. The prevailing wage level attached to your offer is not a detail
to sort out later. It is the main variable you control, and it outweighs almost every difference between
the companies below.

### 1.3 The $100,000 fee is probably not your problem, twice over

- The September 2025 proclamation restricts **entry**. Its text reaches workers outside the US. If you stay
  here and your employer files a **change of status** from F-1, you never seek admission.
- Separately, a federal district court **vacated** the implementing guidance on 2026-06-08 as an unlawful tax,
  and the First Circuit **declined to stay** that ruling in July 2026. It is currently unenforceable.
  A proclamation signed 2026-09-18 extended the policy on paper through September 2027, but the vacatur stands.

Sources: [Fragomen, extension but currently vacated](https://www.fragomen.com/insights/united-states-president-trump-extends-dollar100000-h-1b-fee-but-policy-currently-vacated-by-court-order.html);
[Fragomen, district court vacates](https://www.fragomen.com/insights/united-states-district-court-vacates-dollar100000-h-1b-fee-government-expected-to-quickly-appeal.html);
[CUPA-HR](https://www.cupahr.org/resource/federal-court-vacates-h-1b-visa-fee-policy/);
[Littler, appellate pause](https://www.littler.com/news-analysis/asap/appellate-court-pauses-100000-h-1b-fee-policy-appeal-continues).

**Operational takeaway:** insist the petition be filed as a change of status, and do not leave the US
between filing and your H-1B start date without talking to a lawyer. Travel is what moves you from the
safe category into the expensive one.

The thing actually worth tracking is different: on 2026-08-25 DHS **proposed** a $103,265 fee on all
cap-subject petitions, with no change-of-status carve-out and no small-employer exemption
([91 FR 54817](https://www.federalregister.gov/documents/2026/08/25/2026-17324/fee-for-certain-h-1b-petitions)).
If that is finalized before March 2027, it hits you and it hits small companies hardest.

---

## 2. What the data is, and why it is not what a paywalled site would tell you

You asked for the real government datasets, not aggregators. That is what this is built on.

| Dataset | Coverage | Records |
|---|---|---|
| DOL OFLC LCA disclosure files (ETA-9035) | FY2024 Q1-Q4, FY2025 Q1-Q4, FY2026 Q1-Q3 | 1,570,129 |
| USCIS H-1B Employer Data Hub | FY2024, FY2025, FY2026 (partial) | 151,660 employer-years |

Downloaded from [dol.gov/agencies/eta/foreign-labor/performance](https://www.dol.gov/agencies/eta/foreign-labor/performance)
and [uscis.gov H-1B Employer Data Hub](https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub).
No h1bgrader, no myvisajobs, no h1bdata.info anywhere in the pipeline.

**Validation:** my FY2025 change-of-employer approval count comes to 68,166. NFAP's independently published
analysis of the same USCIS dataset reports 68,167. Off by one, which means the pivot is correct.

**FY2026 is partial** (decisions through 2026-06-30, three of four quarters). Where I compare years I use the
same October-to-June window across all three, so the trend is like-for-like rather than flattered or penalised.

### Why this distinction matters: the ClickHouse case

ClickHouse has **17 certified LCA filings** on record. A paywalled aggregator would show you that and label
the company an H-1B sponsor. The USCIS approval data says ClickHouse has received **zero new-employment
H-1B approvals in FY2024, FY2025 and FY2026**. Only continuations, meaning people who already hold the visa
transferring in.

An LCA is a filing, not an approval. Employers over-file, and one LCA can cover several positions. For
someone who needs a first-time cap petition, those 17 filings are close to worthless. You only ever see
that gap by holding both datasets side by side, which is exactly why your "no aggregators" instruction
was the right call.

---

## 3. The finding that reorders everything: who sponsors most is not who gets you in

Once wage level drives the lottery, "which companies sponsor the most" stops being the right question.
The right question is "if this company files for me, what are my actual odds."

I computed that for every candidate: their real distribution of software-occupation wage levels across all
certified LCAs, weighted by DHS's own published selection probabilities.

| Company | Expected odds | Wage mix I/II/III/IV | USCIS new approvals 24/25/26 | Median SWE wage |
|---|---|---|---|---|
| Together AI | **54.2%** | 3/4/10/45 (n=62) | 2 / 5 / 11 | $211,432 |
| *Crusoe Energy* | *51.7%* | *3/18/20/67 (n=108)* | *2 / 10 / 16* | *$231,000* |
| Domino Data Lab | 49.8% | 0/1/3/3 (n=7) | 1 / 0 / 2 | $180,000 |
| Decagon | 49.0% | 0/2/9/6 (n=17) | 0 / 2 / 3 | $260,000 |
| Tenstorrent | 47.0% | 4/4/8/15 (n=31) | 6 / 5 / 6 | $163,550 |
| Machinify | 46.8% | 1/3/3/6 (n=13) | 0 / 1 / 2 | $187,741 |
| Temporal Technologies | 46.2% | 1/8/5/11 (n=25) | 0 / 1 / 3 | $176,000 |
| Baseten | 44.8% | 0/14/15/12 (n=41) | 1 / 1 / 4 | $210,000 |
| Scale AI | 44.1% | 4/30/30/29 (n=93) | 18 / 10 / 13 | $216,200 |
| d-Matrix | 43.5% | 1/13/3/11 (n=28) | 3 / 5 / 3 | $210,000 |
| Snorkel AI | 43.4% | 9/3/7/16 (n=35) | 1 / 2 / 2 | $206,315 |
| CoreWeave | 41.8% | 1/39/7/23 (n=70) | 0 / 7 / 7 | $230,000 |
| Abnormal AI | 39.5% | 1/19/12/6 (n=38) | 4 / 2 / 5 | $175,000 |
| Cerebras | 38.5% | 7/15/12/9 (n=43) | 2 / 3 / 2 | $199,534 |
| *Distyl AI* | *37.5%* | *4/7/9/3 (n=23)* | *0 / 3 / 2* | *$205,000* |
| Verkada | 36.1% | 35/14/34/20 (n=103) | 25 / 8 / 26 | $155,000 |
| Ramp | 35.6% | 1/40/21/1 (n=63) | 10 / 7 / 17 | $168,000 |
| Fireworks AI | 33.3% | 14/9/14/4 (n=41) | 0 / 4 / 9 | $206,000 |
| Commure | **23.8%** | 23/6/2/3 (n=34) | 9 / 16 / 21 | $155,250 |

*Italics = Crusoe and Distyl, your model companies. Shown for calibration, excluded from the ranking.*
Baseline: all-Level-I is 15.3%. The old uniform-random lottery was about 29.6%.

**Read the top and bottom of that table together.**

**Commure** is the heaviest sponsor in the entire candidate set. Forty-six new-employment approvals over three
years, rising every single year (9, then 16, then 21). On a naive "who sponsors" ranking it wins outright.
But it files **68% of its software LCAs at Level I**, and its median software wage is $155,250. Expected
odds: **23.8%**, below even the old random baseline.

**Together AI** approved far fewer people (18 over three years) but files almost entirely at Level III and IV.
Expected odds **54.2%**, more than double Commure's.

So the company most likely to *offer* to sponsor you is among the least likely to actually *get you through*.
That inversion is invisible in every H-1B ranking site, because they count filings.

**Your model company holds up.** Crusoe went 2, 10, 16 on new approvals while filing 67 of 108 software LCAs
at Level IV, for 51.7% expected odds and a $231,000 median. You picked it on shape and it independently
scores near the top on visa mechanics. That is a good signal about your instincts, and it gives you a
concrete template: vertically-integrated infra companies pay Level III/IV because they are competing for
systems engineers, and that pay is what buys lottery entries.

### A caveat I want to be straight about

This metric describes how a company has filed **historically, across all its software roles**, most of which
are more senior than the job you are targeting. A new-grad offer at any of these companies will sit lower in
their range than their median. Fireworks is the clearest example: company-wide median $206,000, but the
actual New Grad req posts at $160,000-$180,000, which in most metros is Level I or low Level II.

So treat the column as **the ceiling a company's pay bands make available to you**, not a promise. A company
that never files above Level II cannot put you at Level III. A company that routinely files Level IV can,
if you negotiate there. Which brings the whole thing back to one lever: negotiate the wage level explicitly,
and ask what OEWS level the offer maps to before you sign.

---

## 4. The shortlist

Scored on the rubric fixed before any results were seen ([.research/rubric.md](.research/rubric.md)):
H-1B assurance 45, role-level fit 35, culture 20.

**One disclosure.** That rubric put 15 points on sponsorship *trend* and did not anticipate the wage-level
finding in section 3. Rather than quietly rewrite the rubric to match what I found, I scored it as written
and show expected lottery odds as a separate column. Where the two disagree, section 3 explains why, and
I trust the odds column more. You should see both rather than a single number I tuned after the fact.

| # | Company | H-1B /45 | Role /35 | Exp. odds | Trend | New grad req | Key caution |
|---|---|---|---|---|---|---|---|
| 1 | **Fireworks AI** | 39.0 | 35.0 | 33.3% | RISING 0/4/9 | **YES** | Files 14 of 41 at Level I |
| 2 | **Verkada** | 33.0 | 28.8 | 36.1% | rising 25/8/26 | intern 2027 | 5-day onsite San Mateo |
| 3 | **Together AI** | 40.0 | 24.0 | **54.2%** | RISING 2/5/11 | no | Fewer reqs than peers |
| 4 | **Baseten** | 33.2 | 29.6 | 44.8% | RISING 1/1/4 | no | Small absolute volume |
| 5 | **Ramp** | 37.0 | 30.0 | 35.6% | rising 10/7/17 | no | NYC 3 days/week |
| 6 | **Domino Data Lab** | 10.9 | 26.6 | 49.8% | rising 1/0/2 | **YES x2** | Thin sponsorship volume |
| 7 | **Temporal Technologies** | 29.2 | 26.4 | 46.2% | RISING 0/1/3 | no | Small volume |
| 8 | **Decagon** | 19.1 | 25.6 | 49.0% | RISING 0/2/3 | no | "Fully in person" |
| 9 | **CoreWeave** | 32.2 | 24.8 | 41.8% | flat 0/7/7 | no | Export-control exposure |
| 10 | **Scale AI** | 28.0 | 26.4 | 44.1% | rising 18/10/13 | no | Government/defense work |
| 11 | **Abnormal AI** | 31.6 | 22.4 | 39.5% | rising 4/2/5 | no | Remote-USA available |
| 12 | **Commure** | 37.0 | 25.6 | **23.8%** | RISING 9/16/21 | no | Worst odds in the set |

### The three I would actually start with

**Fireworks AI** is the cleanest fit on paper. It is the only company in the whole study with an explicit,
currently-live new-grad software req, and its title convention makes the entry point unambiguous.

> **Member of Technical Staff, New Grad (BS/MS)**, San Mateo and New York, posted 2026-09-08, $160,000-$180,000.
> https://jobs.ashbyhq.com/fireworks/0c78aede-7c21-4d1e-88f1-f309deb9819e

"Member of Technical Staff" with no prefix is their standard IC title and there are no Senior or Staff MTS
variants on the board, so MTS is genuinely the floor, not a disguised senior role. Thirteen such reqs are open.
A sweep of all 79 postings found no sponsorship restriction language anywhere; the application asks the two
neutral work-authorization questions rather than screening you out. Sponsorship is rising fast (0, 4, 9).

The catch, and it is real: expected odds of only 33.3%, because they file 14 of 41 software LCAs at Level I,
and that $160K-$180K new-grad band is probably Level I or low Level II. **Apply here, then negotiate the
wage level.** This is the single best illustration of why section 3 matters.

**Verkada** has the most explicit sponsorship evidence I found anywhere, in the company's own words.
Across 302 live postings:

> **118 postings state verbatim: "We do sponsor and take over sponsorship of employment visas for this role."**

That set includes the core San Mateo engineering roles, for example
[Backend Engineer - Connectivity](https://job-boards.greenhouse.io/verkada/jobs/5194598007) (posted 2026-09-10),
[Software Engineer - Computer Vision](https://job-boards.greenhouse.io/verkada/jobs/5195995007), and the
[2027 engineering internships](https://job-boards.greenhouse.io/verkada/jobs/5210813007).

Critically, 156 other postings say the opposite: *"We are unable to sponsor or take over sponsorship of an
employment visa for this role, at this time."* Those are overwhelmingly sales, solutions engineering and IT.
**Read the clause on the specific req before applying**, because one Senior Backend Engineer, Growth role in
San Mateo sits on the no-sponsorship side. Verkada also has the heaviest raw approval record here
(25, 8, 26). Costs: 5-day onsite in San Mateo, a median software wage of $155,000, and 35 of 103 filings at
Level I, giving middling 36.1% odds.

**Together AI** is the best pure visa play. Expected odds **54.2%**, the highest of any company with
meaningful filing volume, because 45 of its 62 software LCAs are Level IV. Sponsorship is rising cleanly
(2, 5, 11) with a $211,432 median and a zero percent Certified-Withdrawn rate. The tradeoff is fewer open
non-senior reqs than Fireworks or Verkada, so it is a lower-frequency, higher-quality shot.

### Two worth a look that the obvious ranking buries

**Domino Data Lab** scores badly on H-1B assurance (10.9/45) purely because its volume is tiny, and that
score is arguably unfair to it. It carries the most affirmative sponsorship language of any company here:

> "Visa Sponsorship: Visa sponsorship (including H-1B and other employment-based visa categories) may be
> available for this position depending on business considerations and the role's requirements."

and it has **two live new-grad reqs**:
[Software Engineer - New Grad, 2027](https://app.careerpuck.com/job-board/domino-data-lab/job/7992556?gh_jid=7992556)
and [Forward Deployed Engineer - New Grad, 2027](https://app.careerpuck.com/job-board/domino-data-lab/job/7992530?gh_jid=7992530),
both New York City, plus matching 2027 internships. Expected odds 49.8%. The separate public-sector roles are
TS/SCI and Secret clearance-gated, but the commercial and new-grad roles carry none of that language.
Low volume means low certainty, not a closed door.

**Decagon** pairs 49.0% expected odds with a $260,000 median software wage, the highest in the set, and rising
sponsorship. Series D $250M at $4.5B (2026-01-27, Coatue and Index). Seven non-senior core engineering reqs.
The cost is a hard "fully in person" culture.

### Commure: the trap

Commure will look like the obvious answer on any H-1B ranking site. It is the heaviest sponsor here and it is
growing (9, 16, 21). Seven non-senior core reqs including Forward Deployed Engineer. It is also:

- **23.8% expected odds**, the worst in the set and well below the old random baseline, because 23 of 34
  software filings are Level I
- a **$155,250** median software wage
- **5 days a week in-office** in Mountain View, stated verbatim across nine postings
- carrying stated values including "Speed Above All Else" and "High Output Matters"

It is not disqualified. You said the job comes first, and a real offer beats a theoretical better one. But
go in knowing you would be buying the worst lottery odds on this list, and that a Level I filing there is
roughly a one-in-seven shot rather than a coin flip.

---

## 5. Ruled out, and why

Negative findings save you more time than positive ones. All job-board data observed 2026-09-23 via the
companies' own Greenhouse, Ashby and Lever JSON APIs.

### Ruled out on sponsorship reality

| Company | Finding |
|---|---|
| **ClickHouse** | 17 certified LCAs but **zero new-employment approvals in FY2024, FY2025 and FY2026**. Continuations only. Also effectively no non-senior reqs. |
| **Weaviate** | **Zero H-1B filings, ever.** Dutch parent (Weaviate B.V., Amsterdam); the Delaware "Weaviate, LLC" is a private mailbox, not an office. Three open roles worldwide, **zero engineering**. No evidence of a functioning US employing entity. |
| **SmarterDx** | **No record at all** in the USCIS Data Hub across three years, and no LCAs. Fully remote US, but no evidence they have ever sponsored. |
| **Ambience Healthcare** | FY2025 zero approvals; FY2026 **zero approvals and one denial**. Engineering is SF hybrid 3x/week with no remote path. |
| **Browserbase, Braintrust, Arize AI, Positron AI, Fluidstack** | Zero new-employment approvals across all three years. Fluidstack files at high wage levels but has never had one approved. |
| **RunPod, Pinecone, Hebbia, Rogo** | Sponsorship **stopped**: had approvals in FY2024 or FY2025, zero in FY2026. |
| **Glean** | Sponsorship **falling** (6, 7, then 1). Also a stated 4-day in-office mandate in Mountain View, restated verbatim across 8+ postings. |

### Ruled out on role level

**SambaNova** (zero non-senior software roles), **Pinecone** (none), **Weaviate** (no engineering reqs at all),
**ClickHouse** (effectively none), **Grafana Labs**, **Datadog** (every US software req is Senior or Staff),
**Netlify** (effectively not hiring), **Unstructured** (one non-senior req in title only, and it is clearance-gated).

### Ruled out on blockers

- **Palantir** — 118 of 150 US non-senior engineering reqs mention clearance, TS/SCI or US citizenship. Thirty-two do not, including an FDSE New Grad Commercial role, so it is not absolute, but the odds of landing in a gated team are high.
- **OpenEvidence** — one req states verbatim: *"Must be authorized to work in the United States without sponsorship now or in the future."* Engineering is in-person 5 days a week in SF or Miami. They have sponsored exactly once, under the prior entity name Xyla, Inc.
- **Socure** — several reqs carry explicit "cannot provide sponsorship now or in the future."
- **Harvey (Counsel AI)** — *"Visa sponsorship is not available for this role"* appears on some reqs. Check per req.
- **Augury** — core R&D sits in Haifa, Israel. All US roles are go-to-market; **none are software engineering**.
- **Machinify** — core product engineering is remote-US and sponsored, but the legacy Performant government side requires *"client required clearances"* with FBI-level background checks under the Privacy Act of 1974.
- **Path Robotics** — one live req requires US citizenship plus a DoD Secret clearance, and HII/Saronic naval work will likely widen that surface.

### No longer independent employers

**Replicate** (acquired by Cloudflare), **Anyscale** (being acquired by Nscale), **Voltage Park**
(merged into Lightning AI), **SmarterDx** (now part of Smarter Technologies under New Mountain Capital),
**Augmedix** (absorbed into Commure, deregistered 2024-10-15).

---

## 6. What I would actually do

1. **Treat wage level as the negotiation.** Before signing anything, ask: *what OEWS wage level will the
   LCA for this role be filed at, and in which metro?* Moving Level I to Level II doubles your odds from
   15.3% to 30.6%. Nothing else on your side of the table comes close to that leverage.

2. **Apply to Fireworks AI's New Grad req now.** Posted 2026-09-08, it is the only explicit live new-grad
   software req in the study, and new-grad reqs close fast.

3. **For Verkada, read the visa clause on each individual req.** Roughly 39% of their board says they sponsor
   and 52% says they cannot, split cleanly along engineering versus sales. Applying to the wrong one wastes
   the application.

4. **Insist on change of status, not consular processing.** That is what keeps you outside the $100,000
   proclamation even if the vacatur is overturned on appeal. Avoid international travel between filing and
   your start date without legal advice.

5. **Confirm E-Verify enrollment before accepting.** Your 24-month STEM OPT extension requires it, and plenty
   of small startups are not enrolled. This can disqualify an otherwise good job around month 11.

6. **Watch the $103,265 proposed fee.** Docket USCIS-2026-0298. If finalized before March 2027 it applies to
   you with no change-of-status carve-out, and it would push the calculus toward larger employers who can
   absorb it.

7. **Use Crusoe as a template, not just a target.** What made it score well is structural: vertically
   integrated infra companies pay Level III/IV for systems engineers. Together AI, CoreWeave and Baseten
   share that shape. Application-layer companies that compete on headcount rather than scarce systems
   skills tend to file Level I, which is why Commure lands where it does.

---

## 7. Limits, stated plainly

- **FY2026 government data is partial**, covering decisions through 2026-06-30. Trend comparisons use the
  same October-to-June window across all three years, but the FY2026 column understates full-year totals.
- **Expected-odds figures are modelled**, not guaranteed. They combine each company's historical wage-level
  mix with DHS's published probability estimates. Levels III and IV are DHS floors ("over 45%", "over 61%"),
  so I used 45% and 61% and the real numbers are likely a little higher. The figures describe company-wide
  software filings, most of them more senior than your target role. See the caveat at the end of section 3.
- **Employer-name matching** is imperfect. I matched on normalised legal entity names and verified each one
  by hand, but a company filing under an unexpected subsidiary could be undercounted.
- **An LCA is not an approval.** I have kept the two datasets separate throughout for this reason.
- **Culture data is the weakest axis here** and is reported separately; see section 8. Several company
  ratings rest on small review counts that are not statistically meaningful.
- **Funding figures are stale for several companies**, flagged inline in the source files. Pinecone and
  Weaviate have not announced a round in over three years.
- **I could not verify:** USCIS FY2027 unique-employer counts (not published); current I-765 processing
  times (USCIS blocks automated access); any documented cap-exempt arrangement at an AI company.
- **Three facts changed under research and are corrected throughout:** Cerebras **did IPO**
  (Nasdaq: CBRS, 2026-05-14), Scale AI is on its **third CEO in 14 months** (Francis deSouza, August 2026),
  and **Nick Elprin is no longer Domino's CEO**. Any earlier assumption to the contrary is wrong.

### Source files

| File | Contents |
|---|---|
| [.research/rubric.md](.research/rubric.md) | Scoring rubric, fixed before results |
| [.research/policy-landscape.md](.research/policy-landscape.md) | Full immigration policy research with primary citations |
| [.research/part-neocloud.md](.research/part-neocloud.md) | GPU clouds: CoreWeave, Lambda, Together, Nebius, Fluidstack, Vultr |
| [.research/part-inference.md](.research/part-inference.md) | Inference infra: Fireworks, Baseten, Modal, RunPod, Anyscale, Deep Infra |
| [.research/part-silicon.md](.research/part-silicon.md) | Silicon: Groq, Cerebras, SambaNova, d-Matrix, Etched, Tenstorrent, Positron |
| [.research/part-agentinfra.md](.research/part-agentinfra.md) | Agent infra: Browserbase, E2B, LangChain, Braintrust, Arize, Temporal, Daytona |
| [.research/part-data.md](.research/part-data.md) | Data/retrieval: Pinecone, Weaviate, ClickHouse, Glean, Snorkel, LlamaIndex, Chroma |
| [.research/part-applied.md](.research/part-applied.md) | Applied/vertical AI, 27 companies with ATS-verified reqs |
| [.research/part-wildcard.md](.research/part-wildcard.md) | Sierra, Cloudflare, Snowflake, Databricks, Scale AI, Render |
| [.research/h1bdata/h1b_full.db](.research/h1bdata/) | 1,570,129 LCA records, queryable |
| [.research/h1bdata/uscis.db](.research/h1bdata/) | 151,660 USCIS employer-year records |

---

## 8. Culture

This is the weakest axis in the study, and the reason is structural: most of these companies are too small
to have generated a meaningful review corpus, and Glassdoor blocked automated access throughout
(HTTP 403 plus a Cloudflare bot check on 2026-09-24, not bypassed). Figures below are derived from search
snippets of Glassdoor's own pages and are labelled as such.

**One thing to carry into every search you do yourself:** aggregator sites (jobsbyculture.com,
comparably.com, zippia.com) publish confident-looking ratings that directly contradict Glassdoor's own
page titles. One reports Together AI at "4.1/5 on 40 reviews" when Glassdoor's own page says 8 reviews.
Those sites will be the first results you see. Do not trust them.

| Company | Glassdoor | Recent 12mo | Layoffs | Headline caution |
|---|---|---|---|---|
| **Temporal** | 4.4/5, n=18 | Insufficient (positive) | None found | **Cleanest record in the study.** Risk is hypergrowth + real on-call |
| **Ramp** | 3.8/5, 219; **Blind 4.4/137** | Declining on hours only | None found | "Sounds intense? It is." WLB 2.7, down 7% |
| **Verkada** | 3.8/5, ~1,080 (SWE 3.7/40) | **Improving** | Sales only | Hard 5-day; ~27% turnover; harassment suit Nov 2025 |
| **Scale AI** | 3.8/5, 436 (**SWE 3.9/52, 90% rec**) | Stabilizing for eng | Yes, Jul 2025 (~14%) | **Three CEOs in 14 months** |
| **Cerebras** | 4.1/5, n=57 (MTS 3.5/17) | Insufficient | None verified | **IPO'd May 2026**; ~86% of 2025 revenue UAE-affiliated |
| **Crusoe** | 3.6/5, 44-48 | **Declining** | None found | 5-day RTO reversal + 24h on-call; WLB down 12% |
| **CoreWeave** | 3.4/5, 90 | **Declining** | None found | **Co-founder praised "8am to 4:00 a.m." days**; WLB 2.7, down 15% |
| **Abnormal AI** | 3.7/5, 297 (**SWE 3.4/51, 44% rec**) | **Declining, measurably** | None since Mar 2023 | SWE recommend **down 17 points** in 12 months |
| **Commure** | 3.4/5, 345 | **Declining** | **Yes, three rounds** | **Said no layoffs, then cut weeks later** |
| **Domino Data Lab** | 3.5/5, 98 | Stable but thin | **Yes, 2022 (~25%) + 2024 US eng** | Headcount **down 27% from peak**; CEO 4 weeks old |
| **Fireworks AI** | n=11, rating unverifiable | Insufficient, polarized | None found | Two weak sources echo CEO-centralized decisions |
| **Decagon** | n=3 (correct entity) | Insufficient | None found | CEO: six days "part of the company culture" |
| **Baseten** | 5.0/5, **n=2** | Insufficient | None found | Remote-first posture quietly ended (0 of 101 remote) |
| **Together AI** | **n=1-8** | Insufficient | None found | Culture unknowable; engineering excellent |

### The findings that actually matter

**Temporal is the single cleanest record in the study, and it was not on my radar.** No negative finding
on any axis: no layoffs, no controversy, no hours complaints, no leadership issues. Glassdoor 4.4/5, though
on only 18 reviews. What makes it unusual is that its engineering claim is **independently verifiable rather
than asserted**: 23,265 GitHub stars, 2,003 commits in 52 weeks, 43 distinct authors in three months. That is
a real open-source project with a real contributor base, which almost nothing else here can show. The honest
risks are hypergrowth (valuation went $5B to $12.55B in seven months) and genuine on-call, since the product
is durable execution infrastructure. On the H-1B side it pairs 46.2% expected odds with rising sponsorship.
**On combined evidence this is arguably the best-balanced name in the study.**

**Ramp has the best-supported positive culture number here, and it advertises its own intensity.** Blind
4.4/5 across **137 reviews** is the most statistically meaningful positive signal in the whole set (Blind
skews negative, which makes a high score there more meaningful than a high Glassdoor score). Glassdoor 3.8
on 219 reviews, 90% CEO approval, and the strongest engineering blog of the applied companies. The cost is
explicit and self-declared: work-life balance **2.7 and falling**, 60+ hour weeks, and recruiting copy that
literally reads *"Sounds intense? It is."* Reviews also cite abrupt terminations. This is an honest trade,
clearly labelled.

**Scale AI's engineering is doing better than the company's headlines suggest.** Software Engineer reviews
sit at 3.9/5 across 52 reviews with **90% recommend, up 11 points**, and the research org actually grew after
the Meta departures. Against that: **three CEOs in 14 months** (Francis deSouza took over August 2026), a
July 2025 layoff of roughly 14% plus 500 contractors, and ongoing contractor litigation that concerns a
separate worker population from the one you would join.

**Cerebras IPO'd on 2026-05-14 (Nasdaq: CBRS).** That matters to you directly: equity is now liquid and
priceable rather than paper. Glassdoor 4.1 on 57 reviews, though Member of Technical Staff reviews sit lower
at 3.5 on 17. The structural caution is unchanged: roughly **86% of 2025 revenue remained UAE-affiliated**,
which is concentration risk regardless of culture.

**Verkada is the only company here with a statistically meaningful sample**, at roughly 1,080 reviews,
and its trend is **improving**. Glassdoor's own indicator showed culture at 3.8/5 "as of September 2026"
with sentiment on an upward trend, and the 2020-era "frat culture" complaints are fading from recent
reviews. Two things do not fade, and both matter to you specifically:

- **Engineering rates leadership far lower than the company does.** Software Engineer reviews sit at
  3.7/5 across 40 reviews with work-life balance 3.2 and **CEO approval 59%**, against 86% company-wide.
  Sales rates the CEO at 93%. You would be joining the function that is least happy with leadership.
- **Turnover is roughly double the industry.** IPVM puts 2025 turnover near 27% against about 15%
  industry-wide. Reported, not audited, and concentrated in sales rather than engineering.
- The old scandals are genuinely old (2019-2021), but the pattern is not purely historical: a
  sexual-harassment and retaliation suit was filed in November 2025 over a September 2025 termination.
- The 5-day in-office mandate is real and non-negotiable. Equity vests back-weighted 10/20/30/40.

**Decagon is the most thoroughly documented culture in the study, entirely from first-party sources.**
Nothing here rests on anonymous reviews.

- **Five days a week in office, confirmed twice.** All **147 of 147** live job postings carry the verbatim
  line *"We're an in-office company, driven by a shared commitment to excellence and velocity."* Their own
  blog is explicit on 2026-09-04: *"We're an in-office team (five days a week), and this role is open in
  both New York and San Francisco."* Note their careers landing page states no office policy at all; it
  lives only in the posting boilerplate. A secondhand "3 days per week" claim circulating online is
  **contradicted** by the primary source, including for the Toronto roles.
- **Six-day norm, from the CEO in Forbes** (2025-05-03): six days "isn't required" but "has become part of
  the company culture," with up to a third of then-80 staff working Sundays. Caveat worth carrying: that
  described an 80-person company and Decagon is now far larger, so whether the norm survived is unverified.
- **Corroborated by his own non-denial.** Asked directly on 20VC (2025-09-19) "Do you work Saturdays?",
  Zhang answered: *"Saturdays is essentially the spouse day because most of the team here is married and
  families."* He carves out Saturday and does not address Sunday.
- **His stated philosophy on stress**, same transcript: *"my view is that [mitigating stress] actually does
  more harm than good. And instead you should just embrace the stress and treat it as almost like an
  advantage."* And: *"if you invest so much into wellness stuff, it actually makes the stress worse ...
  we've gotta toughen up."* On himself: *"my style is just naturally very intense ... every day is so
  packed that we definitely lean much more on the intensity side."*
- **A fairness note.** The widely-circulated "Why Remote Work is Total BS" line is a **host-written 20VC
  chapter title, not a Zhang quote**. What he actually says is milder and instrumental: in-person is "way
  more productive" and "allows ideas to flow a lot faster." Also, across all 147 postings there are **zero**
  occurrences of "996", "six days", "hustle" or any hours figure. They do not advertise it.

It is the highest-paying company in the study ($260,000 median software wage) with 49% expected lottery
odds, and the trade is stated openly rather than hidden. That is worth something: you can decide with
your eyes open. Separately, and this one will actively mislead you:
searching "Decagon" on Glassdoor returns **3.8/5 across 261 reviews**, which belongs to
**Decagon Institute, a Nigerian developer-training company** (entity E2972902). The SF AI startup is
entity **E11019880 and has 3 reviews**. Search engines and AI summaries merge the two constantly.
Discard any "Decagon has 3.8 stars" claim.

**Crusoe is the one clear, recent, multi-source culture decline in the study.** Glassdoor's own snippet
shows work-life balance **down 12% over the past year**. The complaints converge on two concrete things
rather than generic griping, which is what makes them credible: a move from three office days to five
that reviewers call "a bait and switch," and "untenable 24hr on-call rotations" that reviewers explicitly
link to "attrition in some of the most critical roles." Notably, Crusoe's own careers page states no
location or hours policy at all, and older Built In copy described a 3-day hybrid. **The gap between the
stated policy and the reported reality is itself the finding**, and it is a fair and specific thing to ask
in an interview: how many days in office, and what does on-call look like for a new grad on this team.

**Commure is now the most concerning company in the study, and this changes my earlier read of it.**
Beyond the worst lottery odds (23.8%) and the 5-day Mountain View mandate, the culture research found
**three separate layoff rounds**, headcount **down 4.9% year over year**, and, most damaging,
**leadership publicly stating there would be no layoffs and then cutting weeks later**. Glassdoor 3.4 across
345 reviews and declining.

**A larger item surfaced late, and it outweighs the layoffs.** STAT News published an investigation on
**2026-08-12** ("Inside a $7 billion Silicon Valley startup's mad dash to automate the business of health
care," Casey Ross and Brittany Trang), based on internal communications, legal filings, customer contracts,
and interviews with over three dozen ex-employees, partners and customers. Its published findings: Commure
*"offers thousands of dollars of compensation to customers and other parties willing to refer its products to
new business prospects,"* while *"some of its customers report suffering steep financial losses."* Commure
says its referral programs are industry standard. Notably, its chief legal officer emailed referral-program
members on **2026-08-06 terminating those payments**, six days before publication and after STAT first
inquired in June. An earlier piece in The Information (2024-01-05) reported clinics whose insurer payments
stopped for nearly two months after switching to an Athelas product.

The STAT body is paywalled and I read only the free portion, so treat the detail as reported-not-fully-read.
But this is a customer-harm and sales-practice story, not a culture complaint, and it is the most serious
finding attached to any company in this study.

**Sourcing caveat on the layoff point:** it comes from Becker's Hospital Review, which returned HTTP 403 and
was read via search snippet rather than in full. The corroborating independent evidence is the headcount
contraction, which is solid. Treat the "said no, then cut" sequence as reported-not-fully-verified.

A company that is contracting while telling staff otherwise is a different proposition from one that is
merely intense. I would deprioritise it despite its sponsorship volume. Read its Glassdoor carefully, too:
reviews are split across three entities (Commure, Athelas, Augmedix) and the review base is dominated by
offshore night-shift medical-scribe roles, which makes the US software engineering experience hard to read
from the aggregate.

**Abnormal AI is declining measurably, which is rarer and more meaningful than a low absolute score.**
Software Engineer reviews are 3.4/5 across 51 reviews with **recommend-to-friend at 44%, down 17 points in
twelve months**. The engineering blog went from monthly to annual. No funding round since August 2024, and
secondary market pricing implies roughly 36% below the last mark. Measured decline on a decent sample size
is a stronger signal than most things in this section.

**Domino Data Lab: I need to walk back part of my earlier recommendation.** I flagged it in section 4 as
"worth a look that the obvious ranking buries," on the strength of its explicit H-1B sponsorship language
and two live new-grad reqs. Both of those facts still hold and they are genuinely valuable. But the culture
research found what the H-1B data could not: **layoffs in 2022 (roughly 25%) and again in 2024 affecting US
engineering**, headcount **down 27% from peak**, a thin engineering blog, and a CEO who had been in the job
four weeks. The pattern reads as stagnation rather than mistreatment, and no one alleges a bad culture.
But "small sponsorship volume" now looks less like a young company ramping up and more like a shrinking one.
**Treat the new-grad reqs as worth applying to and the company as worth diligence, not as a hidden gem.**


**Fireworks and Together AI are not knowable from public sources.** Eleven reviews and fewer than eight
reviews respectively. For Fireworks, the small amount of recent signal is unusually consistent on one
point: centralized, CEO-dominated decision-making, appearing independently on Glassdoor and on Blind.
Two weak signals pointing the same way is worth an interview question, not a conclusion. For Together AI,
there is simply nothing; judge it on the engineering, which is the strongest in the set.

**CoreWeave has the most quotable hours problem in the study, and it comes from a founder.**
Co-founder and Chief Strategy Officer Brian Venturo, describing the GB300 launch team on the TBPN podcast
(2025-07-15), said verbatim: *"they go and they work from 8 am to 4:00 a.m. every day and they love it and
it's a cultural thing."* That is a founder framing roughly 20-hour days as a virtue, on the record.

The reviews corroborate it. Glassdoor sits at 3.4/5 across 90 reviews with **work-life balance at 2.7, down
15% over the past year**. Specific 2026 reviews cite "60-80 hours per week" and "some teams are 24/7." The
job board reinforces it: 231 of 300 postings carry the boilerplate *"we work hard, have fun, and move fast!
... We're not afraid of a little chaos,"* with "chaos" appearing 86 times and "on-call" 44 times.

Two things keep this from being a simple no. First, the pattern is **bimodal and the engineering signal is
the good half**: nearly every negative review still praises the engineers, with complaints concentrated on
middle management and hypergrowth chaos. One July 2026 review rates the company 1.0 overall while listing
"great engineering culture" as the pro. Second, reviewers say experience "will vary drastically across
teams," so the team matters more than the company. Also note only 4 of those 90 reviews are tagged Software
Engineer, so the aggregate is not really measuring your role.

Separately, and relevant to an equity-heavy offer: CoreWeave faces **securities class actions** covering
2025-03-28 to 2025-12-15 (allegations, not findings), its $9B Core Scientific bid was voted down by that
company's shareholders on 2025-10-30, and three senior executives sold over $1B in shares in the first four
months of 2026. The last is normal post-lockup behaviour, not wrongdoing, but it belongs in the picture.

**Baseten is unknowable on culture and impressive on engineering.** Two Glassdoor reviews and one Blind
review for a ~150-person company is not a rating, it is three people, and one of the Glassdoor reviews is
from 2022 and the other from a marketing manager. There is effectively zero recent signal from an engineer.

The one concrete, checkable thing is a policy shift. Baseten's own December 2025 recruiting post describes
"remote teammates across the United States and worldwide." The live Ashby board on 2026-09-24 shows
**101 open roles: 89 San Francisco, 7 New York, 5 London, and zero remote**, with all 37 engineering roles
San Francisco only. Non-engineering postings reference a "standard policy of three days per week in-person."
Given you are location-blind that is not disqualifying, but the gap between the stated posture and the live
board is worth asking about directly.

Encouragingly, the grind language that saturates CoreWeave's board is largely absent from Baseten's:
"fast-paced" appears 17 times across 101 postings and "high-intensity" only in sales roles. No founder
statement glorifying hours was found, though that is "not found" rather than "confirmed absent."

### Engineering credibility, which may matter more to you than ratings

For a new grad, portable resume value is worth more than a half-point of Glassdoor.

- **Together AI: strongest.** Nine ICML 2026 papers, FlashAttention-4, ThunderKittens, ParallelKernelBench.
  Blog cadence 8-12 posts a month, roughly 60% genuinely technical. Named authorship on frontier kernel and
  inference research is portable credit that follows you.
  **One correction worth having:** FlashAttention is not in the Together GitHub org. It lives at
  [Dao-AILab/flash-attention](https://github.com/Dao-AILab/flash-attention) (25,005 stars, pushed 2026-09-24),
  connected through Tri Dao, their Chief Scientist. Real adjacency, commonly overstated. Also, the
  flagship research repos are **dormant**: OpenChatKit last pushed 2024-04-09, MoA 2025-01-07. What is
  active is SDK and infrastructure code. The research reputation is sustained through papers, not repos.
- **Crusoe: moderate.** Genuinely technical blog at 3-4 posts a month, and externally audited MLPerf
  Inference v6.1 submissions, which is a real credibility signal. GitHub `crusoecloud` is active daily but
  low-star (top repo 147), so it reads as a working cloud-provider tooling org, not a research org.
  Credit accrues to the company more than to you.
- **Baseten: strong, and the most readable before an interview.** GitHub `basetenlabs` had **37 repos pushed
  in the last 90 days**. [`truss`](https://github.com/basetenlabs/truss), their model-packaging framework,
  has 1,204 stars and 100+ commits in 90 days, so the flagship is genuinely maintained rather than abandoned.
  They work inside the NVIDIA/UCX inference stack (active forks of Megatron-LM, Model-Optimizer, ucxx). The
  blog is majority deep-dive rather than launch posts. For a new grad this is the best case in the set: you
  can read their actual work before the interview and talk about it.
- **CoreWeave: strong.** 121 repos with 22 pushed in 90 days; `tensorizer` (326 stars) and `nccl-tests` (159)
  are real infrastructure. Upstream contribution to Kubernetes Kueue is genuine, not vendored. The blog is
  roughly half technical deep dives, half Nvidia-launch and analyst press releases.
- **Fireworks: moderate, and more marketing-led than its reputation suggests.** The blog runs about 10 posts
  a month, but only roughly 2 in 10 are deeply technical (the ARCv3 cross-region RL post, the DeepSeek V4 Pro
  benchmark piece); the rest are model launches and positioning. GitHub [`fw-ai`](https://github.com/fw-ai)
  is genuinely active: `cookbook` (214 stars, pushed 2026-09-24), `benchmark` (117), `fireconnect` (53), plus
  real infrastructure work in `DeepGEMM` (FP8 GEMM kernels) and `tokenspeed`. Several repos are forks with
  zero stars. Leadership pedigree is strong (Lin Qiao led PyTorch and Caffe2 at Meta; Dmytro Dzhulgakov was a
  PyTorch core maintainer) and engineering is 44.8% of headcount, the largest function. Net: credibility rests
  on who works there and on live repo activity more than on a deep publishing habit.
- **Decagon: moderate, and better than expected on content, but zero open source.** About 4 of 10 recent posts
  are substantively technical, including "How we made one of our largest inference workloads 4.7x more
  GPU-efficient" (2026-09-03) and DuetBench-2, an in-house agent eval benchmark (2026-09-09). Publishing named
  benchmarks and quantified GPU work is a real signal for an application-layer company. Against that:
  **no public open-source presence at all.** Their real GitHub org is
  [`duet-labs`](https://github.com/duet-labs) (confirmed by its own API metadata: display name "Decagon",
  contact `security@decagon.ai`, created 2023-07-30) and it has **zero public repos**. An arXiv API query for
  the company affiliation returns **zero papers**, and the DuetBench benchmarks are blog posts, not papers,
  and are not open-sourced. Their own Decagon Labs launch promised conference talks; none delivered yet.
  For you that means no way to read their code before joining, and no portable public artifacts.
  (Ignore `decagonhq` on GitHub: its contact is `info@decagon.institute`, the Nigerian company.)

### Still pending

Culture research for **Ramp, Temporal, Scale AI, Abnormal AI, Commure, Domino Data Lab and Cerebras**
had not completed when this was written. Those files land in
[.research/_scratch/culture/](.research/_scratch/culture/) as they finish.

### Claims that did not survive verification

Three widely-repeated things about these companies are wrong. You will encounter all of them.

- **Commure's "six-day, 70-hour weeks."** A scan of all **81 live postings** on Commure's own Ashby board
  found **zero** matches for "6 days", "six days", "70 hour", "80 hour" or "60 hour" — and a second pass over
  **215 archived Commure+Athelas postings** from the Internet Archive (2024-11 to 2025-12) found zero as well.
  The "we are not a work-life balance company" line does not exist either; the only "work-life balance" hit in
  the whole archive is a *positive* one in a Bangladesh HR role. A full Hacker News
  corpus search via the Algolia API found no thread about a Commure recruiting email stating hour
  expectations. The only first-party "long hours" language sits on a **Bangladesh operations role**, not US
  engineering. The "there is no rest" line attributed to CEO Tanay Tandon is a **Glassdoor reviewer's
  characterization, not a quote**. Commure has plenty of real problems; this particular one is not evidenced.
- **Ramp CEO Eric Glyman's "80 or 100 hours a week."** This is a **misattribution**. The actual Fortune
  sentence (2026-07-14) is the journalist paraphrasing that Ramp looks for engineers who, **as teenagers,
  poured "80 or 100 hours a week" into Minecraft**. It is not a Glyman quote and it is not about working
  hours at Ramp. Ramp is genuinely loud about intensity, but no first-party source anywhere states an hour
  count; every circulating figure traces to an anonymous review, a journalist's characterization, or this.
- **Decagon's "Why Remote Work is Total BS."** A host-written 20VC chapter title, not something Jesse Zhang
  said, as covered above.

The pattern is worth internalising: the loudest claims about these companies tend to dissolve on inspection,
while the genuinely damning facts (CoreWeave's founder quote, Decagon's Forbes admission, Abnormal's 17-point
decline) are quieter and better sourced.

### What none of this found

No layoffs at any of the five companies above. No executive statement glorifying long hours at Crusoe or
Together AI, searched across their founders' 2025-2026 interviews and podcast appearances. No credible
allegations against the founders of Crusoe or Together AI. Crusoe's one lawsuit was ordinary commercial
patent litigation with Upstream Data, settled November 2025.

Modeled tenure figures (Crusoe 1.2 years, Together AI 1.3 years) come from Revelio Labs, are estimated from
public profile data rather than measured, and were paywalled when checked. **Do not read them as churn.**
Both companies roughly tripled headcount in the measured window, which drags median tenure down
arithmetically even with zero retention problem.
