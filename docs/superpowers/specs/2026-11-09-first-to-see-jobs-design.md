# First to see: every new Greenhouse and Ashby job at one click, and no more picks

Status: approved in chat by Devanshu, 2026-11-09 and 2026-11-10. Nothing here is built. When this lands, #9 gets a
new ruling first (section 10), then each part gets its issue and plan
(`docs/superpowers/plans/2026-11-09-first-to-see-master-plan.md`).

Research: `docs/research/2026-11-09-first-to-see-new-postings.md` (every number below comes from it unless marked).

## 1. Why

Devanshu, 2026-11-09: "I don't want you to choose for me. I will choose the outreach myself, the company, and the
position myself." And: "The main thing is the job board. I want to be the first one to look at those jobs." He
wants Software Engineer 1, early career and new grad roles (software, AI-forward, forward deployed and the like) as
soon as a company posts them, before LinkedIn, Jobright or Simplify show them, so he can reach the company before
anybody else.

2026-11-10: "Greenhouse and Ashby are the most important job boards for us, where we get the max amount of job
postings. Let's have those as first priority." "Don't keep it every 5 minutes or it will mess up my computer. I can
manually click a button that checks and updates." "We don't want to miss any listings posted on these portals." "I
want Knock to be the best ever for me, personally catered for me, and always sorted by the latest posting posted
throughout all the boards."

What stands in the way today:

- A full pass over about 2,000 boards takes about 4 hours, because boards are read one after another (2026-11-02).
- Knock knows only the Greenhouse and Ashby boards its sources found. Both sites host thousands more companies, and
  a job on a board Knock does not know can never show up.
- The list sorts by day, not by the time a job was posted.
- The Today page chooses companies for him, which he no longer wants.

The services he wants to beat are hours behind: LinkedIn reads ATS feeds every 6 hours and says a free listing can
take up to 48; Simplify adds a Greenhouse posting a median 3.2 hours after it goes live (measured, p90 7.9 hours);
Jobright updates hourly.

## 2. What he said (his decisions)

1. **No recommendations.** Today goes away, its top 8 companies with it ("drop it, I don't care"). No daily picks,
   no 08:00 plan, no ranking. He decides what is relevant.
2. **The job board is the product.** He opens Knock and sees the latest openings at the top.
3. **Sorted by when each job was posted**, newest first, across every board, with the exact time.
4. **He fetches by hand.** A Check now button reads the boards and updates the list. No checking every few minutes
   in the background; the background schedule stays as it runs today, so the Mac gets no new load.
5. **Greenhouse and Ashby first**, and no listing on those two sites missed: Knock must know every company there
   that hires US engineers.
6. **Keep the filters:** software roles only (AI and forward deployed roles count, as the rules already say), his
   level (new grad, early career, SWE 1, plus "level not stated"), no internships, no citizenship, clearance or
   ITAR/EAR roles. The #9 level and visa rulings stand as they are.
7. **The outreach side stays as it is and stays separate.** He picks the job and the company, does the outreach
   himself (people, drafts through the MCP, LinkedIn), and Knock keeps tracking, scheduling, sending, follow-ups and
   analytics. "As soon as we do outreach, just log it: this company has been done. We have a record of that."
8. **Today's per-company steps go** (pasting people, the saved LinkedIn people search, LinkedIn note editing, the
   terminal line for Claude, Open role): "they don't mean anything to me."
9. **Analytics stay.** The Progress page is unchanged.

## 3. Goals, non-goals, anti-goals

**Goals**

1. Check now reads every Greenhouse and Ashby board Knock knows in a few minutes, says what it is doing, and puts
   what it found on the list as it goes.
2. Knock knows every Greenhouse and Ashby company that has an open US engineering role.
3. The list sorts by when each job was posted, to the minute where the board says so.
4. Every company he has reached shows as done on its rows, with the date.
5. Nothing on the outreach side changes behavior, and the Mac carries no more background load than today.

**Non-goals**

- Checking boards every few minutes in the background.
- Workday, Oracle, Lever and the other sites beyond today's schedule. They keep being read as now and their jobs
  stay on the list; they just get no new work.
- Ranking, fit scores, or any "why this company" text.
- Anything on LinkedIn, and no paid job data. The #9 sources ruling stands (no LinkedIn, Indeed or Glassdoor
  scraping, no JobSpy).
- Researching companies or finding people for him.

**Anti-goals** (a failure even if it works)

- A repost, an edited old job or a newly added board's old jobs shown as new at the top.
- Getting Knock blocked by Greenhouse or Ashby. One blocked host loses every board on it.
- A list that jumps under his cursor while he reads it.
- Slowing the sender, the API or the Mac.

