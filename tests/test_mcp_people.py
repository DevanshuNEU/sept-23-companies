"""add_people (#13, plan Task 10, lean v1), called through the official SDK's client on a scratch store.

People Devanshu gives Claude are stored added_by claude (set by code). Someone already known by email or LinkedIn
page (or, with neither, by name at the company) is not added again: their id comes back with created false. A
merged company id adds to its survivor. A company or person on an active no-go is refused with a plain reason."""
import unittest

from tests import apiseed as seed
from tests.mcptest import McpCase, fingerprint
from tests.test_mcp_reads import merge, no_go, outreach_touch

PAGE = "https://acme.example/team"


def person(name="Pat Lee", **extra) -> dict:
    out = {"name": name, "role_kind": "engineer"}
    out.update(extra)
    return out


class PeopleCase(McpCase):
    def setUp(self):
        super().setUp()
        self.acme = seed.company(self.db, "Acme", "working")

    def add(self, *people, company_id=None) -> dict:
        return self.ok("add_people", {"company_id": company_id or self.acme, "people": list(people)})

    def people(self):
        return self.db.execute("SELECT * FROM person ORDER BY person_id").fetchall()

    def count(self):
        return self.db.execute("SELECT COUNT(*) FROM person").fetchone()[0]


class AddPeopleTest(PeopleCase):
    def test_a_new_person_is_added_by_claude_with_sources_and_an_event(self):
        got = self.add(person(title="Staff Engineer", email=" Pat@Acme.Example ",
                              linkedin_url="https://www.linkedin.com/in/Pat-Lee/?trk=public",
                              profile_text="Pat builds the payments queue.", notes="Met at a meetup.",
                              sources=[{"kind": "team", "url": PAGE, "label": "Team page", "quote": "Pat Lee, Staff"},
                                       {"kind": "email", "url": PAGE, "email_basis": "published"}]))
        self.assertEqual(got["company"], {"id": self.acme, "name": "Acme", "status": "working"})
        res = got["results"][0]
        row = self.people()[0]
        self.assertEqual((res["index"], res["name"], res["person_id"], res["created"], res["refused"]),
                         (0, "Pat Lee", row["person_id"], True, None))
        self.assertEqual((row["company_id"], row["name"], row["title"], row["role_kind"], row["email"],
                          row["linkedin_url"], row["profile_text"], row["notes"], row["channel"], row["added_by"]),
                         (self.acme, "Pat Lee", "Staff Engineer", "engineer", "pat@acme.example",
                          "https://www.linkedin.com/in/pat-lee", "Pat builds the payments queue.", "Met at a meetup.",
                          "email", "claude"))
        sources = self.db.execute("SELECT kind, url, label, quote, email_basis FROM person_source "
                                  "ORDER BY source_id").fetchall()
        self.assertEqual([tuple(s) for s in sources], [("team", PAGE, "Team page", "Pat Lee, Staff", None),
                                                       ("email", PAGE, "", "", "published")])
        events = self.db.execute("SELECT kind, actor, entity_id, company_id FROM event WHERE entity = 'person'"
                                 ).fetchall()
        self.assertEqual([tuple(e) for e in events], [("added", "claude", row["person_id"], self.acme)])

    def test_a_person_with_no_address_is_a_linkedin_contact(self):
        self.add(person(linkedin_url="https://linkedin.com/in/patlee"))
        row = self.people()[0]
        self.assertEqual((row["channel"], row["linkedin_reason"], row["email"]), ("linkedin", "no_email", None))

    def test_someone_known_by_email_or_linkedin_is_not_added_again(self):
        by_mail = seed.person(self.db, self.acme, "Pat Lee", email="pat@acme.example")
        by_page = seed.person(self.db, self.acme, "Ann Page", email=None, channel="linkedin",
                              linkedin_url="https://linkedin.com/in/Ann-Page/")
        before = fingerprint(self.db)
        got = self.add(person("Patricia Lee", email="PAT@acme.example"),
                       person("Ann P.", linkedin_url="https://www.linkedin.com/in/ann-page?utm=x"))
        self.assertEqual([(r["person_id"], r["created"], r["refused"]) for r in got["results"]],
                         [(by_mail, False, None), (by_page, False, None)])
        self.assertEqual([r["flags"] for r in got["results"]], [["duplicate"], ["duplicate"]])
        self.assertEqual(fingerprint(self.db), before)

    def test_with_no_address_or_page_the_same_name_at_the_company_is_the_same_person(self):
        known = seed.person(self.db, self.acme, "Pat Lee", email=None, channel="linkedin")
        got = self.add(person("  pat   LEE "))
        self.assertEqual((got["results"][0]["person_id"], got["results"][0]["created"]), (known, False))
        self.assertEqual(self.count(), 1)

    def test_a_duplicate_fills_an_empty_profile_and_notes_and_never_overwrites_them(self):
        known = seed.person(self.db, self.acme, "Pat Lee", email="pat@acme.example")
        self.db.execute("UPDATE person SET notes = 'his note' WHERE person_id = ?", (known,))
        got = self.add(person(email="pat@acme.example", profile_text="Builds the queue.", notes="Claude's note",
                              sources=[{"kind": "profile", "url": PAGE}]))
        self.assertEqual(got["results"][0]["flags"], ["duplicate", "filled_profile_text", "added_sources"])
        row = self.people()[0]
        self.assertEqual((row["profile_text"], row["notes"], row["revision"]), ("Builds the queue.", "his note", 2))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM person_source").fetchone()[0], 1)
        again = self.add(person(email="pat@acme.example", profile_text="Something else",
                                sources=[{"kind": "profile", "url": PAGE}]))
        self.assertEqual(again["results"][0]["flags"], ["duplicate"])
        self.assertEqual(self.people()[0]["revision"], 2)

    def test_the_same_person_twice_in_one_call_is_added_once(self):
        got = self.add(person(email="pat@acme.example"), person("Pat L", email="pat@acme.example"))
        first, second = got["results"]
        self.assertEqual((first["created"], second["created"], second["person_id"]),
                         (True, False, first["person_id"]))
        self.assertEqual(self.count(), 1)

    def test_a_person_known_at_another_company_is_returned_and_not_moved(self):
        other = seed.company(self.db, "Other Co")
        theirs = seed.person(self.db, other, "Pat Lee", email="pat@other.example")
        got = self.add(person(email="pat@other.example", notes="moved?"))
        res = got["results"][0]
        self.assertEqual((res["person_id"], res["created"], res["flags"]), (theirs, False,
                                                                            ["duplicate", "other_company"]))
        self.assertIn("Other Co", res["message"])
        row = self.people()[0]
        self.assertEqual((row["company_id"], row["notes"]), (other, ""))

    def test_a_merged_company_id_adds_to_the_survivor(self):
        dup = seed.company(self.db, "Acme Dup")
        merge(self.db, dup, self.acme)
        got = self.add(person(email="pat@acme.example"), company_id=dup)
        self.assertEqual(got["company"]["id"], self.acme)
        self.assertIn(str(dup), got["merged_note"])
        self.assertEqual(self.people()[0]["company_id"], self.acme)

    def test_a_company_on_the_no_go_list_or_removed_refuses_the_call(self):
        dup = seed.company(self.db, "Acme Dup")
        no_go(self.db, company_id=dup)
        merge(self.db, dup, self.acme)  # a no-go on a merged duplicate still stops the survivor
        err = self.error("add_people", {"company_id": self.acme, "people": [person(email="pat@acme.example")]})
        self.assertEqual(err["code"], "company_no_go")
        self.assertIn("Acme", err["message"])
        gone = seed.company(self.db, "Gone Co", "removed")
        self.assertEqual(self.error("add_people", {"company_id": gone, "people": [person()]})["code"],
                         "company_removed")
        self.assertEqual(self.count(), 0)

    def test_a_person_on_the_no_go_list_is_refused_plainly_and_the_rest_are_added(self):
        nora = seed.person(self.db, self.acme, "Nora Nogo", email="nora@acme.example")
        no_go(self.db, person_id=nora)
        got = self.add(person("Nora Nogo", email="nora@acme.example", notes="try again"),
                       person(email="pat@acme.example"))
        refused, added = got["results"]
        self.assertEqual((refused["person_id"], refused["created"], refused["refused"]), (nora, False, "person_no_go"))
        self.assertIn("no-go", refused["message"])
        self.assertTrue(added["created"])
        self.assertEqual(self.db.execute("SELECT notes FROM person WHERE person_id = ?", (nora,)).fetchone()[0], "")

    def test_an_address_and_a_page_of_two_different_people_is_refused(self):
        a = seed.person(self.db, self.acme, "Pat Lee", email="pat@acme.example")
        b = seed.person(self.db, self.acme, "Sam Page", email=None, channel="linkedin",
                        linkedin_url="https://www.linkedin.com/in/sampage")
        got = self.add(person(email="pat@acme.example", linkedin_url="https://www.linkedin.com/in/sampage"))
        res = got["results"][0]
        self.assertEqual((res["refused"], res["person_id"], res["created"]), ("two_people", None, False))
        self.assertIn(str(a), res["message"])
        self.assertIn(str(b), res["message"])

    def test_one_bad_person_rejects_the_whole_call_and_nothing_is_written(self):
        before = fingerprint(self.db)
        for people, where in (([person(email="a@acme.example"), person("B"), person("C", role_kind="ceo")],
                               "people.2"),
                              ([person(email="a@acme.example"), person("B", email="not-an-address")], "people.1"),
                              ([person(), person("B", linkedin_url="https://www.linkedin.com/company/acme")],
                               "people.1"),
                              ([person("   ")], "people.0")):
            with self.subTest(where=where):
                err = self.error("add_people", {"company_id": self.acme, "people": people})
                self.assertEqual(err["code"], "bad_input")
                self.assertIn(where, err["message"])
        self.assertEqual(fingerprint(self.db), before)

    def test_the_batch_and_the_sources_are_bounded(self):
        for people in ([], [person(f"P{i}") for i in range(11)],
                       [person(sources=[{"kind": "profile", "url": f"{PAGE}/{i}"} for i in range(7)])]):
            with self.subTest(n=len(people)):
                self.assertEqual(self.error("add_people", {"company_id": self.acme, "people": people})["code"],
                                 "bad_input")

    def test_who_added_them_their_channel_and_company_are_not_inputs(self):
        for extra in ({"added_by": "devanshu"}, {"channel": "email"}, {"company_id": 5}, {"source": "hand"},
                      {"email_rank": 1}):
            with self.subTest(extra=extra):
                self.assertEqual(self.error("add_people", {"company_id": self.acme,
                                                           "people": [person(**extra)]})["code"], "bad_input")
        self.assertEqual(self.count(), 0)

    def test_source_rules(self):
        for source in ({"kind": "profile", "url": "https://www.linkedin.com/in/pat"},
                       {"kind": "profile", "url": "https://lnkd.in/abc"},
                       {"kind": "profile", "url": "http://acme.example/team"},
                       {"kind": "email", "url": PAGE},
                       {"kind": "team", "url": PAGE, "email_basis": "published"},
                       {"kind": "resume", "url": PAGE},
                       {"kind": "team", "url": PAGE, "observed_on": "2026-13-01"}):
            with self.subTest(source=source):
                self.assertEqual(self.error("add_people", {"company_id": self.acme,
                                                           "people": [person(sources=[source])]})["code"],
                                 "bad_input")
        self.assertEqual(self.count(), 0)

    def test_unknown_company_is_not_found(self):
        self.assertEqual(self.error("add_people", {"company_id": 999, "people": [person()]})["code"], "not_found")


