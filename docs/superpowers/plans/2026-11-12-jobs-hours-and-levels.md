# Jobs: hour windows and level chips (#213) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** On Jobs, "1 hour", "6 hours" and "24 hours" windows, and level chips (New grad, Early career, SDE 1, SDE 2,
Entry level, Level not stated) that combine.

**Architecture:** `JobFilters` gains `window_hours` and `levels`; the list SQL keeps a row when its list time is inside
the hour window (Date unknown rows never are) and its level key (the same split as its tag) is chosen. The API takes
`window` 1h, 6h, 24h and `levels` as a comma list. The page adds the three windows and a Level chip row.

**Spec:** #213 and Devanshu, 2026-11-12: "add a filter where i can search by early career, SDE 1 and SDE 2".

## Global Constraints
- A level chip keeps exactly the rows whose tag it names: uncertain is Level not stated whatever the tier; a fit with
  no level tier is Entry level.
- Counts follow the filters (both tabs). Several chips at once; none is any level.
- Hour windows read the list time; Date unknown rows are in none of them.

### Task 1: Query (`outreach/pipeline/joblist.py`), tests in `tests/test_pipeline_joblist.py`
### Task 2: API (`outreach/api/routers/jobs.py`, `models/jobs.py`), tests in `tests/test_api_jobs.py`
### Task 3: Page (`web/src/routes/browse/index.tsx`, types, client, fixture), tests in `browse.test.tsx`
