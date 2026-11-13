# Speed Tiers, Part 1: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** a board where jobs at his level appear is read every 15 minutes, other boards every 2 hours, dead boards once a day (#9 job board ruling point 6, #251). Part 1 covers the single-request ATSs; Workday, Oracle, Eightfold and Amazon keep today's schedule until their newest-first reads land (part 2).

**Architecture:** `cadence.tier_wait_s(conn, board, now, cfg) -> int | None` picks the wait from a new `[tiers]` section of `outreach/crawl/config.toml`; `ingest` uses it for the next read only, so the stored `change_interval_s` stays the normal one (the low-yield rule's pattern). The crawl job starts every 5 minutes instead of 30; `tick_lock` already keeps two ticks from overlapping, and a tick with nothing due ends at once.

## Global Constraints

- Hot: an open or closed posting first seen in the last 90 days whose current decision is engineering, a software role, not outside the US, and at his level (`STATE_SQL` fit or uncertain). Dead: nothing new for 90 days. Warm: the rest.
- Waits: hot 900 s, warm 7,200 s, dead 86,400 s. Error backoff and the low-yield day are unchanged. Host spacing and the breaker are unchanged, so no host gets more requests a second.
- Tier ATSs: greenhouse, ashby, lever, workable, rippling, breezy, recruitee, jazzhr, bamboohr, teamtailor, gem, join_com, personio, pinpoint, smartrecruiters (switched-off readers stay off).

## Review Focus

- A board that errors keeps its backoff, never the 15 minute wait.
- A big low-yield board on a tier ATS still waits its day.
- A posting outside the US, an Other role, or a senior one never makes a board hot.
- The tier query is per board read: it must use `posting_board_open` or the primary key, never scan every posting.

### Task 1: `tier_wait_s` and `[tiers]` (config, cadence, tests in tests/test_crawl_cadence.py)
### Task 2: ingest uses it for the next read (tests in tests/test_crawl_ingest.py, the next_read_at pins updated)
### Task 3: launchd StartInterval 300 (template and its test), deploy note: `outreach.deploy plan` then `install`.
