"""
Guards that keep a worktree away from the live system: real Gmail sends and real refresh runs happen only from
the live checkout. Nothing here sends mail, reads a real token, or runs a refresh.

Run from the project root:  python3 -m unittest discover -s tests -t .
"""
import inspect, os, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tests.test_mail_sender import FakeGmail, SenderTest, contact, iso, gmail, sender  # noqa: E402
import live_checkout  # noqa: E402


class LiveCheckout(unittest.TestCase):
    def test_this_worktree_is_not_live(self):
        self.assertFalse(live_checkout.is_live(ROOT))

    def test_the_live_root_is_live(self):
        self.assertTrue(live_checkout.is_live(live_checkout.LIVE_ROOT))

    def test_live_root_follows_env(self):
        out = subprocess.run([sys.executable, "-c", "import live_checkout; print(live_checkout.LIVE_ROOT)"],
                             cwd=ROOT, capture_output=True, text=True,
                             env={**os.environ, "OUTREACH_LIVE_ROOT": "/tmp/elsewhere"})
        self.assertEqual(out.stdout.strip(), "/tmp/elsewhere")


class GmailSecretsFollowEnv(unittest.TestCase):
    def test_token_path_follows_outreach_gmail_dir(self):
        d = tempfile.mkdtemp(prefix="outreach-gmail-test-")
        out = subprocess.run([sys.executable, "-c", "import gmail; print(gmail.TOKEN); print(gmail.DIR == gmail.DEFAULT_DIR)"],
                             cwd=os.path.join(ROOT, "mail"), capture_output=True, text=True,
                             env={**os.environ, "OUTREACH_GMAIL_DIR": d})
        token, is_default = out.stdout.split()
        self.assertEqual(token, os.path.join(d, "token.json"))
        self.assertEqual(is_default, "False")

    def test_tests_never_point_at_the_real_secrets(self):
        self.assertNotEqual(gmail.DIR, gmail.DEFAULT_DIR)


class SenderRefusesOutsideLive(SenderTest):
    def test_real_secrets_outside_the_live_checkout_send_nothing(self):
        fake = FakeGmail()
        fake.install(self)
        self.addCleanup(setattr, gmail, "DIR", gmail.DIR)
        gmail.DIR = gmail.DEFAULT_DIR                      # as if no test override were set
        self.seed(contact("a", "approved", approvedAt=iso()))
        sender.main()
        self.assertEqual(fake.sends, [])
        self.assertIn("live checkout", open(sender.LOGF).read())


class RefreshRefusesOutsideLive(unittest.TestCase):
    def test_require_live_exits_outside_the_live_checkout(self):
        with self.assertRaises(SystemExit) as cm:
            live_checkout.require_live("refresh", ROOT, argv=["refresh.py"])
        self.assertIn("live checkout", str(cm.exception.code))

    def test_require_live_allows_an_explicit_override(self):
        live_checkout.require_live("refresh", ROOT, argv=["refresh.py", "--allow-non-live"])

    def test_refresh_main_checks_before_touching_anything(self):
        src = open(os.path.join(ROOT, "refresh", "refresh.py")).read()
        body = src[src.index("def main():"):]
        self.assertIn("live_checkout.require_live(", body)
        self.assertLess(body.index("live_checkout.require_live("), body.index("os.makedirs("))


if __name__ == "__main__":
    unittest.main()
