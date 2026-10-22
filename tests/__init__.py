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
