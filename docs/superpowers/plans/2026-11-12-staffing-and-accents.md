# Staffing Firms and Accented Places: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** known staffing firms leave the Jobs list, uncertain ones carry a tag, and accented non-US places ("México") read as outside the US.

**Architecture:** a posting fact in the rules (`for_client`, from the body's opening, stored on the decision by migration 0015) plus a company rule at list time (`staffing_ids`, from a new `[staffing]` section of `outreach/pipeline/config.toml`). Hidden companies join `LISTED_SQL` through a new `:staffing` parameter, so the list, its counts and Check now's new count agree. The company list is outside the ruleset, so adding a firm needs no rebuild.

**Spec:** #9 job board ruling (2026-11-12, evening) point 3; issue #257.

## Global Constraints

- A known staffing firm is hidden from every tab and count; an uncertain one is flagged, never hidden (#9).
- "Our clients" (plural, consultancies and banks) is not client wording; only the singular forms in the first 400 characters count.
- Tags read "Maybe a staffing firm" and "Posted for a client". No em or en dashes in copy.
- `decide()` stays pure. Tests use scratch settings only.

## Review Focus

- A company merged into a staffing firm: hidden with it (as `listed_params` does for no-go).
- A promoted tier 3 company: no tag.
- A row from before 0015 (`for_client` NULL): not a client posting.
- A company with 2 client postings out of 2: flagged per posting, not hidden (`client_min = 3`).
- Accent folding never turns a US place into a non-US one ("San José, CA" stays yes).

---

### Task 1: Accented places

**Files:** `outreach/rules/location.py` (fold accents in `us_status` before matching), `tests/test_rules_location.py`.

- [ ] Test `test_accented_places_fold`: "México", "Montréal", "Zürich", "Kraków" read no; "San José, CA" reads yes. Watch it fail, fold with `unicodedata.normalize("NFKD")` minus combining marks on location, country and title, watch it pass, commit.

### Task 2: `for_client` on every decision

**Files:** `outreach/rules/config.toml` (`[staffing] client_phrases`, `client_window_chars = 400`), `outreach/rules/config.py` (REQUIRED), new `outreach/rules/staffing.py` with `for_client(body: str, cfg: Config) -> bool` (added to `RULE_FILES`), `outreach/rules/decide.py` (`Decision.for_client: bool = False`), `outreach/store/migrations/0015_decision_for_client.sql` (column `for_client INTEGER CHECK (for_client IN (0,1))`, both views recreated with it), `outreach/rules/rebuild.py` and `outreach/ranking/roles.py` column lists (NULL reads False).

- [ ] Tests: "Our client is a 15-person consumer AI company..." is true; "We help our clients..." is false; the phrase after 400 characters is false; stored on the decision; NULL reads False. Watch fail, implement, full suite, commit.

### Task 3: Staffing at list time

**Files:** `outreach/pipeline/config.toml` and `config.py` (`[staffing] known`, `name_words`, `client_min = 3`, `client_share = 0.5`), `outreach/pipeline/joblist.py` (`staffing_ids(conn, canon, cfg) -> frozenset[int]`; `LISTED_SQL` gains `AND p.company_id NOT IN (SELECT value FROM json_each(:staffing))`; `listed_params` fills it; `JobRow.maybe_staffing` from `work_tier` tier 3 not promoted; `JobRow.for_client`), `outreach/api/models/jobs.py` (`maybe_staffing`, `for_client`), OpenAPI snapshot.

- [ ] Tests: known name hidden on all three tabs and counts; a name with "Staffing" hidden; 3 of 4 client postings hidden; 2 of 2 not hidden but each row `for_client`; a bank with "our clients" untouched; merged-away company of a hidden one hidden; tier 3 tagged, promoted tier 3 not; Check now's new count skips a hidden firm. Watch fail, implement, full suite, commit.

### Task 4: Tags on the row

**Files:** `web/src/api/types.ts`, fixture rows, `web/src/routes/browse/index.tsx` (two tags beside "Says no sponsorship", same quiet style as "Reposted"), browse test.

- [ ] Test the two tags render from the fixture. Watch fail, implement, web checks, commit.

### Task 5: Ship

- [ ] Full Python suite and web checks green, PR against master closing #257, deploy note: pull, sync, `outreach.install` (0015 and one rebuild), web build, API kickstart, tests.
