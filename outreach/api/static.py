"""Serve the built web app (`settings.web_dist`, default `web/dist`) on the API's own origin.

A plain ASGI layer inside the guards. It answers only `GET` and `HEAD` for paths outside `/api`; everything else goes
on to the routers unchanged, so an unknown `/api` path is still the JSON 404 envelope and a wrong method still 405.

- `/assets/<file>`: the hashed build output, `Cache-Control: public, max-age=31536000, immutable`. A file that is not
  inside `web_dist/assets` after resolving links and dots is a 404, never anything else on disk.
- Every other page path: `index.html` with `Cache-Control: no-store`, so client-side routes reload.
- No build: a plain 503 that says how to build it. `/api` keeps working.
- `/openapi.json`, `/docs` and `/redoc` go to the routers (404): the schema is never a public route.
"""
from pathlib import Path

from starlette.responses import FileResponse, PlainTextResponse

from outreach.api.errors import STATUS, envelope

NOT_BUILT = "The app is not built. Run: cd web && pnpm build"
IMMUTABLE = "public, max-age=31536000, immutable"
NO_STORE = "no-store"
FRAMEWORK_PATHS = frozenset({"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"})


def is_api(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def asset_path(web_dist: Path, request_path: str) -> Path | None:
    """The file `request_path` (`/assets/...`, already percent-decoded) names inside `web_dist/assets`, or None."""
    if not request_path.startswith("/assets/") or "\x00" in request_path:
        return None
    try:
        assets = (Path(web_dist) / "assets").resolve()
        candidate = (assets / request_path.removeprefix("/assets/").lstrip("/")).resolve()
        if candidate == assets or not candidate.is_relative_to(assets) or not candidate.is_file():
            return None
    except (OSError, ValueError):
        return None
    return candidate


class StaticMiddleware:
    def __init__(self, app, web_dist: Path):
        self.app = app
        self.web_dist = Path(web_dist)

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if (scope["type"] != "http" or scope.get("method") not in ("GET", "HEAD") or is_api(path)
                or path in FRAMEWORK_PATHS):
            await self.app(scope, receive, send)
            return
        await self.response(path)(scope, receive, send)

    def response(self, path: str):
        index = self.web_dist / "index.html"
        if not index.is_file():
            return PlainTextResponse(NOT_BUILT, status_code=503, headers={"Cache-Control": NO_STORE})
        if path.startswith("/assets/"):
            found = asset_path(self.web_dist, path)
            if found is None:
                return envelope("not_found", "No such file in the built app.", STATUS["not_found"])
            return FileResponse(found, headers={"Cache-Control": IMMUTABLE})
        return FileResponse(index, media_type="text/html", headers={"Cache-Control": NO_STORE})
