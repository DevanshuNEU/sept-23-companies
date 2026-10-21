> **Evidence only.** Decisions live in issue #9; where this file disagrees, #9 wins. Its recommendations feed `superpowers/specs/2026-10-20-opportunity-pipeline-design.md` and decide nothing on their own. Working files and raw data it mentions stay outside the repo in `~/Desktop/outreach-scratch/`.

# Outreach history: who replied, and what they share

Read-only recon, 2026-10-20. Working files in this folder: `companies_parsed.json` (company-file frontmatter), `curated_touches.py` / `.csv` (one row per person first touched), `data_js.json` (copy of the dashboard data for analysis).

## Headline

The local records hold **129 first touches to 92 companies (April to September 2026)**. They do not hold 1,000 to 1,500. The earlier volume ("700+ emails, 6 founder responses", per `wins.md`) went out through Mail.app and the March 2026 Job-Outreach app (Apollo contacts, remote Supabase). No log of it exists on disk. Everything below covers the 129 rows I could see.

## 1. Data inventory

| Source | What it records | Rows | Dates | Reply detection |
|---|---|---|---|---|
| `mail/contacts.json` | New sender's contact store | **0 contacts**, `firstSendDate: null` | n/a | Code counts any non-self message in the thread as a reply, OOO included. Never ran. |
| `mail/send.log` | Sender runs | 3 lines, all "not signed in to Gmail; nothing sent" | 10-15 to 10-17 | n/a |
| `mail/sender.out`, `server.log` | Process output | empty | n/a | n/a |
| `mail/me.md` | Profile facts for drafts | no send data | n/a | n/a |
| DevVault `contacted.txt` | Manual append-only send log | 121 lines, 88 company names | 05-05 to 09-30 | None. Free-text notes only. |
| DevVault `companies/*.md` | One file per company. Frontmatter `people[]` has `sent`, `reply`, `reply_date`, `touch_count`, `email_confidence` | 146 files, 202 people, 101 with a sent date, 20 with a reply value | 04-08 to 09-30 | Manual. Devanshu reports, Claude logs. |
| DevVault `interview-prep/companies/` | One folder per interview process | 13 companies | 07 to 10 | Used to trace where each interview came from |
| `~/Desktop/devboard` mail scanner | Mail.app header scan for replies | `inbox-seen.json` holds 3 ids | last scan 06-19 | Automatic, but stopped in June |
| `radar.db` (read-only, immutable) | Job postings and his applications | 104,639 postings; 265 applied postings at 149 companies; 106 ignores | applied 07-28 to 10-01 | **0 reply, screen, reject or offer events**, despite known interviews |
| `dashboard/data.js` | Target list with size, funding, H-1B data | 1,024 companies (+17 ruled out) | refreshed 10-19 | 9 rows carry a status (8 Interviewing, 1 Replied). Not a send log. |
| `wins.md` | Self-reported tallies | n/a | pre-May | "700+ emails, 6 founder responses"; elsewhere "24 founder replies and interview invites" recovered from trash |
| `voice/authored.jsonl` | Messages he wrote | 4 lines (counted only) | n/a | n/a |

**Overlap.** Every company in `contacted.txt` maps to a company file except 5 (Solid, Klavis AI, Acceler8 Talent, Composio, Sourcegraph). Two file sends are missing from `contacted.txt` (Better Stack, Benchmark Motion). Four touches that led to calls appear only in interview briefs (Gaya AI, General Motors, Runlayer, Field AI). Only 35 of the 92 contacted companies appear in `data.js`, so about 989 of its 1,024 companies have never been contacted.

**How far to trust "reply".** Moderately for positive replies, because each one led to a written-up next step. Weakly for silence. Nobody checked for replies after June 19, bounces were rarely logged, and LinkedIn DMs were mostly logged only after someone answered.

## 2. Funnel by month (first touches, all channels)

"Human reply" leaves out auto-replies. "Positive" means routed to someone, asked for a resume, or a call.