class SameNameTest(PeopleCase):
    """A known person who must not be emailed cannot come back as a fresh, draftable person under a new address or
    LinkedIn page (review I1): the same-name lookup runs across the company family whatever else is given."""

    def stopped(self, how: str, name: str, email: str) -> int:
        nora = seed.person(self.db, self.acme, name, email=email)
        if how == "no_go":
            no_go(self.db, person_id=nora)
        elif how == "touch":
            seed.touch(self.db, nora, "replied", reply_kind="asked_to_stop")
        else:
            outreach_touch(self.db, self.acme, nora)
            self.db.execute("UPDATE outreach_touch SET reply_kind = 'said_no', replied_at = ? WHERE person_id = ?",
                            ("2026-11-02T12:00:00Z", nora))
        return nora

    def test_re_adding_a_stopped_person_with_a_new_email_or_linkedin_url_is_refused(self):
        n = 0
        for how, code in (("no_go", "person_no_go"), ("touch", "said_no"), ("outreach_touch", "said_no")):
            for kind in ("email", "linkedin_url"):
                n += 1
                with self.subTest(how=how, kind=kind):
                    name = f"Nora Stop {'ABCDEF'[n - 1]}"
                    nora = self.stopped(how, name, f"nora{n}@acme.example")
                    new = (f"nora{n}.new@acme.example" if kind == "email"
                           else f"https://www.linkedin.com/in/nora-new-{n}")
                    before = fingerprint(self.db)
                    res = self.add(person(f"  {name.upper()} ", **{kind: new}))["results"][0]
                    self.assertEqual((res["refused"], res["created"], res["person_id"]), (code, False, nora))
                    self.assertIn(name, res["message"])
                    self.assertEqual(fingerprint(self.db), before)

    def test_a_stopped_namesake_at_a_merged_duplicate_counts(self):
        dup = seed.company(self.db, "Acme Dup")
        nora = seed.person(self.db, dup, "Nora Stop", email="nora@acme.example")
        no_go(self.db, person_id=nora)
        merge(self.db, dup, self.acme)
        res = self.add(person("Nora Stop", email="nora2@acme.example"))["results"][0]
        self.assertEqual((res["refused"], res["person_id"]), ("person_no_go", nora))

    def test_a_namesake_who_may_be_emailed_does_not_stop_a_new_person_and_is_named(self):
        pat = seed.person(self.db, self.acme, "Pat Lee", email="pat@acme.example")
        res = self.add(person("Pat Lee", email="pat.lee@acme.example"))["results"][0]
        self.assertTrue(res["created"])
        self.assertEqual(res["flags"], [f"same_name_as:{pat}"])
        self.assertEqual(self.count(), 2)

    def test_a_known_person_who_said_no_is_refused_and_left_as_they_are(self):
        sid = seed.person(self.db, self.acme, "Sid No", email="sid@acme.example")
        seed.touch(self.db, sid, "replied", reply_kind="said_no")
        before = fingerprint(self.db)
        res = self.add(person("Sid No", email="sid@acme.example", notes="try again"))["results"][0]
        self.assertEqual((res["refused"], res["person_id"], res["created"]), ("said_no", sid, False))
        self.assertEqual(fingerprint(self.db), before)

    def test_a_name_only_match_is_flagged_and_fills_nothing(self):
        known = seed.person(self.db, self.acme, "Pat Lee", email=None, channel="linkedin")
        before = fingerprint(self.db)
        res = self.add(person("Pat Lee", profile_text="Builds the queue.", notes="A note",
                              sources=[{"kind": "profile", "url": PAGE}]))["results"][0]
        self.assertEqual((res["person_id"], res["created"], res["flags"]),
                         (known, False, ["duplicate", "name_only_match"]))
        self.assertEqual(fingerprint(self.db), before)

if __name__ == "__main__":
    unittest.main()
