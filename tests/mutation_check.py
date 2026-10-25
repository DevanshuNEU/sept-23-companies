#!/usr/bin/env python3
"""Mutation check: break each rules and store guarantee on purpose, confirm a test notices.

    uv run python tests/mutation_check.py            # from the repo root
    uv run python tests/mutation_check.py --list
    uv run python tests/mutation_check.py --shard 2/6   # only entries whose zero-based index % 6 == 2

NOT part of `unittest discover`, deliberately: it edits source files in place and must never run as a side effect
of the ordinary suite. Run it after changing the rules or the store.

WHY IT EXISTS. A green suite is evidence of nothing until you have watched it go red. Each mutation below is a real
regression someone could plausibly introduce by "simplifying" the code, and each names the guarantee it breaks.
The `expect` field names the test (or a part of its id) that should be among the failures; a mutation caught only by
other tests is still CAUGHT, but the run prints a note so a weak spot is not hidden.

SAFETY. The run refuses to start in the live checkout (after #21 launchd runs the rules from there). The baseline
suite must be green first, and every file a mutation touches must be clean in git (a leftover from a killed run
would otherwise be read as the original). Each file is restored in a `finally` block and the restore is verified
by re-reading its bytes; SIGTERM and SIGHUP are turned into SystemExit so that block runs when the run is stopped.
(SIGKILL cannot be caught: fix with `git checkout -- <path>`.) A missing anchor is SKIP and fails the run, so a
refactor that moves the code cannot make a mutation pass silently. A mutation that does not compile or parse is a
runner error, never CAUGHT.
"""
import argparse
import pathlib
import signal
import subprocess
import sys
import tomllib

SUITE_TIMEOUT = 600  # seconds for one run of the whole suite

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import live_checkout  # noqa: E402

RULES = ROOT / "outreach" / "rules"
STORE = ROOT / "outreach" / "store"
READER = ROOT / "outreach" / "reader"
CRAWL = ROOT / "outreach" / "crawl"
RANKING = ROOT / "outreach" / "ranking"
NET = ROOT / "outreach" / "net.py"
SOURCES = ROOT / "outreach" / "sources"
SENDING = ROOT / "outreach" / "sending"
IMPORTER = ROOT / "outreach" / "importer"
REVIEW = ROOT / "outreach" / "review"
PLAN = ROOT / "outreach" / "plan"
REFRESH = ROOT / "refresh"
SCHEMA = STORE / "migrations" / "0001_initial.sql"

