import unittest

from outreach.importer.link import CompanyIndex, PersonIndex, carry_stops, company_key, parse_person_name
from outreach.importer.manifest import Overrides
from outreach.importer.model import IssueIn, OutcomeIn, TouchIn
from outreach.sources import keys


def overrides(**kw) -> Overrides:
    base = dict(same_company={}, not_same_company=frozenset(), person_name={}, done_add=frozenset(),
                done_remove=frozenset(), touch_for={})
    base.update(kw)
    return Overrides(**base)


def codes(issues) -> list[str]:
    return sorted(i.code for i in issues)


class CompanyIndexTest(unittest.TestCase):
    def test_exact_name_key_links_sources_to_the_data_js_company(self):
        idx = CompanyIndex(overrides())
        base = idx.add("Kestrel Robotics", "data_js:company:kestrel", base=True)
        other = idx.add("Kestrel Robotics, Inc.", "devvault_notes:file:reached-out/kestrel-robotics.md")
        lower = idx.add("kestrel robotics", "contacted_txt:line:5")
        self.assertEqual(base, "kestrelrobotics")
        self.assertEqual({base}, {other, lower})
        self.assertEqual(idx.keys(), ["kestrelrobotics"])
        self.assertEqual(idx.name(base), "Kestrel Robotics")  # the old dashboard's spelling is the name
        self.assertEqual(idx.names(base), frozenset({"Kestrel Robotics", "Kestrel Robotics, Inc.",
                                                     "kestrel robotics"}))
        self.assertEqual(idx.records(base), ("data_js:company:kestrel",
                                             "devvault_notes:file:reached-out/kestrel-robotics.md",
                                             "contacted_txt:line:5"))
        self.assertEqual(idx.issues(), [])

    def test_near_names_raise_one_merge_item_and_stay_two_companies(self):
        def build(**kw):
            idx = CompanyIndex(overrides(**kw))
            a = idx.add("Tidewater AI", "data_js:company:tidewater", base=True)
            b = idx.add("Tidewater", "contacted_txt:line:9")
            return idx, a, b

        idx, a, b = build()
        self.assertNotEqual(a, b)
        self.assertEqual(idx.keys(), ["tidewater", "tidewaterai"])
        self.assertEqual(idx.near_names(), [("tidewater", "tidewaterai")])

        idx, a, b = build(not_same_company=frozenset({frozenset({"Tidewater", "Tidewater AI"})}))
        self.assertNotEqual(a, b)
        self.assertEqual(idx.near_names(), [])

        idx, a, b = build(same_company={"Tidewater": "Tidewater AI"})
        self.assertEqual(a, b)
        self.assertEqual(idx.keys(), ["tidewaterai"])
        self.assertEqual(idx.near_names(), [])

    def test_near_name_words_and_parentheticals(self):
        idx = CompanyIndex(overrides())
        for name in ("Mosaic", "Mosaic HQ", "Fable (YC W24)", "Fable", "Arc", "Arc Labs"):
            idx.add(name, f"contacted_txt:line:{name}")
        self.assertEqual(idx.near_names(), [("arc", "arclabs"), ("fable", "fableycw24"), ("mosaic", "mosaichq")])

    def test_short_prefix_is_not_a_near_name(self):
        idx = CompanyIndex(overrides())
        idx.add("Arc", "contacted_txt:line:1")
        idx.add("Arcadia", "contacted_txt:line:2")
        self.assertEqual(idx.near_names(), [])
        idx.add("Tidal", "contacted_txt:line:3")
        idx.add("Tidalwave Bio", "contacted_txt:line:4")
        self.assertEqual(idx.near_names(), [("tidal", "tidalwavebio")])  # five characters is enough

    def test_same_company_override_by_record_id_moves_only_that_record(self):
        idx = CompanyIndex(overrides(same_company={"curated_touches:row:3": "Tidewater AI"}))
        a = idx.add("Tidewater AI", "data_js:company:tidewater", base=True)
        b = idx.add("TW", "curated_touches:row:3")
        c = idx.add("TW", "curated_touches:row:4")
        self.assertEqual(a, b)
        self.assertEqual(c, "tw")

    def test_override_loop_is_an_error(self):
        with self.assertRaises(ValueError):
            CompanyIndex(overrides(same_company={"Acme": "Acme Labs", "Acme Labs": "Acme"})).add("Acme", "x:1")

    def test_two_data_js_companies_with_one_key_stay_two_and_raise_items(self):
        idx = CompanyIndex(overrides())
        first = idx.add("Relay", "data_js:company:relay-app", base=True)
        again = idx.add("Relay", "data_js:company:relay-app", base=True)  # the same record twice is one company
        second = idx.add("RELAY", "data_js:company:relay-net", base=True)
        mention = idx.add("Relay", "contacted_txt:line:7")
        self.assertEqual(first, again)
        self.assertNotEqual(first, second)
        self.assertEqual(mention, first)
        self.assertEqual(codes(idx.issues()), ["base_same_key", "company_ambiguous"])
        self.assertEqual(idx.near_names(), [])  # already an item; not offered twice
        # his ruling joins them, and then nothing is ambiguous
        idx = CompanyIndex(overrides(same_company={"relay~2": "relay"}))
        a = idx.add("Relay", "data_js:company:relay-app", base=True)
        b = idx.add("RELAY", "data_js:company:relay-net", base=True)
        idx.add("Relay", "contacted_txt:line:7")
        self.assertEqual(a, b)
        self.assertEqual(codes(idx.issues()), [])

    def test_names_outside_ascii_never_collapse_to_one_key(self):
        self.assertEqual(company_key("Kestrel Robotics, Inc."), "kestrelrobotics")
        self.assertEqual(company_key("Café Labs"), company_key("Cafe Labs"))
        self.assertNotEqual(company_key("Ōura"), company_key("Ura"))
        self.assertNotEqual(company_key("東京ロボ"), company_key("大阪ロボ"))
        self.assertTrue(company_key("東京ロボ"))
        with self.assertRaises(ValueError):
            company_key("  ")
        with self.assertRaises(ValueError):
            CompanyIndex(overrides()).add("", "contacted_txt:line:1")


