"""Today as a company queue (#9 company-first ruling, P2): what a company row of `GET /api/plan` shows, and the
In flight list under it.

Each row line is derived by the API from data the store already holds: the size and stage facts, the row's role and
its age, the company's open engineering postings, sponsorship and E-Verify, the stored why signals, the contact state
(`outreach.plan.contact`), the newest outbound touch, the email channel rule (`pick_channel`, read only), the people
on record, the people search link and the careers link. Scratch store, no network.
"""
import json

from outreach.outcomes.context import build_context
from outreach.store import log_touch
from tests import apiseed as s
from tests.apitest import ApiCase

DAY = "2026-11-10"  # NOW is Tuesday 10:14 in New York
US = "geoUrn=%5B%22103644278%22%5D&origin=FACETED_SEARCH"
SEARCH = "https://www.linkedin.com/search/results/people/"


def _context(source="plan"):
    return build_context(source=source, lane="level_role", role_kind="engineer", role_age_days=3,
                         ranker_version="rk-1", signals={}, role_named=False)


class QueueCase(ApiCase):
    def setUp(self):
        super().setUp()
        c = self.conn
        self.conn.execute("INSERT INTO daily_list(list_date, built_at, capacity) VALUES (?, ?, 8)",
                          (DAY, "2026-11-10T13:00:00Z"))
        self.n = 0
        # Tessera: everything known, nobody asked yet.
        self.tessera = s.company(c, "Tessera Labs")
        s.evidence(c, self.tessera, "size", "40-60", confidence="estimate", low=40, high=60)
        s.evidence(c, self.tessera, "stage", "seed", confidence="guess")
        s.evidence(c, self.tessera, "everify", "enrolled")
        s.evidence(c, self.tessera, "domain", "tessera.example")
        s.board(c, self.tessera, "lever", "tessera")
        self.role = s.posting(c, self.tessera, "Forward Deployed Engineer", url="https://jobs.lever.co/tessera/1",
                              first_seen_at="2026-11-01T12:00:00Z")
        c.execute("UPDATE posting SET posted_on = '2026-11-04' WHERE posting_id = ?", (self.role,))
        s.decision(c, self.role)
        for title, seen in (("Backend Engineer", "2026-10-20T12:00:00Z"), ("Platform Engineer", "2026-09-20T12:00:00Z"),
                            ("Account Executive", "2026-11-05T12:00:00Z")):
            p = s.posting(c, self.tessera, title, first_seen_at=seen)
            s.decision(c, p, **({"lane": "not_engineering", "level_ok": 0} if title.startswith("Account") else {}))
        s.posting(c, self.tessera, "Old Engineer", first_seen_at="2026-11-02T12:00:00Z",
                  closed_at="2026-11-03T12:00:00Z")
        s.person(c, self.tessera, "Pat Lee", channel="linkedin")
        s.person(c, self.tessera, "Sam Roe", channel="linkedin")
        self.tessera_entry = self.entry(self.tessera, 1, posting_id=self.role, signals=[
            {"name": "live_role", "reason": "Open role at your level", "points": 40.0},
            {"name": "small", "reason": "Small company", "points": 10.2},
            {"name": "everify", "reason": "E-Verify enrolled", "points": 6.0}], role=(self.role, "Forward Deployed Engineer"))
        # Avante: a full wave of 4 asked 6 days ago.
        self.avante = s.company(c, "Avante")
        self.avante_people = [s.person(c, self.avante, name, channel="linkedin", title="Software Engineer")
                              for name in ("Priya Rao", "Omar Diaz", "Jane Doe", "Li Wei")]
        for i, pid in enumerate(self.avante_people):
            self.request(pid, self.avante, f"2026-11-04T1{i}:00:00Z")
        self.avante_entry = self.entry(self.avante, 2)
        # Quill: someone replied.
        self.quill = s.company(c, "Quill")
        quill_person = s.person(c, self.quill, "Ana Bell", channel="linkedin")
        touch = self.request(quill_person, self.quill, "2026-10-01T15:00:00Z")
        c.execute("UPDATE outreach_touch SET replied_at = '2026-10-03T15:00:00Z', reply_kind = 'interested' WHERE touch_id = ?", (touch,))
        self.quill_entry = self.entry(self.quill, 3)

    def entry(self, company_id, rank, *, posting_id=None, signals=(), role=None):
        why = {"lane": "level_role", "reason": "Ranked high", "reason_sources": [], "signals": list(signals),
               "role": {"posting_id": role[0], "title": role[1], "url": "https://jobs.lever.co/tessera/1",
                        "age_days": 3, "location": "Remote"} if role else None,
               "person": None, "channel_reason": "find_someone", "draft_id": None,
               "find_someone": {"search_url": f"{SEARCH}?keywords=x", "handoff": "h"}, "carried_from": None,
               "first_proposed": DAY, "rotation": None, "score": 50.0, "ranker_version": "rk-1"}
        s.score(self.conn, company_id, {"live_role": 40.0})
        return self.conn.execute(
            "INSERT INTO daily_list_entry(list_date, company_id, list_group, rank, state, posting_id, why, proposed_at, "
            "channel) VALUES (?, ?, 'level_role', ?, 'proposed', ?, ?, '2026-11-10T13:00:00Z', 'linkedin_request')",
            (DAY, company_id, rank, posting_id, json.dumps(why))).lastrowid

    def request(self, person_id, company_id, at, source="plan"):
        self.n += 1
        return log_touch(self.conn, person_id=person_id, company_id=company_id, channel="linkedin_request",
                         sent_at=at, context=_context(source), idempotency_key=f"seed:{self.n}")

    def view(self):
        res = self.get("/api/plan")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def row(self, entry_id):
        return next(r for r in self.view()["rows"] if r["entryId"] == entry_id)


