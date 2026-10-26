import json
import unittest
from dataclasses import replace
from datetime import date, timedelta

from outreach.plan.config import default_plan_config
from outreach.plan.people import (AddResult, Candidate, SourceInput, SourceRef, add_candidate, load_candidates,
                                  linkedin_company_id, linkedin_search_url,
                                  pick_channel, people_search_url, rank_people, tier)
from outreach.ranking.common import canonical_ids
from tests.plan_seed import TODAY, seed_person, seed_scored_company, seed_source
from tests.storetest import fresh_store

CFG = default_plan_config()
PAGE = "https://acme.example/team"


def ago(days: int) -> date:
    return TODAY - timedelta(days=days)


def ref(kind="profile", url=PAGE, observed_on=TODAY, email_basis=None) -> SourceRef:
    return SourceRef(kind=kind, url=url, label="", quote="", observed_on=observed_on, email_basis=email_basis)


def cand(person_id=1, *, role="engineer", fit="other", employer="unknown", found_on=None, name="Pat Doe", email=None,
         basis=None, post_contact=False, company_id=1) -> Candidate:
    return Candidate(person_id=person_id, company_id=company_id, name=name, title="", role_kind=role,
                     linkedin_url=None, email=email, email_basis=basis, fit=fit, employer_check=employer,
                     sources=(found_on,) if found_on else (), found_on=found_on, post_contact=post_contact,
                     draft_id=None)


class RankTest(unittest.TestCase):
    def test_tier_order(self):
        team = cand(1, role="engineer", fit="team")
        alum = cand(2, role="alum", fit="context")
        founder = cand(3, role="founder", fit="founder_small")
        generic = cand(4, role="engineer", fit="other")
        recruiter = cand(5, role="recruiter", fit="recruiter")
        self.assertEqual([tier(c) for c in (team, alum, founder, generic, recruiter)], [1, 2, 3, 4, 5])
        self.assertEqual([c.person_id for c in rank_people([recruiter, generic, founder, alum, team])],
                         [1, 2, 3, 4, 5])

    def test_ties_break_by_employer_check_then_newest_source_then_id(self):
        a = cand(7, employer="stale", found_on=ref(observed_on=ago(1)))
        b = cand(5, employer="ok", found_on=ref(observed_on=ago(20)))
        c = cand(3, employer="unknown", found_on=ref(observed_on=ago(0)))
        self.assertEqual([x.person_id for x in rank_people([a, c, b])], [5, 3, 7])
        d = cand(9, employer="ok", found_on=ref(observed_on=ago(2)))
        e = cand(8, employer="ok", found_on=ref(observed_on=ago(2)))
        f = cand(10, employer="ok", found_on=ref(observed_on=ago(1)))
        self.assertEqual([x.person_id for x in rank_people([d, e, f])], [10, 8, 9])
        none = cand(1, employer="ok", found_on=None)
        self.assertEqual([x.person_id for x in rank_people([none, d])], [9, 1])


class LoadTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.acme = seed_scored_company(self.conn, "Acme", 10.0, ["level_role"])

    def load(self, contacted=(), company_ids=None):
        return load_candidates(self.conn, company_ids or [self.acme], canonical_ids(self.conn), contacted, TODAY, CFG)

    def test_tier_fit_is_set_from_role_and_sources(self):
        eng = seed_person(self.conn, self.acme, "Eng One", "engineer")
        seed_source(self.conn, eng, "team", "https://acme.example/t")
        alum = seed_person(self.conn, self.acme, "Al Um", "alum")
        shared = seed_person(self.conn, self.acme, "Sam Shared", "engineer")
        seed_source(self.conn, shared, "context", "https://school.example/x", label="same lab")
        other = seed_person(self.conn, self.acme, "Oz Other", "other")
        fits = {c.person_id: c.fit for c in self.load()[self.acme]}
        self.assertEqual(fits, {eng: "team", alum: "context", shared: "context", other: "other"})

    def test_founder_small_needs_size_evidence(self):
        small = seed_scored_company(self.conn, "Tiny", 5.0, ["level_role"], size_high=12)
        unknown = seed_scored_company(self.conn, "Mystery", 5.0, ["level_role"])
        big = seed_scored_company(self.conn, "Big", 5.0, ["level_role"], size_high=300)
        pids = {c: seed_person(self.conn, c, f"Fo Under {c}", "founder") for c in (small, unknown, big)}
        got = self.load(company_ids=[small, unknown, big])
        self.assertEqual(got[small][0].fit, "founder_small")
        self.assertEqual(tier(got[small][0]), 3)
        self.assertEqual(got[unknown][0].fit, "other")
        self.assertEqual(tier(got[unknown][0]), 4)
        self.assertEqual(got[big][0].fit, "other")
        self.assertEqual(set(pids), {small, unknown, big})

    def test_recruiter_only_when_nobody_else_is_on_record(self):
        contacted_co = seed_scored_company(self.conn, "Hasone", 5.0, ["level_role"])
        done = seed_person(self.conn, contacted_co, "Done Already", "engineer")
        seed_person(self.conn, contacted_co, "Rec Ruiter", "recruiter")
        only_rec = seed_scored_company(self.conn, "Recs", 5.0, ["level_role"])
        rec = seed_person(self.conn, only_rec, "Only Rec", "recruiter")
        got = self.load(contacted={done}, company_ids=[contacted_co, only_rec])
        self.assertEqual(got.get(contacted_co, []), [])
        self.assertEqual([c.person_id for c in got[only_rec]], [rec])
        self.assertEqual(got[only_rec][0].fit, "recruiter")
        self.assertEqual(tier(got[only_rec][0]), 5)

    def test_contacted_and_no_go_people_are_not_candidates(self):
        a = seed_person(self.conn, self.acme, "Aa Contacted", "engineer")
        b = seed_person(self.conn, self.acme, "Bb Nogo", "engineer")
        c = seed_person(self.conn, self.acme, "Cc Free", "engineer")
        lifted = seed_person(self.conn, self.acme, "Dd Lifted", "engineer")
        self.conn.execute("INSERT INTO no_go(person_id, reason, source, created_at) VALUES (?, 'said_no', 'devanshu', "
                          "'2026-11-01T00:00:00Z')", (b,))
        self.conn.execute("INSERT INTO no_go(person_id, reason, source, created_at, lifted_at, lift_reason) "
                          "VALUES (?, 'said_no', 'devanshu', '2026-11-01T00:00:00Z', '2026-11-02T00:00:00Z', 'x')",
                          (lifted,))
        got = {x.person_id for x in self.load(contacted={a})[self.acme]}
        self.assertEqual(got, {c, lifted})

    def test_people_at_a_merged_away_duplicate_follow_the_survivor(self):
        dup = seed_scored_company(self.conn, "Acme Inc", 1.0, [], status="candidate")
        pid = seed_person(self.conn, dup, "Du Plicate", "engineer")
        self.conn.execute("UPDATE company SET merged_into = ?, status = 'removed', remove_reason = 'merged' "
                          "WHERE company_id = ?", (self.acme, dup))
        got = self.load()[self.acme]
        self.assertEqual([(c.person_id, c.company_id) for c in got], [(pid, self.acme)])

    def test_employer_check_freshness(self):
        fresh = seed_person(self.conn, self.acme, "Fr Esh", "engineer")
        seed_source(self.conn, fresh, "profile", "https://a.example/1", observed_on=ago(29))
        old = seed_person(self.conn, self.acme, "Ol D", "engineer")
        seed_source(self.conn, old, "profile", "https://a.example/2", observed_on=ago(31))
        ctx = seed_person(self.conn, self.acme, "Co Ntext", "engineer")
        seed_source(self.conn, ctx, "context", "https://a.example/3", observed_on=ago(1))
        got = {c.person_id: c.employer_check for c in self.load()[self.acme]}
        self.assertEqual(got, {fresh: "ok", old: "stale", ctx: "unknown"})

    def test_found_on_is_the_newest_profile_team_or_post_source(self):
        pid = seed_person(self.conn, self.acme, "Fo Und", "engineer")
        seed_source(self.conn, pid, "profile", "https://a.example/1", observed_on=ago(9))
        seed_source(self.conn, pid, "team", "https://a.example/2", observed_on=ago(3))
        seed_source(self.conn, pid, "context", "https://a.example/3", observed_on=ago(0))
        got = self.load()[self.acme][0]
        self.assertEqual(got.found_on.url, "https://a.example/2")
        self.assertEqual(len(got.sources), 3)

    def test_post_contact_needs_an_asks_email_post_inside_the_window(self):
        self.conn.execute("INSERT INTO source(source_name, version) VALUES ('hn', 'v1') ON CONFLICT DO NOTHING")
        posts = {"https://hn.example/asks": (1, "2026-10-20T00:00:00Z"),
                 "https://hn.example/quiet": (0, "2026-10-20T00:00:00Z"),
                 "https://hn.example/old": (1, "2026-06-01T00:00:00Z")}
        who = {}
        for url, (asks, at) in posts.items():
            self.conn.execute("INSERT INTO company_post(company_id, source_name, url, posted_at, asks_email) "
                              "VALUES (?, 'hn', ?, ?, ?)", (self.acme, url, at, asks))
            pid = seed_person(self.conn, self.acme, f"Pe {url[-5:]}", "other")
            seed_source(self.conn, pid, "post", url)
            who[url] = pid
        got = {c.person_id: c.post_contact for c in self.load()[self.acme]}
        self.assertEqual(got, {who["https://hn.example/asks"]: True, who["https://hn.example/quiet"]: False,
                               who["https://hn.example/old"]: False})

    def test_email_basis_is_the_strongest_email_source(self):
        pid = seed_person(self.conn, self.acme, "Em Ail", "founder", email="em@acme.example")
        seed_source(self.conn, pid, "email", "https://a.example/e1", email_basis="pattern", quote="em@acme.example")
        seed_source(self.conn, pid, "email", "https://a.example/e2", email_basis="verified", quote="EM@acme.example")
        seed_source(self.conn, pid, "email", "https://a.example/e3", email_basis="published", quote="someone else")
        got = self.load()[self.acme][0]
        self.assertEqual((got.email, got.email_basis), ("em@acme.example", "verified"))

    def test_the_basis_needs_the_address_as_a_whole_token_in_the_quote(self):
        """Review fix E, read side: a quote of joanna@acme.com does not back anna@acme.com."""
        cases = [("anna@acme.com", "Write to joanna@acme.com today"), ("lex@acme.com", "alex@acme.com"),
                 ("pat@acme.co", "pat@acme.com"), ("pat@acme.com", "pat@acme.com.au"),
                 ("pat@acme.com", "pat@acme.com-old"), ("pat@acme.com", "x.pat@acme.com")]
        for email, quote_text in cases:
            self.conn.execute("DELETE FROM person_source")
            self.conn.execute("DELETE FROM person")
            company = seed_scored_company(self.conn, f"Co {email} {quote_text}", 1.0, ["level_role"])
            pid = seed_person(self.conn, company, "Pat Doe", "founder", email=email)
            seed_source(self.conn, pid, "email", "https://a.example/e", email_basis="published", quote=quote_text)
            self.assertIsNone(self.load(company_ids=[company])[company][0].email_basis, (email, quote_text))
        good = [("pat@acme.com", "Email Pat@Acme.com."), ("pat@acme.com", "(pat@acme.com)"),
                ("pat@acme.com", "mail: pat@acme.com, thanks"), ("pat@acme.com", "pat@acme.com")]
        for email, quote_text in good:
            self.conn.execute("DELETE FROM person_source")
            self.conn.execute("DELETE FROM person")
            company = seed_scored_company(self.conn, f"Ok {quote_text}", 1.0, ["level_role"])
            pid = seed_person(self.conn, company, "Pat Doe", "founder", email=email)
            seed_source(self.conn, pid, "email", "https://a.example/e", email_basis="published", quote=quote_text)
            self.assertEqual(self.load(company_ids=[company])[company][0].email_basis, "published", quote_text)

    def test_the_hn_window_counts_new_york_days(self):
        """Review fix J: 2026-08-12T03:30:00Z is 11 August in New York, 91 days before today (the window is 90)."""
        self.conn.execute("INSERT INTO source(source_name, version) VALUES ('hn', 'v1') ON CONFLICT DO NOTHING")
        who = {}
        for url, at in (("https://hn.example/late", "2026-08-12T03:30:00Z"),
                        ("https://hn.example/inside", "2026-08-12T15:00:00Z")):
            self.conn.execute("INSERT INTO company_post(company_id, source_name, url, posted_at, asks_email) "
                              "VALUES (?, 'hn', ?, ?, 1)", (self.acme, url, at))
            who[url] = seed_person(self.conn, self.acme, f"Pe {url[-5:]}", "other")
            seed_source(self.conn, who[url], "post", url)
        got = {c.person_id: c.post_contact for c in self.load()[self.acme]}
        self.assertEqual(got, {who["https://hn.example/late"]: False, who["https://hn.example/inside"]: True})

    def test_draft_id_is_loaded(self):
        pid = seed_person(self.conn, self.acme, "Dr Aft", "engineer")
        did = self.conn.execute(
            "INSERT INTO linkedin_draft(person_id, body, saved_by, created_at, updated_at) "
            "VALUES (?, 'hi', 'claude', '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z')", (pid,)).lastrowid
        self.assertEqual(self.load()[self.acme][0].draft_id, did)


