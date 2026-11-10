# First to see: the newest jobs within minutes, and no more picks

Status: approved in chat by Devanshu, 2026-11-09. Nothing here is built. When this lands, #9 gets a new ruling
first (section 10), then each part gets its issue and plan
(`docs/superpowers/plans/2026-11-09-first-to-see-master-plan.md`).

Research: `docs/research/2026-11-09-first-to-see-new-postings.md` (every number below comes from it unless marked).

## 1. Why

Devanshu, 2026-11-09: "I don't want you to choose for me. I will choose the outreach myself, the company, and the
position myself." And: "The main thing is the job board. I want to be the first one to look at those jobs." He
wants Software Engineer 1, early career and new grad roles (software, AI-forward, forward deployed and the like) as
soon as a company posts them, before LinkedIn, Jobright or Simplify show them, so he can reach the company before
anybody else. "Speed matters the most."

Today Knock is slower than the services he wants to beat:

- A board is read at most every 6 hours (`floor_s = 21600`), one board at a time, and a full pass over about
  2,000 boards took about 4 hours (2026-11-02).
- LinkedIn reads ATS feeds every 6 hours and says a free listing can take up to 48 hours. Simplify adds a
  Greenhouse posting a median 3.2 hours after it goes live (measured, p90 7.9 hours). Jobright updates hourly.
- A board checked every 5 minutes sees a posting a median 2.5 minutes after it goes live: about 3 hours before
  Simplify and 6 or more before LinkedIn.

And the Today page chooses companies for him, which he no longer wants.

## 2. What he said (his decisions)

1. **No recommendations.** Today goes away, its top 8 companies with it ("drop it, I don't care"). No daily picks,
   no 08:00 plan, no ranking. He decides what is relevant.
2. **The job board is the product.** He opens Knock and sees the latest openings at the top.
3. **The exact time each one went up.**
4. **Keep the filters:** software roles only (AI and forward deployed roles count, as the rules already say), his
   level (new grad, early career, SWE 1, plus "level not stated"), no internships, no citizenship, clearance or
   ITAR/EAR roles. The #9 level and visa rulings stand as they are.
5. **A button that checks every board now.**
6. **Speed comes first**, before more coverage.
7. **The outreach side stays as it is and stays separate.** He picks the job and the company, does the outreach
   himself (people, drafts through the MCP, LinkedIn), and Knock keeps tracking, scheduling, sending, follow-ups and
   analytics. "As soon as we do outreach, just log it: this company has been done. We have a record of that."
8. **Today's per-company steps go** (pasting people, the saved LinkedIn people search, LinkedIn note editing, the
   terminal line for Claude, Open role): "they don't mean anything to me."
9. **Analytics stay.** The Progress page is unchanged.

## 3. Goals, non-goals, anti-goals

**Goals**

1. A new matching posting on a hot board (4.2) is on his screen within 5 minutes of going live, while the Mac is
   awake.
2. Check now reads every known board (about 2,000 today) in under 2 minutes, and says what is still running.
3. The job list updates itself: new rows appear at the top without a refresh.
4. Every row says when Knock first saw it, to the minute, and the board's own time when that time is trustworthy.
5. Every company he has reached shows as done on its rows, with the date.
6. Nothing on the outreach side changes behavior.

**Non-goals**

- Finding more companies or boards. Coverage work (#160, #161, #167, #47) stays where it is, after this.
- Ranking, fit scores, or any "why this company" text.
- Anything on LinkedIn, and no paid job data. The #9 sources ruling stands (no LinkedIn, Indeed or Glassdoor
  scraping, no JobSpy).
- Researching companies or finding people for him.

**Anti-goals** (a failure even if it works)

- A repost, an edited old job or an old job found late shown as new. A wrong "new" costs him trust in the list.
- Getting Knock blocked by an ATS. One blocked host loses every board on it.
- A list that jumps under his cursor while he reads it.
- Slowing the sender, the API or the Mac. Sending already works.

## 4. How it works

### 4.1 Read many boards at once

Each board read today already asks "anything changed?" first: a conditional GET with the stored ETag on Greenhouse,
Lever, Ashby, Breezy and Teamtailor (304 when nothing changed), and an id fingerprint on the rest. Most reads change
nothing (84 to 97% on ETag hosts, 2026-10-20). The slow part is that boards are read one after another.

So reads run side by side: each ATS gets a few worker threads that do the network part (the list request), each
host keeps its own spacing, and one thread writes what they find to the store, in the order it arrives. All the
outage rules (breaker, held errors, mass empties) stay as they are.

Two fixes ride along:

- **Lever at 1 request a second.** Its robots.txt says `Crawl-delay: 1`; Knock spaces it 0.25 s today.
- **Ashby without an ETag.** Big Ashby boards (Ramp) answer 200 with no ETag, so today they re-ingest every read.
  When there is no ETag, compare the id fingerprint instead.

