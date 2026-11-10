> **Evidence only.** Decisions live in issue #9; where this file disagrees, #9 wins. It answers one question: how Knock can see a new posting first, before LinkedIn, Indeed, Google, Jobright and Simplify. It builds on `2026-10-17-crawl-and-scheduling.md` and `2026-10-20-company-discovery-and-job-data.md` and does not repeat their endpoint tables.

# Seeing new postings first (research, 2026-11-09)

Status: complete as of 2026-11-09. Live checks were one request per URL, at least 1 s apart, with the User-Agent `knock-research/1.0 (personal job search)`. Scratch scripts and raw files are in `~/Desktop/outreach-scratch/knock-research-main/`, `knock-research-q2/` and `knock-research-q3/`, outside the repo. Nothing in `outreach/` was run. "Measured" means I ran it on 2026-11-09; "unconfirmed" means no primary source says so.

## Summary

1. Knock's clock is the limit today, not the ATSs. A board is read at most every 6 hours (`floor_s = 21600`), the crawler reads one board at a time, and a full pass over about 2,000 boards took about 4 hours on 2026-11-02 ([master plan](../superpowers/plans/2026-11-02-right-job-pipeline-master-plan.md)).
2. The services to beat are hours behind. LinkedIn reads ATS feeds "every six hours", and Greenhouse and Ashby say a post can take 48 hours or 2 business days to show there. Indeed refreshes XML feeds every 6 hours. Simplify adds a Greenhouse posting a median 3.2 hours after its `first_published` (measured, p90 7.9 hours). Jobright's repo commits hourly.
3. Every ATS Knock reads can be checked in one request per board. Greenhouse, Lever, small Ashby boards, Breezy and Teamtailor answer 304 to `If-None-Match`. Workday (on 3 of 4 tenants tried) and Oracle (with `sortBy=POSTING_DATES_DESC`) list newest first, so page 1 tells whether anything is new.
4. The real limit is per host, not per board. All Greenhouse boards share `boards-api.greenhouse.io`, and all Lever boards share `api.lever.co`, whose robots.txt says `Crawl-delay: 1`. Knock's config spaces Lever at 0.25 s today.
5. Timestamps lie on two ATSs. Ashby's `publishedAt` is "when the job was last published", and it ran 1.9 to 23.2 hours later than Simplify's date on 10 of 11 rows. Lever's `createdAt` ran 12 days to over a year before the public date on Shield AI and SpryPoint rows (two samples). Greenhouse `first_published` matched Simplify to the second. Knock's own `first_seen` is the only clock that means the same thing everywhere.
6. A big Ashby board (ramp, 2.9 MB) came back with no ETag, only a CDN `Last-Modified`. Knock's Ashby reader then reports `ok` and re-ingests the whole board on every read.
7. Workable publishes one global XML feed of every customer's jobs, "updated every hour". One request an hour could replace every Workable widget call. Its size and whether a personal tool may use it are unconfirmed.
8. Nothing sees a posting before its ATS does. Sitemaps (none on the three big ATSs), Common Crawl (monthly), certificate logs and DNS are slower or blind. HN "Who is hiring" is the one source with its own postings, and it lands at 11:00 New York time on the first weekday of the month.
9. Open source pollers are slower than Simplify. They run on GitHub Actions, where 15 and 30 minute crons fired about every 6 hours in practice. All of them find new jobs by diffing job ids against a seen set.
10. With async reads and per-host limits, a check of 5,000 boards takes under 2 minutes on every host but Lever and Workable. 15,000 boards take about 5 minutes on the fast hosts, and about 33 minutes on Lever if its `Crawl-delay: 1` is honored.

## 1. Where Knock is today

