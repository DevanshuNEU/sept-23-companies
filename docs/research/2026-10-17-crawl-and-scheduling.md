# Crawl sources, ATS APIs and scheduling (research, 2026-10-17)

Repo stats come from `gh api` on 2026-10-17. Header checks were one read-only `curl -I` per ATS plus one conditional repeat.

## 1. Existing OSS worth reusing

| Project | What it does | License | Last commit | Verdict |
|---|---|---|---|---|
| [speedyapply/JobSpy](https://github.com/speedyapply/JobSpy) | Scrapes LinkedIn, Indeed, Glassdoor, ZipRecruiter and others into a DataFrame, with proxy support | MIT | 2026-10-15 | Skip. Its sources are job sites, not ATS boards, and LinkedIn is off limits. |
| [stapply-ai/ats-scrapers](https://github.com/kalil0321/ats-scrapers) (now redirects to kalil0321/ats-scrapers) | Python adapters for 60+ sources (ten of his eleven ATSs; no Dover adapter), plus a hosted dataset ([manifest](https://storage.stapply.ai/jobhive/v1/manifest.json): 80,390 companies, 5.1M jobs, snapshot 2026-10-17T14:30Z) | MIT | 2026-09-02 (v0.3.0), 59 open issues | Borrow. Mine `ats-companies/*.csv` for new slugs (row counts: Greenhouse 6,032, Workable 4,753, Ashby 3,449, SmartRecruiters 2,748, Lever 2,403). Use its adapters as an endpoint reference. Don't adopt the package (Python 3.11+, third-party deps; the crawler is stdlib-only). |
| [Feashliaa/job-board-aggregator](https://github.com/Feashliaa/job-board-aggregator) | Daily GitHub Actions ETL over 7 ATSs into chunked gzip JSON for a static site | MIT | 2026-10-17 | Borrow ideas: per-ATS concurrency (Workday 50, Greenhouse/Lever 30, BambooHR 10, Ashby 5), force-pushing generated data to a separate repo so history never grows, and an alert when a platform's job count drops sharply. |
| [SimplifyJobs/New-Grad-Positions](https://github.com/SimplifyJobs/New-Grad-Positions) | Curated new-grad list. Machine-readable [`listings.json`](https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json) (19,476 rows) with `url`, `date_posted`, `active` and `sponsorship` fields | No license file (`gh api .../license` returns 404) | 2026-10-17 (bot commits several times a day) | Borrow as a signal: fetch with `If-None-Match` (raw.githubusercontent sends an ETag, `max-age=300`) and seed the hot tier from slugs in `url`. No license, so don't republish. `sponsorship` is "Other" on 19,392 rows, useless as a visa filter. |
| [speedyapply/2027-SWE-College-Jobs](https://github.com/speedyapply/2027-SWE-College-Jobs), [vanshb03/New-Grad-2027](https://github.com/vanshb03/New-Grad-2027), [jobright-ai/2026-Software-Engineer-New-Grad](https://github.com/jobright-ai/2026-Software-Engineer-New-Grad) | Similar daily lists | none / MIT / none | 2026-10-17 / 2026-08-21 / 2026-10-17 | Skip, or mine for slugs only (overlap with Simplify). |

## 2. Public ATS endpoints

| ATS | Endpoint | Documented limits | Header check (2026-10-17) |
|---|---|---|---|
| Greenhouse | `GET boards-api.greenhouse.io/v1/boards/{token}/jobs[?content=true]`. No auth needed on GETs, and each job has `updated_at` ([docs](https://docs.greenhouse.io/job-board.html)) | None documented | `etag: W/"..."`, `cache-control: max-age=0, private, must-revalidate`. `If-None-Match` returns **304**. `If-Modified-Since` returns 200 (no `Last-Modified`). |
| Lever | `GET api.lever.co/v0/postings/{site}?mode=json&skip=&limit=` ([docs](https://github.com/lever/postings-api)) | Only application POSTs (2/s, then 429). No GET limit documented. | `ETag` present. `If-None-Match` returns **304**. |
| Ashby | `GET api.ashbyhq.com/posting-api/job-board/{name}[?includeCompensation=true]` ([docs](https://developers.ashbyhq.com/docs/public-job-posting-api)) | None documented. Feashliaa treats it as the tightest limiter (5 workers). | `etag`, `cache-control: public, max-age=60, stale-while-revalidate=60`, `cf-cache-status: HIT`. `If-None-Match` returns **304**. CDN-cached for 60 s, so sub-minute polling gains nothing. |
| SmartRecruiters | `GET api.smartrecruiters.com/v1/companies/{id}/postings` (offset/limit) | SmartAPIs: 10 req/s and 8 concurrent ([rate limiting](https://developers.smartrecruiters.com/docs/rate-limiting)). The [Posting API page](https://developers.smartrecruiters.com/docs/posting-api) says it needs an API key or OAuth, yet the unauthenticated call returned 200. Unverified whether that keeps working. | `etag` present, no `X-RateLimit-*` headers. `If-None-Match` returns **304**. |
| Workable | `GET apply.workable.com/api/v1/widget/accounts/{slug}` (no first-party doc found; endpoint taken from [ats-scrapers workable.py](https://github.com/kalil0321/ats-scrapers/blob/main/src/ats_scrapers/scrapers/workable.py)) | Unverified | GET returns 200 with **no ETag and no Last-Modified**. HEAD returns 404. Change detection has to hash the body. |
| Workday | `POST {tenant}.wdN.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` with JSON `{limit, offset, searchText, appliedFacets}`. Undocumented internal API (see [ats-scrapers workday.py](https://github.com/kalil0321/ats-scrapers/blob/main/src/ats_scrapers/scrapers/workday.py)). | Unverified | `cache-control: no-store, no-cache`, no ETag. POST only, so conditional requests don't apply. |

Webhooks: none of the six documents a public webhook or delta feed for third parties. The cheapest change signal is ETag plus 304 on Greenhouse, Lever, Ashby and SmartRecruiters: an unchanged board costs one bodiless round trip. None sends `Last-Modified`. Whether 304s count against rate limits is unverified.

## 3. Scheduling

### Mac (launchd, pmset)

- `StartInterval`: "If the system is asleep during the time of the next scheduled interval firing, that interval will be missed" (`man launchd.plist`, macOS 15).
- `StartCalendarInterval`: "Unlike cron ... launchd will start the job the next time the computer wakes up. If multiple intervals transpire before the computer is woken, those events will be coalesced into one event" (same man page). If the machine is **off**, "the job does not execute until the next designated time occurs" ([Apple, Scheduling Timed Jobs](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/ScheduledJobs.html)).
- `pmset repeat` allows "only one pair of repeating events": one power-on/wake and one power-off/sleep, set per weekday (`man pmset`). `pmset schedule wake "MM/dd/yy HH:mm:ss"` is one-shot. Neither can produce a wake every 30 minutes, and both need sudo. Whether scheduled wakes fire with the lid closed on battery is unverified.

Takeaway: use `StartCalendarInterval` on the laptop so a run missed during sleep fires once on wake. Nothing runs while it is off, so the hot loop belongs in the cloud.

### Cloud options (job every 30 min, 2 to 5 min each, about 1,460 runs a month)

| Option | Key facts | Rough monthly cost |
|---|---|---|
| GitHub Actions, public repo | Minimum interval is 5 min. Runs "can be delayed during periods of high loads", worst at the top of the hour, and "some queued jobs may be dropped". Public-repo schedules are disabled after 60 days without repository activity. Default branch only ([events docs](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)). Standard runners are free for public repos ([billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)). | $0 |
| GitHub Actions, private repo | 2,000 free min/month on Free, Linux $0.006/min ([billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)), billed per job and rounded up to the next minute ([job time](https://docs.github.com/en/actions/how-tos/monitor-workflows/view-job-execution-time)) | 2 min runs: 2,920 min, about $5.50. 5 min runs: 7,300 min, about $32. |
| Modal | `modal.Cron("7,37 * * * *")`, active once the app is deployed ([guide](https://modal.com/docs/guide/cron)). $30/month free credits, $0.0000131/core/s (0.125 core minimum), $0.00000222/GiB/s ([pricing](https://modal.com/pricing)). | 0.25 core and 0.5 GiB: about $0.77 (2 min) to $1.92 (5 min), inside the free credit |
| Cloudflare Workers Cron + D1 | Free plan: 10 ms CPU and 50 subrequests per invocation, 5 cron triggers. Paid: 5 min CPU, 10,000 subrequests, 15 min wall clock for cron ([limits](https://developers.cloudflare.com/workers/platform/limits/)). Paid minimum is $5/month ([pricing](https://developers.cloudflare.com/workers/platform/pricing/)). D1 free: 5M rows read and 100k written per day, 5 GB ([D1 pricing](https://developers.cloudflare.com/d1/platform/pricing/)). Needs a rewrite for Workers. | $5 (free plan too small for hundreds of boards per run) |
| Fly.io | `--schedule` only takes hourly, daily, weekly or monthly, "approximate" ([docs](https://docs.fly.io/machines/flyctl/fly-machine-run)). No free tier for new orgs. shared-cpu-1x 256 MB costs $2.19/month always on. ([pricing](https://docs.fly.io/about/pricing)). | About $2.20 for an always-on machine running its own cron |
| VPS | Oracle Always Free gives 2 AMD micro VMs plus Ampere A1 capacity (2 OCPU, 12 GB), but reclaims instances whose 95th-percentile CPU, network and (A1) memory all stay below 20% over 7 days ([Oracle](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)). A bursty crawler looks idle. Hetzner CX23 reported at €5.49/month after the June 2026 increase (unverified: [third-party](https://privatedevops.com/news/hetzner-june-2026-cloud-price-increase-what-to-do), first-party page did not render). | $0 (Oracle, reclaim risk) or about $6 |

## 4. Queue and SQLite tooling

- **Huey** ([coleifer/huey](https://github.com/coleifer/huey), MIT, 3.4.0 on 2026-09-04): `SqliteHuey` needs only stdlib `sqlite3` ([API](https://huey.readthedocs.io/en/latest/api.html)). Periodic tasks are checked once a minute ([guide](https://huey.readthedocs.io/en/latest/guide.html)). After suspend/resume the consumer skips the missed checks instead of replaying them ([consumer.py L188-194](https://github.com/coleifer/huey/blob/master/huey/consumer.py)). No gain over launchd.
- **APScheduler** ([agronholm/apscheduler](https://github.com/agronholm/apscheduler), MIT, stable 3.11.3 on 2026-06-28, v4 still at 4.0.0a6): `SQLAlchemyJobStore` persists jobs. After downtime, missed runs are "misfired" and handled by `misfire_grace_time` and `coalesce` ([3.x guide](https://apscheduler.readthedocs.io/en/3.x/userguide.html)). It needs a long-running process and SQLAlchemy.
- **litequeue** ([litements/litequeue](https://github.com/litements/litequeue), MIT, last commit 2026-07-31, Python 3.12+): SQLite queue with claim IDs, UUIDv7 message IDs and idempotency keys. **persist-queue** ([peter-wangxu/persist-queue](https://github.com/peter-wangxu/persist-queue), BSD-3, last push 2026-01-08) is similar.
- **Litestream** ([benbjohnson/litestream](https://github.com/benbjohnson/litestream), Apache-2.0, v0.5.17 on 2026-08-31): streams WAL pages to S3, B2 and others and restores point in time. It assumes a single writer, and an optional `litestream-vfs` serves read-only queries straight from the replica ([how it works](https://litestream.io/how-it-works/)).
- **Turso/libSQL embedded replicas**: reads hit a local file and writes go to the remote primary, with `syncInterval` for auto-sync. "Do not open the local database while the embedded replica is syncing" ([docs](https://docs.turso.tech/features/embedded-replicas/introduction)). Free plan: 5 GB, 500M rows read and 10M written per month, 3 GB syncs ([pricing](https://turso.tech/pricing)). Needs the libsql client, which is not stdlib.
- **Cloudflare D1**: `wrangler d1 export <db> --remote --output=db.sql` pulls a snapshot, and a running export blocks other requests ([docs](https://developers.cloudflare.com/d1/best-practices/import-export-data/)).
- **sqlite-utils** ([repo](https://github.com/simonw/sqlite-utils), Apache-2.0, 4.2.1 on 2026-08-13) and **Datasette** ([repo](https://github.com/simonw/datasette), Apache-2.0, 1.0a41 on 2026-09-24, alpha): JSON inserts and local browsing.

## 5. Tiered polling design

**State per board** (one SQLite table): `ats, slug, tier, etag, body_sha256, last_checked, last_changed, unchanged_streak, last_entry_level_hit`. **Per posting**: `ats, slug, posting_id, first_seen, last_seen, closed_at, updated_at`.

**Tiers**
- **Hot** (every 30 min, about 300 to 600 boards): the target list, plus any board with a matching entry-level US SWE posting in the last 60 days, plus slugs taken from Simplify `listings.json` URLs. Prefer ETag-capable ATSs.
- **Warm** (every 6 h, a few thousand): boards that changed in the last 30 days, plus hot-eligible Workable/Workday boards (no ETag, so every check is a full download).
- **Cold** (the existing twice-weekly full run over about 36,000 boards): also does discovery and promotion.

**Per request**
1. Send `If-None-Match: <etag>`. On 304, bump `unchanged_streak` and stop.
2. On 200 (or for ATSs without an ETag), hash the sorted posting ID list. If the hash matches, treat it as unchanged.
3. Otherwise diff the ID sets: `new = now - prev` gets `first_seen`, and `prev - now` gets `closed_at`. On Greenhouse, a changed `updated_at` on an existing ID marks an edit.
4. Filter only the new IDs, and push only matches to the dashboard and to notifications.

**Promotion and demotion**: promote on any new matching posting. Demote hot to warm after about 14 days with no matching posting and `unchanged_streak` over 200. Keep concurrency per ATS (borrowing Feashliaa's numbers: Ashby about 5, Greenhouse and Lever about 30). On 429, back off and honor `Retry-After`. A few hundred mostly-304 requests should take under 2 minutes (estimate, not measured).

**Placement**: hot and warm tiers run in the cloud and append new and closed IDs to a delta file. A launchd `StartCalendarInterval` job on the laptop pulls deltas on wake, merges them into local SQLite and regenerates the dashboard JS.

## Recommendation

- **Adopt**: launchd `StartCalendarInterval` for laptop jobs (not `StartInterval`, not `pmset`). Conditional GETs with stored ETags on Greenhouse, Lever, Ashby and SmartRecruiters. Posting-ID set diffing with body hashes as a fallback. GitHub Actions in a **public** repo for the 30-minute hot loop, offset from the top of the hour (`7,37 * * * *`), with state and deltas pushed to a `data` branch of the same repo. That costs $0 and needs no rewrite. The bot pushes probably count as activity for the 60-day rule (unverified), so add a monthly manual commit as insurance. If schedule delays or drops become a problem, or the target list should stay private, move the same stdlib script to **Modal** (about $1 to $2 a month, inside the free credit). Datasette for browsing.
- **Borrow**: slug lists from `ats-scrapers/ats-companies/*.csv` and from Simplify `listings.json` URLs. Endpoint details from ats-scrapers adapters. Per-ATS concurrency limits, stale pruning and volume anomaly alerts from Feashliaa.
- **Skip**: JobSpy (job-site scraping, LinkedIn). The ats-scrapers package as a dependency. Huey and APScheduler (a daemon with no sleep-safety beyond launchd). Fly scheduled machines (hourly at best). Cloudflare Workers (needs a rewrite, $5 for the paid plan). Oracle free VMs (idle reclaim). Turso and Litestream for now: a delta file pulled on wake is enough for one user. Revisit Turso embedded replicas if the laptop needs live SQL over cloud state.
