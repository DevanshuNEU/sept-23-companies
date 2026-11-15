-- 0019: Send next and Send now (#9 outreach fixes ruling, 2026-11-14, point 3). 0001 to 0018 are never edited.
-- `priority` is his click in the Outbox: `normal`, `next` (first in its account's queue, every rule kept) or `now`
-- (the sender's next tick, outside the window, past the spacing and the company gap, every stop kept). `urgent_at`
-- is when he asked for `now`, and is set exactly then. ADD COLUMN keeps every row, index and trigger of 0005 as it
-- is (no table rebuild); every existing row is `normal`.
ALTER TABLE message ADD COLUMN priority TEXT NOT NULL DEFAULT 'normal'
  CHECK (priority IN ('normal','next','now'));
ALTER TABLE message ADD COLUMN urgent_at TEXT
  CHECK (urgent_at IS strftime('%Y-%m-%dT%H:%M:%SZ', urgent_at))
  CHECK ((priority = 'now') = (urgent_at IS NOT NULL));

-- Only an approved email takes `next` or `now` (approve sets the state and the priority in one write); going back to
-- `normal` is allowed in any state that has not been claimed.
CREATE TRIGGER message_priority_only_approved BEFORE UPDATE OF priority ON message
  WHEN NEW.priority IS NOT OLD.priority AND NEW.priority <> 'normal' AND NEW.state <> 'approved'
  BEGIN SELECT RAISE(ABORT, 'only an approved message is sent next or now'); END;

-- A claimed or sent email keeps the priority it went out with: the urgent cap counts the ones sent with `now`.
CREATE TRIGGER message_priority_frozen_once_claimed BEFORE UPDATE OF priority, urgent_at ON message
  WHEN OLD.state IN ('sending','send_unknown','sent')
    AND (NEW.priority IS NOT OLD.priority OR NEW.urgent_at IS NOT OLD.urgent_at)
  BEGIN SELECT RAISE(ABORT, 'a message keeps its priority once it is claimed for sending'); END;

-- A message is born `normal`: only approve or his Send next and Send now change it.
CREATE TRIGGER message_insert_priority BEFORE INSERT ON message
  WHEN NEW.priority IS NOT 'normal' AND NEW.state <> 'sent'
  BEGIN SELECT RAISE(ABORT, 'a message starts with normal priority'); END;
