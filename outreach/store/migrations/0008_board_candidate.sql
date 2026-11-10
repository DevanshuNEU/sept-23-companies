-- 0008: the board candidate list (#192, the shape #160 planned). Only adds a table: nothing is rebuilt, 0001 to 0007 are
-- never edited. One row per Greenhouse or Ashby board a free company list names, kept so a gate can read it once.
-- gate_result: promoted (a board row was made), no_us_engineering (read, no US engineering role open), dead (the
-- board answered with no jobs), error (the read failed, tried again on the next run) or known (a board row already
-- existed). `gated_at` and `gate_result` are both null until the gate has read the board.
CREATE TABLE board_candidate (
  candidate_id        INTEGER PRIMARY KEY,
  ats                 TEXT NOT NULL,
  token               TEXT NOT NULL CHECK (length(token) > 0),
  name                TEXT NOT NULL DEFAULT '',
  url                 TEXT NOT NULL DEFAULT '',
  domain              TEXT,
  source              TEXT NOT NULL,
  source_ref          TEXT NOT NULL DEFAULT '',
  priority            INTEGER NOT NULL DEFAULT 0,
  found_at            TEXT NOT NULL CHECK (found_at IS strftime('%Y-%m-%dT%H:%M:%SZ', found_at)),
  gated_at            TEXT CHECK (gated_at IS strftime('%Y-%m-%dT%H:%M:%SZ', gated_at)),
  gate_result         TEXT CHECK (gate_result IN ('promoted','no_us_engineering','dead','error','known')),
  open_us_engineering INTEGER CHECK (open_us_engineering >= 0),
  board_id            INTEGER REFERENCES board(board_id),
  UNIQUE (ats, token),
  CHECK ((gated_at IS NULL) = (gate_result IS NULL))
) STRICT;
