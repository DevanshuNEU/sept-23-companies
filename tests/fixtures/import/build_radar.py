"""Builds a synthetic Job Radar database for the import tests.

The schema is Job Radar's own (its `store.py`: SCHEMA_V1, the views, and migrations 3 to 5, with comments dropped),
so a reader that works on this file works on the real shape. Every row is made up. Nothing here opens the real
radar.db.

    build(path)   create the file at `path` (it must not exist)
"""
import sqlite3
from pathlib import Path

SCHEMA_V1 = """
CREATE TABLE board (
  board_id            INTEGER PRIMARY KEY,
  ats                 TEXT    NOT NULL,
  token               TEXT    NOT NULL,
  company             TEXT    NOT NULL,
  company_key         TEXT    NOT NULL,
  company_tier        INTEGER,
  sponsor_evidence    TEXT    NOT NULL DEFAULT '',
  sponsor_confidence  TEXT    NOT NULL DEFAULT 'verified',
  enumerates_fully    INTEGER NOT NULL DEFAULT 1,
  active              INTEGER NOT NULL DEFAULT 1,
  synced_at           TEXT    NOT NULL,
  UNIQUE (ats, token)
) STRICT;
CREATE TABLE poll_run (
  run_id          INTEGER PRIMARY KEY,
  started_at      TEXT NOT NULL,
  ended_at        TEXT,
  argv            TEXT NOT NULL,
  ruleset_version TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'running'
) STRICT;
CREATE TABLE board_fetch (
  run_id     INTEGER NOT NULL REFERENCES poll_run(run_id),
  board_id   INTEGER NOT NULL REFERENCES board(board_id),
  ok         INTEGER NOT NULL,
  n_returned INTEGER NOT NULL DEFAULT 0,
  truncated  INTEGER NOT NULL DEFAULT 0,
  latency_ms INTEGER,
  http_status INTEGER,
  error      TEXT,
  PRIMARY KEY (run_id, board_id)
) STRICT;
CREATE TABLE posting (
  posting_id          INTEGER PRIMARY KEY,
  board_id            INTEGER NOT NULL REFERENCES board(board_id),
  source_key          TEXT    NOT NULL,
  fingerprint         TEXT    NOT NULL,
  apply_url           TEXT    NOT NULL,
  title               TEXT    NOT NULL,
  location            TEXT    NOT NULL DEFAULT '',
  merged_into         INTEGER REFERENCES posting(posting_id),
  first_seen_at       TEXT    NOT NULL,
  last_seen_at        TEXT    NOT NULL,
  first_run_id        INTEGER NOT NULL REFERENCES poll_run(run_id),
  posted_at           TEXT,
  posted_at_precision TEXT    NOT NULL DEFAULT 'unknown',
  UNIQUE (board_id, source_key)
) STRICT;
CREATE INDEX posting_fingerprint ON posting(fingerprint);
CREATE INDEX posting_first_seen  ON posting(first_seen_at);
CREATE INDEX posting_board_seen  ON posting(board_id, last_seen_at);
CREATE TABLE posting_version (
  version_id   INTEGER PRIMARY KEY,
  posting_id   INTEGER NOT NULL REFERENCES posting(posting_id),
  run_id       INTEGER NOT NULL REFERENCES poll_run(run_id),
  observed_at  TEXT    NOT NULL,
  content_hash TEXT    NOT NULL,
  payload      TEXT    NOT NULL,
  body_text    TEXT,
  UNIQUE (posting_id, content_hash)
) STRICT;
CREATE INDEX posting_version_posting ON posting_version(posting_id, observed_at);
CREATE TABLE decision (
  posting_id      INTEGER NOT NULL REFERENCES posting(posting_id),
  ruleset_version TEXT    NOT NULL,
  decided_at      TEXT    NOT NULL,
  verdict         TEXT    NOT NULL,
  reason          TEXT    NOT NULL,
  reasons         TEXT    NOT NULL DEFAULT '[]',
  evidence        TEXT    NOT NULL DEFAULT '',
  role_family     TEXT,
  yoe_min         INTEGER,
  fit_score       INTEGER NOT NULL DEFAULT 0,
  thesis_tags     TEXT    NOT NULL DEFAULT '',
  PRIMARY KEY (posting_id, ruleset_version)
) STRICT;
CREATE INDEX decision_verdict ON decision(ruleset_version, verdict, reason);
CREATE TABLE app_event (
  event_id        INTEGER PRIMARY KEY,
  posting_id      INTEGER NOT NULL REFERENCES posting(posting_id),
  at              TEXT    NOT NULL,
  recorded_at     TEXT    NOT NULL,
  kind            TEXT    NOT NULL,
  resume_variant  TEXT,
  channel         TEXT,
  detail          TEXT,
  source          TEXT    NOT NULL DEFAULT 'dashboard',
  client_event_id TEXT    NOT NULL UNIQUE
) STRICT;
CREATE INDEX app_event_at      ON app_event(at);
CREATE INDEX app_event_posting ON app_event(posting_id, at, event_id);
CREATE TABLE outbox (
  outbox_id       INTEGER PRIMARY KEY,
  created_at      TEXT    NOT NULL,
  kind            TEXT    NOT NULL,
  dedup_key       TEXT    NOT NULL UNIQUE,
  payload         TEXT    NOT NULL,
  attempts        INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT    NOT NULL,
  delivered_at    TEXT,
  last_error      TEXT
) STRICT;
CREATE INDEX outbox_pending ON outbox(next_attempt_at) WHERE delivered_at IS NULL;
CREATE TABLE label (
  posting_id     INTEGER NOT NULL REFERENCES posting(posting_id),
  rubric_version TEXT    NOT NULL,
  label          TEXT    NOT NULL,
  stratum        TEXT    NOT NULL,
  sample_weight  REAL    NOT NULL DEFAULT 1.0,
  labeled_at     TEXT    NOT NULL,
  labeler        TEXT    NOT NULL DEFAULT 'devanshu',
  PRIMARY KEY (posting_id, rubric_version)
) STRICT;
CREATE INDEX label_stratum ON label(rubric_version, stratum, label);
CREATE TABLE schema_migration (
  version    INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  applied_at TEXT NOT NULL
) STRICT;
CREATE TABLE meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
) STRICT;
"""

