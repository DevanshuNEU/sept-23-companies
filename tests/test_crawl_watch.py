"""Weekly watched careers pages and the careers_changed item (issue #21, Task 14)."""
import dataclasses
import json
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from outreach.crawl.cadence import add_seconds
from outreach.crawl.config import load_crawl_config
from outreach.crawl.polite import HostTripped
from outreach.crawl.watch import WatchReport, page_fingerprint, usable_text, watch_pages
from outreach.net import FetchError, Response
from tests.fakes import FakeClient, status_response, text_response
from tests.storetest import fresh_store, seed_company

FIXTURES = Path(__file__).parent / "fixtures" / "crawl"
A = (FIXTURES / "careers_a.html").read_text(encoding="utf-8")
CHANGED = (FIXTURES / "careers_a_changed.html").read_text(encoding="utf-8")
CHALLENGE = (FIXTURES / "careers_challenge.html").read_text(encoding="utf-8")
URL = "https://acme.example/careers"
T0 = "2026-10-22T08:00:00Z"
WEEK = 604800
DAY = 86400
MIN_CHARS = 200

JS_ONLY = ("<html><head><title>Acme</title></head><body><div id=\"root\"></div>"
           "<noscript>You need a browser with scripting.</noscript><script src=\"/app.js\"></script></body></html>")
# Another opening, so the second fetch of a changing page can disagree with the first.
THIRD = CHANGED.replace("Firmware Engineer, Early Career", "Data Engineer, New Grad")


def page(markup: str) -> Response:
    return text_response(markup, headers={"content-type": "text/html"})


def seconds_between(start: str, end: str) -> int:
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return int((datetime.strptime(end, fmt) - datetime.strptime(start, fmt)).total_seconds())


def at(seconds: int) -> str:
    return add_seconds(T0, seconds)


class WatchCase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.cfg = load_crawl_config()
        self.clock = [T0]
        self.company = seed_company(self.conn, "Acme")
        self.watch(self.company)

    def watch(self, company_id, url=URL, **cols):
        self.conn.execute("INSERT INTO watched_page(company_id, url) VALUES (?, ?)", (company_id, url))
        for k, v in cols.items():
            self.conn.execute(f"UPDATE watched_page SET {k} = ? WHERE company_id = ?", (v, company_id))

    def run_at(self, seconds: int, routes, *, company_url=None, **kw):
        """One pass at T0 + seconds against a fake client; returns the report. The client stays in self.client."""
        self.clock[0] = at(seconds)
        self.client = FakeClient(routes)
        report = watch_pages(self.conn, self.client, cfg=kw.pop("cfg", self.cfg), now_fn=lambda: self.clock[0], **kw)
        self.assertEqual(self.client.unrouted, [])
        return report

    def row(self, company_id=None):
        return self.conn.execute("SELECT * FROM watched_page WHERE company_id = ?",
                                 (company_id or self.company,)).fetchone()

    def items(self, kind="careers_changed"):
        return self.conn.execute("SELECT * FROM review_item WHERE kind = ? ORDER BY review_id", (kind,)).fetchall()

    def baseline(self, markup=A):
        report = self.run_at(0, {URL: page(markup)})
        self.assertEqual(report.baseline, 1)


