-- 0007: the level verdict and the master's-degree years on each decision (#154). Only adds two columns and recreates
-- the two views that name decision columns: nothing is rebuilt, 0001 to 0006 are never edited. ADD COLUMN is instant;
-- the rows already stored keep both columns NULL until the rebuild under the new ruleset writes every current row.
-- level_state: fit (his level), reject (not his level) or uncertain (a tech posting that does not say). years_ms: the
-- years asked of a master's degree holder, NULL when the posting gives no degree tier figure.
ALTER TABLE decision ADD COLUMN level_state TEXT CHECK (level_state IN ('fit','reject','uncertain'));
ALTER TABLE decision ADD COLUMN years_ms INTEGER;

DROP VIEW current_decision;
CREATE VIEW current_decision AS
SELECT d.* FROM decision d
JOIN ruleset r ON r.ruleset_version = d.ruleset_version AND r.is_current = 1;

DROP VIEW posting_decision;
CREATE VIEW posting_decision AS
SELECT d.posting_id, d.ruleset_version, d.decided_at, d.lane, d.level_tier, d.level_ok, d.level_basis,
       d.years_asked, d.years_quote, d.us, d.sponsorship, d.sponsorship_quote, d.citizenship, d.citizenship_quote,
       d.eligibility, d.eligibility_reason, d.eligibility_quote, d.start_2027, d.provisional, d.reader_version,
       d.level_state, d.years_ms
FROM decision d
WHERE d.rowid = (
  SELECT d2.rowid FROM decision d2 JOIN ruleset r ON r.ruleset_version = d2.ruleset_version
  WHERE d2.posting_id = d.posting_id
  ORDER BY r.is_current DESC, d2.decided_at DESC, d2.ruleset_version DESC
  LIMIT 1);
