"""One module per ranking signal (#6). Each class has a `name`, takes its `[section]` of the config and turns
`RankFacts` into an `Assessment`. `build_signals` joins them in the plan's order, the interview tilt (#81) last."""
from __future__ import annotations

from outreach.insights.similar import InterviewProfile, interview_profile
from outreach.ranking.config import RankerConfig
from outreach.ranking.signals.cap_exempt import CapExempt
from outreach.ranking.signals.everify import EVerify
from outreach.ranking.signals.funding import Funding
from outreach.ranking.signals.interview_like import InterviewLike
from outreach.ranking.signals.large_later_stage import LargeLaterStage
from outreach.ranking.signals.lca_junior import LcaJunior
from outreach.ranking.signals.live_role import LiveRole
from outreach.ranking.signals.pay_level import PayLevel
from outreach.ranking.signals.posting_sponsors import PostingSponsors
from outreach.ranking.signals.reachable_person import ReachablePerson
from outreach.ranking.signals.small import Small
from outreach.ranking.signals.staffing_risk import StaffingRisk
from outreach.ranking.types import Signal

__all__ = ["build_signals"]


def build_signals(cfg: RankerConfig, profile: InterviewProfile | None = None) -> list[Signal]:
    """The twelve signals in order. `profile` is the run's interview profile (#81); with none, the tilt has an empty
    profile and lifts nobody. A signal with no `[weights]` entry raises KeyError naming it; a weight with no signal
    raises ValueError naming it, so neither can quietly score nothing."""
    tilt = cfg.section("interview_like")
    if profile is None:
        profile = interview_profile((), confident_at=tilt["confident_at"])
    signals: list[Signal] = [
        LiveRole(cfg.section("live_role")),
        PostingSponsors(cfg.section("posting_sponsors")),
        EVerify(cfg.section("everify")),
        CapExempt(cfg.section("cap_exempt")),
        PayLevel(cfg.section("pay_level")),
        LcaJunior(cfg.section("lca_junior")),
        Small(cfg.section("small")),
        ReachablePerson(cfg.section("reachable_person")),
        Funding(cfg.section("funding"), cfg.ranker["funding_window_months"]),
        StaffingRisk(cfg.section("staffing_risk")),
        LargeLaterStage(cfg.section("small")),  # no keys of its own: it reads the stage lists in [small]
        InterviewLike(tilt, profile),  # last, so the first eleven keep their order
    ]
    for s in signals:
        if s.name not in cfg.weights:
            raise KeyError(f"[weights] has no entry for the signal {s.name!r}")
    extra = sorted(set(cfg.weights) - {s.name for s in signals})
    if extra:
        raise ValueError(f"[weights] has {', '.join(extra)} but no signal of that name")
    return signals