class FingerprintTests(unittest.TestCase):
    def test_fingerprint_ignores_dates_and_copyright_year(self):
        base = "<p>Open roles</p><p>Software Engineer</p>"
        # The same label with two different dates is one fingerprint.
        for label in ("Updated {}", "Posted {}"):
            for pair in (("2026-10-18", "2026-10-25"), ("October 18, 2026", "November 2, 2026"),
                         ("18 October 2026", "2 November 2026"), ("10/18/2026", "11/2/2026"),
                         ("3 days ago", "2 weeks ago"), ("1 hour ago", "5 months ago")):
                a, b = (f"<p>{base}{label.format(x)}</p>" for x in pair)
                self.assertEqual(page_fingerprint(a), page_fingerprint(b), pair)
        for pair in (("Copyright 2026 Acme", "Copyright 2027 Acme"), ("© 2026 Acme", "© 2027 Acme"),
                     ("(c) 2026 Acme", "(C) 2027 Acme")):
            self.assertEqual(page_fingerprint(f"<p>{base}</p><footer>{pair[0]}</footer>"),
                             page_fingerprint(f"<p>{base}</p><footer>{pair[1]}</footer>"), pair)

    def test_the_shipped_fixtures_differ_only_by_an_opening(self):
        self.assertNotEqual(page_fingerprint(A), page_fingerprint(CHANGED))
        no_opening = CHANGED.replace('    <li class="job">Firmware Engineer, Early Career - Boston, MA</li>\n', "")
        self.assertEqual(page_fingerprint(A), page_fingerprint(no_opening))

    def test_fingerprint_ignores_scripts_and_styles(self):
        a = "<html><head><style>p{color:red}</style><script>var a=1</script></head><body><p>Roles</p></body></html>"
        b = "<html><head><style>p{color:blue}</style><script>var a=2; ship(3)</script></head><body><p>Roles</p></body></html>"
        self.assertEqual(page_fingerprint(a), page_fingerprint(b))

    def test_fingerprint_sees_a_new_opening(self):
        self.assertNotEqual(page_fingerprint("<ul><li>Software Engineer</li></ul>"),
                            page_fingerprint("<ul><li>Software Engineer</li><li>Data Engineer</li></ul>"))

    def test_fingerprint_keeps_a_standalone_year(self):
        self.assertNotEqual(page_fingerprint("<p>2027 new grad roles</p>"), page_fingerprint("<p>2028 new grad roles</p>"))
        self.assertNotEqual(page_fingerprint("<p>Class of 2026</p>"), page_fingerprint("<p>Class of 2027</p>"))

    def test_fingerprint_is_case_and_whitespace_blind_and_a_sha256(self):
        a = page_fingerprint("<p>Software   Engineer</p>")
        self.assertEqual(a, page_fingerprint("<P>software engineer</P>"))
        self.assertRegex(a, r"^[0-9a-f]{64}$")

    def test_challenge_page_is_not_usable(self):
        self.assertFalse(usable_text(CHALLENGE, MIN_CHARS))
        long_challenge = "<p>Just a moment...</p>" + "<p>" + "Filler text about nothing at all. " * 20 + "</p>"
        self.assertFalse(usable_text(long_challenge, 50))
        for phrase in ("Just a moment", "Enable JavaScript", "Access Denied", "captcha", "Checking your browser",
                       "Verify you are human", "Pardon our interruption"):
            self.assertFalse(usable_text(f"<p>{phrase} to continue.</p>" + "<p>padding words here</p>" * 3, 20), phrase)

    def test_a_long_real_page_that_mentions_recaptcha_in_its_footer_is_usable(self):
        footer = "<footer>This site is protected by reCAPTCHA.</footer>"
        self.assertTrue(usable_text(A.replace("</body>", footer + "</body>") + "<p>" + "More openings. " * 150 + "</p>",
                                    MIN_CHARS))

    def test_a_long_real_page_with_a_human_check_notice_in_its_footer_is_usable(self):
        notice = "<footer>Verify you are human when you apply. Checking your browser may take a moment.</footer>"
        long_page = A.replace("</body>", notice + "</body>") + "<p>" + "More openings. " * 150 + "</p>"
        self.assertTrue(usable_text(long_page, MIN_CHARS))

    def test_short_page_is_not_usable(self):
        self.assertFalse(usable_text("<p>Hello</p>", MIN_CHARS))
        self.assertFalse(usable_text(JS_ONLY, MIN_CHARS))
        self.assertTrue(usable_text(A, MIN_CHARS))
        self.assertTrue(usable_text("<p>" + "x" * 200 + "</p>", 200))
        self.assertFalse(usable_text("<p>" + "x" * 199 + "</p>", 200))


