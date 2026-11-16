"""pick_people (#290, #9 reach out ruling of 2026-11-14): code, not the model, decides who at a company gets a cold
email, from the people an Apollo search returned. It reads only."""
from tests import apiseed as seed
from tests.mcptest import McpCase, fingerprint

ATARAXIS_LINK = "https://jobs.ashbyhq.com/ataraxis-ai/6548cc32-6db1-452c-b0b2-516e042fe8d2"


def cand(name, title, city="New York", state="New York", country="United States", **more):
    return {"name": name, "title": title, "city": city, "region": state, "country": country, **more}


class PickPeopleCase(McpCase):
    def setUp(self):
        super().setUp()
        self.cid = seed.company(self.db, "Ataraxis AI")
        self.job = seed.posting(self.db, self.cid, "Software Engineer", url=ATARAXIS_LINK, location="New York, NY",
                                first_seen_at="2026-11-10T09:00:00Z")

    def size(self, n):
        seed.evidence(self.db, self.cid, "size", str(n), low=n, high=n)

    def pick(self, candidates, **more):
        return self.ok("pick_people", {"company_id": self.cid, "posting_url": ATARAXIS_LINK,
                                        "candidates": candidates, **more})

    def names(self, got):
        return [p["name"] for p in got["picked"]]

    def test_under_50_founder_or_cto_first_then_founding_engineer_then_recruiter(self):
        self.size(40)
        got = self.pick([cand("Rae Recruiter", "Technical Recruiter"), cand("Eli Engineer", "Software Engineer"),
                         cand("Fay Founding", "Founding Engineer"), cand("Cy Cto", "Co-Founder & CTO"),
                         cand("Jan Ceo", "Founder & CEO")])
        self.assertEqual(got["size_band"], "under_50")
        self.assertEqual(self.names(got), ["Cy Cto", "Fay Founding", "Rae Recruiter", "Eli Engineer", "Jan Ceo"])
        self.assertEqual([p["role_kind"] for p in got["picked"]][:3], ["founder", "engineer", "recruiter"])
        self.assertTrue(all(p["why"] for p in got["picked"]))

    def test_50_to_200_manager_and_technical_recruiter_then_cto_or_ceo_then_engineer(self):
        self.size(120)
        got = self.pick([cand("Eli Engineer", "Senior Software Engineer"), cand("Jan Ceo", "CEO"),
                         cand("Rae Recruiter", "Technical Recruiter"), cand("Max Manager", "Engineering Manager")])
        self.assertEqual(got["size_band"], "50_to_200")
        self.assertEqual(self.names(got), ["Max Manager", "Rae Recruiter", "Jan Ceo", "Eli Engineer"])

    def test_over_200_recruiter_first_and_never_an_executive(self):
        self.size(900)
        got = self.pick([cand("Eli Engineer", "Software Engineer"), cand("Vic Vp", "VP of Engineering"),
                         cand("Jan Ceo", "Co-Founder & CEO"), cand("Max Manager", "Software Engineering Manager"),
                         cand("Uma Campus", "University Recruiter")])
        self.assertEqual(self.names(got), ["Uma Campus", "Max Manager", "Eli Engineer"])
        skipped = {s["name"]: s["reason"] for s in got["not_picked"]}
        self.assertIn("executive", skipped["Vic Vp"])
        self.assertIn("executive", skipped["Jan Ceo"])

    def test_the_jobs_city_first_then_its_state_then_the_us(self):
        self.size(900)
        got = self.pick([cand("Ana Far", "Software Engineer", city="Austin", state="Texas"),
                         cand("Ben State", "Software Engineer", city="Buffalo", state="New York"),
                         cand("Cal City", "Software Engineer", city="New York", state="NY")], max_people=3)
        self.assertEqual(self.names(got), ["Cal City", "Ben State", "Ana Far"])

    def test_never_outside_the_us_interns_advisors_agency_or_non_engineering_people(self):
        self.size(40)
        got = self.pick([cand("Out Side", "Software Engineer", city="Warsaw", state="Masovian", country="Poland"),
                         cand("In Tern", "Software Engineering Intern"), cand("Ad Visor", "Advisor"),
                         cand("Ag Ency", "Recruiter at a staffing agency"), cand("Sal Es", "Sales Recruiter"),
                         cand("Pro Duct", "Product Manager"), cand("Ok Person", "Software Engineer")])
        self.assertEqual(self.names(got), ["Ok Person"])
        reasons = {s["name"]: s["reason"] for s in got["not_picked"]}
        self.assertIn("outside the US", reasons["Out Side"])
        self.assertIn("intern", reasons["In Tern"])
        self.assertIn("advisor", reasons["Ad Visor"])
        self.assertIn("agency", reasons["Ag Ency"])
        self.assertIn("not an engineering recruiter", reasons["Sal Es"])
        self.assertIn("not on the engineering team", reasons["Pro Duct"])

    def test_pre_sales_and_support_managers_are_never_picked(self):
        """#296 (Verkada): a Solutions Engineering Manager is pre-sales, not the engineering team."""
        self.size(900)
        got = self.pick([cand(t, t) for t in ("Solutions Engineering Manager", "Manager, Sales Engineering",
                                              "Customer Engineering Manager", "Support Engineering Manager",
                                              "Field Engineering Manager", "Software Engineering Manager",
                                              "Senior Software Engineer, Support Systems",
                                              "Software Engineer, Customer Platform")], max_people=10)
        self.assertEqual(sorted(self.names(got)), ["Senior Software Engineer, Support Systems",
                                                   "Software Engineer, Customer Platform", "Software Engineering Manager"])
        self.assertTrue(all("not on the engineering team" in s["reason"] for s in got["not_picked"]))

    def test_people_already_emailed_or_no_go_are_skipped_and_a_linkedin_person_is_kept_with_their_id(self):
        self.size(40)
        sent = seed.person(self.db, self.cid, "Sam Sent", title="Software Engineer")
        seed.send_first(self.db, seed.touch(self.db, sent))
        jan = seed.person(self.db, self.cid, "Jan Witowski", role_kind="founder", channel="linkedin", title="CEO")
        stop = seed.person(self.db, self.cid, "Nora Nogo", title="Software Engineer")
        self.db.execute("INSERT INTO no_go(person_id, reason, source, created_at) VALUES (?, 'said_no', 'devanshu', ?)",
                        (stop, "2026-11-01T00:00:00Z"))
        got = self.pick([cand("Sam Sent", "Software Engineer"), cand("jan witowski", "Founder & CEO"),
                         cand("Nora Nogo", "Software Engineer")])
        self.assertEqual(self.names(got), ["jan witowski"])
        self.assertEqual(got["picked"][0]["person_id"], jan)
        reasons = {s["name"]: s["reason"] for s in got["not_picked"]}
        self.assertIn("already emailed", reasons["Sam Sent"])
        self.assertIn("no-go", reasons["Nora Nogo"])

    def test_a_masked_apollo_name_is_matched_to_the_person_on_record(self):
        """#294: Apollo's free search hides surnames ("Alec Co***y"); Knock's Alec Condry must still be found."""
        self.size(900)
        alec = seed.person(self.db, self.cid, "Alec Condry", channel="linkedin", title="Software Engineer")
        sent = seed.person(self.db, self.cid, "Vanshika Jain", title="Software Engineer")
        seed.send_first(self.db, seed.touch(self.db, sent))
        got = self.pick([cand("Alec Co***y", "Software Engineer"), cand("Vanshika Ja***n", "Software Engineer"),
                         cand("Alec Ba***r", "Software Engineer"), cand("Ann Liu", "Software Engineer")])
        by = {p["name"]: p for p in got["picked"]}
        self.assertEqual(by["Alec Co***y"]["person_id"], alec)
        self.assertIsNone(by["Alec Ba***r"]["person_id"])
        self.assertIsNone(by["Ann Liu"]["person_id"])
        self.assertIn("already emailed", {s["name"]: s["reason"] for s in got["not_picked"]}["Vanshika Ja***n"])

    def test_two_masked_names_that_could_be_one_person_on_record_are_not_guessed(self):
        self.size(900)
        seed.person(self.db, self.cid, "Alec Condry", channel="linkedin", title="Software Engineer")
        seed.person(self.db, self.cid, "Alec Cowdry", channel="linkedin", title="Software Engineer")
        got = self.pick([cand("Alec Co***y", "Software Engineer")])
        self.assertIsNone(got["picked"][0]["person_id"])
        self.assertIn("maybe", got["picked"][0]["why"])

    def test_at_most_max_people_and_the_rest_say_why(self):
        self.size(900)
        got = self.pick([cand(f"Eng {i}", "Software Engineer") for i in range(7)], max_people=2)
        self.assertEqual(len(got["picked"]), 2)
        self.assertEqual(sum("limit" in s["reason"] for s in got["not_picked"]), 5)

    def test_size_from_apollo_only_when_knock_has_none(self):
        got = self.pick([cand("Jan Ceo", "CEO"), cand("Max Manager", "Engineering Manager")], company_size=30)
        self.assertEqual((got["size_band"], got["size_basis"]), ("under_50", "apollo"))
        self.size(150)
        got = self.pick([cand("Jan Ceo", "CEO")], company_size=30)
        self.assertEqual((got["size_band"], got["size_basis"]), ("50_to_200", "knock"))

    def test_unknown_size_uses_the_middle_order(self):
        got = self.pick([cand("Jan Ceo", "CEO"), cand("Max Manager", "Engineering Manager")])
        self.assertEqual((got["size_band"], got["size_basis"]), ("unknown", "none"))
        self.assertEqual(self.names(got), ["Max Manager", "Jan Ceo"])

    def test_team_words_from_the_posting_rank_people_inside_a_role(self):
        self.size(900)
        seed.posting(self.db, self.cid, "Machine Learning Platform Engineer", url="https://jobs.example/ml")
        got = self.ok("pick_people", {"company_id": self.cid, "posting_url": "https://jobs.example/ml", "max_people": 1,
                                      "candidates": [cand("Gen Eral", "Software Engineer"),
                                                     cand("Pla Tform", "Software Engineer, Platform")]})
        self.assertEqual(self.names(got), ["Pla Tform"])

    def test_a_closed_or_no_go_company_picks_no_one(self):
        self.db.execute("UPDATE company SET status = 'closed', close_reason = 'other' WHERE company_id = ?", (self.cid,))
        got = self.pick([cand("Ok Person", "Software Engineer")])
        self.assertEqual(got["picked"], [])
        self.assertIn("closed", got["stop"])

    def test_reads_only(self):
        before = fingerprint(self.db)
        self.pick([cand("Ok Person", "Software Engineer")])
        self.assertEqual(fingerprint(self.db), before)
