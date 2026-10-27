"""The people plan read: `GET /api/plan` (#11 Task 10).

The plan is #15's (`daily_list`, `daily_list_entry` with its `why`, `linkedin_draft`); the API derives every field the
page shows from it and from the touches: the row state, the reason, the search link, tomorrow's line. Scratch store,
no network, no model.
"""
import json
import time
from datetime import date
from pathlib import Path
from unittest import mock

from outreach.drafts.checks import load_banned
from outreach.outcomes.context import build_context
from outreach.outcomes.windows import next_weekday, ny_day
from outreach.plan.build import build_plan
from outreach.plan.capacity import linkedin_capacity, plan_capacity
from outreach.plan.config import default_plan_config
from outreach.plan.drafts import confirm_reason, save_linkedin_draft
from outreach.plan.notecheck import load_unconfirmed
from outreach.api.queries.plan import tomorrow_text as plan_text
from outreach.plan.prospects import HANDOFF
from outreach.ranking.common import canonical_ids
from outreach.store import log_touch, transaction
from tests import apiseed as s
from tests.apitest import NOW, ApiCase

FIX = Path(__file__).resolve().parent / "fixtures" / "plan"
CFG = default_plan_config()
BANNED, _ = load_banned(FIX / "banned_phrases.txt", scopes=("both", "dm"))
UNCONFIRMED = load_unconfirmed(FIX / "me.md")
DAY = "2026-11-10"  # NOW is Tuesday 10:14 in New York
EARLIER = "2026-11-10T14:00:00Z"
REASON = "I read your post on the queue rewrite"
NOTE = f"Hi Lena, {REASON} and built a similar job runner. I would like to hear how your team works."
SEARCH = "https://www.linkedin.com/search/results/people/?keywords=Cerebrium%20software%20engineer"


def why(lane: str = "level_role", *, reason: str = "Hiring a new grad engineer", posting: tuple | None = None,
        person: tuple | None = None, find: bool = False, carried_from: str | None = None,
        first_proposed: str = DAY) -> dict:
    """A `why` with exactly #15's keys. `posting` is (id, title, url); `person` is (id, name, found_on url)."""
    role = None
    if posting is not None:
        role = {"posting_id": posting[0], "title": posting[1], "url": posting[2], "age_days": 3,
                "location": "Remote"}
    who = None
    if person is not None:
        who = {"person_id": person[0], "name": person[1], "title": "Software Engineer", "role_kind": "engineer",
               "fit": "team", "employer_check": "fresh",
               "found_on": {"label": "Team page", "url": person[2]} if person[2] else None}
    return {
        "lane": lane, "reason": reason,
        "reason_sources": [{"label": "Posting", "url": role["url"]}] if role else [],
        "signals": [], "role": role, "person": who, "channel_reason": "linkedin_default",
        "find_someone": {"search_url": SEARCH, "handoff": HANDOFF} if find else None,
        "draft_id": None, "carried_from": carried_from, "first_proposed": first_proposed, "rotation": None,
        "score": 50.0, "ranker_version": "rk-1",
    }


