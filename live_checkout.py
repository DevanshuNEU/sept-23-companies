"""
The live checkout is the one copy of this repo that launchd runs and that holds real contacts. Anything that
sends real email or rewrites the published dashboard checks `is_live()` first, so a worktree cannot do either.

OUTREACH_LIVE_ROOT overrides the location (default: ~/Desktop/Sept 23 Companies).
"""
import os, sys

LIVE_ROOT = os.path.expanduser(os.environ.get("OUTREACH_LIVE_ROOT") or "~/Desktop/Sept 23 Companies")
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

def is_live(root=REPO_ROOT):
    return os.path.realpath(root) == os.path.realpath(LIVE_ROOT)

def require_live(what, root=REPO_ROOT, argv=None):
    """Exit unless `root` is the live checkout or the command line says --allow-non-live."""
    if is_live(root) or "--allow-non-live" in (sys.argv if argv is None else argv):
        return
    raise SystemExit(f"{what} only runs from the live checkout ({LIVE_ROOT}); this is {root}. "
                     f"Pass --allow-non-live to run it here anyway.")
