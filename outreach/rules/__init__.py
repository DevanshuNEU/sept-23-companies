"""The rules: one decide() for every posting. Everything that classifies a posting lives in this package."""
from .config import load_config
# `outreach.rules.decide` is the function and shadows the submodule; reach the module with
# sys.modules["outreach.rules.decide"] or importlib.import_module, and patch that.
from .decide import Decision, PostingInput, decide, passes
from .version import ruleset_version

__all__ = ["PostingInput", "Decision", "decide", "passes", "load_config", "ruleset_version"]
