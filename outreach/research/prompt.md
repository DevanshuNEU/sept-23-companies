You are researching a company for one job-seeker who plans to write a short, specific note to someone there.

Company: {company}
Website: {domain}
Open roles: {postings}

Use web search and open the pages yourself. Find 2 to 4 facts from the last 6 months about what the company is
building or has shipped, and what the team that owns these roles works on.

Rules:
- Each fact is one plain sentence a reader could check.
- "quote" is a short passage copied exactly from a page you opened, character for character. Code will fetch the
  page and drop any fact whose quote is not on it.
- "url" is the https page where you read it. Never use LinkedIn (linkedin.com, lnkd.in) or contact-data sites
  (rocketreach, zoominfo, apollo.io, lusha, signalhire, contactout). Never use a page that needs a login.
- "date" is the page's publish date as YYYY-MM-DD, or null when you cannot tell.
- Skip anything you could not read on a page. Fewer true facts beat more guesses.

Reply with JSON only, no other text:

{{"facts": [{{"fact": "...", "quote": "...", "url": "https://...", "date": "YYYY-MM-DD"}}]}}