| Fact | Value | Source |
|---|---|---|
| Tick | every 30 min, at most 500 boards, no new board after 1,500 s | `outreach/crawl/config.toml` (`boards_per_tick`, `tick_budget_s`) |
| Interval per board | time since the last new posting divided by 8, kept between 6 hours and 7 days | `config.toml` (`floor_s`, `ceiling_s`, `age_divisor`), `outreach/crawl/cadence.py` `next_interval_s` |
| Concurrency | one board at a time; synchronous `urllib` behind one `PoliteClient` | `outreach/crawl/tick.py` `_read_boards`, `outreach/net.py` |
| Per-host spacing | Greenhouse and Lever 0.25 s, Ashby 0.5 s, Workable 2.0 s, Workday 1.0 s, JazzHR 2.0 s | `config.toml` `[host_interval_s]` |
| Switched off | SmartRecruiters and Pinpoint (robots.txt) | `config.toml` `[ats_off]` |
| Measured load | every tick used its whole budget, 104 to 381 boards a tick, 477 overdue; a full pass over about 2,000 boards took about 4 hours | [master plan](../superpowers/plans/2026-11-02-right-job-pipeline-master-plan.md), 2026-11-02 |
| Hot set size | about 1,331 boards had a posting at his level, or with no stated level, in 12 months | same plan |

The time goes to reading and ingesting one board after another (Workday and Oracle paging, bodies, decisions), not to HTTP spacing. #166 cut part of it ([crawler health plan](../superpowers/plans/2026-11-02-crawler-health.md)).

## 2. Each ATS: list endpoint, timestamp, cheap check, limits, cost

Endpoints are the ones Knock's readers use (`outreach/crawl/ats/*.py`). Header checks marked 2026-10-20 are from the earlier doc's section 6.1; the rest were measured today.

