> **Evidence only.** Decisions live in issue #9; where this file disagrees, #9 wins. Its recommendations feed `superpowers/specs/2026-10-20-opportunity-pipeline-design.md` and decide nothing on their own. Working files and raw data it mentions stay outside the repo in `~/Desktop/outreach-scratch/`.

# H-1B LCA recon: small employers filing junior software LCAs (FY2025)

Built 2026-10-20 from public DOL data. Everything here is evidence that a company *filed* for a junior-wage
software H-1B, not that it hired anyone. Scripts and intermediate files are in `recon/lca/`.

## Source

- US Department of Labor, OFLC performance data page: https://www.dol.gov/agencies/eta/foreign-labor/performance
- Files used: `LCA_Disclosure_Data_FY2025_Q1.xlsx` to `_Q4.xlsx` (decisions issued 2024-10-01 to 2025-09-30), plus
  `LCA_Disclosure_Data_FY2026_Q3.xlsx`, the newest file posted. FY2026 Q1 and Q2 are no longer on the page; the Q3
  file is cumulative (record layout: "October 1, 2025 through June 30, 2026", 437,496 rows), so it covers Q1 too.
  FY2026 is used only as a "still filing" signal, not in the main counts.
- Record layouts: `LCA_Record_Layout_FY2025_Q4.pdf`, `LCA_Record_Layout_FY2026_Q3.pdf` (saved in `recon/lca/`).
- Download note: plain `curl` gets HTTP 403 (Akamai bot block) on dol.gov. It worked with full browser headers
  (Accept-Encoding, sec-ch-ua, Sec-Fetch-*) and a cookie jar; see `dl.sh`. DOL offers these files as xlsx only.
  They were converted to parquet with python-calamine (`convert.py`).

## Counts at each step

| Step | Rows or employers |
|---|---|
| FY2025 rows, four quarters | 596,552 |
| After dropping duplicate CASE_NUMBERs across quarters (kept latest decision) | 594,821 |
| VISA_CLASS H-1B (E-3 10,344, H-1B1 3,854 dropped) | 580,623 |
| H-1B by status: Certified 537,796; Certified-Withdrawn 30,111; Withdrawn 9,211; Denied 3,505 | |
| Kept: Certified + Certified-Withdrawn | 567,907 |
| Software/data SOCs (listed below) | 312,861 (301,623 Certified, 11,238 Certified-Withdrawn) |
| Rows with a missing SOC code (title fallback) | 0, so the title fallback never fired |

### Software LCAs per SOC (FY2025, H-1B, Certified + Certified-Withdrawn)

| SOC | Title | LCAs | Certified | Cert-Withdrawn | Level I/II | I/II share |
|---|---|---|---|---|---|---|
| 15-1252 | Software Developers | 176,706 | 170,435 | 6,271 | 95,700 | 54.2% |
| 15-1299 | Computer Occupations, All Other | 48,928 | 47,447 | 1,481 | 23,974 | 49.0% |
| 15-2051 | Data Scientists | 32,701 | 31,465 | 1,236 | 19,179 | 58.6% |
| 15-1253 | Software QA Analysts and Testers | 19,341 | 18,600 | 741 | 11,382 | 58.8% |
| 15-1211 | Computer Systems Analysts | 13,625 | 12,926 | 699 | 8,112 | 59.5% |
| 15-1242 | Database Administrators | 7,205 | 6,923 | 282 | 4,580 | 63.6% |
| 15-1244 | Network and Computer Systems Administrators | 5,447 | 5,300 | 147 | 3,644 | 66.9% |
| 15-1243 | Database Architects | 4,954 | 4,733 | 221 | 2,435 | 49.2% |
| 15-1221 | Computer and Information Research Scientists | 3,954 | 3,794 | 160 | 2,018 | 51.0% |

Not included (outside the requested SOC list) but close: 15-1251 Computer Programmers 13,970; 15-1212 Information
Security Analysts 4,155; 15-1241 Network Architects 2,146; 15-1255 Web and Digital Interface Designers 1,690;
17-2061 Computer Hardware Engineers 1,326; 15-1231 693; 15-1254 Web Developers 573; 15-1232 132.

### Junior signal: prevailing wage level

All 312,861 software LCAs: Level I 38,399 (12.3%), Level II 132,625 (42.4%), Level III 66,527 (21.3%), Level IV
51,515 (16.5%), blank 23,795 (7.6%; blank means the wage came from a DOL determination or a survey, not OES).
**Level I or II: 54.7% of all software LCAs, 59.2% of those with a level.**

