# Data lifecycle and reliability for the v2 store (research, 2026-10-17)

Part A numbers were measured on 2026-10-17, read-only. Databases were opened with `file:...?mode=ro&immutable=1` (sqlite3 3.43.2 CLI and Homebrew Python 3.14.3 / SQLite 3.51.2), and nothing outside this file was written. Part B cites first-party pages fetched the same day. Anything marked **unverified** is an assumption or a third-party claim.

## Part A: what exists

### A1. radar.db

`~/Desktop/DevVault/job-search/pipeline/radar.db` is 679,788,544 bytes (165,964 pages of 4,096 B, freelist 3). File header bytes 18-19 are `02 02`, so it is in WAL mode, and `auto_vacuum` is 0 (NONE), which means it can only shrink through a full VACUUM. Every table is `STRICT`. The `immutable` URI reports `journal_mode=delete`, so read the header instead.

| Table | Rows | On-disk (dbstat, incl. indexes) |
|---|---|---|
| posting_version | 173,253 | 525 MB (77% of file) |
| posting | 101,852 | 61 MB |
| decision | 181,107 (3 rulesets) | 33 MB |
| board_fetch | 612,092 | 34 MB (55 B/row) |
| poll_run | 2,456 (172 ok, 2,284 partial, 0 failed) | 0.4 MB |
| outbox | 4,644 | 1.2 MB |
| app_event | 374 | 0.1 MB |
| board | 508 (494 active) | 0.1 MB |
| body_attempt 707, label 0, meta 1, schema_migration 5 | | small |

