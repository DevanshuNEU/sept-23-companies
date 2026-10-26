"""The company queue's two writes (#9 company-first ruling, P2).

- `POST /api/plan/{entryId}/people`: the fallback when the terminal bridge is not there. He pastes `Name | Title |
  note` lines (up to 10); each becomes one person (added by him, the role kind read from the title), one
  `linkedin_log` `request_sent` row with the note and one `linkedin_request` touch of source `plan`, through the same
  `record_request` every request log uses. No-go and paused refuse the whole paste and write nothing.
- `POST /api/plan/{entryId}/search`: his saved LinkedIn people search for the company, kept as an `event`; a
  `currentCompany` id also becomes a `company_alias` of source `linkedin`.

Nothing here sends anything or reaches LinkedIn. Scratch store, no network.
"""
import json

from outreach.store import undo_touch
from tests.apitest import NOW
from tests.test_api_plan_writes import DASH, PlanWriteCase

SEARCH = "https://www.linkedin.com/search/results/people/"
SAVED = f"{SEARCH}?currentCompany=%5B%221304%22%5D&geoUrn=%5B%22103644278%22%5D&origin=FACETED_SEARCH"


class PeopleCase(PlanWriteCase):
    def paste(self, lines, *, company="Cerebrium", key=None, request_id=None):
        eid = self.entry_id(company)
        return self.post(f"/api/plan/{eid}/people", {"lines": lines, "idempotencyKey": key or f"plan:{eid}:people:a1"},
                         key=request_id)

    def search(self, url, *, company="Cerebrium", request_id=None):
        return self.post(f"/api/plan/{self.entry_id(company)}/search", {"url": url}, key=request_id)

    def at_company(self, company="Cerebrium"):
        return self.conn.execute("SELECT * FROM person WHERE company_id = ? ORDER BY person_id",
                                 (self.ids[company],)).fetchall()

    def count(self, table, where="", args=()):
        return self.conn.execute(f"SELECT count(*) FROM {table} {where}", args).fetchone()[0]

    def snapshot(self):
        return tuple(self.count(t) for t in ("person", "linkedin_log", "outreach_touch", "company_alias")) + (
            self.count("event", "WHERE kind IN ('linkedin_search_saved', 'request_logged')"),)


