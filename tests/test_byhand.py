"""Adding a company by hand with a reason (#31 Task 13)."""
import hashlib
import json
import sqlite3
import unittest
from unittest import mock

from outreach.rules.facts import EvidenceDraft
from outreach.rules.rebuild import record_evidence
from outreach.sources.byhand import ByHandSource, HandAdd, HandEntry, add_by_hand
from outreach.sources.tiers import effective_tier
from outreach.store import add_alias, mark_shared_key, name_key, transaction, utcnow
from tests.contracts import check_source
from tests.storetest import fresh_store, seed_company, table_marks


class AddByHandTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()

    def add(self, name="Acme", reason="a friend works there", **kw):
        return add_by_hand(self.conn, name=name, reason=reason, **kw)

    def evidence(self, company_id):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM evidence WHERE company_id = ? ORDER BY evidence_id", (company_id,))]

    def hand_events(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM event WHERE kind = 'added_by_hand' ORDER BY event_id")]

    def test_add_creates_company_with_locked_domain_and_event(self):
        got = self.add("Acme Robotics", "Dana at Acme said they hire new grads", domain="acme.com", state="MA")
        self.assertEqual(got, HandAdd(company_id=got.company_id, created=True, basis="created", items_raised=0,
                                      status="candidate"))
        company = self.conn.execute("SELECT * FROM company WHERE company_id = ?", (got.company_id,)).fetchone()
        self.assertEqual((company["name"], company["status"]), ("Acme Robotics", "candidate"))
        by_fact = {e["fact"]: e for e in self.evidence(got.company_id)}
        self.assertEqual(set(by_fact), {"domain", "hq"})
        for row in by_fact.values():
            self.assertEqual((row["locked"], row["source_kind"], row["confidence"]), (1, "hand", "confirmed"))
        self.assertEqual(by_fact["domain"]["value"], "acme.com")
        self.assertEqual(by_fact["domain"]["quote"], "Dana at Acme said they hire new grads")
        self.assertEqual(by_fact["hq"]["value"], "MA")
        (event,) = self.hand_events()
        reason = "Dana at Acme said they hire new grads"
        self.assertEqual((event["entity"], event["entity_id"], event["company_id"], event["actor"]),
                         ("company", got.company_id, got.company_id, "devanshu"))
        self.assertEqual(json.loads(event["detail"]), {"reason": reason})
        self.assertEqual(event["idempotency_key"],
                         f"hand:{name_key('Acme Robotics')}@acme.com:{hashlib.sha256(reason.encode()).hexdigest()[:12]}")
        source = self.conn.execute("SELECT * FROM source WHERE source_name = 'hand'").fetchone()
        self.assertEqual(source["version"], "hand-1")
        link = self.conn.execute("SELECT * FROM company_source WHERE company_id = ?", (got.company_id,)).fetchone()
        self.assertEqual((link["source_name"], link["source_ref"]), ("hand", "hand:acmerobotics@acme.com"))

    def test_blank_reason_raises(self):
        for reason in ("", "   ", "\n\t"):
            with self.subTest(reason=reason):
                before = table_marks(self.conn)
                with self.assertRaises(ValueError):
                    self.add(reason=reason)
                self.assertEqual(table_marks(self.conn), before)

    def test_bad_input_raises_before_anything_is_written(self):
        bad = [dict(name="  "), dict(domain="https://acme.com"), dict(domain="Acme.com"), dict(domain="acme"),
               dict(state="XX"), dict(state="Massachusetts"), dict(state=""), dict(ats="acme"),
               dict(ats="nosuchats:acme"), dict(ats="greenhouse:"), dict(ats="workday:acme/x")]
        for kw in bad:
            with self.subTest(**kw):
                before = table_marks(self.conn)
                with self.assertRaises(ValueError):
                    self.add(**kw)
                self.assertEqual(table_marks(self.conn), before)
                self.assertEqual(self.conn.execute("SELECT count(*) FROM source").fetchone()[0], 0)

    def test_state_may_be_a_territory_or_dc_in_any_case(self):
        got = self.add("Isla Works", state="pr")
        self.assertEqual([e["value"] for e in self.evidence(got.company_id)], ["PR"])
        got = self.add("Capital Works", state=" dc ")
        self.assertEqual([e["value"] for e in self.evidence(got.company_id)], ["DC"])

    def test_an_unknown_actor_is_refused(self):
        before = table_marks(self.conn)
        with self.assertRaises(ValueError):
            self.add(actor="sender")
        self.assertEqual(table_marks(self.conn), before)

    def test_claude_add_is_a_guess_with_no_locked_fact_and_no_promotion(self):
        got = self.add("Acme Robotics", "found on a funding post", domain="acme.com", state="MA", actor="claude")
        self.assertTrue(got.created)
        rows = {e["fact"]: e for e in self.evidence(got.company_id)}
        self.assertEqual(set(rows), {"domain", "hq"})
        for row in rows.values():
            self.assertEqual((row["locked"], row["confidence"], row["source_kind"]), (0, "guess", "other"))
        self.assertEqual((rows["domain"]["value"], rows["hq"]["value"]), ("acme.com", "MA"))
        self.assertEqual(rows["domain"]["quote"], "found on a funding post")
        (event,) = self.hand_events()
        self.assertEqual((event["actor"], json.loads(event["detail"])), ("claude", {"reason": "found on a funding post"}))
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM work_tier WHERE company_id = ?", (got.company_id,)).fetchone())
        self.assertEqual(self.conn.execute("SELECT count(*) FROM event WHERE kind = 'promoted'").fetchone()[0], 0)

    def test_claude_add_twice_writes_nothing_new(self):
        self.add("Acme Robotics", "found on a post", domain="acme.com", state="MA", actor="claude")
        before = table_marks(self.conn)
        self.add("Acme Robotics", "found on a post", domain="acme.com", state="MA", actor="claude")
        self.assertEqual(table_marks(self.conn), before)

    def test_claude_state_never_drives_a_name_state_match(self):
        first = self.add("Acme Corp", domain="acme-tx.com", state="TX")
        got = self.add("Acme", "claude saw it in Texas", state="TX", actor="claude")
        self.assertNotEqual(got.company_id, first.company_id)
        self.assertEqual((got.created, got.basis, got.items_raised), (True, "created", 1))
        (item,) = self.conn.execute("SELECT * FROM review_item").fetchall()
        self.assertEqual(item["kind"], "merge_candidate")
        # the state is still stored (as a guess) and still part of the record key
        rows = {(e["fact"], e["value"], e["confidence"]) for e in self.evidence(got.company_id)}
        self.assertEqual(rows, {("hq", "TX", "guess")})
        ref = self.conn.execute("SELECT source_ref FROM company_source WHERE company_id = ?",
                                (got.company_id,)).fetchone()[0]
        self.assertEqual(ref, "hand:acme@TX")

    def test_claude_domain_never_drives_a_match_or_an_alias(self):
        acme = self.add("Acme Robotics", domain="acme.com")
        got = self.add("Foo Labs", "claude guessed the domain", domain="acme.com", actor="claude")
        self.assertNotEqual(got.company_id, acme.company_id)
        self.assertEqual((got.created, got.basis), (True, "created"))
        self.assertEqual(got.items_raised, 0)  # nothing else matches by name, and a guessed domain raises no item
        self.assertEqual(self.conn.execute("SELECT count(*) FROM review_item").fetchone()[0], 0)
        # a same-name Claude add still raises its one item through the name
        twin = self.add("Acme Robotics", "claude saw it", domain="foo.example", actor="claude")
        self.assertEqual((twin.created, twin.items_raised), (True, 1))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM company_alias WHERE company_id = ? "
                                           "AND alias_key = 'foo.example'", (acme.company_id,)).fetchone()[0], 0)
        # the guessed domain is stored on Foo Labs as a guess, and Acme gains nothing
        rows = {(e["fact"], e["value"], e["confidence"], e["locked"]) for e in self.evidence(got.company_id)}
        self.assertEqual(rows, {("domain", "acme.com", "guess", 0)})
        aliases = [r[0] for r in self.conn.execute("SELECT alias_key FROM company_alias WHERE company_id = ?",
                                                   (acme.company_id,))]
        self.assertEqual(aliases, [name_key("Acme Robotics")])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM company").fetchone()[0], 3)

    def test_claude_guess_never_overrides_a_locked_fact(self):
        first = self.add("Acme Robotics", domain="acme.com", state="MA")
        self.add("Acme Robotics", "claude saw it too", domain="acme.com", state="CA", actor="claude")
        hq = self.conn.execute("SELECT value, locked FROM company_fact WHERE company_id = ? AND fact = 'hq'",
                               (first.company_id,)).fetchone()
        self.assertEqual((hq["value"], hq["locked"]), ("MA", 1))

    def test_existing_domain_returns_the_existing_company(self):
        first = self.add("Acme Robotics", domain="acme.com")
        got = self.add("Acme Labs", "second reason", domain="acme.com")
        self.assertEqual((got.company_id, got.created, got.basis), (first.company_id, False, "domain"))
        self.assertEqual(got.items_raised, 0)
        # the domain is already a locked fact: no second row, and the new reason is in the event
        self.assertEqual([e["fact"] for e in self.evidence(first.company_id)], ["domain"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM company").fetchone()[0], 1)
        self.assertEqual([json.loads(e["detail"])["reason"] for e in self.hand_events()],
                         ["a friend works there", "second reason"])

    def test_same_name_again_is_the_same_hand_record(self):
        # "Acme Inc" and "Acme" share a name key, so they share the hand source_ref: one company.
        first = self.add("Acme")
        got = self.add("Acme, Inc.", "added again from a post")
        self.assertEqual((got.company_id, got.created, got.basis), (first.company_id, False, "source_ref"))

    def test_ref_is_domain_then_state_then_bare_name(self):
        refs = [c.source_ref for c in ByHandSource([
            HandEntry("Acme", "r", domain="acme.com", state="TX"), HandEntry("Acme", "r", state="TX"),
            HandEntry("Acme", "r")]).discover()]
        self.assertEqual(refs, ["hand:acme@acme.com", "hand:acme@TX", "hand:acme"])

    def test_a_name_with_a_non_ascii_letter_uses_a_hashed_ref(self):
        refs = [c.source_ref for c in ByHandSource([
            HandEntry("Caf\u00e9 Labs", "r"), HandEntry("Caf Labs", "r")]).discover()]
        self.assertTrue(refs[0].startswith("hand:sha256:"), refs[0])
        self.assertNotEqual(refs[0], refs[1])

    def test_same_name_in_two_states_is_two_companies_and_one_item(self):
        tx = self.add("Acme", domain="acme-tx.com", state="TX")
        ca = self.add("Acme", domain="acme-ca.com", state="CA")
        self.assertNotEqual(tx.company_id, ca.company_id)
        self.assertEqual((ca.created, ca.basis, ca.items_raised), (True, "created", 1))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM review_item").fetchone()[0], 1)
        rows = {(e["fact"], e["value"]) for e in self.evidence(tx.company_id)}
        self.assertEqual(rows, {("domain", "acme-tx.com"), ("hq", "TX")})
        # both events survive: the idempotency key names the record, not just the name
        self.assertEqual(len(self.hand_events()), 2)

    def test_same_name_in_the_same_state_is_a_name_state_match(self):
        first = self.add("Acme Corp", domain="acme-tx.com", state="TX")
        got = self.add("Acme", "heard of it again", state="TX")
        self.assertEqual((got.company_id, got.created, got.basis), (first.company_id, False, "name_state"))

    def test_a_hand_domain_owned_by_another_company_attaches_by_domain(self):
        other = seed_company(self.conn, "Zenith")
        record_evidence(self.conn, other, [EvidenceDraft("domain", "zenith.io", "confirmed", "website")])
        got = self.add("Zenith Systems", domain="zenith.io", state="OR")
        self.assertEqual((got.company_id, got.created, got.basis), (other, False, "domain"))

    def test_the_winning_locked_value_decides_whether_a_repeat_is_written(self):
        # TX, then CA, then TX again: the last add must end on TX, not be skipped because TX was once locked.
        first = self.add("Acme", "first", domain="acme.com", state="TX")
        self.add("Acme", "second", domain="acme.com", state="CA")
        self.add("Acme", "third", domain="acme.com", state="TX")
        hq = self.conn.execute("SELECT value FROM company_fact WHERE company_id = ? AND fact = 'hq'",
                               (first.company_id,)).fetchone()
        self.assertEqual(hq["value"], "TX")
        self.assertEqual(sum(1 for e in self.evidence(first.company_id) if e["fact"] == "hq"), 3)
        # and the same winning value again adds no row
        self.add("Acme", "fourth", domain="acme.com", state="TX")
        self.assertEqual(sum(1 for e in self.evidence(first.company_id) if e["fact"] == "hq"), 3)

    def test_a_removed_company_is_returned_with_nothing_written(self):
        first = self.add("Acme Robotics", domain="acme.com")
        now = utcnow()
        self.conn.execute("UPDATE company SET status = 'removed', remove_reason = 'not a fit', status_at = ? "
                          "WHERE company_id = ?", (now, first.company_id))
        before = table_marks(self.conn)
        got = self.add("Acme Labs", "a new reason", domain="acme.com", state="MA")
        self.assertEqual((got.company_id, got.created, got.basis, got.status, got.items_raised),
                         (first.company_id, False, "domain", "removed", 0))
        self.assertEqual(table_marks(self.conn), before)
        # Claude's guessed domain is not an identity key, so it cannot even find the removed company
        got = self.add("Acme Labs", "a new reason", domain="acme.com", state="MA", actor="claude")
        self.assertNotEqual(got.company_id, first.company_id)

    def test_weak_name_creates_company_and_item(self):
        # Identity refuses hand drafts, so a by-hand candidate has no state at resolve time and a name already known
        # (here from another source) is only a weak match: a new company and one merge_candidate item, never a merge.
        other = seed_company(self.conn, "Orbit Labs")
        add_alias(self.conn, other, "Orbit Labs", "name")
        got = self.add("Orbit Labs", state="CA")
        self.assertEqual((got.created, got.basis, got.items_raised), (True, "created", 1))
        self.assertNotEqual(got.company_id, other)
        (item,) = self.conn.execute("SELECT * FROM review_item").fetchall()
        self.assertEqual(item["kind"], "merge_candidate")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM company").fetchone()[0], 2)

    def test_hand_company_gets_effective_tier_1(self):
        got = self.add()
        self.assertEqual(effective_tier(self.conn, got.company_id), 1)
        row = self.conn.execute("SELECT * FROM work_tier WHERE company_id = ?", (got.company_id,)).fetchone()
        self.assertEqual((row["tier"], row["promoted"], row["reason"], row["source_version"]),
                         (1, 1, "by hand", "hand-1"))

    def test_a_staffing_tier_3_company_added_by_hand_is_worked_as_tier_1(self):
        from outreach.sources.tiers import set_work_tier
        first = self.add("Acme Robotics", domain="acme.com")
        set_work_tier(self.conn, first.company_id, 3, "excluded: staffing", "lca-1-x")
        self.assertEqual(effective_tier(self.conn, first.company_id), 1)  # promoted stays
        self.add("Acme Labs", "second reason", domain="acme.com")
        self.assertEqual(effective_tier(self.conn, first.company_id), 1)

    def test_adding_twice_writes_nothing_new(self):
        self.add("Acme Robotics", domain="acme.com", state="MA", ats="greenhouse:acme")
        before = table_marks(self.conn)
        got = self.add("Acme Robotics", domain="acme.com", state="MA", ats="greenhouse:acme")
        self.assertEqual((got.created, got.basis, got.items_raised), (False, "source_ref", 0))
        self.assertEqual(table_marks(self.conn), before)

    def test_shared_domain_hand_add_never_merges(self):
        mark_shared_key(self.conn, "domain", "shared.example", "2 employers: Alpha, Beta")
        first = self.add("Alpha Works", domain="shared.example")
        second = self.add("Beta Works", domain="shared.example")
        self.assertNotEqual(first.company_id, second.company_id)
        self.assertEqual((first.created, second.created), (True, True))
        self.assertEqual(second.basis, "created")

    def test_ats_key_is_an_alias_not_yet_a_board(self):
        got = self.add("Acme Robotics", ats="Greenhouse:ACME")
        alias = self.conn.execute("SELECT * FROM company_alias WHERE source = 'ats'").fetchone()
        self.assertEqual((alias["company_id"], alias["alias_key"]), (got.company_id, "greenhouse:acme"))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM board").fetchone()[0], 0)

    def test_bad_ats_token_raises_before_anything_is_written(self):
        before = table_marks(self.conn)
        with self.assertRaises(ValueError):
            self.add(ats="workday:acme/x")
        self.assertEqual(table_marks(self.conn), before)

    def test_a_failure_after_resolve_writes_nothing(self):
        before = table_marks(self.conn)
        with mock.patch("outreach.sources.byhand.append_event", side_effect=sqlite3.OperationalError("disk")):
            with self.assertRaises(sqlite3.OperationalError):
                self.add(domain="acme.com", state="MA", ats="greenhouse:acme")
        self.assertEqual(table_marks(self.conn), before)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM source").fetchone()[0], 0)
        self.assertFalse(self.conn.in_transaction)

    def test_a_failure_inside_a_callers_transaction_rolls_back_only_this_add(self):
        with transaction(self.conn):
            kept = seed_company(self.conn, "Kept Co")
            with mock.patch("outreach.sources.byhand.append_event", side_effect=sqlite3.OperationalError("disk")):
                with self.assertRaises(sqlite3.OperationalError):
                    self.add(domain="acme.com")
            self.assertTrue(self.conn.in_transaction)
            self.assertEqual(self.conn.execute("SELECT count(*) FROM company").fetchone()[0], 1)
            # the savepoint is released: the same call works afterwards, in the same transaction
            got = self.add(domain="acme.com")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM company").fetchone()[0], 2)
        self.assertNotEqual(got.company_id, kept)

    def test_by_hand_source_passes_check_source(self):
        source = ByHandSource([HandEntry("Acme Robotics", "met the founder", domain="acme.com", state="MA",
                                         ats="greenhouse:acme"),
                               HandEntry("Orbit Labs", "saw it on a post")])
        self.assertEqual((source.name, source.schedule, source.version), ("hand", "on_demand", "hand-1"))
        got = check_source(self, source)
        self.assertEqual([c.name for c in got], ["Acme Robotics", "Orbit Labs"])
        self.assertEqual([c.source_ref for c in got], ["hand:acmerobotics@acme.com", "hand:orbitlabs"])
        self.assertEqual([c.state for c in got], ["MA", None])
        self.assertEqual(got[0].keys, {"ats": "greenhouse:acme"})
        self.assertEqual(got[0].domain, "acme.com")
        self.assertEqual(got[0].evidence, ())  # hand evidence is written by add_by_hand, never by identity


class HandEntryTest(unittest.TestCase):
    def test_a_bad_ats_raises_when_the_entry_is_built(self):
        with self.assertRaises(ValueError):
            HandEntry("Acme", "why", ats="workday:acme/x")
        with self.assertRaises(ValueError):
            HandEntry("Acme", "why", ats="notanats:acme")

    def test_a_blank_reason_or_bad_state_raises_when_the_entry_is_built(self):
        with self.assertRaises(ValueError):
            HandEntry("Acme", " ")
        with self.assertRaises(ValueError):
            HandEntry("Acme", "why", state="ZZ")


if __name__ == "__main__":
    unittest.main()