Median annual wage offered (WAGE_RATE_OF_PAY_FROM, annualized): Level I $90,000; II $109,845; III $140,000;
IV $165,000; blank $145,000.

Junior share by employer size (total FY2025 certified H-1B LCAs, all jobs):

| Employer filings | Employers | Software LCAs | Level I/II share | Level I share | New-employment share of I/II |
|---|---|---|---|---|---|
| 1-25 | 59,355 | 82,392 | 68.4% | 22.9% | 41.7% |
| 26-100 | 2,168 | 53,622 | 61.6% | 15.4% | 34.6% |
| 101-1,000 | 565 | 72,424 | 41.6% | 6.5% | 34.3% |
| 1,000+ | 40 | 104,423 | 49.4% | 6.3% | 30.9% |

The small-filer bucket is the most junior, but it also holds most of the body shops (next section).

## Small employer proxy and the body-shop filter

The LCA file has no headcount. Employer size proxy = number of FY2025 certified or certified-withdrawn H-1B LCAs
under that employer across all occupations. Small = 1 to 25. Employers are grouped by a normalized legal name
(lowercase, punctuation and "Inc/LLC/Corp/Co/Ltd/The/USA" stripped, parentheses and "dba" tails dropped). FEIN is
present on every row and is kept in the output, but it is not the grouping key: PEO and EOR filers (TriNet 73
LCAs, Deel 57, Insperity 25, Velocity Global 11) share one FEIN across many clients and would merge them.

62,128 H-1B employers in FY2025; 26,285 filed a software LCA; 20,678 filed a Level I/II software LCA. Of those,
**18,273 had 1 to 25 LCAs in total**. Exclusions, applied in order (each employer counted once, under the first
rule it hits):

| Rule | Removed |
|---|---|
| NAICS 5613xx (employment placement, temp staffing) | 144 |
| H-1B dependent = Yes on any filing | 2,950 |
| Willful violator = Yes | 4 |
| Any filing placing the worker at a secondary entity (third-party client site) | 2,995 |
| Name contains a staffing or consulting word (consult, staffing, infotech, pvt, outsourc, recruit, talent, workforce, "IT services", "IT solutions", "software solutions", global services, resourc, and similar) | 415 |
| Body-shop profile: NAICS 54151x AND generic tech name (solutions, systems, technologies, tech, soft, global, services, IT, info, digital, data) AND 90%+ of filings in computer jobs AND 80%+ of software filings at Level I/II AND (half or more of filings list several worksites, or 5+ filings) | 273 |
| **Removed in total** | **6,781** |
| **Final small-junior list** | **11,492 employers** |

AGENT_REPRESENTING_EMPLOYER was checked and not used: 75% of all H-1B LCAs use an agent or attorney, so it does not
separate staffing firms from product companies.

What the rules get wrong, seen by hand:
- Collateral: the secondary-entity rule drops some real product companies that place staff at customers
  (EliseAI, UWorld, Hyundai AutoEver). The body-shop profile drops Nexthop Systems and Codapt Technologies (the
  legal entity behind Solid, which he contacted). Both lists are in `excluded_small.csv` with the reason.
- Leakage: small body shops that file every worker at their own address with no secondary entity still get
  through (for example several Irving, Plano, Alpharetta and Edison "X Systems LLC" filers). To handle them, every
  kept row has a `staffing_risk` score (low, medium, high) from six weak signals: IT-services NAICS, generic tech
  name, 90%+ computer-job filings, 100% Level I/II with 70%+ new-employment, three or more worksite states with 10
  or fewer filings, half or more of filings with multiple worksites. Final list: low 8,392, medium 2,359, high 741.
- `cap_exempt_likely` = Y for 487 employers (universities, hospitals, nonprofits, government by NAICS or name).
  These skip the lottery, which matters for him, but they are not small companies.

## The final list

`recon/lca/small_junior_employers.csv`, one row per employer, 11,492 rows, sorted low staffing risk first, then by Level I/II software filings. Columns: normalized name, legal name,
FEIN (most common), city, state, NAICS, total LCAs, software LCAs, Level I/II software LCAs, Level I count,
new-employment Level I/II count, certified-withdrawn counts, sample Level I/II titles, median Level I/II wage,
median software wage, H-1B dependent flag (all N by construction), secondary-entity count, `tier`,
`staffing_risk`, `cap_exempt_likely`, first and last decision date, FY2026 Q1-Q3 total LCAs and Level I/II
software LCAs, worksite states, DBA, law firm, number of FEINs, and the universe match columns.

