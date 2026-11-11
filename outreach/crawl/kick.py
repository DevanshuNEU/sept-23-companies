"""Start the crawl job at once (#194, design 4.3): `launchctl kickstart`, never `-k`, so a tick that is running is
not killed (the request is picked up when that tick ends). Outside the live checkout it runs nothing."""
import os
import subprocess

from live_checkout import is_live

CRAWL_LABEL = "com.devanshu.outreach-crawl"
KICK_TIMEOUT_S = 10


def kick_crawl() -> str:
    """`"not_live"` outside the live checkout (nothing runs), else `"started"` once `launchctl kickstart` was asked.

    An error from `launchctl` (the job is not loaded, or it is already running) is not raised: the request is in the
    store, and the next crawl tick picks it up.
    """
    if not is_live():
        return "not_live"
    subprocess.run(["launchctl", "kickstart", f"gui/{os.getuid()}/{CRAWL_LABEL}"], check=False,
                   capture_output=True, timeout=KICK_TIMEOUT_S)
    return "started"
