> **Evidence only.** Decisions live in issue #9; where this file disagrees, #9 wins. Overruled by #9: paid Claude API briefs (#9: Claude Code on the Northeastern seat, paid API capped at $2 a month), LinkedIn invite and note caps enforced by the system (#9: Devanshu handles LinkedIn himself), Datasette and Hunter (not adopted).

# Outreach and tracking: OSS components and platform rules

Researched 2026-10-17. GitHub numbers come from `gh api repos/<owner>/<repo>` on that date. "Unverified" marks claims taken from third-party pages or from a page I could not read directly.

## 1. Tracking and CRM

| Tool | License | Last push | Stars | Works on his SQLite file? | Laptop weight |
|---|---|---|---|---|---|
| [Twenty](https://github.com/twentyhq/twenty) | AGPLv3 plus commercial "Enterprise" files ([LICENSE](https://raw.githubusercontent.com/twentyhq/twenty/main/LICENSE)) | 2026-10-17 | 58.4k | No. Postgres, Redis, server and worker containers | Heavy. Docs ask for at least 2 GB RAM ([docs](https://docs.twenty.com/developers/self-host/capabilities/docker-compose)) |
| [Monica](https://github.com/monicahq/monica) | AGPL-3.0 | 2026-09-24 | 25.5k | No. Its own schema. Main branch is beta and defaults to SQLite ([.env.example](https://github.com/monicahq/monica/blob/main/.env.example)); stable 4.x uses MySQL. Last release v4.1.2, May 2024 | Medium (PHP app) |
| [NocoDB](https://github.com/nocodb/nocodb) | "Sustainable Use License", fair-code, not OSI ([LICENSE](https://raw.githubusercontent.com/nocodb/nocodb/develop/LICENSE.md)) | 2026-10-17 | 65.3k | Partly. It can store its own data in SQLite ([README](https://github.com/nocodb/nocodb)), but the docs list only Postgres and MySQL as external sources ([docs](https://nocodb.com/docs/product-docs/data-sources/connect-to-data-source)) | Medium (one Docker container) |
| [Baserow](https://github.com/baserow/baserow) | MIT client JS, separate premium/enterprise licenses ([LICENSE](https://raw.githubusercontent.com/baserow/baserow/develop/LICENSE)) | 2026-10-16 | 6.2k | No. The all-in-one image runs Postgres and Redis inside ([docs](https://baserow.io/docs/installation/install-with-docker)) | Heavy |
| [Teable](https://github.com/teableio/teable) | AGPL-3.0 core, MIT packages ([LICENSE](https://raw.githubusercontent.com/teableio/teable/develop/LICENSE)) | 2026-10-17 | 21.9k | No. "Real PostgreSQL underneath" ([README](https://github.com/teableio/teable)) | Heavy |
| [Grist](https://github.com/gristlabs/grist-core) | Apache-2.0 | 2026-10-17 | 12.0k | No. Each `.grist` document is a SQLite file in Grist's own layout, so other tools can read it, but Grist will not open an arbitrary schema ([README](https://github.com/gristlabs/grist-core)) | Light (one container) |
| [Datasette](https://github.com/simonw/datasette) | Apache-2.0 | 2026-10-13 | 11.5k | Yes. Points straight at a SQLite file. 1.0 alphas (1.0a41, 2026-09-24) have a JSON write API with insert, update, upsert and delete behind tokens ([docs](https://docs.datasette.io/en/latest/json_api.html)) | Very light (pip install) |
| [datasette-write-ui](https://github.com/datasette/datasette-write-ui) | Apache-2.0 | 2026-03-15 | 24 | Adds edit, insert and delete buttons to Datasette tables | Plugin |
| [sqlite-web](https://github.com/coleifer/sqlite-web) | MIT | 2026-10-15 | 4.2k | Yes. `sqlite_web /path/to/db` | Very light (Flask) |

Job-search projects worth reading, not adopting:

- [career-ops-hq/career-ops](https://github.com/career-ops-hq/career-ops) (MIT, 75.3k stars, pushed 2026-10-17). A Claude Code or Codex workflow over markdown files in `data/`. It drafts emails but never sends them, keeps a follow-up cadence calculator, classifies replies into tracker updates, and joins a LinkedIn connections export against target companies ([README](https://github.com/career-ops-hq/career-ops)).
- [Gsync/jobsync](https://github.com/Gsync/jobsync) (MIT, 1.4k stars, pushed 2026-10-12). Self-hosted tracker on Prisma and SQLite with an MCP server ([README](https://github.com/Gsync/jobsync)). It owns its schema, so it would replace his data model.

Verdict. Skip Twenty, Baserow and Teable: each needs Postgres and a second copy of the data. Skip Monica, which tracks personal relationships, not pipelines. NocoDB and Grist would mean migrating out of his schema. Adopt Datasette with datasette-write-ui as a free admin and query view over the same SQLite file, or sqlite-web if he wants something smaller. Borrow ideas from career-ops (follow-up cadence, reply classification, connections-export join). Keep the main UI as his own dashboard.

## 2. Finding and verifying work emails

OSS:

- [reacherhq/check-if-email-exists](https://github.com/reacherhq/check-if-email-exists): MIT, 10.2k stars, pushed 2026-10-16. Returns `is_reachable` as `safe`, `risky`, `invalid` or `unknown`, plus an `is_catch_all` flag. The README says "outbound port 25 must be open" and that more than very small volumes need SMTP proxies ([README](https://github.com/reacherhq/check-if-email-exists)).
- [AfterShip/email-verifier](https://github.com/AfterShip/email-verifier): MIT, 1.6k stars, pushed 2026-10-13. Ships with SMTP checks off "because most of the ISPs block outgoing SMTP requests through port 25" ([README](https://github.com/AfterShip/email-verifier)). Comcast confirms it does not support port 25 for residential customers ([Xfinity](https://www.xfinity.com/support/internet/email-port-25-no-longer-supported/)).
- Permutation tools on GitHub are stale (top hit [emeth-/Email-Permutator](https://github.com/emeth-/Email-Permutator), last pushed 2021). Writing the `first.last@` and `flast@` patterns yourself takes about 20 lines.

Accuracy limits. A catch-all domain accepts every RCPT, so SMTP probing cannot tell a real mailbox from a guess. Reacher flags these rather than resolving them. From a home connection with port 25 blocked, SMTP checks will fail or return `unknown`. MX and syntax checks still work.

Commercial free tiers, checked 2026-10-17:

| Service | Free | Cheapest paid | Source |
|---|---|---|---|
| Hunter | 50 credits/month; a verification costs 0.5 credit | Starter $49/month or $34/month billed yearly, 2,000 credits | [pricing](https://hunter.io/pricing) |
| Snov.io | Trial plan, 50 credits and 100 recipients, renews monthly | Starter $39/month, 1,000 credits | [pricing](https://snov.io/pricing) |
| Apollo | Free plan exists with no time limit; credit counts render client-side and I could not read them. Third-party pages say about 100 email credits/month (unverified) | Not readable | [pricing](https://www.apollo.io/pricing), [costbench](https://www.costbench.com/software/ai-sales-tools/apollo/free-plan/) |
| Prospeo | 75 credits (unverified, third-party); the pricing page did not render | $39/month, 1,000 credits (unverified) | [usebouncer review](https://www.usebouncer.com/prospeo) |

Prospeo, Apollo and Snov also sell Chrome extensions that read LinkedIn pages (unverified, I did not check their store listings). LinkedIn bans extensions that scrape or automate its site (section 4), so he should use only their web apps and domain search.

CAN-SPAM. The Act covers messages whose "primary purpose" is "the commercial advertisement or promotion of a commercial product or service". It has no B2B exemption. Each violating email can cost up to $53,088 ([FTC guide](https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business)). A one-to-one note asking an engineer about a referral promotes no product, so it most likely falls outside "commercial". That is my reading, not legal advice. Complying anyway is cheap: accurate From and subject lines, and honoring any "please stop" at once (the Act allows 10 business days).

## 3. Deliverability

- Google's sender guidelines took effect 2024-02-01 for mail sent *to* personal Gmail accounts. All senders need SPF or DKIM, valid forward and reverse DNS, TLS, and a spam rate below 0.3% in Postmaster Tools. Google advises keeping it below 0.1% ([guidelines](https://support.google.com/a/answer/81126)).
- Senders of about 5,000 or more messages a day to Gmail must add DMARC, From alignment and one-click unsubscribe. Bulk status never expires. One-click unsubscribe applies only to marketing and promotional mail ([FAQ](https://support.google.com/a/answer/14229414)).
- What applies at 25 a day from a personal Gmail: none of the bulk rules. Google's servers sign and send his mail, so the authentication items are handled for him (my inference; Google does not state this for gmail.com senders). The spam-rate rule is the one he controls. Postmaster Tools needs a domain he owns, so he cannot see the rate for gmail.com (inference).
- Limits: personal Gmail stops sending after more than 500 messages a day or 500 recipients in one message, and recovers within 1 to 24 hours ([Gmail help](https://support.google.com/mail/answer/22839)). The Gmail API charges 100 quota units per `messages.send` against 6,000 units per user per minute ([quota](https://developers.google.com/workspace/gmail/api/reference/quota)). His 25 a day at 10-minute spacing is far under both.
- Reply rates: I found no independent primary study on job-referral cold email. Instantly reports a 3.43% average human reply rate on its own platform for 2025 (replies divided by delivered, excluding auto-responses). That figure covers sales email, not job seekers ([Instantly](https://instantly.ai/blog/ai-sales-agent-benchmarks-2026-the-complete-performance-report/)).

## 4. LinkedIn rules

- The User Agreement (effective 2025-11-03), section 8.2, forbids software, scripts, "browser plugins and add-ons" that scrape or copy the Services. It also forbids "bots or other unauthorized automated methods to... add or download contacts, send or redirect messages", and bypassing "use limits" ([User Agreement](https://www.linkedin.com/legal/user-agreement)).
- The help page on prohibited software bans third-party tools that "scrape, modify the appearance of, or automate activity" on the site. Users "risk having their accounts restricted or shut down" ([help 56347](https://www.linkedin.com/help/linkedin/answer/56347)).
- Weekly invitation limit: LinkedIn confirms a limit exists and that hitting it brings a restriction that "typically lasts one week" and that LinkedIn "cannot remove or shorten". It does not publish the number ([help a550555](https://www.linkedin.com/help/linkedin/answer/a550555), [help 4800](https://www.linkedin.com/help/linkedin/answer/4800)). The commonly cited figure of about 100 a week comes from third parties (unverified, e.g. [contentin.io](https://contentin.io/glossary/weekly-invitation-limit/)). After withdrawing an invite he cannot resend to that person for up to three weeks ([help 4800](https://www.linkedin.com/help/linkedin/answer/4800)).
- Invitation notes: "Each message can be up to 200 characters." Free members can add a note to "up to three connection requests per month", and Premium members get unlimited personalized invitations ([help 46662](https://www.linkedin.com/help/linkedin/answer/46662)). Another LinkedIn page says five per month ([help a550555](https://www.linkedin.com/help/linkedin/answer/a550555)). LinkedIn contradicts itself, so plan for three.
- Allowed import: Settings, Data privacy, "Get a copy of your data" exports 1st-degree connections as CSV ([help a566336](https://www.linkedin.com/help/linkedin/answer/a566336)). Loading that file into SQLite by hand is not automation of the site.

Planning numbers for the system: at most about 80 invites a week to stay under the unpublished limit, 3 notes a month on a free account, and every other first touch as a blank invite followed by a DM after acceptance. The 80 figure is my own margin under the unverified 100.

## 5. Research-brief agents

| Project | License | Last push | Stars | LLMs and search | Cost per report |
|---|---|---|---|---|---|
| [assafelovic/gpt-researcher](https://github.com/assafelovic/gpt-researcher) | Apache-2.0 | 2026-10-10 | 30.1k | 27 LLM providers including `anthropic`, `ollama`, `openrouter` ([base.py](https://github.com/assafelovic/gpt-researcher/blob/main/gpt_researcher/llm_provider/generic/base.py)); defaults to Tavily, also DuckDuckGo, Brave, SearXNG, Exa, MCP ([docs](https://docs.gptr.dev/docs/gpt-researcher/search-engines)) | About $0.12 for a standard report; about $0.40 and 5 minutes for "deep" mode on o3-mini ([README](https://github.com/assafelovic/gpt-researcher)) |
| [langchain-ai/open_deep_research](https://github.com/langchain-ai/open_deep_research) | MIT | 2026-08-10, **archived** | 12.7k | Many providers; Tavily by default, native Anthropic and OpenAI search | $46 to $187 for a 100-question benchmark run, so about $0.46 to $1.87 each ([README](https://github.com/langchain-ai/open_deep_research)) |
| [stanford-oval/storm](https://github.com/stanford-oval/storm) | MIT | 2025-09-30 | 31.7k | Any litellm model; You, Bing, Serper, Brave, SearXNG, DuckDuckGo, Tavily ([README](https://github.com/stanford-oval/storm)) | Not published. Writes Wikipedia-style articles |
| [dzhng/deep-research](https://github.com/dzhng/deep-research) | MIT | 2026-04-11 | 19.8k | OpenAI-compatible endpoints and Fireworks; Firecrawl for search ([README](https://github.com/dzhng/deep-research)) | Not published |

Claude API alone. The web search tool costs $10 per 1,000 searches plus tokens, and results count as input tokens ([docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool)). Web fetch has no extra charge. Code execution is free alongside `web_search_20260209` or later. The Batch API halves token prices ([pricing](https://platform.claude.com/docs/en/about-claude/pricing)). Sonnet 5.5 is $2 in and $10 out per million tokens. Haiku 5.5 is $0.10 and $0.50 for prompts up to 100,000 tokens ([pricing](https://platform.claude.com/docs/en/about-claude/pricing)).

My estimates, not measured:

- A light brief for each of the 48 weekly companies on Haiku 5.5, with 3 searches and about 40k input tokens, costs about $0.035.
- A conviction brief on Sonnet 5.5, with 15 searches, about 150k input and 5k output, costs about $0.50.
- A month (about 200 light and 8 deep briefs) costs about $11. Batch saves under $1, because search fees are not discounted. Reusing briefs for companies that recur across weeks cuts the total further.

For a one-page company brief, one API call with `max_uses`, a fixed template and built-in citations does the same job as these frameworks. GPT Researcher is the only one worth keeping as an option for longer conviction reports.

## Recommendation

1. Keep SQLite as the single store and his dashboard as the main UI. Add tables for `companies`, `people`, `touches` (channel, direction, sent_at, body, status), `applications` and `briefs`. Migrate the localStorage, JSON and markdown tracking into them.
2. Run `datasette` with `datasette-write-ui` locally for ad-hoc editing and SQL. Skip Twenty, NocoDB, Baserow, Teable and Grist.
3. Email finding: generate patterns locally, then use Hunter's free 50 credits for domain pattern lookups and verification of the most important contacts. Do not self-host Reacher on a home connection. Keep the current caps, which sit far below Gmail's limits. Log bounces and opt-outs, and never re-email anyone who opts out.
4. LinkedIn stays manual. The system queues people, drafts notes under 200 characters, counts invites per rolling 7 days against a cap of 80, tracks the 3 notes a month, and imports the official connections export. No extensions from Apollo, Prospeo or Snov on LinkedIn pages.
5. Briefs: one Claude API call with web search per company, Haiku for the weekly 48 and Sonnet for the 2 conviction bets, saved to `briefs` with source URLs. Expect about $11 a month or less.
