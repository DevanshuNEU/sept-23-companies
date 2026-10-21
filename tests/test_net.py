"""Task 2 of #30: the one HTTP layer (outreach/net.py) and the one fake (tests/fakes.py).

Nothing here touches the network: the server tests bind 127.0.0.1 on an ephemeral port.
"""
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from outreach.net import USER_AGENT, FetchError, Response, SpacedClient, UrllibClient
from tests.fakes import FakeClient, UnexpectedRequest, json_response, status_response, text_response


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status, body=b"", headers=()):
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/304":
            self._send(304)
        elif self.path == "/redirect":
            self._send(302, b"", [("Location", "/final")])
        elif self.path == "/loop":
            self._send(302, b"", [("Location", "/loop")])
        elif self.path == "/final":
            self._send(200, b"arrived")
        elif self.path == "/big":
            self._send(200, b"x" * 5000)
        elif self.path == "/404":
            self._send(404, b"nope")
        elif self.path == "/slow":
            time.sleep(1.0)
            try:
                self._send(200, b"late")
            except OSError:
                pass
        elif self.path == "/echo-ua":
            self._send(200, (self.headers.get("User-Agent") or "").encode())
        elif self.path == "/xhost":
            port = self.server.server_address[1]
            self._send(302, b"", [("Location", f"http://localhost:{port}/echo-x")])
        elif self.path == "/xhost-ua":
            port = self.server.server_address[1]
            self._send(302, b"", [("Location", f"http://localhost:{port}/echo-ua")])
        elif self.path == "/samehost":
            self._send(302, b"", [("Location", "/echo-x")])
        elif self.path == "/to-file":
            self._send(302, b"", [("Location", "file:///etc/hosts")])
        elif self.path == "/echo-x":
            self._send(200, f"{self.headers.get('X-Token') or ''}|{self.headers.get('User-Agent') or ''}".encode())
        elif self.path == "/cookie":
            self._send(200, b"", [("Set-Cookie", "k=v; Path=/")])
        elif self.path == "/needs-cookie":
            self._send(200, (self.headers.get("Cookie") or "").encode())
        else:
            self._send(200, b"ok")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self._send(200, self.rfile.read(n))


class _Server:
    def __enter__(self):
        class S(ThreadingHTTPServer):
            daemon_threads = True
            block_on_close = False

        self.httpd = S(("127.0.0.1", 0), _Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def _dead_port_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}/"


