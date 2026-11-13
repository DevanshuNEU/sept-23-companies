-- 0016: when each board was last read in full (#251 part 2). A head read (one newest-first request, Workday and
-- Oracle) stores new postings between full reads and leaves this alone; a full read moves it. A board already read
-- well counts its last good read as its last full one.
ALTER TABLE board ADD COLUMN last_full_read_at TEXT
  CHECK (last_full_read_at IS strftime('%Y-%m-%dT%H:%M:%SZ', last_full_read_at));
UPDATE board SET last_full_read_at = last_good_read_at;
