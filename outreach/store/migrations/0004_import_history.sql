-- 0004: import_record takes three more sources and a fourth outcome (#17). 0001 to 0003 are never edited.
-- interview_prep, curated_touches and send_log join the seven sources, and 'held' marks a record the import could
-- not place at all (an unsettled send, a send.log line with no contact). A held record has a reason and no entity,
-- like a skipped one. SQLite cannot change a CHECK in place, so the table is rebuilt: new table, copy, drop, rename,
-- then the two append-only triggers and the run index again. DROP TABLE does not fire the delete trigger.
CREATE TABLE import_record_new (
  import_id   INTEGER PRIMARY KEY,
  run_id      INTEGER NOT NULL REFERENCES job_run(run_id),
  source      TEXT NOT NULL CHECK (source IN ('data_js','local_storage','contacts_json','radar_db',
                                              'devboard','contacted_txt','devvault_notes',
                                              'interview_prep','curated_touches','send_log')),
  source_key  TEXT NOT NULL CHECK (length(source_key) > 0),
  raw         TEXT NOT NULL,
  raw_sha256  TEXT NOT NULL CHECK (length(raw_sha256) = 64),
  outcome     TEXT NOT NULL CHECK (outcome IN ('imported','merged','skipped','held')),
  reason      TEXT NOT NULL DEFAULT '',
  entity      TEXT,
  entity_id   INTEGER,
  imported_at TEXT NOT NULL CHECK (imported_at IS strftime('%Y-%m-%dT%H:%M:%SZ', imported_at)),
  UNIQUE (source, source_key, raw_sha256),
  CHECK (outcome = 'imported' OR length(reason) > 0),
  CHECK ((outcome IN ('skipped','held')) = (entity_id IS NULL))
) STRICT;

INSERT INTO import_record_new SELECT * FROM import_record;
DROP TABLE import_record;
ALTER TABLE import_record_new RENAME TO import_record;

CREATE TRIGGER import_record_no_update BEFORE UPDATE ON import_record
  BEGIN SELECT RAISE(ABORT, 'import_record is append-only'); END;
CREATE TRIGGER import_record_no_delete BEFORE DELETE ON import_record
  BEGIN SELECT RAISE(ABORT, 'import_record is append-only'); END;
CREATE INDEX import_record_run ON import_record(run_id, source, outcome);