class CompanyRowTest(QueueCase):
    def test_a_new_company_row_shows_every_line(self):
        q = self.row(self.tessera_entry)["queue"]
        self.assertEqual(q["sizeLine"], "40 to 60 people (estimate), seed (guess)")
        self.assertEqual(q["roleLine"], "Forward Deployed Engineer, posted 6 days ago")
        self.assertEqual((q["hiring"], q["hiringLine"]), (2, "2 new engineering roles in the last 30 days"))
        self.assertEqual(q["sponsorshipLine"], "Sponsorship not stated, E-Verify enrolled")
        self.assertEqual(q["reasons"], ["Open role at your level", "Small company", "E-Verify enrolled"])
        self.assertEqual(q["standing"], {"status": "new", "text": "New", "reason": "No outreach yet", "asked": 0,
                                         "room": 4, "freeOn": None})
        self.assertIsNone(q["lastTouch"])
        self.assertIsNone(q["emailHint"])
        self.assertEqual((q["peopleOnRecord"], q["peopleLine"]), (2, "2 people on record, none asked"))
        self.assertEqual((q["peopleSearchUrl"], q["searchSaved"]), (f"{SEARCH}?keywords=Tessera%20Labs&{US}", False))
        self.assertEqual(q["careersUrl"], "https://jobs.lever.co/tessera/1")
        self.assertEqual(q["terminalLine"],
                         f"Knock: Tessera Labs (company {self.tessera}), role: Forward Deployed Engineer")

    def test_a_full_wave_waits_and_says_when_it_is_free_again(self):
        q = self.row(self.avante_entry)["queue"]
        self.assertEqual(q["standing"], {"status": "in_flight", "text": "4 asked 6 days ago, free again Nov 18",
                                         "reason": "Request sent 6 days ago, no answer yet", "asked": 4, "room": 0,
                                         "freeOn": "2026-11-18"})
        self.assertEqual(q["lastTouch"], "Request to Li Wei (Engineer) on Nov 4")
        self.assertEqual(q["peopleLine"], "4 people on record, 4 asked")
        self.assertIsNone(q["sizeLine"])
        self.assertEqual((q["hiring"], q["hiringLine"]), (0, "No new engineering roles in the last 30 days"))
        self.assertEqual(q["terminalLine"], f"Knock: Avante (company {self.avante})")
        self.assertIsNone(q["careersUrl"])  # no posting, board, watched page or domain

    def test_two_asked_today_leaves_room_for_two_more(self):
        for pid in self.avante_people[:2]:
            self.request(pid, self.tessera, "2026-11-10T14:00:00Z")
        q = self.row(self.tessera_entry)["queue"]
        self.assertEqual(q["standing"]["text"], "2 asked today, room for 2 more, free again Nov 24")
        self.assertEqual((q["standing"]["asked"], q["standing"]["room"]), (2, 2))

    def test_a_reply_holds_the_company(self):
        q = self.row(self.quill_entry)["queue"]
        self.assertEqual((q["standing"]["status"], q["standing"]["text"]), ("replied", "Someone replied"))
        self.assertEqual(q["standing"]["room"], 0)

    def test_a_hold_on_the_company_counts_as_a_reply(self):
        # The sender's hold (an email reply), with no outbound row that says so: still "Someone replied", no room.
        pat = self.conn.execute("SELECT person_id FROM person WHERE company_id = ? ORDER BY person_id",
                                (self.tessera,)).fetchone()[0]
        thread = s.touch(self.conn, pat)
        self.conn.execute("UPDATE company SET status = 'working', held_by_touch = ?, held_at = '2026-11-09T12:00:00Z' WHERE company_id = ?",
                          (thread, self.tessera))
        q = self.row(self.tessera_entry)["queue"]
        self.assertEqual((q["standing"]["status"], q["standing"]["text"], q["standing"]["room"]),
                         ("replied", "Someone replied", 0))

    def test_the_why_falls_back_to_the_row_reason(self):
        q = self.row(self.avante_entry)["queue"]
        self.assertEqual(q["reasons"], ["Ranked high"])

    def test_a_published_founder_address_gives_the_email_hint(self):
        founder = s.person(self.conn, self.tessera, "Fay Founder", "founder", email="fay@tessera.example",
                           title="Founder")
        self.conn.execute("INSERT INTO person_source(person_id, kind, url, label, quote, email_basis, observed_on, "
                          "recorded_at) VALUES (?, 'email', 'https://tessera.example/about', 'About', "
                          "'write to fay@tessera.example', 'published', '2026-11-01', '2026-11-01T12:00:00Z')",
                          (founder,))
        q = self.row(self.tessera_entry)["queue"]
        self.assertEqual(q["emailHint"], "Email works here: a founder has a published address.")

    def test_a_saved_search_and_a_linkedin_id(self):
        s.alias(self.conn, self.tessera, "linkedin:1304", "linkedin")
        q = self.row(self.tessera_entry)["queue"]
        self.assertEqual((q["peopleSearchUrl"], q["searchSaved"]),
                         (f"{SEARCH}?currentCompany=%5B%221304%22%5D&{US}", False))
        saved = f"{SEARCH}?currentCompany=%5B%221304%22%5D&titleFreeText=engineer"
        s.event(self.conn, self.tessera, "linkedin_search_saved", "2026-11-09T12:00:00Z", actor="devanshu",
                detail={"url": saved})
        q = self.row(self.tessera_entry)["queue"]
        self.assertEqual((q["peopleSearchUrl"], q["searchSaved"]), (saved, True))
        s.event(self.conn, self.tessera, "linkedin_search_saved", "2026-11-09T13:00:00Z", actor="devanshu",
                detail={"url": "https://evil.example/x"})  # never shown: not a LinkedIn people search
        self.assertEqual(self.row(self.tessera_entry)["queue"]["peopleSearchUrl"], saved)

    def test_the_careers_link_falls_back_to_the_board_then_the_page_then_the_domain(self):
        self.conn.execute("UPDATE daily_list_entry SET posting_id = NULL WHERE rowid = ?", (self.tessera_entry,))
        self.assertEqual(self.row(self.tessera_entry)["queue"]["careersUrl"], "https://jobs.lever.co/tessera")
        self.conn.execute("UPDATE board SET active = 0")
        self.conn.execute("INSERT INTO watched_page(company_id, url) VALUES (?, 'https://tessera.example/careers')",
                          (self.tessera,))
        self.assertEqual(self.row(self.tessera_entry)["queue"]["careersUrl"], "https://tessera.example/careers")
        self.conn.execute("DELETE FROM watched_page")
        self.assertEqual(self.row(self.tessera_entry)["queue"]["careersUrl"], "https://tessera.example")

    def test_a_merged_duplicate_counts_for_its_survivor(self):
        dup = s.company(self.conn, "Tessera Labs Inc", merged_into=self.tessera, status="removed")
        other = s.person(self.conn, dup, "Kim Moss", channel="linkedin")
        self.request(other, dup, "2026-11-09T14:00:00Z")
        s.event(self.conn, dup, "linkedin_search_saved", "2026-11-09T12:00:00Z", actor="devanshu",
                detail={"url": f"{SEARCH}?keywords=tessera"})
        q = self.row(self.tessera_entry)["queue"]
        self.assertEqual(q["standing"]["text"], "1 asked 1 day ago, room for 3 more, free again Nov 23")
        self.assertEqual(q["peopleLine"], "3 people on record, 1 asked")
        self.assertEqual(q["peopleSearchUrl"], f"{SEARCH}?keywords=tessera")

    def test_a_person_row_has_no_queue_facts(self):
        pid = s.person(self.conn, self.quill, "Ned Ray", channel="linkedin")
        self.conn.execute("UPDATE daily_list_entry SET person_id = ? WHERE rowid = ?", (pid, self.quill_entry))
        self.assertIsNone(self.row(self.quill_entry)["queue"])