# (file, guarantee broken, find, replace, expect). `find` must be exact text from the code as it is now and appear
# in the file; the first occurrence is replaced. `expect` is a part of the id of a test that must fail.
MUTATIONS = [
    # ---- rules/title.py ---------------------------------------------------------------------------------------
    (RULES / "title.py", "MTS rewrite removed, so the senior check sees 'staff'",
     'tt = _MTS.sub("MTS", title)', "tt = title", "Misfire1"),
    (RULES / "title.py", "II and 2 dropped from the SWE II numeral set",
     '_SWE2 = {"ii", "2"}', "_SWE2 = set()", "Misfire2"),
    (RULES / "title.py", "the 'staff engineer i' exemption from the senior words is restored",
     '    if (m := phrases(t["senior"]).search(tt)) and not phrases(t["strong_entry"]).search(tt):',
     '    if (m := phrases(t["senior"]).search(tt)) and not phrases(t["strong_entry"]).search(tt) '
     'and not re.search(r"staff engineer i", tt, re.IGNORECASE):',
     "Misfire3"),
    (RULES / "title.py", "strong-entry override of the senior words removed",
     ' and not phrases(t["strong_entry"]).search(tt):', ":", "Misfire4"),
    (RULES / "title.py", "numerals accepted anywhere in the title, not only after a level noun",
     "        found.append((f\"{m.group('noun')} {num}\", re.sub(r\"\\s+\", \"\", num).lower()))\n    return found",
     "        found.append((f\"{m.group('noun')} {num}\", re.sub(r\"\\s+\", \"\", num).lower()))\n"
     "    found += [(m.group(0), m.group(0).lower()) for m in re.finditer(r\"(?<!\\w)\" + _NUMERAL, title, "
     "re.IGNORECASE)]\n    return found",
     "Misfire8"),
    (RULES / "title.py", "III, IV, V, 3, 4 and 5 after a level noun no longer read as senior",
     '_SENIOR_NUMERALS = {"iii", "iv", "v", "3", "4", "5"}', "_SENIOR_NUMERALS = set()", "Full Stack"),

    # ---- rules/years.py ---------------------------------------------------------------------------------------
    (RULES / "years.py", "degree-alternative exclusion removed",
     "if p.degree_alternative.search(s, end, found.start() if found else degree_end):", "if False:", "Misfire5"),
    (RULES / "years.py", "the largest requirement becomes the smallest (max to min)",
     "if best is None or n > best[0]:", "if best is None or n < best[0]:", "years"),

    # ---- rules/location.py ------------------------------------------------------------------------------------
    (RULES / "location.py", "the non-US list is checked before US state names",
     "    found_state = p.state_names.search(part) is not None\n",
     '    if p.non_us.search(part):\n        return "no"\n    found_state = p.state_names.search(part) is not None\n',
     "Misfire6"),
    (RULES / "location.py", "non-US-only cities lose to a two-letter state code",
     "    if p.non_us.search(rest):", "    if p.non_us.search(rest) and not p.state_code.search(rest):", "location"),

    (RULES / "location.py", "a structured non-US country beats a US location in the text again",
     '        read = _read_country(country.strip(), p)\n        if read == "yes":',
     '        read = _read_country(country.strip(), p)\n        if read is not None:',
     "test_a_us_location_in_the_text_beats_a_non_us_country"),

    # ---- rules/sponsorship.py and its topic words -------------------------------------------------------------
    (RULES / "sponsorship.py", "the negation check removed",
     "    if topics and (m := p.negation.search(clause)):\n        return NO, m\n", "", "Misfire7"),
    (RULES / "sponsorship.py", "a yes sentence with a negator no longer cancels a refusal in another sentence",
     "    if NO in by_stance and YES not in by_stance and not leans_yes:",
     "    if NO in by_stance and YES not in by_stance:", "test_wave_2_a_yes_with_a_negator_still_cancels_a_refusal"),
    (RULES / "sponsorship.py", "a refusal scoped to interns, contractors or non-US roles reads a flat no",
     "    if reading == NO and p.refusal_scope.search(clause):", "    if False:",
     "test_wave_2_a_scoped_refusal_is_not_a_flat_no"),
    (RULES / "config.toml", "'immigration' added to the topic words",
     '"sponsorship", "sponsorships", "visa", "visas", "h-1b", "h1b",\n]',
     '"sponsorship", "sponsorships", "visa", "visas", "h-1b", "h1b", "immigration",\n]',
     "sponsorship"),

    # ---- rules/facts.py ---------------------------------------------------------------------------------------
    (RULES / "facts.py", "'no filing record' wording read as a small size",
     "    if p.not_size.search(read):\n        return None\n", "", "SmallDefaultIsUnknown"),

    # ---- rules/config.toml numbers ----------------------------------------------------------------------------
    (RULES / "config.toml", "max_years_asked = 3 becomes 4", "max_years_asked = 3", "max_years_asked = 4", "Misfire2"),
    (RULES / "config.toml", 'graduated = "2026-05" becomes "2027-05"', 'graduated = "2026-05"',
     'graduated = "2027-05"', "eligibility"),

    # ---- rules/eligibility.py ---------------------------------------------------------------------------------
    (RULES / "eligibility.py", "the current_student step removed",
     "    for m in p.current_student.finditer(body):", "    for m in ():", "eligibility"),

    # ---- rules/rebuild.py and rules/version.py ----------------------------------------------------------------
    (RULES / "rebuild.py", "to_hidden is never appended",
     "            if was and not now:", "            if False:", "test_flip_to_hidden_is_listed"),
    (RULES / "rebuild.py", "record_evidence uses INSERT OR IGNORE, which also swallows a bad draft",
     '"INSERT INTO evidence(company_id, fact, value, num_low, num_high, confidence, locked, source_kind, "\n'
     '                "source_ref, quote, posting_id, observed_on, recorded_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?) "\n'
     '                "ON CONFLICT DO NOTHING",',
     '"INSERT OR IGNORE INTO evidence(company_id, fact, value, num_low, num_high, confidence, locked, source_kind, "\n'
     '                "source_ref, quote, posting_id, observed_on, recorded_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)",',
     "test_record_evidence_raises_on_a_bad_draft"),
    (RULES / "version.py", "config.toml left out of the ruleset hash",
     'body = path.read_bytes() if path.is_file() else b""',
     'body = path.read_bytes() if path.is_file() and name != "config.toml" else b""',
     "test_version_changes_with_config"),

    # ---- store/db.py ------------------------------------------------------------------------------------------
    (STORE / "db.py", "is_live_path always False",
     '    """True when `path` is the default store file under any spelling."""\n',
     '    """True when `path` is the default store file under any spelling."""\n    return False\n', "live"),
    (STORE / "db.py", "connect calls migrate, so opening the store can change it",
     '        have = conn.execute("PRAGMA user_version").fetchone()[0]\n',
     '        from outreach.store.migrate import migrate\n        migrate(conn)\n'
     '        have = conn.execute("PRAGMA user_version").fetchone()[0]\n',
     "test_open_never_migrates"),
    (STORE / "db.py", "is_live_path stops case-folding",
     'return unicodedata.normalize("NFD", path).casefold()', 'return unicodedata.normalize("NFD", path)', "live"),
    (STORE / "db.py", "recursive_triggers left off, so INSERT OR REPLACE erases a sent message or an append-only row",
     '        conn.execute("PRAGMA recursive_triggers = ON")\n', "",
     "test_insert_or_replace_cannot_erase_protected_rows"),
    (STORE / "writes.py", "the event idempotency key upserts (DO UPDATE SET at) instead of doing nothing",
     "ON CONFLICT(idempotency_key) DO NOTHING", "ON CONFLICT(idempotency_key) DO UPDATE SET at = excluded.at",
     "idempot"),

    # ---- store/migrations/0001_initial.sql --------------------------------------------------------------------
    (SCHEMA, "the event_no_update trigger removed",
     "CREATE TRIGGER event_no_update BEFORE UPDATE ON event\n  BEGIN SELECT RAISE(ABORT, 'event is append-only'); END;\n",
     "", "test_events_are_append_only"),
    # `e.locked DESC` alone is an equivalent mutant: the next key (a CASE that is NULL for unlocked rows) already
    # sorts every hand row first, because SQLite puts NULLs last in DESC order. So the hand-first guarantee is
    # broken by removing both keys.
    (SCHEMA, "the locked keys removed from company_fact, so automation can outrank a hand value",
     "  ORDER BY e2.locked DESC,\n           CASE WHEN e2.locked = 1 THEN e2.recorded_at END DESC,\n",
     "  ORDER BY\n", "test_automation_never_outranks_a_hand_value"),
    (SCHEMA, "the newest hand row no longer wins: the hand rows' recorded_at ordering key removed",
     "           CASE WHEN e2.locked = 1 THEN e2.recorded_at END DESC,\n", "",
     "test_newest_hand_row_wins_whatever_its_confidence"),
    (SCHEMA, "a claimed or sent message can be deleted: the message_no_delete_claimed trigger removed",
     "  WHEN OLD.state IN ('sending','send_unknown','sent')\n  BEGIN SELECT RAISE(ABORT, 'a claimed or sent message is never deleted'); END;\n",
     "  WHEN 0\n  BEGIN SELECT RAISE(ABORT, 'a claimed or sent message is never deleted'); END;\n",
     "test_a_claimed_or_sent_message_is_never_deleted"),
    (SCHEMA, "the BEFORE INSERT replace guard on message removed, so a plain connection can REPLACE a sent message",
     "CREATE TRIGGER message_no_replace_claimed_insert BEFORE INSERT ON message\n  WHEN EXISTS",
     "CREATE TRIGGER message_no_replace_claimed_insert BEFORE INSERT ON message\n  WHEN 0 AND EXISTS",
     "test_replace_cannot_erase_a_sent_message_without_recursive_triggers"),
    (SCHEMA, "the message_legal_transition trigger removed",
     "CREATE TRIGGER message_legal_transition BEFORE UPDATE OF state ON message\n"
     "  WHEN NEW.state <> OLD.state AND (OLD.state || '>' || NEW.state) NOT IN (\n"
     "    'planned>drafted', 'planned>skipped',\n"
     "    'drafted>approved', 'drafted>skipped',\n"
     "    'approved>drafted', 'approved>sending', 'approved>skipped',\n"
     "    'sending>sent', 'sending>send_unknown',\n"
     "    'send_unknown>sent', 'send_unknown>failed',\n"
     "    'failed>approved', 'failed>drafted', 'failed>skipped',\n"
     "    'skipped>drafted')\n"
     "  BEGIN SELECT RAISE(ABORT, 'illegal message state change'); END;\n",
     "", "test_message_state_machine"),
    # The pipeline tables (#30). Each guard is swapped for CHECK (1), so the statement still parses.
    (SCHEMA, "person_source takes LinkedIn pages, which prepare must never read",
     "  CHECK (instr(lower(url), 'linkedin.com') = 0 AND instr(lower(url), 'lnkd.in') = 0),\n", "  CHECK (1),\n",
     "test_person_source_refuses_linkedin_hosts"),
    (SCHEMA, "a Claude save can confirm a LinkedIn reason line",
     "  CHECK (saved_by <> 'claude' OR reason_state = 'proposed')\n", "  CHECK (1)\n",
     "test_linkedin_draft_claude_cannot_confirm"),
    (SCHEMA, "outreach_touch.idempotency_key not unique, so a double click logs two touches",
     "  idempotency_key TEXT UNIQUE,\n", "  idempotency_key TEXT,\n", "test_outreach_touch_idempotency_key_unique"),
    (SCHEMA, "an undone touch can take an outcome",
     "  CHECK (undone_at IS NULL OR (accepted_at IS NULL AND replied_at IS NULL AND referred_at IS NULL\n"
     "                               AND interview_at IS NULL AND offer_at IS NULL AND quiet_at IS NULL))\n",
     "  CHECK (1)\n", "test_outreach_touch_undone_excludes_outcomes"),
    # The ifnull(..., -1) >= 0 test below the type tests also refuses a missing key, so this mutation breaks both.
    (SCHEMA, "an lca key test uses = again and has no ifnull range test, so a missing key reads NULL and the CHECK passes",
     "AND json_type(value, '$.newemp_jr') IS 'integer'\n"
     "           AND ifnull(json_type(value, '$.median_jr_wage'), 'integer') IS 'integer'\n"
     "           AND ifnull(json_extract(value, '$.fy'), -1) >= 0 AND ifnull(json_extract(value, '$.software'), -1) >= 0\n"
     "           AND ifnull(json_extract(value, '$.jr'), -1) >= 0 AND ifnull(json_extract(value, '$.level1'), -1) >= 0\n"
     "           AND ifnull(json_extract(value, '$.newemp_jr'), -1) >= 0\n",
     "AND json_type(value, '$.newemp_jr') = 'integer'\n"
     "           AND ifnull(json_type(value, '$.median_jr_wage'), 'integer') IS 'integer'\n"
     "           AND ifnull(json_extract(value, '$.fy'), -1) >= 0 AND ifnull(json_extract(value, '$.software'), -1) >= 0\n"
     "           AND ifnull(json_extract(value, '$.jr'), -1) >= 0 AND ifnull(json_extract(value, '$.level1'), -1) >= 0\n",
     "test_evidence_lca_value_must_be_json"),
    (SCHEMA, "an outreach_touch can be deleted: the outreach_touch_never_deleted trigger removed",
     "CREATE TRIGGER outreach_touch_never_deleted BEFORE DELETE ON outreach_touch\n  BEGIN SELECT",
     "CREATE TRIGGER outreach_touch_never_deleted BEFORE DELETE ON outreach_touch\n  WHEN 0\n  BEGIN SELECT",
     "test_an_outreach_touch_is_never_deleted"),
    (SCHEMA, "the BEFORE INSERT replace guard on outreach_touch removed, so a plain connection can REPLACE a touch",
     "CREATE TRIGGER outreach_touch_no_replace_insert BEFORE INSERT ON outreach_touch\n  WHEN EXISTS",
     "CREATE TRIGGER outreach_touch_no_replace_insert BEFORE INSERT ON outreach_touch\n  WHEN 0 AND EXISTS",
     "test_replace_cannot_overwrite_an_outreach_touch_without_recursive_triggers"),
    (SCHEMA, 'the BEFORE UPDATE touch_id replace guard on outreach_touch removed',
     '  WHEN EXISTS (SELECT 1 FROM outreach_touch t WHERE t.touch_id <> OLD.touch_id AND t.touch_id = NEW.touch_id)',
     '  WHEN 0 AND EXISTS (SELECT 1 FROM outreach_touch t WHERE t.touch_id <> OLD.touch_id AND t.touch_id = NEW.touch_id)',
     "test_replace_cannot_overwrite_an_outreach_touch_without_recursive_triggers"),
    (SCHEMA, 'a repeated idempotency_key is no longer refused on INSERT',
     'CREATE TRIGGER outreach_touch_key_no_replace_insert BEFORE INSERT ON outreach_touch\n  WHEN NEW.idempotency_key IS NOT NULL',
     'CREATE TRIGGER outreach_touch_key_no_replace_insert BEFORE INSERT ON outreach_touch\n  WHEN 0 AND NEW.idempotency_key IS NOT NULL',
     "test_replace_cannot_overwrite_an_outreach_touch_without_recursive_triggers"),
    (SCHEMA, 'a repeated email_touch_id is no longer refused on INSERT',
     'CREATE TRIGGER outreach_touch_email_no_replace_insert BEFORE INSERT ON outreach_touch\n  WHEN NEW.email_touch_id IS NOT NULL',
     'CREATE TRIGGER outreach_touch_email_no_replace_insert BEFORE INSERT ON outreach_touch\n  WHEN 0 AND NEW.email_touch_id IS NOT NULL',
     "test_replace_cannot_overwrite_an_outreach_touch_without_recursive_triggers"),
    (SCHEMA, 'a repeated idempotency_key is no longer refused on UPDATE',
     'CREATE TRIGGER outreach_touch_key_no_replace_update BEFORE UPDATE OF idempotency_key ON outreach_touch\n  WHEN NEW.idempotency_key IS NOT NULL',
     'CREATE TRIGGER outreach_touch_key_no_replace_update BEFORE UPDATE OF idempotency_key ON outreach_touch\n  WHEN 0 AND NEW.idempotency_key IS NOT NULL',
     "test_replace_cannot_overwrite_an_outreach_touch_without_recursive_triggers"),
    (SCHEMA, 'a repeated email_touch_id is no longer refused on UPDATE',
     'CREATE TRIGGER outreach_touch_email_no_replace_update BEFORE UPDATE OF email_touch_id ON outreach_touch\n  WHEN NEW.email_touch_id IS NOT NULL',
     'CREATE TRIGGER outreach_touch_email_no_replace_update BEFORE UPDATE OF email_touch_id ON outreach_touch\n  WHEN 0 AND NEW.email_touch_id IS NOT NULL',
     "test_replace_cannot_overwrite_an_outreach_touch_without_recursive_triggers"),
    (SCHEMA, "one email touch can be counted twice: the outreach_touch_email unique index removed",
     "CREATE UNIQUE INDEX outreach_touch_email ON outreach_touch(email_touch_id) WHERE email_touch_id IS NOT NULL;\n",
     "", "test_one_email_touch_is_counted_once"),
    (SCHEMA, "an lca filing count can be negative",
     "           AND ifnull(json_extract(value, '$.jr'), -1) >= 0 AND ifnull(json_extract(value, '$.level1'), -1) >= 0\n",
     "           AND ifnull(json_extract(value, '$.level1'), -1) >= 0\n",
     "test_evidence_lca_and_funding_numbers_cannot_be_negative"),
    (SCHEMA, "a funding amount can be negative",
     "           AND ifnull(json_extract(value, '$.amount_usd'), 0) >= 0\n", "",
     "test_evidence_lca_and_funding_numbers_cannot_be_negative"),

    # ---- reader/types.py --------------------------------------------------------------------------------------
    (READER / "types.py", "the Unicode hyphens and the soft hyphen no longer join two letters into one word",
     '_JOINERS = r"-\'\\u2019\\u2010\\u2011\\u2012\\u2013\\u00ad"', '_JOINERS = r"-\'\\u2019"',
     "test_other_hyphen_characters_between_letters_join_words"),

    # ---- crawl/base.py (#30) ----------------------------------------------------------------------------------
    (CRAWL / "base.py", "the Oracle grammar is keyed on 'oracle', a name board.ats does not allow",
     '"oracle_orc": _ORACLE', '"oracle": _ORACLE', "test_oracle_is_keyed_on_the_board_ats_name"),
    (CRAWL / "base.py", "an ATS name outside board.ats falls back to the slug grammar again",
     "if not isinstance(token, str) or ats not in BOARD_ATS:", "if not isinstance(token, str):",
     "test_an_ats_name_outside_the_board_vocabulary_is_refused"),

    (NET, "a cross-host redirect keeps the caller's User-Agent (the SEC contact address) again",
     "                headers = {}\n",
     "                headers = {k: v for k, v in (headers or {}).items() if k.lower() == \"user-agent\"}\n",
     "test_a_caller_user_agent_with_an_address_does_not_reach_the_second_host"),

    (READER / "types.py", "quote_in_body's default floor drops from 3 words to 1",
     "def quote_in_body(quote: str, body: str, *, min_words: int = 3) -> bool:",
     "def quote_in_body(quote: str, body: str, *, min_words: int = 1) -> bool:",
     "test_quote_in_body_rejects_short_quote"),
    (READER / "types.py", "quote_in_body accepts a min_words below 1",
     "    if min_words < 1:\n", "    if False:\n",
     "test_min_words_can_raise_the_floor_and_must_be_at_least_one"),

    (SOURCES / "base.py", "a candidate domain may be a URL, a host with a port or one with whitespace again",
     "_NOT_A_HOST.search(d) or ", "", "test_candidate_rejects_bad_domain"),

    # ---- store/writes.py helpers (#30) ------------------------------------------------------------------------
    (STORE / "writes.py", "body_sha256 folds case, so 'A b' and 'a b' are the same body",
     '.strip().encode("utf-8")', '.strip().lower().encode("utf-8")', "test_body_sha256_collapses_whitespace_and_keeps_case"),
    (STORE / "writes.py", "add_alias hands an alias that belongs to another company back as a success",
     "    if owner != company_id:\n", "    if False:\n", "test_add_alias_refuses_other_company"),
    (STORE / "writes.py", "a FEIN alias goes through name_key, which drops the dash",
     "        key = alias.lower()\n", "        key = name_key(alias)\n", "test_add_alias_fein_kept_raw"),
    (STORE / "writes.py", "record_outcome writes an outcome on an undone touch",
     '    if row["undone_at"] is not None:\n        raise ValueError(f"touch {touch_id} was undone")\n',
     '    if False:\n        raise ValueError(f"touch {touch_id} was undone")\n',
     "test_undo_touch_sets_undone_at_and_blocks_outcomes"),
    (STORE / "writes.py", "record_outcome accepts an outcome dated before the touch was sent",
     '    if when < row["sent_at"]:\n', "    if False:\n", "test_record_outcome_refuses_before_sent"),
    (STORE / "writes.py", "a late reply no longer clears quiet_at",
     '"replied_at = ?, reply_kind = ?, quiet_at = NULL"', '"replied_at = ?, reply_kind = ?"',
     "test_replied_stores_the_kind_and_clears_quiet"),
    (STORE / "writes.py", "a second outcome of the same kind moves the date again",
     "    if row[column] is not None:\n", "    if False:\n", "test_a_second_outcome_of_the_same_kind_is_refused"),
    (STORE / "writes.py", "a first reply with no kind is stored as NULL instead of unsorted",
     '        reply_kind = reply_kind or "unsorted"\n', "", "test_a_first_replied_with_no_reply_kind_is_unsorted"),
    (STORE / "writes.py", "undo_touch undoes a touch that has an outcome",
     "    if any(row[c] is not None for c in _OUTCOME_COLUMNS.values()):\n", "    if False:\n",
     "test_undo_touch_refuses_after_an_outcome"),
    (STORE / "writes.py", "log_touch accepts a missing idempotency key, so a retry logs the touch again",
     '    if not isinstance(idempotency_key, str) or not idempotency_key:\n', "    if False:\n",
     "test_log_touch_refuses_a_missing_idempotency_key"),
    (SCHEMA, "an evidence domain can be a URL, carry a port or whitespace, or start or end with a dot",
     "AND value NOT LIKE '%.' AND value NOT GLOB ('*[@/:' || char(9,10,11,12,13,32,160) || ']*')", "",
     "test_evidence_domain_must_be_a_bare_lower_case_host"),
    (SCHEMA, "a reader_disagrees item can be raised with no posting",
     "CHECK (kind NOT IN ('sponsorship_unclear','reader_disagrees') OR posting_id IS NOT NULL)",
     "CHECK (kind <> 'sponsorship_unclear' OR posting_id IS NOT NULL)", "test_reader_disagrees_needs_a_posting"),
    (STORE / "writes.py", "set_reply_kind sets a kind on a touch that never replied",
     '    if row["replied_at"] is None:\n        raise ValueError(f"touch {touch_id} has no reply, so it has no',
     '    if False:\n        raise ValueError(f"touch {touch_id} has no reply, so it has no',
     "test_set_reply_kind_refuses_a_touch_with_no_reply"),
    (STORE / "writes.py", "an auto reply is accepted as a reply kind again",
     '_REPLY_KINDS = ("unsorted", "interested", "not_now", "said_no", "asked_to_stop")',
     '_REPLY_KINDS = ("unsorted", "interested", "not_now", "said_no", "asked_to_stop", "auto_reply")',
     "test_an_auto_reply_is_not_an_outcome"),
    (SCHEMA, "outreach_touch.reply_kind accepts auto_reply again",
     "  reply_kind      TEXT CHECK (reply_kind IN ('unsorted','interested','not_now','said_no','asked_to_stop')),\n  referred_at",
     "  reply_kind      TEXT CHECK (reply_kind IN ('unsorted','interested','not_now','said_no','asked_to_stop','auto_reply')),\n  referred_at",
     "test_outreach_touch_quiet_reply_person_and_context"),

    # ---- crawl (#21): the read contract, the breaker, watched pages and stale postings --------------------------
    (CRAWL / "streaks.py", "the first empty read closes a board's postings (the threshold is 1, not 2)",
     "EMPTIES_TO_CLOSE = 2", "EMPTIES_TO_CLOSE = 1", "test_first_empty_keeps_postings_second_closes_them"),
    (CRAWL / "streaks.py", "two errors in a row raise board_unreadable (the threshold is 2, not 3)",
     "ERRORS_TO_REVIEW = 3", "ERRORS_TO_REVIEW = 2", "test_error_never_closes_and_three_errors_raise_the_item"),
    (CRAWL / "boardstate.py", "an empty read keeps the stored validators, so the board's return reads as unchanged",
     '        etag, fingerprint = "", ""\n', "        etag, fingerprint = board.etag, board.ids_fingerprint\n",
     "test_empty_twice_then_the_same_board_returns_reopens_postings"),
    (CRAWL / "ingest.py", "read_board closes the board's postings after an error read",
     "    if closes_postings(result.result, streaks.empty_streak):\n",
     '    if closes_postings(result.result, streaks.empty_streak) or result.result == "error":\n',
     "test_error_never_closes_and_three_errors_raise_the_item"),
    (CRAWL / "ingest.py", "an error read is diffed like an ok read with no jobs, so every posting is gone_from_board",
     '    if result.result == "ok":\n        existing = load_existing(conn, board.board_id)\n',
     '    if result.result in ("ok", "error"):\n        existing = load_existing(conn, board.board_id)\n',
     "test_error_never_closes_and_three_errors_raise_the_item"),
    (CRAWL / "ats" / "_common.py", "finish_list answers ok for a list with no jobs (an empty board is never empty)",
     '        return ReadResult("empty", (), Validators(), board_name=board_name)\n',
     '        return ReadResult("ok", (), Validators(), board_name=board_name)\n',
     "test_no_jobs_is_empty_with_no_validators"),
    (CRAWL / "polite.py", "a 403 no longer counts as a host failure for the breaker",
     "        elif status == 403 or status >= 500:\n", "        elif status >= 500:\n",
     "test_polite_fetch_errors_403_and_5xx_count"),
    (CRAWL / "tick.py", "a tripped bucket defers every held error, not only those of boards whose last read was good",
     "h.bucket in self.down and self._shielded(h.board, h.now):", "h.bucket in self.down:",
     "test_bad_boards_never_starve_the_healthy_ones"),
    (CRAWL / "tick.py",
     "an outage shields a board however old its last good read is, so a lasting block is deferred forever",
     "                and board.last_read_at > add_seconds(now, -self.cfg.outage_grace_s))",
     "                )", "test_a_host_that_blocks_for_good_ends_with_one_host_item"),
    (CRAWL / "tick.py", "a mass-empty tick is written as that many empty reads, so a broken API closes every posting",
     "            if self.empties[h.bucket] >= MASS_EMPTY_BOARDS:", "            if False:",
     "test_six_boards_reading_empty_at_once_close_nothing"),
    (CRAWL / "ingest.py", "the weekly full read is gone: a week of unchanged reads hides a retitle under the same ids",
     "    validators = Validators() if full else Validators(board.etag, board.ids_fingerprint)",
     "    validators = Validators(board.etag, board.ids_fingerprint)",
     "test_a_week_without_a_listing_finds_a_retitle_under_the_same_ids"),
    (CRAWL / "watch.py", "a non-200 careers page with readable text is fingerprinted like a good read",
     '    if r.status != 200:\n        return _Fetched("error", why=f"HTTP {r.status}")\n', "",
     "test_a_503_with_a_long_readable_page_is_an_error"),
    (CRAWL / "boardstate.py",
     "the empty read that closes the postings leaves the board_empty item saying they stay open",
     "            elif streaks.empty_streak == EMPTIES_TO_CLOSE:", "            elif False:",
     "test_the_empty_read_that_closes_updates_the_open_board_empty_reason"),
    (CRAWL / "tick.py", "the tick counts errors of never-read and already-erroring boards toward its own breaker",
     "if bucket is not None and board.last_read in _GOOD_LAST and result.http_status not in _BOARD_STATUSES:",
     "if bucket is not None and result.http_status not in _BOARD_STATUSES:",
     "test_erroring_boards_with_wrong_shapes_never_trip_the_bucket"),
    (CRAWL / "ingest.py", "a changed title or body is written in one unit but decided in another",
     "            decided += _decide(conn, batch, cfg, rules_cfg, combiner)\n    return decided\n",
     "        decided += _decide(conn, batch, cfg, rules_cfg, combiner)\n    return decided\n",
     "test_a_retitle_and_its_decision_are_one_unit"),
    (CRAWL / "stale.py", "a posting exactly 12 months old is stale (the age cutoff uses <=)",
     "date.fromisoformat(posted_on) < months_before(today, max_age_months)",
     "date.fromisoformat(posted_on) <= months_before(today, max_age_months)",
     "test_exactly_twelve_months_is_not_stale_and_a_day_more_is"),
    (CRAWL / "stale.py", "load_stale reads error_streak as the absence signal in place of empty_streak",
     'b.empty_streak "\n           "FROM posting p LEFT JOIN board b ON b.board_id = p.board_id "\n'
     '           "WHERE p.closed_at IS NULL AND (p.posted_on < ? OR b.empty_streak >= 1)")',
     'b.error_streak "\n           "FROM posting p LEFT JOIN board b ON b.board_id = p.board_id "\n'
     '           "WHERE p.closed_at IS NULL AND (p.posted_on < ? OR b.error_streak >= 1)")',
     "test_error_streak_alone_never_makes_a_posting_stale"),
    (CRAWL / "stale.py", "load_stale returns closed postings (the query neither filters them nor reports closed_at)",
     'p.closed_at, b.empty_streak "\n           "FROM posting p LEFT JOIN board b ON b.board_id = p.board_id "\n'
     '           "WHERE p.closed_at IS NULL AND (p.posted_on',
     'NULL, b.empty_streak "\n           "FROM posting p LEFT JOIN board b ON b.board_id = p.board_id "\n'
     '           "WHERE (p.posted_on', "test_closed_posting_is_not_stale"),
    (CRAWL / "stale.py", "stale_reason reports a closed posting as stale",
     "    if closed_at is not None:\n        return None\n    kinds",
     "    kinds", "test_closed_posting_is_never_stale"),
    # ---- the off switch (#46): no read a host's robots.txt or terms forbid ----------------------------------------
    (CRAWL / "config.py", "a switched-off ATS reads again (the switch is ignored)",
     "        return ats not in self.ats_off\n", "        return True\n",
     "test_shipped_config_switches_off_smartrecruiters_and_pinpoint_with_a_robots_reason"),
    (CRAWL / "config.py", "an unknown ATS name under [ats_off] loads, so a misspelled switch turns nothing off",
     "        if ats not in ATS_HOSTS:\n            raise ValueError(", "        if False:\n            raise ValueError(",
     "test_an_unknown_ats_name_fails_at_load"),
    (CRAWL / "polite.py", "the polite client sends to a switched-off ATS's host",
     "        if ats is not None:\n            raise HostOff(", "        if False:\n            raise HostOff(",
     "test_every_host_of_a_switched_off_ats_is_refused_with_no_request"),
    (CRAWL / "tick.py", "the tick selects boards on a switched-off ATS, so they are deferred and their state changes",
     "supported=reading_ats(cfg), plan_ids=plan_company_ids(conn, plan_date(now)),\n"
     "                              cfg=cfg, limit=limit or cfg.boards_per_tick)",
     "supported=reading_ats(replace(cfg, ats_off={})), plan_ids=plan_company_ids(conn, plan_date(now)),\n"
     "                              cfg=cfg, limit=limit or cfg.boards_per_tick)",
     "test_a_switched_off_board_keeps_its_state_postings_and_decisions_through_a_tick"),
    (CRAWL / "tick.py", "read_company_boards selects boards on a switched-off ATS",
     "supported=reading_ats(cfg), plan_ids=plan_company_ids(conn, plan_date(now)),\n"
     "                              cfg=cfg, limit=cfg.boards_per_tick, only_company_ids=ids, force=True)",
     "supported=reading_ats(replace(cfg, ats_off={})), plan_ids=plan_company_ids(conn, plan_date(now)),\n"
     "                              cfg=cfg, limit=cfg.boards_per_tick, only_company_ids=ids, force=True)",
     "test_read_company_boards_skips_a_switched_off_board_and_counts_it"),
    (CRAWL / "tick.py", "the tick report stops counting the switched-off boards",
     "        off = len(switched_off_boards(conn, cfg.ats_off))\n", "        off = 0\n",
     "test_a_switched_off_board_keeps_its_state_postings_and_decisions_through_a_tick"),
    (CRAWL / "probe.py", "the slug probe asks a switched-off ATS",
     "    asking = [ats for ats in PROBE_ATS if cfg.reads(ats)]", "    asking = list(PROBE_ATS)",
     "test_slug_probing_asks_no_switched_off_ats"),
    (CRAWL / "__main__.py", "`read` reads quietly past a switched-off board and exits 0",
     "        for board in switched_off_boards(conn, cfg.ats_off, only_company_ids=company_ids):",
     "        for board in []:", "test_read_refuses_a_switched_off_board_in_plain_words"),
    (CRAWL / "__main__.py", "`probe` sends a list request to a switched-off ATS",
     "    if ats in cfg.ats_off:\n", "    if False:\n", "test_probe_refuses_a_switched_off_ats_and_sends_nothing"),
    (SOURCES / "stapply.py", "the stapply refresh downloads the wellfound and ycombinator slices again",
     "            if name in TERMS_EXCLUDED:\n", "            if False:\n",
     "test_the_wellfound_and_ycombinator_slices_are_never_downloaded_and_their_rows_are_counted"),
    (REFRESH / "crawl_more.py", "crawl_more passes boards on a dropped ATS to the crawl again",
     "    slugs = [s for s in slugs if s[0] not in DROPPED]\n", "",
     "test_the_refresh_command_line_still_runs"),
    (REFRESH / "crawl_more.py", "crawl_more says nothing when --only or --skip names a dropped ATS",
     '        print(f"{ats} is no longer read ({DROPPED[ats]}); carrying on without it", flush=True)',
     "        pass", "test_only_and_skip_naming_a_dropped_ats_print_one_plain_line_each_and_carry_on"),
    # ---- ranking/ (#6) ----------------------------------------------------------------------------------------
    (RANKING / "hide.py", "write_no_go stops counting a lifted no_go row, so a lifted company is re-added",
     "SELECT 1 FROM no_go WHERE company_id IN (SELECT id FROM grp) LIMIT 1",
     "SELECT 1 FROM no_go WHERE lifted_at IS NULL AND company_id IN (SELECT id FROM grp) LIMIT 1",
     "test_a_lifted_no_go_is_never_re_added_or_re_applied"),
    (RANKING / "hide.py", "write_no_go writes a no_go row for a provisional refusal",
     'if hide.kind != "no_sponsorship" or hide.provisional or hide.posting_id is None:',
     'if hide.kind != "no_sponsorship" or hide.posting_id is None:',
     "test_provisional_refusal_hides_provisionally_and_writes_nothing"),
    (RANKING / "hide.py", "a refusal counts for an intern posting or a posting outside the US",
     'elif r["lane"] == "engineering" and r["us"] != "no":',
     "else:",
     "test_refusals_that_say_nothing_about_his_roles_never_count"),
    (RANKING / "hide.py", "a refusal counts for a posting outside the US (intern postings still skipped)",
     'elif r["lane"] == "engineering" and r["us"] != "no":',
     'elif r["lane"] == "engineering":',
     "test_refusals_that_say_nothing_about_his_roles_never_count"),
    (RANKING / "hide.py", "over_size hides on any size evidence, a guess included",
     's is not None and s.confidence in cfg.hide["size_confidences"] and s.low > over:',
     "s is not None and s.low > over:",
     "test_over_size_needs_evidence"),
    (RANKING / "roles.py", "postings on a doubtful board count as roles",
     'if r["board_id"] in doubtful:\n            g["ignored"] += 1',
     'if False:\n            g["ignored"] += 1',
     "test_doubtful_board_postings_count_for_nothing"),
    (RANKING / "signals" / "small.py", "an unknown size scores the early-stage value instead of neutral",
     'return Assessment(0.0, "Size unknown", grade)',
     'return Assessment(float(p["early_value"]), "Size unknown", grade)',
     "test_small_unknown_size_is_exactly_neutral"),
    (RANKING / "score.py", "a negative signal value is applied with the wrong sign (staffing risk pushes up)",
     "raw = _round(weight * got.value)",
     "raw = _round(weight * abs(got.value))",
     "test_staffing_risk_high_is_below_a_clean_twin"),
    (RANKING / "config.toml", "the large_later_stage weight is 1.0, so it no longer pushes a company to the bottom",
     "large_later_stage = 200.0",
     "large_later_stage = 1.0",
     "test_bottom_weights_outweigh_every_positive_weight"),
    (RANKING / "pay.py", "a bare number with no currency parses as pay",
     '_CUR = r"(?:(?<![A-Za-z])(?:US)?\\$|\\bUSD\\b)\\s{0,2}"',
     '_CUR = r"(?:(?<![A-Za-z])(?:US)?\\$|\\bUSD\\b)?\\s{0,2}"',
     "test_no_pay_is_none"),
    (RANKING / "job.py", "the rank write runs outside the transaction",
     "        with transaction(conn):\n            # The compute read a snapshot",
     "        if True:\n            # The compute read a snapshot",
     "test_a_failure_midway_leaves_the_previous_version_and_no_go_untouched"),
    (RANKING / "version.py", "signals/*.py is dropped from the ranker version hash",
     'rels += [f"signals/{p.name}" for p in signals.glob("*.py")]',
     "rels += []",
     "test_version_moves_with_config_wages_and_a_signal_file"),
    (RANKING / "facts.py", "recent LCA years are the two oldest in the store, not the two newest",
     "evidence WHERE fact = 'lca' \"\n                        \"ORDER BY fy DESC LIMIT ?\"",
     "evidence WHERE fact = 'lca' \"\n                        \"ORDER BY fy ASC LIMIT ?\"",
     "test_lca_recent_uses_the_two_newest_fiscal_years_in_the_store"),
    (RANKING / "facts.py", "a fiscal year keeps its oldest LCA row, not the newest",
     'if data["fy"] not in years or key > years[data["fy"]][0]:',
     'if data["fy"] not in years or key < years[data["fy"]][0]:',
     "test_a_fiscal_year_recorded_on_two_merged_companies_counts_once_with_the_newest_row"),
    (RANKING / "roles.py", "stale postings count toward lanes, the best posting and open_count",
     "is_stale = pid in stale",
     "is_stale = False",
     "test_stale_postings_never_make_a_lane_best_posting_or_open_count"),
    (RANKING / "hide.py", "a stale posting counts as a refusal (and as a sponsor)",
     'if r["board_id"] in doubtful or r["posting_id"] in stale:',
     'if r["board_id"] in doubtful:',
     "test_a_stale_refusal_never_hides_and_writes_no_no_go"),
    (RANKING / "score.py", "the confidence discount is applied to negative points too",
     "points = raw if raw <= 0 else _round(raw * factor(got.confidence, cfg))",
     "points = _round(raw * factor(got.confidence, cfg))",
     "test_a_positive_component_counts_by_its_confidence_and_a_warning_counts_in_full"),
    (RANKING / "score.py", "the confidence discount is applied to no points at all",
     "points = raw if raw <= 0 else _round(raw * factor(got.confidence, cfg))",
     "points = raw",
     "test_a_positive_component_counts_by_its_confidence_and_a_warning_counts_in_full"),
    (RANKING / "confidence.py", "grade_evidence maps a guess to medium",
     '_COLUMN_LEVEL = {"confirmed": "high", "estimate": "medium", "guess": "low"}',
     '_COLUMN_LEVEL = {"confirmed": "high", "estimate": "medium", "guess": "medium"}',
     "test_grade_evidence_maps_the_column"),
    (RANKING / "confidence.py", "rank_confidence ignores the low share of the points",
     'if largest == "low" or share["low"] >= settings["rank_low_share"] - _SHARE_EPS:',
     'if largest == "low":',
     "test_the_low_share_alone_can_make_it_low"),
    (RANKING / "version.py", "the stale_after_months value is dropped from the ranker version hash",
     'digest.update(f"stale_after_months={load_crawl_config().stale_after_months}".encode())',
     'digest.update(b"")',
     "test_version_moves_with_the_stale_module_and_the_stale_months_value"),
    (RANKING / "confidence.py", "an unknown fact (no evidence row) is graded high instead of low",
     'return Confidence("low", f"no {label} evidence")',
     'return Confidence("high", f"no {label} evidence")',
     "test_grades_come_from_the_evidence_rows"),
    (RANKING / "facts.py", "a signal with no stored grade defaults to high instead of low",
     "Confidence(\"low\", f\"no data for {name.replace('_', ' ')}\")",
     "Confidence(\"high\", f\"no data for {name.replace('_', ' ')}\")",
     "test_a_grade_that_is_not_stored_is_the_low_default"),
    (RANKING / "hide.py", "decide_hide forgets a lift, so a company he put back is hidden by size or a refusal",
     'if inputs.no_go == "lifted":\n        return None',
     'if False:\n        return None',
     "test_a_lifted_no_go_of_any_reason_cancels_the_size_hide"),
    (RANKING / "job.py", "the write transaction writes a no_go row for a company closed during the compute",
     "not s.hide.provisional \\\n                        and s.company_id in listed:",
     "not s.hide.provisional:",
     "test_a_company_closed_during_the_compute_gets_no_no_go_row"),

    # ---- sending (#14): the claim, the Sent scan, the ramp, the queue, the classifier, the tick, the guard ----------
    (SENDING / "claim.py", "the claim moved after client.send, so a crash after Gmail took the email leaves no record",
     '    if not claim(conn, q, rfc_id=rfc, now=now):\n        return "lost_race"\n    try:\n'
     '        outgoing = outgoing_for(conn, q, cfg, rfc)\n    except Exception as err:\n'
     '        failure = GmailError("bad_request", None, f"the email could not be built: {describe_error(err)}")\n'
     '        record_unclear(conn, q.message_id, failure, at=_stamp(clock()), cfg=cfg, rng=rng)\n'
     '        raise failure from err\n    try:\n        sent = client.send(outgoing)\n',
     '    try:\n        outgoing = outgoing_for(conn, q, cfg, rfc)\n    except Exception as err:\n'
     '        failure = GmailError("bad_request", None, f"the email could not be built: {describe_error(err)}")\n'
     '        record_unclear(conn, q.message_id, failure, at=_stamp(clock()), cfg=cfg, rng=rng)\n'
     '        raise failure from err\n    try:\n        sent = client.send(outgoing)\n'
     '        if not claim(conn, q, rfc_id=rfc, now=now):\n            return "lost_race"\n',
     "test_send_is_claimed_with_its_message_id_before_the_call"),
    (SENDING / "gmail.py", "find_sent falls back to a To plus Subject match when the Message-ID differs",
     'if normalise_message_id(_headers(msg).get("message-id")) == wanted:',
     'if normalise_message_id(_headers(msg).get("message-id")) == wanted or (\n'
     '                        _headers(msg).get("to") and _headers(msg).get("subject")):',
     "test_find_sent_matches_by_message_id_only"),
    (SENDING / "claim.py", "a miss counts as proof although the scan was incomplete, so a send is called not sent early",
     "if complete and normalise_message_id(scanned) == normalise_message_id(u.rfc_id)",
     "if normalise_message_id(scanned) == normalise_message_id(u.rfc_id)",
     "test_not_found_needs_a_scan_back_to_the_claim"),
    (SENDING / "claim.py", "the attempt cap is removed, so a failing message is retried for ever",
     'if max(len(fails), _attempts(conn, row["message_id"])) >= cfg.max_send_attempts:',
     "if False:",
     "test_requeue_retries_once_then_stops"),
    (SENDING / "mail.py", "a bad request becomes retryable, so a bad address is sent again",
     'RETRYABLE = frozenset({"rate_limit",', 'RETRYABLE = frozenset({"bad_request", "rate_limit",',
     "test_bad_request_failure_is_not_requeued"),
    (SENDING / "ramp.py", "a warning day rises like a clean one",
     '    if outcome == "clean":\n        return _step_up(prev["send_limit"], cfg)',
     '    if outcome in ("clean", "warning"):\n        return _step_up(prev["send_limit"], cfg)',
     "test_warning_day_does_not_rise"),
    (SENDING / "signals.py", "exactly 2% bounces counts as clean (the test becomes <= 2%)",
     "return rate >= cfg.clean_bounce_rate - 1e-12", "return rate > cfg.clean_bounce_rate + 1e-12",
     "test_bounce_rate_of_exactly_two_percent_is_a_warning"),
    (SENDING / "ramp.py", "the ceiling is removed, so the ramp goes past 100",
     "return min(level + cfg.ramp_step, cfg.ramp_ceiling)", "return level + cfg.ramp_step",
     "test_ramp_stops_at_100"),
    (SENDING / "queue.py", "the per-company 60 minute gap is dropped",
     "if sends and now < max(sends) + gap:", "if False:",
     "test_second_send_at_one_company_waits_60_minutes"),
    (SENDING / "queue.py", "the per-company gap is compared with <=, so exactly 60 minutes still waits",
     "if sends and now < max(sends) + gap:", "if sends and now <= max(sends) + gap:",
     "test_exactly_60_minutes_after_the_last_send_is_ready"),
    (SENDING / "queue.py", "the company hold check is removed, so a reply no longer holds the colleagues' email",
     '    if held:\n        return "company_held", None', '    if False:\n        return "company_held", None',
     "test_reply_holds_every_queued_message_at_the_company"),
    (SENDING / "queue.py", "a person's own company is used instead of the surviving one, so a merge is ignored",
     'company_id=resolve_company(companies, row["person_company"])[-1],', 'company_id=row["person_company"],',
     "test_person_at_merged_company_sends_under_the_survivor"),
    (SENDING / "classify.py", "the delay check is removed, so a still-trying notice counts as a bounce",
     'if (DELAY_SUBJ.search(subject) and klass == "unknown") or temporary:', "if False:",
     "test_delay_notice_is_not_a_bounce"),
    (SENDING / "classify.py", "Auto-Submitted is ignored, so an out-of-office reads as a person's reply",
     '    if h.get("auto-submitted", "no").strip().lower() not in ("no", ""):\n        return True\n    if "x-autoreply"',
     '    if "x-autoreply"',
     "test_out_of_office_header_is_not_a_reply"),
    (SENDING / "tick.py", "the dry run's rollback becomes a commit, so a dry run writes",
     '                    self.conn.execute("ROLLBACK")', '                    self.conn.execute("COMMIT")',
     "test_dry_run_writes_nothing"),
    (SENDING / "__main__.py", "the require_live line is removed, so the sender runs from a worktree",
     "        live_checkout.require_live(what, root or live_checkout.REPO_ROOT, live_root or live_checkout.LIVE_ROOT)",
     "        pass",
     "test_run_refuses_outside_the_live_checkout"),
    (SENDING / "tick.py", "the account check is removed, so a token for another account sends",
     "if email.lower() != self.cfg.from_address.lower():", "if False:",
     "test_wrong_account_sends_nothing"),
    (SENDING / "followups.py", "follow-ups are due in calendar days, not business days",
     "due_on = cal.add_business_days(first_sent_on, days, cfg).isoformat()",
     "due_on = (first_sent_on + __import__(\"datetime\").timedelta(days=days)).isoformat()",
     "test_followup_due_seven_business_days_later"),
    (SENDING / "claim.py", "client.send is called inside an open store transaction",
     "        sent = client.send(outgoing)\n    except GmailError as err:\n        returned = _stamp(clock())",
     '        conn.execute("BEGIN IMMEDIATE")\n        sent = client.send(outgoing)\n        conn.execute("COMMIT")\n'
     "    except GmailError as err:\n        returned = _stamp(clock())",
     "test_no_gmail_call_inside_a_transaction"),
    (SENDING / "followups.py", "stop_planned moves the row to skipped instead of deleting it",
     "conn.execute(\"DELETE FROM message WHERE message_id = ? AND state = 'planned'\", (message_id,))",
     "conn.execute(\"UPDATE message SET state = 'skipped' WHERE message_id = ? AND state = 'planned'\", (message_id,))",
     "test_lapse_deletes_the_planned_row_and_records_it_on_the_touch"),
    (SENDING / "ramp.py", "effective_limit returns the start limit on a warning day",
     "return _step_down(start, cfg) if lowered else start", "return start",
     "test_effective_limit_is_one_level_lower_after_a_warning"),

    # ---- importer/ (#17 Part A) ---------------------------------------------------------------------------------
    (IMPORTER / "write.py", "a plan with a held record is applied instead of refused",
     "    held = plan.held()\n    if held:", "    held = plan.held()\n    if False:",
     "test_held_plan_is_refused_before_any_write"),
    (IMPORTER / "report.py", "a skip with any reason counts as explained",
     "for reason in sorted(set(c.skipped) - EXPLAINED_SKIPS):", "for reason in sorted(set()):",
     "test_unknown_skip_reason_fails_the_check"),
    (IMPORTER / "plan.py", "visaDenied also writes a no-go, so a hand note becomes a hide",
     '            hand.append(EvidenceDraft("sponsorship", "no", "confirmed", "hand", ref, visa))\n',
     '            hand.append(EvidenceDraft("sponsorship", "no", "confirmed", "hand", ref, visa))\n'
     '            self.c(key).no_go.append(("defence_contractor", visa))\n',
     "test_visa_denied_is_hand_evidence_and_an_item_not_a_no_go"),
    (IMPORTER / "records.py", "Job Radar's database is opened read-write",
     'f"file:{path}?mode=ro"', 'f"file:{path}?mode=rw"',
     "test_open_radar_is_read_only"),
    (IMPORTER / "write.py", "an approved old contact is stored as approved, so it can send without his click",
     '                message_id = insert(0, "drafted", subject0 or "(no subject)", mail.body0)',
     '                message_id = insert(0, "approved" if mail.old_approval else "drafted", subject0 or "(no subject)", '
     "mail.body0)",
     "test_approved_contact_is_stored_as_a_drafted_message_never_approved"),
    (IMPORTER / "write.py", "imported wins are not marked seen, so switch day announces them all",
     "        if kind in _WINS or (kind == \"replied\" and reply_kind == \"interested\"):\n"
     "            self.conn.execute(\"INSERT INTO win_seen",
     "        if False:\n            self.conn.execute(\"INSERT INTO win_seen",
     "test_imported_wins_are_marked_seen"),
    (IMPORTER / "write.py", "a company the old dashboard ruled out is removed from view",
     '            self._flag(company_id, self.issue_keys[index], self.plan.issues[index].detail)\n',
     '            self._flag(company_id, self.issue_keys[index], self.plan.issues[index].detail)\n'
     '            if self.plan.issues[index].code == "ruled_out":\n'
     '                self.conn.execute("UPDATE company SET status = \'removed\' WHERE company_id = ?", (company_id,))\n',
     "test_ruled_out_company_stays_visible_with_its_note"),
    (IMPORTER / "write.py", "the near-name merge candidate loses its weak confidence",
     'they may be one company", confidence="weak")', 'they may be one company")',
     "test_near_name_item_has_weak_confidence_and_basis_import_near_name"),
    (IMPORTER / "plan.py", "a follow-up due before the import date is planned anyway",
     "                    if due < self.today:\n                        self.followups_not_planned += 1",
     "                    if False:\n                        self.followups_not_planned += 1",
     "test_follow_up_due_before_the_import_is_not_planned_and_counted"),
    (IMPORTER / "plan.py", "a sent contact with no company is held as a settleable record again",
     '            if "sent" in (plan.message0, plan.message1):', "            if False:",
     "test_skip_record_cannot_settle_a_sent_contact_with_no_company"),

    # ---- review/ (#16 Needs you) ----------------------------------------------------------------------------------
    (REVIEW / "items.py", "decided_before finds nothing, so a settled item comes back on a new posting",
     '    person. An item the system cleared is not a decision."""\n',
     '    person. An item the system cleared is not a decision."""\n    return None\n',
     "test_the_same_wording_on_a_new_posting_does_not_return_after_he_marked_it_ok"),
    (REVIEW / "triggers.py", "a ruleset that is not current is read as nothing wrong, so the sweep clears items",
     "    if current is None or current[0] != version:\n        return SyncResult(0, 0, \"ruleset not current\")\n", "",
     "test_ruleset_not_current_skips_and_clears_nothing"),
    (REVIEW / "triggers.py", "a locked sponsorship fact no longer suppresses the item",
     " and owner not in locked}", "}",
     "test_a_locked_sponsorship_fact_suppresses_and_clears"),
    (REVIEW / "hand.py", "a hand fact is written unlocked, so a later automated row can overwrite it",
     "quote, posting_id, observed_on, recorded_at) VALUES (?, ?, ?, ?, ?, ?, 1, 'hand', ?, ?, ?, ?, ?)\"",
     "quote, posting_id, observed_on, recorded_at) VALUES (?, ?, ?, ?, ?, ?, 0, 'hand', ?, ?, ?, ?, ?)\"",
     "test_hand_fact_is_locked_hand_evidence_and_wins_company_fact_over_a_later_confirmed_automated_row"),
    (REVIEW / "merge.py", "unmerge does not put back the pause he set",
     'was.get("previous_paused_at") if status == "working" else None,', "None,",
     "test_unmerge_restores_status_close_reason_and_pause_exactly"),

    # ---- plan/ (#15 Today's plan) -----------------------------------------------------------------------------------
    (PLAN / "contact.py", "the 14-day boundary uses <=, so a request on day fourteen still waits",
     "(today - o.anchor_on).days < rotate_days]", "(today - o.anchor_on).days <= rotate_days]",
     "test_boundary_is_day_fourteen"),
    (PLAN / "contact.py", "an accepted request waits from the sent date, not the accept date",
     "(today - o.anchor_on).days < rotate_days]", "(today - o.sent_on).days < rotate_days]",
     "test_accepted_request_waits_from_the_accept_date"),
    (PLAN / "outbound.py", "an accept no longer moves the anchor of a touch off its sent date",
     "anchor_on=max(anchor, accepted_on or sent_on),", "anchor_on=anchor,",
     "test_accepted_moves_the_anchor_and_replied_and_progress_flags_follow"),
    (PLAN / "outbound.py", "a log entry with a live touch twin counts a second time (the dedupe is removed)",
     "        if (person_id, channel, day) in twins:\n            continue\n", "",
     "test_each_touch_counts_once"),
    (PLAN / "capacity.py", "the month and its requests are counted in UTC, not New York",
     '    tz = cfg.get("plan", "timezone")\n    first, last = month_range(today)',
     '    tz = "UTC"\n    first, last = month_range(today)',
     "test_month_boundary_is_new_york"),
    (PLAN / "capacity.py", "a spent or overspent budget gives a negative day (the floor and the guard are gone)",
     "    remaining = max(0, min(budget - used, hard_limit - hard_used))\n"
     "    days_left = weekdays_left(today)  # 0 on a weekend that has no weekday left in its month\n"
     "    if remaining <= 0 or days_left <= 0:\n",
     "    remaining = min(budget - used, hard_limit - hard_used)\n"
     "    days_left = weekdays_left(today)  # 0 on a weekend that has no weekday left in its month\n"
     "    if days_left <= 0:\n",
     "test_budget_spent_or_overspent_is_zero_not_negative"),
    (PLAN / "select.py", "the role and reason shares are ignored: one pool, strongest score first",
     '    fill(pools["role"], quota["role"])\n    fill(pools["reason"], quota["reason"])\n', "",
     "test_lane_share_80_20"),
    (PLAN / "select.py", "a stale company rotates whatever its place in the ranked list",
     '    if p.position > cfg.get("linkedin", "rotate_top_n"):\n        return False, "stale_below_rotation_cutoff"\n',
     "", "test_stale_rotates_only_near_the_top"),
    (PLAN / "select.py", "a company is not marked used, so one company can take two rows",
     "        used.add(p.company_id)\n        status = p.contact.status\n", "        status = p.contact.status\n",
     "test_capacity_is_never_exceeded"),
    (PLAN / "people.py", "a recruiter is offered even when an engineer, manager, founder or alum is on record",
     '            if p["role_kind"] == "recruiter" and has_non_recruiter:\n                continue\n', "",
     "test_recruiter_only_when_nobody_else_is_on_record"),
    (PLAN / "people.py", "an email is accepted whatever the page quotes (the whole-address check always passes)",
     '    return re.search(pattern, quote_text or "", re.I) is not None', "    return True",
     "test_email_must_be_in_the_quote"),
    (PLAN / "notecheck.py", "the length check fires one character early (>= the limit)",
     "    if len(body) > max_chars:", "    if len(body) >= max_chars:", "test_length_boundary"),
    (PLAN / "notecheck.py", "the playbook's connection-note bans (extra_banned) are not applied",
     '    rules += [(p, re.compile(p, re.I)) for p in cfg.get("note", "extra_banned")]\n', "",
     "test_extra_banned_referral_demand"),
    (PLAN / "drafts.py", "a Claude save may overwrite a confirmed or written line, and a draft he saved",
     '        if stored is not None and saved_by == "claude" and (\n'
     '                stored["reason_state"] in READY_STATES or stored["saved_by"] == "devanshu"):\n',
     "        if False:\n",
     "test_claude_cannot_overwrite_a_confirmed_or_written_line"),
    (PLAN / "drafts.py", "a Claude save may overwrite a draft Devanshu saved when its line is still proposed",
     ' or stored["saved_by"] == "devanshu"):\n', "):\n",
     "test_claude_cannot_overwrite_a_draft_he_saved"),
    (PLAN / "prepare_store.py", "a candidate with a LinkedIn source page is no longer refused by the reply check",
     '    if any(isinstance(s, dict) and isinstance(s.get("url"), str) and is_linkedin_url(s["url"]) for s in raw):\n'
     "        return None\n", "",
     "test_a_linkedin_source_is_refused_before_the_store_door_is_reached"),
    (PLAN / "build.py", "a second build for the same date starts a job run before it finds the plan is there",
     "    if existing is not None:\n        return PlanResult(\"already_built\"", "    if False:\n        return PlanResult(\"already_built\"",
     "test_second_run_changes_nothing"),
    (PLAN / "build.py", "the writes are not one transaction, so a failure midway leaves rows behind",
     "    with transaction(conn):\n        made = conn.execute(",
     "    with __import__(\"contextlib\").nullcontext():\n        made = conn.execute(",
     "test_failure_midway_writes_nothing_and_a_rerun_builds"),
    (PLAN / "prospects.py", "a carried row never ages out (the roll limit on first_proposed is removed)",
     "        if business_days_between(first, today) > limit:\n            continue\n", "",
     "test_carry_limit_is_weekdays_since_first_proposed"),
]


