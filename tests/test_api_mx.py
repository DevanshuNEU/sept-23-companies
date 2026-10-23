"""The mail server lookup (#11 Task 17): DNS over HTTPS, the `mx_check` cache, and no network in any test.

Resolvers are injected; the DoH request itself is checked against a fake opener. `tests/__init__` also refuses any
real network call, so a resolver that escaped its fake would fail the test loudly.
"""
import io
import json
import sqlite3
import urllib.parse
import urllib.request
from unittest import mock

from outreach.api import mx
from outreach.api.app import create_app
from outreach.api.db import connect_for
from outreach.store import connect
from tests.apitest import ApiCase

NOW = "2026-11-10T15:14:00Z"


class Recorder:
    """A resolver that answers from a script and records each domain it was asked."""

    def __init__(self, answer=None, error: BaseException | None = None):
        self.answer, self.error, self.asked = answer, error, []

    def __call__(self, domain: str):
        self.asked.append(domain)
        if self.error is not None:
            raise self.error
        return self.answer


class FakeOpener:
    """Stands in for urllib's opener: records each request and its timeout, answers with a fixed body or error."""

    def __init__(self, body: bytes | None = None, error: BaseException | None = None):
        self.body, self.error, self.requests = body, error, []

    def open(self, request, timeout=None):
        self.requests.append((request, timeout))
        if self.error is not None:
            raise self.error
        return io.BytesIO(self.body)


def doh_body(status: int = 0, answers: list[tuple[int, str]] | None = None) -> bytes:
    payload = {"Status": status}
    if answers is not None:
        payload["Answer"] = [{"name": "acme.example.", "type": t, "TTL": 300, "data": d} for t, d in answers]
    return json.dumps(payload).encode()


class CheckDomain(ApiCase):
    def test_first_resolver_none_second_ok_gives_ok(self):
        first, second = Recorder(None), Recorder(["10 mx1.acme.example."])
        self.assertEqual(mx.check_domain("acme.example", [first, second]), "ok")
        self.assertEqual((first.asked, second.asked), (["acme.example"], ["acme.example"]))

    def test_a_failing_first_resolver_falls_through_to_the_second(self):
        for error in (OSError("timed out"), TimeoutError(), ValueError("bad json"), RuntimeError("boom")):
            with self.subTest(error=error):
                self.assertEqual(mx.check_domain("acme.example", [Recorder(error=error),
                                                                  Recorder(["5 mx.acme.example."])]), "ok")

    def test_empty_answer_is_none_without_asking_the_second(self):
        first, second = Recorder([]), Recorder(["10 mx1.acme.example."])
        self.assertEqual(mx.check_domain("acme.example", [first, second]), "none")
        self.assertEqual(second.asked, [])

    def test_null_mx_is_none(self):
        for answer in (["0 ."], ["0 ."] * 2, ["10 ."]):
            with self.subTest(answer=answer):
                self.assertEqual(mx.check_domain("acme.example", [Recorder(answer)]), "none")
        self.assertEqual(mx.check_domain("acme.example", [Recorder(["0 .", "10 mx.acme.example."])]), "ok")

    def test_all_failing_is_unknown(self):
        resolvers = [Recorder(None), Recorder(error=OSError("down")), Recorder(["garbage that is not mx"])]
        self.assertEqual(mx.check_domain("acme.example", resolvers), "unknown")
        self.assertEqual(mx.check_domain("acme.example", []), "unknown")

    def test_an_invalid_domain_is_unknown_and_asks_nobody(self):
        for domain in ("", "   ", "no spaces.com", "acme", "a..b.com", "x" * 300 + ".com", "acme.example/x"):
            with self.subTest(domain=domain):
                r = Recorder(["10 mx.acme.example."])
                self.assertEqual(mx.check_domain(domain, [r]), "unknown")
                self.assertEqual(r.asked, [])

    def test_the_domain_is_normalised_before_it_is_asked(self):
        r = Recorder(["10 mx.acme.example."])
        mx.check_domain("  Acme.Example. ", [r])
        self.assertEqual(r.asked, ["acme.example"])


