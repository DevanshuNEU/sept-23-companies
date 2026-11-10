# Handwritten drafts, part 1: the checks and the signature

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every draft and follow-up is checked for the rules Devanshu set on 2026-11-09 (no markdown, no visa mention, at
most 120 words, a subject a phone shows whole, no lines about the email itself, his signature with the portfolio), and
the brief hands the drafter his signature.

**Architecture:** All rules live in the one checks module (`outreach/drafts/checks.py`) and the pure voice checks
(`outreach/drafting/voice_checks.py`), which `save_draft`, `save_followup`, the approve route and the sender's
pre-claim check already call. The signature is read from a local file outside the public repo, because it holds his
phone number.

**Tech Stack:** Python 3.14 standard library, `unittest`.

**Spec:** `docs/superpowers/specs/2026-11-09-handwritten-drafts-design.md` (Part 1 and the sign-off), #9 (drafting
rulings of 2026-11-09).

## Global Constraints

- No visa, OPT, STEM OPT, F-1, sponsorship, H-1B or work authorization in any email, to anyone, recruiters included.
- First email: at most 120 words, no floor. Follow-up: at most 80 words (unchanged).
- First email: exactly 2 links. The portfolio `https://www.devanshuchicholikar.com` sits in the signature; the other
  link is in the body.
- Signature, every email and follow-up: an optional sign-off word line, then the signature file's lines (name,
  portfolio, phone).
- The signature file is `~/Library/Application Support/outreach/signature.txt`, or `OUTREACH_SIGNATURE`. Never commit
  the phone number: this repo is public.
- No em or en dashes in any text a person reads, check copy included.
- Tests never read the real signature file (`tests/__init__.py` points `OUTREACH_SIGNATURE` at a scratch path).

## Review Focus

- A visa word inside an ordinary word ("optimize", "opt in", "adopted") must not block. Test: `test_visa_line_ignores_opt_inside_words`.
- A first email whose only links are both in the body (old style, portfolio first) must warn, not pass. Test: `test_link_count_warns_when_portfolio_is_not_in_signature`.
- A follow-up with the signature and no body link must pass the link rules. Test: `test_followup_with_signature_only_passes_links`.
- A missing or empty signature file falls back to name plus portfolio and never raises. Test: `test_read_signature_falls_back_when_missing`.
- The plain follow-up nudge ("just bumping this in case it got buried") no longer warns. Test: `test_followup_nudge_is_not_banned`.

---

### Task 1: The signature file and the brief

**Files:**
- Create: `outreach/drafting/signature.py`
- Modify: `outreach/mcp/tools/drafts.py` (`draft_brief`), `tests/__init__.py`
- Test: `tests/test_drafting_signature.py`, `tests/test_mcp_draft_brief.py`

**Interfaces:**
- Produces: `signature_path() -> Path` (env `OUTREACH_SIGNATURE`, else `~/Library/Application Support/outreach/signature.txt`);
  `read_signature(path: Path | None = None) -> str` (the file's non-empty lines joined by `\n`; missing, unreadable or
  empty gives `FALLBACK = "Devanshu Chicholikar\nhttps://www.devanshuchicholikar.com"`); `signature_lines() -> list[str]`.
- `draft_brief` returns a new key `"signature": read_signature()`.

- [ ] **Step 1: Write the failing tests**

```python
def test_read_signature_reads_the_file(self):        # tmp file "Devanshu Chicholikar\nhttps://www.devanshuchicholikar.com\n(555) 010-0199\n"
    self.assertEqual(read_signature(p), "Devanshu Chicholikar\nhttps://www.devanshuchicholikar.com\n(555) 010-0199")
def test_read_signature_falls_back_when_missing(self):   # missing path and an empty file
    self.assertEqual(read_signature(missing), FALLBACK); self.assertEqual(read_signature(empty), FALLBACK)
def test_signature_path_honours_env(self): ...
# tests/test_mcp_draft_brief.py
def test_brief_carries_the_signature(self):           # OUTREACH_SIGNATURE -> tmp file
    self.assertEqual(brief["signature"], "Devanshu Chicholikar\nhttps://www.devanshuchicholikar.com\n(555) 010-0199")
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run python -m unittest tests.test_drafting_signature tests.test_mcp_draft_brief -v`
Expected: FAIL (`No module named outreach.drafting.signature`, `KeyError: 'signature'`).