class RunnerError(Exception):
    """The mutation itself is broken (it does not compile or parse). Never reported as CAUGHT."""


def run_suite():
    """(returncode, failure lines, timed_out). A timeout counts as CAUGHT: an infinite loop is a broken guarantee,
    and the test that hangs is the one noticing it."""
    try:
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                           capture_output=True, text=True, cwd=ROOT, timeout=SUITE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return 1, [f"TIMEOUT: suite did not finish in {SUITE_TIMEOUT} s"], True
    fails = [l for l in r.stderr.splitlines() if l.startswith(("FAIL:", "ERROR:"))]
    return r.returncode, fails, False


def check_syntax(path, data):
    """Raise RunnerError when the mutated bytes are not valid Python (.py) or TOML (.toml). SQL is not checked here:
    a bad statement fails the schema tests, which is a different failure from a guarantee being broken, so the
    SQL mutations are kept to edits of whole clauses and triggers."""
    try:
        if path.suffix == ".py":
            compile(data, str(path), "exec")
        elif path.suffix == ".toml":
            tomllib.loads(data.decode("utf-8"))
    except (SyntaxError, ValueError, tomllib.TOMLDecodeError) as e:
        raise RunnerError(f"mutation does not {'compile' if path.suffix == '.py' else 'parse'}: {e}") from e