class ChannelTest(unittest.TestCase):
    def test_pick_channel(self):
        founder = cand(1, role="founder", name="Fay Founder", email="fay@acme.example", basis="published")
        self.assertEqual(pick_channel(founder, [], CFG), ("email", "founder_published_email"))

        eng = cand(2, role="engineer", name="Eli Eng", email="eli@acme.example", basis="published")
        sibling = cand(3, role="engineer", name="Sib Ling")
        self.assertEqual(pick_channel(eng, [sibling], CFG), ("linkedin_request", "linkedin"))

        # the HN post contact is an email row even when an engineer exists
        hn = cand(4, role="other", name="Hn Poster", email="hn@acme.example", basis="published", post_contact=True)
        self.assertEqual(pick_channel(hn, [eng, sibling], CFG), ("email", "hn_post_asks_email"))

        # a company whose every candidate is a recruiter has nobody to ask on LinkedIn
        lone = cand(5, role="recruiter", name="Lo Ne", email="lone@acme.example", basis="verified")
        self.assertEqual(pick_channel(lone, [], CFG), ("email", "no_linkedin_person"))
        recruiter = cand(6, role="recruiter", name="Rec Ruiter")
        self.assertEqual(pick_channel(lone, [recruiter], CFG), ("email", "no_linkedin_person"))

    def test_an_engineer_with_an_address_is_still_a_linkedin_row_unless_the_company_has_only_recruiters(self):
        """Review fix G: email is for a founder, an HN post that asks for email, or a company with no LinkedIn-capable
        non-recruiter. A lone engineer is that non-recruiter himself, with or without a LinkedIn URL."""
        lone = cand(1, role="engineer", name="Lo Ne", email="lone@acme.example", basis="verified")
        self.assertEqual(pick_channel(lone, [], CFG), ("linkedin_request", "linkedin"))
        with_url = replace(lone, linkedin_url="https://www.linkedin.com/in/lone")
        self.assertEqual(pick_channel(with_url, [], CFG), ("linkedin_request", "linkedin"))
        recruiter = cand(2, role="recruiter", name="Rec Ruiter")
        self.assertEqual(pick_channel(lone, [recruiter], CFG), ("linkedin_request", "linkedin"))
        manager = cand(3, role="manager", name="Ma Nager", email="ma@acme.example", basis="published")
        self.assertEqual(pick_channel(manager, [], CFG), ("linkedin_request", "linkedin"))
        alum = cand(4, role="alum", name="Al Um", email="al@acme.example", basis="published")
        self.assertEqual(pick_channel(alum, [], CFG), ("linkedin_request", "linkedin"))

    def test_a_generic_mailbox_never_makes_an_email_row_unless_the_post_asked(self):
        """Review fix F, read side: a stored address like hello@ is not chosen, but an HN post that asks for email
        and names such a mailbox is an explicit invitation."""
        for local in ("hello", "info", "jobs", "careers", "hr", "Info+x"):
            founder = cand(1, role="founder", name="Fay Founder", email=f"{local.lower()}@acme.example",
                           basis="published")
            self.assertEqual(pick_channel(founder, [], CFG), ("linkedin_request", "linkedin"), local)
        post = cand(2, role="other", name="Hn Poster", email="jobs@acme.example", basis="published",
                    post_contact=True)
        self.assertEqual(pick_channel(post, [], CFG), ("email", "hn_post_asks_email"))
        personal = cand(3, role="founder", name="Fay Founder", email="fay@acme.example", basis="published")
        self.assertEqual(pick_channel(personal, [], CFG), ("email", "founder_published_email"))

    def test_an_address_without_a_usable_basis_never_makes_an_email_row(self):
        none = cand(1, role="founder", name="Fay Founder", email="fay@acme.example", basis=None)
        self.assertEqual(pick_channel(none, [], CFG), ("linkedin_request", "linkedin"))
        no_addr = cand(2, role="founder", name="Gus Founder")
        self.assertEqual(pick_channel(no_addr, [], CFG), ("linkedin_request", "linkedin"))
        hn_no_addr = cand(3, role="other", name="Hn Poster", post_contact=True)
        self.assertEqual(pick_channel(hn_no_addr, [], CFG), ("linkedin_request", "linkedin"))

    def test_a_pattern_address_needs_a_proven_sibling_shape(self):
        guess = cand(1, role="founder", name="Ann Lee", email="ann.lee@acme.example", basis="pattern")
        self.assertEqual(pick_channel(guess, [], CFG), ("linkedin_request", "linkedin"))
        wrong = cand(2, role="engineer", name="Bo Chen", email="bchen@acme.example", basis="published")
        self.assertEqual(pick_channel(guess, [wrong], CFG), ("linkedin_request", "linkedin"))
        unproven = cand(3, role="engineer", name="Cy Diaz", email="cy.diaz@acme.example", basis="pattern")
        self.assertEqual(pick_channel(guess, [unproven], CFG), ("linkedin_request", "linkedin"))
        elsewhere = cand(4, role="engineer", name="Di Egan", email="di.egan@other.example", basis="published")
        self.assertEqual(pick_channel(guess, [elsewhere], CFG), ("linkedin_request", "linkedin"))
        proven = cand(5, role="engineer", name="Ed Frye", email="ed.frye@acme.example", basis="published")
        self.assertEqual(pick_channel(guess, [proven], CFG), ("email", "founder_published_email"))
        verified = cand(6, role="engineer", name="Flo Gray", email="fgray@acme.example", basis="verified")
        initial = cand(7, role="founder", name="Gil Hart", email="ghart@acme.example", basis="pattern")
        self.assertEqual(pick_channel(initial, [verified], CFG), ("email", "founder_published_email"))


