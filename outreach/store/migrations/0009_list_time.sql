-- 0009: the list time of a posting (#193, first to see, part 3). Only adds columns and one index: nothing is rebuilt,
-- 0001 to 0008 are never edited. ADD COLUMN is instant.
-- posted_at: when the board says the job went up, canonical UTC, written once when the posting is first stored and
-- only from a board time Knock trusts (Greenhouse first_published, Ashby publishedAt).
-- seen_gap_s: seconds from the board's last good read to the read that first stored the posting. NULL on a board's
-- first read, and for postings stored before this migration.
-- repost_of: the posting at the same company with the same title that closed in the 120 days before this one was
-- first stored.
-- list_at: the time the job list sorts by (outreach.crawl.row_clock.list_at). NULL means the date is not known.
ALTER TABLE posting ADD COLUMN posted_at TEXT CHECK (posted_at IS strftime('%Y-%m-%dT%H:%M:%SZ', posted_at));
ALTER TABLE posting ADD COLUMN seen_gap_s INTEGER CHECK (seen_gap_s >= 0);
ALTER TABLE posting ADD COLUMN repost_of INTEGER REFERENCES posting(posting_id);
ALTER TABLE posting ADD COLUMN list_at TEXT CHECK (list_at IS strftime('%Y-%m-%dT%H:%M:%SZ', list_at));
-- last_good_read_at: the board's last ok, unchanged or empty read. An error read never moves it.
ALTER TABLE board ADD COLUMN last_good_read_at TEXT
  CHECK (last_good_read_at IS strftime('%Y-%m-%dT%H:%M:%SZ', last_good_read_at));

-- Postings stored before this keep their current date: the posted date (05:00 UTC), else the first-seen time.
UPDATE posting SET list_at = coalesce(posted_on || 'T05:00:00Z', first_seen_at);
-- A board's last good read: its last read when that was good, else the newest good read it keeps.
UPDATE board SET last_good_read_at = CASE
  WHEN last_read IN ('ok', 'unchanged', 'empty') THEN last_read_at
  ELSE (SELECT max(r.at) FROM board_read r WHERE r.board_id = board.board_id AND r.result <> 'error')
END;

CREATE INDEX posting_list_at ON posting(list_at) WHERE closed_at IS NULL;
