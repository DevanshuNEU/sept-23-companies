"""The ATS adapters and the one registry that maps an ATS name to its adapter (#21)."""
from __future__ import annotations

from collections.abc import Callable

from outreach.crawl.ats.ashby import AshbyAdapter
from outreach.crawl.ats.breezy import BreezyAdapter
from outreach.crawl.ats.greenhouse import GreenhouseAdapter
from outreach.crawl.ats.lever import LeverAdapter
from outreach.crawl.ats.oracle_orc import OracleAdapter
from outreach.crawl.ats.pinpoint import PinpointAdapter
from outreach.crawl.ats.recruitee import RecruiteeAdapter
from outreach.crawl.ats.rippling import RipplingAdapter
from outreach.crawl.ats.smartrecruiters import SmartRecruitersAdapter
from outreach.crawl.ats.teamtailor import TeamtailorAdapter
from outreach.crawl.ats.workable import WorkableAdapter
from outreach.crawl.ats.workday import WorkdayAdapter
from outreach.crawl.base import Adapter
from outreach.net import HttpClient

__all__ = ["ADAPTERS", "adapter_for", "supported_ats"]

# Each adapter task adds one line.
ADAPTERS: dict[str, Callable[[HttpClient], Adapter]] = {
    "greenhouse": GreenhouseAdapter,
    "lever": LeverAdapter,
    "ashby": AshbyAdapter,
    "smartrecruiters": SmartRecruitersAdapter,
    "workday": WorkdayAdapter,
    "workable": WorkableAdapter,
    "oracle_orc": OracleAdapter,
    "rippling": RipplingAdapter,
    "breezy": BreezyAdapter,
    "pinpoint": PinpointAdapter,
    "teamtailor": TeamtailorAdapter,
    "recruitee": RecruiteeAdapter,
}


def adapter_for(ats: str, client: HttpClient) -> Adapter | None:
    factory = ADAPTERS.get(ats)
    return factory(client) if factory else None


def supported_ats() -> frozenset[str]:
    return frozenset(ADAPTERS)
