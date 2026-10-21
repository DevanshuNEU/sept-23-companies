> **Evidence only.** Decisions live in issue #9; where this file disagrees, #9 wins. Its recommendations feed `superpowers/specs/2026-10-20-opportunity-pipeline-design.md` and decide nothing on their own. Working files and raw data it mentions stay outside the repo in `~/Desktop/outreach-scratch/`.

# Who to contact, through which channel, and what to record (research, 2026-10-20)

Labels: **PRIMARY** = platform help center, terms, official docs, or a study with published methods. **VENDOR** = a company's own claim about its own product or data, methods not public. **UNVERIFIED** = could not read the page, or no source found. *inference* = my reasoning. "His data" = `~/Desktop/outreach-scratch/recon/outreach-history.md` (129 logged first touches, April to September 2026).

Builds on, and does not repeat: `docs/research/2026-10-17-outreach-and-tracking.md` and `docs/research/2026-10-18-cold-email-deliverability.md` in the repo.

## Summary

1. No study ranks founders, managers, engineers and recruiters by reply rate to job seekers. His own data is the best signal: managers 2 calls from 12, founders 3 from 61, recruiters 0 positive from 43.
2. Referrals are the prize. Referred applicants were 6% of applicants but 29% of hires at one firm (Brown, Setren, Topa), and about 6 times as likely to be hired at a large high-tech firm (Burks et al.).
3. Ashby's startup-heavy ATS data (vendor): referrals are 1% of applications and about 18% of hires; 40% reach an interview versus 3% for inbound.
4. Referrers are mostly peers or seniors in the same division, and moderately weak ties (some mutual context) move the most jobs in software (LinkedIn's own experiments). So: an engineer or manager on the team first, alumni and shared-context people ranked up.
5. Timing: half of applications land within 48 hours and postings run a median of 7 days (Dice data, NBER 2024). No study with methods shows early applicants are hired more often; "first 72 or 96 hours" claims are vendor or unsourced.
6. LinkedIn: weekly invite cap exists but is unpublished; free notes are 200 characters and 3 (or 5) a month; Premium Career gives unlimited notes and 5 InMails but does not lift the people-search cap. No open API for search or messaging.
7. LinkedIn's User Agreement 8.2 bans bots, scrapers, extensions and data brokers. Drafting off-site and sending by hand is fine; post search with Boolean strings and the Posts filters is the manual way to find "we're hiring" posts.
8. Email: catch-all domains defeat verification, home port 25 is blocked, and GitHub's AUP bans using commit emails for unsolicited mail. Send only given, published, Hunter-valid or proven-pattern addresses, one variant per person.
9. HN "Who is hiring" posters commit to replying (9 of 266 October posts mention early-career roles); Wellfound's application note is a sanctioned message; WaaS and NUsource rules could not be read. CAN-SPAM penalties are unchanged for 2026.
10. Design: prepare a person, a live role link, a specific reason and a draft for each channel; never automate anything on LinkedIn; log every touch with role type, channel, role age and outcome timestamps so the ranking learns from honest zeros.

## 1. Who to contact, and what referrals are worth

### 1.1 What his own data says (the only role-level data found)

- Recruiters: 43 emails, 2 human replies, 0 positive, 9 auto-replies. Founders: 61 emails, 3 calls. Hiring managers: 12 emails, 2 calls. Engineers: 1 email, but 6 of 6 replied across channels, 5 on LinkedIn, inflated because unanswered DMs were not logged (his data, sections 3 and 5).
- Since August, every positive outcome began with a message to a named engineer or manager about a named live role (his data, section 6 finding 4).

### 1.2 Response rate by recipient role: no primary study exists

- I found no peer-reviewed or first-party study that measures replies to job seekers' cold messages by recipient role (founder, CTO, hiring manager, engineer, recruiter). Searches on 2026-10-20 returned only SEO pages and vendor posts.
- Vendor and SEO claims seen, none with methods: "Cold emails sent directly to hiring managers see a 15-25% response rate" and "HR specialists respond at 8.5%" (**UNVERIFIED**, no data source on the page, e.g. [jobply.ai](https://jobply.ai/guides/cold-email-hiring-managers), [pitchhired.com](https://pitchhired.com/blog/cold-email-reply-rates-job-search-data), read 2026-10-20). Recruiter-to-candidate reply rates (Pin, Gem) measure the opposite direction and do not apply (**VENDOR**, [Pin](https://www.pin.com/blog/recruiting-outreach-benchmark-report/), read 2026-10-20).
- So his own table (1.1) is the best role-level evidence available to the system. It is small (n = 12 for hiring managers) and should be treated as a prior, not a fact (*inference*).

### 1.3 Referrals: what the studies with methods show

**Brown, Setren and Topa (PRIMARY).** One US financial services firm with 2,000 to 5,000 employees. All hires 2000 to April 2011 and all applicants 2006 to 2010, about 62,000 applications, traced through interview, offer and acceptance ([NY Fed Staff Report 568, revised June 2013](https://resources.newyorkfed.org/medialibrary/media/research/staff_reports/sr568.pdf), section 4, read 2026-10-20; published as Journal of Labor Economics 34(1), 2016, 161 to 209, [econpapers](https://econpapers.repec.org/RePEc:fip:fednsr:568)).
- "referred employees constitute only 6 percent of the applicant sample, but 21 percent of interviewees, 27 percent of offer recipients, and 29 percent of hires."
- Relative to job board applicants, referred applicants are "7.3 percentage points more likely to be interviewed" and "2.4 percentage points more likely to receive an offer". Base rates: "6.0 percent of job board applicants receive interviews and 32.3 percent of interviewees from internet job boards receive offers". So referred applicants are "more than twice as likely to be interviewed" and, once interviewed, "about 40% more likely to receive an offer".
- Starting salary premium of 2.1 percent that fades over about three years. Referred workers are about 85 percent as likely to leave.
- Effects vary by level: support, junior and senior staff show "a one percentage point smaller referral effect than for mid-level staff", executives a much larger one. Most referrers sit at the same (47.9%) or a higher (48.1%) staff level than the person they refer. 73.2% of referrals are within the same division.
- Caveat: the numbers above come from the 2013 staff report. The 2016 journal version may differ in detail (**not checked**).

**Burks, Cowgill, Hoffman and Housman (PRIMARY).** Personnel data from nine large firms in call centers, trucking and high-tech. Referred applicants "are more likely to be hired and more likely to accept offers, even though referrals and nonreferrals have similar skill characteristics" ([QJE 130(2), 2015, 805 to 839, abstract](https://experts.umn.edu/en/publications/the-value-of-hiring-through-employee-referrals/), read 2026-10-20).
- High-tech firm, 1,175,016 applicants: the offer rate for non-referred applicants is 0.39% and referral adds 1.75 percentage points. The hire rate for non-referred is 0.28% and referral adds 1.37 points, so referred applicants are hired at roughly 6 times the rate (Table C7, [IZA DP 7382, May 2013](https://docs.iza.org/dp7382.pdf), read 2026-10-20; my arithmetic).
- Referred high-tech workers produce more patents, and referred workers are 10 to 30% less likely to quit (same paper).
- Caveat: these are very large firms with a formal referral program. A 50-person startup has no such pipeline data (*inference*).

**Rajkumar et al. 2022 (PRIMARY, LinkedIn's own experiments).** Randomized tests of LinkedIn's People You May Know feature on about 20 million people over five years, with 600,000 new jobs ([Science, 2022-07-27, Stanford Digital Economy Lab summary](https://digitaleconomy.stanford.edu/publication/a-causal-test-of-the-strength-of-weak-ties), read 2026-10-20). Moderately weak ties produced the most job mobility. "connections with 10 mutual friends were the most effective", and weak ties helped more in "more software intensive" fields ([MIT Sloan](https://mitsloan.mit.edu/ideas-made-to-matter/study-weak-ties-make-a-difference-finding-a-job-online), read 2026-10-20).
- For him: a stranger who shares some mutual connections (Northeastern, a past employer) is the best-supported kind of weak tie. A total stranger with no overlap is weaker (*inference* from the inverted U).

**Ashby (VENDOR, first-party ATS data, partial methods).** Ashby's customers are "predominantly US-based tech companies with between 1 and 1,500 employees" ([Ashby 2023 applications report](https://www.ashbyhq.com/talent-trends-report/reports/2023-trends-report-applications-per-job), read 2026-10-20), which matches his targets.
- 38M applications across 93K jobs, January 2021 to December 2024: inbound is 93.8% of applications, referral 1% ([Ashby referrals report, 2025-05-16](https://www.ashbyhq.com/talent-trends-report/reports/referrals), read 2026-10-20).
- Application to interview: referral 40%, sourced 25%, inbound 3%. Interview to offer: referral 16%, sourced 6%, inbound 6% (same page).
- Offer rate per application: referral fell from 12.1% to 7.3% and inbound from 0.7% to 0.2% between Q3 2021 and Q4 2024 (same page).
- Nearly 250,000 hires, January 2021 to June 2025: referral about 18% of hires, sourced about 16%, inbound 43% to 52%. Referrals hold "~17% of hires regardless of inbound volume" ([Ashby inbound report](https://www.ashbyhq.com/talent-trends-report/reports/inbound), read 2026-10-20).
- Ashby does not split these by seniority, so the new-grad rate is unknown.

**Greenhouse.** I found no public Greenhouse report with referral conversion numbers. Its help center only documents a "Candidate quality by referrer" report for customers ([Greenhouse support](https://support.greenhouse.io/hc/en-us/articles/203941219-Candidate-quality-by-referrer-report), read 2026-10-20). Common lines such as "referrals fill 30% to 50% of hires" or "34% of referred candidates get hired" trace to listicles with no methods (**UNVERIFIED**, e.g. [noon.ai](https://www.noon.ai/blog/articles/360-employee-referral-statistics-2026)).

### 1.4 What follows for "who first"

- A referral roughly doubles to sextuples the chance of a hire in every dataset with methods (Brown et al., Burks et al.), and in Ashby's startup-heavy data a referred application reaches an interview at 40% versus 3% for inbound. The prize is a referral, which only an employee can give (PRIMARY plus VENDOR, above).
- Referrers are mostly peers or seniors in the same division (Brown et al.). That fits an engineer on the team, or the manager, as first contact (*inference*).
- No study ranks founders, CTOs, managers, engineers and recruiters for cold replies. His data is the only signal: managers 2 calls from 12, founders 3 from 61, recruiters 0 positive from 43 (his data).

## 2. Timing: does the first few days matter

- **Applications pile up early (PRIMARY).** Dice.com data, 125 million applications: "posting durations are quite short, often only two or three days, with a median of seven days", and "almost half of applications flowing to openings posted in the past 48 hours" ([Davis and Samaniego de la Parra, NBER WP 32320, April 2024](https://www.nber.org/papers/w32320), read 2026-10-20). An earlier cut of the same data: 41% of applications within 48 hours and 56% within 96 hours ([St. Louis Fed, Nov 2017](https://www.stlouisfed.org/on-the-economy/2017/november/how-quickly-people-apply-jobs), read 2026-10-20). Tech employers accept applications for "a mere nine days on average" and take "45 days on average" to fill the job ([UChicago News, 2017-08-31](https://news.uchicago.edu/story/looking-job-you-may-have-only-nine-days-apply), read 2026-10-20).
- Caveat: two thirds of Dice postings come from staffing firms ([NBER abstract](https://www.nber.org/papers/w32320)). Startups posting on Ashby or Greenhouse may keep roles open longer (*inference*).
- **Same pattern at small tech companies (VENDOR).** Ashby: "The first week of inbound applications is consistently 2.5-3x higher than all following weeks". Technical roles got 60 applications in their first four weeks in 2021 and 174 in 2023 ([Ashby 2023 report](https://www.ashbyhq.com/talent-trends-report/reports/2023-trends-report-applications-per-job), read 2026-10-20). Ashby gives no data on whether early applicants are hired more often.
- **"Apply in the first 72 or 96 hours" claims (VENDOR).** TalentWorks (2017, its own users' applications) claimed applying within 96 hours makes an interview up to 8 times more likely, with chances falling about 28% per day after that ([summary on paulvanderlaken.com](https://paulvanderlaken.com/2017/10/24/talent-works-data-science-to-improve-job-application-chances/), read 2026-10-20; original TalentWorks page not read). No sample size or method is public. "First 72 hours" figures I saw had no source at all (**UNVERIFIED**).
- **Showing the applicant count changes behavior (PRIMARY).** A LinkedIn field experiment with 2.3 million job seekers found that showing how many people had applied raised the chance of finishing an application by about 3.5% ([Gee, Management Science; Cornell summary](https://jobsearchstudies.ilr.cornell.edu/papers/f07c7dbe-283e-4ab4-8b51-23315949599e), read 2026-10-20). This is about volume, not hiring outcomes.
- **What is solid.** Competition arrives early and screening often happens in batches after a short posting window (PRIMARY). No study with methods shows that an early applicant is more likely to be hired, all else equal. The referral path matters more than timing: Ashby's referral share of hires stays near 17% "regardless of inbound volume" (VENDOR, above).
- **His data.** Retell: four ATS applications from Aug 21 to Sep 18 drew nothing. One DM to an engineer on Sep 21 got him routed in 11 minutes. Several winning DMs went to people who had just posted that their team was hiring (his data, section 6 finding 4). Freshness of the person's post, not just the posting, seems to matter (*inference*, n is small).

## 3. LinkedIn in 2026

The 2026-10-17 doc already covers User Agreement 8.2 basics, help 56347, the unpublished weekly limit, the 3-week resend rule and the connections export. This section adds what changed or was missing. All LinkedIn pages read 2026-10-20; "updated" ages are as each page shows them.

### 3.1 Limits

| Item | What LinkedIn says | Source |
|---|---|---|
| Weekly invitations | A limit exists; hitting it brings a restriction that "typically lasts one week"; "LinkedIn cannot remove or shorten the wait period"; applies to Basic and Premium alike. No number published. | [help a550555](https://www.linkedin.com/help/linkedin/answer/a550555) (updated about 1 year ago) |
| Triggers | Many invitations sent quickly, many ignored or pending invitations, or suspected automation tools. Too many pending: "Wait for up to one month". | [help 4800](https://www.linkedin.com/help/linkedin/answer/4800) (updated about 10 months ago) |
| The 100 a week figure | Third-party only. LinkedIn articles by members say 100, up to 200 for older accounts (**UNVERIFIED**) | e.g. [member article](https://www.linkedin.com/pulse/linkedin-new-weekly-invitation-limit-dan-porat) |
| Note length, free | "Each message can be up to 200 characters" | [help a563153](https://www.linkedin.com/help/linkedin/answer/a563153) (updated about 6 months ago) |
| Notes per month, free | Three ([a563153](https://www.linkedin.com/help/linkedin/answer/a563153), newest page) or five ([a550555](https://www.linkedin.com/help/linkedin/answer/a550555), [a6239760](https://www.linkedin.com/help/linkedin/answer/a6239760)). Plan for three. | as listed |
| Note length, Premium | 300 characters per an older page ([a6239760](https://www.linkedin.com/help/linkedin/answer/a6239760), updated about 2 years ago). The newest page says 200 for everyone and does not mention 300. Plan for 200. | as listed |
| Notes per month, Premium | Unlimited | [a563153](https://www.linkedin.com/help/linkedin/answer/a563153) |
| InMail, Premium Career | 5 credits a month, up to 15 banked. "Every InMail message that is accepted/declined or responded to directly within 90 days of it being sent is credited back." | [help a543695](https://www.linkedin.com/help/linkedin/answer/a543695/inmail-message-credits-and-renewal-process) (updated about 2 months ago) |
| Search cap, free | A "commercial use limit" on people search, number not disclosed, resets "at midnight PST on the 1st of each calendar month". Name search, 1st-degree browsing and job search do not count. **Premium Career does not remove it**; Premium Business, Recruiter Lite and Sales Navigator raise it. | [help a564226](https://www.linkedin.com/help/linkedin/answer/a564226) (updated about 2 years ago) |

### 3.2 Premium Career features that matter here

From LinkedIn's plan page ([premium.linkedin.com compare plans](https://premium.linkedin.com/careers/compare-plans), read 2026-10-20): 5 InMails a month, "Top applicant jobs", "Advanced search filters for actively hiring people", "Find and reconnect with people actively hiring", AI insights on job posts, a message proofreader, Who viewed your profile, LinkedIn Learning. The page shows no price.

Also free for everyone: a job post's "Meet the Hiring Team" section, where "Members will be able to connect and message the entire team listed in this section, including the job poster", and notifications "when a first- or second-degree connection is hiring for a relevant job" ([help a767235](https://www.linkedin.com/help/linkedin/answer/a767235), updated about 1 year ago). Whether a free member can message a non-connection there without InMail is not stated (**not verified**).

*Inference:* Premium Career is worth it for him only if he sends more than three notes a month. Unlimited notes is the main gain. InMail at 5 a month is too few to plan around. It does not lift the people-search cap.

### 3.3 The rules on automation, scraping and tools (User Agreement effective 2025-11-03)

Section 8.2 "Don'ts" ([User Agreement](https://www.linkedin.com/legal/user-agreement), read 2026-10-20) includes:

- "Develop, support or use software, devices, scripts, robots or any other means or processes (such as crawlers, browser plugins and add-ons or any other technology) to scrape or copy the Services, including profiles and other data from the Services".
- "Use bots or other unauthorized automated methods to access the Services, add or download contacts, send or redirect messages, create, comment on, like, share, or re-share posts, or otherwise drive inauthentic engagement".
- "Override any security feature or bypass or circumvent any access controls or use limits of the Services".
- "Copy, use, display or distribute any information (including content) obtained from the Services, whether directly or through third parties (such as search tools or data aggregators or brokers), without the consent of the content owner".
- "Deep-link to our Services for any purpose other than to promote your profile or a Group on our Services, without LinkedIn's consent".
- "Overlay or otherwise modify the Services or their appearance".

Section 3.6 covers LinkedIn's own generative AI features and says users must review and edit generated content before sharing (same page, as summarized on fetch).

Professional Community Policies, "Do not spam members or the platform": "We don't allow untargeted, irrelevant, obviously unwanted, unauthorized, inappropriate commercial or promotional, or gratuitously repetitive messages or similar content. Do not use our invitation feature to send promotional messages to people you don't know or to otherwise spam people." ([Professional Community Policies](https://www.linkedin.com/legal/professional-community-policies), read 2026-10-20).

Prohibited software help page: no "crawlers', bots, browser plug-ins, or browser extensions that scrape, modify the appearance of, or automate activity on LinkedIn's website"; accounts risk being "restricted or shut down" ([help 56347](https://www.linkedin.com/help/linkedin/answer/56347), updated about 2 years ago).

### 3.4 What is safe for a person using LinkedIn by hand (*inference* from the text above, not legal advice)

Clearly fine:
- Reading LinkedIn in his own browser, typing or pasting his own message, clicking Send himself.
- A local tool that writes a draft from facts he supplies, and that he copies by hand. Nothing in 8.2 covers writing text off the site.
- Importing his own connections export (covered in the 2026-10-17 doc).

Gray, keep small:
- Storing a person's profile URL or a post URL in a local tool so he can click it later. The deep-link clause targets links that put LinkedIn content inside another service. A private bookmark list is not that, but the tool should never publish or share these links (*inference*).
- Pasting a person's post text into the tool to draft a reply. The "copy, use, display" clause needs the owner's consent for use of their content. Store a one-line note in his own words and the URL, not the full post (*inference*).

Never:
- Any extension, headless browser, or script that opens LinkedIn pages, reads them, fills the message box, clicks Connect or Send, or checks replies.
- Any "LinkedIn data" vendor (Apollo, Prospeo, Snov extensions, Proxycurl-style APIs). That is the "through third parties (such as search tools or data aggregators or brokers)" clause.
- Sending the same message to many people. The spam policy names "gratuitously repetitive messages", and two recipients already said his DMs read as AI-written (his data, section 3).

### 3.5 Finding "we're hiring" posts by hand

LinkedIn's own post search, desktop: type keywords, "See all results", the **Posts** tab, then filters. "All filters" has "Sort by, Date posted, Content type, From member, From company, Posted by, Mentioning member, Mentioning company, Author industry, and Author company" ([help a526104](https://www.linkedin.com/help/linkedin/answer/a526104), updated about 11 months ago).

Boolean search works in the main search bar: AND, OR, NOT in capitals, "quotes" for phrases, parentheses; no wildcards, and + and - are not supported ([help a524335](https://www.linkedin.com/help/linkedin/answer/a524335), updated about 7 months ago). The page does not say which tabs honor Boolean, so test it on Posts.

A search he can run by hand (my example, untested):
- Keywords: `("we're hiring" OR "my team is hiring" OR "join my team") AND ("new grad" OR "new graduate" OR "early career" OR "software engineer")`
- Posts tab, Date posted: Past week (or Past 24 hours), Sort by: Latest.
- Author company: one target company at a time, or Author industry: Software Development.
- Then open the post, check the role on the company's ATS page, and note the person's URL.

Other built-in, free signals: "Meet the Hiring Team" on a job post, and the #Hiring frame on a person's photo, which adds them to that section ([help a1336879](https://www.linkedin.com/help/linkedin/answer/a1336879), [help a767235](https://www.linkedin.com/help/linkedin/answer/a767235)).

Whether post search counts toward the commercial use limit is not stated. The help page lists only profile search and profile browsing (**not verified**).

### 3.6 Is there a compliant API

No, not for this. LinkedIn's API docs list three open, self-serve permissions: name, headline and photo of the signed-in member, their email, and "Post, comment and like posts on behalf of an authenticated member" ([Getting Access, Microsoft Learn, updated 2026-06-03](https://learn.microsoft.com/en-us/linkedin/shared/authentication/getting-access), read 2026-10-20). Everything else needs partner approval: Marketing, Sales Navigator (SNAP), Talent programs for ATS vendors (Recruiter System Connect, Apply Connect, Apply with LinkedIn), and closed Compliance permissions. None offers people search, post search or messaging to an individual job seeker.

## 4. Finding and verifying one person's work email

The 2026-10-17 doc covers Reacher, AfterShip, Comcast's port 25 block and first-look free tiers. The 2026-10-18 doc covers bounce codes, Gmail's bounce lock and null MX. This section adds what was missing.

### 4.1 Pattern guess plus SMTP check: where it breaks

- **Catch-all domains** accept any address at RCPT time, so a probe returns "accepted" for a guess that does not exist. Hunter's API labels these `accept_all`: "email is valid but the server accepts all addresses" ([Hunter API docs](https://hunter.io/api-documentation/v2#email-verifier), read 2026-10-20). A send to a wrong address at a catch-all may still bounce later, or vanish silently (*inference*).
- **Google Workspace** rejects an unknown mailbox with 550 5.1.1 ([Google SMTP codes](https://knowledge.workspace.google.com/admin/support/troubleshooting/gmail-smtp-errors-and-codes), cited in the 2026-10-18 doc). **Microsoft 365** rejects at the edge with 550 5.4.1 when Directory Based Edge Blocking is on, which is the default ([DBEB](https://learn.microsoft.com/en-us/exchange/mail-flow-best-practices/use-directory-based-edge-blocking), same doc). So a real send is the true test, and every wrong guess shows up as a hard bounce against his Gmail account.
- One verifier vendor says Google Workspace and Microsoft 365 "block external mailbox verification regardless of source IP" and returns them as unverifiable (**VENDOR**, [Apify listing](https://apify.com/labrat011/email-verifier), read 2026-10-20). I could not confirm this from Google or Microsoft. Reacher's open-source code does run SMTP checks against Gmail and Microsoft B2B hosts and uses a headless browser for Outlook.com consumer and Yahoo ([Reacher README](https://github.com/reacherhq/check-if-email-exists), [CLI README](https://github.com/reacherhq/check-if-email-exists/blob/master/cli/README.md), read 2026-10-20), which suggests results vary by provider and IP (*inference*).
- From his home connection, port 25 is blocked (2026-10-17 doc), so he cannot probe at all. He has to rely on a vendor's verification.

### 4.2 Free tiers, checked 2026-10-20

| Service | Free | Credit cost | Notes | Source |
|---|---|---|---|---|
| Hunter | 50 credits a month | 1 per email found, 0.5 per verification | Starter $49 a month or $34 billed yearly, 2,000 credits | [hunter.io/pricing](https://hunter.io/pricing) |
| Snov.io | Trial plan renews every 30 days: 50 credits, 100 recipients | 1 credit per lookup or verification, interchangeable | Starter $39 a month. Also sells "LinkedIn Automation", which LinkedIn's terms ban (section 3.3) | [snov.io/pricing](https://snov.io/pricing) |
| Apollo | Free plan with no time limit. "50 email credits per month" and 25-record exports per search summaries (**UNVERIFIED**, Apollo's pages did not show numbers) | n/a | Free plan includes a Chrome extension (search summary). Apollo's own insight page (2026-09-17) gives no numbers | [Apollo insights](https://www.apollo.io/insights/more-information-on-what-they-are-paying-for-in-apollo-plan-detailsusage-walkthrough) |
| Prospeo | 100 credits a month per a third-party review; older reviews say 75 (**UNVERIFIED**, pricing page renders client-side) | n/a | Starter $49 a month, 2,000 credits (**UNVERIFIED**) | [emelia.io](https://emelia.io/hub/prospeo-pricing) |

For one person at a time, Hunter's 50 credits buy about 25 finds plus 50 verifications a month, or 100 verifications of his own guesses (*inference* from its credit costs). Hunter's verifier gives a clean `valid`, `invalid`, `accept_all` or `unknown` answer.

### 4.3 GitHub commit emails

- GitHub's Acceptable Use Policies, section 7 "Information Usage Restrictions": "You may not use information from the Service (whether scraped, collected through our API, or obtained otherwise) for spamming purposes, including for the purposes of sending unsolicited emails to users or selling personal information, such as to recruiters, headhunters, and job boards." Section 8 says anyone collecting data must follow the GitHub Privacy Statement ([GitHub AUP](https://docs.github.com/en/site-policy/acceptable-use-policies/github-acceptable-use-policies), read 2026-10-20).
- "obtained otherwise" covers reading a commit's author line by hand. A cold referral ask is an "unsolicited email" to a GitHub user. So using a commit email for this falls inside the ban's plain words, even sent one at a time (*inference*, not legal advice).
- Many commit emails are GitHub no-reply addresses anyway, because users can pick "Keep my email addresses private" ([GitHub docs](https://docs.github.com/en/account-and-profile/setting-up-and-managing-your-personal-account-on-github/managing-email-preferences/setting-your-commit-email-address), read 2026-10-20).
- Verdict: do not source addresses from GitHub commits. An address the person lists publicly for contact (a personal site, a README "contact me" line) is a different case, and the system should record where it came from.

### 4.4 What is reliable enough for a personal Gmail

Ranked, most to least safe (*inference*, built on the cited facts):

1. An address the person gave, or published for contact. No bounce risk.
2. A vendor `valid` result (Hunter), on a domain that is not catch-all.
3. A guess that matches a pattern already proven at that company by a delivered, unbounced send (24-hour wait for late bounces, per the 2026-10-18 doc).
4. A guess on a catch-all domain, or `accept_all` / `unknown`. Send only for a high-value person, and only one variant.
5. Never: several variants to the same person, or a GitHub commit address.

Why it matters: Gmail can lock sending for 24 hours when "a large number" of messages bounce (2026-10-18 doc). His own records show 50 of 101 sends went to guessed patterns and 41 to unknown provenance, with only 1 bounce logged because bounce notices were not read (his data, section 5 item 6). The bounce rate is unknown, not low.

## 5. Other channels

No study with methods compares these channels for new grads. NACE's 2025 Student Survey (13,684 students, 1,479 Class of 2025 seniors, April 1 to May 30, 2025) reports that the class applied to more jobs (10 versus 6) and got fewer offers, but its job-source breakdown is in the paid report ([NACE press release](https://naceweb.org/about-us/press/class-of-2025-college-grads-responded-to-challenges-of-a-tough-job-market), read 2026-10-20; source breakdown **not verified**). What follows is each channel's own rules plus his data.

### 5.1 Applying through the ATS at the same time as a message

- In Greenhouse an employee submits a referral against a specific job ("It is not possible to refer a prospect, so choose a job for your referral"), and the referral counts only when "the candidate's source must be Referral and you must be credited as the referrer" ([Greenhouse Referrals FAQ](https://support.greenhouse.io/hc/en-us/articles/12320251521435-Referrals-FAQ), [Submit referrals](https://support.greenhouse.io/hc/en-us/articles/201982560-Submit-referrals), read 2026-10-20). What happens when he has already applied before the referral is not documented (**not verified**). It is set per company.
- Ashby's numbers (section 1.3) count "referral" as a source. An application that arrives as inbound may be read as inbound (*inference*).
- His data: 77 emails tied to a named req, often with a same-day application, drew 9 replies; 38 without a req drew 0. Applications alone produced about as many interviews as all his cold email (his data, sections 3 and 6).
- *Inference:* apply when the role is fresh, message the same day, and if the person offers a referral, ask whether they want to submit it themselves. Record which came first.

### 5.2 YC Work at a Startup

- YC's site says candidates complete one profile and founders reach out ("Creating a profile is free and founders reach out to you", per search summary of [workatastartup.com](https://www.workatastartup.com/), read 2026-10-20). The FAQ and home pages render client-side and returned no text (HTTP 406 to curl), so the messaging rules, any caps and the visa filter are **not verified**.
- His data: Retell (YC W24, about 50 people) came through a LinkedIn DM to an engineer, not through WaaS (his data, section 4).

### 5.3 Wellfound

- Each candidate has a cap on active applications at one time. The number is not published; slots free up "As your job applications begin to expire" ([Wellfound help 767](https://help.wellfound.com/article/767-application-limits), read 2026-10-20).
- The application carries a note "akin to a cover letter", and the company sees his profile and resume ([Wellfound help 769](https://help.wellfound.com/article/769-how-do-i-apply-to-a-job), read 2026-10-20). Companies can require the note ([help 751](https://help.wellfound.com/article/751-how-do-i-require-candidates-to-include-a-note-in-their-job-application)).
- *Inference:* the note is a free, sanctioned message to the hiring team. The draft tool can prepare it with the same facts as an email.

### 5.4 Hacker News "Who is hiring"

- The October 2026 thread (posted 2026-10-01) asks posters to post only "if you personally are part of the hiring company" and "if you are actively filling a position and are committed to replying to applicants". Readers: "please only email if you are personally interested in the job" ([HN item 49922569 via Algolia API](https://hn.algolia.com/api/v1/items/49922569), read 2026-10-20).
- My count on that thread, 2026-10-20: 266 top-level posts; 150 mention remote; 78 include an email address; 22 mention visas or sponsorship (7 of them say no); 9 mention new grad, junior, entry level or early career; 3 mention Boston. Regex count, not hand-checked.
- *Inference:* the poster has promised to reply, the address is given for this purpose, and the post names a role. That removes the address-guessing risk (section 4) and fits his req-tied pattern. Few posts target new grads, so volume is low.
- Pulling the thread through HN's public Algolia API is reading public data. Using posted addresses for anything beyond a personal reply to that post would break the thread's own rule.

### 5.5 Northeastern alumni

- NUsource is Northeastern's mentoring and networking platform, with "over 11,800 alumni users" who can be found "by industry, desired roles, companies, location" ([Alumni 101](https://alumni.northeastern.edu/alumni-101/), read 2026-10-20). It needs a login, so its rules are **not verified**. People on it have opted in to help (per search summary, **not verified** on the page).
- LinkedIn's school Alumni tab lets members "Identify alumni in your field and reach out to them"; filtering by year "is no longer available" ([help a567083](https://www.linkedin.com/help/linkedin/answer/a567083), updated about 6 months ago). Viewing profiles "on the People tab of LinkedIn Pages" counts toward the free commercial use limit ([help a564226](https://www.linkedin.com/help/linkedin/answer/a564226)), and the Alumni tab is likely the same kind of view (*inference*).
- Weak-tie evidence favors ties with some shared context (section 1.3). Alumni at a target company are the clearest such tie (*inference*).
- His data records no alumni-specific touch, so there is no outcome to compare.

### 5.6 Discord and Slack communities

- No primary data found on job outcomes from these. Rules are set per server or workspace. Most hiring channels ask for posts in a set format and forbid unsolicited DMs (**UNVERIFIED** general pattern; check each community's rules).
- Treat as a source of named people and roles, not as a send channel.

## 6. Compliance: what changed since the 2026-10-18 doc

- **CAN-SPAM penalty amount did not change for 2026.** The FTC notice (published 2026-09-15): "the Federal Trade Commission ('FTC')'s civil penalty amounts will remain unchanged during 2026. The FTC will continue to apply the 2025 civil penalty levels." The reason: OMB memo M-26-11 (2026-04-17) cancelled the 2026 inflation adjustment because the shutdown left BLS without October 2025 CPI data ([Federal Register 2026-18853, public inspection copy](https://public-inspection.federalregister.gov/2026-18853.pdf), read 2026-10-20). So the $53,088 per email figure in the earlier docs still stands. The scope question is unchanged: a one-to-one job inquiry most likely is not "commercial" (*inference*, as before).
- **Gmail enforcement.** Google's FAQ: "Starting November 2025, Gmail is ramping up its enforcement on non-compliant traffic. Messages that fail to meet the email sender requirements will experience disruptions, including temporary and permanent rejections." No 2026 dates appear on the page ([Google FAQ 14229414](https://support.google.com/a/answer/14229414), read 2026-10-20). His gmail.com mail is signed by Google, so this changes nothing for him (*inference*, as in the 2026-10-18 doc).
- **State privacy laws.** Indiana, Kentucky and Rhode Island laws took effect 2026-01-01. They apply to businesses processing data of at least 100,000 residents (Indiana, Kentucky) or 35,000 (Rhode Island) (**SECONDARY**, [JD Supra](https://www.jdsupra.com/legalnews/no-slowing-down-three-more-state-8826203/), [Enzuzo tracker](https://www.enzuzo.com/blog/us-state-privacy-laws), read 2026-10-20). A person keeping a few hundred contacts for his own job search is far below these (*inference*).
- **State anti-spam.** A Washington appeals panel held the state's commercial electronic message law covers unsolicited recruiting texts sent by a company (**SECONDARY**, [Law360 headline](https://www.law360.com/articles/2388224), paywalled, not read). Virginia's telephone solicitation changes, effective 2026-01-01, cover soliciting texts and calls (**SECONDARY**, [WDTV](https://www.wdtv.com/2025/12/31/new-virginia-law-lets-you-stop-unwanted-soliciting-texts-with-one-word/)). Neither touches a person emailing about his own job search. Do not add SMS as a channel (*inference*).
- **LinkedIn and GitHub.** No change found to the LinkedIn User Agreement since its 2025-11-03 version. GitHub's AUP clause on unsolicited email (section 4.3) was not in the earlier docs.

## 7. What this means for the design

Each point says what it rests on: **[study]**, **[platform rule]**, **[vendor]**, or **[his data]**. Issue #9 wins where it disagrees.

### 7.1 Who the system should pick first

1. **A named engineer or manager on the team that owns a live role, ideally one who posted about it.** Every positive outcome since August started this way, Retell in 11 minutes [his data]. Referrers are mostly peers or seniors in the same division [study: Brown et al.]. A referral is the prize: referred applicants reach an interview at 40% versus 3% for inbound at small tech companies [vendor: Ashby], and are hired at 2 to 6 times the rate of others [study: Brown et al.; Burks et al.].
2. **Seed-stage founder or CTO** when the company is too small to have a team lead. 3 calls from 61 founder emails, 2 of them seed [his data].
3. **Recruiter last.** 0 positive from 43 [his data]. No study supports a different order; none ranks roles at all [no primary source found].
4. **Prefer people with some shared context** (Northeastern, a past employer, mutual connections). Moderately weak ties move the most jobs in software fields [study: Rajkumar et al.].

### 7.2 Per channel

| Channel | System prepares | Never automate | Basis |
|---|---|---|---|
| **LinkedIn note or DM** | The person, their profile URL and the post URL, the live role link, one specific reason in his own words, a draft under 200 characters for an invite note (longer for a DM to a connection). A count of notes used this month (3 if free) and invites in the last 7 days | Opening pages, reading profiles, filling the box, clicking Connect or Send, checking replies. No extensions or data vendors on LinkedIn. No bulk-copied post text | [platform rule: User Agreement 8.2; help a563153, a550555, 56347]. Two recipients said his DMs read as AI-written [his data] |
| **LinkedIn post search** | Saved Boolean strings and the filter recipe (Posts, Date posted: past week, Sort: latest, Author company) for him to run by hand; a place to paste the post URL he picks | Running the search, scraping results, any "monitor posts" job | [platform rule: help a526104, a524335; no open API for search, Microsoft Learn] |
| **Email** | One person, the address with its provenance (given, published, Hunter `valid`, proven pattern, or guess), the live role link, the specific reason, the draft | Sending more than one guessed variant per person. Sourcing from GitHub commits. Sending without a provenance label | [platform rule: GitHub AUP section 7; Hunter API statuses]. 50 of 101 past sends were guesses and bounces went unread [his data] |
| **ATS application** | The role URL, posted date, and a same-day prompt to also message one person | Auto-applying or form filling | Posting windows are short: median 7 days, half of applications within 48 hours [study: Davis and Samaniego de la Parra]. Req-tied emails drew 9 of 77 replies, others 0 of 38 [his data] |
| **HN "Who is hiring"** | The monthly thread's posts filtered for new grad, Boston or US remote, and visa wording, each with its posted address and role; a reply draft | Emailing anyone for any purpose other than that post's role | Posters commit "to replying to applicants"; readers "only email if you are personally interested" [platform rule: HN thread]. Only 9 of 266 October posts mention early-career roles [my count] |
| **Wellfound** | The application note, drafted like an email | Applying in bulk; the platform caps active applications | [platform rule: Wellfound help 767, 769] |
| **YC Work at a Startup** | Keep his profile current; log any founder who writes first | Anything; rules not verified | [not verified] |
| **Alumni (NUsource, LinkedIn Alumni tab)** | A list of alumni at target companies he finds by hand, with a "shared context" flag that raises rank | Scraping the Alumni tab or NUsource | [platform rule: help a564226 counts People-tab views toward the free limit; NUsource rules not verified] |
| **Discord, Slack** | A place to log a person or role found there | DMs at volume; follow each community's rules | [no primary data] |

### 7.3 Timing

- Rank a role higher in its first week, and highest in its first 48 hours, because that is when applications arrive and postings often close within days [study: Davis and Samaniego de la Parra; vendor: Ashby first-week peak 2.5 to 3 times later weeks].
- Do not claim that early applicants are hired more often. No study with methods shows it [no primary source found]. The stronger lever is the referral, which holds a steady share of hires regardless of inbound volume [vendor: Ashby].
- Rank a person higher when their hiring post is recent [his data, small n].

### 7.4 What to record per touch, so ranking can learn

His history has no real denominator: unanswered DMs were not logged, replies stopped being checked on June 19, and 265 applications have no outcome [his data]. Record every touch, including the ones that get nothing.

Per touch:
- **Who:** person, company, role type (engineer on team, manager, founder, CTO, recruiter, alumni, other), seniority, shared context (school, past employer, mutual count if he sees it).
- **What for:** the role link, the role's posted date, whether the person posted about it and when, whether he applied and when (before, same day, after).
- **How:** channel (LinkedIn invite with note, invite without note, LinkedIn DM, InMail, email, HN reply, Wellfound note, WaaS, other), for email the address provenance, the draft version, word or character count, sent time.
- **Outcome, with timestamps:** no reply by day 14, auto-reply, bounce (code), decline, routed to someone (who), asked for resume, referral submitted (if he learns of it), screen, interview, offer, reject. Who answered, if not the person messaged.
- **Flags:** recipient said it read as automated or AI-written; asked to stop.

The ranking then learns reply and referral rates by role type, channel and role age, with honest zeros. The Brown et al. pattern (referrals matter most at the interview step) means "screen within 30 days" is a better label to learn on than "reply" [study, *inference* on the choice of label].

## Not verified

- Brown, Setren and Topa numbers come from the 2013 staff report, not the 2016 journal version. Burks et al. high-tech numbers come from the 2013 IZA working paper, not the 2015 QJE version.
- LinkedIn's weekly invitation number (third parties say about 100). LinkedIn's own pages disagree on free notes (3 or 5 a month) and on Premium note length (200 or 300).
- Whether LinkedIn post search, or the school Alumni tab, counts toward the free commercial use limit.
- Whether a free member can message the "Meet the Hiring Team" people without InMail.
- Work at a Startup's messaging rules, caps and visa filter (pages did not render).
- NUsource's rules and whether its members opt in to be contacted (login required).
- Apollo's and Prospeo's current free credits (pricing pages render client-side; numbers are from third parties).
- Whether Google Workspace and Microsoft 365 block outside mailbox verification (one vendor says so; not confirmed by Google or Microsoft).
- What Greenhouse or Ashby do with a referral submitted after the candidate already applied.
- TalentWorks' "8 times more likely within 96 hours" (vendor, no methods, original page not read).
- Role-based reply rates for job seekers' cold messages ("15 to 25%" to hiring managers and similar): no source with data found.
- NACE's breakdown of how the Class of 2025 found jobs (paid report).
- The Washington court ruling on recruiting texts (paywalled).
- My HN count is a regex over 266 posts, not checked by hand.

Working files from this session (PDF text extracts, the WaaS fetch attempt) are in `~/Desktop/outreach-scratch/research/work/`.
