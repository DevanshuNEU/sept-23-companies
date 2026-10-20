"""The ruleset version: a short hash of every file that can change a decision."""
import functools
import hashlib
from pathlib import Path

RULES_DIR = Path(__file__).resolve().parent

# The files whose bytes decide what a posting gets. config.py is in because phrases() decides how every word list
# matches. Files that do not exist yet (the rule modules land task by task) hash as empty.
RULE_FILES = ("config.toml", "config.py", "text.py", "title.py", "years.py", "location.py", "sponsorship.py",
              "eligibility.py", "facts.py", "decide.py")
# Files in this folder that cannot change a decision. A test fails on any file that is in neither tuple.
NOT_RULES = ("__init__.py", "version.py", "rebuild.py")


@functools.lru_cache(maxsize=None)  # cached per folder, so the crawler does not re-hash on every posting
def ruleset_version(rules_dir: Path = RULES_DIR) -> str:
    digest = hashlib.sha256()
    for name in sorted(RULE_FILES):
        path = Path(rules_dir) / name
        body = path.read_bytes() if path.is_file() else b""
        digest.update(name.encode() + b"\0" + body + b"\0")
    return "r-" + digest.hexdigest()[:12]
