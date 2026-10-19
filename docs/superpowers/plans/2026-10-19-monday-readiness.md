# Monday readiness: me.md, cold-email skill, draft checker (issue #10, non-blocked half) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Monday's drafts follow #9: a cold-email skill that states every Drafts rule, a `mail/me.md` built only from sourced facts, and a checker the skill runs on every draft before it is queued.

**Architecture:** `mail/check_draft.py` is a standalone, standard-library script. It reads the same contact JSON that `mail/add.py` accepts, applies the #9 Drafts checks, prints blocking problems and style warnings, and exits 1 on any blocking problem. Its banned phrases come from the DevVault `banned_phrases.txt` (path argument, fixture file in tests); its unconfirmed facts come from the `ASK:` lines in `mail/me.md`; its "subject unique" and "near-identical to a colleague" checks read the contact list read-only through `mail/store.py`. The skill calls it between writing a draft and `add.py`.

**Tech Stack:** Python 3.14 standard library (`/opt/homebrew/bin/python3`), `unittest`.

**Spec:** GitHub issue #9 (Drafts, Sending: Content and Follow-ups), issue #10 scope items 1 and 2, issue #8, `docs/superpowers/specs/2026-10-18-outreach-platform-design.md` section 9.

## Out of scope

- First 5 companies (#10 scope item 3): another teammate.
- Drafting any real email or queueing anything: blocked on Devanshu's review of me.md and on #8 people.
- Sending code (`mail/sender.py`, `mail/followup.py`, `mail/add.py`): stays as it is until the switch (#9).

## Global Constraints

- Standard library only in `mail/`. Tests: `/opt/homebrew/bin/python3 -m unittest discover -s tests -t .` from the repo root.
- Word range: engineer, manager, founder 80 to 120 words; recruiter about 90 (checker range 80 to 100). Follow-ups: short (checker warns over 80).
- First email: exactly 2 links, the first one `https://www.devanshuchicholikar.com`. Any email: at most 2 links, no shortener.
- Subject: unique per person; no `Re:` on a first email.
- No em or en dashes anywhere a person reads.
- Facts only from `mail/me.md` and what Devanshu pastes about the person. `ASK:` lines in me.md are not facts.
- No private DevVault message text in the repo (the repo is public). Voice traits only.
- Leave `dashboard/data.js` and `dashboard/index.html` untouched.

## The checks

| Check | Source | Result |
|---|---|---|
| Phrase from `banned_phrases.txt` (scopes `both` and `email`), in subject or body | #9 Drafts, lint check 6 | block |
| "bumping this", "bump this", "got buried", "gentle reminder", "in case you missed" | #9 Follow-ups | block |
| Em dash or en dash (U+2012 to U+2015) | #9 Drafts, lint check 2 | block |
| First email: link count is not exactly 2, or the first link is not the portfolio | #9 Content | block |
| Any email: more than 2 links | #9 Approve checks | block |
| A link shortener (bit.ly, t.co, lnkd.in, ...) | #9 Approve checks | block |
| First email: subject starts with `Re:`; empty subject or body | #9 Drafts, lint check 11 | block |
| Subject already used by another contact | #9 Content | block |
| Body near-identical (difflib ratio 0.8 or more) to a colleague's draft at the same company | #9 Approve checks | block |
| `TODO` or `ASK:` in the draft, or a backtick-quoted value from an `ASK:` line of me.md | #10 scope 1 | block |
| `kind` not one of engineer, manager, founder, recruiter (alum accepted as engineer) | #9 readers | block |
| Word count outside the reader's range | #9 Drafts, lint check 4 | warn |
| Recruiter email with no work-authorization words (OPT, F-1, visa, sponsor) | #10 recruiter rule | warn |
| First line does not greet the person by first name | lint check 7 | warn |
| Subject has "quick question", "following up" or "excited" | lint check 11 | warn |

Left out, and why: mail-server (MX) lookup needs the network; hook age needs a date the JSON does not carry (the skill states the 30-day rule instead); flattery beyond the banned list needs judgment (skill); attachments cannot exist, because `add.py` only stores text.

## Review Focus

1. A bare domain such as `opencodeintel.com` or `Claude.ai`, which Gmail turns into a link: counted as a link. Pinned in Task 1.
2. The portfolio written as `https://devanshuchicholikar.com/` (no www, trailing slash): accepted as the portfolio. Pinned in Task 1.
3. Devanshu's own email address in the body (`chicholikar.d@northeastern.edu`): not counted as a link. Pinned in Task 1.
4. A banned-phrase file that is missing: exit 2 with the path, never a silent pass. Pinned in Task 1.
5. A follow-up (`--followup`): `Re:` subject allowed, 0 or 1 link allowed, subject uniqueness not checked. Pinned in Task 1.

---

### Task 1: draft checker

**Files:**
- Create: `mail/check_draft.py`
- Create: `tests/test_check_draft.py`, `tests/fixtures/banned_phrases.txt`, `tests/fixtures/me.md`

**Interfaces:**
- Produces:
  - `load_banned(path: str) -> list[tuple[str, re.Pattern]]`: (source pattern, compiled), scopes `both` and `email` only, `[[:space:]]` translated to `\s`, case-insensitive. Raises `FileNotFoundError` if missing, `ValueError` if no pattern.
  - `load_unconfirmed(me_path: str) -> list[str]`: every backtick-quoted span on a line that starts with `- ASK:`.
  - `links(text: str) -> list[str]`: links in reading order, full URLs and bare domains, emails excluded.
  - `check(contact: dict, banned: list, unconfirmed: list[str], others: list[dict], followup: bool = False) -> tuple[list[str], list[str]]`: (blocks, warns). Message is `contact["draft"]` if present, else `{"subject", "body"}` at the top level.
  - CLI: `python3 mail/check_draft.py [--banned PATH] [--me PATH] [--followup] < contact.json`. Prints `BLOCK: ...` and `WARN: ...` lines, then a summary line with the word count. Exit 0 clean or warnings only, 1 on any block, 2 on bad input or a missing list.

- [ ] **Step 1: Write failing tests** in `tests/test_check_draft.py`: a clean engineer draft (98 words, portfolio then opencodeintel.com) has no blocks and no warns; one test per row of "The checks" table; the five Review Focus inputs; CLI exit codes 0, 1, 2 through `subprocess` with the fixtures.
- [ ] **Step 2: Run** `/opt/homebrew/bin/python3 -m unittest tests.test_check_draft -v`. Expected: errors, `No module named 'check_draft'`.
- [ ] **Step 3: Implement** `mail/check_draft.py` to the interfaces above. `others` in the CLI is `store.load()["contacts"]` (read-only).
- [ ] **Step 4: Run** the module, then the full suite. Expected: all pass.
- [ ] **Step 5: Commit** `Draft checker for cold emails (#10)`.

### Task 2: me.md from public sources

**Files:**
- Modify: `mail/me.md`

- [ ] **Step 1:** Collect facts from `gh api users/DevanshuNEU`, the READMEs of OpenCodeIntel, retrieval-research, Overhear, CallBudget, Saar (lco), saar CLI, tool-crowding, and https://www.devanshuchicholikar.com (home, /resume, /projects/*).
- [ ] **Step 2:** Rewrite me.md: Who, Experience, Projects (each with the link to use as the second link and who it fits), Links, Signature, each fact tagged with its source; a "Needs Devanshu" section where every unknown is a `- ASK:` line, with any value the drafter must not use quoted in backticks.
- [ ] **Step 3:** Verify: every number in me.md appears in a cited source (re-grep the fetched text); `load_unconfirmed("mail/me.md")` returns the guarded values.
- [ ] **Step 4: Commit** `me.md from public sources, open questions as ASK lines (#10)`.

### Task 3: skill rewrite

**Files:**
- Modify: `.claude/skills/cold-email/SKILL.md`, `CLAUDE.md` (Writing section), `docs/README.md` (plans table)

- [ ] **Step 1:** Rewrite the skill (writing-for-agents): steps with completion criteria (inputs, role, hook, evidence, draft, check, queue, report), then reference (voice, readers, links, subject, follow-ups, truth). States the JSON shape `add.py` takes, that queueing happens only from `~/Desktop/Sept 23 Companies`, and that Claude never approves or sends.
- [ ] **Step 2:** Remove the stale-skill banner; replace the "until #10 rewrites it" sentence in `CLAUDE.md`; add this plan to `docs/README.md`.
- [ ] **Step 3:** Verify: walk #9 Drafts and #10 scope item 2 line by line and point to the skill line that carries each; a sample draft written by the skill's rules passes `check_draft.py` with the fixture list.
- [ ] **Step 4: Commit** `Cold-email skill rewritten to #9 (#10)`.

### Task 4: verify

- [ ] Full suite green; `grep -rn $'—\|–'` over changed files finds nothing; `git status` shows `dashboard/` untouched.