### 4.2 Three speeds

| Tier | Boards | Checked every |
|---|---|---|
| Hot | Any company he has reached, and every board with a posting at his level or "level not stated" in the last 12 months (about 1,331 today) | 5 min (Lever 10, Workday and Oracle 30 until part 6) |
| Warm | Any board with a US engineering posting in the last 12 months | 60 min |
| Cold | Everything else | today's cadence, 6 hours to 7 days |

A new posting at his level makes a board Hot at its next read. Reaching a company makes its boards Hot at the next
tick. Ashby is never checked faster than once a minute (its CDN serves the same answer for 60 s).

### 4.3 Polite, per host

Starting rates (research 6.3): Greenhouse 20 a second, Ashby 10, Lever 1, Workday 1 a second per tenant host,
BambooHR and the other per-tenant hosts 2 a second per host. The circuit breaker, outage shield, host block item
and `[ats_off]` list stay keyed as they are today. All rates live in `outreach/crawl/config.toml`.

### 4.4 Check now

A button on the job list. One click checks every board, ordered Hot, then Warm, then the rest by time since the last
read. A board read in the last 60 s is skipped. While it runs, one line under the header says what is happening
("Checked 1,240 of 2,013 boards, 3 new") and new rows land in the list as they are found. When it ends: "Checked
2,013 boards in 74 s. 6 new." If Lever is still going, it says so and finishes behind. Clicking again while a check
runs shows the running one.

### 4.5 The clock on each row

Knock's first sighting is the one clock that means the same thing on every board, so it is the row's main time, to
the minute ("first seen 10:42, 3 min ago"). Beside it, the board's own time where it is trustworthy:

- Greenhouse `first_published`: "posted 10:38" (it matched Simplify to the second, 8 of 8).
- Ashby `publishedAt` and Lever `createdAt`: never shown as a posted time (Ashby's resets on a republish, Lever's ran
  12 days to over a year early).
- Boards that give only a date: the date only.

**How fresh a sighting is.** Each new posting stores how long it had been since Knock last read its board well (the
seen gap). A posting found 5 minutes after the last good read went up in those 5 minutes; one found after a 3 day gap
(a cold board, a long sleep, a host outage, a reader turned back on) went up sometime in those 3 days. So:

- **Fresh:** seen gap of 2 hours or less, and not a repost. It sorts by its first sighting, can carry the "new"
  marker, and can raise the Mac alert.