# The views as they stand after migration 3 (closed-detection v2) plus the application view.
VIEWS = """
CREATE VIEW posting_live AS
SELECT p.*, b.company, b.company_key, b.ats, b.company_tier,
       b.sponsor_evidence, b.sponsor_confidence,
       CASE WHEN b.enumerates_fully = 1
                 AND p.last_seen_at < (
                       SELECT r.started_at
                         FROM board_fetch f JOIN poll_run r USING(run_id)
                        WHERE f.board_id = p.board_id
                          AND f.ok = 1
                          AND f.n_returned > 0
                        ORDER BY f.run_id DESC
                        LIMIT 1 OFFSET 2)
            THEN 1 ELSE 0 END AS closed
FROM posting p JOIN board b USING(board_id)
WHERE p.merged_into IS NULL;
CREATE INDEX board_fetch_board_run ON board_fetch(board_id, run_id);
CREATE VIEW application AS
SELECT e.posting_id,
       MIN(CASE WHEN kind='opened'  THEN at END) AS opened_at,
       MIN(CASE WHEN kind='applied' THEN at END) AS applied_at,
       MIN(CASE WHEN kind='reply'   THEN at END) AS first_reply_at,
       MIN(CASE WHEN kind='screen'  THEN at END) AS screen_at,
       MIN(CASE WHEN kind='offer'   THEN at END) AS offer_at,
       (SELECT e3.resume_variant FROM app_event e3
         WHERE e3.posting_id = e.posting_id AND e3.kind='applied'
         ORDER BY e3.at ASC, e3.event_id ASC LIMIT 1)   AS resume_variant,
       (SELECT e2.kind FROM app_event e2
         WHERE e2.posting_id = e.posting_id
           AND e2.kind IN ('applied','ignored','unignored','reply','screen',
                           'onsite','offer','rejected','withdrawn')
         ORDER BY e2.at DESC, e2.event_id DESC LIMIT 1) AS last_kind,
       MAX(at) AS updated_at
FROM app_event e
GROUP BY e.posting_id;
"""

# Migration 5: failure tracking for the body backfill.
MIGRATION_5 = """
CREATE TABLE body_attempt (
  posting_id      INTEGER PRIMARY KEY REFERENCES posting(posting_id),
  attempts        INTEGER NOT NULL DEFAULT 0,
  last_error      TEXT,
  last_attempt_at TEXT    NOT NULL,
  next_attempt_at TEXT    NOT NULL
) STRICT;
CREATE INDEX body_attempt_due ON body_attempt(next_attempt_at);
"""

MIGRATIONS = (
    (1, "initial schema"), (2, "views"), (3, "canonical posted_at; closed-detection v2"),
    (4, "posted_at_precision='relative' for reconstructed dates"), (5, "body_attempt: backoff for the JD backfill"),
)

BOARDS = (
    ("greenhouse", "kestrel", "Kestrel Robotics", "kestrelrobotics"),
    ("ashby", "marlow", "Marlow Dynamics", "marlowdynamics"),
    ("lever", "tidewater", "Tidewater AI", "tidewaterai"),
)
RUNS = ("2026-10-18T08:00:00Z", "2026-10-19T08:00:00Z", "2026-10-20T08:00:00Z")
# (board 1-3, native id, title, location, posted_at, precision)
POSTINGS = (
    (1, "1001", "Software Engineer, New Grad", "Boston, MA", "2026-10-01T14:00:00Z", "exact"),
    (1, "1002", "Robotics Perception Engineer", "Boston, MA", "2026-10-02T14:00:00Z", "exact"),
    (2, "aa-11", "Platform Engineer", "Remote, US", "2026-09-29T00:00:00Z", "day"),
    (2, "aa-12", "Staff Product Manager", "Remote, US", "2026-09-30T00:00:00Z", "day"),
    (3, "6f1c0a52", "Machine Learning Engineer", "Seattle, WA", "2026-10-05T16:30:00Z", "exact"),
    (3, "6f1c0a53", "Data Engineer", "Seattle, WA", None, "unknown"),
)

