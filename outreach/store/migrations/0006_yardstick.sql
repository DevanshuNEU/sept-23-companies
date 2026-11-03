-- 0006: the pipeline scorecard's yardstick (#153). Only adds tables: nothing is rebuilt, 0001 to 0005 are never edited.
-- feed_listing: one row per listing of an outside feed (SimplifyJobs new grad list), kept only to measure Knock
-- against it. It is never shown as a job and never gives a posting a date. `ats`, `token` and `native_id` are the link
-- read by outreach.pipeline.urls.board_key, null when the link names no known board or no job.
CREATE TABLE feed_listing (
  feed_id       INTEGER PRIMARY KEY,
  source        TEXT NOT NULL,
  listing_id    TEXT NOT NULL,
  url           TEXT NOT NULL,
  ats           TEXT,
  token         TEXT,
  native_id     TEXT,
  company_name  TEXT NOT NULL,
  title         TEXT NOT NULL,
  posted_hint   TEXT,
  first_seen_at TEXT NOT NULL CHECK (first_seen_at IS strftime('%Y-%m-%dT%H:%M:%SZ', first_seen_at)),
  last_seen_at  TEXT NOT NULL CHECK (last_seen_at IS strftime('%Y-%m-%dT%H:%M:%SZ', last_seen_at)),
  active        INTEGER NOT NULL CHECK (active IN (0, 1)),
  -- 1 for the listings already in the feed when the poll first ran: they have no honest first-seen time.
  backfill      INTEGER NOT NULL CHECK (backfill IN (0, 1)),
  UNIQUE (source, listing_id)
) STRICT;

-- The scorecard joins a listing to a posting by its board and native id.
CREATE INDEX feed_listing_native ON feed_listing(ats, token, native_id) WHERE native_id IS NOT NULL;

-- miss: a job link Devanshu pasted because Knock did not show it, with the first cause found for it.
CREATE TABLE miss (
  miss_id     INTEGER PRIMARY KEY,
  url         TEXT NOT NULL,
  note        TEXT NOT NULL DEFAULT '',
  pasted_at   TEXT NOT NULL CHECK (pasted_at IS strftime('%Y-%m-%dT%H:%M:%SZ', pasted_at)),
  cause       TEXT NOT NULL CHECK (cause IN ('no_company','no_board','not_read_yet','read_wrong_level','read_not_tech',
                                             'hidden_visa','wrong_date','found','unknown_site')),
  detail      TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(detail)),
  posting_id  INTEGER REFERENCES posting(posting_id),
  company_id  INTEGER REFERENCES company(company_id),
  board_id    INTEGER REFERENCES board(board_id),
  resolved_at TEXT CHECK (resolved_at IS strftime('%Y-%m-%dT%H:%M:%SZ', resolved_at))
) STRICT;

-- pipeline_meta: small facts the poll keeps, such as the feed's ETag.
CREATE TABLE pipeline_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
) STRICT;
