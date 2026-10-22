"""SEC Form D source (#31 Task 11): the data set reader, funding evidence, the SEC lookups and the end-to-end runs.

The fixture data set is the real 2026 Q3 header rows plus invented rows; `submissions/CIK*.json` stand in for
data.sec.gov. Nothing here touches the network: a `FakeClient` serves the JSON.
"""
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from outreach.net import FetchError, SpacedClient
from outreach.sources import formd
from outreach.sources.formd import SEC_USER_AGENT, FormDSource, sec_headers
from outreach.sources.jobs import run_discover, store_name_lookup
from outreach.sources.lca import LcaSource
from outreach.sources.tabular import EmptyTable, MissingColumns, TabularError
from outreach.store import add_alias, name_key
from tests.contracts import check_source
from tests.fakes import FakeClient, status_response, text_response
from tests.storetest import fresh_store, seed_company, table_marks
from tests.test_lca_source import FILES

DATASET = Path(__file__).parent / "fixtures" / "formd"
SEC = "https://data.sec.gov/submissions/CIK{}.json"
ORCHARD, NIMBUS, MESH, CLEARPATH, FUND, UNKNOWN, LUMEN, _TEST, _GARBLED, TORONTO = (
    f"{n:010d}" for n in range(1000001, 1000011))
FETCHED = {ORCHARD, NIMBUS, MESH, CLEARPATH, UNKNOWN, LUMEN, TORONTO}  # every readable non-fund issuer


def routes(**overrides):
    """Every fixture submissions file under its URL; `overrides` maps a CIK to a route value of its own."""
    out = {SEC.format(p.stem[3:]): p for p in sorted((DATASET / "submissions").glob("CIK*.json"))}
    out.update({SEC.format(cik): value for cik, value in overrides.items()})
    return out


def make(client=None, **kw):
    kw.setdefault("interval_s", 0.0)
    return FormDSource(kw.pop("dataset", DATASET), client or FakeClient(routes()), **kw)


def by_cik(source, since=None):
    return {c.keys["formd_cik"]: c for c in source.discover(since)}


def funding(cand):
    return [(json.loads(e.value), e) for e in cand.evidence if e.fact == "funding"]


def copy_dataset(tc):
    folder = Path(tempfile.mkdtemp(prefix="outreach-formd-test-"))
    tc.addCleanup(shutil.rmtree, folder, True)
    for p in DATASET.glob("*.tsv"):
        shutil.copy(p, folder / p.name)
    return folder


class SecHeadersTest(unittest.TestCase):
    def test_the_user_agent_is_the_approved_one(self):
        self.assertEqual(SEC_USER_AGENT, "outreach-research chicholikar.d@northeastern.edu")

    def test_sec_hosts_get_it(self):
        for url in ("https://data.sec.gov/submissions/CIK0001000001.json", "https://www.sec.gov/x",
                    "https://SEC.GOV/y"):
            self.assertEqual(sec_headers(url), {"User-Agent": SEC_USER_AGENT}, url)

    def test_no_other_host_gets_it(self):
        for url in ("https://example.com/", "https://sec.gov.evil.example/", "https://notsec.gov/",
                    "https://data.sec.gov.evil.example/", "https://efts.example.org/sec.gov", "not a url",
                    "https://boards.greenhouse.io/x"):
            self.assertEqual(sec_headers(url), {}, url)


class ClassTest(unittest.TestCase):
    def test_attributes(self):
        s = make()
        self.assertEqual((s.name, s.schedule, s.version, s.creates_companies),
                         ("formd", "daily", "formd-1", False))

    def test_the_default_spacing_is_one_request_a_second(self):
        s = FormDSource(DATASET, FakeClient({}))
        self.assertIsInstance(s.client, SpacedClient)
        self.assertEqual(s.client.per_host["data.sec.gov"], 1.0)
        self.assertEqual(FormDSource(DATASET, FakeClient({}), interval_s=0.0).client.per_host["data.sec.gov"], 0.0)