- **Found late:** seen gap over 2 hours. It sorts by its first sighting with an honest tag ("found after a 3 day
  gap") and never gets the "new" marker or an alert.
- **First read of a board:** no gap at all. It sorts by the board's trustworthy time or date, else it goes to a
  "Date unknown" group at the end of the list in every window. Never new, never an alert. This keeps a newly added board from flooding the top.
- **Repost:** same company and title as a posting closed in the last 120 days (the window #155 planned). It sorts by
  its first sighting with a "reposted" tag and the old first-seen date, so a company hiring again still reaches the
  top, but it gets no "new" marker and no alert.
- **Rows from before this change** keep their current date (the posted date, else the first-seen day).

(The seen gap and the 2 hours: Claude's call. The 2 hours is config.)

**What "new" means on the page.** A fresh row first seen after he last opened the job list. The browser keeps that
moment, so no write is needed. The "3 new" button counts the same rows. The 7, 30, 60 and 90 day windows filter on
the time the list sorts by. (Claude's call.)

### 4.6 The page

- `/` opens the job list. `/today` and `/proposals` redirect to it.
- New rows slide in at the top. If he has scrolled down or has a row open, the list does not move: a small "3 new"
  button at the top shows them when he clicks it.
- Sorted newest first by the time in 4.5. Windows, filters, Pass and the keyboard keys stay.
- **Reached companies are marked.** Every row of a company he has reached (an email sent, a LinkedIn request logged,
  or his own mark) shows "reached Nov 9" (the existing `contacted_on`). The existing "not contacted" filter hides
  them.
- **Go ahead becomes Done.** Go ahead meant "add to today's list", which is gone. The same key now means "I've
  reached out to this company": it records his mark, so the company shows as reached even when the outreach happened
  outside Knock. He can undo it. No other check stops it. (Replacing Go ahead with Done: Claude's call, from "just
  log it: this company has been done".)
- A one-line summary of today's outreach from the Progress numbers sits under the header ("Today: 34 sent, 2
  replies"), linking to Progress.
- The Mac alert stays: one grouped notice for fresh matching jobs, each job once, quiet 22:00 to 08:00 New York. The
  gap between notices drops from 15 to 5 minutes so a second job is not held back long (Claude's call, config).

### 4.7 Where it runs

The crawl job runs every 60 seconds instead of every 30 minutes, with a tick budget under a minute, so Hot boards
come due on time. A long tick simply delays the next one; launchd never runs two at once and the crawl lock backs it
up. The tick's other passes keep their own schedules (the slug probe daily, watched pages weekly), and the Simplify
feed poll moves to every 30 minutes.

Check now is a POST on the API that records the request and starts the crawl job at once (`launchctl kickstart`
without `-k`, so a running tick is not killed). The tick sees the request and runs the check-every-board pass; a
request that arrives while a tick runs is picked up when that tick ends. The API itself never sends a request to a
job board. (Every-minute tick and kickstart instead of a long-running watcher: Claude's call; it keeps "every job
starts fresh each run".)

### 4.8 What gets switched off

The 08:00 plan job (`[plan] enabled = false`, printing one line and exiting, like prepare and the judge), Today's
page, the Proposals page, Pipeline's "Email today" bench, and Today's per-company steps. The plan job's code and the daily list data
stay on disk; the web pages and components for Today, Proposals and Email today are deleted, and a revert brings
them back. Touches made from now on carry no daily list context (source `own`, as for any company
he picks himself); the outcome analytics keep working on what they have.

## 5. Constraints

- Python 3.14 and the pinned dependencies. Threads from the standard library; no new HTTP library.
- SQLite, one writer. The worker threads only do network; one thread writes.
- Tests never touch the network or the live store (`tests/__init__.py` scratch settings). Network code gets fake
  clients, as the crawler's tests do now.
- Only the live checkout runs the crawler for real (CLAUDE.md). Claude never calls Check now or any API write route.
- The Mac sleeps. A sleep pauses checks; the first ticks after waking catch up, Hot boards first, and what they find
  is marked found late, not new.

## 6. Order of work

Each part is one issue, one branch, one PR, in this order:

1. Many boards at once, Lever's rate, the Ashby fallback (4.1, 4.3).
2. Three speeds and the every-minute tick (4.2, 4.7).
3. The clock on each row: first seen, posted time, seen gap, reposts (4.5).
4. Check now and the self-updating list (4.4, 4.6).
5. Today goes away, Done replaces Go ahead, the plan job off (4.6, 4.8).
6. Workday and Oracle newest first, so enterprise boards join the 5 minute tier.

## 7. How we know it worked

Measured on the live checkout for one week after part 4:

- Median minutes from a Greenhouse `first_published` to Knock's first sighting, for postings on Hot boards. Target:
  under 5. Compared with Simplify's add time for the same postings.
- Check now wall time. Target: under 2 minutes for every board but the Lever tail.
- Zero blocks: no host's breaker opens from our own rate.
- Zero false "new": no row marked new that was a repost, a late find or a first read.

## 8. Relation to the open job pipeline issues

This design reshapes three open issues of the 2026-11-02 master plan and adds two:

- #158 (parallel reads and tiers) becomes parts 1 and 2, with faster intervals (5 minutes, not 30) and one writer
  thread instead of workers that write.
- #155 (true posted dates and reposts) gives part 3 its repost window and column names (`repost_of`); the rest of
  #155 (date sources, requisition ids, form questions) stays in #155.
- #157 (Workday done right) gives part 6 its Workday delta read; part 6 adds Oracle's newest-first read.
- New: Check now and the self-updating list (part 4); Today goes away (part 5).
- #169 (carry an unfinished own pick to the next day) is moot once Today is gone.

## 9. Left to the builder

- Exact worker counts per ATS within the rates above.
- Which Workday tenants list newest first (part 6, from captured responses).
- The copy and layout of the progress line, the "new" button and the row clock, through the craft gate.
- Whether to test Workable's hourly global XML feed (research section 4) later.

## 10. The #9 ruling to add

**First to see ruling (2026-11-09, from Devanshu):** Knock stops choosing for him. Today and its top 8, the 08:00
plan, the Proposals page and Pipeline's Email today are switched off, and the job list is home. Speed comes first:
Hot boards (companies he has reached, and boards with a role at his level in 12 months) are checked every 5 minutes,
Warm boards every hour, the rest as before, and Check now checks every board on demand. Every row shows Knock's first
sighting to the minute and the board's own time where it is trustworthy; a repost, a late find or a board's first
read is never shown as new. Go ahead becomes Done: his mark that a company has been reached. The level, visa and
tech filters, the outreach side and the analytics are unchanged. This replaces the "Today" lines of the 2026-10-21
changes, the company-first ruling's queue of about 8 companies a day, the job list ruling's "Go ahead puts the
company on today's list", and the 15 minute gap between new job alerts (now 5).
