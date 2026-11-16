---
description: Cold email through Knock. Give a job link or a company; Claude checks Knock, lets Knock pick the people from an Apollo search, gets their addresses, drafts one email each into the Outbox, and reports who is in and who was dropped. He approves in the Outbox.
argument-hint: <job link or company> | "status" | "followups"
---

You are running Devanshu's cold email pipeline: **Knock** (the `outreach` MCP tools) with **Apollo** for people and
addresses. Run it from a Claude Code session in the live checkout, `~/Desktop/Sept 23 Companies`, where the Knock tools
work. DevVault's own `/outreach` is the old company-file flow; it is not used for email (#294).

## Setup (always do first)

1. Read `.claude/skills/cold-email/SKILL.md`. It is the drafting skill: steps, the email's rules, the subject,
   links, length. Follow it. Do not narrate that you read it.
2. Call `knock_status`. If the `outreach` tools are not loaded or it fails, say "Knock is not reachable from this
   session" and stop. If `pick_people` is missing from the tools, say "start a new Claude Code session: the Knock
   tools changed" and stop. If any Apollo tool asks you to sign in, say "Apollo needs signing in: run /mcp and connect
   apollo" and stop before drafting anything.

## Standing rules (#9, one outreach path ruling, 2026-11-16)

- **No questions mid-run.** A job link or company in `$ARGUMENTS` is his yes to research that company and to spend
  Apollo credits within the budget below. Ask only when two companies share the name and the link does not settle
  which.
- **One finished email per person, straight to the Outbox.** Never show him drafts, versions or a pick. He reviews
  and edits every email in the Outbox; his Approve click is his sign-off.
- **Knock picks the people, not you.** `pick_people` decides who from the Apollo search. Use its picks as they are;
  if its order looks wrong, say so in the report instead of picking around it.
- **Apollo is pre-approved:** up to **5 credits per company**, the waterfall included, without asking him each time.
  This is his standing yes to Apollo's confirmation prompts for that spend; do not ask again. Searches are free and
  never count. Past 5 credits, stop and tell him how many people are still without an address.
- **Only a sure address.** Apollo marks it verified, or the person publishes it on a page you opened. A guessed,
  unverified or catch-all-only address is no address. Never guess and never build one from a name pattern. No sure
  address means the person is dropped, with one line saying so.
- **Never** approve, send, skip or pause an email, and never call the Knock API's write routes. **No visa,
  sponsorship or H-1B line** in any email to a founder, engineer or manager.

## Argument

User invoked: `/outreach $ARGUMENTS`

- A job link (Ashby, Greenhouse, Lever, a careers page) or a company name: **Run** below.
- `status`: call `knock_status` and show it in a short table. Nothing else.
- `followups`: the skill's "Follow-ups" section (`followups_due`, then `draft_brief` and `save_followup` each).
- `sent <people>` (LinkedIn requests he says he sent): the skill's `log_sent` rule. Never for email; Knock records
  its own sends.
- Empty: ask "Which job or company?" in one line.

## Run

1. **The job.** From the link, the company and the posting: for Greenhouse
   `https://boards-api.greenhouse.io/v1/boards/<board>/jobs/<id>`, for Ashby
   `https://api.ashbyhq.com/posting-api/job-board/<board>`. Note the title, the location and what the role does.
2. **Knock first.** `contact_check` with the company name; follow merges. "Not in Knock": run the `add_command` it
   gives, then check again. Then `company` for its postings and the people on record with their status.
3. **H-1B.** `h1b_history`. Anything new you learn about the company's sponsorship (USCIS or DOL filings, a careers
   page) goes to Knock with `save_research` (the page and its words). It never goes into an email.
4. **Company size, free.** When `company` shows no size fact, ask Apollo with two free people searches on the
   company's domain, `per_page` 1: `organization_num_employees_ranges` `["1,49"]` and `["50,200"]`. People under
   "1,49" means size 40; under "50,200" means 120; neither means over 200 (pass 1000). Never use the paid company
   search or organization enrich for this.
5. **Search Apollo by place (free).** People at the company's domain with engineering, engineering manager,
   recruiter (university, technical, talent) and founder or CTO titles. Search the job's city first
   (`person_locations` `["<City>, <State>"]`), then its state, then `["United States"]`; a remote job searches the US
   only. Stop widening once you have about 25 people. Apollo's free search does not return where a person is, so set
   each candidate's `city`, `region` and `country` from the filter of the search that found them.
6. **`pick_people`** with `company_id`, `posting_url` (his link), `company_size` from step 4, and the candidates
   (name as Apollo shows it, title, city, region, country, `apollo_id`). It returns up to 5 picks in #9's order with
   why, and the reason for everyone left out. A pick with a `person_id` is already in Knock; a "maybe ... in Knock"
   note means check the full name after the reveal and use that person.
7. **Addresses.** `apollo_users_api_profile` once (`include_credit_usage`, `include_waterfall_capability`). Then
   `apollo_people_bulk_match` for the picked people only (their `apollo_id`). For anyone without a verified work
   email, run the waterfall once (`run_waterfall_email`, only when enabled) and keep drafting the others meanwhile;
   check `apollo_webhook_result_show` after each draft and give it about 3 minutes in all. Still nothing: drop them.
8. **Into Knock.** One `intake` call for everyone with a sure address: full name, title, `company_id`, `email`,
   `linkedin_url` when known. Report every `stop` route as a drop; `wait` and `find_someone_else` are a heads-up only.
9. **Draft each person** through the skill's steps 2 to 10: `person`, `draft_brief` part 1 with `posting_url` set to
   his link (part 2 once per session), company research when `research_needed` (his link was the yes), person
   research, `check_draft`, `save_draft`. Fix every block and save again.
10. **Report**, short:

```
In the Outbox (review, edit, approve there):
- <Name>, <title>: <posting named>, opens on <fact>, leans on <project>, <N> words<, warnings left>
Dropped:
- <Name>, <title>: <no verified email in Apollo or the waterfall, or pick_people's reason>
Apollo: <credits used>, balance <N>.
Knock: <anything written back>.
```

Then the text of each saved email, so he can read it without opening Knock. People dropped for no email are his
LinkedIn targets: list them last, one line each.
