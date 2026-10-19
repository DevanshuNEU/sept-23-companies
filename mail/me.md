# Evidence bank for cold emails

The only facts a draft may use about Devanshu, besides what he pastes about the person. Every line names its
source; nothing here is invented. Devanshu edits this file. Lines under "Needs Devanshu" are open questions, not
facts: a draft never uses them, and `mail/check_draft.py` blocks any value quoted in backticks on an `ASK:` line.

Sources: [site] https://www.devanshuchicholikar.com, [resume] https://www.devanshuchicholikar.com/resume,
[page] its project page (`/projects/<name>`), [gh] the GitHub repo or profile named. Read 2026-10-19.

## Who
- Devanshu Chicholikar, software engineer and AI engineer: MCP servers, RAG, evals with LLM-as-a-judge, voice
  agents, plus the full-stack and infra work around them. [site] [gh profile]
- MS, Software Engineering Systems, Northeastern University, Sep 2024 to May 2026, GPA 3.85. [resume]
  Say it as "graduated from Northeastern in May 2026" or leave it out; never "MS from Northeastern".
- B.Tech, Information and Communication Technology, Dhirubhai Ambani Institute of ICT, Gujarat, India,
  2018 to 2022. [resume]
- Based in Boston, MA; open to relocation [resume]; San Francisco preferred (Devanshu's earlier note in this file).
- On F-1 OPT: authorized to work in the US now (Devanshu's earlier note in this file). Say this plainly when the
  role or person makes it relevant; never claim he needs no sponsorship ever.
- Open to Software Engineer, AI Engineer and Forward Deployed Engineer roles. [site] [gh profile]

## Experience
- Software Engineer, B2B Industrial-Equipment Platform, Jaksh Enterprise, Ahmedabad, India, Aug 2022 to Jul 2024.
  [resume]
  - Built a Java / Spring Boot product-catalog and quotation engine for 590+ customizable products serving a 10K+
    B2B customer base, on PostgreSQL with a rules-based pricing module.
  - Cut quote-page p95 latency 65% (800ms to 280ms) with PostgreSQL indexing, Redis caching and async processing.
  - One of two engineers: gathered client requirements, scoped specs, shipped through Dockerized CI/CD on GitHub
    Actions; release cycles went from 2 weeks to 3 days.
- Software Development Engineer Intern, Pitney Bowes, Pune, India, Jan 2022 to Jul 2022. [resume]
  - Built REST APIs for PitneyShipPro with idempotent endpoints, removing 40% of manual data entry; raised
    automated test coverage to 85% with Jest and Cypress.
- Graduate Teaching Assistant, CSYE 6225 Network Structures and Cloud Computing, Northeastern, Sep 2025 to
  May 2026. [resume]
  - Taught AWS, Terraform and distributed systems to 100+ graduate students across two semesters.
  - Wrote a Docker + GitHub Actions CI/CD lab adopted as official course content across 3 sections (180+ students).
  - Led system-design and code reviews for 15 cloud-native API project teams.

## Projects (pick the one closest to the role; its link is the email's second link)

- **OpenCodeIntel**, live: https://opencodeintel.com. Fits: AI coding tools, MCP and agent infra, search and
  retrieval, devtools. [gh OpenCodeIntel/opencodeintel] [page] [resume]
  - Code-search platform for AI coding agents: a web app, a REST API and a 12-tool MCP server (stdio for local
    agents, streamable HTTP for hosted Claude connectors).
  - Vector search fused with BM25 keyword scoring and reranking, over tree-sitter function-level chunks.
  - 94% average Hit@1 on a 665-query research eval across 14 open-source codebases; a 98-run ablation isolated
    reranking at +8.4 points. Say "research eval": the repo notes most queries came from function names.
  - Public playground search: 641ms median cold, 242ms cached.
  - Built since Nov 2025. Writing: "Rerankers made my code search worse. Then the same one made it better."
    (https://www.devanshuchicholikar.com/writing/rerankers-code-search)
- **Overhear**: https://github.com/DevanshuNEU/overhear. Fits: voice AI, evals, LLM-as-a-judge, QA, healthcare
  workflows. [gh DevanshuNEU/overhear] [page] [resume]
  - AI QA analyst for voice agents: grades every call a Retell healthcare-scheduling agent takes. Code checks what
    the call's action log proves (3 of 7 rubric dimensions, 50% of the score); an LLM judge checks what the agent
    said against the clinic's real open slots.
  - On a 39-call golden dataset with 4 planted failure types: caught 23 of 23 planted failures, macro-F1 0.89,
    0.99 self-consistency across 3 repeat runs. The set is small, mostly synthetic and labeled by one person; say so if
    the reader is an eval person.
  - Next.js, TypeScript, Retell, Claude API, Postgres, Drizzle, Vitest. Built Sep 2026. The hosted app is behind a
    login, so link the repo.
  - Writing: "My LLM judge missed a skipped ID check. 24 lines of code didn't."
    (https://www.devanshuchicholikar.com/writing/llm-judge-missed-id-check)
- **CallBudget**: https://github.com/DevanshuNEU/callbudget. Fits: voice agents, applied ML, healthcare, agent
  tooling. [gh DevanshuNEU/callbudget] [page] [resume]
  - Predicts which pharmacy most likely has a hard-to-find drug and ranks who a voice agent should call first.
  - On a simulated 19-pharmacy Boston pool (real pharmacies, simulated stock), expected calls to find the drug
    fell from 4.3 to 2.3 (47%). Always say "simulated".
  - An abstention layer on the stock extractor took false "in stock" answers from 10% to 0% under a deliberately
    unreliable extractor, in the evals (wiring it into the live call is next).
  - 4-tool FastMCP server; Claude-driven voice agent with Pipecat, Deepgram and Cartesia.
- **Saar** (Chrome extension): https://getsaar.com. Fits: browser extensions, devtools, AI cost and usage,
  front-end. [gh OpenCodeIntel/lco] [page] [resume]
  - Published on the Chrome Web Store (cleared review May 2026). Tracks Claude token usage and cost in real time,
    entirely in the browser, no backend.
  - Chrome MV3: page script that reads the streaming responses, Shadow DOM overlay, service worker running a BPE
    tokenizer. 1,808 Vitest tests across 63 files, plus Playwright end-to-end tests.
- **saar CLI**: https://pypi.org/project/saar/. Fits: developer tooling, AI coding agents. [gh OpenCodeIntel/saar]
  [site] [PyPI]
  - Published on PyPI. Statically analyzes a codebase and writes agent context files (AGENTS.md, CLAUDE.md,
    .cursorrules).
- **tool-crowding**: https://github.com/DevanshuNEU/tool-crowding. Fits: agent, MCP, tool-calling and eval
  companies. [gh DevanshuNEU/tool-crowding] [site]
  - An open, pre-registered benchmark for whether an agent picks the wrong tool when 10 to 20 MCP servers are
    installed at once, separating prompt length from interference between similar tool descriptions.
  - Pre-pilot: 345 tests, a 199-tool synthetic corpus, 19 exploratory trials. Describe it as being built; it has no
    results to claim.
- Smaller, for infra or cloud roles: **SecureScale**, multi-AZ AWS infrastructure as modular Terraform
  (provisioning 2 hours to 10 minutes, cloud spend down 30%, 99.9% uptime) [resume]; **Mem Machines**, serverless
  data ingestion on GCP (Pub/Sub, Cloud Run, Firestore) with multi-tenant isolation and PII redaction
  (https://github.com/DevanshuNEU/mem-machines) [gh]; **watsonx Test Forge**, an IBM watsonx Orchestrate hackathon
  agent that generates test cases from deployed agent specs [site].

## Links
- Portfolio, always the first link: https://www.devanshuchicholikar.com [site]
- Resume (a page, never an attachment): https://www.devanshuchicholikar.com/resume [site]
- LinkedIn: https://www.linkedin.com/in/devanshuchicholikar/ [site]
- GitHub: https://github.com/DevanshuNEU [gh profile]
- Email: chicholikar.d@northeastern.edu [site]

A first email carries exactly 2 links: the portfolio, then one project link from above. The resume, LinkedIn and
GitHub are one click away from the portfolio, so they stay out of the body.

## Signature
Devanshu Chicholikar

## Needs Devanshu
Open questions from #8. Answer one by moving the fact above with "(Devanshu, <date>)" as its source and deleting
the ASK line.

- ASK: work authorization line for recruiters. Public pages say nothing past F-1 OPT. Is it STEM OPT, and through
  when? Until answered, drafts say only "on F-1 OPT, authorized to work in the US now" and never `STEM OPT`.
- ASK: sponsorship and H-1B, if at all. Until answered, drafts never mention `H-1B` and never say `no sponsorship`.
- ASK: earliest start date. Until answered, drafts name no start date.
- ASK: a Saar user or install count, if you want one quoted. No public source states one; until answered, drafts
  give no `installs` number.
- ASK: a public Overhear demo link. The hosted app is behind a login; until answered, the second link is the repo.
- ASK: phone number for the signature (optional). Until answered, the signature is the name only.
- ASK: review this file once (#10). Every number above comes from your own public pages; confirm each is current,
  the Jaksh and Pitney Bowes numbers above all.
