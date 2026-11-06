# Person corrections: fix and move people through the MCP

Date: 2026-11-06. Status: approved by Devanshu 2026-11-06. Issue: #181.

## Why

Claude can add a person once and never touch the record again. Whatever is saved first stays.

Today's case: Bo C's address `bo@prophetsecurity.ai` was a guess and it bounced. The sender marked the touch
`bounced`, but the address is still on the person. Claude could only raise a Needs-you item. `add_people` skips
anyone already known (`created: false`), so re-adding Bo with a fixed or empty email does nothing.

Why it matters (Devanshu): a second send to a dead address hurts the sender's reputation. A bounced address must
come off fast, with no hand work from him.

Other corrections Claude cannot make: a surname inferred from an email (Nick Poe), a domain that needed a check
(Denise, talentful.com), addresses greyed out in the source (Ayon, Nik), an employer change (Rohit K), and a draft
whose recipient is dead (`can_draft_email` stays false with `already_emailed` even though the send failed).

## Goals

1. Claude clears or replaces a person's email, and clears a bounced one with a reason, in one call.
2. Claude fixes name, title, role kind, LinkedIn URL, profile text and notes after the fact.
3. Claude moves a person to another company.
4. Every edit is logged with who made it and the old value, and he sees it in Needs-you.

## Non-goals and anti-goals

- Claude still never approves, sends, skips, unapproves or pauses an email, and never writes a no-go row.
- No secret code or passphrase. Claude reads the chat, so a code given to it is in its own context and it could
  confirm its own proposals. His click in the app is the confirmation.
- No tool writes `touch` or `message`. The sender's bounce detection owns touch state, and the sender already holds
  any message whose person has no address (`no_address`).
- No deletes. Removing a person is #183. A fresh first email after a corrected address is #182.
- Failure to avoid even if it "works": an edit that quietly lets a draft go to the wrong person or to a dead address.

## What Devanshu decided

- **Fast path for bounces.** `mark_email_bounced` takes effect at once. It is logged, so it can be undone by hand
  from the old value. The person stays on record.
- **Plain edits and moves apply at once**, each logged with the actor and the old value, each raising a Needs-you
  item. Nothing besides removal needs his click.
- **Removal waits for his click** (#183, built later).
- **A fresh first email after a bounce and a corrected address: yes**, but it needs a second thread per person and
  is its own change (#182).

## Tools

Three new MCP tools, each with its own narrow grants.

**`update_person(person_id, ...)`**
Optional typed fields: `name`, `title`, `role_kind`, `linkedin_url`, `email`, `profile_text`, `notes`. Only fields
sent change; `email: null` clears the address. One `event` (`entity=person`, `kind=edited`, `actor=claude`) holds
`{field: {"old": ..., "new": ...}}` for every field that changed. A call that changes nothing returns `changed: []`
and writes nothing.

**`mark_email_bounced(person_id, reason)`**
Clears `person.email`, records the old address and the reason (3 to 300 characters) in an `event`, and applies the
channel rule. It writes no message: drafted or approved messages stay and the sender holds them with "No email
address for this person", which the Outbox shows. A message already `sending` is the sender's to settle. It works for
any person who has an address, whether or not the sender has seen the bounce yet, so a bounce he saw in Gmail can be
reported first.

**`move_person(person_id, company_id)`**
Changes the person's company to the target (the survivor if the target was merged, as other tools resolve it).
Refused when the person has a no-go row. Sent emails, LinkedIn requests and old events stay under the company they
happened at. The move event names both companies. A closed or no-go target company still works and returns a warning.

## The channel rule

A person on the email channel must have an address (`person` CHECK), except imports. Clearing an email on an
email-channel person also sets `channel = linkedin` and `linkedin_reason = no_email`. Setting an email on a
LinkedIn-channel person flips them to `email` and clears the reason, as `add_people` already does when it fills an
address. An edit that would break a `person` CHECK or the unique email and LinkedIn indexes returns a plain
`{code, message, fix}` error and writes nothing.

## Replacing an email

- Never sent to: applies at once.
- First email sent and not bounced (a touch with `first_sent_at`, an import touch, or a first message in a sent
  state, the same test as `why_not_email`): refused. Follow-ups ride the same Gmail thread and a new address may be a
  different human. The fix says to clear it with `mark_email_bounced` or ask Devanshu.
- After a bounce: allowed. The touch is closed and no follow-ups are queued.

## Guard and audit

- `person.company_id` is forbidden for every grant today (`NEVER_COLUMNS`, and the `person_update` trigger).
  `move_person` is the one exception: a grant naming `company_id` may name only `company_id`, `updated_at` and
  `revision`, and the trigger lets `company_id` change only on a connection whose grant names it.
  `person_id`, `added_by` and `created_at` stay forbidden for every tool. `tests/test_mcp_write_grants.py` pins the
  three new grants and fails if any other tool gets `company_id`.
- Audit is the existing `event` table. No new table.
- Needs-you items reuse the `flagged` kind (`raise_flag` in `outreach/mcp/tools/research.py`): no new kind, no
  migration. Claude may only insert review items, so each call inserts one item. Its `evidence_key` is `claude-edit:`
  plus a hash of the person and the changed fields with old and new values: a repeat of the same call returns the
  same item, and a later different change gets a new one. Its company is the person's company after the edit. `ok`
  clears it.
- `SERVER_VERSION` goes from "2" to "3", since what tools write changes.

## Drafts after an edit

- `save_draft` keeps its rule that it cannot change a recipient; the recipient is always the person's current email.
- The check order in `why_not_email` already puts `no_email` before `already_emailed`, so a person whose address
  was cleared reads `no_email`. After a corrected address is set on a bounced person, `already_emailed` returns
  until #182 lands.

## Left to the builder

- Event kind names beyond `edited`, error codes, and the Needs-you wording, in the repo's plain style, no dashes.
- Whether `update_person` rejects a no-op or returns `changed: []` (the lean is the second).

## My calls, for Devanshu to check

1. No touch or message write: the sender owns bounce state and holds mail with no address.
2. Audit lives in `event`, not a new table.
3. Edit notices reuse the `flagged` review kind, one per call, so there is no migration.
4. A replacement email applies at once, except after a sent first email that did not bounce (refused).
5. A move keeps old sends under the old company, and warns instead of refusing for a closed or no-go target.
6. Removal (#183) and a fresh first email (#182) are separate issues and PRs.

## Done when

- Claude clears Bo's bounced address with `mark_email_bounced`; the person stays, an event holds the old address
  and the reason, and `person` shows `no_email` instead of `already_emailed`.
- `update_person` and `move_person` change a record, write an event with old values, and raise a Needs-you item.
- No tool writes `touch`, `message`, `no_go`, approval fields or a recipient; the grant test proves it.
- The repo's full test suite passes.
