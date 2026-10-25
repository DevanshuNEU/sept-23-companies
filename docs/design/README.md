# Knock: UI and UX design (#12)

Status: draft for Devanshu's approval. No React code starts until he signs off on this file and the mockups.
Decision record: issue #9 wins over everything here. Architecture: `docs/superpowers/specs/2026-10-18-outreach-platform-design.md`
(entity names in this file follow its section 4).

Mockups: open `index.html` in this folder (or serve the folder: `python3 -m http.server 3012`, never 3001, 3002
or 3011). One page per view, one shared `mockup.css` and a small `mockup.js` for the command palette, theme,
keys and tabs. All data in them is fake; email addresses use `.example` domains.

| View | Mockup | Shows |
|---|---|---|
| Today | `today.html` | 18 proposals in two groups, 3 of 5 picked, preview panel, remove dialog |
| LinkedIn list | `linkedin.html` | people grouped by company, role to name, request to reply progress |
| Outbox | `outbox.html` | reply holding a company, blocked drafts, recruiter warning, follow-up 2, ramp at 20 of 30 |
| Needs you | `needs-you.html` | four states, switched by a dashed "Mockup state" bar: Morning (all five triggers, one mid-edit, one waiting 11 days), Duplicates and Undo (the merge confirm, Company details, Resolved with Undo, "2 settled today, 3 open"), Every kind (each other kind with its fixes), All settled |
| No-go list | `no-go.html` | companies and people with reason, evidence and source |
| Company page | `company.html` | Browserbase held by a reply, six tabs |
| Done | `done.html` | Crusoe, Retell AI, Distyl AI, a referral, replies per month |
| Today as a plan (#33) | `today-plan.html` | 7 people on a day rail, Lena Ortiz ready, find someone, an email row, the 7-day question, the watch list, a win arriving (section 16) |
| Finish (#33) | `today-finish.html` | all 7 done, and one carried to tomorrow |
| Progress (#33) | `progress.html` | today against the plan, week and month ledgers, a first-week state, monthly findings |
| Quiet week (#33) | `progress-quiet.html` | no replies this week: work first, his own usual rate second |
| Wins (#33) | `wins.html` | 6 kept wins with what he sent, a new one, and no wins yet |
| Company page, Score (#33) | `company-score.html` | Pylon's 8 signals with points, share, reason and source |

## 1. What the UI is for

One user, every weekday morning: pick companies, paste people into the Claude side terminal, review and approve
drafts, watch replies. The metric is replies per month, so replies are the loudest thing on every screen.

Rules the UI keeps everywhere:

1. **The UI never sends.** Approve is Devanshu's click and nothing else. The sender sends on its own schedule.
   No button anywhere says Send.
2. **The UI never classifies.** It shows stored decisions (posting level, sponsorship stance, check results,
   ordering, capacity). If the UI needs a judgement, the API returns it.
3. **No layout jumps.** Skeletons take the final height. A row you act on stays where it is, relabelled, until
   you move away (section 6). Numbers use tabular figures so counts do not shift text.
4. **Keyboard first.** Every action has a key. The mouse works everywhere too.
5. **Calm.** One neutral palette. Color is used only for status, always with a word or icon.

## 2. Information architecture and navigation

### Shell

A left sidebar (shadcn `Sidebar`, 240 px, collapsible with ⌘B) and a content area (`SidebarInset`) with a 48 px top
bar (breadcrumb on the left; freshness and the keys button on the right).

Sidebar, top to bottom:

| Item | Count shown | Meaning of the count |
|---|---|---|
| Search (opens the palette) | ⌘K | |
| Today | `3 of 5` | picked today of today's capacity |
| Outbox | green `1`, then `7` | replies not yet handled (green), drafts waiting for review |
| LinkedIn | `12` | people in "To reach" |
| Needs you | amber `5` | open review items |
| Lists: No-go | `31` | companies plus people |
| Lists: Done | `4` | done companies |
| Recent | | last 3 company pages opened |
| Footer | | sender state and sent of limit today, replies this month, last board read |

Order follows #12's numbering, which also matches the morning: Today, then Outbox, LinkedIn, Needs you. Counts that
need action get a colored badge (green for replies, amber for review items); counts that are only information
stay plain grey text. The footer's "Boards read" line opens a `Popover` with the last run of every `job_run`
kind (board read, daily list, sender, reconcile): start, result, counts, error.

### Routes

`/today`, `/outbox`, `/outbox/:touchId`, `/linkedin`, `/needs-you`, `/no-go`, `/no-go/people`, `/companies/:id`
(with `?tab=roles|people|emails|evidence|history`), `/done`. The selected row, tab and filters live in the URL, so
reload and back land in the same place.

### Command palette and search

`⌘K` anywhere, or `/` when not typing. shadcn `CommandDialog`.

- **Groups, in order:** Companies, People, Go to, Actions. Each result shows its current status in muted text
  ("Held: reply from Priya Natarajan", "No-go: does not sponsor", "Backlog").
- **Companies** match name, aliases and domain. **People** match name, email and title. **Postings** match title
  when the query has 3 or more characters ("new grad", "MTS", "FDE").
- **Speed:** the app loads a compact search index once (about 1,100 companies and every person: id, name,
  aliases, domain, status, one status line) and filters it in the browser on every keystroke, so results show in
  under a frame. Posting title search goes to the API, debounced 120 ms, and appears under the instant groups.
- **Ranking:** prefix match on name first, then companies in progress (picked, emailing, held), then recent,
  then everything else. Synonyms from the old dashboard carry over (mts, fde, sf, nyc).
- **Actions** in the palette: Pause sending or Resume sending, Toggle theme, Keyboard shortcuts, Copy picks for
  Claude. Actions that change data still show their normal confirmation.
- Enter opens; ⌘Enter opens a company in the Outbox filtered to it. Esc closes and returns focus.

## 3. Keyboard map

Single-letter keys work only when focus is not in a text field. `⌘` combinations work everywhere. `G` starts a
chord that waits 1 second for the second key.

### Everywhere

| Key | Action |
|---|---|
| ⌘K or / | Search and commands |
| G T, G O, G L, G N, G X, G D | Today, Outbox, LinkedIn, Needs you, No-go, Done |
| G P, G W | Progress, Wins (#33, section 16.6) |
| J / K or ↓ / ↑ | Next, previous row |
| Enter | Open the row (company page, or the item's detail) |
| Z | Undo the last action (while its toast is up, and for 8 seconds) |
| Esc | Close the dialog or palette, leave the editor, then go back |
| ? | Shortcut sheet for this page |
| ⌘B | Toggle sidebar |

### Today

With #33 these keys move to Proposals (`/proposals`, the list of all proposed companies). Today as a plan has its
own keys in section 16.6.

| Key | Action |
|---|---|
| X | Pick or unpick (stops at capacity, see section 5.1) |
| B | Send to backlog |
| R | Remove with a reason (dialog) |
| C | Copy role title |
| O | Open the posting |
| ⇧C | Copy today's picks for the side terminal |
| 1 / 2 | Proposed tab, Backlog tab |

### Outbox

| Key | Action |
|---|---|
| A | Approve the selected draft (refused with a toast if blocked) |
| E | Edit the body (focus moves into the editor) |
| ⌘S | Save edits (checks run again) |
| ⌘Enter | Save and approve, from inside the editor |
| S | Skip (undo with Z) |
| U | Withdraw approval (only before the sender claims it) |
| ⇧J / ⇧K | Next group, previous group |
| P | Pause or resume the company |
| C | Close the company with a reason |
| O | Open the company page |

### LinkedIn list

| Key | Action |
|---|---|
| C | Copy role title for this person's company |
| L | Open their LinkedIn profile |
| R, A, M, Y | Log request sent, accepted, message sent, reply |
| 1 to 4 | To reach, Waiting, Replied, All |
| [ / ] | Previous page, next page (J and K also cross into the next or previous page) |

### Needs you

| Key | Action |
|---|---|
| E | Edit the field, with a source |
| O | Mark ok; on a possible duplicate, keep the two companies separate |
| N | Mark no-go, with a reason (does nothing on a possible duplicate, which has no no-go) |
| M | Merge into the suggested company (possible duplicates only; merging into the other one is a click, with no key) |
| ⌘Enter | Save and lock (inside the edit form) |

### No-go list

| Key | Action |
|---|---|
| 1 / 2 | Companies, People |
| O | Open the source (posting, reply, or company page) |
| ⌫ | Take off the list (dialog) |

### Company page

| Key | Action |
|---|---|
| 1 to 6 | Overview, Roles, People, Emails, Evidence, History |
| C | Close with a reason |
| P / R | Pause, resume |
| Y | Copy role title |

## 4. The daily flow, through the views

1. **08:00** the daily list job writes today's `daily_list`. Devanshu opens the app; it lands on **Today**.
2. Before picking, he glances at the sidebar: a green badge on Outbox means a reply arrived overnight. Replies
   come first: **Outbox**, top group "Replied". He reads it, answers in Gmail, sets what the reply said
   (Interested, Not now, Said no, Asked to stop), and closes or resumes the company when it settles.
3. Back on **Today**, he reads the proposals top down. The preview panel shows why each was picked (role at his
   level and its age, size and stage with confidence and source, sponsorship evidence). He presses X on up to the
   capacity shown ("Pick up to 5"). B sends one to the backlog now; R removes one for good with a reason.
   Unpicked proposals roll to the backlog at the next 08:00 run.
4. **Needs you** may now show items for the companies he just picked (unknown size, missing email domain). He
   fixes them in one click each, since a missing domain blocks every draft for that company.
5. He presses ⇧C on Today to copy his picks, switches to the side terminal and pastes people per company. Claude
   calls `add_people` and `save_draft`. Drafts appear in the **Outbox** within a few seconds (the Outbox polls),
   under "Drafts to review", with a "2 new" pill rather than a jump.
6. In the **Outbox** he walks drafts with J/K. For each: read the check results, edit if needed (E, ⌘S), approve
   (A). Blocked drafts cannot be approved until the block is fixed. Approved drafts wait for a slot; the strip at
   the top shows today's count against the limit and the next send.
7. People beyond the email budget, or without an email, are on the **LinkedIn list**. He works it at his own pace
   and logs each step (R, A, M, Y).
8. Through the day the sender sends inside the window. Follow-up drafts arrive in the Outbox on their due dates
   (Claude writes them through `save_followup`); he approves them like first emails.
9. When a company ends, he closes it with a reason from the Outbox or the company page. Referral or Interview
   moves it to **Done**; No sponsorship adds it to the **No-go list**.

## 5. Views

Each view lists its purpose, layout, elements, states and actions. Shared states (loading, empty, error, stale,
partial, conflict) are drawn in `index.html` and defined once in section 7.

### 5.1 Today

With #33 this view moves to Proposals (`/proposals`), unchanged, linked quietly from under the plan as "All proposed
companies". Today is now a plan of people: section 16.1 replaces this section for `/today`.

**Purpose.** Pick today's companies from 15 to 20 proposals, and see the companies still being emailed.

**Layout.** Content on the left; a 380 px preview panel on the right for the active row (no Sheet, so J/K updates
it in place without covering the list).

**Elements.**

- Heading with the date and one line on capacity: "Pick up to 5 companies. Today's limit of 30 emails carries
  that many new companies on top of follow-ups and day 2 and 3 sends." Action: Copy picks for Claude.
- Capacity row: Picked today (3 of 5, with five small segments), Still emailing (5), Proposed (18), Backlog (41).
- **Still emailing** table: companies picked on earlier days that are not closed. Columns: company, day (Day 2
  of 3), emailed (5 of 8), next step in words (what he has to do, or "Nothing. Follow-ups are scheduled"), state
  badge (Emailing, Held: reply, Paused, Waiting).
- **Proposed** table with tabs Proposed and Backlog. Two group rows, in the order the API returns: "Role at your
  level: new grad, early career, SWE I, SWE II" and "Open US engineering role, level not named". Columns: pick
  checkbox, company and city, role to name (title, level badge, age, years asked when stated), size (number plus a
  three-bar confidence meter, or an amber "Guess" or "Unknown" badge with a tooltip), sponsorship badge (Has
  sponsored, Says it sponsors, Not stated, Unclear), row actions on hover or focus (Backlog, Remove). A "Starts
  2027" badge appears next to the level when the posting says so (shown, not hidden, per #9).
- Preview panel: name, domain and city, picked state; one sentence on why it was proposed (from the API); role to
  name card with Copy title (C) and Open posting (O); Evidence list (size, stage, sponsorship, email domain, each
  with source and date); other open roles; People (count added, and the copyable handoff block for the side
  terminal); actions Pick or Unpick, Backlog, Remove, Company page.

**States.**

- Loading: skeleton rows in both tables at row height; the capacity numbers show as skeleton blocks.
- Empty (no list yet today, before 08:00 or the job failed): "Today's list is not ready. It is built at 08:00 on
  weekdays." If the daily list `job_run` failed, the shared stale banner says so with the error and a link to job
  runs. Weekend: "No list on weekends. The backlog is still here."
- Picked at capacity: unchecked checkboxes become disabled with a tooltip "Today's limit carries 5 companies.
  Unpick one first." The count turns into "5 of 5".
- Partial: if evidence for one company fails to load, that preview section shows the partial-state row; the
  table still works.

**Actions.**

| Action | Confirmation | Undo |
|---|---|---|
| Pick, unpick | none | toggle again |
| Backlog | none | Z |
| Remove | Dialog with a reason (Not a fit for me, Already applied or in touch another way, Bigger than it looks, Other) and an optional note | Z, or take it off the No-go list later |
| Copy role, copy picks | none, toast "Copied" | |

### 5.2 LinkedIn list

**Purpose.** Reach the people who are past the email budget (best 6 to 8 get email) or have no email, and log
what happened, so the company page tells the whole story.

**Layout.** One table grouped by company. Tabs: To reach, Waiting, Replied, All. A filter field on the right.
"Requests this week" shown as a plain count (LinkedIn has no rule in #9, so no limit is drawn).
Ten companies a page, never splitting a company; the page is in the URL (`?page=2`) and a new tab or filter
starts at page 1. A footer under the table says "Showing 11 to 20 of 52 people" with Previous, "Page 2 of 5" and
Next. The header and footer stick while the page scrolls. Every column fits from about 1100 px with the sidebar
open; in a narrower table Why LinkedIn moves under the person's name.

**Elements.**

- Company group row: name (link), role to name with Copy, a hold badge when the company is held by an email
  reply, and "8 emailed, 4 here".
- Person row: avatar initials, name, title, kind badge (Engineer, Manager, Founder, Recruiter, Alum), why
  LinkedIn (Past the 8 emailed, No email found, Alum: LinkedIn first), progress as four steps (Request, Accepted,
  Message, Reply) with the latest step and date in words, the profile link, and one button for the next step.
- When the company is held by an email reply, the next-step cell shows a muted "Wait for Priya?" with a tooltip.
  It is advice, not a block: #9 holds email only.

**States.** Loading skeleton; empty per tab ("Nobody to reach. People past the email budget land here when Claude
adds them."); error and stale per section 7.

**Actions.** Log request, accepted, message, reply: one click, toast with undo. Log reply also asks for the reply
kind (same four as email). Each log writes one `linkedin_log` row and an `events` row.

### 5.3 Outbox

**Purpose.** Review and approve every draft, and see exactly what the sender is doing. The most used view.

**Layout.** A sending strip across the top, then two resizable panes (shadcn `ResizablePanelGroup`): the list
(400 px default) and the detail of the selected email.

#### Sending strip

| Cell | Shows |
|---|---|
| Sender | Running (pulsing green dot), Paused by you, Paused: hard block (red), Outside the window, Weekend, or Gmail signed out (red, with the fix). Sub-line: "Weekdays 08:30 to 16:30, their time". |
| Sent today | big number "20", "of 30", and the **ramp ladder** |
| Limit | "Level 2 of 9, 30 a day" and "40 tomorrow if today stays clean". After a warning: "Stepped down to 20 after a Gmail limit warning at 13:02". |
| Next send | time in the recipient's zone and who ("10:42 ET, Jordan Price, Mintlify"), or why nothing is next ("No approved emails", "Next window opens 08:30 ET tomorrow") |
| Pause sending | outline button; Resume sending when paused. Resuming after a hard block opens a dialog that shows Gmail's message first. |

The **ramp ladder** is the one custom visual in the app: nine bars for the nine limit levels (20, 30, up to 100),
growing in height. Levels below today's are filled grey, today's level fills from the bottom by sent over limit,
levels above are empty. It answers "how much can I send, and how far up am I" at a glance. A tooltip lists the
clean-day signals: bounce rate over the last 3 days, Gmail errors today, spam or stop replies.

#### List groups, in order

| Group | Touch states in it | Row shows |
|---|---|---|
| Replied (pinned on top, green edge) | replied, until he closes or resumes the company | the first line of the reply, time, "Holding N queued emails at Company" |
| Drafts to review | drafted, followup_ready, failed | subject, status badge (Draft, Blocked, N warnings, Follow-up N of 3, Failed), the first blocking reason or warning in words |
| Approved | approved, followup_approved | when it will send ("10:42 ET", "after 11:05 ET", "tomorrow") and why ("60 minutes after the last Graphite email", "Graphite has 3 today already", "Recruiter: day 2") |
| Sending | sending | "Locked: cannot edit, approve or skip" |
| Checking whether it went out | send_unknown | "Gmail did not answer. Looking in Sent at 10:15" |
| Sent | sent, followup_sent | sent time, and the next follow-up: "Follow-up 1 due Fri Nov 13" or "Follow-up 3 sent, no reply" |
| Paused | any unsent touch at a company paused by him | the pause reason |
| Closed (collapsed) | bounced, skipped, closed | a one-line breakdown: "bounced 1, skipped 4, company closed 12" |

Within Drafts to review the order is: first emails before follow-ups due later, then by company in pick order, then
by person kind (engineers and managers, founders, then recruiters, matching send order). Blocked drafts stay in
place rather than sinking, so the list does not reorder when checks rerun. Held touches at a replied company are
listed inside the reply's detail, not in Approved, so nothing in Approved can be misread as about to go out.

#### Detail: draft

- Header: avatar, name, title at company (link), email in mono, LinkedIn link; badges for kind, First email or
  Follow-up N of 3, and the status.
- Context row, three cells: Role to name (title, age, place); Hook (the fact used and its date, with age in days);
  the company's sending today ("1 sent, 2 slots left, next slot 11:05 ET"), or for a recruiter "Sends Wed Nov 11,
  day 2 for Mintlify, after engineers".
- **Check results** card, in this order:
  - **Blocking** rows (red tint, octagon icon). Each says what is wrong in one line and how to fix it in the
    next, with a button when the fix lives elsewhere (Compare drafts, Fix the domain in Needs you, Open no-go
    entry). The safety blocks from #9: no mail server for the domain; more than 2 links, an attachment or a link
    shortener; near-identical to a colleague's draft at the same company; person on the no-go or said-no list;
    company closed as no sponsorship.
  - **Warnings** rows (amber tint, triangle icon). The style warns from #9: banned phrases (each one named and
    underlined in the editor), em or en dashes (positions marked), length outside 80 to 120 (about 90 for
    recruiters), hook older than 30 days, subject wording (missing, "Re:" on a first email, not unique to the
    person). Plus the **recruiter warning**: "Recruiters go out on day 2, after engineers and managers. They do
    not count toward Mintlify's 3 a day, but they do count toward today's 30." with a role kind select to
    override (Recruiter, Engineer, Manager, Founder). Changing it saves on the person and reruns checks.
  - **Passed**: one collapsed line "9 checks passed" that expands to the list.
- Editor: subject field, body textarea (plain text, monospace off, 22 px line height), and a meter row with live
  word count, link count, "Plain text", and who saved last ("Saved by Claude 9:58", "Saved by you 10:03"). Live
  counts are display only; the stored check results come from the API after save.
- Footer (sticky): Approve (A) primary, disabled when anything blocks, with a tooltip naming the block; Save (⌘S);
  Skip (S); on the right, Pause company (P) and Close company (C).

The sample drafts in `outbox.html` follow the merged cold-email skill (`.claude/skills/cold-email/SKILL.md`) and pass
`mail/check_draft.py`: bare links on their own lines (portfolio first, then one project), the work-authorization
line from `mail/me.md` word for word for the recruiter, lowercase subjects of 4 to 7 words, and the signature
"Devanshu Chicholikar". Kenji Watanabe's draft passes on its own and is blocked only because Ava Moreno's is a 92%
copy, which is the real checker's result.

#### Detail: reply

- Green alert: "Browserbase is on hold. Priya replied, so nothing else goes to Browserbase: Lena Szabo
  (approved) and Tom Becker (follow-up 1, due today) are held. Answer her in Gmail. This app never sends a reply."
  with Open in Gmail.
- Their message first, then a segmented control "What did she say?": Interested, Not now, Said no, Asked to
  stop. Said no and Asked to stop put the person on the no-go list, and Asked to stop also counts against today
  being clean (it is a stop signal in #9). The sender's auto-reply filter keeps out-of-office mail out of this
  group; if one slips in, a fifth choice "Auto-reply, not a person" releases the hold.
- The thread below, oldest folded.
- Footer: Close company with a reason (C), Resume company. Held emails stay held until he does one of these.

#### Detail: sent, sending, checking

Read-only thread, the **follow-up track** (four steps: First email, Follow-up 1, 2, 3 with sent or due dates;
the third is labelled "polite close"), and the state line. Sending and Checking show no actions at all. Approved
shows Withdraw approval (U) until the sender claims it.

#### Outbox states

- Loading: strip cells as skeletons, list skeleton rows at real row height, detail empty.
- Empty Outbox: "No drafts to review. Paste people for a picked company in the side terminal. Claude's drafts show
  up here."
- Gmail signed out: red strip cell plus an alert above the list naming the fix. Approve still works (approval is
  not sending); approved mail waits.
- Hard block: red strip cell, alert with Gmail's message, Resume behind a dialog.
- New items while he works: a "2 new" pill at the top of the group; click or ⇧J to reveal. Nothing moves under the
  cursor.
- Edit conflict (another tab saved, or Claude saved a new version): the shared conflict alert above the editor,
  his text kept, choices Load newer or Save mine over it.

#### Outbox actions

| Action | Confirmation | Undo |
|---|---|---|
| Approve | none (warnings are visible above the button; reading them is the confirmation) | Z or U, until the sender claims it |
| Save | none | none (history keeps both versions) |
| Skip | none | Z |
| Withdraw approval | none | approve again |
| Set reply kind | none | change it |
| Resume company | Dialog listing what will send again | pause again |
| Close company | Dialog with reason: Referral, Interview, No sponsorship (adds to no-go), No referrals, Already hired, Not now, Other | Z for 8 seconds; after that, reopen from the company page |
| Pause, resume sending | Pause: none. Resume after a hard block: dialog | toggle |

### 5.4 Needs you

**Purpose.** Every review item from #9 and #16 with its reason and a one-click fix. A fix is locked so automation
never overwrites it. The page has a finish line: when nothing is open, it says so and shows what he settled.

**Layout.** Under the title, one progress line in grey: "2 settled today, 3 open". With nothing settled yet it reads
"5 open" (no zero shown as a score); when nothing is open the line goes away and the settled panel says it instead.
Then sections by what the item holds up, always in this order, an empty one left out: "Holding up email" (any item
for a company he is emailing whose kind can stop a draft: missing domain, unknown or doubtful size), "Sponsorship"
(unclear wording, postings that disagree), "Company details" (size and domain items that hold nothing up yet),
"Job boards" (unreadable, zero jobs, careers page unreadable), "Possible duplicates" (merge questions), "Other"
(the reader disagrees, a doubtful board match, Claude's suggestions, a changed careers page). Below, "Resolved this
week", collapsed.

**Elements.** Each item: icon, company, trigger badge, context badge (Picked today, Claude suggests), age ("Opened
Mon Nov 9", or an amber "Waiting 11 days" once older than 7 days), one plain sentence on what is wrong and what it
affects, the evidence with its source link (a quoted posting or profile line; a run strip of the last reads, green
ok, a filled amber square for an error, a hollow amber square for an empty read, with the run in words beside it),
and the actions on the right. Edit opens an inline
form in the row: the field (domain, size, sponsorship Yes, OPT only, Unclear or No, board URL), a Source field
(required), Cancel, and Save and lock. While the form is open, Mark ok is hidden; Mark no-go stays. Sponsorship No
opens the no-go dialog, since it is the same outcome. A size takes a number or a band ("34", "about 45", "11 to 50").

Trigger badges are amber, the "look at it, nothing is stopped" color of section 8, and so is everything else on the
page: nothing here is red (Devanshu, 2026-10-25). A missing email domain under "Holding up email" is amber too; its
sentence says no draft for that company can be approved until it is fixed, and the section puts it first. A waiting
item never turns red, however long it waits.

- **Merge row** (a `merge_candidate`). The head names both companies ("Mesh Systems and Mesh"), the reason sentence
  says what matched and what did not. Below, the two companies side by side: a column each, headed by the name and
  its status ("Working since Nov 4", "Backlog"), the suggested one with a grey "Suggested" badge. Rows: Domain,
  Size, Stage, Boards, Open postings, People, Touches. A row whose values differ ends in a "≠ Differs" mark and
  shows its values in medium weight; a row that matches shows its values in grey. Color is never the signal. One
  line under the table says why the suggestion ("your 6 touches are there"), what merging does, and that it can be
  undone from Resolved. Actions: "Merge into Mesh Systems" (M, the suggested one, outline), "Merge into Mesh"
  (no key), "Keep separate" (O). No Mark no-go.
- **A person suggestion from Claude** (`flagged`, key `claude-no-go:`) shows the person, the quoted reason and
  "Nothing changes until you choose. Mark no-go adds him, not the company." Its dialog lists person reasons.
- **Resolved this week**: company, the kind as a grey badge, what he did in words, when, and on the right either
  **Undo** (an ok, a no-go or a merge; a tooltip says what Undo does) or a lock with "Edited, locked" (a value he
  set). An edited row's text ends with how to change it: "To change it, set it again on the company page." Rows
  are grouped Today and Earlier this week.
- **Settled panel** ("All settled."), shown in place of the list when nothing is open and he settled something
  today: a grey check, the heading (the focus target), "You settled 5 today.", one line per item settled today
  (company, what he did, "Locked" for an edit, the time), "Boards are read again Thu Nov 12 at 08:00. New items
  appear after each board read and when you pick a company." and the next place to go: "Open the Outbox" (G O)
  with "7 drafts wait for review there." when the shell has drafts.

**States.** Settled: the panel above. Empty with nothing settled today: "Nothing needs you. New items appear after
each board read and when you pick a company." Stale: if the last board read is older than its schedule, the shared
stale banner sits on top, because board items depend on it.

**Actions.**

| Action | Confirmation | Undo |
|---|---|---|
| Mark ok | none | Z for 8 seconds, then Undo in Resolved |
| Edit field, Save and lock | none (the source is required) | Z for 8 seconds; after that it stays locked: set it again on the company page |
| Mark no-go | Dialog with a reason and a source | Z, then Undo in Resolved (also takes it off the no-go list, unless it was there before) |
| Sponsorship set to No | same dialog as Mark no-go (it is the same outcome) | same |
| Merge into either company | none (the comparison is the confirmation) | Z, then Undo in Resolved (makes two companies again) |
| Keep separate | none | Z, then Undo in Resolved |

A resolved item returns only when its evidence changes, and then it says what changed ("Reopened: LinkedIn now
shows 51 to 200, you set about 45 on Nov 6").

### 5.5 No-go list

**Purpose.** One place that answers "why is this company or person not showing up", with the source of each
reason.

**Layout.** Tabs Companies and People, reason filter chips (`ToggleGroup`), filter field, a table.

**Companies columns.** Company, reason badge, evidence (quote or value), source (link to the posting, the reply,
"You, from Today", or an Imported tag with the import source), added date, a row menu. Reasons: Does not sponsor,
Citizenship or clearance, Over 300 people (with evidence), Removed by you, Marked no-go by you. Posting-level hides
(2027 graduation or current students only) are not listed here because the company stays eligible; a note under
the table says so.

**People columns.** Person and company, reason (Asked to stop, Said no, Marked by you, Flagged by Claude), what
they said, source, added.

**Actions.** Take off the list: dialog explaining the effect ("Taking it off locks your choice: the rules will not
add it back for this posting, and a new posting that says the same raises a review item instead") with a required
reason. Undo with Z.

### 5.6 Company page

**Purpose.** Everything known about one company, including everything imported, in one place.

**Layout.** Header with name, status badges and actions; a facts strip; tabs.

- Header: name, status badges (Emailing, Day 2 of 3, Held: reply, Paused, Backlog, Done, No-go), one sentence on
  where it stands. Actions: Resume or Pause, Close with a reason, a More menu (Pick for today, Send to backlog,
  Mark no-go, Reopen when done or closed, Open careers page).
- Facts strip: Size (value, confidence meter, source), Stage, Sponsorship badge and source, Email domain (lock
  icon when set by hand, mail server state), Job board (last read result and time).
- Tabs:
  - **Overview:** "Where it stands" (the last 8 events in words), role to name card, people summary.
  - **Roles:** every posting, open and closed, never deleted: title, level badge, where, sponsorship stance,
    first seen, last seen, state (Open, Closed with date and reason, Out of lane, Hidden with the rule). A note
    with the ruleset version.
  - **People:** name, email, kind, channel (Email or LinkedIn), state, last touch, link to the Outbox or the
    LinkedIn list.
  - **Emails:** one block per touch: the sequence with each message, follow-ups, replies, bounces.
  - **Evidence:** fact, value, source, quote, confidence, Locked badge for hand values. Hand values win.
  - **History:** every `events` row, newest first, each with a source tag: You, Claude, Sender, Crawler, Rules,
    and for imports Old dashboard, Browser stages, Job Radar, Old devboard, contacted.txt, DevVault notes.

**States.** Loading skeleton per tab; each tab loads on its own, so one failing tab shows the partial state while
the others work.

### 5.7 Done companies

**Purpose.** The wins, and the metric.

**Elements.** A metric row: Replies this month with an 8-week bar chart (one series, monochrome, hover tooltip per
bar, the current week in solid foreground), Reply rate (replies over first emails this month), Referrals,
Interviews. Then a table: company, outcome badge (Interviewing, Interviewed, Referral), how it started, role,
closed date, note. Imported companies show an Imported tag and blank fields where the old data has none.

**Actions.** Open the company; Reopen lives on the company page (dialog: it returns to the backlog with history).

**Empty.** "Nothing done yet. A company lands here when you close it with Referral or Interview."

## 6. Interaction rules

- **Acting on a row never moves it under the cursor.** Approve relabels the row "Approved" in place and moves
  selection to the next draft; the row joins its new group when the list next refreshes after the cursor has left
  it. Same for Today picks and LinkedIn logs.
- **Polling, not pushing.** The Outbox polls every 15 seconds while visible (sending status and new drafts),
  other views every 60 seconds and on focus. The top bar shows "Updated 10:14". New rows arrive behind a "N new"
  pill.
- **Undo** is a Sonner toast with an Undo button and Z, for 8 seconds, for every reversible action. Toast text
  repeats the action's verb: Approve gives "Approved ...", Skip gives "Skipped ...".
- **Dialogs** only for actions that are hard to reverse or change other things: remove, close company, mark
  no-go, take off no-go, resume company, resume sending after a hard block. Destructive confirms use
  `AlertDialog` with the destructive button on the right.
- **Edits carry a revision** (#11). A 409 shows the conflict alert; nothing is silently overwritten.
- **Every write is idempotent** (#11): the UI sends a request id, so a double press or a retry after a timeout
  does one thing.
- **Motion:** overlays fade and scale in 120 ms; the sender dot pulses; nothing else animates. With reduced
  motion, the pulse and all transitions are off.
- Large lists (Sent, History, Backlog) are virtualized (TanStack Virtual) past 200 rows.

## 7. Shared states

| State | Looks like | Copy pattern |
|---|---|---|
| Loading | skeleton rows and blocks at final size | none |
| Empty | icon, one bold line, one line saying what to do next | "No drafts to review. Paste people ... " |
| Error (API unreachable) | red alert above the content, last loaded data kept and marked | "Cannot reach the outreach server. Nothing was changed. Showing what loaded at 10:14." |
| Stale | amber banner when a `job_run` is late or failed | "Boards last read 6 days ago. Thursday's read did not run (the Mac was asleep)." |
| Partial | one section shows a warning row with Retry; the rest works | "Evidence did not load for this company." |
| Conflict | amber alert above the editor | "This draft changed in another tab. Your edit is kept below." |
| Hard block | red strip cell plus alert | "Sending paused: Gmail said the daily limit was reached." |

## 8. Status vocabulary and colors

One word per state, the same everywhere. Color is never the only signal: every badge has a word, most have a dot
or icon.

| Color | Meaning | Words that use it |
|---|---|---|
| Green | a person answered, or a win | Replied, Held: reply, Interested, Interviewing, Interviewed, Referral, Has sponsored, Says it sponsors, Mail server found, Open (posting), ok (board) |
| Blue | the system is working on it | Approved, Sending, Checking, Emailing, Picked |
| Amber | look at it, nothing is stopped | N warnings, Recruiter, Unknown, Guess, Unclear, Starts 2027, Waiting N days, Empty (board) |
| Red | stopped until fixed | Blocked, Bounced, Failed, Does not sponsor, Citizenship or clearance, Asked to stop, Said no, Error (board), Paused: hard block |
| Grey | waiting or finished, no action | Draft, Sent, Follow-up N of 3, Waiting, Paused (by you), Held (a held touch), Skipped, Closed, Backlog, Removed by you, Over 300 people, Not stated, Out of lane |

Posting states: Open, Closed (date and reason), Out of lane, Hidden (rule). Company states: Proposed, Picked,
Emailing, Held: reply, Paused, Backlog, Removed, Done, No-go, Closed (reason). Touch states: Draft, Blocked,
Approved, Sending, Checking, Sent, Follow-up N of 3, Replied, Bounced, Failed, Held, Paused, Skipped, Closed.

## 9. Component inventory

| Need | shadcn/ui component | Notes |
|---|---|---|
| App shell | `Sidebar`, `SidebarInset`, `SidebarTrigger`, `Breadcrumb` | sidebar-07 style, collapsible to icons |
| Search and commands | `CommandDialog` (cmdk) | client-side index plus debounced posting search |
| Tables | `Table` with TanStack Table (the DataTable pattern), TanStack Virtual for long lists | group rows, active row, hover actions |
| Status | `Badge` with cva variants `ok`, `warn`, `bad`, `info`, `idle` | wrapped as custom `StatusBadge` |
| Buttons and keys | `Button`, `Kbd` | every action button shows its key |
| Tabs | `Tabs` | URL-synced |
| Panels | `Card`, `Separator`, `ScrollArea` | |
| Outbox split | `ResizablePanelGroup` | list width remembered |
| Groups | `Collapsible` | Outbox groups, Resolved this week |
| Messages | `Alert` | error, stale, hold, conflict, hard block |
| Confirmations | `Dialog`, `AlertDialog`, `RadioGroup` | remove, close, no-go |
| Hints | `Tooltip`, `HoverCard` | blocked Approve, size guesses, ladder signals |
| Job runs | `Popover` | from the sidebar footer |
| Toasts | `Sonner` | undo on every reversible action |
| Loading | `Skeleton` | final-size placeholders |
| Forms | `Input`, `Textarea`, `Select`, `Checkbox`, `ToggleGroup` | reason chips, reply kind |
| People | `Avatar` (initials only) | no photos |
| Row menus | `DropdownMenu` | No-go, company More |

Custom components (built from the primitives above, nothing new in look):

`RampLadder`, `SendingStrip`, `CheckList` (blocking, warnings, passed), `DraftEditor` (subject, body, live
meter), `FollowupTrack`, `ConfidenceMeter` (three bars), `RunStrip` (last board reads), `LinkedInSteps`,
`EvidenceList`, `Timeline`, `EmptyState`, `StateBanner`, `ShortcutSheet` with a `useHotkeys` hook (single keys
off in text fields, `G` chords, per-view maps registered by each route).

## 10. Visual system

shadcn/ui new-york style, zinc base, as `mockup.css` sets it. Font: Inter, self-hosted through
`@fontsource-variable/inter` in the build (no network at runtime), `cv11` and `ss01` on, tabular figures for every
number.

### Color tokens

| Token | Light | Dark |
|---|---|---|
| background | #ffffff | #09090b |
| foreground | #09090b | #fafafa |
| card | #ffffff | #0c0c0e |
| muted | #f4f4f5 | #27272a |
| muted-foreground | #6b6b75 | #a1a1aa |
| subtle (table heads, group rows) | #fafafa | #111113 |
| border, input | #e4e4e7 | #27272a, #3f3f46 |
| ring | #a1a1aa | #71717a |
| primary, primary-foreground | #18181b, #fafafa | #fafafa, #18181b |
| destructive | #dc2626 | #ef4444 |
| sidebar | #fafafa | #0c0c0e |

Status tokens (text, soft background, border, dot):

| Status | Light | Dark |
|---|---|---|
| ok | #15803d, #f0fdf4, #bbf7d0, #16a34a | #4ade80, green at 12%, green at 30%, #22c55e |
| warn | #a16207, #fefce8, #fde68a, #eab308 | #facc15, amber at 12%, amber at 30%, #eab308 |
| bad | #b91c1c, #fef2f2, #fecaca, #ef4444 | #f87171, red at 12%, red at 32%, #ef4444 |
| info | #1d4ed8, #eff6ff, #bfdbfe, #3b82f6 | #60a5fa, blue at 13%, blue at 32%, #3b82f6 |
| idle | #52525b, #f4f4f5, #e4e4e7, #a1a1aa | #d4d4d8, #27272a, #3f3f46, #71717a |

Muted foreground is one step darker than shadcn's zinc-500 (#71717a) so that secondary text passes 4.5:1 on the
active-row grey.

### Type scale

12 / 16 (meta, badges, table sub-lines), 13 / 18 (tables, buttons), 14 / 20 (body, default), 16 / 24 (pane
titles), 20 / 28 (page titles), 24 / 32 (company name, big numbers). Weights 400, 500, 600 only. Sentence case
everywhere; no all-caps labels.

### Spacing, radius, density

4 px grid: 4, 8, 12, 16, 20, 24, 32. Radius 8 px on cards and dialogs, 6 px on buttons and inputs, 6 px on
badges. Table rows 36 px for one line, about 52 px for two. Sidebar 240 px, top bar 48 px, Today preview 380 px,
Outbox list 400 px. Layout is built for a 1440 by 900 laptop; at 1280 it still fits; under 1100 px the sidebar
moves into a `Sheet`.

### Icons

Lucide (shadcn's default), 16 px, stroke 2. Each status has one icon used everywhere: octagon for blocked,
triangle for warnings, check for passed, reply arrow for replies, pause for paused and held, clock for waiting.

## 11. Copy rules

- Plain words, short sentences, sentence case. Name things by what Devanshu does, not by tables ("Log request",
  not "Create linkedin_log").
- No em dashes or en dashes anywhere, in UI or docs. Use a comma, a colon, or two sentences. Times use "to":
  "08:30 to 16:30".
- One verb per action across button, key hint and toast: Approve, Approved. Skip, Skipped. Remove, Removed.
- Status words are the ones in section 8 and match #9: "limit" (not cap or quota), "level", "clean day", "held",
  "follow-up 1 of 3", "role to name", "no-go", "Needs you", "backlog", "remove", "close with a reason", and the
  close reasons exactly as the design doc lists them.
- Errors say what happened, what did not happen, and what to do. They do not apologize.
- Never say Send on a button. Approve says it waits for the sender: "Approved. Sends after 10:55 ET."
- Times are shown in the recipient's zone with its abbreviation when it is about sending (10:42 ET), and in his own
  zone without one otherwise.
- Progress is plain counts in grey: "2 settled today, 3 open". No zero as a score ("5 open", not "0 settled today, 5
  open"), no percentages, no bars that empty out.
- Never "streak", "missed", "overdue", "behind" or "keep it up", and no praise ("Great job"). A finish line states the
  fact: "All settled." A waiting item says how long, in amber at most: "Waiting 11 days".
- A settled item says what was done, by whom and from where: "Size set to 34 by you, from the LinkedIn company
  page". A row that cannot be undone says so and how to change it: "Edited, locked" and "To change it, set it again
  on the company page."
- Undo's toast says what came back: "Undone. Ambergate's sponsorship question is open again."
- A reason names both sides when it is a choice: "Mark no-go if the reader is right; mark ok to keep the rules'
  reading." A suggestion from Claude says nothing happens until he chooses.

## 12. Accessibility

- **Contrast** (computed, WCAG 2.1): body text 19.9:1; secondary text 5.3:1 on white, 4.8:1 on the active-row
  grey; status text on its soft background: ok 4.8, warn 4.8, bad 5.9, info 6.2, idle 7.0. In dark mode every
  pair is 5.8:1 or higher. Primary button text 17:1, destructive 4.8:1. Needs you additions (light, dark): progress
  line 5.3, 7.8; merge head name on the table head grey 19.1, 18.1, its status line 5.1, 7.4; grey fact labels and
  matching values 5.3, 7.6 (5.0, 6.7 on row hover); "Differs" and differing values 19.9, 18.7; hints in the active
  row 4.8, 6.7; amber badges 4.8, 10.6 (8.9 in the active row). One shared style is under 4.5: in dark mode a tab's
  count on the selected tab is 4.1:1 (`.tab .count` on `--input`), on every page that has tabs.
- **Focus order:** sidebar, top bar, then the view in reading order: strip, list, detail, footer actions. In the
  Outbox, Tab from the list goes to the detail's first control; Esc from the editor returns to the list row.
  Focus is always visible (2 px ring). Dialogs trap focus and return it on close.
- **Lists** are `role="listbox"` or tables with `aria-selected` on the active row; J/K moves selection and focus
  together; the selection change is announced through a polite live region ("Kenji Watanabe, Trigger.dev,
  blocked").
- **Toasts** live in a polite region; undo is reachable by key (Z) and by Tab.
- **Color is never alone:** every status has a word; run strips and the ladder have text alternatives.
- **Reduced motion:** the pulse and all transitions turn off.
- Hit targets are at least 28 px high; row actions appear on focus as well as hover.

## 13. Data each view needs

This is the contract for #4 (store) and #11 (API). Entity names follow design section 4. "Derived" means the API
computes it from stored rows (the UI never does). Each editable object includes `revision`; each write accepts a
`request_id`. Times are UTC in the API; the API also returns the recipient time zone where sending is involved.

### 13.1 Shell (every page)

- Counts: `daily_list` picked today and `capacity_today` (derived); unhandled replies (touches in replied whose
  company is not closed or resumed); drafts to review (touches in drafted, followup_ready, failed); LinkedIn to
  reach (persons routed to LinkedIn with no `linkedin_log` row); open `review_item` count; no-go companies plus
  no-go persons; done companies.
- Sending summary: `sending.state`, `sent_today`, `limit_today`.
- Replies this month (count of first human replies, by `events`).
- Health: latest `job_run` per kind: kind, started_at, ended_at, result, counts, error, `expected_next_at`
  (derived, for staleness).
- Recent companies: kept in the browser (a UI preference, not data).
- Search index: for every company: id, name, aliases, domain, status, `status_line` (derived, for example "Held:
  reply from Priya Natarajan"); for every person: id, company_id, name, title, email, status line. Posting search:
  `GET` with a query returning posting id, title, company id and name, open or closed.
- Actions: pause sending, resume sending (with `acknowledge_block` when resuming after a hard block).

### 13.2 Today

Read:

- `daily_list` for today: date, built_at, `capacity_today` (derived from the ramp: 5 early, 10 once ramped),
  picked count, entries in API order, each with `group` (`level_role` or `any_us_role`), rank, state (proposed,
  picked, backlog, removed).
- Per entry, company: id, name, domain, HQ city, status.
- Role to name (derived by the rules): posting id, title, url, location, first seen (age), level tier (new grad,
  early career, SWE I, SWE II, or none), years asked, start year (for "Starts 2027").
- Evidence per company for size, stage, sponsorship, domain: value, confidence (1 to 3), source, quote, observed
  at, locked, and `is_guess` for size. Sponsorship shown as one of: has sponsored, says it sponsors, not stated,
  unclear (derived from decisions and evidence).
- `why` (derived): one sentence built by the API from the same facts.
- Other open postings: title, age.
- People count and drafts count per company (from person and touch), for the preview.
- Still emailing: companies picked on earlier days and not closed: day N of 3 (derived), emailed count of budget,
  `next_step` (derived text plus a kind: review drafts, fix review item, answer reply, nothing), company state
  (emailing, held, paused, waiting).
- Backlog: same entry shape, with the date first proposed.

Actions: pick, unpick (refused past capacity with a clear reason), backlog, remove (reason enum plus note; writes
company status removed and an `events` row), copy handoff (UI only; needs company id and role to name).

### 13.3 LinkedIn list

Read, per company with LinkedIn people: id, name, hold state, role to name (title), emailed count, LinkedIn count.
Per person: id, name, title, role kind, linkedin_url, `linkedin_reason` (past the budget, no email, alum first),
and the latest `linkedin_log` steps with dates: requested_at, accepted_at, messaged_at, replied_at, reply_kind.
Tab counts: to reach, waiting, replied, all. Requests this week (count of `linkedin_log` requests since Monday).

Actions: log step (`request`, `accept`, `message`, `reply` with reply_kind and optional text), undo a log (delete
the last step by id, written as an event), set role kind.

### 13.4 Outbox

Sending status:

- `sending.state`: running, paused_by_you, paused_hard_block, outside_window, weekend, signed_out; `paused_reason`
  and `paused_at`; for a hard block, Gmail's message.
- `sent_today`, `limit_today`, `ramp_level` (1 to 9), `ramp_levels` ([20, 30, ... 100]), `next_limit_if_clean`,
  last step-down (at, reason).
- Clean-day signals: bounce rate over the last 3 days, Gmail limit or policy errors today, spam or stop replies
  today.
- Window (08:30 to 16:30) and weekdays.
- `next_send`: at, recipient time zone, touch id, person name, company name; or `none_reason`.

List, per touch:

- touch: id, state (drafted, approved, sending, send_unknown, sent, followup_ready, followup_approved,
  followup_sent, replied, bounced, failed, skipped, closed), `step` (first email, follow-up 1, 2, 3), revision,
  updated_at, `last_saved_by` (Claude or you).
- person: id, name, title, role kind, email, linkedin_url; company: id, name, paused, held.
- message for the current step: subject, body, saved_at.
- `group` (derived, matching section 5.3), `sort_key` (derived).
- `checks` (stored by the API on save): list of { code, severity (block, warn, pass), title, detail, `fix`
  (where the fix lives: an action, a related touch id for near-identical, a review item id for a missing domain,
  a no-go entry id), `spans` (character ranges in subject or body for banned phrases and dashes) }.
- `schedule` (derived): earliest_send_at, recipient time zone, reason (per-company limit, 60-minute spacing,
  recruiter day 2, window, daily limit), company sent today and slots left.
- Hook used (text, date), project used, role to name (title, age, place), saved by Claude through `save_draft`.
- For sent touches: sent_at per step, follow-up due dates for every remaining step (3, 7 and 15 business days),
  bounce reason.
- For replied: reply message (from, received_at, text), `reply_kind`, the held touches at that company (id,
  person, state, step, due).
- Thread: every message of the touch in order (subject, body, sent_at or received_at, direction).

Actions:

- approve (touch id, revision): refused with the blocking checks if any block; returns the schedule.
- unapprove (only before claim), save (subject, body, revision; reruns checks, returns them), skip.
- set reply kind (interested, not now, said no, asked to stop, auto-reply). Said no and asked to stop add the
  person to no-go; asked to stop feeds the clean-day signal; auto-reply releases the hold.
- set person role kind (overrides the recruiter warning; reruns checks).
- pause company, resume company (returns what would send again), close company (reason enum, note).
- compare drafts: the two bodies and the similarity score with matching spans.
- pause sending, resume sending.

### 13.5 Needs you

Read, per `review_item`: id, trigger (board_unreadable, zero_jobs, unclear_sponsorship, unknown_size,
missing_domain), company id and name, `holds_email` (derived: true for missing domain and unknown size on a picked
company), opened_at, age in days, reopened_from (previous resolution and what changed), evidence: for sponsorship,
the quote, posting id, title, url, first seen; for boards, the last N reads from `board` and `job_run` (at, result
ok, empty or error, HTTP status or error text), last ok read, open posting count; for size, the evidence rows.
Resolved this week: item, resolution (ok, edited with value, no-go, merged), by, at, locked, revision, `canUndo`.
For a merge item, `merge`: both companies (name, status, domain, size, stage, boards, open postings, people,
touches) and `suggestedKeep`. `settledToday` for the progress line and the settled panel, and the next board read
time (from the shell's health).

Actions: mark ok, edit field (field: domain, size, sponsorship, board url; value; source required), mark no-go
(reason, source), merge (keep a or b), reopen (Undo from Resolved, for ok, no-go and merge). Each resolution locks the value and writes an `events` row; the item stays resolved unless its
evidence changes.

### 13.6 No-go list

Companies: id, name, reason (does_not_sponsor, citizenship_or_clearance, over_300, removed_by_you, marked_by_you),
evidence (quote or value), source (posting id and url, touch id of a reply, "you" with the view it came from, or
import source), added_at, added_by (rules, you, Claude, import), locked. Counts per reason.

People: id, name, title, company, reason (asked_to_stop, said_no, marked_by_you, flagged_by_claude), what they said
(quote), source (touch id or text), added_at.

Actions: take off the list (company or person, reason required; locks the choice), open source.

### 13.7 Company page

- company: id, name, domain, aliases, HQ, status, paused (reason, at), held (by touch id), picked_on, day N of 3,
  closed (reason, note, at), revision.
- evidence: every row (kind, value, source, quote, confidence, observed_at, locked).
- board: ats, token, url, last result, last read at, error streak, empty streak.
- postings: every row, open and closed: id, title, location, url, first seen, last seen, closed at and reason,
  pay; with its decision for the current ruleset (in lane, level tier, US, sponsorship stance, citizenship, years
  asked, hidden and which rule); ruleset version.
- role to name (derived).
- persons with their touch summary (state, last touch at, channel) and `linkedin_log` summary.
- touches with full threads (as in 13.4).
- events: every row (at, kind, text, actor: you, Claude, sender, crawler, rules, import, and import source).
- review items for this company (open and resolved).

Actions: pause, resume, close (reason, note), reopen, pick for today, send to backlog, mark no-go, edit evidence
(value and source, locks it).

### 13.8 Done companies

- Companies with status done: id, name, outcome (interviewing, interviewed, referral), closed_at, how it started
  (first touch or LinkedIn step, or the import source), role (posting title if recorded), note.
- Metrics (derived): replies this month, replies per week for the last 8 weeks, reply rate this month (replies
  over first emails sent), referrals, interviews.

Actions: open company; reopen lives on the company page.

## 14. Open questions for Devanshu

Devanshu has not answered these yet. Each one carries a working default that phase 1 builds; changing a default later is a
small, local change.

1. **Should Claude's `mark_no_go` (#13) take effect at once, or wait for you?** #9 says Claude can "flag" and
   only you approve. Default until Devanshu decides: Claude's no-go creates a Needs you item ("Claude suggests no-go: profile says
   no cold outreach") that you confirm with one key. The People tab shows it as "Flagged by Claude" only after you
   confirm.
2. **When does Today's capacity move from 5 to 10 companies?** #9 says "once the send limit has ramped up"
   without a number. Default until Devanshu decides: 10 once the limit reaches 60 (level 5), since 10 companies at up to 3 a day
   plus follow-ups needs about that many.
3. **Can a removed company come back?** #15 says remove is permanent. Default until Devanshu decides: never proposed again
   automatically, but you can take it off the No-go list by hand (with a reason), because a mis-click on R should
   not be forever.
4. **Should you tag every reply (Interested, Not now, Said no, Asked to stop)?** It costs one click and gives
   replies per month by kind, and "Asked to stop" feeds the clean-day rule. Default until Devanshu decides: yes, with Interested
   preselected so it is usually zero clicks.
5. **Light or dark by default?** Default until Devanshu decides: follow the Mac's setting, toggle with the palette or the
   sidebar button.

## 15. Where the issues disagree with #9, or leave gaps

- **Reply stops or holds.** #9: "Any reply from anyone at the company stops all queued email there." #14: a
  human reply "holds" every queued email. This design follows #9's effect (nothing more sends on its own) and #14's
  word: queued emails are held until Devanshu resumes or closes the company. #9 should say whether resume is
  allowed.
- **Bounce threshold.** #9 defines a clean day as bounces under 2% over 3 days and says a warning steps down while
  a hard block pauses. #14 lists "bounce rate over 3%" as a stop condition. The UI can show either, but #9 should
  say whether over 3% is a step-down or a pause.
- **Claude marking no-go.** #13 gives Claude a `mark_no_go` tool; #9 lets Claude read, add people, save drafts
  and flag. See open question 1.
- **Remove is permanent** (#15) versus the No-go list letting him undo it. See open question 3.
- **Gaps in #11's endpoint list** that these views need: pause and resume sending (global), search (index and
  posting query), take off no-go, set reply kind, set person role kind (recruiter override), compare drafts,
  reopen a done or closed company, undo a LinkedIn log, and job run health detail.
- **Gaps in design section 4** for #4: company needs `paused` (with reason and time) and `held` (derived from
  touches) beside its status; person needs `linkedin_reason`; touch needs `reply_kind` and the stored check
  results with spans; `daily_list` needs `group`, rank and capacity per day.

## 16. Web experience (#33): Today as a plan, Finish, Progress, Wins, Score

Status: mockups for Devanshu's approval. They gate #33 Tasks 7 to 16. Plan:
`docs/superpowers/plans/2026-10-21-web-experience.md`. The bar for every view is the design spec's section 16 (how it
should feel). Each mockup has a "Mockup state" switch in its top bar; it is not part of the product.

What changes in the shell: the sidebar Today count reads people done of today's plan (`2 of 7`); a Results group
holds Progress and Wins; Wins shows a green count with a dot while a win is unseen (`Wins, 1 new`); the footer's
replies count is this month's replies, the same number Progress shows.

### 16.1 Today as a plan (replaces 5.1)

**Purpose.** Make today's effort clear and quick: about 7 people, one at a time, in order, ending in a finish line.

**Layout.** Under the date and `7 people, 2 done` (with `Requests this month 52 of 150` on the right), two columns:
the day rail (320 px) and the work panel (text at most 62 characters wide). Below the panel, quiet and collapsed: the
7-day question and the watch list. A win banner, when there is one, sits above everything.

**The day rail** is the one bold element in the app. It is a vertical line of nodes, one per person, ending in a
finish node, because the content really is a sequence. Each option shows its number, the person (or "Find
someone"), the company, and a status word:

| Node | Word | Meaning |
|---|---|---|
| empty circle | To do | nothing logged yet |
| ring | To do | the selected row |
| blue dot (info pair) | Logged | one step logged, one to go |
| filled check | Done | every step logged |
| half-filled circle | Carried over | carried to tomorrow; grey, never red |
| half-filled circle | Not a fit | removed with Not a fit (the company is on the No-go list); stays in place, grey, takes no key |
| dashed circle with a flag | Done for today | the finish node, with `Tomorrow: 7 people`; filled when the day is done |

The line between two done nodes is drawn darker, so the distance travelled shows. A logged row stays where it is,
relabelled; the selection moves to the next row to do. Rows carried from an earlier day say "Carried over from
Monday" in grey under the company. A row whose line Claude only proposed says "Your line to confirm".

**The work panel**, for the selected row, top to bottom: the person's name (24/32), title, kind badge and where they
were found (a link); the lane word on the right (`Role at your level`, `Intern or junior work`, `Reason to reach out`,
`Open US role`); the one reason (16/24) with its sources as links; the role with Open role and Apply page; the note
block; then the actions, with the one next step as the only filled button, and a footer with the step marks
(Requested, Applied, each with the time once logged) and the quiet actions Carry to tomorrow and Not a fit.

**The note block** is the only tinted surface on Today (paper: `subtle`). It holds the note text, its length
against the limit from the API (`168 of 200 characters`), "Why her, in your words" (his reason line), "Facts used"
with each fact's link before any action, the check result, and Copy and Edit. When Claude only proposed the line,
the block asks first: "Is this why you are writing to him? Confirm it or write your own.", with Confirm line (Y)
as the next step and Write my own (E).

**Row kinds.**

- Person with a role: Log request sent (R), then Log applied (P).
- Person without a role: Log request sent only; P says "No role to apply for."
- Find someone (no person yet): three numbered steps (search, hand it to Claude, back here), Open LinkedIn search (S)
  as the next step, Copy handoff (C) with the handoff text shown. No note, no logging key.
- Email row: the company's drafts in the Outbox and Review in Outbox as the next step. The sender and the reply reader
  log email outcomes, so no logging key acts on it.
- Done: the step marks with their times and "To change a log, open Resend's page."

**Below the plan.** "Last week, what happened?" with a grey count asks one question at a time for the oldest touch
with no outcome ("What happened with Kai Brennan? You sent a request on Nov 2 for Software Engineer, Platform at
Railway."): Accepted (1), Replied (2, then the reply kind), Nothing yet (3, a normal answer that asks again in 7
days). The watch list (grey count) shows careers pages that changed, new companies to reach and possible duplicates,
each with its reason, date, one link and Dismiss.

**States.** First load lands on the first person to do, ready (Lena Ortiz in the fixtures). The weekend, a plan not
built yet and a failed list job use the shared empty and stale states (#33 Task 8 copy). The phase-1 picks live on at
Proposals.

**Actions.**

| Action | Key | Toast | Undo |
|---|---|---|---|
| Log request sent | R | Logged: request sent to Lena Ortiz. | Z, 8 seconds |
| Log applied | P | Logged: applied for Software Engineer I at Pylon. | Z |
| Carry to tomorrow | X | Carried to tomorrow: Inngest. | Z |
| Not a fit | N | the phase-1 remove dialog: Removed Pylon. Find it on the No-go list. | None: a dialog action, it commits at once (phase 1). The row stays in place, reads Not a fit, and takes no key. |
| Confirm line | Y | Confirmed your line. | Z |
| Copy the note, the handoff | C, ⇧C | Copied the note. / Copied 1 handoff for Claude. | |
| Answer the question | 1, 2, 3 | Logged: Kai Brennan accepted. | Z (Said no and Asked to stop log at once) |
| Mark wins seen | W | Marked seen. | Z |

A key on a step already logged says "Already logged." and does nothing else. After the 8 seconds a log is corrected
from the touch's history on the company page.

### 16.2 Finish

When no row is left to do, the work panel becomes the finish panel and focus moves to its heading, "Done for today"
(announced once: "All 7 done for today."). The finish node on the rail fills. The panel, on paper: the heading with a
filled flag and one calm line ("All 7 people on today's plan are done. Nothing else is asked of you today."); What you
did (counts, then one line per row with its time); Carried to tomorrow (only when a row was carried); Tomorrow (the
count, when the plan is built, up to 3 people, then "and 4 more"); This week so far (plain counts and a link to
Progress). Nothing red, no counting of days in a row, no "come back".

### 16.3 Progress and the quiet week

**Progress** (`/progress`): Today against the plan (a one-row ledger: people done of the plan, requests,
applications, accepted, replied); then the week or month (tabs, keys 1 and 2) as a ledger, not tiles and not a
funnel graphic. Each stage shows its count, the rate beside its count and denominator (`9 of 31 (29%) of requests`),
and one thin monochrome bar. Beside it, "Your work this week" lists the effort. Below, "What the data says" shows the
month's one or two findings with their counts, a grey caution when a group is small, "Changed: ..." when the plan
changed because of it, and earlier changes collapsed.

The ledger bar (dataviz rules): one series, so no legend; the column head names it ("On one scale"). It grows from one
baseline, square there and rounded 4 px at the data end, 8 px tall, `muted-foreground` on the card (5.3:1 light, 7.6:1
dark). A non-zero count is never under 2 px; zero draws no bar and a grey 0. Every value is also a number in the same
row, so the table is the text equivalent; each bar also has a text alternative and a hover tip. The scale is the
period's largest count, but never less than the minimum sample (30 requests), so a first week of 9 looks small.

Honest numbers: with fewer than 30 requests in the period, rates are dropped and counts stay ("2 of 9"), with "Only 9
requests so far. Rates this small swing a lot, so none are shown." A stage measured against replies shows a percent
only from 20 replies, the same rule findings use. With nothing to measure, the rate says "None yet". No NaN, no "1 in
0".

**The quiet week** (a week with requests and no replies) keeps the page and changes the order: "Your work this week"
first (counts at 20/28), then one paragraph from his own data on paper ("About 1 in 16 requests gets a reply, and
replies often take about 6 days." with "From your own 142 requests since Oct 12: 41 accepted, 9 replies."), then the
week's counts, muted, with zeros in grey. With too few outcomes the paragraph says "Not enough outcomes yet to say what
is usual. It takes about 30 requests." With no replies ever, it is left out. A month that is not quiet keeps the usual
order.

### 16.4 Wins

**The win banner** sits at the top of Today the moment an accept or a reply is known (logged on the 7-day question,
or found by the reply reader): a green check that draws once, the line ("Dana Whitfield accepted your request. 9:41"),
"Open the win log", and Mark seen (W). It enters with one 240 ms fade, the only animated moment in the app, and stays
across polls and reloads until he marks it seen. Reduced motion shows it complete, without the fade or the draw.

**The win log** (`/wins`) keeps referrals, interviews, offers and replies he marked Interested, newest first, grouped by
month. Each entry: the date, the line, the person and role, "What you sent" (collapsed, with the channel and date),
and a green kind badge (Interview, Referral, Replied). A counts line sits on top ("Since Oct 12: 2 interviews, 1
referral, 9 replies, 41 accepted requests."), with a toggle All (1) or Referrals and interviews (2). A win he has not
seen carries "New" and a green edge for this visit; opening the page marks it seen. Empty: "No wins yet. Referrals,
interviews and interested replies land here the moment you log them, with what you sent."

### 16.5 Score tab

The company page gains a seventh tab, Score (key 7, count = signals). Header: "Score 0.74, rank 12 of 1,010" and the
lanes with the strongest named. A ledger, one row per signal in API order: label; a signed bar from the share (right
when it adds, left when it costs, on a centre axis); points with value times weight under them; the share as a percent
of all points moved; the reason sentence and its source as a link. A neutral signal reads "No data, scored neutral" in
place of a bar. Footer: "Ranker v4. Weights live in the ranking config. A change makes a new version." and, when set,
"Was 0.52 under v3."

### 16.6 Keyboard map (#33)

Single keys work only outside text fields, as in section 3.

| View | Key | Action |
|---|---|---|
| Today | J / K, ↓ / ↑ | Next, previous person on the rail |
| Today | Enter | Open the company |
| Today | L | Open profile |
| Today | O | Open role |
| Today | A | Open the apply page |
| Today | S | Open LinkedIn search (find someone rows) |
| Today | C | Copy the note, or the handoff on a find-someone row |
| Today | E | Edit the note |
| Today | Y | Confirm your line |
| Today | R | Log request sent |
| Today | P | Log applied |
| Today | X | Carry to tomorrow |
| Today | N | Not a fit (dialog) |
| Today | 1 / 2 / 3 | Answer the question: Accepted, Replied, Nothing yet |
| Today | W | Mark wins seen |
| Today | ⇧C | Copy every find-someone handoff |
| Today | Z | Undo the last log (8 seconds) |
| Progress | 1 / 2 | Week, Month |
| Wins | 1 / 2 | All, Referrals and interviews |
| Company page | 7 | Score |
| Everywhere | G P, G W | Progress, Wins |

### 16.7 Copy rules for these views

- One verb per action across button, key hint and toast: Log request sent, "Logged: request sent to ...". Carry to
  tomorrow, "Carried to tomorrow: ...". Confirm line, "Confirmed your line.". Mark seen, "Marked seen.".
- No button says Send. The request is sent by him on LinkedIn; the app only logs it.
- Calm: no guilt copy and no counting of days in a row (the words the plan's Global Constraints list never appear, and
  `web/src/copy-rules.test.ts` checks them). Unfinished rows say "Carried over" in grey. "Nothing yet" is a normal
  answer.
- Every number sits beside what it is out of: `2 of 7`, `9 of 31 (29%)`, `52 of 150`. A rate never shows without its
  count, and never under the minimum sample.
- The win line, the reason, the finding text and the base-rate inputs come from the API; the UI words them only as
  given.
- Reasons name their source: "From Pylon blog, Oct 14". A fact in a note always shows its link.

### 16.8 Visual additions and contrast

No new token and no new hue. `mockup.css` gains four families only: the day rail (`.rail-*`, `.work-*`,
`.plan-steps`), the note block (`.note-*`), the ledger and its bars (`.ledger`, `.lbar`, `.sbar`, `.ledger-effort`),
and the win banner (`.win-*`, with the `win-in` and `win-draw` keyframes the global reduced-motion rule turns off).
`mockup.js` gains the Progress and Wins chords, three icons (chart, award, flag), a `data-key` hook so a visible
control answers its key, and `window.mockToast` for the pages' scripts.

Contrast, computed (WCAG 2.1) for every text and background pair on the six pages. Light: body 19.9:1, on the note
paper 19.1:1, on the active rail row 18.1:1, on the win banner 19.0:1; secondary text 5.3:1 on white, 5.1:1 on paper,
4.8:1 on the active row, 5.0:1 on the win banner; Logged 6.7:1 (6.1:1 on the active row); badges ok 4.8, warn 4.8,
info 6.2, idle 7.0; primary button 17.0:1. Dark: every text pair 5.8:1 or higher. Graphics (3:1 needed): bars and
empty nodes 5.3:1 light and 7.6:1 dark on the card, the Logged dot 3.4:1 light and 4.8:1 dark, the win check 4.8:1 light. The lowest text
pair is the existing warn badge at 4.76:1.
