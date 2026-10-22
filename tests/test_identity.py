"""Identity (#31 Task 7, design 5.2): one company per key, shared keys never merge, weaker matches raise items.

Candidates are built by hand; every store comes from fresh_store() and every source name is registered first.
Idempotency is checked with table_marks snapshots, so a re-run that adds any row fails.
"""
import json
import sqlite3
import unittest
from unittest import mock

from outreach import identity
from outreach.identity import (Match, Resolution, company_state, find_company, follow_merged, raise_merge_candidate,
                               resolve_candidate)
from outreach.rules.facts import EvidenceDraft
from outreach.rules.rebuild import record_evidence
from outreach.sources.base import Candidate
from outreach.store import mark_shared_key, name_key, transaction
from tests.storetest import fresh_store, table_marks


def hq(value, confidence="estimate", kind="lca", ref="r"):
    return EvidenceDraft("hq", value, confidence, kind, ref)


def dom(value, confidence="estimate", kind="lca", ref="r"):
    return EvidenceDraft("domain", value, confidence, kind, ref)


def cand(name, *, fein=None, domain=None, state=None, city="Austin", ref=None, legal_name=None, dba=None, ats=None,
         linkedin=None, domain_confidence="estimate", hq_confidence="estimate", kind="lca", evidence=()):
    keys = {}
    for key, value in (("fein", fein), ("legal_name", legal_name), ("dba", dba), ("ats", ats),
                       ("linkedin", linkedin)):
        if value is not None:
            keys[key] = value
    ev = list(evidence)
    if state:
        ev.append(hq(f"{city}, {state}" if city else state, hq_confidence, kind))
    if domain:
        ev.append(dom(domain, domain_confidence, kind))
    return Candidate(name=name, domain=domain, keys=keys, evidence=tuple(ev),
                     source_ref=ref if ref is not None else f"t:{name_key(name) or name}")