class UrllibClientTests(unittest.TestCase):
    def test_urllib_client_returns_304_as_a_response(self):
        with _Server() as s:
            r = UrllibClient().get(s.base + "/304")
        self.assertEqual(r.status, 304)

    def test_urllib_client_returns_404_as_a_response(self):
        with _Server() as s:
            r = UrllibClient().get(s.base + "/404")
        self.assertEqual((r.status, r.body), (404, b"nope"))

    def test_urllib_client_gets_ok_with_lower_case_headers(self):
        with _Server() as s:
            r = UrllibClient().get(s.base + "/ok")
        self.assertEqual((r.status, r.text()), (200, "ok"))
        self.assertIn("content-length", r.headers)
        self.assertTrue(r.url.endswith("/ok"))

    def test_urllib_client_does_not_follow_a_redirect_by_default(self):
        with _Server() as s:
            r = UrllibClient().get(s.base + "/redirect")
        self.assertEqual(r.status, 302)
        self.assertEqual(r.headers["location"], "/final")

    def test_urllib_client_follows_a_redirect_when_asked(self):
        with _Server() as s:
            r = UrllibClient().get(s.base + "/redirect", follow_redirects=True)
            self.assertEqual((r.status, r.text()), (200, "arrived"))
            self.assertTrue(r.url.endswith("/final"))

    def test_a_redirect_hop_to_a_file_url_raises_fetch_error(self):
        with _Server() as s:
            with self.assertRaises(FetchError):
                UrllibClient().get(s.base + "/to-file", follow_redirects=True)

    def test_a_redirect_to_another_host_drops_every_caller_header_user_agent_included(self):
        hdrs = {"X-Token": "secret", "User-Agent": "Custom/2"}
        with _Server() as s:
            same = UrllibClient().get(s.base + "/samehost", hdrs, follow_redirects=True)
            other = UrllibClient().get(s.base + "/xhost", hdrs, follow_redirects=True)
            default_ua = UrllibClient().get(s.base + "/xhost", {"X-Token": "secret"}, follow_redirects=True)
        self.assertEqual(same.text(), "secret|Custom/2")
        self.assertEqual(other.text(), "|" + USER_AGENT)
        self.assertEqual(default_ua.text(), "|" + USER_AGENT)

    def test_a_caller_user_agent_with_an_address_does_not_reach_the_second_host(self):
        contact = "outreach-research someone@example.edu"
        with _Server() as s, tempfile.TemporaryDirectory() as d:
            got = UrllibClient().get(s.base + "/xhost", {"User-Agent": contact}, follow_redirects=True)
            path = Path(d) / "f.bin"
            UrllibClient().download(s.base + "/xhost-ua", path, {"User-Agent": contact})
            downloaded = path.read_bytes()
        for seen in (got.text(), downloaded.decode()):
            self.assertNotIn("@", seen)
            self.assertNotIn("someone", seen)
        self.assertEqual(got.text(), "|" + USER_AGENT)
        self.assertEqual(downloaded.decode(), USER_AGENT)

    def test_urllib_client_stops_a_redirect_loop_at_max_redirects(self):
        with _Server() as s:
            r = UrllibClient(max_redirects=2).get(s.base + "/loop", follow_redirects=True)
        self.assertEqual(r.status, 302)

    def test_urllib_client_raises_too_large_over_max_bytes(self):
        with _Server() as s:
            with self.assertRaises(FetchError) as cm:
                UrllibClient(max_bytes=1000).get(s.base + "/big")
        self.assertEqual(cm.exception.kind, "too_large")

    def test_urllib_client_raises_fetch_error_when_nothing_listens(self):
        with self.assertRaises(FetchError) as cm:
            UrllibClient(timeout_s=2).get(_dead_port_url())
        self.assertEqual(cm.exception.kind, "network")

    def test_urllib_client_raises_timeout(self):
        with _Server() as s:
            with self.assertRaises(FetchError) as cm:
                UrllibClient(timeout_s=0.2).get(s.base + "/slow")
        self.assertEqual(cm.exception.kind, "timeout")

    def test_urllib_client_bad_url_is_a_fetch_error(self):
        with self.assertRaises(FetchError):
            UrllibClient().get("not a url")

    def test_urllib_client_refuses_a_non_http_scheme(self):
        for url in ("file:///etc/hosts", "ftp://example.com/x"):
            with self.assertRaises(FetchError):
                UrllibClient().get(url)

    def test_urllib_client_posts_a_body(self):
        with _Server() as s:
            r = UrllibClient().post(s.base + "/anything", b'{"a": 1}', {"Content-Type": "application/json"})
        self.assertEqual(r.json(), {"a": 1})

    def test_declared_profile_sends_the_one_user_agent_on_the_wire(self):
        with _Server() as s:
            r = UrllibClient().get(s.base + "/echo-ua")
        self.assertEqual(r.text(), USER_AGENT)

    def test_declared_profile_sends_the_one_user_agent_and_no_at_sign(self):
        req = UrllibClient()._request("https://example.com/x", None)
        headers = {k.lower(): v for k, v in req.header_items()}
        self.assertEqual(headers["user-agent"], USER_AGENT)
        self.assertNotIn("@", USER_AGENT)
        self.assertFalse([k for k in headers if k not in ("user-agent",)], headers)
        self.assertFalse(any("@" in v for v in headers.values()))

    def test_a_caller_user_agent_replaces_the_default(self):
        client = UrllibClient()
        req = client._request("https://example.com/x", {"user-agent": "Other Co admin@other.example"})
        self.assertEqual([v for k, v in req.header_items() if k.lower() == "user-agent"],
                         ["Other Co admin@other.example"])
        req = client._request("https://example.com/x", {"Accept": "application/json"})
        agents = [v for k, v in req.header_items() if k.lower() == "user-agent"]
        self.assertEqual(agents, [USER_AGENT])
        self.assertEqual(req.get_header("Accept"), "application/json")

    def test_a_caller_user_agent_wins_on_the_wire(self):
        with _Server() as s:
            r = UrllibClient().get(s.base + "/echo-ua", {"User-Agent": "Custom/2"})
        self.assertEqual(r.text(), "Custom/2")

    def test_browser_profile_sends_sec_fetch_headers(self):
        req = UrllibClient(profile="browser")._request("https://www.dol.gov/x", None)
        h = {k.lower(): v for k, v in req.header_items()}
        for name in ("accept", "accept-language", "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform",
                     "sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site", "upgrade-insecure-requests"):
            self.assertIn(name, h)
        self.assertEqual(h["sec-fetch-dest"], "document")
        self.assertEqual(h["sec-fetch-mode"], "navigate")
        self.assertEqual(h["sec-fetch-site"], "none")
        self.assertIn("Chrome/", h["user-agent"])
        self.assertNotEqual(h["user-agent"], USER_AGENT)
        self.assertNotIn("accept-encoding", h)

    def test_browser_profile_keeps_cookies(self):
        with _Server() as s:
            c = UrllibClient(profile="browser")
            c.get(s.base + "/cookie")
            self.assertEqual(c.get(s.base + "/needs-cookie").text(), "k=v")
            self.assertEqual(UrllibClient().get(s.base + "/needs-cookie").text(), "")

    def test_unknown_profile_is_refused(self):
        with self.assertRaises(ValueError):
            UrllibClient(profile="curl")


