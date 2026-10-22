"""LCA classification (#31 Task 5): staffing risk, likely cap-exempt and the work tier of one employer-year.

Nothing here drops an employer. A staffing-flagged one is classified `excluded`, given risk "high", tier 3 and the
rule that caught it, and stays in the store ranked low (design 5.1): the rules are heuristics and the research found
product companies they wrongly catch (EliseAI and UWorld on the secondary-entity rule, Codapt on the body-shop
profile). Every verdict carries its reason in words, and cap-exempt is only ever "likely".

Ported from the research script's `analyze.py`; the settings live in `lca.toml`.
"""
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from outreach.sources.lca import Employer, FyStats

CONFIG_PATH = Path(__file__).with_name("lca.toml")

REQUIRED = ("max_small_filings", "staffing_name_regex", "generic_name_regex", "it_services_naics",
            "staffing_naics_prefix", "cap_exempt_naics_prefixes", "cap_exempt_strong_name_regex",
            "cap_exempt_weak_name_regex", "corporate_name_regex", "cap_exempt_override_naics_prefixes", "naics_tier_a_prefixes", "naics_tier_b_prefix")


@dataclass(frozen=True)
class LcaConfig:
    max_small_filings: int
    staffing_name_regex: re.Pattern
    generic_name_regex: re.Pattern
    it_services_naics: tuple[str, ...]
    staffing_naics_prefix: str
    cap_exempt_naics_prefixes: tuple[str, ...]
    cap_exempt_override_naics_prefixes: tuple[str, ...]
    cap_exempt_strong_name_regex: re.Pattern
    cap_exempt_weak_name_regex: re.Pattern
    corporate_name_regex: re.Pattern
    naics_tier_a_prefixes: tuple[str, ...]
    naics_tier_b_prefix: str


def load_lca_config(path: Path | None = None) -> LcaConfig:
    """Read `lca.toml` (or `path`). A missing key raises KeyError naming it, so a typo cannot turn a rule off."""
    path = Path(path) if path else CONFIG_PATH
    data = tomllib.loads(path.read_bytes().decode("utf-8"))
    for key in REQUIRED:
        if key not in data:
            raise KeyError(f"{key} is missing from {path.name}")
    rx = lambda key: re.compile(data[key], re.I)
    return LcaConfig(
        max_small_filings=int(data["max_small_filings"]),
        staffing_name_regex=rx("staffing_name_regex"), generic_name_regex=rx("generic_name_regex"),
        it_services_naics=tuple(data["it_services_naics"]), staffing_naics_prefix=data["staffing_naics_prefix"],
        cap_exempt_naics_prefixes=tuple(data["cap_exempt_naics_prefixes"]),
        cap_exempt_override_naics_prefixes=tuple(data["cap_exempt_override_naics_prefixes"]),
        cap_exempt_strong_name_regex=rx("cap_exempt_strong_name_regex"),
        cap_exempt_weak_name_regex=rx("cap_exempt_weak_name_regex"),
        corporate_name_regex=rx("corporate_name_regex"),
        naics_tier_a_prefixes=tuple(data["naics_tier_a_prefixes"]), naics_tier_b_prefix=data["naics_tier_b_prefix"])


@dataclass(frozen=True)
class Classification:
    excluded: bool
    staffing_risk: Literal["low", "medium", "high"]
    staffing_reason: str
    cap_exempt: str | None  # "likely" or None
    cap_reason: str
    naics_tier: Literal["A", "B", "C"]
    work_tier: int
    work_reason: str


def qualifies(stats: FyStats, cfg: LcaConfig) -> bool:
    """A small employer with at least one junior software filing that fiscal year."""
    return 1 <= stats.total <= cfg.max_small_filings and stats.jr >= 1


def source_fy(emp: Employer, cfg: LcaConfig) -> int | None:
    """The latest fiscal year in which the employer qualifies, or None."""
    years = [fy for fy, s in emp.fy.items() if qualifies(s, cfg)]
    return max(years) if years else None


def naics_tier(naics: str, cfg: LcaConfig) -> Literal["A", "B", "C"]:
    if naics.startswith(cfg.naics_tier_b_prefix):
        return "B"
    if naics.startswith(cfg.naics_tier_a_prefixes):
        return "A"
    return "C"


def _name_text(emp: Employer) -> str:
    return f"{emp.legal_name} {emp.key}"


def _hard_exclusion(emp: Employer, s: FyStats, cfg: LcaConfig) -> str | None:
    """The first hard rule the employer hits, in the research's order, or None."""
    name = _name_text(emp)
    if emp.naics.startswith(cfg.staffing_naics_prefix):
        return f"NAICS {cfg.staffing_naics_prefix}"
    if s.h1b_dependent:
        return "H-1B dependent"
    if s.willful:
        return "willful violator"
    if s.secondary > 0:
        return "secondary entity"
    if cfg.staffing_name_regex.search(name):
        return "staffing word in name"
    if (emp.naics.startswith(cfg.it_services_naics) and cfg.generic_name_regex.search(name)
            and s.computer_share >= 0.9 and s.jr_share_soft >= 0.8
            and (s.multi_worksite_share >= 0.5 or s.total >= 5)):
        return "body-shop profile"
    return None


