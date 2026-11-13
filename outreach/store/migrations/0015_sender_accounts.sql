-- 0015: several sender accounts (multi-sender design, 2026-11-12). 0001 to 0014 are never edited.
-- sender_account holds one row per address that sends. `sender` row 1 stays as the global pause (his button).
-- company_sender pins a company to one account, bounced_address is the dead-address list across accounts, and
-- touch.account_id says which account sent the first email. send_day is rebuilt per account (the 0012 pattern).
CREATE TABLE sender_account (
  account_id      INTEGER PRIMARY KEY,
  address         TEXT NOT NULL UNIQUE COLLATE NOCASE,
  kind            TEXT NOT NULL CHECK (kind IN ('gmail','mailapp')),
  paused          INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0,1)),
  pause_kind      TEXT CHECK (pause_kind IN ('hard_block')),
  paused_reason   TEXT NOT NULL DEFAULT '',
  paused_at       TEXT CHECK (paused_at IS strftime('%Y-%m-%dT%H:%M:%SZ', paused_at)),
  history_id      TEXT NOT NULL DEFAULT '',
  next_send_at    TEXT CHECK (next_send_at IS strftime('%Y-%m-%dT%H:%M:%SZ', next_send_at)),
  auth_state      TEXT NOT NULL DEFAULT 'unknown'
                  CHECK (auth_state IN ('unknown','ok','signed_out','wrong_account','app_closed')),
  auth_checked_at TEXT CHECK (auth_checked_at IS strftime('%Y-%m-%dT%H:%M:%SZ', auth_checked_at)),
  auth_error      TEXT NOT NULL DEFAULT '',
  created_at      TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%SZ', created_at)),
  updated_at      TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%SZ', updated_at)),
  revision        INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
  CHECK ((paused = 1) = (paused_at IS NOT NULL)),
  CHECK ((paused = 1) = (pause_kind IS NOT NULL))
) STRICT;

CREATE TABLE company_sender (
  company_id INTEGER PRIMARY KEY REFERENCES company(company_id),
  account_id INTEGER NOT NULL REFERENCES sender_account(account_id),
  pinned_by  TEXT NOT NULL CHECK (pinned_by IN ('rule','you','import')),
  pinned_at  TEXT NOT NULL CHECK (pinned_at IS strftime('%Y-%m-%dT%H:%M:%SZ', pinned_at))
) STRICT;

CREATE TABLE bounced_address (
  address       TEXT PRIMARY KEY COLLATE NOCASE,
  first_seen_at TEXT NOT NULL CHECK (first_seen_at IS strftime('%Y-%m-%dT%H:%M:%SZ', first_seen_at)),
  source        TEXT NOT NULL CHECK (source IN ('bounce','marked','backfill')),
  source_ref    TEXT NOT NULL DEFAULT ''
) STRICT;

ALTER TABLE touch ADD COLUMN account_id INTEGER REFERENCES sender_account(account_id);

-- Account 1 is the address the old single sender signed in as. A store that never signed in (account '') gets no
-- account row, and has no send days or sent touches to carry over.
INSERT INTO sender_account(account_id, address, kind, history_id, next_send_at, auth_state, auth_checked_at,
                           auth_error, created_at, updated_at)
  SELECT 1, account, 'gmail', history_id, next_send_at, auth_state, auth_checked_at, auth_error,
         strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
  FROM sender WHERE sender_id = 1 AND account <> '';

UPDATE touch SET account_id = 1
  WHERE (first_sent_at IS NOT NULL OR origin = 'import') AND EXISTS (SELECT 1 FROM sender_account WHERE account_id = 1);

INSERT INTO company_sender(company_id, account_id, pinned_by, pinned_at)
  SELECT p.company_id, 1, 'import', strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
  FROM person p WHERE p.person_id IN (SELECT person_id FROM touch WHERE account_id = 1)
  GROUP BY p.company_id;

-- Every address that bounced: from a bounced touch's person, and from the old address of each email_bounced event
-- (`mark_email_bounced` writes detail {"email": {"old": ..., "new": ...}}).
INSERT OR IGNORE INTO bounced_address(address, first_seen_at, source, source_ref)
  SELECT p.email, COALESCE(t.bounced_at, strftime('%Y-%m-%dT%H:%M:%SZ', 'now')), 'backfill', 'touch:' || t.touch_id
  FROM touch t JOIN person p ON p.person_id = t.person_id
  WHERE t.state = 'bounced' AND p.email IS NOT NULL AND p.email <> ''
  ORDER BY t.touch_id;
INSERT OR IGNORE INTO bounced_address(address, first_seen_at, source, source_ref)
  SELECT json_extract(e.detail, '$.email.old'), e.at, 'backfill', 'event:' || e.event_id
  FROM event e
  WHERE e.entity = 'person' AND e.kind = 'email_bounced'
    AND json_extract(e.detail, '$.email.old') IS NOT NULL AND json_extract(e.detail, '$.email.old') <> ''
  ORDER BY e.event_id;

CREATE TABLE send_day_new (
  account_id   INTEGER NOT NULL REFERENCES sender_account(account_id),
  day          TEXT CHECK (day IS date(day)),
  send_limit   INTEGER NOT NULL CHECK (send_limit BETWEEN 0 AND 200),
  outcome      TEXT CHECK (outcome IN ('clean','warning','hard_block')),
  outcome_note TEXT NOT NULL DEFAULT '',
  created_at   TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%SZ', created_at)),
  closed_at    TEXT CHECK (closed_at IS strftime('%Y-%m-%dT%H:%M:%SZ', closed_at)),
  PRIMARY KEY (account_id, day)
) STRICT;
INSERT INTO send_day_new(account_id, day, send_limit, outcome, outcome_note, created_at, closed_at)
  SELECT 1, day, send_limit, outcome, outcome_note, created_at, closed_at FROM send_day
  WHERE EXISTS (SELECT 1 FROM sender_account WHERE account_id = 1);
DROP TABLE send_day;
ALTER TABLE send_day_new RENAME TO send_day;