class DownloadTests(unittest.TestCase):
    def test_download_writes_the_file_and_returns_its_size(self):
        with _Server() as s, tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "f.bin"
            n = UrllibClient().download(s.base + "/big", dest)
            self.assertEqual((n, dest.stat().st_size), (5000, 5000))
            self.assertFalse(Path(str(dest) + ".part").exists())

    def test_download_leaves_no_file_on_failure(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "f.bin"
            with self.assertRaises(FetchError):
                UrllibClient(timeout_s=2).download(_dead_port_url(), dest)
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_download_leaves_no_file_when_too_large_midway(self):
        with _Server() as s, tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "f.bin"
            with self.assertRaises(FetchError) as cm:
                UrllibClient(max_bytes=1000).download(s.base + "/big", dest)
            self.assertEqual(cm.exception.kind, "too_large")
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_download_of_an_http_error_leaves_no_file(self):
        with _Server() as s, tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "f.bin"
            with self.assertRaises(FetchError):
                UrllibClient().download(s.base + "/404", dest)
            self.assertEqual(list(Path(d).iterdir()), [])


class SpacedClientTests(unittest.TestCase):
    def _spaced(self, **kw):
        self.now = 100.0
        self.sleeps = []

        def sleep(s):
            self.sleeps.append(s)
            self.now += s

        inner = FakeClient({"https://a.com/1": "x", "https://a.com/2": "x", "https://b.com/1": "x",
                            "https://data.sec.gov/1": "x", "https://data.sec.gov/2": "x",
                            ("POST", "https://a.com/p"): "x"})
        return inner, SpacedClient(inner, clock=lambda: self.now, sleep=sleep, **kw)

    def test_spaced_client_spaces_requests_per_host(self):
        inner, c = self._spaced()
        c.get("https://a.com/1")
        c.get("https://a.com/2")
        self.assertAlmostEqual(sum(self.sleeps), 1.5)
        before = len(self.sleeps)
        c.get("https://b.com/1")
        self.assertEqual(sum(self.sleeps[before:]), 0)
        self.assertEqual(inner.requests, ["https://a.com/1", "https://a.com/2", "https://b.com/1"])

    def test_spaced_client_does_not_sleep_when_time_has_passed(self):
        _, c = self._spaced()
        c.get("https://a.com/1")
        self.now += 5
        c.get("https://a.com/2")
        self.assertEqual(self.sleeps, [])

    def test_spaced_client_spaces_posts_too(self):
        _, c = self._spaced()
        c.get("https://a.com/1")
        c.post("https://a.com/p", b"")
        self.assertAlmostEqual(sum(self.sleeps), 1.5)

    def test_spaced_client_sec_hosts_use_short_interval(self):
        _, c = self._spaced()
        c.get("https://data.sec.gov/1")
        c.get("https://data.sec.gov/2")
        self.assertAlmostEqual(sum(self.sleeps), 0.15)

    def test_spaced_client_custom_interval(self):
        _, c = self._spaced(min_interval_s=3.0, per_host={"a.com": 0.5})
        c.get("https://a.com/1")
        c.get("https://a.com/2")
        self.assertAlmostEqual(sum(self.sleeps), 0.5)

    def test_a_caller_per_host_keeps_the_sec_defaults(self):
        _, c = self._spaced(per_host={"a.com": 0.5})
        c.get("https://data.sec.gov/1")
        c.get("https://data.sec.gov/2")
        self.assertAlmostEqual(sum(self.sleeps), 0.15)

    def test_an_explicit_sec_key_overrides_the_default_and_keys_are_case_blind(self):
        _, c = self._spaced(per_host={"DATA.SEC.GOV": 0.4})
        c.get("https://data.sec.gov/1")
        c.get("https://data.sec.gov/2")
        self.assertAlmostEqual(sum(self.sleeps), 0.4)

    def test_spaced_client_passes_download_through(self):
        inner, c = self._spaced()
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(c.download("https://a.com/1", Path(d) / "f"), 1)
            c.download("https://a.com/2", Path(d) / "g")
        self.assertAlmostEqual(sum(self.sleeps), 1.5)


class ResponseTests(unittest.TestCase):
    def test_response_text_replaces_bad_bytes(self):
        r = Response(200, "u", b"ok \xff\xfe", {})
        self.assertEqual(r.text(), "ok ��")

    def test_response_json(self):
        self.assertEqual(Response(200, "u", b'{"a": [1]}', {}).json(), {"a": [1]})
        with self.assertRaises(ValueError):
            Response(200, "u", b"<html>", {}).json()


class FakeClientTests(unittest.TestCase):
    def test_fake_client_unknown_url_fails_the_test(self):
        c = FakeClient({"https://a.com/": "x"})
        with self.assertRaises(UnexpectedRequest):
            c.get("https://a.com/other")
        self.assertTrue(issubclass(UnexpectedRequest, AssertionError))
        with self.assertRaises(UnexpectedRequest):
            c.post("https://a.com/", b"")  # a GET route is not a POST route
        self.assertEqual(c.unrouted, ["https://a.com/other", "https://a.com/"])

    def test_fake_client_unrouted_survives_code_that_swallows_the_error(self):
        c = FakeClient({})
        try:
            c.get("https://a.com/x")
        except Exception:
            pass  # code under test that catches Exception hides the failure; `unrouted` does not
        self.assertEqual(c.unrouted, ["https://a.com/x"])
        self.assertEqual(FakeClient({"https://a.com/": "x"}).unrouted, [])

    def test_fake_client_exception_route_raises(self):
        c = FakeClient({"https://a.com/": FetchError("network", "down"), "https://b.com/": RuntimeError("x")})
        with self.assertRaises(FetchError):
            c.get("https://a.com/")
        with self.assertRaises(RuntimeError):
            c.get("https://b.com/")

    def test_fake_client_consumes_a_list_in_order(self):
        c = FakeClient({"https://a.com/": [status_response(503), "second", FetchError("timeout", "slow")]})
        self.assertEqual(c.get("https://a.com/").status, 503)
        self.assertEqual(c.get("https://a.com/").text(), "second")
        with self.assertRaises(FetchError):
            c.get("https://a.com/")
        with self.assertRaises(UnexpectedRequest):
            c.get("https://a.com/")

    def test_fake_client_value_kinds(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "body.txt"
            p.write_bytes(b"from file")
            seen = []

            def cb(method, url, headers, body):
                seen.append((method, url, headers, body))
                return text_response("called", status=201)

            c = FakeClient({"https://a.com/bytes": b"raw", "https://a.com/str": "s", "https://a.com/path": p,
                            "https://a.com/resp": json_response({"k": 1}, etag='"e1"'),
                            ("POST", "https://a.com/cb"): cb})
            self.assertEqual((c.get("https://a.com/bytes").status, c.get("https://a.com/bytes").body), (200, b"raw"))
            self.assertEqual(c.get("https://a.com/str").text(), "s")
            self.assertEqual(c.get("https://a.com/path").body, b"from file")
            r = c.get("https://a.com/resp")
            self.assertEqual((r.json(), r.headers["etag"]), ({"k": 1}, '"e1"'))
            r = c.post("https://a.com/cb", b"payload", {"X": "1"})
            self.assertEqual(r.status, 201)
            self.assertEqual(seen, [("POST", "https://a.com/cb", {"X": "1"}, b"payload")])

    def test_fake_client_records_calls_and_requests(self):
        c = FakeClient({"https://a.com/1": "x", "https://a.com/2": "y"})
        c.get("https://a.com/1", {"A": "b"})
        c.get("https://a.com/2", follow_redirects=True)
        self.assertEqual(c.requests, ["https://a.com/1", "https://a.com/2"])
        self.assertEqual(c.calls[0], ("GET", "https://a.com/1", {"A": "b"}, None))
        self.assertEqual(c.calls[1][0:2], ("GET", "https://a.com/2"))

    def test_fake_client_download(self):
        c = FakeClient({"https://a.com/f": b"12345", })
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(c.download("https://a.com/f", Path(d) / "f"), 5)
            self.assertEqual((Path(d) / "f").read_bytes(), b"12345")
            self.assertEqual([p.name for p in Path(d).iterdir()], ["f"])

    def test_helpers(self):
        self.assertEqual(status_response(404).status, 404)
        r = text_response("hi", status=202, headers={"X-A": "b"})
        self.assertEqual((r.status, r.text(), r.headers["x-a"]), (202, "hi", "b"))
        self.assertNotIn("etag", json_response([]).headers)


if __name__ == "__main__":
    unittest.main()
