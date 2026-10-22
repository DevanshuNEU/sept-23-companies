"""The ranker version: a short hash of everything that can change a score (#6)."""
from __future__ import annotations

import hashlib
from pathlib import Path

from outreach.crawl.config import load_crawl_config
from outreach.rules.version import ruleset_version

__all__ = ["RANKING_DIR", "CRAWL_DIR", "RANKING_FILES", "NOT_RANKING", "ranker_version"]

RANKING_DIR = Path(__file__).resolve().parent
CRAWL_DIR = RANKING_DIR.parent / "crawl"

# The files whose bytes decide a score, plus every signals/*.py. Files that do not exist yet hash as empty.
# job.py is in because compute_rank lives there: how a run builds facts and scores them changes scores.
RANKING_FILES = ("config.toml", "wages.toml", "config.py", "types.py", "common.py", "pay.py", "confidence.py",
                 "roles.py", "facts.py", "hide.py", "score.py", "job.py")
# Files in this folder that cannot change a score. A test fails on any file that is in neither tuple.
NOT_RANKING = ("__init__.py", "version.py", "scores.py", "review.py", "diff.py", "__main__.py")


def ranker_version(ranking_dir: Path = RANKING_DIR, crawl_dir: Path = CRAWL_DIR) -> str:
    """`rk-` plus 12 hex over the ranking files (path, NUL, bytes, NUL), the crawler's stale.py bytes, the text
    `stale_after_months=<n>` and `ruleset=<outreach.rules ruleset_version()>` (the rules decide what is visible, so a
    new ruleset is a new score)."""
    ranking_dir, crawl_dir = Path(ranking_dir), Path(crawl_dir)
    signals = ranking_dir / "signals"
    rels = list(RANKING_FILES)
    if signals.is_dir():
        rels += [f"signals/{p.name}" for p in signals.glob("*.py")]
    digest = hashlib.sha256()
    for rel in sorted(rels):
        path = ranking_dir / rel
        body = path.read_bytes() if path.is_file() else b""
        digest.update(rel.encode() + b"\0" + body + b"\0")
    stale = crawl_dir / "stale.py"
    digest.update(stale.read_bytes() if stale.is_file() else b"")
    digest.update(f"stale_after_months={load_crawl_config().stale_after_months}".encode())
    digest.update(f"ruleset={ruleset_version()}".encode())
    return "rk-" + digest.hexdigest()[:12]
