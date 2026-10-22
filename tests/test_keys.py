"""Key normalizers (#31 Task 4): FEIN, registrable domain, domain classes."""
import unittest

from outreach.sources.keys import classify_domain, normalize_fein, registrable_domain


class KeysTest(unittest.TestCase):
    def test_normalize_fein(self):
        self.assertEqual(normalize_fein("46-3517853"), "463517853")
        self.assertEqual(normalize_fein("463517853"), "463517853")
        self.assertEqual(normalize_fein(" 46 3517853 "), "463517853")
        self.assertIsNone(normalize_fein("000000000"))
        self.assertIsNone(normalize_fein("00-0000000"))
        self.assertIsNone(normalize_fein("46-351785"))
        self.assertIsNone(normalize_fein("46-35178531"))
        self.assertIsNone(normalize_fein(""))
        self.assertIsNone(normalize_fein(None))

    def test_registrable_domain(self):
        self.assertEqual(registrable_domain("eng.nimbus.com"), "nimbus.com")
        self.assertEqual(registrable_domain("acme.co.uk"), "acme.co.uk")
        self.assertEqual(registrable_domain("www.x.com"), "x.com")
        self.assertEqual(registrable_domain("a@mail.x.com"), "x.com")
        self.assertEqual(registrable_domain("Ops@WWW.Nimbus.COM "), "nimbus.com")
        self.assertEqual(registrable_domain("mail.acme.com.au"), "acme.com.au")
        self.assertEqual(registrable_domain("nimbus.co"), "nimbus.co")
        self.assertEqual(registrable_domain("a.b.nimbus.io"), "nimbus.io")

    def test_registrable_domain_none_without_a_usable_host(self):
        for raw in ("localhost", "", "a@", "nimbus", "a@b", "bad..com", "has space.com", "@.", None):
            self.assertIsNone(registrable_domain(raw), raw)

    def test_classify_domain(self):
        self.assertEqual(classify_domain("nimbus.com"), "company")
        self.assertEqual(classify_domain("gmail.com"), "free")
        self.assertEqual(classify_domain("yahoo.co.in"), "free")
        self.assertEqual(classify_domain("trinet.com"), "peo")
        self.assertEqual(classify_domain("velocityglobal.com"), "peo")
        self.assertEqual(classify_domain("greenhouse.io"), "platform")
        self.assertEqual(classify_domain("notion.site"), "platform")

    def test_classify_domain_accepts_a_host_or_mixed_case(self):
        self.assertEqual(classify_domain("Mail.Gmail.com"), "free")
        self.assertEqual(classify_domain("jobs.lever.co"), "platform")

    def test_every_listed_name_has_its_class(self):
        for d in ("gmail.com", "outlook.com", "yahoo.com", "hotmail.com", "icloud.com", "proton.me", "aol.com"):
            self.assertEqual(classify_domain(d), "free", d)
        for d in ("trinet.com", "deel.com", "justworks.com", "insperity.com", "velocityglobal.com", "adp.com",
                  "gusto.com", "rippling.com", "oysterhr.com", "remote.com"):
            self.assertEqual(classify_domain(d), "peo", d)
        for d in ("github.com", "linkedin.com", "ycombinator.com", "notion.site", "medium.com", "x.com",
                  "twitter.com", "google.com", "lever.co", "greenhouse.io", "ashbyhq.com", "workable.com",
                  "bamboohr.com"):
            self.assertEqual(classify_domain(d), "platform", d)

    def test_hosting_suffixes_are_platforms_with_their_subdomains(self):
        hosts = ("vercel.app", "netlify.app", "herokuapp.com", "pages.dev", "web.app", "firebaseapp.com",
                 "appspot.com", "webflow.io", "myshopify.com", "framer.website", "carrd.co", "onrender.com",
                 "fly.dev", "replit.app", "azurewebsites.net", "cloudfront.net", "amazonaws.com", "blogspot.com",
                 "gitbook.io", "github.io")
        for host in hosts:
            for d in (host, f"acme.{host}", f"www.acme.{host}", f"a.b.{host}"):
                self.assertEqual(classify_domain(d), "platform", d)
        self.assertEqual(classify_domain("acmevercel.app"), "company")


if __name__ == "__main__":
    unittest.main()
