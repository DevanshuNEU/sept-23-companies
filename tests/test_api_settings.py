import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from outreach.api.__main__ import main
from outreach.api.app import create_app
from outreach.api.config import load_config
from outreach.api.settings import FORBIDDEN_PORTS, WEB_DEV_PORTS, Settings, SettingsError, settings_from_env
from outreach.store import DEFAULT_PATH
from tests.apitest import ApiCase
from tests.storetest import fresh_store


class SettingsTest(unittest.TestCase):
    def test_default_is_3100_on_127_0_0_1(self):
        s = settings_from_env({})
        self.assertEqual((s.host, s.port), ("127.0.0.1", 3100))
        self.assertEqual(s.dev_origins, frozenset())
        self.assertEqual(s.busy_timeout_ms, 5000)
        self.assertTrue(s.sweeper)
        self.assertEqual(s.web_dist.name, "dist")
        self.assertTrue(s.web_dist.is_absolute())
        self.assertEqual(s.banned_phrases, Path("~/Desktop/DevVault/job-search/outreach/banned_phrases.txt").expanduser())

    def test_forbidden_ports_are_refused(self):
        self.assertEqual(FORBIDDEN_PORTS, frozenset({3001, 3002, 3011, 3013}))
        for port in sorted(FORBIDDEN_PORTS):
            with self.subTest(port=port), self.assertRaises(SettingsError):
                settings_from_env({"OUTREACH_API_PORT": str(port)})

    def test_the_web_dev_port_is_refused_as_a_listen_port_but_allowed_as_an_origin(self):
        self.assertEqual(WEB_DEV_PORTS, frozenset({5173}))
        with self.assertRaises(SettingsError) as ctx:
            settings_from_env({"OUTREACH_API_PORT": "5173"})
        self.assertIn("5173", str(ctx.exception))
        with self.assertRaises(SettingsError):
            settings_from_env({"CONDUCTOR_PORT": "5172"})  # 5172 + 1
        self.assertEqual(
            settings_from_env({"OUTREACH_DEV_ORIGINS": "http://127.0.0.1:5173"}).dev_origins,
            frozenset({"http://127.0.0.1:5173"}),
        )

    def test_settings_from_env_stays_pure_about_the_checkout(self):
        # The live-checkout rule for port 3100 lives in main(), so this works the same in every checkout.
        with mock.patch("live_checkout.is_live", return_value=False):
            self.assertEqual(settings_from_env({}).port, 3100)

    def test_port_must_be_a_number_in_range(self):
        for bad in ("80", "1023", "65536", "abc", "", "31.5", "-1"):
            with self.subTest(port=bad), self.assertRaises(SettingsError):
                settings_from_env({"OUTREACH_API_PORT": bad})
        self.assertEqual(settings_from_env({"OUTREACH_API_PORT": "1024"}).port, 1024)
        self.assertEqual(settings_from_env({"OUTREACH_API_PORT": "65535"}).port, 65535)

    def test_a_workspace_dev_port_is_conductor_port_plus_one(self):
        self.assertEqual(settings_from_env({"CONDUCTOR_PORT": "55110"}).port, 55111)
        self.assertEqual(settings_from_env({"CONDUCTOR_PORT": "55110", "OUTREACH_API_PORT": "3100"}).port, 3100)
        with self.assertRaises(SettingsError):  # 3010 + 1 is 3011, the old dashboard preview
            settings_from_env({"CONDUCTOR_PORT": "3010"})

    def test_only_loopback_host(self):
        for host in ("0.0.0.0", "localhost", "::1", "", "192.168.1.5", "127.0.0.2"):
            with self.subTest(host=host), self.assertRaises(SettingsError):
                settings_from_env({"OUTREACH_API_HOST": host})
        self.assertEqual(settings_from_env({"OUTREACH_API_HOST": "127.0.0.1"}).host, "127.0.0.1")

    def test_dev_origins_must_be_loopback_http(self):
        ok = settings_from_env({"OUTREACH_DEV_ORIGINS": "http://127.0.0.1:5173, http://localhost:55110"})
        self.assertEqual(ok.dev_origins, frozenset({"http://127.0.0.1:5173", "http://localhost:55110"}))
        for bad in (
            "https://evil.example",
            "https://127.0.0.1:5173",
            "http://evil.example:5173",
            "http://127.0.0.1",
            "http://127.0.0.1:5173/",
            "http://127.0.0.1:5173/path",
            "http://127.0.0.1.evil.example:5173",
            "http://127.0.0.1:99999",
            "*",
            "null",
            "http://127.0.0.1:3001",
            "http://localhost:3002",
        ):
            with self.subTest(origin=bad), self.assertRaises(SettingsError):
                settings_from_env({"OUTREACH_DEV_ORIGINS": bad})

    def test_busy_timeout_and_sweeper(self):
        self.assertEqual(settings_from_env({"OUTREACH_API_BUSY_MS": "250"}).busy_timeout_ms, 250)
        for bad in ("0", "-5", "x", "600001"):
            with self.subTest(busy=bad), self.assertRaises(SettingsError):
                settings_from_env({"OUTREACH_API_BUSY_MS": bad})
        for text, want in (("0", False), ("off", False), ("false", False), ("1", True), ("on", True), ("TRUE", True)):
            self.assertIs(settings_from_env({"OUTREACH_API_SWEEPER": text}).sweeper, want)
        with self.assertRaises(SettingsError):
            settings_from_env({"OUTREACH_API_SWEEPER": "maybe"})

    def test_paths_come_from_the_environment(self):
        s = settings_from_env({
            "OUTREACH_DB": "/tmp/x/outreach.db",
            "OUTREACH_WEB_DIST": "/tmp/dist",
            "OUTREACH_BANNED_PHRASES": "/tmp/banned.txt",
        })
        self.assertEqual(s.web_dist, Path("/tmp/dist"))
        self.assertEqual(s.banned_phrases, Path("/tmp/banned.txt"))
        self.assertTrue(s.db_path.endswith("/x/outreach.db"))

    def test_settings_are_frozen(self):
        with self.assertRaises(Exception):
            settings_from_env({}).port = 1  # type: ignore[misc]

    def test_the_error_is_plain_and_names_the_variable(self):
        with self.assertRaises(SettingsError) as ctx:
            settings_from_env({"OUTREACH_API_PORT": "3002"})
        self.assertIn("OUTREACH_API_PORT", str(ctx.exception))
        self.assertIn("3002", str(ctx.exception))


