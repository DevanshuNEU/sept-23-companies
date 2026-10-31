-- 0005: a thread may have 4 follow-ups (#9 follow-up sequence ruling, 2026-10-31; #141), so message.seq runs 0 to 4.
-- 0001 to 0004 are never edited. SQLite cannot change a CHECK in place, so the table is rebuilt as 0004 rebuilt
-- import_record: new table, copy, drop, rename, then every index and trigger again, word for word. No table refers
-- to message by foreign key, and DROP TABLE does not fire the delete trigger.
-- One email: seq 0 is the first email, 1 to 4 the follow-ups (3, 7, 14 and 21 business days).
CREATE TABLE message_new (
  message_id       INTEGER PRIMARY KEY,
  touch_id         INTEGER NOT NULL REFERENCES touch(touch_id),
  seq              INTEGER NOT NULL CHECK (seq BETWEEN 0 AND 4),
  state            TEXT NOT NULL CHECK (state IN ('planned','drafted','approved','sending','send_unknown',
                                                  'sent','failed','skipped')),
  subject          TEXT NOT NULL DEFAULT '',
  body             TEXT NOT NULL DEFAULT '',
  hook             TEXT NOT NULL DEFAULT '',
  project          TEXT NOT NULL DEFAULT '',
  due_on           TEXT CHECK (due_on IS date(due_on)),
  -- [{code, severity: block|warn|pass, title, detail, fix, spans}], written by #11 on every save.
  checks           TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(checks) AND json_type(checks) = 'array'),
  saved_by         TEXT NOT NULL DEFAULT 'claude' CHECK (saved_by IN ('claude','devanshu','sender','import')),
  approved_at      TEXT CHECK (approved_at IS strftime('%Y-%m-%dT%H:%M:%SZ', approved_at)),
  pending_rfc_id   TEXT,
  claimed_at       TEXT CHECK (claimed_at IS strftime('%Y-%m-%dT%H:%M:%SZ', claimed_at)),
  rfc_message_id   TEXT,
  gmail_message_id TEXT,
  sent_at          TEXT CHECK (sent_at IS strftime('%Y-%m-%dT%H:%M:%SZ', sent_at)),
  reconciled_at    TEXT CHECK (reconciled_at IS strftime('%Y-%m-%dT%H:%M:%SZ', reconciled_at)),
  error            TEXT NOT NULL DEFAULT '',
  created_at       TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%SZ', created_at)),
  updated_at       TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%SZ', updated_at)),
  revision         INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
  UNIQUE (touch_id, seq),
  CHECK (state = 'planned' OR (length(trim(subject)) > 0 AND length(trim(body)) > 0)),
  CHECK (seq = 0 OR due_on IS NOT NULL),
  CHECK ((state IN ('sending','send_unknown')) = (pending_rfc_id IS NOT NULL)),
  CHECK ((state IN ('sending','send_unknown')) = (claimed_at IS NOT NULL)),
  CHECK ((state = 'sent') = (sent_at IS NOT NULL)),
  CHECK ((state = 'sent') = (rfc_message_id IS NOT NULL)),
  CHECK (state NOT IN ('approved','sending','send_unknown') OR approved_at IS NOT NULL)
) STRICT;

INSERT INTO message_new SELECT * FROM message;
DROP TABLE message;
ALTER TABLE message_new RENAME TO message;

CREATE UNIQUE INDEX message_one_sending ON message(state) WHERE state = 'sending';
CREATE UNIQUE INDEX message_pending_rfc ON message(pending_rfc_id) WHERE pending_rfc_id IS NOT NULL;
CREATE UNIQUE INDEX message_rfc ON message(rfc_message_id) WHERE rfc_message_id IS NOT NULL;
CREATE INDEX message_sent_at ON message(sent_at) WHERE sent_at IS NOT NULL;
CREATE INDEX message_state ON message(state, due_on);

