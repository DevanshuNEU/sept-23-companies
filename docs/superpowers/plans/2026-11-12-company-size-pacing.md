# Company size pacing (#229) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A company's first emails a day and the gap between two emails to it follow its size, so a big company can
take 15 a day and a small one 2.

**Architecture:** Five size bands in the sender's `config.toml`, one key per column. `outreach/sending/pace.py` reads
each queued company's size (its fact, across merged duplicates) or the job list's big guess, and returns its band.
The queue's per-company limit and gap, and the Outbox's slots left, read the band. Phase 2 of the sending plan.

**Spec:** #9 "Company size pacing ruling (2026-11-12)" and #229.

## Global Constraints

- Bands: up to 100 (2 a day, 60 + 0..60), 101 to 200 (4, 60 + 0..30), 201 to 500 (5, 45 + 0..30), 501 to 1,000
  (8, 30 + 0..20), over 1,000 (15, 20 + 0..15). Guessed big (enterprise board or over 50 open US postings): band 4.
  No size, no sign: the default band, `per_company_daily` 2 with the existing gap keys.
- Follow-ups never count toward the daily number and always keep the gap (#9, 2026-10-31).
- The guess reuses `joblist.BIG_BY_POSTINGS` and `joblist.ENTERPRISE_ATS`, so the list and the sender agree.
- The tests' scratch config keeps 3 a day for the default band, so the existing exact-time tests stand.

## Review Focus

1. A size on a merged duplicate counts for the survivor (test in Task 2).
2. A size on file wins over the big guess, both ways (test in Task 1).
3. `max_company_gap` covers the longest band, so the queue still looks back far enough (test in Task 3).
4. Thirty colleagues' drafts must not trip near-identical in the simulation, or the day test proves nothing.

### Task 1: Config
- `config.toml`: `size_band_up_to`, `size_band_daily`, `size_band_gap_minutes`, `size_band_gap_jitter_minutes`,
  `size_guessed_big_band`; `per_company_daily` 2. `config.py`: fields and `_check_bands`.
- Tests `tests/test_sending_pace.py` (shipped bands, refusals), `tests/test_sending_config.py` (field count 39).

### Task 2: `outreach/sending/pace.py`
- `CompanyPace(daily, gap_minutes, gap_jitter_minutes)`, `default_pace(cfg)`, `pace_for(size, guessed_big, cfg)`,
  `company_paces(conn, company_ids, cfg) -> dict[int, CompanyPace]`.
- Tests: size picks its band at each bound; merged size; Workday board; inactive board; 51 vs 50 US postings.

### Task 3: Queue, gap and Outbox
- `human.company_gap(company_id, last, cfg, pace=None)`; `human.max_company_gap(cfg)` over every band.
- `queue.Prepared.paces`; `_decide` reads `company_pace.daily` and the band's gap.
- `api/queries/outbox.py`: `company_slots_left` from the band.
- Tests: small stops at 2 while big goes on; big stops at 15; a 25 minute gap holds a small company, not a big one.

### Task 4: A big company's day
- `simulate.Scenario.sizes`: company index to a size fact.
- Test: one 20,000-person company with 30 approved first emails gets 15 in a day, at least 20 minutes apart.

Deploy: no schema change. Pull, `uv sync`, tests, then kickstart the API; the sender reads the config on its next tick.