- [ ] **Step 3: Implement `signature.py`, add the key to `draft_brief`, and set `OUTREACH_SIGNATURE` to a scratch missing path in `tests/__init__.py`.**

- [ ] **Step 4: Run to see them pass** (same command). Expected: OK.

- [ ] **Step 5: Commit** `git commit -m "Drafting: his signature comes from a local file, and the brief carries it"`

### Task 2: Signature and link rules

**Files:**
- Modify: `outreach/drafts/checks.py` (`_link_rules`, new `_signature`, `WARN_CODES`, `run_checks`)
- Test: `tests/test_drafts_checks.py`

**Interfaces:**
- Consumes: `signature_lines()` from Task 1.
- Produces: warn code `signature` (title "Signature missing"; pass title "Signature is there"), added to `WARN_CODES`
  after `link_count`. `link_count` keeps its code; its pass is now "2 links and the last one is the portfolio, in the
  signature".

- [ ] **Step 1: Write the failing tests**

```python
def test_signature_passes_when_body_ends_with_it(self):          # "...\n\nThanks,\n" + signature
def test_signature_passes_with_no_signoff_word(self):            # body ends with the signature lines only
def test_signature_warns_when_missing(self):                     # body ends "Thanks,\nDevanshu Chicholikar"
    self.assertEqual(sev(found, "signature"), "warn")
def test_link_count_passes_with_project_in_body_and_portfolio_in_signature(self):
def test_link_count_warns_when_portfolio_is_not_in_signature(self):   # portfolio and project both above the signature
def test_followup_with_signature_only_passes_links(self):        # seq=1, only the signature's link
```

- [ ] **Step 2: Run to see them fail.** `uv run python -m unittest tests.test_drafts_checks -v` Expected: FAIL.

- [ ] **Step 3: Implement.** `_signature(d)`: strip trailing blank lines; the last `len(signature_lines())` non-empty
  lines must equal `signature_lines()` (compared with surrounding whitespace stripped). `_link_rules`: for `seq == 0`,
  pass when there are exactly 2 links and the last link is the portfolio and sits inside the signature block; warn
  "Not 2 links" or "Portfolio belongs in the signature" otherwise. Detail copy names the fix, for example "End with your
  signature; it carries the portfolio. Keep one link in the body."

- [ ] **Step 4: Run to see them pass.** Expected: OK, and the whole `tests.test_drafts_checks` module still passes
  after updating any older test that expected "portfolio first".

- [ ] **Step 5: Commit** `git commit -m "Checks: the portfolio lives in his signature, and a missing signature warns"`

### Task 3: The two new blocks

**Files:**
- Modify: `outreach/drafts/checks.py` (new `_markdown_link`, new `_visa_line`, `BLOCK_CODES`, `_recruiter` copy),
  `outreach/drafting/voice_checks.py` (remove `voice_opt_line` and `OPT`)
- Test: `tests/test_drafts_checks.py`, `tests/test_drafting_voice_checks.py`

**Interfaces:**
- Produces: block codes `markdown_link` and `visa_line`, appended to `BLOCK_CODES` after `placeholder`. `voice_opt_line`
  leaves `WARN_CODES` and `voice_checks`.

- [ ] **Step 1: Write the failing tests**