def _risk_signals(emp: Employer, s: FyStats, cfg: LcaConfig) -> list[str]:
    signals = [
        (emp.naics.startswith(cfg.it_services_naics), "IT-services NAICS"),
        (bool(cfg.generic_name_regex.search(_name_text(emp))), "generic tech name"),
        (s.computer_share >= 0.9 and s.total >= 3, "90%+ computer-job filings"),
        (s.jr_share_soft >= 1 and s.newemp_share_soft >= 0.7 and s.software >= 4, "all junior with new hires"),
        (s.n_worksite_states >= 3 and s.total <= 10, "3+ worksite states on few filings"),
        (s.multi_worksite_share >= 0.5, "multi-worksite filings"),
    ]
    return [label for fired, label in signals if fired]


def _is_corporate(legal_name: str, cfg: LcaConfig) -> bool:
    """The legal name ends in a company form (Inc, LLC, Corp ...), ignoring dots, parentheses and a dba tail."""
    s = re.sub(r"\(.*?\)", " ", legal_name.lower()).replace(".", "")
    s = re.sub(r"\bd/?b/?a\b.*$", " ", s)
    s = re.sub(r"[\s,;:-]+", " ", s).strip()
    return bool(cfg.corporate_name_regex.search(s))


def _cap_exempt(emp: Employer, cfg: LcaConfig) -> tuple[str | None, str]:
    """("likely", why) or (None, why not). Only a guess from NAICS and name: nonprofit status is not in the file."""
    name = emp.legal_name
    strong = cfg.cap_exempt_strong_name_regex.search(name)
    if strong:
        return "likely", f"legal name has '{strong.group(0)}'"
    override = next((p for p in cfg.cap_exempt_override_naics_prefixes if emp.naics.startswith(p)), None)
    if override:
        if _is_corporate(name, cfg):
            return "likely", f"NAICS {emp.naics} (strong signal, overrides the corporate-name veto)"
        return "likely", f"NAICS {emp.naics} (strong signal)"
    naics = next((p for p in cfg.cap_exempt_naics_prefixes if emp.naics.startswith(p)), None)
    weak = cfg.cap_exempt_weak_name_regex.search(name)
    if not naics and not weak:
        return None, "no cap-exempt NAICS or name word"
    signal = f"NAICS {emp.naics}" if naics else f"legal name has '{weak.group(0)}'"
    if _is_corporate(name, cfg):
        return None, (f"{signal}, but the legal name is a corporation with no university, college, hospital or "
                      "institute word")
    return "likely", f"{signal}, legal name has no company form"


_MAY_BE_PRODUCT = {
    "secondary entity": "this rule can catch a real product company that places staff at customers",
    "staffing word in name": "this rule matches plain substrings (consult, talent, resourc, workforce ...) and can catch a real product company",
    "body-shop profile": "this rule can catch a real product company on a computer-services NAICS",
}


def classify(emp: Employer, fy: int, cfg: LcaConfig) -> Classification:
    """Classify the employer on that fiscal year's stats. ValueError if it has none for `fy`."""
    s = emp.fy.get(fy)
    if s is None:
        raise ValueError(f"{emp.legal_name}: no LCA stats for fiscal year {fy}")
    tier_naics = naics_tier(emp.naics, cfg)
    cap, cap_reason = _cap_exempt(emp, cfg)
    rule = _hard_exclusion(emp, s, cfg)
    if rule:
        reason = f"excluded: {rule}"
        work = f"tier 3: {reason}, stored and ranked last, not dropped"
        if rule in _MAY_BE_PRODUCT:
            work += f" ({_MAY_BE_PRODUCT[rule]})"
        return Classification(True, "high", reason, cap, cap_reason, tier_naics, 3, work)

    signals = _risk_signals(emp, s, cfg)
    risk = "low" if len(signals) <= 1 else "medium" if len(signals) == 2 else "high"
    reason = "signals: " + ", ".join(signals) if signals else "no staffing signals"

    if risk == "low" and cap is None and tier_naics in ("A", "B"):
        tier, why = 1, f"low staffing risk, not cap-exempt, NAICS tier {tier_naics}"
    elif cap and risk != "high":
        tier, why = 1, f"likely cap-exempt ({cap_reason}), staffing risk {risk}"
    else:
        tier = 2
        parts = [f"staffing risk {risk}"]
        if cap:
            parts.append(f"likely cap-exempt ({cap_reason}) but risk is high")
        if risk == "low" and cap is None:
            parts = [f"NAICS tier {tier_naics}"]
        elif tier_naics == "C" and cap is None:
            parts.append(f"NAICS tier {tier_naics}")
        why = ", ".join(parts)
    return Classification(False, risk, reason, cap, cap_reason, tier_naics, tier, f"tier {tier}: {why}")
