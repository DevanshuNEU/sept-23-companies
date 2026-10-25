"""
Every test process starts on scratch settings, so no test can reach live contacts, the real Gmail token, or the
live store, even when the suite runs inside the live checkout during a deploy. Individual test modules may point
these at their own temp folders.
"""
import os, tempfile

for _var, _prefix in (("OUTREACH_MAIL_HOME", "outreach-mail-test-"), ("OUTREACH_GMAIL_DIR", "outreach-gmail-test-"),
                       ("OUTREACH_SOURCES_DIR", "outreach-sources-test-")):
    os.environ[_var] = tempfile.mkdtemp(prefix=_prefix)
os.environ["OUTREACH_DB"] = os.path.join(tempfile.mkdtemp(prefix="outreach-db-test-"), "outreach.db")
SCRATCH = True


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