class DohRequest(ApiCase):
    def test_doh_request_carries_only_the_name_and_the_type(self):
        for base in mx.DOH_ENDPOINTS:
            with self.subTest(base=base):
                opener = FakeOpener(doh_body(0, [(15, "10 mx.acme.example.")]))
                self.assertEqual(mx.doh(base, opener=opener)("acme.example"), ["10 mx.acme.example."])
                (request, timeout), = opener.requests
                self.assertEqual(timeout, 5)
                parts = urllib.parse.urlsplit(request.full_url)
                self.assertEqual(f"{parts.scheme}://{parts.netloc}{parts.path}", base)
                self.assertEqual(urllib.parse.parse_qs(parts.query, keep_blank_values=True),
                                 {"name": ["acme.example"], "type": ["MX"]})
                self.assertEqual(request.get_method(), "GET")
                self.assertIsNone(request.data)
                self.assertEqual(dict(request.header_items()), {"Accept": "application/dns-json"})

    def test_the_default_opener_keeps_no_cookies_and_names_nobody(self):
        opener = mx._opener()
        self.assertFalse(any(isinstance(h, urllib.request.HTTPCookieProcessor) for h in opener.handlers))
        headers = dict(opener.addheaders)
        self.assertEqual(set(headers), {"User-agent"})
        self.assertNotIn("@", headers["User-agent"])
        self.assertNotIn("devanshu", headers["User-agent"].lower())

    def test_endpoints_are_cloudflare_then_google(self):
        self.assertEqual(mx.DOH_ENDPOINTS, ("https://cloudflare-dns.com/dns-query", "https://dns.google/resolve"))
        opener = FakeOpener(error=OSError("down"))
        with mock.patch.object(mx, "_opener", return_value=opener):
            self.assertEqual(mx.check_domain("acme.example"), "unknown")
        self.assertEqual([urllib.parse.urlsplit(r.full_url).netloc for r, _ in opener.requests],
                         ["cloudflare-dns.com", "dns.google"])

    def test_doh_answers(self):
        cases = [
            (doh_body(0, [(5, "alias.acme.example."), (15, "20 mx2.acme.example."), (15, "10 mx1.acme.example.")]),
             ["20 mx2.acme.example.", "10 mx1.acme.example."]),
            (doh_body(0, []), []),
            (doh_body(0, None), []),
            (doh_body(3, None), []),   # NXDOMAIN: the domain does not exist, so it has no mail server
        ]
        for body, expected in cases:
            with self.subTest(body=body):
                self.assertEqual(mx.doh(mx.DOH_ENDPOINTS[0], opener=FakeOpener(body))("acme.example"), expected)
        for body in (doh_body(2, None), doh_body(5, None), b"not json", b"[]", b'{"Status": "0"}',
                     json.dumps({"Status": 0, "Answer": "x"}).encode(),
                     json.dumps({"Status": 0, "Answer": [{"type": 15}]}).encode()):
            with self.subTest(body=body):
                self.assertIsNone(mx.doh(mx.DOH_ENDPOINTS[0], opener=FakeOpener(body))("acme.example"))

    def test_a_transport_error_is_no_answer(self):
        for error in (OSError("refused"), TimeoutError(), urllib.error.URLError("dns")):
            with self.subTest(error=error):
                self.assertIsNone(mx.doh(mx.DOH_ENDPOINTS[0], opener=FakeOpener(error=error))("acme.example"))