`tier`: A = software publisher, data, internet, R&D or hardware NAICS (1,871); B = computer-services NAICS 5415xx
that passed the filters, often startups that picked 541511 (3,253); C = every other industry, such as banks,
insurers, retailers, hospitals (6,368).

- Software LCAs at these employers: 31,295, of which 20,249 (64.7%) are Level I/II; 9,899 of those are
  new-employment petitions (the box a cap petition for an F-1/OPT hire uses); 6,659 employers have at least one.
- Size: 4,317 filed exactly 1 LCA, 2,240 filed 2, 2,424 filed 3 to 5, 1,382 filed 6 to 10, 1,129 filed 11 to 25.
- 4,075 have 2+ Level I/II software LCAs; 1,917 have 3+.
- Still filing: 6,083 filed again in FY2026 Q1-Q3; 3,527 filed another Level I/II software LCA.
- Median of employer median Level I/II software wage: $102,937.
- The most useful slice for outreach is low risk, not cap-exempt, tier A or B: **2,902 employers**, 800 of which
  filed Level I/II software again in FY2026.

Top states (all 11,492): CA 2,588; TX 1,254; NY 1,178; MA 553; IL 549; NJ 512; FL 474; GA 404; VA 376; PA 343;
WA 270; OH 262.

Top cities (employer address): New York 745; San Francisco 472; Chicago 254; Houston 225; Boston 163; Austin 163;
Atlanta 156; San Jose 147; Dallas 145; Palo Alto 115; Los Angeles 110; Irvine 106; Santa Clara 95; Seattle 94;
Plano 92. Low-risk only: New York 580, San Francisco 331, Houston 189, Chicago 164, Atlanta 110, Boston 108.

## 50 example product companies (none are in his radar, dashboard, or contacted list)

Picked by hand from the low-risk tier A/B slice: product companies I recognise or whose titles read like
product engineering, sorted by Level I/II software filings. "New-hire I/II" = Level I/II software LCAs with
new employment marked. Level II often sits on titles that say Senior (see limits).

