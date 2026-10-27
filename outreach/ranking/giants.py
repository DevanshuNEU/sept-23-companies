"""The giants list: companies far over the size cap, each under its parent (size gate, 2026-10-27).

`giants.toml` holds the names; this module loads them into `Giants`, keyed by `outreach.store.writes.name_key`, so a
company matches whatever case, punctuation or legal suffix its stored name has. The hide policy asks `Giants.match`
with every name key a company is known by. A bad file fails loudly: a missing parent or names, a name with no key, an
unknown field, or a name listed twice.
"""
from __future__ import annotations

import functools
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from outreach.store.writes import name_key

__all__ = ["GIANTS_PATH", "Giant", "Giants", "load_giants", "default_giants"]

GIANTS_PATH = Path(__file__).with_name("giants.toml")
_FIELDS = {"parent", "names"}


@dataclass(frozen=True)
class Giant:
    name: str    # as the list writes it
    parent: str  # "" when the company is large on its own

    def reason(self, over: int) -> str:
        """The hide's reason: "Over 300 people: part of Amazon (giants list)"."""
        if self.parent and name_key(self.parent) != name_key(self.name):
            return f"Over {over} people: part of {self.parent} (giants list)"
        return f"Over {over} people: {self.name} is on the giants list"


@dataclass(frozen=True)
class Giants:
    by_key: Mapping[str, Giant]

    def match(self, keys: Iterable[str]) -> Giant | None:
        """The giant any of these name keys is, else None. Keys are tried in sorted order, so the answer is stable."""
        for key in sorted(set(keys)):
            if key in self.by_key:
                return self.by_key[key]
        return None


def load_giants(path: Path = GIANTS_PATH) -> Giants:
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    unknown = set(data) - {"group"}
    if unknown:
        raise ValueError(f"{Path(path).name}: unknown table {sorted(unknown)}; only [[group]] is read")
    by_key: dict[str, Giant] = {}
    for i, group in enumerate(data.get("group", []), 1):
        extra = set(group) - _FIELDS
        if extra:
            raise ValueError(f"{Path(path).name}: group {i} has unknown fields {sorted(extra)}")
        parent, names = group.get("parent"), group.get("names")
        if not isinstance(parent, str):
            raise ValueError(f"{Path(path).name}: group {i} needs a parent (\"\" for a company large on its own)")
        if not isinstance(names, list) or not names:
            raise ValueError(f"{Path(path).name}: group {i} ({parent or 'no parent'}) needs a list of names")
        for name in names:
            key = name_key(name) if isinstance(name, str) else ""
            if not key:
                raise ValueError(f"{Path(path).name}: group {i} has a name with no letters or digits: {name!r}")
            if key in by_key:
                raise ValueError(f"{Path(path).name}: {name!r} is listed twice (key {key!r}, "
                                 f"first as {by_key[key].name!r})")
            by_key[key] = Giant(name.strip(), parent.strip())
    return Giants(by_key)


@functools.lru_cache(maxsize=1)
def default_giants() -> Giants:
    """The shipped list, loaded once."""
    return load_giants()
