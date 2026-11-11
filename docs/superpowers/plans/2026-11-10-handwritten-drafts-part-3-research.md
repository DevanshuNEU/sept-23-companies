# Handwritten drafts, part 3: research that is checked and kept

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every draft starts from true, checked facts: what the company is building (researched once per company,
kept 14 days), what the person wrote or shipped, the posting's own words, and his approved stories. Code opens each
page and keeps a fact only when its quote is really there.

**Architecture:** A new `outreach/research` package holds the store (a `research_fact` table), the page check (fetch
the URL, strip it to text, find the quote) and a GPT research command. The MCP gets one write tool, `save_research`,
that runs the same page check before it stores. `draft_brief` hands the drafter the company facts, the posting text
and his stories; the checks warn when a draft rests on the role alone.

**Tech Stack:** Python 3.14 standard library, SQLite, `unittest`. GPT through the Codex CLI (`codex --search exec`,
his ChatGPT login) for the research command only.

**Spec:** `docs/superpowers/specs/2026-11-09-handwritten-drafts-design.md` (Part 3, and "Devanshu's steps" in Part 4),
#9's rulings of 2026-11-09 and 2026-11-10 (projects first, company research first).

## Global Constraints

- A fact is kept only when code fetched its `https://` page and found its quote on the page (case, spacing and curly
  quotes ignored). Never LinkedIn (`linkedin.com`, `lnkd.in`), never a contact-data site (`rocketreach`, `zoominfo`,
  `apollo.io`, `lusha`, `signalhire`, `contactout`), never a page that needs a login.
- Company facts are fresh for 14 days (`FRESH_DAYS = 14`); the brief shows only fresh ones.
- The research command and anything that calls a model or the network for real runs only in the live checkout
  (`live_checkout.is_live()`); `--dry-run` runs anywhere and calls nothing.
- Tests never reach the network (`tests/__init__.py` already enforces loopback only): every fetch goes through an
  injected client.
- The stories file lives outside the public repo: `OUTREACH_STORIES`, else
  `~/Desktop/DevVault/job-search/outreach/stories/stories.md`. A missing file means no stories, never a refusal.
- The `brief_id` changes when the stories change (they are part of what the drafter is given).
- No em or en dashes in copy. No recipient names in committed files (the repo is public).

## Review Focus

- A page that answers 403, times out or is not HTML rejects that one fact with a reason and keeps the others. Test: `test_unreachable_page_rejects_only_that_fact`.
- A quote split across tags or lines on the page still matches. Test: `test_quote_across_tags_and_lines_matches`.
- A fact already stored for the company (same URL and quote) is not stored twice. Test: `test_same_fact_twice_is_stored_once`.
- A company merged into another stores and reads facts on the survivor. Test: `test_merged_company_uses_the_survivor`.
- GPT output that is not JSON, or has extra prose around the JSON, is handled: parse what is there, else record a failed run and store nothing. Test: `test_gpt_reply_with_prose_around_json_is_parsed`.

---

### Task 1: The research store

**Files:**
- Create: `outreach/store/migrations/0010_research_fact.sql`, `outreach/research/__init__.py`, `outreach/research/store.py`
- Test: `tests/test_research_store.py`

**Interfaces:**
- Produces: table `research_fact(fact_id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES company,
  fact TEXT NOT NULL CHECK (length(fact) BETWEEN 1 AND 300), quote TEXT NOT NULL CHECK (length(quote) BETWEEN 1 AND 500),
  url TEXT NOT NULL CHECK (url LIKE 'https://_%' AND instr(lower(url),'linkedin.com') = 0 AND instr(lower(url),'lnkd.in') = 0),
  page_date TEXT CHECK (page_date IS date(page_date)), found_by TEXT NOT NULL CHECK (found_by IN ('gpt','claude','hand')),
  verified_at TEXT NOT NULL (UTC Z), UNIQUE (company_id, url, quote)) STRICT`, index on `company_id`.
- `FRESH_DAYS = 14`; `Fact` (frozen dataclass: `fact, quote, url, page_date: str | None`);
  `store_company_facts(conn, company_id: int, facts: Sequence[Fact], *, found_by: str, now: str) -> int` (inserted
  count; resolves the company to its merge survivor; a repeat is ignored); `fresh_company_facts(conn, company_id: int,
  *, now: str) -> list[dict]` (`fact, quote, url, page_date, found_by, verified_at`, newest first, on the survivor).