class ReadTest(unittest.TestCase):
    def test_funds_are_skipped(self):
        s = make()
        cands = by_cik(s)
        self.assertNotIn(FUND, cands)
        self.assertNotIn("Acme Growth Fund LP", {c.name for c in cands.values()})
        self.assertEqual(s.summary()["funds_skipped"], 1)
        self.assertNotIn(SEC.format(FUND), s.client.inner.requests)

    def test_only_the_primary_issuer_is_read(self):
        names = {c.name for c in make().discover()}
        self.assertNotIn("Sundial Health Inc", names)
        self.assertEqual(names, {"Orchard, Inc.", "Nimbus Robotics Holdings Inc", "Mesh Systems Inc",
                                 "Clearpath Holdings LLC", "Unknown Startup Inc", "Lumen Apps Holdings Inc",
                                 "Toronto Cloud Corp"})

    def test_a_test_filing_and_an_unreadable_date_are_not_yielded_and_are_counted(self):
        s = make()
        names = {c.name for c in s.discover()}
        self.assertNotIn("Test Filing Corp", names)
        self.assertNotIn("Garbled Date Inc", names)
        summary = s.summary()
        self.assertEqual((summary["test_filings"], summary["unreadable"]), (1, 1))

    def test_funding_value_and_kind(self):
        orchard = by_cik(make())[ORCHARD]
        d, da = funding(orchard)
        self.assertEqual(d[1].value, '{"amount_usd":4500000,"filed_on":"2026-08-14","kind":"form_d"}')
        self.assertEqual(da[1].value, '{"amount_usd":6500000,"filed_on":"2026-09-10","kind":"form_d_amendment"}')
        for _, e in (d, da):
            self.assertEqual((e.confidence, e.source_kind), ("confirmed", "formd"))
        self.assertEqual((d[1].source_ref, d[1].observed_on), ("0001000001-26-000001", "2026-08-14"))
        self.assertEqual((da[1].source_ref, da[1].observed_on), ("0001000001-26-000002", "2026-09-10"))

    def test_an_amount_not_sold_yet_is_null(self):
        (d,) = funding(by_cik(make())[NIMBUS])
        self.assertEqual(d[0], {"amount_usd": None, "filed_on": "2026-08-20", "kind": "form_d"})

    def test_date_formats(self):
        """14-AUG-2026 (the SEC's) and 2026-08-14 both read."""
        orchard = by_cik(make())[ORCHARD]
        self.assertEqual([d["filed_on"] for d, _ in funding(orchard)], ["2026-08-14", "2026-09-10"])

    def test_since_filters_by_filing_date(self):
        s = make()
        got = by_cik(s, since="2026-09-10")
        self.assertEqual(set(got), {ORCHARD, LUMEN, UNKNOWN, TORONTO})
        self.assertEqual([d["kind"] for d, _ in funding(got[ORCHARD])], ["form_d_amendment"])
        self.assertEqual(set(by_cik(s, since="2026-09-11")), {LUMEN, UNKNOWN, TORONTO})
        self.assertEqual(set(by_cik(s, since="2026-09-30T08:00:00Z")), {UNKNOWN})  # a timestamp reads as its date
        self.assertEqual(by_cik(s, since="2026-10-01"), {})  # a quiet period is real, not an error
        self.assertEqual(len(by_cik(s, since=None)), 7)

    def test_a_since_that_is_not_a_date_raises(self):
        with self.assertRaises(ValueError):
            list(make().discover(since="last week"))

    def test_candidate_shape(self):
        c = by_cik(make())[ORCHARD]
        self.assertEqual((c.name, c.domain, c.keys, c.source_ref),
                         ("Orchard, Inc.", None, {"formd_cik": ORCHARD}, f"formd:{ORCHARD}"))
        hq = [e for e in c.evidence if e.fact == "hq"]
        self.assertEqual(len(hq), 1)
        self.assertEqual((hq[0].value, hq[0].confidence, hq[0].source_kind, hq[0].source_ref, hq[0].observed_on),
                         ("New York, NY", "estimate", "formd", "0001000001-26-000002", "2026-09-10"))
        self.assertEqual({e.fact for e in c.evidence}, {"funding", "hq"})

    def test_only_a_us_state_is_an_hq(self):
        c = by_cik(make())[TORONTO]
        self.assertEqual({e.fact for e in c.evidence}, {"funding"})

    def test_no_size_stage_or_headcount_is_ever_drafted(self):
        for c in make().discover():
            self.assertFalse({e.fact for e in c.evidence} & {"size", "stage", "headcount"}, c.name)

    def test_fein_comes_from_the_sec_ein(self):
        got = by_cik(make())
        self.assertEqual(got[NIMBUS].keys, {"formd_cik": NIMBUS, "fein": "111111111"})
        self.assertEqual(got[CLEARPATH].keys["fein"], "333333333")  # identity refuses it: it is a shared key
        for cik in (ORCHARD, MESH, UNKNOWN, LUMEN):  # "", null and 000000000 are no FEIN
            self.assertNotIn("fein", got[cik].keys, cik)

    def test_summary_counts(self):
        s = make()
        list(s.discover())
        self.assertEqual(s.summary(), {"filings": 11, "issuers": 7, "funds_skipped": 1, "test_filings": 1,
                                       "unreadable": 1, "fetches": 7, "fetch_errors": 0})
        list(s.discover(since="2026-09-30"))
        self.assertEqual(s.summary()["issuers"], 1)  # a pass reports itself, not a running total

    def test_formd_source_passes_check_source(self):
        s = make()
        self.assertEqual(len(check_source(self, s)), 7)
        self.assertEqual(s.client.inner.unrouted, [])


