# Speed Tiers, Part 2: Newest-First Head Reads

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans.

**Goal:** a hot Workday or Oracle board (a job at his level in the last 90 days) is checked every 15 minutes with one request for its newest postings, and read in full on its usual rhythm (#251, #9 job board ruling point 6).

**Architecture:** a head read is an adapter's optional `head(token)`: page 1 of Workday's list, Oracle's list sorted by posting date. It answers a partial `ok` (`ReadResult.head`), so `read_board` stores what is new and closes nothing, keeps the stored interval, and never moves `board.last_full_read_at` (migration 0016). `head_read_due` picks a head read when the board's ATS is in `[tiers] head_ats` and its last full read is younger than its normal interval. `tier_wait_s` gives hot head-ATS boards the 15 minute wait.

## Global Constraints

- One request per head read; host spacing and the breaker are unchanged.
- A head read never closes a posting and never counts as an empty read.
- Workday page 1 is newest first on most tenants, not all (Adobe, 2026-11-09 research): a tenant out of order only finds new jobs at its full read, as today.

### Task 1: store and plumbing (migration 0016, BoardRow, write_board_state, ReadResult.head, config head_ats, tier_wait_s, head_read_due, list_board, fanout, tick, ingest). Tests in tests/test_crawl_ingest.py and tests/test_crawl_cadence.py.
### Task 2: Workday `head` (page 1, limit 20, no facets) and Oracle `head` (limit 25, sortBy=POSTING_DATES_DESC). Tests next to each adapter's.
### Task 3: ship (PR, CI, deploy: pull, sync, install for 0016, kickstart API, tests).
