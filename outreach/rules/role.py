"""The software role: whether a posting title is one of the roles the Jobs list shows (#250).

#9's job board ruling (2026-11-12) lists the roles he wants: software engineers and developers, backend, frontend,
full stack, AI and cloud engineers, and a few startup titles. Every other title is `other` and goes to the Other
roles tab. The title alone decides, through the `[role]` lists in config.toml, first match wins:

1. `out_always`: other ("Software Engineer in Test", "Software Engineer, iOS").
2. `software_core`: software ("Software Engineer, Machine Learning").
3. `ai`: software ("AI/ML Engineer").
4. `out_unless_core`: other ("Machine Learning Engineer", "Member of Technical Staff - Security").
5. `allowed`: software ("Forward Deployed Engineer", "Python Developer").
6. Anything else: other.
"""
import functools
import re

from .config import Config, phrases

ROLES = ("software", "other")
_ORDER = (("out_always", "other"), ("software_core", "software"), ("ai", "software"), ("out_unless_core", "other"),
          ("allowed", "software"))


@functools.lru_cache(maxsize=8)
def _patterns(cfg: Config) -> tuple[tuple[re.Pattern, str], ...]:
    return tuple((phrases(cfg.role[key]), role) for key, role in _ORDER)


def role_of(title: str, cfg: Config) -> str:
    """`software` or `other` for a posting title. Pure: the title and the config decide."""
    for pattern, role in _patterns(cfg):
        if pattern.search(title or ""):
            return role
    return "other"