| Month | People | Companies | Human replies (people / cos) | Positive | Led to call | Declines | OOO | Bounce | Follow-ups logged | ATS applications (radar) |
|---|---|---|---|---|---|---|---|---|---|---|
| Apr | 1 | 1 | 1 / 1 | 0 | 0 | 1 | 0 | 0 | 0 | n/a |
| May | 44 | 39 | 1 / 1 | 1 | 1 | 0 | 0 | 0 | 0 | n/a |
| Jun | 70 | 41 | 7 / 5 | 3 | 2 | 2 | 8 | 1 | 1 | n/a |
| Jul | 3 | 3 | 2 / 2 | 2 | 2 | 0 | 0 | 0 | 8 | 43 |
| Aug | 4 | 3 | 4 / 3 | 4 | 1 | 0 | 0 | 0 | 0 | 143 |
| Sep | 7 | 5 | 5 / 5 | 5 | 2 | 0 | 1 | 0 | 1 | 80 |
| **Total** | **129** | **92** | **20 / 17** | **15** | **8** | **3** | **9** | **1** | **10** | **266** |

Email alone: 117 people at 84 companies. 11 humans replied (9.4%) at 9 companies (10.7%). 4 companies led to a call (Maven Bio, Distyl, Gaya, GM): 4.8% of companies emailed.

## 3. Reply rate by slice (email first touches only, n=117)

**By company stage.** "Growth" means Series C to E or late private, roughly 100 to 1,000 people.

| Stage | People | Cos | Human reply people | Positive | Call | Note |
|---|---|---|---|---|---|---|
| Seed / pre-seed / YC | 26 | 23 | 2 | 2 | 2 | Maven Bio, Gaya |
| Series A | 11 | 9 | 0 | 0 | 0 | |
| Series B | 11 | 6 | 3 | 2 | 2 | Distyl x2, plus Better Stack's no |
| Growth (C to E) | 34 | 19 | **0** | 0 | 0 | Cursor, Cognition, Factory, Baseten, Fireworks, Exa, Arize, Federato, LiveKit, Cohere, DevRev, WHOOP, Lila, Docker, Render, Coder, EliseAI, RapDev, Sourcegraph |
| Large / public | 33 | 25 | 6 | 2 | 1 | GM call; WWT routed; HubSpot, C3 x2, Klaviyo |
| Agency | 2 | 2 | 0 | 0 | 0 | |

**By recipient role.**

| Role | People | Human replies | Positive | Call | Auto-reply |
|---|---|---|---|---|---|
| Founder | 61 | 4 | 3 | 3 | 0 |
| Hiring manager | 12 | 4 | 3 | 2 | 0 |
| Recruiter | 43 | 2 | **0** | 0 | 9 |
| Engineer (peer) | 1 | 1 | 0 | 0 | 0 |

All channels together, engineers went 6 for 6 on replies, 5 of those on LinkedIn. That number is inflated by logging bias (see section 5).

**Channel (all touches).** Email: 117 sent, 11 human replies. LinkedIn DM: 11 logged, 9 replies, 3 calls. iMessage: 1 sent, no reply. The DM rate is not real, because unanswered DMs were not logged. Two recipients, a Distyl contact and a Cartesia recruiter, said his DMs read as AI-written, so more DMs went out than the log shows.

**Open role at his level.** Of the 117 emails, 77 went with a named live req, often with an application the same day. Those drew 9 replies. 38 went without a specific req (the June 9 big-company recruiter batch and some May founders) and drew 0 human replies, only 8 OOOs. GM's call had no posting at all.

**Sponsorship in the posting.** Too few cases to measure. Interviews came at companies with no H-1B record (Runlayer, Pax Historia, Aarden, Composio, Gaya, Go.AI) as well as at verified sponsors. The June 9 batch, aimed at 19 verified Boston-area sponsors, got 1 soft reply from 23 people.

