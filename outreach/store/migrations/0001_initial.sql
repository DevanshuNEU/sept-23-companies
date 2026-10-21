CREATE TABLE schema_migration (
  version    INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  applied_at TEXT NOT NULL CHECK (applied_at IS strftime('%Y-%m-%dT%H:%M:%SZ', applied_at))
) STRICT;

CREATE TABLE company (
  company_id    INTEGER PRIMARY KEY,
  name          TEXT NOT NULL CHECK (length(trim(name)) > 0),
  status        TEXT NOT NULL DEFAULT 'candidate'
                CHECK (status IN ('candidate','backlog','working','closed','removed')),
  close_reason  TEXT CHECK (close_reason IN ('referral','interview','no_sponsorship','no_referrals',
                                             'already_hired','not_now','other')),
  remove_reason TEXT,
  status_note   TEXT NOT NULL DEFAULT '',
  status_at     TEXT NOT NULL CHECK (status_at IS strftime('%Y-%m-%dT%H:%M:%SZ', status_at)),
  -- Paused by Devanshu and held by a reply are separate, so a reply never overwrites his pause.
  paused_at     TEXT CHECK (paused_at IS strftime('%Y-%m-%dT%H:%M:%SZ', paused_at)),
  pause_note    TEXT NOT NULL DEFAULT '',
  held_by_touch INTEGER REFERENCES touch(touch_id),
  held_at       TEXT CHECK (held_at IS strftime('%Y-%m-%dT%H:%M:%SZ', held_at)),
  -- A duplicate merged into another company; every read follows it.
  merged_into   INTEGER REFERENCES company(company_id),
  created_at    TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%SZ', created_at)),
  revision      INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
  CHECK ((status = 'closed') = (close_reason IS NOT NULL)),
  CHECK ((status = 'removed') = (remove_reason IS NOT NULL AND length(trim(remove_reason)) > 0)),
  CHECK (paused_at IS NULL OR status = 'working'),
  CHECK ((held_by_touch IS NULL) = (held_at IS NULL)),
  CHECK (merged_into IS NULL OR (merged_into <> company_id AND status = 'removed'))
) STRICT;
CREATE INDEX company_merged ON company(merged_into) WHERE merged_into IS NOT NULL;
CREATE INDEX company_held ON company(held_by_touch) WHERE held_by_touch IS NOT NULL;

CREATE TABLE company_alias (
  alias_key  TEXT PRIMARY KEY CHECK (length(alias_key) > 0 AND alias_key = lower(alias_key)),
  company_id INTEGER NOT NULL REFERENCES company(company_id),
  alias      TEXT NOT NULL,
  -- fein, ats and linkedin keys are the raw value lower-cased; a board's key is '<ats>:<token>' (#30).
  source     TEXT NOT NULL CHECK (source IN ('name','hand','import','crawl','merge','fein','dba','legal_name',
                                             'ats','formd','linkedin')),
  created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%SZ', created_at))
) STRICT;
CREATE INDEX company_alias_company ON company_alias(company_id);