class PersonNameTest(unittest.TestCase):
    def test_role_word_is_not_a_name(self):
        for text in ("founders", "Founders", "the CTO", "hiring team", "recruiter", "CEO", "2 engineers",
                     "a b c d e", "", "   "):
            with self.subTest(text=text):
                self.assertIsNone(parse_person_name(text))
        for text, want in (("Dana", "Dana"), ("Dana  Whitfield", "Dana Whitfield"), ("Jean-Luc O'Brien", "Jean-Luc O'Brien"),
                           ("María de la Cruz", "María de la Cruz")):
            with self.subTest(text=text):
                self.assertEqual(parse_person_name(text), want)


class PersonIndexTest(unittest.TestCase):
    CO = "kestrelrobotics"

    def test_email_joins_before_name(self):
        people = PersonIndex()
        a = people.add(self.CO, "Ada Fenwick", email="Ada.Fenwick@Example.com ", record="contacts_json:contact:c01")
        b = people.add(self.CO, "Ada Fenwik", email="ada.fenwick@example.com",
                       record="devvault_notes:file:reached-out/kestrel-robotics.md")
        people.finalize()
        self.assertEqual(people.resolve(a), people.resolve(b))
        self.assertEqual(len(people.people()), 1)
        person = people.person(a)
        self.assertEqual(person.email, "ada.fenwick@example.com")
        self.assertEqual(person.name, "Ada Fenwick")
        self.assertEqual(person.key, "kestrelrobotics/ada.fenwick@example.com")
        self.assertEqual(people.issues, [])

    def test_linkedin_slug_joins_in_any_spelling(self):
        people = PersonIndex()
        a = people.add(self.CO, "Oren Vale", linkedin="https://www.linkedin.com/in/Oren-Vale/", record="r:1")
        b = people.add(self.CO, "O. Vale", linkedin="linkedin.com/in/oren-vale", record="r:2")
        c = people.add(self.CO, "Oren", linkedin="http://linkedin.com/in/OREN-VALE?trk=x", record="r:3")
        people.finalize()
        self.assertEqual(len({people.resolve(k) for k in (a, b, c)}), 1)
        self.assertEqual(people.person(a).linkedin, "https://www.linkedin.com/in/oren-vale")

    def test_first_name_joins_the_company_file_person_once(self):
        people = PersonIndex()
        dana = people.add(self.CO, "Dana Whitfield", email="dana@example.com", record="devvault_notes:file:k.md")
        first = people.add(self.CO, "Dana", record="contacted_txt:line:5")
        again = people.add(self.CO, "dana", record="contacted_txt:line:9")
        people.finalize()
        self.assertEqual({people.resolve(first), people.resolve(again)}, {people.resolve(dana)})
        self.assertEqual(len(people.people()), 1)
        self.assertEqual(people.person(first).records,
                         ("devvault_notes:file:k.md", "contacted_txt:line:5", "contacted_txt:line:9"))
        self.assertEqual(codes(people.issues), ["same_person"])  # one line for him, not one per mention

        # the order the sources come in does not change it
        people = PersonIndex()
        first = people.add(self.CO, "Dana", record="contacted_txt:line:5")
        dana = people.add(self.CO, "Dana Whitfield", email="dana@example.com", record="devvault_notes:file:k.md")
        people.finalize()
        self.assertEqual(people.resolve(first), people.resolve(dana))

        # two Danas: a first name alone is nobody in particular
        people = PersonIndex()
        whitfield = people.add(self.CO, "Dana Whitfield", record="devvault_notes:file:k.md")
        price = people.add(self.CO, "Dana Price", email="dprice@example.com", record="contacts_json:contact:c09")
        first = people.add(self.CO, "Dana", record="contacted_txt:line:5")
        people.finalize()
        self.assertNotIn(people.resolve(first), {people.resolve(whitfield), people.resolve(price)})
        self.assertEqual(len(people.people()), 3)
        self.assertEqual(codes(people.issues), ["person_ambiguous"])

        # a first name at another company is someone else
        people = PersonIndex()
        dana = people.add(self.CO, "Dana Whitfield", record="devvault_notes:file:k.md")
        other = people.add("lumenfreight", "Dana", record="contacted_txt:line:6")
        people.finalize()
        self.assertNotEqual(people.resolve(other), people.resolve(dana))

    def test_name_only_mention_folds_into_the_one_namesake(self):
        people = PersonIndex()
        bare = people.add(self.CO, "Ada Fenwick", record="contacted_txt:line:1")
        full = people.add(self.CO, "ada  fenwick", email="ada@example.com", record="contacts_json:contact:c01")
        people.finalize()
        self.assertEqual(people.resolve(bare), people.resolve(full))
        self.assertEqual(people.issues, [])

    def test_same_name_with_another_address_is_a_new_person_and_an_item(self):
        people = PersonIndex()
        a = people.add(self.CO, "Sam Lee", email="sam@example.com", record="r:1")
        b = people.add(self.CO, "Sam Lee", email="sam.lee@other.example", record="r:2")
        bare = people.add(self.CO, "Sam Lee", record="r:3")
        people.finalize()
        self.assertNotEqual(people.resolve(a), people.resolve(b))
        self.assertNotIn(people.resolve(bare), {people.resolve(a), people.resolve(b)})  # never a guess
        self.assertEqual(codes(people.issues), ["person_ambiguous", "same_name"])

    def test_address_known_at_another_company_is_one_person_and_an_item(self):
        people = PersonIndex()
        a = people.add("tidewaterai", "Ivo Marsh", email="ivo@tidewater.example", record="r:1")
        b = people.add("tidewater", "Ivo Marsh", email="ivo@tidewater.example", record="r:2")
        people.finalize()
        self.assertEqual(people.resolve(a), people.resolve(b))
        self.assertEqual(people.person(b).company_key, "tidewaterai")
        self.assertEqual(codes(people.issues), ["person_other_company"])

    def test_address_and_page_of_two_people_is_an_item_and_the_address_wins(self):
        people = PersonIndex()
        a = people.add(self.CO, "Ada Fenwick", email="ada@example.com", record="r:1")
        b = people.add(self.CO, "Oren Vale", linkedin="https://www.linkedin.com/in/oren-vale", record="r:2")
        c = people.add(self.CO, "Ada Fenwick", email="ada@example.com",
                       linkedin="https://www.linkedin.com/in/oren-vale", record="r:3")
        people.finalize()
        self.assertEqual(people.resolve(c), people.resolve(a))
        self.assertNotEqual(people.resolve(a), people.resolve(b))
        self.assertIsNone(people.person(a).linkedin)
        self.assertEqual(codes(people.issues), ["two_people"])

    def test_bad_address_is_dropped_with_an_item_and_blanks_quietly(self):
        people = PersonIndex()
        a = people.add(self.CO, "Ada Fenwick", email="ada at example", record="r:1")
        b = people.add(self.CO, "Oren Vale", email="n/a", linkedin="see notes", record="r:2")
        c = people.add(self.CO, "Ned Pruitt", email="unknown", linkedin="none", record="r:3")
        people.finalize()
        self.assertIsNone(people.person(a).email)
        self.assertIsNone(people.person(b).email)
        self.assertIsNone(people.person(c).linkedin)
        self.assertEqual(codes(people.issues), ["email_unparsed", "linkedin_unparsed"])

    def test_person_name_override_names_the_record(self):
        people = PersonIndex(overrides(person_name={"contacted_txt:line:9": "Emil Strand"}))
        key = people.add(self.CO, "Emil", record="contacted_txt:line:9")
        people.finalize()
        self.assertEqual(people.person(key).name, "Emil Strand")

    def test_placeholders_never_join_anyone(self):
        people = PersonIndex()
        a = people.placeholder(self.CO, "curated_touches:row:1")
        b = people.placeholder(self.CO, "curated_touches:row:2")
        people.finalize()
        self.assertNotEqual(a, b)
        self.assertTrue(people.person(a).placeholder)
        self.assertEqual(a, "kestrelrobotics/placeholder:curated_touches:row:1")

    def test_people_match_with_the_same_keys_as_add_people(self):
        from outreach.mcp.tools import people as mcp_people
        self.assertIs(mcp_people.linkedin_key, keys.linkedin_key)
        self.assertIs(mcp_people.name_key, keys.person_name_key)
        self.assertIs(mcp_people.EMAIL, keys.EMAIL)
        self.assertEqual(keys.person_name_key("  Dana   WHITFIELD "), "dana whitfield")
        self.assertEqual(keys.linkedin_key("https://uk.linkedin.com/in/Dana-W/"), "https://www.linkedin.com/in/dana-w")
        self.assertIsNone(keys.linkedin_key("linkedin.com/in/dana-w"))  # add_people stays strict
        self.assertEqual(keys.email_key(" Dana@Example.COM "), "dana@example.com")
        self.assertIsNone(keys.email_key("dana at example"))


