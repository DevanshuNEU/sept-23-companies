"""The rules: one decide() for every posting. Everything that classifies a posting lives in this package."""
from .config import load_config
from .decide import Decision, PostingInput, decide, passes
from .version import ruleset_version

__all__ = ["PostingInput", "Decision", "decide", "passes", "load_config", "ruleset_version"]
