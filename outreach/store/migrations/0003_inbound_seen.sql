-- 0003: inbound mail the sender read and then left alone (#14). 0001 and 0002 are never edited.
-- His own mail, a delay notice, or mail outside the tracked threads writes nothing else, so without this row the
-- same message would be read again on every tick and a big backlog would never finish. Rows are forgotten after
-- 30 days (Gmail's history cursor lasts about a week, so nothing older is read again).
CREATE TABLE inbound_seen (
  gmail_message_id TEXT PRIMARY KEY CHECK (length(gmail_message_id) > 0),
  thread_id        TEXT NOT NULL DEFAULT '',
  why              TEXT NOT NULL CHECK (why IN ('ours','delay','untracked')),
  seen_at          TEXT NOT NULL CHECK (seen_at IS strftime('%Y-%m-%dT%H:%M:%SZ', seen_at))
) STRICT;
CREATE INDEX inbound_seen_at ON inbound_seen(seen_at);