def dirty_files():
    """The mutable files git reports as modified or untracked (empty when the tree is clean for them)."""
    paths = sorted({str(p.relative_to(ROOT)) for p, *_ in MUTATIONS})
    r = subprocess.run(["git", "status", "--porcelain", "--", *paths], capture_output=True, text=True, cwd=ROOT)
    if r.returncode != 0:
        raise RunnerError(f"git status failed: {r.stderr.strip()}")
    return [line[3:] for line in r.stdout.splitlines()]


def _stop(signum, frame):
    raise SystemExit(128 + signum)  # unwinds through the `finally` that restores the file


def parse_shard(text):
    """'I/N' as (I, N) with 0 <= I < N. Anything else is refused, so a typo cannot quietly run nothing."""
    left, slash, right = text.partition("/")
    if not (slash and left.isascii() and left.isdigit() and right.isascii() and right.isdigit()):
        raise argparse.ArgumentTypeError(f"expected I/N with whole numbers, like 2/6, got {text!r}")
    i, n = int(left), int(right)
    if n < 1 or i >= n:
        raise argparse.ArgumentTypeError(f"shard {text!r} is out of range: I must be below N and N at least 1")
    return i, n


def select_shard(items, shard):
    """The items whose zero-based position % N == I; all of them when there is no shard."""
    if shard is None:
        return list(items)
    i, n = shard
    return [item for position, item in enumerate(items) if position % n == i]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="print every mutation and exit (a shard does not narrow it)")
    ap.add_argument("--shard", type=parse_shard, metavar="I/N",
                    help="run only the mutations whose zero-based index %% N == I; the numbers in the output stay "
                         "the full-run numbers. Each shard fails on its own missed or skipped entries.")
    args = ap.parse_args(argv)

    if args.list:
        for n, (path, name, _, _, expect) in enumerate(MUTATIONS, start=1):
            print(f"  {n:2} {path.name:22} {name}  [{expect}]")
        return 0

    if live_checkout.is_live():
        print("refusing to run in the live checkout: this edits source files in place.")
        return 2

    dirty = dirty_files()
    if dirty:
        print("refusing to run: these files are modified, and a mutated leftover would be read as the original.")
        print("If a killed run left them mutated, restore each one:")
        for f in dirty:
            print(f"    git checkout -- {f}")
        print("If the changes are yours, commit or stash them first.")
        return 2

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGHUP, _stop)

    rc, fails, _ = run_suite()
    if rc != 0:
        print("baseline suite is RED; fix that before mutation testing:")
        for f in fails[:10]:
            print("   ", f)
        print("(mutable files are clean in git, so the failure is not a leftover mutation)")
        return 2
    chosen = select_shard(list(enumerate(MUTATIONS, start=1)), args.shard)
    which = f" in shard {args.shard[0]}/{args.shard[1]}, of {len(MUTATIONS)}" if args.shard else ""
    print(f"baseline green ({len(chosen)} mutations to apply{which})\n", flush=True)

    caught = missed = skipped = 0
    for n, (path, name, old, new, expect) in chosen:
        original = path.read_bytes()
        label = f"{n:2} {path.name}: {name}"
        old_b, new_b = old.encode("utf-8"), new.encode("utf-8")
        if old_b not in original:
            skipped += 1
            print(f"  SKIP    {label}\n            (anchor not found in {path.name}; "
                  f"the code moved, so this mutation needs updating)", flush=True)
            continue
        mutated = original.replace(old_b, new_b, 1)
        try:
            check_syntax(path, mutated)
            path.write_bytes(mutated)
            rc, fails, timed_out = run_suite()
        except RunnerError as e:
            print(f"\nRUNNER ERROR in mutation {label}\n            {e}")
            return 4
        finally:
            path.write_bytes(original)
            restored = path.read_bytes() == original
            if not restored:  # never leave the tree mutated
                print(f"\nFATAL: could not restore {path}. Run: git checkout -- {path.relative_to(ROOT)}")
                sys.stdout.flush()
                raise SystemExit(3)
        if rc != 0:
            caught += 1
            print(f"  CAUGHT  {label}")
            if timed_out:
                print(f"            {fails[0]}")
            elif fails:
                print(f"            {fails[0].split(' (')[0]}")
            if not any(expect in f for f in fails):
                print(f"            note: no failure names {expect!r}; caught by other tests only")
        else:
            missed += 1
            print(f"  MISSED  {label}")
            print("            nothing failed. The guarantee is unprotected.")
        sys.stdout.flush()

    print(f"\ncaught {caught}  missed {missed}  skipped {skipped}")
    if missed:
        print("A missed mutation means a guarantee has no test. Add one.")
    return 1 if (missed or skipped) else 0


if __name__ == "__main__":
    sys.exit(main())
