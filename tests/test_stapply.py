"""The stapply directory (#31 Task 14): name keys, board tokens, lookups and the refresh."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from outreach.net import USER_AGENT, FetchError, Response
from outreach.sources.boards import BoardRef
from outreach.sources.stapply import (MANIFEST_URL, STAPPLY_ATS, BoardHit, StapplyDirectory, board_match_key,
                                      board_token)
from tests.fakes import FakeClient

FIXTURES = Path(__file__).parent / "fixtures" / "stapply"


def folder_with(**lists: str) -> Path:
    """A temp stapply folder: a manifest and one CSV per keyword (`name,slug,url` rows given as text)."""
    folder = Path(tempfile.mkdtemp(prefix="outreach-stapply-test-"))
    by = {}
    for name, rows in lists.items():
        body = ("name,slug,url\n" + rows).encode()
        (folder / f"{name}.csv").write_bytes(body)
        by[name] = {"csv": f"https://example.test/{name}.csv", "sha256": hashlib.sha256(body).hexdigest()}
    (folder / "manifest.json").write_text(json.dumps({"generated_at": "2026-10-20T00:00:00+00:00",
                                                      "by_ats_companies": by}))
    return folder


class MatchKeyTest(unittest.TestCase):
    def test_board_match_key_matches_the_research_normalization(self):
        self.assertEqual(board_match_key("Stryder Corporation"), "stryder")
        self.assertEqual(board_match_key("Nimbus Robotics, Inc."), "nimbusrobotics")
        self.assertEqual(board_match_key("Orchard Labs LLC dba Orchard"), "orchardlabs")  # the dba tail is dropped
        self.assertEqual(board_match_key("Acme (formerly Zed) Holdings Group"), "acme")
        self.assertEqual(board_match_key("The L L C Company USA"), "")
        self.assertEqual(board_match_key("IBM Inc"), "ibm")  # a short key is still a key; it never matches a row


class TokenTest(unittest.TestCase):
    def test_board_token_formats(self):
        self.assertEqual(board_token("workday", "23andme/23", "https://23andme.wd5.myworkdayjobs.com/23"),
                         "23andme.wd5/23")
        self.assertEqual(board_token("workday", "x/y", "https://Riverside.wd5.myworkdayjobs.com/en-US/External"),
                         "riverside.wd5/External")
        self.assertEqual(board_token("oracle", "ebwg", "https://EBWG.fa.us2.oraclecloud.com/hcmUI/CandidateExperience"
                                                       "/en/sites/CX"), "ebwg.fa.us2.oraclecloud.com/CX")
        self.assertEqual(board_token("icims", "142designgroup", "https://careers-142designgroup.icims.com"),
                         "careers-142designgroup.icims.com")
        self.assertEqual(board_token("greenhouse", "NimbusRobotics", "https://boards.greenhouse.io/nimbusrobotics"),
                         "nimbusrobotics")
        self.assertIsNone(board_token("cornerstone", "https://acme.csod.com/ux/ats/careersite/1/home?c=acme",
                                      "https://acme.csod.com/ux/ats/careersite/1/home?c=acme"))
        self.assertIsNone(board_token("workday", "x", "https://example.com/not-a-workday-url"))
        self.assertIsNone(board_token("oracle", "x", "https://acme.oraclecloud.com/hcmUI/sites"))
        self.assertIsNone(board_token("icims", "x", "https://careers.example.com"))

    def test_the_stapply_list_names_map_to_schema_ats(self):
        self.assertEqual(STAPPLY_ATS["oracle"], "oracle_orc")
        self.assertEqual(STAPPLY_ATS["greenhouse"], "greenhouse")
        for absent in ("jobvite", "adp", "ukg", "taleo", "paylocity"):
            self.assertNotIn(absent, STAPPLY_ATS)


class LookupTest(unittest.TestCase):
    def setUp(self):
        self.directory = StapplyDirectory.load(FIXTURES)

    def test_exact_name_match_and_slug_match_and_host_match(self):
        (hit,) = self.directory.lookup(["Nimbus Robotics, Inc."], None)
        self.assertEqual(hit, BoardHit(BoardRef("greenhouse", "nimbusrobotics"),
                                       "https://boards.greenhouse.io/nimbusrobotics", "name"))
        (hit,) = self.directory.lookup(["Granite Peak Inc"], None)  # the row's slug "granite-peak", not its name
        self.assertEqual((hit.ref, hit.matched_on), (BoardRef("lever", "granite-peak"), "slug"))
        (hit,) = self.directory.lookup(["Unrelated Name"], "plumehealth.io")  # the row's URL host is the domain
        self.assertEqual((hit.ref, hit.matched_on), (BoardRef("lever", "plumehealth"), "host"))
        (hit,) = self.directory.lookup([], "jobs.plumehealth.io")  # a subdomain reduces to its registrable domain
        self.assertEqual(hit.matched_on, "host")
        self.assertEqual(self.directory.lookup(["Nimbus"], None), [])  # exact only
        self.assertEqual(self.directory.lookup(["Nimbus Robotics"], "other.com")[0].matched_on, "name")

    def test_a_row_found_two_ways_is_reported_once_by_its_strongest_way(self):
        (hit,) = self.directory.lookup(["Plume Health"], "plumehealth.io")
        self.assertEqual(hit.matched_on, "host")

    def test_a_host_must_equal_the_domain_or_be_a_subdomain_of_it(self):
        self.assertEqual(self.directory.lookup([], "ealth.io"), [])
        self.assertEqual(self.directory.lookup([], "lever.co"), [])  # a platform host is never a company's own

    def test_short_keys_never_match(self):
        self.assertEqual(self.directory.lookup(["Ab"], None), [])
        self.assertEqual(self.directory.lookup(["AB Inc"], None), [])

    def test_unsupported_ats_lists_are_ignored(self):
        hits = self.directory.lookup(["Orchard"], None)
        self.assertEqual([(h.ref.ats, h.ref.token) for h in hits], [("ashby", "orchard")])

    def test_a_row_with_a_bad_token_is_skipped_and_counted(self):
        self.assertEqual(self.directory.lookup(["Quartz Cat"], None), [])
        self.assertEqual(self.directory.skipped_rows, 1)

    def test_more_than_three_hits_returns_none(self):
        rows = {name: "Dupe Works,dupeworks,https://example.test/dupeworks\n"
                for name in ("greenhouse", "lever", "ashby", "recruitee")}
        directory = StapplyDirectory.load(folder_with(**rows))
        self.assertEqual(directory.lookup(["Dupe Works"], None), [])
        three = StapplyDirectory.load(folder_with(**{k: v for k, v in rows.items() if k != "recruitee"}))
        self.assertEqual(len(three.lookup(["Dupe Works"], None)), 3)

    def test_the_same_board_in_two_rows_is_one_hit(self):
        folder = folder_with(greenhouse="Dupe Works,dupeworks,u\nDupe Works Inc,dupeworks,u\n")
        self.assertEqual(len(StapplyDirectory.load(folder).lookup(["Dupe Works"], None)), 1)

    def test_version_comes_from_the_manifest(self):
        self.assertEqual(self.directory.version, "stapply-1-2026-10-20T14:30:05.124532+00:00")

    def test_a_folder_with_no_lists_or_no_manifest_raises(self):
        empty = Path(tempfile.mkdtemp(prefix="outreach-stapply-test-"))
        with self.assertRaises(FileNotFoundError):
            StapplyDirectory.load(empty)
        (empty / "manifest.json").write_text(json.dumps({"generated_at": "x", "by_ats_companies": {}}))
        with self.assertRaises(ValueError):
            StapplyDirectory.load(empty)  # error is not "no matches"
        only_ignored = folder_with(jobvite="A Name,a,u\n")
        with self.assertRaises(ValueError):
            StapplyDirectory.load(only_ignored)


def manifest_body(files: dict[str, bytes], *, wrong: dict[str, str] | None = None) -> bytes:
    by = {name: {"csv": f"https://storage.stapply.ai/jobhive/v1/{name}/companies.csv",
                 "sha256": (wrong or {}).get(name, hashlib.sha256(body).hexdigest())}
          for name, body in files.items()}
    return json.dumps({"generated_at": "2026-10-21T00:00:00+00:00", "by_ats_companies": by}).encode()


def url_of(name: str) -> str:
    return f"https://storage.stapply.ai/jobhive/v1/{name}/companies.csv"


class RefreshTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="outreach-stapply-test-"))
        self.files = {"greenhouse": b"name,slug,url\nA One,aone,u\n", "ashby": b"name,slug,url\nB Two,btwo,u\n",
                      "lever": b"name,slug,url\nC Three,cthree,u\n"}

    def test_refresh_downloads_only_changed_files_and_checks_sha256(self):
        (self.folder / "lever.csv").write_bytes(self.files["lever"])  # already current
        (self.folder / "ashby.csv").write_bytes(b"old")  # stale
        client = FakeClient({MANIFEST_URL: manifest_body(self.files),
                             **{url_of(n): self.files[n] for n in ("greenhouse", "ashby")}})
        updated = StapplyDirectory.refresh(client, self.folder)
        self.assertEqual(sorted(updated), ["ashby.csv", "greenhouse.csv"])
        for name in ("greenhouse", "ashby", "lever"):
            self.assertEqual((self.folder / f"{name}.csv").read_bytes(), self.files[name])
        self.assertNotIn(url_of("lever"), client.requests)
        self.assertEqual(client.unrouted, [])
        self.assertEqual(json.loads((self.folder / "manifest.json").read_text())["generated_at"],
                         "2026-10-21T00:00:00+00:00")
        self.assertEqual(StapplyDirectory.refresh(FakeClient({MANIFEST_URL: manifest_body(self.files)}), self.folder),
                         [])  # a second refresh downloads nothing

    def test_a_corrupted_body_raises_and_leaves_the_old_file_and_manifest(self):
        (self.folder / "ashby.csv").write_bytes(b"old")
        (self.folder / "manifest.json").write_text('{"generated_at": "old"}')
        client = FakeClient({MANIFEST_URL: manifest_body({"ashby": self.files["ashby"]}),
                             url_of("ashby"): b"name,slug,url\ncorrupted\n"})
        with self.assertRaises(ValueError):
            StapplyDirectory.refresh(client, self.folder)
        self.assertEqual((self.folder / "ashby.csv").read_bytes(), b"old")
        self.assertEqual(json.loads((self.folder / "manifest.json").read_text()), {"generated_at": "old"})
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["ashby.csv", "manifest.json"])

    def test_a_failed_manifest_or_download_raises(self):
        with self.assertRaises(FetchError):
            StapplyDirectory.refresh(FakeClient({MANIFEST_URL: FetchError("timeout", "slow")}), self.folder)
        with self.assertRaises(ValueError):  # an HTTP error is not a manifest
            StapplyDirectory.refresh(FakeClient({MANIFEST_URL: Response(503, MANIFEST_URL, b"", {})}), self.folder)
        with self.assertRaises(ValueError):
            StapplyDirectory.refresh(FakeClient({MANIFEST_URL: b"<html>"}), self.folder)
        with self.assertRaises(ValueError):
            StapplyDirectory.refresh(FakeClient({MANIFEST_URL: b'{"generated_at": "x"}'}), self.folder)
        client = FakeClient({MANIFEST_URL: manifest_body({"ashby": self.files["ashby"]}),
                             url_of("ashby"): Response(500, url_of("ashby"), b"", {})})
        with self.assertRaises(FetchError):
            StapplyDirectory.refresh(client, self.folder)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_a_list_name_that_is_not_a_plain_name_is_never_a_file_path(self):
        body = manifest_body({"../evil": b"x", "ashby": self.files["ashby"]})
        client = FakeClient({MANIFEST_URL: body, url_of("ashby"): self.files["ashby"]})
        self.assertEqual(StapplyDirectory.refresh(client, self.folder), ["ashby.csv"])
        self.assertFalse((self.folder.parent / "evil.csv").exists())

    def test_refresh_uses_a_declared_user_agent(self):
        client = FakeClient({MANIFEST_URL: manifest_body({"ashby": self.files["ashby"]}),
                             url_of("ashby"): self.files["ashby"]})
        StapplyDirectory.refresh(client, self.folder)
        self.assertEqual(len(client.calls), 2)
        for _method, _url, headers, _body in client.calls:
            self.assertEqual(headers.get("User-Agent"), USER_AGENT)
            self.assertNotIn("@", USER_AGENT)


if __name__ == "__main__":
    unittest.main()
