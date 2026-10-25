import unittest

from outreach.importer.link import (
    CompanyIndex,
    interview_date,
    interview_touch,
    is_placeholder,
    join_curated,
    merge_outcome,
    origin_touch,
    touch_key,
)
from outreach.importer.manifest import Overrides
from outreach.importer.model import ApplicationIn, OutcomeIn, PersonIn, TouchIn
from outreach.importer.records import SourceRecord

CO = "kestrelrobotics"


def overrides(**kw) -> Overrides:
    base = dict(same_company={}, not_same_company=frozenset(), person_name={}, done_add=frozenset(),
                done_remove=frozenset(), touch_for={})
    base.update(kw)
    return Overrides(**base)


def person(key, role_kind):
    return PersonIn(key=key, company_key=key.split("/")[0], name=key.split("/")[1], placeholder=False, title="",
                    role_kind=role_kind, email=None, linkedin_url=None, profile_text="", tz="America/New_York",
                    records=())


def touch(person_key, sent_at, *, company=CO, channel="email", outcomes=(), record=None):
    return TouchIn(key=touch_key(company, person_key, channel, sent_at), company_key=company, person_key=person_key,
                   channel=channel, sent_at=sent_at, posting_key=None, role_named=False, role_named_known=False,
                   outcomes=tuple(outcomes), email=None, followups_sent=0,
                   records=(record or f"contacted_txt:line:{person_key}",))


def row(n, *, date="2026-10-06", company="Kestrel Robotics", role="engineer", channel="email", outcome="none",
        fu="", req=""):
    return SourceRecord("curated_touches", f"row:{n}", {
        "date": date, "company": company, "band": "A", "role": role, "channel": channel, "outcome": outcome,
        "src": "contacted", "fu": fu, "req": req})


def companies(**kw):
    idx = CompanyIndex(overrides(**kw))
    idx.add("Kestrel Robotics", "data_js:company:kestrel", base=True)
    return idx


def codes(issues):
    return sorted(i.code for i in issues)