class LogPeopleTests(PeopleCase):
    def test_each_line_is_one_person_one_log_with_the_note_and_one_touch(self):
        before = len(self.at_company())
        res = self.ok(self.paste("Jane Doe | Senior Software Engineer | Hi Jane, I read your post on queues.\n"
                                  "\n"
                                  "Omar Diaz | Engineering Manager, Platform |\n"
                                  "Ana Silva\n"))
        people = self.at_company()
        self.assertEqual(len(people), before + 3)
        jane, omar, ana = people[-3:]
        self.assertEqual([(p["name"], p["title"], p["role_kind"], p["added_by"]) for p in (jane, omar, ana)],
                         [("Jane Doe", "Senior Software Engineer", "engineer", "devanshu"),
                          ("Omar Diaz", "Engineering Manager, Platform", "manager", "devanshu"),
                          ("Ana Silva", "", "other", "devanshu")])
        for p, note in ((jane, "Hi Jane, I read your post on queues."), (omar, ""), (ana, "")):
            (log,) = self.log_rows(p["person_id"])
            (touch,) = self.touches("linkedin_request", person_id=p["person_id"])
            self.assertEqual((log["kind"], log["note"], log["at"]), ("request_sent", note, NOW))
            self.assertEqual((touch["company_id"], touch["sent_at"]), (self.ids["Cerebrium"], NOW))
            self.assertEqual(json.loads(touch["context"])["source"], "plan")
        row = res["row"]
        self.assertIsNone(row["person"])
        self.assertIsNotNone(row["steps"][0]["at"])  # the company's request step is done
        self.assertEqual(row, self.row("Cerebrium"))
        self.assertEqual([(p["name"], p["added"], p["noteChars"]) for p in res["people"]],
                         [("Jane Doe", True, 36), ("Omar Diaz", True, 0), ("Ana Silva", True, 0)])
        self.assertEqual(row["queue"]["standing"]["text"], "3 asked today, room for 1 more, free again Nov 24")
        events = self.conn.execute("SELECT detail FROM event WHERE kind = 'request_logged' ORDER BY event_id").fetchall()
        self.assertEqual([json.loads(e["detail"])["via"] for e in events], ["app"] * 3)

    def test_the_terminal_paste_block_with_the_company_first_is_read_too(self):
        self.ok(self.paste("Cerebrium | Wen Zhao | Member of Technical Staff | Hi Wen | a note with a bar"))
        wen = self.at_company()[-1]
        self.assertEqual((wen["name"], wen["title"], wen["role_kind"]), ("Wen Zhao", "Member of Technical Staff",
                                                                         "engineer"))
        self.assertEqual(self.log_rows(wen["person_id"])[0]["note"], "Hi Wen | a note with a bar")

    def test_a_known_name_is_reused_not_added_again(self):
        self.ok(self.paste("Ana Silva | Engineer"))
        count = len(self.at_company())
        res = self.paste("ANA  SILVA | Engineer", key=f"plan:{self.entry_id('Cerebrium')}:people:b2")
        self.refused(res, "not_allowed", 409)  # already asked since the list date: nothing more is written
        self.assertIn("Already logged", res.json()["message"])
        self.assertEqual(len(self.at_company()), count)

    def test_a_replay_with_the_same_key_writes_once(self):
        first = self.ok(self.paste("Ana Silva | Engineer\nBo Chen | Founder"))
        before = self.snapshot()
        again = self.ok(self.paste("Ana Silva | Engineer\nBo Chen | Founder"))  # a new request id, the same key
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(again["row"]["steps"], first["row"]["steps"])
        self.assertEqual([p["added"] for p in again["people"]], [False, False])

    def test_an_undone_request_gives_its_key_back(self):
        self.ok(self.paste("Ana Silva | Engineer"))
        ana = self.at_company()[-1]["person_id"]
        (touch,) = self.touches("linkedin_request", person_id=ana)
        undo_touch(self.conn, touch["touch_id"], at=NOW, revision=touch["revision"])
        self.conn.execute("INSERT INTO linkedin_log(person_id, kind, at, recorded_at, undoes) VALUES (?, 'undo', ?, ?, "
                          "(SELECT entry_id FROM linkedin_log WHERE person_id = ? AND kind = 'request_sent'))",
                          (ana, NOW, NOW, ana))
        self.ok(self.paste("Ana Silva | Engineer"))
        live = [t for t in self.touches("linkedin_request", person_id=ana) if t["undone_at"] is None]
        self.assertEqual(len(live), 1)

    def test_refusals_write_nothing(self):
        before = self.snapshot()
        cases = [("", "bad_request"), ("   \n  ", "bad_request"),
                 ("\n".join(f"P{i} Person | Engineer" for i in range(11)), "bad_request"),
                 (" | Engineer | no name", "bad_request"), ("Ana Silva\nana silva", "bad_request"),
                 ("Ana Silva | " + "x" * 201, "bad_request"),
                 ("Ana Silva | Engineer | " + "y" * 301, "bad_request")]
        for lines, code in cases:
            with self.subTest(lines=lines[:30]):
                res = self.paste(lines)
                self.assertEqual((res.status_code, res.json()["code"]), (400, code), res.text)
        self.assertEqual(self.snapshot(), before)

    def test_no_go_and_paused_refuse_the_whole_paste(self):
        ana = self.conn.execute(
            "INSERT INTO person(company_id, name, role_kind, channel, linkedin_reason, added_by, created_at, "
            "updated_at) VALUES (?, 'Ana Silva', 'engineer', 'linkedin', 'no_email', 'devanshu', ?, ?)",
            (self.ids["Cerebrium"], NOW, NOW)).lastrowid
        self.conn.execute("INSERT INTO no_go(person_id, reason, source, created_at) VALUES (?, 'stop_reply', "
                          "'devanshu', ?)", (ana, NOW))
        before = self.snapshot()
        self.refused(self.paste("Bo Chen | Engineer\nAna Silva | Engineer"), "not_allowed", 409)
        self.assertEqual(self.snapshot(), before)  # Bo Chen was not kept either
        self.conn.execute("DELETE FROM no_go")
        self.conn.execute("UPDATE company SET status = 'working', paused_at = ? WHERE company_id = ?",
                          (NOW, self.ids["Cerebrium"]))
        self.refused(self.paste("Bo Chen | Engineer"), "not_allowed", 409)
        self.conn.execute("UPDATE company SET paused_at = NULL, status = 'candidate'")
        self.conn.execute("INSERT INTO no_go(company_id, reason, source, created_at) VALUES (?, 'not_a_fit', "
                          "'devanshu', ?)", (self.ids["Cerebrium"], NOW))
        self.refused(self.paste("Bo Chen | Engineer"), "not_allowed", 409)
        self.assertEqual(self.snapshot(), before)

    def test_only_a_company_row_takes_a_paste(self):
        self.refused(self.paste("Bo Chen | Engineer", company="Pylon"), "bad_request", 400)
        self.refused(self.paste("Bo Chen | Engineer", company="Mintlify"), "not_allowed", 409)

    def test_his_note_is_checked_as_his_own_and_logged_as_sent(self):
        # The note already went out, so the log says exactly what he sent; the checks come back as warnings.
        res = self.ok(self.paste(f"Ana Silva | Engineer | Hi Ana {DASH} I am passionate about synergy"))
        (person,) = res["people"]
        self.assertTrue(any("banned" in w.lower() for w in person["warnings"]), person["warnings"])
        self.assertTrue(any("dash" in w.lower() for w in person["warnings"]), person["warnings"])
        ana = self.at_company()[-1]["person_id"]
        self.assertEqual(self.log_rows(ana)[0]["note"], f"Hi Ana {DASH} I am passionate about synergy")
        detail = json.loads(self.conn.execute("SELECT detail FROM event WHERE kind = 'request_logged'").fetchone()[0])
        self.assertEqual(set(detail["checks"]), {"banned_phrase", "dash"})
        # his note is never judged by Claude's reason line or fact rules
        self.assertFalse(any("reason" in w.lower() for w in person["warnings"]))


