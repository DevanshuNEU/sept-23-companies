# Drafts that read handwritten

Date: 2026-11-09. Status: scope and rulings approved by Devanshu 2026-11-09; the subject rules, "Stories" and `narrated_email` were added after lab 3 and wait for his review. Issue: to be opened when he approves.

## Scope (Devanshu, 2026-11-09)

This work is the drafting part only: what a cold email or follow-up says. Other parts of Knock own the rest, and this
work does not touch them. The job board finds the latest jobs. Devanshu reads them and decides where to apply and
whom to contact, as many people per company as he likes. The scheduler sends. So no check here looks at whether a
role is open or closed, how old a posting is, a posting's citizenship terms, or how many people at a company get
email.

## Why

Devanshu wants every email to feel like he typed it by hand for that one person: personal, welcoming, never
reading as AI. The goal it serves is #9's: replies per month.

What the Outbox shows on 2026-11-09 (read only, 459 first emails: 275 approved, 184 sent):

- 184 first emails since Oct 27 have produced 1 reply, a no.
- The emails read as one template. "I graduated from Northeastern in May" is in 345 of 459. "Thursday 10am ET" is in
  57, "who owns hiring" in 32, the same Overhear line ("caught 23 of 23 planted failures") in 41. Most open with one
  of five moves. A reader who gets two of these, or a team that compares notes, sees a mail merge.
- Nothing true and personal to build on. 0 of 459 people have a researched source on file, and 258 have no profile
  text. The hook is often "thin: title only". So the emails fall back to his resume.
- 77 of 459 put markdown links in the body (`[portfolio](https://...)`). The sender sends plain text
  (`outreach/sending/gmail.py`, `set_content`), so the brackets arrive as typed. 21 went out like that; 56 are approved.
- 13 non-recruiters got the work-authorization line, 6 of them still approved. #9 (2026-10-27) gives it to
  recruiters only, but `mail/me.md` says the line is "for recruiters and managers", and `voice_opt_line` only warns.
- Follow-ups can switch roles mid-thread (one thread's first email named one role, its follow-up another).

The lab (`docs/research/2026-11-09-drafting-lab.md`): three rounds on 12 approved drafts, each judged blind by a
fresh GPT session, plus an inbox test where the subject and Gmail preview sat among 6 template decoys.

| Round | Winner | Feels human | Picked best | Opened first in the inbox |
|---|---|---|---|---|
| 1: original vs GPT rewrite vs Claude rewrite | Claude rewrite (v1) | 7.7 vs 5.3 original, 4.7 GPT | 11 of 12 | not run |
| 2: v1 vs added warmth | v1 (7 of 12); warm lines about the email itself flagged every time | 7.3 | 7 of 12 | v1 4, warm 4, decoys 0 of 72 |
| 3: the combined recipe (v3) vs v1 vs warm | v3 | 7.8 vs 6.8 and 6.8 | 9 of 12 | v3 8 of 12 |

GPT's own rewrites drifted formal and uniform and once invented a detail, so GPT judges and does not draft.

Research: `docs/research/2026-11-09-human-cold-outreach.md` (ChatGPT Deep Research) and
`docs/research/2026-11-09-subject-lines-and-warm-cold-email.md` (GPT with live web search), both 2026-11-09. Checked by hand:
Russell, Karpinska, Iyyer (ACL 2025: five frequent LLM users by majority vote miss 1 of 300), the Wikipedia "Signs of
AI writing" guide (negative parallelism, rule of three, "-ing" tails, inflated significance; em dashes now a historical
indicator), and Nick Singh's page (real cold emails that got him interviews). The PNAS paper (Jakesch et al. 2023,
people judge AI text at 50 to 52%) could not be opened through PMC; its numbers are as the research reports them.