## 4. How it works

### 4.1 Read many boards at once

Each board read already asks "anything changed?" first: a conditional GET with the stored ETag (Greenhouse and
small Ashby boards answer 304 when nothing changed), and an id fingerprint where there is no ETag. The slow part is
that boards are read one after another. So reads run side by side: each ATS gets a few worker threads that send the
list requests, each host keeps its own spacing, and one thread writes what they find to the store. All the outage
rules (breaker, held errors, mass empties) stay as they are.

Two fixes ride along:

- **Ashby without an ETag.** Big Ashby boards (Ramp) answer 200 with no ETag, so today they re-ingest every read.
  When there is no ETag, compare the id fingerprint instead.
- **Lever at 1 request a second**, as its robots.txt asks (`Crawl-delay: 1`; Knock spaces it 0.25 s today).

Rates (research 6.3): Greenhouse 20 a second, Ashby 10. The circuit breaker trips at once on a 429. All rates live
in `outreach/crawl/config.toml`.

### 4.2 Every Greenhouse and Ashby company

stapply publishes a free daily list of every company on each ATS (Knock already downloads these lists; the
`wellfound` and `ycombinator` lists stay excluded). Knock reads the Greenhouse and Ashby lists and gates each board
it does not have yet with one list request: a board is added only when it has an open posting that the rules read
as US engineering. A board that fails the gate is noted and tried again in 30 days, so a company that starts
hiring later gets in. New rows on later lists are gated the same way each week.

The first seed is a one-time job of one request per board, at the polite rates above (Greenhouse 20 a second,
Ashby 10), run once in the live checkout after Devanshu says go. How many boards it adds decides how long a Check
now takes; the research's arithmetic puts 5,000 boards at under 2 minutes and 15,000 at about 5.

### 4.3 Check now

A button on the job list, the main way he fetches. One click reads every active Greenhouse and Ashby board, plus any
other board that is due on today's schedule. A board read in the last 60 s is skipped. While it runs, one line under
the header says what is happening ("Checked 1,240 of 4,013 boards, 3 new") and new rows land in the list as they are
found. When it ends: "Checked 4,013 boards in 3 min 10 s. 6 new." Clicking again while a check runs shows the
running one.

The API records the request and starts the crawl job at once (`launchctl kickstart` without `-k`, so a running tick
is not killed). The crawl job sees the request and runs the check; a request that comes in while a tick runs is
picked up when that tick ends. The API itself never sends a request to a job board.

The background tick stays every 30 minutes with today's cadence, so other sites keep being read and nothing is lost
between clicks; it just does not get faster.

### 4.4 Sorted by when the job was posted

The list's time for a row (its list time):

- **The board's own posting time, never later than Knock's first sighting.** Greenhouse `first_published` (it
  matched Simplify to the second, 8 of 8). Ashby `publishedAt`, taken only the first time Knock sees that job id,
  because Ashby resets it on a republish; a job Knock already has never moves. If Knock saw the job before the board
  says it was posted, the first sighting wins.
- **A date only** (Workable, Workday, Oracle, Breezy, Teamtailor): that date at 00:00 New York.
- **No board time** (Lever, whose `createdAt` runs days to a year early; Rippling, BambooHR, JazzHR): Knock's first
  sighting, when the board had been read well before (the row says how long it had been: "found after a 3 day gap"
  when that is over 2 hours). On a board's first read, a row with no board time goes to a "Date unknown" group at the
  end of the list in every window.
- **Rows from before this change** keep their current date (the posted date, else the first-seen day).

**A newly added board does not flood the top.** Its jobs sort by their own posting times, so a job posted in March
sits in March. Only jobs actually posted recently reach the top.

