"""The launchd jobs the switch installs and retires, and the one way a template becomes a plist.

The one launchd convention every new job's template follows: the file is `launchd/<label>.plist.template`; `Label`
is the job's label; `WorkingDirectory` is `{{LIVE_CHECKOUT}}`; `ProgramArguments` starts
`{{LIVE_CHECKOUT}}/.venv/bin/python -m <module>` (an absolute path, because launchd's PATH has no `uv`);
`StandardOutPath` and `StandardErrorPath` are `{{LOG_PATH}}`. There is no other placeholder, no secret, and no
environment variable that points at the store or the Gmail folder: the defaults are the live ones.
"""
import plistlib
import re
from dataclasses import dataclass
from xml.sax.saxutils import escape


class DeployError(Exception):
    """A deploy step cannot go on. The message says why in plain words."""


@dataclass(frozen=True)
class Job:
    label: str
    template: str | None  # repo-relative template path; None for an old job, which has no template here
    plist: str  # the file name in ~/Library/LaunchAgents
    owner: str  # the issue that owns the job
    required: bool  # install refuses to run without it


def _new(label: str, owner: str, required: bool) -> Job:
    return Job(label=label, template=f"launchd/{label}.plist.template", plist=f"{label}.plist", owner=owner,
               required=required)


def _old(label: str, owner: str) -> Job:
    return Job(label=label, template=None, plist=f"{label}.plist", owner=owner, required=False)


# Required for the first switch (ruling 2026-10-24): api, sender, crawl and sources. The rest install only when
# their template exists, until #16, #32 and #15 land.
NEW_JOBS: tuple[Job, ...] = (
    _new("com.devanshu.outreach-api", "#11", True),
    _new("com.devanshu.outreach-sender", "#14", True),
    _new("com.devanshu.outreach-crawl", "#21", True),
    _new("com.devanshu.outreach-sources", "#31", True),  # the old discovery retires at S12; this replaces it
    _new("com.devanshu.outreach-needs-you", "#16", False),
    _new("com.devanshu.outreach-reader-night", "#32", False),
    _new("com.devanshu.outreach-prepare", "#15", False),
    _new("com.devanshu.outreach-plan", "#15", False),
)

OLD_JOBS: tuple[Job, ...] = (
    _old("com.devanshu.outreach-mail-sender", "old mailer"),
    _old("com.devanshu.outreach-mail-server", "old mailer"),
    _old("com.devanshu.outreach-dashboard", "old dashboard"),
    _old("com.devanshu.outreach-refresh", "old refresh"),
)

PLACEHOLDERS = ("{{LIVE_CHECKOUT}}", "{{LOG_PATH}}")
_ANY_PLACEHOLDER = re.compile(r"\{\{[^{}]*\}\}")


def render(template_text: str, *, live_root: str, log_path: str, label: str | None = None) -> str:
    """Fill the two placeholders and check the result is a plist (with `label`, when given).

    Values are filled in one pass and XML-escaped, so a path holding `&`, `<` or text that looks like a placeholder
    is written as it is and never filled again.
    """
    unknown = sorted(set(_ANY_PLACEHOLDER.findall(template_text)) - set(PLACEHOLDERS))
    if unknown:
        raise DeployError(f"the template has placeholders the deploy tool does not fill: {', '.join(unknown)}")
    values = {"{{LIVE_CHECKOUT}}": escape(live_root), "{{LOG_PATH}}": escape(log_path)}
    text = _ANY_PLACEHOLDER.sub(lambda match: values[match.group(0)], template_text)
    try:
        plist = plistlib.loads(text.encode("utf-8"))
    except Exception as err:  # plistlib raises several kinds of parse errors
        raise DeployError(f"the rendered template is not a valid plist: {err}") from None
    if not isinstance(plist, dict) or not isinstance(plist.get("Label"), str):
        raise DeployError("the rendered template has no Label")
    if label is not None and plist["Label"] != label:
        raise DeployError(f"the template's Label is {plist['Label']!r}, expected {label!r}")
    return text
