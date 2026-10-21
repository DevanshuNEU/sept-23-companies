"""The rules: one decide() for every posting. Everything that classifies a posting lives in this package."""
import warnings

from .config import load_config
# `outreach.rules.decide` is the function and shadows the submodule; reach the module with
# sys.modules["outreach.rules.decide"] or importlib.import_module, and patch that.
from .decide import Decision, PostingInput, decide, passes
from .version import ruleset_version

# `rebuild` the function shadows the module of the same name on this package: reach the module with
# importlib.import_module("outreach.rules.rebuild"). This import is also what makes `python -m outreach.rules.rebuild`
# find the module already loaded, so runpy would warn about it on every run; that one warning is silenced here.
warnings.filterwarnings("ignore", message=r"'outreach\.rules\.rebuild' found in sys\.modules", category=RuntimeWarning)

from .rebuild import Flip, FlipReport, RulesetNotCurrent, decide_and_store, rebuild, record_evidence  # noqa: E402

__all__ = ["PostingInput", "Decision", "decide", "passes", "load_config", "ruleset_version",
           "rebuild", "decide_and_store", "record_evidence", "RulesetNotCurrent", "Flip", "FlipReport"]
