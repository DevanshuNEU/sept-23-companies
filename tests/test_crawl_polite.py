"""Polite client: host buckets, spacing and the breaker (issue #21, Task 2)."""
import unittest

from outreach.crawl.config import load_crawl_config
from outreach.crawl.polite import HostTripped, PoliteClient, bucket_for
from outreach.net import FetchError, Response
from tests.fakes import FakeClient, json_response, status_response

CFG = load_crawl_config()
GH = "https://boards-api.greenhouse.io/v1/boards/acme/jobs"
GH2 = "https://boards-api.greenhouse.io/v1/boards/other/jobs"
BAMBOO_A = "https://a.bamboohr.com/careers/list"
BAMBOO_B = "https://b.bamboohr.com/careers/list"
LEVER = "https://api.lever.co/v0/postings/acme"
PLAIN = "https://jobs.example.org/list"


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


def make(routes):
    inner = FakeClient(routes)
    clock = Clock()
    return PoliteClient(inner, CFG, clock=clock, sleep=clock.sleep), inner, clock


class PoliteClientTests(unittest.TestCase):
    def test_polite_sets_the_declared_user_agent(self):
        polite, inner, _ = make({GH: json_response({})})
        polite.get(GH, {"user-agent": "something else", "Accept": "application/json"})
        headers = inner.calls[0][2]
        agents = [v for k, v in headers.items() if k.lower() == "user-agent"]
        self.assertEqual(agents, [CFG.user_agent])
        self.assertNotIn("@", agents[0])
        self.assertEqual(headers["Accept"], "application/json")

    def test_polite_spaces_requests_to_one_bucket(self):
        polite, _, clock = make({GH: [json_response({}), json_response({})]})
        polite.get(GH)
        self.assertEqual(clock.slept, [])
        polite.get(GH)
        self.assertEqual(clock.slept, [CFG.host_interval_s["greenhouse.io"]])

    def test_polite_counts_time_already_passed(self):
        polite, _, clock = make({GH: [json_response({}), json_response({})]})
        polite.get(GH)
        clock.now += 0.1
        polite.get(GH)
        self.assertAlmostEqual(clock.slept[0], 0.15)

    def test_polite_buckets_share_by_suffix(self):
        self.assertEqual(bucket_for("a.bamboohr.com", CFG), "bamboohr.com")
        self.assertEqual(bucket_for("B.BambooHR.com", CFG), "bamboohr.com")
        self.assertEqual(bucket_for("bamboohr.com", CFG), "bamboohr.com")
        self.assertEqual(bucket_for("notbamboohr.com", CFG), "notbamboohr.com")
        self.assertEqual(bucket_for("jobs.example.org", CFG), "jobs.example.org")
        polite, _, clock = make({BAMBOO_A: json_response({}), BAMBOO_B: json_response({})})
        polite.get(BAMBOO_A)
        polite.get(BAMBOO_B)
        self.assertEqual(clock.slept, [CFG.host_interval_s["bamboohr.com"]])

    def test_polite_longest_suffix_wins(self):
        from dataclasses import replace
        cfg = replace(CFG, host_interval_s={**CFG.host_interval_s, "jobs.greenhouse.io": 3.0})
        self.assertEqual(bucket_for("x.jobs.greenhouse.io", cfg), "jobs.greenhouse.io")
        self.assertEqual(bucket_for("boards-api.greenhouse.io", cfg), "greenhouse.io")

    def test_polite_different_buckets_do_not_wait(self):
        polite, _, clock = make({GH: json_response({}), LEVER: json_response({})})
        polite.get(GH)
        polite.get(LEVER)
        self.assertEqual(clock.slept, [])

    def test_polite_unlisted_host_uses_the_default_interval(self):
        polite, _, clock = make({PLAIN: [json_response({}), json_response({})]})
        polite.get(PLAIN)
        polite.get(PLAIN)
        self.assertEqual(clock.slept, [CFG.default_host_interval_s])

    def test_polite_breaker_trips_after_n_failures(self):
        polite, inner, _ = make({GH: [status_response(500)] * CFG.breaker_failures})
        for _ in range(CFG.breaker_failures):
            polite.get(GH)
        self.assertTrue(polite.tripped("greenhouse.io"))
        sent = len(inner.requests)
        with self.assertRaises(HostTripped) as ctx:
            polite.get(GH)
        self.assertEqual(ctx.exception.bucket, "greenhouse.io")
        self.assertEqual(len(inner.requests), sent)
        self.assertEqual(inner.unrouted, [])

    def test_polite_host_tripped_is_not_a_fetch_error(self):
        self.assertFalse(issubclass(HostTripped, FetchError))

    def test_polite_one_failure_short_of_the_limit_does_not_trip(self):
        polite, _, _ = make({GH: [status_response(503)] * (CFG.breaker_failures - 1)})
        for _ in range(CFG.breaker_failures - 1):
            polite.get(GH)
        self.assertFalse(polite.tripped("greenhouse.io"))

    def test_polite_fetch_errors_403_and_5xx_count(self):
        for failure in (FetchError("timeout", "slow"), status_response(403), status_response(500),
                        status_response(502)):
            polite, _, _ = make({GH: [failure] * CFG.breaker_failures})
            for _ in range(CFG.breaker_failures):
                try:
                    polite.get(GH)
                except FetchError:
                    pass
            self.assertTrue(polite.tripped("greenhouse.io"), repr(failure))

    def test_polite_a_fetch_error_still_reaches_the_caller(self):
        polite, _, _ = make({GH: FetchError("network", "down")})
        with self.assertRaises(FetchError):
            polite.get(GH)

    def test_polite_a_success_resets_the_failure_count(self):
        n = CFG.breaker_failures - 1
        routes = [status_response(500)] * n + [json_response({})] + [status_response(500)] * n
        polite, _, _ = make({GH: routes})
        for _ in range(len(routes)):
            polite.get(GH)
        self.assertFalse(polite.tripped("greenhouse.io"))

    def test_polite_304_and_redirects_reset_the_count(self):
        n = CFG.breaker_failures - 1
        for ok in (status_response(304), status_response(301), json_response({})):
            routes = [status_response(500)] * n + [ok] + [status_response(500)] * n
            polite, _, _ = make({GH: routes})
            for _ in range(len(routes)):
                polite.get(GH)
            self.assertFalse(polite.tripped("greenhouse.io"))

    def test_polite_three_requests_to_one_host_accumulate_spacing(self):
        polite, _, clock = make({GH: [json_response({})] * 3})
        for _ in range(3):
            polite.get(GH)
        gap = CFG.host_interval_s["greenhouse.io"]
        self.assertEqual(clock.slept, [gap, gap])
        self.assertAlmostEqual(clock.now, 1000.0 + 2 * gap)

    def test_polite_a_tripped_bucket_raises_without_sleeping(self):
        polite, _, clock = make({GH: status_response(429), GH2: json_response({})})
        polite.get(GH)
        with self.assertRaises(HostTripped):
            polite.get(GH2)
        self.assertEqual(clock.slept, [])

    def test_polite_401_and_408_leave_the_count_unchanged(self):
        n = CFG.breaker_failures - 1
        for odd in (401, 408, 400):
            routes = [status_response(500)] * n + [status_response(odd)] + [status_response(500)]
            polite, _, _ = make({GH: routes})
            for _ in range(len(routes)):
                polite.get(GH)
            self.assertTrue(polite.tripped("greenhouse.io"), odd)
            polite, _, _ = make({GH: routes[:n + 1]})
            for _ in range(n + 1):
                polite.get(GH)
            self.assertFalse(polite.tripped("greenhouse.io"), odd)

    def test_polite_follows_a_redirect_chain_across_hosts(self):
        hop1 = Response(301, GH, b"", {"location": LEVER})
        hop2 = Response(302, LEVER, b"", {"location": "/v0/postings/final"})
        final = "https://api.lever.co/v0/postings/final"
        polite, inner, clock = make({GH: hop1, LEVER: hop2, final: json_response({"ok": 1})})
        r = polite.get(GH, {"Authorization": "secret", "Accept": "application/json"}, follow_redirects=True)
        self.assertEqual(r.status, 200)
        self.assertEqual(inner.requests, [GH, LEVER, final])
        self.assertEqual(clock.slept, [CFG.host_interval_s["lever.co"]])  # hop 3 waits behind hop 2, same bucket
        # the inner client is never asked to follow, so every hop passed through the wrapper
        self.assertEqual(inner.unrouted, [])
        # a cross-host hop does not carry the caller's own headers
        self.assertEqual(inner.calls[0][2]["Authorization"], "secret")
        self.assertNotIn("Authorization", inner.calls[1][2])
        self.assertEqual(inner.calls[1][2]["User-Agent"], CFG.user_agent)

    def test_polite_does_not_follow_unless_asked(self):
        polite, inner, _ = make({GH: Response(301, GH, b"", {"location": LEVER})})
        self.assertEqual(polite.get(GH).status, 301)
        self.assertEqual(inner.requests, [GH])

    def test_polite_stops_after_three_hops(self):
        urls = [f"https://jobs.example.org/{i}" for i in range(6)]
        routes = {u: Response(302, u, b"", {"location": urls[i + 1]}) for i, u in enumerate(urls[:-1])}
        polite, inner, _ = make(routes)
        r = polite.get(urls[0], follow_redirects=True)
        self.assertEqual(r.status, 302)
        self.assertEqual(inner.requests, urls[:4])  # the first request and three hops

    def test_polite_a_redirect_into_a_tripped_bucket_is_refused(self):
        polite, inner, _ = make({GH: status_response(429), LEVER: Response(301, LEVER, b"", {"location": GH2})})
        polite.get(GH)
        with self.assertRaises(HostTripped) as ctx:
            polite.get(LEVER, follow_redirects=True)
        self.assertEqual(ctx.exception.bucket, "greenhouse.io")
        self.assertEqual(inner.requests, [GH, LEVER])

    def test_polite_download_counts_statuses_like_get(self):
        import tempfile
        from pathlib import Path
        n = CFG.breaker_failures - 1
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "x"

            def run(statuses):
                polite, _, _ = make({GH: [status_response(c) for c in statuses]})
                for _ in statuses:
                    try:
                        polite.download(GH, dest)
                    except FetchError:
                        pass
                return polite

            self.assertTrue(run([429]).tripped("greenhouse.io"))
            self.assertTrue(run([500] * CFG.breaker_failures).tripped("greenhouse.io"))
            self.assertTrue(run([403] * CFG.breaker_failures).tripped("greenhouse.io"))
            for reset in (404, 410):
                self.assertFalse(run([500] * n + [reset] + [500] * n).tripped("greenhouse.io"), reset)
            # other 4xx change nothing
            self.assertTrue(run([500] * n + [401, 500]).tripped("greenhouse.io"))
            self.assertFalse(run([500] * n + [401]).tripped("greenhouse.io"))

    def test_polite_download_too_large_is_not_a_host_failure(self):
        import tempfile
        from pathlib import Path
        polite, _, _ = make({GH: [FetchError("too_large", "over limit")] * (CFG.breaker_failures * 2)})
        with tempfile.TemporaryDirectory() as d:
            for _ in range(CFG.breaker_failures * 2):
                with self.assertRaises(FetchError):
                    polite.download(GH, Path(d) / "x")
        self.assertFalse(polite.tripped("greenhouse.io"))

    def test_polite_a_too_large_get_is_not_a_host_failure(self):
        polite, _, _ = make({GH: [FetchError("too_large", "over limit")] * (CFG.breaker_failures * 2)})
        for _ in range(CFG.breaker_failures * 2):
            with self.assertRaises(FetchError):
                polite.get(GH)
        self.assertFalse(polite.tripped("greenhouse.io"))

    def test_polite_a_404_is_not_a_failure(self):
        polite, _, _ = make({GH: [status_response(404)] * (CFG.breaker_failures * 2)})
        for _ in range(CFG.breaker_failures * 2):
            polite.get(GH)
        self.assertFalse(polite.tripped("greenhouse.io"))

    def test_polite_a_404_or_410_resets_the_count(self):
        n = CFG.breaker_failures - 1
        for gone in (status_response(404), status_response(410)):
            routes = [status_response(500)] * n + [gone] + [status_response(500)] * n
            polite, _, _ = make({GH: routes})
            for _ in range(len(routes)):
                polite.get(GH)
            self.assertFalse(polite.tripped("greenhouse.io"))

    def test_polite_a_429_trips_at_once(self):
        polite, inner, _ = make({GH: status_response(429)})
        r = polite.get(GH)
        self.assertEqual(r.status, 429)
        self.assertTrue(polite.tripped("greenhouse.io"))
        with self.assertRaises(HostTripped):
            polite.get(GH)
        self.assertEqual(len(inner.requests), 1)

    def test_polite_no_request_to_a_tripped_bucket_even_for_another_url(self):
        polite, inner, _ = make({GH: status_response(429), GH2: json_response({})})
        polite.get(GH)
        with self.assertRaises(HostTripped):
            polite.get(GH2)
        with self.assertRaises(HostTripped):
            polite.post(GH2, b"{}")
        self.assertEqual(inner.requests, [GH])
        self.assertEqual(inner.unrouted, [])

    def test_polite_a_tripped_bucket_leaves_other_buckets_alone(self):
        polite, _, _ = make({GH: status_response(429), LEVER: json_response({})})
        polite.get(GH)
        self.assertEqual(polite.get(LEVER).status, 200)
        self.assertFalse(polite.tripped("lever.co"))

    def test_polite_post_and_download_are_spaced_and_counted(self):
        import tempfile
        from pathlib import Path
        polite, inner, clock = make({("POST", GH): [json_response({}), status_response(429)],
                                     GH2: b"data"})
        polite.post(GH, b"{}")
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(polite.download(GH2, Path(d) / "x"), 4)
        self.assertEqual(clock.slept, [CFG.host_interval_s["greenhouse.io"]])
        polite.post(GH, b"{}")
        self.assertTrue(polite.tripped("greenhouse.io"))
        for call in inner.calls:
            self.assertEqual({k.lower(): v for k, v in call[2].items()}["user-agent"], CFG.user_agent)


if __name__ == "__main__":
    unittest.main()
