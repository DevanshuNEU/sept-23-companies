import json
import unittest

from outreach.api.config import load_config
from outreach.outcomes import report

CFG = load_config()
PERIOD = ("2026-10-19", "2026-10-25")   # Monday to Sunday


def ctx(*, lane="role_at_level", role_kind="swe", role_named=True, signals=None):
    return {"source": "plan", "lane": lane, "roleKind": role_kind, "roleAgeDays": 3, "rankerVersion": "v1",
            "signals": signals or {}, "roleNamed": role_named}


_ids = iter(range(1, 100000))


def touch(*, channel="linkedin_request", sent="2026-10-20T15:00:00Z", accepted=None, replied=None,
          reply_kind=None, referred=None, interview=None, offer=None, quiet=None, undone=None, context=None):
    if replied and reply_kind is None:
        reply_kind = "unsorted"
    return {"touch_id": next(_ids), "channel": channel, "sent_at": sent, "accepted_at": accepted,
            "replied_at": replied, "reply_kind": reply_kind, "referred_at": referred, "interview_at": interview,
            "offer_at": offer, "quiet_at": quiet, "undone_at": undone,
            "context": json.dumps(context or ctx(), sort_keys=True)}


def many(n, **kw):
    return [touch(**kw) for _ in range(n)]


def replied(n, **kw):
    return many(n, replied="2026-10-21T15:00:00Z", **kw)


def stage(counts, name):
    return next(c for c in counts if c.stage == name)


class RateTest(unittest.TestCase):
    def test_rates_never_divide_by_zero_and_hide_a_rate_under_the_minimum(self):
        self.assertIsNone(report.rate(0, 0, 1))
        self.assertIsNone(report.rate(5, 0, 0))
        self.assertIsNone(report.rate(3, 29, 30))      # under the minimum: hidden
        self.assertEqual(report.rate(3, 30, 30), 0.1)  # at the minimum: shown
        self.assertEqual(report.rate(0, 40, 30), 0.0)  # a real zero is a rate
        self.assertEqual(report.rate(1, 4, 1), 0.25)

    def test_the_funnel_and_base_rates_never_make_nan(self):
        counts = report.funnel([], PERIOD)
        self.assertEqual([c.stage for c in counts], ["requests", "accepts", "replies", "referrals", "interviews"])
        self.assertTrue(all(c.count == 0 for c in counts))
        base = report.base_rates([], CFG)
        self.assertEqual((base.requests, base.accept_rate, base.reply_rate, base.median_days_to_reply),
                         (0, None, None, None))


class FunnelTest(unittest.TestCase):
    def test_funnel_denominators(self):
        rows = [
            *many(10),                                                      # requests in the week
            *many(4, accepted="2026-10-21T15:00:00Z"),                      # accepts
            *many(3, replied="2026-10-22T15:00:00Z", referred="2026-10-23T15:00:00Z"),
            touch(replied="2026-10-22T15:00:00Z", interview="2026-10-24T15:00:00Z"),
        ]
        counts = report.funnel(rows, PERIOD)
        by = {c.stage: c for c in counts}
        self.assertEqual(by["requests"].count, 18)
        self.assertEqual((by["requests"].of_stage, by["requests"].of), (None, None))
        self.assertEqual((by["accepts"].count, by["accepts"].of_stage, by["accepts"].of), (4, "requests", 18))
        self.assertEqual((by["replies"].count, by["replies"].of_stage, by["replies"].of), (4, "requests", 18))
        self.assertEqual((by["referrals"].count, by["referrals"].of_stage, by["referrals"].of), (3, "replies", 4))
        self.assertEqual((by["interviews"].count, by["interviews"].of_stage, by["interviews"].of), (1, "replies", 4))

    def test_counting_is_by_the_date_of_the_event_not_the_cohort(self):
        rows = [
            touch(sent="2026-10-10T15:00:00Z", accepted="2026-10-21T15:00:00Z"),   # sent before, accepted inside
            touch(sent="2026-10-20T15:00:00Z", accepted="2026-10-28T15:00:00Z"),   # sent inside, accepted after
        ]
        by = {c.stage: c.count for c in report.funnel(rows, PERIOD)}
        self.assertEqual((by["requests"], by["accepts"]), (1, 1))

    def test_week_counts_by_new_york_time(self):
        # 2026-10-26 03:30Z is Sunday 23:30 in New York: still inside the week.
        rows = [touch(replied="2026-10-26T03:30:00Z"), touch(replied="2026-10-26T04:00:00Z")]
        self.assertEqual(stage(report.funnel(rows, PERIOD), "replies").count, 1)

    def test_only_linkedin_requests_are_requests_but_any_channel_can_reply(self):
        rows = [touch(channel="email", replied="2026-10-21T15:00:00Z"), touch(channel="application"),
                touch(channel="hn_reply"), touch()]
        by = {c.stage: c.count for c in report.funnel(rows, PERIOD)}
        self.assertEqual((by["requests"], by["replies"]), (1, 1))

    def test_a_said_no_is_still_a_reply_in_the_funnel(self):
        rows = [touch(replied="2026-10-21T15:00:00Z", reply_kind="said_no")]
        self.assertEqual(stage(report.funnel(rows, PERIOD), "replies").count, 1)

    def test_a_period_is_inclusive_of_both_ends(self):
        rows = [touch(sent="2026-10-19T04:00:00Z"), touch(sent="2026-10-26T03:59:59Z"),
                touch(sent="2026-10-19T03:59:59Z"), touch(sent="2026-10-26T04:00:00Z")]
        self.assertEqual(stage(report.funnel(rows, PERIOD), "requests").count, 2)