class CuratedJoinTest(unittest.TestCase):
    def setUp(self):
        self.ada = f"{CO}/ada fenwick"
        self.people = {self.ada: person(self.ada, "engineer")}

    def test_curated_row_annotates_the_matching_touch_and_is_merged(self):
        t = touch(self.ada, "2026-10-06T13:14:00Z")
        out, issues, outcomes = join_curated([t], [row(1, outcome="call", fu="1", req="R-100")], companies(),
                                             people=self.people)
        self.assertEqual(len(out), 1)
        joined = out[0]
        self.assertEqual(joined.key, t.key)
        self.assertEqual([(o.kind, o.reply_kind, o.at, o.estimated) for o in joined.outcomes], [
            ("replied", "interested", "2026-10-06T13:14:00Z", True),
            ("interview", None, "2026-10-06T13:14:00Z", True)])
        self.assertTrue(joined.role_named and joined.role_named_known)
        self.assertEqual(joined.followups_sent, 1)
        self.assertEqual(joined.records, (t.records[0], "curated_touches:row:1"))
        self.assertEqual(outcomes["curated_touches:row:1"].outcome, "merged")
        self.assertEqual(outcomes["curated_touches:row:1"].reason, "annotation")
        self.assertEqual(outcomes["curated_touches:row:1"].entity_key, t.key)
        self.assertEqual(issues, [])

    def test_the_new_york_day_joins_a_late_evening_send(self):
        t = touch(self.ada, "2026-10-07T01:30:00Z")  # 21:30 on the 6th in New York
        out, issues, outcomes = join_curated([t], [row(1, outcome="soft")], companies(), people=self.people)
        self.assertEqual(outcomes["curated_touches:row:1"].outcome, "merged")

    def test_three_curated_rows_pair_with_three_touches_in_order(self):
        keys = [f"{CO}/r{i}" for i in (1, 2, 3)]
        people = {k: person(k, "recruiter") for k in keys}
        touches = [touch(k, "2026-10-06T16:00:00Z") for k in keys]
        rows = [row(1, role="recruiter", outcome="no"), row(2, role="recruiter", outcome="soft"),
                row(3, role="recruiter", outcome="call")]
        out, issues, outcomes = join_curated(touches, rows, companies(), people=people)
        by_key = {t.key: t for t in out}
        got = [[(o.kind, o.reply_kind) for o in by_key[t.key].outcomes] for t in touches]
        self.assertEqual(got, [[("replied", "said_no")], [("replied", "unsorted")],
                               [("replied", "interested"), ("interview", None)]])
        self.assertEqual([outcomes[f"curated_touches:row:{n}"].entity_key for n in (1, 2, 3)],
                         [t.key for t in touches])
        # pairing by order is a guess: each pair is a line he can fix with [touch_for]
        self.assertEqual(codes(issues), ["curated_by_order"] * 3)

    def test_the_row_role_picks_the_touch_and_an_unknown_role_matches_any(self):
        bram = f"{CO}/bram okafor"
        people = {**self.people, bram: person(bram, "recruiter")}
        touches = [touch(self.ada, "2026-10-06T16:00:00Z"), touch(bram, "2026-10-06T17:00:00Z")]
        out, issues, outcomes = join_curated(touches, [row(1, role="recruiter", outcome="no")], companies(),
                                             people=people)
        self.assertEqual(outcomes["curated_touches:row:1"].entity_key, touches[1].key)
        self.assertEqual(issues, [])
        # a touch whose person's role is not known matches any row role, so one touch is never split in two
        nobody = f"{CO}/emil"
        people = {nobody: person(nobody, "other")}
        out, issues, outcomes = join_curated([touch(nobody, "2026-10-06T16:00:00Z")],
                                             [row(1, role="Software Engineer", outcome="no")], companies(),
                                             people=people)
        self.assertEqual(outcomes["curated_touches:row:1"].outcome, "merged")

    def test_when_the_exact_role_is_taken_a_row_falls_back_to_a_touch_of_unknown_role(self):
        bram, emil = f"{CO}/bram okafor", f"{CO}/emil"
        people = {bram: person(bram, "recruiter"), emil: person(emil, "other")}
        touches = [touch(emil, "2026-10-06T16:00:00Z"), touch(bram, "2026-10-06T17:00:00Z")]
        out, issues, outcomes = join_curated(
            touches, [row(1, role="recruiter", outcome="no"), row(2, role="recruiter", outcome="soft")], companies(),
            people=people)
        self.assertEqual([outcomes[f"curated_touches:row:{n}"].entity_key for n in (1, 2)],
                         [touches[1].key, touches[0].key])
        self.assertEqual(len(out), 2)
        self.assertEqual(issues, [])

    def test_unmatched_curated_row_is_its_own_touch_with_an_item(self):
        t = touch(self.ada, "2026-10-06T16:00:00Z")
        out, issues, outcomes = join_curated(
            [t], [row(1, date="2026-10-08", company="Pinewood Logistics", role="engineer", outcome="bounce")],
            companies(), people=self.people)
        self.assertEqual(len(out), 2)
        new = out[1]
        self.assertEqual(new.company_key, "pinewoodlogistics")
        self.assertTrue(is_placeholder(new.person_key))
        self.assertEqual(new.channel, "email")
        self.assertEqual(new.sent_at, "2026-10-08T16:00:00Z")
        self.assertEqual([o.kind for o in new.outcomes], ["bounced"])
        self.assertEqual(new.records, ("curated_touches:row:1",))
        self.assertEqual(outcomes["curated_touches:row:1"].outcome, "imported")
        self.assertEqual(outcomes["curated_touches:row:1"].entity, "outreach_touch")
        self.assertEqual(codes(issues), ["touch_unmatched"])
        self.assertEqual(issues[0].company_key, "pinewoodlogistics")

    def test_unknown_curated_role_is_an_item(self):
        t = touch(self.ada, "2026-10-06T13:14:00Z")
        _, issues, _ = join_curated([t], [row(1, role="Software Engineer"), row(2, role="")], companies(),
                                    people=self.people)
        self.assertEqual([(i.code, i.record) for i in issues if i.code == "role_unmapped"],
                         [("role_unmapped", "curated_touches:row:1")])

    def test_unknown_req_is_not_a_known_role_named(self):
        t = touch(self.ada, "2026-10-06T16:00:00Z")
        (joined,), _, _ = join_curated([t], [row(1, req="")], companies(), people=self.people)
        self.assertEqual((joined.role_named, joined.role_named_known), (False, False))
        (joined,), _, _ = join_curated([t], [row(1, req="n")], companies(), people=self.people)
        self.assertEqual((joined.role_named, joined.role_named_known), (False, True))
        (joined,), _, _ = join_curated([t], [row(1, req="y")], companies(), people=self.people)
        self.assertEqual((joined.role_named, joined.role_named_known), (True, True))

    def test_a_row_that_cannot_be_placed_is_held(self):
        out, issues, outcomes = join_curated([], [row(1, company=""), row(2, date="sometime")], companies(),
                                             people=self.people)
        self.assertEqual(out, [])
        self.assertEqual({outcomes[f"curated_touches:row:{n}"].outcome for n in (1, 2)}, {"held"})
        self.assertTrue(all(i.holds for i in issues))
        self.assertEqual(codes(issues), ["unparsed_record", "unparsed_record"])

    def test_unknown_curated_outcome_is_an_unsorted_reply_with_an_item(self):
        t = touch(self.ada, "2026-10-06T16:00:00Z")
        (joined,), issues, _ = join_curated([t], [row(1, outcome="ghosted?")], companies(), people=self.people)
        self.assertEqual([(o.kind, o.reply_kind) for o in joined.outcomes], [("replied", "unsorted")])
        self.assertEqual(codes(issues), ["reply_text"])
        self.assertIn("ghosted?", issues[0].detail)

    def test_touch_for_override_wins_for_a_curated_row(self):
        far = touch(self.ada, "2026-09-01T16:00:00Z", record="contacted_txt:line:2")
        near = touch(f"{CO}/ned", "2026-10-06T16:00:00Z")
        out, issues, outcomes = join_curated(
            [far, near], [row(1, outcome="soft")], companies(touch_for={"curated_touches:row:1": "contacted_txt:line:2"}),
            people=self.people)
        self.assertEqual(outcomes["curated_touches:row:1"].entity_key, far.key)
        self.assertEqual(issues, [])


