#!/usr/bin/env python3
"""Mutation check: break each rules and store guarantee on purpose, confirm a test notices.

    uv run python tests/mutation_check.py            # from the repo root
    uv run python tests/mutation_check.py --list

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
NET = ROOT / "outreach" / "net.py"
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="print mutations and exit")
    args = ap.parse_args()

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
    print(f"baseline green ({len(MUTATIONS)} mutations to apply)\n", flush=True)

    caught = missed = skipped = 0
    for n, (path, name, old, new, expect) in enumerate(MUTATIONS, start=1):
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