class WatchTests(WatchCase):
    def test_first_read_is_a_baseline_with_no_item(self):
        report = self.run_at(0, {URL: page(A)})
        self.assertEqual(report, WatchReport(baseline=1, unchanged=0, changed=0, unstable=0, error=0))
        row = self.row()
        self.assertEqual(row["fingerprint"], page_fingerprint(A))
        self.assertEqual((row["last_fetch_at"], row["last_change_at"], row["error_streak"]), (T0, None, 0))
        self.assertEqual(row["next_fetch_at"], at(WEEK))
        self.assertEqual(self.items(), [])
        self.assertEqual(self.client.requests, [URL])

    def test_same_page_is_unchanged_and_not_due_for_7_days(self):
        self.baseline()
        report = self.run_at(DAY, {})  # nothing routed: any request would be unrouted
        self.assertEqual(report, WatchReport(0, 0, 0, 0, 0))
        self.assertEqual(self.client.requests, [])
        report = self.run_at(WEEK - 1, {})
        self.assertEqual(self.client.requests, [])
        report = self.run_at(WEEK, {URL: page(A)})
        self.assertEqual((report.unchanged, report.baseline, report.changed), (1, 0, 0))
        row = self.row()
        self.assertEqual((row["last_fetch_at"], row["next_fetch_at"], row["last_change_at"]),
                         (at(WEEK), at(2 * WEEK), None))
        self.assertEqual(self.items(), [])

    def test_a_date_only_change_is_unchanged(self):
        self.baseline()
        report = self.run_at(WEEK, {URL: page(A.replace("October 18, 2026", "October 25, 2026")
                                                .replace("Copyright 2026", "Copyright 2027"))})
        self.assertEqual((report.unchanged, report.changed), (1, 0))
        self.assertEqual(self.client.requests, [URL])

    def test_changed_page_raises_one_careers_changed_item(self):
        self.baseline()
        report = self.run_at(WEEK, {URL: [page(CHANGED), page(CHANGED)]})
        self.assertEqual(report, WatchReport(baseline=0, unchanged=0, changed=1, unstable=0, error=0))
        self.assertEqual(self.client.requests, [URL, URL])  # fetched once more at once
        row = self.row()
        fp = page_fingerprint(CHANGED)
        self.assertEqual((row["fingerprint"], row["last_change_at"], row["last_fetch_at"]), (fp, at(WEEK), at(WEEK)))
        (item,) = self.items()
        self.assertEqual((item["company_id"], item["board_id"], item["raised_by"], item["state"]),
                         (self.company, None, "crawler", "open"))
        self.assertEqual(item["evidence_key"], fp[:16])
        self.assertEqual(item["reason"], f"Careers page changed: {URL}")

    def test_the_same_change_twice_raises_no_second_item(self):
        self.baseline()
        self.run_at(WEEK, {URL: [page(CHANGED), page(CHANGED)]})
        self.conn.execute("UPDATE watched_page SET fingerprint = ?, next_fetch_at = NULL WHERE company_id = ?",
                          (page_fingerprint(A), self.company))
        report = self.run_at(2 * WEEK, {URL: [page(CHANGED), page(CHANGED)]})
        self.assertEqual(report.changed, 1)
        self.assertEqual(len(self.items()), 1)

    def test_unstable_page_raises_nothing(self):
        self.baseline()
        report = self.run_at(WEEK, {URL: [page(CHANGED), page(THIRD)]})
        self.assertEqual(report, WatchReport(baseline=0, unchanged=0, changed=0, unstable=1, error=0))
        row = self.row()
        self.assertEqual((row["fingerprint"], row["last_change_at"], row["error_streak"]),
                         (page_fingerprint(A), None, 0))
        self.assertEqual(row["next_fetch_at"], at(2 * WEEK))
        self.assertEqual(self.items(), [])

    def test_a_failing_second_fetch_concludes_nothing(self):
        self.baseline()
        fp = page_fingerprint(A)
        for i, second in enumerate((status_response(500), FetchError("timeout", "slow"), page(CHALLENGE)), start=1):
            with self.subTest(second=i):
                self.conn.execute("UPDATE watched_page SET next_fetch_at = NULL")
                report = self.run_at(i * WEEK, {URL: [page(CHANGED), second]})
                self.assertEqual((report.error, report.changed, report.unstable), (1, 0, 0))
                self.assertEqual(self.client.requests, [URL, URL])
                row = self.row()
                self.assertEqual((row["fingerprint"], row["last_change_at"], row["error_streak"]), (fp, None, i))
                self.assertEqual(self.items(), [])

    def test_a_host_tripping_on_the_second_fetch_concludes_nothing(self):
        self.baseline()
        report = self.run_at(WEEK, {URL: [page(CHANGED), HostTripped("acme.example")]})
        self.assertEqual((report.deferred, report.changed, report.error), (1, 0, 0))
        row = self.row()
        self.assertEqual((row["fingerprint"], row["last_change_at"], row["error_streak"]),
                         (page_fingerprint(A), None, 0))
        self.assertEqual(row["next_fetch_at"], at(WEEK + self.cfg.defer_s))
        self.assertEqual(self.items(), [])

    def test_an_unexpected_exception_on_one_page_is_counted_and_the_pass_continues(self):
        other = seed_company(self.conn, "Other")
        other_url = "https://other.example/careers"
        self.watch(other, other_url)
        report = self.run_at(0, {URL: RuntimeError("parser blew up"), other_url: page(A)})
        self.assertEqual((report.error, report.baseline), (1, 1))
        self.assertEqual(self.client.requests, [URL, other_url])
        bad = self.row()
        self.assertEqual((bad["error_streak"], bad["fingerprint"]), (1, ""))
        self.assertEqual(bad["next_fetch_at"], at(DAY))
        self.assertEqual(self.row(other)["fingerprint"], page_fingerprint(A))
        run = self.conn.execute("SELECT status FROM job_run WHERE job = 'watch_pages'").fetchone()
        self.assertEqual(run["status"], "ok")

    def test_403_and_challenge_never_raise_a_change(self):
        self.baseline()
        fp = page_fingerprint(A)
        for i, answer in enumerate((status_response(403), page(CHALLENGE), status_response(500),
                                    FetchError("timeout", "slow")), start=1):
            with self.subTest(answer=i):
                t = i * WEEK
                self.conn.execute("UPDATE watched_page SET next_fetch_at = NULL WHERE company_id = ?", (self.company,))
                report = self.run_at(t, {URL: answer})
                self.assertEqual((report.error, report.changed, report.unchanged), (1, 0, 0))
                row = self.row()
                self.assertEqual((row["fingerprint"], row["last_change_at"], row["error_streak"]), (fp, None, i))
                self.assertEqual(row["last_fetch_at"], at(t))
                self.assertEqual(self.items(), [])

    def test_error_backoff_doubles_to_7_days(self):
        self.baseline()
        waits = []
        for _ in range(6):
            due = self.conn.execute("SELECT next_fetch_at FROM watched_page").fetchone()[0]
            self.run_at(seconds_between(T0, due), {URL: status_response(503)})
            row = self.row()
            waits.append(seconds_between(row["last_fetch_at"], row["next_fetch_at"]))
        self.assertEqual(waits, [DAY, 2 * DAY, 4 * DAY, WEEK, WEEK, WEEK])
        self.assertEqual(self.row()["error_streak"], 6)

    def test_a_good_read_after_errors_resets_the_streak(self):
        self.baseline()
        self.run_at(WEEK, {URL: status_response(500)})
        self.assertEqual(self.row()["error_streak"], 1)
        report = self.run_at(WEEK + DAY, {URL: page(A)})
        self.assertEqual(report.unchanged, 1)
        row = self.row()
        self.assertEqual((row["error_streak"], row["next_fetch_at"]), (0, at(WEEK + DAY + WEEK)))

    def test_a_page_that_never_had_a_baseline_stays_baseline_after_an_error(self):
        self.run_at(0, {URL: status_response(500)})
        self.assertEqual(self.row()["fingerprint"], "")
        report = self.run_at(DAY, {URL: page(A)})
        self.assertEqual(report.baseline, 1)
        self.assertEqual(self.items(), [])

    def test_closed_and_removed_companies_are_skipped(self):
        closed = seed_company(self.conn, "Closed Co")
        self.conn.execute("UPDATE company SET status = 'closed', close_reason = 'other' WHERE company_id = ?", (closed,))
        removed = seed_company(self.conn, "Removed Co")
        self.conn.execute("UPDATE company SET status = 'removed', remove_reason = 'not a fit' WHERE company_id = ?",
                          (removed,))
        merged = seed_company(self.conn, "Merged Co")
        self.conn.execute("UPDATE company SET status = 'removed', remove_reason = 'merged', merged_into = ? "
                          "WHERE company_id = ?", (self.company, merged))
        urls = {}
        for cid, name in ((closed, "closed"), (removed, "removed"), (merged, "merged")):
            urls[cid] = f"https://{name}.example/careers"
            self.watch(cid, urls[cid])
        report = self.run_at(0, {URL: page(A)})
        self.assertEqual(report.baseline, 1)
        self.assertEqual(self.client.requests, [URL])
        for cid in urls:
            self.assertIsNone(self.row(cid)["last_fetch_at"])

    def test_no_transaction_is_open_during_a_request(self):
        seen = []

        def route(method, url, headers, body):
            seen.append(self.conn.in_transaction)
            return page(CHANGED if len(seen) >= 2 else A)

        self.run_at(0, {URL: route})
        self.run_at(WEEK, {URL: route})
        self.assertEqual(len(seen), 3)  # baseline, then the changed page and its second fetch
        self.assertEqual(seen, [False, False, False])

    def test_watch_pages_refuses_to_start_inside_a_transaction(self):
        self.conn.execute("BEGIN")
        try:
            with self.assertRaises(ValueError):
                self.run_at(0, {URL: page(A)})
        finally:
            self.conn.execute("ROLLBACK")

    def test_at_most_the_per_tick_limit_is_read_oldest_due_first(self):
        urls = [URL]
        for i in range(3):
            cid = seed_company(self.conn, f"Co {i}")
            u = f"https://co{i}.example/careers"
            self.watch(cid, u, next_fetch_at=at(-(i + 1) * DAY))
            urls.append(u)
        routes = {u: page(A) for u in urls}
        cfg = dataclasses.replace(self.cfg, watch_per_tick=2)
        report = self.run_at(0, routes, cfg=cfg)
        self.assertEqual(report.baseline, 2)
        # Never fetched first (Acme), then the oldest due.
        self.assertEqual(self.client.requests, [URL, "https://co2.example/careers"])
        report = self.run_at(1, routes, cfg=cfg, limit=1)
        self.assertEqual(report.baseline, 1)
        self.assertEqual(self.client.requests, ["https://co1.example/careers"])

    def test_a_tripped_host_defers_the_page_with_no_streak_change(self):
        report = self.run_at(0, {URL: HostTripped("acme.example")})
        self.assertEqual((report.baseline, report.error), (0, 0))
        row = self.row()
        self.assertEqual((row["error_streak"], row["fingerprint"]), (0, ""))
        self.assertEqual(row["next_fetch_at"], at(self.cfg.defer_s))
        self.assertEqual(self.items("flagged"), [])

    def test_one_job_run_of_watch_pages(self):
        self.baseline()
        self.run_at(WEEK, {URL: [page(CHANGED), page(CHANGED)]})
        runs = self.conn.execute("SELECT job, status, summary FROM job_run WHERE job = 'watch_pages' ORDER BY run_id"
                                 ).fetchall()
        self.assertEqual([r["status"] for r in runs], ["ok", "ok"])
        self.assertEqual(json.loads(runs[1]["summary"]),
                         {"baseline": 0, "unchanged": 0, "changed": 1, "unstable": 0, "error": 0, "deferred": 0})


