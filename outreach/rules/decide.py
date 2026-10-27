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
from .text import strip_invisible
from .title import classify_title
from .years import entry_wording, years_asked


def _clean(text: str | None) -> str:
    """The text without NUL bytes or invisible characters. SQLite's length() stops at a NUL, so a quote that holds one
    fails the decision table's CHECKs; a NUL is never part of a word, so dropping it changes no reading. An invisible
    character inside a word hides it from every word list (`strip_invisible`, #112)."""
    return strip_invisible((text or "").replace("\x00", ""))


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

    def as_row(self) -> dict:
        """The `decision` table's columns (without posting_id, ruleset_version and decided_at), booleans as 0 or 1."""
        return {
            "lane": self.lane, "level_tier": self.level_tier, "level_ok": int(self.level_ok),
            "level_basis": self.level_basis, "years_asked": self.years_asked, "years_quote": self.years_quote,
            "us": self.us, "sponsorship": self.sponsorship, "sponsorship_quote": self.sponsorship_quote,
            "citizenship": int(self.citizenship), "citizenship_quote": self.citizenship_quote,
            "eligibility": self.eligibility, "eligibility_reason": self.eligibility_reason,
            "eligibility_quote": self.eligibility_quote, "start_2027": int(self.start_2027),
        }


def _level(lane: str, tier: str | None, title_basis: str, years: int | None, entry: str,
           max_years: int) -> tuple[str, bool, str]:
    """(tier, ok, basis): the title's tier and the body's years combined, in this order (port of
    refresh/finalcheck.py:91-100)."""
    if lane != "engineering":
        return tier or "not_stated", False, title_basis
    if tier == "senior":
        return "senior", False, title_basis
    if years is not None and years > max_years:
        return tier or "not_stated", False, f"Asks {years} years"
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


def decide(p: PostingInput, cfg: Config | None = None) -> Decision:
    cfg = cfg if cfg is not None else default_config()
    title, body, location = _clean(p.title), _clean(p.body), _clean(p.location)
    country = None if p.country is None else _clean(p.country)
    t = classify_title(title, cfg)
    years, years_quote = years_asked(body, cfg)
    tier, ok, basis = _level(t.lane, t.tier, t.basis, years, entry_wording(body, cfg),
                             cfg.candidate["max_years_asked"])
    stance, stance_quote = sponsorship_stance(body, cfg)
    citizen, citizen_quote = citizenship_required(body, cfg)
    status, reason, elig_quote = eligibility(title, body, cfg)
    return Decision(
        lane=t.lane, level_tier=tier, level_ok=ok, level_basis=basis, years_asked=years, years_quote=years_quote,
        us=us_status(location, country, cfg, title), sponsorship=stance, sponsorship_quote=stance_quote,
        citizenship=citizen, citizenship_quote=citizen_quote, eligibility=status, eligibility_reason=reason,
        eligibility_quote=elig_quote, start_2027=starts_2027(title, body, cfg),
    )


def passes(d: Decision) -> bool:
    """The posting-level half of #9's hidden list: false when the posting is hidden for its own sake.

    This is hide policy, not a classification. #6 calls it and adds the company-level half (size over 300 with
    evidence, no-go); it must not restate it. OPT-only sponsorship and a failed level are not hidden here.
    """
    return (d.lane == "engineering" and d.us != "no" and d.sponsorship != "no" and not d.citizenship
            and d.eligibility == "ok")
