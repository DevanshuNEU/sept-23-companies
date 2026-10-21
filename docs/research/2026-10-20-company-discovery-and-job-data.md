> **Evidence only.** Decisions live in issue #9; where this file disagrees, #9 wins. Its recommendations feed `superpowers/specs/2026-10-20-opportunity-pipeline-design.md` and decide nothing on their own. Working files and raw data it mentions stay outside the repo in `~/Desktop/outreach-scratch/`.

# Company discovery and job data for small US employers (as of 2026-10-20)

Status: complete as of 2026-10-20. Scripts and raw outputs are in `research/work/`. Builds on `docs/research/2026-10-17-crawl-and-scheduling.md` and `2026-10-17-growth-signals.md` and does not repeat them.

## Summary

1. Of 40 sampled small junior H-1B filers, only 6 (15%) use Greenhouse, Lever or Ashby; 13 (32.5%) have any pollable board, and 21 (52.5%) have no machine-readable board at all.
2. The LCA file's `EMPLOYER_POC_EMAIL` gives a company domain for 2,734 of the 2,902 target employers (94%), free and offline. It was right on every sample row I could check and beat my domain guesses five times.
3. stapply's free daily company lists (80,390 companies, 49 ATSs) match 521 of the 2,902 by exact name (18%), 459 of them not tracked today.
4. Only 104 of the 2,902 (3.6%) are YC companies, so YC-first targeting misses most small sponsors. YC and Wellfound terms both forbid scraping.
5. Greenhouse, Lever, Ashby, SmartRecruiters, Breezy, Pinpoint and Teamtailor return 304 on `If-None-Match`. Workable, Rippling, BambooHR, Recruitee, JazzHR, Workday and Oracle do not, so hash sorted job IDs (raw bodies change per request on Recruitee and JazzHR).
6. Workday and Oracle bodies are one detail GET per posting away (`/wday/cxs/.../job/{path}` and `recruitingCEJobRequisitionDetails`). Greenhouse bodies need only `?content=true`, which the radar does not send (10% coverage today).
7. Half of 499 radar boards add a posting less than once every two weeks (median Ashby board 0.25 change days a week). A cadence keyed on time since the last new posting cuts most requests.
8. The FEIN in the LCA file equals the `ein` in SEC submissions JSON: an exact match proved the entity for 4 of 12 funded startups. In total 8 of the 12 had a Form D under their own name or an investor SPV.
9. Common Crawl's index found 283 live-looking Ashby slugs missing from both stapply and the radar in half of one host (10 of 15 checked were live), but the index server often fails.
10. Paid aggregators help mainly for payroll-suite ATSs (Fantastic.jobs covers Paycom, Hirebridge and Dover from $95/month) and job-board-only posters (TheirStack, API from $49/month). None was tested with an account, and none shows personal-use terms.

## 1. Which ATS small US companies use

### 1.1 Published counts (no neutral market-share study exists for seed to Series B)

No primary, neutral study measures ATS share among seed to Series B US startups. The best available counts are crawl-based and each has a bias.

