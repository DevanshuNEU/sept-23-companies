"""The terminal bridge (#9, company-first ruling 2026-10-26): knock_status, contact_check, intake and log_sent, called
through the official SDK client on a scratch store with a fixed clock (Tuesday 2026-11-10, 10:14 in New York)."""
import json
import os
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import apiseed as seed
from outreach.mcp.server import build_server
from outreach.outcomes import requests as request_writes
from tests.mcptest import NOW, McpCase, call, fingerprint
from tests.test_drafts_checks import FIXTURE_BANNED

TODAY = "2026-11-10"
NOTE = "Hi Lin, I read your post on the queue rewrite and would like to hear how the team works."


class BridgeCase(McpCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"OUTREACH_BANNED_PHRASES": str(FIXTURE_BANNED)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.acme = seed.company(self.db, "Acme Labs", "working")
        self.lin = seed.person(self.db, self.acme, "Lin Kedin", channel="linkedin")

    def request_touch(self, person: int, company: int, at: str, source: str = "own", key: str | None = None) -> int:
        self.db.execute("INSERT INTO linkedin_log(person_id, kind, at, recorded_at) VALUES (?, 'request_sent', ?, ?)",
                        (person, at, at))
        ctx = {"source": source, "lane": "level_role", "roleKind": "engineer", "roleAgeDays": None,
               "rankerVersion": None, "signals": {}, "roleNamed": False}
        return self.db.execute(
            "INSERT INTO outreach_touch(person_id, company_id, channel, sent_at, context, idempotency_key, created_at) "
            "VALUES (?, ?, 'linkedin_request', ?, ?, ?, ?)",
            (person, company, at, json.dumps(ctx), key or f"seed:{person}:{at}", at)).lastrowid

    def plan_today(self, company: int, group: str = "level_role") -> None:
        if self.db.execute("SELECT 1 FROM daily_list WHERE list_date = ?", (TODAY,)).fetchone() is None:
            self.db.execute("INSERT INTO daily_list(list_date, built_at, capacity) VALUES (?, ?, 8)", (TODAY, NOW))
        rank = self.db.execute("SELECT count(*) FROM daily_list_entry WHERE list_date = ?", (TODAY,)).fetchone()[0] + 1
        self.db.execute("INSERT INTO daily_list_entry(list_date, company_id, list_group, rank, proposed_at) "
                        "VALUES (?, ?, ?, ?, ?)", (TODAY, company, group, rank, NOW))

    def contacted(self, text: str) -> None:
        folder = tempfile.mkdtemp(prefix="outreach-contacted-")
        path = Path(folder) / "contacted.txt"
        path.write_text(text, encoding="utf-8")
        patcher = mock.patch.dict(os.environ, {"OUTREACH_CONTACTED_TXT": str(path)})
        patcher.start()
        self.addCleanup(patcher.stop)


class KnockStatusTest(BridgeCase):
    def test_status_reports_the_windows_flight_queue_and_a_missing_contacted_file(self):
        bolt = seed.company(self.db, "Bolt Robotics", "working")
        bea = seed.person(self.db, bolt, "Bea Bolt", channel="linkedin")
        self.request_touch(self.lin, self.acme, "2026-11-08T15:00:00Z", source="plan")
        self.request_touch(bea, bolt, "2026-11-09T15:00:00Z")
        self.plan_today(bolt)
        before = fingerprint(self.db)
        out = self.ok("knock_status")
        self.assertEqual(fingerprint(self.db), before)
        self.assertEqual((out["reachable"], out["server_version"]), (True, "2"))
        week = out["linkedin_week"]
        self.assertEqual((week["used"], week["limit"], week["hard_used"], week["hard_limit"]), (1, 150, 2, 200))
        self.assertEqual(out["companies_in_flight"], 2)
        self.assertEqual(out["today_queue"], {"date": TODAY, "size": 1, "reached": 0})
        self.assertFalse(out["contacted_txt"]["readable"])
        self.assertIn("not readable", out["contacted_txt"]["message"])

    def test_contacted_gaps_skip_imported_lines_and_people_the_store_has(self):
        self.request_touch(self.lin, self.acme, "2026-11-08T15:00:00Z")
        self.contacted("2026-10-01 - Old Co - Ann Old\n2026-11-08 - Acme Labs - Lin Kedin\n"
                       "2026-10-25 - Flaysh - Ido Rebhun\n# a comment\n")
        run = self.db.execute("INSERT INTO job_run(job, started_at, finished_at, status) VALUES ('import', ?, ?, 'ok')",
                              (NOW, NOW)).lastrowid
        self.db.execute("INSERT INTO import_record(run_id, source, source_key, raw, raw_sha256, outcome, reason, "
                        "imported_at) VALUES (?, 'contacted_txt', 'line:1', ?, ?, 'skipped', 'seen before', ?)",
                        (run, json.dumps({"line": "2026-10-01 - Old Co - Ann Old"}), "0" * 64, NOW))
        out = self.ok("knock_status")["contacted_txt"]
        self.assertEqual((out["readable"], out["gaps"]), (True, 1))
        check = self.ok("contact_check", {"company": "Flaysh", "people": [{"name": "Ido Rebhun"}]})
        self.assertEqual([g["company"] for g in check["legacy"]], ["Flaysh"])


class ContactCheckTest(BridgeCase):
    def test_an_unknown_company_is_a_question_with_the_by_hand_command_and_nothing_is_written(self):
        before = fingerprint(self.db)
        out = self.ok("contact_check", {"company": "Avante; rm -rf ~", "people": [{"name": "Jane Doe"}]})
        self.assertEqual(fingerprint(self.db), before)
        self.assertEqual((out["company"]["resolution"], out["verdict"]), ("unknown", "not_in_knock"))
        self.assertIn("Add it?", out["company"]["message"])
        command = out["company"]["add_command"]
        self.assertIn("python -m outreach.sources add --name", command)
        self.assertIn(shlex.quote("Avante; rm -rf ~"), command)
        self.assertNotIn("--domain", command)

    def test_a_near_name_and_a_tie_are_questions_never_a_guess(self):
        fuzzy = self.ok("contact_check", {"company": "Acme Labz"})
        self.assertEqual(fuzzy["company"]["resolution"], "fuzzy")
        self.assertEqual([c["company_id"] for c in fuzzy["company"]["candidates"]], [self.acme])
        self.assertIsNone(fuzzy["state"])
        one, two = seed.company(self.db, "Nova"), seed.company(self.db, "Nova Inc")
        tie = self.ok("contact_check", {"company": "Nova"})
        self.assertEqual(tie["company"]["resolution"], "ambiguous")
        self.assertEqual({c["company_id"] for c in tie["company"]["candidates"]}, {one, two})
        by_id = self.ok("contact_check", {"company_id": two})
        self.assertEqual((by_id["company"]["resolution"], by_id["company"]["id"]), ("found", two))

    def test_a_merged_name_answers_as_its_survivor(self):
        old = seed.company(self.db, "Acme Old Name", "removed", merged_into=self.acme)
        out = self.ok("contact_check", {"company": "Acme Old Name"})
        self.assertEqual((out["company"]["resolution"], out["company"]["id"]), ("found", self.acme))
        by_id = self.ok("contact_check", {"company_id": old})
        self.assertEqual(by_id["company"]["id"], self.acme)
        self.assertIn("merged", by_id["company"]["merged_note"])

    def test_state_history_and_one_advice_line_per_person(self):
        self.request_touch(self.lin, self.acme, "2026-11-06T15:00:00Z")
        before = fingerprint(self.db)
        out = self.ok("contact_check", {"company": "acme labs", "people": [
            {"name": "Lin Kedin"}, {"name": "New Person", "title": "Software Engineer"}]})
        self.assertEqual(fingerprint(self.db), before)
        self.assertEqual((out["state"]["label"], out["verdict"], out["state"]["free_again"]),
                         ("in_flight", "in_flight", "2026-11-20"))
        lin, new = out["people"][0], out["people"][1]
        self.assertEqual((lin["known"], lin["touches"][0]["channel"], lin["touches"][0]["sent_on"]),
                         (True, "linkedin_request", "2026-11-06"))
        self.assertEqual(lin["route"]["verdict"], "wait")
        self.assertIn("Nov 6", lin["route"]["reason"])
        self.assertEqual((new["known"], new["route"]["verdict"]), (False, "linkedin_note"))
        self.assertEqual(new["preference"]["label"], "unmeasured")
        self.assertEqual(out["linkedin_week"]["hard_used"], 1)

    def test_a_no_go_company_stops_everyone(self):
        self.db.execute("INSERT INTO no_go(company_id, reason, source, created_at) VALUES (?, 'other', 'devanshu', ?)",
                        (self.acme, NOW))
        out = self.ok("contact_check", {"company": "Acme Labs", "people": [{"name": "Lin Kedin"}]})
        self.assertEqual((out["state"]["label"], out["verdict"]), ("no_go", "stop"))
        self.assertEqual({p["route"]["verdict"] for p in out["people"]}, {"stop"})


class IntakeTest(BridgeCase):
    def paste(self, *people, company="Acme Labs"):
        return {"people": [{"company": company, **p} for p in people]}

    def test_people_are_recorded_as_found_with_a_role_kind_and_a_route_and_a_repeat_writes_nothing(self):
        args = self.paste({"name": "Priya Rao", "title": "Software Engineer", "tenure_months": 26,
                           "linkedin_url": "https://www.linkedin.com/in/priya-rao", "source_text": "Builds the queue."},
                          {"name": "Mo Lead", "title": "Engineering Manager"})
        out = self.ok("intake", args)
        priya, mo = out["people"]
        self.assertEqual((priya["recorded"], priya["created"], priya["role_kind"]), (True, True, "engineer"))
        self.assertEqual(mo["role_kind"], "manager")
        self.assertEqual(priya["route"]["verdict"], "linkedin_note")
        self.assertIn("2 years in role", priya["route"]["reason"])
        self.assertEqual(priya["key"], f"{self.acme}:priya rao:https://www.linkedin.com/in/priya-rao")
        row = self.db.execute("SELECT * FROM person WHERE person_id = ?", (priya["person_id"],)).fetchone()
        self.assertEqual((row["added_by"], row["channel"], row["role_kind"], row["title"], row["profile_text"]),
                         ("claude", "linkedin", "engineer", "Software Engineer", "Builds the queue."))
        # found, not contacted
        self.assertEqual(self.db.execute("SELECT count(*) FROM outreach_touch").fetchone()[0], 0)
        before = fingerprint(self.db)
        again = self.ok("intake", args)
        self.assertEqual(fingerprint(self.db), before)
        self.assertEqual([p["person_id"] for p in again["people"]], [priya["person_id"], mo["person_id"]])
        self.assertEqual([p["created"] for p in again["people"]], [False, False])
        self.assertEqual([p["route"] for p in again["people"]], [p["route"] for p in out["people"]])

    def test_an_unknown_company_records_no_one(self):
        before = fingerprint(self.db)
        out = self.ok("intake", self.paste({"name": "Jane Doe", "title": "Engineer"}, company="Avante"))
        self.assertEqual(fingerprint(self.db), before)
        self.assertEqual(out["companies"][0]["resolution"], "unknown")
        self.assertIn("outreach.sources add", out["companies"][0]["add_command"])
        self.assertEqual((out["people"][0]["recorded"], out["people"][0]["route"]), (False, None))

    def test_a_no_go_company_records_no_one_and_says_stop(self):
        self.db.execute("INSERT INTO no_go(company_id, reason, source, created_at) VALUES (?, 'other', 'devanshu', ?)",
                        (self.acme, NOW))
        before = fingerprint(self.db)
        out = self.ok("intake", self.paste({"name": "Jane Doe", "title": "Software Engineer"}))
        self.assertEqual(fingerprint(self.db), before)
        self.assertEqual((out["people"][0]["recorded"], out["people"][0]["route"]["verdict"]), (False, "stop"))

    def test_a_person_who_said_no_is_refused_and_stops(self):
        sam = seed.person(self.db, self.acme, "Sam Said", channel="linkedin")
        self.db.execute("INSERT INTO no_go(person_id, reason, source, created_at) VALUES (?, 'said_no', 'reply', ?)",
                        (sam, NOW))
        out = self.ok("intake", self.paste({"name": "Sam Said", "title": "Software Engineer",
                                            "linkedin_url": "https://www.linkedin.com/in/sam-said"}))
        self.assertEqual((out["people"][0]["refused"], out["people"][0]["route"]["verdict"]), ("person_no_go", "stop"))

    def test_an_address_he_gave_routes_to_email_with_or_without_a_page(self):
        out = self.ok("intake", self.paste(
            {"name": "Ana Ruiz", "title": "Co-founder & CEO", "email": "ana@acme.example",
             "email_source": {"url": "https://acme.example/about", "basis": "published",
                              "quote": "Write to ana@acme.example"}},
            {"name": "Ben Apollo", "title": "Software Engineer", "email": "ben@acme.example"}))
        ana, ben = out["people"]
        self.assertEqual((ana["route"]["verdict"], ana["route"]["key"]), ("email", "#9 supplied email"))
        self.assertEqual((ben["route"]["verdict"], ben["route"]["key"]), ("email", "#9 supplied email"))
        rows = self.db.execute("SELECT name, channel, email FROM person WHERE person_id IN (?, ?) ORDER BY person_id",
                               (ana["person_id"], ben["person_id"])).fetchall()
        self.assertEqual([tuple(r) for r in rows], [("Ana Ruiz", "email", "ana@acme.example"),
                                                    ("Ben Apollo", "email", "ben@acme.example")])

    def test_the_channel_field_can_keep_an_address_on_record_and_still_connect(self):
        out = self.ok("intake", self.paste({"name": "Cy Both", "title": "Software Engineer",
                                            "email": "cy@acme.example", "channel": "linkedin",
                                            "linkedin_url": "https://www.linkedin.com/in/cy-both"}))
        got = out["people"][0]
        self.assertEqual(got["route"]["verdict"], "linkedin_note")
        self.assertIn("stays on record", got["route"]["reason"])

    def test_an_email_for_someone_already_pasted_with_a_profile_fills_them_in_place(self):
        first = self.ok("intake", self.paste({"name": "Dee Profile", "title": "Software Engineer",
                                              "linkedin_url": "https://www.linkedin.com/in/dee-profile"}))
        pid = first["people"][0]["person_id"]
        count = self.db.execute("SELECT count(*) FROM person").fetchone()[0]
        second = self.ok("intake", self.paste({"name": "Dee Profile", "title": "Software Engineer",
                                               "email": "dee@acme.example"}))
        got = second["people"][0]
        self.assertEqual((got["person_id"], got["created"], got["route"]["verdict"]), (pid, False, "email"))
        self.assertIn("filled_email", got["flags"])
        self.assertEqual(self.db.execute("SELECT count(*) FROM person").fetchone()[0], count)
        row = self.db.execute("SELECT email, channel, linkedin_url FROM person WHERE person_id = ?", (pid,)).fetchone()
        self.assertEqual(tuple(row), ("dee@acme.example", "email", "https://www.linkedin.com/in/dee-profile"))

    def test_a_full_wave_warns_and_every_note_still_goes(self):
        people = [{"name": f"Eng {n}", "title": "Software Engineer"} for n in "ABCDE"]
        people.insert(0, {"name": "Rae Recruiter", "title": "Technical Recruiter"})
        out = self.ok("intake", self.paste(*people))
        self.assertEqual(len(out["people"]), 6)
        self.assertTrue(all(p["recorded"] for p in out["people"]))
        verdicts = [p["route"]["verdict"] for p in out["people"]]
        self.assertEqual(verdicts, ["find_someone_else"] + ["linkedin_note"] * 5)
        self.assertEqual(out["people"][5]["route"]["key"], "linkedin.hard_total")
        self.assertIn("your call", out["people"][5]["route"]["reason"])

    def test_a_company_on_todays_queue_routes_against_the_plan_quota(self):
        self.plan_today(self.acme)
        out = self.ok("intake", self.paste({"name": "Priya Rao", "title": "Software Engineer"}))
        self.assertEqual(out["people"][0]["route"]["key"], "linkedin.plan_quota")


class LogSentTest(BridgeCase):
    def log(self, **request):
        return self.ok("log_sent", {"requests": [{"channel": "linkedin_request", **request}]})

    def test_a_request_is_one_log_row_one_touch_and_its_events_and_a_repeat_writes_nothing(self):
        out = self.log(person_id=self.lin, note_text=NOTE)
        (r,) = out["results"]
        self.assertEqual((r["status"], r["sent_on"], r["source"]), ("logged", TODAY, "own"))
        (log,) = self.db.execute("SELECT * FROM linkedin_log").fetchall()
        self.assertEqual((log["kind"], log["note"], log["at"], log["reply_kind"]), ("request_sent", NOTE, NOW, None))
        (touch,) = self.db.execute("SELECT * FROM outreach_touch").fetchall()
        self.assertEqual((touch["channel"], touch["person_id"], touch["company_id"], touch["replied_at"]),
                         ("linkedin_request", self.lin, self.acme, None))
        self.assertTrue(touch["idempotency_key"].startswith(f"bridge:{self.lin}:{TODAY}:"))
        kinds = [(e["kind"], e["actor"]) for e in self.db.execute(
            "SELECT kind, actor FROM event WHERE entity = 'outreach_touch' ORDER BY event_id")]
        self.assertEqual(kinds, [("sent", "claude"), ("request_logged", "claude")])
        self.assertEqual(out["linkedin_week"]["hard_used"], 1)
        self.assertEqual(out["companies"][0]["label"], "in_flight")
        self.assertEqual(out["check_note"][:20], "check_note(author='u")
        before = fingerprint(self.db)
        again = self.log(person_id=self.lin, note_text=NOTE)
        self.assertEqual(fingerprint(self.db), before)
        self.assertEqual(again["results"][0]["status"], "already_logged")
        other = self.log(person_id=self.lin, note_text="A different note.")  # LinkedIn takes one request per person
        self.assertEqual(fingerprint(self.db), before)
        self.assertEqual(other["results"][0]["status"], "already_logged")
        self.assertIn(TODAY, other["results"][0]["message"])

    def test_a_company_on_todays_queue_logs_as_plan_with_the_rows_lane(self):
        self.plan_today(self.acme, "intern_junior")
        self.log(name="Lin Kedin", company="Acme Labs", note_text="")
        ctx = json.loads(self.db.execute("SELECT context FROM outreach_touch").fetchone()[0])
        self.assertEqual((ctx["source"], ctx["lane"]), ("plan", "intern_junior"))
        self.assertEqual(self.ok("knock_status")["linkedin_week"]["used"], 1)

    def test_refusals_write_nothing(self):
        self.db.execute("INSERT INTO no_go(person_id, reason, source, created_at) VALUES (?, 'other', 'devanshu', ?)",
                        (seed.person(self.db, self.acme, "Ned Nogo", channel="linkedin"), NOW))
        before = fingerprint(self.db)
        out = self.ok("log_sent", {"requests": [
            {"channel": "linkedin_request", "name": "Nobody Here", "company": "Acme Labs"},
            {"channel": "linkedin_request", "name": "Ned Nogo", "company": "Acme Labs"},
            {"channel": "linkedin_request", "name": "Lin Kedin", "company": "Avante"},
            {"channel": "linkedin_request", "person_id": self.lin, "at": "2026-11-05T15:00:00Z"},
            {"channel": "linkedin_request", "person_id": self.lin, "at": "2026-11-10T18:00:00Z"},
            {"channel": "linkedin_request", "person_id": self.lin, "at": "yesterday"},
        ]})
        self.assertEqual(fingerprint(self.db), before)
        self.assertEqual([r.get("refused") for r in out["results"]],
                         ["person_unknown", "no_go", "company_unknown", "bad_time", "bad_time", "bad_time"])

    def test_a_note_he_sent_is_logged_with_warnings_never_refused(self):
        out = self.log(person_id=self.lin, note_text="Hi Lin — I am passionate about queues. Let's connect.")
        (r,) = out["results"]
        self.assertEqual(r["status"], "logged")
        text = " ".join(r["warnings"])
        self.assertIn("passionate about", text)
        self.assertIn("dash", text)
        self.assertEqual(len(out["checks_used"]), 3)

    def test_a_note_over_300_characters_is_bad_input(self):
        err = self.error("log_sent", {"requests": [{"channel": "linkedin_request", "person_id": self.lin,
                                                    "note_text": "x" * 301}]})
        self.assertEqual(err["code"], "bad_input")
        err = self.error("log_sent", {"requests": [{"channel": "email", "person_id": self.lin}]})
        self.assertEqual(err["code"], "bad_input")

    def test_backdated_within_three_days_and_a_request_taken_back_can_be_logged_again(self):
        first = self.log(person_id=self.lin, note_text=NOTE, at="2026-11-08T14:00:00Z")["results"][0]
        self.assertEqual(first["sent_on"], "2026-11-08")
        # Taken back on the LinkedIn page: the touch is undone and the log gets an undo row (`undo_step`).
        self.db.execute("UPDATE outreach_touch SET undone_at = ? WHERE touch_id = ?", (NOW, first["touch_id"]))
        entry = self.db.execute("SELECT max(entry_id) FROM linkedin_log").fetchone()[0]
        self.db.execute("INSERT INTO linkedin_log(person_id, kind, undoes, at, recorded_at) VALUES (?, 'undo', ?, ?, ?)",
                        (self.lin, entry, NOW, NOW))
        again = self.log(person_id=self.lin, note_text=NOTE, at="2026-11-08T14:00:00Z")["results"][0]
        self.assertEqual(again["status"], "logged")
        keys = [r[0] for r in self.db.execute("SELECT idempotency_key FROM outreach_touch ORDER BY touch_id")]
        self.assertEqual(keys[1], keys[0] + ":again2")


class ReviewFixesTest(BridgeCase):
    """Review of PR #101: the batch is one transaction, a log-only request counts, a no-go elsewhere still stops,
    a backdate across the clock change is fine, and company names are clipped."""

    def log(self, *requests):
        return self.ok("log_sent", {"requests": [{"channel": "linkedin_request", **r} for r in requests]})

    def test_a_three_day_backdate_across_the_clock_change_is_logged(self):
        server = build_server(path=self.path, clock=lambda: "2026-11-04T04:59:00Z")  # Nov 3, 23:59 in New York
        is_error, out = call(server, "log_sent", {"requests": [
            {"channel": "linkedin_request", "person_id": self.lin, "at": "2026-10-31T04:30:00Z"}]})
        self.assertFalse(is_error, out)
        self.assertEqual((out["results"][0]["status"], out["results"][0]["sent_on"]), ("logged", "2026-10-31"))

    def test_the_log_row_and_the_touch_land_together_or_not_at_all(self):
        pat = seed.person(self.db, self.acme, "Pat Two", channel="linkedin")
        real, calls = request_writes.log_touch, []

        def second_fails(*args, **kwargs):
            calls.append(kwargs["person_id"])
            if len(calls) == 2:
                raise RuntimeError("the store went away")
            return real(*args, **kwargs)

        before = fingerprint(self.db)
        with mock.patch.object(request_writes, "log_touch", side_effect=second_fails):
            err = self.error("log_sent", {"requests": [
                {"channel": "linkedin_request", "person_id": self.lin},
                {"channel": "linkedin_request", "person_id": pat}]})
        self.assertEqual(err["code"], "internal")
        self.assertEqual(calls, [self.lin, pat])
        self.assertEqual(fingerprint(self.db), before)  # the first request's rows went back too

    def test_an_imported_request_with_no_touch_counts_as_already_logged(self):
        self.db.execute("INSERT INTO linkedin_log(person_id, kind, at, recorded_at) VALUES (?, 'request_sent', ?, ?)",
                        (self.lin, "2026-11-05T15:00:00Z", "2026-11-05T15:00:00Z"))
        before = fingerprint(self.db)
        (r,) = self.log({"person_id": self.lin})["results"]
        self.assertEqual(fingerprint(self.db), before)
        self.assertEqual(r["status"], "already_logged")
        self.assertIn("2026-11-05", r["message"])
        entry = self.db.execute("SELECT max(entry_id) FROM linkedin_log").fetchone()[0]
        self.db.execute("INSERT INTO linkedin_log(person_id, kind, undoes, at, recorded_at) VALUES (?, 'undo', ?, ?, ?)",
                        (self.lin, entry, NOW, NOW))
        self.assertEqual(self.log({"person_id": self.lin})["results"][0]["status"], "logged")

    def test_a_no_go_person_known_at_another_company_still_stops(self):
        bolt = seed.company(self.db, "Bolt Robotics")
        url = "https://www.linkedin.com/in/nia-nogo"
        nia = seed.person(self.db, bolt, "Nia Nogo", channel="linkedin", linkedin_url=url)
        self.db.execute("INSERT INTO no_go(person_id, reason, source, created_at) VALUES (?, 'said_no', 'reply', ?)",
                        (nia, NOW))
        check = self.ok("contact_check", {"company": "Acme Labs", "people": [
            {"name": "Nia N.", "title": "Software Engineer", "linkedin_url": url}]})
        self.assertEqual(check["people"][0]["route"]["verdict"], "stop")
        out = self.ok("intake", {"people": [{"name": "Nia N.", "title": "Software Engineer", "company": "Acme Labs",
                                             "linkedin_url": url}]})
        self.assertEqual(out["people"][0]["route"]["verdict"], "stop")

    def test_company_names_are_clipped_like_the_read_tools(self):
        long = seed.company(self.db, "L" * 260, "working")
        lee = seed.person(self.db, long, "Lee Long", channel="linkedin")
        check = self.ok("contact_check", {"company_id": long})
        self.assertEqual(len(check["company"]["name"]), 200)
        logged = self.log({"person_id": lee})
        self.assertEqual(len(logged["results"][0]["company"]["name"]), 200)
        self.assertEqual(len(logged["companies"][0]["name"]), 200)
        out = self.ok("intake", {"people": [{"name": "Kim Long", "title": "Software Engineer", "company_id": long}]})
        self.assertEqual(len(out["companies"][0]["name"]), 200)


if __name__ == "__main__":
    unittest.main()
