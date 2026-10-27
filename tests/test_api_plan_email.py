"""The email fit and the email lane on the API (2026-10-27): each company row of `GET /api/plan` says whether it can
be emailed and why, the view counts today's emailable picks, and `GET /api/plan/bench` carries the email lane (up to
`email.email_companies_per_day` companies beyond today's list, by rank). Scratch store; nothing here drafts or sends.
"""
import dataclasses
from unittest import mock

from outreach.plan.config import default_plan_config
from tests import apiseed as s
from tests.test_api_plan_picks import PicksCase


class EmailFitViewTest(PicksCase):
    def test_each_pick_says_email_or_linkedin_only_and_why(self):
        view = self.view()
        rows = {r["company"]["name"]: r["queue"]["emailFit"] for r in view["rows"]}
        self.assertEqual(rows["Aster"], {
            "verdict": "email", "label": "Email",
            "reasons": ["Website known (aster.example)", "Size not on file, no sign it is large"],
            "website": {"text": "aster.example", "note": "confirmed, its website"}})
        self.assertEqual(rows["Beacon"], {
            "verdict": "linkedin_only", "label": "LinkedIn only",
            "reasons": ["No website on file, so no address pattern"], "website": {"text": None, "note": None}})
        self.assertEqual((view["emailCount"], view["emailText"]), (1, "1 of today's 3 can be emailed"))

    def test_a_big_company_with_a_website_is_linkedin_only(self):
        s.evidence(self.conn, self.ids["Beacon"], "domain", "beacon.example")
        s.evidence(self.conn, self.ids["Beacon"], "size", "250", confidence="estimate", low=250, high=250)
        beacon = next(r for r in self.view()["rows"] if r["company"]["name"] == "Beacon")["queue"]["emailFit"]
        self.assertEqual(beacon["verdict"], "linkedin_only")
        self.assertEqual(beacon["reasons"], ["About 250 people: over 200, a cold email gets lost there"])
        self.assertEqual(beacon["website"]["text"], "beacon.example")


class EmailLaneTest(PicksCase):
    def setUp(self):
        super().setUp()
        for name in ("Dune", "Ember", "Fable", "Gale", "Iris", "Jolt"):
            s.evidence(self.conn, self.ids[name], "domain", f"{name.lower()}.example")

    def lane(self):
        b = self.bench()
        return self.names(b["emailLane"]), b["emailLaneText"], b

    def test_by_rank_without_todays_picks_or_anyone_held_back(self):
        names, text, b = self.lane()
        # Gale (Not now), Iris (a wave out) and Jolt (no-go) never come; Aster is on today's list.
        self.assertEqual(names, ["Ember", "Dune", "Fable"])
        self.assertEqual(text, "3 more companies you can email today, by rank. Find up to 2 people at each and bring "
                               "the names to your terminal.")
        first = b["emailLane"][0]
        self.assertEqual(first["queue"]["emailFit"]["verdict"], "email")
        self.assertEqual(first["position"], 1)
        # The bench is unchanged by the lane.
        self.assertEqual(self.names(b["companies"]), ["Dune", "Fable", "Ember"])

    def test_capped_at_the_config_number(self):
        cfg = default_plan_config()
        two = dataclasses.replace(cfg, email={**cfg.email, "email_companies_per_day": 2})
        with mock.patch("outreach.api.routers.plan.default_plan_config", return_value=two):
            self.assertEqual(self.lane()[0], ["Ember", "Dune"])

    def shared(self, *names):
        """Each name's domain becomes a shared key (a parent's portal): no website on file for it any more. Evidence
        is append-only, so this is how a test takes a website away."""
        for name in names:
            self.conn.execute("INSERT INTO shared_key(kind, value, reason, recorded_at) VALUES ('domain', ?, "
                              "'2 employers: A, B', '2026-11-01T00:00:00Z')", (f"{name.lower()}.example",))

    def test_a_company_without_a_website_is_not_in_the_lane(self):
        self.shared("Ember")
        self.assertEqual(self.lane()[0], ["Dune", "Fable"])

    def test_no_company_left_says_so(self):
        self.shared("Dune", "Ember", "Fable", "Gale", "Iris", "Jolt")
        names, text, _ = self.lane()
        self.assertEqual(names, [])
        self.assertTrue(text.startswith("No other company can be emailed today."))
