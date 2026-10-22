"""Adapter plumbing shared by every ATS (issue #21, Task 4)."""
import itertools
import unittest

from outreach.crawl.ats._common import d10, fetch_list, finish_list, json_body, read_error, safe_token
from outreach.crawl.base import JobRef, ReadResult, Validators, ids_fingerprint
from outreach.crawl.polite import HostTripped
from outreach.net import FetchError, Response
from tests.fakes import FakeClient, json_response, status_response, text_response
from tests.storetest import fresh_store, seed_company

URL = "https://example.test/list"


def job(native_id, title="Engineer"):
    return JobRef(native_id, title, f"https://example.test/{native_id}", "Remote", None, None, None)


class SafeTokenTests(unittest.TestCase):
    def test_accepts_the_slug_grammar(self):
        self.assertTrue(safe_token("greenhouse", "acme-inc_2.0"))

    def test_refuses_anything_else(self):
        for token in ("a/b", "a b", "", "Acme", "a%20b"):
            self.assertFalse(safe_token("greenhouse", token), token)

    def test_an_unknown_ats_is_refused(self):
        self.assertFalse(safe_token("nope", "acme"))


class ReadErrorTests(unittest.TestCase):
    def test_an_error_result(self):
        r = read_error("boom", status=500)
        self.assertEqual((r.result, r.jobs, r.validators, r.http_status, r.error),
                         ("error", (), Validators(), 500, "boom"))
        self.assertIsNone(read_error("x").http_status)


class FetchListTests(unittest.TestCase):
    def test_a_200_comes_back_as_the_response(self):
        client = FakeClient({URL: json_response({"a": 1}, etag='"e1"')})
        got = fetch_list(client, URL, Validators())
        self.assertIsInstance(got, Response)
        self.assertEqual(got.status, 200)

    def test_a_304_is_unchanged_with_the_same_validators(self):
        client = FakeClient({URL: status_response(304)})
        v = Validators(etag='"e1"', ids_fingerprint="abc")
        got = fetch_list(client, URL, v, use_etag=True)
        self.assertEqual((got.result, got.jobs, got.validators, got.http_status), ("unchanged", (), v, 304))
        self.assertEqual(client.calls[0][2]["If-None-Match"], '"e1"')

    def test_no_if_none_match_unless_asked_or_known(self):
        for use_etag, etag in ((False, '"e1"'), (True, "")):
            client = FakeClient({URL: json_response({})})
            fetch_list(client, URL, Validators(etag=etag), use_etag=use_etag)
            self.assertNotIn("If-None-Match", client.calls[0][2])

    def test_a_304_nobody_asked_for_is_an_error(self):
        # No conditional header went out, so a 304 is not "nothing changed": it must not hide the board.
        client = FakeClient({URL: status_response(304)})
        got = fetch_list(client, URL, Validators())
        self.assertEqual((got.result, got.http_status), ("error", 304))
        self.assertIn("no If-None-Match", got.error)
        self.assertNotIn("redirect", got.error)

    def test_other_statuses_are_errors_with_the_status(self):
        for status in (302, 403, 404, 429, 500, 204, 201):
            client = FakeClient({URL: status_response(status)})
            got = fetch_list(client, URL, Validators())
            self.assertIsInstance(got, ReadResult, status)
            self.assertEqual((got.result, got.http_status, got.jobs), ("error", status, ()), status)
            self.assertIn(str(status), got.error)

    def test_a_fetch_error_is_an_error_with_no_status(self):
        client = FakeClient({URL: FetchError("timeout", "slow")})
        got = fetch_list(client, URL, Validators())
        self.assertEqual((got.result, got.http_status), ("error", None))
        self.assertIn("timeout", got.error)

    def test_a_tripped_host_is_not_swallowed(self):
        client = FakeClient({URL: HostTripped("example.test")})
        with self.assertRaises(HostTripped):
            fetch_list(client, URL, Validators())

    def test_a_post_sends_the_body_and_headers(self):
        client = FakeClient({("POST", URL): json_response({})})
        got = fetch_list(client, URL, Validators(etag='"e"'), headers={"Accept": "application/json"},
                         use_etag=True, method="POST", body=b'{"q": 1}')
        self.assertIsInstance(got, Response)
        method, _url, headers, body = client.calls[0]
        self.assertEqual((method, body), ("POST", b'{"q": 1}'))
        self.assertEqual(headers, {"Accept": "application/json", "If-None-Match": '"e"'})


class JsonBodyTests(unittest.TestCase):
    def test_json_comes_back_parsed(self):
        self.assertEqual(json_body(json_response({"jobs": []})), {"jobs": []})
        self.assertIsNone(json_body(json_response(None)))

    def test_an_html_page_is_an_error(self):
        got = json_body(text_response("<html><title>Just a moment...</title></html>"))
        self.assertIsInstance(got, ReadResult)
        self.assertEqual((got.result, got.http_status), ("error", 200))

    def test_bytes_that_are_not_utf8_are_an_error(self):
        got = json_body(Response(200, URL, b"\xff\xfe\x00", {}))
        self.assertEqual(got.result, "error")


