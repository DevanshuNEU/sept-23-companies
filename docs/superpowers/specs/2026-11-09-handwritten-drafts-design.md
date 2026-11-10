# Drafts that read handwritten

Date: 2026-11-09. Status: approved by Devanshu 2026-11-09 with the scope cut below. Issue: to be opened when he approves.

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
- Follow-ups can switch roles mid-thread (Hark: first email about Computer-Use Agents, follow-up about LLM &
  Production).

The lab (`.context/drafting-lab.md` in the harrisburg workspace): 12 approved drafts, three versions each (the
original, a GPT rewrite after two rounds of critique, and a Claude rewrite that followed the rules below). A fresh GPT
session judged them blind, versions shuffled:

| Version | Feels human (1 to 10) | Would reply (1 to 10) | Picked best |
|---|---|---|---|
| Original | 5.3 | 4.4 | 0 of 12 |
| GPT rewrite | 4.7 | 4.8 | 1 of 12 |
| Claude rewrite | 7.7 | 7.2 | 11 of 12 |

GPT's rewrites drifted formal and uniform ("I'm contacting you as a...", "I don't know whether..." in nearly every
email) and once invented a detail. One judge on 12 emails is a strong hint, not proof.

Research: `docs/research/2026-11-09-human-cold-outreach.md` (ChatGPT Deep Research, 2026-11-09). Checked by hand:
Russell, Karpinska, Iyyer (ACL 2025: five frequent LLM users by majority vote miss 1 of 300), the Wikipedia "Signs of
AI writing" guide (negative parallelism, rule of three, "-ing" tails, inflated significance; em dashes now a historical
indicator), and Nick Singh's page (real cold emails that got him interviews). The PNAS paper (Jakesch et al. 2023,
people judge AI text at 50 to 52%) could not be opened through PMC; its numbers are as the research reports them.

What the research says, in short: the tell people actually catch is sameness across messages, inflated or generic
wording, and leaked formatting, not contractions or warmth. Replies come from messages that say why this person,
use one specific true detail, ask one small thing a stranger can answer in a line, and make "no" cheap. A small
honest doubt reads as human. Feeling belongs to the work, never to the job hunt. Planted typos do not help.

## Goals

1. No email carries markdown or gives the visa line to a non-recruiter.
2. A draft cannot quietly repeat the openings and sentences of his other emails.
3. Each engineer, manager or founder email carries one true, sourced fact about the person or their team when one
   exists, and says plainly that they haven't met when none does.
4. The cold-email skill writes the way the lab's winning set did.
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
- `opt_line` (block): `voice_opt_line` becomes a block for any `role_kind` other than `recruiter`. `mail/me.md`'s
  line changes to "for recruiters only", matching #9.
