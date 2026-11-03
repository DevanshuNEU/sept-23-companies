"""decide(): the one function every posting goes through, and passes(): the posting-level hide policy.

A decision is stored with the ruleset version; the UI only shows stored decisions. decide() is pure: it reads no
store, no clock and no network, so the same posting and config always give the same decision. Every vocabulary
lives in config.toml; this file only combines what the rule modules return.
"""
from dataclasses import dataclass

from .config import Config, default_config
from .eligibility import eligibility, starts_2027
from .location import us_status
from .sponsorship import citizenship_required, sponsorship_stance
from .text import clean_text
from .title import III_BASIS, classify_title
from .years import YearsAsked, entry_wording, read_years, requires_phd


@dataclass(frozen=True)
class PostingInput:
    """What decide() reads from one posting.

    `body` is plain text: the crawler strips HTML (tags, entities, scripts and styles) before a posting is stored, and
    the rules never parse markup, so a tag left in the body is read as ordinary characters. `title` and `location` are
    plain text too, and `country` is the structured country when the source gave one, else None. decide() drops NUL
    bytes and invisible characters from all four before it reads them.
    """
    title: str
    body: str = ""
    location: str = ""
    country: str | None = None


@dataclass(frozen=True)
class Decision:
    lane: str
    level_tier: str
    level_ok: bool
    level_basis: str
    years_asked: int | None
    years_quote: str
    us: str
    sponsorship: str
    sponsorship_quote: str
    citizenship: bool
    citizenship_quote: str
    eligibility: str
    eligibility_reason: str | None
    eligibility_quote: str
    start_2027: bool
    level_state: str  # fit, reject or uncertain (#154): every builder states it
    years_ms: int | None = None  # years asked of a master's degree holder, None when the posting gives none

    def as_row(self) -> dict:
        """The `decision` table's columns (without posting_id, ruleset_version and decided_at), booleans as 0 or 1."""
        return {
            "lane": self.lane, "level_tier": self.level_tier, "level_ok": int(self.level_ok),
            "level_basis": self.level_basis, "years_asked": self.years_asked, "years_quote": self.years_quote,
            "us": self.us, "sponsorship": self.sponsorship, "sponsorship_quote": self.sponsorship_quote,
            "citizenship": int(self.citizenship), "citizenship_quote": self.citizenship_quote,
            "eligibility": self.eligibility, "eligibility_reason": self.eligibility_reason,
            "eligibility_quote": self.eligibility_quote, "start_2027": int(self.start_2027),
            "level_state": self.level_state, "years_ms": self.years_ms,
        }


def _level_ok(lane: str, tier: str | None, title_basis: str, years: int | None, entry: str, max_years: int,
              limit_years: int | None) -> tuple[str, bool, str]:
    """(tier, ok, basis): the title's tier and the body's years combined, in this order (port of
    refresh/finalcheck.py:91-100). `limit_years` is the figure the over-the-limit test reads (the general figure,
    except for a III title)."""
    if lane != "engineering":
        return tier or "not_stated", False, title_basis
    if tier == "senior":
        return "senior", False, title_basis
    if limit_years is not None and limit_years > max_years:
        return tier or "not_stated", False, f"Asks {limit_years} years"
    if tier == "new_grad":
        return "new_grad", True, title_basis
    if tier == "early_career":
        return "early_career", True, title_basis
    if entry and (years is None or years <= 1):
        return "early_career", True, "Posting says entry level"
    if years is not None and years <= 1:
        return "early_career", True, "Posting asks 0 to 1 years"
    if tier in ("swe1", "swe2"):
        return tier, True, title_basis
    if years is not None:
        return "swe2", True, f"Asks {years} years"
    if tier == "open":
        return "open", True, title_basis
    return "not_stated", False, "Posting does not state a level"


def _state(lane: str, tier: str, his_years: int | None, entry: str, max_years: int, phd: bool) -> str:
    """fit, reject or uncertain (#154). `his_years` is the master's figure when the body gives one, else the general
    figure. Reject: not tech, tier senior, a PhD required of a scientist, or more years than his band. Fit: a tier for
    new graduates (swe1 too), entry wording, or at most 2 years. Everything else is uncertain."""
    if lane != "engineering" or tier == "senior" or phd:
        return "reject"
    if his_years is not None and his_years > max_years:
        return "reject"
    if tier in ("new_grad", "early_career", "swe1") or entry or (his_years is not None and his_years <= 2):
        return "fit"
    return "uncertain"


def _level(lane: str, tier: str | None, title_basis: str, years: YearsAsked, entry: str, max_years: int,
           phd: bool = False) -> tuple[str, bool, str, str]:
    """(tier, ok, basis, state). `ok` reads the general figure exactly as before, except that a III title (tier swe2,
    "Title says III") reads the master's figure first for the over-the-limit test, as the state does."""
    his = years.ms if years.ms is not None else years.general
    limit = his if title_basis == III_BASIS else years.general
    out_tier, ok, basis = _level_ok(lane, tier, title_basis, years.general, entry, max_years, limit)
    return out_tier, ok, basis, _state(lane, out_tier, his, entry, max_years, phd)


def interim_level_state(lane: str, level_ok: bool) -> str:
    """The state of a row stored before 0007 and not yet rebuilt (stored_decision fills it): fit when level_ok, else
    uncertain for tech, else reject. decide() computes the real state."""
    if level_ok:
        return "fit"
    return "uncertain" if lane == "engineering" else "reject"


def decide(p: PostingInput, cfg: Config | None = None) -> Decision:
    cfg = cfg if cfg is not None else default_config()
    title, body, location = clean_text(p.title), clean_text(p.body), clean_text(p.location)
    country = None if p.country is None else clean_text(p.country)
    t = classify_title(title, cfg, body)
    asked = read_years(body, cfg)
    years, years_quote = asked.general, asked.quote
    phd = t.lane == "engineering" and requires_phd(title, body, cfg)
    tier, ok, basis, state = _level(t.lane, t.tier, t.basis, asked, entry_wording(body, cfg),
                                    cfg.candidate["max_years_asked"], phd)
    stance, stance_quote = sponsorship_stance(body, cfg)
    citizen, citizen_quote = citizenship_required(body, cfg)
    status, reason, elig_quote = eligibility(title, body, cfg)
    return Decision(
        lane=t.lane, level_tier=tier, level_ok=ok, level_basis=basis, years_asked=years, years_quote=years_quote,
        us=us_status(location, country, cfg, title), sponsorship=stance, sponsorship_quote=stance_quote,
        citizenship=citizen, citizenship_quote=citizen_quote, eligibility=status, eligibility_reason=reason,
        eligibility_quote=elig_quote, start_2027=starts_2027(title, body, cfg),
        level_state=state, years_ms=asked.ms,
    )


def passes(d: Decision) -> bool:
    """The posting-level half of #9's hidden list: false when the posting is hidden for its own sake.

    This is hide policy, not a classification. #6 calls it and adds the company-level half (size over 300 with
    evidence, no-go); it must not restate it. Sponsorship is a flag, never a hide (#9, 2026-11-01: he wants F-1 OPT
    and STEM OPT, not H-1B), so "no sponsorship", OPT-only wording and a failed level are not hidden here. Citizenship,
    clearance and ITAR "US person" wording are, and hide the posting only.
    """
    return d.lane == "engineering" and d.us != "no" and not d.citizenship and d.eligibility == "ok"