class SaveSearchTests(PeopleCase):
    def test_a_saved_search_is_an_event_and_its_company_id_an_alias(self):
        row = self.ok(self.search(SAVED))
        self.assertEqual((row["queue"]["peopleSearchUrl"], row["queue"]["searchSaved"]), (SAVED, True))
        self.assertEqual(row["findSomeone"]["searchUrl"], SAVED)
        (event,) = self.conn.execute("SELECT * FROM event WHERE kind = 'linkedin_search_saved'").fetchall()
        self.assertEqual((event["entity"], event["entity_id"], event["company_id"], event["actor"],
                          json.loads(event["detail"])),
                         ("company", self.ids["Cerebrium"], self.ids["Cerebrium"], "devanshu", {"url": SAVED}))
        (alias,) = self.conn.execute("SELECT * FROM company_alias WHERE source = 'linkedin'").fetchall()
        self.assertEqual((alias["alias_key"], alias["company_id"]), ("linkedin:1304", self.ids["Cerebrium"]))
        self.assertEqual(row, self.row("Cerebrium"))

    def test_saving_the_same_search_again_writes_nothing(self):
        self.ok(self.search(SAVED))
        before = self.snapshot()
        self.ok(self.search(SAVED))
        self.ok(self.search(f"  {SAVED}\n"))
        self.assertEqual(self.snapshot(), before)
        other = f"{SEARCH}?keywords=cerebrium%20engineer"
        self.assertEqual(self.ok(self.search(other))["queue"]["peopleSearchUrl"], other)
        self.assertEqual(self.count("event", "WHERE kind = 'linkedin_search_saved'"), 2)

    def test_only_a_linkedin_people_search_is_kept(self):
        before = self.snapshot()
        for bad in ("https://evil.example/search/results/people/", "http://www.linkedin.com/search/results/people/",
                    "https://me:pw@www.linkedin.com/search/results/people/",
                    "https://www.linkedin.com/in/someone", "javascript:alert(1)", ""):
            with self.subTest(bad=bad):
                self.refused(self.search(bad), "bad_request", 400)
        self.assertEqual(self.snapshot(), before)

    def test_an_id_another_company_owns_is_not_moved(self):
        self.conn.execute("INSERT INTO company_alias(alias_key, company_id, alias, source, created_at) VALUES "
                          "('linkedin:1304', ?, 'linkedin:1304', 'linkedin', ?)", (self.ids["Pylon"], NOW))
        row = self.ok(self.search(SAVED))
        self.assertEqual(row["queue"]["peopleSearchUrl"], SAVED)  # the search is still his
        owners = [r[0] for r in self.conn.execute("SELECT company_id FROM company_alias WHERE source = 'linkedin'")]
        self.assertEqual(owners, [self.ids["Pylon"]])

    def test_a_removed_row_takes_no_search(self):
        self.conn.execute("UPDATE daily_list_entry SET state = 'removed', decided_at = ? WHERE rowid = ?",
                          (NOW, self.entry_id("Cerebrium")))
        self.refused(self.search(SAVED), "not_allowed", 409)
        self.assertEqual(self.count("event", "WHERE kind = 'linkedin_search_saved'"), 0)
