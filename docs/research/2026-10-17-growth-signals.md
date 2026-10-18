# Growth-moment signals: data sources (researched 2026-10-17)

Goal: find US companies under ~200 people, seed through Series E, that just raised and are starting to hire engineers, in the user's themes. Claims carry a source URL. "Measured" means I computed it from the primary data on 2026-10-17. "UNVERIFIED" means no primary source confirmed it.

## 1. Funding signals

### SEC EDGAR, Form D

- **Access rules.** Max 10 requests/second per user across all machines, and automated clients must declare a User-Agent like `Sample Company Name AdminContact@<domain>.com` ([webmaster FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions), [developer resources](https://www.sec.gov/about/developer-resources)). The repo's `.edgar_search.py` already sends one.
- **Submissions API.** `https://data.sec.gov/submissions/CIK##########.json`, per-company filing history, "typical processing delay of less than a second"; nightly bulk `submissions.zip` ([EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)). Good for "has this company filed a new D?" once you know the CIK.
- **Discovery feeds.** (a) Daily form index, e.g. `Archives/edgar/daily-index/2026/QTR4/form.20261015.idx`: 345 `D`/`D/A` lines on 2026-10-15 (measured). (b) Latest-filings Atom feed, `browse-edgar?action=getcurrent&type=D&output=atom`, which showed filings from the prior evening (measured). (c) Full-text search JSON at `efts.sec.gov/LATEST/search-index?forms=D&startdt=...&enddt=...` returned 344 Form Ds for 2026-10-14 (measured). The FTS UI is documented ([FAQ](https://www.sec.gov/edgar/search/efts-faq.html)); the `efts` JSON endpoint behind it is not, so treat it as unofficial. Form D carries almost no prose, so keyword search ("artificial intelligence") is nearly useless (1 hit for Oct 1-16, measured).
- **Structured data sets.** Quarterly TSVs (FORMDSUBMISSION, ISSUERS, OFFERING, RELATEDPERSONS, RECIPIENTS, SIGNATURES); 2026 Q3 is posted ([Form D data sets](https://www.sec.gov/data-research/sec-markets-data/form-d-data-sets)). Useful OFFERING columns: `INDUSTRYGROUPTYPE`, `REVENUERANGE`, `SALE_DATE`, `ISAMENDMENT`, `TOTALOFFERINGAMOUNT`, `TOTALAMOUNTSOLD`, `TOTALNUMBERALREADYINVESTED`, `ISEQUITYTYPE`, `DESCRIPTIONOFOTHERTYPE`, `ISPOOLEDINVESTMENTFUNDTYPE`; ISSUERS has `YEAROFINC_VALUE_ENTERED` and address; RELATEDPERSONS lists executives and directors (measured from `2026q3_d.zip`). Too slow for alerts, ideal for backfill.
- **Latency.** The rule is "within 15 days after the first sale," where first sale means the first investor is "irrevocably contractually committed" ([SEC Form D page](https://www.sec.gov/resources-small-businesses/exempt-offerings/filing-form-d-notice)). Measured on 2026 Q3 new, non-fund filings (n=3,441): median filing lag 15 days, p25 11, p75 42, p90 202; 16.9% filed more than 90 days after first sale. Tech subset (Other Technology + Computers, n=970): median 15, p75 35.
- **Round size and industry.** `TOTALAMOUNTSOLD` and `TOTALOFFERINGAMOUNT` give size (96 of 1,031 new tech filings said "Indefinite"); median sold among new tech filings was $1.0M, p90 $25M (measured). `INDUSTRYGROUPTYPE` is coarse: no "AI" bucket, so tech startups land in "Other Technology". `REVENUERANGE` is mostly "Decline to Disclose" (9,063 of 15,952 Q3 filings, measured). No headcount field.
- **Blind spots.** Form D is only for Reg D (504/506) offerings ([SEC](https://www.sec.gov/resources-small-businesses/exempt-offerings/filing-form-d-notice)); issuers can rely on Securities Act 4(a)(2) directly ([15 U.S.C. 77d](https://www.law.cornell.edu/uscode/text/15/77d)) and file nothing. Skipping Form D only costs the exemption if a court has enjoined the issuer for not filing ([17 CFR 230.507](https://www.ecfr.gov/current/title-17/section-230.507)), so many seed rounds never appear. SAFEs do show up sometimes: 230 of 1,031 new Q3 tech filings name a SAFE in `DESCRIPTIONOFOTHERTYPE` (measured). Filer names are legal entities ("Acme Labs, Inc."), so you need fuzzy matching to brand names. Non-US parents may file under a different entity.
- **Tooling.** `edgartools` (MIT) has a Form D parser ([repo](https://github.com/dgunning/edgartools), `edgar/offerings/formd.py`).

### Other funding sources

| Source | Coverage | Latency | Cost / terms |
|---|---|---|---|
| yc-oss/api ([repo](https://github.com/yc-oss/api)) | 6,307 YC companies; `team_size`, `isHiring`, `batch`, `stage`, `one_liner`, `long_description`, `tags`; 1,492 flagged hiring (measured via [meta.json](https://yc-oss.github.io/api/meta.json)) | Daily GitHub Action, plus `changes/latest.json` diff | Free. Unofficial, pulled from YC's Algolia index; repo has no license (GitHub API `license: null`) |
| Crunchbase API | Best general funding DB | Fast | No new Basic API keys ([docs](https://data.crunchbase.com/docs/crunchbase-basic-using-api)); API is sales-only ([docs](https://data.crunchbase.com/docs/using-the-api)). Pro reportedly $49/mo annual with no API (UNVERIFIED, secondary [source](https://pipeline.zoominfo.com/sales/crunchbase-pricing)). Out of budget |
| HN "Who is hiring" via Algolia | Monthly thread (Oct 2026 is item 49922569, measured); founders post directly, many small AI cos | Monthly | Free; `search_by_date?tags=story,author_whoishiring`, then `items/:id`; 10,000 requests/hour/IP ([hn.algolia.com/api](https://hn.algolia.com/api)) |
| TechCrunch / Crunchbase News RSS | Announced rounds, biased to larger raises | Same day | Free feeds ([TC venture](https://techcrunch.com/category/venture/feed/), [CB News](https://news.crunchbase.com/feed/)), both live on 2026-10-16 (measured). Reuse terms for automated parsing UNVERIFIED; fine for personal reading |
| Product Hunt API | Launches, not rounds | Real time | GraphQL, developer token; "must not be used for commercial purposes" ([docs](https://api.producthunt.com/v2/docs)). Personal use OK, weak signal |
| VC portfolio pages | Per-firm lists | Irregular | No APIs; per-site terms. UNVERIFIED as a reliable feed |
| OSS trackers | None mature found; GitHub search turned up only 0-15 star repos (measured) | | |

## 2. Size and headcount without LinkedIn

- **ATS posting counts (realistic, already have).** Open-req count is a proxy for hiring intent, not headcount. Greenhouse list endpoint needs no auth ([docs](https://docs.greenhouse.io/job-board.html)) and returns `first_published` and `updated_at` per job (measured on a live board). Lever returns `createdAt` and optional `salaryRange` ([repo](https://github.com/lever/postings-api), field presence measured). Ashby's `publishedAt` is "when the job was last published," so it resets on republish ([docs](https://developers.ashbyhq.com/docs/public-job-posting-api)).
- **LCA counts (realistic, already have).** DOL publishes LCA disclosure files quarterly, latest FY2026 Q3 ([DOL performance data](https://www.dol.gov/agencies/eta/foreign-labor/performance)). Distinct LCAs per year is a "sponsorship apparatus" signal and a weak size floor, not a headcount. USCIS Data Hub gives approvals by employer through FY2026 Q3 ([USCIS](https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub)).
- **Form D (partial).** `TOTALNUMBERALREADYINVESTED` and amount sold hint at stage; no headcount.
- **YC `team_size` (realistic for YC companies).** 4,084 of 4,352 active YC companies report 200 or fewer (measured). Self-reported and can be stale.
- **GitHub org activity (realistic, noisy).** 5,000 requests/hour with a token, 60 without ([GitHub docs](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)). Count of distinct recent committers on public repos is a lower bound for infra/devtools companies only.
- **People Data Labs.** Free plan "up to 100 monthly records" ([pricing](https://www.peopledatalabs.com/pricing/company)); `employee_count_by_month` is a base field, `employee_growth_rate` and funding fields are premium ([schema](https://docs.peopledatalabs.com/docs/company-schema)). PDL counts derive from profile data. Realistic for spot checks of ~100 shortlisted companies/month.
- **Apollo.** API only on Custom plans ([pricing](https://www.apollo.io/pricing)). Not realistic.
- **Harmonic.** Pricing by contact, aimed at investors and GTM teams, no free tier listed ([pricing](https://harmonic.ai/pricing)). Not realistic.
- **The Org.** No first-party public API found; third-party Apify scrapers exist ([Apify](https://apify.com/canadesk/the-org/api/python)). Not recommended (terms risk, UNVERIFIED).
- **Wikidata.** Property P1128 "employees" with point-in-time qualifier ([P1128](https://www.wikidata.org/wiki/Property:P1128)). Spot check: ElevenLabs has an item, Braintrust and Vapi do not (measured). Too sparse for startups under 200.

## 3. Hiring velocity

Compute from your snapshots, not from ATS dates:

1. Key each posting by (ats, board, job_id). Store `first_seen` and `last_seen` per snapshot run; keep Greenhouse `first_published` and Lever `createdAt` as secondary evidence (Ashby `publishedAt` can reset).
2. Classify engineering roles with the existing ENG regex.
3. Per company: `new_eng_30d` = engineering postings with `first_seen` in the last 30 days; `open_eng` = currently live; `velocity_ratio` = new_eng_30d / max(1, mean of prior three 30-day windows). Flag when `new_eng_30d >= 3` and ratio >= 2, or when a board goes from 0 to any engineering posting.
4. With twice-weekly crawls, `first_seen` error is at most ~3.5 days (per `.context/architecture-map.md`), fine for a 30-day window.
5. Join to funding: days from Form D `SALE_DATE` or press date to the velocity flag.

**Evidence on funding-to-hiring lag.**
- Davila, Foster and Gupta (2003), 494 mainly Silicon Valley startups, 1994-2000, study employee growth around VC funding events ([DOI 10.1016/S0883-9026(02)00127-1](https://doi.org/10.1016/S0883-9026(02)00127-1); [Stanford GSB](https://www.gsb.stanford.edu/faculty-research/working-papers/venture-capital-financing-growth-startup-firms)). Their specific month-by-month result is UNVERIFIED here (full text paywalled).
- Signalbase (vendor, June 2026, 474 funded startups, 107 with signals): median 35 days from announcement to first observable hire; Seed 30d, Series A 54d, B 26d, C 20d; 63% of first signals were job changes, not postings ([article](https://www.trysignalbase.com/article/is-the-fundraising-signal-still-useful-in-2026)). Vendor data, not peer reviewed.
- Employbl reports a controlled comparison (41% posted more jobs after a raise vs 37% in no-raise quarters, median 62 days to first post-raise posting): UNVERIFIED, page rate-limited, seen only in a search snippet ([link](https://www.employbl.com/blog/post-raise-hiring-spike-control-group)).
- Practical read: watch boards for 30 to 90 days after a Form D, and treat Form D's own ~15-day median lag as part of that window.

## 4. Pay

### Pay-transparency laws

Verified on primary sources by a sub-agent on 2026-10-17. Rule: the posting must carry a pay range when the job could be filled in, or reports into, the jurisdiction.

| Jurisdiction | Law | Posting rule effective | Employer size | Remote roles |
|---|---|---|---|---|
| California | Lab. Code 432.3 (SB 1162; "pay scale" redefined by SB 642) | Jan 1, 2023 (inferred; leginfo page did not load) | 15+, at least 1 in CA | Covered if the job may ever be filled in CA, in person or remote ([DLSE](https://www.dir.ca.gov/dlse/california_equal_pay_act.htm)) |
| New York State | Labor Law 194-b | Sept 17, 2023 | 4+ | Covered if done partly in NY or reporting into NY ([NY DOL](https://dol.ny.gov/pay-transparency-act-frequently-asked-questions)) |
| New York City | NYC Human Rights Law | Nov 1, 2022 | 4+ | Covered if done in whole or part in NYC, including from home ([CCHR](https://www.nyc.gov/assets/cchr/downloads/pdf/publications/Salary-Transparency-Factsheet.pdf)) |
| Washington | RCW 49.58.110 (amended by SB 5408) | Jan 1, 2023 | 15+ | Covered if it could be filled by a WA-based employee ([L&I](https://lni.wa.gov/workers-rights/_docs/ES.E.2.pdf)) |
| Colorado | C.R.S. 8-5-201 | Jan 1, 2021 | 1+ in CO | Covered unless performable solely outside CO (CDLE INFO #9A) |
| Illinois | 820 ILCS 112/10(b-25) | Jan 1, 2025 | 15+ | Covered if partly in IL or reporting into IL ([IDOL](https://labor.illinois.gov/faqs/equal-pay-act-salary-transparency-faq.html)) |
| Massachusetts | G.L. c.149 s.105F | Oct 29, 2025 | 25+ | Covered if MA is the primary place of work ([AGO](https://www.mass.gov/doc/ago-wage-transparency-act-faqs-revised-11132025/download)) |
| Maryland | Lab. & Empl. 3-304.2 | Oct 1, 2024 | All | Covered if physically performed partly in MD ([MD Labor](https://labor.maryland.gov/labor/wages/esswagerangefaq.shtml)) |
| Minnesota | Minn. Stat. 181.173 | Jan 1, 2025 | 30+ in MN | Statute silent (UNVERIFIED) ([statute](https://www.revisor.mn.gov/statutes/cite/181.173)) |
| Hawaii | Act 203 (2023) | Jan 1, 2024 | 50+ | Not addressed (UNVERIFIED) |
| District of Columbia | D.C. Code 32-1453.01 | June 30, 2024 | All with DC employees | Postings soliciting DC employees |
| Vermont | 21 V.S.A. 495p | July 1, 2025 | 5+ | Covered if remote but predominantly for a VT office |
| New Jersey | N.J.S.A. 34:6B-23 | June 1, 2025 | 10+ | Covers national postings and remote jobs performable from NJ ([NJ DOL](https://www.nj.gov/labor/myworkrights/wages/pay-transparency/)) |
| Virginia | Va. Code 40.1-28.7:12 | July 1, 2026 | No threshold | Not addressed |
| Maine | 26 MRSA 622-A | July 29, 2026 | 10+ | Not addressed |
| Connecticut | C.G.S. 31-40z (P.A. 26-12) | Oct 1, 2026 | 1+ | Covered if done in CT or reporting into CT |
| Delaware | 19 Del. C. 709C | Sept 26, 2027 | 26+ | DE jobs plus US remote roles of DE-based employers |
| Cleveland, OH | Codified Ord. ch. 669 | Oct 27, 2025 | 15+ in the city | UNVERIFIED |

Practical read: almost every US startup hub (SF Bay Area, NYC, Seattle, Boston, Chicago, Denver) is covered, and many remote US postings are pulled in by the CA, CO, WA and NJ remote rules, so most in-scope postings should carry a range. Ashby exposes it via `?includeCompensation=true`; Lever has an optional `salaryRange`; otherwise parse the description.

### Mapping a posted salary to an H-1B wage level

- **Rule.** DHS final rule, "Weighted Selection Process for Registrants and Petitioners Seeking To File Cap-Subject H-1B Petitions," 90 FR 60864, published 2025-12-29, FR Doc 2025-23853, effective 2026-02-27 ([Federal Register](https://www.federalregister.gov/documents/2025/12/29/2025-23853/weighted-selection-process-for-registrants-and-petitioners-seeking-to-file-cap-subject-h-1b)). Registrations are weighted by "the highest OEWS wage level that the beneficiary's proffered wage will equal or exceed for the relevant SOC code in the area(s) of intended employment": level IV enters the pool four times, III three, II two, I once. Multiple worksites use the lowest corresponding level. Wages below level I are grouped with level I ([full text](https://www.federalregister.gov/documents/full_text/text/2025/12/29/2025-23853.txt)).
- **Data.** OFLC wage files, 2026-27 wage year (7/2026 to 6/2027), `OFLC_Wages_2026-27.zip` ([FLAG downloads](https://flag.dol.gov/wage-data/wage-data-downloads)); interactive version is the [OFLC Wage Search](https://flag.dol.gov/wage-data/wage-search). `ALC_Export.csv` has `Area, SocCode, GeoLvl, Level1..Level4, Average` as hourly rates; `Geography.csv` maps county to `Area` (MSA code) (measured).
- **SOC 15-1252 (Software Developers), annual = hourly x 2080, measured from ALC_Export:**

| MSA (Area) | Level I | II | III | IV |
|---|---|---|---|---|
| San Jose (41940) | $152,797 | $187,075 | $221,374 | $255,653 |
| SF-Oakland (41860) | $137,093 | $165,131 | $193,149 | $221,187 |
| Boston (14460) | $114,275 | $138,237 | $162,198 | $186,160 |
| Seattle (42660) | $111,613 | $143,104 | $174,595 | $206,086 |
| NYC (35620) | $109,845 | $137,717 | $165,589 | $193,461 |
| Austin (12420) | $95,888 | $119,642 | $143,395 | $167,149 |

- **Mapping.** Resolve the posting's worksite to a county, then to `Area` via Geography.csv; look up 15-1252 in ALC_Export; level = highest N where posted salary >= LevelN x 2080. For a range, compute the level at both the minimum and the midpoint and show both (the employer chooses the proffered wage). For remote roles, use the MSA where you would actually work (UNVERIFIED that this is how every employer will file; confirm with them). The SOC choice (15-1252 vs 15-1299 or 15-2051 for ML roles) changes the answer; default to 15-1252 and flag ML titles.

## 5. Thesis tagging

- **Description sources (free).** yc-oss `one_liner` and `long_description`; ATS job descriptions (Greenhouse with `?content=true`, Lever `descriptionPlain`); the company homepage `<meta name="description">`; HN hiring comments; Wikidata descriptions for larger companies.
- **Embeddings.** `all-MiniLM-L6-v2` runs locally, Apache 2.0, 384 dimensions, 256 word-piece limit ([model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)). Write each theme as 3 to 5 example sentences, embed, take the max cosine per theme, and tune a threshold on ~50 hand-labeled companies.
- **LLM classification.** Claude Haiku 5.5 costs $0.10/MTok input and $0.50/MTok output, half that on the Batch API ([pricing](https://platform.claude.com/docs/en/about-claude/pricing)). Ten thousand companies at ~500 input and ~50 output tokens is about $0.40 in batch. Use embeddings to shortlist, then the LLM to assign themes plus a one-line reason, stored with a ruleset version so tags can be rebuilt.

## Recommendation

Build these first:

1. **New-Form-D watcher.** Daily form index or Atom feed, filtered to `INDUSTRYGROUPTYPE` in (Other Technology, Computers), non-fund, not amendment, `TOTALAMOUNTSOLD` >= $1M, fuzzy-joined to companies already on ATS boards. Backfill from the quarterly data sets.
2. **Engineering hiring velocity from your own snapshots.** `first_seen` per posting, `new_eng_30d` and `velocity_ratio`, with a "board went from 0 to engineering" flag. This is the most direct "starting to hire" signal and costs nothing.
3. **YC enrichment.** Join yc-oss `team_size`, `isHiring`, `batch`, `stage` and descriptions daily; use `changes/latest.json` to catch flips to hiring.
4. **Wage-level badge.** OFLC 2026-27 ALC_Export for SOC 15-1252 by MSA against posted ranges, showing the lottery weight (1x to 4x) beside each posting.
5. **Theme tags.** Local embeddings over yc-oss and ATS descriptions, with Haiku batch classification for the borderline cases.

Skip for now: Crunchbase, Apollo, Harmonic (no affordable API); The Org (scraper-only); Wikidata (sparse). Add PDL's free 100 records/month later, only to check headcount on a final shortlist.