```python
def test_markdown_link_blocks(self):             # body has "my [portfolio](https://www.devanshuchicholikar.com)"
    self.assertEqual(sev(found, "markdown_link"), "block")
def test_bare_link_passes_markdown(self):
def test_visa_line_blocks_for_every_reader(self):   # for role_kind in ("recruiter", "engineer", "manager", "founder")
    # bodies: "I'm on F-1 OPT", "eligible for STEM OPT", "I don't need sponsorship", "no H-1B needed",
    #         "authorized to work now", "my work authorization", "my visa"
def test_visa_line_ignores_opt_inside_words(self):  # "I optimize queries", "users can opt in", "adopted across 3 sections"
    self.assertEqual(sev(found, "visa_line"), "pass")
def test_recruiter_warn_no_longer_mentions_work_authorization(self):
    self.assertNotIn("authorization", check(found, "recruiter")["detail"].lower())
# tests/test_drafting_voice_checks.py: the old voice_opt_line tests are deleted; add
def test_voice_checks_have_no_opt_check(self):
    self.assertNotIn("voice_opt_line", [c["code"] for c in voice_checks("I'm on F-1 OPT.", "engineer")])
```

- [ ] **Step 2: Run to see them fail.** `uv run python -m unittest tests.test_drafts_checks tests.test_drafting_voice_checks -v`

- [ ] **Step 3: Implement.** Markdown: `re.compile(r"\[[^\]\n]+\]\(\s*https?://[^)\s]+\s*\)")` on subject and body, with
  spans. Visa: one pattern, case-insensitive except `OPT`, which must be upper case: `\bF-1\b`, `\bOPT\b` (case
  sensitive), `\bvisas?\b`, `\bsponsor(?:ship|ed|s)?\b`, `\bH-?1B\b`, `work authori[sz]ation`, `authori[sz]ed to work`,
  `\bgreen card\b`, `\bimmigration\b`. Block copy: "No visa, OPT or sponsorship in any email (#9, 2026-11-09). Take
  this out; it comes up only if they ask." Recruiter warn copy drops the work-authorization sentence.

- [ ] **Step 4: Run to see them pass.** Expected: OK.

- [ ] **Step 5: Commit** `git commit -m "Checks: markdown links and any visa mention block, for every reader"`

### Task 4: Length, subjects, lines about the email, follow-up nudges

**Files:**
- Modify: `outreach/drafts/checks.py` (`WORDS`, `DEFAULT_WORDS`, `_length`, `WEAK_SUBJECT`, new `_subject_length`,
  `NEVER`, new `_narrated`), `outreach/mcp/tools/drafts.py` (`limits.words`)
- Test: `tests/test_drafts_checks.py`, `tests/test_mcp_draft_brief.py`

**Interfaces:**
- Produces: `DEFAULT_WORDS = (0, 120)` for every reader (`WORDS` removed); warn codes `subject_length` and
  `narrated_email` appended to `WARN_CODES`; `SUBJECT_MAX_CHARS = 40`; `NARRATED` tuple; `NEVER` removed.

- [ ] **Step 1: Write the failing tests**

```python
def test_length_has_no_floor(self):              # a 30-word first email to a recruiter passes
def test_length_warns_over_120(self):            # 121 words warns "Too long"
def test_subject_length_warns_over_40_chars(self):   # "keeping intraday trade data right for traders" warns
def test_subject_length_passes_40_chars(self):
def test_vague_subjects_warn(self):              # "Quick question", "Opportunity at Acme", "Hoping to connect", "Referral request"
def test_narrated_email_warns_on_first_email(self):
    # each: "I'll be brief.", "Short one.", "You must get a lot of these.", "Your inbox is probably full.",
    #       "Thank you for opening this.", "I hope it's okay that I'm asking.", "Sorry to bother you."
def test_narrated_email_is_not_checked_on_followups(self):   # seq=1 "If you're busy, one line back would help." passes
def test_followup_nudge_is_not_banned(self):     # seq=1 "Just bumping this in case it got buried." has no banned_phrase warn
def test_brief_limits_words_are_0_to_120(self):  # tests/test_mcp_draft_brief.py, for a recruiter and an engineer
```

