-- 0015: client wording on each decision (#257, #9 job board ruling 2026-11-12 point 3). Adds one column and recreates
-- the two views that name decision columns, as 0014 did. Rows already stored keep for_client NULL until the rebuild
-- under the new ruleset writes every current row; readers treat NULL as not a client posting.
ALTER TABLE decision ADD COLUMN for_client INTEGER CHECK (for_client IN (0, 1));

DROP VIEW current_decision;
CREATE VIEW current_decision AS
SELECT d.* FROM decision d
JOIN ruleset r ON r.ruleset_version = d.ruleset_version AND r.is_current = 1;

DROP VIEW posting_decision;
CREATE VIEW posting_decision AS
SELECT d.posting_id, d.ruleset_version, d.decided_at, d.lane, d.level_tier, d.level_ok, d.level_basis,
       d.years_asked, d.years_quote, d.us, d.sponsorship, d.sponsorship_quote, d.citizenship, d.citizenship_quote,
       d.eligibility, d.eligibility_reason, d.eligibility_quote, d.start_2027, d.provisional, d.reader_version,
       d.level_state, d.years_ms, d.role, d.for_client
FROM decision d
WHERE d.rowid = (
  SELECT d2.rowid FROM decision d2 JOIN ruleset r ON r.ruleset_version = d2.ruleset_version
  WHERE d2.posting_id = d.posting_id
  ORDER BY r.is_current DESC, d2.decided_at DESC, d2.ruleset_version DESC
  LIMIT 1);
