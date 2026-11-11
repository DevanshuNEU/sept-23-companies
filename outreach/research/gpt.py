"""Research one company with GPT and keep only the facts code can check (#205).

GPT runs through the Codex CLI with live web search. Its reply is never trusted: `check_facts` fetches each page and
keeps a fact only when its quote is there. The runner and the HTTP client are injected, so tests call neither.
"""
import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from outreach.drafts.followups import canonical_company
from outreach.net import HttpClient
from outreach.research.store import Fact, fresh_company_facts, store_company_facts
from outreach.research.verify import MAX_FACT, MAX_QUOTE, check_facts

__all__ = ["Runner", "CodexRunner", "build_prompt", "parse_reply", "research_company", "company_info"]

MAX_POSTINGS = 10
TIMEOUT_SECONDS = 600
_PROMPT = Path(__file__).with_name("prompt.md")


class Runner(Protocol):
    def run(self, prompt: str) -> str: ...


class CodexRunner:
    """`codex exec` (with `--search` only when search is on) in a fresh scratch directory; returns the text of its output file."""

    def __init__(self, timeout: int = TIMEOUT_SECONDS, search: bool = True):
        self.timeout = timeout
        self.search = search

    def run(self, prompt: str) -> str:
        codex = shutil.which("codex")
        if codex is None:
            raise RuntimeError("the codex CLI is not on PATH")
        with tempfile.TemporaryDirectory(prefix="knock-research-") as scratch:
            out = Path(scratch) / "reply.txt"
            cmd = [codex, *(["--search"] if self.search else []), "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only",
                   "-C", scratch, "-o", str(out), "-"]
            try:
                done = subprocess.run(cmd, input=prompt, text=True, capture_output=True, timeout=self.timeout)
            except subprocess.TimeoutExpired as err:
                raise RuntimeError(f"codex timed out after {self.timeout} seconds") from err
            if done.returncode != 0:
                raise RuntimeError(f"codex exited {done.returncode}")
            text = out.read_text(encoding="utf-8") if out.exists() else ""
            if not text.strip():
                raise RuntimeError("codex wrote no reply")
            return text


def build_prompt(company: dict, postings: list[str]) -> str:
    titles = "; ".join(postings[:MAX_POSTINGS]) or "none listed"
    return _PROMPT.read_text(encoding="utf-8").format(
        company=company.get("name") or "", domain=company.get("domain") or "unknown", postings=titles)


def _first_object(text: str) -> object | None:
    decoder = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text, m.start())
        except ValueError:
            continue
        return obj
    return None


def _date(value: object) -> str | None:
    if isinstance(value, str):
        try:
            return date.fromisoformat(value).isoformat() if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else None
        except ValueError:
            return None
    return None


def parse_reply(text: str) -> list[Fact]:
    """The facts in the first JSON object of `text`. Items missing fact, quote or url, with a non-https url, or with a fact or quote the table would refuse for length, are skipped."""
    obj = _first_object(text or "")
    items = obj.get("facts") if isinstance(obj, dict) else None
    if not isinstance(items, list):
        return []
    facts: list[Fact] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        fact, quote, url = (item.get(k) for k in ("fact", "quote", "url"))
        if not all(isinstance(v, str) and v.strip() for v in (fact, quote, url)):
            continue
        if urlsplit(url.strip()).scheme != "https" or len(fact.strip()) > MAX_FACT or len(quote.strip()) > MAX_QUOTE:
            continue
        facts.append(Fact(fact.strip(), quote.strip(), url.strip(), _date(item.get("date"))))
    return facts


def company_info(conn: sqlite3.Connection, company_id: int) -> tuple[dict, list[str]]:
    """The company's name and domain, and the titles of up to 10 open postings."""
    survivor = canonical_company(conn, company_id)
    row = conn.execute("SELECT name FROM company WHERE company_id = ?", (survivor,)).fetchone()
    if row is None:
        raise LookupError(f"no company {company_id}")
    dom = conn.execute("SELECT value FROM evidence WHERE company_id = ? AND fact = 'domain' "
                       "ORDER BY recorded_at DESC, evidence_id DESC LIMIT 1", (survivor,)).fetchone()
    titles = [r["title"] for r in conn.execute(
        "SELECT title FROM posting WHERE company_id = ? AND closed_at IS NULL ORDER BY first_seen_at DESC, "
        "posting_id DESC LIMIT ?", (survivor, MAX_POSTINGS))]
    return {"name": row["name"], "domain": dom["value"] if dom else None}, titles


def research_company(conn: sqlite3.Connection, company_id: int, *, runner: Runner, client: HttpClient, now: str,
                     force: bool = False) -> dict:
    """Ask GPT once, check every fact against its page, store what survives."""
    if not force and fresh_company_facts(conn, company_id, now=now):
        return {"status": "fresh", "kept": 0, "rejected": []}
    company, postings = company_info(conn, company_id)
    try:
        reply = runner.run(build_prompt(company, postings))
    except Exception as err:  # a failed run stores nothing
        return {"status": "failed", "kept": 0, "rejected": [], "error": str(err)}
    facts = parse_reply(reply)
    if not facts:
        return {"status": "failed", "kept": 0, "rejected": [], "error": "no usable facts in the reply"}
    kept, rejected = check_facts(client, facts)
    if not kept:
        return {"status": "nothing", "kept": 0, "rejected": rejected}
    written = store_company_facts(conn, company_id, kept, found_by="gpt", now=now)
    return {"status": "stored", "kept": written, "rejected": rejected}