- Date range: first_seen 2026-07-28T21:24Z to 2026-10-17T18:30Z (81 days), 30.3 runs/day.
- Growth: 6,588 to 7,642 new postings per week in weeks 36 to 41, which is 31,532 in the last 30 days (1,051/day). Over its life the file has grown 8.4 MB/day.
- Bodies: 67,670 version rows carry `body_text` across 45,019 postings, but only 37,630 bodies are distinct, so 44% of stored bodies are duplicates. That comes from version rows re-copying an unchanged body, and from one JD posted under several locations. Bodies total 239.8 MB and payloads 204.6 MB, and some payloads embed the description again (Amazon's `desc`).
- On a random sample of 3,000 bodies the mean is 3,859 B and the median 3,961 B. zlib level 6 brings the mean to 1,777 B (2.17x), and level 9 gains nothing. Payloads compress 2.15x.
- Per-posting cost: posting row plus its indexes is 602 B, and one decision row plus its indexes is 180 B, so metadata is 782 B. The current all-in cost is 6,344 B per posting, excluding board_fetch.
- In-lane: 19,953 postings (19.6%) got `show` at least once. That share comes from 508 hand-picked tech boards.

### A2. Crawl working files (`Sept 23 Companies/refresh/work/`)

- `work/` is 743 MB in total. `research/` accounts for 650 MB, `everify/` 56 MB and `crawl/` 32 MB.
- slugs.json has 7,891 boards (greenhouse 3,015, ashby 2,476, lever 2,400). slugs_live.json has 2,314 and slugs_more.json 34,082. slugs.json and slugs_more.json share no boards. live plus more is 36,396, which is the "~36,000".
- No raw crawl output keeps descriptions. `crawl/more/alive_cache.json` keeps a job count per board. Across 48,150 probed boards, 25,471 are alive with 782,721 open postings. For slugs_more alone it is 24,985 alive boards with **680,253 postings** (mean 27, median 5 per alive board). SmartRecruiters accounts for 288,810 of those (42%), led by Domino's (24,863, cached twice under `Dominos` and `dominos`).
- slugs_live boards are not in the cache. Using per-ATS means from the cache (greenhouse 25.6, ashby 14.4, lever 25.4) they add about 48,800 (estimate). One full crawl therefore sees about **729,000 open postings**. The description length (3,859 B) is borrowed from radar.db; unverified for the broad crawl.
- A full crawl of 31,214 to 31,402 boards took 2,388 to 2,550 s on the laptop (`more/run1.log` to `run4.log`). That is longer than the planned 30-minute interval.
- `.research/h1bdata/` holds h1b.db (271.6 MB, 676,973 LCA rows), h1b_full.db (750.2 MB, 1,570,129 rows) and uscis.db (24.6 MB, 151,660 rows). That is 1.05 GB of databases in a 2.3 GB directory that also holds the source xlsx and csv files.

### A3. Projections

Assumptions:

| Symbol | Value | Basis |
|---|---|---|
| O, open postings | 729,000 | A2 |
| L, mean posting lifetime | 45 days | **Unverified**. radar.db brackets it at 23 to 81 days (1,051 new/day against 24,697 seen in the last 2 days versus 84,774 not flagged closed) |
| N, new postings/day | O/L = 16,200 | 5.91 M/year |
| Postings ever seen | P1 = O + 365N = 6.64 M; P3 = O + 1,095N = 18.47 M | |
| f, in-lane share | 5% (sensitivity 20%) | **Unverified**. 19.6% on curated boards, far lower on BambooHR and SmartRecruiters retail |
| Metadata | 782 B/posting | A1 |
| Body, zlib, page fill 0.915 | 1,777 × 1.093 = 1,942 B, one per posting after dedupe | A1 |

(a) Every posting gets a full body, stored the way radar.db stores it today. Today's cost is 6,344 B per posting with 44.2% of postings bodied, and a bodied posting costs 6,105 B, so full coverage costs 6,344 + 0.558 × 6,105 = 9,751 B per posting.

(b) Metadata for every posting, plus a compressed, deduped body for in-lane postings only: 782 + f × 1,942 = 879 B (f = 5%) or 1,171 B (f = 20%).

(c) As (b), but bodies are dropped 90 days after close. Bodies held at steady state = f × N × (L + 90) = 0.05 × 16,200 × 135 = 109,000 × 1,942 B = 212 MB (850 MB at f = 20%). Size = P × 782 + 212 MB.

(d) is added for comparison: out-of-lane rows are deleted 90 days after close and kept as a 35 B archive row plus monthly rollups. Live window = N × 135 × 782 = 1.71 GB. In-lane rows grow 0.23 GB/yr, the archive 0.21 GB/yr, bodies stay at 0.21 GB and rollups grow 0.05 GB/yr (25,000 boards × 12 months × 3 cells × 60 B; **unverified** cell count).

| Scenario | 1 year | 3 years |
|---|---|---|
| (a) all bodies | 6.64 M × 9,751 = **64.8 GB** | **180 GB** |
| (b) in-lane bodies, zlib | 5.84 GB (7.78 at f=20%) | 16.2 GB (21.6) |
| (c) (b) + drop bodies at close+90d | 5.41 GB (6.04) | 14.7 GB (15.3) |
| (d) (c) + fold out-of-lane rows | 2.42 GB (3.75) | 3.40 GB (6.12) |

Sensitivity to L: at 23 days (a) reaches 120 GB in year 1 and (c) 27.7 GB at year 3. At 81 days the figures are 39 GB and 8.3 GB.

Two findings follow. Once bodies are limited to in-lane postings, metadata rows for out-of-lane postings are 90% or more of the file, so (d) is what bounds growth. Separately, a radar-style `board_fetch` row per board per run would cost 36,396 × 48 × 55 B = 92 MB/day, or **33 GB/yr**, which is more than all the postings combined. The laptop should keep per-board health state and run summaries, not per-run fetch rows.

## Part B: research

### B4. SQLite at this scale

| Topic | Fact | Source |
|---|---|---|
| Limits | Maximum size is about 17.5 TB at 4,096 B pages (4,294,967,294 pages), and the default BLOB limit is 1,000,000,000 bytes | [limits](https://www.sqlite.org/limits.html) |
| Fit | "less than a terabyte of content, SQLite is almost always a better solution"; one writer at a time | [whentouse](https://www.sqlite.org/whentouse.html) |
| WAL | Readers and writers don't block each other. Auto-checkpoint runs at 1,000 pages. With "always at least one active reader ... the WAL file will grow without bound". Checkpoints don't truncate the WAL unless `journal_size_limit` is set. Not usable over a network filesystem | [wal](https://www.sqlite.org/wal.html) |
| synchronous | NORMAL in WAL "loses durability across power loss" but stays atomic, consistent and isolated | [pragma](https://www.sqlite.org/pragma.html#pragma_synchronous) |
| auto_vacuum | Must be set "before any tables are created", otherwise changing it needs a VACUUM. INCREMENTAL frees pages only when `incremental_vacuum(N)` runs. It does not defragment, and FULL "can actually make fragmentation worse" | [pragma](https://www.sqlite.org/pragma.html#pragma_auto_vacuum) |
| Free pages | Deleted pages go on the freelist "and are reused when additional pages are required" | [fileformat](https://www.sqlite.org/fileformat2.html) |
| VACUUM | Needs up to 2x the file size in free disk space and is a write operation (VACUUM INTO is not) | [vacuum](https://www.sqlite.org/lang_vacuum.html) |
| FTS5 size | On 1,636 MiB of email the index was 743 MiB with `detail=full`, 340 MiB with `detail=column` and 134 MiB with `detail=none`. `contentless_delete=1` (3.43.0+) stores only the index and supports DELETE | [fts5](https://www.sqlite.org/fts5.html) |
| Blobs | "For BLOBs smaller than 100KB, reads are faster when the BLOBs are stored directly in the database file" | [blobs](https://www.sqlite.org/intern-v-extern-blob.html) |
| JSON | Built in since 3.38.0. JSONB (3.45.0) is "intended for internal use by SQLite only" | [json1](https://www.sqlite.org/json1.html) |
| STRICT | Since 3.37.0. Rejects lossy type coercion with SQLITE_CONSTRAINT_DATATYPE | [strict](https://www.sqlite.org/stricttables.html) |
| optimize | For long-lived connections run `PRAGMA optimize=0x10002` on open and `PRAGMA optimize` periodically | [pragma](https://www.sqlite.org/pragma.html#pragma_optimize) |
| Online backup | VACUUM INTO (3.27.0, [release](https://www.sqlite.org/releaselog/3_27_0.html)) writes "a consistent snapshot". The backup API restarts if another process writes mid-backup, and holds no read lock between steps ([backup](https://www.sqlite.org/backup.html)). Python's `Connection.backup` "works even if the database is being accessed by other clients" ([python](https://docs.python.org/3/library/sqlite3.html)). `sqlite3_rsync` works with both ends live ([rsync](https://www.sqlite.org/rsync.html)) | |
| Corruption | Never `cp` a live database or separate it from its `-wal` file | [howtocorrupt](https://www.sqlite.org/howtocorrupt.html) |

Applied to this project:

- Compressed bodies (1.9 KB) stay inside the database.
- An external-content FTS table cannot read zlib blobs, so use a contentless-delete FTS5 table with `detail=column`. At the email ratio (0.21x) the 109,000 retained bodies (421 MB of raw text) would need about 88 MB of index.
- `/usr/bin/python3` links SQLite 3.43.2, which is the floor for contentless_delete. Keep launchd on `/opt/homebrew/bin/python3` (3.51.2), as `com.devanshu.jobradar.plist` already does.
- ~/Desktop is not iCloud-synced (no `Mobile Documents/.../Desktop`), so WAL is safe there. 366 GiB of disk is free.

### B5. Retention

Time-series systems keep raw data briefly and aggregates for much longer. InfluxDB's guide keeps raw data for 2 hours and downsampled data for 52 weeks ([influx](https://docs.influxdata.com/influxdb/v1/guides/downsample_and_retain/)). Thanos downsamples raw data after 40 hours and 5m data after 10 days, and warns that raw retention must outlast the downsampling lag ([thanos](https://thanos.io/tip/components/compact.md/)). TimescaleDB says aggregates survive raw deletion "so long as you don't refresh the aggregate over the deleted data" ([tiger](https://www.tigerdata.com/docs/use-timescale/latest/data-retention/data-retention-with-continuous-aggregates)). For this project, a month's rollup must be frozen before its raw rows can go.

Edits and reposts:

- Kimball type 2 adds a new row with effective and expiry dates ([kimball](https://www.kimballgroup.com/data-warehouse-business-intelligence-resources/kimball-techniques/dimensional-modeling-techniques/type-2/)). `posting_version` already does this. Add `valid_to` and write a version only when the content hash changes.
- Ashby's `publishedAt` is "when the job was last published" ([ashby](https://developers.ashbyhq.com/docs/public-job-posting-api)), so it moves on republish. Do not use it as first_seen. Link reposts through the existing `fingerprint` and count a repost within 30 days as one requisition (**unverified** threshold).
- A posting that closes gets a tombstone: set `closed_at` and keep the row. Hard delete happens only in the sweep, after the rollup is frozen.
- A closed posting is not a hire. Rollups count postings opened and closed, not people hired.

### B6. Off-machine backup

| | Free tier | After free tier | Source |
|---|---|---|---|
| Backblaze B2 | First 10 GB of storage. Class A, B and C calls free. Egress free up to 3x stored | $6.95/TB/month | [b2 pricing](https://www.backblaze.com/cloud-storage/pricing) |
| Cloudflare R2 | 10 GB-month, 1 M Class A ops, 10 M Class B ops. Egress free | $0.015/GB-month, $4.50/M Class A ops | [r2 pricing](https://developers.cloudflare.com/r2/pricing/) |

R2's getting-started page requires "the checkout flow to add an R2 subscription", so whether it needs a card is **unverified** ([r2](https://developers.cloudflare.com/r2/get-started/)). Both offer prefix lifecycle expiry ([b2](https://www.backblaze.com/docs/cloud-storage-lifecycle-rules), [r2](https://developers.cloudflare.com/r2/buckets/object-lifecycles/)).

Litestream v0.5.17 (2026-08-31) supports macOS ([releases](https://github.com/benbjohnson/litestream/releases)). It auto-detects R2 and B2 endpoints ([guide](https://litestream.io/guides/s3-compatible/)). Defaults are a 1 s sync, 24 h snapshot interval and **24 h retention** ([config](https://litestream.io/reference/config/)). It holds a long-running read transaction so that it controls checkpoints ([how it works](https://litestream.io/how-it-works/)), and it advises `wal_autocheckpoint = 0` ([tips](https://litestream.io/tips/)). Restore with `litestream restore -o PATH [-timestamp T]`, which refuses to overwrite an existing file ([restore](https://litestream.io/reference/restore/)). Its behaviour across laptop sleep is undocumented (**unverified**).

Verdict: use the nightly `VACUUM INTO` plus upload. Litestream's strength is a one-second recovery point on a server that never sleeps, which this setup doesn't need. Posting data can be replayed from the data branch, and the irreplaceable rows (374 app_events, 4,644 outbox rows) are small. A 24 h retention window would not catch a bad sweep noticed a week later. `VACUUM INTO` also produces a compacted file.

### B7. Monitoring

- **healthchecks.io Hobbyist**, $0: "Monitor 20 jobs", "100 log entries per job", no SMS or phone credits ([pricing](https://healthchecks.io/pricing/)). Endpoints are `hc-ping.com/<uuid>` with `/start`, `/fail`, `/<exit-status>` and `?rid=` for durations. Pings are rate-limited above 5 per minute ([api](https://healthchecks.io/docs/http_api/)). At a 30-minute cadence, 100 entries cover about 50 hours.
- **GitHub notifications**: scheduled-run notifications go to the user who created the workflow or last edited its cron, and can be limited to failed runs ([notifications](https://docs.github.com/en/actions/concepts/workflows-and-actions/notifications-for-workflow-runs)).
- **60-day rule**: "In a public repository, scheduled workflows are automatically disabled when no repository activity has occurred in 60 days" ([events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)). GitHub does not define activity.
  - One community answer says a bot commit keeps the workflow alive ([discussion 148632](https://github.com/orgs/community/discussions/148632), not GitHub staff). Whether Actions-bot commits to a non-default `data` branch count is **unverified**.
  - `PUT /repos/{o}/{r}/actions/workflows/{id}/enable` re-enables a workflow ([rest](https://docs.github.com/en/rest/actions/workflows#enable-a-workflow)). Whether that resets the timer is a third-party claim ([gh-workflow-immortality](https://github.com/PhrozenByte/gh-workflow-immortality)).
  - The popular `gautamkrishnar/keepalive-workflow` repo now shows as disabled by GitHub Staff for a terms violation, so avoid dummy-commit keepalives.
- **Actions terms**: on GitHub-hosted runners the terms prohibit "any other activity unrelated to the production, testing, deployment, or publication of the software project" ([terms](https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features)). A crawl-as-cron is a gray area; that is a risk to weigh, not a ruling. Public repos run standard runners free ([billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)), with a 6 h job limit ([limits](https://docs.github.com/en/actions/reference/limits)).

### B8. Data branch growth

GitHub's guidance is "ideally less than 1 GB, and less than 5 GB is strongly recommended" ([large files](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)). The hard limits are 10 GB of `.git` on disk, 2 GB per push and a recommended 6 pushes per minute ([limits](https://docs.github.com/en/repositories/creating-and-managing-repositories/repository-limits)).

Estimated delta volume: 2 × 16,200 open or close records × 300 B, plus 5% of 16,200 bodies × 3,859 B, is 12.8 MB/day raw. At about 2.2x git zlib (**unverified**, borrowed from A1) that is 5.8 MB/day, or **2.1 GB/yr** of history. Kept forever, the branch passes 1 GB in about 6 months.

Rotate it:

1. Weekly, the Action makes a `git checkout --orphan` commit holding only the last 30 days of delta files and force-pushes `data` ([checkout](https://git-scm.com/docs/git-checkout)).
2. The laptop fetches with `+refs/heads/data:refs/remotes/origin/data` ([fetch](https://git-scm.com/docs/git-fetch)).
3. Ingestion is keyed by delta filename in an `ingested_file` table, never by commit ancestry.

Old objects stay reachable on GitHub by SHA until GitHub support runs a server-side GC ([removing data](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)), so when the reported size drops is **unverified**.

## Recommendation

**Pragmas.** Run these once at creation, before any table exists: `PRAGMA auto_vacuum=INCREMENTAL; PRAGMA journal_mode=WAL;`. On every connection run:

```sql
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=5000;
PRAGMA foreign_keys=ON;
PRAGMA temp_store=MEMORY;
PRAGMA cache_size=-65536;
PRAGMA journal_size_limit=67108864;
PRAGMA optimize=0x10002;
```

Run `PRAGMA optimize` on close. After each ingest, run `PRAGMA wal_checkpoint(TRUNCATE)` when the dashboard is idle. Keep the default 4,096 B pages and STRICT tables everywhere. Store bodies as zlib BLOBs in a `body` table keyed by the SHA-256 of the normalized text. Index them with a contentless-delete FTS5 table (`detail=column`), plus a small title index over all postings.

**Retention policy.**

| Data | Rule |
|---|---|
| People, outreach messages, outbox, app_event and applications, research briefs, company signals, boards (deactivate, never delete), rollups, archive, schema_migration, run summaries | Never deleted |
| Postings the user opened, applied to or labelled | Row and body kept forever |
| In-lane postings | Row kept forever. Body and FTS entry deleted at closed_at + 90 days |
| Out-of-lane postings | Full row kept until closed_at + 90 days, then folded into `company_month` (company, month, role_family, level, opened, closed, open at month end, median days to close, reposts) and a 35 B `posting_archive` row (board, interned title, first_seen day, closed day, family, level), then deleted |
| posting_version | Written on content change only. Pruned along with its posting |
| Per-board fetch detail | 14 days. Per-board health state is one row per board, updated in place |

**Monthly sweep** (launchd, first Sunday, 03:00):

1. Take the run lock, check that no merge is running, run `PRAGMA quick_check`, then `VACUUM INTO` a dated local file and quick_check that copy too.
2. In one read transaction, count candidates per rule and write the counts to `sweep_run`. In `--dry-run` mode, stop here.
3. Abort if any rule would remove more than 40% of a table or more than 2x last month's count.
4. Freeze the `company_month` rows for months whose postings all closed more than 90 days ago. Insert archive rows, then check that archive counts per company-month equal the rollup totals.
5. Run each delete inside `BEGIN IMMEDIATE`, guarded by `NOT EXISTS` against app_event, outbox and label.
6. Finish with `PRAGMA incremental_vacuum`, `wal_checkpoint(TRUNCATE)` and `optimize`, then record the result and ping healthchecks.

**Backup.** Nightly at 02:00:

1. `VACUUM INTO`, then gzip.
2. Upload to Backblaze B2 (lower storage price; A, B and C calls free). R2 is an equal second choice.
3. Keep 7 daily and 12 monthly copies through lifecycle rules. At about 1 GB compressed in year 1 (**unverified** ratio) that is roughly 19 GB, or about $0.06/month on B2.
4. Once a month, restore the latest copy to a temp path and check it with `quick_check` and row counts.

Consider Litestream only if the dashboard moves to an always-on host.

**Monitoring.** Use five healthchecks.io checks, which fits the free tier:

| Check | Period | Grace |
|---|---|---|
| gh-crawl (pinged from the Action, `/start` and exit status) | 30 min | 90 min, given the measured 40 to 43 min crawls |
| laptop-merge | 1 day | 12 h |
| nightly-backup | 1 day | 6 h |
| monthly-sweep | 31 days | 2 days |
| data-freshness | 1 day | none |

The data-freshness check pings only when the newest ingested delta is under 2 hours old.

On GitHub, keep failed-run email on. Add a `job_run` table (job, started_at, finished_at, status, rows_in, rows_out, error) and show "last success" for each job on the dashboard, red past 2x its period. A disabled schedule then appears as a gh-crawl alert within 2 hours, whatever the 60-day rule turns out to count.