class PlanCase(ApiCase):
    """A plan of 7 rows for Tuesday 2026-11-10, as #33's default fixture: rows 1 and 2 done, row 5 with nobody,
    row 7 an email row. Tests of the writes reuse it."""

    def setUp(self):
        super().setUp()
        c = self.conn
        self.ids = {}
        self.people = {}
        self.postings = {}
        rows = [
            ("Resend", "Dana Whitfield", "Software Engineer, New Grad", "level_role"),
            ("Baseten", "Tomas Reyes", "Forward Deployed Engineer, New Grad", "level_role"),
            ("Pylon", "Lena Ortiz", "Software Engineer I", "level_role"),
            ("Inngest", "Jonah Park", "Software Engineer II", "level_role"),
            ("Cerebrium", None, "Member of Technical Staff", "level_role"),
            ("Turso", "Ines Duarte", "Software Engineering Intern", "intern_junior"),
            ("Mintlify", "Mia Chen", None, "reason_no_role"),
        ]
        for rank, (company, name, title, lane) in enumerate(rows, 1):
            cid = s.company(c, company)
            self.ids[company] = cid
            s.score(c, cid, {"role": 40.0, "team": 10.0}, lanes=(lane,))
            pid = post = None
            if title is not None:
                post = s.posting(c, cid, title, url=f"https://jobs.example/{company.lower()}")
                self.postings[company] = post
            channel = None
            if name is not None:
                if company == "Mintlify":
                    pid = s.person(c, cid, name, "founder", channel="email")
                    channel = "email"
                else:
                    slug = name.lower().replace(" ", "-")
                    pid = s.person(c, cid, name, "founder" if company == "Turso" else "engineer",
                                   channel="linkedin", linkedin_url=f"https://www.linkedin.com/in/{slug}")
                    channel = "linkedin_request"
                self.people[company] = pid
            self.entry(cid, rank, person_id=pid, channel=channel, posting_id=post, list_group=lane,
                       why=why(lane, posting=(post, title, f"https://jobs.example/{company.lower()}") if post else None,
                               person=(pid, name, f"https://{company.lower()}.example/team") if pid else None,
                               find=pid is None))
        for company in ("Resend", "Baseten"):
            self.request(company)
            self.apply(company)
        self.lena_note = save_linkedin_draft(
            c, person_id=self.people["Pylon"], body=NOTE, reason_line=REASON, saved_by="claude",
            facts=[{"text": "the queue rewrite", "label": "Blog", "url": "https://pylon.example/blog", "in_note": True},
                   {"text": "a team of 12", "label": "Team page", "url": "https://pylon.example/team",
                    "in_note": False}],
            cfg=CFG, banned=BANNED, unconfirmed=UNCONFIRMED)
        self.lena_note = confirm_reason(c, self.lena_note.draft_id, self.lena_note.revision, cfg=CFG, banned=BANNED,
                                        unconfirmed=UNCONFIRMED)
        self.jonah_note = save_linkedin_draft(
            c, person_id=self.people["Inngest"], body="Hi Jonah, I saw the Inngest step functions talk.",
            reason_line="I saw the Inngest step functions talk", saved_by="claude", facts=[], cfg=CFG,
            banned=BANNED, unconfirmed=UNCONFIRMED)
        self.email_touch = s.touch(c, self.people["Mintlify"])
        self.email_message = s.message(c, self.email_touch, 0, "drafted")

    # ------------------------------------------------------------------------------------------- seeding

    def entry(self, company_id: int, rank: int, *, person_id=None, channel=None, posting_id=None,
              list_group="level_role", why=None, state="proposed", list_date=DAY) -> int:
        self.conn.execute("INSERT INTO daily_list(list_date, built_at, capacity) VALUES (?, ?, 7) "
                          "ON CONFLICT(list_date) DO NOTHING", (list_date, "2026-11-10T13:00:00Z"))
        return self.conn.execute(
            "INSERT INTO daily_list_entry(list_date, company_id, list_group, rank, state, posting_id, why, proposed_at, "
            "decided_at, person_id, channel) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (list_date, company_id, list_group, rank, state, posting_id, json.dumps(why or {}),
             "2026-11-10T13:00:00Z", None if state == "proposed" else "2026-11-10T13:30:00Z", person_id,
             channel)).lastrowid

    def context(self, source: str = "plan") -> dict:
        return build_context(source=source, lane="level_role", role_kind="engineer", role_age_days=3,
                             ranker_version="rk-1", signals={}, role_named=True)

    def request(self, company: str, at: str = EARLIER, source: str = "plan") -> int:
        return log_touch(self.conn, person_id=self.people[company], company_id=self.ids[company],
                         channel="linkedin_request", sent_at=at, context=self.context(source),
                         idempotency_key=f"seed:{company}:request:{at}:{source}")

    def apply(self, company: str, at: str = EARLIER) -> int:
        return log_touch(self.conn, person_id=None, company_id=self.ids[company], channel="application",
                         posting_id=self.postings[company], sent_at=at, context=self.context(),
                         idempotency_key=f"seed:{company}:apply:{at}")

    def entry_id(self, company: str, list_date: str = DAY) -> int:
        return self.conn.execute("SELECT rowid FROM daily_list_entry WHERE list_date = ? AND company_id = ?",
                                 (list_date, self.ids[company])).fetchone()[0]

    def set_why(self, company: str, text: str) -> None:
        self.conn.execute("UPDATE daily_list_entry SET why = ? WHERE company_id = ?", (text, self.ids[company]))

    # ------------------------------------------------------------------------------------------- reading

    def ok(self, res) -> dict:
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def plan(self) -> dict:
        return self.ok(self.get("/api/plan"))

    def row(self, company: str) -> dict:
        return next(r for r in self.plan()["rows"] if r["company"]["name"] == company)

    def expected_draft(self) -> list[dict]:
        """Tomorrow's preview as #15's own dry run of the next weekday's plan chooses it: its rows in its order, each
        with the names the store holds and the title of the company's best posting. Never empty here."""
        day = date.fromisoformat(next_weekday(ny_day(self.now)))
        result = build_plan(self.conn, today=day, cfg=CFG, read_boards=False, dry_run=True)
        self.assertEqual(result.status, "built")
        out = []
        for row in result.chosen:
            company = self.conn.execute("SELECT name FROM company WHERE company_id = ?", (row.company_id,)).fetchone()
            person = self.conn.execute("SELECT name FROM person WHERE person_id = ?", (row.person_id,)).fetchone()
            role = self.conn.execute(
                "SELECT p.title FROM company_score s JOIN posting p "
                "ON p.posting_id = json_extract(s.signals, '$.best_posting_id') WHERE s.company_id = ?",
                (row.company_id,)).fetchone()
            out.append({"companyName": company[0], "personName": person[0] if person else None,
                        "roleTitle": role[0] if role else None, "channel": row.channel,
                        "findSomeone": row.person_id is None})
        self.assertTrue(out)
        return out


