"""Source paths (`sources.toml`) and the overrides file.

Overrides are a TOML file of his rulings about the import. Every table is optional; an unknown table, or a value of
the wrong shape, is a `ValueError`, so a typo never reads as "no override". Keys are `name_key`s or record ids
`<source>:<source_key>`.

    [same_company]            key = "other key"        these two names are one company
    [not_same_company]        pairs = [["a", "b"]]     never offered as a merge
    [person_name]             "<record id>" = "Full Name"
    [done_add]                keys = ["name key"]      company joins the done set
    [done_remove]             keys = ["name key"]      company leaves it
    [touch_for]               "<record id>" = "<record id>"   this row annotates that touch
"""
import glob
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

import live_checkout
from outreach.importer.records import SOURCES, SourceError

DEFAULT_SOURCES = Path(__file__).resolve().parent / "sources.toml"


@dataclass(frozen=True)
class Manifest:
    paths: dict[str, Path]
    overrides: Path | None


@dataclass(frozen=True)
class Overrides:
    same_company: dict[str, str]
    not_same_company: frozenset[frozenset[str]]
    person_name: dict[str, str]
    done_add: frozenset[str]
    done_remove: frozenset[str]
    touch_for: dict[str, str]


def _expand(text: str, env) -> str:
    mail_home = env.get("OUTREACH_MAIL_HOME") or os.path.join(live_checkout.LIVE_ROOT, "mail")
    text = text.replace("{live}", live_checkout.LIVE_ROOT).replace("{mail_home}", mail_home)
    return os.path.expanduser(text)


def load_manifest(path: Path | None = None, *, env=os.environ) -> Manifest:
    path = Path(path) if path else DEFAULT_SOURCES
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise SourceError(f"sources manifest: cannot read {path}: {e}") from e
    listed = data.get("paths", {})
    unknown, missing = set(listed) - set(SOURCES), set(SOURCES) - set(listed)
    if unknown or missing:
        raise SourceError(f"sources manifest {path}: unknown {sorted(unknown)}, missing {sorted(missing)}")
    paths: dict[str, Path] = {}
    for source in SOURCES:
        expanded = _expand(listed[source], env)
        if source == "local_storage":
            # Newest by name. With no match the pattern itself stays, so the reader names the missing file.
            matches = sorted(glob.glob(expanded))
            expanded = matches[-1] if matches else expanded
        paths[source] = Path(expanded)
    override = data.get("overrides", {}).get("path")
    return Manifest(paths=paths, overrides=Path(_expand(override, env)) if override else None)


_PAIR_TABLES = ("same_company", "person_name", "touch_for")
_LIST_TABLES = {"not_same_company": "pairs", "done_add": "keys", "done_remove": "keys"}


def load_overrides(path: Path | None) -> Overrides:
    if path is None or not Path(path).exists():
        return Overrides({}, frozenset(), {}, frozenset(), frozenset(), {})
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ValueError(f"overrides {path}: cannot read: {e}") from e
    for name, table in data.items():
        if name not in _PAIR_TABLES and name not in _LIST_TABLES:
            raise ValueError(f"overrides {path}: unknown table {name!r}")
        if not isinstance(table, dict):
            raise ValueError(f"overrides {path}: {name!r} must be a table")
    mappings = {}
    for name in _PAIR_TABLES:
        table = data.get(name, {})
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in table.items()):
            raise ValueError(f"overrides {path}: [{name}] values must be text")
        mappings[name] = dict(table)
    lists = {}
    for name, key in _LIST_TABLES.items():
        table = data.get(name, {})
        if set(table) - {key}:
            raise ValueError(f"overrides {path}: [{name}] takes only {key!r}")
        lists[name] = table.get(key, [])
    pairs = lists["not_same_company"]
    if not all(isinstance(p, list) and len(p) == 2 and all(isinstance(x, str) for x in p) for p in pairs):
        raise ValueError(f"overrides {path}: [not_same_company] pairs must be lists of two names")
    for name in ("done_add", "done_remove"):
        if not all(isinstance(x, str) for x in lists[name]):
            raise ValueError(f"overrides {path}: [{name}] keys must be text")
    return Overrides(
        same_company=mappings["same_company"],
        not_same_company=frozenset(frozenset(p) for p in pairs),
        person_name=mappings["person_name"],
        done_add=frozenset(lists["done_add"]),
        done_remove=frozenset(lists["done_remove"]),
        touch_for=mappings["touch_for"],
    )
