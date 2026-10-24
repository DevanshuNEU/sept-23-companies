"""The API serves the built web app from web/dist (#11 Task 23, built in #17 PR B1), and its launchd template."""
import os
import plistlib
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from outreach.api import static
from outreach.deploy import jobs
from tests.apitest import ApiCase

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "launchd" / "com.devanshu.outreach-api.plist.template"
INDEX = "<!doctype html><title>Outreach</title><div id=root></div>"
SCRIPT = "console.log('app');"
SECRET = "outside the build"


class BuiltAppCase(ApiCase):
    def setUp(self):
        super().setUp()
        dist = self.settings.web_dist
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text(INDEX, encoding="utf-8")
        (dist / "assets" / "app-3f9a.js").write_text(SCRIPT, encoding="utf-8")
        (dist.parent / "secret.txt").write_text(SECRET, encoding="utf-8")  # a sibling of the build
        os.symlink(dist.parent / "secret.txt", dist / "assets" / "link.js")  # a link that points out of it


class ServeTest(BuiltAppCase):
    def test_index_and_client_routes_serve_index_html_no_store(self):
        for path in ("/", "/index.html", "/outbox", "/companies/42", "/needs-you?x=1"):
            with self.subTest(path=path):
                reply = self.get(path)
                self.assertEqual(reply.status_code, 200)
                self.assertEqual(reply.text, INDEX)
                self.assertEqual(reply.headers["cache-control"], "no-store")
                self.assertTrue(reply.headers["content-type"].startswith("text/html"))

    def test_head_on_a_page_has_no_body(self):
        reply = self.client.head("/outbox")
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.content, b"")

    def test_hashed_assets_are_immutable(self):
        reply = self.get("/assets/app-3f9a.js")
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.text, SCRIPT)
        self.assertEqual(reply.headers["cache-control"], "public, max-age=31536000, immutable")

    def test_a_missing_asset_is_a_404_not_the_page(self):
        reply = self.get("/assets/gone-0000.js")
        self.assertEqual(reply.status_code, 404)
        self.assertEqual(reply.json()["code"], "not_found")

    def test_unknown_api_path_is_a_json_404(self):
        for path in ("/api", "/api/", "/api/no-such-thing", "/api/assets/app-3f9a.js"):
            with self.subTest(path=path):
                reply = self.get(path)
                self.assertEqual(reply.status_code, 404)
                self.assertEqual(reply.json()["code"], "not_found")

    def test_api_routes_still_answer(self):
        self.assertEqual(self.get("/api/ping").json(), {"ok": True})

    def test_framework_doc_paths_stay_404(self):
        # The schema is served by the app object, never a public route (tests/test_api_openapi.py).
        for path in ("/openapi.json", "/docs", "/redoc"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path).status_code, 404)

    def test_a_write_to_a_page_is_not_the_page(self):
        reply = self.post("/outbox", {})
        self.assertNotEqual(reply.status_code, 200)
        self.assertNotIn("Outreach", reply.text)

    def test_path_traversal_is_refused(self):
        for path in ("/assets/../../pyproject.toml", "/%2e%2e/%2e%2e/etc/passwd", "/assets/%2e%2e/%2e%2e/secret.txt",
                     "/assets/..%2f..%2fsecret.txt", "/assets/link.js", "/assets//etc/passwd"):
            with self.subTest(path=path):
                reply = self.get(path)
                self.assertNotIn(SECRET, reply.text)
                self.assertNotIn("[project]", reply.text)
                self.assertIn(reply.status_code, (200, 404))
                if reply.status_code == 200:
                    self.assertEqual(reply.text, INDEX)

    def test_asset_path_never_leaves_the_assets_folder(self):
        # Straight at the resolver, so no client can tidy the path first.
        dist = self.settings.web_dist
        self.assertEqual(static.asset_path(dist, "/assets/app-3f9a.js"), (dist / "assets" / "app-3f9a.js").resolve())
        for path in ("/assets/../index.html", "/assets/../../secret.txt", "/assets/link.js", "/assets//etc/passwd",
                     "/assets/../../../../../../etc/passwd", "/assets/a\x00b.js", "/assets/", "/assets"):
            with self.subTest(path=path):
                self.assertIsNone(static.asset_path(dist, path))

    def test_static_obeys_the_host_guard(self):
        for path in ("/", "/assets/app-3f9a.js"):
            with self.subTest(path=path):
                reply = self.client.get(path, headers={"Host": "evil.example:3100"})
                self.assertEqual(reply.status_code, 403)
                self.assertNotIn(SCRIPT, reply.text)


class NotBuiltTest(ApiCase):
    def test_missing_build_is_a_503_page_and_api_still_works(self):
        self.assertFalse(self.settings.web_dist.exists())
        for path in ("/", "/outbox"):
            with self.subTest(path=path):
                reply = self.get(path)
                self.assertEqual(reply.status_code, 503)
                self.assertEqual(reply.text, "The app is not built. Run: cd web && pnpm build")
                self.assertTrue(reply.headers["content-type"].startswith("text/plain"))
        self.assertEqual(self.get("/api/ping").json(), {"ok": True})


class TemplateTest(unittest.TestCase):
    def test_template_has_no_hard_coded_secret_or_live_path_other_than_the_checkout(self):
        raw = TEMPLATE.read_text(encoding="utf-8")
        self.assertEqual(set(re.findall(r"\{\{[^}]*\}\}", raw)), {"{{LIVE_CHECKOUT}}", "{{LOG_PATH}}"})
        values = plistlib.dumps(plistlib.loads(raw.encode("utf-8"))).decode("utf-8")
        for word in ("OUTREACH_", "token", "secret", "Library/Application Support", ".db", "EnvironmentVariables",
                     "Desktop", "/Users/", "@", "uv"):
            self.assertNotIn(word, values)

    def test_template_renders_with_the_deploy_tool(self):
        live = tempfile.mkdtemp(prefix="outreach-api-live-")
        self.addCleanup(shutil.rmtree, live, True)
        log = os.path.join(live, "api.log")
        text = jobs.render(TEMPLATE.read_text(encoding="utf-8"), live_root=live, log_path=log,
                           label="com.devanshu.outreach-api")
        plist = plistlib.loads(text.encode("utf-8"))
        self.assertEqual(plist["Label"], "com.devanshu.outreach-api")
        self.assertEqual(plist["WorkingDirectory"], live)
        self.assertEqual(plist["ProgramArguments"], [f"{live}/.venv/bin/python", "-m", "outreach.api"])
        self.assertIs(plist["KeepAlive"], True)
        self.assertIs(plist["RunAtLoad"], True)
        self.assertEqual(plist["ThrottleInterval"], 60)  # a crash loop (no store, port taken) restarts once a minute
        self.assertEqual((plist["StandardOutPath"], plist["StandardErrorPath"]), (log, log))
        self.assertEqual(sorted(plist), ["KeepAlive", "Label", "ProgramArguments", "RunAtLoad", "StandardErrorPath",
                                         "StandardOutPath", "ThrottleInterval", "WorkingDirectory"])

    def test_template_says_how_to_build_the_app_and_that_it_is_not_installed_here(self):
        raw = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("pnpm build", raw)
        self.assertIn("Not installed by the repo", raw)
        self.assertIn("3100", raw)


if __name__ == "__main__":
    unittest.main()
