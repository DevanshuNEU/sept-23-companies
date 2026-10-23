"""The guards that keep every other page, and every agent, from reading or writing through the API (Review Focus 2).

A scratch route records whether its handler ran, so each refusal is shown to stop the request before any handler.
"""
import dataclasses
import json
import sys

from fastapi.testclient import TestClient

from tests.apitest import BASE_URL, ORIGIN, ApiCase

SCRATCH = '''
from fastapi import APIRouter

router = APIRouter()
CALLS = []


@router.get("/guard/read")
def read():
    CALLS.append("read")
    return {"ok": True}


@router.post("/guard/write")
def write(body: dict):
    CALLS.append("write")
    return {"ok": True}


@router.put("/guard/write")
def put(body: dict):
    CALLS.append("put")
    return {"ok": True}


@router.patch("/guard/write")
def patch(body: dict):
    CALLS.append("patch")
    return {"ok": True}


@router.delete("/guard/write")
def delete():
    CALLS.append("delete")
    return {"ok": True}
'''

JSON = "application/json"
WRITES = ("POST", "PUT", "PATCH", "DELETE")


class GuardCase(ApiCase):
    def setUp(self):
        super().setUp()
        self.use_routers(zz_guard_scratch=SCRATCH)

    def build(self):
        super().build()
        # A client with no default headers at all: the shape of a plain curl call.
        self.bare = TestClient(self.app, base_url=BASE_URL, raise_server_exceptions=False)
        self.addCleanup(self.bare.close)

    @property
    def calls(self) -> list:
        module = sys.modules.get("outreach.api.routers.zz_guard_scratch")
        return module.CALLS if module else []

    def write(self, method="POST", headers=None, client=None, body=b'{"a": 1}'):
        sent = {"Origin": ORIGIN, "Content-Type": JSON}
        sent.update(headers or {})
        sent = {k: v for k, v in sent.items() if v is not None}
        return (client or self.bare).request(method, "/api/guard/write", content=body, headers=sent)

    def read(self, headers=None):
        return self.bare.get("/api/guard/read", headers=headers or {})

    def assert_refused(self, reply, word: str | None = None):
        self.assertEqual(reply.status_code, 403, reply.text)
        body = reply.json()
        self.assertEqual(body["code"], "forbidden")
        self.assertEqual(body["checks"], [])
        if word:
            self.assertIn(word, body["message"].lower())
        self.assertEqual(self.calls, [], "a refused request reached its handler")


class WriteGuardTest(GuardCase):
    def test_an_own_page_write_runs(self):
        for method in WRITES:
            with self.subTest(method=method):
                reply = self.write(method, body=b"" if method == "DELETE" else b'{"a": 1}')
                self.assertEqual(reply.status_code, 200, reply.text)
        self.assertEqual(self.calls, ["write", "put", "patch", "delete"])

    def test_write_without_origin_is_refused_and_never_runs(self):
        for method in WRITES:
            with self.subTest(method=method):
                self.assert_refused(self.write(method, {"Origin": None}), "origin")

    def test_write_without_origin_is_refused_even_through_the_default_client(self):
        reply = self.client.post("/api/guard/write", content=b"{}", headers={"Content-Type": JSON, "Origin": "null"})
        self.assert_refused(reply, "origin")

    def test_write_with_foreign_origin_is_refused(self):
        for origin in ("https://evil.example", "http://127.0.0.1:3101", "http://localhost:3001",
                       "https://127.0.0.1:3100", "http://127.0.0.1:3100/", "http://127.0.0.1:3100.evil.example"):
            with self.subTest(origin=origin):
                self.assert_refused(self.write(headers={"Origin": origin}), "origin")

    def test_localhost_origin_on_the_same_port_is_allowed(self):
        reply = self.write(headers={"Origin": "http://localhost:3100", "Host": "localhost:3100"})
        self.assertEqual(reply.status_code, 200, reply.text)

    def test_write_with_form_content_type_is_refused(self):
        for kind in ("application/x-www-form-urlencoded", "text/plain", "multipart/form-data; boundary=x",
                     "application/jsonx", None):
            with self.subTest(content_type=kind):
                self.assert_refused(self.write(headers={"Content-Type": kind}), "json")

    def test_json_with_a_charset_is_allowed(self):
        reply = self.write(headers={"Content-Type": "application/json; charset=utf-8"})
        self.assertEqual(reply.status_code, 200, reply.text)

    def test_same_origin_fetch_metadata_is_allowed_on_a_write(self):
        reply = self.write(headers={"Sec-Fetch-Site": "same-origin"})
        self.assertEqual(reply.status_code, 200, reply.text)

    def test_write_with_any_other_fetch_site_is_refused(self):
        for site in ("same-site", "none", "cross-site"):
            with self.subTest(site=site):
                self.assert_refused(self.write(headers={"Sec-Fetch-Site": site}), "sec-fetch-site")


class ReadGuardTest(GuardCase):
    def test_get_without_origin_is_allowed(self):
        reply = self.read()
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertEqual(self.calls, ["read"])

    def test_get_with_own_origin_is_allowed(self):
        self.assertEqual(self.read({"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"}).status_code, 200)

    def test_foreign_origin_on_a_get_is_refused(self):
        for origin in ("https://evil.example", "http://127.0.0.1:3101", "null"):
            with self.subTest(origin=origin):
                self.assert_refused(self.read({"Origin": origin}), "origin")

    def test_head_with_foreign_origin_is_refused(self):
        reply = self.bare.head("/api/guard/read", headers={"Origin": "https://evil.example"})
        self.assertEqual(reply.status_code, 403)  # a HEAD reply has no body to parse
        self.assertEqual(self.calls, [])