| # | Employer (LCA legal name) | City, State | NAICS | FY25 LCAs (all jobs) | Software | Level I/II software | New-hire I/II | FY26 Q1-Q3 I/II software | Median I/II wage | Sample titles |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Nimble Robotics, Inc. | San Francisco, CA | 541715 | 14 | 12 | 9 | 4 | 3 | $155,000 | Software Engineer Iii, Cloud; Machine Learning Software Engineer Ii |
| 2 | Contextual AI, Inc. | Mountain View, CA | 541715 | 16 | 14 | 8 | 6 | 7 | $175,000 | Member Of Technical Staff; Product Manager |
| 3 | Auger Inc. | Bellevue, WA | 513210 | 17 | 17 | 7 | 2 | 21 | $230,000 | Software Development Engineer; Software Development Engineer - I |
| 4 | Anchor Labs Inc. | New York, NY | 541511 | 16 | 11 | 6 | 2 | 7 | $135,250 | Member Of Technical Staff, Direct Custody; Member Of Technical Staff, Transparency |
| 5 | Kodiak Robotics, Inc | Mountain View, CA | 541715 | 16 | 9 | 6 | 5 | 4 | $152,500 | Software Engineer; Perception Software Engineer |
| 6 | AppZen, Inc. | San Jose, CA | 513210 | 9 | 9 | 6 | 2 | 1 | $172,931 | Data Scientist; Lead Frontend Software Engineer |
| 7 | Sitetracker, Inc. | Montclair, NJ | 541511 | 12 | 8 | 6 | 2 | 1 | $107,500 | Software Engineer Ii; Senior Software Development Engineer In Test Ii |
| 8 | Rec Room Inc. | Seattle, WA | 513210 | 9 | 7 | 5 | 2 | 5 | $115,000 | Software Engineer; Game Developer |
| 9 | Socure, Inc. | Incline Village, NV | 541512 | 16 | 13 | 5 | 2 | 4 | $155,000 | Data Scientist Ii; Data Scientist Iii |
| 10 | PartnerTap, Inc | Seattle, WA | 54151 | 5 | 5 | 5 | 2 | 3 | $68,702 | Qa Engineer; Software Engineer |
| 11 | Nexa AI Inc | Cupertino, CA | 541715 | 5 | 5 | 5 | 3 | 1 | $150,000 | Software Engineer; Data Scientist |
| 12 | Aerospike, Inc. | Mountain View, CA | 511210 | 10 | 10 | 5 | 1 | 1 | $155,000 | Software Engineer; Quality Engineer Ll |
| 13 | Cequence Security, Inc. | Santa Clara, CA | 511210 | 16 | 8 | 5 | 1 | 1 | $141,107 | Data Scientist; Senior Software Engineer |
| 14 | Retrocausal, Inc. | Redmond, WA | 541511 | 6 | 5 | 5 | 3 | 0 | $115,000 | Software Engineer; Research Engineer |
| 15 | Nexusflow.ai Inc. | Orinda, CA | 513210 | 5 | 5 | 5 | 3 | 0 | $117,291 | Machine Learning Engineer; Founding Engineer |
| 16 | Itential, Inc. | Atlanta, GA | 513210 | 6 | 6 | 4 | 2 | 8 | $116,750 | Engineering Manager; Network Automation Engineer |
| 17 | Modernizing Medicine, Inc. | Boca Raton, FL | 511210 | 8 | 7 | 4 | 2 | 8 | $115,600 | Salesforce Administrator; Senior Data Engineer 1 |
| 18 | Opusclip Inc. | San Francisco, CA | 541512 | 7 | 5 | 4 | 1 | 7 | $163,282 | Software Development Engineer In Test; Senior Software Engineer |
| 19 | Boson AI USA Inc. | Santa Clara, CA | 541715 | 4 | 4 | 4 | 2 | 2 | $247,500 | Machine Learning Scientist; Machine Learning Engineer |
| 20 | Statsig, Inc. | Bellevue, WA | 51821 | 14 | 9 | 4 | 3 | 0 | $142,500 | Software Engineer; Data Scientist |
| 21 | StarTree, Inc. | Mountain View, CA | 513210 | 7 | 6 | 4 | 2 | 0 | $165,466 | Senior Software Engineer Ii; Sr. Software Engineer Ii |
| 22 | Aurora Solar, Inc. | San Francisco, CA | 513210 | 6 | 5 | 4 | 0 | 0 | $172,500 | Software Engineer, Full Stack; Software Developers |
| 23 | Spectro Cloud, Inc. | San Jose, CA | 54151 | 8 | 4 | 3 | 3 | 6 | $146,307 | Software Engineer |
| 24 | BitGo, Inc. | Palo Alto, CA | 541511 | 20 | 14 | 3 | 0 | 5 | $175,000 | Software Engineer - E2; Engineer - E2 |
| 25 | Qurrent, Inc. | San Francisco, CA | 513210 | 4 | 3 | 3 | 2 | 3 | $160,000 | Lead Ai Strategist; Solutions Engineer |
| 26 | Waabi Innovation US Inc. | San Francisco, CA | 541511 | 8 | 4 | 3 | 2 | 3 | $180,000 | Software Developer Ii; Staff Researcher |
| 27 | FORTANIX INC | Santa Clara, CA | 541519 | 9 | 7 | 3 | 0 | 3 | $150,000 | Software Engineer Iii; Scrum Master |
| 28 | Ambient AI, Inc. | Redwood City, CA | 518210 | 6 | 5 | 3 | 2 | 2 | $156,000 | Senior Applied Research Scientist; Applied Research Scientist |
| 29 | Rocket Lawyer, Inc. | San Francisco, CA | 511210 | 6 | 5 | 3 | 1 | 2 | $170,000 | Ai/Machine Learning Software Engineer; Product Security Engineer |
| 30 | Hover Inc. | San Francisco, CA | 513210 | 6 | 5 | 3 | 2 | 1 | $160,000 | Lead Analyst, Product Analytics & Strategy; Senior Computer Vision Engineer |
| 31 | TensorOpera, Inc. | Palo Alto, CA | 513210 | 5 | 4 | 3 | 2 | 1 | $165,000 | Software Engineer; Machine Learning Engineer |
| 32 | Kneron Inc. | San Diego, CA | 511210 | 3 | 3 | 3 | 1 | 1 | $117,700 | System Engineer; Software Engineer |
| 33 | Wiliot Inc. | San Diego, CA | 518210 | 5 | 4 | 3 | 3 | 1 | $96,000 | Solution Engineer; Data Scientist |
| 34 | RAD AI, INC. | San Francisco, CA | 541714 | 13 | 10 | 3 | 1 | 1 | $180,000 | Software Engineer; Senior Software Engineer, Full Stack - Core Services |
| 35 | Fastbreak.ai. Inc. | Charlotte, NC | 511210 | 3 | 3 | 3 | 0 | 1 | $135,000 | Senior Quality Assurance Engineer; Quality Assurance Engineer |
| 36 | Comma.ai, Inc. | San Diego, CA | 541511 | 7 | 4 | 3 | 2 | 0 | $115,000 | Machine Learning Engineer; Research Engineer |
| 37 | SentiLink Corp. | San Francisco, CA | 518210 | 7 | 5 | 3 | 0 | 0 | $190,000 | Senior Data Scientist; Senior Business Analyst |
| 38 | Orby AI, Inc. | Mountain View, CA | 541715 | 7 | 6 | 3 | 3 | 0 | $180,000 | Software Engineer, Machine Learning; Senior Software Engineer (Full Stack) |
| 39 | Embedchain Inc. | San Francisco, CA | 54151 | 3 | 3 | 3 | 3 | 0 | $150,000 | Lead Software Developer |
| 40 | Cognitiv Corp | New York, NY | 541511 | 6 | 5 | 2 | 2 | 8 | $139,568 | Software Engineer Ii; Data Science Analyst Ii |
| 41 | Cribl, Inc. | San Francisco, CA | 54151 | 15 | 11 | 2 | 0 | 4 | $140,572 | Senior Designated Support Engineer; Software Engineer Ii |
| 42 | Forward Networks, Inc. | Santa Clara, CA | 541511 | 8 | 6 | 2 | 1 | 4 | $180,336 | Member Of Technical Staff |
| 43 | Topaz Labs, LLC | Dallas, TX | 511210 | 5 | 4 | 2 | 2 | 4 | $164,860 | Deep Learning Researcher; Software Engineer |
| 44 | DUPLOCLOUD, INC. | San Jose, CA | 541512 | 8 | 3 | 2 | 1 | 3 | $120,000 | Machine Learning Engineer; Qa Engineer |
| 45 | Span.IO, Inc. | San Francisco, CA | 54171 | 16 | 5 | 2 | 2 | 3 | $148,000 | Mobile Engineer; Senior Data Scientist |
| 46 | Abstract Security Inc. | Palo Alto, CA | 513210 | 2 | 2 | 2 | 2 | 3 | $105,000 | Software Engineer, Streaming Platform |
| 47 | CelerData Inc | Menlo Park, CA | 518210 | 5 | 2 | 2 | 2 | 2 | $160,000 | Senior Software Engineer |
| 48 | FrontApp, Inc. | San Francisco, CA | 51321 | 9 | 5 | 2 | 2 | 2 | $177,500 | Software Engineer |
| 49 | Spot AI, Inc. | Lehi, UT | 541511 | 4 | 2 | 2 | 0 | 2 | $154,094 | Sr. Software Engineer, Ai; Lead Technical Support Engineer |
| 50 | HiddenLayer, Inc. | Leander, TX | 541519 | 2 | 2 | 2 | 2 | 1 | $185,000 | Machine Learning Engineer |