class InFlightTest(QueueCase):
    def test_in_flight_lists_waiting_and_replied_companies_with_the_date_free_again(self):
        elsewhere = s.company(self.conn, "Elsewhere")
        e = s.person(self.conn, elsewhere, "Eve Long", channel="linkedin")
        self.request(e, elsewhere, "2026-11-07T15:00:00Z", source="own")
        old = s.company(self.conn, "Old Co")
        o = s.person(self.conn, old, "Olga Hart", channel="linkedin")
        self.request(o, old, "2026-10-01T15:00:00Z")  # no answer for 40 days: stale, not in flight
        done = s.company(self.conn, "Done Co", status="closed", close_reason="interview")
        d = s.person(self.conn, done, "Dee Park", channel="linkedin")
        t = self.request(d, done, "2026-10-01T15:00:00Z")
        self.conn.execute("UPDATE outreach_touch SET replied_at = '2026-10-02T15:00:00Z', reply_kind = 'interested' WHERE touch_id = ?", (t,))
        flight = self.view()["inFlight"]
        self.assertEqual([(f["company"]["name"], f["status"], f["freeOn"], f["text"]) for f in flight], [
            ("Quill", "replied", None, "Someone replied"),
            ("Avante", "in_flight", "2026-11-18", "4 asked 6 days ago, free again Nov 18"),
            ("Elsewhere", "in_flight", "2026-11-21", "1 asked 3 days ago, room for 3 more, free again Nov 21"),
        ])
        self.assertEqual(flight[1]["reason"], "Request sent 6 days ago, no answer yet")

    def test_tomorrow_counts_companies(self):
        text = self.view()["tomorrow"]["text"]
        self.assertRegex(text, r"Room for \d+ compan(y|ies)\.")
