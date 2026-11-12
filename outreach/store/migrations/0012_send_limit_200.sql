-- 0012: the send limit is 200 a day (#9 send limit 200 ruling, 2026-11-12; #227), so send_day.send_limit runs 0 to
-- 200. 0001 to 0011 are never edited. SQLite cannot change a CHECK in place, so the table is rebuilt as 0005 rebuilt
-- message: new table, copy, drop, rename. send_day has no index or trigger, and no table refers to it by foreign key.
-- The columns keep their order, closed_at (0002) last.
CREATE TABLE send_day_new (
  day          TEXT PRIMARY KEY CHECK (day IS date(day)),
  send_limit   INTEGER NOT NULL CHECK (send_limit BETWEEN 0 AND 200),
  outcome      TEXT CHECK (outcome IN ('clean','warning','hard_block')),
  outcome_note TEXT NOT NULL DEFAULT '',
  created_at   TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%SZ', created_at)),
  closed_at    TEXT CHECK (closed_at IS strftime('%Y-%m-%dT%H:%M:%SZ', closed_at))
) STRICT;

INSERT INTO send_day_new(day, send_limit, outcome, outcome_note, created_at, closed_at)
  SELECT day, send_limit, outcome, outcome_note, created_at, closed_at FROM send_day;
DROP TABLE send_day;
ALTER TABLE send_day_new RENAME TO send_day;
