"""Loads outreach/rules/config.toml and builds the matcher every word list goes through.

`config.py` is part of the ruleset hash (see version.py): `phrases()` decides how every word list matches, so
changing it can change a decision just as much as changing a word.
"""
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path(__file__).with_name("config.toml")

# Every section and key a loaded config must have. A missing one raises KeyError at load, naming it, so a typo in
# the TOML fails loudly instead of quietly turning a rule off.
REQUIRED = {
    "candidate": ("graduated", "max_years_asked"),
    "title": ("engineering", "intern", "off_lane", "general_application", "senior", "manager", "strong_entry",
              "new_grad", "early_career", "open_level", "level_nouns"),
    "years": ("experience_words", "preferred", "degree_alternative", "company_age", "body_entry"),
    "location": ("us_marks", "us_state_codes", "us_state_names", "us_cities", "non_us_countries", "non_us_cities",
                 "shared_city_names"),
    "sponsorship": ("topic", "negation", "hedge", "opt", "citizenship", "citizenship_negation"),
    "eligibility": ("grad_words", "current_student", "graduates_welcome", "soft", "company_history", "upper_bound",
                    "cohort_title", "start_2027", "immediate_start"),
    "facts": ("stage", "size_guess_words"),
}


@dataclass(frozen=True, eq=False)  # eq=False: the dict fields are not hashable, identity hashing is enough
class Config:
    candidate: dict
    title: dict
    years: dict
    location: dict
    sponsorship: dict
    eligibility: dict
    facts: dict
    raw: bytes  # the file bytes, stored with the ruleset row


def load_config(path: Path = CONFIG_PATH) -> Config:
    raw = Path(path).read_bytes()
    data = tomllib.loads(raw.decode("utf-8"))
    for section, keys in REQUIRED.items():
        if section not in data:
            raise KeyError(f"[{section}] is missing from {Path(path).name}")
        for key in keys:
            if key not in data[section]:
                raise KeyError(f"[{section}] {key} is missing from {Path(path).name}")
    return Config(raw=raw, **{section: data[section] for section in REQUIRED})


# A space inside a phrase matches a space, a hyphen or nothing, so "early career" finds "Early-Career" and
# "earlycareer". The run is capped so a long stretch of spaces cannot make a search slow.
_GAP = r"[\s\-]{0,3}"


def phrases(words: list[str], *, case: bool = False) -> re.Pattern:
    """One pattern for a word list: any of the words, as whole words, case-insensitive unless `case` is set."""
    if isinstance(words, str):
        raise TypeError("phrases() takes a list of words, not one string")
    parts = []
    for word in sorted(set(words), key=lambda w: (-len(w), w)):  # longest first, so "series a era" beats "series"
        pieces = word.split()
        if not pieces:
            raise ValueError(f"empty word in phrase list: {word!r}")
        parts.append(_GAP.join(re.escape(p) for p in pieces))
    if not parts:
        return re.compile(r"(?!)")  # an empty list matches nothing
    # Lookarounds instead of \b, so a word may begin or end with punctuation ("u.s.", "est.").
    return re.compile(r"(?<!\w)(?:" + "|".join(parts) + r")(?!\w)", 0 if case else re.IGNORECASE)
