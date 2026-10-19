"""
Guards that keep a worktree away from the live system: real Gmail use and real refresh runs happen only from
the live checkout, with no switch to turn that off. Nothing here sends mail, reads a real token, opens a
browser, or runs a refresh. These tests pass in a worktree and in the live checkout alike.

Run from the project root:  python3 -m unittest discover -s tests -t .
"""
import os, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tests.test_mail_sender import FakeGmail, SenderTest, contact, iso, gmail, sender  # noqa: E402
import live_checkout  # noqa: E402


class LiveCheckout(unittest.TestCase):
    def test_a_root_is_live_only_when_it_is_the_live_root(self):
        live = tempfile.mkdtemp(prefix="outreach-live-test-")
        self.assertTrue(live_checkout.is_live(live, live_root=live))
        self.assertFalse(live_checkout.is_live(ROOT, live_root=live))

    def test_no_environment_variable_moves_the_live_root(self):
        out = subprocess.run([sys.executable, "-c", "import live_checkout; print(live_checkout.LIVE_ROOT)"],
                             cwd=ROOT, capture_output=True, text=True,
                             env={**os.environ, "OUTREACH_LIVE_ROOT": "/tmp/elsewhere"})
        self.assertEqual(out.stdout.strip(), os.path.expanduser("~/Desktop/Sept 23 Companies"))

    def test_require_live_exits_without_offering_a_bypass(self):
        live = tempfile.mkdtemp(prefix="outreach-live-test-")
        with self.assertRaises(SystemExit) as cm:
            live_checkout.require_live("refresh", ROOT, live_root=live)
        self.assertIn("live checkout", str(cm.exception.code))
        self.assertNotIn("allow", str(cm.exception.code).lower())

    def test_no_bypass_flag_exists(self):
        for path in ("live_checkout.py", "refresh/refresh.py", "mail/sender.py", "mail/gmail.py"):
            self.assertNotIn("allow-non-live", open(os.path.join(ROOT, path)).read(), path)


class GmailSecretsFollowEnv(unittest.TestCase):
    def test_token_path_follows_outreach_gmail_dir(self):
        d = tempfile.mkdtemp(prefix="outreach-gmail-test-")
        out = subprocess.run([sys.executable, "-c", "import gmail; print(gmail.TOKEN)"],
                             cwd=os.path.join(ROOT, "mail"), capture_output=True, text=True,
                             env={**os.environ, "OUTREACH_GMAIL_DIR": d})
        self.assertEqual(out.stdout.strip(), os.path.join(d, "token.json"))

    def test_tests_never_point_at_the_real_secrets(self):
        self.assertNotEqual(os.path.realpath(gmail.DIR), os.path.realpath(gmail.DEFAULT_DIR))


class RealTokenOnlyInLive(SenderTest):
    """A real token (any token file on disk, wherever it was copied) is used only from the live checkout."""
    def setUp(self):
        super().setUp()
        self.fake = FakeGmail()
        self.fake.install(self)
        token_dir = tempfile.mkdtemp(prefix="outreach-gmail-test-")
        self.addCleanup(setattr, gmail, "TOKEN", gmail.TOKEN)
        gmail.TOKEN = os.path.join(token_dir, "token.json")
        open(gmail.TOKEN, "w").write("{}")                       # a copied token
        self.addCleanup(setattr, sender.live_checkout, "is_live", sender.live_checkout.is_live)
        sender.live_checkout.is_live = lambda *a, **k: False
        self.seed(contact("a", "approved", approvedAt=iso()))

    def test_sending_refuses(self):
        sender.main()
        self.assertEqual(self.fake.sends, [])
        self.assertIn("live checkout", open(sender.LOGF).read())

    def test_dry_run_refuses_too(self):
        self.addCleanup(setattr, sender, "DRY", sender.DRY)
        sender.DRY = True
        sender.main()
        self.assertEqual(self.fake.sends, [])
        self.assertIn("live checkout", open(sender.LOGF).read())


class GmailAuthOnlyInLive(unittest.TestCase):
    def test_auth_into_the_real_secrets_refuses_outside_live(self):
        self.addCleanup(setattr, gmail, "DIR", gmail.DIR)
        self.addCleanup(setattr, gmail.live_checkout, "is_live", gmail.live_checkout.is_live)
        gmail.DIR = gmail.DEFAULT_DIR
        gmail.live_checkout.is_live = lambda *a, **k: False
        with self.assertRaises(SystemExit) as cm:
            gmail.check_auth_location()
        self.assertIn("live checkout", str(cm.exception.code))

    def test_auth_into_a_scratch_folder_is_allowed(self):
        gmail.check_auth_location()          # tests point OUTREACH_GMAIL_DIR at a scratch folder


class RefreshRefusesOutsideLive(unittest.TestCase):
    def test_refresh_main_checks_before_touching_anything(self):
        src = open(os.path.join(ROOT, "refresh", "refresh.py")).read()
        body = src[src.index("def main():"):]
        self.assertIn("live_checkout.require_live(", body)
        self.assertLess(body.index("live_checkout.require_live("), body.index("os.makedirs("))


class TokenGuardedWhereItIsRead(unittest.TestCase):
    """The real token is refused outside the live checkout by the one function that reads it, so whoami, ad-hoc
    scripts and any future sender are covered, not only sender.main()."""
    def test_reading_a_token_outside_live_refuses(self):
        token_dir = tempfile.mkdtemp(prefix="outreach-gmail-test-")
        self.addCleanup(setattr, gmail, "TOKEN", gmail.TOKEN)
        self.addCleanup(setattr, gmail.live_checkout, "is_live", gmail.live_checkout.is_live)
        gmail.TOKEN = os.path.join(token_dir, "token.json")
        open(gmail.TOKEN, "w").write('{"access_token": "x", "expires_at": 9999999999}')
        gmail.live_checkout.is_live = lambda *a, **k: False
        with self.assertRaises(SystemExit) as cm:
            gmail._token()
        self.assertIn("live checkout", str(cm.exception.code))

    def test_reading_a_token_in_live_works(self):
        token_dir = tempfile.mkdtemp(prefix="outreach-gmail-test-")
        self.addCleanup(setattr, gmail, "TOKEN", gmail.TOKEN)
        self.addCleanup(setattr, gmail.live_checkout, "is_live", gmail.live_checkout.is_live)
        gmail.TOKEN = os.path.join(token_dir, "token.json")
        open(gmail.TOKEN, "w").write('{"access_token": "x", "expires_at": 9999999999}')
        gmail.live_checkout.is_live = lambda *a, **k: True
        self.assertEqual(gmail._token(), "x")


class TestsRunOnScratchSettings(unittest.TestCase):
    """Every test process gets scratch settings from tests/__init__.py, even in the live checkout."""
    def test_scratch_settings_are_set_for_every_test(self):
        import tests
        for var in ("OUTREACH_MAIL_HOME", "OUTREACH_GMAIL_DIR", "OUTREACH_DB"):
            value = os.environ.get(var, "")          # never assert on os.environ itself: a failure would print it
            self.assertTrue(value, f"{var} is not set")
            self.assertTrue(os.path.realpath(value).startswith(os.path.realpath(tempfile.gettempdir())), f"{var} is not a temp path")
        self.assertTrue(tests.SCRATCH)


if __name__ == "__main__":
    unittest.main()
