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
suite must be green first. Each file is restored in a `finally` block and the restore is verified by re-reading
the file; if a restore ever fails the run aborts at once and says which file to check. A missing anchor is SKIP and
fails the run, so a refactor that moves the code cannot make a mutation pass silently.
"""
import argparse
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import live_checkout  # noqa: E402

RULES = ROOT / "outreach" / "rules"
STORE = ROOT / "outreach" / "store"
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

    # ---- rules/sponsorship.py and its topic words -------------------------------------------------------------
    (RULES / "sponsorship.py", "the negation check removed",
     "    if m := p.negation.search(clause):\n        return NO, m\n", "", "Misfire7"),
    (RULES / "config.toml", "'immigration' added to the topic words",
     '"sponsorship", "visa", "visas", "h-1b", "h1b",\n]', '"sponsorship", "visa", "visas", "h-1b", "h1b", "immigration",\n]',
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
     "           ORDER BY e.locked DESC,\n                    CASE WHEN e.locked = 1 THEN e.recorded_at END DESC,\n",
     "           ORDER BY\n", "test_automation_never_outranks_a_hand_value"),
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
]


def run_suite():
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                       capture_output=True, text=True, cwd=ROOT)
    fails = [l for l in r.stderr.splitlines() if l.startswith(("FAIL:", "ERROR:"))]
    return r.returncode, fails


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

    rc, fails = run_suite()
    if rc != 0:
        print("baseline suite is RED; fix that before mutation testing:")
        for f in fails[:10]:
            print("   ", f)
        return 2
    print(f"baseline green ({len(MUTATIONS)} mutations to apply)\n", flush=True)

    caught = missed = skipped = 0
    for n, (path, name, old, new, expect) in enumerate(MUTATIONS, start=1):
        original = path.read_text(encoding="utf-8")
        label = f"{n:2} {path.name}: {name}"
        if old not in original:
            skipped += 1
            print(f"  SKIP    {label}\n            (anchor not found in {path.name}; "
                  f"the code moved, so this mutation needs updating)", flush=True)
            continue
        try:
            path.write_text(original.replace(old, new, 1), encoding="utf-8")
            rc, fails = run_suite()
        finally:
            path.write_text(original, encoding="utf-8")
            restored = path.read_text(encoding="utf-8") == original
        if not restored:  # never leave the tree mutated
            print(f"\nFATAL: could not restore {path}. Check it by hand.")
            return 3
        if rc != 0:
            caught += 1
            print(f"  CAUGHT  {label}")
            if fails:
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