class UnreadablePageTests(WatchCase):
    def flagged(self):
        return self.items("flagged")

    def new_page(self, stored_markup):
        """Start this page over: no items, no streak, and the fingerprint of `stored_markup` (None: nothing stored)."""
        self.conn.execute("DELETE FROM review_item")
        fp = page_fingerprint(stored_markup) if stored_markup else ""
        self.conn.execute("UPDATE watched_page SET fingerprint = ?, error_streak = 0, next_fetch_at = NULL, "
                          "last_fetch_at = NULL, last_change_at = NULL", (fp,))

    def test_a_javascript_only_page_raises_one_flagged_item_at_once(self):
        report = self.run_at(0, {URL: page(JS_ONLY)})
        self.assertEqual(report.error, 1)
        (item,) = self.flagged()
        self.assertTrue(item["reason"].startswith("careers page unreadable"), item["reason"])
        self.assertIn(URL, item["reason"])
        self.assertEqual((item["company_id"], item["board_id"], item["raised_by"]), (self.company, None, "crawler"))
        self.assertEqual(self.row()["fingerprint"], "")
        # Next week: still unreadable, still one item.
        self.conn.execute("UPDATE watched_page SET next_fetch_at = NULL")
        self.run_at(WEEK, {URL: page(JS_ONLY)})
        self.assertEqual(len(self.flagged()), 1)
        self.assertEqual(self.row()["error_streak"], 2)

    def test_three_fetch_errors_in_a_row_raise_one_flagged_item(self):
        self.baseline()
        for i, answer in enumerate((status_response(500), FetchError("network", "down"), status_response(403),
                                    status_response(500)), start=1):
            self.conn.execute("UPDATE watched_page SET next_fetch_at = NULL")
            self.run_at(i * WEEK, {URL: answer})
            self.assertEqual(len(self.flagged()), 0 if i < 3 else 1, f"after error {i}")
        item = self.flagged()[0]
        self.assertTrue(item["reason"].startswith("careers page unreadable"))
        self.assertEqual(self.items("careers_changed"), [])

    def test_a_short_200_on_a_page_with_a_stored_fingerprint_waits_for_three_errors(self):
        self.baseline()
        for i in (1, 2, 3):
            self.conn.execute("UPDATE watched_page SET next_fetch_at = NULL")
            self.run_at(i * WEEK, {URL: page(JS_ONLY)})
            self.assertEqual(len(self.flagged()), 0 if i < 3 else 1, f"after short page {i}")
        self.assertEqual(self.row()["fingerprint"], page_fingerprint(A))

    def test_the_next_usable_read_clears_the_open_item(self):
        cases = {
            "baseline": (None, [page(A)]),
            "unchanged": (A, [page(A)]),
            "changed": (A, [page(CHANGED), page(CHANGED)]),
            "unstable": (A, [page(CHANGED), page(THIRD)]),
        }
        for name, (stored, answers) in cases.items():
            with self.subTest(read=name):
                self.new_page(stored)
                bad = 3 if stored else 1  # a page that read before is flagged on the third error
                for i in range(bad):
                    self.conn.execute("UPDATE watched_page SET next_fetch_at = NULL")
                    self.run_at(i * DAY, {URL: page(JS_ONLY)})
                self.assertEqual([r["state"] for r in self.flagged()], ["open"])
                self.conn.execute("UPDATE watched_page SET next_fetch_at = NULL")
                self.run_at(bad * DAY, {URL: answers})
                (item,) = self.flagged()
                self.assertEqual((item["state"], item["resolution"], item["resolved_at"]),
                                 ("resolved", "cleared", at(bad * DAY)))
                self.assertEqual(item["revision"], 2)

    def test_a_failed_read_does_not_clear_the_item(self):
        self.run_at(0, {URL: page(JS_ONLY)})
        self.conn.execute("UPDATE watched_page SET next_fetch_at = NULL")
        self.run_at(DAY, {URL: status_response(500)})
        self.assertEqual([r["state"] for r in self.flagged()], ["open"])

    def test_clearing_touches_only_that_pages_item(self):
        other = seed_company(self.conn, "Other")
        other_url = "https://other.example/careers"
        self.watch(other, other_url)
        self.run_at(0, {URL: page(JS_ONLY), other_url: page(JS_ONLY)})
        self.assertEqual(len(self.flagged()), 2)
        self.conn.execute("UPDATE watched_page SET next_fetch_at = NULL")
        self.run_at(DAY, {URL: page(A), other_url: page(JS_ONLY)})
        states = {r["company_id"]: r["state"] for r in self.flagged()}
        self.assertEqual(states, {self.company: "resolved", other: "open"})

    def test_a_cleared_item_reopens_when_the_page_fails_again(self):
        self.run_at(0, {URL: page(JS_ONLY)})
        self.conn.execute("UPDATE watched_page SET next_fetch_at = NULL")
        self.run_at(DAY, {URL: page(A)})
        self.assertEqual([r["state"] for r in self.flagged()], ["resolved"])
        for i in (2, 3, 4):  # a stored fingerprint now, so three errors in a row
            self.conn.execute("UPDATE watched_page SET next_fetch_at = NULL")
            self.run_at(i * DAY, {URL: page(JS_ONLY)})
            self.assertEqual([r["state"] for r in self.flagged()], ["resolved" if i < 4 else "open"])
        (item,) = self.flagged()
        self.assertEqual((item["resolution"], item["resolved_at"]), (None, None))

    def test_a_dismissed_unreadable_item_stays_dismissed(self):
        self.run_at(0, {URL: page(JS_ONLY)})
        self.conn.execute("UPDATE review_item SET state = 'resolved', resolution = 'marked_ok', resolved_at = ? "
                          "WHERE kind = 'flagged'", (T0,))
        self.conn.execute("UPDATE watched_page SET next_fetch_at = NULL")
        self.run_at(WEEK, {URL: page(JS_ONLY)})
        self.assertEqual([r["state"] for r in self.flagged()], ["resolved"])

    def test_a_challenge_page_waits_for_three_errors(self):
        for i in range(3):
            self.conn.execute("UPDATE watched_page SET next_fetch_at = NULL")
            self.run_at(i * WEEK, {URL: page(CHALLENGE)})
            self.assertEqual(len(self.flagged()), 0 if i < 2 else 1)


if __name__ == "__main__":
    unittest.main()
