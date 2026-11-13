# Multi-sender design (2026-11-12)

Status: draft for Devanshu's review. Becomes current once the matching #9 ruling is posted.

## Why

devanshurajesh@gmail.com now lands in Gmail spam, even for a plain "hello" to his own second inbox. The store shows
why: 481 emails since Oct 27, about 20 a day until Nov 6, then 80 to 86 a day from Nov 9; 1 human reply; 16 bounces
(3.3%); up to 4 follow-ups each. His Northeastern address, used by hand since April, reached 214 people cold and got
10 real replies (about 4.7%, read from Mail.app on 2026-11-12). It also shows 18 Microsoft "Undeliverable" notices,
so bounces matter there too.

The lesson is per account: one account sending a lot of unanswered mail gets flagged. So Knock sends from several
real accounts of his, each slowly ramped, each with its own limits, and never more than one account per company.

## Accounts

| Account | Transport | Lane | Start | Step | Ceiling | Role |
|---|---|---|---|---|---|---|
| chicholikar.d@northeastern.edu | Mail.app (account "Exchange") | sponsor | 15 | +3 a clean day | 60 | sends |
| devanshuchi9@gmail.com | Gmail API (own token) | general | 15 | +3 a clean day | 40 | sends |
| devanshurajesh@gmail.com | Gmail API (existing token) | none | 0 | | | reads only, until he turns sending back on |

Adding an account later is one `[[account]]` block in `outreach/sending/config.toml` (plus a Mail.app account or a
Gmail sign-in). Nothing in code names an account.