## Cross-reference with his company universe

Sources, read only: `radar.db` table `board` (508 boards, 494 distinct companies; opened with Python sqlite3 in
`mode=ro` because the macOS sqlite3 CLI would not open the file), the outreach repo's `dashboard/data.js`
`window.COMPANIES` (1,010 companies, read from the yamoussoukro worktree copy; includes the E-Verify legal entity
names researched earlier), and `contacted.txt` (89 distinct company strings).

Matching (`xref.py`, `review.py`): each name and each parenthesized alias was normalized the same way as the LCA
names, then matched against FY2025 legal names and DBA names in four passes: exact normalized key; same key with
spaces removed; key with a generic tail word removed (ai, labs, technologies, tech, software, systems, health,
holdings, group, platform) when 4+ characters remain; fuzzy (rapidfuzz token_sort_ratio 92+, names of 6+
characters) against the small list. Then every match into the small list was reviewed by hand.

Precision, measured on that review: exact 202 right, 4 wrong, 7 unsure (about 95%); tail-strip 29 right, 24 wrong,
13 unsure (about 45%); fuzzy 0 right of 10. Wrong matches were dropped. The false-positive risk that remains is in
short one-word names (Arch, Mesh, Marble, Campfire, Atlas): treat rows with `universe_match_review = UNSURE` as
unconfirmed. Misses also happen when the legal name differs from the brand and no alias was on file (likely
example: Mem0 files as Embedchain Inc., which is in the 50 above).

