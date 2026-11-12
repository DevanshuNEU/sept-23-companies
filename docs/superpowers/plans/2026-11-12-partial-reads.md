# Partial reads for Workday and Oracle (#232) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A big Workday or Oracle board whose list has a few broken, repeated or missing rows still adds its new
postings, and closes nothing.

**Architecture:** `ReadResult` gains `partial` and `partial_note` (an ok read only). `finish_list` makes a partial ok,
never `unchanged`. Ingest closes nothing on a partial read; board state keeps no validators from one. Workday skips
broken and repeated rows; Workday and Oracle accept a shortfall of at most 2% (at least 3) as partial.

## Global Constraints
- Network or HTTP trouble, a wrong shape, a list far short and a family over the 2,000 cap stay `error`.
- A partial read never closes a posting and is no baseline.

## Review Focus
1. A partial read must never close anything, even a posting it did not list (test in ingest).
2. The next whole read after a partial one still closes what is gone (test in ingest).
3. A page that repeats forever must still end the read (Workday and Oracle tests).

### Task 1: `crawl/base.py`, `crawl/ats/_common.py`, `crawl/ingest.py`, `crawl/boardstate.py`; tests in `test_crawl_ingest.py`
### Task 2: `crawl/ats/workday.py`, `crawl/ats/oracle_orc.py`; tests in their adapter tests
### Task 3: `crawl/tick.py`, `crawl/__main__.py`: count partial reads
