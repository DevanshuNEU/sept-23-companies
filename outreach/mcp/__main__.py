"""`python -m outreach.mcp`: serve over stdio; `--check`: open the store, count the tools, exit;
`--print-registration`: print the command that registers this server with Claude Code in the live checkout.

The store is checked first, before stdin is read: a missing, old or newer store, or the live store outside the live
checkout, is one line on stderr and exit 1, with nothing on stdout. Printing the registration never opens the store
and never registers anything.
"""
from __future__ import annotations

import argparse
import json
import shlex
import shutil
import sys

import live_checkout
from outreach.mcp.guard import check_store
from outreach.mcp.server import build_server, tool_specs
from outreach.store import StoreError

__all__ = ["main", "registration_command"]


def registration_command(live_root: str = live_checkout.LIVE_ROOT) -> str:
    """One shell line: from the live checkout, register `outreach` for that folder only (local scope), run by the
    repo's uv from its locked environment. No OUTREACH_DB: there it is the live store, as it should be."""
    config = {
        "type": "stdio",
        "command": shutil.which("uv") or "uv",
        "args": ["run", "--directory", live_root, "--frozen", "python", "-m", "outreach.mcp"],
    }
    return (f"cd {shlex.quote(live_root)} && claude mcp add-json outreach "
            f"{shlex.quote(json.dumps(config, separators=(',', ':')))} --scope local")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m outreach.mcp", description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="open the store, count the tools and exit")
    mode.add_argument("--print-registration", action="store_true",
                      help="print the claude mcp add-json command for the live checkout")
    args = parser.parse_args(argv)

    if args.print_registration:
        print(registration_command())
        return 0
    try:
        path = check_store()
    except (StoreError, SystemExit) as err:
        print(f"outreach mcp: {err}", file=sys.stderr)
        return 1
    specs = tool_specs()
    server = build_server(path=path, specs=specs)
    if args.check:
        print(f"ok {path} {len(specs)} tools")
        return 0
    server.run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
