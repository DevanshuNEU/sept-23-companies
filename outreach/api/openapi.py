"""The OpenAPI snapshot: `web/openapi.json`, from which `pnpm gen:api` writes `web/src/api/schema.ts`.

    uv run python -m outreach.api.openapi --write    rewrite web/openapi.json
    uv run python -m outreach.api.openapi --check    exit 1 when it differs from the app
"""
import argparse
import copy
import json
import sys
from pathlib import Path

import live_checkout
from fastapi import FastAPI

from outreach.api.errors import ErrorEnvelope
from outreach.api.settings import Settings

OPENAPI_PATH = Path(live_checkout.REPO_ROOT) / "web" / "openapi.json"
REGENERATE = "uv run python -m outreach.api.openapi --write && cd web && pnpm gen:api"

# Documented on every operation. Reads cannot conflict or be blocked, so they list fewer.
ERROR_RESPONSES: dict[int | str, dict] = {
    400: {"model": ErrorEnvelope, "description": "bad_request: the request was not valid"},
    403: {"model": ErrorEnvelope, "description": "forbidden: not one of this app's own pages"},
    404: {"model": ErrorEnvelope, "description": "not_found: no such record"},
    409: {"model": ErrorEnvelope, "description": "conflict, capacity or not_allowed: the write changed nothing"},
    422: {"model": ErrorEnvelope, "description": "blocked or idempotency_mismatch: the write changed nothing"},
    500: {"model": ErrorEnvelope, "description": "server_error: nothing was saved"},
    503: {"model": ErrorEnvelope, "description": "busy: the store is in use, nothing was saved, try again"},
}
READ_METHODS = {"get", "head", "options"}
FRAMEWORK_SCHEMAS = ("HTTPValidationError", "ValidationError")


def refine(schema: dict) -> dict:
    """Drop what does not apply: conflict and blocked on reads, and FastAPI's own validation-error shapes."""
    for item in schema.get("paths", {}).values():
        for method, operation in item.items():
            if method in READ_METHODS:
                operation["responses"].pop("409", None)
                operation["responses"].pop("422", None)
    for name in FRAMEWORK_SCHEMAS:
        schema.get("components", {}).get("schemas", {}).pop(name, None)
    return schema


def install(app: FastAPI) -> None:
    """Make `app.openapi()` return the refined schema (cached), so the app and the snapshot cannot differ."""
    original = app.openapi

    def openapi() -> dict:
        cached = getattr(app.state, "openapi_refined", None)
        if cached is None:
            cached = refine(copy.deepcopy(original()))
            app.state.openapi_refined = cached
        return cached

    app.openapi = openapi  # type: ignore[method-assign]


def build_schema(app: FastAPI | None = None) -> dict:
    """The app's schema. With no app, a scratch one: the schema never depends on the environment or a store."""
    if app is None:
        from outreach.api.app import create_app

        nowhere = Path("/nonexistent")
        app = create_app(Settings(
            db_path=str(nowhere / "outreach.db"), host="127.0.0.1", port=3100, dev_origins=frozenset(),
            web_dist=nowhere / "dist", banned_phrases=nowhere / "banned_phrases.txt", busy_timeout_ms=5000,
            sweeper=False,
        ))
    return app.openapi()


def render(schema: dict) -> str:
    return json.dumps(schema, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m outreach.api.openapi", description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="write web/openapi.json")
    mode.add_argument("--check", action="store_true", help="exit 1 when web/openapi.json is out of date")
    args = parser.parse_args(argv)
    text = render(build_schema())
    if args.write:
        OPENAPI_PATH.parent.mkdir(parents=True, exist_ok=True)
        OPENAPI_PATH.write_text(text, encoding="utf-8")
        print(f"wrote {OPENAPI_PATH}")
        return 0
    current = OPENAPI_PATH.read_text(encoding="utf-8") if OPENAPI_PATH.exists() else None
    if current != text:
        print(f"{OPENAPI_PATH} is out of date. Run: {REGENERATE}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