class CarryStopsTest(unittest.TestCase):
    def touch(self, person_key, reply_kind, record):
        return TouchIn(key=f"k|{person_key}|email|2026-10-06", company_key="kestrelrobotics", person_key=person_key,
                       channel="email", sent_at="2026-10-06T16:00:00Z", posting_key=None, role_named=False,
                       role_named_known=False,
                       outcomes=(OutcomeIn("replied", "2026-10-07T16:00:00Z", reply_kind, False, record),),
                       email=None, followups_sent=0, records=(record,))

    def test_a_stop_in_any_source_stays_and_reaches_a_split_namesake(self):
        people = PersonIndex()
        a = people.add("kestrelrobotics", "Sam Lee", email="sam@example.com", record="r:1")
        b = people.add("kestrelrobotics", "Sam Lee", email="sam.lee@other.example", record="r:2")
        c = people.add("kestrelrobotics", "Ada Fenwick", email="ada@example.com", record="r:3")
        people.finalize()
        stops, issues = carry_stops(people, [self.touch(a, "asked_to_stop", "r:1"),
                                             self.touch(c, "interested", "r:3")])
        self.assertEqual(stops, {people.resolve(a): ("stop_reply", "r:1"), people.resolve(b): ("stop_reply", "r:1")})
        self.assertEqual(codes(issues), ["same_name_stopped"])
        self.assertTrue(all(isinstance(i, IssueIn) and not i.holds for i in issues))

    def test_a_stop_on_an_ambiguous_first_name_stops_every_candidate(self):
        people = PersonIndex()
        whitfield = people.add("acme", "Dana Whitfield", email="dana.w@acme.example", record="r:1")
        price = people.add("acme", "Dana Price", email="dana.p@acme.example", record="r:2")
        loose = people.add("acme", "Dana", record="contacted_txt:line:5")
        other = people.add("acme", "Ned Pruitt", email="ned@acme.example", record="r:3")
        people.finalize()
        self.assertEqual(len({people.resolve(k) for k in (whitfield, price, loose)}), 3)  # still three people
        stops, issues = carry_stops(people, [self.touch(loose, "asked_to_stop", "contacted_txt:line:5")])
        want = ("stop_reply", "contacted_txt:line:5")
        self.assertEqual(stops, {people.resolve(loose): want, people.resolve(whitfield): want,
                                 people.resolve(price): want})
        self.assertNotIn(people.resolve(other), stops)
        self.assertEqual(codes(issues), ["ambiguous_stopped", "ambiguous_stopped"])
        self.assertTrue(all("[person_name]" in i.detail and not i.holds for i in issues))

    def test_said_no_on_a_folded_mention_stops_the_person(self):
        people = PersonIndex()
        full = people.add("kestrelrobotics", "Dana Whitfield", email="dana@example.com", record="r:1")
        first = people.add("kestrelrobotics", "Dana", record="contacted_txt:line:5")
        people.finalize()
        stops, _ = carry_stops(people, [self.touch(first, "said_no", "contacted_txt:line:5")])
        self.assertEqual(stops, {people.resolve(full): ("said_no", "contacted_txt:line:5")})


if __name__ == "__main__":
    unittest.main()