- [ ] **Step 1: Write the failing tests**: `test_migration_creates_research_fact`, `test_store_and_read_fresh_facts`,
  `test_facts_older_than_14_days_are_not_fresh`, `test_same_fact_twice_is_stored_once`, `test_merged_company_uses_the_survivor`,
  `test_linkedin_url_is_refused_by_the_table`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_research_store -v`. Expected: FAIL (no module, no table).
- [ ] **Step 3: Implement.** Follow `0007_level_state.sql`'s header style. Use the existing merge resolution
  (`outreach.drafts.followups.company_map` and `resolve_company`).
- [ ] **Step 4: Run** it again, then `tests.test_store_migrate` (or whichever test pins the migration list). Expected: OK.
- [ ] **Step 5: Commit** `Research: a table for checked company facts (#205)`

### Task 2: The page check

**Files:**
- Create: `outreach/research/verify.py`
- Test: `tests/test_research_verify.py`

**Interfaces:**
- Consumes: `outreach.net.HttpClient` (protocol with `get(url, headers=None, *, follow_redirects=False, ...)` returning
  `Response` with status and body); `Fact` (Task 1).
- Produces: `page_text(html: str) -> str` (scripts, styles and tags removed, entities unescaped, spaces collapsed);
  `quote_on_page(text: str, quote: str) -> bool` (case, whitespace and curly quotes ignored); `refused_url(url: str)
  -> str | None` (the reason a URL may not be used, or None); `check_facts(client, facts: Sequence[Fact]) ->
  tuple[list[Fact], list[dict]]` (kept facts, and `{url, quote, reason}` for each rejected one; one fetch per URL,
  redirects followed, any status other than 200 or a fetch error rejects that URL's facts with the reason).

- [ ] **Step 1: Write the failing tests**: `test_page_text_strips_tags_scripts_and_entities`,
  `test_quote_across_tags_and_lines_matches`, `test_quote_not_on_page_is_rejected`,
  `test_unreachable_page_rejects_only_that_fact`, `test_refused_urls` (LinkedIn, lnkd.in, a contact-data site, http
  not https), `test_one_fetch_per_url` (a fake client counts calls).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_research_verify -v`. Expected: FAIL.
- [ ] **Step 3: Implement** with `html.parser` from the standard library.
- [ ] **Step 4: Run** it again. Expected: OK.
- [ ] **Step 5: Commit** `Research: a fact is kept only when its quote is on its page (#205)`

### Task 3: The save_research MCP tool

**Files:**
- Modify: `outreach/mcp/tools/research.py` (new `SaveResearchInput`, `save_research`, its `ToolSpec`), the tool lists
  pinned in `tests/test_mcp_server.py` and `tests/test_mcp_limits.py`
- Test: `tests/test_mcp_save_research.py`

**Interfaces:**
- Consumes: `check_facts` (Task 2), `store_company_facts` (Task 1).
- Produces: tool `save_research(company_id: int, person_id: int | None, facts: list[{fact, quote, url, page_date?}],
  max 8)`. With no `person_id` the kept facts go to `research_fact` (`found_by='claude'`); with a `person_id` they go
  to `person_source` (`kind='post'`, `label` = the fact, `observed_on` = `page_date`), and the person must belong to
  the company's family. Returns `{"saved": n, "rejected": [{url, quote, reason}]}`. The page fetch happens before the
  write transaction. Grants: `research_fact` insert, `person_source` insert, `event` insert. The client comes from a
  module-level factory the tests replace (the real one is `outreach.net.UrllibClient` with the default profile).

- [ ] **Step 1: Write the failing tests**: `test_save_research_keeps_checked_company_facts`,
  `test_save_research_stores_person_facts_as_sources`, `test_rejected_facts_are_reported_and_not_stored`,
  `test_person_from_another_company_is_refused`, `test_tool_is_registered_with_its_grants`, and add the tool to the
  pinned lists (27 tools).
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_mcp_save_research tests.test_mcp_server tests.test_mcp_limits -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** it again. Expected: OK.
- [ ] **Step 5: Commit** `MCP: save_research keeps only facts whose quote is on the page (#205)`

### Task 4: The brief carries the research, the posting and his stories; a thin draft warns

**Files:**
- Modify: `outreach/drafting/brief.py` (new `stories_path()`, `read_stories()`), `outreach/mcp/tools/drafts.py`
  (`draft_brief`, `require_brief`, the `ground` text of `save_draft`, `save_followup` and `check_draft`),
  `outreach/drafts/checks.py` (new warn `no_person_fact`), `tests/__init__.py` (scratch `OUTREACH_STORIES`)
- Test: `tests/test_drafting_brief.py`, `tests/test_mcp_draft_brief.py`, `tests/test_drafts_checks.py`

**Interfaces:**
- Consumes: `fresh_company_facts` (Task 1).
- Produces:
  - `read_stories(path=None) -> str` (the file's text; missing or unreadable gives "").
  - `brief["company_facts"]` (fresh facts of the person's company), `brief["research_needed"]` (true when there are
    none), `brief["posting"]` (`{"title", "url", "text"}` for `role_to_name`, `text` from `posting_body` cut to 4,000
    characters, or None), `brief["stories"]` (the stories text).
  - `brief_id` is computed over `evidence + "\n" + stories`; `require_brief` checks against the same.
  - The numbers check's `ground` also holds the stories and the company facts' quotes.
  - Warn `no_person_fact` (after `same_opening` in `WARN_CODES`): a first email to an engineer, manager or founder
    whose person has no profile text and no `person_source` row. Copy: "This email rests on the role alone. Look for
    something they wrote or shipped, or say plainly why you picked them."

- [ ] **Step 1: Write the failing tests**: `test_read_stories_missing_file_is_empty`, `test_brief_has_company_facts_and_research_needed`,
  `test_brief_has_the_posting_text`, `test_brief_has_stories_and_its_id_changes_with_them`,
  `test_numbers_from_stories_are_grounded`, `test_no_person_fact_warns_for_engineer_without_facts`,
  `test_no_person_fact_passes_with_a_source_or_profile`, `test_no_person_fact_skips_recruiters_and_followups`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_drafting_brief tests.test_mcp_draft_brief tests.test_drafts_checks -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** it again, then `tests.test_mcp_drafts tests.test_api_approve tests.test_sending_precheck tests.test_followup_drafter`. Expected: OK.
- [ ] **Step 5: Commit** `Brief: company facts, the posting and his stories; warn when a draft rests on the role alone (#205)`

### Task 5: The GPT research command

**Files:**
- Create: `outreach/research/gpt.py`, `outreach/research/__main__.py`, `outreach/research/prompt.md`
- Test: `tests/test_research_gpt.py`

**Interfaces:**
- Consumes: `check_facts` (Task 2), `store_company_facts` and `fresh_company_facts` (Task 1).
- Produces:
  - `build_prompt(company: dict, postings: list[str]) -> str` from `prompt.md` (company name, domain, open posting
    titles; asks for 2 to 4 facts from the last 6 months about what the company is building and what the team owning
    the role works on, each `{fact, quote, url, date}`, JSON only, no LinkedIn or contact-data sites).
  - `parse_reply(text: str) -> list[Fact]` (the first JSON object in the text; `{"facts": [...]}`; bad items skipped).
  - `Runner` protocol `run(prompt: str) -> str`; the real one runs `codex --search exec --skip-git-repo-check
    --ephemeral -s read-only -C <scratch dir> -o <file> -` with the prompt on stdin and returns the file's text.
  - `research_company(conn, company_id, *, runner, client, now) -> dict` (`{"status": "fresh"|"stored"|"nothing"|"failed",
    "kept": n, "rejected": [...]}`; a company with fresh facts is skipped unless `force`).
  - CLI: `python -m outreach.research company <id> [<id>...] [--force] [--dry-run]`, one summary line per company;
    real runs only in the live checkout, `--dry-run` prints the prompt and calls nothing.

- [ ] **Step 1: Write the failing tests**: `test_prompt_names_the_company_and_its_postings`,
  `test_gpt_reply_with_prose_around_json_is_parsed`, `test_bad_reply_stores_nothing_and_reports_failed`,
  `test_research_company_checks_then_stores`, `test_fresh_company_is_skipped_unless_forced`,
  `test_real_run_outside_the_live_checkout_is_refused`, `test_dry_run_calls_nothing`.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_research_gpt -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Follow `outreach/followup_drafter/__main__.py` for the live checkout guard and the summary line.
- [ ] **Step 4: Run** it again. Expected: OK.
- [ ] **Step 5: Commit** `Research: a GPT command that researches a company once and keeps only checked facts (#205)`

### Task 6: The skill and the docs

**Files:**
- Modify: `.claude/skills/cold-email/SKILL.md`, `docs/superpowers/specs/2026-11-09-handwritten-drafts-design.md`
  (Part 3 as built), `CLAUDE.md` (the research command in the live-checkout-only list)
- Test: none (docs); the full suite must stay green.

- [ ] **Step 1: Edit the skill**: after `draft_brief`, when `research_needed` is true, run
  `uv run python -m outreach.research company <company_id>` in the live checkout (or research the web yourself and
  call `save_research`), then call `draft_brief` again. Research the person the same way and save what you find with
  `save_research` and the `person_id`. Write from `company_facts` (stated plainly in the source's own words), the
  `posting` text, his projects first and a story from `stories` that fits the reader.
- [ ] **Step 2: Edit the design's Part 3** to say what was built (the table, the page check, the tool, the command,
  the stories file in DevVault). Add the command to CLAUDE.md's live-checkout-only list.
- [ ] **Step 3: Run** `uv run python -m unittest discover -s tests -t .`. Expected: OK.
- [ ] **Step 4: Commit** `Skill and docs: research first, from checked facts (#205)`

## Deploy (after merge)

Pull in the live checkout, `uv sync`, run the tests, `uv run python -m outreach.install` (the new migration), kickstart
the API. Write `~/Desktop/DevVault/job-search/outreach/stories/stories.md` from the approved stories (outside the repo).
The next Claude Code session loads `save_research`.