| ATS | List endpoint | Timestamp in the list | What it means | Cheap change check | Limits and robots | Requests per full read |
|---|---|---|---|---|---|---|
| Greenhouse | `GET boards-api.greenhouse.io/v1/boards/{t}/jobs` | `first_published`, `updated_at` | `first_published` is the first publish time: it equals Simplify's date to the second on 8 of 8 rows (measured). `updated_at` is the last edit: it differed from `first_published` on 480 of 676 Anthropic jobs (measured). The [docs](https://docs.greenhouse.io/job-board.html) show `first_published` only on "Retrieve a job" and define neither field. | Weak ETag, 304 in 0.13 s with no body (measured). No Last-Modified. | "authentication is not required for any GET endpoints"; no limit stated ([docs](https://docs.greenhouse.io/job-board.html)). robots.txt on the API host: `Disallow: /embed/` only (measured). | 1, plus 1 detail per posting whose body Knock wants |
| Lever | `GET api.lever.co/v0/postings/{t}?mode=json` | `createdAt` (ms) | Creation time, not publish time: 12 days to 520 days before Simplify's date on Shield AI and SpryPoint rows (measured, two samples). The [README](https://github.com/lever/postings-api) does not define it. | Weak ETag, 304 (measured). A 304 still took 0.5 to 4.2 s on Palantir's 5.9 MB board, so the server builds the body first. | Only application POSTs are limited, 2 a second ([README](https://github.com/lever/postings-api)). `api.lever.co/robots.txt`: `Crawl-delay: 1` (measured). "These jobs may be scraped by third parties" (README). | 1 (no paging needed) |
| Ashby | `GET api.ashbyhq.com/posting-api/job-board/{t}` | `publishedAt` | "ISO DateTime when the job was last published" ([docs](https://developers.ashbyhq.com/docs/public-job-posting-api)), so it resets on a republish. No `updatedAt`. | ETag and 304 on Linear and Notion; Ramp's 2.9 MB board had **no ETag**, only `last-modified` equal to the CDN refresh, and `If-Modified-Since` returned 200 (measured). `cache-control: public, max-age=60`, Cloudflare HIT (measured). | None documented. `api.ashbyhq.com/robots.txt` answers 401 (measured). The CDN serves the same body for 60 s, so polling faster than once a minute gains nothing. | 1 |
| Workable | `GET apply.workable.com/api/v1/widget/accounts/{t}?details=true` (undocumented) | `published_on`, `created_at` | Dates only (`2026-05-29`, measured). | No ETag (2026-10-20), so hash the ids. The global XML feed (section 4.2) covers all boards at once. | Undocumented. robots.txt: `Disallow:` empty, so all allowed (measured). Knock spaces it at 2.0 s. | 1 |
| Workday | `POST {tenant}.wdN.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` (undocumented) | `postedOn` ("Posted Today", "Posted 3 Days Ago", "30+") | Relative day, no time. | No ETag, `no-store` (measured). Page 1 was newest first on NVIDIA, Salesforce and Mastercard but not Adobe (measured), so a one-page check works per tenant once its order is confirmed. `total` sits on page 1. | Undocumented. Page 1 took 1.6 s on NVIDIA (measured). | ceil(total / 20), capped at 2,000 rows a facet, plus facet splits (`workday.py`) |
| Oracle ORC | `GET {host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?...finder=findReqs;siteNumber=S,limit=200,offset=N` | `PostedDate` | Date only. | No ETag (2026-10-20). `findReqs` takes a `sortBy` variable and posting date variables ([Oracle REST docs](https://docs.oracle.com/en/cloud/saas/human-resources/farws/op-recruitingcejobrequisitions-get.html)). `sortBy=POSTING_DATES_DESC,limit=5` returned five requisitions posted today in 0.9 s (measured), with `TotalJobsCount`. The value name is not in the docs. | Undocumented for third parties. | ceil(total / 200) |
| Rippling | `GET api.rippling.com/platform/api/ats/v1/board/{t}/jobs` (undocumented) | none | Detail has `createdOn` (fixture `rippling_detail.json`). | No ETag, `no-store` (2026-10-20). | Undocumented. | 1, plus 1 detail per posting for a date |
| BambooHR | `GET {t}.bamboohr.com/jobs/embed2.php` (HTML widget) | none | Detail JSON has `datePosted` (2026-10-20 doc). | No ETag; raw page changes, so hash ids (`bamboohr.py`). | Undocumented; the official API needs a customer key. | 1, plus 1 detail per posting for a date |
| Breezy | `GET {t}.breezy.hr/json` (undocumented) | `published_date` | Publish date (meaning not documented). | Weak ETag, 304 (2026-10-20). | Undocumented. | 1 |
| Teamtailor | `GET {t}.teamtailor.com/jobs.rss?per_page=&offset=` | RSS `pubDate` | "publication date" ([support article](https://support.teamtailor.com/en/articles/11171756-rss-feed-how-to-guide)). | Weak ETag and Last-Modified, both 304 (2026-10-20). | First 100 jobs by default, `per_page` and `offset` to page (same article). | ceil(n / 100) |
| Recruitee | `GET {t}.recruitee.com/api/offers/` | `created_at`, `updated_at` | Meaning not documented. | No ETag; raw body changes per request, so hash ids (`recruitee.py`). | The careers site API needs no key ([FAQ](https://support.recruitee.com/en/articles/8213076-faq-api)). | 1 |
| JazzHR | `GET {t}.applytojob.com/apply/jobs` (HTML) | none | The detail page carries a JobPosting JSON-LD block (`jazzhr.py`); whether it fills `datePosted` is unconfirmed. | No ETag; raw page changes, so hash ids. | Undocumented. | 1, plus 1 detail per posting |
| SmartRecruiters (off) | `GET api.smartrecruiters.com/v1/companies/{t}/postings?limit=100&offset=` | `releasedDate` | Meaning not documented. | Page 1 ETag, 304 (`smartrecruiters.py`). | 10 requests a second, 8 concurrent ([rate limiting](https://developers.smartrecruiters.com/docs/rate-limiting)). Off because robots.txt allows only LinkedInBot (`config.toml`). | ceil(n / 100) |
| Pinpoint (off) | `GET {t}.pinpointhq.com/postings.json` | `first_published_at` | Name says first publish; not checked. | Weak ETag, 304 (2026-10-20). | Off: robots.txt `Disallow: /` (`config.toml`). | 1 |

**Webhooks.** None of these ATSs offers outsiders a subscription. Greenhouse has a `job_post_created` webhook, but it fires on creation, not on going live, and a customer sets it up with its own endpoint and secret ([webhooks docs](https://docs.greenhouse.io/webhooks.html)). I found no public webhook or delta feed for the others.

**What a 304 means.** A 304 says the whole list is unchanged. A 200 says something changed, but on the 2026-10-20 data most Ashby ETag changes were edits or closures, not new jobs (2026-10-20 doc, section 6.3). So a 200 still means "diff the ids", never "new job".

## 3. How fast the big services see a new ATS posting

| Service | First-party statement | Lag to beat |
|---|---|---|
| LinkedIn (Basic Jobs, the old Limited Listings) | "We scrape the XML feeds of ATS partners every six hours and for job board partners every 24 hours"; edits land "by the next scrape, typically within 24 hours"; no guarantee every job is ingested ([XML feeds FAQ](https://learn.microsoft.com/en-us/linkedin/talent/job-postings/xml-feeds-faq)). The ATS interval went from 24 to 12 hours in November 2021 and to 6 in February 2022 ([release notes](https://learn.microsoft.com/en-us/linkedin/talent/release-notes)). Greenhouse: Limited Listings "can require up to 48 hours", Apply Connect posts "appear instantaneously" ([Greenhouse](https://support.greenhouse.io/hc/en-us/articles/17073067102747-LinkedIn-Apply-Connect-integration)). Ashby: "Jobs appear on LinkedIn within two business days" ([Ashby](https://www.ashbyhq.com/integrations-directory/linkedin)). Lever: "once every 24 hours" (search snippet of [Lever help](https://help.lever.co/hc/en-us/articles/20087311222045); page blocked, unconfirmed). | 6 to 48 hours for a free listing. Paid posts and Apply Connect can be instant; how many small companies use them is unconfirmed. |
| Indeed | XML crawled from a URL or partner FTP: "four times a day at six-hour intervals"; Indeed-hosted FTP "as often as every two hours" ([FAQ](https://docs-plus.indeed.com/support/faq)). Job Sync API: "Indeed takes one to two hours to publish and index it" ([Job Sync FAQ](https://docs.indeed.com/job-sync-api/faq)). | 1 to 6 hours or more |
| Google for Jobs | Without the Indexing API, "it may take several days after publishing a page for Google to find and crawl it"; the Indexing API "prompts Googlebot to crawl your page sooner" ([JobPosting docs](https://developers.google.com/search/docs/appearance/structured-data/job-posting)). Default quota 200 publish requests a day ([quota](https://developers.google.com/search/apis/indexing-api/v3/quota-pricing)). Lever and Ashby job pages carry JobPosting JSON-LD; a Greenhouse job page had none in its server HTML (measured, one page each). Whether any ATS calls the Indexing API: no first-party statement, unconfirmed. | Hours to days |
| Jobright | "fill your dashboard with your best matched jobs every hour" ([llms-full.txt](https://jobright.ai/llms-full.txt)). Its repo's bot commits a median every 60 minutes, longest gap 1.3 hours, over the last 100 commits ([commits](https://github.com/jobright-ai/2026-Software-Engineer-New-Grad/commits)). Rows link to jobright.ai, so its real lag behind the ATS is unmeasured. | At least about 1 hour |
| Simplify | README: "updated and maintained daily" ([repo](https://github.com/SimplifyJobs/New-Grad-Positions)). In practice `listings.json` on `dev` is committed every 30 minutes ([commits](https://github.com/SimplifyJobs/New-Grad-Positions/commits/dev)); `raw.githubusercontent.com` serves it with an ETag and `max-age=300`. | Median 3.2 hours on Greenhouse (below) |

**Simplify lag, measured.** Method: eight `listings.json` snapshots from git history on 2026-11-09 (01:31 to 19:31 UTC, five of them one or two hours apart). Every one of the 11 rows that first appeared in a snapshot had a `date_updated` inside the window where it appeared, so `date_updated` is the time Simplify added a fresh row. On Greenhouse rows `date_posted` equals the ATS `first_published`, so `date_updated - date_posted` is Simplify's lag:

| Rows posted in the last 7 days | n | p25 | Median | p75 | p90 | Within 3 h |
|---|---|---|---|---|---|---|
| Greenhouse | 18 | 1.8 h | 3.2 h | 5.7 h | 7.9 h | 44% |
| Greenhouse, 14 days | 37 | 1.8 h | 3.4 h | 6.3 h | 9.5 h | 43% |

Caveats: rows Simplify has not added yet are missing, so this leans low. Ashby and Workday rows carry a day-only `date_posted` (midnight UTC) and are mostly added in an evening batch (about 23:30 UTC), so their numbers (median about 23.5 h) measure the batch, not the lag. Lever rows' `date_posted` equals the add time, so Lever lag cannot be read this way. A separate sample of 9 fresh Greenhouse rows checked against the ATS one by one gave a median of 1.8 hours (range 0.1 to 6.4). The 2026-11-01 design measured 2.8 hours on Greenhouse ([design](../superpowers/specs/2026-11-01-fresh-jobs-quiet-needs-you-design.md)).

**So:** a board read every 5 minutes sees a Greenhouse posting a median 2.5 minutes after it goes live. That is about 3 hours before Simplify, 6 or more hours before LinkedIn and Indeed, and days before Google without the Indexing API.

## 4. Sources that see a posting before or at the ATS

| Source | Latency | Cost | Terms | Verdict |
|---|---|---|---|---|
| ATS sitemaps | None exist: `sitemap.xml` is 404 on `jobs.lever.co`, `jobs.ashbyhq.com` and `job-boards.greenhouse.io`, also per company; no `Sitemap:` line in their robots.txt (measured) | n/a | `jobs.lever.co` robots: `Allow: /`, `Crawl-delay: 1` (measured) | No use |
| Company sitemaps | Stripe 404; Notion and OpenAI sitemaps list no careers or job URLs (measured, 3 sites) | 1 request a site | site by site | No use for speed |
| Workable global XML feed | "updated every hour" ([help](https://help.workable.com/hc/en-us/articles/4420464031767)) | 1 request covers every Workable customer; size unknown (HEAD gave `content-length: 0`, no ETag) | "should not be used to connect with job boards that have existing integrations with Workable"; nothing on personal use, unconfirmed | Worth one test download |
| Recruitee feeds | Same data as the API Knock reads (`/api/feeds/offers.xml`, [FAQ](https://support.recruitee.com/en/articles/8213076-faq-api), [feed doc](https://docs.recruitee.com/docs/feed)) | 1 a board | public | No gain |
| Teamtailor RSS | Already read by Knock | | "all of the data is publicly available" ([article](https://support.teamtailor.com/en/articles/11171756-rss-feed-how-to-guide)) | In use |
| JazzHR, Breezy, BambooHR feeds | No first-party public feed found; third-party pages name per-account URLs (unconfirmed) | | | No |
| HN "Who is hiring" | 2026 threads all posted at 11:00 or 11:01 New York time on the first weekday of the month (Algolia `search_by_date?tags=story,author_whoishiring`, measured). November's thread (item 50338728): 32 top-level posts in the first hour, 100 by 6 hours, 163 by 24 hours, 212 by 7 days (measured). Algolia indexed a new item 23 s after Firebase (measured). | Free; Algolia allows 10,000 requests an hour per IP ([hn.algolia.com/api](https://hn.algolia.com/api)); Firebase has "no rate limit" ([HackerNews/API](https://github.com/HackerNews/API)) | Public API, MIT | Use: poll every 15 min on that day |
| YC Work at a Startup, Wellfound | n/a | n/a | Both forbid scraping ([YC legal](https://www.ycombinator.com/legal), [Wellfound terms](https://wellfound.com/terms); quoted in the 2026-10-20 doc, section 2) | Do not crawl |
| GitHub new-grad repos | See table below | Free; one conditional GET | Read for board links only (#9 sources ruling) | Seed boards, not speed |
| Common Crawl | Monthly: CC-MAIN-2026-43, -39, -34, -30, -25 ([collinfo.json](https://index.commoncrawl.org/collinfo.json)) | Free | Open data | Slug discovery only |
| Certificate transparency, DNS | Boards live on the ATS's domains under wildcard certificates, so a new posting makes no new certificate. A new `careers.company.com` CNAME shows a new board, not a posting. crt.sh's JSON output is undocumented (unconfirmed). | Free | | No use for speed |

GitHub new-grad repos, last 100 commits each (`gh api repos/{o}/{r}/commits`, 2026-11-09):

| Repo | Commits a day | Median gap | p90 gap | Who commits |
|---|---|---|---|---|
| [SimplifyJobs/New-Grad-Positions](https://github.com/SimplifyJobs/New-Grad-Positions) | 78.7 | 29.5 min | 30.1 min | a pusher account (62) and `actions-user` (38) |
| [SimplifyJobs/Summer2027-Internships](https://github.com/SimplifyJobs/Summer2027-Internships) | 81.4 | 29.4 min | 30.1 min | same |
| [vanshb03/New-Grad-2027](https://github.com/vanshb03/New-Grad-2027) | 23.8 | 21.9 min | 240 min | owner and `actions-user` |
| [jobright-ai/2026-Software-Engineer-New-Grad](https://github.com/jobright-ai/2026-Software-Engineer-New-Grad) | 24.2 | 60 min | 61.5 min | `jobright-ai-bot` |
| [speedyapply/2027-SWE-College-Jobs](https://github.com/speedyapply/2027-SWE-College-Jobs) | 1.0 | 1,438 min | 1,576 min | owner; cron `0 12 * * *` in `.github/workflows/get-jobs.yml` |

Simplify's README build runs `on: push` (`.github/workflows/update_readmes.yml`), so the 30-minute rhythm comes from an outside system; which one is unconfirmed.

## 5. How open source trackers poll

| Repo | Boards | Schedule (actual) | Concurrency | New-posting detection |
|---|---|---|---|---|
| [kalil0321/ats-scrapers](https://github.com/kalil0321/ats-scrapers) (stapply), MIT | 80,390 companies, 5,125,222 jobs, 64 ATSs ([manifest](https://storage.stapply.ai/jobhive/v1/manifest.json), generated 2026-11-09T14:30Z) | Daily at 14:30 UTC (same minute on 2026-10-17, 10-20, 11-09) | `asyncio.Semaphore`, default `--concurrency 8`; Greenhouse, Lever and Ashby set no own cap (`scripts/run_pipeline.py` lines 1455, 1865, 612 to 629) | Full snapshot; Workday details cached in SQLite and fetched only for new listing URLs (lines 496 to 520). Greenhouse `posted_at = first_published or updated_at` (`scrapers/greenhouse.py` lines 77 to 82) |
| [Riley-Dorrington/job-board-aggregator](https://github.com/Riley-Dorrington/job-board-aggregator) (was Feashliaa), MIT | Slug lists: Greenhouse 8,333, Workday 12,884, BambooHR 11,316, iCIMS 10,108, Lever 4,368, Ashby 3,161 | Cron `33 7 * * *` (`.github/workflows/scrape-jobs.yml` line 4); last 5 runs started 6.5 to 10 hours late; scrape step 61 min | `MAX_WORKERS`: Workday 50, Greenhouse 30, Lever 30, iCIMS 30, BambooHR 10, Ashby 5, Paylocity 5; all platforms at once; 0.5 to 2.0 s jitter before each request (`scripts/scraper.py` lines 723 to 731, 1076, 236) | Dedup key (Greenhouse job id, else URL), `first_seen` carried forward (`scripts/merge_data.py` lines 11 to 25, 108 to 116) |
| [ambicuity/New-Grad-Jobs](https://github.com/ambicuity/New-Grad-Jobs), MIT | About 1,100 sources (Greenhouse 289, Workday 224, Ashby 126, Lever 56 and more, `config.yml`) | Cron `7,37 * * * *` (`update-jobs.yml` line 18); the last 30 runs came a median 370 min apart | `GREENHOUSE_CONCURRENCY = 25` with a per-domain limiter (`scripts/ngj/http.py` lines 189 to 193) | `first_seen` carried forward by `job_id` (`scripts/ngj/outputs/previous.py`) |
| [capy-zhiao/Job_Monitor](https://github.com/capy-zhiao/Job_Monitor), no license | 180 sources (`config.json`) | Cron `*/15 * * * *` (`monitor.yml` line 5); actual median gap 372 min | One request at a time, `urllib` | `seen.json` set of `greenhouse:{board}:{id}` uids (`jobmonitor/state.py`). Has a LinkedIn module, so off limits as a source |
| [rishilahoti/ashby-job-scraper](https://github.com/rishilahoti/ashby-job-scraper), license NOASSERTION | "2,000+ companies" on Ashby | Daily `0 0 * * *` plus up to 5 min jitter (`src/config/index.js` lines 11 to 14) | `CONCURRENCY = 16` (`src/scheduler/pipeline.js` line 12); sends a browser User-Agent | Diff by `jobId` plus `contentHash`: new, updated, removed (`src/diff/index.js`) |
| [adgramigna/job-board-scraper](https://github.com/adgramigna/job-board-scraper), MIT | not counted | Daily `0 7 * * *` | Scrapy, `ROBOTSTXT_OBEY = True` (`settings.py` line 34) | not checked |
| [speedyapply/JobSpy](https://github.com/speedyapply/JobSpy), MIT (ruled out, approach only) | n/a | on demand | Scrapes LinkedIn, Indeed, Glassdoor and others concurrently with `hours_old` and rotating proxies (README) | Reads job-site search pages, not ATS boards |

What they share: none polls faster than a few times a day in practice, none uses ETags, and all detect new postings by diffing job ids against the last run. Per-ATS concurrency runs 5 to 50, with Ashby lowest at 5. A local poller on the Mac with ETags and a 5-minute hot tier beats all of them.

## 6. Budget: how long a full read takes

### 6.1 The formula

With async HTTP (httpx or aiohttp) and one rate limit per host, hosts run side by side, so:

`wall time = max over hosts (requests to that host / that host's rate) + the slowest single request`

Today's crawler reads one board at a time, so its HTTP time is the sum over hosts, not the max. A 304 costs one round trip: 0.13 s on Greenhouse, 0.15 s on a cached Ashby board, 0.5 to 4.2 s on a big Lever board, 1.6 s for a Workday page, 0.9 s for an Oracle top-5 (measured). At 0.15 s, 20 requests a second needs only 3 or 4 in flight.

### 6.2 Assumed board mix

For 15,000 boards (a third of each for 5,000): Greenhouse 5,000, Ashby 3,000, Lever 2,000, Workable 2,000, BambooHR 1,000, Workday 800 tenants, others (Rippling, Breezy, Teamtailor, Recruitee, JazzHR, Oracle) 1,200. This follows the shape of stapply's lists (2026-10-20 doc, section 1.1), not Knock's store. One request per board: a conditional GET, or page 1 for Workday and Oracle.

### 6.3 Arithmetic

Rates: "today" is Knock's `host_interval_s` with one request at a time per bucket. "Proposed" is my suggestion. No vendor documents a GET limit except SmartRecruiters and Lever's `Crawl-delay: 1`. The Greenhouse rate sits below the 30 workers with 0.5 to 2 s jitter (about 24 a second) that job-board-aggregator runs daily.

| Host | Today: rate | 15,000 boards | Proposed: rate | 15,000 boards | 5,000 boards |
|---|---|---|---|---|---|
| Greenhouse (one host) | 4/s | 5,000 / 4 = 1,250 s | 20/s | 5,000 / 20 = 250 s | 83 s |
| Ashby (one host, CDN) | 2/s | 3,000 / 2 = 1,500 s | 10/s | 300 s | 100 s |
| Lever (one host) | 4/s | 500 s | 1/s (Crawl-delay) | 2,000 s | 667 s |
| Workable | 0.5/s | 4,000 s | 1/s, or the hourly global feed | 2,000 s, or 1 request | 667 s, or 1 request |
| BambooHR (tenant subdomains) | 1/s | 1,000 s | 5/s | 200 s | 67 s |
| Workday (tenant hosts) | 1/s for the whole bucket | 800 s | 1/s per tenant, 10 tenants at once, 1.6 s a page | 800 / 10 x 1.6 = 128 s | 43 s |
| Others (6 buckets, about 200 each) | 0.5 to 2/s | up to 400 s | 2/s each | 100 s | 33 s |
| **Wall time** | | sequential today: about 9,450 s (2.6 h) of spacing alone; in parallel: 4,000 s (Workable) | | **300 s** without Lever and Workable; **2,000 s** with Lever at 1/s | **100 s** without Lever and Workable; **667 s** with them |

Two hosts decide the length. Workable goes away with the global feed. Lever stays the long pole if `Crawl-delay: 1` is honored. RFC 9309, the robots standard, does not define `Crawl-delay`, but Knock already treats robots.txt as binding (`[ats_off]`), and a 1-per-second host means 3,600 Lever boards an hour at most.

Bodies and rules are a separate cost. On 2026-10-20, 84 to 97% of reads on ETag hosts came back with no rows (2026-10-20 doc, section 6.3), so only a few boards per pass need ingest work.

### 6.4 Tiered schedule

| Tier | Which boards | How often | Load |
|---|---|---|---|
| Pinned | Companies he picked, and the about 1,331 fit boards (his level or no stated level in 12 months) | Every 5 min on Greenhouse, Ashby, Breezy, Teamtailor, Recruitee, Workday page 1, Oracle top 5; every 10 to 15 min on Lever and Workable | 1,331 / 300 s is about 4.4 requests a second across all hosts |
| Warm | Any board with a US engineering posting in 12 months | Every 30 to 60 min | a few thousand an hour |
| Cold | Everything else | Today's cadence (6 hours to 7 days) | as today |
| Events | HN thread day, a fresh Form D, a new Simplify board link | Promote the company's board to Pinned for 14 days | small |

Promotion: any new id at his level moves a board up. Demotion: 30 quiet days move it down. Ashby faster than 60 s is pointless (CDN).

### 6.5 "Check now" in under 2 minutes

1. **Detect, do not ingest.** One request per board: conditional GET, Workday page 1, Oracle top 5. Diff ids against the store. No bodies, no decisions inside the pass.
2. **Order.** His picked companies first, then Pinned, then Warm, then the rest by time since the last read. Skip a board read in the last 60 s.
3. **Async with per-host token buckets** at the proposed rates, and all hosts at once. The global breaker stays as it is.
4. **Show new ids as they arrive**, with `first_seen = now`. Bodies and the rules run after the pass, only for new ids whose titles pass the tech filter.
5. **Say what is left:** "Checked 1,980 of 2,000 boards in 74 s. 6 new. Lever: 20 still to go." For about 2,000 boards, the whole pass fits in 2 minutes unless more than about 120 are on Lever (at 1/s). For 15,000 boards it does not, so the button covers Pinned plus Warm in the 2 minutes and lets the rest finish behind.
6. **One lock.** It shares `tick_lock` with the 30-minute tick, so the two never read the same board twice at once.

## 7. Ruled out by his decisions (noted only)

- LinkedIn, Indeed and Glassdoor scraping, JobSpy, paid job data services, and GitHub commit emails (#9 sources ruling, 2026-11-02; pipeline priority ruling). Reading their public help and developer docs for section 3 is not scraping.
- Postings or dates taken from SimplifyJobs or other new-grad lists: board links only (#9 sources ruling).

## Not verified

- Whether Greenhouse `first_published` survives an unpublish and republish.
- What JazzHR's JSON-LD `datePosted`, Breezy's `published_date` and Recruitee's `created_at` mean.
- Whether Workday's CXS list takes a sort parameter. The order on page 1 differs by tenant.
- `POSTING_DATES_DESC` works, but Oracle's docs do not list sort values.
- Why the big Ashby board lost its ETag (size, compression or CDN), and whether it is stable.
- Size, compression and terms of Workable's global XML feed.
- Whether 304s count against any limit, and whether Greenhouse or Ashby would mind 20 or 10 requests a second.
- Lever's LinkedIn sync interval (page blocked), and whether any ATS uses Google's Indexing API.
- Jobright's real lag behind the ATS.

## What this means for Knock

Ordered by how much earlier each move lets him see a posting.

1. **Split detect from ingest and run detect async per host.** Today a board waits at least 6 hours and a pass takes about 4 hours. One request per board, hosts in parallel, ingest only for boards whose ids changed. This alone moves first sight from hours to minutes.
2. **Read Pinned boards every 5 minutes.** That covers his picks and the about 1,331 fit boards, at about 4 requests a second in total. Median first sight is about 2.5 minutes after publish, about 3 hours before Simplify and 6 or more before LinkedIn.
3. **Ship "Check now" as a detect pass** in the order of section 6.5. Today's about 2,000 boards fit in under 2 minutes, and the screen shows what is still running.
4. **Read Workday and Oracle newest first.** Workday page 1 (on tenants whose page 1 is date ordered) and Oracle `sortBy=POSTING_DATES_DESC,limit=5` cost one request where a full read costs up to 100. That is what lets big enterprise boards join the 5-minute tier.
5. **Fix the two clock and ETag gaps.** When an Ashby 200 has no ETag, fall back to the id fingerprint, so big boards stop re-ingesting on every read. Date every posting by Knock's `first_seen`, and keep Ashby `publishedAt` and Lever `createdAt` as display only.
6. **Add a Warm tier at 30 to 60 minutes** for every board with a US engineering posting in 12 months. `floor_s` stays 6 hours only for Cold.
7. **Respect Lever's `Crawl-delay: 1`, and test Workable's hourly global feed.** Today Knock spaces Lever at 0.25 s. If the Workable feed is a sane size, one request an hour replaces every Workable call.
8. **On HN thread day, poll Algolia every 15 minutes from 11:00 New York time for 24 hours,** and read Simplify's `listings.json` every 30 minutes with `If-None-Match`, for new board links only. These add boards Knock lacks; they do not make known boards faster.