class PlanReadTests(PlanCase):
    def test_default_plan_counts_and_order(self):
        view = self.plan()
        self.assertEqual([r["position"] for r in view["rows"]], [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual([r["company"]["name"] for r in view["rows"]],
                         ["Resend", "Baseten", "Pylon", "Inngest", "Cerebrium", "Turso", "Mintlify"])
        self.assertEqual((view["done"], view["total"]), (2, 7))
        self.assertEqual([r["state"] for r in view["rows"]], ["done", "done"] + ["todo"] * 5)
        self.assertEqual((view["asOf"], view["date"], view["builtAt"], view["notReady"]),
                         (NOW, DAY, "2026-11-10T13:00:00Z", None))
        self.assertEqual(view["week"], {"requests": 2, "accepts": 0, "replies": 0})
        first = view["rows"][0]
        self.assertEqual(first["entryId"], self.entry_id("Resend"))
        self.assertEqual(first["person"]["person"]["name"], "Dana Whitfield")
        self.assertEqual(first["person"]["foundOn"], {"label": "Team page", "url": "https://resend.example/team"})
        self.assertEqual(first["company"]["revision"], 1)
        self.assertEqual(first["revision"], 1)
        self.assertEqual(first["reason"], "Hiring a new grad engineer")
        self.assertEqual(first["reasonSources"], [{"label": "Posting", "url": "https://jobs.example/resend"}])
        self.assertEqual(first["applyUrl"], "https://jobs.example/resend")
        self.assertEqual(first["role"]["title"], "Software Engineer, New Grad")
        self.assertEqual((first["channel"], first["lane"]), ("linkedin_request", "level_role"))

    def test_find_someone_row_has_a_people_search_link_and_no_note(self):
        rows = self.plan()["rows"]
        five = rows[4]
        self.assertIsNone(five["person"])
        self.assertIsNone(five["note"])
        # The link is read now (his saved search, a stored LinkedIn id, else the name), not the one stored at 08:00.
        self.assertEqual(five["findSomeone"], {"searchUrl": five["queue"]["peopleSearchUrl"], "handoff": HANDOFF})
        self.assertEqual(five["queue"]["peopleSearchUrl"], "https://www.linkedin.com/search/results/people/"
                                                           "?keywords=Cerebrium&geoUrn=%5B%22103644278%22%5D"
                                                           "&origin=FACETED_SEARCH")
        self.assertEqual(five["state"], "todo")
        self.assertTrue(all(r["findSomeone"] is None for i, r in enumerate(rows) if i != 4))

    def test_search_link_is_built_from_the_company_when_why_has_none(self):
        self.set_why("Cerebrium", "{}")
        five = self.row("Cerebrium")
        self.assertEqual(five["findSomeone"]["searchUrl"],
                         "https://www.linkedin.com/search/results/people/?keywords=Cerebrium"
                         "&geoUrn=%5B%22103644278%22%5D&origin=FACETED_SEARCH")
        self.assertEqual(five["findSomeone"]["handoff"], HANDOFF)

    def test_email_row_points_at_its_outbox_touch_and_has_no_steps_to_log(self):
        seven = self.row("Mintlify")
        self.assertEqual((seven["channel"], seven["emailTouchId"], seven["steps"]), ("email", self.email_touch, []))
        self.assertEqual((seven["role"], seven["applyUrl"], seven["note"], seven["state"]), (None, None, None, "todo"))
        self.conn.execute("UPDATE message SET state = 'approved', approved_at = ? WHERE message_id = ?",
                          (EARLIER, self.email_message))
        self.assertEqual(self.row("Mintlify")["state"], "done")
        self.assertEqual(self.plan()["done"], 3)

    def test_apply_step_exists_only_with_a_role(self):
        three = self.row("Pylon")
        self.assertEqual([st["kind"] for st in three["steps"]], ["request", "apply"])
        self.assertEqual(three["steps"][0], {"kind": "request", "touchId": None, "at": None})
        self.conn.execute("UPDATE daily_list_entry SET posting_id = NULL WHERE company_id = ?", (self.ids["Pylon"],))
        self.set_why("Pylon", json.dumps(why()))
        three = self.row("Pylon")
        self.assertEqual([st["kind"] for st in three["steps"]], ["request"])
        self.assertEqual((three["role"], three["applyUrl"]), (None, None))

    def test_a_closed_posting_drops_the_apply_step_unless_he_already_applied(self):
        self.conn.execute("UPDATE posting SET closed_at = ?, close_reason = 'hand' WHERE posting_id = ?",
                          (EARLIER, self.postings["Pylon"]))
        self.assertEqual([st["kind"] for st in self.row("Pylon")["steps"]], ["request"])
        self.conn.execute("UPDATE posting SET closed_at = ? , close_reason = 'hand' WHERE posting_id = ?",
                          (EARLIER, self.postings["Resend"]))
        resend = self.row("Resend")
        self.assertEqual([st["kind"] for st in resend["steps"]], ["request", "apply"])
        self.assertEqual(resend["state"], "done")

    def test_row_is_done_only_when_every_step_has_a_time(self):
        self.request("Pylon", at="2026-11-09T15:00:00Z")  # before the list date: not this row's step
        self.assertIsNone(self.row("Pylon")["steps"][0]["at"])
        touch = self.request("Pylon")
        three = self.row("Pylon")
        self.assertEqual(three["steps"][0], {"kind": "request", "touchId": touch, "at": EARLIER})
        self.assertEqual((three["steps"][1]["at"], three["state"]), (None, "todo"))
        applied = self.apply("Pylon")
        three = self.row("Pylon")
        self.assertEqual(three["steps"][1], {"kind": "apply", "touchId": applied, "at": EARLIER})
        self.assertEqual(three["state"], "done")

    def test_an_undone_touch_is_no_step(self):
        touch = self.request("Pylon")
        self.conn.execute("UPDATE outreach_touch SET undone_at = ? WHERE touch_id = ?", (NOW, touch))
        self.assertIsNone(self.row("Pylon")["steps"][0]["at"])

    def test_skipped_row_and_carried_row_state(self):
        self.conn.execute("UPDATE daily_list_entry SET state = 'backlog', decided_at = ? WHERE company_id = ?",
                          (EARLIER, self.ids["Inngest"]))
        s.event(self.conn, self.ids["Inngest"], "row_skipped", EARLIER, actor="devanshu", entity="daily_list",
                detail={"list_date": DAY, "company_id": self.ids["Inngest"]})
        self.set_why("Turso", json.dumps(why("intern_junior", carried_from="2026-11-09", first_proposed="2026-11-09")))
        view = self.plan()
        rows = {r["company"]["name"]: r for r in view["rows"]}
        self.assertEqual(rows["Inngest"]["state"], "skipped")
        self.assertEqual((rows["Turso"]["carriedFrom"], rows["Turso"]["state"]), ("2026-11-09", "todo"))
        self.assertIsNone(rows["Pylon"]["carriedFrom"])
        self.assertEqual((view["done"], view["total"]), (2, 7))
        self.conn.execute("UPDATE daily_list_entry SET state = 'backlog', decided_at = ? WHERE company_id = ?",
                          (EARLIER, self.ids["Resend"]))
        self.assertEqual(self.row("Resend")["state"], "done")  # done beats skipped

    def test_a_removed_row_is_not_on_the_plan(self):
        self.conn.execute("UPDATE daily_list_entry SET state = 'removed', decided_at = ? WHERE company_id = ?",
                          (EARLIER, self.ids["Inngest"]))
        view = self.plan()
        self.assertNotIn("Inngest", [r["company"]["name"] for r in view["rows"]])
        self.assertEqual([r["position"] for r in view["rows"]], [1, 2, 3, 4, 5, 6])
        self.assertEqual(view["total"], 6)

    def test_note_comes_from_linkedin_draft_with_chars_and_limit(self):
        note = self.row("Pylon")["note"]
        self.assertEqual(note["noteId"], self.lena_note.draft_id)
        self.assertEqual((note["text"], note["chars"], note["maxChars"]), (NOTE, len(NOTE), 300))
        self.assertEqual((note["reasonLine"], note["reasonState"], note["savedBy"]), (REASON, "confirmed", "devanshu"))
        self.assertEqual(note["revision"], self.lena_note.revision)
        self.assertEqual(note["savedAt"], self.lena_note.updated_at)
        self.assertEqual(note["facts"], [
            {"text": "the queue rewrite", "source": {"label": "Blog", "url": "https://pylon.example/blog"},
             "inNote": True},
            {"text": "a team of 12", "source": {"label": "Team page", "url": "https://pylon.example/team"},
             "inNote": False}])
        stored = json.loads(self.conn.execute("SELECT checks FROM linkedin_draft WHERE draft_id = ?",
                                              (self.lena_note.draft_id,)).fetchone()[0])
        self.assertEqual(note["checks"], stored)
        self.assertEqual([c["code"] for c in note["checks"]], [c.code for c in self.lena_note.checks])

    def test_proposed_reason_state_needs_your_line_and_confirmed_does_not(self):
        rows = {r["company"]["name"]: r for r in self.plan()["rows"]}
        jonah = rows["Inngest"]["note"]
        self.assertEqual((jonah["reasonState"], jonah["needsYourLine"], jonah["savedBy"]), ("proposed", True, "claude"))
        self.assertEqual(jonah["reasonLine"], "I saw the Inngest step functions talk")
        self.assertFalse(rows["Pylon"]["note"]["needsYourLine"])
        self.assertIsNone(rows["Turso"]["note"])  # no draft yet

    def test_reason_falls_back_to_the_lane_sentence_when_why_is_empty(self):
        self.set_why("Turso", "{}")
        turso = self.row("Turso")
        self.assertEqual(turso["reason"], "Ranked for an internship or junior role.")
        self.assertEqual((turso["reasonSources"], turso["carriedFrom"]), ([], None))
        self.assertIsNone(turso["person"]["foundOn"])
        self.assertEqual(turso["applyUrl"], "https://jobs.example/turso")  # the posting's own link

    def test_malformed_why_json_does_not_500(self):
        bad = [
            "[1, 2]", '"text"', "null", "7",
            json.dumps({"reason": 5, "reason_sources": "x", "role": [], "person": {"found_on": "nope"},
                        "find_someone": {"search_url": 3}, "carried_from": "yesterday", "lane": None}),
            json.dumps({"reason": "", "reason_sources": [{"label": 1}, "x", {"label": "Ok", "url": ""}],
                        "role": {"url": 9}, "person": {"found_on": {"label": "L"}},
                        "find_someone": {"search_url": "javascript:alert(1)", "handoff": "x"},
                        "carried_from": "2026-13-45"}),
        ]
        for text in bad:
            with self.subTest(why=text):
                for company in ("Cerebrium", "Turso"):
                    self.set_why(company, text)
                rows = {r["company"]["name"]: r for r in self.plan()["rows"]}
                for company in ("Cerebrium", "Turso"):
                    row = rows[company]
                    self.assertTrue(row["reason"])
                    self.assertTrue(all(src["url"] for src in row["reasonSources"]))
                    self.assertIsNone(row["carriedFrom"])
                self.assertTrue(rows["Cerebrium"]["findSomeone"]["searchUrl"].startswith(
                    "https://www.linkedin.com/search/results/people/"))
                self.assertIsNone(rows["Turso"]["person"]["foundOn"])

    def test_weekend_with_no_list_is_not_ready_and_still_previews_tomorrow(self):
        self.set_now("2026-11-14T15:00:00Z")  # Saturday
        view = self.plan()
        self.assertEqual(view["notReady"]["reason"], "weekend")
        self.assertEqual(view["notReady"]["text"], "No plan on weekends.")
        self.assertEqual((view["rows"], view["done"], view["total"], view["builtAt"]), ([], 0, 0, None))
        total = plan_capacity(self.conn, canonical_ids(self.conn), date(2026, 11, 16), CFG)
        self.assertEqual(view["tomorrow"]["total"], total)
        preview = view["tomorrow"]["preview"]
        self.assertEqual(preview, self.expected_draft())
        self.assertNotIn("Resend", [p["companyName"] for p in preview])  # asked on Tuesday: the company waits
        self.assertEqual(view["tomorrow"]["text"], f"Monday's plan builds at 08:00. Room for {total} companies. "
                                                   "A draft of who it would propose is below.")

    def test_weekday_with_no_list_is_not_built(self):
        self.set_now("2026-11-11T12:00:00Z")  # Wednesday 07:00, before the 08:00 build
        view = self.plan()
        self.assertEqual(view["notReady"]["reason"], "not_built")
        self.assertEqual((view["rows"], view["builtAt"], view["date"]), ([], None, "2026-11-11"))
        total = plan_capacity(self.conn, canonical_ids(self.conn), date(2026, 11, 12), CFG)
        self.assertEqual(view["tomorrow"]["total"], total)
        self.assertEqual(view["tomorrow"]["preview"], self.expected_draft())
        self.assertEqual(view["tomorrow"]["text"], f"Tomorrow's plan builds at 08:00. Room for {total} companies. "
                                                   "A draft of who it would propose is below.")

    def test_failed_plan_job_is_job_failed_with_its_error(self):
        self.set_now("2026-11-11T15:00:00Z")
        self.conn.execute("INSERT INTO job_run(job, started_at, finished_at, status, error) "
                          "VALUES ('plan', '2026-11-11T13:00:00Z', '2026-11-11T13:00:05Z', 'failed', ?)",
                          ("RuntimeError: the ranked list is empty",))
        view = self.plan()
        self.assertEqual(view["notReady"]["reason"], "job_failed")
        self.assertIn("RuntimeError: the ranked list is empty", view["notReady"]["text"])
        self.assertEqual((view["listJob"]["job"], view["listJob"]["status"]), ("plan", "failed"))
        self.assertEqual(view["rows"], [])
        self.assertGreaterEqual(view["tomorrow"]["total"], 0)

    def test_an_old_failed_job_does_not_make_today_job_failed(self):
        self.set_now("2026-11-11T12:00:00Z")
        self.conn.execute("INSERT INTO job_run(job, started_at, finished_at, status, error) "
                          "VALUES ('plan', '2026-11-09T13:00:00Z', '2026-11-09T13:00:05Z', 'failed', 'old')")
        self.assertEqual(self.plan()["notReady"]["reason"], "not_built")

    def test_tomorrow_on_friday_names_monday_and_drafts_mondays_plan(self):
        today = self.plan()["tomorrow"]
        self.assertTrue(today["text"].startswith("Tomorrow's plan builds at 08:00. "), today["text"])
        friday = "2026-11-13"
        self.conn.execute("UPDATE daily_list_entry SET state = 'removed', decided_at = ? WHERE list_date = ?",
                          (EARLIER, DAY))  # Tuesday's rows would carry too: this test is about Friday's
        for rank, company in enumerate(("Pylon", "Inngest", "Resend"), 1):
            self.entry(self.ids[company], rank, person_id=self.people[company], channel="linkedin_request",
                       posting_id=self.postings[company], why=why(first_proposed=friday), list_date=friday)
        self.request("Resend", at="2026-11-13T15:00:00Z")  # Friday's Resend row is done
        self.set_now("2026-11-13T16:00:00Z")
        with mock.patch("outreach.api.queries.plan.build_plan", wraps=build_plan) as spy:
            view = self.plan()
        self.assertEqual(spy.call_args.kwargs["today"], date(2026, 11, 16))
        total = plan_capacity(self.conn, canonical_ids(self.conn), date(2026, 11, 16), CFG)
        self.assertEqual(view["tomorrow"]["text"], f"Monday's plan builds at 08:00. Room for {total} companies. "
                                                   "A draft of who it would propose is below.")
        preview = view["tomorrow"]["preview"]
        self.assertEqual(preview, self.expected_draft())
        self.assertEqual([p["companyName"] for p in preview][:2], ["Pylon", "Inngest"])  # Friday's, still to do
        self.assertNotIn("Resend", [p["companyName"] for p in preview])

    def test_tomorrow_text_has_no_below_when_the_draft_is_empty(self):
        self.conn.execute("DELETE FROM company_score")  # nothing ranked: the next plan would propose nobody
        tomorrow = self.plan()["tomorrow"]
        total = plan_capacity(self.conn, canonical_ids(self.conn), date(2026, 11, 11), CFG)
        self.assertEqual((tomorrow["text"], tomorrow["preview"]),
                         (f"Tomorrow's plan builds at 08:00. Room for {total} {'company' if total == 1 else 'companies'}.",
                          []))
        self.assertNotIn("below", tomorrow["text"])

    def test_tomorrow_text_says_person_for_one_and_names_the_weekday_for_monday(self):
        self.conn.execute("DELETE FROM company_score")
        self.set_now("2026-11-13T16:00:00Z")  # Friday: the next plan is Monday's
        tomorrow = self.plan()["tomorrow"]
        self.assertEqual(tomorrow["preview"], [])
        self.assertEqual(tomorrow["text"], f"Monday's plan builds at 08:00. Room for {tomorrow['total']} "
                                           f"{'company' if tomorrow['total'] == 1 else 'companies'}.")
        self.assertEqual(plan_text("Tomorrow", 1, 0), "Tomorrow's plan builds at 08:00. Room for 1 person.")
        # company first (#9): the queue counts companies
        self.assertEqual(plan_text("Tomorrow", 1, 0, companies=True), "Tomorrow's plan builds at 08:00. Room for 1 company.")
        self.assertEqual(plan_text("Monday", 8, 0, companies=True), "Monday's plan builds at 08:00. Room for 8 companies.")
        self.assertEqual(plan_text("Monday", 7, 2), "Monday's plan builds at 08:00. Room for 7 people. "
                                                    "A draft of who it would propose is below.")

    def test_linkedin_week_comes_from_linkedin_capacity_with_both_limits(self):
        self.request("Pylon", source="own")  # his own request: the hard total only
        self.request("Pylon", at="2026-11-04T05:30:00Z")  # 00:30 on Wednesday 4 November: inside the 7 days
        self.request("Pylon", at="2026-11-04T04:30:00Z")  # 23:30 on Tuesday 3 November: a day too old
        view = self.plan()
        self.assertNotIn("linkedinMonth", view)
        week = view["linkedinWeek"]
        cap = linkedin_capacity(self.conn, canonical_ids(self.conn), date(2026, 11, 10), CFG)
        self.assertEqual(week, {"used": cap.used, "limit": cap.budget, "hardUsed": cap.hard_used,
                                "hardLimit": cap.hard_limit})
        self.assertEqual(week, {"used": 3, "limit": 150, "hardUsed": 4, "hardLimit": 200})

    def test_entry_id_survives_incremental_vacuum(self):
        before = {r["company"]["name"]: r["entryId"] for r in self.plan()["rows"]}
        for n in range(200):
            self.conn.execute("INSERT INTO api_request(request_id, method, path, fingerprint, status, response, "
                              "created_at) VALUES (?, 'POST', '/x', ?, 200, '{}', ?)",
                              (f"filler-{n:04d}", "0" * 64, NOW))
        self.conn.execute("DELETE FROM api_request")
        self.conn.execute("PRAGMA incremental_vacuum")
        after = {r["company"]["name"]: r["entryId"] for r in self.plan()["rows"]}
        self.assertEqual(before, after)
        self.assertEqual(after["Pylon"], self.entry_id("Pylon"))

    def test_the_shell_progress_and_the_plan_agree_on_a_built_plan(self):
        view = self.plan()
        self.assertEqual((view["done"], view["total"]), (2, 7))
        self.assertEqual(self.ok(self.get("/api/shell"))["plan"], {"done": 2, "total": 7})
        today = self.ok(self.get("/api/progress"))["today"]
        self.assertEqual((today["done"], today["planned"]), (2, 7))
        self.conn.execute("UPDATE daily_list_entry SET state = 'backlog', decided_at = ? WHERE company_id = ?",
                          (EARLIER, self.ids["Inngest"]))  # a skipped row still counts in the total
        self.assertEqual(self.ok(self.get("/api/shell"))["plan"], {"done": 2, "total": 7})

    def test_a_find_someone_row_is_done_once_anyone_at_the_company_was_asked(self):
        ana = s.person(self.conn, self.ids["Cerebrium"], "Ana Silva", channel="linkedin")
        self.people["Cerebrium"] = ana
        self.request("Cerebrium", at="2026-11-09T15:00:00Z")  # before the list date: not this row's
        self.assertEqual(self.row("Cerebrium")["state"], "todo")
        touch = self.request("Cerebrium")
        five = self.row("Cerebrium")
        self.assertIsNone(five["person"])
        self.assertEqual(five["steps"][0], {"kind": "request", "touchId": touch, "at": EARLIER})
        self.assertEqual(five["steps"][1]["at"], None)  # the row has a role: the apply step is still to do
        self.apply("Cerebrium")
        self.assertEqual(self.row("Cerebrium")["state"], "done")

    def test_a_find_someone_row_counts_an_email_and_a_merged_duplicate(self):
        dup = s.company(self.conn, "Cerebrium Inc", "removed", merged_into=self.ids["Cerebrium"])
        kim = s.person(self.conn, dup, "Kim Ito", channel="email")
        touch = log_touch(self.conn, person_id=kim, company_id=dup, channel="email", sent_at=EARLIER,
                          context=self.context(), idempotency_key="seed:kim:email")
        self.conn.execute("UPDATE daily_list_entry SET posting_id = NULL WHERE company_id = ?",
                          (self.ids["Cerebrium"],))
        five = self.row("Cerebrium")
        self.assertEqual((five["steps"], five["state"]), ([{"kind": "request", "touchId": touch, "at": EARLIER}],
                                                          "done"))

    def test_a_find_someone_row_counts_a_request_logged_only_in_the_linkedin_log(self):
        ana = s.person(self.conn, self.ids["Cerebrium"], "Ana Silva", channel="linkedin")
        s.linkedin(self.conn, ana, "request_sent", EARLIER)  # imported history: no touch behind it
        self.conn.execute("UPDATE daily_list_entry SET posting_id = NULL WHERE company_id = ?",
                          (self.ids["Cerebrium"],))
        five = self.row("Cerebrium")
        self.assertEqual(five["steps"], [{"kind": "request", "touchId": None, "at": "2026-11-10T05:00:00Z"}])
        self.assertEqual(five["state"], "done")

    def test_an_email_approved_before_the_list_date_does_not_finish_the_row(self):
        self.conn.execute("UPDATE message SET state = 'approved', approved_at = ? WHERE message_id = ?",
                          ("2026-11-10T04:59:59Z", self.email_message))  # still the 9th in New York
        self.assertEqual(self.row("Mintlify")["state"], "todo")
        self.conn.execute("UPDATE message SET approved_at = ? WHERE message_id = ?",
                          ("2026-11-10T05:00:00Z", self.email_message))
        self.assertEqual(self.row("Mintlify")["state"], "done")

    def test_a_stored_check_that_does_not_parse_becomes_one_block_check(self):
        self.conn.execute("UPDATE linkedin_draft SET checks = ? WHERE draft_id = ?",
                          (json.dumps([{"code": "note_too_long", "severity": "block"}, {"nope": 1}]),
                           self.lena_note.draft_id))
        checks = self.row("Pylon")["note"]["checks"]
        self.assertEqual([(c["code"], c["severity"]) for c in checks], [("checks_unreadable", "block")])

    def test_stored_checks_that_are_not_a_list_become_one_block_check(self):
        from outreach.api.queries.plan import note_draft
        stored = dict(self.conn.execute("SELECT * FROM linkedin_draft WHERE draft_id = ?",
                                        (self.lena_note.draft_id,)).fetchone())
        for raw in ("null", '"text"', "{}", "not json", None, "[1]"):
            with self.subTest(checks=raw):
                note = note_draft({**stored, "checks": raw}, CFG)
                self.assertEqual([(c.code, c.severity) for c in note.checks], [("checks_unreadable", "block")])
                self.assertIn("could not be read", note.checks[0].title)
        self.assertEqual(note_draft({**stored, "checks": "[]"}, CFG).checks, [])

    def test_a_merged_away_company_answers_as_its_survivor(self):
        survivor = s.company(self.conn, "Pylon Labs")
        self.conn.execute("UPDATE company SET merged_into = ?, status = 'removed', remove_reason = 'merged' "
                          "WHERE company_id = ?", (survivor, self.ids["Pylon"]))
        three = self.plan()["rows"][2]
        self.assertEqual((three["company"]["id"], three["company"]["name"]), (survivor, "Pylon Labs"))


SATURDAY = "2026-11-14T15:00:00Z"
COUNTED = ("daily_list", "daily_list_entry", "job_run", "event", "person", "linkedin_draft")


class TomorrowDraftTests(PlanCase):
    """Tomorrow's preview is #15's dry run of the next weekday's plan: the people it would propose, not only the rows
    carried from earlier plans. It writes nothing, never reads boards, ranks or calls out, and a failure is the count
    alone."""

    def setUp(self):
        super().setUp()
        from tests.plan_seed import seed_person, seed_scored_company
        for company, posting in self.postings.items():
            self.conn.execute("UPDATE company_score SET signals = json_set(signals, '$.best_posting_id', ?) "
                              "WHERE company_id = ?", (posting, self.ids[company]))
        self.ids["Hex"] = seed_scored_company(self.conn, "Hex", 95, ["level_role"], version="rk-1")
        seed_person(self.conn, self.ids["Hex"], "Ada Lin", "founder", email="ada@hex.example",
                    sources=[("email", "https://hex.example/about", "ada@hex.example")])
        self.set_now(SATURDAY)

    def counts(self) -> dict:
        return {t: self.conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in COUNTED}

    def test_the_draft_lists_the_next_plans_companies(self):
        # Company first (plan.propose_people false, shipped): the people on file (Ada Lin with an address, Lena,
        # Jonah, Ines) are not proposed; every row is the company, by score, on LinkedIn.
        tomorrow = self.plan()["tomorrow"]
        def row(company, title):
            return {"companyName": company, "personName": None, "roleTitle": title, "channel": "linkedin_request",
                    "findSomeone": True}
        self.assertEqual(tomorrow["preview"], [
            row("Hex", "Software Engineer"),
            row("Pylon", "Software Engineer I"),
            row("Inngest", "Software Engineer II"),
            row("Cerebrium", "Member of Technical Staff"),
            row("Turso", "Software Engineering Intern"),
        ])
        self.assertEqual(tomorrow["preview"], self.expected_draft())
        self.assertTrue(tomorrow["text"].endswith(" A draft of who it would propose is below."))

    def test_reading_the_draft_writes_nothing(self):
        before = self.counts()
        version = self.conn.execute("PRAGMA data_version").fetchone()[0]
        for now in (SATURDAY, NOW, "2026-11-11T12:00:00Z", "2026-11-13T16:00:00Z"):
            with self.subTest(now=now):
                self.set_now(now)
                self.assertTrue(self.plan()["tomorrow"]["preview"])
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.conn.execute("PRAGMA data_version").fetchone()[0], version)  # nobody committed

    def test_the_draft_is_a_dry_run_that_reads_no_boards_and_runs_no_ranker(self):
        with mock.patch("outreach.api.queries.plan.build_plan", wraps=build_plan) as spy:
            self.plan()
        self.assertEqual(spy.call_count, 1)
        kwargs = spy.call_args.kwargs
        self.assertEqual(kwargs["today"], date(2026, 11, 16))
        self.assertEqual((kwargs["dry_run"], kwargs["read_boards"]), (True, False))
        self.assertIsNone(kwargs.get("boards_reader"))
        self.assertIsNone(kwargs.get("ranker"))

    def test_a_failing_dry_run_falls_back_to_the_count_and_logs_once(self):
        total = plan_capacity(self.conn, canonical_ids(self.conn), date(2026, 11, 16), CFG)
        for target in ("build_plan", "people_by_ids"):  # the dry run itself, then naming its rows
            with self.subTest(target=target):
                error = RuntimeError(f"draft broke in {target}")
                self.app.state.view_cache.clear()  # a patch changes the code, not the store: drop the last answer
                with mock.patch(f"outreach.api.queries.plan.{target}", side_effect=error):
                    with self.assertLogs("outreach.api", level="WARNING") as logs:
                        first = self.plan()["tomorrow"]
                        second = self.plan()["tomorrow"]
                self.assertEqual(first, {"total": total, "preview": [],
                                         "text": f"Monday's plan builds at 08:00. Room for {total} companies."})
                self.assertEqual(second, first)
                self.assertEqual(sum(str(error) in r.getMessage() for r in logs.records), 1)

    def test_a_plan_already_built_for_the_next_day_gives_no_draft(self):
        build_plan(self.conn, today=date(2026, 11, 16), cfg=CFG, read_boards=False)
        tomorrow = self.plan()["tomorrow"]
        self.assertEqual(tomorrow["preview"], [])
        self.assertNotIn("below", tomorrow["text"])

    def test_weekday_and_weekend_labels(self):
        for now, label in ((NOW, "Tomorrow"), ("2026-11-13T16:00:00Z", "Monday"), (SATURDAY, "Monday"),
                           ("2026-11-15T15:00:00Z", "Tomorrow")):
            with self.subTest(now=now):
                self.set_now(now)
                text = self.plan()["tomorrow"]["text"]
                self.assertTrue(text.startswith(f"{label}'s plan builds at 08:00. Room for "), text)
                self.assertTrue(text.endswith(" A draft of who it would propose is below."), text)


class TomorrowDraftSpeedTests(ApiCase):
    """The draft runs on every plan read: on a store of the real shape it stays well under a second."""

    def test_the_draft_stays_fast_on_a_large_store(self):
        c = self.conn
        lanes = ("level_role", "intern_junior", "reason_no_role", "any_us_role")
        with transaction(c):
            c.executemany("INSERT INTO company(name, status, status_at, created_at) VALUES (?, 'candidate', ?, ?)",
                          [(f"Bulk {n}", s.T0, s.T0) for n in range(5000)])
            first = c.execute("SELECT min(company_id) FROM company WHERE name = 'Bulk 0'").fetchone()[0]
            c.executemany(
                "INSERT INTO posting(company_id, source_key, title, location, url, first_seen_at, last_seen_at) "
                "VALUES (?, ?, 'Software Engineer', 'Remote', ?, ?, ?)",
                [(first + n % 5000, f"url:bulk-{n}", f"https://jobs.example/{n}", s.T0, s.T0) for n in range(15000)])
            postings = {r[0]: r[1] for r in c.execute("SELECT company_id, min(posting_id) FROM posting GROUP BY 1")}
            c.executemany(
                "INSERT INTO company_score(company_id, ranker_version, score, signals, lanes, scored_at) "
                "VALUES (?, 'rk-1', ?, ?, ?, '2026-11-10T08:00:00Z')",
                [(first + n, 90 - n / 100, json.dumps({"values": {}, "reasons": ["Hiring"], "hide": None, "flags": [],
                                                       "best_posting_id": postings[first + n]}),
                  json.dumps([lanes[n % 4]])) for n in range(5000)])
            c.executemany(
                "INSERT INTO person(company_id, name, title, role_kind, linkedin_url, channel, linkedin_reason, "
                "added_by, created_at, updated_at) VALUES (?, ?, 'Engineer', 'engineer', ?, 'linkedin', 'no_email', "
                "'claude', ?, ?)",
                [(first + n * 2, f"Person {n}", f"https://www.linkedin.com/in/p{n}", s.T0, s.T0) for n in range(2000)])
        self.set_now(SATURDAY)
        self.get("/api/plan")  # warm: the first call imports and compiles
        started = time.perf_counter()
        tomorrow = self.ok(self.get("/api/plan"))["tomorrow"]
        elapsed = time.perf_counter() - started
        # Nobody here has an email address, so the draft fills Monday's LinkedIn rows only.
        monday = linkedin_capacity(self.conn, canonical_ids(self.conn), date(2026, 11, 16), CFG).today
        self.assertEqual(len(tomorrow["preview"]), monday)
        self.assertGreater(monday, 0)
        self.assertLess(elapsed, 1.0, f"the plan read took {elapsed * 1000:.0f} ms on 5,000 scored companies")

    def ok(self, res) -> dict:
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()


class LeanTodayTests(ApiCase):
    """The lean Today must never take the day from the plan job (fix round 1)."""

    def seed(self) -> int:
        from tests.plan_seed import seed_person, seed_scored_company
        cid = seed_scored_company(self.conn, "Pylon", 90, ["level_role"], version="rk-1")
        seed_person(self.conn, cid, "Lena Ortiz", "engineer")
        return cid

    def test_a_today_read_before_the_plan_job_leaves_the_day_to_the_plan(self):
        from outreach.plan.build import build_plan
        self.seed()
        self.assertEqual(self.get("/api/today").status_code, 200)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM daily_list").fetchone()[0], 0)
        result = build_plan(self.conn, today=date(2026, 11, 10), read_boards=False)
        self.assertEqual(result.status, "built")
        view = self.get("/api/plan").json()
        self.assertIsNone(view["notReady"])
        # company first: Lena is on file, but the row is Pylon's, with nobody named
        self.assertEqual([(r["company"]["name"], r["person"]) for r in view["rows"]], [("Pylon", None)])

    def test_a_lean_built_list_reads_as_not_built(self):
        self.seed()
        self.lean_today = True
        self.build()
        self.assertIsNone(self.get("/api/today").json()["notReady"])  # the lean list exists
        self.assertEqual(self.conn.execute("SELECT count(*) FROM daily_list_entry WHERE channel IS NULL").fetchone()[0], 1)
        view = self.get("/api/plan").json()
        self.assertEqual(view["notReady"]["reason"], "not_built")
        self.assertEqual((view["rows"], view["done"], view["total"]), ([], 0, 0))

    def test_a_plan_built_list_with_only_find_someone_rows_is_still_the_plan(self):
        from outreach.plan.build import build_plan
        from tests.plan_seed import seed_scored_company
        seed_scored_company(self.conn, "Cerebrium", 90, ["level_role"], version="rk-1")
        self.assertEqual(build_plan(self.conn, today=date(2026, 11, 10), read_boards=False).status, "built")
        view = self.get("/api/plan").json()
        self.assertIsNone(view["notReady"])
        self.assertEqual([r["person"] for r in view["rows"]], [None])


class EmptyPlanTests(ApiCase):
    def test_empty_store_is_a_not_built_view(self):
        res = self.get("/api/plan")
        self.assertEqual(res.status_code, 200, res.text)
        view = res.json()
        self.assertEqual(view["notReady"]["reason"], "not_built")
        self.assertEqual((view["rows"], view["done"], view["total"], view["builtAt"], view["listJob"]),
                         ([], 0, 0, None, None))
        self.assertEqual(view["week"], {"requests": 0, "accepts": 0, "replies": 0})
        self.assertEqual(view["linkedinWeek"], {"used": 0, "limit": 150, "hardUsed": 0, "hardLimit": 200})
        self.assertEqual(view["tomorrow"]["preview"], [])
        self.assertNotIn("carried", view["tomorrow"]["text"])
        self.assertTrue(view["tomorrow"]["text"].startswith("Tomorrow's plan builds at 08:00. Room for "))

    def test_a_weekend_on_an_empty_store_is_a_weekend(self):
        self.set_now("2026-11-15T15:00:00Z")  # Sunday: tomorrow is Monday, the next calendar day
        view = self.get("/api/plan").json()
        self.assertEqual(view["notReady"]["reason"], "weekend")
        self.assertEqual(view["notReady"]["text"], "No plan on weekends.")
        self.assertTrue(view["tomorrow"]["text"].startswith("Tomorrow's plan builds at 08:00. Room for "))
        self.assertNotIn("below", view["notReady"]["text"] + view["tomorrow"]["text"])