What the research says, in short: the tell people actually catch is sameness across messages, inflated or generic
wording, and leaked formatting, not contractions or warmth. Replies come from messages that say why this person,
use one specific true detail, ask one small thing a stranger can answer in a line, and make "no" cheap. A small
honest doubt reads as human. Feeling belongs to the work, never to the job hunt. Planted typos do not help.
On subjects: short (Boomerang, 40 million emails: 3 to 4 words got the most replies; Gong, 85 million cold emails:
short wins, buzzwords hurt), naming the real topic, with no trick (a blank subject raised opens and cut replies). A
first name in the subject lifted opens in marketing email (Sahni et al. 2018) but a 2023 replication found nothing.
Gmail shows about 38 characters of subject on a phone, then the first line of the body as preview, so the subject and
the first line are read as one unit. On warmth: slightly warm emails got about 15% more replies than neutral ones,
very positive ones lost it (Boomerang), and senders underrate how much a sincere note is appreciated (Kumar and Epley
2018; Liu et al. 2022). A recruiter documented getting the same "you probably don't get many of these" line again and
again; commenting on the email itself is a template too.

## Goals

1. No email carries markdown or mentions visa, OPT or sponsorship to anyone.
2. A draft cannot quietly repeat the openings and sentences of his other emails.
3. Each engineer, manager or founder email carries one true, sourced fact about the person or their team when one
   exists, and says plainly that they haven't met when none does.
4. The cold-email skill writes the way the lab's winning set (v3) did, subjects included.
5. Any change to the skill can be scored against the old one, blind, before it replaces it.

## Non-goals and anti-goals

- Claude still never approves, sends, skips, unapproves or pauses an email. His click approves.
- No model calls inside `outreach/drafting` or the save path (#135). The judge is a separate, on-demand tool.
- GPT does not write drafts. In the lab it wrote worse emails than Claude; it judges.
- No reply analytics or style tagging yet: 1 reply in 184 is too little data. Revisit at 30+ replies (the DevVault
  outreach rule).
- Out of scope (other parts of Knock): open or closed roles, posting age, citizenship or clearance terms, people
  per company, send timing.
- Anti-goal: a rule set that becomes the next template. The lab showed it: GPT applied the same advice to all 12
  and made a new mail merge. Every rule below is about what is true and specific, not a menu of openings.
- Anti-goal: fake familiarity. A personal touch that isn't true is worse than "we haven't met".

## What changes

### Part 1. Checks on what the draft says (code, first PR)

New and changed checks in `outreach/drafts/checks.py`, run by `save_draft`, `save_followup` and the approve route.
The sender already reruns the same checks before each send (`outreach/sending/precheck.py`), so an approved email
that now blocks goes back to drafted with its reason; that is existing sender behavior and this work does not change
it.

- `markdown_link` (block): a `[text](url)` in the body. Fix: write the bare URL on its own line.
- `visa_line` (block): any mention of visa, OPT, STEM OPT, F-1, sponsorship, H-1B or work authorization, for every
  reader, recruiters included (Devanshu, 2026-11-09: no visa mention anywhere; he only reaches companies he is
  eligible for). Replaces `voice_opt_line`. The work-authorization line leaves `mail/me.md`'s usable facts (it moves
  under "Needs Devanshu" or a "reply only" note, so the brief never hands it over), and the skill's recruiter row
  loses its visa line and "on OPT" subject.
