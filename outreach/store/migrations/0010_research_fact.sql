-- 0010: checked company facts (#205). Adds one table and nothing else: 0001 to 0009 are never edited. A row is a fact
-- about a company that code found on an https page, with the quote it matched there. The table refuses LinkedIn links
-- (linkedin.com, lnkd.in), non-https links, and an over-long fact or quote. found_by says who found it: gpt, claude or
-- hand. verified_at is when code last saw the quote on the page (UTC); a fact is fresh for 14 days after it.
CREATE TABLE research_fact (
  fact_id     INTEGER PRIMARY KEY,
  company_id  INTEGER NOT NULL REFERENCES company(company_id),
  fact        TEXT NOT NULL CHECK (length(fact) BETWEEN 1 AND 300),
  quote       TEXT NOT NULL CHECK (length(quote) BETWEEN 1 AND 500),
  url         TEXT NOT NULL CHECK (url LIKE 'https://_%' AND instr(lower(url), 'linkedin.com') = 0
                                   AND instr(lower(url), 'lnkd.in') = 0),
  page_date   TEXT CHECK (page_date IS date(page_date)),
  found_by    TEXT NOT NULL CHECK (found_by IN ('gpt', 'claude', 'hand')),
  verified_at TEXT NOT NULL CHECK (verified_at IS strftime('%Y-%m-%dT%H:%M:%SZ', verified_at)),
  UNIQUE (company_id, url, quote)
) STRICT;
CREATE INDEX research_fact_company ON research_fact(company_id);
