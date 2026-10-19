"""
The live checkout is the one copy of this repo that launchd runs and that holds real contacts. Anything that
uses the real Gmail token or rewrites the published dashboard checks `is_live()` first, so a worktree cannot do
either. The location is fixed on purpose: there is no setting that makes another checkout count as live.
"""
import os

LIVE_ROOT = os.path.expanduser("~/Desktop/Sept 23 Companies")
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

def is_live(root=REPO_ROOT, live_root=LIVE_ROOT):
    return os.path.realpath(root) == os.path.realpath(live_root)

def require_live(what, root=REPO_ROOT, live_root=LIVE_ROOT):
    """Exit unless `root` is the live checkout."""
    if not is_live(root, live_root):
        raise SystemExit(f"{what} only runs from the live checkout ({live_root}); this is {root}.")