class MergeOutcomeTest(unittest.TestCase):
    def test_a_stop_beats_another_reply_kind_and_raises_a_conflict(self):
        said_yes = OutcomeIn("replied", "2026-10-08T16:00:00Z", "interested", False, "devvault_notes:file:a.md")
        said_no = OutcomeIn("replied", "2026-10-06T16:00:00Z", "said_no", True, "curated_touches:row:1")
        merged, conflict = merge_outcome((said_yes,), said_no)
        self.assertEqual([(o.reply_kind, o.at, o.estimated) for o in merged],
                         [("said_no", "2026-10-08T16:00:00Z", False)])  # the stop, on the known date
        self.assertIsNotNone(conflict)
        # an unsorted reply never overrides a sorted one, and is no conflict
        merged, conflict = merge_outcome((said_yes,), OutcomeIn("replied", "2026-10-06T16:00:00Z", "unsorted", True,
                                                                "r:2"))
        self.assertEqual([o.reply_kind for o in merged], ["interested"])
        self.assertIsNone(conflict)
        # two sorted non-stop kinds that differ keep the first, with a conflict
        merged, conflict = merge_outcome((said_yes,), OutcomeIn("replied", "2026-10-09T16:00:00Z", "not_now", False,
                                                                "r:3"))
        self.assertEqual([o.reply_kind for o in merged], ["interested"])
        self.assertIsNotNone(conflict)
        # another kind is added beside it
        merged, _ = merge_outcome((said_yes,), OutcomeIn("interview", "2026-10-12T16:00:00Z", None, False, "r:4"))
        self.assertEqual([o.kind for o in merged], ["replied", "interview"])