| Source | What it counts | Greenhouse | Lever | Ashby | Workable | SmartRecruiters | Others |
|---|---|---|---|---|---|---|---|
| TheirStack technology pages ([greenhouse](https://theirstack.com/en/technology/greenhouse), [ashby](https://theirstack.com/en/technology/ashby), read 2026-10-20) | Companies whose job posts show the tool, worldwide | 27,412 | 11,134 | 10,459 | 35,110 | 14,734 | BambooHR 40,845; JazzHR 22,509; Breezy 11,351; Recruitee 11,049; Teamtailor 8,712; Rippling 9,361; Gem 284; Wellfound 301; Paycom 48,283; Workday 96,439 |
| stapply jobhive manifest ([manifest.json](https://storage.stapply.ai/jobhive/v1/manifest.json), generated 2026-10-20T14:30Z, read 2026-10-20) | Companies with a board in its open crawl, 63 sources, 80,390 companies, 5.14M jobs | 6,031 | 2,402 | 3,448 | 4,752 | 2,747 | join.com 23,547; BambooHR 5,632; Paycom 5,135; Workday 3,530; JazzHR 2,689; iCIMS 2,498; Personio 2,463; Rippling 1,923; Teamtailor 1,464; Breezy 1,384; Recruitee 1,164; Gem 496; Pinpoint 406 |
| ResumeGeni "ATS Market Share 2026" ([page](https://resumegeni.com/research/ats-market-share-2026), seen in search 2026-10-20, not opened) | 800 top-rated employers, so large companies | 22.0% | 3.4% | 5.1% | | | Workday 29.4%, Phenom 7.0% |

Notes on these counts:
- TheirStack counts BambooHR, Paycom and Rippling as "used" even when the company only runs payroll or HR on them, so they overstate those as job boards. Its pages show no date.
- stapply only counts boards it has found. Its Greenhouse, Lever and Ashby lists come from slug lists, so they are complete for those three only as far as the slug lists are.
- Gem, Dover and Wellfound are small as hosts of public boards: Gem 284 to 496 companies, Wellfound 301 (TheirStack). Dover had no TheirStack page (404).

### 1.2 Measured: what the radar tracks today

Query: `select ats,count(*),count(distinct company_key) from board group by ats` on radar.db, opened with `sqlite3.connect('file:...radar.db?mode=ro', uri=True)`, 2026-10-20.

| ATS | Boards | Companies | Postings stored |
|---|---|---|---|
| Ashby | 328 | 326 | 13,616 |
| Greenhouse | 106 | 106 | 14,969 |
| Lever | 29 | 29 | 2,071 |
| Workday | 18 | 18 | 30,490 |
| Eightfold | 7 | 4 | 3,210 |
| Oracle ORC | 6 | 3 | 29,782 |
| Apple, Google, Amazon | 9 | 3 | 9,252 |
| BambooHR | 2 | 2 | 79 |
| SmartRecruiters | 2 | 2 | 1,265 |
| Workable | 1 | 1 | 0 |
| Total | 508 | 494 | |

The mix is a result of how the targets were picked (YC first, then big sponsors), not a market measurement: 91% of boards are Greenhouse, Lever or Ashby. RADAR.md says Workday, SmartRecruiters, Workable and BambooHR tokens are added by hand only, and JazzHR, iCIMS and Jobvite are out of scope.

### 1.3 Measured: where new-grad postings live (Simplify list)

Query: the host of `url` in [SimplifyJobs listings.json](https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json), `active = true` rows only, fetched 2026-10-20 (19,308 rows, 3,027 active, 1,079 companies). The host was matched by substring (`greenhouse.io`, `lever.co`, `ashbyhq.com`, `myworkdayjobs`, and so on) in an inline script; the raw file is `work/simplify_listings.json`.

| Host | Active postings | Companies |
|---|---|---|
| Workday | 875 | 272 |
| Greenhouse | 393 | 160 |
| Ashby | 203 | 154 |
| Other (own site, others) | 753 | 121 |
| Lever | 134 | 67 |
| iCIMS | 130 | 62 |
| SmartRecruiters | 109 | 56 |
| Oracle | 165 | 53 |
| Workable | 67 | 46 |
| JazzHR | 42 | 32 |
| Rippling | 22 | 19 |
| SuccessFactors | 62 | 15 |
| Jobvite, Paylocity, BambooHR, Eightfold, Pinpoint, Recruitee, Breezy | 72 | 32 |

Greenhouse, Lever and Ashby hold 381 of 1,079 companies (35%) in this list. The list is biased toward larger companies that post new-grad programs.

### 1.4 Measured: the 40-company LCA sample

The 40 rows in `work/sample40.csv` were drawn from the 2,902 low-risk tier A/B small junior H-1B filers in `recon/lca/small_junior_employers.csv`. I checked each one in three steps, about 1.5 s between requests, and at most three requests per site:

1. The previous attempt's slug probe (`work/probe.py`, `work/probe_results.json`): name-derived slugs against Greenhouse, Lever, Ashby and Workable.
2. A careers-page check (`work/careers_probe.py`, `careers_pass2.py`, `save_html.py`; results in `careers_results.json`, `careers_results2.json`, `html/`): fetch `/careers` or the homepage, follow one careers link, and grep the HTML for 25 ATS hosts.
3. A verify step (`work/verify_slugs.py`): one API GET per candidate board, checking titles and locations against the company.

Result (2026-10-20):

| Where the jobs are | Count | Companies |
|---|---|---|
| Greenhouse | 3 | Hazel Health (`hazel`), Vilya (`vilya`), Chan Zuckerberg Biohub (`biohub`) |
| Ashby | 2 | Aurora Solar (`aurorasolar`), Hooglee (`hooglee`, SF roles, site says "Coming Soon", so identity likely but not proven) |
| Lever | 1 | Upscale AI (`upscale-ai`, careers page loads a Lever embed script) |
| Workable | 1 | Hugging Face (`huggingface`) |
| SmartRecruiters | 2 | Sportradar (`Sportradar`, 92 jobs), Altium (parent Renesas links to SmartRecruiters) |
| Rippling | 1 | MemryX (`memryx`, careers page loads `ripplingcdn.com/ats/embeds/job-board.v1.js`) |
| Breezy | 1 | Zen Labs, which trades as Complizen (`complizen.breezy.hr`; domain from the LCA contact email, section 7) |
| Pinpoint | 1 | Oxford Economics |
| Paycom | 1 | Pictor Labs |
| Hirebridge | 1 | Kibo Commerce |
| Workday | 1 | Texas A&M Transportation Institute (`tamus.wd1.myworkdayjobs.com/TTI_External`) |
| Parent company portal (enterprise) | 4 | Supplyframe (Siemens), Seqirus (CSL, site returned 429), Ascentis (now UKG), Norstella (portal returned 403) |
| Careers page with no ATS in the HTML (self-hosted posts, a form, an email, or a JS app) | 10 | Synbio, Magwerks, Mon Ami (own `/jobs/*` pages), Hooke Labs (text only), Pasteur Labs (email `careers@simulation.science`), Ixana (own `/company/careers/*` pages), Epsilla, Alltius, Vivalink, Mercury Intermedia (`mercury.io/careers`) |
| No careers page found, or site down | 11 | Cargo (its `/careers` is a job board of other companies' GTM jobs), ACREW Imaging, Ubiquant US (`ubiquantus.com` unreachable), Datavant/Ciox (open-roles page has no ATS in HTML; unresolved), Aryn, Famrev, Big Little Robots, Strayos, Impact Bridge Media (`impactbridge.net` unreachable), Perception Medicine (`perceptionmedicine.ai`, no careers link), Insilicom |

My first domain guesses were wrong for 5 of the 40 (Zen Labs, Impact Bridge, Perception Medicine, Ubiquant, Mercury Intermedia). The LCA contact email gave the right domain for all five (section 7), and that turned up two more careers pages.

What this says:
- Only 6 of 40 (15%) are on the three ATSs the radar polls well. Adding Workable, SmartRecruiters, Rippling and Breezy gets 11 of 40 (27.5%). Adding Workday and Pinpoint gets 13 (32.5%).
- 21 of 40 (52.5%) have no machine-readable board I could find. For them, an ATS crawl will never see a posting. The only signals are the LCA filing itself, the careers page text, HN posts, and aggregators that read company sites.
- Slug guessing alone is noisy. Of the previous probe's 9 hits with jobs, 6 were right, 2 were a different company with the same first word (`greenhouse:mercury` is Mercury the bank, `greenhouse:impact` is impact.com), and 1 is unresolved (`ashby:hazel`). Every Workable "hit" with 0 jobs was noise: the Workable widget returns 200 and a name for many unrelated accounts (`zen` is Zayo, `acrew` is "Dook's Place"). Slug guessing also missed 3 boards whose slug differs from the legal name (`biohub`, `Sportradar` on SmartRecruiters, `memryx` on Rippling, an ATS it did not try).
- LCA small filers are not YC startups. They include subsidiaries, labs, research institutes and services firms, which use payroll-suite ATSs (Paycom, UKG, Hirebridge) or no ATS at all.

## 2. Public endpoints per ATS

Every row was checked live on 2026-10-20 with `work/cond_test.py` (one plain GET, then one GET with `If-None-Match` when an ETag came back, then one with `If-Modified-Since`, 1.5 s apart; raw output in `work/cond_results.json`). Field lists are the keys of the first job object in the real response. "Undocumented" means I found no first-party page for the endpoint; the shape comes from [kalil0321/ats-scrapers](https://github.com/kalil0321/ats-scrapers) (MIT, last push 2026-09-24, read 2026-10-20), files under `src/ats_scrapers/scrapers/`.

| ATS | List URL (no auth) | Dates in list | Full body | ETag, 304? | If-Modified-Since | Documented limits and terms |
|---|---|---|---|---|---|---|
| Greenhouse | `GET boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` | `first_published`, `updated_at` | Yes with `content=true` (HTML-escaped `content`) | Weak ETag, 304 | 200 (no Last-Modified) | "authentication is not required for any GET endpoints" ([docs](https://docs.greenhouse.io/job-board.html), read 2026-10-20). No limit or terms stated. |
| Lever | `GET api.lever.co/v0/postings/{site}?mode=json&skip=&limit=` | `createdAt` (ms) | Yes: `descriptionPlain`, `descriptionBodyPlain`, `lists`, `additionalPlain`; `salaryRange` | Weak ETag, 304 | 200 | Only application POSTs are limited (2/s). Published postings "may be scraped by third parties" ([postings-api README](https://github.com/lever/postings-api), read 2026-10-20). |
| Ashby | `GET api.ashbyhq.com/posting-api/job-board/{name}?includeCompensation=true` | `publishedAt` ("when the job was last published", so it can reset) | Yes: `descriptionHtml`, `descriptionPlain`, `compensation` | Weak ETag, 304; `cache-control: public, max-age=60`, Cloudflare | 200 | No limit or terms stated ([docs](https://developers.ashbyhq.com/docs/public-job-posting-api), read 2026-10-20). |
| Workable | `GET apply.workable.com/api/v1/widget/accounts/{slug}?details=true` (undocumented) | `created_at`, `published_on` | Yes with `details=true` (`description`); also `apply.workable.com/{slug}/jobs/view/{shortcode}.md` per ats-scrapers, and the account page links an `llms.txt` | No ETag | 200 | Undocumented. Returns 200 and a name for many slugs with 0 jobs, so a 200 does not prove a real board (section 1.4). |
| SmartRecruiters | `GET api.smartrecruiters.com/v1/companies/{id}/postings?limit=100&offset=` | `releasedDate` | No; detail call `.../postings/{postingId}` has the job ad | Weak ETag, 304 | 200 | The [Posting API page](https://developers.smartrecruiters.com/docs/posting-api) (read 2026-10-20) says it "supports both API Key and OAuth 2.0", yet the unauthenticated call returned 200 again today. SmartAPIs limit: 10 req/s, 8 concurrent ([rate limiting](https://developers.smartrecruiters.com/docs/rate-limiting), per the 2026-10-17 doc). |
| Rippling | `GET api.rippling.com/platform/api/ats/v1/board/{slug}/jobs` (undocumented) | none in list | No; detail `.../jobs/{uuid}` has `description.company` and `description.role` (18,158 chars on the test job), `createdOn`, `payRangeDetails` | No ETag, `no-store` | 200 | Undocumented; the platform API is partner-gated. |
| BambooHR | `GET {slug}.bamboohr.com/careers/list` (undocumented) | none in list | No; detail `.../careers/{id}/detail` has `description`, `datePosted`, `compensation`, `minimumExperience` | No ETag, `no-cache` | 200 | Official API needs a company API key. N+1 calls per board, as RADAR.md notes. |
| Breezy | `GET {slug}.breezy.hr/json` (undocumented) | `published_date` | No (list has `salary`, no body) | Weak ETag, 304 | 200 | The v3 API is OAuth-gated (ats-scrapers `breezy.py`). |
| Recruitee | `GET {slug}.recruitee.com/api/offers/` | `created_at`, `close_at` | Yes (`description`, `requirements`) | No ETag | 200 | Careers Site API "does not require authorization" (Recruitee help, quoted via search 2026-10-20; [support.recruitee.com](https://support.recruitee.com/en/articles/1066282), not opened). |
| Teamtailor | `GET {slug}.teamtailor.com/jobs.rss` | RSS `pubDate` | Yes (description in RSS) | Weak ETag, 304 | **304** (sends Last-Modified) | First 100 jobs by default, `offset` and `per_page` to page ([support article](https://support.teamtailor.com/en/articles/11171756-rss-feed-how-to-guide), via search 2026-10-20). The JSON API needs a key. |
| Pinpoint | `GET {slug}.pinpointhq.com/postings.json` | yes (per ats-scrapers) | Yes (per ats-scrapers; my test board had 0 jobs) | Weak ETag, 304 | 200 | Developer docs at developers.pinpointhq.com (not opened). |
| JazzHR | `GET {slug}.applytojob.com/apply/jobs` (HTML) | in HTML | Detail page `.../apply/jobs/details/{id}` (HTML) | No ETag | 200 | No public JSON; the API needs a key (ats-scrapers `jazzhr.py`). |
| Gem | `POST jobs.gem.com/api/public/graphql/batch` (undocumented) | on detail query | On detail query `ExternalJobPostingQuery` | POST, so no | n/a | Undocumented (ats-scrapers `gem.py`). |
| Paycom | `GET www.paycomonline.net/v4/ats/web.php/portal/{token}/career-page`, then `POST .../api/ats/job-posting-previews/search` (undocumented) | yes | `GET .../api/ats/job-postings/{id}` | not tested | not tested | Undocumented (ats-scrapers `paycom.py`). Token is a 32-hex client key, not a name, so it cannot be guessed. |
| Workday | `POST {t}.wdN.myworkdayjobs.com/wday/cxs/{t}/{site}/jobs` (undocumented) | `postedOn` ("Posted 5 Days Ago") | Detail `GET .../wday/cxs/{t}/{site}/job/{externalPath}` returns `jobPostingInfo.jobDescription` (9,308 chars on the test job) and `startDate` (`2026-10-15`, an exact date) | No ETag, `no-store` | 200 | Undocumented. |
| Oracle ORC | `GET {host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&finder=findReqs;siteNumber={site},limit=,offset=` | `PostedDate` (list) | Detail `GET .../recruitingCEJobRequisitionDetails?expand=all&onlyData=true&finder=ById;Id="{id}",siteNumber={site}` returns `ExternalDescriptionStr`, `ExternalResponsibilitiesStr`, `ExternalQualificationsStr` | No ETag; `max-age=1800` | 200 | Undocumented for third parties. |
| Dover | not found | | | | | No first-party doc and no ats-scrapers adapter; TheirStack has no Dover page. Not verified. |
| Wellfound | none | | | | | Terms forbid "automated or non-automated harvesting, collection or 'scraping'" and automated access beyond human load ([terms](https://wellfound.com/terms), effective 2020-06-05, read 2026-10-20). ats-scrapers says the site sits behind Akamai and needs a browser service. Do not crawl. |
| YC Work at a Startup | `api.ycombinator.com/v0.1/companies?isHiring=true` answers (50 pages; `teamSize`, `website`, `slug`, `batch`, `status`), but it is not documented | | | Weak ETag, 304 | 200 | YC terms (updated September 2026) say "you will not engage in or use any data mining, robots, scraping or similar data gathering or extraction methods" ([legal](https://www.ycombinator.com/legal), read 2026-10-20). |

### 2.1 Getting bodies for Workday and Oracle (the 71% gap)

Measured body coverage (query: postings joined to `posting_version`, counting `length(body_text) >= 500`, radar.db, 2026-10-20):

| ATS | Postings | With a 500+ char body |
|---|---|---|
| Workday | 30,493 | 2,074 (6.8%) |
| Oracle ORC | 29,783 | 1,845 (6.2%) |
| Greenhouse | 14,970 | 1,541 (10.3%) |
| SmartRecruiters | 1,266 | 120 (9.5%) |
| Eightfold | 3,210 | 1,062 (33%) |
| Ashby | 13,620 | 13,614 (99.96%) |
| Lever | 2,071 | 2,015 (97%) |

- **Workday:** one GET per posting to `/wday/cxs/{tenant}/{site}/job/{externalPath}`, where `externalPath` is the path in the list row (and in the stored apply URL after the site name). It returns the full HTML `jobDescription` and an exact `startDate`. Tested on Adobe R170600, 2026-10-20.
- **Oracle ORC:** one GET per requisition to `recruitingCEJobRequisitionDetails` with `finder=ById;Id="{id}",siteNumber={site}` and `expand=all`. Tested on Oracle requisition 347832, 2026-10-20 (357 + 1,244 + 2,812 chars across the three description fields).
- **Greenhouse:** no extra call needed. `?content=true` on the list call already returns every body. The 10% coverage is the radar not asking for it.
- **SmartRecruiters:** one GET per posting to `/v1/companies/{id}/postings/{postingId}`.
- Cost: bodies are needed only for new postings that pass the title filter. Fetch the body once on first sight, store it, and never fetch it again unless the list's `updated_at` (Greenhouse) or the content hash changes.

## 3. Finding a company's board from its name or domain

Four methods, from cheapest to most work. Use them in this order and stop at the first verified hit.

### 3.1 Existing board lists (free, fresh)

- **stapply jobhive company lists.** One CSV per ATS (`name,slug,url`), linked from the [manifest](https://storage.stapply.ai/jobhive/v1/manifest.json), regenerated daily (generated 2026-10-20T14:30Z when read). 49 lists, 80,390 companies, 6.2 MB in total; downloads need a User-Agent (plain urllib got 403). The repo is MIT; the dataset has no stated license, so use it as a lookup, do not republish it.
- **Measured match rate.** Exact normalized-name match (legal name and DBA, suffixes stripped) of the 2,902 low-risk tier A/B LCA employers against all 49 lists: **521 matched (18.0%)**, 459 of them not in his universe. By ATS: Greenhouse 157, Ashby 117, Lever 60, BambooHR 56, SmartRecruiters 47, Workable 40, Rippling 32, Workday 27, Breezy 17, JazzHR 16, iCIMS 13, Gem 10. Script `work/stapply_match.py`.
- **Precision spot check** (25 random matches, by eye): most are plausible, and the DBA column does real work (Stryder Corporation to `handshake`, Nino Finance to `cointracker`, Panorama Artificial Intelligence to `gradial`, Jetlenses to `versemedical`). Multi-list hits (Sportradar in Breezy, SmartRecruiters and Workday; Reltio in three) need a check of which board is live. On the 40-company sample the lists found 9 boards; my manual work found 14.
- **Other lists.** SimplifyJobs `listings.json` (ETag, `max-age=300`, 19,308 rows on 2026-10-20) and yc-oss (daily, `meta.json` has Last-Modified and answers `If-Modified-Since` with 304, measured). Both were covered in the 2026-10-17 crawl doc.

### 3.2 Careers-page parsing (one or two requests per company)

Fetch `https://{domain}/careers` (fall back to the homepage and follow one link whose href contains career, jobs or join), then grep for ATS hosts and embed scripts. Signals seen on the sample:
- Greenhouse: `boards.greenhouse.io/embed/job_board/js?for={token}`, `job-boards.greenhouse.io/{token}`, or a WordPress Greenhouse plugin.
- Lever: `jobs.lever.co/{site}`, or a third-party `lever-jobs-embed` script (Upscale AI).
- Ashby: `jobs.ashbyhq.com/{name}` links.
- Rippling: `static-assets.ripplingcdn.com/ats/embeds/job-board.v1.js` (MemryX).
- SmartRecruiters: 15-digit posting IDs starting `7440001` in the page's own job links (Sportradar), or `join.smartrecruiters.com` links.
- Paycom: `paycomonline.net/v4/ats/web.php/jobs?clientkey=...` (Pictor Labs). Hirebridge: `recruit.hirebridge.com` (Kibo).
- Workday: `{tenant}.wdN.myworkdayjobs.com/{site}` (TTI).

Measured on the sample: 29 of 40 had a careers page or a jobs link on the homepage. Static HTML named the ATS for 13 of those 29. Four were a parent company's portal. Ten had no ATS in the HTML: their own posts, a form, an email address, or a JS app that loads jobs later. A headless browser would catch some of the JS cases; that is not verified here. Scripts: `work/careers_probe.py`, `careers_pass2.py`, `save_html.py`.

### 3.3 Slug probing (cheap, but verify every hit)

Probe the name-derived slugs (joined, hyphenated, DBA) against the JSON endpoints in section 2. Rules learned from the sample (section 1.4):
- Never accept the bare first word of a multi-word name (`mercury`, `impact` were other companies).
- Never accept a Workable 200 with 0 jobs.
- Accept a hit only when a posting's location or the board's company name fits the LCA employer (city, state, product). The radar's own rule says the same: "Verify a token returns real jobs for the right company before adding it" (RADAR.md, "Growing the universe").
- Greenhouse has a name check endpoint: `GET boards-api.greenhouse.io/v1/boards/{token}` returns the board's `name` ([docs](https://docs.greenhouse.io/job-board.html), read 2026-10-20).

### 3.4 Common Crawl index (free, monthly, partial)

- Latest index: `CC-MAIN-2026-39`, September 2026 ([collinfo.json](https://index.commoncrawl.org/collinfo.json), read 2026-10-20).
- Query: `https://index.commoncrawl.org/CC-MAIN-2026-39-index?url=jobs.ashbyhq.com/*&output=json&fl=url,timestamp,status&page=N`. `showNumPages=true` reported 2 pages for `jobs.ashbyhq.com`, 5 for `job-boards.greenhouse.io`, 1 for `apply.workable.com` and 1 for `ats.rippling.com`; `jobs.lever.co` timed out (504).
- The server is overloaded. Page 0 for Ashby failed four times (502 and 504) over about 15 minutes. Page 1 worked: 8,105 URLs, 1,121 distinct Ashby slugs (names from "openai" to "zyphra", so about half the alphabet). **283 of those 1,121 are in neither stapply's 3,448 Ashby slugs nor the radar's 328.** A random 15 of the 283, checked against the Ashby API: 10 live boards with jobs, 5 returned 404.
- The same index is published as Parquet files (the "columnar index"), which can be filtered by `url_host_name` without the busy CDX server. Not tested here.
- Use it as a monthly top-up for slug lists, not as a per-company lookup.

### 3.5 Web search (paid, small volume only)

- Google Custom Search JSON API: "closed to new customers. Existing ... customers have until January 1, 2027"; 100 free queries a day, then $5 per 1,000 up to 10,000 a day ([overview](https://developers.google.com/custom-search/v1/overview), read 2026-10-20). Not an option for a new key.
- Brave Search API: $5 of free credit a month, then $5 per 1,000 requests, 50 queries/s; storing results needs "a plan that includes storage rights" ([pricing](https://brave.com/search/api/), read 2026-10-20). About 1,000 free lookups a month, enough for a `"{legal name}" careers` query on new LCA rows only.
- The manual Google x-ray queries in SOURCES.md (`site:jobs.ashbyhq.com "..."`) still work by hand.

## 4. Aggregators and paid data

None of these was tested with an account. Coverage numbers are the vendor's own claims, read on 2026-10-20. The question for each one: does it see companies an ATS crawl cannot (the 21 of 40 with no machine-readable board, section 1.4)?

| Vendor | What it is | Vendor coverage claim | Freshness claim | Low tier | Personal use | Finds what an ATS crawl misses? |
|---|---|---|---|---|---|---|
| TheirStack ([pricing](https://theirstack.com/en/pricing)) | Job postings plus tech stack, from job boards and ATSs | "225M records + 305k new per day", "352k sources", 195 countries, history to 2021 | Daily | Free UI plan ("500 results per page, unlimited pages"); API 1,500 credits for $49/month ($32.67 per 1,000 jobs); 1,000 company credits $109 one-time | Not stated; `/en/terms` returned 404 | Partly. Its sources include job boards, so a company that posts only on LinkedIn or Indeed can show up. Best fit: a one-time lookup of the 2,902 LCA names to see who is posting at all. |
| Coresignal ([pricing](https://coresignal.com/pricing/)) | B2B company, employee and job data | Not stated on the pricing page | Not stated | 7-day trial with 2,000 credits; Mini $49/month for 2,500 credits; 1 credit per job record | Marketed as B2B; no individual restriction stated | Partly (job boards), not verified. |
| Fantastic.jobs, Active Jobs DB ([site](https://fantastic.jobs/), [ATS list](https://fantastic.jobs/ats)) | ATS and career-site crawl | "3M+ Career site jobs / month", "275,000+ ATS & Career sites", 54 named ATSs including Paycom, Hirebridge, Dover, TriNet, Paylocity, iCIMS | "hourly" with "daily expiration checks" | Self-serve from $95/month; Apify actor $0.80 per 1,000 jobs ([actor](https://apify.com/fantastic-jobs/career-site-job-listing-feed)), "posted during the last 6 months", max 10,000 per run | Terms page not found | Yes for payroll-suite ATSs the radar lacks (Paycom, Hirebridge, Paylocity, Dover). No for self-hosted careers pages. |
| jobdata API ([site](https://jobdataapi.com/)) | ATS aggregator | "52,192,871 Total Jobs", "over 100 different ATS and HR software providers", "539,122 New Jobs in The Last 7 Days" | Daily | "Access lite $345/mo" (last 90 days); free "Public endpoints ... for limited testing" | Not stated | Same as Fantastic.jobs, at about 4x the price. |
| PredictLeads ([pricing](https://predictleads.com/pricing)) | Job openings, news events (funding), tech detections, per company | Not stated on the pricing page | Not stated | 100 free API calls a month; then $40 minimum plus $0.04 per credit | Not stated | Its funding "news events" could flag raises that Form D misses. 100 free calls a month fits a final shortlist only. |
| Apify actors | Third-party scrapers; about 20 actors re-wrap the public Greenhouse, Lever and Ashby APIs (seen in search results, 2026-10-20) | Varies | Varies | Free plan $5 credit a month; Starter $19/month ([pricing](https://apify.com/pricing)) | Not stated | The ATS wrappers add nothing over calling the free APIs directly. The Fantastic.jobs actor is the only one worth a look. |
| stapply jobhive ([manifest](https://storage.stapply.ai/jobhive/v1/manifest.json)) | Open crawl of 63 sources | 80,390 companies, 5.14M jobs | Daily snapshot | Free download (all-jobs parquet is 2.3 GB) | No dataset license stated | Same ATS families as a crawl, but covers Paycom, JazzHR, iCIMS, BambooHR, Rippling boards he does not poll. |

Notes:
- An aggregator row carries no sponsor evidence. That is why the radar retired hiring.cafe (RADAR.md). The LCA list fixes that: the visa evidence comes from DOL, so an aggregator only has to say "this employer has an open SWE posting".
- The cheapest useful test is a one-off: run the 2,902 LCA names through TheirStack's free UI or a $49 month, and count how many have any posting in the last 90 days that no ATS crawl saw. Not done here.

## 5. Company discovery sources one person can use

### 5.1 Hacker News "Who is hiring"

- **Official API** (Firebase): `https://hacker-news.firebaseio.com/v0/item/{id}.json` and `/v0/updates.json`. "There is currently no rate limit"; MIT license ([HackerNews/API](https://github.com/HackerNews/API), read 2026-10-20). One request per comment, so a 400-comment thread is 400 requests. Live check: 200, `cache-control: no-cache`, no ETag, `If-Modified-Since` ignored (`work/cond_results.json`).
- **Algolia HN Search API**: `hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring` finds the monthly threads, and `/api/v1/items/{id}` returns the whole comment tree in one request. "We are limiting the number of API requests from a single IP to 10,000 per hour" (text in the [hn.algolia.com/api](https://hn.algolia.com/api) page bundle, read 2026-10-20). No ETag.
- **Measured, October 2026 thread** (item 49922569, posted 2026-10-01, 393 comments): 266 top-level posts with text. Regex counts over them: 22 mention visa, sponsor or H-1B (6 of those say they do not sponsor); 8 say new grad, junior, entry level or early career; 3 mention Boston; 150 mention remote; 134 show an email address or "at" form. Only 14 of the 266 first segments (the company name before the first `|`) match a radar board company by exact normalized name (an undercount, since names are messy). First segments saved in `work/hn_oct2026_companies.json`.
- Use: one Algolia call per month, keep posts that name a US location or US remote, extract company plus email, and resolve the company to a board (section 3).

### 5.2 YC company directory

- **No documented public API.** `api.ycombinator.com/v0.1/companies?isHiring=true` answers with 50 pages of companies (`teamSize`, `website`, `slug`, `batch`, `status`), ETag and 304 supported (measured 2026-10-20). I found no YC page that documents it.
- **YC terms** forbid it: "you will not engage in or use any data mining, robots, scraping or similar data gathering or extraction methods" (last updated September 2026, [ycombinator.com/legal](https://www.ycombinator.com/legal), read 2026-10-20). The same applies to the Algolia index the radar's SOURCES.md names (app `45BWZJ1SGC`).
- **yc-oss/api** is a third-party daily copy (6,311 companies, last push 2026-10-20T02:41Z, `license: null`; [repo](https://github.com/yc-oss/api), read 2026-10-20). Reading a public GitHub file is not scraping YC, but the data still comes from YC's index, so treat it as personal reference only.
- **Overlap with the LCA list (measured).** Exact normalized-name match of the 2,902 LCA employers against yc-oss `all.json`: **104 (3.6%)**. YC-first targeting covers a small part of the small sponsors.

### 5.3 SEC EDGAR Form D

- **Access rules.** "Our current maximum access rate is 10 requests per second", and automated tools must declare a User-Agent like `Sample Company Name AdminContact@<sample company domain>.com` ([webmaster FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions), read 2026-10-20). The data APIs "do not require any authentication or API keys" ([EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces), read 2026-10-20).
- **Daily discovery.** `Archives/edgar/daily-index/2026/QTR4/form.{yyyymmdd}.idx`: 150 rows with form `D` on 2026-10-19 (measured; the 2026-10-17 doc counted 345 `D` plus `D/A` for 2026-10-15). The directory listing sends `Last-Modified` and answers `If-Modified-Since` with 304 (measured), so a daily poll is one cheap request.
- **Full-text search.** Covers "all EDGAR filings submitted electronically since 2001" ([FAQ](https://www.sec.gov/edgar/search/efts-faq.html), read 2026-10-20). The JSON behind it, `efts.sec.gov/LATEST/search-index?q="{name}"&forms=D`, worked for every name lookup in section 7, but the FAQ does not mention an API, so treat it as unofficial.
- **Bulk.** Form D data sets are quarterly; the latest is 2026 Q3 (3.48 MB) ([data sets](https://www.sec.gov/data-research/sec-markets-data/form-d-data-sets), read 2026-10-20). `submissions.zip` (all filer profiles, 1,569,965,048 bytes, Last-Modified 2026-10-20 05:00 GMT, measured with HEAD) is nightly.
- **"Raised money" and round size.** A new `D` (not `D/A`) from a non-fund issuer means a Reg D sale started within the last 15 days or so. `TOTALAMOUNTSOLD` and `TOTALOFFERINGAMOUNT` in the data sets (or the filing's XML) give size. The lag, industry and blind-spot numbers are in `docs/research/2026-10-17-growth-signals.md` section 1. Two new points from section 7 below: SPV filings ("Gaingels Orby AI LLC", "AVSF - Vectara 2024, LLC", "Contextual AI May 2024 a Series of CGF2021 LLC") also reveal a raise in a named company, and 4 of 12 well-known funded LCA startups had no Form D under their own name.

### 5.4 Crunchbase and alternatives

- Crunchbase: "API access is available through the Crunchbase sales team"; Enterprise limits are 200 requests per minute and 3 concurrent ([using the API](https://data.crunchbase.com/docs/using-the-api), read 2026-10-20). No self-serve price. Out of reach for one person.
- Usable alternatives: Form D (free, legal names, no prose), PredictLeads news events (100 free calls a month, section 4), yc-oss `batch` and `stage`, and TechCrunch and Crunchbase News RSS (2026-10-17 doc).

### 5.5 GitHub org activity

- REST limits: 60 requests per hour unauthenticated, 5,000 with a token ([rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api), read 2026-10-20). "Making a conditional request does not count against your primary rate limit if a 304 response is returned and the request was made while correctly authorized" ([best practices](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api), read 2026-10-20).
- Use: an activity check on a shortlist (recent pushes, count of distinct committers), not discovery. Only dev-tools and infra companies have public repos worth reading.

## 6. Crawling only the diff

### 6.1 Which endpoints honor conditional requests (live, 2026-10-20)

From `work/cond_test.py` (section 2 has the full table). One plain GET, then the same GET with the returned ETag in `If-None-Match`, then with `If-Modified-Since`.

| Returns 304 on `If-None-Match` | No ETag (must hash) | Returns 304 on `If-Modified-Since` |
|---|---|---|
| Greenhouse, Lever, Ashby, SmartRecruiters, Breezy, Pinpoint, Teamtailor RSS, YC v0.1 API, yc-oss, raw.githubusercontent (Simplify) | Workable, Rippling, BambooHR, Recruitee, JazzHR, Workday (list is POST; detail is `no-store`), Oracle ORC (`max-age=1800`), HN Firebase, HN Algolia | Only where a `Last-Modified` header exists: Teamtailor RSS, yc-oss, SEC daily-index directory. SEC `data.sec.gov/submissions` also returned 304 for a date of "now". Greenhouse, Lever, Ashby and SmartRecruiters ignore it. |

This matches the 2026-10-17 doc for the four ATSs it tested and adds eleven more.

### 6.2 Content hashing where there is no ETag

Two GETs 5 s apart (`work/hash_stability.py`, 2026-10-20):
- Raw body identical: Workable, Rippling, BambooHR, Workday detail, Greenhouse.
- Raw body **differs** while the job-ID set is the same: Recruitee and JazzHR (both bodies carry something per request, such as a token).
- So hash the sorted list of (job ID, updated field if any), never the raw bytes. On Greenhouse, include `updated_at` to catch edits.

### 6.3 How often boards change (measured from radar.db)

Script `work/board_change.py`, read-only. A board's "change day" is a calendar day on which at least one posting was first seen, excluding the board's first day (backfill). Boards observed for 28+ days only (499 boards, median window 62 days, 2026-07-28 to 2026-10-21).

| ATS | Boards | Median change days per week | Median new postings per week | Boards with no new posting in the window |
|---|---|---|---|---|
| Ashby | 322 | 0.25 | 0.38 | 23.9% |
| Greenhouse | 103 | 1.13 | 1.58 | 11.7% |
| Lever | 29 | 0.34 | 0.34 | 37.9% |
| Workday | 18 | 6.25 | 100 | 0% |
| Oracle ORC | 6 | 6.71 | 285 | 0% |
| All | 499 | 0.50 | 0.68 | 20.4% |

Spread of change days per week over all 499 boards: p10 0, p25 0.11, p50 0.50, p75 1.58, p90 4.25. Half the boards (249 of 499) get a new posting less than once every two weeks.

Share of successful fetches that returned no rows (a 304 or an empty board; query on `board_fetch`, `ok = 1 and n_returned = 0`): Ashby 95.9%, Lever 96.9%, Greenhouse 88.0%, SmartRecruiters 84.1%, Workday, Oracle, Eightfold and BambooHR 0% (no conditional support). So the ETag boards are already cheap at a 30-minute cadence. The cost sits in the few huge enterprise boards that must be re-downloaded every time.

Two cautions from the same data:
- On Ashby about 4% of fetches came back changed, roughly 2 a day per board at 48 polls a day, while new postings arrive about once a month per median board. Most ETag changes are edits, republishes or closures. A changed ETag means "diff the IDs", not "new job".
- Failed fetches (`ok = 0`) by ATS: Ashby 16,157, Greenhouse 7,589, Workday 2,282, Eightfold 2,173, Lever 1,210, Oracle 1,071, BambooHR 422. A failure must never read as "board empty" (the store already guards this with `n_returned > 0`).

### 6.4 Adaptive cadence (proposal built on the numbers above)

- Keep per board: `etag`, `id_hash`, `last_changed_at`, `last_new_posting_at`.
- Interval = time since `last_new_posting_at` divided by 8, clamped to between 1 hour and 3 days. A board that added a posting yesterday is checked every few hours; one quiet for 30 days is checked about every 4 days.
- With the measured spread (median 0.5 change days per week), most small boards land near the 1 to 3 day end, which matches the falsified-speed finding in SOURCES.md (89.5% of reqs still open at 30 days, median 84 days for engineering).
- Big enterprise boards with no ETag: fetch the list daily, and fetch bodies only for new IDs (section 2.1).
- New LCA-derived boards start at daily, then decay by the same rule.
- Expected load for 3,000 boards: at an average interval of a day, about 3,000 requests a day, most of them 304s. A Mac handles that in minutes with per-host limits (Ashby about 5 concurrent, Greenhouse and Lever about 30, from the 2026-10-17 doc).

## 7. Matching a DOL employer to a domain, a board and a funding record

### 7.1 The LCA file already carries the domain

The FY2025 LCA disclosure files have an `EMPLOYER_POC_EMAIL` column (plus `EMPLOYER_POC_FIRST_NAME`, `EMPLOYER_POC_LAST_NAME`, `EMPLOYER_POC_JOB_TITLE`, `TRADE_NAME_DBA`, `EMPLOYER_FEIN`, address and phone; schema read from `recon/lca/FY2025_Q4.parquet`). The record layout PDF is in `recon/lca/layout_FY2025.pdf`.

Measured (`work/poc_domains.py`, output `work/poc_domains.csv`): for each of the 2,902 low-risk tier A/B employers, take the most common contact-email domain across its FY2025 H-1B rows.

| Domain kind | Employers |
|---|---|
| A company domain | 2,734 (94.2%) |
| Free mail (gmail and similar) | 95 |
| Same domain as the attorney or preparer | 44 (some are self-prepared filings, such as Hooke Labs, so this overcounts law firms) |
| None | 28 |
| PEO domain | 1 |

On the 40-company sample the contact domain was right for every one I could check, and it beat my guesses five times (Zen Labs is `complizen.ai`, Impact Bridge is `impactbridge.net`, Perception Medicine is `perceptionmedicine.ai`, Ubiquant is `ubiquantus.com`, Mercury Intermedia is `mercury.io`). For subsidiaries it gives the parent's domain (Seqirus to `cslbehring.com`, Ascentis to `ukg.com`, Ciox to `datavant.com`), which is itself a signal.

A domain shared by several LCA employers points to a big parent: 30 domains are shared, led by `take2games.com` (5 employers), `bruker.com`, `clarivate.com`, `fortive.com` and `qiagen.com` (3 each). Use that as a "not small" flag.

The top contact titles are chief executive officer (553), president (214), COO (77), CFO (70) and CTO (62) (count of one row per employer). In small companies the LCA contact is often a founder. That is public government data, but it is the contact for the visa filing, so treat it as a lead to verify, not as an outreach list.

### 7.2 FEIN joins

- **SEC.** `data.sec.gov/submissions/CIK{10 digits}.json` includes an `ein` field (measured 2026-10-20). Test (`work/sec_fein.py`, output `work/sec_fein_results.json`): 12 known funded startups from the LCA list, Form D issuers found through `efts.sec.gov/LATEST/search-index?q="{name}"&forms=D`, then `ein` compared with the LCA FEIN.
  - Exact FEIN match, so the same legal entity is proven: 4 (Aurora Solar, Orby AI, HiddenLayer, Qurrent).
  - Issuer found but `ein` is blank (`000000000` or null): 2 (Hazel Health, Anchor Labs).
  - Only investor SPVs found, with different EINs (correctly rejected): 2 (Contextual AI, Vectara).
  - No Form D under that name: 4 (Nimble Robotics, Retell AI, Statsig, Epsilla).
  - A same-name different company was also returned and the FEIN kept it out: "Aurora Solar Technologies Inc." (North Vancouver, `ein` 000000000) and "Qurrent LLC" (Coeur d'Alene, different EIN).
- **For bulk joins**, `submissions.zip` (1.57 GB, nightly) holds every filer's profile, so an EIN to CIK index can be built once offline and joined to all 11,492 LCA FEINs with no per-company requests. Not run here.
- **USCIS H-1B Employer Data Hub.** Its CSVs have a `Tax ID` column holding the last four digits of the FEIN, plus employer name, city, state and ZIP (header of `DevVault/job-search/pipeline/uscis_h1b_2023.csv`, read only). Join on last-4 plus ZIP plus fuzzy name to get approval counts.
- **PEO and EOR filers** (TriNet, Deel, Justworks and others) share one FEIN across many clients (`recon/lca.md`), so never join on FEIN alone for those.

### 7.3 Order of operations for one LCA employer

1. Domain from `EMPLOYER_POC_EMAIL` (skip free mail, attorney and PEO domains).
2. Board from the stapply lists by name or DBA (section 3.1), then by careers-page parse on the domain (section 3.2), then by verified slug probes (section 3.3).
3. Funding from Form D: full-text search on the legal name, accept only when `ein` equals the FEIN or the city and state match; SPV names that contain the brand count as a raise signal.
4. Size check: shared contact domain, a parent portal, or Workday or SuccessFactors as the ATS all suggest "not small".

### 7.4 Open tools

| Tool | Use | License, activity (gh api, 2026-10-20) |
|---|---|---|
| [RapidFuzz](https://github.com/rapidfuzz/RapidFuzz) | Fast fuzzy name scores (the LCA cross-reference already uses it) | MIT, pushed 2026-10-19 |
| [cleanco](https://github.com/psolin/cleanco) | Strips legal suffixes (Inc, LLC, GmbH) | MIT, pushed 2026-06-23 |
| [Splink](https://github.com/moj-analytical-services/splink) | Probabilistic record linkage on name, city, ZIP and EIN fragments, runs on DuckDB locally | MIT, pushed 2026-10-19 |
| [dedupe](https://github.com/dedupeio/dedupe) | Active-learning record linkage | MIT, last push 2025-07-29 |
| [edgartools](https://github.com/dgunning/edgartools) | Form D parsing | MIT, pushed 2026-10-21 |
| OpenCorporates API | Company registry lookups | Free only for journalists, NGOs, universities and anti-corruption research; otherwise from £2,250 a year for 500 calls a month ([page](https://opencorporates.com/plug-in-our-data/), read 2026-10-20). Out of reach. |

The measured precision of the matching passes in `recon/lca.md` still applies: exact normalized name about 95%, tail-word strip about 45%, fuzzy 0 of 10. A FEIN or a contact domain beats any name score.

## 8. Source comparison

| Source | Coverage for small US employers | Freshness | Cost | Terms for one person | Diff support |
|---|---|---|---|---|---|
| Greenhouse, Lever, Ashby public APIs | 6 of 40 LCA sample; 35% of Simplify new-grad companies | Live (Ashby CDN 60 s) | Free | Greenhouse: no auth for GETs; Lever: "may be scraped by third parties"; Ashby: nothing stated | ETag and 304 |
| SmartRecruiters, Breezy, Pinpoint, Teamtailor | 3 of 40 | Live | Free | SmartRecruiters says key needed, but GET works unauthenticated; others undocumented or support pages | ETag and 304 (Teamtailor also Last-Modified) |
| Workable, Rippling, BambooHR, Recruitee, JazzHR | 2 of 40 | Live | Free | Undocumented except Recruitee ("does not require authorization") | None; hash sorted IDs |
| Workday, Oracle ORC | 1 of 40 (plus parent portals) | Live | Free | Undocumented | None; POST or no-store; bodies need one detail call per posting |
| Paycom, Hirebridge, payroll-suite ATSs | 2 of 40 | Live | Free | Undocumented; Paycom token is a client key that cannot be guessed | Not tested |
| stapply company lists | 521 of 2,902 LCA slice by exact name | Daily | Free | MIT code, no dataset license | Manifest has sha256 per file |
| Common Crawl index | Half of Ashby host gave 283 slugs new to stapply and radar | Monthly | Free | Open data | Monthly snapshots |
| LCA contact email domains | 2,734 of 2,902 have a company domain | Quarterly (DOL) | Free | Public disclosure data | New quarter file |
| HN Who is hiring | 266 posts in October 2026, 14 match radar companies | Monthly | Free | Firebase: no rate limit, MIT; Algolia: 10,000 req/hour/IP | Algolia `created_at_i` filters; no ETag |
| YC directory | 104 of 2,902 LCA slice | Daily (yc-oss) | Free | YC terms forbid scraping; yc-oss has no license | yc-oss: ETag, Last-Modified, 304 |
| SEC Form D | 8 of 12 funded startups had a Form D (own name or SPV) | Daily index; filing within 15 days of first sale | Free | 10 req/s, declared User-Agent | Daily index: Last-Modified, 304 |
| TheirStack | Claims 352k sources incl. job boards | Daily | Free UI; API $49/month for 1,500 jobs | Terms page not found | n/a |
| Fantastic.jobs | Claims 54 ATSs, 275,000+ sites | Hourly | $95/month or $0.80 per 1,000 jobs on Apify | Terms page not found | Time-window filters (Apify actor) |
| Coresignal | Not stated | Not stated | $49/month for 2,500 records | B2B marketing | n/a |
| PredictLeads | Not stated | Not stated | 100 free calls a month | Not stated | n/a |
| Wellfound | Large startup board | Live | n/a | Scraping forbidden | n/a |
| Crunchbase | Best funding data | Live | Sales only | Paid license | n/a |

## 9. What to build, in order

1. **Join the LCA list to domains** using `EMPLOYER_POC_EMAIL` (section 7.1). Free, offline, 94% coverage. Flag shared domains as "not small".
2. **Board lookup per domain**: stapply lists first (daily, free), then one careers-page fetch per domain with the host patterns in section 3.2, then verified slug probes. Expect about a third of LCA employers to resolve to a pollable board (sample: 13 of 40).
3. **Add adapters** for Breezy, Pinpoint, Teamtailor RSS, Rippling and Recruitee (all simple GET JSON or RSS, section 2), and turn on `?content=true` for Greenhouse.
4. **Body backfill** for Workday and Oracle: one detail GET per new posting that passes the title filter (section 2.1).
5. **Adaptive cadence** keyed on time since the last new posting, with ETag or sorted-ID hashing (section 6).
6. **For the half with no board**: keep them on a "watch" list fed by LCA quarters, Form D filings (FEIN join), HN monthly posts and a one-off TheirStack or Fantastic.jobs check. Their careers pages can be hashed weekly as text, one request each.
7. **Top-ups**: Common Crawl index monthly for new Ashby, Greenhouse, Workable and Rippling slugs.

## Not verified

- Whether a headless browser would reveal an ATS on the 10 careers pages with none in static HTML (Epsilla, Alltius and others).
- Hooglee's Ashby board is the LCA employer (site is "Coming Soon"); `ashby:hazel` (7 jobs) belongs to Hazel Health; Datavant's ATS (search snippet said Greenhouse; `greenhouse:datavant` returned 404).
- Precision of the stapply exact-name matches beyond a 25-row eyeball check.
- Common Crawl page 0 for `jobs.ashbyhq.com` (failed four times), all of `jobs.lever.co` (504), and the Parquet columnar index route.
- Whether 304 responses count against any ATS rate limit; whether SmartRecruiters will keep allowing unauthenticated GETs.
- Pinpoint, Gem, Paycom and Dover field shapes beyond the ats-scrapers source; Dover has no endpoint found at all.
- The `api.ycombinator.com/v0.1` endpoint's status; it works but is undocumented and YC's terms forbid scraping.
- TheirStack, Fantastic.jobs, Coresignal, jobdata and PredictLeads coverage of the 2,902 LCA names, and their terms for personal use (no terms page found for TheirStack or Fantastic.jobs at `/terms`). Fantastic.jobs RapidAPI pricing ($1 per 1,000 jobs, $45/month entry) comes from a search summary only.
- Recruitee and Teamtailor doc quotes come from search snippets of their help pages, not from opening the pages.
- ResumeGeni's 800-employer ATS share was seen in a search snippet only.
- TheirStack counts of HR suites (BambooHR, Paycom, Rippling) as ATS use.
- How many of the 2,734 contact domains host a pollable board (only the 40-row sample was checked).
- The EIN to CIK join over `submissions.zip` at scale (tested on 12 names only).
- Whether the board change rates from 499 curated radar boards hold for LCA-derived boards.

