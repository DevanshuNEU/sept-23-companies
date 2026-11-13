"""Client wording: whether a posting is a recruiter's, for an unnamed client (#257).

#9's job board ruling (2026-11-12, point 3) hides known staffing firms and flags uncertain ones. A recruiter's posting
says so in its opening ("Our client is a 15-person consumer AI company"), so only the first `client_window_chars` of
the body are read, through the `[staffing]` phrases in config.toml. Which companies are staffing firms is decided at
list time (`outreach.pipeline.joblist.staffing_ids`), not here.
"""
import functools
import re

from .config import Config, phrases


@functools.lru_cache(maxsize=8)
def _pattern(cfg: Config) -> re.Pattern:
    return phrases(cfg.staffing["client_phrases"])


def for_client(body: str, cfg: Config) -> bool:
    """True when the body's opening says the job is for a client. Pure: the body and the config decide."""
    return bool(_pattern(cfg).search((body or "")[: cfg.staffing["client_window_chars"]]))