class BaseRatesTest(unittest.TestCase):
    def test_base_rates_median_days_to_reply(self):
        def row(days):
            return touch(sent="2026-10-01T12:00:00Z", replied=f"2026-10-{1 + days:02d}T12:00:00Z")
        odd = report.base_rates([row(1), row(2), row(4)], CFG)
        self.assertEqual(odd.median_days_to_reply, 2.0)
        even = report.base_rates([row(1), row(2), row(4), row(7)], CFG)
        self.assertEqual(even.median_days_to_reply, 3.0)
        pair = report.base_rates([row(1), row(2)], CFG)
        self.assertEqual(pair.median_days_to_reply, 1.5)
        none = report.base_rates(many(5), CFG)
        self.assertIsNone(none.median_days_to_reply)

    def test_base_rates_are_all_time_over_linkedin_requests_and_hidden_under_the_minimum(self):
        rows = [*many(20, accepted="2026-10-21T15:00:00Z"), *many(5, replied="2026-10-22T15:00:00Z"),
                *many(5, channel="email", replied="2026-10-22T15:00:00Z")]
        small = report.base_rates(rows, CFG)
        self.assertEqual((small.requests, small.accepts, small.replies), (25, 20, 5))
        self.assertIsNone(small.accept_rate)    # 25 requests is under the 30 minimum
        self.assertFalse(small.enough)
        rows += many(5)
        big = report.base_rates(rows, CFG)
        self.assertEqual(big.requests, 30)
        self.assertTrue(big.enough)
        self.assertAlmostEqual(big.accept_rate, 20 / 30)
        self.assertAlmostEqual(big.reply_rate, 5 / 30)


class QuietTest(unittest.TestCase):
    def test_quiet_means_no_accepts_or_replies_with_work_done(self):
        self.assertFalse(report.is_quiet([], PERIOD))                                   # no work: not quiet
        self.assertTrue(report.is_quiet(many(3), PERIOD))                               # requests, nothing back
        self.assertTrue(report.is_quiet([touch(channel="application")], PERIOD))        # an application is work
        self.assertFalse(report.is_quiet([*many(3), touch(accepted="2026-10-22T15:00:00Z")], PERIOD))
        self.assertFalse(report.is_quiet([*many(3), touch(replied="2026-10-22T15:00:00Z")], PERIOD))
        # work in the week, but the only reply is outside it
        self.assertTrue(report.is_quiet([*many(3), touch(sent="2026-10-01T15:00:00Z",
                                                         replied="2026-10-30T15:00:00Z")], PERIOD))

    def test_undone_work_is_not_work(self):
        self.assertFalse(report.is_quiet([touch(undone="2026-10-20T16:00:00Z")], PERIOD))