class FetchMetadataTest(GuardCase):
    def test_cross_site_fetch_metadata_is_refused_on_read_and_write(self):
        self.assert_refused(self.read({"Sec-Fetch-Site": "cross-site"}), "sec-fetch-site")
        self.assert_refused(self.write(headers={"Sec-Fetch-Site": "cross-site"}), "sec-fetch-site")

    def test_a_typed_address_is_allowed_on_a_read(self):
        self.assertEqual(self.read({"Sec-Fetch-Site": "none"}).status_code, 200)


class HostGuardTest(GuardCase):
    def test_rebinding_host_is_refused_on_read_and_write(self):
        for host in ("evil.example:3100", "127.0.0.1:9999", "evil.example", "127.0.0.1", "0.0.0.0:3100",
                     "[::1]:3100"):
            with self.subTest(host=host):
                self.assert_refused(self.read({"Host": host}), "host")
                self.assert_refused(self.write(headers={"Host": host}), "host")

    def test_host_rule_covers_paths_outside_the_api(self):
        self.assertEqual(self.bare.get("/index.html", headers={"Host": "evil.example:3100"}).status_code, 403)
        self.assertEqual(self.bare.get("/", headers={"Host": "evil.example:3100"}).status_code, 403)

    def test_localhost_host_is_allowed(self):
        self.assertEqual(self.read({"Host": "localhost:3100"}).status_code, 200)


class DevOriginTest(GuardCase):
    VITE = "http://localhost:5173"

    def test_dev_origin_is_allowed_only_when_configured(self):
        self.assert_refused(self.write(headers={"Origin": self.VITE}), "origin")
        self.assert_refused(self.read({"Origin": self.VITE}), "origin")
        self.settings = dataclasses.replace(self.settings, dev_origins=frozenset({self.VITE}))
        self.build()
        self.assertEqual(self.write(headers={"Origin": self.VITE}).status_code, 200)
        self.assertEqual(self.read({"Origin": self.VITE}).status_code, 200)
        self.assertEqual(self.calls, ["write", "read"])

    def test_the_port_comes_from_the_settings(self):
        self.settings = dataclasses.replace(self.settings, port=4200)
        self.build()
        self.assert_refused(self.write(), "host")  # Host 127.0.0.1:3100 is not this app any more
        reply = self.write(headers={"Host": "127.0.0.1:4200", "Origin": "http://127.0.0.1:4200"})
        self.assertEqual(reply.status_code, 200, reply.text)


class NoCorsTest(GuardCase):
    def test_no_cors_headers_on_any_response(self):
        replies = [
            self.read({"Origin": ORIGIN}),
            self.write(),
            self.write(headers={"Origin": "https://evil.example"}),
            self.bare.options("/api/guard/write", headers={
                "Origin": "https://evil.example", "Access-Control-Request-Method": "POST"}),
            self.bare.get("/api/no-such-thing", headers={"Origin": ORIGIN}),
        ]
        for reply in replies:
            with self.subTest(status=reply.status_code):
                cors = [name for name in reply.headers if name.lower().startswith("access-control-")]
                self.assertEqual(cors, [])

    def test_options_is_refused(self):
        for origin in (ORIGIN, "https://evil.example", None):
            with self.subTest(origin=origin):
                headers = {"Access-Control-Request-Method": "POST"}
                if origin:
                    headers["Origin"] = origin
                self.assert_refused(self.bare.options("/api/guard/write", headers=headers))

    def test_an_unknown_method_is_refused(self):
        self.assert_refused(self.bare.request("TRACE", "/api/guard/read"))


class EnvelopeTest(GuardCase):
    def test_the_refusal_is_the_envelope(self):
        reply = self.write(headers={"Origin": None})
        self.assertEqual(reply.status_code, 403)
        self.assertEqual(reply.headers["content-type"], "application/json")
        body = reply.json()
        self.assertEqual(set(body), {"code", "message", "checks"})
        self.assertEqual(body["code"], "forbidden")
        self.assertIn("origin", body["message"].lower())
        self.assertNotIn("{", body["message"])

    def test_the_refusal_never_echoes_the_header(self):
        reply = self.write(headers={"Origin": "https://secret-page.example"})
        self.assertNotIn("secret-page", reply.text)

    def test_a_refusal_never_reads_the_body(self):
        seen = []

        async def app(scope, receive, send):  # pragma: no cover - never reached
            seen.append("app")

        from outreach.api.guards import GuardMiddleware

        guard = GuardMiddleware(app, self.settings)
        sent = []

        async def receive():
            seen.append("receive")
            return {"type": "http.request", "body": b"{}", "more_body": False}

        async def send(message):
            sent.append(message)

        import asyncio

        scope = {"type": "http", "method": "POST", "path": "/api/guard/write", "raw_path": b"/api/guard/write",
                 "query_string": b"", "headers": [(b"host", b"127.0.0.1:3100"), (b"content-type", b"text/plain")]}
        asyncio.run(guard(scope, receive, send))
        self.assertEqual(seen, [])
        self.assertEqual(sent[0]["status"], 403)
        self.assertEqual(json.loads(sent[1]["body"])["code"], "forbidden")


if __name__ == "__main__":
    import unittest

    unittest.main()