**Why the second Gmail uses the Gmail API, not Mail.app.** Knock's Gmail path is proven: it puts a marker header on
every email, so a send in doubt is found in Sent and never goes twice, and it reads replies through Gmail's history.
Mail.app cannot set a header. Mail.app is used only where there is no API Knock may use (Northeastern's Microsoft 365).

## Lanes (which account a company gets)

- **sponsor lane:** E-Verify enrolled (`everify = enrolled`), or marked by hand `sponsorship = yes` or `opt_only`.
  When H-1B (LCA) evidence lands with the Visa tab, `lca` evidence joins this rule; nothing else changes.
- **general lane:** every other company.
- A company is pinned to an account the first time Knock claims an email to it (`company_sender`). Every later email
  to anyone there, follow-ups included, goes from that account. He can change the pin in the Outbox only while the
  company has no sent email.
- A company already emailed by devanshurajesh@gmail.com is pinned there. Its old threads are read; new people there
  are routed by lane to a sending account and the pin moves (the old account no longer sends). The company stop rule
  (any reply from anyone there stops every queued email) reads replies from every account.
- When the sponsor account is full for the day, sponsor lane emails wait for the next day. They never move to Gmail.

## Window and pacing

- Weekdays, **08:00 to 15:00 recipient local time** (replaces 08:30 to 16:30). Holidays: 2026-11-26, 2026-11-27,
  2026-12-24, 2026-12-25, 2026-12-31, 2027-01-01.
- **Spread pacing per account** (replaces the fixed 1.5 minute median): after each send, the gap is the minutes left
  until the latest window close among that account's ready messages, divided by the sends it has left today, times a
  random factor from 0.6 to 1.4, clipped to 2 to 45 minutes. With 15 a day this is about one email every 25 to 40
  minutes; with 60, about every 8. The breaks and the pick from the first 5 ready messages stay.
- Company size bands (2026-11-12, #229) stay as they are and count across all accounts (one account per company makes
  that automatic).

## Ramp and stops, per account

- Each account has its own day row (`send_day` keyed by account and day). Start 15. A clean day raises the next day
  by 3, to the account's ceiling. A warning day lowers it by 3, never below 15. Clean means what #9 already says:
  bounces under 2% over the last 3 days, no provider limit or block error, no spam or stop reply.
- **Hard pause of that account** (only that account; the others keep going): a provider limit or block (Gmail's, or
  Microsoft's restricted-sender or "unusual activity" notice, or a Mail.app send error saying the account cannot
  send); two "asked to stop" replies in 7 days; **2% or more of its last 100 first emails bounced, once 30 are sent**
  (this reverses the 2026-10-30 bounce ruling for the new accounts; the reason is the 2026-11-12 spam placement). A
  pause raises a Needs-you item; he resumes it.
- Follow-ups count toward the account's daily number (replaces "follow-ups do not count", 2026-11-12), because the
  provider counts them.

## Follow-ups

- **One follow-up, 7 business days after the first email**, from the same account, in the same thread. More than one
  he writes himself.
- Every planned, drafted or approved follow-up of seq 2 to 4 is skipped by migration (reason `rule_changed`).
- Every follow-up of a devanshurajesh@gmail.com thread is skipped too (that account is resting, and its threads
  cannot move to another account).

## Never send again

- #224 already puts a bounced person on the no-go list. This adds: the sender refuses any address that has ever
  bounced, from any account, on any person record (`bounced_address`), checked at claim time. The one bounced touch
  with no no-go row today is fixed by migration.
- A Microsoft "Undeliverable" notice (multipart/report, `Your message to X couldn't be delivered`, status 5.x.x) is a
  bounce like Gmail's.
- No paid verifier. Apollo-verified addresses are his normal input.

## Links

- New first emails carry **at most 1 link: the portfolio in the signature**. A project is named, not linked.
  Approved drafts already in the Outbox go as they are.

## Mail.app transport

`outreach/sending/mailapp.py`, `MailAppClient`, keeps the sender's contract with these substitutions:

| Sender need | Gmail API | Mail.app |
|---|---|---|
| Who is signed in | profile email | the Mail.app account's email addresses include the configured one, and Mail.app is running |
| Send | API send with marker header | AppleScript `make new outgoing message` (sender "Devanshu Chicholikar <address>"), `send` |
| Find a send in doubt | marker header in Sent | the one message in Sent Items to that address with that exact subject, sent after the claim; subjects are unique per person and a follow-up's is "Re: " plus the first |
| Proof before sending freely | first marked email read back | first email found in Sent Items by that rule, with a Message-ID; until then exactly one email may be claimed (the existing proof rule) |
| Thread id | Gmail thread id | the first email's Message-ID |
| Follow-up threading | In-Reply-To and References | AppleScript `reply` to the first email in Sent Items, To and body set, quoted text removed; sets In-Reply-To and References |
| New inbound mail | history cursor | every mailbox of the account except Sent, Drafts and Outbox, messages received after the cursor time less 10 minutes, deduped by Message-ID |
| Matching a reply | thread id | In-Reply-To or References naming a Message-ID Knock sent from this account, else the sender's address equals a person emailed from this account |
| Bounce | Gmail DSN | Microsoft NDR as above; its In-Reply-To names the email that bounced, and the address comes from the body |

Every AppleScript call has a timeout (sends 60 s, reads 120 s). A timeout on a send is a send in doubt and is settled
by the Sent Items rule, never by sending again. If Mail.app is not running, the account waits and raises one
Needs-you item a day.

## What stays

His click approves every email; the sender sends only what he approved; Claude never sends. Drafting, the voice
checks, the company size bands, the no-go rules and the Outbox stay. The 4 follow-up sequence, the flat 200 and the
1.5 minute median go.

## Order

1. **Phase A (Gmail API only, live first):** accounts in the store and config, per-account ramp, window, pacing,
   stops, lanes and pins, one follow-up and the cleanup, the bounced-address block. devanshurajesh reads only;
   devanshuchi9 sends the general lane; sponsor lane emails wait for phase B.
2. **Phase B:** Mail.app transport for the Northeastern account, after a spike he runs that proves send, find in
   Sent Items, reply threading and NDR reading.
3. **Phase C:** the Outbox shows and changes each company's account; the link rule in the drafting checks.