class SecRequestsTest(unittest.TestCase):
    def test_every_request_is_to_data_sec_gov_with_the_sec_user_agent(self):
        client = FakeClient(routes())
        list(make(client).discover())
        self.assertEqual(len(client.calls), 7)
        for method, url, headers, _ in client.calls:
            self.assertEqual(method, "GET")
            self.assertRegex(url, r"^https://data\.sec\.gov/submissions/CIK\d{10}\.json$")
            self.assertEqual(headers, {"User-Agent": SEC_USER_AGENT})
        self.assertEqual({u for _, u, _, _ in client.calls}, {SEC.format(c) for c in FETCHED})

    def test_nothing_is_fetched_for_a_fund_a_test_filing_or_an_unreadable_row(self):
        client = FakeClient(routes())
        list(make(client).discover())
        self.assertFalse({SEC.format(c) for c in (FUND, _TEST, _GARBLED)} & set(client.requests))

    def test_one_fetch_per_issuer_whatever_its_filing_count(self):
        client = FakeClient(routes())
        list(make(client).discover())
        self.assertEqual(client.requests.count(SEC.format(ORCHARD)), 1)  # a D and a D/A

    def test_the_name_lookup_gates_the_fetches_and_gets_the_name_key(self):
        client = FakeClient(routes())
        seen = []

        def lookup(key):
            seen.append(key)
            return key == "orchard"

        got = by_cik(make(client, name_lookup=lookup))
        self.assertEqual(client.requests, [SEC.format(ORCHARD)])
        self.assertIn("nimbusroboticsholdings", seen)
        self.assertEqual(len(got), 7)  # a gated issuer is still yielded, only without a FEIN
        self.assertNotIn("fein", got[NIMBUS].keys)

    def test_failures_for_one_issuer_drop_its_fein_and_are_counted(self):
        bad = {"a transport failure": FetchError("timeout", "slow"),
               "a 503": status_response(503),
               "a 404": status_response(404),
               "a 429": status_response(429),
               "a redirect": status_response(301),
               "a body that is not JSON": text_response("<html>blocked</html>"),
               "JSON that is not an object": text_response("[1, 2]")}
        for label, value in bad.items():
            with self.subTest(label):
                client = FakeClient(routes(**{NIMBUS: value}))
                s = make(client)
                got = by_cik(s)
                self.assertEqual(len(got), 7)  # the filing is never dropped
                self.assertEqual(got[NIMBUS].keys, {"formd_cik": NIMBUS})
                self.assertEqual(got[CLEARPATH].keys["fein"], "333333333")  # the others are unaffected
                self.assertEqual(s.summary()["fetch_errors"], 1)

    def test_an_unrouted_request_is_not_swallowed(self):
        client = FakeClient({})
        with self.assertRaises(AssertionError):
            list(make(client).discover())

    def test_a_failure_is_counted_once_per_issuer(self):
        client = FakeClient(routes(**{ORCHARD: FetchError("network", "down")}))
        s = make(client)
        list(s.discover())
        self.assertEqual(s.summary()["fetch_errors"], 1)  # Orchard has two filings, one issuer
        self.assertEqual(client.requests.count(SEC.format(ORCHARD)), 1)