class ConfigTest(unittest.TestCase):
    def test_the_shipped_numbers(self):
        c = load_config()
        self.assertEqual(c.notes_max_chars, 300)
        self.assertEqual(
            (c.min_requests, c.finding_group_min, c.prompt_after_days, c.reask_after_days, c.quiet_after_days),
            (30, 20, 7, 7, 21),
        )
        self.assertEqual(c.search_index_top, 3000)


def scratch_env(**extra):
    """The environment `main` reads: no workspace port, no API overrides, and a store that is installed."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("OUTREACH_API_") and k != "CONDUCTOR_PORT"}
    env["OUTREACH_API_PORT"] = "3150"  # 3100 is the live API's port and main refuses it outside the live checkout
    env.update(extra)
    return mock.patch.dict(os.environ, env, clear=True)


def installed_store_path(case):
    conn = fresh_store()
    case.addCleanup(conn.close)
    return conn.execute("PRAGMA database_list").fetchone()["file"]


class CheckTest(unittest.TestCase):
    def test_check_on_a_scratch_store_prints_ok(self):
        out = io.StringIO()
        with scratch_env(OUTREACH_DB=installed_store_path(self)), redirect_stdout(out):
            code = main(["--check"])
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue().strip(), "ok")

    def test_check_with_a_bad_setting_fails_with_the_reason(self):
        out, err = io.StringIO(), io.StringIO()
        with scratch_env(OUTREACH_API_PORT="3002"), redirect_stdout(out):
            with mock.patch("sys.stderr", err):
                code = main(["--check"])
        self.assertEqual(code, 1)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("OUTREACH_API_PORT", err.getvalue())

    def test_check_with_a_missing_store_fails_with_the_install_command(self):
        err = io.StringIO()
        with scratch_env(OUTREACH_DB=str(Path(tempfile.gettempdir()) / "outreach-api-no-such-dir" / "outreach.db")):
            with mock.patch("sys.stderr", err):
                code = main(["--check"])
        self.assertEqual(code, 1)
        self.assertIn("outreach.install", err.getvalue())

    def test_check_refuses_the_live_store_outside_the_live_checkout(self):
        # is_live is forced false so this test is the same in a worktree and in the live checkout. connect is patched
        # to fail the test, so a missing guard cannot open the live store from here.
        def opened(*args, **kwargs):
            raise AssertionError("the live store was opened")

        with mock.patch("live_checkout.is_live", return_value=False):
            with mock.patch("outreach.api.__main__.connect", opened):
                with scratch_env(OUTREACH_DB=DEFAULT_PATH):
                    with self.assertRaises(SystemExit) as ctx:
                        main(["--check"])
        self.assertIn("live checkout", str(ctx.exception))

    def test_port_3100_is_refused_outside_the_live_checkout_with_a_plain_message(self):
        for argv in (["--check"], []):
            with self.subTest(argv=argv):
                err = io.StringIO()
                served = []
                with mock.patch("live_checkout.is_live", return_value=False):
                    with mock.patch("uvicorn.run", lambda app, **kw: served.append(kw)):
                        with scratch_env(OUTREACH_DB=installed_store_path(self), OUTREACH_API_PORT="3100"):
                            with mock.patch("sys.stderr", err):
                                self.assertEqual(main(argv), 1)
                self.assertEqual(served, [])
                self.assertIn("3100", err.getvalue())
                self.assertIn("live checkout", err.getvalue())
                self.assertIn("OUTREACH_API_PORT", err.getvalue())

    def test_port_3100_is_the_default_and_allowed_in_the_live_checkout(self):
        calls = []
        with mock.patch("live_checkout.is_live", return_value=True):
            with mock.patch("uvicorn.run", lambda app, **kw: calls.append(kw)):
                with scratch_env(OUTREACH_DB=installed_store_path(self), OUTREACH_API_PORT="3100"):
                    self.assertEqual(main([]), 0)
        self.assertEqual(calls[0]["port"], 3100)

    def test_the_app_refuses_the_live_store_outside_the_live_checkout(self):
        live = Settings(
            db_path=DEFAULT_PATH, host="127.0.0.1", port=3100, dev_origins=frozenset(),
            web_dist=Path("/nonexistent"), banned_phrases=Path("/nonexistent"), busy_timeout_ms=5000, sweeper=False,
        )
        with mock.patch("live_checkout.is_live", return_value=False):
            with self.assertRaises(SystemExit):
                create_app(live)

    def test_serving_binds_loopback_and_passes_the_hardening_flags(self):
        calls = []
        with scratch_env(OUTREACH_DB=installed_store_path(self)):
            with mock.patch("uvicorn.run", lambda app, **kw: calls.append(kw)):
                self.assertEqual(main([]), 0)
        self.assertEqual(len(calls), 1)
        kw = calls[0]
        self.assertEqual(kw["host"], "127.0.0.1")
        self.assertEqual(kw["port"], 3150)
        self.assertIs(kw["proxy_headers"], False)
        self.assertIs(kw["server_header"], False)


class PingTest(ApiCase):
    def test_ping(self):
        r = self.get("/api/ping")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True})

    def test_the_operation_id_is_the_function_name(self):
        ids = {op["operationId"] for item in self.client.app.openapi()["paths"].values() for op in item.values()}
        self.assertIn("ping", ids)

    def test_the_app_keeps_its_injected_clock(self):
        self.assertEqual(self.client.app.state.clock(), "2026-11-10T15:14:00Z")
        self.set_now("2026-11-11T09:00:00Z")
        self.assertEqual(self.client.app.state.clock(), "2026-11-11T09:00:00Z")

    def test_the_test_store_is_scratch(self):
        self.assertNotIn("Application Support", self.client.app.state.settings.db_path)

    def test_routers_are_found_by_scanning_the_package(self):
        # A module dropped into outreach/api/routers is included under /api with no edit to app.py.
        self.use_routers(zz_probe="""
            from fastapi import APIRouter
            router = APIRouter()
            @router.get("/probe")
            def probe():
                return {"probe": True}
        """)
        self.assertEqual(self.get("/api/probe").json(), {"probe": True})


if __name__ == "__main__":
    unittest.main()