class OriginTest(unittest.TestCase):
    def reply(self, at="2026-10-08T16:00:00Z"):
        return OutcomeIn("replied", at, "interested", False, "r")

    def test_interview_goes_to_the_touch_that_got_a_reply(self):
        quiet = touch(f"{CO}/a", "2026-10-01T16:00:00Z")
        answered = touch(f"{CO}/b", "2026-10-02T16:00:00Z", outcomes=[self.reply()])
        later = touch(f"{CO}/c", "2026-10-03T16:00:00Z")
        elsewhere = touch("lumenfreight/d", "2026-10-02T16:00:00Z", company="lumenfreight",
                          outcomes=[self.reply()])
        self.assertEqual(origin_touch(CO, "2026-10-12", [quiet, answered, later, elsewhere], []),
                         (answered.key, ()))

    def test_the_only_touch_before_or_the_latest_with_an_item(self):
        only = touch(f"{CO}/a", "2026-10-01T16:00:00Z")
        after = touch(f"{CO}/b", "2026-10-20T16:00:00Z")
        self.assertEqual(origin_touch(CO, "2026-10-12", [only, after], []), (only.key, ()))
        second = touch(f"{CO}/c", "2026-10-05T16:00:00Z")
        self.assertEqual(origin_touch(CO, "2026-10-12", [only, second, after], []), (second.key, ("interview_origin",)))
        # two touches that got a reply: the latest, with an item
        r1 = touch(f"{CO}/a", "2026-10-01T16:00:00Z", outcomes=[self.reply()])
        r2 = touch(f"{CO}/c", "2026-10-05T16:00:00Z", outcomes=[self.reply()])
        self.assertEqual(origin_touch(CO, "2026-10-12", [r1, r2], []), (r2.key, ("interview_origin",)))

    def test_interview_falls_back_to_the_application_touch(self):
        apps = [ApplicationIn(key=f"{CO}|app|1", company_key=CO, posting_key=None, applied_on="2026-09-20", url="",
                              outcome="open", outcome_at=None, records=("radar_db:app_event:1",)),
                ApplicationIn(key=f"{CO}|app|2", company_key=CO, posting_key=None, applied_on="2026-10-01", url="",
                              outcome="open", outcome_at=None, records=("radar_db:app_event:2",)),
                ApplicationIn(key=f"{CO}|app|3", company_key=CO, posting_key=None, applied_on="2026-10-20", url="",
                              outcome="open", outcome_at=None, records=("radar_db:app_event:3",))]
        after = touch(f"{CO}/b", "2026-10-20T16:00:00Z")
        self.assertEqual(origin_touch(CO, "2026-10-12", [after], apps), (f"{CO}|app|2", ()))

    def test_interview_without_touch_gets_an_other_touch_and_an_item(self):
        self.assertEqual(origin_touch(CO, "2026-10-12", [], []), (None, ("interview_origin",)))
        made = interview_touch(CO, "2026-10-12", "interview_prep:folder:kestrel-robotics")
        self.assertEqual(made.channel, "other")
        self.assertEqual(made.company_key, CO)
        self.assertTrue(is_placeholder(made.person_key))
        self.assertEqual(made.sent_at, "2026-10-12T16:00:00Z")
        self.assertEqual(made.records, ("interview_prep:folder:kestrel-robotics",))
        self.assertEqual(made.key, touch_key(CO, made.person_key, "other", made.sent_at))

    def test_touch_for_override_wins(self):
        answered = touch(f"{CO}/b", "2026-10-02T16:00:00Z", outcomes=[self.reply()])
        cold = touch(f"{CO}/c", "2026-10-03T16:00:00Z", record="local_storage:company:kestrel")
        rid = "interview_prep:folder:kestrel-robotics"
        by_key = overrides(touch_for={rid: cold.key})
        by_record = overrides(touch_for={rid: "local_storage:company:kestrel"})
        for ov in (by_key, by_record):
            self.assertEqual(origin_touch(CO, "2026-10-12", [answered, cold], [], record=rid, overrides=ov),
                             (cold.key, ()))
        unknown = overrides(touch_for={rid: "contacted_txt:line:999"})
        self.assertEqual(origin_touch(CO, "2026-10-12", [answered, cold], [], record=rid, overrides=unknown),
                         (answered.key, ("touch_for_unknown", "interview_origin")))
        self.assertEqual(origin_touch(CO, "2026-10-12", [], [], record=rid, overrides=unknown),
                         (None, ("touch_for_unknown", "interview_origin")))

    def test_interview_date_prefers_sessions_then_verified_on(self):
        both = SourceRecord("interview_prep", "folder:k", {
            "slug": "k", "brief": {"verified_on": "2026-10-11"}, "session_dates": ["2026-10-14", "2026-10-12"],
            "has_brief": True})
        brief_only = SourceRecord("interview_prep", "folder:k", {
            "slug": "k", "brief": {"verified_on": "2026-10-11"}, "session_dates": [], "has_brief": True})
        neither = SourceRecord("interview_prep", "folder:e", {
            "slug": "e", "brief": None, "session_dates": [], "has_brief": True})
        bad = SourceRecord("interview_prep", "folder:e", {
            "slug": "e", "brief": {"verified_on": "soon"}, "session_dates": [], "has_brief": True})
        self.assertEqual(interview_date(both), ("2026-10-12", False))
        self.assertEqual(interview_date(brief_only), ("2026-10-11", False))
        self.assertEqual(interview_date(neither, origin_at="2026-10-07T01:30:00Z"), ("2026-10-06", True))
        self.assertEqual(interview_date(neither), (None, True))
        self.assertEqual(interview_date(bad), (None, True))


if __name__ == "__main__":
    unittest.main()
