> **Evidence only.** Decisions live in issue #9; where this file disagrees, #9 wins. Adopting a paid finder, a verifier or a new address source is a new #9 decision. Nothing here was signed up for, bought, called with a paid key or sent.

# Finding and verifying work email addresses at about 2,000 a month (research, 2026-11-06)

Labels: **PRIMARY** = the vendor's own pricing page, docs, API reference, terms or help center, an RFC, or a provider's admin docs. **VENDOR** = a company's claim about its own product or data, methods not public. **UNVERIFIED** = could not read the page, or the number appears only on a secondary site. *inference* = my reasoning. Every page below was read on 2026-11-06 unless marked otherwise.

Builds on, and does not repeat: `docs/research/2026-10-17-outreach-and-tracking.md` (Reacher, AfterShip, CAN-SPAM), `docs/research/2026-10-18-cold-email-deliverability.md` (bounce codes, Gmail's bounce lock, null MX) and `docs/research/2026-10-20-people-and-channels.md` section 4 (pattern guessing, GitHub commit emails, a first look at free tiers).

## 1. Short answer

Stop stacking free Apollo accounts: Apollo's terms ban it and let Apollo close the account with no refund. Pay for one finder that charges only for verified finds. The cheapest one I checked is Icypeas Premium: $39 a month for 4,000 credits that roll over, 1 credit per found email. Prospeo ($49 for 2,000) and Hunter Starter ($49, or $34 a month billed yearly) come close behind. Keep one free Apollo account for people search, which costs no credits. Add two free changes inside Knock: keep a list of every bounced address, and learn each domain's pattern from its own delivered mail. That comes to about $39 to $49 a month.

Caveats: (1) no vendor can be sure about catch-all domains, so bounces will not reach zero; (2) if the 500 a week includes follow-ups, he needs far fewer than 2,000 new addresses; (3) prices change often.

## 2. Comparison at about 2,000 found addresses a month

"Finds" = who produces the address. "Verifies" = who checks it before the send. Costs are list prices from the vendor's page, monthly billing unless noted; per-address cost is my arithmetic.

| Option | Monthly cost at 2,000 | Finds / verifies | Risk | Effort | Source |
|---|---|---|---|---|---|
| Several free Apollo accounts (today) | $0 | Apollo / Apollo, partly | **High**: banned by Apollo's terms, closure with no refund; not guaranteed, some bounce | Low, but manual copy | [apollo.io/terms](https://www.apollo.io/terms) |
| Apollo Basic, 1 seat | $49 listed (billing basis unclear) | Apollo / Apollo | Credits per plan not published on the page; credits expire monthly | Low; MCP in Claude Code | [apollo.io/pricing](https://www.apollo.io/pricing) |
| **Icypeas Premium** | **$39** (4,000 credits, roll over) | Icypeas / Icypeas (catch-all check included) | Low; vendor claims only | Low; API | [icypeas.com/pricing](https://www.icypeas.com/pricing) |
| Prospeo Starter | $49 (2,000 credits) | Prospeo / Prospeo | Low; no headroom | Low; API and MCP | [prospeo.io/email-finder](https://prospeo.io/email-finder) |
| Hunter Starter | $49, or $34 billed yearly (2,000 credits) | Hunter / Hunter | Low; no headroom for extra checks | Low; API | [hunter.io/pricing](https://hunter.io/pricing) |
| Tomba Basic | $49 (2,500 searches + 2,500 checks) | Tomba / Tomba | Low | Low; API (stated on Growth) | [tomba.io/pricing](https://tomba.io/pricing) |
| Datagma Regular | $49, or $39 billed yearly (3,000 credits) | Datagma / Datagma | Low | Low; API | [datagma.com/pricing](https://datagma.com/pricing) |
| Anymailfinder | $89 for 2k, or $708 a year for 24k ($59 a month) | Anymailfinder / Anymailfinder | Low; bills only verified finds | Low; API and MCP | [anymailfinder.com/pricing](https://anymailfinder.com/pricing) |
| Enrow Pro | $87 (10,000 credits; Start is $17 for 1,000) | Enrow / Enrow | Low | Low; API | [enrow.io/pricing](https://enrow.io/pricing) |
| Findymail Starter | $99 (5,000 finds + 5,000 checks) | Findymail / Findymail | Low | Low; API and MCP | [findymail.com/pricing](https://www.findymail.com/pricing/) |
| Snov.io Pro S | $99, or $74.25 billed yearly (5,000 credits; a find and a check cost 1 each) | Snov / Snov | Medium: also sells LinkedIn automation | Low | [snov.io/pricing](https://snov.io/pricing) |
| LeadMagic Essential | $99 (5,000 credits, no free credits) | LeadMagic / LeadMagic | Low | Low; API and MCP | [leadmagic.io/pricing](https://leadmagic.io/pricing) |
| Voila Norbert Butler | $99, or $79 billed yearly (5,000 leads) | Norbert / not stated | Low | Low; API | [voilanorbert.com/pricing](https://www.voilanorbert.com/pricing) |
| Wiza | $99 buys only 500 emails; unlimited is $990 a year | Wiza / not stated | **Flag**: built around LinkedIn exports (*inference*, testimonial on page) | Low | [wiza.co/pricing](https://wiza.co/pricing) |
| Lusha Premium | $399.90 (3,400 credits) | Lusha / Lusha | Cost | Low | [lusha.com/pricing](https://www.lusha.com/pricing/) |
| Dropcontact, FullEnrich, RocketReach | price at 2,000 not read | vendor / vendor | see section B | Low | section B |
| Clearbit | not available | n/a | Free tools ended 2025-04-30 | n/a | [clearbit.com/changelog](https://clearbit.com/changelog) |
| Pattern guess + paid verifier | about $16 (Bouncer, $8 per 1,000 checks) to $39 | Knock / verifier | Medium: wrong guesses on catch-all domains | Medium: guess code in Knock | [usebouncer.com/pricing](https://www.usebouncer.com/pricing/) |
| Pattern guess + self-hosted Reacher | VPS plus SMTP proxy (prices not read) | Knock / Knock | High: port 25, IP blocklists, rate limits | High | [Reacher README](https://github.com/reacherhq/check-if-email-exists) |
| Public addresses (team pages, HN posts, personal sites) | $0 | the company / no check needed | Low if read by hand; terms risk if scraped | Medium, low volume | section D |

## 3. Recommended permanent setup

### His four questions

1. **Is paying for Apollo, or the Apollo MCP, worth it?** The MCP costs nothing to connect and spends the same credits as the web app. Free accounts made with a personal email address cannot use people search or enrichment through it (section A). Apollo Basic is worth paying for only if he wants Apollo's people search and email reveal together inside Claude Code. For email alone, cheaper finders that bill only verified finds do the same job.
2. **Cheaper alternatives?** Icypeas ($39), then Prospeo, Hunter, Tomba or Datagma (each $49). Each says it charges nothing when no address is found. All have an API, and Prospeo, Anymailfinder, Findymail and LeadMagic also have an MCP server.
3. **Unconventional ideas that beat today's setup?** Two cost nothing and use Knock's own data: a list of bounced addresses that can never come back, and a per-domain pattern learned from delivered mail. A third is cheap: store which mail provider each domain uses (Google, Microsoft, a security gateway), because that predicts whether a check means anything. Public role addresses (founders@, hello@) and HN posts that ask for email are real, but small in volume.
4. **Permanent solution at about 2,000 a month?** The two stages below. The research worth doing first: a 30-minute bake-off on his own 301 sends (section C.6).

### Stage 1: cheap (about $39 to $49 a month)

- One free Apollo account (only one) for finding people in the web app. People search spends no credits ([Apollo API pricing](https://docs.apollo.io/docs/api-pricing)). He already finds people himself under the company-first ruling in #9.
- One paid finder that bills only for verified finds: Icypeas Premium ($39, credits roll over), or Prospeo or Hunter ($49). He signs up and pays; Claude does not.
- In Knock (no network in the MCP server, so no vendor call from Claude yet): a bounced-address list that `intake` and `add_people` check, the MX provider stored next to `mx_check`, and a pattern learned per domain from Knock's own sends (section D.2).
- **What Claude can do:** take the name, title and domain he pastes, plus the address the finder gave him, and record it through `intake` as today. It goes straight to the Outbox with a draft (#9 intake ruling, 2026-10-28). Claude never approves or sends. Claude can say "this address bounced before" or "this domain's known pattern is first.last and this address is not" as a warning on the draft.

### Stage 2: better (about $50 to $100 a month, after a month of Stage 1 data)

- A waterfall: the first finder, then a second one only for misses (for example Icypeas, then Prospeo or Anymailfinder). Each bills only verified finds, so a miss costs nothing.
- A finder call inside Knock, built the way the MX lookup is: the API (or a job) makes the call, caches the answer in a table, and the MCP server and the sender read only the cache. Spending stops at a hard monthly cap in config. That needs a new provenance value, because today an email source must be a public https page (`EmailSourceIn` in `outreach/mcp/tools/bridge.py`).
- **What Claude can do:** ask Knock to look up a person he named, if Devanshu rules that Claude may spend credits (a new #9 line). The result still lands as a draft in the Outbox, and he still approves every email by his click. A vendor's `risky` or `accept_all` verdict shows on the draft as a warning, the same way an `unknown` mail server shows now.
- Paying for Apollo Basic instead of a second finder makes sense only if he wants Claude to search Apollo for people through the Apollo MCP. That would change the company-first ruling ("he finds the people himself") and needs his say.

## 4. Findings

### A. Apollo

**Plans and prices (PRIMARY, partly).** The pricing page's structured data lists "Free Plan" at $0, "Basic Plan" at $49 and "Professional Plan" at $99 (`billingDuration` P1M), and "Enterprise Plan" at "Contact for pricing" ([apollo.io/pricing](https://www.apollo.io/pricing)). The page's plan table loads in the browser, so it showed no per-plan credit numbers. Whether $49 is monthly or the yearly rate per month is **UNVERIFIED**. Secondary sites say Basic is $49 billed yearly or $59 monthly, with about 2,500 credits a seat a month, and Professional about 4,000 a month (**UNVERIFIED**, for example [joinvalley.co](https://www.joinvalley.co/blog/apollo-pricing-and-credits-explained-2026), [warmly.ai](https://www.warmly.ai/p/blog/apollo-pricing)). The page does name three credit types: "Email credits", "Mobile credits", "Export credits".

**Free plan limits (PRIMARY).** The pricing page's fair-use text: "For non-paying customers using an email address with a valid and verified (by Apollo in its sole discretion) corporate domain, Apollo has implemented a credit limit of 10,000 credits per account per month to prevent abuse. Non-paying customers using an email address without a valid and verified corporate domain have a lower credit limit of 100 credits per account per month." So the domain he signed up with matters a lot. The free plan's own monthly grant is **UNVERIFIED**: recent secondary sites say 75 a month and older ones say 100 or 50 (for example [trexinet.com](https://www.trexinet.com/blog/apollo-free-credits)).

**Paid plan cap (PRIMARY).** "For paying customers, in order to prevent abuse, we have implemented an email credit limit of the lesser of (i) the dollar amount paid by the customer for the term of their subscription, divided by $0.025 or (ii) 1 million email credits per account per year" ([apollo.io/pricing](https://www.apollo.io/pricing)). At $49 a month that is $588 a year, divided by $0.025, about 23,520 email credits a year, or about 1,960 a month (my arithmetic). So one Basic seat sits right at his 2,000 a month target.

**Credits when you reveal an email (PRIMARY).** Apollo's credit page says "Apollo never charges more than 1 credit per contact", credits refresh each billing cycle and "do not roll over into the following billing cycle or upon renewal" ([apollo.io/pricing/about-credits](https://www.apollo.io/pricing/about-credits)). The per-type figures on that page render blank ("Email = - credit"). The API pricing page: people enrichment is "1 credit for demographics/email" and "+8 credits if mobile phone is returned", charged "only if credit-consuming data is found", and for the waterfall "some vendors consume credits per lookup even when no data is found" ([docs.apollo.io/docs/api-pricing](https://docs.apollo.io/docs/api-pricing)). Add-on credits "renew with your subscription. Unused credits expire at the end of your billing cycle and don't roll over" ([help: manage plan](https://knowledge.apollo.io/hc/en-us/articles/4677130104333-Customize-and-Manage-Your-Apollo-Plan), updated 2026-10-31). The price per add-on credit is not on any page I could read (**UNVERIFIED**). A 14-day trial of Basic or Professional can be used once, with record selection capped at 25 at a time and 20 phone numbers ([help: free trial](https://knowledge.apollo.io/hc/en-us/articles/5288168088205-Access-a-Free-Trial-of-Apollo), updated 2026-11-04).

**Apollo MCP (PRIMARY).** It exists and is official: endpoint `https://mcp.apollo.io/mcp`, OAuth, "No API key or local install is required" ([help: Apollo MCP](https://knowledge.apollo.io/hc/en-us/articles/45119679436557-Use-Apollo-MCP-with-AI-Tools-to-Run-Your-GTM-Workflow), updated 2026-11-02). "Apollo MCP is available on all Apollo plans, including free plans." "Everything runs on your existing Apollo permissions, plan limits, and credit balance." The developer docs add "There's no additional cost to connect to Apollo MCP" and "Free Apollo accounts registered with a personal email address can't use people or company search and enrichment actions" ([docs.apollo.io/docs/apollo-mcp](https://docs.apollo.io/docs/apollo-mcp)). Its actions table: "Search for people: No" credits ("Use enrichment to reveal contact details"), "People enrichment: Yes", "Bulk people enrichment ... including verified email data: Yes", "Email and phone validation ... Yes". So an agent can search and reveal, and each reveal spends a normal credit; the AI tool "explains the cost and asks for approval" before agent-led research and validation. The credit usage log shows MCP as its own "surface" ([help: credit usage](https://knowledge.apollo.io/hc/en-us/articles/9527776320781-Review-Credit-Usage-in-Apollo), updated 2026-10-28). The MCP can also "send one-off emails" and enroll people in sequences, which Knock's rules forbid Claude to do: if he connects it, those tools must stay unused (*inference* from #9).

**Multiple free accounts (PRIMARY, plain risk).** Apollo's terms: "You may not open multiple accounts for your use under a self-serve offering or otherwise take any actions which would allow you to circumvent the credit or rate limits which apply to self-serve offerings. Should we discover you doing so, we may in our sole discretion terminate your account immediately, with no right to refund or reinstatement." They also forbid "any measure intended to circumvent limitations to purchased credits" and using Business Contact Information "to create competing databases" ([apollo.io/terms](https://www.apollo.io/terms)). What he does today breaks the first rule. The realistic outcome is losing the accounts, not a lawsuit (*inference*, not legal advice).

### B. Alternatives

All figures are from each vendor's own pricing page or docs. "Bills misses?" means whether a search that finds nothing costs a credit.

| Vendor | Free | Paid tiers (monthly) | Bills misses? | Verification | API / MCP | LinkedIn |
|---|---|---|---|---|---|---|
| [Hunter](https://hunter.io/pricing) | 50 credits a month | Starter $49 ($34 yearly) 2,000; Growth $149 10,000; Scale $299 25,000 | No: "If the email address can't be found, it's free" | Built in; verifier 0.5 credit; "If we fail to verify, no credit is counted" | API ([docs](https://hunter.io/api-documentation/v2)); finder takes domain plus name, or a LinkedIn handle | Optional input only |
| [Snov.io](https://snov.io/pricing) | 50 credits, renews monthly | Starter $39 1,000; Pro S $99 5,000; Pro M $189 | Not stated | "Each email verification and each prospect costs 1 credit" | API | **Flag**: sells LinkedIn automation (2026-10-20 doc) |
| [Prospeo](https://prospeo.io/email-finder) | 100 credits a month | from $49 for 2,000 | No: "You won't be charged if no results are found" ([API](https://prospeo.io/api-docs/enrich-person)) | "Every email on Prospeo is always verified" | API and MCP ([docs](https://prospeo.io/api-docs)); name plus `company_website` works | LinkedIn URL optional; has a LinkedIn side panel |
| [Findymail](https://www.findymail.com/pricing/) | 10 credits | Starter $99: 5,000 finder + 5,000 verifier; usage plans $99, $399, $999 | No: "No charge if we can't find it" | "verified in real-time"; claims catch-all checks | "API & MCP access" | Not stated |
| [Icypeas](https://www.icypeas.com/pricing) | 50 credits (offer) | Basic $19 1,000; Premium $39 4,000; Advanced $89 10,000; yearly saves 20% | Implied no: "Pay For Verified Emails Only"; API card "1 credit if found" ([API](https://api-doc.icypeas.com/)) | Verifier 0.1 credit; "Catch-All verification" | API; no MCP listed | Not stated; API takes first, last name and domain |
| [Anymailfinder](https://anymailfinder.com/pricing) | 100 trial credits | $29 400; $49 1k; $89 2k; $149 5k; yearly 24k for $708 | No: person search "1 credit" "only if a verified email is found" | Included; "searches no longer return risky emails" ([OpenAPI](https://api.anymailfinder.com/openapi.json)) | API and MCP (`https://api.anymailfinder.com/mcp`) | LinkedIn URL optional |
| [Dropcontact](https://www.dropcontact.com/pricing) | 50 credits | Starter from €79 for 500, up to 150,000; Growth from €120 | No: a miss "will be re-credited to you" | "Advanced catch-all verification"; "does not use any contact databases" | "API & MCP access" | **Flag**: Growth lists "LinkedIn enrichment" |
| [Datagma](https://datagma.com/pricing) | 100 credits a month | Regular $49 ($39 yearly) 3,000; Popular $99 7,500; Expert $249 22,500 | No: "No match = no credit used" | "up to 93% email accuracy" (VENDOR) | API on all plans | Not stated |
| [Wiza](https://wiza.co/pricing) | 20 valid emails | Starter $49 100; Email $99 500; unlimited $990 a year | Not stated | Not stated | API on Team only | **Flag**: built around LinkedIn and Sales Navigator (*inference*) |
| [Lusha](https://www.lusha.com/pricing/) | 40 credits a month | Starter $49.90 400; Pro $69.90 600; Premium $399.90 3,400 | Not stated | "Reveal verified email address: 1 credit" | API and MCP share the balance | Has a browser extension (not checked) |
| RocketReach | lookups, "three sets in total" ([help](https://knowledgebase.rocketreach.co/hc/en-us/articles/25534772227739-Lookup-Credits-vs-Export-Credits)) | prices not readable (**UNVERIFIED**) | No: "If nothing verified is found, no credit is used" | verified emails only | API and MCP use export credits | Accepts LinkedIn URL lists |
| Clearbit / HubSpot Breeze | none: "On April 30, 2025, the Free Clearbit Platform ... will be discontinued" ([changelog](https://clearbit.com/changelog)) | Breeze needs a HubSpot plan (**UNVERIFIED**, community posts only) | n/a | n/a | n/a | Not an option for him |
| [LeadMagic](https://leadmagic.io/pricing) | "There are no free credits" | Essential $99 5,000; Growth $249 20,000 | No: "Failed lookups and unknown results cost nothing" | 0.25 credit per conclusive check | API, CLI and MCP | Not stated |
| [Enrow](https://enrow.io/pricing) | 50 credits | Start $17 1,000; Pro $87 10,000; Scale $397 50,000 | No: "No result, no charge" | 0.25 credit; "Catch-all Verification" | API | Not stated |
| [Tomba](https://tomba.io/pricing) | 25 searches, 5 a day | Basic $49 2,500 + 2,500; Growth $89 5,000 + 5,000 | No: "A search that finds no address is free" | Separate verifier allowance | API (stated on Growth) | Not stated |
| [Voila Norbert](https://www.voilanorbert.com/pricing) | 50 credits | Valet $49 1,000; Butler $99 5,000 ($79 yearly) | No: "I count only successful email founds" | Not stated for finder plans | "Bulk & API usage included" | Not stated |
| [FullEnrich](https://fullenrich.com/pricing) | 50 credits | Pro about $55 for 1K ("$0.055 per credit"); 2K not read | No: "If we can't find the data, you don't pay" | "Advanced Email & Phone Verification" | API | A waterfall of other vendors (*inference*) |

**LinkedIn, plainly.** None of these vendors says where its data comes from. Most offer a LinkedIn browser extension, and several accept a LinkedIn URL as input. Knock's rule is about what Knock does: it takes no LinkedIn data and does no LinkedIn scraping. A finder called with a name and a company domain stays inside that rule. Wiza, Snov's LinkedIn automation and Dropcontact's "LinkedIn enrichment" do not, so leave them out (*inference*).

**Cost per found address at 2,000 a month** (my arithmetic, list prices): Icypeas $0.020, Prospeo, Hunter, Tomba and Datagma $0.025, Anymailfinder $0.030 (yearly) to $0.045, Enrow $0.044, Findymail, Snov and LeadMagic about $0.050, Lusha $0.20.

### C. Build it ourselves

**C.1 Pattern guessing.** I found no independent study of how often first@, first.last@ or flast@ is right. The only figures come from vendors' own databases. One Prospeo page says first.last "wins decisively" at 1,000 or more people and that small companies mostly use first@ (**VENDOR, UNVERIFIED**, [prospeo.io/s/email-permutation](https://prospeo.io/s/email-permutation)). Hunter's Domain Search returns a `pattern` field per domain, for example `"pattern": "{first}"`, and counts a query only "for calls returning at least one result" ([Hunter API](https://hunter.io/api-documentation/v2)). His own record: the 2026-10-20 doc found 50 of 101 older sends went to guessed patterns, and bounce notices were not read then, so that guess rate is unknown. Today 5 of 301 first emails bounced (his figure, mostly Apollo addresses).

**C.2 How SMTP verification works.** The verifier looks up the domain's MX, opens port 25, and asks the server whether it will accept `RCPT TO` for the address, then hangs up without sending. Its answers:
- Google Workspace rejects an unknown mailbox: "550 5.1.1 The email account that you tried to reach does not exist" ([Google SMTP codes](https://knowledge.workspace.google.com/admin/support/troubleshooting/gmail-smtp-errors-and-codes)).
- Microsoft 365 rejects at the edge with "550 5.4.1 Recipient address rejected: Access denied", and "If all recipients for your domain are in Exchange Online, DBEB is already in effect" ([Microsoft DBEB](https://learn.microsoft.com/en-us/exchange/mail-flow-best-practices/use-directory-based-edge-blocking)).
- Catch-all: a Google admin can route mail for an address "that contains one of your verified domains but that isn't associated with a user" to one mailbox ([Google routing](https://knowledge.workspace.google.com/admin/gmail/advanced/set-up-default-routing-for-your-organization), [catch-all help](https://support.google.com/a/answer/12943537)). It is an opt-in setting. Then every guess is accepted, and the check proves nothing. Hunter labels this `accept_all`: "the email address is valid but any email address is accepted by the server" ([Hunter API](https://hunter.io/api-documentation/v2)).
- How common catch-all domains are: vendor figures only, from 15% to 67% of B2B domains depending on the sample (**UNVERIFIED**, for example [bouncezero.io](https://bouncezero.io/blog/state-of-email-validation-2026), [kustiq.com](https://kustiq.com/blog/b2b-catch-all-email-benchmark)).
- Greylisting means "providing temporarily degraded service to unknown email clients", a 4xx asking the client to come back later. RFC 6647 section 4.2 notes it "can interfere" with checks that stop before sending ([RFC 6647](https://datatracker.ietf.org/doc/html/rfc6647)). A one-shot probe reads that as unknown.
- Rate limits: Gmail answers "421 4.7.28 Gmail has detected an unusual rate of email originating from your IP address" ([Google SMTP codes](https://knowledge.workspace.google.com/admin/support/troubleshooting/gmail-smtp-errors-and-codes)). A probing IP gets throttled, then blocked.
- VRFY: servers "MAY" disable it (RFC 5321, cited in the 2026-10-18 doc), so nobody relies on it.

**C.3 Port 25.** Xfinity blocks port 25 in "Both" directions: "Port 25 is unsecured, and Botnet spammers can use it to send spam" ([Xfinity blocked ports](https://www.xfinity.com/support/articles/list-of-blocked-ports)). Google Cloud "by default ... blocks egress packets sent to TCP destination port 25" ([GCP firewalls](https://docs.cloud.google.com/vpc/docs/firewalls)). AWS EC2 blocks port 25 to public addresses by default, and a customer "can request that this restriction be removed" ([AWS EC2 quotas](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-resource-limits.html)). From his Mac at home, a check over SMTP cannot run at all.

**C.4 Reacher (check-if-email-exists).** MIT license, 10,429 stars, last pushed 2026-10-30, not archived ([GitHub API](https://github.com/reacherhq/check-if-email-exists)). It runs with `docker run -p 8080:8080 reacherhq/backend:latest`. "Please note that outbound port 25 must be open." "if you wish to process more than very small volumes you will need SMTP proxy servers" (README). Its docs list the methods per provider: Gmail by SMTP only, and Hotmail consumer and Yahoo by "headless" browser or SMTP. Proxies must be SOCKS5, and the cloud provider "must allow outbound port 25 connections" ([Reacher install](https://docs.reacher.email/self-hosting/install), [search summary of config docs](https://docs.reacher.email/self-hosting/reacher-configuration-v0.10); the summary is **UNVERIFIED** in detail). Reacher's SaaS price and proxy prices did not render (**UNVERIFIED**). So the real needs are a VPS with port 25 opened by the provider, a clean IP, and a proxy pool once volume grows. All of that costs more, and carries more risk, than a $16 to $39 verifier at 2,000 a month (*inference*).

**C.5 Verification services, price per 1,000 checks** (pay as you go):

| Service | Price | Unknowns charged? | Source |
|---|---|---|---|
| Bouncer | $8 for 1,000 (minimum), $35 for 5,000, $60 for 10,000; 100 free | "never charge ... for 'unknown' results" | [usebouncer.com/pricing](https://www.usebouncer.com/pricing/) |
| NeverBounce | $8 for 1,000 per search snippet; page returned 403 | not read | **UNVERIFIED**, [neverbounce.com/pricing](https://neverbounce.com/pricing) |
| MillionVerifier | "Plans From $39"; 50,000 for $89 ($0.00178 each); 1M for $449; "Credits never expire"; refund on risky results | refund claim | [millionverifier.com](https://www.millionverifier.com/) (smallest package size **UNVERIFIED**) |
| ZeroBounce | $39 for 2,000; 100 free a month with a business domain | "we will never charge you for 'unknown' results" | [zerobounce.net pricing](https://www.zerobounce.net/email-validation-pricing) |
| Emailable | $38 for 5,000 (minimum); 250 free | refunded | [emailable.com/pricing](https://emailable.com/pricing/) |
| Hunter, Icypeas, Enrow, LeadMagic | 0.5, 0.1, 0.25, 0.25 credit per check | see B | section B |

**C.6 Does verification predict bounces at small startups on Google Workspace?** No independent measurement found. On a Workspace or Microsoft 365 domain without a catch-all route, the SMTP answer a verifier gets is the same answer Gmail's send will get, so `valid` and `invalid` should match the send closely (*inference* from the Google and Microsoft pages above). On catch-all domains and behind security gateways, the result is `accept_all`, `risky` or `unknown`, and the send is a coin flip. The cheapest way to find out for his targets is a bake-off on his own history: run the 301 first-email addresses, including the 5 that bounced, through one verifier (about $3 at Bouncer's rate, or free tiers). Then count how many of the 5 it flags and how many good addresses it wrongly flags. That needs his OK, because it shares those addresses with a vendor.

**C.7 MX lookup and the mail provider.** The MX host names the provider: Google (`aspmx.l.google.com`), Microsoft (`*.mail.protection.outlook.com`), or a gateway such as Proofpoint or Mimecast. Reacher keeps separate settings for `PROOFPOINT` and `MIMECAST` (config docs above), and Icypeas returns `"mxProvider": "google"` in its responses ([Icypeas API](https://api-doc.icypeas.com/)). What that implies: Google and Microsoft give a clear yes or no unless a catch-all is set; gateways and small hosts more often answer "accept" or "unknown" (*inference*). Knock already looks up MX (`outreach/api/mx.py`) but stores only `ok` or `none` (`mx_check` table, `outreach/store/migrations/0001_initial.sql`), and drops the host. Keeping the host would cost nothing extra.

### D. Unconventional sources of real, public addresses

**D.1 Ranked by likely yield for small US tech startups** (yield is *inference*; no source measures it):

| Rank | Source | What it gives | Terms and legal risk |
|---|---|---|---|
| 1 | Role addresses on the company site (founders@, hello@, jobs@, careers@) | An address that exists, often read by a founder at a 5 to 30 person company | Low if read by hand; check MX. Not a person's name, so a lower reply rate is likely |
| 2 | HN "Who is hiring" posts that say "email me" | A person who asked for email | HN terms: "will not engage in or use any data mining, robots, scraping" and no harvesting "email addresses ... of other users" "for sending unsolicited emails" ([YC legal](https://www.ycombinator.com/legal/)). A reply to a post that asks for email is solicited (*inference*). Knock's HN source should use the official API, not page scraping |
| 3 | Founder and engineer personal sites, blog "contact" lines | An address published for contact | Low; record the page as the source (Knock already supports `published`) |
| 4 | Job postings with an "apply by email" line | Usually a role or recruiter address | Low; already read by the posting reader |
| 5 | Team pages with `mailto:` links | Personal work addresses | Low by hand; check the site's terms before any automated crawl |
| 6 | Signatures and CCs in replies he gets | Colleagues' real addresses and the domain's real pattern | His own mail; low risk, and a strong pattern signal |
| 7 | arXiv and paper author emails (AI startups) | Often a university address, sometimes the company's | arXiv API: "no more than one request every three seconds" ([arXiv API terms](https://info.arxiv.org/help/api/tou.html)); the terms say nothing about author emails. Read by hand |
| 8 | SEC Form D | Officer names and the company, no emails | EDGAR fair access: "Current max request rate: 10 requests/second", User-Agent with a contact ([SEC](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)). Knock already uses Form D for discovery |
| 9 | Conference speaker pages, patents | Names and employers, almost never an email | Low |
| 10 | Public GitHub profile email | An address the user chose to show | **No.** GitHub's Acceptable Use Policies, section 7: "You may not use information from the Service" for spamming, including "sending unsolicited emails to users" ([GitHub AUP](https://docs.github.com/en/site-policy/acceptable-use-policies/github-acceptable-use-policies)). That covers profile fields as well as commits |
| 11 | PGP keyserver | Nothing new | keys.openpgp.org returns a key "only if it exactly matches the email address" and is built so a spammer cannot get "a list of all email addresses" ([FAQ](https://keys.openpgp.org/about/faq)). At most a tiny yes for a guess |
| 12 | WHOIS / RDAP | Nothing | ICANN's Registration Data Policy (effective 21 August 2025): a registry "MUST Redact" the registrant email, and a registrar publishes a contact that "MUST NOT identify the contact email address" ([ICANN](https://www.icann.org/resources/pages/registration-data-policy-2024-02-21-en)) |
| 13 | Contact forms | A channel, not an address | Knock cannot send through forms; he can by hand |

**D.2 Knock's own data.**
1. **Learn each domain's pattern from its own mail.** Every first email that was delivered and never bounced, and every reply, shows a working local part at that domain. Every bounce rules out one shape. Knock stores the address (`person.email`), the touch state (`open`, `replied`, `bounced`) and the bounce notice with the failed address (`outreach/sending/classify.py` keeps `failed[0]`). With 1 or 2 people per company the gain comes from the second person at a company and from reply signatures. A rule like "only `first.last` has been delivered at this domain" can warn on a draft whose address has another shape (*inference*). Across companies, his own delivered set gives a prior for small startups that no vendor publishes. A read-only count in the live checkout (local-part shape per domain, delivered versus bounced) would settle it in minutes.
2. **One shared list of bounced addresses.** `mark_email_bounced` (`outreach/mcp/tools/corrections.py`) clears a dead address from the person and logs it in `event`. I found no check in `add_people`, `intake` or the draft checks that refuses the same address if he pastes it again later, for example from a second Apollo account. A small `dead_address` table (address, first bounce, source), read by `clean_people` and by the approve checks as a block, closes that gap. It also stops a vendor's stale address from being retried (*inference* from reading the code, not from a test).

### E. Compliance (not legal advice)

- **CAN-SPAM** covers "any electronic mail message the primary purpose of which is the commercial advertisement or promotion of a commercial product or service". "The law makes no exception for business-to-business email." Opt-outs must be honored "within 10 business days", and "Each separate email in violation ... is subject to penalties of up to $53,088" ([FTC guide](https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business)). One person asking another for a job referral promotes no product, so it most likely falls outside "commercial" (*inference*, as in the 2026-10-17 and 2026-10-20 docs, which also found the 2026 penalty unchanged). It costs nothing to comply anyway: true From and subject lines, and stop at once when asked (Knock already stops on "Asked to stop").
- **Apollo**: one account only; no sharing or reselling its contact data; no building a competing database from it ([terms](https://www.apollo.io/terms)).
- **Other finders**: I did not read the terms of Hunter, Icypeas, Prospeo or the others (**not checked**). Read the one he picks before paying, for limits on multiple accounts and on storing results.
- **HN, GitHub, arXiv, SEC, WHOIS, keyserver**: see D.1. GitHub profile and commit emails are out. HN is fine only for replying to a post that asks for email, through the official API.
- **LinkedIn**: the 2026-10-20 doc covers User Agreement 8.2 (no bots, scrapers or data brokers). That is why Wiza and Snov's LinkedIn tools are flagged above.

### F. How Knock takes addresses today

- `intake` (`outreach/mcp/tools/bridge.py`) takes an `email` that is "Only an address he gave (for example from Apollo) or a page publishes. A page is not needed." An optional `email_source` must be a public https page (not LinkedIn), with `basis` `published` or `verified` and a quote. Channel defaults to email when an address is given.
- `add_people` (`outreach/mcp/tools/people.py`) stores each source with `email_basis` in `published`, `verified` or `pattern` (schema CHECK in `0001_initial.sql`). An address with no page gets the basis `provided` ("he gave it, for example from Apollo; he checks every draft in the Outbox").
- MX: the API looks it up over DNS over HTTPS (Cloudflare, then Google) and caches `ok` or `none` for 24 hours. The MCP server ("this server has no network") and the sender ("makes no DNS call") read only the cache. A missing MX blocks approval; `unknown` only warns.
- #9 rules that bind any change: an address he supplies goes straight to the Outbox with a draft (intake ruling, 2026-10-28); a bounce rate never pauses sending (bounce ruling, 2026-10-30); Claude never approves or sends.

**Where a verifier or finder plugs in without breaking those rules:**
1. **Cache, then read.** A verifier or finder call runs where the MX lookup runs (the API, or a new launchd job), never in the MCP server or the sender, and writes a cache table (for example `email_check`: address, verdict, vendor, checked_at). The draft checks read it the way they read `mx_check`: `invalid` blocks, `accept_all` or `risky` or `unknown` warns. The sender re-reads it in its pre-claim check as it does now.
2. **Provenance.** Add a basis such as `vendor` (with the vendor's name) beside `published`, `verified`, `pattern` and `provided`, so the ranking and the bounce numbers can later be split by source. That needs a migration, because `EmailSourceIn` accepts only a public page today.
3. **Spend.** Any call that costs credits needs a monthly cap in config and his explicit say-so in #9 before Claude can trigger it. Until then, he runs the finder himself and pastes the result, which `intake` already accepts.
4. **Dead addresses.** The bounced-address list from D.2 is checked in `clean_people` (rejects the add with a plain reason) and in the approve checks (a block).

## 5. Unverified or could not check

- Apollo: credits included per plan, whether $49 and $99 are monthly or yearly-per-month rates, the free plan's own monthly grant (secondary sites say 75), the price per add-on credit, mobile credit cost, and whether an email Apollo cannot verify costs a credit (the about-credits page shows blank figures).
- RocketReach prices (page rendered no text); NeverBounce prices (403, search snippet only); MillionVerifier's smallest package and its price; Dropcontact's price at 2,000 credits; FullEnrich's price at 2,000; HubSpot Breeze pricing.
- Snov.io: whether a miss costs a credit. Icypeas: an explicit "no charge on a miss" line in its docs, and how catch-all results are billed. Anymailfinder: how a catch-all result is billed now that "searches no longer return risky emails".
- Where each vendor's data comes from, and whether it is built from LinkedIn. None says.
- The share of small-startup domains that are catch-all; the pattern mix at small startups (vendor figures only).
- Reacher SaaS price, proxy25 price, and whether Google Workspace answers a fresh VPS IP reliably at small volume.
- The terms of service of every finder except Apollo.
- Whether the Apollo MCP's people search works for a free account opened with a .edu address (Apollo's rule names "a personal email address" without defining it).

## 6. Questions only Devanshu can answer

1. What can he spend a month: $0, about $40, about $100? Does he want to pay at all?
2. Is 2,000 new addresses a month the real need? If "500 emails a week" counts follow-ups (up to 4 per thread under #9), new addresses may be closer to 400 to 800 a month. Today's plan of about 8 companies a day with 1 or 2 emailed people each points the same way.
3. Will he stop using more than one free Apollo account, given the termination clause?
4. Which email address are his Apollo accounts registered with? A "verified corporate domain" lifts the free cap from 100 to 10,000 credits a month.
5. May Claude spend credits by calling a finder (a new #9 line with a monthly cap), or does he always run the finder and paste the result?
6. Does #9's "no hosted MCP" line also cover connecting Apollo's hosted MCP to Claude Code? If he connects it, the tools that send email or enroll sequences must stay unused.
7. Will he email role addresses (founders@, hello@) at very small companies?
8. May the 301 sent addresses go to one verifier for the bake-off in C.6 (about $3, or free tiers)?
9. What bounce rate is acceptable to him, given that a bounce no longer pauses sending but still makes a day not clean?
