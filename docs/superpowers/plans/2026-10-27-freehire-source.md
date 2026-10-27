# freehire.me as a lead to US early-career boards (#115, Part of #47)

Goal: more companies with a fresh junior or intern engineering role on a board Knock already crawls. freehire is a
lead to a board and nothing more: every fact Knock acts on still comes from its own crawl and rules.

Facts checked on 2026-10-27: the terms allow the API without a plan for "your own job search, research, or a tool you
run for yourself"; robots.txt allows the API for `*`; the lean search caps a page at 100 and ignores `is_tech`;
`external_id` is `<board token>:<job id>` for every simple-token board (24 of 24 Greenhouse rows agreed with the URL),
not for Workday or Oracle; a posting URL on the company's own site (`stripe.com/jobs/search?gh_jid=`) names no board
but gives the company's domain.

## Task 1: `outreach/sources/freehire.toml` and `freehire.py`

- Config: `enabled`, the lean search URL, `page_size` 100, `max_pages` 20, `days` 3, countries `us`, seniority
  `junior` and `intern`, the engineering categories, the freehire platform name to `board.ats` map (only platforms
  with a crawler adapter; never wellfound or ycombinator, #46), and the staffing company types. The version is
  `freehire-1-<sha of the toml>`.
- The request asks only for platforms whose reader is on (`CrawlConfig.reads`), so SmartRecruiters and Pinpoint are
  never asked for while #46 has them off.
- A pass reads every page first (a short page, `total`, or `max_pages` ends it; hitting the cap is counted), then
  yields. An HTTP error, a non-JSON body or a page without a `data` list raises `FreehireError`: nothing yielded.
- Per posting: the board from `detect_all(url)` when it names exactly one readable board; else, for a simple-token
  platform, the prefix of `external_id` through `normalize_token`. The domain is the URL's registrable domain when
  `classify_domain` says `company`. Skipped and counted: an unmapped or unread platform, no board, a name with a
  slash or empty, a repeat of a board already yielded this pass.
- Candidate: the name, the domain, `keys={"ats": "<ats>:<token>"}`, no evidence, `source_ref`
  `freehire:<ats>:<token>`. `work_tier`: 3 for the staffing company types (reason names freehire's label), else None.
- Tests (`tests/test_freehire.py`, fake client only): the pinned first request URL, every skip reason, the
  external_id fallback with domain, paging and the cap, each error, the map holds only crawler adapters, and a
  `run_discover` on a fresh store (a new company gets the `ats` alias, a known board attaches, tier 3 is set,
  find-boards makes the board).

## Task 2: the CLI and run-due

- `python -m outreach.sources discover freehire [--days N]`; a `freehire` step in `run-due` after `hn` (skipped
  when `enabled = false`).
- Tests (`tests/test_sources_cli.py`): the command, the step order, a failed freehire step leaves every other step
  running. The fake routes answer freehire with an empty page, so the other sources' counts do not move.

Deploy: Python changed in the sources job; nothing to kickstart (it starts fresh each run). The first run reads the
last 3 days; a backfill is `discover freehire --days 30` by hand in the live checkout.