class Ensure(ApiCase):
    def setUp(self):
        super().setUp()
        self.opened: list[sqlite3.Connection] = []

    def factory(self, settings):
        conn = connect_for(settings)
        self.opened.append(conn)
        return conn

    def stored(self, domain: str = "acme.example"):
        row = self.conn.execute("SELECT status, checked_at FROM mx_check WHERE domain = ?", (domain,)).fetchone()
        return None if row is None else tuple(row)

    def put(self, status: str, at: str, domain: str = "acme.example") -> None:
        self.conn.execute("INSERT INTO mx_check(domain, status, checked_at) VALUES (?,?,?)", (domain, status, at))

    def test_ensure_stores_ok_and_none(self):
        self.assertEqual(mx.ensure(self.settings, self.factory, "acme.example", NOW,
                                   resolvers=[Recorder(["10 mx.acme.example."])]), "ok")
        self.assertEqual(self.stored(), ("ok", NOW))
        self.assertEqual(mx.ensure(self.settings, self.factory, "Gone.Example", NOW, resolvers=[Recorder([])]),
                         "none")
        self.assertEqual(self.stored("gone.example"), ("none", NOW))
        self.assertTrue(all(_closed(c) for c in self.opened))

    def test_all_failing_is_unknown_and_not_cached(self):
        self.assertEqual(mx.ensure(self.settings, self.factory, "acme.example", NOW,
                                   resolvers=[Recorder(None), Recorder(error=OSError("down"))]), "unknown")
        self.assertIsNone(self.stored())
        with self.subTest("a stale row is left as it was, not overwritten with unknown"):
            self.put("none", "2026-11-01T00:00:00Z")
            self.assertEqual(mx.ensure(self.settings, self.factory, "acme.example", NOW,
                                       resolvers=[Recorder(None)]), "unknown")
            self.assertEqual(self.stored(), ("none", "2026-11-01T00:00:00Z"))

    def test_cache_hit_within_24_hours_asks_nobody(self):
        self.put("none", "2026-11-09T15:14:01Z")
        r = Recorder(["10 mx.acme.example."])
        self.assertEqual(mx.ensure(self.settings, self.factory, "acme.example", NOW, resolvers=[r]), "none")
        self.assertEqual(r.asked, [])

    def test_a_row_24_hours_old_is_asked_again_and_refreshed(self):
        self.put("none", "2026-11-09T15:14:00Z")
        r = Recorder(["10 mx.acme.example."])
        self.assertEqual(mx.ensure(self.settings, self.factory, "acme.example", NOW, resolvers=[r]), "ok")
        self.assertEqual(r.asked, ["acme.example"])
        self.assertEqual(self.stored(), ("ok", NOW))

    def test_ensure_runs_outside_a_transaction(self):
        settings = self.settings

        def resolver(domain):
            for conn in self.opened:  # every connection ensure opened is closed or idle while it waits on DNS
                self.assertTrue(_closed(conn) or not conn.in_transaction)
            own = connect(settings.db_path)
            try:
                own.execute("PRAGMA busy_timeout = 0")
                own.execute("BEGIN IMMEDIATE")  # fails at once if anyone holds a write transaction
                self.assertTrue(own.in_transaction)
                own.execute("ROLLBACK")
                self.assertFalse(own.in_transaction)
            finally:
                own.close()
            return ["10 mx.acme.example."]

        self.assertEqual(mx.ensure(settings, self.factory, "acme.example", NOW, resolvers=[resolver]), "ok")
        self.assertGreaterEqual(len(self.opened), 1)
        self.assertEqual(self.stored(), ("ok", NOW))

    def test_an_invalid_domain_is_unknown_without_touching_the_store(self):
        r = Recorder(["10 mx.acme.example."])
        self.assertEqual(mx.ensure(self.settings, self.factory, "not a domain", NOW, resolvers=[r]), "unknown")
        self.assertEqual((r.asked, self.opened), ([], []))

    def test_a_busy_store_still_answers_and_stores_nothing(self):
        holder = connect(self.settings.db_path)
        self.addCleanup(holder.close)
        holder.execute("BEGIN IMMEDIATE")
        try:
            self.assertEqual(mx.ensure(self.settings, self.factory, "acme.example", NOW,
                                       resolvers=[Recorder(["10 mx.acme.example."])]), "ok")
        finally:
            holder.execute("ROLLBACK")
        self.assertIsNone(self.stored())

    def test_app_state_mx_resolvers_replaces_the_network(self):
        r = Recorder(["10 mx.acme.example."])
        app = create_app(self.settings, clock=lambda: NOW, mx_resolvers=[r])
        self.assertEqual(mx.ensure(app.state.settings, connect_for, "acme.example", app.state.clock(),
                                   resolvers=app.state.mx_resolvers), "ok")
        self.assertEqual(r.asked, ["acme.example"])


def _closed(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return True
    return False