US = "geoUrn=%5B%22103644278%22%5D"  # the United States on LinkedIn's people search
SAVED = ("https://www.linkedin.com/search/results/people/?currentCompany=%5B%221304%22%5D&geoUrn=%5B%22103644278%22%5D"
         "&origin=FACETED_SEARCH&titleFreeText=engineer")


class SearchUrlTest(unittest.TestCase):
    def test_keywords_are_the_company_name_in_the_us(self):
        self.assertEqual(people_search_url("Acme AI"),
                         f"https://www.linkedin.com/search/results/people/?keywords=Acme%20AI&{US}&origin=FACETED_SEARCH")
        self.assertIn("keywords=A%26B%3F%23&", people_search_url("A&B?#"))

    def test_a_stored_linkedin_id_filters_by_company(self):
        self.assertEqual(people_search_url("Acme AI", linkedin_id="1304"),
                         "https://www.linkedin.com/search/results/people/?currentCompany=%5B%221304%22%5D"
                         f"&{US}&origin=FACETED_SEARCH")
        for bad in ("13a4", "", "1304&x=1", "1" * 21):
            with self.subTest(bad=bad):
                self.assertIn("keywords=Acme", people_search_url("Acme", linkedin_id=bad))

    def test_his_saved_search_wins_as_he_pasted_it(self):
        self.assertEqual(people_search_url("Acme AI", saved_url=SAVED, linkedin_id="99"), SAVED)
        # a stored value that is not a LinkedIn people search is never used
        self.assertEqual(people_search_url("Acme", saved_url="https://evil.example/search/results/people/",
                                           linkedin_id="7"),
                         f"https://www.linkedin.com/search/results/people/?currentCompany=%5B%227%22%5D&{US}"
                         "&origin=FACETED_SEARCH")

    def test_what_counts_as_a_people_search(self):
        for good in (SAVED, "https://linkedin.com/search/results/people/?keywords=acme",
                     "https://www.linkedin.com/search/results/people?keywords=acme", "  " + SAVED + "\n"):
            with self.subTest(good=good):
                self.assertEqual(linkedin_search_url(good), good.strip())
        for bad in ("http://www.linkedin.com/search/results/people/?keywords=a",
                    "https://www.linkedin.com.evil.example/search/results/people/",
                    "https://user:pw@www.linkedin.com/search/results/people/",
                    "https://www.linkedin.com:8443/search/results/people/",
                    "https://www.linkedin.com/search/results/companies/?keywords=a",
                    "https://www.linkedin.com/in/someone", "https://www.linkedin.com/search/results/people/ x",
                    "https://www.linkedin.com/search/results/people/#frag", "javascript:alert(1)", "", None, 5,
                    "https://www.linkedin.com/search/results/people/?k=" + "a" * 2000):
            with self.subTest(bad=bad):
                self.assertIsNone(linkedin_search_url(bad))

    def test_the_company_id_in_a_saved_search(self):
        self.assertEqual(linkedin_company_id(SAVED), "1304")
        self.assertEqual(linkedin_company_id('https://www.linkedin.com/search/results/people/?currentCompany=["88"]'),
                         "88")
        for none in ("https://www.linkedin.com/search/results/people/?keywords=acme",
                     "https://www.linkedin.com/search/results/people/?currentCompany=%5B%221%22%2C%222%22%5D",
                     "https://www.linkedin.com/search/results/people/?currentCompany=%5B%22x1%22%5D",
                     "https://www.linkedin.com/search/results/people/?currentCompany=1304",
                     "https://evil.example/search/results/people/?currentCompany=%5B%221304%22%5D"):
            with self.subTest(none=none):
                self.assertIsNone(linkedin_company_id(none))