CREATE TRIGGER message_followup_waits BEFORE UPDATE OF state ON message
  WHEN NEW.seq > 0 AND OLD.state = 'planned' AND NEW.state <> 'planned'
    AND (SELECT first_sent_at FROM touch WHERE touch_id = NEW.touch_id) IS NULL
  BEGIN SELECT RAISE(ABORT, 'a follow-up leaves planned only after the first email is sent'); END;

CREATE TRIGGER message_frozen_once_claimed BEFORE UPDATE OF subject, body, seq, touch_id ON message
  WHEN OLD.state IN ('sending','send_unknown','sent')
    AND (NEW.subject IS NOT OLD.subject OR NEW.body IS NOT OLD.body
         OR NEW.seq IS NOT OLD.seq OR NEW.touch_id IS NOT OLD.touch_id)
  BEGIN SELECT RAISE(ABORT, 'a message is frozen once it is claimed for sending'); END;

CREATE TRIGGER message_insert_state BEFORE INSERT ON message
  WHEN NEW.state NOT IN ('planned','drafted','sent')
    OR (NEW.seq > 0 AND NEW.state <> 'planned'
        AND (SELECT first_sent_at FROM touch WHERE touch_id = NEW.touch_id) IS NULL)
  BEGIN SELECT RAISE(ABORT, 'a message starts planned or drafted; a follow-up waits for the first send'); END;

CREATE TRIGGER message_legal_transition BEFORE UPDATE OF state ON message
  WHEN NEW.state <> OLD.state AND (OLD.state || '>' || NEW.state) NOT IN (
    'planned>drafted', 'planned>skipped',
    'drafted>approved', 'drafted>skipped',
    'approved>drafted', 'approved>sending', 'approved>skipped',
    'sending>sent', 'sending>send_unknown',
    'send_unknown>sent', 'send_unknown>failed',
    'failed>approved', 'failed>drafted', 'failed>skipped',
    'skipped>drafted')
  BEGIN SELECT RAISE(ABORT, 'illegal message state change'); END;

CREATE TRIGGER message_no_delete_claimed BEFORE DELETE ON message
  WHEN OLD.state IN ('sending','send_unknown','sent')
  BEGIN SELECT RAISE(ABORT, 'a claimed or sent message is never deleted'); END;

CREATE TRIGGER message_no_replace_claimed_insert BEFORE INSERT ON message
  WHEN EXISTS (SELECT 1 FROM message m WHERE m.state IN ('sending','send_unknown','sent')
               AND (m.message_id = NEW.message_id OR (m.touch_id = NEW.touch_id AND m.seq = NEW.seq)
                    OR m.rfc_message_id = NEW.rfc_message_id OR m.pending_rfc_id = NEW.pending_rfc_id))
  BEGIN SELECT RAISE(ABORT, 'a claimed or sent message is never deleted'); END;

CREATE TRIGGER message_no_replace_claimed_update
  BEFORE UPDATE OF message_id, touch_id, seq, state, rfc_message_id, pending_rfc_id ON message
  WHEN EXISTS (SELECT 1 FROM message m WHERE m.message_id <> OLD.message_id
               AND m.state IN ('sending','send_unknown','sent')
               AND (m.message_id = NEW.message_id OR (m.touch_id = NEW.touch_id AND m.seq = NEW.seq)
                    OR m.rfc_message_id = NEW.rfc_message_id OR m.pending_rfc_id = NEW.pending_rfc_id))
  BEGIN SELECT RAISE(ABORT, 'a claimed or sent message is never deleted'); END;

CREATE TRIGGER message_no_replace_sending BEFORE UPDATE OF state ON message
  WHEN NEW.state = 'sending' AND OLD.state <> 'sending'
    AND EXISTS (SELECT 1 FROM message m WHERE m.state = 'sending' AND m.message_id <> OLD.message_id)
  BEGIN SELECT RAISE(ABORT, 'UNIQUE: only one message is sending at a time'); END;