class FindingsTest(unittest.TestCase):
    def two_groups(self, n_high=20, n_low=20, high_replies=10, low_replies=4, dim=None):
        """High group: role kind 'swe'; low group: 'data'."""
        dim = dim or (lambda kind: ctx(role_kind=kind))
        rows = []
        for i in range(n_high):
            rows.append(touch(replied="2026-10-21T15:00:00Z" if i < high_replies else None,
                              context=dim("swe")))
        for i in range(n_low):
            rows.append(touch(replied="2026-10-21T15:00:00Z" if i < low_replies else None,
                              context=dim("data")))
        return rows

    def test_findings_need_20_in_each_group_and_a_two_times_gap(self):
        found = report.findings(self.two_groups(), CFG)
        self.assertEqual([f.dimension for f in found], ["role_kind"])
        f = found[0]
        self.assertEqual((f.high_group, f.low_group), ("swe", "data"))
        self.assertEqual((f.high_n, f.high_replies, f.low_n, f.low_replies), (20, 10, 20, 4))
        self.assertAlmostEqual(f.ratio, 2.5)
        # exactly twice is enough
        self.assertEqual(len(report.findings(self.two_groups(high_replies=8, low_replies=4), CFG)), 1)
        # just under twice is not
        self.assertEqual(report.findings(self.two_groups(high_replies=7, low_replies=4), CFG), [])
        # 19 in one group is not enough
        self.assertEqual(report.findings(self.two_groups(n_low=19), CFG), [])
        self.assertEqual(report.findings(self.two_groups(n_high=19), CFG), [])

    def test_no_finding_on_small_samples(self):
        rows = self.two_groups(n_high=5, n_low=5, high_replies=5, low_replies=0)
        self.assertEqual(report.findings(rows, CFG), [])
        self.assertEqual(report.findings([], CFG), [])

    def test_a_zero_reply_group_against_a_group_that_replied_is_a_finding_and_two_zeros_are_not(self):
        found = report.findings(self.two_groups(high_replies=5, low_replies=0), CFG)
        self.assertEqual(len(found), 1)
        self.assertIsNone(found[0].ratio)     # no infinity on the wire
        self.assertEqual(report.findings(self.two_groups(high_replies=0, low_replies=0), CFG), [])

    def test_findings_are_stable_across_runs(self):
        rows = self.two_groups()
        rows += [touch(channel="email", replied="2026-10-21T15:00:00Z" if i < 12 else None) for i in range(20)]
        first = report.findings(rows, CFG)
        second = report.findings(list(reversed(rows)), CFG)
        self.assertEqual([(f.id, f.key) for f in first], [(f.id, f.key) for f in second])
        self.assertEqual(first, second)
        self.assertEqual(first, report.findings(rows, CFG))
        self.assertEqual(len({f.id for f in first}), len(first))

    def test_findings_are_the_top_two_by_ratio(self):
        # Four cohorts of 20: (swe, lane a), (swe, lane b), (data, lane a), (data, lane b). Lane a is the named one.
        # role kind: 18 of 40 against 4 of 40 (4.5); lane and role named: 16 of 40 against 6 of 40 (2.67).
        rows = []
        for kind, lane, named, replies in (("swe", "a", True, 14), ("swe", "b", False, 4),
                                           ("data", "a", True, 2), ("data", "b", False, 2)):
            for i in range(20):
                rows.append(touch(replied="2026-10-21T15:00:00Z" if i < replies else None,
                                  context=ctx(role_kind=kind, lane=lane, role_named=named)))
        every = report.findings(rows, CFG, limit=None)
        self.assertEqual([f.dimension for f in every], ["role_kind", "lane", "role_named"])  # tie: by key
        self.assertEqual([f.dimension for f in report.findings(rows, CFG)], ["role_kind", "lane"])
        self.assertEqual(len(report.findings(rows, CFG, limit=1)), 1)

    def test_findings_group_by_channel_and_by_signal_bucket(self):
        # A signal whose positive bucket has 20 touches and a 2x rate beats its zero bucket.
        rows = []
        for i in range(20):
            rows.append(touch(replied="2026-10-21T15:00:00Z" if i < 8 else None,
                              context=ctx(signals={"live_role": 1.0, "e_verify": 0.0})))
        for i in range(20):
            rows.append(touch(replied="2026-10-21T15:00:00Z" if i < 3 else None,
                              context=ctx(signals={"live_role": 0.0, "e_verify": 0.0})))
        found = report.findings(rows, CFG, limit=None)
        self.assertEqual([f.dimension for f in found], ["signal:live_role"])
        self.assertEqual((found[0].high_group, found[0].low_group), ("above_zero", "zero"))

    def test_a_negative_signal_bucket_is_its_own_group(self):
        rows = []
        for i in range(20):
            rows.append(touch(replied="2026-10-21T15:00:00Z" if i < 2 else None,
                              context=ctx(signals={"staffing_risk": -1.0})))
        for i in range(20):
            rows.append(touch(replied="2026-10-21T15:00:00Z" if i < 8 else None,
                              context=ctx(signals={"staffing_risk": 0.0})))
        found = report.findings(rows, CFG)
        self.assertEqual((found[0].dimension, found[0].high_group, found[0].low_group),
                         ("signal:staffing_risk", "zero", "below_zero"))

    def test_a_touch_without_a_signal_is_in_no_bucket_for_it(self):
        rows = []
        for i in range(20):
            rows.append(touch(replied="2026-10-21T15:00:00Z" if i < 10 else None,
                              context=ctx(signals={"live_role": 1.0})))
        for i in range(30):    # plenty of touches, none with that signal: they cannot be its zero bucket
            rows.append(touch(context=ctx(signals={})))
        self.assertEqual([f for f in report.findings(rows, CFG) if f.dimension.startswith("signal:")], [])

    def test_channel_is_a_dimension(self):
        rows = [touch(channel="email", replied="2026-10-21T15:00:00Z" if i < 10 else None) for i in range(20)]
        rows += [touch(channel="linkedin_request", replied="2026-10-21T15:00:00Z" if i < 3 else None)
                 for i in range(20)]
        found = report.findings(rows, CFG)
        self.assertEqual((found[0].dimension, found[0].high_group, found[0].low_group),
                         ("channel", "email", "linkedin_request"))

    def test_lane_and_role_named_are_dimensions(self):
        found = report.findings(self.two_groups(dim=lambda k: ctx(lane=k)), CFG)
        self.assertEqual((found[0].dimension, found[0].high_group), ("lane", "swe"))
        found = report.findings(self.two_groups(dim=lambda k: ctx(role_named=(k == "swe"))), CFG)
        self.assertEqual((found[0].dimension, found[0].high_group, found[0].low_group),
                         ("role_named", "named", "not_named"))

    def test_undone_touches_are_not_counted_anywhere(self):
        undone = "2026-10-20T16:00:00Z"
        rows = [touch(undone=undone, context=ctx(role_kind="swe"))]
        # One undone touch would make group 'swe' 20 strong (a finding); it must be ignored
        rows += self.two_groups(n_high=19, n_low=20, high_replies=10, low_replies=4)
        self.assertEqual(report.findings(rows, CFG), [])
        # funnel, base rates and quiet ignore them too
        self.assertEqual(stage(report.funnel(many(5, undone=undone), PERIOD), "requests").count, 0)
        self.assertEqual(report.base_rates(many(40, undone=undone), CFG).requests, 0)
        self.assertFalse(report.is_quiet(many(5, undone=undone), PERIOD))

    def test_a_context_given_as_a_dict_or_a_string_reads_the_same(self):
        rows = self.two_groups()
        as_dict = [{**r, "context": json.loads(r["context"])} for r in rows]
        self.assertEqual(report.findings(rows, CFG), report.findings(as_dict, CFG))

    def test_a_row_with_a_null_role_kind_is_in_no_role_kind_group(self):
        rows = self.two_groups(dim=lambda k: ctx(role_kind=None))
        self.assertEqual([f for f in report.findings(rows, CFG) if f.dimension == "role_kind"], [])


if __name__ == "__main__":
    unittest.main()
