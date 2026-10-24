"""The data.js reader: the old dashboard's research data, read through Node.

data.js is JavaScript (`window.COMPANIES = [...]`), so it is evaluated by `data_js_dump.mjs` in a `vm` sandbox that
defines only `window`. The file is never written; Node is the only thing that touches it.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterator

from outreach.importer.records import SourceError, SourceRecord

DUMP_SCRIPT = Path(__file__).resolve().parent / "data_js_dump.mjs"
NVM_NODE = os.path.expanduser("~/.nvm/versions/node/v20.19.5/bin/node")
NODE_TIMEOUT_S = 30
VM_TIMEOUT_MS = 5000


def _find_node(node: str | None) -> str:
    if node:
        return node
    found = shutil.which("node") or (NVM_NODE if os.path.exists(NVM_NODE) else None)
    if not found:
        raise SourceError(f"data_js: Node was not found on PATH or at {NVM_NODE}; the data.js reader needs it")
    return found


def _dump(path: Path, node: str | None) -> dict:
    path = Path(path)
    if not path.is_file():
        raise SourceError(f"data_js: no file at {path}")
    exe = _find_node(node)
    try:
        done = subprocess.run(
            [exe, str(DUMP_SCRIPT), str(path), str(VM_TIMEOUT_MS)],
            capture_output=True, text=True, encoding="utf-8", timeout=NODE_TIMEOUT_S, stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as e:
        raise SourceError(f"data_js: cannot run Node at {exe}: {e}") from e
    except PermissionError as e:
        raise SourceError(f"data_js: cannot run Node at {exe}: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise SourceError(f"data_js: reading {path} timed out after {NODE_TIMEOUT_S} seconds") from e
    if done.returncode != 0:
        raise SourceError(f"data_js: Node could not read {path} (exit {done.returncode}): {done.stderr.strip()[-2000:]}")
    try:
        data = json.loads(done.stdout)
    except json.JSONDecodeError as e:
        raise SourceError(f"data_js: Node printed something that is not JSON for {path}: {e}") from e
    return data


def read_data_js(path: Path, *, node: str | None = None) -> Iterator[SourceRecord]:
    """Records, in this order: `company:<id>` per COMPANIES entry, `ruled_out:<n>` per RULED_OUT entry (n counts
    from 1), `meta`, `reference`. A file that does not define all four, or whose companies lack a unique id, raises
    `SourceError`: a changed file never reads as zero records."""
    data = _dump(path, node)
    companies, ruled_out = data.get("COMPANIES"), data.get("RULED_OUT")
    meta, reference = data.get("META"), data.get("REFERENCE")
    if not isinstance(companies, list):
        raise SourceError(f"data_js: {path} does not define COMPANIES as a list")
    if not isinstance(ruled_out, list):
        raise SourceError(f"data_js: {path} does not define RULED_OUT as a list")
    if not isinstance(meta, dict):
        raise SourceError(f"data_js: {path} does not define META as an object")
    if not isinstance(reference, dict):
        raise SourceError(f"data_js: {path} does not define REFERENCE as an object")
    seen = set()
    for entry in companies:
        company_id = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(company_id, str) or not company_id:
            raise SourceError(f"data_js: {path} has a COMPANIES entry with no id")
        if company_id in seen:
            raise SourceError(f"data_js: {path} repeats the company id {company_id!r}")
        seen.add(company_id)
        yield SourceRecord("data_js", f"company:{company_id}", entry)
    for n, entry in enumerate(ruled_out, start=1):
        if not isinstance(entry, dict) or not isinstance(entry.get("n"), str) or not entry["n"].strip():
            raise SourceError(f"data_js: {path} has a RULED_OUT entry {n} with no name")
        yield SourceRecord("data_js", f"ruled_out:{n}", entry)
    yield SourceRecord("data_js", "meta", meta)
    yield SourceRecord("data_js", "reference", reference)
