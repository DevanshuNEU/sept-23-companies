-- 0002: the sending engine's state (#14). Adds columns only; 0001 is never edited.
-- A column added with NOT NULL carries a default, so the existing sender row stays valid.

-- Gmail history cursor: '' means read every tracked thread once, then take the profile's history id.
ALTER TABLE sender ADD COLUMN history_id TEXT NOT NULL DEFAULT '';
-- When the next send may go out (the random gap between two sends). NULL: no gap pending.
ALTER TABLE sender ADD COLUMN next_send_at TEXT
  CHECK (next_send_at IS strftime('%Y-%m-%dT%H:%M:%SZ', next_send_at));
-- Whether the token signs in as the one sender address. A sign-in failure is a visible state, never silence.
ALTER TABLE sender ADD COLUMN auth_state TEXT NOT NULL DEFAULT 'unknown'
  CHECK (auth_state IN ('unknown','ok','signed_out','wrong_account'));
ALTER TABLE sender ADD COLUMN auth_checked_at TEXT
  CHECK (auth_checked_at IS strftime('%Y-%m-%dT%H:%M:%SZ', auth_checked_at));
ALTER TABLE sender ADD COLUMN auth_error TEXT NOT NULL DEFAULT '';
-- The address the token signs in as.
ALTER TABLE sender ADD COLUMN account TEXT NOT NULL DEFAULT '';

-- When the day was closed (its outcome frozen), on the first tick of a later New York date. NULL: still open.
ALTER TABLE send_day ADD COLUMN closed_at TEXT
  CHECK (closed_at IS strftime('%Y-%m-%dT%H:%M:%SZ', closed_at));

-- How a bounce read: a hard failure, a policy refusal, a Gmail or server limit, or not clear. NULL: not a bounce.
ALTER TABLE inbound_message ADD COLUMN dsn_class TEXT
  CHECK (dsn_class IS NULL OR dsn_class IN ('hard','policy','limit','unknown'));
