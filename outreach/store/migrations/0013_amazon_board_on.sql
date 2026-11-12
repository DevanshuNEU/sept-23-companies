-- 0013: Amazon has a reader (outreach/crawl/ats/amazon.py, 2026-11-12), so the amazon board the import left switched
-- off is switched on, and its read state starts clean, as a never-read board. 0001 to 0012 are never edited. Only
-- `board` rows of ats 'amazon' change; nothing else does. A store with no such board changes nothing.
UPDATE board SET active = 1, last_read = NULL, last_read_at = NULL, empty_streak = 0, error_streak = 0,
                 streak_since = NULL, last_error = '', etag = '', ids_fingerprint = '', next_read_at = NULL
WHERE ats = 'amazon' AND active = 0;
