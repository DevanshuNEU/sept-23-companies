"""What a tool is: a name, a short description, a strict input model, a handler and the writes it declares.

A handler is a plain function `handler(ctx, args) -> dict`. It gets a `Ctx` (a guarded connection, the clock's now
and the New York day) and the validated input model, never a path, and never opens a connection of its own. It
raises `ToolError` for a plain refusal (`not_found` and the like); `outreach.mcp.server.run_tool` turns every other
outcome into a result or a plain error.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date

from pydantic import BaseModel, ConfigDict

from outreach.mcp.guard import Grant

__all__ = ["Ctx", "Input", "ToolError", "ToolSpec"]


class Input(BaseModel):
    """The base of every tool's input: an unknown field, a wrong type or a value past a bound refuses the call.
    `strict` means no coercion: "5" is not an integer and 1 is not a string."""
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ToolError(Exception):
    """A plain refusal: `code` for the program, `message` and `fix` for the reader."""

    def __init__(self, code: str, message: str, fix: str = ""):
        super().__init__(message)
        self.code, self.message, self.fix = code, message, fix

    def payload(self) -> dict:
        return {"code": self.code, "message": self.message, "fix": self.fix}


@dataclass(frozen=True)
class Ctx:
    conn: sqlite3.Connection
    now: str      # canonical UTC
    today: date   # the New York day of `now`


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[Input]
    handler: Callable[[Ctx, Input], dict]
    grants: Mapping[str, Grant] = field(default_factory=dict)  # empty: the tool writes nothing

    @property
    def read_only(self) -> bool:
        return not self.grants