| Universe | Companies | Matched any FY2025 LCA (raw) | In small-junior list, reviewed OK | Plus unsure |
|---|---|---|---|---|
| radar.db boards | 494 | 229 | 75 | 87 |
| outreach dashboard | 1,010 | 469 | 171 | 183 |
| contacted.txt | 89 | 56 | 19 | 19 |

Contacted companies in the small-junior list (19): Anysphere (Cursor), Arize AI, Baseten, Coder, Cohere,
DataRobot, DevRev, Distyl AI, Factory (The San Francisco AI Factory, Inc.), Federato, Fireworks AI, Glean,
Happyrobot, Klavis AI, Patronus AI, RapDev, Retell AI, SpaceX, Lila Sciences. Several are not small (SpaceX,
Cohere, DataRobot): they file few H-1Bs, which is the weakness of the proxy.

33 contacted names have no FY2025 LCA under any form of their name, for example Greptile, Cline, Composio,
Daytona, Continue.dev, Blitzy, Qodo, Render, Sourcegraph, OpenHands, Mem0 (but see Embedchain). Either they filed no
H-1B in FY2025 or they file under a different legal name. Contacted companies that filed but are not in the list
are either too big (Stripe, Databricks, Snowflake, HubSpot, Datadog), filed no Level I/II software LCA (Fleet AI,
Teradar, Exa Labs, LiveKit, Docker), or were excluded (EliseAI: secondary entity; Solid/Codapt: body-shop profile).

**What he is missing:** 229 of the 11,492 small-junior employers are in his universe (212 confirmed). 11,263 are
not. In the low-risk, not-cap-exempt slice, 7,809 of 7,942 are not in his universe; in the low-risk tier A/B
slice, 2,805 of 2,902 are not, and 742 of those also filed Level I/II software in FY2026 Q1-Q3.

## E-Verify

Not checked. The E-Verify Employer Search (https://www.e-verify.gov/e-verify-employer-search) is a search tool
updated daily with no bulk download, and it lists only employers that self-report five or more employees. The
outreach dashboard already holds E-Verify status for 316 companies from earlier one-by-one lookups.

## Limits

- An LCA is not a hire. Employers file LCAs before the lottery, file several per person, and file for
  extensions and amendments. Certified-Withdrawn (11,238 software LCAs) often means the job or the petition ended.
- Wage level is a weak junior signal. The employer picks the level from the job's requirements against local OES
  data; many Level II filings carry titles like "Senior Software Engineer" or "Staff". Level I is the cleaner signal
  (12.3% of software LCAs).
- Few LCAs does not mean a small company. Mid-size and large firms with little H-1B use (SpaceX, Motorola Mobility,
  Sony, Dun & Bradstreet) and big-company subsidiaries filing under their own entity (IMDb.com) land in the 1 to 25
  bucket. Cross-check size before outreach.
- Name normalization groups by legal name. One company with two legal names (Amazon Data Services vs Amazon.com
  Services) splits; two different companies with the same cleaned name merge. Brands often differ from the legal
  name (Sentry is Functional Software, Teleport is Gravitational, Bland AI is Intelliga).
- PEO and EOR filings (TriNet, Deel, Insperity, Velocity Global, Justworks, ADP TotalSource) hide the real
  company: those LCAs count for the PEO, not the client, so some small startups are invisible here.
- Employer city is the filing address. Many startups use a Delaware or registered-agent address (Dover, Wilmington),
  and worksites can be elsewhere.
- Body-shop filtering is heuristic; see the collateral and leakage notes above.

## Files

- `recon/lca/small_junior_employers.csv`: the list (11,492 rows).
- `recon/lca/excluded_small.csv`: the 6,781 small employers removed, with the reason.
- `recon/lca/universe_matches.csv`, `universe_matches_small_reviewed.csv`: every universe match with method and review.
- `recon/lca/stats.json`, `xref_stats.json`, `review_stats.json`: raw numbers behind this file.
- `recon/lca/employers_fy2025.parquet`: all 62,128 FY2025 H-1B employers with the same aggregates.
- Scripts: `dl.sh`, `convert.py`, `analyze.py`, `xref.py`, `review.py`, `pick50.py`. Raw xlsx and parquet are kept.