- `length` (warn): first emails at most 120 words, with no floor (Devanshu, 2026-11-09: "until that anything is
  fine"). Replaces 80 to 120, and the recruiters' "about 90".
- `reused_sentence` (warn): a sentence of 6 or more words that also appears in 3 or more of his other first emails
  (drafted, approved or sent), quoted with the count.
- `same_opening` (warn): the first sentence after the greeting starts with the same 4 words as 5 or more of his last
  100 first emails.

- `narrated_email` (warn): a line about the email itself or the reader's inbox or time ("I'll be brief", "short
  one", "you must get a lot of these", "your inbox is probably full", "thank you for opening this", "I hope it's okay
  that I'm asking"). Every lab judge flagged these as performed.
- `subject_length` (warn): a subject over 40 characters (what a phone shows), or one on the vague list ("quick
  question", "hoping to connect", "following up", "opportunity").

The sameness checks warn, never block. Warnings show in the Outbox and `save_draft` returns them, and the skill
fixes every one it can.

### Part 2. The brief carries what to avoid (code)

`draft_brief` adds two lists computed from the store, so the drafter is told in code what is overused, not left to
remember:

- `recent_openings`: the first sentence of his last 30 first emails.
- `overused`: sentences and 6-word runs found in 10 or more of his first emails, with counts.
- `recent_subjects`: the subjects of his last 30 first emails.
- `evidence_used`: how many of his first emails in the last 14 days leaned on each project or job (the `project`
  field), so the drafter can pick a fitting one he has not used much.

The brief id stays the hash of the voice files, me.md and the person; these lists are advice and do not change it.

### Part 3. Research that actually runs (code plus skill)

The skill already says to research each engineer, manager or founder on the web, but the result is never stored, so
no draft has a source. Change:

- `save_draft` takes optional `sources` (kind, URL, label, quote, `observed_on`), the same shape `add_people` takes,
  and writes them to `person_source`. The MCP guard grants that insert to `save_draft`.
- New warn `no_person_fact`: an engineer, manager or founder draft whose person has no profile text and no source.
  The draft still saves; the warning tells him the email is built on the role alone.
- **His stories** (me.md, new section "Stories"): short true moments from his own public writing, each with its
  link, that only he can tell, so an email can carry one that fits the reader. Examples from his LinkedIn posts: the
  $200 then $500 Claude bills that made him build Saar for himself first; the pharmacy caller he built during the
  Adderall shortage, whose hardest part was getting it to admit when it doesn't know; explaining every diff out loud
  to an empty room before he commits; the reranker that made his code search worse before it made it better. He
  approves each one before it goes in. A feeling about his work is used only when he wrote it himself; the drafter
  never adds one (the lab showed made-up feelings get caught, and they would not be true).
- The skill: when research finds nothing, the email says so plainly (for example that they haven't met and why
  this person: their title is on the team that owns the role). Never fake a connection.

**As built (#205).** Table `research_fact` (migration 0010): checked company facts with fact, quote, url, page date,
`found_by` (gpt, claude or hand) and `verified_at`, fresh for 14 days, kept on the merge survivor.
`outreach/research/verify.py` fetches each fact's https page (redirects followed hop by hop, each hop checked) and
keeps a fact only when its quote is on the page; it refuses LinkedIn, contact-data sites, private or local addresses
and non-https. MCP tool `save_research(company_id, person_id?, facts)` takes up to 8 facts, checks them first, writes
company facts to `research_fact` and person facts to `person_source` (kind post), works only in the live checkout and
returns what was saved and what was rejected, with reasons. `draft_brief` now returns `company_facts` (fresh, marked
untrusted), `research_needed`, `posting` (title, url and text cut to 4,000 characters, marked untrusted, or null) and
`stories`; the brief id covers the stories. The new warn `no_person_fact` fires on a first email to an engineer,
manager or founder with no profile text and no source. The command `uv run python -m outreach.research company <id>
[<id>...] [--force] [--dry-run]` has GPT with web search (Codex CLI) research a company once and keeps only checked
facts (`found_by` gpt), one line per company; real runs only in the live checkout, and `--dry-run` prints the prompt
and needs a scratch `OUTREACH_DB`. The stories live in a private DevVault file (`OUTREACH_STORIES`, else
`~/Desktop/DevVault/job-search/outreach/stories/stories.md`), not in me.md, because many come from his private notes;
a missing file means no stories.

### Part 4. The skill (`.claude/skills/cold-email/SKILL.md`, edited with the writing-for-agents skill)

Keep every hard rule (#9's limits, truth, links, no visa mention at all, his voice files, the brief).
Replace the fixed "Shape" and the by-reader table's fixed asks with what the lab and research showed works:

**Devanshu's steps (2026-11-09), in order.** (1) Read the posting Knock has stored. (2) Research the company: what
it is building or shipped lately and what the team owning the role works on, from public pages, quoted with link and
date. (3) Research the person. (4) Find where he fits: the one project of his that does the kind of work they do.
(5) Say how he'd help, humbly, in their own words for their work, never "you have a problem I can fix". (6) His
projects lead (Overhear, OpenCodeIntel, CallBudget, Saar, Mem Machines, saar CLI, tool-crowding as being built); his
two jobs in India are at most one supporting line, used when the role is plainly backend or the email argues
experience. (7) A story if it fits. (8) Write. (9) Subject last. (10) Check and save. Lab rounds 4 and 5: the
research must be stated plainly, in the posting's or the launch's own words; a line that bridges their news to his
work ("that's the problem I keep coming back to") reads as forced and lost.

The company facts are stored like person facts: `save_draft` takes them as `sources` of kind `company` (URL, quote,
date), so a draft's claims about the company can be checked later.

**The subject.** Written last, from the most concrete true thing in the email, so it promises what the first line
delivers. At most about 40 characters, lowercase is fine. What won the inbox test: a shared tie with the reader (a
student group of theirs and his school), a result ("800ms to 280ms"), a lesson ("my reranker made search worse
first"), an honest gap ("two years against your three"), a plain question about the role ("which team owns engineer
I?"), or the role plus who he is ("<the program>, from a backend engineer"). What lost: warm but vague subjects ("one
engineer to another", "a small favor, if you have a minute"), ticket titles, fake "Re:", their first name, and
anything from `recent_subjects`.

**The first line.** It is the Gmail preview, so it pays off the subject at once: the tie, the story or the reason for
writing to this person. No pleasantries and no line about the email itself.

**The body.**
- Say why this person in the first two lines, from a true fact: their team, their title's area, something they wrote,
  a shared school or employer. A shared school activity the original email missed (it recited the person's job
  history instead) is the kind of fact that matters.
- Open differently from `recent_openings`. Northeastern is a fact to use when it connects, never the default opener.
- One piece of proof chosen for this role and this reader (a data platform role hears about data and backend work, not
  a voice-agent evaluator), preferring one not heavy in `evidence_used`. Name the role once, exactly.
- A story from "Stories" when one fits the reader; told plainly, in one or two sentences. A story that does not fit
  the reader loses (the lab's spending story to a manager at an unrelated product).
- Warmth comes from the specific detail, a plain sentence about why he wants this role, and an easy ask. Never from
  talking about the reader's time or inbox, praising them, or adding a feeling he didn't write.
- One ask a stranger can answer in a line and that the posting does not already answer. A name counts as success, and
  "no" should be cheap. Word the ask fresh each time; "a name is plenty" and "is this yours, else who" were already
  repeating across 12 emails.
- A small honest doubt or gap is allowed, at most once, in his words ("I have two full-time against your three. I'm
  writing anyway.").
- No visa, OPT or sponsorship, to anyone.
- Vary the sign-off ("Thanks,", "Thank you,", "Best,", or none). The name line stays "Devanshu Chicholikar".
- The second link may be the piece of his writing the email leans on (the rerankers post, the LLM judge post) when that
  pays off the subject better than the project page.
- Before saving, read the draft against `recent_openings`, `recent_subjects` and `overused`; check it with
  `check_draft`; save; fix every sameness, `narrated_email` and `no_person_fact` warning the facts allow.

Follow-ups (`outreach/followup_drafter/prompt.md` and the skill's follow-up section), Devanshu 2026-11-09: a
follow-up may be a plain, short human nudge, the way people actually write one ("just bumping this in case it got
buried", "if you're busy, one line back would really help"). The ban on saying you are following up or bumping the
thread goes. Each follow-up no longer has to add a new fact; it may, when there is a good one. It stays on the first
email's role, uses no markdown, and never repeats a sentence from the thread.

**As built (#211).** The skill is at version 5: research first, projects first, a plain company fact or a true tie to
open, a personal subject from his side, his stories when they fit, and the five-openings and three-drafts steps kept
from #9's 2026-10-27 ruling. The MCP server instructions now match #9 (at most 120 words, 2 links with the portfolio in
his signature, no visa mention). His review before sending is the test of a skill change; the judge in Part 5 is a tool for comparing versions (2026-11-11).

### Part 5. The blind judge (tool, on demand)

A command that scores two sets of drafts the way the lab did, so a skill change is measured before it ships:
`python -m outreach.draft_lab judge --a <set> --b <set>`. A set is a JSON file of drafts (message id, subject, body)
or `--a stored --sample 12` for a sample from the store, read only. It shuffles the versions per recipient, sends
them to GPT through `codex exec` (his ChatGPT login, no API key, read-only sandbox, no tools), and writes a report:
per-recipient scores, the deciding quotes, and phrases repeated across each set.

- Live checkout only when it reads the store (it opens the live database, read only); any checkout with two set files.
- Never run by a launchd job. It sends names, titles and drafts to OpenAI, so it runs only when he or Claude starts
  it.
- The judge's prompt is a file in the module, the one used in the lab, so scores stay comparable.

**As built (#211).** `outreach/draft_lab`. Set files are JSON lists of `{id, recipient, subject, body}`. The judge is GPT
through the Codex CLI with no web search (read-only sandbox). Two modes. Body (default): per recipient it picks the most
human, personal, warm, likely to get a reply, and best overall. Inbox: the subject and the first 110 characters after the
greeting line against 4 template decoys (first picks, opened, archived). Versions are shuffled per recipient with the
seed printed in the report. Body verdict: PASS when the new
set is picked in at least 8 of 12 (two thirds of the compared, non-tie items) and loses neither the human nor the reply
average; INCOMPLETE when any item is missing or malformed; FAIL otherwise. `python -m outreach.draft_lab judge --a A.json
--b B.json [--mode body|inbox] [--seed N] [--out FILE] [--dry-run]` prints one summary line and writes a markdown report
(default `.context/draft-lab/<timestamp>.md`); exit 0, 1 on a hard failure, 2 on INCOMPLETE (body mode). Real runs only in
the live checkout; `--dry-run` prints the prompt and calls nothing. An `--out` inside the repo must sit under `.context/`
(set files hold real names and the repo is public). `python -m outreach.draft_lab export --sample N [--state
approved|drafted|sent] [--seed N] --out FILE` writes a read-only random sample of stored first emails as a set file; live
checkout only.

## Devanshu's rulings (2026-11-09)

- Closed or old roles, citizenship terms and people per company are not this work's concern (see Scope).
- Length: at most 120 words; anything under that is fine.
- Links: unchanged, exactly 2 in a first email.
- No visa, OPT or sponsorship mention in any email, to anyone, unless they ask in a reply.
- Follow-ups: a plain human nudge is allowed (Part 4).

## Calls made in the design (Claude's, Devanshu can overrule)

- **#9 first.** The two new blocks (`markdown_link`, `visa_line`) join #9's "Approve checks" list, and #9 records
  the length and follow-up rulings, before any code.
- **A dry-run check tool.** New read-only MCP tool `check_draft` (person, subject, body, `seq`): runs every save
  check, stores nothing, and leaves the message's own stored text out of the sameness counts. The skill uses it to
  test a set of drafts for a judge run without touching the store; those drafts live in a JSON file.
- **When a skill change ships.** First rule: the new skill replaced the old when, on a sample of 12, the judge picked
  it in at least 8 and it lost neither average. The version 5 gate (2026-11-11, PR #214's thread) came out 8 and 7 of
  12 with averages about even, and two tuned rounds did worse on bodies. Devanshu ruled the same day: his review of
  every email before sending is the test, and the judge stays a tool, not a gate.

## Left to the builder

- Exact thresholds (3 emails for `reused_sentence`, 5 of 100 for `same_opening`) may be tuned
  against the store before merge; say the chosen values in the PR.
- Whether the sameness checks read a cached table or compute per save; a save must stay fast at 1,000+ emails.
- Order of PRs: Part 1 first (it protects the queue), then Parts 2 and 3 together, then Part 4 with a judge run,
  then Part 5 if it was not already needed for Part 4. One issue per part.
