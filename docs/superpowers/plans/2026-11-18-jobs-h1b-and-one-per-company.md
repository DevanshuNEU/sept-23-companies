# Jobs: H-1B chips and One per company (#309)

He asked for this on 2026-11-18 in a grilling session: companies that have sponsored H-1B are the ones he reaches out to, so the
job list should show only those, and show each company once.

## Rulings (his answers)

- **Has H-1B** keeps a company when the H-1B list holds approvals for it, whatever the trend ("stopped" and "one year" count).
  **No H-1B record** keeps the rest. **Any H-1B** is the default and hides nothing.
- The chip uses the row tag's own lookup (`outreach.h1b.lookup.tag_for`), so a chip and a tag never disagree. The match stays
  strict. Better name matching is a separate job.
- **One per company** (off by default): each company shows once per tab, by its newest posting under every other filter, sorted by
  that posting. The row says "N more roles"; they open in place with Pass and Mark applied. Mark reached stays on the leading row.
- Both work on all three tabs and the tab counts follow them. Both are remembered in the browser. Jobs only; the Outbox is untouched.

## Design

- `JobFilters` gains `h1b`, `one_per_company` and `company_id`. `GET /api/jobs` takes `h1b`, `oneRowPerCompany` and `companyId`; a row
  carries `moreRoles`. No schema change, no query per row.
- H-1B: an SQLite function `h1b_has(name)` registered on the read connection, called in the `rows` filter. Off, the `CASE` never
  calls it.
- One per company: a `shown` CTE over `rows`: `ROW_NUMBER() OVER (PARTITION BY cid, grp ORDER BY unk, at DESC, rank, posting_id)`
  keeps row 1 and `COUNT(*) OVER (PARTITION BY cid, grp) - 1` is `more_roles`. The cursor is unchanged, since the shown row keeps
  its `(unk, at, rank, posting_id)`. Off, `shown` is a pass-through.
- `company_id` lists one company's postings (the survivor after a merge): the rows behind "N more roles".
- With no H-1B list built, `has` and `none` are a 409 in plain words (error is not empty), never an empty list. The page shows the
  message and "Show every company".
- Web: two chip groups beside the level chips, `filters-store.ts` for the browser memory (a bad stored value means off), a `MoreRoles`
  block under a leading row. The keys stay on the leading rows. The headline counts companies when One per company is on.

## Tests

- `tests/test_pipeline_joblist_h1b_company.py`: each rule above at the query.
- `tests/test_api_jobs.py`: the wire, the 409, the 400, the view cache keeping the filters apart.
- `web/src/routes/browse/browse.test.tsx`, `web/src/api/http/jobs.test.ts`: chips, expansion, Pass under "more roles", memory, the
  no-list message.

## Known limits

- With One per company on, a company with over 50 matching postings opens its newest 50 and says so.
- Passing the leading row hides it at once; the company's next role leads after the list reads again.