**A repost is not new.** A posting with the same company and title as one closed in the last 120 days (the window
#155 planned) shows a "reposted, first seen Oct 2" tag. It sorts by its posting time, so a company hiring again
still shows near the top, but it never gets the "new" marker.

**What "new" means on the page.** A row first seen after he last opened the job list, that is not a repost and not
from a board's first read. The browser keeps that moment, so no write is needed. Each row shows its posting time
("posted 10:38, 6 min ago") and, where the board gives no time, when Knock first saw it. The 7, 30, 60 and 90 day
windows filter on the list time. (Claude's call.)

### 4.5 The page

- `/` opens the job list. `/today` and `/proposals` redirect to it.
- New rows slide in at the top. If he has scrolled down or has a row open, the list does not move: a small "3 new"
  button at the top shows them when he clicks it.
- Windows, filters, Pass and the keyboard keys stay.
- **Reached companies are marked.** Every row of a company he has reached (an email sent, a LinkedIn request logged,
  or his own mark) shows "reached Nov 9" (the existing `contacted_on`). The existing "not contacted" filter hides
  them.
- **Go ahead becomes Done.** Go ahead meant "add to today's list", which is gone. The same key now means "I've
  reached out to this company": it records his mark, so the company shows as reached even when the outreach happened
  outside Knock. He can undo it. No other check stops it. (Claude's call, from "just log it: this company has been
  done".)
- A one-line summary of today's outreach from the Progress numbers sits under the header ("Today: 34 sent, 2
  replies"), linking to Progress.
- The Mac alert stays as it is today (one grouped notice from the background tick, quiet 22:00 to 08:00 New York),
  except that a repost or a newly added board's job never alerts.

### 4.6 What gets switched off

The 08:00 plan job (`[plan] enabled = false`, printing one line and exiting, like prepare and the judge), Today's
page, the Proposals page, Pipeline's "Email today" bench, and Today's per-company steps. The plan job's code and the
daily list data stay on disk; the web pages and components for Today, Proposals and Email today are deleted, and a
revert brings them back. Touches made from now on carry no daily list context (source `own`, as for any company he
picks himself); the outcome analytics keep working on what they have.

## 5. Constraints

- Python 3.14 and the pinned dependencies. Threads from the standard library; no new HTTP library.
- SQLite, one writer. The worker threads only do network; one thread writes.
- Tests never touch the network or the live store (`tests/__init__.py` scratch settings). Network code gets fake
  clients, as the crawler's tests do now.
- Only the live checkout runs the crawler or the seed for real (CLAUDE.md). Claude never calls Check now or any API
  write route.
- No new background load: no new launchd job, no shorter interval.

## 6. Order of work

Each part is one issue, one branch, one PR, in this order:

1. Many boards at once, the Ashby fallback, Lever's rate (4.1).
2. Every Greenhouse and Ashby company (4.2).
3. Sorted by when the job was posted (4.4).
4. Check now and the self-updating list (4.3, 4.5).
5. Today goes away, Done replaces Go ahead, the plan job off (4.5, 4.6).

## 7. How we know it worked

Measured on the live checkout for one week after part 4:

- Check now wall time. Target: a few minutes for every Greenhouse and Ashby board.
- Coverage: of the Greenhouse and Ashby postings at his level that Simplify lists in that week, the share Knock
  already had. Target: all of them (each miss names its cause through the miss log).
- Zero blocks: no Greenhouse or Ashby breaker trips from our own rate.
- Zero false "new": no row marked new that was a repost or an old job from a newly added board.

## 8. Relation to the open job pipeline issues

- #158 (parallel reads and tiers): part 1 takes the parallel reads with one writer thread. The tiers and faster
  intervals are dropped (he fetches by hand).
- #161 (stapply boards through the funnel) and #160 (one funnel): part 2 takes the Greenhouse and Ashby lists only,
  with a direct gate. The other lists and the general funnel stay in #160 and #161.
- #155 (true posted dates and reposts) gives part 3 its repost window and column name (`repost_of`); the rest of
  #155 stays in #155.
- #157 (Workday) stays as it is, not on this path.
- New: Check now and the self-updating list (part 4); Today goes away (part 5).
- #169 (carry an unfinished own pick to the next day) and #33 (Today as a people plan) are moot once Today is gone.

## 9. Left to the builder

- Exact worker counts for Greenhouse and Ashby within the rates above.
- How the gate reads US engineering from a list answer (titles and locations through the existing rules).
- The copy and layout of the progress line, the "new" button and the row time, through the craft gate.

## 10. The #9 ruling to add

**First to see ruling (2026-11-09 and 2026-11-10, from Devanshu):** Knock stops choosing for him. Today and its top
8, the 08:00 plan, the Proposals page and Pipeline's Email today are switched off, and the job list is home, sorted
by when each job was posted, newest first, across every board. He fetches by hand: Check now reads every Greenhouse
and Ashby board on demand; the background schedule stays as it is, with no faster checks. Greenhouse and Ashby come
first: Knock adds every company on both sites with an open US engineering role, from stapply's lists through a
one-request gate. A repost or a newly added board's old jobs are never shown as new. Go ahead becomes Done: his mark
that a company has been reached. The level, visa and tech filters, the outreach side and the analytics are
unchanged. This replaces the "Today" lines of the 2026-10-21 changes, the company-first ruling's queue of about 8
companies a day, and the job list ruling's "Go ahead puts the company on today's list" and "sorts by the real posted
date" (now the posting time, to the minute where the board gives one).