class DatasetTest(unittest.TestCase):
    def test_a_zip_reads_like_the_folder(self):
        folder = copy_dataset(self)
        for nested in ("", "2026Q3_d/"):
            archive = folder.parent / f"formd-{len(nested)}.zip"
            self.addCleanup(archive.unlink, True)
            with zipfile.ZipFile(archive, "w") as z:
                for p in folder.glob("*.tsv"):
                    z.write(p, nested + p.name)
                z.writestr(nested + "RECIPIENTS.tsv", "ACCESSIONNUMBER\n")  # a file we never read
            with self.subTest(nested=nested):
                self.assertEqual(by_cik(make(dataset=archive)).keys(), by_cik(make()).keys())

    def test_a_folder_with_the_files_one_level_down_reads(self):
        folder = copy_dataset(self)
        inner = folder / "2026Q3_d"
        inner.mkdir()
        for p in list(folder.glob("*.tsv")):
            p.rename(inner / p.name)
        self.assertEqual(len(by_cik(make(dataset=folder))), 7)

    def test_a_missing_path_raises(self):
        with self.assertRaises(TabularError):
            list(make(dataset=DATASET / "nope").discover())

    def test_a_file_that_is_not_a_zip_raises(self):
        folder = copy_dataset(self)
        junk = folder / "junk.zip"
        junk.write_bytes(b"not a zip")
        with self.assertRaises(TabularError):
            list(make(dataset=junk).discover())

    def test_a_missing_member_raises_naming_it(self):
        folder = copy_dataset(self)
        (folder / "OFFERING.tsv").unlink()
        with self.assertRaisesRegex(TabularError, "OFFERING"):
            list(make(dataset=folder).discover())

    def test_a_missing_column_raises_naming_it(self):
        folder = copy_dataset(self)
        path = folder / "OFFERING.tsv"
        path.write_text(path.read_text(encoding="utf-8").replace("TOTALAMOUNTSOLD", "TOTALSOLD"), encoding="utf-8")
        with self.assertRaisesRegex(MissingColumns, "TOTALAMOUNTSOLD"):
            list(make(dataset=folder).discover())

    def test_a_header_with_no_rows_raises_and_is_never_zero_issuers(self):
        for member in ("FORMDSUBMISSION.tsv", "ISSUERS.tsv", "OFFERING.tsv"):
            folder = copy_dataset(self)
            path = folder / member
            path.write_text(path.read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")
            with self.subTest(member), self.assertRaises(EmptyTable):
                list(make(dataset=folder).discover())

    def test_an_empty_file_raises(self):
        folder = copy_dataset(self)
        (folder / "ISSUERS.tsv").write_text("", encoding="utf-8")
        with self.assertRaises(EmptyTable):
            list(make(dataset=folder).discover())

    def test_files_that_join_to_nothing_raise(self):
        folder = copy_dataset(self)
        path = folder / "ISSUERS.tsv"
        path.write_text(path.read_text(encoding="utf-8").replace("-26-", "-25-"), encoding="utf-8")
        with self.assertRaisesRegex(TabularError, "join"):
            list(make(dataset=folder).discover())

    def test_bytes_that_are_not_text_raise(self):
        folder = copy_dataset(self)
        (folder / "ISSUERS.tsv").write_bytes(b"ACCESSIONNUMBER\tCIK\n\xff\xfe\x00bad\n")
        with self.assertRaises(TabularError):
            list(make(dataset=folder).discover())

    def test_an_oversize_zip_member_is_refused(self):
        folder = copy_dataset(self)
        archive = folder.parent / "formd-big.zip"
        self.addCleanup(archive.unlink, True)
        with zipfile.ZipFile(archive, "w") as z:
            for p in folder.glob("*.tsv"):
                z.write(p, p.name)
        s = make(dataset=archive)
        s.max_member_bytes = 100
        with self.assertRaisesRegex(TabularError, "ISSUERS|OFFERING|FORMDSUBMISSION"):
            list(s.discover())

    def test_a_row_with_no_cik_or_name_is_unreadable_not_a_crash(self):
        folder = copy_dataset(self)
        path = folder / "ISSUERS.tsv"
        lines = path.read_text(encoding="utf-8").splitlines(True)
        header = lines[0].rstrip("\n").split("\t")
        cols = {c: i for i, c in enumerate(header)}
        out = [lines[0]]
        for line in lines[1:]:
            f = line.rstrip("\n").split("\t")
            if f[cols["CIK"]] == MESH:
                f[cols["ENTITYNAME"]] = ""
            if f[cols["CIK"]] == UNKNOWN:
                f[cols["CIK"]] = "12x"
            out.append("\t".join(f) + "\n")
        path.write_text("".join(out), encoding="utf-8")
        s = make(dataset=folder)
        got = by_cik(s)
        self.assertNotIn(MESH, got)
        self.assertEqual(s.summary()["unreadable"], 3)  # the garbled date and these two


class StoreLookupTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        run_discover(self.conn, [LcaSource(FILES)])

    def test_exact_a_longer_issuer_name_and_a_shorter_one(self):
        look = store_name_lookup(self.conn)
        self.assertTrue(look("meshsystems"))  # equal
        self.assertTrue(look("nimbusroboticsholdings"))  # the stored key is a prefix of the issuer's
        self.assertTrue(look("orchard"))  # equal to the key of the name "Orchard"
        self.assertFalse(look("unknownstartup"))
        self.assertFalse(look("zzzz"))

    def test_a_key_shorter_than_four_characters_never_matches(self):
        look = store_name_lookup(self.conn)
        self.assertFalse(look("orc"))
        self.assertFalse(look(""))

    def test_a_hand_alias_counts_too(self):
        add_alias(self.conn, seed_company(self.conn, "Birchwood"), "Birchwood", "name")
        self.assertTrue(store_name_lookup(self.conn)("birchwoodrobotics"))

    def test_a_fein_alias_is_not_a_name(self):
        self.assertFalse(store_name_lookup(self.conn)("111111111"))


class EndToEndTest(unittest.TestCase):
    """Form D over the LCA fixture store, through `run_discover` (creates_companies False, store-backed lookup)."""

    def setUp(self):
        self.conn = fresh_store()
        run_discover(self.conn, [LcaSource(FILES)])
        self.client = FakeClient(routes())
        self.source = make(self.client)
        self.run_id, self.reports = run_discover(self.conn, [self.source])
        self.report = self.reports["formd"]

    def company(self, name):
        return self.conn.execute("SELECT company_id FROM company WHERE name = ?", (name,)).fetchone()[0]

    def fundings(self, name):
        return [json.loads(v) for (v,) in self.conn.execute(
            "SELECT value FROM evidence WHERE company_id = ? AND fact = 'funding' ORDER BY observed_on",
            (self.company(name),))]

    def items(self):
        return self.conn.execute("SELECT COUNT(*) FROM review_item").fetchone()[0]

    def test_the_report(self):
        r = self.report
        self.assertEqual((r.candidates, r.created, r.attached, r.skipped_unmatched, r.items_raised, r.error, r.failed),
                         (7, 0, 2, 5, 0, "", []))
        self.assertEqual(r.version, "formd-1")
        self.assertEqual(r.notes["fetch_errors"], 0)
        self.assertEqual(self.conn.execute("SELECT status FROM job_run WHERE run_id = ?",
                                           (self.run_id,)).fetchone()[0], "ok")

    def test_the_lookups_follow_the_store(self):
        """Only issuers whose name relates to a stored company are fetched; each request carries the SEC agent."""
        self.assertEqual(set(self.client.requests), {SEC.format(c) for c in (ORCHARD, NIMBUS, MESH, CLEARPATH, LUMEN)})
        self.assertTrue(all(h == {"User-Agent": SEC_USER_AGENT} for _, _, h, _ in self.client.calls))
        self.assertEqual(self.client.unrouted, [])

    def test_orchard_attaches_by_name_and_state(self):
        self.assertEqual(self.fundings("Orchard"), [
            {"amount_usd": 4500000, "filed_on": "2026-08-14", "kind": "form_d"},
            {"amount_usd": 6500000, "filed_on": "2026-09-10", "kind": "form_d_amendment"}])
        self.assertEqual(self.conn.execute(
            "SELECT json_extract(detail, '$.basis') FROM event WHERE company_id = ? AND kind = 'matched'",
            (self.company("Orchard"),)).fetchone()[0], "name_state")

    def test_nimbus_attaches_by_fein_from_ein(self):
        """The issuer name differs ("Holdings") and its state is TX; the SEC ein equals the LCA FEIN."""
        self.assertEqual(self.fundings("Nimbus Robotics, Inc."),
                         [{"amount_usd": None, "filed_on": "2026-08-20", "kind": "form_d"}])
        self.assertEqual(self.conn.execute(
            "SELECT json_extract(detail, '$.basis') FROM event WHERE company_id = ? AND kind = 'matched'",
            (self.company("Nimbus Robotics, Inc."),)).fetchone()[0], "fein")

    def test_weak_name_other_state_is_dropped(self):
        """Mesh Systems Inc in TX against the CA company: no evidence, no item, no link, and it is counted."""
        self.assertEqual(self.fundings("Mesh Systems Inc"), [])
        self.assertEqual(self.items(), 0)
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM company_source WHERE source_name = 'formd' AND source_ref = ?",
            (f"formd:{MESH}",)).fetchone())
        self.assertEqual(self.report.skipped_unmatched, 5)

    def test_shared_ein_never_attaches(self):
        """ein 333333333 is a PEO's FEIN, marked shared by LCA: "Clearpath Holdings LLC" joins none of its clients."""
        self.assertIn(SEC.format(CLEARPATH), self.client.requests)  # the ein was read
        for client_name in ("Clearpath Inc", "Greenfield AI Inc", "Sundial Health Inc"):
            self.assertEqual(self.fundings(client_name), [], client_name)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM company_alias WHERE alias_key = '333333333'").fetchone()[0], 0)
        self.assertEqual(self.items(), 0)

    def test_blank_ein_is_ignored(self):
        self.assertIn(SEC.format(LUMEN), self.client.requests)
        self.assertEqual(self.fundings("Lumen Apps Inc"), [])
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM company_alias WHERE alias_key = '000000000'").fetchone()[0], 0)

    def test_unmatched_issuer_creates_no_company(self):
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM company").fetchone()[0], 14)
        for name in ("Unknown Startup Inc", "Toronto Cloud Corp", "Acme Growth Fund LP"):
            self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM company WHERE name = ?", (name,)).fetchone()[0], 0)
            self.assertEqual(self.conn.execute(
                "SELECT COUNT(*) FROM company_alias WHERE alias_key = ?", (name_key(name),)).fetchone()[0], 0)

    def test_form_d_writes_no_stage_size_or_headcount(self):
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM evidence WHERE fact IN ('stage', 'size', 'headcount')").fetchone()[0], 0)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM evidence WHERE source_kind = 'formd' AND fact NOT IN ('funding', 'hq')"
            ).fetchone()[0], 0)

    def test_rerun_writes_nothing_new(self):
        before = table_marks(self.conn)
        _, again = run_discover(self.conn, [make(FakeClient(routes()))])
        r = again["formd"]
        self.assertEqual(table_marks(self.conn), before)
        self.assertEqual((r.attached, r.created, r.evidence_added, r.items_raised, r.skipped_unmatched),
                         (2, 0, 0, 0, 5))

    def test_a_sec_fetch_error_keeps_the_filing_without_fein(self):
        conn = fresh_store()
        run_discover(conn, [LcaSource(FILES)])
        client = FakeClient(routes(**{NIMBUS: FetchError("timeout", "slow")}))
        run_id, reports = run_discover(conn, [make(client)])
        r = reports["formd"]
        self.assertEqual(r.notes["fetch_errors"], 1)
        self.assertEqual(r.error, "")
        self.assertEqual((r.attached, r.skipped_unmatched), (1, 6))  # Orchard by name still works; Nimbus has no key
        row = conn.execute("SELECT status, summary FROM job_run WHERE run_id = ?", (run_id,)).fetchone()
        self.assertNotEqual(row["status"], "failed")
        self.assertEqual(json.loads(row["summary"])["formd"]["notes"]["fetch_errors"], 1)
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM evidence WHERE fact = 'funding' AND company_id = ?",
            (conn.execute("SELECT company_id FROM company WHERE name = 'Orchard'").fetchone()[0],)).fetchone()[0], 2)

    def test_an_unreadable_data_set_is_a_failed_run_not_zero_issuers(self):
        folder = copy_dataset(self)
        path = folder / "ISSUERS.tsv"
        path.write_text(path.read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")
        conn = fresh_store()
        run_id, reports = run_discover(conn, [make(dataset=folder)])
        self.assertIn("EmptyTable", reports["formd"].error)
        self.assertEqual(conn.execute("SELECT status FROM job_run WHERE run_id = ?", (run_id,)).fetchone()[0], "failed")

    def test_since_reaches_the_source(self):
        conn = fresh_store()
        run_discover(conn, [LcaSource(FILES)])
        _, reports = run_discover(conn, [make(FakeClient(routes()))], since="2026-09-10")
        self.assertEqual(reports["formd"].candidates, 4)

    def test_an_explicit_name_lookup_is_not_replaced_by_the_store_one(self):
        conn = fresh_store()
        run_discover(conn, [LcaSource(FILES)])
        client = FakeClient(routes())
        run_discover(conn, [make(client, name_lookup=lambda key: False)])
        self.assertEqual(client.requests, [])

    def test_an_issuer_that_attaches_strongly_can_still_raise_one_item_for_another_company_with_its_name(self):
        """The one way Form D raises a merge item: it attaches by FEIN to A, but its name is already B's (the weak
        same-name hit and the alias collision are one pair, so one item)."""
        conn = fresh_store()
        run_discover(conn, [LcaSource(FILES)])
        a = conn.execute("SELECT company_id FROM company WHERE name = 'Nimbus Robotics, Inc.'").fetchone()[0]
        b = seed_company(conn, "Nimbus Robotics Holdings Inc")
        add_alias(conn, b, "Nimbus Robotics Holdings Inc", "name")
        _, reports = run_discover(conn, [make(FakeClient(routes()))])
        self.assertEqual(reports["formd"].items_raised, 1)
        (kind, reason) = conn.execute("SELECT kind, reason FROM review_item").fetchone()
        self.assertEqual(kind, "merge_candidate")
        self.assertIn(f"Nimbus Robotics, Inc. (#{a}) and Nimbus Robotics Holdings Inc (#{b})", reason)
        self.assertIn(f"formd:{NIMBUS}", reason)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM evidence WHERE fact = 'funding' AND company_id = ?",
                                      (a,)).fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM evidence WHERE fact = 'funding' AND company_id = ?",
                                      (b,)).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