class FinishListTests(unittest.TestCase):
    def test_a_repeated_id_is_kept_once_first_wins(self):
        r = finish_list([job("1", "first"), job("2"), job("1", "second")], json_response({}), Validators())
        self.assertEqual([(j.native_id, j.title) for j in r.jobs], [("1", "first"), ("2", "Engineer")])
        self.assertEqual(r.validators.ids_fingerprint, ids_fingerprint(["1", "2"]))

    def test_no_jobs_is_empty_with_no_validators(self):
        r = finish_list([], json_response({}, etag='"e"'), Validators(etag='"old"', ids_fingerprint="old"),
                        use_etag=True)
        self.assertEqual((r.result, r.jobs, r.validators), ("empty", (), Validators()))

    def test_the_same_ids_are_unchanged_for_a_fingerprint_adapter(self):
        seen = Validators(ids_fingerprint=ids_fingerprint(["1", "2"]))
        r = finish_list([job("2"), job("1")], json_response({}), seen)
        self.assertEqual((r.result, r.jobs, r.validators), ("unchanged", (), seen))

    def test_one_more_id_is_ok(self):
        seen = Validators(ids_fingerprint=ids_fingerprint(["1", "2"]))
        r = finish_list([job("1"), job("2"), job("3")], json_response({}), seen)
        self.assertEqual(r.result, "ok")
        self.assertEqual(r.validators.ids_fingerprint, ids_fingerprint(["1", "2", "3"]))

    def test_an_etag_adapter_is_ok_even_when_the_ids_match(self):
        # The server sent a 200, so it says the board changed (a title can change under the same ids).
        seen = Validators(etag='"old"', ids_fingerprint=ids_fingerprint(["1"]))
        r = finish_list([job("1")], json_response({}, etag='"new"'), seen, use_etag=True)
        self.assertEqual(r.result, "ok")
        self.assertEqual(r.validators, Validators(etag='"new"', ids_fingerprint=ids_fingerprint(["1"])))

    def test_a_fingerprint_adapter_keeps_no_etag(self):
        r = finish_list([job("1")], json_response({}, etag='"e"'), Validators())
        self.assertEqual(r.validators.etag, "")

    def test_fingerprint_ids_replace_the_native_ids(self):
        r = finish_list([job("1")], json_response({}), Validators(), fingerprint_ids=["a", "b"])
        self.assertEqual(r.validators.ids_fingerprint, ids_fingerprint(["a", "b"]))

    def test_the_board_name_is_copied(self):
        r = finish_list([job("1")], json_response({}), Validators(), board_name="Acme")
        self.assertEqual(r.board_name, "Acme")
        self.assertEqual(finish_list([job("1")], json_response({}), Validators()).board_name, "")


class D10Tests(unittest.TestCase):
    def test_an_iso_prefix(self):
        self.assertEqual(d10("2026-09-15T10:21:33-04:00"), "2026-09-15")

    def test_epoch_seconds_and_milliseconds(self):
        self.assertEqual(d10(1760000000), "2025-10-09")
        self.assertEqual(d10(1760000000000), "2025-10-09")
        self.assertEqual(d10("1760000000000"), "2025-10-09")

    def test_nothing_usable_is_none(self):
        for value in (None, "", 0, "soon", "99999999999999999999999", True, [], {}):
            self.assertIsNone(d10(value), repr(value))

    def test_an_impossible_date_is_none(self):
        for value in ("2026-02-30", "2026-13-45", "2026-00-10", "0000-01-01", "2026-02-30T10:00:00Z"):
            self.assertIsNone(d10(value), value)

    def test_a_number_that_is_not_an_epoch_is_none(self):
        # Only 10 digits (seconds) or 13 (milliseconds) are an epoch; a year or a compact date is not.
        for value in ("2026", "20261021", "202610211", "17600000000", "176000000000000", 2026, 20261021, -1760000000):
            self.assertIsNone(d10(value), repr(value))

    def test_a_float_epoch(self):
        self.assertEqual(d10(1760000000.5), "2025-10-09")
        self.assertEqual(d10(1760000000000.0), "2025-10-09")
        self.assertIsNone(d10(float("nan")))
        self.assertIsNone(d10(float("inf")))

    def test_every_output_is_a_date_the_store_accepts(self):
        conn = fresh_store()
        company = seed_company(conn)
        counter = itertools.count()
        for value in ("2026-09-15T10:21:33-04:00", "2026-09-15", 1760000000, 1760000000000, "1760000000",
                      1760000000.5, "2026-02-28", "2024-02-29"):
            out = d10(value)
            self.assertIsNotNone(out, repr(value))
            conn.execute(
                "INSERT INTO posting(company_id, source_key, title, posted_on, first_seen_at, last_seen_at) "
                "VALUES (?, ?, 'x', ?, '2026-10-22T00:00:00Z', '2026-10-22T00:00:00Z')",
                (company, f"url:d10-{next(counter)}", out))