- [ ] **Step 2: Run to see them fail.** `uv run python -m unittest tests.test_drafts_checks tests.test_mcp_draft_brief -v`

- [ ] **Step 3: Implement.** `WEAK_SUBJECT` becomes `["quick question", "following up", "excited", "opportunity",
  "hoping to connect", "touching base", "checking in", "application for", "referral request"]`. `NARRATED` holds the
  phrases in the test plus "i'll keep this short", "i'll keep it short", "quick note", "many of these", "you don't owe
  me", "i know you're busy", "out of the blue", "this cold email"; matched case-insensitively on word boundaries, first
  emails only, with spans. Copy: "This line talks about the email itself. Cut it; the specifics do the work."

- [ ] **Step 4: Run to see them pass.** Expected: OK.

- [ ] **Step 5: Commit** `git commit -m "Checks: up to 120 words, short real subjects, no lines about the email itself"`

### Task 5: me.md, the follow-up prompt and the skill say the same

**Files:**
- Modify: `mail/me.md`, `outreach/followup_drafter/prompt.md`, `.claude/skills/cold-email/SKILL.md`
- Test: `tests/test_drafting_brief.py`, `tests/test_followup_drafter.py`

**Interfaces:**
- Consumes: `read_evidence()` (unchanged: it stops at `## Needs Devanshu`).

- [ ] **Step 1: Write the failing tests**

```python
def test_evidence_has_no_work_authorization(self):   # read_evidence() of the repo's mail/me.md
    for w in ("OPT", "F-1", "sponsor", "authorized to work"): self.assertNotIn(w, evidence)
def test_evidence_lists_projects_before_experience(self):
    self.assertLess(evidence.index("## Projects"), evidence.index("## Experience"))
def test_followup_prompt_never_mentions_visa_and_allows_a_nudge(self):
    # prompt.md has no "OPT" or "recruiter"; it contains "signature" and allows "following up"
```

- [ ] **Step 2: Run to see them fail.** `uv run python -m unittest tests.test_drafting_brief tests.test_followup_drafter -v`

- [ ] **Step 3: Edit.**
  - `mail/me.md`: move "## Projects" above "## Experience"; move the work-authorization lines to a new section
    "## Only if they ask" placed after "## Needs Devanshu" (so no brief carries them); "## Signature" says the signature
    comes from the local signature file and is never typed by hand; delete the phone ASK line (answered 2026-11-10, kept
    out of this public repo).
  - `prompt.md`: a follow-up may be a short plain nudge and may say it is following up; it need not add a new fact; it
    stays on the first email's role; it ends with the brief's `signature`; no visa, OPT or sponsorship to anyone; no
    markdown.
  - `SKILL.md` (the minimum until part 4 rewrites it): at most 120 words; no visa line for anyone (recruiter row and
    recruiter subject lose it); end with the brief's `signature` after an optional sign-off word; one link in the body,
    the portfolio is in the signature; the follow-up section matches `prompt.md`.

- [ ] **Step 4: Run to see them pass, then the whole suite.** `uv run python -m unittest discover -s tests -t .`
  Expected: OK.

- [ ] **Step 5: Commit** `git commit -m "me.md, follow-up prompt and skill: projects first, no visa, his signature"`

## Deploy (in the live checkout, after merge)

1. `git pull --ff-only https://github.com/DevanshuNEU/sept-23-companies.git master`, `uv sync`, the tests.
2. Write `~/Library/Application Support/outreach/signature.txt` with three lines: `Devanshu Chicholikar`,
   `https://www.devanshuchicholikar.com`, and his phone number as he gave it in chat on 2026-11-10. It is never committed.
3. Kickstart the API job (`launchctl kickstart -k gui/$(id -u)/com.devanshu.outreach-api`). The sender and the
   follow-up drafter start fresh each run. The next Claude Code session loads the new brief.
4. Tell him: approved emails that now block (markdown links, any visa line, recruiters' included) go back to his
   drafts when the sender reaches them, each with its reason.