- `length` (warn): first emails at most 120 words, with no floor (Devanshu, 2026-11-09: "until that anything is
  fine"). Replaces 80 to 120, and the recruiters' "about 90".
- `reused_sentence` (warn): a sentence of 6 or more words that also appears in 3 or more of his other first emails
  (drafted, approved or sent), quoted with the count.
- `same_opening` (warn): the first sentence after the greeting starts with the same 4 words as 5 or more of his last
  100 first emails.

The sameness checks warn, never block. Warnings show in the Outbox and `save_draft` returns them, and the skill
fixes every one it can.

### Part 2. The brief carries what to avoid (code)

`draft_brief` adds two lists computed from the store, so the drafter is told in code what is overused, not left to
remember:

- `recent_openings`: the first sentence of his last 30 first emails.
- `overused`: sentences and 6-word runs found in 10 or more of his first emails, with counts.

The brief id stays the hash of the voice files, me.md and the person; these lists are advice and do not change it.

### Part 3. Research that actually runs (code plus skill)

The skill already says to research each engineer, manager or founder on the web, but the result is never stored, so
no draft has a source. Change:

- `save_draft` takes optional `sources` (kind, URL, label, quote, `observed_on`), the same shape `add_people` takes,
  and writes them to `person_source`. The MCP guard grants that insert to `save_draft`.
- New warn `no_person_fact`: an engineer, manager or founder draft whose person has no profile text and no source.
  The draft still saves; the warning tells him the email is built on the role alone.
- The skill: when research finds nothing, the email says so plainly (for example that they haven't met and why
  this person: their title is on the team that owns the role). Never fake a connection.

### Part 4. The skill (`.claude/skills/cold-email/SKILL.md`, edited with the writing-for-agents skill)

Keep every hard rule (#9's limits, truth, links, no visa line except for recruiters, his voice files, the brief).
Replace the fixed "Shape" and the by-reader table's fixed asks with what the lab and research showed works:

- Say why this person in the first two lines, from a true fact: their team, their title's area, something they wrote,
  a shared school or employer. The fact the original Jason email missed (he did Sandbox at Northeastern) is the
  kind of fact that matters.
- Open differently from `recent_openings`. Northeastern is a fact to use when it connects, never the default opener.
- One piece of proof chosen for this role (a Data Platform role hears about data and backend work, not a voice-agent
  evaluator). Name the role once, exactly.
- One ask a stranger can answer in a line and that the posting does not already answer. A name counts as success.
  Make "no" cheap ("if it's not yours, a name helps just as much"). No fixed meeting time unless the reader is a
  recruiter.
- A small honest doubt or gap is allowed, at most once, in his words ("I have two full-time against your three. I'm
  writing anyway.").
- Vary the sign-off ("Thanks,", "Thank you,", "Best,", or none). The name line stays "Devanshu Chicholikar".
- Cut anything that exists to sound warm ("that kind of work is what I'd want to keep doing", "caught my attention").
  Warmth comes from the specific detail and the easy ask.
- Before saving, read the draft against `recent_openings` and `overused`; save; fix every `reused_sentence`,
  `same_opening` and `no_person_fact` warning the facts allow.

Follow-ups (`outreach/followup_drafter/prompt.md` and the skill's follow-up section), Devanshu 2026-11-09: a
follow-up may be a plain, short human nudge, the way people actually write one ("just bumping this in case it got
buried", "if you're busy, one line back would really help"). The ban on saying you are following up or bumping the
thread goes. Each follow-up no longer has to add a new fact; it may, when there is a good one. It stays on the first
email's role, uses no markdown, and never repeats a sentence from the thread.

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

## Devanshu's rulings (2026-11-09)

- Closed or old roles, citizenship terms and people per company are not this work's concern (see Scope).
- Length: at most 120 words; anything under that is fine.
- Links: unchanged, exactly 2 in a first email.
- Follow-ups: a plain human nudge is allowed (Part 4).

## Calls made in the design (Claude's, Devanshu can overrule)

- **#9 first.** The two new blocks (`markdown_link`, `opt_line`) join #9's "Approve checks" list, and #9 records
  the length and follow-up rulings, before any code.
- **A dry-run check tool.** New read-only MCP tool `check_draft` (person, subject, body, `seq`): runs every save
  check, stores nothing, and leaves the message's own stored text out of the sameness counts. The skill uses it to
  test a set of drafts for a judge run without touching the store; those drafts live in a JSON file.
- **When a skill change ships.** The new skill replaces the old when, on a fresh sample of 12, the blind judge picks
  it in at least 8 and it does not lose on either average score. If it falls short, tune and run again, at most twice,
  then bring the results to him.

## Left to the builder

- Exact thresholds (3 emails for `reused_sentence`, 5 of 100 for `same_opening`) may be tuned
  against the store before merge; say the chosen values in the PR.
- Whether the sameness checks read a cached table or compute per save; a save must stay fast at 1,000+ emails.
- Order of PRs: Part 1 first (it protects the queue), then Parts 2 and 3 together, then Part 4 with a judge run,
  then Part 5 if it was not already needed for Part 4. One issue per part.
