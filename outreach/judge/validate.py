"""Read and check one judge reply: the trust boundary of the judge.

The reply is model output written over public page text, so both are untrusted. `parse_reply` takes the result text of
a `claude -p --output-format json` envelope and accepts exactly one JSON object, alone or in one Markdown fence, the
way the prepare step does: prose around it, two objects, or an object quoted inside other text is refused, so a fake
judgment echoed from a page is never taken.

`check_judgment` reads only the fields of the schema and refuses (`Refused`, nothing stored) a reply for another
company, a fit that is not a whole number from 0 to 100, an empty `builds`, `why` or `risk`, a `level_ok` that is
not yes, no or unknown, more than 3 quotes, or any quote that is not word for word on a page that was read
(`quote_in_body`, at least three words, the reader's check). Unknown domain words are dropped (none left is
`other`). Every text becomes one plain line with no long dash and at most `TEXT_MAX` characters. Nothing a page or the
reply says is acted on: code sets everything else (who, when, the version, the key).
"""
from __future__ import annotations

import json
from collections.abc import Sequence

from outreach.plan.claude_run import _FENCE
from outreach.ranking.confidence import scrub_text
from outreach.ranking.judged import DOMAINS, LEVEL_OK, TEXT_MAX
from outreach.reader.night import _envelope
from outreach.reader.types import quote_in_body

__all__ = ["Refused", "MAX_QUOTES", "MAX_DOMAINS", "QUOTE_MIN_WORDS", "parse_reply", "check_judgment"]

MAX_QUOTES = 3
MAX_DOMAINS = 4
QUOTE_MIN_WORDS = 3
_QUOTE_MAX = 400


class Refused(ValueError):
    """The reply is not a judgment code can store. The message says why in plain words."""


def parse_reply(stdout: str) -> dict:
    """The judgment object from a `claude -p --output-format json` envelope. `Refused` on anything else: no
    envelope, an error result, a result that is not text, or text that is not exactly one JSON object."""
    envelope = _envelope(stdout or "")
    if envelope is None:
        raise Refused("no claude -p result object in the output")
    if envelope.get("is_error") is True:
        raise Refused("the call ended in an error")
    text = envelope["result"]
    if not isinstance(text, str):
        raise Refused("the result is not text")
    text = text.strip()
    fenced = _FENCE.fullmatch(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        reply = json.loads(text)
    except ValueError:
        raise Refused("the reply is not exactly one JSON object") from None
    if not isinstance(reply, dict):
        raise Refused("the reply is not a JSON object")
    return reply


def _line(value, name: str) -> str:
    if not isinstance(value, str) or not scrub_text(value):
        raise Refused(f"{name} is not a sentence")
    return scrub_text(value)[:TEXT_MAX]


def _level(value) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    if value in LEVEL_OK:
        return value
    raise Refused(f"level_ok must be yes, no or unknown, got {str(value)[:40]!r}")


def _domains(value) -> list[str]:
    if not isinstance(value, list):
        raise Refused("domains is not a list")
    out = []
    for d in value:
        if isinstance(d, str) and d in DOMAINS and d not in out:
            out.append(d)
    return out[:MAX_DOMAINS] or ["other"]


def check_judgment(reply: dict, *, company_id: int, pages: Sequence[dict]) -> dict:
    """The stored fields of a checked judgment: fit, builds, domains, why, risk, level_ok, quotes, evidence (each
    quote with the page it is on) and thin (no page was read, or no quote backs the judgment). `pages` are the pages
    sent to the model: `kind`, `status` and `text`."""
    if not isinstance(reply, dict):
        raise Refused("the reply is not a JSON object")
    cid = reply.get("company_id")
    if isinstance(cid, bool) or not isinstance(cid, int) or cid != company_id:
        raise Refused(f"the reply is for company {str(cid)[:20]}, not {company_id}")
    fit = reply.get("fit")
    if isinstance(fit, bool) or not isinstance(fit, int) or not 0 <= fit <= 100:
        raise Refused(f"fit must be a whole number from 0 to 100, got {str(fit)[:20]!r}")
    fields = {"fit": fit, "builds": _line(reply.get("builds"), "builds"), "domains": _domains(reply.get("domains")),
              "why": _line(reply.get("why"), "why"), "risk": _line(reply.get("risk"), "risk"),
              "level_ok": _level(reply.get("level_ok"))}
    evidence = reply.get("evidence", [])
    if evidence is None:
        evidence = []
    if not isinstance(evidence, list):
        raise Refused("evidence is not a list")
    if len(evidence) > MAX_QUOTES:
        raise Refused(f"{len(evidence)} quotes, more than {MAX_QUOTES}")
    read = [p for p in pages if p.get("status") == "ok" and isinstance(p.get("text"), str) and p["text"]]
    kept = []
    for item in evidence:
        quote = item.get("quote") if isinstance(item, dict) else item
        if not isinstance(quote, str) or not quote.strip():
            raise Refused("a quote is not text")
        named = item.get("page") if isinstance(item, dict) else None
        on = [p for p in read if p.get("kind") == named] or read
        page = next((p for p in on if quote_in_body(quote, p["text"], min_words=QUOTE_MIN_WORDS)), None)
        if page is None:
            raise Refused(f"a quote is not on any page that was read: {' '.join(quote.split())[:80]!r}")
        kept.append({"page": page.get("kind"), "url": page.get("url"), "quote": " ".join(quote.split())[:_QUOTE_MAX]})
    fields["quotes"] = [k["quote"] for k in kept]
    fields["evidence"] = kept
    fields["thin"] = not read or not kept
    return fields
