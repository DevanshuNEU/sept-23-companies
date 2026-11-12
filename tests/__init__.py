"""
Every test process starts on scratch settings, so no test can reach live contacts, the real Gmail token, or the
live store, even when the suite runs inside the live checkout during a deploy. Individual test modules may point
these at their own temp folders.
"""
import os, tempfile

for _var, _prefix in (("OUTREACH_MAIL_HOME", "outreach-mail-test-"), ("OUTREACH_GMAIL_DIR", "outreach-gmail-test-"),
                       ("OUTREACH_SOURCES_DIR", "outreach-sources-test-")):
    os.environ[_var] = tempfile.mkdtemp(prefix=_prefix)
# The drafting harness reads his DevVault voice files; no test reads the real ones (#135).
os.environ["OUTREACH_VOICE_DIR"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "devvault")
# His signature holds his phone number and lives outside the repo (#191); no test reads the real file.
os.environ["OUTREACH_SIGNATURE"] = os.path.join(tempfile.mkdtemp(prefix="outreach-signature-test-"), "none.txt")
# His stories live in DevVault; no test reads the real file (a missing one is what most tests want).
os.environ["OUTREACH_STORIES"] = os.path.join(tempfile.mkdtemp(prefix="outreach-stories-test-"), "none.txt")
os.environ["OUTREACH_DB"] = os.path.join(tempfile.mkdtemp(prefix="outreach-db-test-"), "outreach.db")
# The bridge reads the frozen contacted.txt; no test reads DevVault's. A missing file is what most tests want.
os.environ["OUTREACH_CONTACTED_TXT"] = os.path.join(tempfile.mkdtemp(prefix="outreach-contacted-test-"), "none.txt")
# No test pops a real Mac notification, even in the live checkout during a deploy test run (#153).
os.environ["OUTREACH_NO_NOTIFY"] = "1"
SCRATCH = True

# The shipped send limit is flat 200 a day (#9, 2026-11-12) and its pacing is human (#9, 2026-11-06). Most sending
# tests walk the adaptive ramp (20 up to 100, a warning steps down) with exact times, so every default `load_config()`
# in a test reads a scratch copy with the old ramp (20 to 100), the old 2 minute median gap, no start or company-gap
# jitter and first-come order.
# `SHIPPED_SEND_CONFIG` is the real file, for the tests that check what ships.
import pathlib as _pathlib
from outreach.sending import config as _send_config

SHIPPED_SEND_CONFIG = _send_config.DEFAULT_PATH
_ramp_copy = _pathlib.Path(tempfile.mkdtemp(prefix="outreach-sending-config-test-")) / "config.toml"
_ramp_text = SHIPPED_SEND_CONFIG.read_text(encoding="utf-8")
for _old, _new in (("ramp_start = 200", "ramp_start = 20"), ("ramp_floor = 200", "ramp_floor = 20"),
                   ("ramp_ceiling = 200", "ramp_ceiling = 100"), ("gap_median_minutes = 1.5", "gap_median_minutes = 2.0"),
                   ("pick_pool = 5", "pick_pool = 1"), ("day_start_jitter_minutes = 25", "day_start_jitter_minutes = 0"),
                   ("per_company_gap_jitter_minutes = 60", "per_company_gap_jitter_minutes = 0")):
    # A line that no longer matches would leave the scratch copy on the shipped value without a word: refuse it.
    assert _ramp_text.count(_old) == 1, f"tests/__init__.py: {_old!r} is not in config.toml once"
    _ramp_text = _ramp_text.replace(_old, _new)
_ramp_copy.write_text(_ramp_text, encoding="utf-8")
_send_config.DEFAULT_PATH = _ramp_copy
_send_config.load_config.__defaults__ = (_ramp_copy,)


# No test reaches the network or opens a browser. Every urllib request (urlopen and any custom opener) goes through
# OpenerDirector.open, so that is where loopback-only is enforced; tests that serve on 127.0.0.1 keep working. The
# error is a BaseException so code under test that catches Exception cannot swallow it.
import urllib.parse as _parse
import urllib.request as _request
import webbrowser as _webbrowser


class NetworkUsedInTest(BaseException):
    """A test tried to reach a host other than this machine, or to open a browser."""


_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_real_opener_open = _request.OpenerDirector.open


def _loopback_only(self, fullurl, *args, **kwargs):
    url = fullurl.full_url if isinstance(fullurl, _request.Request) else str(fullurl)
    host = (_parse.urlsplit(url).hostname or "").lower()
    if host not in _LOOPBACK:
        raise NetworkUsedInTest(f"a test tried to reach {host or url!r}; tests use fakes or 127.0.0.1 only")
    return _real_opener_open(self, fullurl, *args, **kwargs)


def _no_browser(*args, **kwargs):
    raise NetworkUsedInTest("a test tried to open a browser")


_request.OpenerDirector.open = _loopback_only
_webbrowser.open = _webbrowser.open_new = _webbrowser.open_new_tab = _no_browser