class Base(unittest.TestCase):
    sources = ("lca", "hn", "formd", "hand")

    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        for s in self.sources:
            self.conn.execute("INSERT INTO source(source_name, version) VALUES (?, 'v1')", (s,))

    def resolve(self, c, source="lca", **kw):
        return resolve_candidate(self.conn, c, source_name=source, **kw)

    def companies(self):
        return [tuple(r) for r in self.conn.execute("SELECT company_id, name, status FROM company ORDER BY 1")]

    def aliases(self):
        return {tuple(r) for r in self.conn.execute("SELECT alias_key, company_id, source FROM company_alias")}

    def items(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT kind, company_id, evidence_key, raised_by, state, reason FROM review_item ORDER BY review_id")]

    def count(self, sql, *args):
        return self.conn.execute(sql, args).fetchone()[0]

    def assert_one_item(self, low, high, *pieces):
        items = self.items()
        self.assertEqual(len(items), 1, items)
        item = items[0]
        self.assertEqual((item["kind"], item["company_id"], item["evidence_key"], item["raised_by"], item["state"]),
                         ("merge_candidate", low, str(high), "source", "open"))
        for piece in pieces:
            self.assertIn(piece, item["reason"])
        return item


class NewAndRerunTest(Base):
    def acme(self):
        return Candidate(name="Acme, Inc.", domain="acme.com", keys={"fein": "123456789", "legal_name": "Acme, Inc."},
                         evidence=(hq("Austin, TX"), dom("acme.com")), source_ref="t:acme")

    def test_new_candidate_creates_company_aliases_evidence_source(self):
        res = self.resolve(self.acme(), actor="source")
        self.assertEqual(res, Resolution(company_id=1, created=True, basis="created", confidence="new",
                                         aliases_added=2, evidence_added=2, items_raised=0))
        self.assertEqual(self.companies(), [(1, "Acme, Inc.", "candidate")])
        self.assertEqual(self.aliases(), {("acme", 1, "name"), ("123456789", 1, "fein")})
        self.assertEqual([tuple(r) for r in self.conn.execute(
            "SELECT company_id, source_name, source_ref FROM company_source")], [(1, "lca", "t:acme")])
        self.assertEqual(sorted(tuple(r) for r in self.conn.execute(
            "SELECT fact, value, source_kind FROM evidence WHERE company_id = 1")),
            [("domain", "acme.com", "lca"), ("hq", "Austin, TX", "lca")])
        events = [tuple(r) for r in self.conn.execute(
            "SELECT entity, kind, actor, company_id FROM event ORDER BY event_id")]
        self.assertEqual(events, [("company", "created", "source", 1), ("evidence", "recorded", "source", 1)])
        detail = json.loads(self.conn.execute("SELECT detail FROM event WHERE kind = 'created'").fetchone()[0])
        self.assertEqual((detail["basis"], detail["confidence"], detail["source"], detail["source_ref"]),
                         ("created", "new", "lca", "t:acme"))
        self.assertEqual(sorted(detail["aliases"]), [["fein", "123456789"], ["name", "Acme, Inc."]])

    def test_rerun_writes_nothing_new(self):
        self.resolve(self.acme())
        before = table_marks(self.conn)
        res = self.resolve(self.acme())
        self.assertEqual(res, Resolution(company_id=1, created=False, basis="source_ref", confidence="certain",
                                         aliases_added=0, evidence_added=0, items_raised=0))
        self.assertEqual(table_marks(self.conn), before)

    def test_default_actor_is_system_and_evidence_is_source(self):
        self.resolve(self.acme())
        self.assertEqual([tuple(r) for r in self.conn.execute("SELECT kind, actor FROM event ORDER BY event_id")],
                         [("created", "system"), ("recorded", "source")])


class StrongMatchTest(Base):
    def test_same_domain_attaches(self):
        a = self.resolve(cand("Acme", domain="acme.com", state="TX"))
        b = self.resolve(cand("Acme Labs", domain="acme.com", kind="hn", ref="hn:acmelabs"), source="hn")
        self.assertEqual((b.company_id, b.created, b.basis, b.confidence, b.items_raised),
                         (a.company_id, False, "domain", "strong", 0))
        self.assertEqual(self.count("SELECT COUNT(*) FROM company"), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM company_source WHERE company_id = ?", a.company_id), 2)
        self.assertEqual(self.aliases(), {("acme", 1, "name"), ("acmelabs", 1, "name")})
        detail = json.loads(self.conn.execute("SELECT detail FROM event WHERE kind = 'matched'").fetchone()[0])
        self.assertEqual((detail["basis"], detail["confidence"], detail["key"], detail["source"]),
                         ("domain", "strong", "acme.com", "hn"))

    def test_same_fein_attaches(self):
        a = self.resolve(cand("Nimbus", fein="11-1111111", state="CA"))
        b = self.resolve(cand("Nimbus Holdings", fein="111111111", ref="formd:1"), source="formd", creates=False)
        self.assertEqual((b.company_id, b.created, b.basis, b.confidence, b.items_raised),
                         (a.company_id, False, "fein", "certain", 0))
        self.assertEqual(self.count("SELECT COUNT(*) FROM company"), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM company_alias WHERE source = 'fein'"), 1)

    def test_name_and_state_attach(self):
        a = self.resolve(cand("Acme, Inc.", state="TX", city="Austin"))
        b = self.resolve(cand("ACME Inc", state="TX", city="Dallas", ref="t:acme-dallas"))
        self.assertEqual((b.company_id, b.created, b.basis, b.confidence, b.items_raised),
                         (a.company_id, False, "name_state", "likely", 0))
        self.assertEqual(self.count("SELECT COUNT(*) FROM company"), 1)

    def test_conflicting_strong_keys_attach_first_and_raise_item(self):
        alpha = self.resolve(cand("Alpha", fein="111111111"))
        beta = self.resolve(cand("Beta", domain="beta.com"))
        res = self.resolve(cand("Gamma", fein="111111111", domain="beta.com"))
        self.assertEqual((res.company_id, res.basis, res.confidence, res.items_raised),
                         (alpha.company_id, "fein", "certain", 1))
        self.assert_one_item(alpha.company_id, beta.company_id, "Alpha (#1)", "Beta (#2)", "Basis fein+domain",
                             "confidence strong", "beta.com", "111111111")
        before = table_marks(self.conn)
        self.resolve(cand("Gamma", fein="111111111", domain="beta.com"))
        self.assertEqual(table_marks(self.conn), before)

    def test_merged_company_is_followed(self):
        a = self.resolve(cand("Acme", fein="111111111"))
        b = self.resolve(cand("Acme Two"))
        c = self.resolve(cand("Acme Three"))
        for old, new in ((a.company_id, b.company_id), (c.company_id, a.company_id)):
            self.conn.execute("UPDATE company SET status = 'removed', remove_reason = 'merged', merged_into = ? "
                              "WHERE company_id = ?", (new, old))
        self.assertEqual([follow_merged(self.conn, i) for i in (1, 2, 3)], [2, 2, 2])
        by_fein = self.resolve(cand("Acme Holdings", fein="111111111", ref="hn:x"), source="hn")
        self.assertEqual((by_fein.company_id, by_fein.basis, by_fein.items_raised), (2, "fein", 0))
        self.assertEqual(self.count("SELECT company_id FROM company_alias WHERE alias_key = '111111111'"), 1)
        by_ref = self.resolve(cand("Acme Three"))
        self.assertEqual((by_ref.company_id, by_ref.basis, by_ref.items_raised), (2, "source_ref", 0))
        self.assertEqual(self.count("SELECT COUNT(*) FROM review_item"), 0)

    def test_merge_loop_is_an_error_not_an_answer(self):
        self.resolve(cand("Acme"))
        self.resolve(cand("Acme Two"))
        self.conn.execute("UPDATE company SET status = 'removed', remove_reason = 'm', merged_into = 2 "
                          "WHERE company_id = 1")
        self.conn.execute("UPDATE company SET status = 'removed', remove_reason = 'm', merged_into = 1 "
                          "WHERE company_id = 2")
        with self.assertRaises(ValueError):
            follow_merged(self.conn, 1)


class SharedKeyTest(Base):
    def test_shared_fein_never_merges(self):
        mark_shared_key(self.conn, "fein", "333333333", "3 employers")
        a = self.resolve(cand("Clearpath", fein="333333333"))
        b = self.resolve(cand("Greenfield AI", fein="333333333"))
        self.assertNotEqual(a.company_id, b.company_id)
        self.assertEqual((a.basis, b.basis), ("created", "created"))
        self.assertEqual(self.count("SELECT COUNT(*) FROM company_alias WHERE source='fein'"), 0)
        self.assertEqual(self.count("SELECT COUNT(*) FROM review_item"), 0)

    def test_shared_domain_never_merges(self):
        mark_shared_key(self.conn, "domain", "veltrix.com", "2 employers")
        a = self.resolve(cand("Veltrix Pharma", domain="veltrix.com", state="NJ"))
        b = self.resolve(cand("Veltrix Diagnostics", domain="veltrix.com", state="NJ"))
        c = self.resolve(cand("Veltrix Careers", domain="careers.veltrix.com", ref="hn:vc"), source="hn")
        self.assertEqual(len({a.company_id, b.company_id, c.company_id}), 3)
        self.assertEqual((b.basis, c.basis), ("created", "created"))
        self.assertEqual(self.count("SELECT COUNT(*) FROM review_item"), 0)
        self.assertEqual(find_company(self.conn, cand("Other", domain="veltrix.com", ref="x"), source_name="lca"),
                         Match(strong=(), weak=()))
        # Same exact name and state still merges: name_state does not use the shared key.
        s1 = self.resolve(cand("Seqirus", domain="veltrix.com", state="NJ"))
        s2 = self.resolve(cand("Seqirus Inc", domain="veltrix.com", state="NJ", ref="t:seqirus-2"))
        self.assertEqual((s2.company_id, s2.basis), (s1.company_id, "name_state"))

    def test_subsidiary_never_inherits_size(self):
        parent = self.resolve(cand("CSL Behring", domain="p.com", state="PA"))
        record_evidence(self.conn, parent.company_id,
                        [EvidenceDraft("size", "5000", "estimate", "linkedin", "li", num_low=5000)], actor="source")
        mark_shared_key(self.conn, "domain", "p.com", "3 employers")
        subs = [self.resolve(cand(n, domain="p.com", state="PA")) for n in ("Seqirus", "CSL Plasma")]
        self.assertEqual([s.created for s in subs], [True, True])
        self.assertEqual(len({parent.company_id, *(s.company_id for s in subs)}), 3)
        for s in subs:
            self.assertEqual(self.count("SELECT COUNT(*) FROM evidence WHERE company_id = ? AND fact IN "
                                        "('size','headcount')", s.company_id), 0)
        self.assertEqual(self.count("SELECT COUNT(*) FROM evidence WHERE fact = 'size'"), 1)

    def test_shared_company_fein_does_not_veto(self):
        a = self.resolve(cand("Acme", fein="111111111", state="TX"))
        mark_shared_key(self.conn, "fein", "111111111", "2 employers")
        b = self.resolve(cand("Acme Inc", fein="222222222", state="TX", ref="t:acme-2"))
        self.assertEqual((b.company_id, b.basis, b.items_raised), (a.company_id, "name_state", 0))


class FeinVetoTest(Base):
    """Ruling: two valid non-shared FEINs that differ veto every non-FEIN strong match."""

    def lca_pair(self, fein):
        return Candidate(name="Acme", domain="acme.com", keys={"legal_name": "ACME INC", "fein": fein},
                         evidence=(hq("Austin, TX"), dom("acme.com", "guess")), source_ref=f"lca:acme#{fein}")

    def test_fein_split_pair_stays_two_companies_with_one_item(self):
        mark_shared_key(self.conn, "domain", "acme.com", "2 employers: ACME INC")
        a = self.resolve(self.lca_pair("111111111"))
        b = self.resolve(self.lca_pair("222222222"))
        self.assertEqual((a.company_id, b.company_id, b.created, b.basis, b.confidence, b.items_raised),
                         (1, 2, True, "created", "new", 1))
        self.assert_one_item(1, 2, "Acme (#1)", "Acme (#2)", "Basis fein_conflict+name_state", "confidence weak",
                             "111111111", "222222222")
        self.assertEqual(self.aliases(), {("acme", 1, "name"), ("111111111", 1, "fein"), ("222222222", 2, "fein")})
        before = table_marks(self.conn)
        self.resolve(self.lca_pair("111111111"))
        self.resolve(self.lca_pair("222222222"))
        self.assertEqual(table_marks(self.conn), before)

    def test_fein_split_pair_with_unshared_domain_also_stays_apart(self):
        a = self.resolve(Candidate(
            name="Acme", domain="acme.com", keys={"fein": "111111111"},
            evidence=(hq("Austin, TX"), dom("acme.com")), source_ref="lca:a"))
        b = self.resolve(Candidate(name="Acme", domain="acme.com", keys={"fein": "222222222"},
                                   evidence=(hq("Austin, TX"), dom("acme.com")), source_ref="lca:b"))
        self.assertEqual((a.company_id, b.company_id, b.basis, b.items_raised), (1, 2, "created", 1))
        self.assert_one_item(1, 2, "Basis fein_conflict+domain", "confidence weak")

    def test_domain_veto_needs_two_feins(self):
        a = self.resolve(cand("Orbit", domain="orbit.io", fein="111111111"))
        vetoed = self.resolve(cand("Orbit Labs", domain="orbit.io", fein="222222222"))
        self.assertEqual((vetoed.created, vetoed.items_raised), (True, 1))
        self.assert_one_item(a.company_id, vetoed.company_id, "Basis fein_conflict+domain", "FEIN 222222222")
        no_fein = self.resolve(cand("Orbit Inc", domain="orbit.io", ref="hn:orbit"), source="hn")
        self.assertEqual((no_fein.created, no_fein.basis), (False, "domain"))

    def test_veto_with_creates_false_returns_none(self):
        self.resolve(cand("Acme", fein="111111111", state="TX"))
        before = table_marks(self.conn)
        self.assertIsNone(self.resolve(cand("Acme", fein="222222222", state="TX", ref="formd:9"), source="formd",
                                       creates=False))
        self.assertEqual(table_marks(self.conn), before)

    def test_invalid_fein_never_vetoes(self):
        a = self.resolve(cand("Acme", fein="111111111", state="TX"))
        b = self.resolve(cand("Acme", fein="000000000", state="TX", ref="t:acme-2"))
        self.assertEqual((b.company_id, b.basis), (a.company_id, "name_state"))
        self.assertEqual(self.count("SELECT COUNT(*) FROM company_alias WHERE source = 'fein'"), 1)


class WeakMatchTest(Base):
    def test_same_name_other_state_one_company_one_item(self):
        ca = self.resolve(cand("Acme Inc", state="CA", city="San Jose", ref="lca:acme-ca"))
        ny = self.resolve(cand("Acme Inc", state="NY", city="New York", ref="lca:acme-ny"))
        self.assertEqual((ca.company_id, ny.company_id, ny.created, ny.basis, ny.items_raised),
                         (1, 2, True, "created", 1))
        self.assert_one_item(1, 2, "Acme Inc (#1)", "Acme Inc (#2)", "Basis name,", "confidence weak", "CA", "NY")
        self.assertEqual(self.count("SELECT company_id FROM company_alias WHERE alias_key = 'acme'"), 1)
        before = table_marks(self.conn)
        again = self.resolve(cand("Acme Inc", state="NY", city="New York", ref="lca:acme-ny"))
        self.resolve(cand("Acme Inc", state="CA", city="San Jose", ref="lca:acme-ca"))
        self.assertEqual((again.company_id, again.basis, again.items_raised), (2, "source_ref", 0))
        self.assertEqual(table_marks(self.conn), before)

    def test_name_without_state_is_weak(self):
        tx = self.resolve(cand("Mesh Systems", state="TX"))
        self.assertEqual(find_company(self.conn, cand("Mesh Systems", ref="hn:mesh"), source_name="hn"),
                         Match(strong=(), weak=((tx.company_id, "name"),)))
        no_state = self.resolve(cand("Mesh Systems", ref="hn:mesh"), source="hn")
        self.assertEqual((no_state.created, no_state.items_raised), (True, 1))
        self.assertEqual((tx.company_id, no_state.company_id), (1, 2))
        self.assert_one_item(1, 2, "Basis name,", "unknown here and TX there")
        # A guess hq on the candidate is not a state either.
        # Both earlier Mesh Systems companies hold the name now, so this one is weak against each.
        guess = self.resolve(cand("Mesh Systems", state="TX", hq_confidence="guess", ref="hn:mesh2"), source="hn")
        self.assertEqual((guess.company_id, guess.created, guess.items_raised), (3, True, 2))
        self.assertEqual([(i["company_id"], i["evidence_key"]) for i in self.items()], [(1, "2"), (1, "3"), (2, "3")])
        # And a company with no state never matches a candidate that has one.
        stateless = self.resolve(cand("Quartzline", ref="hn:q"), source="hn")
        later = self.resolve(cand("Quartzline", state="TX"))
        self.assertEqual((later.created, later.items_raised), (True, 1))
        self.assertNotEqual(later.company_id, stateless.company_id)

    def test_short_name_never_matches_by_name(self):
        a = self.resolve(cand("Ace", state="TX", ref="t:ace-1"))
        self.assertEqual(find_company(self.conn, cand("Ace", state="TX", ref="t:ace-2"), source_name="lca"),
                         Match(strong=(), weak=()))
        b = self.resolve(cand("Ace", state="TX", ref="t:ace-2"))
        self.assertEqual((b.created, b.basis), (True, "created"))
        self.assertNotEqual(a.company_id, b.company_id)
        # The alias stays with the first; the collision is an item, never a merge.
        self.assert_one_item(a.company_id, b.company_id, "Basis alias:name", "confidence weak")
        # Four characters is enough to match.
        c = self.resolve(cand("Aces", state="TX", ref="t:aces-1"))
        d = self.resolve(cand("Aces", state="TX", ref="t:aces-2"))
        self.assertEqual((d.company_id, d.basis), (c.company_id, "name_state"))

    def test_name_with_no_ascii_letters_still_stores(self):
        self.assertEqual(name_key("日本"), "")
        a = self.resolve(cand("日本", state="TX", ref="t:jp-1"))
        b = self.resolve(cand("日本", state="TX", ref="t:jp-2"))
        self.assertEqual((a.created, b.created, a.aliases_added, b.aliases_added), (True, True, 0, 0))
        self.assertNotEqual(a.company_id, b.company_id)
        self.assertEqual(self.count("SELECT COUNT(*) FROM company_alias"), 0)
        self.assertEqual(self.count("SELECT COUNT(*) FROM review_item"), 0)

    def test_unusable_domain_never_matches_or_stores(self):
        made = [self.resolve(cand(n, domain=d)) for n, d in (("Solo One", "gmail.com"), ("Solo Two", "gmail.com"),
                                                             ("Hub One", "github.com"), ("Hub Two", "github.com"))]
        self.assertEqual([m.created for m in made], [True] * 4)
        self.assertEqual(len({m.company_id for m in made}), 4)
        self.assertEqual(self.count("SELECT COUNT(*) FROM evidence WHERE fact = 'domain'"), 0)
        self.assertEqual(self.count("SELECT COUNT(*) FROM review_item"), 0)

    def test_alias_owned_elsewhere_raises_item(self):
        north = self.resolve(cand("Northwind", ats="greenhouse:northwind"))
        bright = self.resolve(cand("Brightline", ats="greenhouse:northwind", linkedin="brightline-co"))
        self.assertEqual((bright.created, bright.aliases_added, bright.items_raised), (True, 2, 1))
        self.assert_one_item(north.company_id, bright.company_id, "Northwind (#1)", "Brightline (#2)",
                             "Basis alias:ats", "confidence strong", "greenhouse:northwind")
        self.assertEqual(self.aliases(), {("northwind", 1, "name"), ("greenhouse:northwind", 1, "ats"),
                                          ("brightline", 2, "name"), ("brightline-co", 2, "linkedin")})
        before = table_marks(self.conn)
        self.resolve(cand("Brightline", ats="greenhouse:northwind", linkedin="brightline-co"))
        self.assertEqual(table_marks(self.conn), before)


class NonCreatingTest(Base):
    def test_non_creating_source_returns_none_when_unmatched_and_attaches_when_matched(self):
        before = table_marks(self.conn)
        self.assertIsNone(self.resolve(cand("Orchard Labs", state="CA", ref="formd:1"), source="formd",
                                       creates=False))
        self.assertEqual(table_marks(self.conn), before)
        orchard = self.resolve(cand("Orchard Labs", state="CA"))
        funding = EvidenceDraft("funding", json.dumps({"amount_usd": 5000000, "filed_on": "2026-03-01",
                                                       "kind": "form_d"}, sort_keys=True), "confirmed", "formd",
                                "0001-26-000001", observed_on="2026-03-01")
        hit = self.resolve(cand("Orchard Labs, Inc.", state="CA", kind="formd", ref="formd:1", evidence=(funding,)),
                           source="formd", creates=False)
        self.assertEqual((hit.company_id, hit.created, hit.basis, hit.confidence, hit.evidence_added),
                         (orchard.company_id, False, "name_state", "likely", 2))
        self.assertEqual(self.count("SELECT COUNT(*) FROM evidence WHERE fact = 'funding' AND company_id = ?",
                                    orchard.company_id), 1)
        before = table_marks(self.conn)
        self.assertIsNone(self.resolve(cand("Orchard Labs", state="NY", ref="formd:2"), source="formd",
                                       creates=False))
        self.assertEqual(table_marks(self.conn), before)


class RefusalAndRollbackTest(Base):
    def test_hand_evidence_draft_is_refused_by_identity(self):
        before = table_marks(self.conn)
        c = cand("Acme", evidence=(EvidenceDraft("hq", "TX", "confirmed", "hand"),))
        with self.assertRaisesRegex(ValueError, "hand"):
            self.resolve(c, source="hand")
        self.assertEqual(table_marks(self.conn), before)

    def test_sqlite_error_rolls_back_the_whole_candidate(self):
        before = table_marks(self.conn)
        bad = Candidate(name="Acme", domain=None, keys={"fein": "123456789"},
                        evidence=(hq("Austin, TX"), dom("Acme.com")), source_ref="t:acme")  # upper case: CHECK fails
        with self.assertRaises(sqlite3.IntegrityError):
            self.resolve(bad)
        self.assertEqual(table_marks(self.conn), before)
        self.assertFalse(self.conn.in_transaction)

    def test_value_error_rolls_back_the_whole_candidate(self):
        before = table_marks(self.conn)
        with mock.patch.object(identity, "record_evidence", side_effect=ValueError("boom")):
            with self.assertRaisesRegex(ValueError, "boom"):
                self.resolve(cand("Acme", fein="123456789", state="TX"))
        self.assertEqual(table_marks(self.conn), before)

    def test_failure_inside_a_caller_transaction_undoes_only_that_candidate(self):
        with transaction(self.conn):
            kept = self.resolve(cand("Kept Co", state="TX"))
            with mock.patch.object(identity, "record_evidence", side_effect=sqlite3.IntegrityError("x")):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.resolve(cand("Lost Co", state="TX"))
        self.assertEqual(self.companies(), [(kept.company_id, "Kept Co", "candidate")])
        self.assertEqual(self.count("SELECT COUNT(*) FROM company_alias"), 1)

    def test_unregistered_source_and_empty_ref_raise_before_writing(self):
        before = table_marks(self.conn)
        with self.assertRaisesRegex(ValueError, "not registered"):
            self.resolve(cand("Acme"), source="nowhere")
        with self.assertRaisesRegex(ValueError, "source_ref"):
            self.resolve(cand("Acme", ref=""))
        self.assertEqual(table_marks(self.conn), before)


class HelpersTest(Base):
    def company_with(self, *hqs):
        res = self.resolve(cand(f"Co {len(self.companies())}"))
        record_evidence(self.conn, res.company_id, [hq(v, c, "lca", f"r{i}") for i, (v, c) in enumerate(hqs)])
        return res.company_id

    def test_company_state(self):
        self.assertEqual(company_state(self.conn, self.company_with(("Austin, TX", "estimate"))), "TX")
        self.assertEqual(company_state(self.conn, self.company_with(("NY", "confirmed"))), "NY")
        self.assertIsNone(company_state(self.conn, self.company_with(("Austin, TX", "guess"))))
        self.assertIsNone(company_state(self.conn, self.company_with(("Austin, TX", "estimate"),
                                                                     ("Boston, MA", "estimate"))))
        self.assertEqual(company_state(self.conn, self.company_with(("Austin, TX", "estimate"),
                                                                    ("Dallas, TX", "estimate"),
                                                                    ("Remote", "estimate"))), "TX")
        self.assertIsNone(company_state(self.conn, self.company_with(("Austin", "estimate"))))
        self.assertIsNone(company_state(self.conn, self.company_with()))

    def test_raise_merge_candidate_contract(self):
        a = self.resolve(cand("Alpha")).company_id
        b = self.resolve(cand("Beta")).company_id
        self.assertTrue(raise_merge_candidate(self.conn, b, a, "domain", "both on alpha.com"))
        self.assertFalse(raise_merge_candidate(self.conn, a, b, "name", "again"))
        item = self.assert_one_item(a, b, "Alpha (#1)", "Beta (#2)", "Basis domain", "confidence strong",
                                    "both on alpha.com")
        self.assertEqual(int(item["evidence_key"]), b)
        with self.assertRaises(ValueError):
            raise_merge_candidate(self.conn, a, a, "domain", "self")
        with self.assertRaises(ValueError):
            raise_merge_candidate(self.conn, a, b, "mystery", "no confidence known")
        self.assertTrue(raise_merge_candidate(self.conn, a, self.resolve(cand("Gamma")).company_id, "mystery",
                                              "stated", confidence="likely"))

    def test_find_company_is_read_only(self):
        self.resolve(cand("Acme", domain="acme.com", state="TX"))
        before, changes = table_marks(self.conn), self.conn.total_changes
        m = find_company(self.conn, cand("Acme", domain="acme.com", state="TX", ref="hn:a"), source_name="hn")
        self.assertEqual(m, Match(strong=((1, "domain"),), weak=()))
        self.assertEqual((table_marks(self.conn), self.conn.total_changes), (before, changes))
        self.assertEqual(find_company(self.conn, cand("Acme", domain="acme.com", state="TX"), source_name="lca"),
                         Match(strong=((1, "source_ref"),), weak=()))


class ReviewFixTest(Base):
    """Review round 1 of Task 7: each test here was red before its fix."""

    def test_fein_veto_reads_feins_of_companies_merged_into_the_hit(self):  # I1
        a = self.resolve(cand("Acme Robotics", fein="111111111", state="TX"))
        k = self.resolve(cand("Kestrel", state="TX"))
        self.conn.execute("UPDATE company SET status = 'removed', remove_reason = 'merged', merged_into = ? "
                          "WHERE company_id = ?", (k.company_id, a.company_id))
        c = self.resolve(cand("Acme Robotics", fein="222222222", state="TX", ref="t:acme-robotics-2"))
        self.assertEqual((c.company_id, c.created, c.items_raised), (3, True, 1))
        self.assert_one_item(2, 3, "Basis fein_conflict+name_state", "111111111", "222222222")
        self.assertEqual(self.count("SELECT company_id FROM company_alias WHERE alias_key = '222222222'"), 3)

    def test_a_name_shared_by_two_companies_matches_the_one_in_the_same_state(self):  # I2
        tx = self.resolve(cand("Acme Robotics", state="TX", ref="lca:tx"))
        ca = self.resolve(cand("Acme Robotics", state="CA", city="San Jose", ref="lca:ca"))
        self.assertEqual((tx.company_id, ca.company_id, ca.created), (1, 2, True))
        self.assertEqual([tuple(r) for r in self.conn.execute(
            "SELECT name_key, company_id FROM company_name_key ORDER BY company_id")],
            [("acmerobotics", 1), ("acmerobotics", 2)])
        self.assertEqual(find_company(self.conn, cand("Acme Robotics", state="CA", ref="hn:x"), source_name="hn"),
                         Match(strong=((2, "name_state"),), weak=((1, "name"),)))
        more = [self.resolve(cand("Acme Robotics", state="CA", city="Oakland", ref=r, kind="hn"), source="hn")
                for r in ("hn:ar1", "hn:ar2")]
        self.assertEqual([(m.company_id, m.created, m.basis, m.items_raised) for m in more],
                         [(2, False, "name_state", 0)] * 2)
        hit = self.resolve(cand("Acme Robotics", state="CA", ref="formd:ar", kind="formd"), source="formd",
                           creates=False)
        self.assertEqual((hit.company_id, hit.basis), (2, "name_state"))
        self.assertEqual(self.count("SELECT COUNT(*) FROM company"), 2)
        self.assert_one_item(1, 2, "Basis name,")
        self.assertIn("company_name_key", table_marks(self.conn))
        before = table_marks(self.conn)
        self.resolve(cand("Acme Robotics", state="CA", city="San Jose", ref="lca:ca"))
        self.assertEqual(table_marks(self.conn), before)

    def test_name_keys_under_four_characters_are_not_stored(self):  # I2
        self.resolve(cand("Ace", legal_name="Ace Widgets", dba="Acme", state="TX"))
        self.assertEqual({r[0] for r in self.conn.execute("SELECT name_key FROM company_name_key")},
                         {"acewidgets", "acme"})

    def test_hosting_domains_never_match_or_store(self):  # I3
        made = [self.resolve(cand(n, domain=d)) for n, d in (("Acme", "acme.vercel.app"), ("Beta", "beta.vercel.app"),
                                                             ("Gamma", "vercel.app"), ("Delta", "vercel.app"))]
        self.assertEqual([m.company_id for m in made], [1, 2, 3, 4])
        self.assertEqual(self.count("SELECT COUNT(*) FROM evidence WHERE fact = 'domain'"), 0)
        self.assertEqual(self.count("SELECT COUNT(*) FROM review_item"), 0)

    def test_state_must_be_a_us_state_dc_or_territory(self):  # M1
        cases = {"London, UK": None, "Toronto, ON": None, "Remote, US": None, "Berlin, DE": "DE",
                 "San Juan, PR": "PR", "Washington, DC": "DC", "Hagatna, GU": "GU", "Austin, TX": "TX", "ZZ": None}
        for value, state in cases.items():
            res = self.resolve(cand(f"Co {value}", evidence=(hq(value),), ref=f"t:{value}"))
            self.assertEqual(company_state(self.conn, res.company_id), state, value)
        a = self.resolve(cand("Northstar Labs", evidence=(hq("London, UK"),)))
        b = self.resolve(cand("Northstar Labs", evidence=(hq("Leeds, UK"),), ref="t:northstar-2"))
        self.assertNotEqual(a.company_id, b.company_id)

    def test_a_name_with_a_non_ascii_letter_is_never_a_strong_name_key(self):  # M2
        a = self.resolve(cand("東京 Robotics", state="TX", ref="t:tokyo"))
        b = self.resolve(cand("大阪 Robotics", state="TX", ref="t:osaka"))
        self.assertEqual((a.company_id, b.company_id, b.created, b.items_raised), (1, 2, True, 1))
        self.assert_one_item(1, 2, "Basis name,")
        c = self.resolve(cand("Café Labs", state="TX", ref="t:cafe-1"))
        d = self.resolve(cand("Café Labs", state="TX", ref="t:cafe-2"))
        self.assertEqual((d.created, d.items_raised), (True, 1))
        self.assertNotEqual(c.company_id, d.company_id)
        # An ASCII legal name on the same candidate is still a strong key.
        e = self.resolve(cand("Café Labs", legal_name="Cafe Labs Inc", state="TX", ref="t:cafe-3"))
        f = self.resolve(cand("Cafe Labs", state="TX", ref="t:cafe-4"))
        self.assertEqual((f.company_id, f.basis), (e.company_id, "name_state"))

    def test_a_full_rollback_by_sqlite_does_not_hide_the_real_error(self):  # M3
        def roll_back_everything(conn, *args, **kw):
            conn.execute("ROLLBACK")
            raise sqlite3.IntegrityError("the real error")
        self.conn.execute("BEGIN")
        with mock.patch.object(identity, "record_evidence", side_effect=roll_back_everything):
            with self.assertRaisesRegex(sqlite3.IntegrityError, "the real error"):
                self.resolve(cand("Acme", state="TX"))
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(self.count("SELECT COUNT(*) FROM company"), 0)

    def test_raise_merge_candidate_follows_merges(self):  # M4
        a, b, c = (self.resolve(cand(n)).company_id for n in ("Alpha", "Beta", "Gamma"))
        self.conn.execute("UPDATE company SET status = 'removed', remove_reason = 'm', merged_into = ? "
                          "WHERE company_id = ?", (b, a))
        self.assertFalse(raise_merge_candidate(self.conn, a, b, "domain", "already one"))
        self.assertEqual(self.count("SELECT COUNT(*) FROM review_item"), 0)
        self.assertTrue(raise_merge_candidate(self.conn, c, a, "domain", "a is b now"))
        self.assert_one_item(b, c, "Beta (#2)", "Gamma (#3)")

    def test_www_is_stripped_for_matching_and_storing(self):  # M5
        a = self.resolve(cand("Acme", domain="acme.com"))
        b = self.resolve(cand("Acme Labs", domain="www.acme.com", ref="hn:al"), source="hn")
        self.assertEqual((b.company_id, b.basis), (a.company_id, "domain"))
        c = self.resolve(cand("Zeta", domain="www.zeta.io"))
        self.assertEqual([r[0] for r in self.conn.execute(
            "SELECT value FROM evidence WHERE fact = 'domain' AND company_id = ?", (c.company_id,))], ["zeta.io"])
        d = self.resolve(cand("Zeta Two", domain="zeta.io"))
        self.assertEqual((d.company_id, d.basis), (c.company_id, "domain"))

    def test_formd_name_alias_has_source_formd(self):  # M7
        self.resolve(cand("Nimbus", fein="111111111", state="CA"))
        res = self.resolve(cand("Nimbus Holdings", fein="111111111", ref="formd:9"), source="formd", creates=False)
        self.assertEqual((res.basis, res.aliases_added), ("fein", 1))
        self.assertIn(("nimbusholdings", 1, "formd"), self.aliases())

    def test_new_aliases_on_a_known_record_append_aliases_added(self):  # M7
        self.resolve(cand("Acme", state="TX"))
        res = self.resolve(cand("Acme", state="TX", dba="Acme Widgets"))
        self.assertEqual((res.basis, res.aliases_added), ("source_ref", 1))
        detail = json.loads(self.conn.execute(
            "SELECT detail FROM event WHERE kind = 'aliases_added'").fetchone()[0])
        self.assertEqual((detail["basis"], detail["confidence"], detail["aliases"]),
                         ("source_ref", "certain", [["dba", "Acme Widgets"]]))
        before = table_marks(self.conn)
        self.resolve(cand("Acme", state="TX", dba="Acme Widgets"))
        self.assertEqual(table_marks(self.conn), before)

    def test_strong_and_weak_together(self):  # M7
        a = self.resolve(cand("Alpha", fein="111111111", state="TX"))
        b = self.resolve(cand("Bright Labs", state="CA"))
        res = self.resolve(cand("Bright Labs", fein="111111111", state="TX", ref="t:bl-tx"))
        self.assertEqual(find_company(self.conn, cand("Bright Labs", fein="111111111", state="TX", ref="hn:q"),
                                      source_name="hn"),
                         Match(strong=((a.company_id, "fein"),), weak=((b.company_id, "name"),)))
        self.assertEqual((res.company_id, res.basis, res.items_raised), (a.company_id, "fein", 1))
        self.assert_one_item(1, 2, "Basis name,", "TX here and CA there")

    def test_two_domain_hits_are_ordered_by_id(self):  # M7
        ids = [self.resolve(cand(n)).company_id for n in ("Alpha", "Beta")]
        for cid in reversed(ids):
            record_evidence(self.conn, cid, [dom("shared-by-two.com", "estimate", "website", f"w{cid}")])
        c = cand("Gamma", domain="shared-by-two.com")
        self.assertEqual(find_company(self.conn, c, source_name="lca"),
                         Match(strong=((1, "domain"), (2, "domain")), weak=()))
        res = self.resolve(c)
        self.assertEqual((res.company_id, res.basis, res.items_raised), (1, "domain", 1))
        self.assert_one_item(1, 2, "Basis domain+domain", "confidence strong")


if __name__ == "__main__":
    unittest.main()