**Follow-ups.** 8 second follow-ups on July 6 (Axiomatic, Brex, Cohere, DevRev), 0 replies. Two other follow-ups (HubSpot, Wolters Kluwer) also got nothing. Too small to mean much, but nothing argues for more follow-ups.

**Day sent.** Tuesday 59 sends, 2 replies. Thursday 25 sends, 5 replies. This mostly reflects which batch went out on which day (June 11 was Distyl and C3), so it says nothing about timing. No send times were recorded.

**Length / template.** Journal lint lines put drafts at 82 to 105 words throughout. The May emails buried the visa. The June emails opened with the OPT and $100K-fee line. Both did badly, and I cannot separate them.

**Source list.** Not recorded per send. `data.js` sources (`addedBy`) exist only for companies added after October 3, none of them contacted.

## 4. The repliers

| Company | Size / stage | Person | Channel | What they said | Outcome |
|---|---|---|---|---|---|
| Better Stack | Series B | Founder emailed, talent lead replied | Email, Apr 8 | Polite no on experience, "stay in touch" | Closed |
| Maven Bio | Pre-seed | CEO, co-founder | Email, May 23 | Warm, meet at his event May 27 | In-person meet; nothing further recorded |
| HubSpot | Public | Recruiter | Email, Jun 9 | Apply on the site, passed to the right recruiter | No new-grad req existed; nothing further |
| Klaviyo | Public | Engineer (referral ask) | Email, Jun 11 | Already referred someone else | No |
| C3 AI | Public | FDE lead (name never recorded) + recruiter | Email, Jun 11 (replies Jul 6) | Recruiter polite no; HM content not recorded | Closed |
| Distyl AI | Series B, ~225 | CEO + founding head of data science, then recruiter | Email + application, Jun 11 (reply Jul 6) | Scheduled call, then take-home | Rejected after round 2, Jul 14 |
| Docker | Late private | Sales engineering director | LinkedIn, Jun 11 | Asked for resume and a few paragraphs, would forward | Emoji reply only |
| Gaya AI | Seed | Co-founder, CTO | Email, early Jul | Agreed to a call | Founder call; outcome not recorded |
| Runlayer | Series A | Employee | LinkedIn + application, Jul | Redirected to FDE req, passed profile | Screen Jul 24; outcome not recorded |
| General Motors | Public | Security engineering manager | Email, Aug, no posting | Booked an intro call himself | Call; outcome not recorded |
| World Wide Technology | Large private | Division director, then partner manager | LinkedIn Aug 18, then email | Routed within a minute; second person replied Aug 24 | Not recorded |
| Microsoft (DC hub) | Public | Architect who posted the opening | LinkedIn, Aug 21 | Apply and send resume | Not recorded |
| Slalom | Public | FDE tech lead who posted roles | LinkedIn, Sep 9 | Took resume for referral | Not recorded |
| Field AI | ~150, Series B era | Staff ML engineer | LinkedIn, Sep 10 | "Amazing let's chat" | Intro call, Oct |
| Wolters Kluwer | Public | Engineer (1st degree) | LinkedIn, Sep 18 | Gave the recruiter's address | Recruiter sent auto-reply only |
| Retell AI | ~50, YC W24 | Member of technical staff | LinkedIn, Sep 21 | Asked for link + resume, routed in 11 minutes | Interview loop, Oct |
| Go.AI | Series A, ~80 | Founder, CEO | LinkedIn, Sep 27 | Routed to Head of Technology | Call Sep 27 (per data.js); HM email unanswered |

Interviews that did not start with his outreach: Crusoe (application Jul 29, panel Sep 8, rejected), Composio (application Aug 20, take-home; his May 12 cold email to the CEO got no reply), Pax Historia (application Aug 23, Chief of Staff wrote Aug 24), Aarden (application, screen Aug 19), RealPage (recruiter found him on LinkedIn), Truen (founder found him on LinkedIn), Benchmark Motion (third-party recruiter). Repliers he recalls from before May, with no records: Greptile, Pebble, Tamr, TraceLabs, Gentuity, Solid.