-- A FEIN or domain seen on more than one employer (a payroll provider, a parent's domain). It is recorded, but
-- identity never merges two companies on it (#31 Review Focus 1).
CREATE TABLE shared_key (
  kind        TEXT NOT NULL CHECK (kind IN ('fein','domain')),
  value       TEXT NOT NULL CHECK (length(value) > 0 AND value = lower(value)),
  reason      TEXT NOT NULL CHECK (length(trim(reason)) > 0),
  recorded_at TEXT NOT NULL CHECK (recorded_at IS strftime('%Y-%m-%dT%H:%M:%SZ', recorded_at)),
  PRIMARY KEY (kind, value)
) STRICT;

-- One row per discovery source (lca, formd, hn, stapply...): the version that last ran and its job run.
CREATE TABLE source (
  source_name TEXT PRIMARY KEY CHECK (length(source_name) > 0),
  version     TEXT NOT NULL CHECK (length(version) > 0),
  last_run_id INTEGER REFERENCES job_run(run_id)
) STRICT;

-- Which source saw which company, under the source's own key: a re-run of the source adds no row.
CREATE TABLE company_source (
  company_id    INTEGER NOT NULL REFERENCES company(company_id),
  source_name   TEXT NOT NULL REFERENCES source(source_name),
  source_ref    TEXT NOT NULL CHECK (length(source_ref) > 0),
  first_seen_at TEXT NOT NULL CHECK (first_seen_at IS strftime('%Y-%m-%dT%H:%M:%SZ', first_seen_at)),
  last_seen_at  TEXT NOT NULL CHECK (last_seen_at IS strftime('%Y-%m-%dT%H:%M:%SZ', last_seen_at)),
  PRIMARY KEY (company_id, source_name, source_ref),
  CHECK (last_seen_at >= first_seen_at)
) STRICT;
-- Identity looks a source's key up to find the company it already made.
CREATE INDEX company_source_ref ON company_source(source_name, source_ref);

CREATE TABLE board (
  board_id     INTEGER PRIMARY KEY,
  company_id   INTEGER NOT NULL REFERENCES company(company_id),
  ats          TEXT NOT NULL CHECK (ats IN ('ashby','greenhouse','lever','workable','smartrecruiters',
                                            'rippling','gem','recruitee','bamboohr','dover','breezy',
                                            'workday','eightfold','oracle_orc','amazon','apple','google',
                                            'pinpoint','teamtailor','jazzhr','paycom','icims','personio',
                                            'join_com','cornerstone','successfactors')),
  token        TEXT NOT NULL CHECK (length(token) > 0),
  active       INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
  -- unchanged: the board said nothing changed since the last read (a 304, or the same ids), so it clears both
  -- streaks as ok does.
  last_read    TEXT CHECK (last_read IN ('ok','empty','error','unchanged')),
  last_read_at TEXT CHECK (last_read_at IS strftime('%Y-%m-%dT%H:%M:%SZ', last_read_at)),
  empty_streak INTEGER NOT NULL DEFAULT 0 CHECK (empty_streak >= 0),
  error_streak INTEGER NOT NULL DEFAULT 0 CHECK (error_streak >= 0),
  streak_since TEXT CHECK (streak_since IS strftime('%Y-%m-%dT%H:%M:%SZ', streak_since)),
  last_error   TEXT NOT NULL DEFAULT '',
  -- What the last read saw, so the next one can ask for changes only: the ETag and a fingerprint of the job ids.
  etag              TEXT NOT NULL DEFAULT '',
  ids_fingerprint   TEXT NOT NULL DEFAULT '',
  next_read_at      TEXT CHECK (next_read_at IS strftime('%Y-%m-%dT%H:%M:%SZ', next_read_at)),  -- NULL: due now
  change_interval_s INTEGER CHECK (change_interval_s > 0),
  -- How the board was found: #31 sets it; #21 and ranking read it (a name-only stapply match is doubtful).
  found_by     TEXT NOT NULL DEFAULT 'import'
               CHECK (found_by IN ('stapply_name','slug_probe','careers_page','import','hand')),
  created_at   TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%SZ', created_at)),
  UNIQUE (ats, token),
  CHECK ((last_read IS NULL) = (last_read_at IS NULL)),
  CHECK ((last_read IS NULL) = (streak_since IS NULL)),
  CHECK (last_read IS NOT NULL OR (empty_streak = 0 AND error_streak = 0)),
  CHECK (last_read IS NOT 'ok'    OR (empty_streak = 0 AND error_streak = 0)),
  CHECK (last_read IS NOT 'unchanged' OR (empty_streak = 0 AND error_streak = 0)),
  CHECK (last_read IS NOT 'empty' OR (empty_streak >= 1 AND error_streak = 0)),
  CHECK (last_read IS NOT 'error' OR error_streak >= 1)
) STRICT;
CREATE INDEX board_company ON board(company_id);

-- Recent reads per board, for the Needs-you evidence (#16, #12 view 4). #21 keeps the last 10 per board.
CREATE TABLE board_read (
  board_id    INTEGER NOT NULL REFERENCES board(board_id),
  run_id      INTEGER NOT NULL REFERENCES job_run(run_id),
  at          TEXT NOT NULL CHECK (at IS strftime('%Y-%m-%dT%H:%M:%SZ', at)),
  result      TEXT NOT NULL CHECK (result IN ('ok','empty','error','unchanged')),
  http_status INTEGER,
  error       TEXT NOT NULL DEFAULT '',
  postings    INTEGER NOT NULL DEFAULT 0 CHECK (postings >= 0),
  PRIMARY KEY (board_id, run_id),
  CHECK (result = 'error' OR error = '')
) STRICT;

-- A careers page with no readable board, watched for changes (#21): one per company.
CREATE TABLE watched_page (
  company_id     INTEGER PRIMARY KEY REFERENCES company(company_id),
  url            TEXT NOT NULL CHECK (url LIKE 'https://_%' OR url LIKE 'http://_%'),
  fingerprint    TEXT NOT NULL DEFAULT '',
  last_fetch_at  TEXT CHECK (last_fetch_at IS strftime('%Y-%m-%dT%H:%M:%SZ', last_fetch_at)),
  last_change_at TEXT CHECK (last_change_at IS strftime('%Y-%m-%dT%H:%M:%SZ', last_change_at)),
  next_fetch_at  TEXT CHECK (next_fetch_at IS strftime('%Y-%m-%dT%H:%M:%SZ', next_fetch_at)),
  error_streak   INTEGER NOT NULL DEFAULT 0 CHECK (error_streak >= 0)
) STRICT;

CREATE TABLE posting (
  posting_id    INTEGER PRIMARY KEY,
  company_id    INTEGER NOT NULL REFERENCES company(company_id),
  board_id      INTEGER REFERENCES board(board_id),
  source_key    TEXT NOT NULL UNIQUE,
  title         TEXT NOT NULL,
  location      TEXT NOT NULL DEFAULT '',
  country       TEXT,
  url           TEXT NOT NULL DEFAULT '',
  posted_on     TEXT CHECK (posted_on IS date(posted_on)),
  pay           TEXT NOT NULL DEFAULT '',
  first_seen_at TEXT NOT NULL CHECK (first_seen_at IS strftime('%Y-%m-%dT%H:%M:%SZ', first_seen_at)),
  last_seen_at  TEXT NOT NULL CHECK (last_seen_at IS strftime('%Y-%m-%dT%H:%M:%SZ', last_seen_at)),
  closed_at     TEXT CHECK (closed_at IS strftime('%Y-%m-%dT%H:%M:%SZ', closed_at)),
  close_reason  TEXT CHECK (close_reason IN ('gone_from_board','board_empty_twice','page_closed',
                                             'hand','imported_closed')),
  CHECK ((closed_at IS NULL) = (close_reason IS NULL)),
  CHECK (last_seen_at >= first_seen_at),
  CHECK (board_id IS NOT NULL OR source_key LIKE 'url:%')
) STRICT;
CREATE INDEX posting_company_open ON posting(company_id, closed_at);
-- Covers the crawler's per-board read of open postings by source_key.
CREATE INDEX posting_board_open ON posting(board_id, closed_at, source_key);

-- The posting text, apart from posting: a body is 95% of a posting's bytes and spills to overflow pages, so
-- inline it made every read of a later column (closed_at, last_seen_at) walk the chain (store audit 2026-10-20:
-- the #6 join took 2.5 s at 300,000 postings, 32 ms with the body apart). Rows may be deleted to save space.
CREATE TABLE posting_body (
  posting_id  INTEGER PRIMARY KEY REFERENCES posting(posting_id),
  body        TEXT NOT NULL,
  -- When the body was last fetched; NULL for imported bodies.
  fetched_at  TEXT CHECK (fetched_at IS strftime('%Y-%m-%dT%H:%M:%SZ', fetched_at)),
  -- outreach.store.writes.body_sha256(body), lower-case hex; '' for rows written before it existed, filled lazily
  -- by the crawler. The reader keys its queue and readings on this value.
  body_sha256 TEXT NOT NULL DEFAULT ''
              CHECK (body_sha256 = '' OR (length(body_sha256) = 64 AND body_sha256 NOT GLOB '*[^0-9a-f]*'))
) STRICT;

CREATE TABLE ruleset (
  ruleset_version TEXT PRIMARY KEY CHECK (ruleset_version GLOB 'r-[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'),
  config_toml     TEXT NOT NULL,
  created_at      TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%SZ', created_at)),
  is_current      INTEGER NOT NULL DEFAULT 0 CHECK (is_current IN (0,1))
) STRICT;
-- Change the current ruleset in two statements (clear, then set); one UPDATE trips this index going backwards.
CREATE UNIQUE INDEX ruleset_one_current ON ruleset(is_current) WHERE is_current = 1;

CREATE TABLE decision (
  posting_id         INTEGER NOT NULL REFERENCES posting(posting_id),
  ruleset_version    TEXT NOT NULL REFERENCES ruleset(ruleset_version),
  decided_at         TEXT NOT NULL CHECK (decided_at IS strftime('%Y-%m-%dT%H:%M:%SZ', decided_at)),
  lane               TEXT NOT NULL CHECK (lane IN ('engineering','intern','off_lane','not_engineering',
                                                   'general_application')),
  level_tier         TEXT NOT NULL CHECK (level_tier IN ('new_grad','early_career','swe1','swe2','open',
                                                         'not_stated','senior')),
  level_ok           INTEGER NOT NULL CHECK (level_ok IN (0,1)),
  level_basis        TEXT NOT NULL CHECK (length(level_basis) > 0),
  years_asked        INTEGER CHECK (years_asked BETWEEN 0 AND 30),
  years_quote        TEXT NOT NULL DEFAULT '',
  us                 TEXT NOT NULL CHECK (us IN ('yes','no','unknown')),
  sponsorship        TEXT NOT NULL CHECK (sponsorship IN ('yes','no','opt_only','unclear','silent')),
  sponsorship_quote  TEXT NOT NULL DEFAULT '',
  citizenship        INTEGER NOT NULL CHECK (citizenship IN (0,1)),
  citizenship_quote  TEXT NOT NULL DEFAULT '',
  eligibility        TEXT NOT NULL CHECK (eligibility IN ('ok','excluded')),
  eligibility_reason TEXT CHECK (eligibility_reason IN ('grad_window','current_student','cohort_title')),
  eligibility_quote  TEXT NOT NULL DEFAULT '',
  start_2027         INTEGER NOT NULL CHECK (start_2027 IN (0,1)),
  -- 1 until a verified reading agrees (#32); reader_version names that reader and is NULL while provisional. A
  -- final row may have no reader_version: the body could not be read and the rules' hides were relaxed.
  provisional        INTEGER NOT NULL DEFAULT 1 CHECK (provisional IN (0,1)),
  reader_version     TEXT,
  PRIMARY KEY (posting_id, ruleset_version),
  CHECK ((eligibility = 'excluded') = (eligibility_reason IS NOT NULL)),
  CHECK (eligibility = 'ok' OR length(eligibility_quote) > 0),
  CHECK ((sponsorship = 'silent') = (sponsorship_quote = '')),
  CHECK (citizenship = 0 OR length(citizenship_quote) > 0),
  CHECK ((years_asked IS NULL) = (years_quote = '')),
  CHECK (level_ok = 0 OR (lane = 'engineering' AND level_tier NOT IN ('not_stated','senior'))),
  CHECK (provisional = 0 OR reader_version IS NULL)
) STRICT;
CREATE INDEX decision_version ON decision(ruleset_version);

-- The reader's work list (#32 alone writes it): one row per posting, body and reader version, leased out in
-- priority order.
CREATE TABLE read_queue (
  posting_id     INTEGER NOT NULL REFERENCES posting(posting_id),
  body_sha256    TEXT NOT NULL CHECK (length(body_sha256) = 64 AND body_sha256 NOT GLOB '*[^0-9a-f]*'),
  reader_version TEXT NOT NULL CHECK (length(reader_version) > 0),
  priority       INTEGER NOT NULL,
  state          TEXT NOT NULL DEFAULT 'queued' CHECK (state IN ('queued','leased','done','refused')),
  lease_until    TEXT CHECK (lease_until IS strftime('%Y-%m-%dT%H:%M:%SZ', lease_until)),
  attempts       INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  last_error     TEXT NOT NULL DEFAULT '',
  enqueued_at    TEXT NOT NULL CHECK (enqueued_at IS strftime('%Y-%m-%dT%H:%M:%SZ', enqueued_at)),
  PRIMARY KEY (posting_id, body_sha256, reader_version),
  CHECK (state <> 'leased' OR lease_until IS NOT NULL)
) STRICT;
CREATE INDEX read_queue_next ON read_queue(state, priority DESC, enqueued_at);

-- One reading of one body by one reader version. fields is PostingReading.to_json() (outreach/reader/types.py);
-- verified is 1 only when every quote was found in the stored body.
CREATE TABLE posting_reading (
  posting_id     INTEGER NOT NULL REFERENCES posting(posting_id),
  body_sha256    TEXT NOT NULL CHECK (length(body_sha256) = 64 AND body_sha256 NOT GLOB '*[^0-9a-f]*'),
  reader_version TEXT NOT NULL CHECK (length(reader_version) > 0),
  read_at        TEXT NOT NULL CHECK (read_at IS strftime('%Y-%m-%dT%H:%M:%SZ', read_at)),
  fields         TEXT NOT NULL CHECK (json_valid(fields)),
  verified       INTEGER NOT NULL CHECK (verified IN (0,1)),
  refused_fields TEXT NOT NULL DEFAULT '[]'
                 CHECK (json_valid(refused_fields) AND json_type(refused_fields) = 'array'),
  PRIMARY KEY (posting_id, body_sha256, reader_version)
) STRICT;

CREATE TABLE evidence (
  evidence_id  INTEGER PRIMARY KEY,
  company_id   INTEGER NOT NULL REFERENCES company(company_id),
  fact         TEXT NOT NULL CHECK (fact IN ('size','stage','sponsorship','domain','hq','everify','lca',
                                             'cap_exempt','staffing_risk','funding','headcount')),
  value        TEXT NOT NULL CHECK (length(value) > 0),
  num_low      INTEGER CHECK (num_low >= 0),
  num_high     INTEGER CHECK (num_high >= num_low),
  confidence   TEXT NOT NULL CHECK (confidence IN ('confirmed','estimate','guess')),
  locked       INTEGER NOT NULL DEFAULT 0 CHECK (locked IN (0,1)),
  source_kind  TEXT NOT NULL CHECK (source_kind IN ('hand','posting','old_dashboard','yc','builtin',
                                                    'linkedin','website','formd','lca','everify','other',
                                                    'stapply','commoncrawl','hn','simplify')),
  source_ref   TEXT NOT NULL DEFAULT '',
  quote        TEXT NOT NULL DEFAULT '',
  posting_id   INTEGER REFERENCES posting(posting_id),
  observed_on  TEXT CHECK (observed_on IS date(observed_on)),
  recorded_at  TEXT NOT NULL CHECK (recorded_at IS strftime('%Y-%m-%dT%H:%M:%SZ', recorded_at)),
  CHECK (locked = 0 OR source_kind = 'hand'),
  CHECK (fact NOT IN ('size','headcount') OR num_low IS NOT NULL),
  CHECK (fact IN ('size','headcount') OR (num_low IS NULL AND num_high IS NULL)),
  CHECK (fact <> 'sponsorship' OR value IN ('yes','no','opt_only','unclear')),
  CHECK (fact <> 'stage' OR value IN ('pre_seed','seed','series_a','series_b','series_c','series_d',
                                      'series_e_plus','late_private','public','yc','early')),
  CHECK (fact <> 'domain' OR (value = lower(value) AND value NOT LIKE '%@%' AND value LIKE '%_._%')),
  CHECK (fact <> 'everify' OR (value IN ('enrolled','not_enrolled') AND observed_on IS NOT NULL)),
  -- One fiscal year of LCA filings (#31): {"fy","software","jr","level1","newemp_jr"} as integers, plus
  -- "median_jr_wage" (USD a year) when known. json_type() raises on text that is not JSON, so CASE tests that
  -- first; every test is IS or ifnull, so a missing key reads false (a NULL CHECK passes). Extra keys cannot be
  -- refused here (a CHECK has no subquery); EvidenceDraft refuses them.
  CHECK (fact <> 'lca' OR CASE WHEN json_valid(value) THEN
           json_type(value) IS 'object'
           AND json_type(value, '$.fy') IS 'integer' AND json_type(value, '$.software') IS 'integer'
           AND json_type(value, '$.jr') IS 'integer' AND json_type(value, '$.level1') IS 'integer'
           AND json_type(value, '$.newemp_jr') IS 'integer'
           AND ifnull(json_type(value, '$.median_jr_wage'), 'integer') IS 'integer'
           AND ifnull(json_extract(value, '$.fy'), -1) >= 0 AND ifnull(json_extract(value, '$.software'), -1) >= 0
           AND ifnull(json_extract(value, '$.jr'), -1) >= 0 AND ifnull(json_extract(value, '$.level1'), -1) >= 0
           AND ifnull(json_extract(value, '$.newemp_jr'), -1) >= 0
           AND ifnull(json_extract(value, '$.median_jr_wage'), 0) >= 0
         ELSE 0 END),
  CHECK (fact <> 'cap_exempt' OR value IN ('likely','confirmed','no')),
  CHECK (fact <> 'staffing_risk' OR value IN ('low','medium','high')),
  -- A Form D filing (#31): {"filed_on": date, "amount_usd": integer or null, "kind": text}.
  CHECK (fact <> 'funding' OR CASE WHEN json_valid(value) THEN
           json_type(value) IS 'object'
           AND json_type(value, '$.filed_on') IS 'text'
           AND json_extract(value, '$.filed_on') IS date(json_extract(value, '$.filed_on'))
           AND ifnull(json_type(value, '$.amount_usd'), '') IN ('integer','null')
           AND ifnull(json_extract(value, '$.amount_usd'), 0) >= 0
           AND json_type(value, '$.kind') IS 'text' AND length(json_extract(value, '$.kind')) > 0
         ELSE 0 END)
) STRICT;
CREATE INDEX evidence_company ON evidence(company_id, fact);
-- Identity looks companies up by domain.
CREATE INDEX evidence_domain ON evidence(value) WHERE fact = 'domain';
-- Automation re-reading the same source adds nothing; hand rows are never de-duplicated (Review Focus 3).
-- Writers use INSERT ... ON CONFLICT DO NOTHING, never INSERT OR IGNORE, which also swallows CHECK failures.
CREATE UNIQUE INDEX evidence_once ON evidence(company_id, fact, source_kind, source_ref, value, quote,
  ifnull(observed_on, '')) WHERE locked = 0;

-- Which companies get board finding first (#31 design 5.1); the only work-tier table, written by #31, read by
-- ranking. Tier 3 is staffing-flagged and is worked only when promoted. source_version is the source version
-- that set the tier.
CREATE TABLE work_tier (
  company_id     INTEGER PRIMARY KEY REFERENCES company(company_id),
  tier           INTEGER NOT NULL CHECK (tier IN (1,2,3)),
  promoted       INTEGER NOT NULL DEFAULT 0 CHECK (promoted IN (0,1)),
  reason         TEXT NOT NULL CHECK (length(trim(reason)) > 0),
  source_version TEXT NOT NULL DEFAULT '',
  set_at         TEXT NOT NULL CHECK (set_at IS strftime('%Y-%m-%dT%H:%M:%SZ', set_at))
) STRICT;

-- HN "who is hiring" and similar posts, kept so the people plan can link to them and reply.
CREATE TABLE company_post (
  post_id     INTEGER PRIMARY KEY,
  company_id  INTEGER NOT NULL REFERENCES company(company_id),
  source_name TEXT NOT NULL REFERENCES source(source_name),
  url         TEXT NOT NULL CHECK (length(url) > 0),
  posted_at   TEXT CHECK (posted_at IS strftime('%Y-%m-%dT%H:%M:%SZ', posted_at)),
  text        TEXT NOT NULL DEFAULT '',
  asks_email  INTEGER NOT NULL DEFAULT 0 CHECK (asks_email IN (0,1)),
  UNIQUE (source_name, url)
) STRICT;
CREATE INDEX company_post_company ON company_post(company_id);

-- One score per company and ranker version (#6): signals is name to value, lanes the lanes it qualifies for.
CREATE TABLE company_score (
  company_id     INTEGER NOT NULL REFERENCES company(company_id),
  ranker_version TEXT NOT NULL CHECK (length(ranker_version) > 0),
  score          REAL NOT NULL,
  signals        TEXT NOT NULL CHECK (json_valid(signals)),
  lanes          TEXT NOT NULL CHECK (json_valid(lanes)),
  scored_at      TEXT NOT NULL CHECK (scored_at IS strftime('%Y-%m-%dT%H:%M:%SZ', scored_at)),
  PRIMARY KEY (company_id, ranker_version)
) STRICT;

CREATE TABLE person (
  person_id       INTEGER PRIMARY KEY,
  company_id      INTEGER NOT NULL REFERENCES company(company_id),
  name            TEXT NOT NULL CHECK (length(trim(name)) > 0),
  title           TEXT NOT NULL DEFAULT '',
  role_kind       TEXT NOT NULL CHECK (role_kind IN ('engineer','manager','founder','recruiter','alum','other')),
  email           TEXT CHECK (email IS NULL OR (email = lower(trim(email)) AND email LIKE '_%@_%._%')),
  linkedin_url    TEXT,
  profile_text    TEXT NOT NULL DEFAULT '',
  notes           TEXT NOT NULL DEFAULT '',
  tz              TEXT NOT NULL DEFAULT 'America/New_York',
  channel         TEXT NOT NULL CHECK (channel IN ('email','linkedin')),
  linkedin_reason TEXT CHECK (linkedin_reason IN ('past_budget','no_email','alum_first')),
  email_rank      INTEGER CHECK (email_rank >= 1),
  added_by        TEXT NOT NULL CHECK (added_by IN ('devanshu','claude','import')),
  created_at      TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%SZ', created_at)),
  updated_at      TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%SZ', updated_at)),
  revision        INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
  -- People emailed before the system (contacted.txt) may have no address on record.
  CHECK (channel <> 'email' OR email IS NOT NULL OR added_by = 'import'),
  CHECK (channel <> 'linkedin' OR linkedin_reason IS NOT NULL)
) STRICT;
CREATE UNIQUE INDEX person_email ON person(email) WHERE email IS NOT NULL;
CREATE UNIQUE INDEX person_linkedin ON person(linkedin_url) WHERE linkedin_url IS NOT NULL;
CREATE INDEX person_company ON person(company_id);

-- Public pages about a person (#15): never LinkedIn, which prepare must not read. kind: profile (names the person
-- and the employer), team (they work on the team that owns the role), context (shared context; the label says
-- which), email (a page that publishes the address), post (an HN or similar post whose contact this is).
CREATE TABLE person_source (
  source_id   INTEGER PRIMARY KEY,
  person_id   INTEGER NOT NULL REFERENCES person(person_id),
  kind        TEXT NOT NULL CHECK (kind IN ('profile','team','context','email','post')),
  url         TEXT NOT NULL CHECK (url LIKE 'https://_%'),
  label       TEXT NOT NULL DEFAULT '',
  quote       TEXT NOT NULL DEFAULT '',
  email_basis TEXT CHECK (email_basis IN ('published','verified','pattern')),
  observed_on TEXT CHECK (observed_on IS date(observed_on)),
  recorded_at TEXT NOT NULL CHECK (recorded_at IS strftime('%Y-%m-%dT%H:%M:%SZ', recorded_at)),
  UNIQUE (person_id, kind, url),
  CHECK (instr(lower(url), 'linkedin.com') = 0 AND instr(lower(url), 'lnkd.in') = 0),
  CHECK ((kind = 'email') = (email_basis IS NOT NULL))
) STRICT;
CREATE INDEX person_source_person ON person_source(person_id);

-- One email sequence per person: the outcome of the whole thread. State flags are one-way, so closing a
-- replied or bounced touch keeps when it replied or bounced.
CREATE TABLE touch (
  touch_id        INTEGER PRIMARY KEY,
  person_id       INTEGER NOT NULL UNIQUE REFERENCES person(person_id),
  origin          TEXT NOT NULL DEFAULT 'sender' CHECK (origin IN ('sender','import')),
  state           TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open','replied','bounced','closed')),
  close_reason    TEXT CHECK (close_reason IN ('no_reply','skipped','company_closed','no_go')),
  gmail_thread_id TEXT,
  first_rfc_id    TEXT,
  first_sent_at   TEXT CHECK (first_sent_at IS strftime('%Y-%m-%dT%H:%M:%SZ', first_sent_at)),
  replied_at      TEXT CHECK (replied_at IS strftime('%Y-%m-%dT%H:%M:%SZ', replied_at)),
  reply_kind      TEXT CHECK (reply_kind IN ('unsorted','interested','not_now','said_no','asked_to_stop',
                                             'auto_reply')),
  bounced_at      TEXT CHECK (bounced_at IS strftime('%Y-%m-%dT%H:%M:%SZ', bounced_at)),
  auto_reply_at   TEXT CHECK (auto_reply_at IS strftime('%Y-%m-%dT%H:%M:%SZ', auto_reply_at)),
  closed_at       TEXT CHECK (closed_at IS strftime('%Y-%m-%dT%H:%M:%SZ', closed_at)),
  created_at      TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%SZ', created_at)),
  updated_at      TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%SZ', updated_at)),
  revision        INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
  CHECK (state <> 'replied' OR (replied_at IS NOT NULL AND reply_kind IS NOT NULL AND reply_kind <> 'auto_reply')),
  CHECK (state <> 'bounced' OR bounced_at IS NOT NULL),
  CHECK (state <> 'closed' OR (closed_at IS NOT NULL AND close_reason IS NOT NULL)),
  CHECK (origin = 'import' OR (first_sent_at IS NULL) = (first_rfc_id IS NULL)),
  CHECK (origin = 'import' OR (first_sent_at IS NULL) = (gmail_thread_id IS NULL))
) STRICT;

-- One email: seq 0 is the first email, 1 to 3 the follow-ups (#9: 3, 7 and 15 business days).
CREATE TABLE message (
  message_id       INTEGER PRIMARY KEY,
  touch_id         INTEGER NOT NULL REFERENCES touch(touch_id),
  seq              INTEGER NOT NULL CHECK (seq BETWEEN 0 AND 3),
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
CREATE UNIQUE INDEX message_rfc ON message(rfc_message_id) WHERE rfc_message_id IS NOT NULL;
CREATE UNIQUE INDEX message_pending_rfc ON message(pending_rfc_id) WHERE pending_rfc_id IS NOT NULL;
-- At most one send in flight across the whole store: a second sender process cannot claim.
CREATE UNIQUE INDEX message_one_sending ON message(state) WHERE state = 'sending';
CREATE INDEX message_state ON message(state, due_on);
CREATE INDEX message_sent_at ON message(sent_at) WHERE sent_at IS NOT NULL;

-- Replies, auto-replies and bounces read from Gmail, shown in the thread (#12 view 3).
CREATE TABLE inbound_message (
  inbound_id       INTEGER PRIMARY KEY,
  touch_id         INTEGER NOT NULL REFERENCES touch(touch_id),
  gmail_message_id TEXT NOT NULL UNIQUE,
  from_addr        TEXT NOT NULL,
  subject          TEXT NOT NULL DEFAULT '',
  body_text        TEXT NOT NULL DEFAULT '',
  kind             TEXT NOT NULL CHECK (kind IN ('human','auto_reply','bounce')),
  received_at      TEXT NOT NULL CHECK (received_at IS strftime('%Y-%m-%dT%H:%M:%SZ', received_at)),
  recorded_at      TEXT NOT NULL CHECK (recorded_at IS strftime('%Y-%m-%dT%H:%M:%SZ', recorded_at))
) STRICT;
CREATE INDEX inbound_touch ON inbound_message(touch_id, received_at);

CREATE TABLE linkedin_log (
  entry_id    INTEGER PRIMARY KEY,
  person_id   INTEGER NOT NULL REFERENCES person(person_id),
  kind        TEXT NOT NULL CHECK (kind IN ('request_sent','accepted','message_sent','replied','note','undo')),
  reply_kind  TEXT CHECK (reply_kind IN ('interested','not_now','said_no','asked_to_stop')),
  undoes      INTEGER REFERENCES linkedin_log(entry_id),
  at          TEXT NOT NULL CHECK (at IS strftime('%Y-%m-%dT%H:%M:%SZ', at)),
  note        TEXT NOT NULL DEFAULT '',
  recorded_at TEXT NOT NULL CHECK (recorded_at IS strftime('%Y-%m-%dT%H:%M:%SZ', recorded_at)),
  CHECK ((kind = 'undo') = (undoes IS NOT NULL)),
  CHECK (kind = 'replied' OR reply_kind IS NULL)
) STRICT;
CREATE INDEX linkedin_log_person ON linkedin_log(person_id, at);
CREATE UNIQUE INDEX linkedin_log_undo_once ON linkedin_log(undoes) WHERE undoes IS NOT NULL;

-- The one LinkedIn note per person (#15 writes it, #11 reads and edits it). Claude can never confirm a reason line:
-- only a save by Devanshu moves reason_state past proposed. facts: [{text, label, url, in_note}]; checks: the
-- message.checks shape.
CREATE TABLE linkedin_draft (
  draft_id            INTEGER PRIMARY KEY,
  person_id           INTEGER NOT NULL UNIQUE REFERENCES person(person_id),
  posting_id          INTEGER REFERENCES posting(posting_id),
  body                TEXT NOT NULL CHECK (length(trim(body)) > 0),
  reason_line         TEXT NOT NULL DEFAULT '',
  reason_state        TEXT NOT NULL DEFAULT 'proposed' CHECK (reason_state IN ('proposed','confirmed','written')),
  facts               TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(facts) AND json_type(facts) = 'array'),
  checks              TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(checks) AND json_type(checks) = 'array'),
  saved_by            TEXT NOT NULL CHECK (saved_by IN ('claude','devanshu')),
  prepared_by_version TEXT NOT NULL DEFAULT '',
  created_at          TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%SZ', created_at)),
  updated_at          TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%SZ', updated_at)),
  revision            INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
  CHECK (saved_by <> 'claude' OR reason_state = 'proposed')
) STRICT;

-- The one outcome record per touch, on every channel, for outcome learning. linkedin_log keeps the LinkedIn
-- action history; the API writes both in one transaction for a LinkedIn action, and progress reads only this
-- table, so each touch counts once. An undone touch (logged by mistake) is kept but counts nowhere and takes no
-- outcome. context is written only by outreach.outcomes.context.build_context (#11): exactly the keys source
-- ('plan' or 'own'), lane, roleKind, roleAgeDays, rankerVersion, signals and roleNamed (values may be JSON null
-- where there is none; a missing key is refused, so each test is IS or ifnull: a NULL CHECK passes). A CHECK
-- cannot refuse extra keys; build_context writes none.
CREATE TABLE outreach_touch (
  touch_id        INTEGER PRIMARY KEY,
  person_id       INTEGER REFERENCES person(person_id),
  company_id      INTEGER NOT NULL REFERENCES company(company_id),
  channel         TEXT NOT NULL CHECK (channel IN ('linkedin_request','linkedin_message','email','application',
                                                   'hn_reply','other')),
  posting_id      INTEGER REFERENCES posting(posting_id),
  email_touch_id  INTEGER REFERENCES touch(touch_id),
  sent_at         TEXT NOT NULL CHECK (sent_at IS strftime('%Y-%m-%dT%H:%M:%SZ', sent_at)),
  accepted_at     TEXT CHECK (accepted_at IS strftime('%Y-%m-%dT%H:%M:%SZ', accepted_at)),
  replied_at      TEXT CHECK (replied_at IS strftime('%Y-%m-%dT%H:%M:%SZ', replied_at)),
  reply_kind      TEXT CHECK (reply_kind IN ('unsorted','interested','not_now','said_no','asked_to_stop',
                                             'auto_reply')),
  referred_at     TEXT CHECK (referred_at IS strftime('%Y-%m-%dT%H:%M:%SZ', referred_at)),
  interview_at    TEXT CHECK (interview_at IS strftime('%Y-%m-%dT%H:%M:%SZ', interview_at)),
  offer_at        TEXT CHECK (offer_at IS strftime('%Y-%m-%dT%H:%M:%SZ', offer_at)),
  quiet_at        TEXT CHECK (quiet_at IS strftime('%Y-%m-%dT%H:%M:%SZ', quiet_at)),
  undone_at       TEXT CHECK (undone_at IS strftime('%Y-%m-%dT%H:%M:%SZ', undone_at)),
  context         TEXT NOT NULL CHECK (CASE WHEN json_valid(context) THEN
                    json_type(context) IS 'object'
                    AND ifnull(json_extract(context, '$.source'), '') IN ('plan','own')
                    AND json_type(context, '$.lane') IS NOT NULL
                    AND json_type(context, '$.roleKind') IS NOT NULL
                    AND json_type(context, '$.roleAgeDays') IS NOT NULL
                    AND json_type(context, '$.rankerVersion') IS NOT NULL
                    AND json_type(context, '$.signals') IS 'object'
                    AND ifnull(json_type(context, '$.roleNamed'), '') IN ('true','false')
                  ELSE 0 END),
  -- A double click logs one touch: log_touch returns the touch that already has the key (see the guard triggers).
  idempotency_key TEXT UNIQUE,
  revision        INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
  created_at      TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%SZ', created_at)),
  CHECK (person_id IS NOT NULL OR channel = 'application'),
  CHECK (email_touch_id IS NULL OR channel = 'email'),
  CHECK (accepted_at IS NULL OR accepted_at >= sent_at),
  CHECK (replied_at IS NULL OR replied_at >= sent_at),
  CHECK (referred_at IS NULL OR referred_at >= sent_at),
  CHECK (interview_at IS NULL OR interview_at >= sent_at),
  CHECK (offer_at IS NULL OR offer_at >= sent_at),
  CHECK (quiet_at IS NULL OR quiet_at >= sent_at),
  CHECK (quiet_at IS NULL OR replied_at IS NULL),
  CHECK (reply_kind IS NULL OR replied_at IS NOT NULL),
  CHECK (undone_at IS NULL OR (accepted_at IS NULL AND replied_at IS NULL AND referred_at IS NULL
                               AND interview_at IS NULL AND offer_at IS NULL AND quiet_at IS NULL))
) STRICT;
CREATE INDEX outreach_touch_company ON outreach_touch(company_id, sent_at);
CREATE INDEX outreach_touch_person ON outreach_touch(person_id, sent_at) WHERE person_id IS NOT NULL;
CREATE INDEX outreach_touch_sent ON outreach_touch(channel, sent_at);
-- One email touch is one outcome record, so it is counted once.
CREATE UNIQUE INDEX outreach_touch_email ON outreach_touch(email_touch_id) WHERE email_touch_id IS NOT NULL;

-- A job application he sent; its touch is an outreach_touch with channel 'application' and no person.
CREATE TABLE application (
  application_id INTEGER PRIMARY KEY,
  company_id     INTEGER NOT NULL REFERENCES company(company_id),
  posting_id     INTEGER REFERENCES posting(posting_id),
  applied_on     TEXT NOT NULL CHECK (applied_on IS date(applied_on)),
  url            TEXT NOT NULL DEFAULT '',
  outcome        TEXT NOT NULL DEFAULT 'open'
                 CHECK (outcome IN ('open','screen','interview','offer','rejected','closed')),
  outcome_at     TEXT CHECK (outcome_at IS strftime('%Y-%m-%dT%H:%M:%SZ', outcome_at)),
  revision       INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
  CHECK (outcome = 'open' OR outcome_at IS NOT NULL)
) STRICT;
CREATE INDEX application_company ON application(company_id);

CREATE TABLE sender (
  sender_id     INTEGER PRIMARY KEY CHECK (sender_id = 1),
  paused        INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0,1)),
  pause_kind    TEXT CHECK (pause_kind IN ('by_you','hard_block')),
  paused_reason TEXT NOT NULL DEFAULT '',
  paused_at     TEXT CHECK (paused_at IS strftime('%Y-%m-%dT%H:%M:%SZ', paused_at)),
  updated_at    TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%SZ', updated_at)),
  revision      INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
  CHECK ((paused = 1) = (paused_at IS NOT NULL)),
  CHECK ((paused = 1) = (pause_kind IS NOT NULL))
) STRICT;
INSERT INTO sender (sender_id, updated_at) VALUES (1, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));

-- One row per sending day (America/New_York date): the ramp level that day and how the day ended.
CREATE TABLE send_day (
  day          TEXT PRIMARY KEY CHECK (day IS date(day)),
  send_limit   INTEGER NOT NULL CHECK (send_limit BETWEEN 0 AND 100),
  outcome      TEXT CHECK (outcome IN ('clean','warning','hard_block')),
  outcome_note TEXT NOT NULL DEFAULT '',
  created_at   TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%SZ', created_at))
) STRICT;

CREATE TABLE no_go (
  no_go_id    INTEGER PRIMARY KEY,
  company_id  INTEGER REFERENCES company(company_id),
  person_id   INTEGER REFERENCES person(person_id),
  reason      TEXT NOT NULL CHECK (reason IN ('no_sponsorship','citizenship','over_300','defence_contractor',
                                              'agency','said_no','stop_reply','not_a_fit','other')),
  quote       TEXT NOT NULL DEFAULT '',
  note        TEXT NOT NULL DEFAULT '',
  source      TEXT NOT NULL CHECK (source IN ('devanshu','rules','reply','import','claude')),
  source_ref  TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%SZ', created_at)),
  -- Taken off the list: the row stays, so automation can see the choice and never re-adds it.
  lifted_at   TEXT CHECK (lifted_at IS strftime('%Y-%m-%dT%H:%M:%SZ', lifted_at)),
  lift_reason TEXT,
  CHECK ((company_id IS NOT NULL) + (person_id IS NOT NULL) = 1),
  CHECK ((lifted_at IS NULL) = (lift_reason IS NULL))
) STRICT;
CREATE UNIQUE INDEX no_go_company ON no_go(company_id) WHERE company_id IS NOT NULL AND lifted_at IS NULL;
CREATE UNIQUE INDEX no_go_person  ON no_go(person_id)  WHERE person_id  IS NOT NULL AND lifted_at IS NULL;
-- Every row, lifted or not: a lifted company is never re-added (#6), and that check must not scan.
CREATE INDEX no_go_company_any ON no_go(company_id) WHERE company_id IS NOT NULL;
CREATE INDEX no_go_person_any  ON no_go(person_id)  WHERE person_id  IS NOT NULL;

CREATE TABLE review_item (
  review_id       INTEGER PRIMARY KEY,
  kind            TEXT NOT NULL CHECK (kind IN ('board_unreadable','board_empty','sponsorship_unclear',
                                                'size_unknown','size_guess','domain_missing','flagged',
                                                'merge_candidate','reader_disagrees','careers_changed',
                                                'size_check')),
  company_id      INTEGER NOT NULL REFERENCES company(company_id),
  board_id        INTEGER REFERENCES board(board_id),
  posting_id      INTEGER REFERENCES posting(posting_id),
  person_id       INTEGER REFERENCES person(person_id),
  reason          TEXT NOT NULL CHECK (length(trim(reason)) > 0),
  evidence_key    TEXT NOT NULL,
  raised_by       TEXT NOT NULL CHECK (raised_by IN ('crawler','rules','daily_list','sender','claude','import',
                                                     'source','reader','ranker')),
  state           TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open','resolved')),
  -- cleared: the condition went away by itself (the board read ok again, new evidence arrived).
  resolution      TEXT CHECK (resolution IN ('marked_ok','marked_no_go','field_edited','cleared')),
  resolution_note TEXT NOT NULL DEFAULT '',
  created_at      TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%SZ', created_at)),
  resolved_at     TEXT CHECK (resolved_at IS strftime('%Y-%m-%dT%H:%M:%SZ', resolved_at)),
  revision        INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
  CHECK ((state = 'resolved') = (resolution IS NOT NULL)),
  CHECK ((state = 'resolved') = (resolved_at IS NOT NULL)),
  CHECK (kind NOT IN ('board_unreadable','board_empty') OR board_id IS NOT NULL),
  CHECK (kind <> 'sponsorship_unclear' OR posting_id IS NOT NULL)
) STRICT;
-- Raising the same item on the same evidence again is a no-op, so a resolved item stays resolved
-- until the evidence (and so evidence_key) changes (#16).
CREATE UNIQUE INDEX review_item_once ON review_item(kind, company_id, ifnull(board_id, 0),
  ifnull(posting_id, 0), ifnull(person_id, 0), evidence_key);
CREATE INDEX review_item_open ON review_item(state, created_at);
CREATE INDEX review_item_company ON review_item(company_id, state);
CREATE INDEX review_item_board   ON review_item(board_id)   WHERE board_id   IS NOT NULL;
CREATE INDEX review_item_posting ON review_item(posting_id) WHERE posting_id IS NOT NULL;

CREATE TABLE daily_list (
  list_date TEXT PRIMARY KEY CHECK (list_date IS date(list_date)),
  built_at  TEXT NOT NULL CHECK (built_at IS strftime('%Y-%m-%dT%H:%M:%SZ', built_at)),
  capacity  INTEGER NOT NULL CHECK (capacity BETWEEN 0 AND 60)
) STRICT;

CREATE TABLE daily_list_entry (
  list_date   TEXT NOT NULL REFERENCES daily_list(list_date),
  company_id  INTEGER NOT NULL REFERENCES company(company_id),
  list_group  TEXT NOT NULL CHECK (list_group IN ('level_role','any_us_role','intern_junior','reason_no_role')),
  rank        INTEGER NOT NULL CHECK (rank >= 1),
  state       TEXT NOT NULL DEFAULT 'proposed' CHECK (state IN ('proposed','picked','backlog','removed')),
  posting_id  INTEGER REFERENCES posting(posting_id),
  why         TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(why)),
  proposed_at TEXT NOT NULL CHECK (proposed_at IS strftime('%Y-%m-%dT%H:%M:%SZ', proposed_at)),
  decided_at  TEXT CHECK (decided_at IS strftime('%Y-%m-%dT%H:%M:%SZ', decided_at)),
  revision    INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
  -- The person the people plan picked for this row (#15) and how to reach them (outreach_touch.channel values).
  person_id   INTEGER REFERENCES person(person_id),
  channel     TEXT CHECK (channel IN ('linkedin_request','linkedin_message','email','application','hn_reply',
                                      'other')),
  PRIMARY KEY (list_date, company_id),
  UNIQUE (list_date, rank),
  CHECK ((state = 'proposed') = (decided_at IS NULL)),
  CHECK (person_id IS NULL OR channel IS NOT NULL)
) STRICT;
CREATE INDEX daily_entry_company ON daily_list_entry(company_id, list_date);
-- One row per person per day: the same person never shows twice on one list.
CREATE UNIQUE INDEX daily_entry_person ON daily_list_entry(list_date, person_id) WHERE person_id IS NOT NULL;

CREATE TABLE event (
  event_id        INTEGER PRIMARY KEY,
  at              TEXT NOT NULL CHECK (at IS strftime('%Y-%m-%dT%H:%M:%SZ', at)),
  recorded_at     TEXT NOT NULL CHECK (recorded_at IS strftime('%Y-%m-%dT%H:%M:%SZ', recorded_at)),
  entity          TEXT NOT NULL CHECK (entity IN ('company','person','touch','message','posting','board',
                                                  'evidence','review_item','no_go','daily_list','linkedin',
                                                  'sender','application','source','reading','outreach_touch',
                                                  'score')),
  entity_id       INTEGER,
  company_id      INTEGER REFERENCES company(company_id),
  kind            TEXT NOT NULL CHECK (length(kind) > 0),
  actor           TEXT NOT NULL CHECK (actor IN ('devanshu','claude','sender','crawler','rules','daily_list',
                                                 'import','system','source','reader')),
  detail          TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(detail)),
  idempotency_key TEXT UNIQUE
) STRICT;
CREATE INDEX event_company ON event(company_id, at);
CREATE INDEX event_entity ON event(entity, entity_id, at);

CREATE TABLE job_run (
  run_id          INTEGER PRIMARY KEY,
  job             TEXT NOT NULL CHECK (job IN ('install','rebuild','crawl','daily_list','sender','import',
                                                 'discover','resolve','find_boards','read_boards','watch_pages',
                                                 'read_postings','rank','plan','prepare')),
  started_at      TEXT NOT NULL CHECK (started_at IS strftime('%Y-%m-%dT%H:%M:%SZ', started_at)),
  finished_at     TEXT CHECK (finished_at IS strftime('%Y-%m-%dT%H:%M:%SZ', finished_at)),
  status          TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running','ok','partial','failed')),
  summary         TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(summary)),
  error           TEXT NOT NULL DEFAULT '',
  ruleset_version TEXT REFERENCES ruleset(ruleset_version),
  CHECK ((status = 'running') = (finished_at IS NULL))
) STRICT;
CREATE INDEX job_run_job ON job_run(job, started_at);

CREATE TABLE import_record (
  import_id   INTEGER PRIMARY KEY,
  run_id      INTEGER NOT NULL REFERENCES job_run(run_id),
  source      TEXT NOT NULL CHECK (source IN ('data_js','local_storage','contacts_json','radar_db',
                                              'devboard','contacted_txt','devvault_notes')),
  source_key  TEXT NOT NULL CHECK (length(source_key) > 0),
  raw         TEXT NOT NULL,
  raw_sha256  TEXT NOT NULL CHECK (length(raw_sha256) = 64),
  outcome     TEXT NOT NULL CHECK (outcome IN ('imported','merged','skipped')),
  reason      TEXT NOT NULL DEFAULT '',
  entity      TEXT,
  entity_id   INTEGER,
  imported_at TEXT NOT NULL CHECK (imported_at IS strftime('%Y-%m-%dT%H:%M:%SZ', imported_at)),
  UNIQUE (source, source_key, raw_sha256),
  CHECK (outcome = 'imported' OR length(reason) > 0),
  CHECK ((outcome = 'skipped') = (entity_id IS NULL))
) STRICT;

-- The API's idempotency store (#11): a write replayed with the same Idempotency-Key returns the stored response.
-- fingerprint is sha256 of method, path and the canonical JSON body; only successful writes are kept.
CREATE TABLE api_request (
  request_id  TEXT PRIMARY KEY CHECK (length(request_id) BETWEEN 8 AND 80),
  method      TEXT NOT NULL,
  path        TEXT NOT NULL,
  fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
  status      INTEGER NOT NULL CHECK (status BETWEEN 200 AND 299),
  response    TEXT NOT NULL CHECK (json_valid(response)),
  created_at  TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%SZ', created_at))
) STRICT;

-- Whether a domain has a mail server, cached for the draft checks (#11).
CREATE TABLE mx_check (
  domain     TEXT PRIMARY KEY CHECK (domain = lower(domain)),
  status     TEXT NOT NULL CHECK (status IN ('ok','none')),
  checked_at TEXT NOT NULL CHECK (checked_at IS strftime('%Y-%m-%dT%H:%M:%SZ', checked_at))
) STRICT;

-- Wins he has seen (#11): a win with no row here is unseen.
CREATE TABLE win_seen (
  touch_id INTEGER NOT NULL REFERENCES outreach_touch(touch_id),
  kind     TEXT NOT NULL CHECK (kind IN ('accepted','replied','referred','interview','offer')),
  seen_at  TEXT NOT NULL CHECK (seen_at IS strftime('%Y-%m-%dT%H:%M:%SZ', seen_at)),
  PRIMARY KEY (touch_id, kind)
) STRICT;

-- Watch list rows he dismissed (#11).
CREATE TABLE watch_dismissal (
  kind         TEXT NOT NULL CHECK (kind IN ('new_reason_no_role')),
  ref_id       INTEGER NOT NULL,
  dismissed_at TEXT NOT NULL CHECK (dismissed_at IS strftime('%Y-%m-%dT%H:%M:%SZ', dismissed_at)),
  PRIMARY KEY (kind, ref_id)
) STRICT;

CREATE TRIGGER event_no_update BEFORE UPDATE ON event
  BEGIN SELECT RAISE(ABORT, 'event is append-only'); END;
CREATE TRIGGER event_no_delete BEFORE DELETE ON event
  BEGIN SELECT RAISE(ABORT, 'event is append-only'); END;
CREATE TRIGGER evidence_no_update BEFORE UPDATE ON evidence
  BEGIN SELECT RAISE(ABORT, 'evidence is append-only; add a new row'); END;
CREATE TRIGGER evidence_no_delete BEFORE DELETE ON evidence
  BEGIN SELECT RAISE(ABORT, 'evidence is append-only; add a new row'); END;
CREATE TRIGGER import_record_no_update BEFORE UPDATE ON import_record
  BEGIN SELECT RAISE(ABORT, 'import_record is append-only'); END;
CREATE TRIGGER import_record_no_delete BEFORE DELETE ON import_record
  BEGIN SELECT RAISE(ABORT, 'import_record is append-only'); END;
CREATE TRIGGER linkedin_log_no_update BEFORE UPDATE ON linkedin_log
  BEGIN SELECT RAISE(ABORT, 'linkedin_log is append-only; log an undo'); END;
CREATE TRIGGER linkedin_log_no_delete BEFORE DELETE ON linkedin_log
  BEGIN SELECT RAISE(ABORT, 'linkedin_log is append-only; log an undo'); END;
-- A touch is the record of an action that happened: never deleted (an undo sets undone_at). REPLACE fires the delete
-- trigger only with recursive_triggers ON, so these INSERT and UPDATE guards (which run before conflict resolution)
-- refuse a collision on touch_id, idempotency_key or email_touch_id on any connection. Because a trigger cannot see
-- ON CONFLICT DO NOTHING, a repeated key is refused here too: log_touch looks the key up first and returns the touch
-- that has it, and no other writer inserts into this table. A key or email collision says which key it is; a touch_id
-- collision (only a REPLACE can cause one) says the touch is never deleted.
CREATE TRIGGER outreach_touch_never_deleted BEFORE DELETE ON outreach_touch
  BEGIN SELECT RAISE(ABORT, 'a touch is never deleted; set undone_at'); END;
CREATE TRIGGER outreach_touch_no_replace_insert BEFORE INSERT ON outreach_touch
  WHEN EXISTS (SELECT 1 FROM outreach_touch t WHERE t.touch_id = NEW.touch_id)
  BEGIN SELECT RAISE(ABORT, 'a touch is never deleted; set undone_at'); END;
CREATE TRIGGER outreach_touch_key_no_replace_insert BEFORE INSERT ON outreach_touch
  WHEN NEW.idempotency_key IS NOT NULL
    AND EXISTS (SELECT 1 FROM outreach_touch t WHERE t.idempotency_key = NEW.idempotency_key)
  BEGIN SELECT RAISE(ABORT, 'a touch with this idempotency_key already exists'); END;
CREATE TRIGGER outreach_touch_email_no_replace_insert BEFORE INSERT ON outreach_touch
  WHEN NEW.email_touch_id IS NOT NULL
    AND EXISTS (SELECT 1 FROM outreach_touch t WHERE t.email_touch_id = NEW.email_touch_id)
  BEGIN SELECT RAISE(ABORT, 'this email_touch_id already has a touch'); END;
CREATE TRIGGER outreach_touch_no_replace_update BEFORE UPDATE OF touch_id ON outreach_touch
  WHEN EXISTS (SELECT 1 FROM outreach_touch t WHERE t.touch_id <> OLD.touch_id AND t.touch_id = NEW.touch_id)
  BEGIN SELECT RAISE(ABORT, 'a touch is never deleted; set undone_at'); END;
CREATE TRIGGER outreach_touch_key_no_replace_update BEFORE UPDATE OF idempotency_key ON outreach_touch
  WHEN NEW.idempotency_key IS NOT NULL
    AND EXISTS (SELECT 1 FROM outreach_touch t WHERE t.touch_id <> OLD.touch_id
                AND t.idempotency_key = NEW.idempotency_key)
  BEGIN SELECT RAISE(ABORT, 'a touch with this idempotency_key already exists'); END;
CREATE TRIGGER outreach_touch_email_no_replace_update BEFORE UPDATE OF email_touch_id ON outreach_touch
  WHEN NEW.email_touch_id IS NOT NULL
    AND EXISTS (SELECT 1 FROM outreach_touch t WHERE t.touch_id <> OLD.touch_id
                AND t.email_touch_id = NEW.email_touch_id)
  BEGIN SELECT RAISE(ABORT, 'this email_touch_id already has a touch'); END;

CREATE TRIGGER inbound_no_update BEFORE UPDATE ON inbound_message
  BEGIN SELECT RAISE(ABORT, 'inbound_message is append-only'); END;
CREATE TRIGGER inbound_no_delete BEFORE DELETE ON inbound_message
  BEGIN SELECT RAISE(ABORT, 'inbound_message is append-only'); END;
CREATE TRIGGER posting_never_deleted BEFORE DELETE ON posting
  BEGIN SELECT RAISE(ABORT, 'postings are never deleted; set closed_at and close_reason'); END;
-- REPLACE fires the delete trigger only with recursive_triggers ON; these hold on any connection (INSERT and UPDATE
-- triggers fire before conflict resolution). An INSERT is refused on a taken posting_id only, not on a taken
-- source_key, because the crawler upserts on source_key (#21: INSERT ... ON CONFLICT(source_key) DO UPDATE).
CREATE TRIGGER posting_no_replace_insert BEFORE INSERT ON posting
  WHEN EXISTS (SELECT 1 FROM posting p WHERE p.posting_id = NEW.posting_id)
  BEGIN SELECT RAISE(ABORT, 'postings are never deleted; set closed_at and close_reason'); END;
CREATE TRIGGER posting_no_replace_update BEFORE UPDATE OF posting_id, source_key ON posting
  WHEN EXISTS (SELECT 1 FROM posting p WHERE p.posting_id <> OLD.posting_id
               AND (p.posting_id = NEW.posting_id OR p.source_key = NEW.source_key))
  BEGIN SELECT RAISE(ABORT, 'postings are never deleted; set closed_at and close_reason'); END;

-- A message is born planned or drafted (sent only for imported history); approval is always an UPDATE.
-- A follow-up exists beyond planned only once the first email has gone out.
CREATE TRIGGER message_insert_state BEFORE INSERT ON message
  WHEN NEW.state NOT IN ('planned','drafted','sent')
    OR (NEW.seq > 0 AND NEW.state <> 'planned'
        AND (SELECT first_sent_at FROM touch WHERE touch_id = NEW.touch_id) IS NULL)
  BEGIN SELECT RAISE(ABORT, 'a message starts planned or drafted; a follow-up waits for the first send'); END;

-- Only these state changes exist. sent is final, so a sent message can never be re-drafted and re-sent.
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

CREATE TRIGGER message_followup_waits BEFORE UPDATE OF state ON message
  WHEN NEW.seq > 0 AND OLD.state = 'planned' AND NEW.state <> 'planned'
    AND (SELECT first_sent_at FROM touch WHERE touch_id = NEW.touch_id) IS NULL
  BEGIN SELECT RAISE(ABORT, 'a follow-up leaves planned only after the first email is sent'); END;

-- Once claimed, the text and identity of a message are frozen.
-- A claimed or sent message is the record of an email that may have gone out: never deleted, and with
-- recursive_triggers ON (connect() and install set it) INSERT OR REPLACE cannot erase it either.
CREATE TRIGGER message_no_delete_claimed BEFORE DELETE ON message
  WHEN OLD.state IN ('sending','send_unknown','sent')
  BEGIN SELECT RAISE(ABORT, 'a claimed or sent message is never deleted'); END;
-- On a connection without recursive_triggers (the sqlite3 shell, a DB browser) REPLACE skips the delete trigger, so
-- an INSERT or UPDATE that would collide with a claimed or sent message on any unique key is refused before conflict
-- resolution runs.
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
-- The same for the one-sending index: UPDATE OR REPLACE ... SET state = 'sending' would erase the message sending
-- now. Its error still names UNIQUE, as the index's own error does.
CREATE TRIGGER message_no_replace_sending BEFORE UPDATE OF state ON message
  WHEN NEW.state = 'sending' AND OLD.state <> 'sending'
    AND EXISTS (SELECT 1 FROM message m WHERE m.state = 'sending' AND m.message_id <> OLD.message_id)
  BEGIN SELECT RAISE(ABORT, 'UNIQUE: only one message is sending at a time'); END;

CREATE TRIGGER message_frozen_once_claimed BEFORE UPDATE OF subject, body, seq, touch_id ON message
  WHEN OLD.state IN ('sending','send_unknown','sent')
    AND (NEW.subject IS NOT OLD.subject OR NEW.body IS NOT OLD.body
         OR NEW.seq IS NOT OLD.seq OR NEW.touch_id IS NOT OLD.touch_id)
  BEGIN SELECT RAISE(ABORT, 'a message is frozen once it is claimed for sending'); END;

-- The value of each fact a company shows (one row per company and fact, picked by a correlated subquery so a
-- join reads only that company's evidence; a window function here sorted all evidence on every join): a hand (locked) row wins, and among hand rows the newest wins
-- whatever its confidence; then confirmed over estimate over guess; then the newest.
CREATE VIEW company_fact AS
SELECT e.evidence_id, e.company_id, e.fact, e.value, e.num_low, e.num_high, e.confidence, e.locked,
       e.source_kind, e.source_ref, e.quote, e.posting_id, e.observed_on, e.recorded_at
FROM evidence e
WHERE e.evidence_id = (
  SELECT e2.evidence_id FROM evidence e2
  WHERE e2.company_id = e.company_id AND e2.fact = e.fact
  ORDER BY e2.locked DESC,
           CASE WHEN e2.locked = 1 THEN e2.recorded_at END DESC,
           CASE e2.confidence WHEN 'confirmed' THEN 0 WHEN 'estimate' THEN 1 ELSE 2 END,
           e2.recorded_at DESC, e2.evidence_id DESC
  LIMIT 1);

-- Decisions under the current ruleset (every open posting has one after a rebuild).
CREATE VIEW current_decision AS
SELECT d.* FROM decision d
JOIN ruleset r ON r.ruleset_version = d.ruleset_version AND r.is_current = 1;

-- One decision per posting: the current one, else the newest kept (closed postings are not re-decided).
CREATE VIEW posting_decision AS
SELECT d.posting_id, d.ruleset_version, d.decided_at, d.lane, d.level_tier, d.level_ok, d.level_basis,
       d.years_asked, d.years_quote, d.us, d.sponsorship, d.sponsorship_quote, d.citizenship, d.citizenship_quote,
       d.eligibility, d.eligibility_reason, d.eligibility_quote, d.start_2027, d.provisional, d.reader_version
FROM decision d
WHERE d.rowid = (
  SELECT d2.rowid FROM decision d2 JOIN ruleset r ON r.ruleset_version = d2.ruleset_version
  WHERE d2.posting_id = d.posting_id
  ORDER BY r.is_current DESC, d2.decided_at DESC, d2.ruleset_version DESC
  LIMIT 1);