def count(conn, table="person") -> int:
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


class AddCandidateTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.acme = seed_scored_company(self.conn, "Acme", 10.0, ["level_role"])

    def add(self, **kw) -> AddResult:
        args = dict(company_id=self.acme, name="Pat Doe", title="Engineer", role_kind="engineer",
                    sources=[SourceInput("profile", PAGE, quote="Pat Doe, engineer")], today=TODAY)
        args.update(kw)
        return add_candidate(self.conn, **args)

    def test_add_candidate_refuses(self):
        other = seed_scored_company(self.conn, "Other", 1.0, [])
        seed_person(self.conn, other, "Th Eirs", "engineer", email="theirs@other.example",
                    linkedin_url="https://www.linkedin.com/in/theirs")
        removed = seed_scored_company(self.conn, "Gone", 1.0, [], status="removed")

        def email_source(url="https://acme.example/c", quote="pat@acme.example", basis="published"):
            return [SourceInput("email", url, quote=quote, email_basis=basis)]

        cases = [
            ("linkedin_source", dict(sources=[SourceInput("profile", "https://www.linkedin.com/in/pat")])),
            ("linkedin_source", dict(sources=[SourceInput("profile", "https://lnkd.in/abc")])),
            ("person_elsewhere", dict(linkedin_url="https://www.linkedin.com/in/theirs")),
            ("person_elsewhere", dict(email="theirs@other.example",
                                      sources=email_source(quote="theirs@other.example"))),
            ("email_needs_source", dict(email="pat@acme.example")),
            ("email_needs_source", dict(email="pat@acme.example", sources=email_source(quote="no address here"))),
            ("email_needs_source", dict(email="pat@acme.example", sources=email_source(basis=None))),
            ("email_source_not_allowed", dict(email="pat@acme.example", sources=email_source(
                url="https://github.com/acme/repo/commit/abc"))),
            ("email_source_not_allowed", dict(email="pat@acme.example", sources=email_source(
                url="https://raw.githubusercontent.com/acme/repo/x"))),
            ("email_source_not_allowed", dict(email="12345+pat@users.noreply.github.com", sources=email_source(
                quote="12345+pat@users.noreply.github.com"))),
            ("bad_linkedin_url", dict(linkedin_url="https://www.linkedin.com/company/acme")),
            ("bad_linkedin_url", dict(linkedin_url="https://example.com/in/pat")),
            ("company_removed", dict(company_id=removed)),
            ("bad_role_kind", dict(role_kind="wizard")),
            ("bad_source", dict(sources=[SourceInput("profile", "http://acme.example/team")])),
            ("bad_source", dict(sources=[SourceInput("gossip", PAGE)])),
        ]
        for expected, kw in cases:
            before = (count(self.conn), count(self.conn, "person_source"), count(self.conn, "event"))
            result = self.add(**kw)
            self.assertEqual(result, AddResult(None, False, expected), kw)
            self.assertEqual((count(self.conn), count(self.conn, "person_source"), count(self.conn, "event")),
                             before, kw)

    def test_an_address_that_only_contains_the_quoted_one_is_refused(self):
        """Review fix E, write side: the address must be on the page as a whole address."""
        def backed(email, quote_text):
            return dict(email=email, sources=[SourceInput("email", "https://acme.example/c", quote=quote_text,
                                                          email_basis="published")])
        for email, quote_text in (("anna@acme.com", "Write to joanna@acme.com"), ("lex@acme.com", "alex@acme.com"),
                                  ("pat@acme.co", "pat@acme.com"), ("pat@acme.com", "pat@acme.com.au"),
                                  ("pat@acme.com", "x_pat@acme.com")):
            before = count(self.conn)
            self.assertEqual(self.add(**backed(email, quote_text)), AddResult(None, False, "email_needs_source"),
                             (email, quote_text))
            self.assertEqual(count(self.conn), before)
        self.assertTrue(self.add(**backed("pat@acme.com", "Write to Pat@Acme.com.")).created)

    def test_a_generic_mailbox_is_refused_unless_the_page_is_an_asks_email_post(self):
        """Review fix F: hello@, info@ and the rest of people.generic_mailboxes are not a person's address."""
        for local in ("hello", "info", "jobs", "careers", "hr", "team", "support", "Info+tag", "no-reply"):
            email = f"{local.lower()}@acme.example"
            result = self.add(email=email, sources=[SourceInput(
                "email", "https://acme.example/contact", quote=f"Reach us at {email}", email_basis="published")])
            self.assertEqual(result, AddResult(None, False, "email_source_not_allowed"), local)
        self.assertEqual(count(self.conn), 0)
        self.conn.execute("INSERT INTO source(source_name, version) VALUES ('hn', 'v1') ON CONFLICT DO NOTHING")
        # The exception also needs the address in the stored post text (fix round 1, G3).
        self.conn.execute("INSERT INTO company_post(company_id, source_name, url, posted_at, text, asks_email) "
                          "VALUES (?, 'hn', 'https://hn.example/job', '2026-11-01T00:00:00Z', "
                          "'Acme is hiring, send CVs to jobs@acme.example', 1)", (self.acme,))
        invited = self.add(email="jobs@acme.example", sources=[SourceInput(
            "email", "https://hn.example/job", quote="send CVs to jobs@acme.example", email_basis="published")])
        self.assertTrue(invited.created)
        quiet = seed_scored_company(self.conn, "Quiet", 1.0, [])
        self.conn.execute("INSERT INTO company_post(company_id, source_name, url, posted_at, asks_email) "
                          "VALUES (?, 'hn', 'https://hn.example/quiet', '2026-11-01T00:00:00Z', 0)", (quiet,))
        self.assertEqual(self.add(company_id=quiet, email="jobs@quiet.example", sources=[SourceInput(
            "email", "https://hn.example/quiet", quote="jobs@quiet.example", email_basis="published")]).refused,
            "email_source_not_allowed")

    def test_a_published_email_is_stored_with_its_source_and_a_quote_that_holds_it(self):
        src = SourceInput("email", "https://acme.example/contact", quote="Write to Pat@Acme.example any time",
                          email_basis="published")
        result = self.add(email="pat@acme.example", sources=[SourceInput("profile", PAGE), src])
        self.assertTrue(result.created)
        row = self.conn.execute("SELECT email, channel, linkedin_reason FROM person WHERE person_id = ?",
                                (result.person_id,)).fetchone()
        self.assertEqual(tuple(row), ("pat@acme.example", "email", None))
        rows = self.conn.execute("SELECT kind, email_basis FROM person_source WHERE person_id = ? ORDER BY kind",
                                 (result.person_id,)).fetchall()
        self.assertEqual([tuple(r) for r in rows], [("email", "published"), ("profile", None)])

    def test_add_candidate_is_idempotent(self):
        first = self.add()
        self.assertTrue(first.created)
        again = self.add(sources=[SourceInput("profile", PAGE, quote="Pat Doe, engineer"),
                                  SourceInput("team", "https://acme.example/eng", quote="Pat on the platform team")],
                         name="pat  DOE")
        self.assertEqual((again.person_id, again.created, again.refused), (first.person_id, False, None))
        self.assertEqual(count(self.conn), 1)
        self.assertEqual(count(self.conn, "person_source"), 2)
        events = self.conn.execute("SELECT detail FROM event WHERE entity = 'person' AND kind = 'added'").fetchall()
        self.assertEqual(len(events), 1)
        self.assertEqual(json.loads(events[0][0])["sources"], [PAGE])

    def test_the_same_linkedin_url_or_email_at_the_company_returns_the_person(self):
        first = self.add(linkedin_url="https://www.linkedin.com/in/pat-doe")
        again = self.add(name="Patricia Doe", linkedin_url="https://linkedin.com/in/Pat-Doe/")
        self.assertEqual((again.person_id, again.created), (first.person_id, False))
        self.assertEqual(count(self.conn), 1)

    def test_add_candidate_normalizes_linkedin_url_and_sets_channel_hint(self):
        r = self.add(linkedin_url="https://linkedin.com/in/Pat-Doe-123/?utm=x")
        row = self.conn.execute("SELECT linkedin_url, channel, linkedin_reason, added_by, observed.d AS d "
                                "FROM person, (SELECT observed_on AS d FROM person_source LIMIT 1) observed "
                                "WHERE person_id = ?", (r.person_id,)).fetchone()
        self.assertEqual(row["linkedin_url"], "https://www.linkedin.com/in/pat-doe-123")
        self.assertEqual((row["channel"], row["linkedin_reason"], row["added_by"]), ("linkedin", "no_email", "claude"))
        self.assertEqual(row["d"], TODAY.isoformat())
        alum = self.add(name="Al Um", role_kind="alum")
        self.assertEqual(self.conn.execute("SELECT linkedin_reason FROM person WHERE person_id = ?",
                                           (alum.person_id,)).fetchone()[0], "alum_first")
        ev = self.conn.execute("SELECT actor FROM event WHERE entity = 'person' AND kind = 'added' "
                               "AND entity_id = ?", (r.person_id,)).fetchone()
        self.assertEqual(ev[0], "claude")

    def test_devanshu_adds_are_attributed_to_him(self):
        r = self.add(added_by="devanshu")
        self.assertEqual(self.conn.execute("SELECT added_by FROM person WHERE person_id = ?",
                                           (r.person_id,)).fetchone()[0], "devanshu")
        self.assertEqual(self.conn.execute("SELECT actor FROM event WHERE entity = 'person'").fetchone()[0],
                         "devanshu")

    def test_a_person_added_at_a_merged_company_lands_on_the_survivor(self):
        dup = seed_scored_company(self.conn, "Acme Inc", 1.0, [])
        self.conn.execute("UPDATE company SET merged_into = ?, status = 'removed', remove_reason = 'merged' "
                          "WHERE company_id = ?", (self.acme, dup))
        r = self.add(company_id=dup)
        self.assertTrue(r.created)
        self.assertEqual(self.conn.execute("SELECT company_id FROM person WHERE person_id = ?",
                                           (r.person_id,)).fetchone()[0], self.acme)

    def test_source_dates_default_to_today_and_can_be_given(self):
        r = self.add(sources=[SourceInput("profile", PAGE, observed_on="2026-11-01")])
        self.assertEqual(self.conn.execute("SELECT observed_on FROM person_source WHERE person_id = ?",
                                           (r.person_id,)).fetchone()[0], "2026-11-01")


if __name__ == "__main__":
    unittest.main()