## 5. What looks broken

1. **The new system has never sent anything.** `contacts.json` is empty and every logged run stopped at Gmail sign-in.
2. **The volume claim has no records behind it.** 129 touches are logged. The pre-May sends sit only in Mail.app and remote Supabase.
3. **Replies that were never captured.** Gaya's file still reads `sent: null, status: drafted`, but it produced a founder call. GM, Runlayer and Field AI are missing from `contacted.txt`. radar.db logs no outcome on any of 265 applications, even though at least 4 became interviews. `data.js` marks Composio "Replied", but its brief says the cold email got no reply.
4. **Reply checking stopped June 19.** That was the devboard scanner's last run. Since then, replies exist only if Devanshu mentioned them.
5. **Sends with no named recipient.** 17 sent records are named "TBD", among them the June 9 bulk sends, both C3 people and a Docker recruiter. 13 `contacted.txt` lines name no person.
6. **Unverified addresses, almost no bounces logged.** Of 101 sent records, 50 used a guessed pattern, 41 are unknown, 5 verified, 5 provided. Only 1 bounce was logged. Either the guesses held up, or bounce notices went unread.
7. **Duplicates and same-day pile-ups.** Cascade is logged twice. Notion and Klaviyo got a June 9 bulk touch and then a second person two days later. Blitzy got its founder, then its recruiter. 30 of 92 companies got 2 to 4 people, usually on the same day (Federato 4, Stripe 4, Arize 3, Docker 3, Compa 3). Those 30 replied at the same rate as single-contact companies (7 of 30 vs 10 of 62).
8. **State errors.** C3's status flipped from replied back to emailed. Benchmark Motion's `sent` date (09-30) comes after its `reply_date` (09-26), because the thread was inbound.

## 6. Top 5 findings

1. **Cold email to strangers almost never worked.** 117 emails to 84 companies drew 11 human replies at 9 companies. 4 companies led to a call (4.8%). The new system should expect about 1 call per 20 companies emailed and rank for quality, not volume.

2. **Hot growth-stage AI companies were a dead end. The calls came from seed and Series B.** Series C to E got 34 emails at 19 companies and 0 replies. Series A got 11 emails, 0 replies. The email-led calls were 2 of 23 seed companies, 1 of 6 Series B, and one large-company manager (GM). Across every channel, 10 of the 13 companies that produced an interview are under about 250 people (Retell 50, Go.AI 80, Field AI 150, Distyl 225, Composio 55, Pax Historia 7, Gaya, Runlayer, Aarden, Truen). **Rank companies under 250 people above famous growth-stage labs.**

3. **Recruiters are the wrong first contact.** 43 recruiter emails: 2 human replies, 0 positive, 9 auto-replies. Founders: 61 emails, 3 calls. Hiring managers: 12 emails, 2 calls. **Rank a named hiring manager or a peer engineer on the team first, a founder at seed stage, and a recruiter last.**

4. **Every positive outcome since August came from messaging a specific person about a specific live role.** Retell is the clearest case. Four ATS applications between Aug 21 and Sep 18 got nothing. One LinkedIn DM to an engineer on Sep 21 got him routed in 11 minutes, and an interview loop followed. Field AI, Runlayer, Go.AI, WWT, Microsoft and Slalom all started the same way, often with someone who had just posted that their own team was hiring. The rate is not measurable, since unanswered DMs were not logged. **Rank companies higher when a findable person has posted about the role recently.**

5. **Live roles at his level matter, and applications count.** Emails with a named req drew 9 replies from 77. Emails without one drew 0 human replies from 38. Applications alone (265 at 149 companies) produced about as many interviews as all the cold email: Crusoe, Composio, Pax Historia and Aarden, all small companies or explicit new-grad and SWE I roles. Follow-ups added nothing (0 of 10). **Rank companies with an open new-grad, SWE I, founding-engineer or FDE new-grad req highest. Pair each send with an application. Record outcomes in one place, so the next version of this analysis has a real denominator.**