_VERSION = ("INSERT INTO posting_version(posting_id, run_id, observed_at, content_hash, payload, body_text) "
            "VALUES (?, ?, ?, ?, ?, ?)")


def build(path: Path) -> Path:
    path = Path(path)
    if path.exists():
        raise FileExistsError(path)
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute("PRAGMA foreign_keys = ON")
        db.executescript(SCHEMA_V1)
        db.executescript(VIEWS)
        db.executescript(MIGRATION_5)
        for version, name in MIGRATIONS:
            db.execute("INSERT INTO schema_migration VALUES (?, ?, ?)", (version, name, "2026-10-01T00:00:00Z"))
        db.execute("PRAGMA user_version = 5")
        db.execute("INSERT INTO meta VALUES ('cutover', '2026-08-01T00:00:00Z')")
        for run, started in enumerate(RUNS, start=1):
            db.execute("INSERT INTO poll_run VALUES (?, ?, ?, 'fixture', 'r-fixture', 'ok')",
                       (run, started, started))
        for board, (ats, token, company, key) in enumerate(BOARDS, start=1):
            db.execute(
                "INSERT INTO board(board_id, ats, token, company, company_key, company_tier, sponsor_evidence, "
                "synced_at) VALUES (?, ?, ?, ?, ?, 1, 'fixture evidence', '2026-10-20T08:00:00Z')",
                (board, ats, token, company, key))
            for run in (1, 2, 3):
                db.execute("INSERT INTO board_fetch VALUES (?, ?, 1, 2, 0, 120, 200, NULL)", (run, board))
        for posting, (board, native, title, where, posted, precision) in enumerate(POSTINGS, start=1):
            ats, token = BOARDS[board - 1][0], BOARDS[board - 1][1]
            db.execute(
                "INSERT INTO posting(posting_id, board_id, source_key, fingerprint, apply_url, title, location, "
                "first_seen_at, last_seen_at, first_run_id, posted_at, posted_at_precision) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '2026-10-20T08:00:00Z', 1, ?, ?)",
                (posting, board, f"{ats}:{token}:{native}", f"fp-{posting}",
                 f"https://jobs.example.com/{token}/{native}", title, where, RUNS[0], posted, precision))
            db.execute(_VERSION, (posting, 1, RUNS[0], f"h2-{posting}-a", f'{{"title":"{title}"}}',
                                  f"Fixture job description {posting}."))
        # A second version for two postings: a title change and a body change.
        db.execute(_VERSION, (1, 3, RUNS[2], "h2-1-b", '{"title":"Software Engineer I, New Grad"}',
                              "Fixture job description 1, edited."))
        db.execute(_VERSION, (5, 2, RUNS[1], "h2-5-b", '{"title":"Machine Learning Engineer"}', None))
        for posting, verdict, reason in ((1, "show", "ok"), (4, "hide", "non-eng-blacklist"), (6, "hide", "yoe")):
            db.execute(
                "INSERT INTO decision(posting_id, ruleset_version, decided_at, verdict, reason) "
                "VALUES (?, 'r-fixture', '2026-10-20T08:05:00Z', ?, ?)", (posting, verdict, reason))
        events = (
            (1, "2026-10-19T09:00:00Z", "opened", None, None, "fixture-ev-1"),
            (1, "2026-10-19T09:30:00Z", "applied", "resume-a", "ats", "fixture-ev-2"),
            (4, "2026-10-19T10:00:00Z", "ignored", None, None, "fixture-ev-3"),
            (5, "2026-10-20T11:00:00Z", "screen", None, "referral", "fixture-ev-4"),
        )
        for posting, at, kind, variant, channel, client_id in events:
            db.execute(
                "INSERT INTO app_event(posting_id, at, recorded_at, kind, resume_variant, channel, source, "
                "client_event_id) VALUES (?, ?, ?, ?, ?, ?, 'dashboard', ?)",
                (posting, at, at, kind, variant, channel, client_id))
        db.execute("INSERT INTO outbox(created_at, kind, dedup_key, payload, next_attempt_at, delivered_at) "
                   "VALUES ('2026-10-19T08:00:00Z', 'notify', 'fixture-1', '{}', '2026-10-19T08:00:00Z', "
                   "'2026-10-19T08:00:05Z')")
        db.execute("INSERT INTO label VALUES (1, 'rubric-1', 'apply', 'P', 1.0, '2026-10-21T08:00:00Z', 'devanshu')")
        db.execute("INSERT INTO body_attempt VALUES (6, 2, 'not found', '2026-10-20T08:00:00Z', '2026-10-20T08:04:00Z')")
    finally:
        db.close()
    return path
